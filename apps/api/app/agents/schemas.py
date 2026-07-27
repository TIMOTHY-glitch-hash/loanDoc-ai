"""Pydantic models for the extraction agent.

Two families of models live here:

* ``PageExtraction`` and its parts are the *LLM-facing* schema. They are handed
  to ``with_structured_output``, so their field names and docstrings are part of
  the prompt - wording changes here change model behaviour.
* ``DocumentExtraction`` is the *API-facing* result after pages are merged. It
  inherits :class:`app.schemas.CamelModel`, so it serialises as camelCase like
  the rest of the wire contract.
"""

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema
from pydantic.alias_generators import to_camel

from app.schemas import CamelModel

#: A ``Decimal`` field, but advertised to the provider as a plain string.
#:
#: Pydantic's own JSON schema for ``Decimal`` carries a regex with a lookahead
#: (``^(?!^[-+.]*$)...``), and constrained-decoding backends behind OpenAI-compatible
#: gateways reject lookaround outright - the request fails with "Unsupported
#: structured output regex" before the model ever runs. Dropping to a plain string
#: in the *advertised* schema keeps structured output portable while validation
#: still parses the figure into an exact ``Decimal``.


def _normalise_number(value: object) -> object:
    """Strip grouping and currency characters before decimal parsing.

    Models emit "$102,480.00" no matter how firmly the prompt says not to, and
    rejecting a correctly-read figure over its punctuation throws away a whole
    page of extraction. Only grouping/currency characters are removed, so genuine
    nonsense still fails validation.
    """
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "").replace("\u00a0", "")
        # Accounting negatives: "(1,200.00)" means -1200.00.
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        return cleaned
    return value


LlmDecimal = Annotated[
    Decimal,
    BeforeValidator(_normalise_number),
    WithJsonSchema({"type": "string", "description": 'A number, e.g. "84500.00".'}),
]

#: Truncation limit for evidence snippets. Long enough to prove where a value
#: came from, short enough that we are not echoing whole pages of PII back.
MAX_EVIDENCE_CHARS = 240


class LoanDocumentType(str, Enum):
    """Document taxonomy the extractor is asked to choose from."""

    W2 = "W2"
    PAY_STUB = "payStub"
    TAX_RETURN = "taxReturn"
    BANK_STATEMENT = "bankStatement"
    ID = "ID"
    UNKNOWN = "unknown"


class EmploymentStatus(str, Enum):
    FULL_TIME = "fullTime"
    PART_TIME = "partTime"
    SELF_EMPLOYED = "selfEmployed"
    RETIRED = "retired"
    UNEMPLOYED = "unemployed"
    UNKNOWN = "unknown"


class LlmModel(BaseModel):
    """Base for LLM-facing schemas: plain snake_case, no aliasing."""

    model_config = ConfigDict(extra="ignore")


class TextEvidence(LlmModel):
    """A value the model claims to have found, with the proof it was asked for."""

    value: str = Field(description="The extracted value, verbatim from the document.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How certain you are, 0-1. Use below 0.5 when the value is inferred "
            "rather than printed on the page."
        ),
    )
    raw_text: str = Field(
        description="The exact sentence or line from the page that contains this value.",
    )


class NumericEvidence(LlmModel):
    """Same contract as :class:`TextEvidence` for monetary/numeric fields.

    The value is a ``Decimal`` because loan amounts must not be subject to binary
    float rounding; ``LlmDecimal`` normalises "$84,500.00" style output into one.
    """

    value: LlmDecimal = Field(description="The numeric value, without currency symbols or commas.")
    confidence: float = Field(ge=0.0, le=1.0, description="How certain you are, 0-1.")
    raw_text: str = Field(description="The exact line from the page containing this value.")


class PageExtraction(LlmModel):
    """Structured output requested from the model for a *single* page.

    Every field is optional: a pay stub has no property value, and forcing the
    model to emit one is how hallucinations get invited. Absent means "not on
    this page", which the merge step treats very differently from a low
    confidence value.
    """

    document_type: LoanDocumentType = Field(
        description="Which kind of loan document this page belongs to.",
    )
    document_type_confidence: float = Field(
        ge=0.0, le=1.0, description="Certainty in the document_type classification, 0-1."
    )

    applicant_name: TextEvidence | None = None
    annual_income: NumericEvidence | None = None
    monthly_debt_payments: NumericEvidence | None = None
    employment_status: TextEvidence | None = None
    employer_name: TextEvidence | None = None
    loan_amount_requested: NumericEvidence | None = None
    property_value: NumericEvidence | None = None
    credit_score: NumericEvidence | None = None


# --- API-facing models -------------------------------------------------------


class PiiKind(str, Enum):
    SSN = "ssn"
    ACCOUNT_NUMBER = "accountNumber"
    DATE_OF_BIRTH = "dateOfBirth"


class PiiFinding(CamelModel):
    """A PII hit. Deliberately carries no raw value - only where it was found."""

    kind: PiiKind
    page: int = Field(ge=1)
    occurrences: int = Field(ge=1)
    #: Masked sample, e.g. '***-**-6789', safe to show an operator.
    masked_sample: str


class ExtractedValue(CamelModel):
    """A merged field value plus its provenance."""

    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    page: int = Field(ge=1)
    #: Evidence snippet, truncated and PII-redacted before it leaves the service.
    raw_text: str


class PageOutcome(CamelModel):
    """Per-page audit row: which pages succeeded, and why any failed."""

    page: int = Field(ge=1)
    status: Literal["extracted", "empty", "failed"]
    detected_type: LoanDocumentType | None = None
    error: str | None = None


class DocumentExtraction(CamelModel):
    """The merged result returned by ``POST /api/v1/extract``."""

    file_name: str
    document_type: LoanDocumentType
    document_type_confidence: float = Field(ge=0.0, le=1.0)
    page_count: int = Field(ge=0)

    applicant_name: ExtractedValue | None = None
    annual_income: ExtractedValue | None = None
    monthly_debt_payments: ExtractedValue | None = None
    employment_status: EmploymentStatus | None = None
    employment_status_evidence: ExtractedValue | None = None
    employer_name: ExtractedValue | None = None
    loan_amount_requested: ExtractedValue | None = None
    property_value: ExtractedValue | None = None
    credit_score: ExtractedValue | None = None

    #: Empty when nothing sensitive was detected; non-empty triggers a logged
    #: privacy notice on the server side.
    pii_findings: list[PiiFinding] = Field(default_factory=list)
    privacy_notice: str | None = None

    pages: list[PageOutcome] = Field(default_factory=list)
    #: Non-fatal problems (a failed page, a page over the size limit, ...).
    warnings: list[str] = Field(default_factory=list)

    model: str
    latency_ms: int = Field(ge=0)


class ExtractionRequestMetadata(CamelModel):
    """Optional multipart form fields accompanying the upload."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    application_id: str | None = None
    #: Set false to skip the LLM and return page text statistics only.
    run_llm: bool = True
