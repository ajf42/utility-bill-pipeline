# TASKS.md — Backlog

The backlog for Phases 1 (remaining), 2, 3, and 4 of the build. Phase 2/3/4 sub-items are transcribed **verbatim** from [DESIGN.md](DESIGN.md) §6; if a Phase 2/3/4 item below no longer matches §6 word-for-word, DESIGN.md is the source of truth and this file must be re-synced.

One task per commit. When a task is completed, check it off and record the short commit hash next to it. The commit convention is `<scope>: <imperative description>` (see [CLAUDE.md](CLAUDE.md) and [DESIGN.md](DESIGN.md) §8).

Mandatory-on-every-change rules (see [CLAUDE.md](CLAUDE.md) and [DESIGN.md](DESIGN.md) §8) gate every checkmark in this file — do not check a task until all eight steps are done.

---

## Phase 1 — Foundation (remaining)

Phase 1 sub-items from [DESIGN.md](DESIGN.md) §6, with done items checked.

- [x] Repo created, .gitignore, pyproject.toml — commit `e602eeb`
- [x] CLAUDE.md v1 written — commit `e602eeb`
- [x] DECISIONS.md initialized with: stack choices, SQLite-not-Postgres, structural-only confidence, three-route triage, entity model alignment to Measurabl — commit `e602eeb`
- [x] TASKS.md initialized with the full Phase 2–4 backlog — commit `e602eeb`
- [x] README.md skeleton with placeholder for Mermaid diagram — commit `e602eeb`
- [x] Mandatory-on-every-change rules documented in CLAUDE.md — commit `e602eeb`
- [x] pydantic data models (`RawBillInput`, `NormalizedBill`, `ReconciledBill`, `ValidatedBill`, `TriageDecision`, `AuditEntry`, `QualityFlag`, `RoutingKey`) — commit `ad86a2f`
- [x] SQLite schema (DDL file) — commit `e6f2527`
- [x] Store implementations (MeterHistoryStore, AuditLogStore) — commit `e6f2527`
- [x] Fixture data: 3 sites, 5 accounts, 8 meters, 30+ historical readings — commit `0c7b548`

## Phase 2 — Single-Row Pipeline

Verbatim from [DESIGN.md](DESIGN.md) §6.

- [ ] FastAPI scaffolding, `POST /bills` endpoint — commit `________`
- [ ] JSON-row ingestion handler — commit `________`
- [x] Reference data layer with 10 providers + unit conversion — commit `services: add reference data layer with providers, units, regional rules` (lookup via `git log --grep`; this commit's own hash cannot be embedded into itself — will be backfilled in the next commit)
- [ ] Normalization service with structural quality signals — commit `________`
- [ ] Reconciliation service consulting the store — commit `________`
- [ ] Schema validation — commit `________`
- [ ] Two domain heuristics (gap, overlap) — commit `________`
- [ ] Structural checks (unit/currency/name/inactive) — commit `________`
- [ ] Tests for each service — commit `________`

## Phase 3 — Triage, Drafter, Batch

Verbatim from [DESIGN.md](DESIGN.md) §6.

- [ ] Triage service with three-route decision logic — commit `________`
- [ ] Resolution Drafter Service (single Claude call, structured output) — commit `________`
- [ ] `POST /batches` endpoint with simplified XLSX template parser — commit `________`
- [ ] Batch summary report assembly — commit `________`
- [ ] AuditEntry writes to SQLite — commit `________`
- [ ] 4 sample scenarios constructed and documented in `samples/scenarios.md` — commit `________`
- [ ] Tests for triage logic with explicit expected decisions — commit `________`
- [ ] Tests for resolution drafter (mock the Claude call in tests) — commit `________`

## Phase 4 — Walkthrough Prep

Verbatim from [DESIGN.md](DESIGN.md) §6.

- [ ] Walkthrough script (README section) — commit `________`
- [ ] Architecture diagram polished (Mermaid) — commit `________`
- [ ] CLAUDE.md current-state section fully reflects what was built — commit `________`
- [ ] DECISIONS.md updated with every decision made during the build — commit `________`
- [ ] All sample scenarios documented in `samples/scenarios.md` with expected outcomes — commit `________`
- [ ] One out-loud dry-run — commit `________`
- [ ] Anything caught in the dry-run that breaks the flow — commit `________`
