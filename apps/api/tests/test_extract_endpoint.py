"""Contract tests for POST /api/v1/extract.

The extractor dependency is overridden with a faked LLM so the HTTP surface -
multipart handling, camelCase JSON, status-code mapping - is asserted without a
provider.
"""

from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.extractor import DocumentExtractor
from app.agents.schemas import (
    LoanDocumentType,
    NumericEvidence,
    PageExtraction,
    TextEvidence,
)
from app.config import Settings
from app.main import app
from app.routers.extract import get_extractor
from tests.conftest import FakeLlm

PAGE_TEXT = """LOAN APPLICATION SUMMARY
Applicant: Dana Whitfield
Employee SSN: 123-45-6789
Annual income 102,000.00
"""


def _result() -> PageExtraction:
    return PageExtraction(
        document_type=LoanDocumentType.W2,
        document_type_confidence=0.9,
        applicant_name=TextEvidence(
            value="Dana Whitfield",
            confidence=0.97,
            raw_text="Applicant: Dana Whitfield, SSN 123-45-6789",
        ),
        annual_income=NumericEvidence(
            value=Decimal("102000"),
            confidence=0.91,
            raw_text="Annual income 102,000.00",
        ),
    )


@pytest.fixture
def client(request: pytest.FixtureRequest) -> Iterator[TestClient]:
    """Client whose extractor is wired to a scripted FakeLlm."""
    scripted: list[PageExtraction | Exception] = getattr(request, "param", [_result()])
    app.dependency_overrides[get_extractor] = lambda: DocumentExtractor(
        Settings(api_env="development"), llm=FakeLlm(scripted)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_returns_camel_case_structured_extraction(
    client: TestClient, make_pdf: Callable[[list[str], str], Path]
) -> None:
    path = make_pdf([PAGE_TEXT], "w2.pdf")

    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/extract",
            files={"file": ("w2.pdf", handle, "application/pdf")},
            data={"applicationId": "app_123"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["documentType"] == "W2"
    assert body["fileName"] == "w2.pdf"
    assert body["pageCount"] == 1
    assert body["applicantName"]["value"] == "Dana Whitfield"
    assert body["annualIncome"]["confidence"] == 0.91
    assert body["model"]
    assert body["latencyMs"] >= 0


def test_privacy_notice_is_surfaced_and_evidence_is_redacted(
    client: TestClient, make_pdf: Callable[[list[str], str], Path]
) -> None:
    path = make_pdf([PAGE_TEXT], "w2.pdf")

    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/extract", files={"file": ("w2.pdf", handle, "application/pdf")}
        )

    body = response.json()
    assert body["privacyNotice"] is not None
    assert body["piiFindings"][0]["kind"] == "ssn"
    assert "123-45-6789" not in response.text


def test_non_pdf_upload_returns_415(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("plain text")

    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/extract", files={"file": ("notes.txt", handle, "text/plain")}
        )

    assert response.status_code == 415


def test_empty_upload_returns_422(client: TestClient) -> None:
    response = client.post("/api/v1/extract", files={"file": ("empty.pdf", b"", "application/pdf")})

    assert response.status_code == 422


def test_scanned_pdf_without_text_returns_422(
    client: TestClient, make_pdf: Callable[[list[str], str], Path]
) -> None:
    path = make_pdf([""], "scan.pdf")

    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/extract", files={"file": ("scan.pdf", handle, "application/pdf")}
        )

    assert response.status_code == 422
    assert "OCR" in response.json()["detail"]


@pytest.mark.parametrize("client", [[RuntimeError("provider down")]], indirect=True)
def test_provider_failure_returns_503(
    client: TestClient, make_pdf: Callable[[list[str], str], Path]
) -> None:
    path = make_pdf([PAGE_TEXT], "w2.pdf")

    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/extract", files={"file": ("w2.pdf", handle, "application/pdf")}
        )

    assert response.status_code == 503


def test_missing_file_field_returns_422(client: TestClient) -> None:
    assert client.post("/api/v1/extract").status_code == 422


def test_extract_route_is_documented_in_openapi(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/api/v1/extract" in schema["paths"]
