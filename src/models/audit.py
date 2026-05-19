"""AuditEntry — the full lineage record persisted for every bill.

Every record that flows through the pipeline produces exactly one
AuditEntry, whether it auto-resolved, was drafted for human review, or was
escalated. The entry is the glass-box receipt the back-office team relies
on to reconstruct why a decision was made.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .drafter import DrafterOutput
from .quality import QualityFlag, TriageDecision


class AuditEntry(BaseModel):
    """One row of the audit log.

    ``id`` is None before the row is persisted; the store assigns the
    SQLite rowid on insert. ``batch_id`` is set when the bill arrived via
    the XLSX batch path, allowing the log to be queried by upload as well
    as by bill. ``output_payload`` is the readings-table write payload that
    was (or would have been) emitted; ``None`` when the route did not
    produce a write.
    """

    id: Optional[int] = None
    bill_external_ref: str
    parent_bill_external_ref: Optional[str] = None
    batch_id: Optional[str] = None
    timestamp: datetime
    source_mode: str
    normalized_fields: dict[str, Any] = Field(default_factory=dict)
    structural_signals: dict[str, Any] = Field(default_factory=dict)
    reconciliation_result: dict[str, Any] = Field(default_factory=dict)
    flags: list[QualityFlag] = Field(default_factory=list)
    triage_decision: TriageDecision
    drafter_output: Optional[DrafterOutput] = None
    output_payload: Optional[dict[str, Any]] = None
