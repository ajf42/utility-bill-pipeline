"""Tests for the reconciliation service.

Each test uses a tmp_path SQLite DB seeded with the Phase 1 fixtures.
The Liberty Tower main electric meter is the canonical target — it
has six prior readings (four recent + two older) which exercises
both the match path and the prior-context summary.

Route-integration tests override the FastAPI ``get_store`` dependency
to point at the same tmp_path DB so the TestClient sees the same
fixture data the unit tests see.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.db.fixtures import seed_fixtures
from src.db.store import MeterHistoryStore
from src.main import app
from src.models.bill import NormalizedBill, RawBillInput, SourceMode
from src.routes.dependencies import get_store
from src.services.normalization import normalize
from src.services.reconciliation import reconcile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_store(tmp_path):
    s = MeterHistoryStore(tmp_path / "scratch.db")
    seed_fixtures(s)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def seeded_client(tmp_path):
    """A TestClient whose route uses a tmp-path store seeded with fixtures.

    Uses ``app.dependency_overrides`` so the test isn't sensitive to the
    real ``DB_PATH`` env var or to any prototype.db that happens to be
    on disk in the working tree.
    """

    db_path = tmp_path / "route.db"
    seed_store = MeterHistoryStore(db_path)
    seed_fixtures(seed_store)
    seed_store.close()

    def _override():
        store = MeterHistoryStore(db_path)
        try:
            yield store
        finally:
            store.close()

    app.dependency_overrides[get_store] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_store, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _liberty_main_payload() -> dict:
    return {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }


def _normalize_payload(payload: dict) -> NormalizedBill:
    raw = RawBillInput(
        source_mode=SourceMode.JSON_ROW, raw_payload=payload, batch_id=None
    )
    return normalize(raw)


# ---------------------------------------------------------------------------
# Tier 1 — contract
# ---------------------------------------------------------------------------


def test_reconcile_returns_reconciled_bill(seeded_store):
    normalized = _normalize_payload(_liberty_main_payload())
    result = reconcile(normalized, seeded_store)
    assert result.__class__.__name__ == "ReconciledBill"
    # Normalization fields are carried through unchanged.
    assert result.canonical_provider == "Consolidated Edison"
    assert result.normalized_units == {"usage": "kWh"}


def test_reconcile_no_match_returns_empty_prior_context(seeded_store):
    payload = _liberty_main_payload()
    payload["meter_id_string"] = "MSR.(Unknown)(FAKE-001):(M99)"
    payload["account_number"] = "FAKE-001"
    payload["site_name"] = "Atlantis"

    result = reconcile(_normalize_payload(payload), seeded_store)

    assert result.matched_meter is None
    assert result.prior_readings == []
    assert result.prior_context["prior_period_end"] is None
    assert result.prior_context["count_of_prior_readings"] == 0


# ---------------------------------------------------------------------------
# Tier 2 — behavior
# ---------------------------------------------------------------------------


def test_matched_meter_attaches_meter_and_priors(seeded_store):
    result = reconcile(_normalize_payload(_liberty_main_payload()), seeded_store)

    assert result.matched_meter is not None
    assert (
        result.matched_meter.meter_id_string == "MSR.(ConEd)(LT-ELEC-001):(M1)"
    )
    # Liberty main electric carries 4 recent (Jan-Apr 2026) + 2 older
    # (Jul-Aug 2025) = 6 prior readings from the fixtures.
    assert len(result.prior_readings) >= 6
    # The most recent prior is the April 2026 reading (period_end Apr 30).
    assert result.prior_context["prior_period_end"] == date(2026, 4, 30)
    assert result.prior_context["count_of_prior_readings"] == len(
        result.prior_readings
    )


def test_unmatched_meter_does_not_raise(seeded_store):
    payload = _liberty_main_payload()
    payload["meter_id_string"] = "MSR.(NoSuch)(XX-NEVER):(M0)"
    payload["account_number"] = "XX-NEVER"
    payload["site_name"] = "Nowhere"

    # The point: this returns cleanly. No exception.
    result = reconcile(_normalize_payload(payload), seeded_store)
    assert result.matched_meter is None


def test_partial_match_wrong_account_does_not_resolve(seeded_store):
    """The lookup is a three-key triple; any single mismatch is a miss."""

    payload = _liberty_main_payload()
    payload["account_number"] = "WRONG-001"  # meter+site exist; account doesn't

    result = reconcile(_normalize_payload(payload), seeded_store)
    assert result.matched_meter is None
    assert result.prior_context["count_of_prior_readings"] == 0


def test_partial_match_wrong_site_does_not_resolve(seeded_store):
    payload = _liberty_main_payload()
    payload["site_name"] = "Pacific Plaza"  # meter+account belong to Liberty

    result = reconcile(_normalize_payload(payload), seeded_store)
    assert result.matched_meter is None


def test_prior_readings_limit_is_respected(seeded_store):
    """The DESIGN.md §4 default is 12, but a caller can override."""

    result = reconcile(
        _normalize_payload(_liberty_main_payload()),
        seeded_store,
        prior_readings_limit=3,
    )
    assert len(result.prior_readings) == 3
    # Still sorted period_end DESC; April most recent.
    assert result.prior_context["prior_period_end"] == date(2026, 4, 30)


def test_prior_period_end_is_a_date_not_a_datetime(seeded_store):
    result = reconcile(_normalize_payload(_liberty_main_payload()), seeded_store)
    assert isinstance(result.prior_context["prior_period_end"], date)


# ---------------------------------------------------------------------------
# Tier 2 — route integration
# ---------------------------------------------------------------------------


def test_post_bills_known_meter_returns_matched_with_priors(seeded_client):
    response = seeded_client.post("/bills", json=_liberty_main_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_status"] == "reconciled"
    reconciled = body["reconciled"]
    assert reconciled["matched_meter"] is not None
    assert (
        reconciled["matched_meter"]["meter_id_string"]
        == "MSR.(ConEd)(LT-ELEC-001):(M1)"
    )
    assert len(reconciled["prior_readings"]) >= 6
    assert reconciled["prior_context"]["count_of_prior_readings"] >= 6
    assert reconciled["prior_context"]["prior_period_end"] == "2026-04-30"


def test_post_bills_unknown_meter_returns_200_with_null_match(seeded_client):
    payload = _liberty_main_payload()
    payload["meter_id_string"] = "MSR.(NoSuch)(XX-NEVER):(M0)"
    payload["account_number"] = "XX-NEVER"
    payload["site_name"] = "Nowhere"

    response = seeded_client.post("/bills", json=payload)
    assert response.status_code == 200
    reconciled = response.json()["reconciled"]
    assert reconciled["matched_meter"] is None
    assert reconciled["prior_readings"] == []
    assert reconciled["prior_context"]["prior_period_end"] is None
    assert reconciled["prior_context"]["count_of_prior_readings"] == 0
