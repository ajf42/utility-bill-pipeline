"""Quality flags, triage routes, and the triage decision record.

These are the contracts the validation and triage services hand to each
other and to the audit log. See DESIGN.md §4 ("Triage Service") for the
three-route model and the routing-key escalation taxonomy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class Severity(str, Enum):
    """Severity attached to a QualityFlag. Drives triage threshold logic."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FlagType(str, Enum):
    """The set of structural and heuristic flags the validation service emits."""

    GAP = "GAP"
    OVERLAP = "OVERLAP"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    NAME_MISMATCH = "NAME_MISMATCH"
    INACTIVE_METER = "INACTIVE_METER"
    GENERATION_MISMATCH = "GENERATION_MISMATCH"
    METER_UNASSIGNED = "METER_UNASSIGNED"
    FORMAT_INVALID = "FORMAT_INVALID"


class QualityFlag(BaseModel):
    """A single quality issue raised against a bill.

    The triage service consumes a ``list[QualityFlag]`` and uses the
    severity distribution plus structural signals to pick a route.
    """

    type: FlagType
    severity: Severity
    description: str
    recommended_action: str


class RoutingKey(str, Enum):
    """Sub-route for the Escalate triage outcome.

    ``UNCATEGORIZED`` is deliberately visible in the taxonomy — when it shows
    up in the audit log, it is a signal that the rule set has a weak spot
    the back-office team needs to see (see DESIGN.md §4).
    """

    CONNECT_INTEGRITY = "CONNECT_INTEGRITY"
    METER_UNASSIGNED = "METER_UNASSIGNED"
    OVERLAP = "OVERLAP"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    INACTIVE_METER = "INACTIVE_METER"
    UNCATEGORIZED = "UNCATEGORIZED"


class TriageRoute(str, Enum):
    """The three triage outcomes. See ADR-004."""

    AUTO_RESOLVE = "AUTO_RESOLVE"
    DRAFT_FOR_HUMAN_REVIEW = "DRAFT_FOR_HUMAN_REVIEW"
    ESCALATE = "ESCALATE"


class TriageDecision(BaseModel):
    """The triage service's per-bill output.

    ``routing_key`` is set only on the Escalate route. ``drafted_resolution``
    is set only on the DraftForHumanReview route and carries the Resolution
    Drafter Service's output (proposed action, drafted email, basis note).
    """

    route: TriageRoute
    routing_key: Optional[RoutingKey] = None
    reasoning: str
    drafted_resolution: Optional[dict[str, Any]] = None
