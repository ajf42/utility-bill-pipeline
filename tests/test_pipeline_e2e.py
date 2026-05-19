"""End-to-end pipeline integration — Phase 2's acceptance gate.

Two scenarios go through the real FastAPI route against a tmp-path
fixture-seeded SQLite DB: a clean Liberty-Tower bill that produces no
HIGH-severity flags, and a dirty Liberty-Tower bill that produces
exactly UNIT_MISMATCH (HIGH) and GAP (HIGH) flags. If both scenarios
behave as expected, Phase 2 is done.

Why these two: Liberty Tower main electric is the densest meter in the
fixtures (six prior readings, period_end 2026-04-30) so both gap and
overlap heuristics have something to chew on. UNIT_MISMATCH and GAP
together exercise the structural + heuristic branches of validation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.db.fixtures import seed_fixtures
from src.db.store import AuditLogStore, MeterHistoryStore
from src.main import app
from src.routes.dependencies import get_audit_store, get_drafter, get_store


@pytest.fixture()
def seeded_client(tmp_path):
    db_path = tmp_path / "e2e.db"
    seed = MeterHistoryStore(db_path)
    seed_fixtures(seed)
    seed.close()

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


def _liberty_main_clean() -> dict:
    # Most recent fixture reading on Liberty main electric ends 2026-04-30.
    # period_start 2026-05-01 is 1 day later -- inside the "no flag"
    # window. 31-day period stays under the structural normalization
    # threshold of 35; gap heuristic in validation uses 2 days. The
    # value_in_range billing_period_length signal is normalization-side
    # not validation-side, so 31 days is fine for the acceptance gate
    # which checks validation flags.
    return {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 30500,
        "usage_units": "kWh",
        "currency": "USD",
        "cost": 3660.0,
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }


def _liberty_main_dirty() -> dict:
    # Same meter, but: unit therms (meter is kWh) -> UNIT_MISMATCH HIGH.
    # period_start 2026-05-14 = 14 days after prior period_end -> GAP HIGH.
    return {
        "period_start": "2026-05-14",
        "period_end": "2026-06-12",
        "usage": 30500,
        "usage_units": "therms",
        "currency": "USD",
        "cost": 3660.0,
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }


def test_clean_bill_produces_no_high_severity_flags(seeded_client):
    response = seeded_client.post("/bills", json=_liberty_main_clean())
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_status"] == "triaged"
    validated = body["validated"]

    # Sanity: the meter resolved and prior context attached.
    assert validated["matched_meter"] is not None
    assert validated["matched_meter"]["meter_id_string"] == (
        "MSR.(ConEd)(LT-ELEC-001):(M1)"
    )
    assert validated["prior_context"]["count_of_prior_readings"] >= 6
    assert validated["prior_context"]["prior_period_end"] == "2026-04-30"

    # Acceptance gate: no HIGH-severity flags on a clean bill.
    flags = validated["flags"]
    high = [f for f in flags if f["severity"] == "HIGH"]
    assert high == [], f"expected no HIGH flags, got {high}"


def test_dirty_bill_produces_unit_mismatch_and_gap_flags(seeded_client):
    response = seeded_client.post("/bills", json=_liberty_main_dirty())
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_status"] == "triaged"
    validated = body["validated"]

    # Sanity: meter still resolved -- only the unit + gap are off.
    assert validated["matched_meter"] is not None

    flags_by_type = {f["type"]: f for f in validated["flags"]}
    assert "UNIT_MISMATCH" in flags_by_type
    assert flags_by_type["UNIT_MISMATCH"]["severity"] == "HIGH"
    assert "GAP" in flags_by_type
    assert flags_by_type["GAP"]["severity"] == "HIGH"
