# TASKS.md — Backlog

The backlog for Phases 2–4 of the build. Phase 1 (Foundation) covers the repo skeleton, methodology artifacts, data models, schema, stores, and fixtures — its bootstrap commit is the seed of this file but is not enumerated here as a checklist item.

One task per commit. When a task is completed, check it off and record the short commit hash next to it. The commit convention is `<scope>: <imperative description>` (see [CLAUDE.md](CLAUDE.md) and [DESIGN.md](DESIGN.md) §8).

Mandatory-on-every-change rules (see [CLAUDE.md](CLAUDE.md)) gate every checkmark in this file — do not check a task until all eight steps are done.

---

## Phase 1 — Foundation (remaining)

- [ ] Define pydantic data models — `RawBillInput`, `NormalizedBill`, `ReconciledBill`, `ValidatedBill`, `TriageDecision`, `AuditEntry`, `QualityFlag`, `RoutingKey` — commit `________`
- [ ] Write SQLite DDL in `src/db/schema.sql` for `sites`, `accounts`, `meters`, `readings`, `audit_entries` — commit `________`
- [ ] Implement `MeterHistoryStore` and `AuditLogStore` in `src/db/store.py` — commit `________`
- [ ] Seed fixture data: 3 sites, 5 accounts, 8 meters, 30+ historical readings — commit `________`

## Phase 2 — Single-Row Pipeline

- [ ] FastAPI scaffolding and app entry in `src/main.py` — commit `________`
- [ ] `POST /bills` route handler in `src/routes/bills.py` — commit `________`
- [ ] JSON-row ingestion handler in `src/services/ingestion.py` — commit `________`
- [ ] Reference data layer in `src/services/reference.py`: 10 providers, aliases, regions, typical units, 1–2 quirks each — commit `________`
- [ ] Unit conversion table (kWh, therms, MMBtu, m³, ccf, gallons, HCF) and regional ruleset (US, EU) — commit `________`
- [ ] Normalization service in `src/services/normalization.py` with structural quality signals — commit `________`
- [ ] Reconciliation service in `src/services/reconciliation.py` consulting `MeterHistoryStore` — commit `________`
- [ ] Schema validation via pydantic in `src/services/validation.py` — commit `________`
- [ ] Gap heuristic (medium 2–7 days, high >7 days) — commit `________`
- [ ] Overlap heuristic (any overlap = high) — commit `________`
- [ ] Structural checks: unit mismatch, currency mismatch, building name mismatch, inactive-meter reading, generation account mismatch — commit `________`
- [ ] Tests for normalization (`tests/test_normalization.py`) — commit `________`
- [ ] Tests for reconciliation (`tests/test_reconciliation.py`) — commit `________`
- [ ] Tests for validation including gap and overlap (`tests/test_validation.py`) — commit `________`

## Phase 3 — Triage, Drafter, Batch

- [ ] Triage service in `src/services/triage.py` with three-route decision logic and routing keys — commit `________`
- [ ] Triage thresholds configurable and logged in DECISIONS.md — commit `________`
- [ ] Resolution Drafter Service in `src/services/resolution_drafter.py` (single Claude call, structured output: proposed action, drafted email, basis note) — commit `________`
- [ ] `POST /batches` route handler in `src/routes/batches.py` — commit `________`
- [ ] Simplified XLSX template parser (8 required columns) — commit `________`
- [ ] Batch summary report assembly (per-row outcomes + aggregate counts) — commit `________`
- [ ] AuditEntry writes to SQLite via `src/services/audit_log.py` with `batch_id` linkage — commit `________`
- [ ] Output writer in `src/services/output_writer.py` emitting readings-table write payload (JSON per bill) — commit `________`
- [ ] Sample scenario 1 — clean bill, AutoResolve path — commit `________`
- [ ] Sample scenario 2 — unit mismatch, DraftForHumanReview path (demo highlight) — commit `________`
- [ ] Sample scenario 3 — overlap, Escalate with `overlap` routing key — commit `________`
- [ ] Sample scenario 4 — clean batch of N rows, mixed outcomes — commit `________`
- [ ] `samples/scenarios.md` documenting all four scenarios with meter setup, bills, and expected outputs — commit `________`
- [ ] Tests for triage with explicit expected decisions (`tests/test_triage.py`) — commit `________`
- [ ] Tests for resolution drafter with Claude call mocked (`tests/test_resolution_drafter.py`) — commit `________`
- [ ] Tests for audit log (`tests/test_audit_log.py`) — commit `________`
- [ ] Tests for batch endpoint (`tests/test_batch.py`) — commit `________`

## Phase 4 — Walkthrough Prep

- [ ] Walkthrough script written as a README section — commit `________`
- [ ] Architecture diagram polished (Mermaid) in README — commit `________`
- [ ] `docs/architecture.md` with per-stage narrative — commit `________`
- [ ] CLAUDE.md current-state section fully reflects what was built — commit `________`
- [ ] DECISIONS.md updated with every decision made during the build — commit `________`
- [ ] All sample scenarios documented in `samples/scenarios.md` with expected outcomes — commit `________`
- [ ] One out-loud dry-run completed — commit `________`
- [ ] Anything caught in the dry-run that breaks the flow — fix and recommit — commit `________`
