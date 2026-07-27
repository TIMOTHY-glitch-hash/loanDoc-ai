"""Provider wiring: OpenAI-compatible gateways and figure normalisation."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agents.schemas import NumericEvidence, PageExtraction
from app.config import Settings
from app.llm import build_chat_model, structured_output_kwargs


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"openai_api_key": "sk-test"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_default_provider_is_openai_direct() -> None:
    model = build_chat_model(_settings(), temperature=0)

    assert model.openai_api_base is None
    assert structured_output_kwargs(_settings()) == {"method": "function_calling"}


def test_base_url_routes_to_gateway_and_identifies_the_caller() -> None:
    settings = _settings(
        openai_base_url="https://openrouter.ai/api/v1",
        openai_model="nvidia/nemotron-3-super-120b-a12b:free",
        llm_structured_output_method="json_schema",
    )

    model = build_chat_model(settings, temperature=0)

    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    # OpenRouter attributes usage by these headers; without them the free tier is
    # rate-limited against an anonymous bucket.
    assert model.default_headers is not None
    assert "X-Title" in model.default_headers
    assert model.model_name == "nvidia/nemotron-3-super-120b-a12b:free"
    assert structured_output_kwargs(settings) == {"method": "json_schema"}


def test_non_openrouter_gateway_gets_no_openrouter_headers() -> None:
    model = build_chat_model(_settings(openai_base_url="http://localhost:8001/v1"), temperature=0)

    assert model.openai_api_base == "http://localhost:8001/v1"
    assert not model.default_headers


def test_advertised_schema_avoids_lookaround_regex() -> None:
    """Guided-decoding backends reject lookaround, so it must not be advertised.

    Pydantic's stock ``Decimal`` schema carries ``^(?!^[-+.]*$)...``, which makes
    gateway-hosted models fail the request outright rather than answer it.
    """
    schema = repr(PageExtraction.model_json_schema())

    assert "(?!" not in schema


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("102,480.00", Decimal("102480.00")),
        ("$84,500", Decimal("84500")),
        (" 1200.50 ", Decimal("1200.50")),
        ("(1,200.00)", Decimal("-1200.00")),
    ],
)
def test_money_is_normalised_not_rejected(raw: str, expected: Decimal) -> None:
    """A correctly-read figure must not be lost to its punctuation."""
    evidence = NumericEvidence(value=raw, confidence=0.9, raw_text="Box 1: " + raw)  # type: ignore[arg-type]

    assert evidence.value == expected


def test_normalisation_does_not_accept_nonsense() -> None:
    with pytest.raises(ValidationError):
        NumericEvidence(value="approximately sixty thousand", confidence=0.9, raw_text="x")  # type: ignore[arg-type]
