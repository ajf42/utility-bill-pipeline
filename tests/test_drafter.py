"""Tests for the Resolution Drafter service.

Four tiers per the build conventions:
- Tier 1 (contract): the pydantic model round-trips and exposes a usable
  JSON schema for the Anthropic tool input_schema.
- Tier 2 (behavior): the service parses a canned tool_use response,
  fails loud on validation errors, fails loud when no tool_use block
  is present, and builds a user message that includes meter context.
- Tier 4 (integration): a single live-API test, marked and skipped when
  ANTHROPIC_API_KEY is not set. Default `pytest` runs skip this tier
  via the addopts in pyproject.toml.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from src.models.bill import (
    NormalizedBill,
    RawBillInput,
    ReconciledBill,
    SourceMode,
    ValidatedBill,
)
from src.models.drafter import DrafterOutput, EmailRecipientType, ProposedAction
from src.models.entities import (
    LandlordOrTenant,
    Meter,
    MeterType,
    Reading,
    Unit,
)
from src.models.quality import FlagType, QualityFlag, Severity
from src.services.drafter import (
    DrafterParseError,
    DrafterService,
    build_drafter_user_message,
)
from tests.fakes import FakeAnthropicClient, FakeContentBlock, FakeMessage


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _meter() -> Meter:
    return Meter(
        id=1,
        meter_id_string="MSR.(ConEd)(LT-ELEC-001):(M1)",
        account_id=1,
        unit=Unit.KWH,
        currency="USD",
        type=MeterType.ELECTRIC,
        landlord_or_tenant=LandlordOrTenant.LANDLORD,
        active=True,
        start_date=date(2024, 1, 1),
    )


def _unit_mismatch_bill() -> ValidatedBill:
    payload = {
        "site_name": "Liberty Tower",
        "account_number": "LT-ELEC-001",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "usage": 1200.0,
        "usage_units": "therms",
        "cost": 200.0,
        "currency": "USD",
    }
    return ValidatedBill(
        source_mode=SourceMode.JSON_ROW,
        raw_payload=payload,
        canonical_provider="ConEd",
        flags=[
            QualityFlag(
                type=FlagType.UNIT_MISMATCH,
                severity=Severity.HIGH,
                description="Reading reports therms but meter is locked to kWh",
                recommended_action="Propose unit relabel to kWh",
            )
        ],
    )


def _canned_drafter_input() -> dict:
    return {
        "proposed_action": "CONVERT_UNIT",
        "proposed_correction": {"usage_units": "kWh"},
        "draft_email_subject": "Likely unit-label error on ConEd bill",
        "draft_email_body": (
            "Hello team, the April 2026 reading on Liberty Tower's main "
            "electric meter arrived labeled as therms. The meter is "
            "locked to kWh and prior readings are in the kWh range, so "
            "this looks like an upload-row mislabel rather than a real "
            "fuel change. Could you confirm before we approve the relabel?"
        ),
        "draft_email_recipient_type": "INTERNAL_TEAM",
        "basis_note": (
            "Meter MSR.(ConEd)(LT-ELEC-001):(M1) is locked to kWh and "
            "prior readings are 800-1500 kWh. ConEd reports electric "
            "usage in kWh. The incoming value 1200 therms is consistent "
            "with a label-only error, so CONVERT_UNIT is proposed with a "
            "usage_units override."
        ),
        "confidence_note": "Assumes numeric value is correct; only the label is suspect.",
    }


def _tool_use_response(input_payload: dict) -> FakeMessage:
    return FakeMessage(
        content=[
            FakeContentBlock(type="tool_use", name="draft_resolution", input=input_payload)
        ]
    )


# ---------------------------------------------------------------------------
# Tier 1 — contract
# ---------------------------------------------------------------------------


def test_drafter_output_round_trip() -> None:
    raw = _canned_drafter_input()
    out = DrafterOutput.model_validate(raw)
    assert out.proposed_action is ProposedAction.CONVERT_UNIT
    assert out.draft_email_recipient_type is EmailRecipientType.INTERNAL_TEAM
    assert out.proposed_correction == {"usage_units": "kWh"}
    # Serialization round-trip.
    again = DrafterOutput.model_validate(out.model_dump(mode="json"))
    assert again == out


def test_drafter_output_json_schema_has_expected_properties() -> None:
    schema = DrafterOutput.model_json_schema()
    assert schema["type"] == "object"
    props = set(schema["properties"].keys())
    expected = {
        "proposed_action",
        "proposed_correction",
        "draft_email_subject",
        "draft_email_body",
        "draft_email_recipient_type",
        "basis_note",
        "confidence_note",
    }
    assert expected.issubset(props)


# ---------------------------------------------------------------------------
# Tier 2 — behavior
# ---------------------------------------------------------------------------


def test_draft_returns_parsed_output_on_canned_tool_use(tmp_path) -> None:
    client = FakeAnthropicClient()
    client.set_next_response(_tool_use_response(_canned_drafter_input()))
    service = DrafterService(client=client)  # type: ignore[arg-type]

    out = service.draft(_unit_mismatch_bill(), _meter(), prior_readings=[])

    assert isinstance(out, DrafterOutput)
    assert out.proposed_action is ProposedAction.CONVERT_UNIT
    assert out.proposed_correction == {"usage_units": "kWh"}
    # The service forced the tool.
    assert client.last_call_kwargs is not None
    assert client.last_call_kwargs["tool_choice"] == {
        "type": "tool",
        "name": "draft_resolution",
    }


def test_draft_raises_parse_error_on_invalid_tool_input() -> None:
    bad = _canned_drafter_input()
    bad["proposed_action"] = "NOT_A_REAL_ACTION"
    client = FakeAnthropicClient()
    client.set_next_response(_tool_use_response(bad))
    service = DrafterService(client=client)  # type: ignore[arg-type]

    with pytest.raises(DrafterParseError) as exc_info:
        service.draft(_unit_mismatch_bill(), _meter(), prior_readings=[])
    assert exc_info.value.raw_response is not None


def test_draft_raises_parse_error_when_no_tool_use_block() -> None:
    text_only = FakeMessage(content=[FakeContentBlock(type="text", text="hello")])
    client = FakeAnthropicClient()
    client.set_next_response(text_only)
    service = DrafterService(client=client)  # type: ignore[arg-type]

    with pytest.raises(DrafterParseError):
        service.draft(_unit_mismatch_bill(), _meter(), prior_readings=[])


def test_build_user_message_includes_meter_unit_and_flag() -> None:
    bill = _unit_mismatch_bill()
    meter = _meter()
    msg = build_drafter_user_message(bill, meter, history=[])

    # Meter's locked unit must be in the rendered text.
    assert "kWh" in msg
    # The specific flag must be visible.
    assert "UNIT_MISMATCH" in msg
    assert meter.meter_id_string in msg


# ---------------------------------------------------------------------------
# Tier 4 — integration (live API)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_draft_against_live_api_on_unit_mismatch() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")

    import anthropic

    service = DrafterService(client=anthropic.Anthropic(api_key=api_key))
    out = service.draft(_unit_mismatch_bill(), _meter(), prior_readings=[])

    assert isinstance(out, DrafterOutput)
    assert out.proposed_action in set(ProposedAction)
    assert out.draft_email_body.strip() != ""
    assert out.basis_note.strip() != ""
