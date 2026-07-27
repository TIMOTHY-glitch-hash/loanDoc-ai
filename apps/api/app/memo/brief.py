"""Underwriting brief construction.

The model is given a brief, not the raw payloads. Two reasons:

* **Hallucination surface.** The brief is the only place numbers come from, so
  "cite only these figures" is enforceable - anything the model cites that is not
  here is visibly invented.
* **Formatting.** Money and ratios are rendered once, here, in banking
  conventions. The model never formats a number, so it cannot round or restate
  one incorrectly.

Facts that were not captured are listed explicitly as *not evidenced*. Absence
stated is absence the memo can report; absence omitted is absence the model will
fill in.
"""

from decimal import Decimal

from app.policy.models import ExtractedFinancials, LoanRequest, PolicyEvaluation

_NOT_EVIDENCED = "not evidenced in the submitted documents"


def _money(value: Decimal | None) -> str:
    if value is None:
        return _NOT_EVIDENCED
    return f"${value.quantize(Decimal('1')):,}"


def build_brief(
    financials: ExtractedFinancials,
    request: LoanRequest,
    evaluation: PolicyEvaluation,
) -> str:
    """Render the deterministic brief handed to the model."""
    lines: list[str] = [
        "Applicant and request",
        f"- Applicant: {financials.applicant_name or _NOT_EVIDENCED}",
        f"- Employer: {financials.employer_name or _NOT_EVIDENCED}",
        f"- Employment status: {financials.employment_status or _NOT_EVIDENCED}",
        f"- Loan amount requested: {_money(request.loan_amount)}",
        f"- Property value: {_money(request.property_value)}",
        (
            f"- Term: {request.term_months} months"
            if request.term_months is not None
            else f"- Term: {_NOT_EVIDENCED}"
        ),
        "",
        "Financial figures",
        f"- Annual income: {_money(financials.annual_income)}",
        f"- Monthly debt payments: {_money(financials.monthly_debt_payments)}",
        (
            f"- Credit score: {financials.credit_score}"
            if financials.credit_score is not None
            else f"- Credit score: {_NOT_EVIDENCED}"
        ),
    ]

    lines.append("")
    lines.append("Derived ratios (already calculated - do not recompute)")
    for name, value in evaluation.facts.items():
        lines.append(f"- {name}: {value}")

    if evaluation.missing_facts:
        lines.append("")
        lines.append("Figures that could not be derived (report as not evidenced)")
        lines.extend(f"- {name}" for name in evaluation.missing_facts)

    lines.append("")
    lines.append(
        f"Policy evaluation (policy {evaluation.policy_version}, "
        f"risk score {evaluation.overall_risk_score}/100)"
    )
    lines.append(f"- Recommended action: {evaluation.recommended_action.value}")
    lines.append(f"- All evaluated rules passed: {'yes' if evaluation.passed else 'no'}")

    if evaluation.flags:
        lines.append("- Rules raised:")
        for flag in evaluation.flags:
            lines.append(
                f"  - [{flag.severity.value}] {flag.rule_id} on {flag.field}: {flag.message}"
            )
    else:
        lines.append("- Rules raised: none")

    return "\n".join(lines)
