"""Policy models.

Everything here is immutable (``frozen=True``). A policy that can be mutated
after a decision references it is not an audit trail - it is a story about one.
Changing a rule therefore means publishing a new :class:`PolicyVersion`, never
editing an existing one.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas import CamelModel


class Operator(str, Enum):
    """Comparison vocabulary available to rule authors.

    Deliberately tiny: every operator has one obvious semantic, so a rule reads
    the same way to an engineer, an underwriter and an auditor.
    """

    GT = "GT"
    LT = "LT"
    EQ = "EQ"
    CONTAINS = "CONTAINS"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class RecommendedAction(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    DECLINE = "DECLINE"


#: Severity weights feeding the 0-100 risk score. INFO is 0 on purpose: an
#: observation (including "this field was missing") must not by itself look like
#: risk, though it does block auto-approval elsewhere.
SEVERITY_WEIGHTS: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.WARNING: 25,
    Severity.CRITICAL: 60,
}


class FrozenCamelModel(CamelModel):
    model_config = ConfigDict(
        alias_generator=CamelModel.model_config["alias_generator"],
        populate_by_name=True,
        frozen=True,
    )


class PolicyRule(FrozenCamelModel):
    """A single deterministic check.

    ``field`` names a *fact*, not a document field: facts include derived values
    such as ``dti`` and ``ltv`` (see :mod:`app.policy.facts`). Keeping the
    derivation out of the rule means the rule stays a plain comparison, which is
    what makes the engine reproducible and explainable.
    """

    id: str = Field(min_length=1, description="Stable identifier, referenced by flags.")
    field: str = Field(min_length=1)
    operator: Operator
    #: Numeric for GT/LT, string for CONTAINS, either for EQ.
    threshold: Decimal | str
    severity: Severity
    #: Inverts the comparison, so "employment status is not full-time" stays a
    #: single rule instead of forcing a NOT_CONTAINS operator into the vocabulary.
    negate: bool = False
    #: Rendered with the evaluated facts, e.g.
    #: "DTI of {dti:.1%} exceeds the {threshold:.1%} limit".
    message_template: str = Field(min_length=1)
    #: Why this rule exists, for the audit file and the UI tooltip.
    rationale: str = ""

    @model_validator(mode="after")
    def _check_operator_and_threshold_agree(self) -> "PolicyRule":
        if self.operator in {Operator.GT, Operator.LT} and not isinstance(self.threshold, Decimal):
            raise ValueError(
                f"Rule '{self.id}': {self.operator.value} requires a numeric threshold"
            )
        if self.operator is Operator.CONTAINS and not isinstance(self.threshold, str):
            raise ValueError(f"Rule '{self.id}': CONTAINS requires a string threshold")
        return self


class PolicyVersion(FrozenCamelModel):
    """An immutable, ordered set of rules published under one version string."""

    version: str = Field(min_length=1, description="Semantic version, e.g. '2025.07.1'.")
    name: str
    description: str = ""
    effective_from: datetime
    rules: tuple[PolicyRule, ...]

    #: Score at or above which the engine stops recommending auto-approval.
    manual_review_score: int = Field(default=25, ge=0, le=100)

    @model_validator(mode="after")
    def _check_rule_ids_unique(self) -> "PolicyVersion":
        ids = [rule.id for rule in self.rules]
        duplicates = {rule_id for rule_id in ids if ids.count(rule_id) > 1}
        if duplicates:
            raise ValueError(f"Duplicate rule ids in policy {self.version}: {sorted(duplicates)}")
        return self


class LoanRequest(CamelModel):
    """The terms being asked for, as opposed to what the documents say."""

    loan_amount: Decimal = Field(gt=0)
    property_value: Decimal | None = Field(default=None, gt=0)
    term_months: int | None = Field(default=None, gt=0)


class ExtractedFinancials(CamelModel):
    """The subset of extracted data the policy layer consumes.

    A narrow model rather than a free-form dict: the engine's inputs are part of
    the audit record, so they must be typed and validated, and a renamed
    extraction field should fail here instead of silently evaluating to "missing".
    """

    annual_income: Decimal | None = Field(default=None, ge=0)
    monthly_debt_payments: Decimal | None = Field(default=None, ge=0)
    credit_score: int | None = Field(default=None, ge=300, le=850)
    employment_status: str | None = None
    employer_name: str | None = None
    applicant_name: str | None = None


class EvaluationFlag(FrozenCamelModel):
    """One triggered rule, carrying enough to reconstruct the decision."""

    rule_id: str
    field: str
    severity: Severity
    message: str
    #: The fact value compared, as a string so 0.43 and "fullTime" share a shape.
    observed_value: str | None
    threshold: str
    operator: Operator


class PolicyEvaluation(CamelModel):
    """The audit record returned by ``POST /api/v1/evaluate``."""

    passed: bool
    flags: list[EvaluationFlag]
    overall_risk_score: int = Field(ge=0, le=100)
    recommended_action: RecommendedAction

    policy_version: str
    #: SHA-256 over the canonical rule set. Two evaluations quoting the same
    #: version but different checksums prove the policy was tampered with.
    policy_checksum: str
    #: SHA-256 over the canonical inputs, so a replay can be verified as a replay.
    input_fingerprint: str

    #: Derived facts the rules were evaluated against (dti, ltv, ...). Recording
    #: them means a reviewer never has to recompute the arithmetic by hand.
    facts: dict[str, str]
    #: Facts that could not be computed; these block AUTO_APPROVE.
    missing_facts: list[str]

    engine_version: Literal["1"] = "1"
