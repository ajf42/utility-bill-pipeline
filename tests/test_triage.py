"""Tests for the Triage service.

Two tiers: routing logic (no drafter) and drafter integration (using
the FakeAnthropicClient from tests/fakes.py). No real API calls in the
default test run.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.models.bill import (
    NormalizedBill,
    ReconciledBill,
    SourceMode,
    ValidatedBill,
)
from src.models.drafter import DrafterOutput
from src.models.entities import (
    Account,
    AccountType,
    LandlordOrTenant,
    Meter,
    MeterType,
    Unit,
)
from src.models.quality import (
    FlagType,
    QualityFlag,
    RoutingKey,
    Severity,
    TriageRoute,
)
from src.services.drafter import DrafterParseError, DrafterService
from src.services.triage import TriageService
from tests.fakes import FakeAnthropicClient, FakeContentBlock, FakeMessage


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _meter(*, unit: Unit = Unit.KWH) -> Meter:
    return Meter(
        id=1,
        meter_id_string="MSR.(ConEd)(LT-ELEC-001):(M1)",
        account_id=1,
        unit=unit,
        currency="USD",
        type=MeterType.ELECTRIC,
        landlord_or_tenant=LandlordOrTenant.LANDLORD,
        active=True,
        start_date=date(2024, 1, 1),
    )


def _account() -> Account:
    return Account(
        id=1,
        account_number="LT-ELEC-001",
        account_type=AccountType.BILL_UPLOAD,
        site_id=1,
        generation_account=False,
    )


def _flag(flag_type: FlagType, severity: Severity) -> QualityFlag:
    return QualityFlag(
        type=flag_type,
        severity=severity,
        description=f"{flag_type.value} {severity.value}",
        recommended_action="see flag",
    )


def _bill(*, flags: list[QualityFlag], with_meter: bool = True) -> ValidatedBill:
    payload = {
        "site_name": "Liberty Tower",
        "account_number": "LT-ELEC-001",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "usage": 1200.0,
        "usage_units": "therms",
        "currency": "USD",
    }
    return ValidatedBill(
        source_mode=SourceMode.JSON_ROW,
        raw_payload=payload,
        matched_meter=_meter() if with_meter else None,
        matched_account=_account() if with_meter else None,
        flags=flags,
    )


def _canned_drafter_output_dict() -> dict:
    return {
        "proposed_action": "CONVERT_UNIT",
        "proposed_correction": {"usage_units": "kWh"},
        "draft_email_subject": "Likely unit-label error",
        "draft_email_body": "We observed therms on a kWh meter; please confirm.",
        "draft_email_recipient_type": "INTERNAL_TEAM",
        "basis_note": "Meter is locked to kWh; prior readings consistent.",
        "confidence_note": "Assumes value is correct; only label is suspect.",
    }


def _drafter_with_canned_response() -> tuple[DrafterService, FakeAnthropicClient]:
    client = FakeAnthropicClient()
    client.set_next_response(
        FakeMessage(
            content=[
                FakeContentBlock(
                    type="tool_use",
                    name="draft_resolution",
                    input=_canned_drafter_output_dict(),
                )
            ]
        )
    )
    service = DrafterService(client=client)  # type: ignore[arg-type]
    return service, client


# ---------------------------------------------------------------------------
# Routing logic (no drafter)
# ---------------------------------------------------------------------------


def test_auto_resolve_when_no_flags():
    decision = TriageService().triage(_bill(flags=[]))
    assert decision.route is TriageRoute.AUTO_RESOLVE
    assert decision.drafter_output is None


def test_auto_resolve_with_one_medium_flag():
    decision = TriageService().triage(
        _bill(flags=[_flag(FlagType.GAP, Severity.MEDIUM)])
    )
    assert decision.route is TriageRoute.AUTO_RESOLVE


def test_escalate_when_meter_unassigned():
    bill = _bill(flags=[_flag(FlagType.METER_UNASSIGNED, Severity.HIGH)], with_meter=False)
    decision = TriageService().triage(bill)
    assert decision.route is TriageRoute.ESCALATE
    assert decision.routing_key is RoutingKey.METER_UNASSIGNED


def test_escalate_on_overlap_high():
    decision = TriageService().triage(
        _bill(flags=[_flag(FlagType.OVERLAP, Severity.HIGH)])
    )
    assert decision.route is TriageRoute.ESCALATE
    assert decision.routing_key is RoutingKey.OVERLAP


def test_escalate_on_currency_mismatch_high():
    decision = TriageService().triage(
        _bill(flags=[_flag(FlagType.CURRENCY_MISMATCH, Severity.HIGH)])
    )
    assert decision.route is TriageRoute.ESCALATE
    assert decision.routing_key is RoutingKey.FORMAT_MISMATCH


def test_draft_for_human_review_on_unit_mismatch_only_high():
    decision = TriageService().triage(
        _bill(flags=[_flag(FlagType.UNIT_MISMATCH, Severity.HIGH)])
    )
    assert decision.route is TriageRoute.DRAFT_FOR_HUMAN_REVIEW
    assert decision.drafter_output is None


def test_escalate_on_three_mediums():
    decision = TriageService().triage(
        _bill(
            flags=[
                _flag(FlagType.GAP, Severity.MEDIUM),
                _flag(FlagType.GAP, Severity.MEDIUM),
                _flag(FlagType.GAP, Severity.MEDIUM),
            ]
        )
    )
    assert decision.route is TriageRoute.ESCALATE
    assert decision.routing_key is RoutingKey.UNCATEGORIZED


def test_draft_for_human_review_on_two_mediums():
    decision = TriageService().triage(
        _bill(
            flags=[
                _flag(FlagType.GAP, Severity.MEDIUM),
                _flag(FlagType.GAP, Severity.MEDIUM),
            ]
        )
    )
    assert decision.route is TriageRoute.DRAFT_FOR_HUMAN_REVIEW


# ---------------------------------------------------------------------------
# Drafter integration
# ---------------------------------------------------------------------------


def test_triage_with_drafter_populates_drafter_output():
    drafter, client = _drafter_with_canned_response()
    decision = TriageService(drafter=drafter).triage(
        _bill(flags=[_flag(FlagType.UNIT_MISMATCH, Severity.HIGH)])
    )
    assert decision.route is TriageRoute.DRAFT_FOR_HUMAN_REVIEW
    assert isinstance(decision.drafter_output, DrafterOutput)
    assert decision.drafter_output.proposed_correction == {"usage_units": "kWh"}
    # The drafter was actually called.
    assert client.last_call_kwargs is not None


def test_triage_with_drafter_failure_escalates_with_drafter_failure_key(caplog):
    class _RaisingDrafter:
        def draft(self, *args, **kwargs):
            raise DrafterParseError("test failure", raw_response=None)

    bill = _bill(flags=[_flag(FlagType.UNIT_MISMATCH, Severity.HIGH)])
    decision = TriageService(drafter=_RaisingDrafter()).triage(bill)  # type: ignore[arg-type]

    assert decision.route is TriageRoute.ESCALATE
    assert decision.routing_key is RoutingKey.DRAFTER_FAILURE
    # The exception message is preserved as a flag on the validated bill.
    drafter_flags = [f for f in bill.flags if f.type is FlagType.DRAFTER_FAILURE]
    assert len(drafter_flags) == 1
    assert "DrafterParseError" in drafter_flags[0].description


def test_triage_with_no_drafter_on_draft_route_logs_warning(caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="src.services.triage")
    decision = TriageService(drafter=None).triage(
        _bill(flags=[_flag(FlagType.UNIT_MISMATCH, Severity.HIGH)])
    )
    assert decision.route is TriageRoute.DRAFT_FOR_HUMAN_REVIEW
    assert decision.drafter_output is None
    assert any("no drafter attached" in r.message for r in caplog.records)
