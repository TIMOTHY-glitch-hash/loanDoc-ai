"""Wire schemas.

These mirror ``packages/types`` one-to-one. Python stays snake_case internally
while ``alias_generator=to_camel`` emits the camelCase JSON the TypeScript Zod
schemas expect, so neither side has to compromise on its idioms.
"""

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DocumentKind(str, Enum):
    PAY_STUB = "pay_stub"
    BANK_STATEMENT = "bank_statement"
    TAX_RETURN = "tax_return"
    ID_DOCUMENT = "id_document"
    PROPERTY_APPRAISAL = "property_appraisal"
    UNKNOWN = "unknown"


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    CLASSIFYING = "classifying"
    EXTRACTING = "extracting"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    FAILED = "failed"


class LoanDocument(CamelModel):
    id: UUID
    application_id: UUID
    file_name: str
    size_bytes: int = Field(ge=0)
    mime_type: str
    kind: DocumentKind
    status: DocumentStatus
    uploaded_at: datetime
    processed_at: datetime | None = None


class ExtractedField(CamelModel):
    name: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    page: int = Field(ge=1)
    #: Normalised [x0, y0, x1, y1]; ``None`` when the value could not be located.
    bounding_box: tuple[float, float, float, float] | None = None


class ValidationIssue(CamelModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "info"


class ExtractionResult(CamelModel):
    document_id: UUID
    detected_kind: DocumentKind
    fields: list[ExtractedField]
    issues: list[ValidationIssue]
    model: str
    latency_ms: int = Field(ge=0)


class HealthResponse(CamelModel):
    status: Literal["ok"] = "ok"
    version: str
    agent_enabled: bool


class DocumentListResponse(CamelModel):
    items: list[LoanDocument]
    total: int = Field(ge=0)
    next_cursor: str | None = None


class ProcessDocumentResponse(CamelModel):
    document: LoanDocument
    extraction: ExtractionResult


class ApiErrorResponse(CamelModel):
    """Matches FastAPI's default ``{"detail": ...}`` shape, plus a stable code."""

    detail: str
    code: str | None = None
