"""Document processing pipeline.

The pipeline is deliberately split into classify -> extract -> validate so each
stage is unit-testable and swappable. The default implementation is a
deterministic stub: it produces contract-correct output without an LLM key, which
keeps the demo runnable (and free) while leaving one obvious seam
(:meth:`DocumentPipeline._extract`) to plug a real agent into.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import UUID

from app.config import Settings
from app.schemas import (
    DocumentKind,
    DocumentStatus,
    ExtractedField,
    ExtractionResult,
    LoanDocument,
    ValidationIssue,
)

#: Filename hints used by the stub classifier. A real implementation would send
#: the first page to a vision model instead of pattern-matching names.
_FILENAME_HINTS: tuple[tuple[str, DocumentKind], ...] = (
    ("paystub", DocumentKind.PAY_STUB),
    ("pay_stub", DocumentKind.PAY_STUB),
    ("payslip", DocumentKind.PAY_STUB),
    ("bank", DocumentKind.BANK_STATEMENT),
    ("statement", DocumentKind.BANK_STATEMENT),
    ("1040", DocumentKind.TAX_RETURN),
    ("tax", DocumentKind.TAX_RETURN),
    ("passport", DocumentKind.ID_DOCUMENT),
    ("licence", DocumentKind.ID_DOCUMENT),
    ("license", DocumentKind.ID_DOCUMENT),
    ("appraisal", DocumentKind.PROPERTY_APPRAISAL),
)

#: Fields an underwriter needs per document kind. Extraction is driven by this
#: table so adding a document type is a data change, not a code change.
_EXPECTED_FIELDS: dict[DocumentKind, tuple[str, ...]] = {
    DocumentKind.PAY_STUB: ("employer_name", "gross_pay", "net_pay", "pay_period_end"),
    DocumentKind.BANK_STATEMENT: ("account_holder", "closing_balance", "statement_period_end"),
    DocumentKind.TAX_RETURN: ("taxpayer_name", "adjusted_gross_income", "tax_year"),
    DocumentKind.ID_DOCUMENT: ("full_name", "document_number", "expiry_date"),
    DocumentKind.PROPERTY_APPRAISAL: ("property_address", "appraised_value", "appraisal_date"),
    DocumentKind.UNKNOWN: (),
}


class DocumentPipeline:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def classify(self, file_name: str) -> DocumentKind:
        """Map a document to its kind. Lowercased so hints are case-insensitive."""
        haystack = file_name.lower()
        for hint, kind in _FILENAME_HINTS:
            if hint in haystack:
                return kind
        return DocumentKind.UNKNOWN

    def process(self, document: LoanDocument) -> tuple[LoanDocument, ExtractionResult]:
        """Run the full pipeline and return the updated document + extraction."""
        started = time.perf_counter()

        detected_kind = self.classify(document.file_name)
        fields = self._extract(detected_kind)
        issues = self._validate(detected_kind, fields)

        # A single low-confidence field is enough to require human review: in
        # underwriting a silent wrong number is far more costly than a slow one.
        needs_review = any(
            field.confidence < self._settings.review_confidence_threshold for field in fields
        ) or any(issue.severity == "error" for issue in issues)

        processed = document.model_copy(
            update={
                "kind": detected_kind,
                "status": DocumentStatus.NEEDS_REVIEW if needs_review else DocumentStatus.VERIFIED,
                "processed_at": datetime.now(timezone.utc),
            }
        )

        extraction = ExtractionResult(
            document_id=document.id,
            detected_kind=detected_kind,
            fields=fields,
            issues=issues,
            model=self._settings.openai_model if self._settings.agent_enabled else "stub",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return processed, extraction

    def _extract(self, kind: DocumentKind) -> list[ExtractedField]:
        """Seam for the real agent.

        Returns placeholder values with a below-threshold confidence so the
        review path is exercised end-to-end until an LLM is wired in.
        """
        return [
            ExtractedField(
                name=name,
                value="",
                confidence=0.0,
                page=1,
                bounding_box=None,
            )
            for name in _EXPECTED_FIELDS[kind]
        ]

    def _validate(self, kind: DocumentKind, fields: list[ExtractedField]) -> list[ValidationIssue]:
        """Deterministic rules that must never depend on model output quality."""
        issues: list[ValidationIssue] = []

        if kind is DocumentKind.UNKNOWN:
            issues.append(
                ValidationIssue(
                    code="unclassified_document",
                    message="Document kind could not be determined; manual triage required.",
                    severity="warning",
                )
            )

        extracted = {field.name for field in fields if field.value}
        for name in _EXPECTED_FIELDS[kind]:
            if name not in extracted:
                issues.append(
                    ValidationIssue(
                        code="missing_field",
                        message=f"Required field '{name}' was not extracted.",
                        severity="error",
                    )
                )
        return issues


def new_document(
    application_id: UUID,
    document_id: UUID,
    file_name: str,
    mime_type: str,
    size_bytes: int,
) -> LoanDocument:
    """Factory for a freshly uploaded document, before any processing."""
    return LoanDocument(
        id=document_id,
        application_id=application_id,
        file_name=file_name,
        size_bytes=size_bytes,
        mime_type=mime_type,
        kind=DocumentKind.UNKNOWN,
        status=DocumentStatus.UPLOADED,
        uploaded_at=datetime.now(timezone.utc),
        processed_at=None,
    )
