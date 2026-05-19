"""Tests for fixture seeding."""

from __future__ import annotations

import pytest

from src.db.fixtures import seed_fixtures
from src.db.store import MeterHistoryStore
from src.models.entities import Region, Unit


@pytest.fixture
def store(tmp_path):
    s = MeterHistoryStore(tmp_path / "fixtures.db")
    try:
        yield s
    finally:
        s.close()


def test_seed_fixtures_produces_expected_counts(store):
    counts = seed_fixtures(store)
    assert counts["sites"] == 3
    assert counts["accounts"] == 5
    assert counts["meters"] == 10
    assert counts["readings"] >= 30
    # 8 baseline meters * 4 recent + 2 older on Liberty main electric
    # = 34, plus the inactive meter (1 old reading) and the
    # unknown-provider gas meter (4 monthly readings) added for the demo
    # canonical bills 5 and 6 = 39 total.
    assert counts["readings"] == 39


def test_find_meter_resolves_liberty_main_electric(store):
    seed_fixtures(store)
    meter = store.find_meter(
        "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "LT-ELEC-001",
        "Liberty Tower",
    )
    assert meter is not None
    assert meter.unit is Unit.KWH
    assert meter.currency == "USD"
    assert meter.active is True


def test_liberty_main_electric_has_six_or_more_readings(store):
    seed_fixtures(store)
    meter = store.find_meter(
        "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "LT-ELEC-001",
        "Liberty Tower",
    )
    priors = store.get_prior_readings(meter.id, limit=12)
    # Four recent (Jan-Apr 2026) + two older (Jul-Aug 2025) = 6 minimum.
    assert len(priors) >= 6
    # Sorted period_end DESC: April 2026 most recent.
    assert priors[0].period_start.year == 2026
    assert priors[0].period_start.month == 4
    # The 5th-most-recent reading is from 2025 (the gap-scenario seed).
    older = [r for r in priors if r.period_start.year == 2025]
    assert len(older) == 2


def test_fixtures_round_trip_via_models(store):
    seed_fixtures(store)

    liberty = store.get_site_by_name("Liberty Tower")
    pacific = store.get_site_by_name("Pacific Plaza")
    thames = store.get_site_by_name("Thames Court")
    assert liberty is not None and liberty.region is Region.US
    assert pacific is not None and pacific.region is Region.US
    assert thames is not None and thames.region is Region.EU

    # Each commodity surface: kWh, therms, HCF — all valid Unit enum values.
    elec = store.find_meter("MSR.(ConEd)(LT-ELEC-001):(M1)", "LT-ELEC-001", "Liberty Tower")
    gas = store.find_meter("MSR.(NationalGrid)(LT-GAS-002):(M1)", "LT-GAS-002", "Liberty Tower")
    water = store.find_meter("MSR.(EBMUD)(PP-WATER-002):(M1)", "PP-WATER-002", "Pacific Plaza")
    assert elec.unit is Unit.KWH
    assert gas.unit is Unit.THERMS
    assert water.unit is Unit.HCF

    # Thames meter is GBP — currency round-trips intact.
    tc = store.find_meter("MSR.(OctopusEnergy)(TC-ELEC-001):(M1)", "TC-ELEC-001", "Thames Court")
    assert tc.currency == "GBP"
