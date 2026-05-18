"""Reference data layer: provider library, unit conversion, regional rules.

A small, in-memory module with no DB and no FastAPI dependencies. Downstream
services (normalization, validation) import the constants and helpers below;
this module is the single place provider canonicalization, unit conversion,
and regional convention defaults are defined.

The provider library is illustrative — see DESIGN.md §4 "Reference Data Layer"
for the acknowledged simplification (real provider quirks are a tree of
provider -> tariff -> rate schedule -> bill format; that tariff-aware store
is treated in the scale-to-production doc).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ReferenceRegion(str, Enum):
    """Reference-layer region.

    Finer-grained than ``models.entities.Region`` (which is US / EU and is
    used to tag a Site). The reference layer separates US-East from US-West
    because the provider library groups by service territory.
    """

    US_EAST = "US-East"
    US_WEST = "US-West"
    EU = "EU"


class Provider(BaseModel):
    """A utility provider entry in the reference library.

    Pydantic for runtime validation — the canonical names and unit codes
    are checked at module load, so a typo in the constants below surfaces
    at import time rather than at first lookup.
    """

    name: str
    aliases: list[str] = Field(default_factory=list)
    region: ReferenceRegion
    typical_units: list[str] = Field(min_length=1)
    quirks: list[str] = Field(default_factory=list)


class RegionalRules(BaseModel):
    """Decimal/date/currency conventions for a region.

    Used by the normalization service to parse incoming numeric and date
    strings consistent with the region of the bill's Site, and to choose
    a fallback currency if a row arrives without one.
    """

    region: ReferenceRegion
    decimal_separator: str
    date_format: str
    currency_default: str = Field(pattern=r"^[A-Z]{3}$")


# ---------------------------------------------------------------------------
# Provider library
# ---------------------------------------------------------------------------
# Ten providers covering US-East, US-West, and EU. Aliases cover common
# abbreviations and spelling variants seen in real-world bill data; quirks
# are illustrative free-text strings (one or two per provider) of the kind
# a back-office team would actually want surfaced. Pacific Gas is included
# as a distinct US-West gas entry separate from PG&E — in production these
# would be cross-referenced through a tariff-aware store (DESIGN.md §4).

PROVIDERS: list[Provider] = [
    Provider(
        name="Consolidated Edison",
        aliases=["ConEd", "Con Edison", "Con-Ed", "CECONY"],
        region=ReferenceRegion.US_EAST,
        typical_units=["kWh", "therms"],
        quirks=[
            "Bills electric and gas on separate accounts even at the same premise.",
            "Demand kW reported on commercial tariffs; absent on residential.",
        ],
    ),
    Provider(
        name="National Grid",
        aliases=["NationalGrid", "Nat Grid", "NGrid"],
        region=ReferenceRegion.US_EAST,
        typical_units=["therms", "kWh"],
        quirks=[
            "Operates in both US-East (NY, MA, RI) and UK gas; same brand, different entities.",
            "Therms reported with two-decimal precision on commercial bills.",
        ],
    ),
    Provider(
        name="Duke Energy",
        aliases=["Duke", "Duke Power"],
        region=ReferenceRegion.US_EAST,
        typical_units=["kWh"],
        quirks=[
            "Multi-state operator (NC, SC, FL, IN, OH, KY); rate schedules vary by jurisdiction.",
        ],
    ),
    Provider(
        name="Pacific Gas & Electric",
        aliases=["PG&E", "PGE", "PG and E"],
        region=ReferenceRegion.US_WEST,
        typical_units=["kWh", "therms"],
        quirks=[
            "Time-of-use rate schedules are the default for commercial; flat-rate is opt-in.",
            "Generation and delivery components broken out on the bill but rolled into Cost for ingestion.",
        ],
    ),
    Provider(
        name="Southern California Edison",
        aliases=["SoCal Edison", "SCE", "Edison"],
        region=ReferenceRegion.US_WEST,
        typical_units=["kWh"],
        quirks=[
            "Demand charge billed on highest 15-minute interval in the period.",
        ],
    ),
    Provider(
        name="Xcel Energy",
        aliases=["Xcel", "Public Service Co"],
        region=ReferenceRegion.US_WEST,
        typical_units=["kWh", "therms"],
        quirks=[
            "Operates across CO, MN, WI, ND, SD, NM, MI, TX; service territory crosses time zones.",
        ],
    ),
    Provider(
        name="Pacific Gas",
        aliases=["Pacific Gas Co", "PacGas"],
        region=ReferenceRegion.US_WEST,
        typical_units=["therms", "ccf"],
        quirks=[
            "Reports usage in ccf on legacy accounts; therms on accounts after the 2018 migration.",
        ],
    ),
    Provider(
        name="British Gas",
        aliases=["BritishGas", "BG"],
        region=ReferenceRegion.EU,
        typical_units=["kWh", "m3"],
        quirks=[
            "UK gas reported in kWh after the smart-meter rollout; m3 on legacy meters.",
            "Bills carry a calorific-value adjustment factor in fine print.",
        ],
    ),
    Provider(
        name="Thames Water",
        aliases=["ThamesWater"],
        region=ReferenceRegion.EU,
        typical_units=["m3"],
        quirks=[
            "Estimated reads bracket actual reads on a 6-month cycle; high false-positive rate for gap heuristics.",
        ],
    ),
    Provider(
        name="EDF Energy",
        aliases=["EDF", "EDF UK"],
        region=ReferenceRegion.EU,
        typical_units=["kWh"],
        quirks=[
            "UK retail arm of Électricité de France; bills denominated in GBP not EUR.",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Unit conversion table
# ---------------------------------------------------------------------------
# All values are at standard conditions (~15 deg C, 1 atm) for natural gas
# where a fuel-energy bridge is involved. Pure-volume conversions
# (ccf <-> gallons, m3 <-> gallons) are exact volumetric and carry no fuel
# assumption. Conversion is direct (no transitive chains): if the (from, to)
# or (to, from) pair is not in this map, convert_unit raises.
#
# Canonical targets per commodity (DESIGN.md §4): energy -> kWh, gas -> therms,
# water -> gallons. The pairs below are the closure needed to take any unit
# in the canonical set to its commodity canonical.

# Source notes per pair:
#   1 therm = 100,000 BTU (definitional)
#   1 BTU   = 0.00029307107 kWh; therefore 1 therm = 29.3001 kWh.
#   1 MMBtu = 1,000,000 BTU = 10 therms (definitional).
#   1 ccf (natural gas at standard conditions) ~= 1.037 therms (EIA / utility
#       industry standard heat-content factor).
#   1 m3 (natural gas at standard conditions) ~= 0.0345 MMBtu ~= 0.347 therms
#       (DOE / EIA conversion factor).
#   1 ccf = 100 ft^3 (definitional). 1 ft^3 = 7.48052 US gallons; therefore
#       1 ccf = 748.052 US gallons.
#   1 HCF = "Hundred Cubic Feet" = 1 ccf (synonym in water utility usage).
#   1 m3 = 264.172 US gallons (definitional).
#   1 m3 = 35.3147 ft^3 = 0.353147 ccf (definitional).

_CONVERSIONS: dict[tuple[str, str], float] = {
    # Energy / gas-energy cross-fuel
    ("therms", "kWh"): 29.3001,
    ("MMBtu", "kWh"): 293.071,
    ("MMBtu", "therms"): 10.0,
    # Gas volume to gas energy (natural gas, standard conditions)
    ("ccf", "therms"): 1.037,
    ("m3", "therms"): 0.347,
    # Pure volume (water + gas-volume share the same volumetric factors)
    ("HCF", "ccf"): 1.0,
    ("HCF", "gallons"): 748.052,
    ("ccf", "gallons"): 748.052,
    ("m3", "gallons"): 264.172,
    ("m3", "ccf"): 0.353147,
    ("m3", "HCF"): 0.353147,
}


# ---------------------------------------------------------------------------
# Regional rules
# ---------------------------------------------------------------------------
# US-East and US-West share decimal/date conventions; they are split here so
# the provider library can group by service territory. EU defaults to GBP
# because the EU provider entries in the library (British Gas, Thames Water,
# EDF Energy) all bill in GBP — see DECISIONS.md "Spec gaps observed" for the
# acknowledged ambiguity (DESIGN.md §4 lists US/EU regional rules without
# specifying a default currency).

REGIONAL_RULES: dict[ReferenceRegion, RegionalRules] = {
    ReferenceRegion.US_EAST: RegionalRules(
        region=ReferenceRegion.US_EAST,
        decimal_separator=".",
        date_format="MM/DD/YYYY",
        currency_default="USD",
    ),
    ReferenceRegion.US_WEST: RegionalRules(
        region=ReferenceRegion.US_WEST,
        decimal_separator=".",
        date_format="MM/DD/YYYY",
        currency_default="USD",
    ),
    ReferenceRegion.EU: RegionalRules(
        region=ReferenceRegion.EU,
        decimal_separator=",",
        date_format="DD/MM/YYYY",
        currency_default="GBP",
    ),
}


# ---------------------------------------------------------------------------
# Lookup functions
# ---------------------------------------------------------------------------


def _normalize_provider_key(raw: str) -> str:
    """Case-fold, strip whitespace, collapse internal whitespace. Used for
    both the haystack (provider names + aliases) and the needle.
    """

    return " ".join(raw.strip().split()).casefold()


# Pre-built index from normalized name/alias -> Provider, computed once at
# import time. Last-write-wins on collision; the constants above are curated
# to avoid alias collisions.
_PROVIDER_INDEX: dict[str, Provider] = {}
for _p in PROVIDERS:
    _PROVIDER_INDEX[_normalize_provider_key(_p.name)] = _p
    for _alias in _p.aliases:
        _PROVIDER_INDEX[_normalize_provider_key(_alias)] = _p


def canonicalize_provider(raw_name: str) -> Optional[Provider]:
    """Resolve a raw provider name string to a Provider record.

    Match is case-insensitive on whitespace-normalized input, checked against
    both the canonical ``name`` and each entry in ``aliases``. Returns ``None``
    if no match — the caller (normalization service) decides whether that
    becomes a structural quality flag.
    """

    if not raw_name:
        return None
    return _PROVIDER_INDEX.get(_normalize_provider_key(raw_name))


def is_known_provider(raw_name: str) -> bool:
    """``True`` iff ``canonicalize_provider`` would return a Provider."""

    return canonicalize_provider(raw_name) is not None


def convert_unit(value: float, from_unit: str, to_unit: str) -> float:
    """Convert ``value`` from ``from_unit`` to ``to_unit``.

    Identity conversions are returned unchanged. Otherwise the direct
    (from, to) pair is looked up in ``_CONVERSIONS``; on miss the reverse
    pair is tried and the reciprocal applied. Raises ``ValueError`` if the
    pair is not defined in either direction — the caller treats that as an
    incompatible-unit error (e.g., kWh -> gallons).
    """

    if from_unit == to_unit:
        return value
    factor = _CONVERSIONS.get((from_unit, to_unit))
    if factor is not None:
        return value * factor
    inverse = _CONVERSIONS.get((to_unit, from_unit))
    if inverse is not None:
        return value / inverse
    raise ValueError(
        f"No conversion defined from {from_unit!r} to {to_unit!r}; "
        f"units are likely incompatible."
    )


def get_regional_rules(region: str) -> RegionalRules:
    """Return the rules record for ``region``.

    Accepts either a ``ReferenceRegion`` instance or its string value.
    Raises ``ValueError`` if the region is not in the rules table.
    """

    if isinstance(region, ReferenceRegion):
        key = region
    else:
        try:
            key = ReferenceRegion(region)
        except ValueError as exc:
            raise ValueError(
                f"Unknown region {region!r}; expected one of "
                f"{[r.value for r in ReferenceRegion]}."
            ) from exc
    return REGIONAL_RULES[key]
