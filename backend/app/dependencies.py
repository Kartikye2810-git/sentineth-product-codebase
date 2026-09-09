"""Application-scoped provider factories used as FastAPI dependencies.

Providers are cached for the lifetime of the process. Before this module
existed, `Depends(get_embedding_provider)` constructed a new
`LocalEmbeddingProvider` on *every* request, which reloaded the
all-MiniLM-L6-v2 weights from disk each time, and every request also
re-ran the Qdrant `collection_exists` round-trip.

Caching here is what AGENTS.md section 29-30 asks for: a single place that owns
provider construction, with heavyweight clients reused rather than
rebuilt per request.

The cached factories are plain functions, so they can also be called
from scripts and background jobs, not just from request handlers.
"""

import logging
from functools import lru_cache, wraps
from pathlib import Path

from app.errors import ProviderUnavailable
from app.providers.embeddings.base import EmbeddingProvider
from app.providers.embeddings.local import LocalEmbeddingProvider
from app.providers.embeddings.nvidia import NvidiaEmbeddingProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openrouter import OpenRouterProvider
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.local import LocalRerankProvider
from app.providers.storage.base import StorageProvider
from app.providers.storage.local import LocalStorageProvider
from app.providers.vector.base import VectorStore
from app.providers.vector.qdrant import QdrantVectorStore
from app.settings import get_settings


logger = logging.getLogger(__name__)


# backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]

STORAGE_DIR = BACKEND_DIR / "storage" / "documents"


EMBEDDING_PROVIDERS = {
    "local": LocalEmbeddingProvider,
    "nvidia": NvidiaEmbeddingProvider,
}

# nemotron-3-embed-1b is the primary model. It beat all-MiniLM-L6-v2 by
# 12.7 points of recall@5 on the 102-question eval set (82.4% -> 95.1%,
# p=0.004), which is not a margin a default should be on the wrong side of.
# "local" remains for offline development and CI, where no key exists.
DEFAULT_EMBEDDING_PROVIDER = "nvidia"

# One collection per embedding model. A collection's vector size is fixed
# when it is created, so 2048-dimension vectors have nowhere to go in a
# 384-dimension collection; keeping them apart is also what makes a model
# rollback free rather than a second migration.
COLLECTIONS = {
    "local": "sentineth_documents",
    "nvidia": "sentineth_documents_nemotron",
}


def active_embedding_provider() -> str:
    return get_settings().embedding_provider


def active_collection_name() -> str:
    """The collection this configuration reads and writes.

    Derived from the provider rather than defaulted alongside it, so the two
    cannot disagree about which vectors belong to which model. This was
    computed inline in two places and one of them did not case-fold, so
    EMBEDDING_PROVIDER=NVIDIA pointed the application and the reindex script
    at different collections.
    """
    return get_settings().collection_name


def configured_provider(factory):
    @wraps(factory)
    def build():
        try:
            return factory()
        except Exception as exc:
            logger.exception("Provider initialization failed")
            raise ProviderUnavailable("Provider is unavailable or not configured.") from exc
    return build


@lru_cache(maxsize=1)
@configured_provider
def get_embedding_provider() -> EmbeddingProvider:
    # Selected by environment because switching embedding models means
    # rebuilding every vector, and the safe way to do that is to build the
    # new collection alongside the old one and change which one the process
    # reads. That makes the cutover - and the rollback - a restart with a
    # different value here, not a deploy. See scripts/reindex.py.
    name = active_embedding_provider()

    if name not in EMBEDDING_PROVIDERS:
        raise ValueError(
            f"Unknown EMBEDDING_PROVIDER {name!r}. "
            f"Expected one of: {', '.join(sorted(EMBEDDING_PROVIDERS))}."
        )

    provider = EMBEDDING_PROVIDERS[name]()

    logger.info(
        "Embedding provider ready: %s (dimension=%s)",
        type(provider).__name__,
        provider.dimension,
    )

    return provider


@lru_cache(maxsize=1)
@configured_provider
def get_vector_store() -> VectorStore:
    # Derive the collection dimension from the active embedding provider
    # instead of hardcoding it, so the two can never drift apart.
    dimension = get_embedding_provider().dimension

    store = QdrantVectorStore(
        collection_name=active_collection_name(),
        vector_size=dimension,
        # Like the dimension, this is fixed when the collection is created
        # and cannot be added later, so changing it is a reindex into a new
        # collection rather than a setting that takes effect on restart.
        hybrid=get_settings().qdrant_hybrid,
    )

    logger.info(
        "Vector store ready: collection=%s dimension=%s hybrid=%s",
        store.collection_name,
        dimension,
        store.hybrid,
    )

    return store


@lru_cache(maxsize=1)
@configured_provider
def get_rerank_provider() -> RerankProvider | None:
    # Off unless asked for. It loads a second model into the process and
    # adds a forward pass per candidate to every search, so it should be
    # switched on by someone who has seen it pay for that.
    if not get_settings().rerank:
        return None

    provider = LocalRerankProvider()

    logger.info("Rerank provider ready: %s", type(provider).__name__)

    return provider


@lru_cache(maxsize=1)
def get_storage_provider() -> StorageProvider:
    return LocalStorageProvider(get_settings().storage_dir)


@lru_cache(maxsize=1)
def _build_llm_provider() -> LLMProvider:
    return OpenRouterProvider()


@configured_provider
def get_llm_provider() -> LLMProvider:
    return _build_llm_provider()


def reset_provider_cache() -> None:
    """Drop all cached providers.

    Intended for tests and for scripts that change provider
    configuration at runtime.
    """
    get_embedding_provider.cache_clear()
    get_vector_store.cache_clear()
    get_rerank_provider.cache_clear()
    get_storage_provider.cache_clear()
    _build_llm_provider.cache_clear()
