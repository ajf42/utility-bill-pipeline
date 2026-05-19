"""POST /bills — ingest -> normalize -> reconcile.

Thin route handler per the coding pattern in DESIGN.md §8: accept the
request body, delegate to the services, return the result. Currently
runs ingestion + normalization + reconciliation; validation and triage
land in subsequent prompts and will extend the response shape further.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.db.store import MeterHistoryStore
from src.routes.dependencies import get_store
from src.services.ingestion import ingest_json_row
from src.services.normalization import normalize
from src.services.reconciliation import reconcile

router = APIRouter()


@router.post("/bills")
def post_bill(
    payload: dict[str, Any],
    store: MeterHistoryStore = Depends(get_store),
) -> dict[str, Any]:
    """Accept a single bill row as JSON and return the reconciled artifact.

    A missing required field becomes a 422 — surfaced through HTTPException
    rather than FastAPI's automatic pydantic-422 because we accept a loose
    ``dict`` body and do the required-field check inside the service.
    Normalization and reconciliation never raise on bad data (per ADR-003
    / DESIGN.md §4) — failed parse or missing meter is a downstream
    routing signal, not an HTTP error.
    """

    try:
        raw_input = ingest_json_row(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    normalized = normalize(raw_input)
    reconciled = reconcile(normalized, store)

    return {
        "raw_input": raw_input.model_dump(mode="json"),
        "normalized": normalized.model_dump(mode="json"),
        "reconciled": reconciled.model_dump(mode="json"),
        "pipeline_status": "reconciled",
    }
