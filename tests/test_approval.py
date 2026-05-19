"""Tests for the approve / reject endpoints and the Phase 3 e2e gate.

All tests use FakeAnthropicClient — no real API calls. The Phase 3 e2e
test exercises ingest -> normalize -> reconcile -> validate -> triage
-> drafter -> approve -> persistence on a synthetic unit-mismatch bill.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.db.fixtures import seed_fixtures
from src.db.store import AuditLogStore, MeterHistoryStore
from src.main import app
from src.routes.dependencies import get_audit_store, get_drafter, get_store
from src.services.drafter import DrafterService
from tests.fakes import FakeAnthropicClient, FakeContentBlock, FakeMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _drafter_returning(input_payload: dict) -> tuple[DrafterService, FakeAnthropicClient]:
    client = FakeAnthropicClient()
    client.set_next_response(
        FakeMessage(
            content=[
                FakeContentBlock(
                    type="tool_use",
                    name="draft_resolution",
                    input=input_payload,
                )
            ]
        )
    )
    return DrafterService(client=client), client  # type: ignore[arg-type]


def _canned_correction_payload() -> dict:
    return {
        "proposed_action": "CONVERT_UNIT",
        "proposed_correction": {"usage_units": "kWh"},
        "draft_email_subject": "Likely unit-label error on Liberty Tower bill",
        "draft_email_body": "We observed therms reported against a kWh meter.",
        "draft_email_recipient_type": "INTERNAL_TEAM",
        "basis_note": "Meter is locked to kWh; ConEd reports electric in kWh.",
        "confidence_note": "Assumes the numeric value is correct.",
    }


@pytest.fixture()
def seeded_client(tmp_path):
    """A TestClient against a fixture-seeded DB with a fake drafter."""

    db_path = tmp_path / "approval.db"
    seed = MeterHistoryStore(db_path)
    seed_fixtures(seed)
    seed.close()

    drafter, _ = _drafter_returning(_canned_correction_payload())

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
    app.dependency_overrides[get_drafter] = lambda: drafter
    try:
        yield TestClient(app), db_path
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(get_audit_store, None)
        app.dependency_overrides.pop(get_drafter, None)


def _liberty_unit_mismatch_payload() -> dict:
    # ConEd Liberty Tower main electric: meter locked to kWh; we send therms.
    return {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 30500,
        "usage_units": "therms",  # mismatch with kWh meter
        "currency": "USD",
        "cost": 3660.0,
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }


def _clean_payload() -> dict:
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


# ---------------------------------------------------------------------------
# Approve / reject behavior
# ---------------------------------------------------------------------------


def test_approve_writes_reading_with_drafter_approved_source_mode(seeded_client):
    client, db_path = seeded_client

    create = client.post("/bills", json=_liberty_unit_mismatch_payload())
    assert create.status_code == 200
    body = create.json()
    assert body["triage"]["route"] == "DRAFT_FOR_HUMAN_REVIEW"
    audit_ref = body["audit_ref"]

    approve = client.post(f"/bills/{audit_ref}/approve")
    assert approve.status_code == 200, approve.text
    approve_body = approve.json()
    assert "reading_id" in approve_body
    assert "audit_ref" in approve_body
    assert approve_body["audit_ref"] != audit_ref  # follow-up has its own ref

    # Verify the reading lands in SQLite with source_mode=DRAFTER_APPROVED
    # and the corrected unit (kWh, not the original therms).
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT usage_units, source_mode FROM readings WHERE id = ?",
            (approve_body["reading_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "kWh"
    assert row[1] == "DRAFTER_APPROVED"


def test_reject_records_audit_entry_no_reading_written(seeded_client):
    client, db_path = seeded_client

    create = client.post("/bills", json=_liberty_unit_mismatch_payload())
    audit_ref = create.json()["audit_ref"]

    before = _reading_count(db_path)
    reject = client.post(
        f"/bills/{audit_ref}/reject",
        json={"rejection_reason": "value looks wrong on top of unit"},
    )
    assert reject.status_code == 200
    after = _reading_count(db_path)
    assert after == before  # no reading written

    # Two audit entries now share the original ref through parent_bill_external_ref.
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT bill_external_ref, payload_json FROM audit_entries "
            "WHERE bill_external_ref = ? OR payload_json LIKE ? ORDER BY id",
            (audit_ref, f'%"parent_bill_external_ref":"{audit_ref}"%'),
        ).fetchall()
    finally:
        conn.close()
    # Original + rejection follow-up.
    assert len(rows) == 2


def test_approve_nonexistent_audit_ref_returns_404(seeded_client):
    client, _ = seeded_client
    resp = client.post("/bills/no-such-ref/approve")
    assert resp.status_code == 404


def test_approve_auto_resolve_entry_returns_409(seeded_client):
    client, _ = seeded_client
    create = client.post("/bills", json=_clean_payload())
    assert create.json()["triage"]["route"] == "AUTO_RESOLVE"
    audit_ref = create.json()["audit_ref"]

    resp = client.post(f"/bills/{audit_ref}/approve")
    assert resp.status_code == 409


def test_approve_preserves_link_and_carries_before_and_after(seeded_client):
    client, db_path = seeded_client

    create = client.post("/bills", json=_liberty_unit_mismatch_payload())
    audit_ref = create.json()["audit_ref"]
    approve = client.post(f"/bills/{audit_ref}/approve")
    followup_ref = approve.json()["audit_ref"]

    audit_store = AuditLogStore(db_path)
    try:
        followups = audit_store.get_by_bill_ref(followup_ref)
        assert len(followups) == 1
        followup = followups[0]
    finally:
        audit_store.close()

    assert followup.parent_bill_external_ref == audit_ref
    nf = followup.normalized_fields
    assert nf["original_payload"]["usage_units"] == "therms"
    assert nf["corrected_payload"]["usage_units"] == "kWh"
    assert nf["applied_correction"] == {"usage_units": "kWh"}


# ---------------------------------------------------------------------------
# Phase 3 e2e gate
# ---------------------------------------------------------------------------


def test_phase3_e2e_unit_mismatch_then_approve(seeded_client):
    """Full flow: ingest -> normalize -> reconcile -> validate -> triage
    (FakeAnthropicClient) -> approve -> persistence. Reading lands in
    readings table with corrected unit and source_mode=DRAFTER_APPROVED.
    """

    client, db_path = seeded_client

    create = client.post("/bills", json=_liberty_unit_mismatch_payload())
    assert create.status_code == 200
    body = create.json()
    assert body["triage"]["route"] == "DRAFT_FOR_HUMAN_REVIEW"
    assert body["triage"]["drafter_output"]["proposed_action"] == "CONVERT_UNIT"

    audit_ref = body["audit_ref"]
    approve = client.post(f"/bills/{audit_ref}/approve")
    assert approve.status_code == 200
    reading_id = approve.json()["reading_id"]

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT usage, usage_units, source_mode FROM readings WHERE id = ?",
            (reading_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 30500.0
    assert row[1] == "kWh"
    assert row[2] == "DRAFTER_APPROVED"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _reading_count(db_path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    finally:
        conn.close()
