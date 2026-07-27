"""LangChain document extraction agent.

Pipeline for one upload::

    load PDF -> per-page text -> PII scan -> N parallel LLM calls -> merge -> redact

Design notes:

* Pages are processed **independently and in parallel**. A 40-page bank statement
  would blow the context window as one prompt, and per-page calls also mean one
  bad page degrades a single page instead of failing the document.
* The LLM never sees a merged view, so merging is deterministic Python
  (highest-confidence wins) rather than a second model call. That keeps the result
  reproducible and cheap to explain to a reviewer.
* PII is scanned on the *source* text before evidence snippets are returned, and
  every snippet is redacted on the way out.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.agents import pii
from app.agents.errors import (
    CorruptDocumentError,
    DocumentTooLargeError,
    EmptyDocumentError,
    EncryptedDocumentError,
    LlmUnavailableError,
    UnsupportedFileTypeError,
)
from app.agents.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.agents.schemas import (
    MAX_EVIDENCE_CHARS,
    DocumentExtraction,
    EmploymentStatus,
    ExtractedValue,
    LoanDocumentType,
    NumericEvidence,
    PageExtraction,
    PageOutcome,
    PiiFinding,
    TextEvidence,
)
from app.config import Settings

logger = logging.getLogger(__name__)

#: Pages with less text than this are treated as empty (scanned images, dividers)
#: and skipped before spending a model call on them.
MIN_PAGE_CHARS = 40

#: Guard against a single pathological page exhausting the context window.
MAX_PAGE_CHARS = 12_000

_EMPLOYMENT_KEYWORDS: tuple[tuple[str, EmploymentStatus], ...] = (
    ("self-employ", EmploymentStatus.SELF_EMPLOYED),
    ("self employ", EmploymentStatus.SELF_EMPLOYED),
    ("1099", EmploymentStatus.SELF_EMPLOYED),
    ("part-time", EmploymentStatus.PART_TIME),
    ("part time", EmploymentStatus.PART_TIME),
    ("full-time", EmploymentStatus.FULL_TIME),
    ("full time", EmploymentStatus.FULL_TIME),
    ("retired", EmploymentStatus.RETIRED),
    ("unemploy", EmploymentStatus.UNEMPLOYED),
)

#: Fields carrying evidence, mapped from the LLM schema to the API schema. Driving
#: the merge from a table keeps "add a field" a one-line change.
_EVIDENCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("applicant_name", "applicant_name"),
    ("annual_income", "annual_income"),
    ("monthly_debt_payments", "monthly_debt_payments"),
    ("employment_status", "employment_status_evidence"),
    ("employer_name", "employer_name"),
    ("loan_amount_requested", "loan_amount_requested"),
    ("property_value", "property_value"),
    ("credit_score", "credit_score"),
)


class StructuredLlm(Protocol):
    """The slice of a LangChain runnable this module uses.

    Declared as a Protocol because ``with_structured_output`` is typed as
    returning a union of dict/BaseModel; narrowing it here once means no
    ``Any`` leaks into the extraction logic.
    """

    async def ainvoke(self, input: list[SystemMessage | HumanMessage], /) -> PageExtraction: ...


@dataclass(frozen=True)
class _Page:
    number: int
    text: str


def _normalise_value(evidence: TextEvidence | NumericEvidence) -> str:
    """Render either evidence flavour as the string the API returns."""
    if isinstance(evidence.value, Decimal):
        # Normalise away trailing zeros so '84500.00' and '84500' compare equal.
        return format(evidence.value.normalize(), "f")
    return evidence.value.strip()


def _to_extracted_value(evidence: TextEvidence | NumericEvidence, page: int) -> ExtractedValue:
    snippet = pii.redact(evidence.raw_text.strip())[:MAX_EVIDENCE_CHARS]
    return ExtractedValue(
        value=_normalise_value(evidence),
        confidence=evidence.confidence,
        page=page,
        raw_text=snippet,
    )


def _classify_employment(evidence: ExtractedValue | None) -> EmploymentStatus | None:
    """Map the model's free text onto our enum.

    Done in Python rather than asking the model for the enum directly: the raw
    wording stays available as evidence, and the mapping is auditable.
    """
    if evidence is None:
        return None

    haystack = evidence.value.lower()
    for keyword, status in _EMPLOYMENT_KEYWORDS:
        if keyword in haystack:
            return status
    return EmploymentStatus.UNKNOWN


class DocumentExtractor:
    """Extracts loan underwriting fields from a PDF using an LLM per page.

    ``llm`` is injectable so tests can drive the whole pipeline deterministically
    without a provider key or network access.
    """

    def __init__(self, settings: Settings, llm: StructuredLlm | None = None) -> None:
        self._settings = settings
        self._llm = llm

    # -- public API ----------------------------------------------------------

    async def extract(self, path: Path, file_name: str | None = None) -> DocumentExtraction:
        """Run the full pipeline for one file.

        Raises the :mod:`app.agents.errors` types for conditions the caller must
        act on; per-page failures are reported in ``pages``/``warnings`` instead,
        because a 30-page statement should not be lost to one bad page.
        """
        started = time.perf_counter()
        display_name = file_name or path.name

        pages = self._load_pages(path, display_name)
        findings = self._scan_for_pii(pages)
        pii.log_privacy_notice(display_name, findings)

        results, outcomes, warnings = await self._extract_pages(pages, display_name)

        if not results:
            # Every page failed at the provider: that is an availability problem,
            # not a document problem, so it must not look like a clean extraction.
            raise LlmUnavailableError(
                "The extraction model returned no usable result for any page: "
                + "; ".join(warnings)
            )

        extraction = self._merge(
            file_name=display_name,
            page_count=len(pages),
            results=results,
            outcomes=outcomes,
            warnings=warnings,
            findings=findings,
        )
        return extraction.model_copy(
            update={"latency_ms": int((time.perf_counter() - started) * 1000)}
        )

    # -- stages --------------------------------------------------------------

    def _load_pages(self, path: Path, display_name: str) -> list[_Page]:
        if path.suffix.lower() != ".pdf":
            raise UnsupportedFileTypeError(
                f"Only PDF uploads are supported; received '{path.suffix or 'no extension'}'."
            )

        try:
            reader = PdfReader(str(path))
        except PdfReadError as exc:
            raise CorruptDocumentError(f"'{display_name}' is not a readable PDF: {exc}") from exc
        except OSError as exc:
            raise CorruptDocumentError(f"Could not read '{display_name}': {exc}") from exc

        if reader.is_encrypted:
            raise EncryptedDocumentError(
                f"'{display_name}' is password-protected; decrypt it before uploading."
            )

        if len(reader.pages) > self._settings.extraction_max_pages:
            raise DocumentTooLargeError(
                f"'{display_name}' has {len(reader.pages)} pages; the limit is "
                f"{self._settings.extraction_max_pages}."
            )

        pages: list[_Page] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except (PdfReadError, ValueError, KeyError) as exc:
                # One unreadable page must not sink the document.
                logger.warning("Failed to extract text from page %s: %s", index, exc)
                text = ""
            pages.append(_Page(number=index, text=text.strip()[:MAX_PAGE_CHARS]))

        if not any(len(page.text) >= MIN_PAGE_CHARS for page in pages):
            raise EmptyDocumentError(
                f"No extractable text found in '{display_name}'. It is most likely a "
                "scan and needs OCR before extraction."
            )

        return pages

    def _scan_for_pii(self, pages: list[_Page]) -> list[PiiFinding]:
        findings: list[PiiFinding] = []
        for page in pages:
            findings.extend(pii.scan_page(page.number, page.text))
        return findings

    async def _extract_pages(
        self, pages: list[_Page], display_name: str
    ) -> tuple[list[tuple[int, PageExtraction]], list[PageOutcome], list[str]]:
        """Fan out over pages, bounded by a semaphore.

        Concurrency is capped by configuration rather than unbounded `gather`:
        provider rate limits are the real constraint on a 40-page statement.
        """
        llm = self._resolve_llm()
        semaphore = asyncio.Semaphore(self._settings.extraction_page_concurrency)

        substantive = [page for page in pages if len(page.text) >= MIN_PAGE_CHARS]
        skipped = [page for page in pages if len(page.text) < MIN_PAGE_CHARS]

        async def run(page: _Page) -> tuple[int, PageExtraction | Exception]:
            async with semaphore:
                messages: list[SystemMessage | HumanMessage] = [
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(
                        content=USER_PROMPT_TEMPLATE.format(
                            file_name=display_name,
                            page_number=page.number,
                            page_count=len(pages),
                            page_text=page.text,
                        )
                    ),
                ]
                try:
                    return page.number, await llm.ainvoke(messages)
                except Exception as exc:  # noqa: BLE001 - provider errors are opaque
                    return page.number, exc

        completed = await asyncio.gather(*(run(page) for page in substantive))

        results: list[tuple[int, PageExtraction]] = []
        outcomes: list[PageOutcome] = [
            PageOutcome(page=page.number, status="empty") for page in skipped
        ]
        warnings: list[str] = [
            f"Page {page.number} contained no extractable text and was skipped." for page in skipped
        ]

        for page_number, outcome in completed:
            if isinstance(outcome, Exception):
                logger.warning("Extraction failed for page %s: %s", page_number, outcome)
                outcomes.append(
                    PageOutcome(
                        page=page_number,
                        status="failed",
                        error=f"{type(outcome).__name__}: {outcome}",
                    )
                )
                warnings.append(f"Page {page_number} failed extraction and was excluded.")
                continue

            results.append((page_number, outcome))
            outcomes.append(
                PageOutcome(
                    page=page_number,
                    status="extracted",
                    detected_type=outcome.document_type,
                )
            )

        outcomes.sort(key=lambda item: item.page)
        return results, outcomes, warnings

    def _merge(
        self,
        *,
        file_name: str,
        page_count: int,
        results: list[tuple[int, PageExtraction]],
        outcomes: list[PageOutcome],
        warnings: list[str],
        findings: list[PiiFinding],
    ) -> DocumentExtraction:
        """Reduce per-page results into one document view.

        Field values: highest confidence wins, ties broken by earliest page, which
        favours summary pages over appendices.
        Document type: confidence-weighted vote, so one uncertain misclassification
        cannot outvote several confident pages.
        """
        merged: dict[str, ExtractedValue] = {}
        for page_number, result in results:
            for source_field, target_field in _EVIDENCE_FIELDS:
                evidence = getattr(result, source_field)
                if evidence is None:
                    continue
                candidate = _to_extracted_value(evidence, page_number)
                incumbent = merged.get(target_field)
                if incumbent is None or candidate.confidence > incumbent.confidence:
                    merged[target_field] = candidate

        votes: dict[LoanDocumentType, float] = {}
        for _, result in results:
            if result.document_type is LoanDocumentType.UNKNOWN:
                continue
            votes[result.document_type] = (
                votes.get(result.document_type, 0.0) + result.document_type_confidence
            )

        if votes:
            document_type = max(votes, key=lambda kind: votes[kind])
            # Normalise the winner's share of the total vote into a 0-1 confidence.
            document_type_confidence = round(votes[document_type] / sum(votes.values()), 2)
        else:
            document_type = LoanDocumentType.UNKNOWN
            document_type_confidence = 0.0

        employment_evidence = merged.get("employment_status_evidence")

        return DocumentExtraction(
            file_name=file_name,
            document_type=document_type,
            document_type_confidence=document_type_confidence,
            page_count=page_count,
            applicant_name=merged.get("applicant_name"),
            annual_income=merged.get("annual_income"),
            monthly_debt_payments=merged.get("monthly_debt_payments"),
            employment_status=_classify_employment(employment_evidence),
            employment_status_evidence=employment_evidence,
            employer_name=merged.get("employer_name"),
            loan_amount_requested=merged.get("loan_amount_requested"),
            property_value=merged.get("property_value"),
            credit_score=merged.get("credit_score"),
            pii_findings=findings,
            privacy_notice=pii.build_privacy_notice(findings),
            pages=outcomes,
            warnings=warnings,
            model=self._settings.openai_model,
            latency_ms=0,
        )

    # -- provider ------------------------------------------------------------

    def _resolve_llm(self) -> StructuredLlm:
        """Build the structured-output runnable, or reuse the injected one."""
        if self._llm is not None:
            return self._llm

        if not self._settings.agent_enabled:
            raise LlmUnavailableError(
                "No OPENAI_API_KEY configured; set one to enable live extraction."
            )

        # Imported lazily so the module (and the rest of the API) loads without the
        # provider SDK present or configured.
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=self._settings.openai_model,
            # Extraction must be reproducible; sampling has no upside here.
            temperature=0,
            timeout=self._settings.openai_timeout_seconds,
            max_retries=self._settings.openai_max_retries,
            api_key=SecretStr(self._settings.openai_api_key),
        )
        # `include_raw=False` (default) makes the runnable return the parsed model,
        # which is exactly the StructuredLlm contract.
        structured = model.with_structured_output(PageExtraction)
        self._llm = cast(StructuredLlm, structured)
        return self._llm
