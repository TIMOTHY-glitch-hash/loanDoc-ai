"""Policy versioning.

The registry is an append-only map of version string to :class:`PolicyVersion`.
Publishing a rule change means adding an entry; existing entries are never
edited, so an evaluation stored six months ago can still be replayed against the
exact rules that produced it. That replay is what "auditable" means here - not
that we logged a decision, but that we can reproduce it.

``checksum`` is a SHA-256 over the canonical serialisation of the rule set. The
version string is a human label and could in principle be reused by mistake; the
checksum cannot, so a stored evaluation whose checksum no longer matches the
registry is provable tampering rather than a difference of opinion.
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Final

from app.policy.facts import FACT_NAMES
from app.policy.models import Operator, PolicyRule, PolicyVersion, Severity


class UnknownPolicyVersionError(LookupError):
    """Raised when an evaluation asks for a version that was never published."""

    def __init__(self, version: str, known: list[str]) -> None:
        super().__init__(f"Unknown policy version '{version}'. Published versions: {known}")
        self.version = version
        self.known = known


def checksum(policy: PolicyVersion) -> str:
    """Stable digest of a policy's rules.

    ``sort_keys`` plus ``mode="json"`` make the encoding canonical, so the digest
    depends on rule content only - not on field declaration order or on the
    Python version doing the hashing.
    """
    payload = [rule.model_dump(mode="json", by_alias=True) for rule in policy.rules]
    canonical = json.dumps(
        {"version": policy.version, "rules": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- Published policies ------------------------------------------------------
# Ordered oldest first. Treat these as immutable history: to change a threshold,
# append a new PolicyVersion rather than editing one below.

#: The initial policy, kept in the registry purely so historical evaluations that
#: reference it remain replayable. Its DTI limit was later tightened to 43%.
POLICY_2025_01 = PolicyVersion(
    version="2025.01.0",
    name="Baseline residential underwriting",
    description="First published rule set. Superseded by 2025.07.1 (DTI tightened to 43%).",
    effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
    rules=(
        PolicyRule(
            id="DTI_LIMIT",
            field="dti",
            operator=Operator.GT,
            threshold=Decimal("0.50"),
            severity=Severity.CRITICAL,
            message_template="Debt-to-income ratio {observed} exceeds the {threshold} limit.",
            rationale="Pre-2025 internal limit.",
        ),
        PolicyRule(
            id="CREDIT_SCORE_FLOOR",
            field="creditScore",
            operator=Operator.LT,
            threshold=Decimal(620),
            severity=Severity.CRITICAL,
            message_template="Credit score {observed} is below the {threshold} floor.",
            rationale="Sub-620 scores fall outside the conforming credit box.",
        ),
    ),
)

POLICY_2025_07 = PolicyVersion(
    version="2025.07.1",
    name="Residential underwriting",
    description=(
        "Qualified-mortgage aligned rule set: 43% DTI ceiling, 620 credit floor, "
        "90% LTV and 3x income coverage warnings."
    ),
    effective_from=datetime(2025, 7, 1, tzinfo=timezone.utc),
    manual_review_score=25,
    rules=(
        PolicyRule(
            id="DTI_LIMIT",
            field="dti",
            operator=Operator.GT,
            threshold=Decimal("0.43"),
            severity=Severity.CRITICAL,
            message_template="Debt-to-income ratio {observed} exceeds the {threshold} limit.",
            rationale=(
                "43% is the qualified-mortgage ceiling; above it the loan loses QM "
                "safe-harbour treatment."
            ),
        ),
        PolicyRule(
            id="INCOME_COVERAGE",
            field="incomeToLoanRatio",
            operator=Operator.LT,
            threshold=Decimal(3),
            severity=Severity.WARNING,
            message_template=(
                "Annual income covers only {observed}x the requested amount "
                "(minimum {threshold}x)."
            ),
            rationale="Thin income coverage leaves no buffer for a rate or expense shock.",
        ),
        PolicyRule(
            id="EMPLOYMENT_STABILITY",
            field="employmentStatus",
            operator=Operator.CONTAINS,
            threshold="full",
            negate=True,
            severity=Severity.WARNING,
            message_template="Employment status '{observed}' is not full-time.",
            rationale=(
                "Non-full-time income needs a longer history and manual verification; "
                "substring match so 'fullTime' and 'Full-Time salaried' both pass."
            ),
        ),
        PolicyRule(
            id="CREDIT_SCORE_FLOOR",
            field="creditScore",
            operator=Operator.LT,
            threshold=Decimal(620),
            severity=Severity.CRITICAL,
            message_template="Credit score {observed} is below the {threshold} floor.",
            rationale="Sub-620 scores fall outside the conforming credit box.",
        ),
        PolicyRule(
            id="HIGH_LTV",
            field="ltv",
            operator=Operator.GT,
            threshold=Decimal("0.90"),
            severity=Severity.WARNING,
            message_template="Loan-to-value {observed} exceeds {threshold}.",
            rationale="Above 90% LTV the collateral no longer covers a downturn plus costs.",
        ),
    ),
)

_POLICIES: Final[tuple[PolicyVersion, ...]] = (POLICY_2025_01, POLICY_2025_07)

#: The version used when a caller does not name one. Newest published policy.
ACTIVE_POLICY_VERSION: Final[str] = POLICY_2025_07.version

_BY_VERSION: Final[dict[str, PolicyVersion]] = {policy.version: policy for policy in _POLICIES}


def _validate_registry() -> None:
    """Fail at import time on a malformed policy.

    A rule referencing an unknown fact would never fire, which is the most
    dangerous failure mode available: a check that looks present and does nothing.
    """
    for policy in _POLICIES:
        for rule in policy.rules:
            if rule.field not in FACT_NAMES:
                raise ValueError(
                    f"Policy {policy.version} rule '{rule.id}' references unknown fact "
                    f"'{rule.field}'. Known facts: {sorted(FACT_NAMES)}"
                )


_validate_registry()


def list_policies() -> list[PolicyVersion]:
    """All published policies, oldest first."""
    return list(_POLICIES)


def get_policy(version: str | None = None) -> PolicyVersion:
    """Look up a policy, defaulting to the active one."""
    resolved = version or ACTIVE_POLICY_VERSION
    try:
        return _BY_VERSION[resolved]
    except KeyError as exc:
        raise UnknownPolicyVersionError(resolved, sorted(_BY_VERSION)) from exc
