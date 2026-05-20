"""FastAPI app entry.

Mounts the bills router and exposes a simple health check. No middleware,
no auth, no CORS for the prototype — the walkthrough runs against a local
``uvicorn src.main:app --reload`` process.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from src.routes.bills import router as bills_router
from src.routes.dependencies import set_drafter
from src.routes.status import router as status_router
from src.util.logging import configure_logging

# Populate os.environ from a project-local .env file (gitignored) before
# the lifespan reads ANTHROPIC_API_KEY. load_dotenv() walks upward from
# CWD; absent file is a no-op. Real OS env vars take precedence over
# .env values (override=False is the default), so deployments that
# inject secrets via the environment are unaffected.
load_dotenv()

API_VERSION = "0.3.0"

_logger = logging.getLogger(__name__)


configure_logging()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Build the DrafterService once, bound to a real Anthropic client.

    ``ANTHROPIC_API_KEY`` is read from the environment (never from code),
    after ``load_dotenv()`` has populated it from a project-local
    ``.env`` file if present. Missing key is a startup-time
    ``RuntimeError`` — the prototype refuses to run partially-configured
    because the drafter is a load-bearing path, not an optional add-on.
    Tests construct ``TestClient(app)`` without a context manager and so
    do not exercise this lifespan.
    """

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The prototype's Resolution "
            "Drafter is a load-bearing path and the app refuses to start "
            "without it. Create a .env file in the project root with "
            "ANTHROPIC_API_KEY=sk-ant-... or export the variable in this "
            "shell, then restart uvicorn."
        )

    import anthropic

    from src.services.drafter import DrafterService

    set_drafter(DrafterService(client=anthropic.Anthropic(api_key=api_key)))
    _logger.info("DrafterService initialized")
    yield
    set_drafter(None)


app = FastAPI(title="Utility Bill Pipeline", version=API_VERSION, lifespan=_lifespan)
app.include_router(bills_router)
app.include_router(status_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Returns ``status`` and the running API version."""

    return {"status": "ok", "version": API_VERSION}
