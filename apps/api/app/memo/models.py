"""Memo models.

``MemoSections`` is the LLM-facing schema: asking for named sections rather than
one blob of prose means the required structure is enforced by the parser instead
of hoped for in the prompt, and the UI can render Risk Factors next to the flags
that produced them.
"""

from enum import Enum
from typing import Literal

from pydantic import ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.policy.models import ExtractedFinancials, LoanRequest, PolicyEvaluation
from app.schemas import CamelModel

#: Phrases that betray a chatbot rather than a credit file. Rejected after
#: generation, because prompt instructions are a request and validation is a
#: guarantee.
_CONVERSATIONAL_MARKERS: tuple[str, ...] = (
    "as an ai",
    "i hope this helps",
    "let me know",
    "feel free to",
    "certainly!",
    "sure!",
    "here's the memo",
    "here is the memo",
)


class MemoSource(str, Enum):
    """How a memo was produced. Recorded so a reader never has to guess."""

    LLM = "LLM"
    #: Deterministic fallback assembled from the policy evaluation only.
    TEMPLATE = "TEMPLATE"


class MemoSections(CamelModel):
    """The five required sections, as requested from the model.

    Doubles as the LLM-facing schema (field names and descriptions are part of the
    prompt) and as the API-facing shape, so it carries camelCase aliases like the
    rest of the wire contract while still accepting snake_case internally.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    executive_summary: str = Field(
        min_length=1,
        description=("One paragraph: who is applying, for how much, and the recommended action."),
    )
    financial_profile: str = Field(
        min_length=1,
        description=(
            "One paragraph citing income, existing debt service, DTI, LTV and credit "
            "score as supplied. Name any figure that was not evidenced."
        ),
    )
    risk_factors: str = Field(
        min_length=1,
        description=(
            "One paragraph attributing each risk to the policy rule that raised it. "
            "State explicitly when no rules were triggered."
        ),
    )
    recommendation: str = Field(
        min_length=1,
        description="The recommended action and the reasoning that supports it.",
    )
    conditions: list[str] = Field(
        default_factory=list,
        description=(
            "Specific, verifiable actions required before funding. Empty when the " "file is clean."
        ),
    )

    @field_validator("executive_summary", "financial_profile", "risk_factors", "recommendation")
    @classmethod
    def _reject_conversational_tone(cls, value: str) -> str:
        lowered = value.casefold()
        for marker in _CONVERSATIONAL_MARKERS:
            if marker in lowered:
                raise ValueError(f"Memo prose contains conversational filler: '{marker}'")
        return value.strip()

    @field_validator("conditions")
    @classmethod
    def _strip_conditions(cls, value: list[str]) -> list[str]:
        return [condition.strip() for condition in value if condition.strip()]


class UnderwritingMemo(CamelModel):
    """The memo plus the provenance needed to defend it."""

    application_id: str
    sections: MemoSections
    #: Markdown rendering of ``sections``; what gets stored on the application.
    markdown: str

    source: MemoSource
    model: str | None = None
    #: Copied from the evaluation: a memo detached from the rule set that produced
    #: it is a narrative, not a decision record.
    policy_version: str
    policy_checksum: str
    recommended_action: str
    overall_risk_score: int = Field(ge=0, le=100)

    persisted: bool = False
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)

    memo_format_version: Literal["1"] = "1"


class GenerateMemoRequest(CamelModel):
    """Inputs to memo generation.

    The policy evaluation is supplied rather than recomputed so the memo provably
    describes the decision that was actually taken and recorded.
    """

    application_id: str = Field(min_length=1)
    extracted_data: ExtractedFinancials
    loan_request: LoanRequest
    policy_evaluation: PolicyEvaluation

    #: Write the memo back to the Convex ``loanApplications`` row.
    persist: bool = True
    #: Opt in to the deterministic template when no LLM is configured. Off by
    #: default: a memo silently written by a fallback would misrepresent itself.
    allow_template_fallback: bool = False
