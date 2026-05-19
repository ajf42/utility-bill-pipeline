"""Reconciliation service — pipeline stage 3.

Resolves the incoming bill against meter history in the
``MeterHistoryStore``. This is where the pipeline becomes stateful:
everything before this point treats the bill as a self-contained
artifact; from here on it carries prior context attached so the gap
and overlap heuristics in validation can reason historically.

Per DESIGN.md §4 ("Reconciliation Service"):

1. Resolve the meter by the three-key triple (meter_id_string,
   account_number, site_name). All three must agree.
2. If no match, the bill flows on with ``matched_meter=None``; the
   validation/triage layer routes it to ``meter_unassigned``. This
   service does NOT raise on miss — "no match" is a valid pipeline
   outcome.
3. If matched, fetch the last N readings (default 12) sorted by
   period and attach them.
4. Compute the small ``prior_context`` summary the heuristics actually
   consume: the most recent prior period_end (or None) and the count
   of prior readings.

The service is stateless beyond the injected store; it does not open
connections itself.
"""

from __future__ import annotations

from typing import Any

from src.db.store import MeterHistoryStore
from src.models.bill import NormalizedBill, ReconciledBill

# DESIGN.md §4 calls out "the last N readings (default 12)". The default
# lives here as a module-level constant so a future caller can override
# without grepping for magic numbers.
DEFAULT_PRIOR_READINGS_LIMIT = 12


def reconcile(
    normalized: NormalizedBill,
    store: MeterHistoryStore,
    *,
    prior_readings_limit: int = DEFAULT_PRIOR_READINGS_LIMIT,
) -> ReconciledBill:
    """Resolve ``normalized`` against ``store`` and attach prior context.

    Never raises on missing-meter; that case is a valid downstream
    routing signal (``meter_unassigned`` escalation per DESIGN.md §4
    "Triage Service"). The required identifier fields are read from
    ``raw_payload`` — normalization left them there unchanged because
    they are identity, not measurement.
    """

    payload = normalized.raw_payload
    meter_id_string = payload.get("meter_id_string")
    account_number = payload.get("account_number")
    site_name = payload.get("site_name")

    matched_meter = None
    matched_account = None
    prior_readings: list = []
    if (
        isinstance(meter_id_string, str)
        and isinstance(account_number, str)
        and isinstance(site_name, str)
    ):
        matched_meter = store.find_meter(meter_id_string, account_number, site_name)
        if matched_meter is not None:
            prior_readings = store.get_prior_readings(
                matched_meter.id, limit=prior_readings_limit
            )
            # Fetch the parent account so validation can check
            # generation_account without re-opening the store.
            matched_account = store.get_account(matched_meter.account_id)

    prior_context: dict[str, Any] = {
        # The store returns readings sorted by period_end DESC, so the
        # first entry is the most recent. None when there are no priors.
        "prior_period_end": (
            prior_readings[0].period_end if prior_readings else None
        ),
        "count_of_prior_readings": len(prior_readings),
    }

    return ReconciledBill(
        source_mode=normalized.source_mode,
        raw_payload=normalized.raw_payload,
        batch_id=normalized.batch_id,
        canonical_provider=normalized.canonical_provider,
        normalized_units=normalized.normalized_units,
        structural_signals=normalized.structural_signals,
        matched_meter=matched_meter,
        matched_account=matched_account,
        prior_readings=prior_readings,
        prior_context=prior_context,
    )
