# CLAUDE.md — Project Working Memory

This file is the project's living working memory. It describes **what exists right now**, **what the rules are**, and **how to work in this repo**. Refresh it on every commit that touches anything described here, and call out any CLAUDE.md edit at the top of the response that makes it — this is the easiest piece of state to overlook.

## How this document relates to DESIGN.md

[DESIGN.md](DESIGN.md) is the project specification — what the system should be, and the rules that govern building it. It changes deliberately and infrequently.

CLAUDE.md is working memory — what currently exists, what is in flight, and quick-reference pointers into DESIGN.md. It changes after every commit that touches code, structure, or rules.

When the two appear to disagree, DESIGN.md wins for spec questions; CLAUDE.md wins for "what does the repo look like right now." If a disagreement is structural (not just stale state), surface it and update DESIGN.md.

---

## Resuming work after a broken session

If picking up after an interrupted Claude Code session, a context compaction event, a multi-day pause, or a crash:

1. Re-read [DESIGN.md](DESIGN.md) (top to bottom) and [CLAUDE.md](CLAUDE.md) (this document) before doing anything else.
2. Run `git status` and `git log --oneline -10` to see what was committed vs. what is uncommitted.
3. Check [TASKS.md](TASKS.md) for what was in flight — the last unchecked item with no commit hash is the most likely resume point.
4. If [TASKS.md](TASKS.md), the filesystem, and `git log` appear to disagree on what state the project is in, stop and surface the disagreement before continuing. Do not infer.

---

## Project description

A working prototype of an AI-augmented utility bill ingestion and quality assurance pipeline. The system accepts a utility bill row (single or batch), normalizes that data against a reference library, reconciles it against meter history in a real datastore, validates it against a schema and a set of domain heuristics, and decides per-record whether the result auto-resolves, gets a Claude-drafted resolution proposal for human review, or escalates to a routed team queue. Every step is logged to an audit trail. The prototype targets the operational problem set Measurabl runs every day; the goal is not to build a production system but to demonstrate a defensible architectural approach to the problem and the engineering discipline that would scale it across an offshore team.

The walkthrough audience is Matt Richardson, who owns Measurabl's back-office function. He is a process-and-operations leader with high systems intuition. He cannot trust black boxes. Every decision must be explicit and logged. The methodology artifacts (this file, [DECISIONS.md](DECISIONS.md), [TASKS.md](TASKS.md)) and the scale-to-production companion doc are the differentiators, not the code.

## Current state

**Phase 1 — Foundation: complete. Phase 2 — Single-Row Pipeline: in progress.** The repo holds the methodology layer, the pydantic contracts, the SQLite persistence layer, the fixture seed data, the reference data layer, the FastAPI app entry, and `POST /bills` with the JSON-row ingestion handler. `POST /bills` currently returns a stub response (`pipeline_status: "ingested_only_not_yet_processed"`) — subsequent prompts wire normalization, reconciliation, validation, and triage. Next up: normalization service.

What exists:

- Repo skeleton, `.gitignore`, `pyproject.toml` (Python 3.11+, FastAPI, pydantic v2, Anthropic, pandas, openpyxl, pytest, httpx)
- [DESIGN.md](DESIGN.md) — authoritative spec
- [README.md](README.md) — skeleton with architecture diagram placeholder, quick start, walkthrough placeholder, status
- [CLAUDE.md](CLAUDE.md) — this file
- [DECISIONS.md](DECISIONS.md) — initialized with ADR-001 through ADR-007 plus a Spec gaps observed section
- [TASKS.md](TASKS.md) — Phase 2–4 backlog; Phase 1 marked complete with commit references
- pydantic v2 data models under `src/models/` (see Models below)
- SQLite schema and stores under `src/db/` (see Persistence below)
- Fixture seed data under `src/db/fixtures.py` plus the CLI seed script (see Fixtures below)
- `tests/test_models.py` — 12 tests covering instantiation, enum/format validation, serialization roundtrip, and the deferred cross-field check per ADR-006
- `tests/test_store.py` — 7 tests covering CRUD round-trip per entity, three-key meter lookup, prior-readings ordering and limit, audit-log JSON payload round-trip, and schema idempotency
- `tests/test_fixtures.py` — 4 tests covering fixture counts, meter resolution, the Liberty main gap-scenario seed, and end-to-end round-trip via pydantic
- Reference data layer under `src/services/reference.py` (see Reference layer below)
- `tests/test_reference.py` — 15 tests covering module load, provider library contract, alias + case-insensitive canonicalization, unit conversion (direct, inverse, identity, incompatible-unit failure), and regional rules
- FastAPI app entry at `src/main.py`, bills router at `src/routes/bills.py`, ingestion service at `src/services/ingestion.py` (see HTTP surface below)
- `tests/test_ingestion.py` — 11 tests covering RawBillInput construction, payload copy-not-alias, parametrized missing-field rejection across all seven required fields, optional-field preservation, and non-dict rejection
- `tests/test_routes_bills.py` — 3 tests (TestClient) covering `GET /health`, the `POST /bills` stub-response shape, and the 422 boundary
- `scripts/check_design_sync.py` + `tests/test_design_sync.py` — structural drift guard that parses DESIGN.md §8 at runtime and fails if any ≥12-word contiguous block from §8 also appears in CLAUDE.md (normalized comparison)

What does **not** yet exist (Phase 2+):

- Remaining service code (normalization, reconciliation, validation, triage, drafter, audit, output)
- `POST /batches` route (XLSX batch handler)
- Sample scenarios in `samples/scenarios.md`

The next unit of work is Phase 2: FastAPI scaffolding and the `POST /bills` endpoint, then the JSON-row ingestion handler. See [TASKS.md](TASKS.md) Phase 2 for the full ordered list.

### Models

The pydantic models are the contracts every downstream stage and the audit log consume. Foreign-key fields are typed as `int` (SQLite rowids); relational integrity lives in the DB layer.

- [src/models/entities.py](src/models/entities.py) — Measurabl-aligned hierarchy: `Portfolio`, `Site`, `Account`, `Meter`, `Reading`, plus the enums `Region`, `AccountType`, `Unit`, `MeterType`, `LandlordOrTenant`. Currency is validated to ISO 4217 shape; unit-of-measure to the canonical set (kWh, therms, MMBtu, m3, ccf, gallons, HCF).
- [src/models/bill.py](src/models/bill.py) — pipeline-stage artifacts modeled by inheritance: `RawBillInput` → `NormalizedBill` → `ReconciledBill` → `ValidatedBill`. Each stage adds fields without replacing prior ones. Plus the `SourceMode` enum.
- [src/models/quality.py](src/models/quality.py) — `QualityFlag` and `TriageDecision`, plus the enums `Severity`, `FlagType`, `RoutingKey`, `TriageRoute`.
- [src/models/audit.py](src/models/audit.py) — `AuditEntry`, the full lineage record persisted per bill.

### Persistence

Two SQLite-backed stores share one DB file. Stdlib `sqlite3` only, no ORM (see ADR-007).

- [src/db/schema.sql](src/db/schema.sql) — DDL for `sites`, `accounts`, `meters`, `readings`, `audit_entries`. Dates and datetimes are ISO 8601 TEXT; booleans are INTEGER 0/1. All CREATE statements use `IF NOT EXISTS` so the schema is idempotent. Indexes: `readings(meter_id, period_end)` for reconciliation lookups, plus `batch_id` and `bill_external_ref` on `audit_entries`.
- [src/db/store.py](src/db/store.py) — `MeterHistoryStore` (sites/accounts/meters/readings, plus the three-key `find_meter` reconciliation lookup and `get_prior_readings`) and `AuditLogStore` (record / query by bill_external_ref / query by batch_id). Each store owns its own connection with `PRAGMA foreign_keys = ON`; writes commit explicitly. Pydantic models in and out; SQL stays inside this module. The AuditEntry payload round-trips through a single `payload_json` column with a few denormalized columns alongside for query speed.

### Fixtures

Hand-written seed data lives in code, not JSON files — single source of truth, visible in one place. Counts: 3 sites, 5 accounts, 8 meters, 34 readings.

- [src/db/fixtures.py](src/db/fixtures.py) — `seed_fixtures(store: MeterHistoryStore) -> dict[str, int]` populates the DB. Three sites (Liberty Tower / US, Pacific Plaza / US, Thames Court / EU); five accounts spanning CONNECT, BILL_UPLOAD, and MANUAL source modes plus a generation_account=True on Thames Court for the solar case; eight meters covering electric (kWh), gas (therms), water (HCF), and the solar-export generation case (kWh); 34 monthly readings — four recent months per meter, with two additional older readings on the Liberty Tower main electric meter to seed the Phase 3 gap-detection scenario. Realistic provider names (ConEd, National Grid, PG&E, EBMUD, Octopus Energy), synthetic account numbers.
- [src/db/seed.py](src/db/seed.py) — CLI: `python -m src.db.seed [--db-path ./prototype.db]`. Idempotent — exits without writing if any sites row already exists.

### Reference layer

In-memory, no DB and no FastAPI deps. Downstream services (normalization, validation) import the constants and helpers directly.

- [src/services/reference.py](src/services/reference.py) — `Provider` and `RegionalRules` pydantic models plus the `ReferenceRegion` enum (`US-East`, `US-West`, `EU`). Module-level constants: `PROVIDERS` (10 entries: ConEd, National Grid, Duke Energy, PG&E, SoCal Edison, Xcel Energy, Pacific Gas, British Gas, Thames Water, EDF Energy), `_CONVERSIONS` (direct-pair table covering energy/gas-energy cross-fuel and pure-volume conversions with source-cited factors), and `REGIONAL_RULES`. Functions: `canonicalize_provider` (case-insensitive, whitespace-normalized, alias-aware), `is_known_provider`, `convert_unit` (direct lookup, falls back to reciprocal of the reverse pair, raises `ValueError` on incompatible units), `get_regional_rules`. Provider lookup is backed by a precomputed normalized index built at import time.

### HTTP surface

FastAPI app, no middleware / auth / CORS. Run locally with `uvicorn src.main:app --reload`.

- [src/main.py](src/main.py) — instantiates `FastAPI(title="Utility Bill Pipeline", version="0.2.0")`, mounts the bills router, exposes `GET /health` returning `{"status": "ok", "version": "0.2.0"}`.
- [src/routes/bills.py](src/routes/bills.py) — `POST /bills` handler. Accepts a loose `dict` body, delegates to `ingest_json_row`, returns `{"raw_input": <RawBillInput as JSON dict>, "pipeline_status": "ingested_only_not_yet_processed"}`. `ValueError` from the service is mapped to HTTP 422 at the boundary. The stub return is temporary; downstream stages replace it in subsequent prompts.
- [src/services/ingestion.py](src/services/ingestion.py) — `ingest_json_row(payload: dict) -> RawBillInput`. Validates presence of the seven required fields (`period_start`, `period_end`, `usage`, `usage_units`, `meter_id_string`, `account_number`, `site_name`) and constructs a `RawBillInput` with `source_mode=JSON_ROW` and `batch_id=None`. Field parsing and canonicalization belong to normalization, not here. Payload is shallow-copied so caller mutations don't reach the artifact.

## File structure (as it stands)

```
utility-bill-pipeline/
  DESIGN.md
  README.md
  CLAUDE.md
  DECISIONS.md
  TASKS.md
  pyproject.toml
  .gitignore
  src/
    __init__.py
    main.py
    models/
      __init__.py
      entities.py
      bill.py
      quality.py
      audit.py
    db/
      __init__.py
      schema.sql
      store.py
      fixtures.py
      seed.py
    services/
      __init__.py
      reference.py
      ingestion.py
    routes/
      __init__.py
      bills.py
  tests/
    __init__.py
    test_models.py
    test_store.py
    test_fixtures.py
    test_reference.py
    test_ingestion.py
    test_routes_bills.py
    test_design_sync.py
  scripts/
    check_design_sync.py
  samples/__init__.py
  docs/__init__.py
```

The full target structure (what this skeleton grows into) is enumerated in [DESIGN.md](DESIGN.md) §10.

---

## Build rules (summary)

> Authoritative source: [DESIGN.md](DESIGN.md) §8. This section is a working-memory summary; the full text and any future revisions live in DESIGN.md.

### Mandatory on every change

Eight rules govern every change; see [DESIGN.md](DESIGN.md) §8 for the full list.

1. Code runs
2. Tests pass (`pytest`)
3. Inline docs updated
4. README updated when API surface or architecture changes
5. DECISIONS.md updated when an architectural or engineering choice was made
6. TASKS.md updated (task done, commit hash recorded)
7. CLAUDE.md updated when files, directories, services, endpoints, models, stores, or rules change
8. Committed under the commit convention, one commit per task

**Current-state update rule.** The `Current state` section of CLAUDE.md MUST be updated on every commit that adds, removes, or significantly modifies a file, directory, service, endpoint, model, or store. This is the highest-churn part of the working memory and the easiest to silently drift.

**Self-maintenance.** Edits to CLAUDE.md should be called out at the top of the response that makes them, so reviewers see the working-memory change without needing to diff.

**Context budget.** CLAUDE.md targets 200 lines and must not exceed 300. If a commit would push CLAUDE.md past 300 lines, compress before continuing: move detail into per-directory `README.md` files, into [DESIGN.md](DESIGN.md) where it represents spec, or into archive sections in CLAUDE.md itself. The `Current state` section is the primary compression target.

### Coding patterns

Short summary: FastAPI `Depends` for dependency injection; thin route handlers (no business logic); stateless services with state in injected stores; async on I/O (especially Anthropic calls); pydantic for every DTO. See [DESIGN.md](DESIGN.md) §8 for the full list.

### Commit convention

`<scope>: <imperative description>`. One task per commit. Commit hashes recorded in [TASKS.md](TASKS.md). See [DESIGN.md](DESIGN.md) §8 for examples.

### Sample scenarios

Multi-bill stories that demonstrate stateful behavior (gap, overlap), not isolated rows. Documented in `samples/scenarios.md`. See [DESIGN.md](DESIGN.md) §8 for the full convention.

### Anonymization

If real utility bills are used in fixtures, strip all PII before committing. See [DESIGN.md](DESIGN.md) §8.

---

## Working with this codebase

**Ambiguity handling — confidence-filling on ambiguous spec is forbidden.** When [DESIGN.md](DESIGN.md) is silent or ambiguous on something a change requires, do not invent. Stop and flag the gap. Either ask the human, or add an explicit `TODO` comment in the code AND a note in [DECISIONS.md](DECISIONS.md) under the "Spec gaps observed" section. The cost of asking is low; the cost of inventing a constraint that DESIGN.md never sanctioned is high.

**Drift check.** `scripts/check_design_sync.py` (also wired into pytest as `tests/test_design_sync.py`) parses [DESIGN.md](DESIGN.md) §8 at runtime and fails if any ≥12-word contiguous block from §8 appears in CLAUDE.md after normalization (case, whitespace, markdown formatting, smart quotes/dashes folded). The rules-summary section above must reference §8, not copy it. The check is self-updating — when §8 is edited, the next test run uses the new text.
