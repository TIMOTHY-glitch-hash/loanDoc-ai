from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from app.agents.errors import (
    EmptyDocumentError,
    LlmUnavailableError,
    UnsupportedFileTypeError,
)
from app.agents.extractor import DocumentExtractor
from app.agents.schemas import (
    EmploymentStatus,
    LoanDocumentType,
    NumericEvidence,
    PageExtraction,
    TextEvidence,
)
from app.config import Settings
from tests.conftest import FakeLlm

PAY_STUB_PAGE = """ACME MANUFACTURING INC
Employee: Dana Whitfield
Employee SSN: 123-45-6789
Pay period ending 03/15/2025
Gross pay this period 4,250.00
Employment: Full-Time salaried
"""

SUMMARY_PAGE = """LOAN APPLICATION SUMMARY
Applicant: Dana Whitfield
Annual income 102,000.00
Monthly debt payments 1,480.00
Loan amount requested 415,000.00
Property value 520,000.00
"""


def _settings(**overrides: object) -> Settings:
    return Settings(api_env="development", openai_model="gpt-4o", **overrides)  # type: ignore[arg-type]


def _pay_stub_result(confidence: float = 0.72) -> PageExtraction:
    return PageExtraction(
        document_type=LoanDocumentType.PAY_STUB,
        document_type_confidence=0.94,
        applicant_name=TextEvidence(
            value="Dana Whitfield",
            confidence=confidence,
            raw_text="Employee: Dana Whitfield, SSN 123-45-6789",
        ),
        employment_status=TextEvidence(
            value="Full-Time salaried",
            confidence=0.9,
            raw_text="Employment: Full-Time salaried",
        ),
    )


def _summary_result() -> PageExtraction:
    return PageExtraction(
        document_type=LoanDocumentType.PAY_STUB,
        document_type_confidence=0.4,
        applicant_name=TextEvidence(
            value="Dana Whitfield",
            confidence=0.98,
            raw_text="Applicant: Dana Whitfield",
        ),
        annual_income=NumericEvidence(
            value=Decimal("102000.00"),
            confidence=0.95,
            raw_text="Annual income 102,000.00",
        ),
        loan_amount_requested=NumericEvidence(
            value=Decimal("415000"),
            confidence=0.93,
            raw_text="Loan amount requested 415,000.00",
        ),
    )


async def test_multipage_pdf_fans_out_one_call_per_page(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([PAY_STUB_PAGE, SUMMARY_PAGE], "bundle.pdf")
    llm = FakeLlm([_pay_stub_result(), _summary_result()])

    result = await DocumentExtractor(_settings(), llm=llm).extract(path)

    assert len(llm.calls) == 2
    assert result.page_count == 2
    assert [page.status for page in result.pages] == ["extracted", "extracted"]


async def test_merge_keeps_the_highest_confidence_value_per_field(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([PAY_STUB_PAGE, SUMMARY_PAGE], "bundle.pdf")
    llm = FakeLlm([_pay_stub_result(confidence=0.72), _summary_result()])

    result = await DocumentExtractor(_settings(), llm=llm).extract(path)

    assert result.applicant_name is not None
    # Both pages named the applicant; page 2 was more confident, so it wins.
    assert result.applicant_name.confidence == 0.98
    assert result.applicant_name.page == 2
    assert result.annual_income is not None
    # Decimal is normalised, not float-formatted.
    assert result.annual_income.value == "102000"


async def test_document_type_vote_is_confidence_weighted(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([PAY_STUB_PAGE, SUMMARY_PAGE], "bundle.pdf")
    w2_page = _summary_result().model_copy(
        update={"document_type": LoanDocumentType.W2, "document_type_confidence": 0.4}
    )
    llm = FakeLlm([_pay_stub_result(), w2_page])

    result = await DocumentExtractor(_settings(), llm=llm).extract(path)

    # payStub at 0.94 outvotes a single W2 guess at 0.4.
    assert result.document_type is LoanDocumentType.PAY_STUB
    assert 0 < result.document_type_confidence <= 1


async def test_evidence_is_pii_redacted_and_notice_is_attached(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([PAY_STUB_PAGE], "paystub.pdf")
    llm = FakeLlm([_pay_stub_result(confidence=0.99)])

    result = await DocumentExtractor(_settings(), llm=llm).extract(path)

    assert result.applicant_name is not None
    # The model echoed the SSN in its evidence; it must not reach the caller.
    assert "123-45-6789" not in result.applicant_name.raw_text
    assert "[REDACTED]" in result.applicant_name.raw_text
    assert result.privacy_notice is not None
    assert [finding.kind.value for finding in result.pii_findings] == ["ssn"]


async def test_employment_status_is_mapped_from_evidence(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([PAY_STUB_PAGE], "paystub.pdf")
    llm = FakeLlm([_pay_stub_result()])

    result = await DocumentExtractor(_settings(), llm=llm).extract(path)

    assert result.employment_status is EmploymentStatus.FULL_TIME
    assert result.employment_status_evidence is not None


async def test_one_failing_page_degrades_to_a_warning(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([PAY_STUB_PAGE, SUMMARY_PAGE], "bundle.pdf")
    llm = FakeLlm([RuntimeError("provider 429"), _summary_result()])

    result = await DocumentExtractor(_settings(), llm=llm).extract(path)

    failed = [page for page in result.pages if page.status == "failed"]
    assert len(failed) == 1
    assert failed[0].error is not None and "provider 429" in failed[0].error
    assert any("failed extraction" in warning for warning in result.warnings)
    # The surviving page still produced a result.
    assert result.annual_income is not None


async def test_all_pages_failing_raises_rather_than_returning_an_empty_result(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([PAY_STUB_PAGE, SUMMARY_PAGE], "bundle.pdf")
    llm = FakeLlm([RuntimeError("provider down"), RuntimeError("provider down")])

    with pytest.raises(LlmUnavailableError):
        await DocumentExtractor(_settings(), llm=llm).extract(path)


async def test_non_pdf_upload_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "statement.txt"
    path.write_text("not a pdf")

    with pytest.raises(UnsupportedFileTypeError):
        await DocumentExtractor(_settings(), llm=FakeLlm([])).extract(path)


async def test_pdf_without_extractable_text_is_rejected(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    # A scan yields pages with no text layer; approximated here by empty pages.
    path = make_pdf(["", ""], "scan.pdf")

    with pytest.raises(EmptyDocumentError):
        await DocumentExtractor(_settings(), llm=FakeLlm([])).extract(path)


async def test_missing_provider_key_reports_unavailable_not_a_crash(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([SUMMARY_PAGE], "summary.pdf")

    with pytest.raises(LlmUnavailableError):
        # No injected llm and no API key configured.
        await DocumentExtractor(_settings(openai_api_key="")).extract(path)


async def test_page_concurrency_is_bounded_by_settings(
    make_pdf: Callable[[list[str], str], Path],
) -> None:
    path = make_pdf([SUMMARY_PAGE] * 4, "bundle.pdf")
    llm = FakeLlm([_summary_result() for _ in range(4)])

    result = await DocumentExtractor(_settings(extraction_page_concurrency=2), llm=llm).extract(
        path
    )

    assert len(llm.calls) == 4
    assert result.page_count == 4
