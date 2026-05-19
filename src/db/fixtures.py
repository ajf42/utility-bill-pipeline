"""Fixture seed data for the prototype DB.

Three Sites across two regions, five Accounts with a mix of source modes
(CONNECT, BILL_UPLOAD, MANUAL), eight Meters covering electric / gas /
water and one solar-export generation case, and 34 historical Readings:
four months of monthly readings on each of the eight meters, plus two
additional older readings on the Liberty Tower main electric meter to
support a gap-detection scenario in Phase 3.

The data is hand-written and obviously synthetic. Real provider names
appear (ConEd, PG&E, etc.) but no real account numbers or customer
identifiers. The structure is the load-bearing part; the values are
plausible monthly consumption for a commercial building.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from src.db.store import MeterHistoryStore
from src.models.entities import (
    Account,
    AccountType,
    LandlordOrTenant,
    Meter,
    MeterType,
    Reading,
    Region,
    Site,
    Unit,
)


@dataclass(frozen=True)
class _AccountSpec:
    site_name: str
    account_number: str
    account_type: AccountType
    generation_account: bool


@dataclass(frozen=True)
class _MeterSpec:
    account_number: str
    meter_id_string: str
    unit: Unit
    currency: str
    type: MeterType
    landlord_or_tenant: LandlordOrTenant
    active: bool = True


_SITES: list[Site] = [
    Site(id=0, name="Liberty Tower", portfolio_id=1, region=Region.US),
    Site(id=0, name="Pacific Plaza", portfolio_id=1, region=Region.US),
    Site(id=0, name="Thames Court", portfolio_id=1, region=Region.EU),
]


_ACCOUNTS: list[_AccountSpec] = [
    _AccountSpec("Liberty Tower", "LT-ELEC-001", AccountType.CONNECT, False),
    _AccountSpec("Liberty Tower", "LT-GAS-002", AccountType.BILL_UPLOAD, False),
    _AccountSpec("Pacific Plaza", "PP-ELEC-001", AccountType.CONNECT, False),
    _AccountSpec("Pacific Plaza", "PP-WATER-002", AccountType.MANUAL, False),
    _AccountSpec("Thames Court", "TC-ELEC-001", AccountType.BILL_UPLOAD, True),
]


_METERS: list[_MeterSpec] = [
    _MeterSpec(
        "LT-ELEC-001",
        "MSR.(ConEd)(LT-ELEC-001):(M1)",
        Unit.KWH, "USD", MeterType.ELECTRIC, LandlordOrTenant.LANDLORD,
    ),
    _MeterSpec(
        "LT-ELEC-001",
        "MSR.(ConEd)(LT-ELEC-001):(M2)",
        Unit.KWH, "USD", MeterType.ELECTRIC, LandlordOrTenant.LANDLORD,
    ),
    _MeterSpec(
        "LT-GAS-002",
        "MSR.(NationalGrid)(LT-GAS-002):(M1)",
        Unit.THERMS, "USD", MeterType.GAS, LandlordOrTenant.LANDLORD,
    ),
    _MeterSpec(
        "PP-ELEC-001",
        "MSR.(PGE)(PP-ELEC-001):(M1)",
        Unit.KWH, "USD", MeterType.ELECTRIC, LandlordOrTenant.LANDLORD,
    ),
    _MeterSpec(
        "PP-ELEC-001",
        "MSR.(PGE)(PP-ELEC-001):(M2)",
        Unit.KWH, "USD", MeterType.ELECTRIC, LandlordOrTenant.TENANT,
    ),
    _MeterSpec(
        "PP-WATER-002",
        "MSR.(EBMUD)(PP-WATER-002):(M1)",
        Unit.HCF, "USD", MeterType.WATER, LandlordOrTenant.LANDLORD,
    ),
    _MeterSpec(
        "TC-ELEC-001",
        "MSR.(OctopusEnergy)(TC-ELEC-001):(M1)",
        Unit.KWH, "GBP", MeterType.ELECTRIC, LandlordOrTenant.LANDLORD,
    ),
    _MeterSpec(
        "TC-ELEC-001",
        "MSR.(OctopusEnergy)(TC-ELEC-001):(M2)",
        Unit.KWH, "GBP", MeterType.ELECTRIC, LandlordOrTenant.LANDLORD,
    ),
    # Inactive meter — exists in the store but is marked active=False.
    # Bills against this meter fire the INACTIVE_METER flag in validation.
    # Demo case 6 ("inactive meter") targets this row.
    _MeterSpec(
        "LT-ELEC-001",
        "MSR.(ConEd)(LT-ELEC-001):(OLD-M0)",
        Unit.KWH, "USD", MeterType.ELECTRIC, LandlordOrTenant.LANDLORD,
        active=False,
    ),
    # Unknown-provider meter — provider alias 'GreenfieldCoop' is not in
    # the reference library. Bills against this meter expose the
    # `provider_known=False` structural signal. Demo case 5 routes here.
    _MeterSpec(
        "LT-GAS-002",
        "MSR.(GreenfieldCoop)(LT-GAS-002):(M2)",
        Unit.THERMS, "USD", MeterType.GAS, LandlordOrTenant.LANDLORD,
    ),
]


# (year, month, usage, cost | None) per meter. Liberty main electric carries
# two older readings before its four recent ones to support the Phase 3
# gap-detection scenario; every other meter has four recent monthly readings.
_READINGS_BY_METER: dict[str, list[tuple[int, int, float, float | None]]] = {
    "MSR.(ConEd)(LT-ELEC-001):(M1)": [
        (2025, 7, 34500.0, 4140.0),
        (2025, 8, 35200.0, 4224.0),
        (2026, 1, 32100.0, 3852.0),
        (2026, 2, 29800.0, 3576.0),
        (2026, 3, 28400.0, 3408.0),
        (2026, 4, 30100.0, 3612.0),
    ],
    "MSR.(ConEd)(LT-ELEC-001):(M2)": [
        (2026, 1, 6200.0, 744.0),
        (2026, 2, 5800.0, 696.0),
        (2026, 3, 5500.0, 660.0),
        (2026, 4, 5900.0, 708.0),
    ],
    "MSR.(NationalGrid)(LT-GAS-002):(M1)": [
        (2026, 1, 2400.0, 3120.0),
        (2026, 2, 2150.0, 2795.0),
        (2026, 3, 1600.0, 2080.0),
        (2026, 4, 900.0, 1170.0),
    ],
    "MSR.(PGE)(PP-ELEC-001):(M1)": [
        (2026, 1, 27300.0, 4641.0),
        (2026, 2, 25800.0, 4386.0),
        (2026, 3, 26900.0, 4573.0),
        (2026, 4, 28200.0, 4794.0),
    ],
    "MSR.(PGE)(PP-ELEC-001):(M2)": [
        (2026, 1, 4100.0, 697.0),
        (2026, 2, 3850.0, 654.5),
        (2026, 3, 3950.0, 671.5),
        (2026, 4, 4200.0, 714.0),
    ],
    "MSR.(EBMUD)(PP-WATER-002):(M1)": [
        (2026, 1, 220.0, 880.0),
        (2026, 2, 195.0, 780.0),
        (2026, 3, 240.0, 960.0),
        (2026, 4, 265.0, 1060.0),
    ],
    "MSR.(OctopusEnergy)(TC-ELEC-001):(M1)": [
        (2026, 1, 18500.0, 5550.0),
        (2026, 2, 17200.0, 5160.0),
        (2026, 3, 16800.0, 5040.0),
        (2026, 4, 15900.0, 4770.0),
    ],
    "MSR.(OctopusEnergy)(TC-ELEC-001):(M2)": [
        (2026, 1, 2100.0, None),
        (2026, 2, 3400.0, None),
        (2026, 3, 4800.0, None),
        (2026, 4, 5600.0, None),
    ],
    # Inactive meter carries one old reading so the row is realistic;
    # the active=False flag is what drives the INACTIVE_METER demo case.
    "MSR.(ConEd)(LT-ELEC-001):(OLD-M0)": [
        (2024, 6, 28900.0, 3468.0),
    ],
    # Unknown-provider gas meter — four monthly readings so the meter is
    # reconciliation-resolvable and the demo bill exercises the
    # provider_known=False structural signal cleanly.
    "MSR.(GreenfieldCoop)(LT-GAS-002):(M2)": [
        (2026, 1, 1800.0, 2340.0),
        (2026, 2, 1620.0, 2106.0),
        (2026, 3, 1200.0, 1560.0),
        (2026, 4, 720.0, 936.0),
    ],
}


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def seed_fixtures(store: MeterHistoryStore) -> dict[str, int]:
    """Populate ``store`` with the fixture data. Returns counts by entity.

    Assumes ``store`` is empty. The caller is responsible for idempotency
    if invoked against an already-seeded DB (see ``src/db/seed.py`` for
    the script-level idempotency check).
    """

    site_id_by_name: dict[str, int] = {}
    for site in _SITES:
        site_id_by_name[site.name] = store.add_site(site)

    account_id_by_number: dict[str, int] = {}
    for spec in _ACCOUNTS:
        account = Account(
            id=0,
            account_number=spec.account_number,
            account_type=spec.account_type,
            site_id=site_id_by_name[spec.site_name],
            generation_account=spec.generation_account,
        )
        account_id_by_number[spec.account_number] = store.add_account(account)

    meter_by_string: dict[str, tuple[int, _MeterSpec]] = {}
    for spec in _METERS:
        meter = Meter(
            id=0,
            meter_id_string=spec.meter_id_string,
            account_id=account_id_by_number[spec.account_number],
            unit=spec.unit,
            currency=spec.currency,
            type=spec.type,
            landlord_or_tenant=spec.landlord_or_tenant,
            active=spec.active,
            start_date=date(2024, 1, 1),
        )
        meter_id = store.add_meter(meter)
        meter_by_string[spec.meter_id_string] = (meter_id, spec)

    reading_count = 0
    for meter_string, (meter_id, spec) in meter_by_string.items():
        for year, month, usage, cost in _READINGS_BY_METER[meter_string]:
            reading = Reading(
                id=0,
                meter_id=meter_id,
                period_start=date(year, month, 1),
                period_end=_month_end(year, month),
                usage=usage,
                usage_units=spec.unit,
                cost=cost,
                currency=spec.currency,
            )
            store.add_reading(reading, source_mode="FIXTURE", batch_id=None)
            reading_count += 1

    return {
        "sites": len(_SITES),
        "accounts": len(_ACCOUNTS),
        "meters": len(_METERS),
        "readings": reading_count,
    }
