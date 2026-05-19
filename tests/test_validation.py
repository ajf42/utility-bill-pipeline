"""Tests for the validation service.

Validation is pure logic over a ReconciledBill, so these tests build
ReconciledBill instances directly rather than going through the store
or the rest of the pipeline. Each test pins one check (or its absence).
"""

from __future__ import annotations

from datetime import date

from src.models.bill import NormalizedBill, ReconciledBill, SourceMode
from src.models.entities import (
    Account,
    AccountType,
    LandlordOrTenant,
    Meter,
    MeterType,
    Reading,
    Unit,
)
from src.models.quality import FlagType, Severity
from src.services.validation import validate


# ---------------------------------------------------------------------------
# Builders — keep test bodies short and readable
# ---------------------------------------------------------------------------


def _meter(
    *,
    unit: Unit = Unit.KWH,
    currency: str = "USD",
    active: bool = True,
) -> Meter:
    return Meter(
        id=1,
        meter_id_string="MSR.(ConEd)(LT-ELEC-001):(M1)",
        account_id=1,
        unit=unit,
        currency=currency,
        type=MeterType.ELECTRIC,
        landlord_or_tenant=LandlordOrTenant.LANDLORD,
        active=active,
        start_date=date(2020, 1, 1),
    )


def _account(generation: bool = False) -> Account:
    return Account(
        id=1,
        account_number="LT-ELEC-001",
        account_type=AccountType.CONNECT,
        site_id=1,
        generation_account=generation,
    )


def _prior_reading(start: date, end: date, meter_id: int = 1) -> Reading:
    return Reading(
        id=99,
        meter_id=meter_id,
        period_start=start,
        period_end=end,
        usage=30000.0,
        usage_units=Unit.KWH,
        cost=3600.0,
        currency="USD",
    )


def _reconciled(
    *,
    payload: dict | None = None,
    meter: Meter | None = ...,
    account: Account | None = ...,
    priors: list[Reading] | None = None,
    prior_period_end: date | None = ...,
) -> ReconciledBill:
    if payload is None:
        payload = {
            "period_start": "2026-05-01",
            "period_end": "2026-05-31",
            "usage": 30000,
            "usage_units": "kWh",
            "currency": "USD",
            "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
            "account_number": "LT-ELEC-001",
            "site_name": "Liberty Tower",
        }
    if meter is ...:
        meter = _meter()
    if account is ...:
        account = _account()
    if priors is None:
        priors = []
    if prior_period_end is ...:
        prior_period_end = priors[0].period_end if priors else None

    normalized = NormalizedBill(
        source_mode=SourceMode.JSON_ROW,
        raw_payload=payload,
        batch_id=None,
    )
    return ReconciledBill(
        **normalized.model_dump(),
        matched_meter=meter,
        matched_account=account,
        prior_readings=priors,
        prior_context={
            "prior_period_end": prior_period_end,
            "count_of_prior_readings": len(priors),
        },
    )


def _flag_types(validated):
    return [f.type for f in validated.flags]


# ---------------------------------------------------------------------------
# Tier 1 — contract
# ---------------------------------------------------------------------------


def test_validate_returns_validated_bill_with_list_flags():
    result = validate(_reconciled())
    assert result.__class__.__name__ == "ValidatedBill"
    assert isinstance(result.flags, list)


def test_clean_bill_has_no_flags():
    result = validate(_reconciled())
    assert result.flags == []


# ---------------------------------------------------------------------------
# Tier 2 — structural checks
# ---------------------------------------------------------------------------


def test_unit_mismatch_high_severity():
    payload = {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 1000,
        "usage_units": "therms",  # meter is kWh
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    result = validate(_reconciled(payload=payload))
    flags = [f for f in result.flags if f.type is FlagType.UNIT_MISMATCH]
    assert len(flags) == 1
    assert flags[0].severity is Severity.HIGH


def test_currency_mismatch_high_severity():
    payload = {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "EUR",  # meter is USD
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    result = validate(_reconciled(payload=payload))
    flags = [f for f in result.flags if f.type is FlagType.CURRENCY_MISMATCH]
    assert len(flags) == 1
    assert flags[0].severity is Severity.HIGH


def test_inactive_meter_high_severity():
    result = validate(_reconciled(meter=_meter(active=False)))
    flags = [f for f in result.flags if f.type is FlagType.INACTIVE_METER]
    assert len(flags) == 1
    assert flags[0].severity is Severity.HIGH


def test_generation_mismatch_high_severity():
    payload = {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "energy_exported": 500.0,
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    # account.generation_account=False (default) -> mismatch.
    result = validate(_reconciled(payload=payload))
    flags = [f for f in result.flags if f.type is FlagType.GENERATION_MISMATCH]
    assert len(flags) == 1
    assert flags[0].severity is Severity.HIGH


def test_no_generation_mismatch_when_account_is_generation():
    payload = {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "energy_exported": 500.0,
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    result = validate(_reconciled(payload=payload, account=_account(generation=True)))
    assert FlagType.GENERATION_MISMATCH not in _flag_types(result)


# ---------------------------------------------------------------------------
# Tier 2 — domain heuristics: gap
# ---------------------------------------------------------------------------


def test_gap_of_ten_days_is_high_severity():
    # prior_period_end 2026-04-15, incoming period_start 2026-04-25 -> 10 days.
    payload = {
        "period_start": "2026-04-25",
        "period_end": "2026-05-24",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    result = validate(_reconciled(payload=payload, prior_period_end=date(2026, 4, 15)))
    gap_flags = [f for f in result.flags if f.type is FlagType.GAP]
    assert len(gap_flags) == 1
    assert gap_flags[0].severity is Severity.HIGH


def test_gap_of_four_days_is_medium_severity():
    payload = {
        "period_start": "2026-04-19",
        "period_end": "2026-05-18",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    result = validate(_reconciled(payload=payload, prior_period_end=date(2026, 4, 15)))
    gap_flags = [f for f in result.flags if f.type is FlagType.GAP]
    assert len(gap_flags) == 1
    assert gap_flags[0].severity is Severity.MEDIUM


def test_gap_of_one_day_produces_no_flag():
    # Contiguous-or-near-contiguous reads are normal.
    payload = {
        "period_start": "2026-04-16",
        "period_end": "2026-05-15",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    result = validate(_reconciled(payload=payload, prior_period_end=date(2026, 4, 15)))
    assert FlagType.GAP not in _flag_types(result)


def test_no_gap_flag_when_no_prior_readings():
    # First reading on a meter — no prior context, no gap heuristic.
    result = validate(_reconciled(prior_period_end=None))
    assert FlagType.GAP not in _flag_types(result)


# ---------------------------------------------------------------------------
# Tier 2 — domain heuristics: overlap
# ---------------------------------------------------------------------------


def test_overlap_with_prior_reading_high_severity():
    # Prior covers Apr 1 - Apr 30; incoming Apr 20 - May 19 overlaps.
    payload = {
        "period_start": "2026-04-20",
        "period_end": "2026-05-19",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    prior = _prior_reading(date(2026, 4, 1), date(2026, 4, 30))
    result = validate(_reconciled(payload=payload, priors=[prior]))
    overlap_flags = [f for f in result.flags if f.type is FlagType.OVERLAP]
    assert len(overlap_flags) == 1
    assert overlap_flags[0].severity is Severity.HIGH


def test_contiguous_period_is_not_overlap():
    # Prior covers Apr 1 - Apr 30; incoming May 1 - May 31 is contiguous.
    payload = {
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    prior = _prior_reading(date(2026, 4, 1), date(2026, 4, 30))
    result = validate(_reconciled(payload=payload, priors=[prior]))
    assert FlagType.OVERLAP not in _flag_types(result)


# ---------------------------------------------------------------------------
# Tier 2 — unmatched meter
# ---------------------------------------------------------------------------


def test_unmatched_meter_produces_meter_unassigned_only():
    result = validate(_reconciled(meter=None, account=None, prior_period_end=None))
    types = _flag_types(result)
    assert FlagType.METER_UNASSIGNED in types
    # No heuristics or structural checks should have fired.
    for t in (
        FlagType.UNIT_MISMATCH,
        FlagType.CURRENCY_MISMATCH,
        FlagType.INACTIVE_METER,
        FlagType.GENERATION_MISMATCH,
        FlagType.GAP,
        FlagType.OVERLAP,
    ):
        assert t not in types
    # The single METER_UNASSIGNED flag is HIGH severity per the prompt.
    unassigned = [f for f in result.flags if f.type is FlagType.METER_UNASSIGNED]
    assert len(unassigned) == 1
    assert unassigned[0].severity is Severity.HIGH


# ---------------------------------------------------------------------------
# Tier 2 — schema-level safety
# ---------------------------------------------------------------------------


def test_period_start_after_period_end_produces_format_invalid():
    payload = {
        "period_start": "2026-05-31",
        "period_end": "2026-05-01",  # reversed
        "usage": 30000,
        "usage_units": "kWh",
        "currency": "USD",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }
    result = validate(_reconciled(payload=payload))
    assert FlagType.FORMAT_INVALID in _flag_types(result)
