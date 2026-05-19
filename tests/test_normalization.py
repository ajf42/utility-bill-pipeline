"""Tests for the normalization service.

Tier 1 (contract) pins the shape of the NormalizedBill output — the
keys that downstream services (reconciliation, validation, triage)
will read. Tier 2 (behavior) covers the actual signal logic for clean
and dirty inputs. Route-level integration is checked against the live
FastAPI app via TestClient so the wired pipeline is exercised end to
end through the JSON boundary.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.db.store import MeterHistoryStore
from src.main import app
from src.models.bill import NormalizedBill, RawBillInput, SourceMode
from src.routes.dependencies import get_store
from src.services.normalization import normalize


@pytest.fixture()
def client(tmp_path) -> TestClient:
    """TestClient with an empty tmp-path store overriding ``get_store``.

    The normalization tests don't care about meter history, but the route
    runs reconciliation now, so a store has to be provided. Overriding
    here keeps these tests from depending on any prototype.db on disk.
    """

    db_path = tmp_path / "norm-route.db"

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


def _clean_payload() -> dict:
    """A payload that should produce all-True structural signals.

    ConEd is an alias of Consolidated Edison (US-East); US-East default
    currency is USD; ConEd's typical units include kWh. April 2026 has
    30 days, in the 25-35 plausible-monthly range.
    """

    return {
        "period_start": "2026-04-01",
        "period_end": "2026-05-01",
        "usage": 28000,
        "usage_units": "kWh",
        "currency": "USD",
        "cost": 3360.0,
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }


def _raw(payload: dict) -> RawBillInput:
    return RawBillInput(
        source_mode=SourceMode.JSON_ROW,
        raw_payload=payload,
        batch_id=None,
    )


# ---------------------------------------------------------------------------
# Tier 1 — contract
# ---------------------------------------------------------------------------


def test_normalize_returns_normalized_bill():
    result = normalize(_raw(_clean_payload()))
    assert isinstance(result, NormalizedBill)
    assert result.source_mode is SourceMode.JSON_ROW


def test_normalize_emits_all_signal_keys_even_for_dirty_input():
    """Every consumer of structural_signals should be able to read every
    key without a KeyError, even when the input is garbage."""

    junk = {
        "period_start": "not-a-date",
        "period_end": 42,
        "usage": "twenty thousand",
        "usage_units": "fictional-unit",
        "meter_id_string": "bare string with no convention",
        "account_number": "X",
        "site_name": "Y",
    }
    result = normalize(_raw(junk))
    signals = result.structural_signals
    for key in (
        "field_type_valid",
        "value_in_range",
        "provider_known",
        "provider_alias_parsed",
        "unit_known",
        "cross_field_agreement",
    ):
        assert key in signals
    assert "currency_matches_region" in signals["cross_field_agreement"]
    assert "unit_matches_provider_typical" in signals["cross_field_agreement"]


# ---------------------------------------------------------------------------
# Tier 2 — behavior
# ---------------------------------------------------------------------------


def test_clean_payload_produces_all_true_signals():
    result = normalize(_raw(_clean_payload()))
    signals = result.structural_signals

    assert signals["provider_known"] is True
    assert signals["provider_alias_parsed"] is True
    assert signals["unit_known"] is True
    assert all(signals["field_type_valid"].values())
    assert all(signals["value_in_range"].values())
    assert signals["cross_field_agreement"]["currency_matches_region"] is True
    assert signals["cross_field_agreement"]["unit_matches_provider_typical"] is True
    assert result.canonical_provider == "Consolidated Edison"
    assert result.normalized_units == {"usage": "kWh"}


def test_known_provider_alias_resolves_canonical_record():
    result = normalize(_raw(_clean_payload()))
    assert result.canonical_provider == "Consolidated Edison"
    assert result.structural_signals["provider_known"] is True


def test_unknown_provider_alias_flagged_but_does_not_raise():
    payload = _clean_payload()
    payload["meter_id_string"] = "MSR.(Totally Fake Power Co)(ACC-1):(M1)"
    result = normalize(_raw(payload))
    assert result.canonical_provider is None
    assert result.structural_signals["provider_alias_parsed"] is True
    assert result.structural_signals["provider_known"] is False


def test_malformed_meter_id_string_signals_alias_parse_failure():
    payload = _clean_payload()
    payload["meter_id_string"] = "MSR.ConEd123:001"  # missing parens
    result = normalize(_raw(payload))
    assert result.structural_signals["provider_alias_parsed"] is False
    assert result.structural_signals["provider_known"] is False


def test_positive_usage_in_range():
    payload = _clean_payload()
    payload["usage"] = 28000
    result = normalize(_raw(payload))
    assert result.structural_signals["value_in_range"]["usage"] is True


def test_negative_usage_out_of_range():
    payload = _clean_payload()
    payload["usage"] = -100
    result = normalize(_raw(payload))
    assert result.structural_signals["value_in_range"]["usage"] is False
    # The value still type-checks as a number; only range fails.
    assert result.structural_signals["field_type_valid"]["usage"] is True


def test_thirty_day_billing_period_in_range():
    payload = _clean_payload()
    payload["period_start"] = "2026-04-01"
    payload["period_end"] = "2026-05-01"
    result = normalize(_raw(payload))
    assert result.structural_signals["value_in_range"]["billing_period_length"] is True


def test_one_hundred_twenty_day_billing_period_out_of_range():
    payload = _clean_payload()
    payload["period_start"] = "2026-01-01"
    payload["period_end"] = "2026-05-01"  # 120 days
    result = normalize(_raw(payload))
    assert (
        result.structural_signals["value_in_range"]["billing_period_length"] is False
    )


def test_malformed_date_does_not_raise_and_flags_field_type():
    payload = _clean_payload()
    payload["period_start"] = "not-a-date"
    # No exception; signal recorded.
    result = normalize(_raw(payload))
    assert result.structural_signals["field_type_valid"]["period_start"] is False
    # Period-length check degrades to False when either bound is missing.
    assert (
        result.structural_signals["value_in_range"]["billing_period_length"] is False
    )


def test_unit_canonicalized_case_insensitively():
    payload = _clean_payload()
    payload["usage_units"] = "KWH"  # uppercase variant
    result = normalize(_raw(payload))
    assert result.structural_signals["unit_known"] is True
    assert result.normalized_units == {"usage": "kWh"}


def test_unknown_unit_flagged():
    payload = _clean_payload()
    payload["usage_units"] = "barrels"
    result = normalize(_raw(payload))
    assert result.structural_signals["unit_known"] is False
    assert result.normalized_units == {}


def test_currency_mismatch_against_region_flagged():
    payload = _clean_payload()
    # ConEd is US-East -> regional default USD; bill says EUR.
    payload["currency"] = "EUR"
    result = normalize(_raw(payload))
    assert (
        result.structural_signals["cross_field_agreement"]["currency_matches_region"]
        is False
    )
    # Currency shape itself is fine — only the cross-field agreement fails.
    assert result.structural_signals["field_type_valid"]["currency"] is True


def test_unit_not_in_provider_typical_flagged():
    payload = _clean_payload()
    payload["usage_units"] = "gallons"  # ConEd doesn't bill water
    result = normalize(_raw(payload))
    assert (
        result.structural_signals["cross_field_agreement"][
            "unit_matches_provider_typical"
        ]
        is False
    )


def test_cost_negative_out_of_range():
    payload = _clean_payload()
    payload["cost"] = -50.0
    result = normalize(_raw(payload))
    assert result.structural_signals["value_in_range"]["cost"] is False


def test_normalize_does_not_raise_on_completely_busted_payload():
    busted = {
        "period_start": None,
        "period_end": None,
        "usage": None,
        "usage_units": None,
        "meter_id_string": None,
        "account_number": None,
        "site_name": None,
    }
    # The point: no exception. Structural signals tell the story.
    result = normalize(_raw(busted))
    assert isinstance(result, NormalizedBill)
    # Every parse-able field flags False. Currency is absent here, which
    # is the "default from region" path -- not a type failure -- so it
    # stays True. The other four fields fail their type checks.
    ftv = result.structural_signals["field_type_valid"]
    for field in ("period_start", "period_end", "usage", "usage_units"):
        assert ftv[field] is False, f"expected {field}=False, got {ftv[field]}"
    assert ftv["currency"] is True
    assert result.structural_signals["provider_alias_parsed"] is False
    assert result.structural_signals["provider_known"] is False
    assert result.structural_signals["unit_known"] is False


# ---------------------------------------------------------------------------
# Tier 2 — route integration
# ---------------------------------------------------------------------------


def test_post_bills_clean_body_shows_all_true_signals(client: TestClient):
    response = client.post("/bills", json=_clean_payload())
    assert response.status_code == 200
    signals = response.json()["normalized"]["structural_signals"]
    assert signals["provider_known"] is True
    assert signals["unit_known"] is True
    assert all(signals["field_type_valid"].values())
    assert all(signals["value_in_range"].values())
    assert signals["cross_field_agreement"]["currency_matches_region"] is True
    assert signals["cross_field_agreement"]["unit_matches_provider_typical"] is True


def test_post_bills_negative_usage_flags_value_in_range(client: TestClient):
    payload = _clean_payload()
    payload["usage"] = -100
    response = client.post("/bills", json=payload)
    assert response.status_code == 200
    signals = response.json()["normalized"]["structural_signals"]
    assert signals["value_in_range"]["usage"] is False
