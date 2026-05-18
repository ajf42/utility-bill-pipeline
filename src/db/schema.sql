-- SQLite schema for the meter history store and audit log.
--
-- Dates and datetimes are stored as ISO 8601 TEXT and parsed at the Python
-- boundary; SQLite has no native DATETIME type. Booleans are INTEGER (0/1).
-- All CREATE statements use IF NOT EXISTS so this file can be re-executed
-- against an existing DB without error.
--
-- PRAGMA foreign_keys = ON is set on each connection in store.py (it is a
-- per-connection pragma, not a schema-level one).

CREATE TABLE IF NOT EXISTS sites (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    portfolio_id  INTEGER,
    region        TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id                  INTEGER PRIMARY KEY,
    account_number      TEXT NOT NULL,
    account_type        TEXT NOT NULL,
    site_id             INTEGER REFERENCES sites(id),
    generation_account  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meters (
    id                  INTEGER PRIMARY KEY,
    meter_id_string     TEXT UNIQUE NOT NULL,
    account_id          INTEGER REFERENCES accounts(id),
    unit                TEXT NOT NULL,
    currency            TEXT NOT NULL,
    type                TEXT NOT NULL,
    landlord_or_tenant  TEXT,
    active              INTEGER NOT NULL DEFAULT 1,
    start_date          TEXT NOT NULL,
    end_date            TEXT
);

CREATE TABLE IF NOT EXISTS readings (
    id               INTEGER PRIMARY KEY,
    meter_id         INTEGER REFERENCES meters(id),
    period_start     TEXT NOT NULL,
    period_end       TEXT NOT NULL,
    usage            REAL NOT NULL,
    usage_units      TEXT NOT NULL,
    cost             REAL,
    currency         TEXT,
    demand_kw        REAL,
    demand_spend     REAL,
    energy_exported  REAL,
    ingested_at      TEXT NOT NULL,
    source_mode      TEXT NOT NULL,
    batch_id         TEXT
);

CREATE TABLE IF NOT EXISTS audit_entries (
    id                  INTEGER PRIMARY KEY,
    bill_external_ref   TEXT,
    batch_id            TEXT,
    timestamp           TEXT NOT NULL,
    source_mode         TEXT,
    triage_route        TEXT NOT NULL,
    routing_key         TEXT,
    payload_json        TEXT NOT NULL
);

-- Reconciliation: fetch the last N readings on a meter, sorted by period_end.
CREATE INDEX IF NOT EXISTS idx_readings_meter_period
    ON readings(meter_id, period_end);

-- Audit log query patterns.
CREATE INDEX IF NOT EXISTS idx_audit_batch
    ON audit_entries(batch_id);

CREATE INDEX IF NOT EXISTS idx_audit_bill
    ON audit_entries(bill_external_ref);
