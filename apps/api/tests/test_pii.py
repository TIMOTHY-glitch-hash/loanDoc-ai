from app.agents import pii
from app.agents.schemas import PiiKind


def test_detects_ssn_account_number_and_dob() -> None:
    text = (
        "Employee SSN: 123-45-6789\n" "Account Number: 000123456789\n" "Date of Birth: 04/17/1984\n"
    )

    kinds = {finding.kind for finding in pii.scan_page(1, text)}

    assert kinds == {PiiKind.SSN, PiiKind.ACCOUNT_NUMBER, PiiKind.DATE_OF_BIRTH}


def test_findings_never_carry_the_raw_value() -> None:
    findings = pii.scan_page(2, "SSN 123-45-6789")

    assert findings[0].masked_sample == "*****6789"
    assert "123" not in findings[0].masked_sample.replace("*", "")


def test_unlabelled_dates_and_numbers_are_not_flagged() -> None:
    # Pay-period dates and gross-pay figures appear on every pay stub; flagging
    # them would make the notice meaningless.
    text = "Pay period ending 03/15/2025. Gross pay 4,250.00. Check number 10029384."

    assert pii.scan_page(1, text) == []


def test_redact_removes_sensitive_spans_from_evidence() -> None:
    redacted = pii.redact("Employee SSN: 123-45-6789 earned 4,250.00")

    assert "123-45-6789" not in redacted
    assert "4,250.00" in redacted


def test_privacy_notice_lists_kinds_and_pages() -> None:
    findings = pii.scan_page(3, "SSN 123-45-6789")
    notice = pii.build_privacy_notice(findings)

    assert notice is not None
    assert "ssn" in notice
    assert "3" in notice


def test_no_findings_means_no_notice() -> None:
    assert pii.build_privacy_notice([]) is None
