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

**Phase 1 — Foundation: complete. Phase 2 — Single-Row Pipeline: complete. Phase 3 — Triage, Drafter, Demo harness, Observability: complete.** The full single-row pipeline runs end-to-end through `POST /bills`: ingest → normalize → reconcile → validate → triage (with the Resolution Drafter wired in on the DraftForHumanReview route) → audit-log write. The human approval loop is closed via `POST /bills/{audit_ref}/approve` and `POST /bills/{audit_ref}/reject`. A canonical-bill demonstration harness ([scripts/demo.py](scripts/demo.py) + [scripts/demo_bills.json](scripts/demo_bills.json)) walks six curated bills through the live API end-to-end and prints a summary table; [WALKTHROUGH.md](WALKTHROUGH.md) mirrors the same six cases as a standalone portfolio document. Operational surface: structured JSON logging via [src/util/logging.py](src/util/logging.py) (stdlib only, no structlog) and `GET /status` ([src/routes/status.py](src/routes/status.py)) for ops visibility — audit counts in the last 24h by triage route, pending drafted queue depth, last write timestamp, Anthropic key boolean. The XLSX batch endpoint is deferred to the scale-to-production document. Phase 4 (walkthrough dry-run) is next.

What exists:

- Repo skeleton, `.gitignore`, `pyproject.toml` (Python 3.11+, FastAPI, pydantic v2, Anthropic, pandas, openpyxl, pytest, httpx)
- [DESIGN.md](DESIGN.md) — authoritative spec
- [README.md](README.md) — skeleton with architecture diagram placeholder, quick start, walkthrough placeholder, status
- [CLAUDE.md](CLAUDE.md) — this file
- [DECISIONS.md](DECISIONS.md) — ADR-001 through ADR-012 plus a Spec gaps observed section
- [TASKS.md](TASKS.md) — Phase 2–4 backlog; Phase 1 marked complete with commit references
- pydantic v2 data models under `src/models/` (see Models below)
- SQLite schema and stores under `src/db/` (see Persistence below)
- Fixture seed data under `src/db/fixtures.py` plus the CLI seed script (see Fixtures below)
- `tests/test_models.py` — 12 tests covering instantiation, enum/format validation, serialization roundtrip, and the deferred cross-field check per ADR-006
- `tests/test_store.py` — 8 tests covering CRUD round-trip per entity, three-key meter lookup, prior-readings ordering and limit, the `add_reading` keyword-required `source_mode` contract, audit-log JSON payload round-trip, and schema idempotency
- `tests/test_fixtures.py` — 4 tests covering fixture counts, meter resolution, the Liberty main gap-scenario seed, and end-to-end round-trip via pydantic
- Reference data layer under `src/services/reference.py` (see Reference layer below)
- `tests/test_reference.py` — 15 tests covering module load, provider library contract, alias + case-insensitive canonicalization, unit conversion (direct, inverse, identity, incompatible-unit failure), and regional rules
- FastAPI app entry at `src/main.py`, bills router at `src/routes/bills.py`, FastAPI dependency at `src/routes/dependencies.py`, ingestion / normalization / reconciliation services under `src/services/` (see HTTP surface and Services below)
- `tests/test_ingestion.py` — 11 tests covering RawBillInput construction, payload copy-not-alias, parametrized missing-field rejection across all seven required fields, optional-field preservation, and non-dict rejection
- `tests/test_normalization.py` — 19 tests covering NormalizedBill contract, signal-key presence on garbage input, provider canonicalization (known / unknown / malformed meter-id), usage range, billing-period range, malformed-date non-raising, unit case-insensitive canonicalization, currency-region cross-field agreement, unit-typical agreement, cost range, the all-False busted-payload case, and two route-integration paths via TestClient (with `get_store` overridden to an empty tmp DB)
- `tests/test_reconciliation.py` — 10 tests covering ReconciledBill contract, no-match empty-prior-context, matched-meter prior-readings attachment and prior_context summary, partial-match misses (wrong account / wrong site), prior_readings_limit override, date-not-datetime invariant, and two route-integration paths via TestClient (with `get_store` overridden to a fixture-seeded tmp DB)
- `tests/test_validation.py` — 15 tests, one per check or branch: clean-bill no flags, UNIT_MISMATCH (HIGH), CURRENCY_MISMATCH (HIGH), INACTIVE_METER (HIGH), GENERATION_MISMATCH (HIGH) + the no-fire-when-generation-account case, GAP at 10/4/1 days, no-prior no-GAP, OVERLAP (HIGH), contiguous-not-overlap, METER_UNASSIGNED isolation (no other checks fire), FORMAT_INVALID when period_start ≥ period_end
- `tests/test_pipeline_e2e.py` — Phase 2 acceptance gate: 2 tests through the live FastAPI route against a fixture-seeded tmp DB. Clean bill → no HIGH-severity flags; dirty bill (therms on a kWh meter, 14-day gap) → UNIT_MISMATCH HIGH + GAP HIGH.
- `tests/test_routes_bills.py` — 3 tests (TestClient) covering `GET /health`, the `POST /bills` validated-response shape with a METER_UNASSIGNED flag against the empty-store override, and the 422 boundary
- `scripts/check_design_sync.py` + `tests/test_design_sync.py` — structural drift guard that parses DESIGN.md §8 at runtime and fails if any ≥12-word contiguous block from §8 also appears in CLAUDE.md (normalized comparison)
- `src/models/drafter.py`, `src/prompts/drafter_system.md`, `src/services/drafter.py`, `tests/fakes.py`, `tests/test_drafter.py` — the Resolution Drafter Service (see Drafter below). Now wired into triage on the DraftForHumanReview route.
- `src/services/triage.py` — the three-route Triage service (see Triage below). Calls the Drafter on DraftForHumanReview; degrades to Escalate with `DRAFTER_FAILURE` on drafter exceptions.
- `tests/test_triage.py` — 11 tests covering routing logic (no-flags AutoResolve; one-MEDIUM AutoResolve; HIGH-only-fixable DraftForHumanReview; HIGH-non-fixable Escalate with mapped routing key; 3-MEDIUM Escalate; 2-MEDIUM DraftForHumanReview; unmatched-meter Escalate) plus drafter integration (drafter_output populated; DrafterParseError → DRAFTER_FAILURE; no-drafter warning path).
- `tests/test_approval.py` — 6 tests including the Phase 3 e2e acceptance gate: ingest → normalize → reconcile → validate → triage (FakeAnthropicClient) → approve → persistence on a synthetic unit-mismatch bill. Plus approve/reject behavior, 404 on unknown audit_ref, 409 on AutoResolve, and the parent_bill_external_ref linkage carrying before/after payloads.

- [scripts/demo.py](scripts/demo.py) + [scripts/demo_bills.json](scripts/demo_bills.json) — the canonical-bill demonstration harness (see Demo harness below).
- [WALKTHROUGH.md](WALKTHROUGH.md) — case-by-case narrative for the same six bills, suitable for portfolio readers.
- [src/util/logging.py](src/util/logging.py) + [src/routes/status.py](src/routes/status.py) + [tests/test_status.py](tests/test_status.py) — structured-logging utility (stdlib JSON formatter, `StageTimer` context manager, `get_logger`, `log_with_context`) plus the `GET /status` operational endpoint. See Observability below.

What does **not** yet exist (deferred to scale-to-production):

- `POST /batches` route (XLSX batch handler) + batch summary report — deferred; the scale doc covers queue-based fan-out and per-row idempotency properly. The single-row pipeline already exercises every architectural concern the batch path would (fan-out at the route layer is the only addition).
- Sample scenarios in `samples/scenarios.md` — superseded by [WALKTHROUGH.md](WALKTHROUGH.md) and [scripts/demo_bills.json](scripts/demo_bills.json).

Phase 4 (walkthrough dry-run) is next; see [TASKS.md](TASKS.md).

### Models

The pydantic models are the contracts every downstream stage and the audit log consume. Foreign-key fields are typed as `int` (SQLite rowids); relational integrity lives in the DB layer.

- [src/models/entities.py](src/models/entities.py) — Measurabl-aligned hierarchy: `Portfolio`, `Site`, `Account`, `Meter`, `Reading`, plus the enums `Region`, `AccountType`, `Unit`, `MeterType`, `LandlordOrTenant`. Currency is validated to ISO 4217 shape; unit-of-measure to the canonical set (kWh, therms, MMBtu, m3, ccf, gallons, HCF).
- [src/models/bill.py](src/models/bill.py) — pipeline-stage artifacts modeled by inheritance: `RawBillInput` → `NormalizedBill` → `ReconciledBill` → `ValidatedBill`. Each stage adds fields without replacing prior ones. Plus the `SourceMode` enum.
- [src/models/quality.py](src/models/quality.py) — `QualityFlag` and `TriageDecision`, plus the enums `Severity`, `FlagType`, `RoutingKey`, `TriageRoute`.
- [src/models/audit.py](src/models/audit.py) — `AuditEntry`, the full lineage record persisted per bill.
- [src/models/drafter.py](src/models/drafter.py) — `DrafterOutput` plus the enums `ProposedAction` and `EmailRecipientType`. The output contract for the Resolution Drafter Service; fields cover `proposed_action`, `proposed_correction` (machine-applicable partial override, empty when human input is required first), the drafted email triple (`subject`, `body`, `recipient_type`), `basis_note`, and `confidence_note` (required — uses the literal "no uncertainty noted" when none).
- `AuditEntry` (extended this prompt) — gains `parent_bill_external_ref: Optional[str]` linking follow-up entries (approval/rejection) back to the original triaged entry, and `drafter_output: Optional[DrafterOutput]` so the drafter's proposal is preserved on the audit row independently of the TriageDecision. `SourceMode` (extended this prompt) gains `DRAFTER_APPROVED`, the source mode written on readings that landed via the approval flow. `TriageDecision` (extended this prompt) now carries `drafter_output: Optional[DrafterOutput]` in place of the previous loose `drafted_resolution: dict`. `RoutingKey` (extended this prompt) gains `DRAFTER_FAILURE`; `FlagType` gains a matching `DRAFTER_FAILURE` so the drafter-exception flag attached on degradation has a typed home.

### Persistence

Two SQLite-backed stores share one DB file. Stdlib `sqlite3` only, no ORM (see ADR-007).

- [src/db/schema.sql](src/db/schema.sql) — DDL for `sites`, `accounts`, `meters`, `readings`, `audit_entries`. Dates and datetimes are ISO 8601 TEXT; booleans are INTEGER 0/1. All CREATE statements use `IF NOT EXISTS` so the schema is idempotent. Indexes: `readings(meter_id, period_end)` for reconciliation lookups, plus `batch_id` and `bill_external_ref` on `audit_entries`. Five columns are `NOT NULL` to mirror pydantic required-ness per DESIGN.md §4 "Persistence contract" (`sites.region`, `sites.portfolio_id`, `meters.landlord_or_tenant`, `readings.currency`, `audit_entries.bill_external_ref`); tightening constraints is not re-run-safe, so re-seeding requires deleting `prototype.db` first.
- [src/db/store.py](src/db/store.py) — `MeterHistoryStore` (sites/accounts/meters/readings, plus the three-key `find_meter` reconciliation lookup, `get_prior_readings`, and `get_account` for fetching a meter's parent account) and `AuditLogStore` (record / query by bill_external_ref / query by batch_id). Each store owns its own connection with `PRAGMA foreign_keys = ON`; writes commit explicitly. Pydantic models in and out; SQL stays inside this module. The AuditEntry payload round-trips through a single `payload_json` column with a few denormalized columns alongside for query speed. `add_reading` takes `source_mode` as a keyword-required argument with no default per DESIGN.md §4 "add_reading contract" (pipeline writes pass it from the `RawBillInput`; fixture seeding passes `"FIXTURE"` explicitly); `ingested_at` defaults to `datetime.now(UTC)`.

### Fixtures

Hand-written seed data lives in code, not JSON files — single source of truth, visible in one place. Counts: 3 sites, 5 accounts, 10 meters, 39 readings.

- [src/db/fixtures.py](src/db/fixtures.py) — `seed_fixtures(store: MeterHistoryStore) -> dict[str, int]` populates the DB. Three sites (Liberty Tower / US, Pacific Plaza / US, Thames Court / EU); five accounts spanning CONNECT, BILL_UPLOAD, and MANUAL source modes plus a generation_account=True on Thames Court for the solar case; ten meters covering electric (kWh), gas (therms), water (HCF), and the solar-export generation case (kWh) — plus two demo-specific meters: an **inactive** ConEd meter (`MSR.(ConEd)(LT-ELEC-001):(OLD-M0)`, `active=False`, single 2024-06 reading) for the INACTIVE_METER canonical case, and an **unknown-provider** gas meter (`MSR.(GreenfieldCoop)(LT-GAS-002):(M2)`, four monthly readings, provider not in the reference library) for the unknown-provider canonical case. 39 readings total — four recent months on each of the eight baseline meters, two additional older readings on the Liberty Tower main electric meter to seed the Phase 2 gap-detection scenario, one old reading on the inactive meter, and four monthly readings on the unknown-provider meter. Realistic provider names (ConEd, National Grid, PG&E, EBMUD, Octopus Energy), synthetic account numbers. The `_MeterSpec` dataclass carries an `active: bool = True` field so meters that need `active=False` opt in by spec.
- [src/db/seed.py](src/db/seed.py) — CLI: `python -m src.db.seed [--db-path ./prototype.db] [--reset]`. Idempotent — exits without writing if any sites row already exists. `--reset` deletes the DB file first; used by the demo harness to guarantee a known starting state.

### Reference layer

In-memory, no DB and no FastAPI deps. Downstream services (normalization, validation) import the constants and helpers directly.

- [src/services/reference.py](src/services/reference.py) — `Provider` and `RegionalRules` pydantic models plus the `ReferenceRegion` enum (`US-East`, `US-West`, `EU`). DESIGN.md §4 now spec-anchors the three-region granularity, the two-region coarser tag on `Site`, and the EU→GBP currency default. Module-level constants: `PROVIDERS` (10 entries: ConEd, National Grid, Duke Energy, PG&E, SoCal Edison, Xcel Energy, Pacific Gas, British Gas, Thames Water, EDF Energy), `_CONVERSIONS` (direct-pair table covering energy/gas-energy cross-fuel and pure-volume conversions with source-cited factors), and `REGIONAL_RULES`. Functions: `canonicalize_provider` (case-insensitive, whitespace-normalized, alias-aware), `is_known_provider`, `convert_unit` (direct lookup, falls back to reciprocal of the reverse pair, raises `ValueError` on incompatible units), `get_regional_rules`. Provider lookup is backed by a precomputed normalized index built at import time.

### HTTP surface

FastAPI app, no middleware / auth / CORS. Run locally with `uvicorn src.main:app --reload`.

- [src/main.py](src/main.py) — instantiates `FastAPI(title="Utility Bill Pipeline", version="0.3.0", lifespan=_lifespan)`, mounts the bills router, exposes `GET /health` returning `{"status": "ok", "version": "0.3.0"}`. The lifespan handler reads `ANTHROPIC_API_KEY` from the environment at startup (never from code) and installs a `DrafterService` via `set_drafter`; when the key is unset the app still boots and a warning is logged.
- [src/routes/bills.py](src/routes/bills.py) — three endpoints. `POST /bills` runs the full pipeline ingest → normalize → reconcile → validate → triage, records an AuditEntry, returns `{"audit_ref", "raw_input", "normalized", "reconciled", "validated", "triage", "pipeline_status": "triaged"}` where `triage` includes `drafter_output` when present. `POST /bills/{audit_ref}/approve` looks up the original entry, applies `drafter_output.proposed_correction` to a copy of `raw_payload` (validated against the known reading-level field set), constructs a Reading, persists it with `source_mode=DRAFTER_APPROVED`, and writes a follow-up audit entry linked via `parent_bill_external_ref` carrying both the original and corrected payloads. Returns `{"reading_id", "audit_ref"}`. 404 on unknown ref, 409 on non-DraftForHumanReview routes, 422 when `drafter_output` is missing or the correction names unknown fields or the matched_meter snapshot is absent. `POST /bills/{audit_ref}/reject` writes a rejection follow-up audit entry with an optional `rejection_reason` body field; no Reading is written. Approval does NOT re-run validation on the corrected bill — see ADR-012.
- [src/routes/dependencies.py](src/routes/dependencies.py) — three FastAPI generator dependencies. `get_store()` and `get_audit_store()` each open a per-request connection against `$DB_PATH` (default `./prototype.db`); both close on teardown. `get_drafter()` returns the singleton `DrafterService | None` installed by `set_drafter()` at app startup. Tests override all three via `app.dependency_overrides[...]` to point at tmp-path DBs and a `FakeAnthropicClient`-backed drafter.
- [src/services/ingestion.py](src/services/ingestion.py) — `ingest_json_row(payload: dict) -> RawBillInput`. Validates presence of the seven required fields (`period_start`, `period_end`, `usage`, `usage_units`, `meter_id_string`, `account_number`, `site_name`) and constructs a `RawBillInput` with `source_mode=JSON_ROW` and `batch_id=None`. Field parsing and canonicalization belong to normalization, not here. Payload is shallow-copied so caller mutations don't reach the artifact.
- [src/services/normalization.py](src/services/normalization.py) — `normalize(raw_input: RawBillInput) -> NormalizedBill`. Parses ISO dates, canonicalizes the unit string case-insensitively against the `Unit` enum, extracts the provider alias from the `MSR.(provider)(account):(meter)` convention and looks it up in the reference library, ISO-4217-shape-checks the currency (defaults to the regional currency when absent if the provider is known), and emits structural signals under the categories DESIGN.md §4 names: `field_type_valid`, `value_in_range`, `provider_known`, `provider_alias_parsed`, `unit_known`, `cross_field_agreement`. Per ADR-003 / ADR-008 every leaf is a plain boolean; inapplicable per-field checks are omitted from their sub-dict, inapplicable cross-field checks default to `False`. The service never raises on bad data — failures are recorded as `False` signals. Module-level constants pin the plausible-range thresholds (`_BILLING_PERIOD_MIN_DAYS=25`, `_BILLING_PERIOD_MAX_DAYS=35`).
- [src/services/reconciliation.py](src/services/reconciliation.py) — `reconcile(normalized: NormalizedBill, store: MeterHistoryStore, *, prior_readings_limit: int = 12) -> ReconciledBill`. Resolves the three-key triple (`meter_id_string`, `account_number`, `site_name`) against the store; on hit, fetches the last N prior readings and the parent account (via `store.get_account`) and computes `prior_context = {prior_period_end, count_of_prior_readings}`; on miss, returns a `ReconciledBill` with `matched_meter=None`, `matched_account=None`, `prior_readings=[]`, and zeroed prior_context. Never raises on miss — the "no match" case is a valid pipeline outcome that triggers `meter_unassigned` escalation downstream. Stateless beyond the injected store; does not open connections itself. The default 12 is `DEFAULT_PRIOR_READINGS_LIMIT` at module scope.
- [src/services/validation.py](src/services/validation.py) — `validate(reconciled: ReconciledBill) -> ValidatedBill`. Runs schema checks (period_start strictly before period_end → `FORMAT_INVALID`), structural checks against `matched_meter` (`UNIT_MISMATCH`, `CURRENCY_MISMATCH`, `INACTIVE_METER`), structural checks against `matched_account` (`GENERATION_MISMATCH` for `energy_exported > 0` on a non-generation account), and the two domain heuristics with prior context (`GAP`: ≤2 days no flag / 2–7 days MEDIUM / >7 days HIGH; `OVERLAP`: any prior-period intersection HIGH). When `matched_meter` is None, emits exactly one HIGH `METER_UNASSIGNED` flag (plus any schema-level format issues); all matched-meter / matched-account / prior-context checks short-circuit naturally. Never raises. Thresholds (`_GAP_MEDIUM_DAYS=2`, `_GAP_HIGH_DAYS=7`) are module-level constants. The `name_mismatch` check from DESIGN.md §4 is intentionally omitted — strict three-key `find_meter` makes a site-name disagreement an unmatched-meter case, so the check is unreachable; see DECISIONS.md "Spec gaps observed".

### Triage

- [src/services/triage.py](src/services/triage.py) — `TriageService(drafter: DrafterService | None = None)` with a single public method `triage(validated: ValidatedBill) -> TriageDecision`. Routes per DESIGN.md §4: unmatched meter → Escalate(METER_UNASSIGNED); any HIGH flag → Escalate (routing key picked from the first HIGH via `_HIGH_FLAG_TO_ROUTING_KEY`, fallback UNCATEGORIZED) UNLESS every HIGH flag is in `_FIXABLE_HIGH_FLAG_TYPES = {UNIT_MISMATCH}` in which case → DraftForHumanReview; ≥3 MEDIUM → Escalate(UNCATEGORIZED); 2 MEDIUM → DraftForHumanReview; else → AutoResolve. On the DraftForHumanReview route, calls `drafter.draft(validated, meter, prior_readings)` if a drafter is attached; any exception (notably `DrafterParseError`) is caught, the route degrades to Escalate(DRAFTER_FAILURE), and a `FlagType.DRAFTER_FAILURE` flag carrying the exception type and message is appended to `validated.flags` (mutation of the pipeline's in-memory artifact, so the audit log preserves the failure mode). When `drafter is None` on a draft route, returns `drafter_output=None` and emits a logger warning — a test-friendly mode, not a production one.

### Observability

- [src/util/logging.py](src/util/logging.py) — stdlib `logging` + a small `JsonFormatter` that emits one JSON object per log line to stdout. `configure_logging()` is idempotent and is called from `src/main.py`'s lifespan handler (and at module load) so dev sessions and uvicorn-reload see structured output from the first request. `get_logger(service_name)` returns a logger that tags every record with `service=<name>` via a filter. `log_with_context(logger, level, message, **context)` lifts kwargs into the JSON body as siblings of `message` (the structured-logging idiom). `StageTimer` is a context manager that emits `"started"` / `"completed"` log lines with `stage`, any caller-passed kwargs (e.g. `bill_ref`), and `duration_ms`; `.set(...)` lets the inside of the block contribute exit-context fields. On exception inside the block the exit line is promoted to ERROR with `outcome="error"` and `error_type` and the exception then propagates.
- Pipeline-stage logging lives in [src/routes/bills.py](src/routes/bills.py): each `POST /bills` wraps normalize, reconcile, validate, triage, and (on the approval endpoint) the approval flow with a `StageTimer`, passing `bill_ref=<audit_ref>` so a downstream log consumer can pivot on a single bill. Services themselves stay pure — wrapping at the call site keeps the pipeline functions stateless and free of logger injection. The drafter additionally logs the Anthropic model and the `response.usage` token counts (`input_tokens`, `output_tokens`, and any cache-related fields) inside `_log_api_response`, attached to the `drafter` service-tag.
- [src/routes/status.py](src/routes/status.py) — `GET /status`. Always 200, read-only, no DB writes triggered. Returns `{service_name, version, db_state: {open, readings_count, audit_count, last_write_at}, audit_counts_24h: {route: count}, pending_drafted: int, anthropic_api_key_set: bool}`. Pending drafted = `DraftForHumanReview` audit entries whose `drafter_output` is set AND `parent_bill_external_ref` is None (so follow-up entries themselves are not counted) AND no other entry has `parent_bill_external_ref` pointing at them. Distinct from `GET /health` (defined in main.py) which stays as the simple liveness probe.
- AuditLogStore (extended this prompt) gained `count()`, `last_write_at()`, `counts_by_route_since(cutoff)`, and `count_pending_drafted()`. MeterHistoryStore (extended this prompt) gained `readings_count()`. All read-only.
- [tests/test_status.py](tests/test_status.py) — 4 tests: contract (200 + expected keys), zero-counts-on-empty-DB, 24-hour-cutoff filtering, and pending-drafted exclusion of approved/rejected follow-ups (plus auto-resolved and no-drafter-output cases).

### Demo harness

Terminal-based demonstration runner that walks six canonical bills through the live API. Runs against the real Anthropic API on DraftForHumanReview cases; the demo harness itself does not call the API directly — it POSTs to the running uvicorn process and the in-app DrafterService is what calls Anthropic.

- [scripts/demo_bills.json](scripts/demo_bills.json) — six curated bills as JSON (data, not code, so other harnesses can re-use the set). Each entry: `{label, narrative, bill}`. Cases: (1) baseline clean → AutoResolve; (2) `ccf` on a kWh meter → DraftForHumanReview (UNIT_MISMATCH); (3) gap on Liberty M2 (~62 days) → Escalate(UNCATEGORIZED — see spec gap on `gap` routing key in DECISIONS.md); (4) overlap on Pacific Plaza M1 → Escalate(OVERLAP); (5) unknown provider + unit mismatch on the GreenfieldCoop gas meter → DraftForHumanReview (UNIT_MISMATCH, drafter sees `provider_known=False` in context); (6) bill against the inactive ConEd meter → Escalate(INACTIVE_METER).
- [scripts/demo.py](scripts/demo.py) — `python scripts/demo.py {--auto-approve | --interactive}` (mutually exclusive, exactly one required). Resets the DB via `python -m src.db.seed --reset` at the start, checks `/health` (clear error and abort if uvicorn isn't running), POSTs each bill to `/bills`, narrates the route / flags / drafter output (`basis_note`, email subject, confidence_note, body — pretty-printed, not raw JSON), and acts on DraftForHumanReview cases: auto-approve hits `/bills/{audit_ref}/approve` automatically; interactive prompts `[A]pprove / [R]eject / [S]kip` with Enter defaulting to Approve. Closes with a summary table (number, label, route, flag count, outcome, audit_ref prefix). Base URL is a module-level `BASE_URL = "http://localhost:8000"` constant. Reads `ANTHROPIC_API_KEY` indirectly — only the uvicorn process needs it set.
- [WALKTHROUGH.md](WALKTHROUGH.md) — narrative companion for the same six cases. Captured (illustrative) drafter outputs for cases 2 and 5; structured fields are deterministic, natural-language fields vary between runs. Closes with a summary table and a "What this demo is not" paragraph pointing readers at the scale-to-production doc.

### Drafter

Wired into triage on the DraftForHumanReview route. Anthropic client built once at FastAPI startup, injected via `get_drafter`.

- [src/services/drafter.py](src/services/drafter.py) — `DrafterService(client, model="claude-sonnet-4-6", system_prompt_path, max_tokens=1024)`. Single public method `draft(validated_bill, meter, prior_readings) -> DrafterOutput`. Forces structured output via Anthropic's tool-use mechanism (see ADR-010): tool name `draft_resolution`, `input_schema` derived from `DrafterOutput.model_json_schema()`, `tool_choice={"type": "tool", "name": "draft_resolution"}`. Parses the `tool_use` block on response; any parse failure (no tool_use, non-dict input, pydantic validation error) raises `DrafterParseError` with the raw response attached — fail-loud per ADR-011, no retries. Module-level helper `build_drafter_user_message(bill, meter, history)` renders the structured user message with section headers (Incoming bill / Matched meter / Quality flags / Recent readings).
- [src/prompts/drafter_system.md](src/prompts/drafter_system.md) — the drafter's system prompt as a standalone markdown file (see ADR-009 for the in-file rationale). Frames the model as a drafting assistant, names the human as the gate, instructs the model to leave `proposed_correction` empty when it cannot safely self-correct, includes two short example outputs (unit-mismatch and meter-confusion), and ends with the literal "Always call the draft_resolution tool. Never respond outside it." Read at `DrafterService` construction time.
- [tests/fakes.py](tests/fakes.py) — `FakeAnthropicClient` plus `FakeMessage` / `FakeContentBlock` dataclasses. Mirrors the real SDK's `messages.create(...) -> Message` shape: `Message.content` is a list of blocks where a tool_use block exposes `.type == "tool_use"`, `.name`, and `.input` (dict). Configure with `set_next_response(...)`; the client records the last call's kwargs on `last_call_kwargs` so tests can assert tool-choice was forced.
- [tests/test_drafter.py](tests/test_drafter.py) — 7 tests across three tiers. Tier 1 (contract): DrafterOutput round-trip; `model_json_schema()` exposes the expected properties. Tier 2 (behavior): canned tool_use → parsed DrafterOutput; invalid enum in tool input → DrafterParseError; text-only response (no tool_use) → DrafterParseError; `build_drafter_user_message` includes the meter's locked unit, the meter_id_string, and the specific flag. Tier 4 (integration): one `@pytest.mark.integration` test against the real Anthropic API, skipped when `ANTHROPIC_API_KEY` is not set. Default `pytest` runs deselect the integration mark via `pyproject.toml` `addopts`.

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
      drafter.py
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
      normalization.py
      reconciliation.py
      validation.py
      drafter.py
      triage.py
    routes/
      __init__.py
      bills.py
      dependencies.py
      status.py
    util/
      __init__.py
      logging.py
    prompts/
      drafter_system.md
  tests/
    __init__.py
    fakes.py
    test_models.py
    test_store.py
    test_fixtures.py
    test_reference.py
    test_ingestion.py
    test_normalization.py
    test_reconciliation.py
    test_validation.py
    test_pipeline_e2e.py
    test_routes_bills.py
    test_drafter.py
    test_triage.py
    test_approval.py
    test_status.py
    test_design_sync.py
  scripts/
    check_design_sync.py
    demo.py
    demo_bills.json
  samples/__init__.py
  docs/__init__.py
  WALKTHROUGH.md
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
