"""Tests for the SQLite-backed stores."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.db.store import AuditLogStore, MeterHistoryStore
from src.models.audit import AuditEntry
from src.models.entities import (
    Account,
    AccountType,
    LandlordOrTenant,
    Meter,
    MeterType,
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


@pytest.fixture
def store(tmp_path):
    s = MeterHistoryStore(tmp_path / "scratch.db")
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def audit_store(tmp_path):
    s = AuditLogStore(tmp_path / "scratch.db")
    try:
        yield s
    finally:
        s.close()


def _site(name: str = "HQ") -> Site:
    return Site(id=0, name=name, portfolio_id=1, region=Region.US)


def _account(site_id: int, number: str = "A-001") -> Account:
    return Account(
        id=0,
        account_number=number,
        account_type=AccountType.BILL_UPLOAD,
        site_id=site_id,
        generation_account=False,
    )


def _meter(account_id: int, meter_string: str = "MSR.(pge)(A001):(M1)") -> Meter:
    return Meter(
        id=0,
        meter_id_string=meter_string,
        account_id=account_id,
        unit=Unit.KWH,
        currency="USD",
        type=MeterType.ELECTRIC,
        landlord_or_tenant=LandlordOrTenant.LANDLORD,
        active=True,
        start_date=date(2020, 1, 1),
    )


def _reading(meter_id: int, year: int = 2024, month: int = 1) -> Reading:
    return Reading(
        id=0,
        meter_id=meter_id,
        period_start=date(year, month, 1),
        period_end=date(year, month, 28),
        usage=1234.5,
        usage_units=Unit.KWH,
        cost=210.0,
        currency="USD",
    )


def test_full_hierarchy_roundtrip(store):
    site_id = store.add_site(_site())
    fetched = store.get_site_by_name("HQ")
    assert fetched is not None
    assert fetched.id == site_id
    assert fetched.region is Region.US
    assert fetched.portfolio_id == 1

    acct_id = store.add_account(_account(site_id))
    assert acct_id > 0

    meter_id = store.add_meter(_meter(acct_id))
    assert meter_id > 0

    r1 = store.add_reading(_reading(meter_id, month=1), source_mode="FIXTURE")
    r2 = store.add_reading(_reading(meter_id, month=2), source_mode="FIXTURE")
    r3 = store.add_reading(_reading(meter_id, month=3), source_mode="FIXTURE")
    assert r1 != r2 != r3

    priors = store.get_prior_readings(meter_id, limit=12)
    assert len(priors) == 3
    # Sorted by period_end DESC: Mar, Feb, Jan.
    assert priors[0].period_end == date(2024, 3, 28)
    assert priors[1].period_end == date(2024, 2, 28)
    assert priors[2].period_end == date(2024, 1, 28)
    # Round-trip of types and optional fields.
    assert priors[0].usage_units is Unit.KWH
    assert priors[0].currency == "USD"
    assert priors[0].cost == 210.0


def test_find_meter_matches_all_three_keys(store):
    site_id = store.add_site(_site(name="Building A"))
    acct_id = store.add_account(_account(site_id, number="A-123"))
    store.add_meter(_meter(acct_id, meter_string="MSR.(pge)(A123):(M1)"))

    hit = store.find_meter("MSR.(pge)(A123):(M1)", "A-123", "Building A")
    assert hit is not None
    assert hit.meter_id_string == "MSR.(pge)(A123):(M1)"
    assert hit.unit is Unit.KWH

    # Any one wrong field -> miss.
    assert store.find_meter("nope", "A-123", "Building A") is None
    assert store.find_meter("MSR.(pge)(A123):(M1)", "nope", "Building A") is None
    assert store.find_meter("MSR.(pge)(A123):(M1)", "A-123", "nope") is None


def test_get_prior_readings_respects_limit(store):
    site_id = store.add_site(_site())
    acct_id = store.add_account(_account(site_id))
    meter_id = store.add_meter(_meter(acct_id))
    for month in range(1, 13):
        store.add_reading(_reading(meter_id, month=month), source_mode="FIXTURE")

    assert len(store.get_prior_readings(meter_id, limit=5)) == 5
    assert len(store.get_prior_readings(meter_id, limit=20)) == 12


def test_add_reading_requires_source_mode(store):
    """Per DESIGN.md §4 the add_reading contract makes source_mode a
    keyword-required argument with no default; pipeline writes must pass
    it explicitly so the audit log records how the reading entered the
    system. Omitting it is a programming error, not a runtime fallback.
    """

    site_id = store.add_site(_site())
    acct_id = store.add_account(_account(site_id))
    meter_id = store.add_meter(_meter(acct_id))

    with pytest.raises(TypeError):
        store.add_reading(_reading(meter_id))  # type: ignore[call-arg]


def test_meter_optional_end_date_round_trips(store):
    site_id = store.add_site(_site())
    acct_id = store.add_account(_account(site_id))
    m = _meter(acct_id)
    m_id = store.add_meter(m)
    # Round-trip via find_meter.
    found = store.find_meter(m.meter_id_string, "A-001", "HQ")
    assert found is not None
    assert found.id == m_id
    assert found.end_date is None


def test_audit_log_record_and_query_round_trips_payload(audit_store):
    decision = TriageDecision(
        route=TriageRoute.ESCALATE,
        routing_key=RoutingKey.OVERLAP,
        reasoning="overlap with prior reading",
    )
    flag = QualityFlag(
        type=FlagType.OVERLAP,
        severity=Severity.HIGH,
        description="period overlaps prior reading by 5 days",
        recommended_action="escalate",
    )
    entry = AuditEntry(
        bill_external_ref="bill-001",
        batch_id="batch-X",
        timestamp=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
        source_mode="JSON_ROW",
        normalized_fields={"usage": 1234.5, "usage_units": "kWh"},
        structural_signals={"usage_numeric": True, "period_in_range": True},
        reconciliation_result={"matched": True, "meter_id": 1},
        flags=[flag],
        triage_decision=decision,
        output_payload=None,
    )

    audit_id = audit_store.record(entry)
    assert audit_id > 0

    by_bill = audit_store.get_by_bill_ref("bill-001")
    assert len(by_bill) == 1
    fetched = by_bill[0]
    assert fetched.id == audit_id
    assert fetched.bill_external_ref == "bill-001"
    assert fetched.batch_id == "batch-X"
    assert fetched.triage_decision.route is TriageRoute.ESCALATE
    assert fetched.triage_decision.routing_key is RoutingKey.OVERLAP
    assert fetched.flags[0].type is FlagType.OVERLAP
    assert fetched.flags[0].severity is Severity.HIGH
    assert fetched.normalized_fields["usage"] == 1234.5
    assert fetched.structural_signals["period_in_range"] is True
    assert fetched.reconciliation_result["meter_id"] == 1

    by_batch = audit_store.get_by_batch("batch-X")
    assert len(by_batch) == 1
    assert by_batch[0].id == audit_id


def test_audit_log_minimal_entry_without_routing_key(audit_store):
    entry = AuditEntry(
        bill_external_ref="bill-clean",
        timestamp=datetime(2024, 2, 1, 9, 30, tzinfo=timezone.utc),
        source_mode="JSON_ROW",
        triage_decision=TriageDecision(route=TriageRoute.AUTO_RESOLVE, reasoning="all clean"),
    )
    audit_store.record(entry)
    by_bill = audit_store.get_by_bill_ref("bill-clean")
    assert len(by_bill) == 1
    assert by_bill[0].triage_decision.route is TriageRoute.AUTO_RESOLVE
    assert by_bill[0].triage_decision.routing_key is None
    assert by_bill[0].batch_id is None


def test_schema_idempotent_across_reopens(tmp_path):
    db_path = tmp_path / "scratch.db"
    # First open creates the schema.
    s1 = MeterHistoryStore(db_path)
    s1.add_site(_site())
    s1.close()
    # Second open re-runs schema; CREATE ... IF NOT EXISTS keeps it a no-op.
    s2 = MeterHistoryStore(db_path)
    assert s2.get_site_by_name("HQ") is not None
    s2.close()
    # Third open via the other store on the same DB.
    a = AuditLogStore(db_path)
    a.close()
