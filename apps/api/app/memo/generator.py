"""Underwriting memo generation.

Position in the flow::

    /extract (LLM)  ->  /evaluate (deterministic)  ->  /generate-memo (LLM)

The memo is the *last* step and it is explanatory only: the decision was already
taken by :class:`app.policy.engine.PolicyEngine`. This ordering is the point -
prose is generated from a recorded decision, never the other way round, so the
memo cannot quietly become the decision.
"""

import logging
import time
from typing import Protocol, cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.config import Settings
from app.memo.brief import build_brief
from app.memo.errors import MemoRejectedError, MemoUnavailableError
from app.memo.models import (
    GenerateMemoRequest,
    MemoSections,
    MemoSource,
    UnderwritingMemo,
)
from app.memo.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.policy.models import PolicyEvaluation, RecommendedAction, Severity

logger = logging.getLogger(__name__)


class MemoLlm(Protocol):
    """The slice of the LangChain runnable used here (see agents.extractor)."""

    async def ainvoke(self, input: list[SystemMessage | HumanMessage], /) -> MemoSections: ...


def render_markdown(sections: MemoSections) -> str:
    """Render the five sections as the memo stored on the application.

    Markdown rather than HTML: it renders in the review UI, in a PDF export and
    in a plain-text audit file without further processing.
    """
    blocks = [
        "## Executive Summary",
        sections.executive_summary,
        "## Financial Profile",
        sections.financial_profile,
        "## Risk Factors",
        sections.risk_factors,
        "## Recommendation",
        sections.recommendation,
    ]

    if sections.conditions:
        blocks.append("## Conditions")
        blocks.extend(f"- {condition}" for condition in sections.conditions)

    return "\n\n".join(blocks) + "\n"


def _template_sections(payload: GenerateMemoRequest) -> MemoSections:
    """Deterministic memo assembled from the evaluation alone.

    Used only when the caller explicitly opts in via ``allowTemplateFallback``.
    It is intentionally plain: its job is to be honest and reproducible when no
    model is available, not to imitate one.
    """
    evaluation = payload.policy_evaluation
    applicant = payload.extracted_data.applicant_name or "The applicant"
    amount = payload.loan_request.loan_amount

    triggered = [flag for flag in evaluation.flags if flag.severity is not Severity.INFO]
    critical = [flag for flag in triggered if flag.severity is Severity.CRITICAL]

    if triggered:
        risk = "The following policy rules were raised: " + "; ".join(
            f"{flag.rule_id} ({flag.severity.value}) - {flag.message}" for flag in triggered
        )
    else:
        risk = (
            f"No policy rules were raised under policy {evaluation.policy_version}. "
            f"The composite risk score is {evaluation.overall_risk_score} of 100."
        )

    if evaluation.missing_facts:
        risk += (
            " The following figures were not evidenced and could not be assessed: "
            + ", ".join(evaluation.missing_facts)
            + "."
        )

    conditions: list[str] = [
        f"Evidence {name} before further underwriting." for name in evaluation.missing_facts
    ]
    conditions.extend(f"Resolve {flag.rule_id}: {flag.message}" for flag in critical)

    return MemoSections(
        executive_summary=(
            f"{applicant} has requested ${amount:,.0f}. "
            f"Policy {evaluation.policy_version} returns a recommended action of "
            f"{evaluation.recommended_action.value} with a risk score of "
            f"{evaluation.overall_risk_score} of 100."
        ),
        financial_profile=(
            "Derived underwriting ratios: "
            + ", ".join(f"{name} {value}" for name, value in evaluation.facts.items())
            + "."
        ),
        risk_factors=risk,
        recommendation=(
            f"Recommended action is {evaluation.recommended_action.value}, per policy "
            f"{evaluation.policy_version} (checksum {evaluation.policy_checksum[:12]}). "
            "This memo was generated deterministically from the policy evaluation "
            "without model narrative."
        ),
        conditions=conditions,
    )


def _check_consistent_with_policy(sections: MemoSections, evaluation: PolicyEvaluation) -> None:
    """Reject a memo that contradicts the decision it is supposed to explain.

    A model that writes "approval is recommended" over a DECLINE would be worse
    than no memo at all, so this is a hard failure rather than a warning.
    """
    prose = " ".join(
        [sections.executive_summary, sections.recommendation, sections.risk_factors]
    ).casefold()

    contradictions: dict[RecommendedAction, tuple[str, ...]] = {
        RecommendedAction.DECLINE: ("recommend approval", "recommended for approval"),
        RecommendedAction.MANUAL_REVIEW: ("recommend approval", "recommend decline"),
        RecommendedAction.AUTO_APPROVE: ("recommend decline", "recommended for decline"),
    }

    for phrase in contradictions[evaluation.recommended_action]:
        if phrase in prose:
            raise MemoRejectedError(
                f"Memo contradicts the policy recommendation "
                f"({evaluation.recommended_action.value}): contains '{phrase}'."
            )


class MemoGenerator:
    """Generates an underwriting memo from extraction + policy evaluation.

    ``llm`` is injectable so the whole path (brief, prompt, validation, rendering)
    is testable without a provider key.
    """

    def __init__(self, settings: Settings, llm: MemoLlm | None = None) -> None:
        self._settings = settings
        self._llm = llm

    async def generate(self, payload: GenerateMemoRequest) -> UnderwritingMemo:
        started = time.perf_counter()
        evaluation = payload.policy_evaluation
        warnings: list[str] = []

        brief = build_brief(payload.extracted_data, payload.loan_request, evaluation)

        try:
            sections = await self._generate_sections(payload, brief)
            source = MemoSource.LLM
            model: str | None = self._settings.openai_model
        except MemoUnavailableError:
            if not payload.allow_template_fallback:
                raise
            sections = _template_sections(payload)
            source = MemoSource.TEMPLATE
            model = None
            warnings.append(
                "No LLM was available; memo was assembled deterministically from the "
                "policy evaluation."
            )

        if source is MemoSource.LLM:
            _check_consistent_with_policy(sections, evaluation)

        return UnderwritingMemo(
            application_id=payload.application_id,
            sections=sections,
            markdown=render_markdown(sections),
            source=source,
            model=model,
            policy_version=evaluation.policy_version,
            policy_checksum=evaluation.policy_checksum,
            recommended_action=evaluation.recommended_action.value,
            overall_risk_score=evaluation.overall_risk_score,
            persisted=False,
            warnings=warnings,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _generate_sections(self, payload: GenerateMemoRequest, brief: str) -> MemoSections:
        llm = self._resolve_llm()

        messages: list[SystemMessage | HumanMessage] = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=USER_PROMPT_TEMPLATE.format(
                    application_id=payload.application_id, brief=brief
                )
            ),
        ]

        try:
            return await llm.ainvoke(messages)
        except ValidationError as exc:
            # The tone/structure validators rejected the output. Surfaced as a
            # distinct error so the caller can retry rather than store filler.
            raise MemoRejectedError(f"Model output failed the memo contract: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - provider errors are opaque
            logger.warning("Memo generation failed: %s", exc)
            raise MemoUnavailableError(f"Memo model call failed: {exc}") from exc

    def _resolve_llm(self) -> MemoLlm:
        if self._llm is not None:
            return self._llm

        if not self._settings.agent_enabled:
            raise MemoUnavailableError(
                "No OPENAI_API_KEY configured; set one to generate model-written memos."
            )

        # Imported lazily so the module (and the rest of the API) loads without the
        # provider SDK present or configured.
        from app.llm import build_chat_model, structured_output_kwargs

        # Slightly above zero: readable prose, still tightly constrained by the
        # brief and the tone validators.
        model = build_chat_model(self._settings, temperature=self._settings.memo_temperature)
        self._llm = cast(
            MemoLlm,
            model.with_structured_output(MemoSections, **structured_output_kwargs(self._settings)),
        )
        return self._llm
