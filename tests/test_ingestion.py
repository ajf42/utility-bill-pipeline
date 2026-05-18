"""Tests for the ingestion service.

Pre-pipeline boundary: ``ingest_json_row`` only constructs a RawBillInput
and enforces presence of the required keys. Type coercion, parse-checks,
and provider canonicalization belong to the normalization stage and are
not exercised here.
"""

from __future__ import annotations

import pytest

from src.models.bill import RawBillInput, SourceMode
from src.services.ingestion import ingest_json_row


def _minimum_valid_payload() -> dict:
    return {
        "period_start": "2026-04-01",
        "period_end": "2026-05-01",
        "usage": 28000,
        "usage_units": "kWh",
        "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
        "account_number": "LT-ELEC-001",
        "site_name": "Liberty Tower",
    }


def test_ingest_json_row_returns_raw_bill_input_with_expected_shape():
    payload = _minimum_valid_payload()

    raw = ingest_json_row(payload)

    assert isinstance(raw, RawBillInput)
    assert raw.source_mode is SourceMode.JSON_ROW
    assert raw.batch_id is None
    assert raw.raw_payload == payload


def test_ingest_json_row_copies_payload_not_aliases_it():
    payload = _minimum_valid_payload()

    raw = ingest_json_row(payload)
    payload["usage"] = 999_999  # mutate the caller's dict afterwards

    assert raw.raw_payload["usage"] == 28000


@pytest.mark.parametrize(
    "missing_field",
    [
        "period_start",
        "period_end",
        "usage",
        "usage_units",
        "meter_id_string",
        "account_number",
        "site_name",
    ],
)
def test_ingest_json_row_raises_on_missing_required_field(missing_field):
    payload = _minimum_valid_payload()
    payload.pop(missing_field)

    with pytest.raises(ValueError) as excinfo:
        ingest_json_row(payload)

    assert missing_field in str(excinfo.value)


def test_ingest_json_row_preserves_optional_fields_when_present():
    payload = _minimum_valid_payload()
    payload.update(
        cost=3612.0,
        currency="USD",
        demand_kw=125.4,
        demand_spend=540.0,
        energy_exported=0.0,
    )

    raw = ingest_json_row(payload)

    assert raw.raw_payload["cost"] == 3612.0
    assert raw.raw_payload["currency"] == "USD"
    assert raw.raw_payload["demand_kw"] == 125.4
    assert raw.raw_payload["demand_spend"] == 540.0
    assert raw.raw_payload["energy_exported"] == 0.0


def test_ingest_json_row_rejects_non_dict_payload():
    with pytest.raises(ValueError):
        ingest_json_row("not a dict")  # type: ignore[arg-type]
