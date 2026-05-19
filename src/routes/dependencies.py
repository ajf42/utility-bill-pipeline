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
from typing import Iterator

from src.db.store import MeterHistoryStore


def get_store() -> Iterator[MeterHistoryStore]:
    """Yield a MeterHistoryStore connected to ``$DB_PATH`` (default
    ``./prototype.db``). The store is closed when the request finishes.
    """

    db_path = os.environ.get("DB_PATH", "./prototype.db")
    store = MeterHistoryStore(db_path)
    try:
        yield store
    finally:
        store.close()
