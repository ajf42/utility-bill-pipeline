"""SQLite-backed stores for meter history and the audit log.

Two stores share a single SQLite DB file. Each owns its own
``sqlite3.Connection``; foreign-key enforcement is set per-connection via
PRAGMA. All write operations commit explicitly.

The pydantic models from ``src.models`` are the input and output shapes —
SQL is confined to this module. Dates round-trip as ISO 8601 strings,
booleans as 0/1 INTEGER, enums as their ``.value`` string. The
``AuditEntry`` payload round-trips as a single ``payload_json`` column
(serialized via ``model_dump_json``), with a few denormalized columns
alongside (``triage_route``, ``routing_key``, ``batch_id``,
``bill_external_ref``) for query speed.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from src.models.audit import AuditEntry
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

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _open_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _iso(d: date) -> str:
    return d.isoformat()


def _maybe_iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _maybe_parse_date(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def _site_from_row(r: sqlite3.Row) -> Site:
    return Site(
        id=r["id"],
        name=r["name"],
        portfolio_id=r["portfolio_id"],
        region=Region(r["region"]),
    )


def _account_from_row(r: sqlite3.Row) -> Account:
    return Account(
        id=r["id"],
        account_number=r["account_number"],
        account_type=AccountType(r["account_type"]),
        site_id=r["site_id"],
        generation_account=bool(r["generation_account"]),
    )


def _meter_from_row(r: sqlite3.Row) -> Meter:
    return Meter(
        id=r["id"],
        meter_id_string=r["meter_id_string"],
        account_id=r["account_id"],
        unit=Unit(r["unit"]),
        currency=r["currency"],
        type=MeterType(r["type"]),
        landlord_or_tenant=LandlordOrTenant(r["landlord_or_tenant"]),
        active=bool(r["active"]),
        start_date=_parse_date(r["start_date"]),
        end_date=_maybe_parse_date(r["end_date"]),
    )


def _reading_from_row(r: sqlite3.Row) -> Reading:
    return Reading(
        id=r["id"],
        meter_id=r["meter_id"],
        period_start=_parse_date(r["period_start"]),
        period_end=_parse_date(r["period_end"]),
        usage=r["usage"],
        usage_units=Unit(r["usage_units"]),
        cost=r["cost"],
        currency=r["currency"],
        demand_kw=r["demand_kw"],
        demand_spend=r["demand_spend"],
        energy_exported=r["energy_exported"],
    )


def _audit_from_row(r: sqlite3.Row) -> AuditEntry:
    entry = AuditEntry.model_validate_json(r["payload_json"])
    entry.id = r["id"]
    return entry


class MeterHistoryStore:
    """Owns sites, accounts, meters, and readings.

    Consulted by the reconciliation service to resolve a meter and pull
    its prior readings; written to on the AutoResolve triage path.
    """

    def __init__(self, db_path: Path | str):
        self._conn = _open_connection(Path(db_path))

    def close(self) -> None:
        self._conn.close()

    # --- writes ---------------------------------------------------------

    def add_site(self, site: Site) -> int:
        cur = self._conn.execute(
            "INSERT INTO sites (name, portfolio_id, region) VALUES (?, ?, ?)",
            (site.name, site.portfolio_id, site.region.value),
        )
        self._conn.commit()
        return cur.lastrowid

    def add_account(self, account: Account) -> int:
        cur = self._conn.execute(
            "INSERT INTO accounts (account_number, account_type, site_id, generation_account) "
            "VALUES (?, ?, ?, ?)",
            (
                account.account_number,
                account.account_type.value,
                account.site_id,
                int(account.generation_account),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def add_meter(self, meter: Meter) -> int:
        cur = self._conn.execute(
            "INSERT INTO meters (meter_id_string, account_id, unit, currency, type, "
            "landlord_or_tenant, active, start_date, end_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meter.meter_id_string,
                meter.account_id,
                meter.unit.value,
                meter.currency,
                meter.type.value,
                meter.landlord_or_tenant.value,
                int(meter.active),
                _iso(meter.start_date),
                _maybe_iso(meter.end_date),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def add_reading(
        self,
        reading: Reading,
        *,
        source_mode: str,
        batch_id: Optional[str] = None,
        ingested_at: Optional[datetime] = None,
    ) -> int:
        """Insert a reading. Returns the assigned rowid.

        ``source_mode``, ``batch_id``, and ``ingested_at`` are columns on
        the readings table that aren't on the Reading pydantic model —
        they describe how the reading entered the system. Per DESIGN.md §4
        ("Persistence Layer / add_reading contract") ``source_mode`` is
        required with no default (pipeline writes pass it from the
        ``RawBillInput``; fixture seeding passes ``"FIXTURE"`` explicitly).
        ``ingested_at`` defaults to ``datetime.now(UTC)`` when unset.
        """

        if ingested_at is None:
            ingested_at = datetime.now(timezone.utc)
        cur = self._conn.execute(
            "INSERT INTO readings (meter_id, period_start, period_end, usage, "
            "usage_units, cost, currency, demand_kw, demand_spend, energy_exported, "
            "ingested_at, source_mode, batch_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reading.meter_id,
                _iso(reading.period_start),
                _iso(reading.period_end),
                reading.usage,
                reading.usage_units.value,
                reading.cost,
                reading.currency,
                reading.demand_kw,
                reading.demand_spend,
                reading.energy_exported,
                ingested_at.isoformat(),
                source_mode,
                batch_id,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    # --- reads ----------------------------------------------------------

    def get_site_by_name(self, name: str) -> Optional[Site]:
        row = self._conn.execute(
            "SELECT id, name, portfolio_id, region FROM sites WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return _site_from_row(row) if row else None

    def find_meter(
        self,
        meter_id_string: str,
        account_number: str,
        site_name: str,
    ) -> Optional[Meter]:
        """Resolve a meter by its three-part identifier.

        Reconciliation's primary lookup: the incoming bill names a meter,
        an account number, and a site; all three must agree before the
        bill is considered reconciled. Returns ``None`` when no row
        matches all three keys.
        """

        row = self._conn.execute(
            """
            SELECT m.id, m.meter_id_string, m.account_id, m.unit, m.currency,
                   m.type, m.landlord_or_tenant, m.active, m.start_date, m.end_date
            FROM meters m
            JOIN accounts a ON m.account_id = a.id
            JOIN sites s ON a.site_id = s.id
            WHERE m.meter_id_string = ?
              AND a.account_number = ?
              AND s.name = ?
            LIMIT 1
            """,
            (meter_id_string, account_number, site_name),
        ).fetchone()
        return _meter_from_row(row) if row else None

    def get_prior_readings(self, meter_id: int, limit: int = 12) -> list[Reading]:
        rows = self._conn.execute(
            """
            SELECT id, meter_id, period_start, period_end, usage, usage_units,
                   cost, currency, demand_kw, demand_spend, energy_exported
            FROM readings
            WHERE meter_id = ?
            ORDER BY period_end DESC
            LIMIT ?
            """,
            (meter_id, limit),
        ).fetchall()
        return [_reading_from_row(r) for r in rows]


class AuditLogStore:
    """Owns the audit_entries table.

    Every bill that flows through the pipeline produces exactly one
    AuditEntry. The full entry is serialized into ``payload_json``;
    denormalized columns alongside it support the two query patterns
    the audit log is asked for: "what happened to this bill" (by
    ``bill_external_ref``) and "what happened in this upload" (by
    ``batch_id``).
    """

    def __init__(self, db_path: Path | str):
        self._conn = _open_connection(Path(db_path))

    def close(self) -> None:
        self._conn.close()

    def record(self, entry: AuditEntry) -> int:
        payload_json = entry.model_dump_json()
        decision = entry.triage_decision
        cur = self._conn.execute(
            "INSERT INTO audit_entries (bill_external_ref, batch_id, timestamp, "
            "source_mode, triage_route, routing_key, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry.bill_external_ref,
                entry.batch_id,
                entry.timestamp.isoformat(),
                entry.source_mode,
                decision.route.value,
                decision.routing_key.value if decision.routing_key else None,
                payload_json,
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_by_bill_ref(self, bill_external_ref: str) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT id, payload_json FROM audit_entries "
            "WHERE bill_external_ref = ? ORDER BY id",
            (bill_external_ref,),
        ).fetchall()
        return [_audit_from_row(r) for r in rows]

    def get_by_batch(self, batch_id: str) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT id, payload_json FROM audit_entries "
            "WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
        return [_audit_from_row(r) for r in rows]
