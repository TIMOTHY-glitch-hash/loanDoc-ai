"""Fact derivation.

Rules compare *facts*, and facts are computed here - once, in one place, with
`Decimal` arithmetic. Two consequences that matter for the audit story:

* A rule is a plain comparison, so it can be published as data (and stored in the
  Convex ``policies`` table) without shipping code.
* The arithmetic is reviewable on its own. "How was this DTI calculated?" has one
  answer, not one per rule.

Every derivation returns ``None`` when an input is absent. A missing fact is
never silently coerced to zero: zero income is a decision, missing income is an
absence of information, and the engine treats them differently.
"""

from decimal import Decimal
from typing import Final

from app.policy.models import ExtractedFinancials, LoanRequest

#: Fact names rules may reference. Rules naming anything else are rejected at
#: publish time, so a typo cannot quietly disable a check.
FACT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "annualIncome",
        "monthlyDebtPayments",
        "creditScore",
        "employmentStatus",
        "loanAmount",
        "propertyValue",
        "dti",
        "ltv",
        "incomeToLoanRatio",
    }
)

_MONTHS_PER_YEAR = Decimal(12)
#: Ratios are quantised so the same inputs always produce byte-identical output.
_RATIO_PRECISION = Decimal("0.0001")


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(_RATIO_PRECISION)


def derive_facts(
    financials: ExtractedFinancials, request: LoanRequest
) -> tuple[dict[str, Decimal | str], list[str]]:
    """Return ``(facts, missing_fact_names)``.

    ``missing`` lists only facts a rule might reasonably need; the engine turns
    each into an INFO flag rather than guessing.
    """
    facts: dict[str, Decimal | str] = {
        "loanAmount": request.loan_amount,
    }
    missing: list[str] = []

    if request.property_value is not None:
        facts["propertyValue"] = request.property_value
    else:
        missing.append("propertyValue")

    if financials.annual_income is not None:
        facts["annualIncome"] = financials.annual_income
    else:
        missing.append("annualIncome")

    if financials.monthly_debt_payments is not None:
        facts["monthlyDebtPayments"] = financials.monthly_debt_payments
    else:
        missing.append("monthlyDebtPayments")

    if financials.credit_score is not None:
        facts["creditScore"] = Decimal(financials.credit_score)
    else:
        missing.append("creditScore")

    if financials.employment_status:
        facts["employmentStatus"] = financials.employment_status
    else:
        missing.append("employmentStatus")

    # Debt-to-income on gross monthly income - the convention US underwriting
    # uses, and the one the 43% qualified-mortgage threshold is defined against.
    if financials.annual_income is not None and financials.monthly_debt_payments is not None:
        monthly_income = financials.annual_income / _MONTHS_PER_YEAR
        dti = _ratio(financials.monthly_debt_payments, monthly_income)
        if dti is None:
            missing.append("dti")
        else:
            facts["dti"] = dti
    else:
        missing.append("dti")

    # Loan-to-value: the lender's exposure if the collateral has to be sold.
    if request.property_value is not None:
        ltv = _ratio(request.loan_amount, request.property_value)
        if ltv is None:
            missing.append("ltv")
        else:
            facts["ltv"] = ltv
    else:
        missing.append("ltv")

    # Income as a multiple of the loan, so "income < 3x loan" is one comparison.
    if financials.annual_income is not None:
        ratio = _ratio(financials.annual_income, request.loan_amount)
        if ratio is None:
            missing.append("incomeToLoanRatio")
        else:
            facts["incomeToLoanRatio"] = ratio
    else:
        missing.append("incomeToLoanRatio")

    return facts, sorted(set(missing))
