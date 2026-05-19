# DECISIONS.md — Architecture Decision Record

Every significant architectural or engineering decision is logged here. Each entry uses the ADR format: **Status**, **Context**, **Decision**, **Consequences**. Newer decisions append to the bottom. Superseded decisions are kept and marked as such — history is not rewritten.

The authoritative design spec is [DESIGN.md](DESIGN.md). Decisions logged here either implement, refine, or revise that spec.

---

## ADR-001 — Python + FastAPI + pydantic v2 as the stack

**Status:** Accepted (2026-05-15)

**Context:** The prototype is a data-shaped service with HTTP ingress, runtime validation of messy inputs, and an Anthropic API call. The 20-hour build window does not allow time for stack choices to be wrong. The audience (Matt) is a systems-intuition operations leader; readability of the resulting code matters more than runtime micro-performance.

Alternatives considered: Node + Express + Zod (rejected — weaker tabular tooling, and the structural-quality signals lean on pandas-shaped work); Go + chi (rejected — slower to write, weaker AI tooling); a notebook-first prototype (rejected — would not demonstrate the production-discipline pattern the methodology artifacts depend on).

**Decision:** Python 3.11+ runtime. FastAPI for the HTTP surface (lightweight, async-native, OpenAPI built-in). pydantic v2 for all DTOs, internal data passing, and validation. Anthropic SDK for the single Claude call in the Resolution Drafter. pandas + openpyxl for tabular work. pytest for tests. SQLite for persistence (see [ADR-002](#adr-002--sqlite-for-persistence-in-the-prototype)).

**Consequences:** A familiar, productive stack with strong documentation. The pydantic v2 choice constrains every internal data type to a serializable, inspectable shape, which serves the glass-box requirement. FastAPI's `Depends` becomes the dependency-injection mechanism the coding patterns rely on. The cost: anyone reading the repo needs Python 3.11+ available.

---

## ADR-002 — SQLite for persistence in the prototype

**Status:** Accepted (2026-05-15)

**Context:** The reconciliation and audit-log layers need a real datastore — real SQL semantics, real foreign keys, real indexes — for the pipeline to do honest work. A mock store would make every claim about reconciliation suspect. Twenty hours does not buy time to stand up Postgres with realistic ergonomics, and infrastructure setup steals from the artifacts that actually differentiate the build.

Alternatives considered: in-memory dict-backed store (rejected — would not exercise schema, FK, or query semantics, and the reconciliation story collapses); Postgres in Docker (rejected — infrastructure overhead, friction for a fresh reader running the prototype).

**Decision:** SQLite, file-on-disk, schema defined in `src/db/schema.sql`. Tables follow the entity model from [ADR-005](#adr-005--entity-model-aligned-to-measurabls-published-hierarchy): `sites`, `accounts`, `meters`, `readings`, `audit_entries`. Reconciliation reads prior readings from this store; AutoResolve writes append-only readings; every triage decision writes an audit entry.

**Consequences:** A reader can clone the repo and run the prototype with zero infrastructure. Foreign keys and indexes give the reconciliation and gap/overlap heuristics real teeth. The story for production is explicitly documented: Postgres with read replicas for dashboard joins, a queue-based write pattern, and CDC streams to downstream consumers. The scale-to-production doc covers this in detail; the prototype's job is to make the production hook obvious without pretending to be production.

---

## ADR-003 — Structural-only confidence model (no LLM self-reported confidence)

**Status:** Accepted (2026-05-15)

**Context:** Every normalized field needs a confidence signal so triage can decide what to trust. The temptingly-cheap path is to ask the LLM to score its own extractions. That path is wrong for this audience and this problem: LLM-self-reported confidence is well-known to be poorly calibrated, and Matt's situation is "I am liable for the output and cannot trust black boxes." Confidence that originates inside the model cannot be audited later.

Alternatives considered: LLM self-reported confidence (rejected as above); cross-extraction-agreement — run extraction twice with different prompts and treat agreement as the signal (the right answer at production scale, but not earned in 20 hours and not where the prototype's leverage lives).

**Decision:** Confidence comes from **structural quality signals only**: type/format validation (does the date parse, is the number numeric, is the unit in the known set), plausible-range checks (kWh positive, billing period 25–35 days for monthly, etc.), provider presence in the reference library, and within-row agreement of unit/currency/account-type. The signal is **flag-liberal**: better to flag five fields and have three turn out fine than to pass a real error through. Triage decides what to do with flagged fields; the structural layer does not gate.

**Consequences:** Every confidence signal is inspectable and reproducible — a flagged field has a named reason that a back-office reviewer can read. The prototype tells a credible story about why it trusts what it trusts. The deferred richer model (cross-extraction-agreement) is named in the scale-to-production doc with real architectural treatment, and that deferral is itself a piece of evidence about how the build was reasoned about.

---

## ADR-004 — Three-route triage (AutoResolve / DraftForHumanReview / Escalate)

**Status:** Accepted (2026-05-15)

**Context:** Triage is the act of deciding, per bill, what happens next. The decision space has obvious extremes — "the bill is clean, write it" and "the bill is unfixable, send it to a team" — but Matt's operational reality is that the middle is where most of the cost lives. A two-route system collapses the middle into Escalate and loses the highest-leverage AI surface in the system. A four-route system pulls in AutoEstimate, which is genuinely useful but is high logic complexity and the most likely route to be implemented poorly in a 20-hour window.

Alternatives considered: two routes — Resolve / Escalate (rejected, see above); four routes including AutoEstimate (deferred to scale-to-production where it gets real treatment instead of a rushed one).

**Decision:** Three routes.

1. **AutoResolve** — all structural quality signals pass, no high-severity flags, ≤1 medium-severity flag. Written to readings table.
2. **DraftForHumanReview** — moderate severity or fixable issues. Triage calls the Resolution Drafter Service; a proposed action, drafted customer email, and basis note are attached to the audit entry. Human approves or rejects.
3. **Escalate** — high-severity flags, low structural quality, or unresolvable reconciliation failures. Carries a **routing key** mapping to a specific exception class (`connect_integrity`, `meter_unassigned`, `overlap`, `format_mismatch`, `inactive_meter`, `uncategorized`).

The reasoning behind each decision is recorded in the audit entry, not just the decision itself.

**Consequences:** The middle route is where the demo highlight lives — a Claude-drafted customer email on a unit-mismatch case is the "Claude as your back-office writer, gated on human approval" moment. The `uncategorized` escalation bucket is deliberately visible: it makes weak spots in the rule set legible to Matt's teams instead of swallowing them. The deferred AutoEstimate route is named and treated in the scale-to-production doc; this is the explicit scale path for triage and the prototype's three-route model is a clean foundation it slots onto.

---

## ADR-005 — Entity model aligned to Measurabl's published hierarchy

**Status:** Accepted (2026-05-15)

**Context:** A utility ingestion pipeline can be modeled many ways. Most generic data-cleaning tutorials would land on a flat "bill" entity with optional foreign keys. The audience and problem demand the opposite: Matt's teams work inside Measurabl's actual hierarchy every day, the Help Center documents that hierarchy unambiguously, and the published bulk-upload templates encode the field-level constraints (unit locked to meter, currency locked to meter, building name must match a Site, readings are append-only).

Alternatives considered: a generic Bill entity with loose FKs (rejected — collapses domain constraints that matter); modeling only the layers the heuristics touch directly, e.g., Meter and Reading (rejected — would make foreign keys dishonest, and "the entity model matches your published one" is a credibility signal worth more than the lines of code it costs).

**Decision:** The full chain is modeled: **Portfolio → Site → Account → Meter → Reading**. Upper levels (Portfolio, Site) are tiny fixture tables; their job is to make foreign keys honest. Field detail follows Measurabl's published templates: Account Number / Account Type / Generation Account flag on Account; Unique Meter ID, naming convention, Start/End dates, Type, Unit (locked), Currency (locked), Landlord-or-Tenant-paid, Active toggle on Meter; Period Start/End, Usage, Usage Units, Cost, Currency, Demand kW, Demand Spend, Energy Exported on Reading.

Hard rules encoded in validation: meter locked to one unit (mismatch = high-severity flag); readings append-only (corrections via flagged workflow, not overwrite); building name must match an existing Site (mismatch = high-severity); reading on an inactive meter = high-severity.

**Consequences:** Walking Matt through `src/models/entities.py` is recognizable on sight. Every validation rule has a clean home (the entity it constrains). The cost is a few extra fixture rows for Portfolios and Sites; the benefit is that the prototype demonstrably understands the operational object model rather than imposing a generic one.

---

## ADR-006 — Cross-field validation between Reading and Meter is deferred to the validation service

**Status:** Accepted (2026-05-15)

**Context:** Two field-level rules from [ADR-005](#adr-005--entity-model-aligned-to-measurabls-published-hierarchy) — that a `Reading.usage_units` must match its parent `Meter.unit`, and that `Reading.currency` must match `Meter.currency` — could be enforced at three different layers: the pydantic models (raise on construction), the validation service (emit a `QualityFlag` with severity), or the database layer (CHECK constraint joining across tables). Each placement has a cost.

Enforcing at the pydantic boundary would mean either (a) carrying a reference to the parent meter inside `Reading`, which inverts ownership and breaks the append-only, self-contained-artifact property the pipeline relies on, or (b) using a custom validator with external lookup, which couples model construction to an injected store and defeats the point of pydantic as a pure data contract. It would also crash the pipeline on the exact inputs we most want to *see* and route — a unit mismatch is a high-leverage demo moment for the Resolution Drafter (see DESIGN.md §4 "Triage Service" — the unit-mismatch case is the canonical DraftForHumanReview path).

Enforcing only at the database CHECK level loses the structured flag — the back-office team would see a constraint failure, not a `UNIT_MISMATCH` flag with severity and routing key.

Alternatives considered: pydantic-level `field_validator` requiring an injected `Meter` (rejected, see above); SQLite trigger raising on insert (rejected — same structured-flag problem, plus harder to test).

**Decision:** Cross-field validation between `Reading` and its parent `Meter` is the **validation service's** responsibility, not the pydantic model's. The model accepts a mismatch on construction so the validation service can emit a structured `QualityFlag` (`UNIT_MISMATCH` or `CURRENCY_MISMATCH`, high-severity), which triage then routes — typically to DraftForHumanReview for unit mismatches (Resolution Drafter proposes the corrective unit) and to Escalate with `FORMAT_MISMATCH` for currency mismatches. The behavior is pinned by `test_reading_unit_currency_independent_of_parent_meter` in `tests/test_models.py`.

**Consequences:** The mismatched-unit demo moment lives. Every cross-field check has one home (the validation service), which is also where the gap and overlap heuristics live — placement is predictable. The cost is that "a Reading exists with units that don't match its Meter" can briefly be a valid in-memory state between model construction and the validation step; in practice this only happens inside the pipeline call where the validation service runs synchronously immediately after, so no caller of the pipeline observes the invalid state. The DB layer will additionally encode the unit and currency on the meter so the store can refuse an actually-bad write on the AutoResolve path, providing belt-and-suspenders without changing where the structured flag originates.

---

## ADR-007 — Stdlib `sqlite3` for persistence, not an ORM

**Status:** Accepted (2026-05-18)

**Context:** The persistence layer needs CRUD across five tables, one cross-table join (the three-key meter resolution in reconciliation), an idempotent schema, and round-trippable storage of structured payloads on the audit log. The natural Python options were SQLAlchemy (Core or ORM), Peewee, or stdlib `sqlite3`. The audience for this build is Matt Richardson, an operations leader who reads SQL more comfortably than ORM call chains, and the prototype budget is 20 focused hours total.

Alternatives considered: SQLAlchemy ORM (rejected — adds a dependency, introduces session/unit-of-work concepts that don't help the prototype and that obscure the SQL during a walkthrough); SQLAlchemy Core (rejected — Core-level SQL expressions still read less obviously than literal SQL strings, and the migration to Postgres in production is a connection-string change either way); Peewee (rejected — fewer warts than full SQLAlchemy but still ORM-flavored, still a dependency).

**Decision:** Use stdlib `sqlite3` directly. Schema lives in [src/db/schema.sql](src/db/schema.sql) as literal SQL the audience can read on its own. Two store classes ([src/db/store.py](src/db/store.py)) wrap a `sqlite3.Connection` each, with `PRAGMA foreign_keys = ON` set per connection. Row-to-model and model-to-row translation are small private functions at the bottom of the file. All writes commit explicitly. Pydantic models from `src.models` are the only shapes the stores accept or return; SQL stays inside this module.

**Consequences:** Walking Matt through [src/db/store.py](src/db/store.py) is walking him through SQL, which is the point. Zero added dependency surface. The cost is hand-mapping rows to models, which adds maybe 60 lines and is a known pattern. The production hook is unchanged: swap the connection target to Postgres and the SQL ports as-is (SQLite-specific bits — the `PRAGMA`, `TEXT` for dates — are isolated and documented). Cross-field validation between `Reading` and `Meter` from [ADR-006](#adr-006--cross-field-validation-between-reading-and-meter-is-deferred-to-the-validation-service) remains in the validation service; the store does not try to enforce it.

---

## Spec gaps observed

Gaps in [DESIGN.md](DESIGN.md) that surfaced during a build step and required either a decision or a clarification before proceeding. Per the ambiguity-handling rule in [CLAUDE.md](CLAUDE.md), inventing on ambiguous spec is forbidden — any gap encountered is logged here, optionally accompanied by a `TODO` in the code at the point of contact.

When a gap is resolved (DESIGN.md updated, or a human decision recorded), leave the entry in place and mark it `(resolved in <ref>)` rather than deleting; the history of where the spec wasn't quite right is itself useful.

- **DDL nullability is looser than pydantic required-ness for several fields.** Encountered while building [src/db/store.py](src/db/store.py) for Prompt 3. The schema spec in the prompt declared `sites.region`, `sites.portfolio_id`, `meters.landlord_or_tenant`, `readings.currency`, and `audit_entries.bill_external_ref` as nullable (`TEXT` without `NOT NULL`), but the corresponding pydantic models in [src/models/entities.py](src/models/entities.py) and [src/models/audit.py](src/models/audit.py) require all of these. Resolution chosen without surfacing because the asymmetry is benign in practice: stores always populate from the strict pydantic models, so NULL never reaches these columns; if it ever does (direct DB manipulation, or a future code path bypassing the model), the pydantic read-path validates and fails loudly rather than silently coercing. The schema follows the prompt-spec'd DDL exactly; tightening to `NOT NULL` would require an explicit DESIGN.md update. Flagging here so a future reader notices the pattern rather than discovering it via a read-time validation error. **(resolved in this prompt — DESIGN.md §4 updated, schema tightened, signature formalized)**
- **`add_reading(reading: Reading)` has no place for `ingested_at`, `source_mode`, or `batch_id`.** Encountered while building [src/db/store.py](src/db/store.py) for Prompt 3. The Prompt 3 signature is exactly `add_reading(reading: Reading) -> int`, but the readings table per DESIGN.md §4 has three additional columns describing how the reading entered the system. Resolution: the public signature still accepts a Reading as its only positional argument, with `source_mode`, `batch_id`, and `ingested_at` accepted as keyword-only with safe defaults (`source_mode="FIXTURE"`, `batch_id=None`, `ingested_at=datetime.now(UTC)`). Pipeline writes will pass these explicitly when the ingestion handler is built in Phase 2; fixture seeding gets the defaults. **(resolved in this prompt — DESIGN.md §4 updated, schema tightened, signature formalized: `source_mode` is now keyword-required with no default; fixture seeding passes `"FIXTURE"` explicitly)**
- **Four additional DDL columns are nullable where pydantic is required.** Surfaced during the spec-tightening pass that resolved the original "DDL nullability is looser than pydantic" gap. Beyond the five named columns the prompt fixed (`sites.region`, `sites.portfolio_id`, `meters.landlord_or_tenant`, `readings.currency`, `audit_entries.bill_external_ref`), the following columns are also nullable in [src/db/schema.sql](src/db/schema.sql) but required on their pydantic counterparts: `accounts.site_id`, `meters.account_id`, `readings.meter_id` (three foreign-key columns that should arguably be `NOT NULL REFERENCES …`), and `audit_entries.source_mode` (required on `AuditEntry.source_mode`). Not auto-fixed per the prompt's "don't auto-fix beyond the five named above" instruction. The same principle applies (pydantic is the contract source of truth; the schema should mirror it); a follow-up tightening pass would change these in one commit alongside a DESIGN.md note that FK columns are `NOT NULL` whenever the parent relationship is required.
- **Regional rules: granularity and EU currency default.** Encountered while building [src/services/reference.py](src/services/reference.py) for Phase 2 Prompt 1. DESIGN.md §4 specifies "a regional ruleset (US, EU)" with two regions; the Phase 2 prompt asks for three (`US-East`, `US-West`, `EU`) so the provider library can group by service territory. DESIGN.md §4 also does not specify a default currency per region. Resolution: introduce a `ReferenceRegion` enum local to the reference layer with the three prompt-requested values, keep `models.entities.Region` (US / EU) as the coarser tag on a Site, and default the EU regional currency to GBP because all three EU provider entries in the curated library (British Gas, Thames Water, EDF Energy UK arm) bill in GBP. Decimal/date conventions: US-East and US-West share `.` and `MM/DD/YYYY`; EU is `,` and `DD/MM/YYYY`. If DESIGN.md is later tightened to specify per-region currency defaults, this entry resolves and the constants in `reference.py` re-sync to spec. **(resolved in this prompt — DESIGN.md §4 updated, schema tightened, signature formalized: the three-region granularity, the two-region coarser tag on Site, and the EU→GBP default are now spec)**
