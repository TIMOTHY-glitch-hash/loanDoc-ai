"""Document upload + processing endpoints.

Storage is an in-memory dict on purpose: the scaffold demonstrates the contract
and the pipeline without committing to a database. Swapping in SQLAlchemy means
replacing :data:`_DOCUMENTS` with a repository behind the same three calls.
"""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.config import Settings, get_settings
from app.schemas import DocumentListResponse, LoanDocument, ProcessDocumentResponse
from app.services.pipeline import DocumentPipeline, new_document

router = APIRouter(prefix="/documents", tags=["documents"])

# Insertion-ordered, which gives the list endpoint a stable order for free.
_DOCUMENTS: dict[UUID, LoanDocument] = {}


def get_pipeline(settings: Annotated[Settings, Depends(get_settings)]) -> DocumentPipeline:
    return DocumentPipeline(settings)


@router.get("", response_model=DocumentListResponse, response_model_by_alias=True)
def list_documents(
    application_id: Annotated[UUID | None, Query(alias="applicationId")] = None,
) -> DocumentListResponse:
    items = [
        document
        for document in _DOCUMENTS.values()
        if application_id is None or document.application_id == application_id
    ]
    # `nextCursor` stays None until real pagination is needed; the field exists
    # now so adding it later is not a breaking contract change.
    return DocumentListResponse(items=items, total=len(items), next_cursor=None)


@router.post(
    "",
    response_model=ProcessDocumentResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    settings: Annotated[Settings, Depends(get_settings)],
    pipeline: Annotated[DocumentPipeline, Depends(get_pipeline)],
    application_id: Annotated[UUID, Query(alias="applicationId")],
    file: Annotated[UploadFile, File()],
) -> ProcessDocumentResponse:
    contents = await file.read()
    if len(contents) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_bytes} byte limit.",
        )

    document = new_document(
        application_id=application_id,
        document_id=uuid4(),
        file_name=file.filename or "untitled",
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(contents),
    )

    # Processing is synchronous here so the demo has no queue to run; a real
    # deployment would enqueue and let the client poll the document status.
    processed, extraction = pipeline.process(document)
    _DOCUMENTS[processed.id] = processed
    return ProcessDocumentResponse(document=processed, extraction=extraction)


@router.get("/{document_id}", response_model=LoanDocument, response_model_by_alias=True)
def get_document(document_id: UUID) -> LoanDocument:
    document = _DOCUMENTS.get(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return document
