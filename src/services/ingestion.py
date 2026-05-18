"""Ingestion service.

The first stage of the pipeline (DESIGN.md §4 "High-Level Flow", step 1).
Its only job is to take a raw payload off the wire and produce a
``RawBillInput`` for the rest of the pipeline to consume. No parsing of
field types, no canonicalization, no validation beyond presence of the
required keys — those concerns live in normalization and downstream.
"""

from __future__ import annotations

from typing import Any

from src.models.bill import RawBillInput, SourceMode


# Required keys on a JSON-row body. Names match the canonical reading schema
# described in DESIGN.md §4 ("Reading" field detail) — period dates, usage,
# usage units, and the three identifiers that reconciliation needs (meter,
# account, site).
_REQUIRED_FIELDS: tuple[str, ...] = (
    "period_start",
    "period_end",
    "usage",
    "usage_units",
    "meter_id_string",
    "account_number",
    "site_name",
)


def ingest_json_row(payload: dict[str, Any]) -> RawBillInput:
    """Wrap a JSON-row body in a ``RawBillInput`` for the pipeline.

    Raises ``ValueError`` if any required field is missing. The route
    handler catches that at the boundary and returns 422; everything past
    this point (normalization onward) can assume the required keys exist
    (though not yet that they are well-typed — that is normalization's
    structural-quality job).
    """

    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    missing = [field for field in _REQUIRED_FIELDS if field not in payload]
    if missing:
        raise ValueError(
            f"Missing required field(s): {', '.join(missing)}."
        )

    return RawBillInput(
        source_mode=SourceMode.JSON_ROW,
        raw_payload=dict(payload),
        batch_id=None,
    )
