"""Contract tests.

They assert the camelCase JSON shape (not just status codes), because that shape
is what `packages/types` validates on the frontend.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_camel_case_contract() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "agentEnabled" in body


def test_upload_classifies_and_flags_for_review() -> None:
    application_id = uuid4()

    response = client.post(
        f"/api/v1/documents?applicationId={application_id}",
        files={"file": ("march-paystub.pdf", b"%PDF-1.7 fake", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["document"]["kind"] == "pay_stub"
    # The stub extractor returns zero-confidence fields, so review is required.
    assert body["document"]["status"] == "needs_review"
    assert body["extraction"]["detectedKind"] == "pay_stub"


def test_unknown_document_id_returns_404() -> None:
    response = client.get(f"/api/v1/documents/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found."
