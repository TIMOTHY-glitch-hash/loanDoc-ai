"""Contract tests for POST /api/v1/evaluate and GET /api/v1/policies."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.policy.registry import ACTIVE_POLICY_VERSION


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "extractedData": {
            "annualIncome": "180000",
            "monthlyDebtPayments": "2000",
            "creditScore": 760,
            "employmentStatus": "fullTime",
            "applicantName": "Dana Whitfield",
        },
        "loanRequest": {"loanAmount": "50000", "propertyValue": "400000"},
    }
    body.update(overrides)
    return body


def test_clean_file_returns_auto_approve(client: TestClient) -> None:
    response = client.post("/api/v1/evaluate", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["recommendedAction"] == "AUTO_APPROVE"
    assert body["overallRiskScore"] == 0
    assert body["policyVersion"] == ACTIVE_POLICY_VERSION
    # The audit triple: which rules, over which inputs, producing this outcome.
    assert len(body["policyChecksum"]) == 64
    assert len(body["inputFingerprint"]) == 64


def test_flags_are_camel_case_and_carry_provenance(client: TestClient) -> None:
    response = client.post(
        "/api/v1/evaluate",
        json=_payload(
            extractedData={
                "annualIncome": "60000",
                "monthlyDebtPayments": "2500",
                "creditScore": 590,
                "employmentStatus": "selfEmployed",
            },
            loanRequest={"loanAmount": "390000", "propertyValue": "400000"},
        ),
    )

    body = response.json()
    assert body["recommendedAction"] == "DECLINE"
    assert body["passed"] is False
    flag = body["flags"][0]
    assert set(flag) == {
        "ruleId",
        "field",
        "severity",
        "message",
        "observedValue",
        "threshold",
        "operator",
    }
    assert flag["severity"] == "CRITICAL"


def test_response_is_reproducible_across_requests(client: TestClient) -> None:
    first = client.post("/api/v1/evaluate", json=_payload()).json()
    second = client.post("/api/v1/evaluate", json=_payload()).json()

    assert first == second


def test_pinned_policy_version_replays_under_the_old_thresholds(client: TestClient) -> None:
    # 47% DTI: declined by 2025.07.1, allowed by 2025.01.0.
    payload = _payload(
        extractedData={
            "annualIncome": "60000",
            "monthlyDebtPayments": "2350",
            "creditScore": 760,
            "employmentStatus": "fullTime",
        },
        loanRequest={"loanAmount": "15000", "propertyValue": "400000"},
    )

    current = client.post("/api/v1/evaluate", json=payload).json()
    historical = client.post(
        "/api/v1/evaluate", json={**payload, "policyVersion": "2025.01.0"}
    ).json()

    assert current["recommendedAction"] == "DECLINE"
    assert historical["policyVersion"] == "2025.01.0"
    assert "DTI_LIMIT" not in [flag["ruleId"] for flag in historical["flags"]]
    assert current["inputFingerprint"] == historical["inputFingerprint"]
    assert current["policyChecksum"] != historical["policyChecksum"]


def test_unknown_policy_version_returns_422(client: TestClient) -> None:
    response = client.post("/api/v1/evaluate", json=_payload(policyVersion="1999.12.31"))

    assert response.status_code == 422
    assert "Unknown policy version" in response.json()["detail"]


def test_invalid_loan_amount_is_rejected_by_the_schema(client: TestClient) -> None:
    response = client.post("/api/v1/evaluate", json=_payload(loanRequest={"loanAmount": "0"}))

    assert response.status_code == 422


def test_out_of_range_credit_score_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/evaluate",
        json=_payload(extractedData={"creditScore": 1200}),
    )

    assert response.status_code == 422


def test_policies_endpoint_lists_versions_with_their_rules(client: TestClient) -> None:
    response = client.get("/api/v1/policies")

    assert response.status_code == 200
    versions = [policy["version"] for policy in response.json()]
    assert versions == ["2025.01.0", "2025.07.1"]
    active = response.json()[-1]
    assert {rule["id"] for rule in active["rules"]} == {
        "DTI_LIMIT",
        "INCOME_COVERAGE",
        "EMPLOYMENT_STABILITY",
        "CREDIT_SCORE_FLOOR",
        "HIGH_LTV",
    }
    assert active["rules"][0]["messageTemplate"]


def test_routes_are_documented_in_openapi(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/v1/evaluate" in paths
    assert "/api/v1/policies" in paths
