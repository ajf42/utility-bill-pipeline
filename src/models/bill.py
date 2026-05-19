"""Pipeline-stage models.

Each stage of the pipeline (Ingest -> Normalize -> Reconcile -> Validate)
consumes the prior stage's artifact and emits the next. Inheritance is used
to express the additive nature: ``NormalizedBill`` *is* a ``RawBillInput``
with normalization fields attached, and so on. This keeps every artifact
self-contained, serializable, and inspectable in the audit log.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .entities import Account, Meter, Reading
from .quality import QualityFlag


class SourceMode(str, Enum):
    """How the row entered the pipeline. Used by the audit log and triage."""

    JSON_ROW = "JSON_ROW"
    XLSX_ROW = "XLSX_ROW"


class RawBillInput(BaseModel):
    """The input to the pipeline before any processing.

    ``raw_payload`` is intentionally loose (``dict[str, Any]``) — we accept
    whatever came in and let normalization decide what to do with it.
    ``batch_id`` is populated only on the XLSX batch path; single-row JSON
    requests leave it ``None``.
    """

    source_mode: SourceMode
    raw_payload: dict[str, Any]
    batch_id: Optional[str] = None


class NormalizedBill(RawBillInput):
    """Post-normalization artifact.

    ``canonical_provider`` is the reference-library-resolved provider name.
    ``normalized_units`` records the unit-of-measure canonicalization
    decisions (e.g., ``{"usage": "kWh"}``). ``structural_signals`` carries
    the per-field structural quality signals described in DESIGN.md §4
    ("Normalization Service") — type/format parse, plausible-range, provider
    presence, within-row agreement. Consumed by triage.
    """

    canonical_provider: Optional[str] = None
    normalized_units: dict[str, str] = Field(default_factory=dict)
    structural_signals: dict[str, Any] = Field(default_factory=dict)


class ReconciledBill(NormalizedBill):
    """Post-reconciliation artifact.

    ``matched_meter`` is ``None`` when reconciliation could not resolve the
    incoming meter identifier; that case routes to Escalate with
    ``METER_UNASSIGNED``. ``matched_account`` is the parent Account fetched
    alongside the meter (None when ``matched_meter`` is None) — validation
    uses it for the ``GENERATION_MISMATCH`` check. ``prior_readings`` is
    the last N readings on the matched meter sorted by period (default
    N=12). ``prior_context`` is the summary the gap/overlap heuristics
    actually consume:
    ``{"prior_period_end": date | None, "count_of_prior_readings": int}``.
    """

    matched_meter: Optional[Meter] = None
    matched_account: Optional[Account] = None
    prior_readings: list[Reading] = Field(default_factory=list)
    prior_context: dict[str, Any] = Field(default_factory=dict)


class ValidatedBill(ReconciledBill):
    """Post-validation artifact. Hands the bill plus its flags to triage."""

    flags: list[QualityFlag] = Field(default_factory=list)
