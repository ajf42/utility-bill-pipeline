"""POST /bills — full pipeline including triage and audit.

End-to-end flow per DESIGN.md §4: ingest -> normalize -> reconcile ->
validate -> triage (with drafter wired in on the DraftForHumanReview
route) -> audit-log write. Plus the human approval loop:

- POST /bills/{audit_ref}/approve — apply the drafter's
  ``proposed_correction`` and write the resulting reading.
- POST /bills/{audit_ref}/reject — record the rejection with reason.

The Anthropic client is constructed once at app startup (see
[src/main.py]) and injected through ``get_drafter``. The route handler
never touches an API key.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from src.db.store import AuditLogStore, MeterHistoryStore
from src.models.audit import AuditEntry
from src.models.bill import SourceMode, ValidatedBill
from src.models.entities import Reading, Unit
from src.models.quality import TriageRoute
from src.routes.dependencies import get_audit_store, get_drafter, get_store
from src.services.drafter import DrafterService
from src.services.ingestion import ingest_json_row
from src.services.normalization import normalize
from src.services.reconciliation import reconcile
from src.services.triage import TriageService
from src.services.validation import validate
from src.util.logging import StageTimer, get_logger

_logger = get_logger("pipeline")

router = APIRouter()

# Reading-level keys (subset of raw_payload) that the drafter may legally
# override on approval. Anything outside this set is a 422 — see the
# "validates each field" rule in the prompt.
_APPROVABLE_FIELDS = frozenset(
    {
        "period_start",
        "period_end",
        "usage",
        "usage_units",
        "cost",
        "currency",
        "demand_kw",
        "demand_spend",
        "energy_exported",
    }
)


@router.post("/bills")
def post_bill(
    payload: dict[str, Any],
    store: MeterHistoryStore = Depends(get_store),
    audit_store: AuditLogStore = Depends(get_audit_store),
    drafter: Optional[DrafterService] = Depends(get_drafter),
) -> dict[str, Any]:
    """Accept a single bill row, run the full pipeline, record the audit
    entry, and return the assembled artifacts plus ``audit_ref``.

    ``audit_ref`` is the ``bill_external_ref`` UUID that subsequent
    approve/reject calls use to locate this bill's audit row.
    """

    audit_ref = str(uuid.uuid4())

    try:
        raw_input = ingest_json_row(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with StageTimer(_logger, stage="normalize", bill_ref=audit_ref) as t:
        normalized = normalize(raw_input)
        t.set(provider_known=normalized.structural_signals.get("provider_known"))
    with StageTimer(_logger, stage="reconcile", bill_ref=audit_ref) as t:
        reconciled = reconcile(normalized, store)
        t.set(matched_meter=reconciled.matched_meter is not None)
    with StageTimer(_logger, stage="validate", bill_ref=audit_ref) as t:
        validated = validate(reconciled)
        t.set(flag_count=len(validated.flags))
    with StageTimer(_logger, stage="triage", bill_ref=audit_ref) as t:
        decision = TriageService(drafter=drafter).triage(validated)
        t.set(
            route=decision.route.value,
            routing_key=decision.routing_key.value if decision.routing_key else None,
            drafter_called=decision.drafter_output is not None,
        )
    entry = AuditEntry(
        bill_external_ref=audit_ref,
        batch_id=raw_input.batch_id,
        timestamp=datetime.now(timezone.utc),
        source_mode=raw_input.source_mode.value,
        normalized_fields=normalized.model_dump(mode="json"),
        structural_signals=normalized.structural_signals,
        reconciliation_result=_reconciliation_snapshot(validated),
        flags=validated.flags,
        triage_decision=decision,
        drafter_output=decision.drafter_output,
    )
    audit_store.record(entry)

    return {
        "audit_ref": audit_ref,
        "raw_input": raw_input.model_dump(mode="json"),
        "normalized": normalized.model_dump(mode="json"),
        "reconciled": reconciled.model_dump(mode="json"),
        "validated": validated.model_dump(mode="json"),
        "triage": decision.model_dump(mode="json"),
        "pipeline_status": "triaged",
    }


@router.post("/bills/{audit_ref}/approve")
def approve_bill(
    audit_ref: str,
    store: MeterHistoryStore = Depends(get_store),
    audit_store: AuditLogStore = Depends(get_audit_store),
) -> dict[str, Any]:
    """Approve a DraftForHumanReview bill.

    Applies the drafter's ``proposed_correction`` to a copy of the
    original raw_payload, builds a Reading, persists it with
    ``source_mode=DRAFTER_APPROVED``, and writes a follow-up AuditEntry
    linked via ``parent_bill_external_ref``. The original audit entry is
    never mutated.

    Per the design constraint in this prompt: approval does NOT re-run
    validation on the corrected bill. The human approving has decided to
    trust the correction; re-running would just re-flag the same issue
    and loop. The follow-up audit entry carries the corrected and the
    original payloads so a reviewer can reconstruct what changed.
    """

    with StageTimer(_logger, stage="approval", bill_ref=audit_ref) as timer:
        return _approve_bill_impl(audit_ref, store, audit_store, timer)


def _approve_bill_impl(
    audit_ref: str,
    store: MeterHistoryStore,
    audit_store: AuditLogStore,
    timer: StageTimer,
) -> dict[str, Any]:
    original = _load_original_entry(audit_store, audit_ref)
    if original.triage_decision.route is not TriageRoute.DRAFT_FOR_HUMAN_REVIEW:
        timer.set(outcome="rejected:wrong_route")
        raise HTTPException(
            status_code=409,
            detail=(
                f"audit_ref {audit_ref} is on route "
                f"{original.triage_decision.route.value}; only "
                f"DRAFT_FOR_HUMAN_REVIEW entries can be approved"
            ),
        )
    if original.drafter_output is None:
        raise HTTPException(
            status_code=422,
            detail=f"audit_ref {audit_ref} has no drafter_output to apply",
        )

    original_payload = dict(original.normalized_fields.get("raw_payload", {}))
    correction = original.drafter_output.proposed_correction

    unknown = set(correction) - _APPROVABLE_FIELDS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"proposed_correction contains unknown field(s): {sorted(unknown)}",
        )

    corrected_payload = {**original_payload, **correction}

    matched_meter_id = original.reconciliation_result.get("matched_meter_id")
    matched_meter_currency = original.reconciliation_result.get("matched_meter_currency")
    if matched_meter_id is None:
        raise HTTPException(
            status_code=422,
            detail=f"audit_ref {audit_ref} has no matched_meter_id; cannot write reading",
        )

    try:
        reading = _reading_from_payload(
            payload=corrected_payload,
            meter_id=int(matched_meter_id),
            fallback_currency=matched_meter_currency,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"could not construct Reading from corrected payload: {exc}",
        ) from exc

    reading_id = store.add_reading(
        reading,
        source_mode=SourceMode.DRAFTER_APPROVED.value,
        batch_id=None,
    )

    followup_ref = str(uuid.uuid4())
    followup = AuditEntry(
        bill_external_ref=followup_ref,
        parent_bill_external_ref=audit_ref,
        batch_id=None,
        timestamp=datetime.now(timezone.utc),
        source_mode=SourceMode.DRAFTER_APPROVED.value,
        normalized_fields={
            "original_payload": original_payload,
            "corrected_payload": corrected_payload,
            "applied_correction": correction,
        },
        structural_signals={},
        reconciliation_result={
            "matched_meter_id": matched_meter_id,
            "reading_id": reading_id,
        },
        flags=[],
        triage_decision=original.triage_decision,
        drafter_output=original.drafter_output,
        output_payload=reading.model_dump(mode="json"),
    )
    audit_store.record(followup)

    timer.set(outcome="approved", reading_id=reading_id, followup_audit_ref=followup_ref)
    return {"reading_id": reading_id, "audit_ref": followup_ref}


@router.post("/bills/{audit_ref}/reject")
def reject_bill(
    audit_ref: str,
    body: Optional[dict[str, Any]] = Body(default=None),
    audit_store: AuditLogStore = Depends(get_audit_store),
) -> dict[str, Any]:
    """Reject a DraftForHumanReview bill. No reading is written; a
    follow-up AuditEntry records the rejection and the reason.
    """

    original = _load_original_entry(audit_store, audit_ref)
    if original.triage_decision.route is not TriageRoute.DRAFT_FOR_HUMAN_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=(
                f"audit_ref {audit_ref} is on route "
                f"{original.triage_decision.route.value}; only "
                f"DRAFT_FOR_HUMAN_REVIEW entries can be rejected"
            ),
        )

    rejection_reason = (body or {}).get("rejection_reason", "")

    followup_ref = str(uuid.uuid4())
    followup = AuditEntry(
        bill_external_ref=followup_ref,
        parent_bill_external_ref=audit_ref,
        batch_id=None,
        timestamp=datetime.now(timezone.utc),
        source_mode="DRAFTER_REJECTED",
        normalized_fields={
            "original_payload": original.normalized_fields.get("raw_payload", {}),
            "rejection_reason": rejection_reason,
        },
        structural_signals={},
        reconciliation_result={},
        flags=[],
        triage_decision=original.triage_decision,
        drafter_output=original.drafter_output,
        output_payload=None,
    )
    audit_store.record(followup)

    return {"audit_ref": followup_ref}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reconciliation_snapshot(validated: ValidatedBill) -> dict[str, Any]:
    """Snapshot the reconciliation result for storage in the audit entry.

    Captures just enough to drive the approval flow without re-running
    reconciliation: the matched meter's id and currency. Falls back to
    an empty dict when unmatched (the route is Escalate in that case
    and approval is not a valid action).
    """

    meter = validated.matched_meter
    if meter is None:
        return {}
    return {
        "matched_meter_id": meter.id,
        "matched_meter_string": meter.meter_id_string,
        "matched_meter_unit": meter.unit.value,
        "matched_meter_currency": meter.currency,
        "prior_context": validated.prior_context,
    }


def _load_original_entry(audit_store: AuditLogStore, audit_ref: str) -> AuditEntry:
    entries = audit_store.get_by_bill_ref(audit_ref)
    if not entries:
        raise HTTPException(status_code=404, detail=f"audit_ref {audit_ref} not found")
    # The first row is the original; follow-up rows would share a
    # parent_bill_external_ref, not this bill_external_ref.
    return entries[0]


def _reading_from_payload(
    payload: dict[str, Any],
    meter_id: int,
    fallback_currency: Optional[str],
) -> Reading:
    """Build a Reading from a (possibly corrected) raw_payload dict.

    Uses ``id=0`` as a sentinel because the SQLite store assigns the
    real rowid on insert. Currency falls back to the meter's locked
    currency when the payload omits it — pydantic requires non-null
    currency, so we cannot rely on the original bill having sent it.
    """

    period_start = date.fromisoformat(str(payload["period_start"]))
    period_end = date.fromisoformat(str(payload["period_end"]))
    usage_units = Unit(str(payload["usage_units"]))
    currency = payload.get("currency") or fallback_currency
    if not currency:
        raise ValueError("no currency in payload and no fallback available")

    return Reading(
        id=0,
        meter_id=meter_id,
        period_start=period_start,
        period_end=period_end,
        usage=float(payload["usage"]),
        usage_units=usage_units,
        cost=_as_float(payload.get("cost")),
        currency=currency,
        demand_kw=_as_float(payload.get("demand_kw")),
        demand_spend=_as_float(payload.get("demand_spend")),
        energy_exported=_as_float(payload.get("energy_exported")),
    )


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    return float(v)
