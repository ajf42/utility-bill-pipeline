"""Triage service — pipeline stage 5.

Maps a ValidatedBill to one of three routes per DESIGN.md §4 "Triage
Service":

1. **AutoResolve** — all structural quality signals pass, no high-severity
   flags, no more than one medium-severity flag. The bill is written to
   the readings table on commit.
2. **DraftForHumanReview** — the residual: fixable issues (notably
   ``UNIT_MISMATCH``) and the 2-medium case. Triage hands the bill to
   the Resolution Drafter, attaches the structured DrafterOutput to the
   decision, and lets a human approve or reject.
3. **Escalate** — high-severity non-fixable flags, ≥3 medium-severity
   flags, or an unmatched meter. Carries a ``RoutingKey`` mapping to a
   specific exception class.

The fixable-HIGH carve-out for ``UNIT_MISMATCH`` is what makes the Drafter
demo moment possible — ADR-006 and DESIGN.md §4 both call out unit
mismatch as the canonical DraftForHumanReview case. Currency, inactive
meter, overlap, and the rest of the HIGH set escalate.

When the Drafter is reachable but raises, the route degrades to Escalate
with ``RoutingKey.DRAFTER_FAILURE`` and a ``FlagType.DRAFTER_FAILURE``
flag carrying the exception message — the pipeline never aborts because
Claude returned something weird (ADR-011 + this prompt).
"""

from __future__ import annotations

import logging
from typing import Optional

from src.models.bill import ValidatedBill
from src.models.quality import (
    FlagType,
    QualityFlag,
    RoutingKey,
    Severity,
    TriageDecision,
    TriageRoute,
)
from src.services.drafter import DrafterService

_logger = logging.getLogger(__name__)

# DESIGN.md §4: ≥3 medium-severity flags escalate; 2 mediums fall into
# DraftForHumanReview; 0-1 mediums (with no HIGH) AutoResolve.
_MEDIUM_ESCALATE_THRESHOLD = 3
_MEDIUM_DRAFT_THRESHOLD = 2

# Fixable HIGH flags: the drafter can confidently propose a correction.
# Everything else with HIGH severity escalates.
_FIXABLE_HIGH_FLAG_TYPES = frozenset({FlagType.UNIT_MISMATCH})

# DESIGN.md §4 escalation taxonomy. Anything not listed falls through to
# UNCATEGORIZED — that bucket is deliberately visible per ADR-004.
_HIGH_FLAG_TO_ROUTING_KEY = {
    FlagType.METER_UNASSIGNED: RoutingKey.METER_UNASSIGNED,
    FlagType.OVERLAP: RoutingKey.OVERLAP,
    FlagType.INACTIVE_METER: RoutingKey.INACTIVE_METER,
    FlagType.CURRENCY_MISMATCH: RoutingKey.FORMAT_MISMATCH,
    FlagType.NAME_MISMATCH: RoutingKey.FORMAT_MISMATCH,
    FlagType.GENERATION_MISMATCH: RoutingKey.FORMAT_MISMATCH,
    FlagType.FORMAT_INVALID: RoutingKey.FORMAT_MISMATCH,
}


def _route_for_high_flags(high_flags: list[QualityFlag]) -> RoutingKey:
    """Pick a routing key for an escalation driven by HIGH flags.

    First HIGH flag wins — the audit entry preserves all flags, so the
    routing key just needs to name the most informative exception class
    for the back-office queue.
    """
    for flag in high_flags:
        if flag.type in _HIGH_FLAG_TO_ROUTING_KEY:
            return _HIGH_FLAG_TO_ROUTING_KEY[flag.type]
    return RoutingKey.UNCATEGORIZED


class TriageService:
    """Decide a route, then (if applicable) draft a resolution.

    The drafter is optional so unit tests can exercise the routing logic
    without wiring up an Anthropic client. When the route resolves to
    DraftForHumanReview and ``drafter`` is None, the decision is returned
    with ``drafter_output=None`` and a warning is logged — this is a
    test-friendly mode, not a production mode.
    """

    def __init__(self, drafter: Optional[DrafterService] = None) -> None:
        self._drafter = drafter

    def triage(self, validated: ValidatedBill) -> TriageDecision:
        high_flags = [f for f in validated.flags if f.severity is Severity.HIGH]
        medium_flags = [f for f in validated.flags if f.severity is Severity.MEDIUM]

        # 1. Unmatched meter is a hard escalate. METER_UNASSIGNED is the
        #    only flag emitted by validation in that case, so this branch
        #    fires reliably.
        if validated.matched_meter is None:
            return TriageDecision(
                route=TriageRoute.ESCALATE,
                routing_key=RoutingKey.METER_UNASSIGNED,
                reasoning=(
                    "incoming meter triple did not resolve against the meter "
                    "history store; routing to onboarding"
                ),
            )

        # 2. HIGH flag handling. Pure-fixable HIGH set (only UNIT_MISMATCH
        #    today) goes to DraftForHumanReview; mixed or non-fixable HIGH
        #    escalates.
        if high_flags:
            non_fixable = [f for f in high_flags if f.type not in _FIXABLE_HIGH_FLAG_TYPES]
            if non_fixable:
                return TriageDecision(
                    route=TriageRoute.ESCALATE,
                    routing_key=_route_for_high_flags(non_fixable),
                    reasoning=(
                        f"{len(high_flags)} HIGH-severity flag(s); "
                        f"non-fixable flag {non_fixable[0].type.value} drives escalation"
                    ),
                )
            return self._draft(
                validated,
                reasoning=(
                    f"{len(high_flags)} HIGH-severity flag(s), all fixable "
                    f"(e.g. {high_flags[0].type.value}); drafting proposed correction"
                ),
            )

        # 3. Medium-flag counting per DESIGN.md §4 thresholds.
        if len(medium_flags) >= _MEDIUM_ESCALATE_THRESHOLD:
            return TriageDecision(
                route=TriageRoute.ESCALATE,
                routing_key=RoutingKey.UNCATEGORIZED,
                reasoning=(
                    f"{len(medium_flags)} MEDIUM-severity flags exceeds escalate "
                    f"threshold ({_MEDIUM_ESCALATE_THRESHOLD})"
                ),
            )
        if len(medium_flags) >= _MEDIUM_DRAFT_THRESHOLD:
            return self._draft(
                validated,
                reasoning=(
                    f"{len(medium_flags)} MEDIUM-severity flags warrants "
                    "human review with a drafted resolution"
                ),
            )

        # 4. AutoResolve — at most one MEDIUM flag, no HIGH, matched meter.
        reasoning = (
            "no flags raised" if not medium_flags
            else f"one MEDIUM flag ({medium_flags[0].type.value}); under thresholds"
        )
        return TriageDecision(
            route=TriageRoute.AUTO_RESOLVE,
            reasoning=reasoning,
        )

    def _draft(self, validated: ValidatedBill, *, reasoning: str) -> TriageDecision:
        """Build a DraftForHumanReview decision, calling the drafter if
        attached.

        On any drafter exception (notably ``DrafterParseError``) the
        decision degrades to Escalate with ``DRAFTER_FAILURE`` and the
        message is appended to the validated bill's flags so the audit
        log preserves the failure mode. The pipeline never aborts.
        """
        if self._drafter is None:
            _logger.warning(
                "TriageService.draft called with no drafter attached; "
                "returning DraftForHumanReview decision with drafter_output=None"
            )
            return TriageDecision(
                route=TriageRoute.DRAFT_FOR_HUMAN_REVIEW,
                reasoning=reasoning,
                drafter_output=None,
            )

        assert validated.matched_meter is not None  # noqa: S101 — branch guard
        try:
            drafter_output = self._drafter.draft(
                validated_bill=validated,
                meter=validated.matched_meter,
                prior_readings=validated.prior_readings,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft per this prompt
            _logger.exception("Drafter call failed; escalating with DRAFTER_FAILURE")
            validated.flags.append(
                QualityFlag(
                    type=FlagType.DRAFTER_FAILURE,
                    severity=Severity.HIGH,
                    description=f"drafter raised {type(exc).__name__}: {exc}",
                    recommended_action="manual review; drafter unavailable for this bill",
                )
            )
            return TriageDecision(
                route=TriageRoute.ESCALATE,
                routing_key=RoutingKey.DRAFTER_FAILURE,
                reasoning=(
                    f"drafter call failed ({type(exc).__name__}); "
                    "escalating with DRAFTER_FAILURE routing key"
                ),
            )

        return TriageDecision(
            route=TriageRoute.DRAFT_FOR_HUMAN_REVIEW,
            reasoning=reasoning,
            drafter_output=drafter_output,
        )
