"""POST /bills — JSON-row ingestion endpoint.

Thin route handler per the coding pattern in DESIGN.md §8: accept the
request body, delegate to the ingestion service, return the result. The
business logic lives in ``src.services.ingestion``.

The current return shape is a stub — ingestion runs but the downstream
pipeline (normalization, reconciliation, validation, triage) is not yet
wired. Subsequent prompts replace the ``pipeline_status`` field and add
the real artifacts.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from src.services.ingestion import ingest_json_row

router = APIRouter()


@router.post("/bills")
def post_bill(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept a single bill row as JSON and return the ingested artifact.

    A missing required field becomes a 422 — surfaced through HTTPException
    rather than FastAPI's automatic pydantic-422 because we accept a loose
    ``dict`` body and do the required-field check inside the service.
    """

    try:
        raw_input = ingest_json_row(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "raw_input": raw_input.model_dump(mode="json"),
        "pipeline_status": "ingested_only_not_yet_processed",
    }
