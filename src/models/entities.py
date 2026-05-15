"""Measurabl-aligned entity model.

The hierarchy mirrors Measurabl's published one: Portfolio -> Site -> Account
-> Meter -> Reading. Upper levels (Portfolio, Site) are tiny fixture tables;
they exist so the foreign keys downstream are honest. Field detail is drawn
from Measurabl's published bulk upload templates.

Foreign-key fields are typed as ``int`` and correspond to SQLite rowids.
Relational integrity (existence, ON DELETE behavior) lives in the DB layer,
not in these pydantic models.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Region(str, Enum):
    """Regional ruleset selector used downstream for decimal/date conventions."""

    US = "US"
    EU = "EU"


class AccountType(str, Enum):
    """How the bills for this account reach the system. See DESIGN.md §2."""

    CONNECT = "CONNECT"
    BILL_UPLOAD = "BILL_UPLOAD"
    MANUAL = "MANUAL"


class Unit(str, Enum):
    """Canonical unit-of-measure set the pipeline normalizes to.

    Anything outside this set is a structural-quality fail at normalization.
    """

    KWH = "kWh"
    THERMS = "therms"
    MMBTU = "MMBtu"
    M3 = "m3"
    CCF = "ccf"
    GALLONS = "gallons"
    HCF = "HCF"


class MeterType(str, Enum):
    """Commodity served by the meter."""

    ELECTRIC = "ELECTRIC"
    GAS = "GAS"
    WATER = "WATER"
    STEAM = "STEAM"
    CHILLED_WATER = "CHILLED_WATER"
    HOT_WATER = "HOT_WATER"
    OTHER = "OTHER"


class LandlordOrTenant(str, Enum):
    """Who pays for the meter. Drives Measurabl's split-incentive reporting."""

    LANDLORD = "LANDLORD"
    TENANT = "TENANT"


class Portfolio(BaseModel):
    """Top of the hierarchy. A customer's full collection of Sites."""

    id: int
    name: str


class Site(BaseModel):
    """A building. Owns one or more utility Accounts.

    Building-name match against Site is a high-severity validation rule
    (see DESIGN.md §4 entity model hard rules).
    """

    id: int
    name: str
    portfolio_id: int
    region: Region


class Account(BaseModel):
    """A utility account at a Site. Owns one or more Meters."""

    id: int
    account_number: str
    account_type: AccountType
    site_id: int
    generation_account: bool = False


class Meter(BaseModel):
    """A meter under an Account.

    Unit and currency are locked at the meter. A Reading with a different
    unit or currency is a high-severity flag (see ADR-006 — that cross-field
    check lives in the validation service, not here).

    ``meter_id_string`` follows Measurabl's naming convention:
    ``MSR.(provider)(account_number):(meter_number)``.
    """

    id: int
    meter_id_string: str
    account_id: int
    unit: Unit
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 alpha-3 currency code")
    type: MeterType
    landlord_or_tenant: LandlordOrTenant
    active: bool = True
    start_date: date
    end_date: Optional[date] = None


class Reading(BaseModel):
    """A meter reading. Append-only at the store level — corrections move
    through a flagged workflow rather than overwriting (see DESIGN.md §4).
    """

    id: int
    meter_id: int
    period_start: date
    period_end: date
    usage: float
    usage_units: Unit
    cost: Optional[float] = None
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 alpha-3 currency code")
    demand_kw: Optional[float] = None
    demand_spend: Optional[float] = None
    energy_exported: Optional[float] = None
