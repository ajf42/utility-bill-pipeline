# CLAUDE.md — Project Working Memory

This file is the project's living working memory. It describes **what exists right now**, **what the rules are**, and **how to work in this repo**. Update it whenever a completed task changes anything it describes. When CLAUDE.md is modified in a change, surface the change explicitly in the response so it is visible at a glance.

The authoritative spec is [DESIGN.md](DESIGN.md). When in conflict, DESIGN.md wins and CLAUDE.md is updated.

---

## Project description

A working prototype of an AI-augmented utility bill ingestion and quality assurance pipeline. The system accepts a utility bill row (single or batch), normalizes that data against a reference library, reconciles it against meter history in a real datastore, validates it against a schema and a set of domain heuristics, and decides per-record whether the result auto-resolves, gets a Claude-drafted resolution proposal for human review, or escalates to a routed team queue. Every step is logged to an audit trail. The prototype targets the operational problem set Measurabl runs every day; the goal is not to build a production system but to demonstrate a defensible architectural approach to the problem and the engineering discipline that would scale it across an offshore team.

The walkthrough audience is Matt Richardson, who owns Measurabl's back-office function. He is a process-and-operations leader with high systems intuition. He cannot trust black boxes. Every decision must be explicit and logged. The methodology artifacts (this file, [DECISIONS.md](DECISIONS.md), [TASKS.md](TASKS.md)) and the scale-to-production companion doc are the differentiators, not the code.

## Current state

Phase 1 — Foundation, bootstrap step.

What exists:

- Repo skeleton, `.gitignore`, `pyproject.toml` (Python 3.11+, FastAPI, pydantic v2, Anthropic, pandas, openpyxl, pytest, httpx)
- [DESIGN.md](DESIGN.md) — authoritative spec
- [README.md](README.md) — skeleton with architecture diagram placeholder, quick start, walkthrough placeholder, status
- [CLAUDE.md](CLAUDE.md) — this file
- [DECISIONS.md](DECISIONS.md) — initialized with ADR-001 through ADR-005
- [TASKS.md](TASKS.md) — Phase 2–4 backlog
- Empty source tree with `__init__.py` placeholders under `src/` and subpackages

What does **not** yet exist:

- Any pydantic models
- Any SQLite schema, store implementations, or fixtures
- Any service code (normalization, reconciliation, validation, triage, drafter, audit, output)
- Any route handlers (`/bills`, `/batches`)
- Any tests
- Any sample bills or scenarios

The next unit of work is the rest of Phase 1 (data models, SQLite schema, stores, fixtures) per [TASKS.md](TASKS.md) and [DESIGN.md](DESIGN.md) §6 Phase 1.

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
    models/__init__.py
    db/__init__.py
    services/__init__.py
    routes/__init__.py
  tests/__init__.py
  samples/__init__.py
  docs/__init__.py
```

The full target structure (what this skeleton grows into) is enumerated in [DESIGN.md](DESIGN.md) §10.

---

## Mandatory on every change

Verbatim from [DESIGN.md](DESIGN.md) §8.

1. Code runs without errors
2. All tests pass (`pytest`)
3. Inline documentation updated for modified logic
4. README updated if API surface or architecture changed
5. DECISIONS.md updated if the change involved an architectural or engineering decision
6. TASKS.md updated (task moved to done, commit hash recorded)
7. CLAUDE.md updated if the change introduced new files, endpoints, services, or rules
8. Committed under the commit convention, one commit per task

Do not consider a task complete until all eight steps are done.

### CLAUDE.md self-maintenance

CLAUDE.md is updated whenever a completed task changes anything it describes. When CLAUDE.md is modified, surface the change explicitly in the response so it is visible at a glance.

---

## Coding patterns

Verbatim from [DESIGN.md](DESIGN.md) §8.

- Dependency injection via FastAPI's `Depends`. Constructor injection in service classes.
- Thin controllers: route handlers accept the request, call a service, return the result. No business logic in route handlers.
- Services are stateless. State lives in dependency-injected stores.
- Error handling: services raise exceptions; route handlers catch and map to HTTP responses.
- Async wherever I/O happens, especially Anthropic API calls.
- Pydantic models for all DTOs and internal data passing.

### Sample scenarios

Sample scenarios are not isolated rows — they are multi-bill stories that demonstrate stateful behavior. To demonstrate gap detection, two bills on the same meter with a gap. To demonstrate overlap, two bills on the same meter that overlap. Each scenario is documented in `samples/scenarios.md` with meter setup, bills involved, and expected pipeline output.

### Anonymization

If real utility bills are referenced in fixtures, all PII (account numbers, customer names, addresses) is stripped before being committed. The README notes this explicitly.

---

## Commit convention

Verbatim from [DESIGN.md](DESIGN.md) §8.

`<scope>: <imperative description>`

Examples:

- `normalize: add structural quality signals to normalization service`
- `triage: implement three-route decision logic`
- `docs: log decision on structural-only confidence model`

One task per commit. Commits referenced in [TASKS.md](TASKS.md).
