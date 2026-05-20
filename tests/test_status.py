"""Tests for GET /status — the operational visibility endpoint.

All tests use TestClient with tmp-path DB overrides. No real API calls
in the default run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.db.store import AuditLogStore, MeterHistoryStore
from src.main import app
from src.models.audit import AuditEntry
from src.models.drafter import DrafterOutput, EmailRecipientType, ProposedAction
from src.models.quality import RoutingKey, TriageDecision, TriageRoute
from src.routes.dependencies import get_audit_store, get_drafter, get_store


@pytest.fixture()
def client(tmp_path) -> TestClient:
    db_path = tmp_path / "status.db"

    def _store_override():
        s = MeterHistoryStore(db_path)
        try:
            yield s
        finally:
            s.close()

    def _audit_override():
        s = AuditLogStore(db_path)
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_store] = _store_override
    app.dependency_overrides[get_audit_store] = _audit_override
    app.dependency_overrides[get_drafter] = lambda: None
    try:
        yield TestClient(app), db_path
    finally:
        app.dependency_overrides.pop(get_store, None)
        app.dependency_overrides.pop(get_audit_store, None)
        app.dependency_overrides.pop(get_drafter, None)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _drafter_output() -> DrafterOutput:
    return DrafterOutput(
        proposed_action=ProposedAction.CONVERT_UNIT,
        proposed_correction={"usage_units": "kWh"},
        draft_email_subject="subject",
        draft_email_body="body",
        draft_email_recipient_type=EmailRecipientType.INTERNAL_TEAM,
        basis_note="basis",
        confidence_note="none",
    )


def _entry(
    *,
    bill_ref: str,
    route: TriageRoute,
    when: datetime,
    drafter_output: DrafterOutput | None = None,
    parent_ref: str | None = None,
    routing_key: RoutingKey | None = None,
) -> AuditEntry:
    return AuditEntry(
        bill_external_ref=bill_ref,
        parent_bill_external_ref=parent_ref,
        timestamp=when,
        source_mode="JSON_ROW",
        triage_decision=TriageDecision(
            route=route,
            routing_key=routing_key,
            reasoning="test",
            drafter_output=drafter_output,
        ),
        drafter_output=drafter_output,
    )


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_status_returns_200_with_expected_keys(client):
    test_client, _ = client
    response = test_client.get("/status")
    assert response.status_code == 200
    body = response.json()
    expected = {
        "service_name", "version", "db_state", "audit_counts_24h",
        "pending_drafted", "anthropic_api_key_set",
    }
    assert expected.issubset(body.keys())
    db = body["db_state"]
    assert {"open", "readings_count", "audit_count", "last_write_at"}.issubset(db.keys())
    assert db["open"] is True
    assert isinstance(body["anthropic_api_key_set"], bool)


# ---------------------------------------------------------------------------
# Counts behavior
# ---------------------------------------------------------------------------


def test_status_counts_zero_on_empty_db(client):
    test_client, _ = client
    body = test_client.get("/status").json()
    assert body["db_state"]["readings_count"] == 0
    assert body["db_state"]["audit_count"] == 0
    assert body["db_state"]["last_write_at"] is None
    assert body["audit_counts_24h"] == {}
    assert body["pending_drafted"] == 0


def test_status_audit_counts_24h_only_includes_recent(client):
    test_client, db_path = client
    audit_store = AuditLogStore(db_path)
    now = datetime.now(timezone.utc)
    try:
        audit_store.record(_entry(
            bill_ref="recent-1", route=TriageRoute.AUTO_RESOLVE, when=now - timedelta(hours=1)
        ))
        audit_store.record(_entry(
            bill_ref="recent-2", route=TriageRoute.AUTO_RESOLVE, when=now - timedelta(hours=12)
        ))
        audit_store.record(_entry(
            bill_ref="recent-3", route=TriageRoute.ESCALATE, when=now - timedelta(hours=3),
            routing_key=RoutingKey.OVERLAP,
        ))
        audit_store.record(_entry(
            bill_ref="old-1", route=TriageRoute.AUTO_RESOLVE, when=now - timedelta(days=3)
        ))
    finally:
        audit_store.close()

    body = test_client.get("/status").json()
    assert body["db_state"]["audit_count"] == 4
    assert body["audit_counts_24h"] == {
        "AUTO_RESOLVE": 2,
        "ESCALATE": 1,
    }


def test_status_pending_drafted_excludes_entries_with_followups(client):
    test_client, db_path = client
    audit_store = AuditLogStore(db_path)
    now = datetime.now(timezone.utc)
    try:
        # Pending: DraftForHumanReview with drafter_output, no follow-up.
        audit_store.record(_entry(
            bill_ref="pending-1",
            route=TriageRoute.DRAFT_FOR_HUMAN_REVIEW,
            when=now,
            drafter_output=_drafter_output(),
        ))
        # Not pending: original is DraftForHumanReview but an approval
        # follow-up has been recorded against it.
        audit_store.record(_entry(
            bill_ref="approved-orig",
            route=TriageRoute.DRAFT_FOR_HUMAN_REVIEW,
            when=now,
            drafter_output=_drafter_output(),
        ))
        audit_store.record(_entry(
            bill_ref="approved-followup",
            route=TriageRoute.DRAFT_FOR_HUMAN_REVIEW,
            when=now,
            drafter_output=_drafter_output(),
            parent_ref="approved-orig",
        ))
        # Not pending: AutoResolve never enters the draft queue.
        audit_store.record(_entry(
            bill_ref="auto-1", route=TriageRoute.AUTO_RESOLVE, when=now,
        ))
        # Not pending: a DraftForHumanReview entry whose drafter_output
        # is None (no drafter was attached) — there is no proposed
        # correction to apply, so it can't be in the pending queue.
        audit_store.record(_entry(
            bill_ref="no-drafter-output",
            route=TriageRoute.DRAFT_FOR_HUMAN_REVIEW,
            when=now,
            drafter_output=None,
        ))
    finally:
        audit_store.close()

    body = test_client.get("/status").json()
    assert body["pending_drafted"] == 1
