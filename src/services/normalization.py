"""Normalization service — the first real pipeline stage.

Takes a ``RawBillInput`` and produces a ``NormalizedBill``: dates parsed,
provider canonicalized against the reference library, unit canonicalized
to the enum's spelling, currency-shape checked, and a set of structural
quality signals attached.

Per DESIGN.md §4 ("Normalization Service") and ADR-003: confidence comes
from **structural** checks only, never LLM self-reporting. Signals are
flag-liberal — better to flag five fields and have three turn out fine
than to pass a real error through. The service NEVER raises on bad data;
it records the failure as a False signal and lets triage decide what to
do with it. Raising is reserved for inputs that should have been caught
at ingestion (missing required fields).

The service is stateless: no DB, no API calls, no module-level mutation.
It depends only on the reference module and the pydantic models.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from src.models.bill import NormalizedBill, RawBillInput
from src.models.entities import Unit
from src.services.reference import (
    Provider,
    canonicalize_provider,
    get_regional_rules,
)

# Plausible-range thresholds. Monthly utility bills span 25-35 days in
# practice; anything outside that window is a normalization-level signal,
# not a fatal error — gap detection in validation will catch the rest.
_BILLING_PERIOD_MIN_DAYS = 25
_BILLING_PERIOD_MAX_DAYS = 35

# ISO 4217 alpha-3 currency code shape. Matches `pydantic`'s pattern on
# Meter.currency / Reading.currency so the structural signal is consistent
# with the model contract.
_ISO_4217_PATTERN = re.compile(r"^[A-Z]{3}$")

# Measurabl meter-id convention from DESIGN.md §4:
#     MSR.(provider)(account_number):(meter_number)
# The provider substring is the first parenthesized group. A meter_id_string
# that does not match yields a None provider alias; the caller records
# provider_known=False and continues — no exception is raised.
_METER_ID_PROVIDER_RE = re.compile(
    r"^MSR\.\(([^)]+)\)\(([^)]+)\):\(([^)]+)\)$"
)

# Case-insensitive canonicalization table: incoming "kwh" / "KWH" both
# resolve to "kWh", the enum's canonical spelling.
_UNIT_BY_CASEFOLDED: dict[str, str] = {u.value.casefold(): u.value for u in Unit}


def _parse_iso_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_numeric(value: Any) -> bool:
    # Booleans subclass int in Python; explicitly exclude them so a
    # stray `True`/`False` in a numeric field is a type-failure signal.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _extract_provider_alias(meter_id_string: Any) -> Optional[str]:
    """Pull the provider substring out of the meter_id_string convention.

    Returns ``None`` if the string is missing, not a string, or does not
    match the convention. No exception is raised on malformed input —
    structural-signal recording is the caller's job.
    """

    if not isinstance(meter_id_string, str):
        return None
    match = _METER_ID_PROVIDER_RE.match(meter_id_string)
    return match.group(1) if match else None


def _canonicalize_unit(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    return _UNIT_BY_CASEFOLDED.get(raw.strip().casefold())


def normalize(raw_input: RawBillInput) -> NormalizedBill:
    """Produce a NormalizedBill from a RawBillInput.

    Never raises on bad data. Every parse/check that fails is recorded as
    a False entry in ``structural_signals``. The signal layer is the
    audit-grade story for "why did we trust what we trusted" — see
    ADR-003.
    """

    payload = raw_input.raw_payload

    # --- field-by-field parsing (no exceptions, all results captured) ---

    period_start = _parse_iso_date(payload.get("period_start"))
    period_end = _parse_iso_date(payload.get("period_end"))

    usage_raw = payload.get("usage")
    usage_numeric = _is_numeric(usage_raw)

    canonical_unit = _canonicalize_unit(payload.get("usage_units"))
    unit_known = canonical_unit is not None

    provider_alias = _extract_provider_alias(payload.get("meter_id_string"))
    provider: Optional[Provider] = (
        canonicalize_provider(provider_alias) if provider_alias else None
    )
    provider_known = provider is not None

    # Currency: if absent, default to the provider's regional currency
    # (when the provider is known). If present, must match the ISO 4217
    # shape — caller can still emit a currency value and have it flagged.
    raw_currency = payload.get("currency")
    currency_present = raw_currency is not None
    currency_shape_valid = (
        isinstance(raw_currency, str)
        and bool(_ISO_4217_PATTERN.match(raw_currency))
    )

    effective_currency: Optional[str] = None
    if currency_present and currency_shape_valid:
        effective_currency = raw_currency
    elif not currency_present and provider is not None:
        effective_currency = get_regional_rules(provider.region).currency_default

    # Cost is optional; only emit signals for it when present.
    cost_raw = payload.get("cost")
    cost_present = cost_raw is not None
    cost_numeric = _is_numeric(cost_raw) if cost_present else None

    # --- structural signals ---------------------------------------------

    # field_type_valid: does the value parse to its expected type?
    field_type_valid: dict[str, bool] = {
        "period_start": period_start is not None,
        "period_end": period_end is not None,
        "usage": usage_numeric,
        "usage_units": unit_known,
        # Currency passes when absent (we will default) OR present with
        # the right shape. The "is currency really right for this region"
        # check lives in cross_field_agreement, not here.
        "currency": (not currency_present) or currency_shape_valid,
    }
    if cost_present:
        field_type_valid["cost"] = bool(cost_numeric)

    # value_in_range: is the value plausible for its type?
    value_in_range: dict[str, bool] = {
        # Usage must be positive — negative or zero is suspicious on
        # consumption bills (exports are a separate field).
        "usage": bool(usage_numeric) and usage_raw > 0,  # type: ignore[operator]
        # Monthly billing periods land in 25-35 days. Outside that window
        # is suspicious; gap detection downstream catches longer drift.
        "billing_period_length": (
            period_start is not None
            and period_end is not None
            and _BILLING_PERIOD_MIN_DAYS
            <= (period_end - period_start).days
            <= _BILLING_PERIOD_MAX_DAYS
        ),
    }
    if cost_present:
        # Cost must be non-negative when present — a negative cost is
        # either a refund (which the prototype doesn't model) or bad data.
        value_in_range["cost"] = bool(cost_numeric) and cost_raw >= 0  # type: ignore[operator]

    # cross_field_agreement: do unit, currency, and provider agree?
    # Default to False when the dependency is missing — we don't emit
    # None / Optional booleans because triage consumers should not need
    # to handle a third state. ADR-008 covers the shape decision.
    currency_matches_region = False
    unit_matches_provider_typical = False
    if provider is not None:
        regional_default = get_regional_rules(provider.region).currency_default
        if effective_currency is not None:
            currency_matches_region = effective_currency == regional_default
        if unit_known:
            unit_matches_provider_typical = canonical_unit in provider.typical_units

    cross_field_agreement: dict[str, bool] = {
        "currency_matches_region": currency_matches_region,
        "unit_matches_provider_typical": unit_matches_provider_typical,
    }

    # provider_alias_parsed records whether the meter_id_string convention
    # parsed at all — separate from provider_known, so a malformed
    # meter_id_string can be distinguished from a parsed-but-unrecognized
    # provider in downstream triage.
    structural_signals: dict[str, Any] = {
        "field_type_valid": field_type_valid,
        "value_in_range": value_in_range,
        "provider_known": provider_known,
        "provider_alias_parsed": provider_alias is not None,
        "unit_known": unit_known,
        "cross_field_agreement": cross_field_agreement,
    }

    # normalized_units records the canonicalization decision. Empty when
    # the unit didn't canonicalize — keeps the field shape stable.
    normalized_units: dict[str, str] = {}
    if canonical_unit is not None:
        normalized_units["usage"] = canonical_unit

    return NormalizedBill(
        source_mode=raw_input.source_mode,
        raw_payload=raw_input.raw_payload,
        batch_id=raw_input.batch_id,
        canonical_provider=provider.name if provider is not None else None,
        normalized_units=normalized_units,
        structural_signals=structural_signals,
    )
