"""HTTP-surface tests for /health and POST /bills.

Uses FastAPI's TestClient (httpx). Covers the happy path, the response
shape, and the 422 boundary when a required field is missing. The
``get_store`` dependency is overridden to an empty tmp-path DB so these
tests do not depend on (or pollute) any prototype.db on disk.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.db.store import AuditLogStore, MeterHistoryStore
from src.main import app
from src.routes.dependencies import get_audit_store, get_drafter, get_store


@pytest.fixture()
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "routes.db"

    def _store_override():
        store = MeterHistoryStore(db_path)
        try:
            yield store
        finally:
            store.close()

    def _audit_override():
        store = AuditLogStore(db_path)
        try:
            yield store
        finally:
            store.close()

    app.dependency_overrides[get_store] = _store_override
    app.dependency_overrides[get_audit_store] = _audit_override
    app.dependency_overrides[get_drafter] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(get_audit_store, None)
        app.dependency_overrides.pop(get_drafter, None)


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
    assert body["version"] == "0.3.0"


def test_post_bills_with_valid_body_returns_validated_response(client: TestClient):
    body = _minimum_valid_body()

    response = client.post("/bills", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["pipeline_status"] == "triaged"
    assert "audit_ref" in payload
    assert payload["triage"]["route"] == "ESCALATE"
    assert payload["triage"]["routing_key"] == "METER_UNASSIGNED"
    raw_input = payload["raw_input"]
    assert raw_input["source_mode"] == "JSON_ROW"
    assert raw_input["batch_id"] is None
    assert raw_input["raw_payload"] == body
    # Normalization, reconciliation, and validation artifacts present
    # with the expected sub-shapes; specifics live in their own tests.
    normalized = payload["normalized"]
    assert "structural_signals" in normalized
    assert "field_type_valid" in normalized["structural_signals"]
    reconciled = payload["reconciled"]
    # Empty-store override: no match expected.
    assert reconciled["matched_meter"] is None
    assert reconciled["prior_readings"] == []
    assert reconciled["prior_context"]["count_of_prior_readings"] == 0
    validated = payload["validated"]
    # Unmatched meter -> exactly one METER_UNASSIGNED flag.
    flag_types = [f["type"] for f in validated["flags"]]
    assert "METER_UNASSIGNED" in flag_types


def test_post_bills_missing_required_field_returns_422(client: TestClient):
    body = _minimum_valid_body()
    body.pop("period_start")

    response = client.post("/bills", json=body)

    assert response.status_code == 422
    assert "period_start" in response.json()["detail"]
