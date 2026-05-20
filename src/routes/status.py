"""GET /status — operational visibility, not a liveness check.

Always 200. Read-only. No DB writes triggered by hitting it.

Distinct from ``GET /health`` (in [src/main.py](../main.py)) which is the
simple "is the process up" probe. Status answers a different question:
"what does the system look like right now from an ops perspective?"
The prototype surfaces the shape of operational observability; the
scale-to-production doc treats production observability properly with
metrics, traces, and SLO dashboards.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends

from src.db.store import AuditLogStore, MeterHistoryStore
from src.routes.dependencies import get_audit_store, get_store

router = APIRouter()

# Kept in sync with src.main.API_VERSION; imported lazily to avoid a
# circular import (main imports the status router).


@router.get("/status")
def get_status(
    store: MeterHistoryStore = Depends(get_store),
    audit_store: AuditLogStore = Depends(get_audit_store),
) -> dict[str, Any]:
    from src.main import API_VERSION

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    readings_count = store.readings_count()
    audit_count = audit_store.count()
    last_write_at = audit_store.last_write_at()
    counts_24h = audit_store.counts_by_route_since(cutoff_24h)
    pending_drafted = audit_store.count_pending_drafted()

    return {
        "service_name": "utility-bill-pipeline",
        "version": API_VERSION,
        "db_state": {
            "open": True,
            "readings_count": readings_count,
            "audit_count": audit_count,
            "last_write_at": last_write_at.isoformat() if last_write_at else None,
        },
        "audit_counts_24h": counts_24h,
        "pending_drafted": pending_drafted,
        "anthropic_api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }
