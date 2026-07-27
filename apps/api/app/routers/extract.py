"""Extraction endpoint.

Accepts a multipart upload, runs the LangChain agent, and returns the structured
result. The uploaded bytes are written to a temporary file and deleted in a
``finally`` block: pypdf needs a seekable file, and loan documents must not linger
on disk after the request.
"""

import logging
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.agents.errors import (
    CorruptDocumentError,
    DocumentTooLargeError,
    EmptyDocumentError,
    EncryptedDocumentError,
    ExtractionError,
    LlmUnavailableError,
    UnsupportedFileTypeError,
)
from app.agents.extractor import DocumentExtractor
from app.agents.schemas import DocumentExtraction
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["extraction"])

#: Error -> HTTP status. Keeping it as data means the router has no branching on
#: exception types and a new error class cannot silently become a 500.
_STATUS_BY_ERROR: dict[type[ExtractionError], int] = {
    UnsupportedFileTypeError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    CorruptDocumentError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    EncryptedDocumentError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    EmptyDocumentError: status.HTTP_422_UNPROCESSABLE_ENTITY,
    DocumentTooLargeError: status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    LlmUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_extractor(settings: Annotated[Settings, Depends(get_settings)]) -> DocumentExtractor:
    return DocumentExtractor(settings)


@router.post(
    "/extract",
    response_model=DocumentExtraction,
    response_model_by_alias=True,
    summary="Extract loan underwriting fields from a PDF",
)
async def extract_document(
    settings: Annotated[Settings, Depends(get_settings)],
    extractor: Annotated[DocumentExtractor, Depends(get_extractor)],
    file: Annotated[UploadFile, File(description="The loan document (PDF).")],
    application_id: Annotated[str | None, Form(alias="applicationId")] = None,
) -> DocumentExtraction:
    contents = await file.read()

    if not contents:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The uploaded file is empty.",
        )

    if len(contents) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_bytes} byte limit.",
        )

    file_name = file.filename or "upload.pdf"
    # The suffix matters: the extractor rejects non-PDFs by extension.
    suffix = Path(file_name).suffix or ".pdf"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
        handle.write(contents)
        handle.flush()

        try:
            result = await extractor.extract(Path(handle.name), file_name=file_name)
        except ExtractionError as exc:
            logger.info(
                "Extraction rejected upload",
                extra={"file_name": file_name, "code": exc.code, "applicationId": application_id},
            )
            raise HTTPException(
                # Unknown subclasses fall back to 422 rather than leaking a 500.
                status_code=_STATUS_BY_ERROR.get(type(exc), status.HTTP_422_UNPROCESSABLE_ENTITY),
                detail=exc.message,
            ) from exc

    if result.privacy_notice is not None:
        logger.info(
            "Returning extraction with privacy notice",
            extra={"file_name": file_name, "applicationId": application_id},
        )

    return result
