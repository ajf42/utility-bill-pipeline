"""Validation service — pipeline stage 4.

Takes a ``ReconciledBill`` and produces a ``ValidatedBill`` with a list
of ``QualityFlag`` entries — one per check that fired. Per DESIGN.md §4
"Validation Layer" the work splits into two parts:

1. **Schema-level** checks (cheap; defense-in-depth against pydantic).
2. **Structural** checks against the matched meter (unit, currency,
   inactive, generation).
3. **Domain heuristics** that need prior context (gap, overlap).

When the meter could not be resolved upstream the only flag emitted is
``METER_UNASSIGNED`` — the other checks all require the matched meter
or account, so they are skipped naturally. The service never raises;
problems are flags, not exceptions.

Thresholds live as module-level constants near the top so a reviewer
can adjust them without grepping for magic numbers.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from src.models.bill import ReconciledBill, ValidatedBill
from src.models.entities import Reading
from src.models.quality import FlagType, QualityFlag, Severity

# DESIGN.md §4 "Decision Logic Specifics":
#   Gap medium: 2-7 days; high: >7 days.
# A gap of <=2 days is "no flag" — bills landing on the day after the
# prior period_end are exactly contiguous and should not be flagged.
_GAP_MEDIUM_DAYS = 2
_GAP_HIGH_DAYS = 7


def _parse_iso_date(value) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _flag(
    flag_type: FlagType,
    severity: Severity,
    description: str,
    recommended_action: str,
) -> QualityFlag:
    return QualityFlag(
        type=flag_type,
        severity=severity,
        description=description,
        recommended_action=recommended_action,
    )


def _check_schema(reconciled: ReconciledBill) -> list[QualityFlag]:
    """Schema-level checks. pydantic has already enforced presence and
    type on construction; this is the second-line defense for the loose
    ``raw_payload`` shape and for the period_start < period_end invariant
    that pydantic alone does not express.
    """

    flags: list[QualityFlag] = []
    payload = reconciled.raw_payload

    ps = _parse_iso_date(payload.get("period_start"))
    pe = _parse_iso_date(payload.get("period_end"))

    if ps is None or pe is None:
        flags.append(
            _flag(
                FlagType.FORMAT_INVALID,
                Severity.HIGH,
                "period_start or period_end did not parse as ISO date",
                "fix dates or escalate to onboarding",
            )
        )
    elif ps >= pe:
        flags.append(
            _flag(
                FlagType.FORMAT_INVALID,
                Severity.HIGH,
                f"period_start ({ps}) is not strictly before period_end ({pe})",
                "verify billing period orientation",
            )
        )

    return flags


def _check_unit_mismatch(reconciled: ReconciledBill) -> Optional[QualityFlag]:
    meter = reconciled.matched_meter
    if meter is None:
        return None
    incoming = reconciled.raw_payload.get("usage_units")
    # Compare against the meter's locked unit value; case-insensitive on
    # the incoming side to stay consistent with normalization's behavior.
    if not isinstance(incoming, str):
        return None
    if incoming.strip().casefold() != meter.unit.value.casefold():
        return _flag(
            FlagType.UNIT_MISMATCH,
            Severity.HIGH,
            f"bill usage_units={incoming!r} does not match meter unit "
            f"{meter.unit.value!r}",
            "propose unit correction (DraftForHumanReview)",
        )
    return None


def _check_currency_mismatch(reconciled: ReconciledBill) -> Optional[QualityFlag]:
    meter = reconciled.matched_meter
    if meter is None:
        return None
    incoming = reconciled.raw_payload.get("currency")
    if incoming is None:
        return None
    if not isinstance(incoming, str) or incoming != meter.currency:
        return _flag(
            FlagType.CURRENCY_MISMATCH,
            Severity.HIGH,
            f"bill currency={incoming!r} does not match meter currency "
            f"{meter.currency!r}",
            "escalate with FORMAT_MISMATCH routing key",
        )
    return None


def _check_inactive_meter(reconciled: ReconciledBill) -> Optional[QualityFlag]:
    meter = reconciled.matched_meter
    if meter is None or meter.active:
        return None
    return _flag(
        FlagType.INACTIVE_METER,
        Severity.HIGH,
        f"reading received against inactive meter {meter.meter_id_string!r}",
        "escalate with INACTIVE_METER routing key",
    )


def _check_generation_mismatch(reconciled: ReconciledBill) -> Optional[QualityFlag]:
    """Energy_exported on a non-generation account.

    A bill that reports energy exported back to the grid must come from a
    generation account (solar, wind, etc.). When the account's
    ``generation_account`` flag is False, the export is an upstream
    misclassification.
    """

    account = reconciled.matched_account
    if account is None or account.generation_account:
        return None
    energy_exported = reconciled.raw_payload.get("energy_exported")
    if not isinstance(energy_exported, (int, float)) or isinstance(
        energy_exported, bool
    ):
        return None
    if energy_exported <= 0:
        return None
    return _flag(
        FlagType.GENERATION_MISMATCH,
        Severity.HIGH,
        f"energy_exported={energy_exported} on non-generation account "
        f"{account.account_number!r}",
        "verify whether account should be reclassified as generation",
    )


def _check_gap(reconciled: ReconciledBill) -> Optional[QualityFlag]:
    """Days between this bill's period_start and the prior period_end.

    Per DESIGN.md §4: >7 days = HIGH, 2-7 days = MEDIUM, <=2 days = no
    flag. No prior context (new meter / no prior readings) = no flag.
    """

    if reconciled.matched_meter is None:
        return None
    prior_end = reconciled.prior_context.get("prior_period_end")
    if prior_end is None:
        return None
    ps = _parse_iso_date(reconciled.raw_payload.get("period_start"))
    if ps is None:
        return None
    gap_days = (ps - prior_end).days
    if gap_days <= _GAP_MEDIUM_DAYS:
        return None
    severity = (
        Severity.HIGH if gap_days > _GAP_HIGH_DAYS else Severity.MEDIUM
    )
    return _flag(
        FlagType.GAP,
        severity,
        f"{gap_days}-day gap between prior period_end ({prior_end}) and "
        f"this bill's period_start ({ps})",
        "investigate missing readings; route to Escalate if severity HIGH",
    )


def _readings_overlap(
    incoming_start: date,
    incoming_end: date,
    prior: Reading,
) -> bool:
    """Two periods overlap when each starts before the other ends.

    Touch-at-the-boundary (prior ends on the same day this one starts)
    is NOT an overlap — that is a contiguous read and ``_check_gap``
    handles it. Overlap means there is a non-empty intersection.
    """

    return prior.period_end > incoming_start and prior.period_start < incoming_end


def _check_overlap(reconciled: ReconciledBill) -> Optional[QualityFlag]:
    if reconciled.matched_meter is None:
        return None
    ps = _parse_iso_date(reconciled.raw_payload.get("period_start"))
    pe = _parse_iso_date(reconciled.raw_payload.get("period_end"))
    if ps is None or pe is None:
        return None
    for prior in reconciled.prior_readings:
        if _readings_overlap(ps, pe, prior):
            return _flag(
                FlagType.OVERLAP,
                Severity.HIGH,
                f"incoming period {ps}..{pe} overlaps prior reading "
                f"{prior.period_start}..{prior.period_end} on the same meter",
                "escalate with OVERLAP routing key",
            )
    return None


def validate(reconciled: ReconciledBill) -> ValidatedBill:
    """Run all applicable checks against ``reconciled`` and return a
    ``ValidatedBill`` with the resulting flags attached.

    When the meter is unmatched the only flag emitted is METER_UNASSIGNED
    plus any schema-level format issues — the other checks all depend on
    the matched meter/account and short-circuit on None internally.

    Note: the ``name_mismatch`` check listed in DESIGN.md §4 is not
    implemented as a separate flag because ``MeterHistoryStore.find_meter``
    joins on site_name; a site-name mismatch makes the meter unmatchable,
    not a flagged-but-matched case. See DECISIONS.md "Spec gaps observed".
    """

    flags: list[QualityFlag] = []

    # Schema-level checks always run.
    flags.extend(_check_schema(reconciled))

    if reconciled.matched_meter is None:
        flags.append(
            _flag(
                FlagType.METER_UNASSIGNED,
                Severity.HIGH,
                "incoming meter triple (meter_id_string, account_number, "
                "site_name) did not resolve against the meter history store",
                "route to onboarding",
            )
        )
        return ValidatedBill(
            source_mode=reconciled.source_mode,
            raw_payload=reconciled.raw_payload,
            batch_id=reconciled.batch_id,
            canonical_provider=reconciled.canonical_provider,
            normalized_units=reconciled.normalized_units,
            structural_signals=reconciled.structural_signals,
            matched_meter=None,
            matched_account=None,
            prior_readings=reconciled.prior_readings,
            prior_context=reconciled.prior_context,
            flags=flags,
        )

    # Structural checks against the matched meter / account.
    for maybe in (
        _check_unit_mismatch(reconciled),
        _check_currency_mismatch(reconciled),
        _check_inactive_meter(reconciled),
        _check_generation_mismatch(reconciled),
        # Domain heuristics — only meaningful with prior context.
        _check_gap(reconciled),
        _check_overlap(reconciled),
    ):
        if maybe is not None:
            flags.append(maybe)

    return ValidatedBill(
        source_mode=reconciled.source_mode,
        raw_payload=reconciled.raw_payload,
        batch_id=reconciled.batch_id,
        canonical_provider=reconciled.canonical_provider,
        normalized_units=reconciled.normalized_units,
        structural_signals=reconciled.structural_signals,
        matched_meter=reconciled.matched_meter,
        matched_account=reconciled.matched_account,
        prior_readings=reconciled.prior_readings,
        prior_context=reconciled.prior_context,
        flags=flags,
    )
