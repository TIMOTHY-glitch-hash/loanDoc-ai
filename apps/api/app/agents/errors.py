"""Extraction error taxonomy.

Each error maps to exactly one HTTP status in the router, so the API can tell a
caller *why* an upload failed - "this PDF is encrypted" and "the model provider
timed out" are very different problems for a back-office operator.
"""


class ExtractionError(Exception):
    """Base class. ``code`` is the stable identifier surfaced to clients."""

    code = "extraction_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedFileTypeError(ExtractionError):
    code = "unsupported_file_type"


class CorruptDocumentError(ExtractionError):
    """The bytes are not a readable PDF."""

    code = "corrupt_document"


class EncryptedDocumentError(ExtractionError):
    """Password-protected PDFs are rejected rather than silently half-read."""

    code = "encrypted_document"


class EmptyDocumentError(ExtractionError):
    """Parsed fine but contains no extractable text (typically a scan needing OCR)."""

    code = "empty_document"


class DocumentTooLargeError(ExtractionError):
    code = "document_too_large"


class LlmUnavailableError(ExtractionError):
    """No provider key configured, or the provider refused/timed out every page."""

    code = "llm_unavailable"
