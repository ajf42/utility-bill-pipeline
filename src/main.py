"""FastAPI app entry.

Mounts the bills router and exposes a simple health check. No middleware,
no auth, no CORS for the prototype — the walkthrough runs against a local
``uvicorn src.main:app --reload`` process.
"""

from __future__ import annotations

from fastapi import FastAPI

from src.routes.bills import router as bills_router

API_VERSION = "0.2.0"

app = FastAPI(title="Utility Bill Pipeline", version=API_VERSION)
app.include_router(bills_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Returns ``status`` and the running API version."""

    return {"status": "ok", "version": API_VERSION}
