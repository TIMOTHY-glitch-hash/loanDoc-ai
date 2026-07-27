"""PolicyEngine unit tests.

Organised around the guarantees the engine sells: each default rule fires exactly
when it should, the recommendation follows from severities, and evaluation is
reproducible against a pinned policy version.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.policy.engine import PolicyEngine
from app.policy.models import (
    ExtractedFinancials,
    LoanRequest,
    Operator,
    PolicyRule,
    PolicyVersion,
    RecommendedAction,
    Severity,
)
from app.policy.registry import (
    ACTIVE_POLICY_VERSION,
    POLICY_2025_01,
    POLICY_2025_07,
    UnknownPolicyVersionError,
    checksum,
    get_policy,
    list_policies,
)


def _clean_financials(**overrides: object) -> ExtractedFinancials:
    """A file that passes every rule, so each test perturbs exactly one thing."""
    defaults: dict[str, object] = {
        "annual_income": Decimal("180000"),
        "monthly_debt_payments": Decimal("2000"),
        "credit_score": 760,
        "employment_status": "fullTime",
        "employer_name": "Acme Manufacturing",
        "applicant_name": "Dana Whitfield",
    }
    defaults.update(overrides)
    return ExtractedFinancials.model_validate(defaults)


def _clean_request(**overrides: object) -> LoanRequest:
    defaults: dict[str, object] = {
        "loan_amount": Decimal("50000"),
        "property_value": Decimal("400000"),
    }
    defaults.update(overrides)
    return LoanRequest.model_validate(defaults)


def _flag_ids(evaluation: object) -> list[str]:
    return [flag.rule_id for flag in evaluation.flags]  # type: ignore[attr-defined]


def test_clean_file_auto_approves_with_zero_risk() -> None:
    result = PolicyEngine().evaluate(_clean_financials(), _clean_request())

    assert result.passed is True
    assert result.flags == []
    assert result.overall_risk_score == 0
    assert result.recommended_action is RecommendedAction.AUTO_APPROVE
    assert result.missing_facts == []


def test_dti_above_43_percent_is_critical_and_declines() -> None:
    # 60k income -> 5k/month; 2.5k debt = 50% DTI.
    result = PolicyEngine().evaluate(
        _clean_financials(annual_income=Decimal("60000"), monthly_debt_payments=Decimal("2500")),
        _clean_request(loan_amount=Decimal("15000")),
    )

    dti_flag = next(flag for flag in result.flags if flag.rule_id == "DTI_LIMIT")
    assert dti_flag.severity is Severity.CRITICAL
    assert dti_flag.observed_value == "50.0%"
    assert dti_flag.threshold == "43.0%"
    assert result.passed is False
    assert result.recommended_action is RecommendedAction.DECLINE


def test_dti_exactly_at_threshold_does_not_fire() -> None:
    # 43% of a 10k monthly income is 4,300 - GT is strict, so the limit itself passes.
    result = PolicyEngine().evaluate(
        _clean_financials(annual_income=Decimal("120000"), monthly_debt_payments=Decimal("4300")),
        _clean_request(loan_amount=Decimal("30000")),
    )

    assert "DTI_LIMIT" not in _flag_ids(result)


def test_income_below_three_times_loan_is_a_warning() -> None:
    result = PolicyEngine().evaluate(
        _clean_financials(annual_income=Decimal("180000")),
        _clean_request(loan_amount=Decimal("100000")),
    )

    flag = next(flag for flag in result.flags if flag.rule_id == "INCOME_COVERAGE")
    assert flag.severity is Severity.WARNING
    assert flag.observed_value == "1.8"
    assert result.recommended_action is RecommendedAction.MANUAL_REVIEW


@pytest.mark.parametrize("status", ["fullTime", "Full-Time salaried", "FULL TIME"])
def test_full_time_variants_all_satisfy_the_employment_rule(status: str) -> None:
    result = PolicyEngine().evaluate(_clean_financials(employment_status=status), _clean_request())

    assert "EMPLOYMENT_STABILITY" not in _flag_ids(result)


@pytest.mark.parametrize("status", ["partTime", "selfEmployed", "retired"])
def test_non_full_time_employment_warns(status: str) -> None:
    result = PolicyEngine().evaluate(_clean_financials(employment_status=status), _clean_request())

    flag = next(flag for flag in result.flags if flag.rule_id == "EMPLOYMENT_STABILITY")
    assert flag.severity is Severity.WARNING
    assert result.recommended_action is RecommendedAction.MANUAL_REVIEW


def test_credit_score_below_620_is_critical() -> None:
    result = PolicyEngine().evaluate(_clean_financials(credit_score=610), _clean_request())

    flag = next(flag for flag in result.flags if flag.rule_id == "CREDIT_SCORE_FLOOR")
    assert flag.severity is Severity.CRITICAL
    assert flag.observed_value == "610"
    assert result.recommended_action is RecommendedAction.DECLINE


def test_high_ltv_above_90_percent_warns() -> None:
    result = PolicyEngine().evaluate(
        _clean_financials(annual_income=Decimal("2000000")),
        _clean_request(loan_amount=Decimal("380000"), property_value=Decimal("400000")),
    )

    flag = next(flag for flag in result.flags if flag.rule_id == "HIGH_LTV")
    assert flag.severity is Severity.WARNING
    assert flag.observed_value == "95.0%"


def test_risk_score_accumulates_and_caps_at_100() -> None:
    result = PolicyEngine().evaluate(
        _clean_financials(
            annual_income=Decimal("60000"),
            monthly_debt_payments=Decimal("3000"),
            credit_score=580,
            employment_status="partTime",
        ),
        _clean_request(loan_amount=Decimal("390000"), property_value=Decimal("400000")),
    )

    # Two CRITICAL (60 each) + three WARNING (25 each) exceeds 100 and is clamped.
    assert result.overall_risk_score == 100
    assert result.recommended_action is RecommendedAction.DECLINE


def test_critical_outranks_warning_in_the_recommendation() -> None:
    result = PolicyEngine().evaluate(
        _clean_financials(credit_score=600, employment_status="partTime"), _clean_request()
    )

    assert {flag.severity for flag in result.flags} == {Severity.CRITICAL, Severity.WARNING}
    # Ordering is severity-first so a reviewer reads the blocker before the nits.
    assert result.flags[0].severity is Severity.CRITICAL
    assert result.recommended_action is RecommendedAction.DECLINE


def test_missing_data_blocks_auto_approval_without_inflating_risk() -> None:
    result = PolicyEngine().evaluate(_clean_financials(credit_score=None), _clean_request())

    flag = next(flag for flag in result.flags if flag.rule_id == "CREDIT_SCORE_FLOOR")
    assert flag.severity is Severity.INFO
    assert flag.observed_value is None
    # An information gap must not read as evidence of risk...
    assert result.overall_risk_score == 0
    # ...but it must never be auto-approved either.
    assert result.recommended_action is RecommendedAction.MANUAL_REVIEW
    assert "creditScore" in result.missing_facts
    # Rules that *did* run all passed.
    assert result.passed is True


def test_absent_property_value_reports_ltv_as_missing() -> None:
    result = PolicyEngine().evaluate(_clean_financials(), _clean_request(property_value=None))

    assert "ltv" in result.missing_facts
    assert result.recommended_action is RecommendedAction.MANUAL_REVIEW


def test_facts_are_recorded_so_the_arithmetic_need_not_be_redone() -> None:
    result = PolicyEngine().evaluate(_clean_financials(), _clean_request())

    assert result.facts["dti"] == "13.3%"
    assert result.facts["ltv"] == "12.5%"
    assert result.facts["incomeToLoanRatio"] == "3.6"
    assert result.facts["employmentStatus"] == "fullTime"


# --- Reproducibility and versioning -----------------------------------------


def test_same_inputs_and_version_produce_identical_output() -> None:
    financials, request = _clean_financials(credit_score=600), _clean_request()

    first = PolicyEngine(version="2025.07.1").evaluate(financials, request)
    second = PolicyEngine(version="2025.07.1").evaluate(financials, request)

    # Byte-for-byte, including flag order and the fingerprints.
    assert first.model_dump_json() == second.model_dump_json()


def test_different_inputs_produce_different_fingerprints() -> None:
    engine = PolicyEngine()
    baseline = engine.evaluate(_clean_financials(), _clean_request())
    changed = engine.evaluate(_clean_financials(credit_score=759), _clean_request())

    assert baseline.input_fingerprint != changed.input_fingerprint


def test_old_policy_version_still_evaluates_under_its_own_thresholds() -> None:
    # 47% DTI: over the 43% ceiling of 2025.07.1, under the 50% of 2025.01.0.
    financials = _clean_financials(
        annual_income=Decimal("60000"), monthly_debt_payments=Decimal("2350")
    )
    request = _clean_request(loan_amount=Decimal("15000"))

    old = PolicyEngine(policy=POLICY_2025_01).evaluate(financials, request)
    new = PolicyEngine(policy=POLICY_2025_07).evaluate(financials, request)

    assert "DTI_LIMIT" not in _flag_ids(old)
    assert "DTI_LIMIT" in _flag_ids(new)
    # This is the audit guarantee: a decision made in January is still explainable
    # in July, under January's rules.
    assert old.policy_version == "2025.01.0"
    assert old.policy_checksum != new.policy_checksum


def test_checksum_changes_when_a_threshold_changes() -> None:
    tightened = POLICY_2025_07.model_copy(
        update={
            "rules": tuple(
                rule.model_copy(update={"threshold": Decimal("0.40")})
                if rule.id == "DTI_LIMIT"
                else rule
                for rule in POLICY_2025_07.rules
            )
        }
    )

    assert checksum(tightened) != checksum(POLICY_2025_07)


def test_checksum_is_stable_across_calls() -> None:
    assert checksum(POLICY_2025_07) == checksum(POLICY_2025_07)


def test_active_version_is_the_newest_published_policy() -> None:
    published = list_policies()

    assert published[-1].version == ACTIVE_POLICY_VERSION
    assert get_policy().version == ACTIVE_POLICY_VERSION


def test_unknown_version_is_rejected_rather_than_defaulted() -> None:
    with pytest.raises(UnknownPolicyVersionError):
        get_policy("1999.12.31")


def test_published_policies_are_immutable() -> None:
    with pytest.raises(ValueError, match="frozen"):
        POLICY_2025_07.rules[0].threshold = Decimal("0.99")


# --- Rule authoring guardrails ----------------------------------------------


def test_numeric_operator_with_text_threshold_is_rejected_at_definition() -> None:
    with pytest.raises(ValueError, match="requires a numeric threshold"):
        PolicyRule(
            id="BAD",
            field="dti",
            operator=Operator.GT,
            threshold="high",
            severity=Severity.WARNING,
            message_template="{observed}",
        )


def test_duplicate_rule_ids_are_rejected() -> None:
    rule = POLICY_2025_07.rules[0]

    with pytest.raises(ValueError, match="Duplicate rule ids"):
        PolicyVersion(
            version="test",
            name="test",
            effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            rules=(rule, rule),
        )


def test_rule_not_applicable_to_a_fact_is_reported_not_skipped() -> None:
    # CONTAINS against a numeric fact cannot be evaluated; silently treating that
    # as "passed" would be a check that looks present and does nothing.
    policy = PolicyVersion(
        version="test",
        name="test",
        effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        rules=(
            PolicyRule(
                id="MISAPPLIED",
                field="dti",
                operator=Operator.CONTAINS,
                threshold="0.4",
                severity=Severity.CRITICAL,
                message_template="{observed}",
            ),
        ),
    )

    result = PolicyEngine(policy=policy).evaluate(_clean_financials(), _clean_request())

    assert result.flags[0].severity is Severity.INFO
    assert "not applicable" in result.flags[0].message
    assert result.recommended_action is RecommendedAction.MANUAL_REVIEW
