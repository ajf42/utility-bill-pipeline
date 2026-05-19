"""Tests for the pydantic data models.

These cover instantiation, enum/format validation, roundtrip
(model_dump -> model_validate), and the deliberate non-validation of
cross-field constraints (see ADR-006).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from src.models.audit import AuditEntry
from src.models.bill import (
    NormalizedBill,
    RawBillInput,
    ReconciledBill,
    SourceMode,
    ValidatedBill,
)
from src.models.entities import (
    Account,
    AccountType,
    LandlordOrTenant,
    Meter,
    MeterType,
    Portfolio,
    Reading,
    Region,
    Site,
    Unit,
)
from src.models.quality import (
    FlagType,
    QualityFlag,
    RoutingKey,
    Severity,
    TriageDecision,
    TriageRoute,
)


def _meter(unit: Unit = Unit.KWH, currency: str = "USD") -> Meter:
    return Meter(
        id=1,
        meter_id_string="MSR.(PG&E)(123):(M1)",
        account_id=1,
        unit=unit,
        currency=currency,
        type=MeterType.ELECTRIC,
        landlord_or_tenant=LandlordOrTenant.LANDLORD,
        active=True,
        start_date=date(2020, 1, 1),
    )


def _reading(meter_id: int = 1, usage_units: Unit = Unit.KWH, currency: str = "USD") -> Reading:
    return Reading(
        id=1,
        meter_id=meter_id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 31),
        usage=1234.5,
        usage_units=usage_units,
        cost=210.0,
        currency=currency,
    )


def test_portfolio_roundtrip():
    p = Portfolio(id=1, name="Acme REIT")
    assert Portfolio.model_validate(p.model_dump()) == p


def test_site_requires_known_region():
    Site(id=1, name="HQ", portfolio_id=1, region=Region.US)
    with pytest.raises(ValidationError):
        Site(id=1, name="HQ", portfolio_id=1, region="MARS")


def test_account_type_enum_enforced():
    a = Account(
        id=1,
        account_number="A-123",
        account_type=AccountType.MANUAL,
        site_id=1,
        generation_account=False,
    )
    assert a.account_type is AccountType.MANUAL
    with pytest.raises(ValidationError):
        Account(id=1, account_number="A-123", account_type="weird", site_id=1)


def test_meter_currency_must_be_iso_4217_shape():
    _meter(currency="USD")
    for bad in ["usd", "USDX", "12", "dollars"]:
        with pytest.raises(ValidationError):
            _meter(currency=bad)


def test_meter_unit_must_be_known_enum():
    with pytest.raises(ValidationError):
        Meter(
            id=1,
            meter_id_string="MSR.(p)(1):(1)",
            account_id=1,
            unit="megawatts",
            currency="USD",
            type=MeterType.ELECTRIC,
            landlord_or_tenant=LandlordOrTenant.LANDLORD,
            active=True,
            start_date=date(2020, 1, 1),
        )


def test_meter_optional_end_date_defaults_none():
    m = _meter()
    assert m.end_date is None


def test_reading_roundtrip():
    r = _reading()
    dumped = r.model_dump(mode="json")
    assert Reading.model_validate(dumped) == r


def test_reading_unit_currency_independent_of_parent_meter():
    """ADR-006 — cross-field validation between Reading.usage_units /
    Reading.currency and the parent Meter.unit / Meter.currency is the
    validation service's responsibility, not the pydantic model's. The model
    must accept the mismatch so the validation service can emit a
    UNIT_MISMATCH or CURRENCY_MISMATCH flag and triage can route it. This
    test pins that intentional behavior — if it ever starts raising, ADR-006
    has been violated and the validation service is being short-circuited.
    """
    parent = _meter(unit=Unit.KWH, currency="USD")

    Reading(
        id=1,
        meter_id=parent.id,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 31),
        usage=1.0,
        usage_units=Unit.THERMS,
        currency="EUR",
    )


def test_quality_flag_instantiation():
    f = QualityFlag(
        type=FlagType.OVERLAP,
        severity=Severity.HIGH,
        description="period overlaps existing reading by 5 days",
        recommended_action="escalate",
    )
    assert f.type is FlagType.OVERLAP
    assert f.severity is Severity.HIGH


def test_triage_decision_minimal_and_full():
    minimal = TriageDecision(route=TriageRoute.AUTO_RESOLVE, reasoning="all signals pass")
    assert minimal.routing_key is None
    assert minimal.drafter_output is None

    full = TriageDecision(
        route=TriageRoute.ESCALATE,
        routing_key=RoutingKey.OVERLAP,
        reasoning="overlap detected with prior reading",
        drafter_output=None,
    )
    assert full.routing_key is RoutingKey.OVERLAP


def test_pipeline_stages_inherit_and_roundtrip():
    raw = RawBillInput(source_mode=SourceMode.JSON_ROW, raw_payload={"usage": "1234.5"})
    norm = NormalizedBill(
        source_mode=SourceMode.JSON_ROW,
        raw_payload={"usage": "1234.5"},
        canonical_provider="PG&E",
        normalized_units={"usage": "kWh"},
        structural_signals={"usage_numeric": True, "period_days_in_range": True},
    )
    rec = ReconciledBill(
        source_mode=SourceMode.JSON_ROW,
        raw_payload={"usage": "1234.5"},
        matched_meter=_meter(),
        prior_readings=[_reading()],
        prior_context={"prior_period_end": "2023-12-31", "count_of_prior_readings": 1},
    )
    val = ValidatedBill(
        source_mode=SourceMode.JSON_ROW,
        raw_payload={"usage": "1234.5"},
        flags=[
            QualityFlag(
                type=FlagType.GAP,
                severity=Severity.MEDIUM,
                description="3-day gap from prior reading",
                recommended_action="draft_for_human_review",
            )
        ],
    )

    for m in (raw, norm, rec, val):
        roundtripped = type(m).model_validate(m.model_dump(mode="json"))
        assert roundtripped == m


def test_audit_entry_roundtrip_with_json_mode():
    entry = AuditEntry(
        bill_external_ref="bill-001",
        batch_id=None,
        timestamp=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
        source_mode="JSON_ROW",
        normalized_fields={"usage": 1234.5, "usage_units": "kWh"},
        structural_signals={"usage_numeric": True},
        reconciliation_result={"matched": True, "meter_id": 1},
        flags=[
            QualityFlag(
                type=FlagType.UNIT_MISMATCH,
                severity=Severity.HIGH,
                description="reading is therms but meter is kWh",
                recommended_action="escalate",
            )
        ],
        triage_decision=TriageDecision(
            route=TriageRoute.ESCALATE,
            routing_key=RoutingKey.FORMAT_MISMATCH,
            reasoning="unit mismatch high-severity",
        ),
        output_payload=None,
    )
    assert AuditEntry.model_validate(entry.model_dump(mode="json")) == entry
