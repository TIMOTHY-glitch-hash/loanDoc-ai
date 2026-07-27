"""The deterministic decision layer.

Contract, and the reason this layer exists at all: **same inputs + same policy
version = same outputs, always.** No clock, no randomness, no LLM, no I/O, no
floats. Every evaluation records the policy checksum and an input fingerprint, so
a decision taken today can be replayed and verified years later.

The LLM's job ends at extraction. Deciding is done here, in code an auditor can
read.
"""

import hashlib
import json
from decimal import Decimal
from typing import Final

from app.policy.facts import derive_facts
from app.policy.models import (
    SEVERITY_WEIGHTS,
    EvaluationFlag,
    ExtractedFinancials,
    LoanRequest,
    Operator,
    PolicyEvaluation,
    PolicyRule,
    PolicyVersion,
    RecommendedAction,
    Severity,
)
from app.policy.registry import checksum, get_policy

#: Facts that read naturally as percentages in messages.
_PERCENT_FACTS: Final[frozenset[str]] = frozenset({"dti", "ltv"})

#: Highest severity first when ordering flags; ties fall back to policy rule order
#: so the output is a total order, never dependent on dict iteration.
_SEVERITY_RANK: Final[dict[Severity, int]] = {
    Severity.CRITICAL: 0,
    Severity.WARNING: 1,
    Severity.INFO: 2,
}


def _format_fact(name: str, value: Decimal | str) -> str:
    """Render a fact for human-readable messages, deterministically."""
    if isinstance(value, str):
        return value
    if name in _PERCENT_FACTS:
        # Quantised before formatting so rounding never depends on repr.
        return f"{(value * 100).quantize(Decimal('0.1'))}%"
    return format(value.normalize(), "f")


def _compare(rule: PolicyRule, value: Decimal | str) -> bool | None:
    """Apply one rule to one fact.

    Returns ``None`` when the comparison is not defined for the value's type -
    e.g. a numeric threshold against a text fact. That is a policy authoring
    error, and reporting it as "not evaluated" is safer than defaulting to
    "passed": a silently skipped check is how bad loans get approved.
    """
    if rule.operator is Operator.CONTAINS:
        if not isinstance(value, str) or not isinstance(rule.threshold, str):
            return None
        outcome = rule.threshold.casefold() in value.casefold()
    elif rule.operator is Operator.EQ:
        if isinstance(value, str) != isinstance(rule.threshold, str):
            return None
        if isinstance(value, str) and isinstance(rule.threshold, str):
            outcome = value.casefold() == rule.threshold.casefold()
        else:
            outcome = value == rule.threshold
    else:
        if isinstance(value, str) or isinstance(rule.threshold, str):
            return None
        outcome = value > rule.threshold if rule.operator is Operator.GT else value < rule.threshold

    return not outcome if rule.negate else outcome


def _fingerprint(financials: ExtractedFinancials, request: LoanRequest) -> str:
    """Canonical digest of the inputs, so a replay is provably a replay."""
    canonical = json.dumps(
        {
            "financials": financials.model_dump(mode="json", by_alias=True),
            "request": request.model_dump(mode="json", by_alias=True),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class PolicyEngine:
    """Evaluates a loan request against a published, versioned policy.

    Stateless by design: it holds only the policy it was constructed with, so two
    engines on the same version cannot drift apart.
    """

    def __init__(self, policy: PolicyVersion | None = None, version: str | None = None) -> None:
        self._policy = policy if policy is not None else get_policy(version)
        self._checksum = checksum(self._policy)

    @property
    def policy(self) -> PolicyVersion:
        return self._policy

    def evaluate(self, financials: ExtractedFinancials, request: LoanRequest) -> PolicyEvaluation:
        facts, missing_facts = derive_facts(financials, request)

        flags: list[tuple[int, int, EvaluationFlag]] = []
        risk = 0

        for index, rule in enumerate(self._policy.rules):
            value = facts.get(rule.field)

            if value is None:
                # The rule could not run. Recorded as INFO with zero weight: it is
                # an information gap, not evidence of risk - but it does block
                # auto-approval below, because we cannot claim the check passed.
                flags.append(
                    (
                        _SEVERITY_RANK[Severity.INFO],
                        index,
                        EvaluationFlag(
                            rule_id=rule.id,
                            field=rule.field,
                            severity=Severity.INFO,
                            message=(
                                f"'{rule.field}' was not available, so rule "
                                f"{rule.id} could not be evaluated."
                            ),
                            observed_value=None,
                            threshold=_format_fact(rule.field, rule.threshold),
                            operator=rule.operator,
                        ),
                    )
                )
                continue

            triggered = _compare(rule, value)

            if triggered is None:
                flags.append(
                    (
                        _SEVERITY_RANK[Severity.INFO],
                        index,
                        EvaluationFlag(
                            rule_id=rule.id,
                            field=rule.field,
                            severity=Severity.INFO,
                            message=(
                                f"Rule {rule.id} is not applicable to the value of "
                                f"'{rule.field}' and was not evaluated."
                            ),
                            observed_value=_format_fact(rule.field, value),
                            threshold=_format_fact(rule.field, rule.threshold),
                            operator=rule.operator,
                        ),
                    )
                )
                continue

            if not triggered:
                continue

            observed = _format_fact(rule.field, value)
            threshold = _format_fact(rule.field, rule.threshold)
            flags.append(
                (
                    _SEVERITY_RANK[rule.severity],
                    index,
                    EvaluationFlag(
                        rule_id=rule.id,
                        field=rule.field,
                        severity=rule.severity,
                        message=rule.message_template.format(
                            observed=observed, threshold=threshold
                        ),
                        observed_value=observed,
                        threshold=threshold,
                        operator=rule.operator,
                    ),
                )
            )
            risk += SEVERITY_WEIGHTS[rule.severity]

        ordered = [flag for _, _, flag in sorted(flags, key=lambda item: (item[0], item[1]))]
        score = min(risk, 100)

        severities = {flag.severity for flag in ordered}
        has_critical = Severity.CRITICAL in severities
        has_warning = Severity.WARNING in severities
        # INFO is only ever emitted for a rule that could not run (missing fact or
        # a rule not applicable to the value). Either way we cannot claim the check
        # passed, so it must not auto-approve.
        has_unevaluated_rule = Severity.INFO in severities

        # "Passed" means every rule that ran was satisfied; information gaps do
        # not fail the application, they route it to a human.
        passed = not has_critical and not has_warning

        if has_critical:
            action = RecommendedAction.DECLINE
        elif (
            has_warning
            or has_unevaluated_rule
            or missing_facts
            or score >= self._policy.manual_review_score
        ):
            action = RecommendedAction.MANUAL_REVIEW
        else:
            action = RecommendedAction.AUTO_APPROVE

        return PolicyEvaluation(
            passed=passed,
            flags=ordered,
            overall_risk_score=score,
            recommended_action=action,
            policy_version=self._policy.version,
            policy_checksum=self._checksum,
            input_fingerprint=_fingerprint(financials, request),
            facts={name: _format_fact(name, value) for name, value in sorted(facts.items())},
            missing_facts=missing_facts,
        )
