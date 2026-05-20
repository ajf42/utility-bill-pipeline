"""FastAPI app entry.

Mounts the bills router and exposes a simple health check. No middleware,
no auth, no CORS for the prototype — the walkthrough runs against a local
``uvicorn src.main:app --reload`` process.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.routes.bills import router as bills_router
from src.routes.dependencies import set_drafter
from src.routes.status import router as status_router
from src.util.logging import configure_logging

API_VERSION = "0.3.0"

_logger = logging.getLogger(__name__)


configure_logging()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Build the DrafterService once, bound to a real Anthropic client.

    ``ANTHROPIC_API_KEY`` is read from the environment (never from code).
    When it is unset the app still boots; ``get_drafter`` returns None
    and triage logs a warning on the DraftForHumanReview path. Tests
    override the dependency before the app sees a request.
    """

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        import anthropic

        from src.services.drafter import DrafterService

        set_drafter(DrafterService(client=anthropic.Anthropic(api_key=api_key)))
        _logger.info("DrafterService initialized")
    else:
        _logger.warning(
            "ANTHROPIC_API_KEY not set; DrafterService not initialized. "
            "DraftForHumanReview bills will route without a drafter."
        )
        set_drafter(None)
    yield
    set_drafter(None)


app = FastAPI(title="Utility Bill Pipeline", version=API_VERSION, lifespan=_lifespan)
app.include_router(bills_router)
app.include_router(status_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Returns ``status`` and the running API version."""

    return {"status": "ok", "version": API_VERSION}
