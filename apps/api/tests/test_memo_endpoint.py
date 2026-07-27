"""Contract tests for POST /api/v1/generate-memo, including Convex persistence."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.memo.errors import MemoPersistenceError
from app.memo.generator import MemoGenerator
from app.memo.models import UnderwritingMemo
from app.memo.store import SAVE_MEMO_MUTATION, ConvexMemoStore
from app.policy.engine import PolicyEngine
from app.policy.models import ExtractedFinancials, LoanRequest
from app.routers.memo import get_memo_generator, get_memo_store
from tests.test_memo_generator import _CLEAN_SECTIONS, FakeLlm


class FakeStore:
    """Records what would have been written to Convex."""

    def __init__(self, enabled: bool = True, error: str | None = None) -> None:
        self._enabled = enabled
        self._error = error
        self.saved: list[tuple[UnderwritingMemo, list[str]]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def save(self, memo: UnderwritingMemo, risk_flags: list[str]) -> None:
        if self._error is not None:
            raise MemoPersistenceError(self._error)
        self.saved.append((memo, risk_flags))


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def client(store: FakeStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_memo_generator] = lambda: MemoGenerator(
        Settings(openai_api_key="test-key"), llm=FakeLlm()
    )
    app.dependency_overrides[get_memo_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _payload(**overrides: Any) -> dict[str, Any]:
    financials = ExtractedFinancials(
        annual_income=Decimal("180000"),
        monthly_debt_payments=Decimal("2000"),
        credit_score=760,
        employment_status="fullTime",
        applicant_name="Dana Whitfield",
    )
    request = LoanRequest(loan_amount=Decimal("50000"), property_value=Decimal("400000"))
    evaluation = PolicyEngine().evaluate(financials, request)

    body: dict[str, Any] = {
        "applicationId": "app_123",
        "extractedData": financials.model_dump(mode="json", by_alias=True),
        "loanRequest": request.model_dump(mode="json", by_alias=True),
        "policyEvaluation": evaluation.model_dump(mode="json", by_alias=True),
    }
    body.update(overrides)
    return body


def test_memo_is_generated_and_persisted(client: TestClient, store: FakeStore) -> None:
    response = client.post("/api/v1/generate-memo", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is True
    assert body["source"] == "LLM"
    assert body["policyVersion"] == "2025.07.1"
    assert body["recommendedAction"] == "AUTO_APPROVE"
    assert set(body["sections"]) == {
        "executiveSummary",
        "financialProfile",
        "riskFactors",
        "recommendation",
        "conditions",
    }
    assert body["markdown"].startswith("## Executive Summary")
    assert len(store.saved) == 1


def test_risk_flags_written_alongside_the_memo_reference_rule_ids(
    client: TestClient, store: FakeStore
) -> None:
    financials = ExtractedFinancials(
        annual_income=Decimal("180000"),
        monthly_debt_payments=Decimal("2000"),
        credit_score=580,
        employment_status="selfEmployed",
    )
    request = LoanRequest(loan_amount=Decimal("50000"), property_value=Decimal("400000"))
    evaluation = PolicyEngine().evaluate(financials, request)

    response = client.post(
        "/api/v1/generate-memo",
        json=_payload(
            extractedData=financials.model_dump(mode="json", by_alias=True),
            policyEvaluation=evaluation.model_dump(mode="json", by_alias=True),
        ),
    )

    assert response.status_code == 200
    _, risk_flags = store.saved[0]
    assert "CRITICAL:CREDIT_SCORE_FLOOR" in risk_flags
    assert "WARNING:EMPLOYMENT_STABILITY" in risk_flags


def test_persist_false_skips_the_write(client: TestClient, store: FakeStore) -> None:
    response = client.post("/api/v1/generate-memo", json=_payload(persist=False))

    assert response.json()["persisted"] is False
    assert store.saved == []


def test_unconfigured_convex_returns_the_memo_with_a_warning() -> None:
    app.dependency_overrides[get_memo_generator] = lambda: MemoGenerator(
        Settings(openai_api_key="test-key"), llm=FakeLlm()
    )
    app.dependency_overrides[get_memo_store] = lambda: FakeStore(enabled=False)

    with TestClient(app) as test_client:
        response = test_client.post("/api/v1/generate-memo", json=_payload())

    app.dependency_overrides.clear()

    # Generation succeeded; discarding the memo over a missing URL would be worse.
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is False
    assert "not persisted" in " ".join(body["warnings"])


def test_persistence_failure_does_not_discard_the_memo() -> None:
    app.dependency_overrides[get_memo_generator] = lambda: MemoGenerator(
        Settings(openai_api_key="test-key"), llm=FakeLlm()
    )
    app.dependency_overrides[get_memo_store] = lambda: FakeStore(error="Application not found")

    with TestClient(app) as test_client:
        response = test_client.post("/api/v1/generate-memo", json=_payload())

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert "Application not found" in " ".join(response.json()["warnings"])


def test_missing_provider_key_returns_503() -> None:
    app.dependency_overrides[get_memo_generator] = lambda: MemoGenerator(
        Settings(openai_api_key="")
    )
    app.dependency_overrides[get_memo_store] = lambda: FakeStore()

    with TestClient(app) as test_client:
        response = test_client.post("/api/v1/generate-memo", json=_payload())

    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]


def test_contradictory_memo_returns_502() -> None:
    sections = _CLEAN_SECTIONS.model_copy(
        update={"recommendation": "We recommend approval regardless."}
    )
    app.dependency_overrides[get_memo_generator] = lambda: MemoGenerator(
        Settings(openai_api_key="test-key"), llm=FakeLlm(sections)
    )
    app.dependency_overrides[get_memo_store] = lambda: FakeStore()

    financials = ExtractedFinancials(credit_score=580, annual_income=Decimal("180000"))
    request = LoanRequest(loan_amount=Decimal("50000"), property_value=Decimal("400000"))
    evaluation = PolicyEngine().evaluate(financials, request)

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/generate-memo",
            json=_payload(
                extractedData=financials.model_dump(mode="json", by_alias=True),
                policyEvaluation=evaluation.model_dump(mode="json", by_alias=True),
            ),
        )

    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "contradicts" in response.json()["detail"]


def test_missing_application_id_is_rejected(client: TestClient) -> None:
    payload = _payload()
    del payload["applicationId"]

    assert client.post("/api/v1/generate-memo", json=payload).status_code == 422


def test_route_is_documented_in_openapi(client: TestClient) -> None:
    assert "/api/v1/generate-memo" in client.get("/openapi.json").json()["paths"]


# --- Convex HTTP client ------------------------------------------------------


def _memo() -> UnderwritingMemo:
    return UnderwritingMemo(
        application_id="app_123",
        sections=_CLEAN_SECTIONS,
        markdown="## Executive Summary\n\nMemo body.\n",
        source="LLM",  # type: ignore[arg-type]
        policy_version="2025.07.1",
        policy_checksum="a" * 64,
        recommended_action="AUTO_APPROVE",
        overall_risk_score=0,
        latency_ms=12,
    )


@pytest.mark.asyncio
async def test_convex_store_posts_the_mutation_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"status": "success", "value": None})

    transport = httpx.MockTransport(handler)
    settings = Settings(convex_url="https://example.convex.cloud/")

    async with httpx.AsyncClient(transport=transport) as http_client:
        await ConvexMemoStore(settings, client=http_client).save(_memo(), ["WARNING:HIGH_LTV"])

    assert seen["url"] == "https://example.convex.cloud/api/mutation"
    assert SAVE_MEMO_MUTATION in seen["body"]
    assert "WARNING:HIGH_LTV" in seen["body"]
    assert "2025.07.1" in seen["body"]


@pytest.mark.asyncio
async def test_convex_error_status_in_a_200_response_is_still_a_failure() -> None:
    # Convex reports application errors with HTTP 200; trusting the status code
    # alone would silently drop failed writes.
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"status": "error", "errorMessage": "Application app_123 not found"}
        )
    )
    settings = Settings(convex_url="https://example.convex.cloud")

    async with httpx.AsyncClient(transport=transport) as http_client:
        with pytest.raises(MemoPersistenceError, match="not found"):
            await ConvexMemoStore(settings, client=http_client).save(_memo(), [])


@pytest.mark.asyncio
async def test_convex_transport_error_becomes_a_persistence_error() -> None:
    transport = httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ConnectError("x")))
    settings = Settings(convex_url="https://example.convex.cloud")

    async with httpx.AsyncClient(transport=transport) as http_client:
        with pytest.raises(MemoPersistenceError, match="Convex request failed"):
            await ConvexMemoStore(settings, client=http_client).save(_memo(), [])


def test_convex_store_is_disabled_without_a_url() -> None:
    assert ConvexMemoStore(Settings(convex_url="")).enabled is False
