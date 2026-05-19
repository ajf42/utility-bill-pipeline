"""FastAPI dependencies shared across routes.

Kept in its own module so tests can override via
``app.dependency_overrides[get_store] = ...`` without importing the
route module's internals. The prototype reads ``DB_PATH`` from the
environment (default ``./prototype.db``) and opens a fresh
``MeterHistoryStore`` per request — SQLite is cheap to open and the
per-request lifecycle keeps connection state and threading concerns
out of scope for the prototype.
"""

from __future__ import annotations

import os
from typing import Iterator, Optional

from src.db.store import AuditLogStore, MeterHistoryStore
from src.services.drafter import DrafterService


def _db_path() -> str:
    return os.environ.get("DB_PATH", "./prototype.db")


def get_store() -> Iterator[MeterHistoryStore]:
    """Yield a MeterHistoryStore connected to ``$DB_PATH`` (default
    ``./prototype.db``). The store is closed when the request finishes.
    """

    store = MeterHistoryStore(_db_path())
    try:
        yield store
    finally:
        store.close()


def get_audit_store() -> Iterator[AuditLogStore]:
    """Yield an AuditLogStore on the same SQLite file as ``get_store``.

    Per-request lifecycle, same rationale as ``get_store``.
    """

    store = AuditLogStore(_db_path())
    try:
        yield store
    finally:
        store.close()


# Drafter client is constructed at FastAPI startup (see [src/main.py]) and
# assigned here so the route handler can resolve it through Depends. The
# module-level slot keeps the dependency override pattern working: tests
# call ``app.dependency_overrides[get_drafter] = lambda: fake_service``.
_drafter_singleton: Optional[DrafterService] = None


def set_drafter(drafter: Optional[DrafterService]) -> None:
    """Install the DrafterService instance that ``get_drafter`` returns.

    Called once at app startup with a service bound to a real Anthropic
    client. ``None`` is a valid value — in that mode ``get_drafter``
    returns None and Triage logs a warning when a DraftForHumanReview
    decision is reached.
    """

    global _drafter_singleton
    _drafter_singleton = drafter


def get_drafter() -> Optional[DrafterService]:
    """Return the configured DrafterService, or None if not configured.

    Tests can override this via ``app.dependency_overrides[get_drafter]``
    to swap in a service backed by a FakeAnthropicClient.
    """

    return _drafter_singleton
