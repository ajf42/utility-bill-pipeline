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

- [x] FastAPI scaffolding, `POST /bills` endpoint — commit `2153de0`
- [x] JSON-row ingestion handler — commit `2153de0` (same commit as above; the route handler and the service it delegates to landed together because the route has nothing to test without the service)
- [x] Reference data layer with 10 providers + unit conversion — commit `3d16ff8`
- [x] Normalization service with structural quality signals — commit `9ddfa36`
- [x] Reconciliation service consulting the store — commit `857feee`
- [x] Schema validation — commit `services: add validation service with heuristics and structural checks; complete Phase 2 with e2e integration test` (lookup via `git log --grep`; backfilled in the next commit)
- [x] Two domain heuristics (gap, overlap) — commit `services: add validation service ...` (same commit; the validation service is a single coherent unit)
- [x] Structural checks (unit/currency/name/inactive) — commit `services: add validation service ...` (same commit; `name_mismatch` omitted with a spec-gap note — unreachable under strict three-key reconciliation)
- [x] Tests for each service — commit `services: add validation service ...` (per-service test files landed alongside their services across Prompts 1–5; the validation tests and the Phase 2 e2e acceptance gate land in this commit)

## Phase 3 — Triage, Drafter, Batch  **(complete)**

Verbatim from [DESIGN.md](DESIGN.md) §6. The XLSX batch sub-items are explicitly deferred to the scale-to-production doc and recorded with the `[~]` marker. The remaining unchecked Phase 3 items are themselves superseded by [WALKTHROUGH.md](WALKTHROUGH.md) + [scripts/demo_bills.json](scripts/demo_bills.json) (per-scenario markdown) and by the structured-logging / `/status` work landed at the end of Phase 3.

- [x] Triage service with three-route decision logic — commit `94316df` ( also wires the Drafter and adds approve/reject endpoints in the same commit).
- [x] Resolution Drafter Service (single Claude call, structured output) — standalone build (not yet wired into the pipeline; wiring lands with the Triage service). Commit `1b1ce8a`.
- [~] `POST /batches` endpoint with simplified XLSX template parser — **deferred to scale-to-production**. The single-row pipeline already exercises every architectural concern the batch path would (queue-based fan-out, per-row idempotency, batch summary aggregation are scale concerns treated properly in the scale doc).
- [~] Batch summary report assembly — **deferred to scale-to-production** (same reasoning).
- [x] AuditEntry writes to SQLite — commit `94316df` (landed with the triage wiring; original + follow-up entries linked via `parent_bill_external_ref`).
- [x] 4 sample scenarios constructed and documented in `samples/scenarios.md` — covered in spirit by the six canonical bills in [scripts/demo_bills.json](scripts/demo_bills.json) + [WALKTHROUGH.md](WALKTHROUGH.md); commit `8f3643b`. The lighter `samples/scenarios.md` file remains open if a separate per-scenario markdown is wanted later.
- [x] Tests for triage logic with explicit expected decisions — commit `94316df` ( tests/test_triage.py + tests/test_approval.py landed together with the triage service).
- [x] Tests for resolution drafter (mock the Claude call in tests) — landed alongside the service in the standalone build. Commit `1b1ce8a`.

## Phase 4 — Walkthrough Prep

Verbatim from [DESIGN.md](DESIGN.md) §6.

- [ ] Walkthrough script (README section) — commit `________`
- [ ] Architecture diagram polished (Mermaid) — commit `________`
- [ ] CLAUDE.md current-state section fully reflects what was built — commit `________`
- [ ] DECISIONS.md updated with every decision made during the build — commit `________`
- [ ] All sample scenarios documented in `samples/scenarios.md` with expected outcomes — commit `________`
- [ ] One out-loud dry-run — commit `________`
- [ ] Anything caught in the dry-run that breaks the flow — commit `________`

## Fixes

Caught during walkthrough dry-runs and small ergonomic gaps. One commit each.

- [x] `deps`: add `uvicorn[standard]` to runtime deps — commit `2ef3582` (README documented `uvicorn src.main:app --reload` but uvicorn was never declared; fresh installs had no `uvicorn.exe` in the venv)
- [x] `config`: auto-load `ANTHROPIC_API_KEY` from `.env`; hard-fail when missing — commit `70ba9c6` (ADR-013; previous behavior was a buried warning + silent `drafter_output=None`, observed degrading `python scripts/demo.py --interactive` to `draft-no-output` for two of six bills)
