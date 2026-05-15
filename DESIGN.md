# Utility Bill Ingestion & Quality Assurance Pipeline — Internal Design Document

**Status:** Draft v3 (internal working spec, 20-hour build)
**Target conversation:** May 26, 2026 (~30–45 min technical walkthrough with Matt Richardson, Measurabl)
**Build window:** May 13 – May 25 (12 calendar days, ~20 focused hours)
**Author:** Andrew Fitzpatrick

---

## 0. About This Document and Its Companions

Three documents live around this project. Knowing which is which keeps each one focused:

1. **This document — internal working spec.** Engineering-deep. Decisions, tradeoffs, data model, exact build plan. Written for Andrew and for a fresh Claude instance handed the repo. Iterates throughout the build.
2. **Scale-to-production doc** (drafted alongside this one). Takes the prototype and articulates what it becomes at tens of thousands of bills per month, distributed teams, real persistence, real observability, real SLAs. The artifact that signals enterprise-grade thinking to Matt. Every feature cut from the prototype gets named here with real architectural treatment.
3. **Forward-facing design doc** (drafted after this one stabilizes). Problem-led narrative for Matt or for portfolio publication. Lighter on internals, heavier on architectural reasoning and operational framing.

Every design decision in this document carries an implicit tag of **prototype**, **scale-to-production**, or **both**. When a tag is not obvious from context, it is called out explicitly.

---

## 1. What This Is

A working prototype of an AI-augmented utility bill ingestion and quality assurance pipeline. The system accepts a utility bill row (single or batch), normalizes that data against a reference library, reconciles it against meter history in a real datastore, validates it against a schema and a set of domain heuristics, and decides per-record whether the result auto-resolves, gets a Claude-drafted resolution proposal for human review, or escalates to a routed team queue. Every step is logged to an audit trail.

The prototype targets the operational problem set Measurabl runs every day: messy bill data flowing in from many sources, currently handled with significant manual triage. The goal is not to build a production system. The goal is to demonstrate a defensible architectural approach to that problem and to show the engineering discipline that would scale it across an offshore team.

### What this document is for

Two readers. First, Andrew, as a working specification he iterates on. Second, a fresh Claude instance (Claude Code) that picks up the build from here. The document must be self-sufficient for both — enough technical context that no chat history is required to execute, and enough narrative context that decisions made along the way remain grounded in the problem they were solving.

### Success criteria for May 26

The walkthrough succeeds if Matt comes away with three things: a clear understanding of how Andrew decomposes a data problem, evidence that Andrew can produce auditable engineering artifacts (not just code), and one or two concrete points where Matt can imagine handing real work over. The working prototype is the proof. The methodology artifacts and the scale-to-production doc are the differentiators.

### What was deliberately cut from a larger scope, and why it matters

This is a 20-hour build. A larger scope was designed and then trimmed honestly. The cuts are: PDF extraction mode (JSON-row and XLSX cover the same architectural pattern), an AutoEstimate triage route (high logic complexity, becomes future work), a Data Completeness Check XLSX export (the readings payload covers the demo value), the statistical-anomaly heuristic (gap and overlap suffice to demonstrate stateful detection), and the full production XLSX template parser (a stripped-down version makes the same point). Every cut is named in the scale-to-production doc with the engineering treatment it would receive there. The frame to hold: this prototype is the spine, built with discipline; the cut features are exactly the items where rigorous architectural thinking lives in the companion doc.

---

## 2. Problem Context

### Measurabl's operational reality

Measurabl ingests utility data from buildings in 93 countries on behalf of ~1,000 customers, processing roughly 35,000 utility bills per month. Ingestion happens through several pathways:

- **Connect** — direct sync from utility provider portals using stored credentials. Lowest-touch path. Failures surface as "Connect Integrity Issues" routed in a bi-weekly email to the back-office team.
- **Bill Upload** — PDF invoices uploaded into the platform, parsed and extracted automatically.
- **Manual entry** — readings entered one at a time through the UI by property managers.
- **Bulk upload templates** — XLSX templates emailed to `support@measurabl.com` or uploaded by portfolio members directly. Several template variants exist (Sites/Meters/Readings, Meters/Readings, Readings only, Connect Accounts).
- **Data Collection Assist** — Measurabl's service team chasing down data on behalf of customers.

The back-office team owns the exception layer across all of these: resolving Connect Integrity Issues, handling extraction exceptions, processing bulk templates, reconciling gaps and overlaps, chasing down customers on missing or anomalous data. Five teams. This is the workload Matt now owns. GRESB season makes it worse.

Measurabl already has a customer-facing data quality product (**Data Manager**, inside their Navigate suite) that surfaces outliers, manages portfolios/buildings/meters/spaces, and is the dashboard their teams work from. This prototype is conceptually positioned **upstream** of Data Manager — it is the ingestion-and-triage layer that decides what gets written to the canonical readings table in the first place. It is not a replacement for Data Manager and the design never implies that it is.

### Where the leverage lives

Two operational pain points stand out from public documentation and from Matt's situation:

1. **The manual ingestion long-tail.** Every bill that doesn't come through Connect cleanly is human-touched. The cost grows linearly with portfolio expansion.
2. **The quality-remediation loop.** Gaps, overlaps, intermittent meters, unit mismatches, building-name mismatches, and provider-specific quirks surface as tickets requiring human review and customer outreach.

The prototype targets both.

### Audience: Matt Richardson

Matt is a process-and-operations leader. Sixteen years at Measurabl. Was the company's connective tissue (installs Slack, runs cybersecurity, ensures things work) and recently took ownership of the back-office function. Learns by reading manuals and applying them. Has limited formal data background but high systems intuition. Carries liability. Cannot trust black boxes. Cares more about how a system is reasoned about than about how it looks. Confirmed in prior conversation: he agreed on the need to templatize patterns and was openly curious about AI systems to do that work in place of manual processes.

Design implications:
- Every decision is explicit and logged. Nothing implicit.
- Code is readable over clever.
- Operational concerns (lineage, idempotency, observability, escalation routing) are first-class, not afterthoughts.
- AI is a component, not a feature. The system is glass-box even when an LLM is in the loop.
- The methodology artifacts and the scale-to-production doc are the deliverables Matt will spend the most time on.

---

## 3. Architectural Patterns Inherited

### From supplier-emissions-normalizer

The structural pattern transfers directly: messy input → quality-aware extraction → reference data lookup → reconciliation → validation → triage decision with audit. Kept: reference data lookup, JSON schema output, test coverage discipline. Added: entity model aligned to Measurabl's real hierarchy, meter-history reconciliation, three-way triage with structured escalation routing, audit log with full lineage.

### From FormulationImpactAPI

The methodology pattern transfers directly. This is the higher-leverage port — the engineering operations system the prior project demonstrated:

- **CLAUDE.md** — living working memory describing current state, planned state, and rules. Self-maintaining.
- **DECISIONS.md** — Architecture Decision Record. Every significant choice logged with rationale, alternatives considered, tradeoffs accepted.
- **TASKS.md** — backlog with acceptance criteria, status, and commit references. One task per commit.
- **Mandatory-on-every-change rules** — code runs, tests pass, inline docs updated, README updated, DECISIONS.md updated where relevant, TASKS.md updated, CLAUDE.md updated where relevant, committed under convention, before any task is marked done.

This pattern answers two questions Matt will ask: "how does an offshore team execute against your design without sitting next to you" and "how does anyone audit decisions six months from now without re-explaining the system."

---

## 4. Technical Design

### Stack

| Layer | Technology | Why |
|---|---|---|
| Runtime | Python 3.11+ | Standard for data engineering; broad AI tooling |
| API | FastAPI | Lightweight, async-native, OpenAPI built-in |
| Schema | pydantic v2 | Runtime validation, serialization, data contracts |
| AI | Anthropic SDK (Claude) | Resolution drafting (single, well-scoped use) |
| Tabular | pandas, openpyxl | Reference data, XLSX I/O |
| Persistence | SQLite | Real SQL, real foreign keys, zero infrastructure; production hook = Postgres |
| Tests | pytest | Standard |

### Entity Model

Aligned to Measurabl's actual hierarchy as documented in their help center. The prototype models the full chain; upper levels (Portfolio, Site) are tiny fixture tables that exist to make foreign keys honest.

```
Portfolio
   └── Site (building)
          └── Account (utility account, has Account Type: Connect | Bill Upload | Manual)
                  └── Meter (locked to one unit, has Start/End dates, Active flag)
                          └── Reading (Period Start/End, Usage, Cost, Demand, etc.)
```

Field detail (extracted from Measurabl's published bulk upload templates):

- **Account** — Account Number, Account Type, Generation Account flag
- **Meter** — Unique Meter ID, naming convention `MSR.(provider)(account_number):(meter_number)`, activation Start Date, End Date (nullable), Type, Unit (locked), Currency (locked), Landlord-or-Tenant-paid, Active toggle
- **Reading** — Period Start, Period End, Usage, Usage Units (must match meter unit), Cost (optional), Currency (must match meter), Demand kW (optional), Demand Spend (optional), Energy Exported (optional)

Hard rules encoded in validation:
- Each meter is locked to one unit. Unit mismatch is a high-severity flag.
- Readings are append-only. Corrections require a flagged workflow, not an overwrite.
- Building name on incoming data must match an existing Site. Mismatch is a high-severity flag.
- A reading on an Inactive meter is a high-severity flag.

### High-Level Flow

```
[1] INGEST
    Single row (JSON) OR batch (XLSX)
    Batch path = thin orchestration wrapper that fans out to single-row pipeline
        ↓
[2] NORMALIZE
    Reference lookup: provider canonicalization, unit conversion
    Per-field structural quality signals
    → NormalizedBill
        ↓
[3] RECONCILE
    Match to existing Meter via Meter ID + Account + Site
    Pull meter history from store
    → ReconciledBill (with prior context attached)
        ↓
[4] VALIDATE
    Schema validation (pydantic)
    Domain heuristics (gap, overlap, unit/currency/name/inactive checks)
    → ValidatedBill with quality flags
        ↓
[5] TRIAGE
    Apply flag-severity + structural-quality logic → one of three routes
    → TriageDecision (route + reasoning + drafted resolution where applicable)
        ↓
[6] OUTPUT
    JSON payload representing write to readings table
    AuditEntry persisted to log
```

Every stage produces an artifact that the next stage consumes. Every stage is independently testable. Every artifact is serializable and inspectable.

### Component Detail

#### Ingestion Layer

Two endpoints:

- `POST /bills` — single row. JSON body matching the canonical reading schema. Returns the full pipeline result synchronously.
- `POST /batches` — XLSX upload. Thin orchestrator that parses a simplified template (8 required columns), fans out to the single-row pipeline for each row, and assembles a batch summary report (JSON).

The batch endpoint adds a `batch_id` field to each generated audit entry. Single-row calls leave `batch_id` null. This lets the audit log answer both "what happened to this bill" and "what happened in this upload."

Production hook (documented, not built): in real systems this is where queue-based ingestion sits.

**Cut from prototype, documented in scale-to-production:** PDF ingestion handler (pdfplumber + Claude extraction); full production XLSX template parser with conditional optionality and dropdown validation.

#### Normalization Service

For each incoming row: resolve the provider against the reference library (canonicalize names and aliases), convert units to canonical form if needed, attach structural quality signals to each field.

**On confidence (the key decision in this section).** The system does **not** use any LLM-self-reported confidence. Confidence in extracted/normalized fields comes from **structural quality signals** only:

- Does the value pass type and format validation (date parses, number is numeric, unit is in the known set)?
- Is the value within plausible range for its type (a kWh reading is positive, a billing period is between 25 and 35 days for monthly bills, etc.)?
- Is the provider in the reference library?
- Do unit, currency, and account-type agree across the row?

The structural quality signal is **flag-liberal**: it is better to flag five fields and have three turn out fine than to pass through a real error. Errors hurt Measurabl. The triage layer is what decides what to do with flagged fields. Logged in DECISIONS.md.

**Cut from prototype, documented in scale-to-production:** cross-extraction-agreement confidence model (running extraction twice with different prompts and treating agreement as the confidence signal). The right approach at production scale, but not earned in a 20-hour build.

#### Reference Data Layer

A small in-memory library of utility providers (~10), each with:
- Provider name and aliases (string normalization)
- Region
- Typical units of measure
- 1–2 known quirks (illustrative)

Plus a unit conversion table (kWh, therms, MMBtu, m³, ccf, gallons, HCF) and a regional ruleset (US, EU) for decimal/comma separators and date format.

**Acknowledged simplification.** Real provider quirks are not a flat list — they are a tree of provider → tariff type → rate schedule → bill format. The prototype reference library is illustrative. The scale-to-production doc covers what a tariff-aware reference store looks like and how an AI system might observe manual onboarding decisions to propose new entries over time.

#### MeterHistoryStore

SQLite-backed, file-on-disk for the prototype. Schema:

```
sites (id, name, portfolio_id, region)
accounts (id, account_number, account_type, site_id, generation_account)
meters (id, meter_id_string, account_id, unit, currency, type,
         landlord_or_tenant, active, start_date, end_date)
readings (id, meter_id, period_start, period_end, usage, cost,
           currency, demand_kw, demand_spend, energy_exported,
           ingested_at, source_mode, batch_id)
audit_entries (id, bill_external_ref, batch_id, timestamp,
                triage_decision, payload_json)
```

Consulted by Reconciliation (to fetch prior readings for the same meter for gap/overlap analysis) and updated after Triage on the AutoResolve path.

DECISIONS.md entry: "Prototype uses SQLite because it gives real SQL semantics, real foreign keys, real indexes, and zero infrastructure overhead. Production would use Postgres with read replicas for dashboard joins, a queue-based write pattern, and CDC streams to the downstream consumers."

#### Reconciliation Service

For each incoming bill, after normalization:
1. Resolve the meter: match incoming meter identifier + account number + site name against the meters table.
2. If no match → flag for routing to the `meter_unassigned` sub-route of Escalate.
3. If match → fetch the last N readings (default 12) for that meter, sorted by period.
4. Attach prior context to the bill: prior period end, count of prior readings.

This is where stateful logic begins. The rest of the pipeline operates with reconciled context attached.

#### Validation Layer

Two parts:

1. **Schema validation** — pydantic enforces the `ReconciledBill` shape. Type errors, missing required fields, malformed dates, invalid units fail here.
2. **Domain heuristics** — run after schema passes. Two implemented for the prototype:
   - **Gap detection.** This bill's period start vs. prior bill's period end on the same meter. Gap > 2 days = medium-severity flag; gap > 7 days = high-severity.
   - **Overlap detection.** This bill's period overlaps any existing reading on the same meter. Any overlap = high-severity.

Plus a set of cheap structural checks that produce flags directly: unit mismatch, currency mismatch, building name mismatch, inactive-meter reading, generation account mismatch.

Each flag is a `QualityFlag` with a type, severity (low/medium/high), description, and a recommended action class.

**Cut from prototype, documented in scale-to-production:** statistical anomaly detection (z-score against rolling 12-month average); intermittent meter signature detection.

#### Triage Service

Three routes:

1. **AutoResolve** — all structural quality signals pass, no high-severity flags, no more than one medium-severity flag. Bill is written to the readings table. Audit entry recorded.

2. **DraftForHumanReview** — moderate flag severity or quality signals that suggest a fixable issue (unit mismatch the system can propose a correction for, name mismatch with high textual similarity to an existing site, etc.). The system passes the bill plus the flags to the **Resolution Drafter Service**. Output: a drafted resolution attached to the audit entry. Human approves or rejects.

3. **Escalate** — high-severity flags, low structural quality, or unresolvable reconciliation failures. The escalation carries a **routing key** that maps to a specific exception class:
   - `connect_integrity` — Connect-mode bills that failed processing (defined, not exercised in prototype since no Connect path)
   - `meter_unassigned` — meter not in store
   - `overlap` — overlapping billing period detected
   - `format_mismatch` — unit, currency, or building-name mismatch
   - `inactive_meter` — reading on a meter marked inactive
   - `uncategorized` — escalation that doesn't fit any other class (this bucket is explicitly visible — Matt's teams need to see where the rule set is weak)

Thresholds are configurable and logged in DECISIONS.md. The reasoning behind each triage decision is recorded in the audit entry, not just the decision itself. This is the glass-box requirement.

**Cut from prototype, documented in scale-to-production:** AutoEstimate route (system computes an estimate from rolling history for resolvable gaps, attaches the math, either auto-applies for high-confidence cases or queues for one-click human approval).

#### Resolution Drafter Service

A separate service called by Triage on the DraftForHumanReview path. Takes a `ValidatedBill` plus its flags plus the meter and account context, and produces:

- A proposed corrective action (e.g., "change unit from CCF to therms based on this provider's typical reporting")
- A drafted customer email (where customer outreach is appropriate) explaining what was observed and asking for clarification or confirmation
- A note on the proposed resolution's basis

This is the highest-leverage AI surface in the system — the literal "Claude as your back-office writer, gated on human approval" capability. Single Claude call, structured output, attached to the audit entry. Tight, focused, and the demo moment.

#### Audit Log

Every bill produces an `AuditEntry`:
- Timestamp, source mode, source reference (path or row index)
- Optional batch_id linking to the upload that contained it
- Normalized fields with structural quality signals
- Reconciliation result (meter match, prior context)
- Validation flags raised
- Triage decision with reasoning
- Drafted resolution (if applicable)
- Output payload (the readings-table write)

Persists to a SQLite table. Production hook: a durable log with retention rules driven by regulatory framework requirements (GRESB, SFDR, CSRD).

### Downstream Consumer

The prototype outputs one artifact: a **readings-table write payload (JSON)**, always produced. Represents what would be written to Measurabl's canonical readings table in production. From there, in production, the data fans out to: customer dashboards and Meter Completeness metrics, Utility Sync to ENERGY STAR Portfolio Manager, GRESB submission packets, ESGx Securities, and Data Manager outlier surfacing. The prototype writes this to a JSON file per bill.

The batch endpoint additionally returns a **batch summary report (JSON)** with per-row triage outcomes and aggregate counts.

**Cut from prototype, documented in scale-to-production:** Data Completeness Check-shaped XLSX export as a second downstream consumer. The architectural framing for scale-to-production: the pipeline emits one canonical payload; downstream consumers like DCC reports are independent transformers over that payload, which decouples production cadence from ingestion cadence.

### Data Model (pydantic)

```python
RawBillInput         # Whatever came in + mode + batch context if applicable
NormalizedBill       # Post-reference-lookup; units + provider canonicalized; structural signals attached
ReconciledBill       # NormalizedBill + matched Meter + prior context
ValidatedBill        # ReconciledBill + list[QualityFlag]
TriageDecision       # Route + reasoning + drafted resolution (if applicable)
AuditEntry           # Full lineage record
QualityFlag          # Type + severity + description + recommended action class
RoutingKey           # Enum for Escalate sub-routes
```

### Decision Logic Specifics

**Triage thresholds** (initial values, configurable, logged in DECISIONS.md):
- AutoResolve: all structural quality signals pass, no high-severity flags, ≤1 medium-severity flag
- Escalate: any of {≥1 high-severity flag, ≥3 medium-severity flags, no matched meter}
- DraftForHumanReview: the residual

**Heuristic thresholds:**
- Gap medium: 2–7 days; high: >7 days
- Overlap: any overlap is high

These are starting points. The point is that they are named, configurable, and logged.

---

## 5. Scope

### In scope for May 26

- Working ingestion endpoints accepting JSON-row and XLSX batch modes
- Normalization service with structural quality signals
- Reference data layer with ~10 providers and full unit conversion
- SQLite-backed store with Portfolio → Site → Account → Meter → Reading hierarchy
- Reconciliation service consulting the store
- Schema validation
- Two domain heuristics (gap, overlap) plus structural checks
- Triage layer with all three routes
- Resolution Drafter Service for DraftForHumanReview path
- Audit log writing to SQLite
- Downstream consumer: readings-table write payload (JSON) + batch summary JSON
- 4 sample scenarios, one per triage outcome plus a clean batch
- pytest coverage on the service layer
- Complete CLAUDE.md, DECISIONS.md, TASKS.md
- Architecture diagram (Mermaid in README)
- README walking a reader through running the prototype and reading the outputs

### Out of scope (explicitly named, with treatment in scale-to-production)

- PDF extraction mode
- AutoEstimate triage route
- Statistical anomaly heuristic
- Intermittent meter signature detection
- Data Completeness Check XLSX export
- Full production XLSX template parser
- Cross-extraction-agreement confidence model
- Tariff-aware reference data layer
- AI-driven reference library expansion
- Real Connect-mode utility provider API integrations
- Real database, real auth, real queues, streaming ingestion
- Geographic coverage beyond US plus one EU example
- Multi-tenant isolation
- Any UI

### Why the line is drawn here

Twenty focused hours against an existing energy budget. The walkthrough is the deliverable, not the system. Every hour past the threshold is an hour stolen from the methodology artifacts and the scale-to-production doc, which are the real differentiators.

---

## 6. Build Plan

Four phases, ~5 hours each.

### Phase 1 — Foundation (~5 hours)

Goal: Repo skeleton, methodology artifacts, schema in place before any service code.

- Repo created, .gitignore, pyproject.toml
- CLAUDE.md v1 written
- DECISIONS.md initialized with: stack choices, SQLite-not-Postgres, structural-only confidence, three-route triage, entity model alignment to Measurabl
- TASKS.md initialized with the full Phase 2–4 backlog
- README.md skeleton with placeholder for Mermaid diagram
- Mandatory-on-every-change rules documented in CLAUDE.md
- pydantic data models (`RawBillInput`, `NormalizedBill`, `ReconciledBill`, `ValidatedBill`, `TriageDecision`, `AuditEntry`, `QualityFlag`, `RoutingKey`)
- SQLite schema (DDL file)
- Store implementations (MeterHistoryStore, AuditLogStore)
- Fixture data: 3 sites, 5 accounts, 8 meters, 30+ historical readings

**Acceptance:** A fresh Claude instance handed the repo can read CLAUDE.md, DECISIONS.md, and TASKS.md and immediately know what to build next. The DB seeds cleanly. The pydantic models import without error.

### Phase 2 — Single-Row Pipeline (~5 hours)

Goal: `POST /bills` working end-to-end for JSON-row.

- FastAPI scaffolding, `POST /bills` endpoint
- JSON-row ingestion handler
- Reference data layer with 10 providers + unit conversion
- Normalization service with structural quality signals
- Reconciliation service consulting the store
- Schema validation
- Two domain heuristics (gap, overlap)
- Structural checks (unit/currency/name/inactive)
- Tests for each service

**Acceptance:** End-to-end flow runs from JSON-row input to ValidatedBill on at least two sample inputs (one clean, one with injected error). Reconciliation correctly identifies the matched meter and pulls prior context.

### Phase 3 — Triage, Drafter, Batch (~5 hours)

Goal: Triage with three routes, Resolution Drafter, batch endpoint, audit log writes.

- Triage service with three-route decision logic
- Resolution Drafter Service (single Claude call, structured output)
- `POST /batches` endpoint with simplified XLSX template parser
- Batch summary report assembly
- AuditEntry writes to SQLite
- 4 sample scenarios constructed and documented in `samples/scenarios.md`
- Tests for triage logic with explicit expected decisions
- Tests for resolution drafter (mock the Claude call in tests)

**Acceptance:** All 4 sample scenarios produce documented expected outcomes through the full pipeline. The batch endpoint accepts an XLSX, processes N rows, returns a batch summary. Audit entries query cleanly by bill or by batch.

### Phase 4 — Walkthrough Prep (~5 hours)

Goal: Demonstrable, documentation complete, dry-run clean.

- Walkthrough script (README section)
- Architecture diagram polished (Mermaid)
- CLAUDE.md current-state section fully reflects what was built
- DECISIONS.md updated with every decision made during the build
- All sample scenarios documented in `samples/scenarios.md` with expected outcomes
- One out-loud dry-run
- Anything caught in the dry-run that breaks the flow

**Acceptance:** Andrew can walk through the system end-to-end in 25 minutes without referring back to chat history.

### Slack and contingency

Twenty hours is the target. If migraines or other commitments compress that, the contingency cut order is:
1. Drop the XLSX batch endpoint, demo single-row only (saves ~2 hours)
2. Reduce to 3 sample scenarios (saves ~1 hour)
3. Drop the structural checks beyond unit and name mismatch (saves ~1 hour)

If the build runs **ahead** of schedule, the highest-ROI additions in order are: statistical anomaly heuristic, then a third sample batch scenario, then a polished README walkthrough video. Do not reach for cut features (PDF, AutoEstimate, DCC) — those belong in scale-to-production where they get better treatment than a rushed implementation would give them.

---

## 7. The May 26 Walkthrough

Brief note (the meeting is downstream of the work). The walkthrough arc:

**Open** (3 min). Frame the system as upstream of Data Manager. Sketch the six-stage decomposition at the architecture diagram.

**Decisions** (10 min). Walk through three or four key decisions from DECISIONS.md: entity model aligned to Measurabl's actual hierarchy, structural-only confidence model, three-route triage with structured escalation routing, deliberately simplified reference library.

**Run it** (10 min). Walk through the 4 sample scenarios end-to-end. The Resolution Drafter moment (a Claude-drafted customer email on a unit-mismatch case) is the demo highlight.

**The methodology** (5 min). Open CLAUDE.md, DECISIONS.md, TASKS.md. Frame as the operations system for distributed engineering work.

**Where it goes next** (8 min, increased per Andrew's call). Open the scale-to-production doc. Walk the section headers — every feature cut from the prototype lives here with real architectural treatment. This is the highest-leverage segment of the conversation. Be ready for: "how does this handle 1M bills," "how do you keep the reference library current," "what about non-US providers."

**Close** (2 min). Invite Matt to push on any of it.

---

## 8. Rules and Patterns for the Build

### Mandatory on every change

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

### Coding patterns

- Dependency injection via FastAPI's `Depends`. Constructor injection in service classes.
- Thin controllers: route handlers accept the request, call a service, return the result. No business logic in route handlers.
- Services are stateless. State lives in dependency-injected stores.
- Error handling: services raise exceptions; route handlers catch and map to HTTP responses.
- Async wherever I/O happens, especially Anthropic API calls.
- Pydantic models for all DTOs and internal data passing.

### Commit convention

`<scope>: <imperative description>`

Examples:
- `normalize: add structural quality signals to normalization service`
- `triage: implement three-route decision logic`
- `docs: log decision on structural-only confidence model`

One task per commit. Commits referenced in TASKS.md.

### Sample scenarios

Sample scenarios are not isolated rows — they are multi-bill stories that demonstrate stateful behavior. To demonstrate gap detection, two bills on the same meter with a gap. To demonstrate overlap, two bills on the same meter that overlap. Each scenario is documented in `samples/scenarios.md` with meter setup, bills involved, and expected pipeline output.

### Anonymization

If real utility bills are referenced in fixtures, all PII (account numbers, customer names, addresses) is stripped before being committed. The README notes this explicitly.

---

## 9. Open Questions

Andrew to decide (or to revisit during build):

- Real Claude API vs mocked during development. Recommendation: real API during the build with rate-limiting; mocked in tests so tests run free.
- Repo public vs private. Recommendation: public, consistent with prior projects.
- Walkthrough deck or just the repo. Recommendation: no deck. The repo plus the Mermaid diagram in the README is the artifact.

---

## 10. Reference Materials

- supplier-emissions-normalizer (architectural bones): `https://github.com/ajf42/supplier-emissions-normalizer`
- FormulationImpactAPI (methodology pattern): `https://github.com/ajf42/FormulationImpactAPI`
- Measurabl Help Center articles (entity model, templates, Data Manager, Connect): `https://support.measurabl.com`
- Anthropic Python SDK documentation
- FastAPI documentation
- pydantic v2 documentation

### Files this design produces in the new repo

```
utility-bill-pipeline/
  README.md
  CLAUDE.md
  DECISIONS.md
  TASKS.md
  pyproject.toml
  .gitignore
  src/
    main.py                         FastAPI app entry
    models/
      bill.py                       RawBillInput, NormalizedBill, ReconciledBill, ValidatedBill
      quality.py                    QualityFlag, TriageDecision, RoutingKey
      audit.py                      AuditEntry
      entities.py                   Portfolio, Site, Account, Meter, Reading
    db/
      schema.sql                    SQLite DDL
      store.py                      MeterHistoryStore, AuditLogStore
      fixtures.py                   Seed fixture data
    services/
      ingestion.py                  Mode dispatch (single + batch)
      reference.py                  Provider library, unit conversion
      normalization.py              Apply reference data, attach structural signals
      reconciliation.py             Match meter, attach history
      validation.py                 Schema + heuristics
      triage.py                     Three-route decision logic
      resolution_drafter.py         DraftForHumanReview output
      audit_log.py                  Persistence
      output_writer.py              JSON readings payload
    routes/
      bills.py                      POST /bills
      batches.py                    POST /batches
  tests/
    test_normalization.py
    test_reconciliation.py
    test_validation.py
    test_triage.py
    test_resolution_drafter.py
    test_audit_log.py
    test_batch.py
  samples/
    bills/                          Sample bill files (JSON, XLSX)
    scenarios.md                    Multi-bill stories with expected outcomes
  docs/
    architecture.md                 Architecture diagram + narrative
```

---

## Notes for the new Claude instance

If you are reading this as the seed for a build session: this is the working spec. Start at Phase 1 of Section 6 and execute. The mandatory-on-every-change rules in Section 8 govern every commit. Update CLAUDE.md, DECISIONS.md, and TASKS.md as you go — they are not afterthoughts, they are the deliverable Matt cares about most. The audience for the build is Matt Richardson at Measurabl; calibrate every decision to "what would a process-and-operations leader want to see documented here." When in doubt, log the decision. Architecture choices in this document are starting points and may be revised — when they are, log the revision in DECISIONS.md with rationale.
