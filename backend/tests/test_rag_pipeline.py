"""End-to-end regression tests for the document RAG pipeline.

Covers upload -> extract -> chunk -> embed -> index -> retrieve -> answer
using in-memory providers. These are the guardrails AGENTS.md section 34 asks
for.
"""

from sqlalchemy.orm import Session

from tests.pdf_builder import build_pdf


REVENUE_PDF = build_pdf(
    [
        "Sentineth Q3 planning notes.",
        "The revenue target for Q3 is four million dollars.",
        "Project Atlas is blocked on vendor security review.",
    ]
)

HIRING_PDF = build_pdf(
    [
        "Sentineth hiring plan.",
        "We plan to hire two backend engineers in Q3.",
        "Recruiting is owned by the operations team.",
    ]
)


def upload(client, organization_id, filename, content):
    return client.post(
        f"/organizations/{organization_id}/documents",
        files=(
            (
                "file",
                (filename, content, "application/pdf"),
            ),
        ),
    )


def test_health_and_root():
    # No fixtures: these must work with nothing configured.
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "healthy"}
        assert client.get("/").json()["name"] == "Sentineth AI"


def test_upload_extracts_chunks_and_indexes(
    client, organization, vector_store
):
    org_id = organization()

    response = upload(client, org_id, "q3-planning.pdf", REVENUE_PDF)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "READY"
    assert body["filename"] == "q3-planning.pdf"
    assert body["chunk_count"] >= 1
    assert body["file_size"] == len(REVENUE_PDF)

    # Vectors reached the store, stamped with the owning organization.
    assert len(vector_store.points) == body["chunk_count"]

    for point in vector_store.points.values():
        assert point["payload"]["organization_id"] == org_id
        assert point["payload"]["filename"] == "q3-planning.pdf"


def test_query_returns_answer_with_filename_citation(
    client, organization, llm_provider
):
    """Regression: sources[].filename used to always be null.

    `retrieval_service` dropped `filename` from the Qdrant payload while
    `query_service` read it, so every citation came back unattributed.
    """
    org_id = organization()

    assert upload(
        client, org_id, "q3-planning.pdf", REVENUE_PDF
    ).status_code == 200

    response = client.post(
        f"/organizations/{org_id}/query",
        json={"query": "What is the revenue target for Q3?"},
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["answer"] == "Fake grounded answer."
    assert body["sources"], "expected at least one citation"

    source = body["sources"][0]
    assert source["filename"] == "q3-planning.pdf"
    assert source["document_id"]
    assert source["chunk_id"]
    assert source["score"] is not None

    # The retrieved text was actually put in front of the model, and the
    # anti-injection system prompt was included.
    messages = llm_provider.calls[0]
    assert messages[0]["role"] == "system"
    assert "untrusted" in messages[0]["content"].lower()
    assert "revenue target" in messages[1]["content"].lower()


def test_search_returns_filename_and_content(client, organization):
    org_id = organization()

    assert upload(
        client, org_id, "q3-planning.pdf", REVENUE_PDF
    ).status_code == 200

    response = client.post(
        f"/organizations/{org_id}/search",
        json={"query": "revenue target", "limit": 5},
    )

    assert response.status_code == 200, response.text
    results = response.json()["results"]

    assert results
    assert results[0]["filename"] == "q3-planning.pdf"
    assert "revenue target" in results[0]["content"].lower()


def test_retrieval_ranks_the_relevant_document_first(client, organization):
    org_id = organization()

    assert upload(
        client, org_id, "q3-planning.pdf", REVENUE_PDF
    ).status_code == 200
    assert upload(
        client, org_id, "hiring-plan.pdf", HIRING_PDF
    ).status_code == 200

    response = client.post(
        f"/organizations/{org_id}/search",
        json={"query": "how many backend engineers are we hiring", "limit": 2},
    )

    assert response.status_code == 200, response.text
    results = response.json()["results"]

    assert len(results) == 2
    assert results[0]["filename"] == "hiring-plan.pdf"


def test_organization_isolation_on_search_and_query(client, organization):
    """A second organization must not see the first one's documents."""

    org_a = organization("Org A")
    org_b = organization("Org B")

    assert upload(
        client, org_a, "q3-planning.pdf", REVENUE_PDF
    ).status_code == 200

    search = client.post(
        f"/organizations/{org_b}/search",
        json={"query": "revenue target"},
    )
    assert search.status_code == 200, search.text
    assert search.json()["results"] == []

    query = client.post(
        f"/organizations/{org_b}/query",
        json={"query": "What is the revenue target for Q3?"},
    )
    assert query.status_code == 200, query.text

    body = query.json()
    assert body["sources"] == []
    assert "couldn't find enough relevant information" in body["answer"]


def test_non_pdf_upload_is_rejected(client, organization, vector_store):
    org_id = organization()

    response = client.post(
        f"/organizations/{org_id}/documents",
        files=(("file", ("notes.txt", b"plain text notes", "text/plain")),),
    )

    assert response.status_code == 415
    assert response.json()["detail"]["error_code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert vector_store.points == {}


def test_empty_upload_is_rejected(client, organization):
    org_id = organization()

    response = client.post(
        f"/organizations/{org_id}/documents",
        files=(("file", ("empty.pdf", b"", "application/pdf")),),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "EXTRACTION_FAILED"


def test_query_validation_rejects_blank_and_oversized_limit(
    client, organization
):
    org_id = organization()

    assert client.post(
        f"/organizations/{org_id}/query",
        json={"query": "   "},
    ).status_code == 400

    assert client.post(
        f"/organizations/{org_id}/query",
        json={"query": "anything", "limit": 999},
    ).status_code == 422


def test_same_filename_twice_does_not_overwrite(
    client, organization, tmp_path
):
    """Regression: uploads were stored at {org}/{filename}.

    Two documents sharing a filename resolved to the same path, so the
    second upload silently overwrote the first one's bytes and deleting
    either document removed the file both rows pointed at.
    """
    org_id = organization()
    storage_root = tmp_path / "documents" / org_id

    first = upload(client, org_id, "notes.pdf", REVENUE_PDF)
    second = upload(client, org_id, "notes.pdf", HIRING_PDF)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    first_id = first.json()["id"]
    second_id = second.json()["id"]
    assert first_id != second_id

    # Each document owns a directory named after its id.
    first_file = storage_root / first_id / "notes.pdf"
    second_file = storage_root / second_id / "notes.pdf"

    assert first_file.read_bytes() == REVENUE_PDF
    assert second_file.read_bytes() == HIRING_PDF

    # Deleting one leaves the other's bytes on disk.
    assert client.delete(
        f"/organizations/{org_id}/documents/{first_id}"
    ).status_code == 204

    assert not first_file.exists()
    assert second_file.read_bytes() == HIRING_PDF


PUBLIC_DOCUMENT_FIELDS = {
    "id",
    "filename",
    "content_type",
    "file_size",
    "status",
    "error_code",
    "chunk_count",
    "created_at",
    "updated_at",
}


def test_document_responses_do_not_leak_internal_fields(client, organization):
    """Regression: neither route had a response_model.

    The listing returned raw ORM objects and the upload route hand-built a
    dict, so both published storage_path - an absolute filesystem path - and
    the listing published content_hash and error_message too.
    """
    org_id = organization()

    upload_body = upload(
        client, org_id, "q3-planning.pdf", REVENUE_PDF
    ).json()

    assert set(upload_body) == PUBLIC_DOCUMENT_FIELDS | {"message"}

    listing = client.get(f"/organizations/{org_id}/documents")

    assert listing.status_code == 200, listing.text
    assert set(listing.json()["items"][0]) == PUBLIC_DOCUMENT_FIELDS

    for leaked in ("storage_path", "content_hash", "error_message"):
        assert leaked not in listing.text
        assert leaked not in str(upload_body)


def test_listing_documents_is_paginated(client, organization):
    org_id = organization()

    for name in ("first.pdf", "second.pdf", "third.pdf"):
        assert upload(client, org_id, name, REVENUE_PDF).status_code == 200

    first_page = client.get(
        f"/organizations/{org_id}/documents", params={"limit": 2}
    ).json()

    assert first_page["total"] == 3
    assert first_page["limit"] == 2
    assert first_page["offset"] == 0
    assert len(first_page["items"]) == 2

    second_page = client.get(
        f"/organizations/{org_id}/documents",
        params={"limit": 2, "offset": 2},
    ).json()

    assert second_page["total"] == 3
    assert len(second_page["items"]) == 1

    # Every document appears exactly once across the two pages.
    paged = [item["id"] for item in first_page["items"] + second_page["items"]]
    assert len(set(paged)) == 3

    assert client.get(
        f"/organizations/{org_id}/documents", params={"limit": 0}
    ).status_code == 422


def test_a_failed_document_reports_a_categorised_error_code(
    client, organization
):
    org_id = organization()

    client.post(
        f"/organizations/{org_id}/documents",
        files=(("file", ("notes.txt", b"plain text notes", "text/plain")),),
    )

    items = client.get(f"/organizations/{org_id}/documents").json()["items"]

    assert len(items) == 1
    assert items[0]["status"] == "FAILED"
    assert items[0]["error_code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_a_failed_upload_leaves_no_file_behind(client, organization, tmp_path):
    org_id = organization()

    response = client.post(
        f"/organizations/{org_id}/documents",
        files=(("file", ("notes.txt", b"plain text notes", "text/plain")),),
    )

    assert response.status_code == 415

    # The row survives as FAILED, but nothing readable will ever point at
    # the bytes again, so they should not still be on disk.
    stored = [
        path for path in (tmp_path / "documents").rglob("*") if path.is_file()
    ]
    assert stored == []


def test_a_successful_upload_commits_once(client, organization, monkeypatch):
    org_id = organization()

    commits = []
    original_commit = Session.commit

    def counting_commit(self):
        commits.append(self)
        return original_commit(self)

    monkeypatch.setattr(Session, "commit", counting_commit)

    response = upload(client, org_id, "revenue.pdf", REVENUE_PDF)

    assert response.status_code == 200
    # One layer owns the transaction boundary: the request writes the
    # document, its chunks and its READY status or none of them.
    assert len(commits) == 1


def test_reindexing_rebuilds_the_vectors_and_returns_the_document(
    client, organization, vector_store
):
    org_id = organization()
    document_id = upload(client, org_id, "revenue.pdf", REVENUE_PDF).json()["id"]

    indexed = dict(vector_store.points)
    assert indexed

    response = client.post(
        f"/organizations/{org_id}/documents/{document_id}/reindex"
    )

    assert response.status_code == 200, response.text

    body = response.json()
    assert set(body) == PUBLIC_DOCUMENT_FIELDS
    assert body["id"] == document_id
    assert body["status"] == "READY"
    assert body["chunk_count"] == len(indexed)

    # The old points were dropped and rebuilt, not added alongside.
    assert len(vector_store.points) == len(indexed)


def test_reindexing_an_unknown_document_is_a_404(client, organization):
    org_id = organization()

    assert client.post(
        f"/organizations/{org_id}/documents/"
        "00000000-0000-0000-0000-000000000000/reindex"
    ).status_code == 404
