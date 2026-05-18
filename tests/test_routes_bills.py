"""HTTP-surface tests for /health and POST /bills.

Uses FastAPI's TestClient (httpx). Covers the happy path, the stub-response
shape (which subsequent prompts replace once the real pipeline is wired),
and the 422 boundary when a required field is missing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _minimum_valid_body() -> dict:
    return {
        "period_start": "2026-04-01",
        "period_end": "2026-05-01",
        "usage": 28000,
        "usage_units": "kWh",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }


def test_health_returns_ok_and_version(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.2.0"


def test_post_bills_with_valid_body_returns_stub_response(client: TestClient):
    body = _minimum_valid_body()

    response = client.post("/bills", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["pipeline_status"] == "ingested_only_not_yet_processed"
    raw_input = payload["raw_input"]
    assert raw_input["source_mode"] == "JSON_ROW"
    assert raw_input["batch_id"] is None
    assert raw_input["raw_payload"] == body


def test_post_bills_missing_required_field_returns_422(client: TestClient):
    body = _minimum_valid_body()
    body.pop("period_start")

    response = client.post("/bills", json=body)

    assert response.status_code == 422
    assert "period_start" in response.json()["detail"]
