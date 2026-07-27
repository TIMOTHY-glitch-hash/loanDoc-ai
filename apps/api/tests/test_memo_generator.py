"""MemoGenerator tests, driven by a faked LLM.

The generator's value is in what it refuses to do: cite figures it was not given,
contradict the policy decision, or emit chatbot prose. Those are the assertions
worth having.
"""

from decimal import Decimal
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.config import Settings
from app.memo.brief import build_brief
from app.memo.errors import MemoRejectedError, MemoUnavailableError
from app.memo.generator import MemoGenerator, render_markdown
from app.memo.models import GenerateMemoRequest, MemoSections, MemoSource
from app.memo.prompts import SYSTEM_PROMPT
from app.policy.engine import PolicyEngine
from app.policy.models import ExtractedFinancials, LoanRequest, RecommendedAction

pytestmark = pytest.mark.asyncio


def _financials(**overrides: Any) -> ExtractedFinancials:
    defaults: dict[str, Any] = {
        "annual_income": Decimal("180000"),
        "monthly_debt_payments": Decimal("2000"),
        "credit_score": 760,
        "employment_status": "fullTime",
        "employer_name": "Acme Manufacturing",
        "applicant_name": "Dana Whitfield",
    }
    defaults.update(overrides)
    return ExtractedFinancials.model_validate(defaults)


def _request(**overrides: Any) -> LoanRequest:
    defaults: dict[str, Any] = {
        "loan_amount": Decimal("50000"),
        "property_value": Decimal("400000"),
    }
    defaults.update(overrides)
    return LoanRequest.model_validate(defaults)


def _payload(
    financials: ExtractedFinancials | None = None,
    request: LoanRequest | None = None,
    **overrides: Any,
) -> GenerateMemoRequest:
    financials = financials or _financials()
    request = request or _request()
    body: dict[str, Any] = {
        "application_id": "app_123",
        "extracted_data": financials,
        "loan_request": request,
        "policy_evaluation": PolicyEngine().evaluate(financials, request),
        "persist": False,
    }
    body.update(overrides)
    return GenerateMemoRequest.model_validate(body)


_CLEAN_SECTIONS = MemoSections(
    executive_summary=(
        "Dana Whitfield seeks $50,000 secured against a property valued at $400,000. "
        "Policy 2025.07.1 returns AUTO_APPROVE."
    ),
    financial_profile=(
        "Annual income of $180,000 against monthly debt service of $2,000 yields a "
        "debt-to-income ratio of 13.3%. Loan-to-value is 12.5% and the credit score is 760."
    ),
    risk_factors="No policy rules were raised. The composite risk score is 0 of 100.",
    recommendation="Approval is recommended in line with the policy determination.",
    conditions=[],
)


class FakeLlm:
    """Records the messages it was asked to answer, then returns a fixture."""

    def __init__(self, sections: MemoSections | Exception = _CLEAN_SECTIONS) -> None:
        self._sections = sections
        self.calls: list[list[SystemMessage | HumanMessage]] = []

    async def ainvoke(self, input: list[SystemMessage | HumanMessage], /) -> MemoSections:
        self.calls.append(input)
        if isinstance(self._sections, Exception):
            raise self._sections
        return self._sections


def _generator(llm: Any, **settings_overrides: Any) -> MemoGenerator:
    return MemoGenerator(Settings(**settings_overrides), llm=llm)


async def test_memo_contains_all_five_sections_in_the_rendered_markdown() -> None:
    conditions = MemoSections(
        **{**_CLEAN_SECTIONS.model_dump(), "conditions": ["Obtain a current payoff statement."]}
    )
    memo = await _generator(FakeLlm(conditions)).generate(_payload())

    for heading in (
        "## Executive Summary",
        "## Financial Profile",
        "## Risk Factors",
        "## Recommendation",
        "## Conditions",
    ):
        assert heading in memo.markdown
    assert "- Obtain a current payoff statement." in memo.markdown


async def test_conditions_heading_is_omitted_when_the_file_is_clean() -> None:
    memo = await _generator(FakeLlm()).generate(_payload())

    assert "## Conditions" not in memo.markdown
    assert memo.sections.conditions == []


async def test_memo_carries_the_policy_provenance_of_the_decision() -> None:
    payload = _payload()
    memo = await _generator(FakeLlm()).generate(payload)

    assert memo.source is MemoSource.LLM
    assert memo.policy_version == payload.policy_evaluation.policy_version
    assert memo.policy_checksum == payload.policy_evaluation.policy_checksum
    assert memo.recommended_action == RecommendedAction.AUTO_APPROVE.value
    assert memo.application_id == "app_123"
    assert memo.persisted is False


async def test_the_underwriter_system_prompt_is_sent_verbatim() -> None:
    llm = FakeLlm()
    await _generator(llm).generate(_payload())

    system, human = llm.calls[0]
    assert system.content == SYSTEM_PROMPT
    assert "senior loan underwriter at a regional bank" in str(system.content)
    # The brief, and only the brief, supplies the figures.
    assert "UNDERWRITING BRIEF" in str(human.content)
    assert "app_123" in str(human.content)


async def test_prose_contradicting_a_decline_is_rejected_not_stored() -> None:
    declined = _payload(_financials(credit_score=580))
    assert declined.policy_evaluation.recommended_action is RecommendedAction.DECLINE

    contradiction = MemoSections(
        **{
            **_CLEAN_SECTIONS.model_dump(),
            "recommendation": "We recommend approval notwithstanding the credit score.",
        }
    )

    with pytest.raises(MemoRejectedError, match="contradicts the policy recommendation"):
        await _generator(FakeLlm(contradiction)).generate(declined)


async def test_conversational_filler_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValidationError, match="conversational filler"):
        MemoSections(
            **{
                **_CLEAN_SECTIONS.model_dump(),
                "executive_summary": "Sure! Here's the memo you asked for.",
            }
        )


async def test_provider_failure_surfaces_as_unavailable() -> None:
    with pytest.raises(MemoUnavailableError, match="Memo model call failed"):
        await _generator(FakeLlm(RuntimeError("502 upstream"))).generate(_payload())


async def test_without_a_key_generation_fails_rather_than_inventing_a_memo() -> None:
    with pytest.raises(MemoUnavailableError, match="No OPENAI_API_KEY"):
        await MemoGenerator(Settings(openai_api_key="")).generate(_payload())


async def test_template_fallback_is_opt_in_and_declares_itself() -> None:
    payload = _payload(allow_template_fallback=True)

    memo = await MemoGenerator(Settings(openai_api_key="")).generate(payload)

    assert memo.source is MemoSource.TEMPLATE
    assert memo.model is None
    assert "assembled deterministically" in " ".join(memo.warnings)
    assert "## Recommendation" in memo.markdown
    assert memo.recommended_action == RecommendedAction.AUTO_APPROVE.value


async def test_template_fallback_lists_missing_figures_as_conditions() -> None:
    payload = _payload(_financials(credit_score=None), allow_template_fallback=True, persist=False)

    memo = await MemoGenerator(Settings(openai_api_key="")).generate(payload)

    assert any("creditScore" in condition for condition in memo.sections.conditions)


# --- The brief: the only source of numbers the model may cite -----------------


def test_brief_supplies_the_figures_and_the_derived_ratios() -> None:
    financials = _financials()
    request = _request()
    brief = build_brief(financials, request, PolicyEngine().evaluate(financials, request))

    assert "Annual income: $180,000" in brief
    assert "Loan amount requested: $50,000" in brief
    assert "Credit score: 760" in brief
    assert "dti: 13.3%" in brief
    assert "Recommended action: AUTO_APPROVE" in brief
    assert "Rules raised: none" in brief


def test_brief_states_absent_figures_instead_of_omitting_them() -> None:
    # An omitted figure is one the model will invent; a stated absence is one it
    # can report.
    financials = _financials(annual_income=None, credit_score=None)
    request = _request(property_value=None)
    brief = build_brief(financials, request, PolicyEngine().evaluate(financials, request))

    assert "Annual income: not evidenced in the submitted documents" in brief
    assert "Credit score: not evidenced in the submitted documents" in brief
    assert "Figures that could not be derived" in brief
    assert "- ltv" in brief


def test_brief_attributes_each_risk_to_the_rule_that_raised_it() -> None:
    financials = _financials(credit_score=580, employment_status="selfEmployed")
    request = _request()
    brief = build_brief(financials, request, PolicyEngine().evaluate(financials, request))

    assert "[CRITICAL] CREDIT_SCORE_FLOOR" in brief
    assert "[WARNING] EMPLOYMENT_STABILITY" in brief


def test_markdown_rendering_is_stable() -> None:
    assert render_markdown(_CLEAN_SECTIONS) == render_markdown(_CLEAN_SECTIONS)
    assert render_markdown(_CLEAN_SECTIONS).endswith("\n")
