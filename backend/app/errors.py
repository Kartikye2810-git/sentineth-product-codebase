"""Failure taxonomy for document ingestion.

Before this existed every failure surfaced as ValueError("Document
processing failed.") and the API answered 500, so a PDF the parser could not
read, a .txt file, and Qdrant being down were indistinguishable to the
caller and to the logs.

Each class carries the HTTP status the API should answer with and the stable
code stored on Document.error_code, so the mapping lives in one place
instead of being re-derived per route.
"""


class DocumentProcessingError(Exception):
    """A document could not be ingested. Cause unknown or internal."""

    code = "PROCESSING_FAILED"
    status_code = 500


class UnsupportedMediaType(DocumentProcessingError):
    """The file is not a type we can ingest."""

    code = "UNSUPPORTED_MEDIA_TYPE"
    status_code = 415


class ExtractionFailed(DocumentProcessingError):
    """The file is the right type but no usable text came out of it."""

    code = "EXTRACTION_FAILED"
    status_code = 422


class ProviderUnavailable(DocumentProcessingError):
    """An embedding or vector-store call failed. Retrying may work."""

    code = "PROVIDER_UNAVAILABLE"
    status_code = 503
