# DECISIONS.md — Architecture Decision Record

Every significant architectural or engineering decision is logged here. Each entry uses the ADR format: **Status**, **Context**, **Decision**, **Consequences**. Newer decisions append to the bottom. Superseded decisions are kept and marked as such — history is not rewritten.

The authoritative design spec is [DESIGN.md](DESIGN.md). Decisions logged here either implement, refine, or revise that spec.

---

## ADR-001 — Python + FastAPI + pydantic v2 as the stack

**Status:** Accepted (2026-05-15)

**Context:** The prototype is a data-shaped service with HTTP ingress, runtime validation of messy inputs, and an Anthropic API call. The 20-hour build window does not allow time for stack choices to be wrong. The audience is a systems-intuition operations leader; readability of the resulting code matters more than runtime micro-performance.

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

**Context:** Every normalized field needs a confidence signal so triage can decide what to trust. The temptingly-cheap path is to ask the LLM to score its own extractions. That path is wrong for this audience and this problem: LLM-self-reported confidence is well-known to be poorly calibrated, and the audience's situation is "I am liable for the output and cannot trust black boxes." Confidence that originates inside the model cannot be audited later.

Alternatives considered: LLM self-reported confidence (rejected as above); cross-extraction-agreement — run extraction twice with different prompts and treat agreement as the signal (the right answer at production scale, but not earned in 20 hours and not where the prototype's leverage lives).

**Decision:** Confidence comes from **structural quality signals only**: type/format validation (does the date parse, is the number numeric, is the unit in the known set), plausible-range checks (kWh positive, billing period 25–35 days for monthly, etc.), provider presence in the reference library, and within-row agreement of unit/currency/account-type. The signal is **flag-liberal**: better to flag five fields and have three turn out fine than to pass a real error through. Triage decides what to do with flagged fields; the structural layer does not gate.

**Consequences:** Every confidence signal is inspectable and reproducible — a flagged field has a named reason that a back-office reviewer can read. The prototype tells a credible story about why it trusts what it trusts. The deferred richer model (cross-extraction-agreement) is named in the scale-to-production doc with real architectural treatment, and that deferral is itself a piece of evidence about how the build was reasoned about.

---

## ADR-004 — Three-route triage (AutoResolve / DraftForHumanReview / Escalate)

**Status:** Accepted (2026-05-15)

**Context:** Triage is the act of deciding, per bill, what happens next. The decision space has obvious extremes — "the bill is clean, write it" and "the bill is unfixable, send it to a team" — but the operational reality is that the middle is where most of the cost lives. A two-route system collapses the middle into Escalate and loses the highest-leverage AI surface in the system. A four-route system pulls in AutoEstimate, which is genuinely useful but is high logic complexity and the most likely route to be implemented poorly in a 20-hour window.

Alternatives considered: two routes — Resolve / Escalate (rejected, see above); four routes including AutoEstimate (deferred to scale-to-production where it gets real treatment instead of a rushed one).

**Decision:** Three routes.

1. **AutoResolve** — all structural quality signals pass, no high-severity flags, ≤1 medium-severity flag. Written to readings table.
2. **DraftForHumanReview** — moderate severity or fixable issues. Triage calls the Resolution Drafter Service; a proposed action, drafted customer email, and basis note are attached to the audit entry. Human approves or rejects.
3. **Escalate** — high-severity flags, low structural quality, or unresolvable reconciliation failures. Carries a **routing key** mapping to a specific exception class (`connect_integrity`, `meter_unassigned`, `overlap`, `format_mismatch`, `inactive_meter`, `uncategorized`).

The reasoning behind each decision is recorded in the audit entry, not just the decision itself.

**Consequences:** The middle route is where the demo highlight lives — a Claude-drafted customer email on a unit-mismatch case is the "Claude as your back-office writer, gated on human approval" moment. The `uncategorized` escalation bucket is deliberately visible: it makes weak spots in the rule set legible to the back-office teams instead of swallowing them. The deferred AutoEstimate route is named and treated in the scale-to-production doc; this is the explicit scale path for triage and the prototype's three-route model is a clean foundation it slots onto.

---

## ADR-005 — Entity model aligned to Measurabl's published hierarchy

**Status:** Accepted (2026-05-15)

**Context:** A utility ingestion pipeline can be modeled many ways. Most generic data-cleaning tutorials would land on a flat "bill" entity with optional foreign keys. The audience and problem demand the opposite: the back-office teams work inside Measurabl's actual hierarchy every day, the Help Center documents that hierarchy unambiguously, and the published bulk-upload templates encode the field-level constraints (unit locked to meter, currency locked to meter, building name must match a Site, readings are append-only).

Alternatives considered: a generic Bill entity with loose FKs (rejected — collapses domain constraints that matter); modeling only the layers the heuristics touch directly, e.g., Meter and Reading (rejected — would make foreign keys dishonest, and "the entity model matches your published one" is a credibility signal worth more than the lines of code it costs).

**Decision:** The full chain is modeled: **Portfolio → Site → Account → Meter → Reading**. Upper levels (Portfolio, Site) are tiny fixture tables; their job is to make foreign keys honest. Field detail follows Measurabl's published templates: Account Number / Account Type / Generation Account flag on Account; Unique Meter ID, naming convention, Start/End dates, Type, Unit (locked), Currency (locked), Landlord-or-Tenant-paid, Active toggle on Meter; Period Start/End, Usage, Usage Units, Cost, Currency, Demand kW, Demand Spend, Energy Exported on Reading.

Hard rules encoded in validation: meter locked to one unit (mismatch = high-severity flag); readings append-only (corrections via flagged workflow, not overwrite); building name must match an existing Site (mismatch = high-severity); reading on an inactive meter = high-severity.

**Consequences:** Walking the audience through `src/models/entities.py` is recognizable on sight. Every validation rule has a clean home (the entity it constrains). The cost is a few extra fixture rows for Portfolios and Sites; the benefit is that the prototype demonstrably understands the operational object model rather than imposing a generic one.

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

**Context:** The persistence layer needs CRUD across five tables, one cross-table join (the three-key meter resolution in reconciliation), an idempotent schema, and round-trippable storage of structured payloads on the audit log. The natural Python options were SQLAlchemy (Core or ORM), Peewee, or stdlib `sqlite3`. The audience is an operations leader who reads SQL more comfortably than ORM call chains, and the prototype budget is 20 focused hours total.

Alternatives considered: SQLAlchemy ORM (rejected — adds a dependency, introduces session/unit-of-work concepts that don't help the prototype and that obscure the SQL during a walkthrough); SQLAlchemy Core (rejected — Core-level SQL expressions still read less obviously than literal SQL strings, and the migration to Postgres in production is a connection-string change either way); Peewee (rejected — fewer warts than full SQLAlchemy but still ORM-flavored, still a dependency).

**Decision:** Use stdlib `sqlite3` directly. Schema lives in [src/db/schema.sql](src/db/schema.sql) as literal SQL the audience can read on its own. Two store classes ([src/db/store.py](src/db/store.py)) wrap a `sqlite3.Connection` each, with `PRAGMA foreign_keys = ON` set per connection. Row-to-model and model-to-row translation are small private functions at the bottom of the file. All writes commit explicitly. Pydantic models from `src.models` are the only shapes the stores accept or return; SQL stays inside this module.

**Consequences:** Walking the audience through [src/db/store.py](src/db/store.py) is walking through SQL, which is the point. Zero added dependency surface. The cost is hand-mapping rows to models, which adds maybe 60 lines and is a known pattern. The production hook is unchanged: swap the connection target to Postgres and the SQL ports as-is (SQLite-specific bits — the `PRAGMA`, `TEXT` for dates — are isolated and documented). Cross-field validation between `Reading` and `Meter` from [ADR-006](#adr-006--cross-field-validation-between-reading-and-meter-is-deferred-to-the-validation-service) remains in the validation service; the store does not try to enforce it.

---

## ADR-008 — Structural signals are nested dicts of plain booleans

**Status:** Accepted (2026-05-19)

**Context:** [ADR-003](#adr-003--structural-only-confidence-model-no-llm-self-reported-confidence) commits the system to structural-only confidence — signals come from observable checks, never an LLM self-report. The normalization service ([src/services/normalization.py](src/services/normalization.py)) is the first place that produces signals at scale, and the shape it picks dictates how triage, the audit log, and any future human reviewer read them. Three real options surfaced during implementation: a flat list of structured records (e.g., `[{name, ok, reason}, ...]`), a flat dict of dotted names (e.g., `{"field_type_valid.usage": True, "value_in_range.usage": True}`), or a small set of nested dicts grouped by signal category (`{"field_type_valid": {"usage": True, ...}, "value_in_range": {...}, ...}`).

The list-of-records shape is the most "queryable" but it duplicates the QualityFlag concept the validation service already owns and pushes triage to do its own filtering by `name`. The flat-dotted-name shape is the most database-friendly but is the least readable when printed verbatim in an audit entry. The nested-dict shape mirrors the categories DESIGN.md §4 names ("type and format validation", "plausible range", "provider presence", "cross-field agreement") so an audit reader sees the categories at a glance, and each leaf is a single boolean a triage rule can check directly.

A secondary decision under the same heading: how to represent "this check did not apply" (e.g., `value_in_range.cost` when no cost was provided, or `cross_field_agreement.currency_matches_region` when the provider could not be resolved). The options were tri-valued booleans (`True` / `False` / `None`) or the binary-with-omission pattern.

**Decision:** Structural signals are a `dict` with these top-level keys: `field_type_valid` (dict[str, bool] per field), `value_in_range` (dict[str, bool] per field), `provider_known` (bool), `provider_alias_parsed` (bool), `unit_known` (bool), `cross_field_agreement` (dict[str, bool] per check). All leaves are plain booleans — no `Optional[bool]`. Inapplicable per-field checks are **omitted** from their sub-dict (so the absence of `value_in_range["cost"]` is itself the "no cost was provided" signal). Inapplicable top-level cross-field checks (when the provider is unknown) default to `False` rather than being omitted — they are flag-liberal, biasing toward "we noticed something might be wrong" per ADR-003.

**Consequences:** Triage rules look like `signals["value_in_range"]["usage"]` — readable and decoupled from the per-flag QualityFlag concept that validation owns separately. Audit-log readers see the DESIGN.md §4 categories on sight when the JSON is pretty-printed. The omission convention requires that triage and validation use `.get()` or membership checks rather than direct indexing on per-field sub-dicts; this is explicit in the consumer code and is testable. The False-on-missing-dependency convention for cross-field checks costs us the ability to distinguish "noticed and disagreed" from "couldn't check"; the audit reader gets that distinction from the sibling `provider_known=False` signal. The structured-record alternative remains available as a future enhancement — it would be a transform over the same dict, not a replacement.

---

## ADR-009 — Drafter system prompt lives in a markdown file, not in Python

**Status:** Accepted (2026-05-19)

**Context:** The Resolution Drafter is a single Anthropic API call that takes a flagged bill and produces a structured drafted resolution. The system prompt is several hundred words long, contains worked examples, and is the load-bearing document that defines drafter behavior. Three natural places for it: a `str` constant inside [src/services/drafter.py](src/services/drafter.py), a `.py` module under `src/prompts/`, or a standalone markdown file under `src/prompts/`. The audience considerations from [ADR-005](#adr-005--entity-model-aligned-to-measurabls-published-hierarchy) apply — the audience reads the prompt as part of the walkthrough and judges whether the AI is genuinely glass-box.

Alternatives considered: prompt as a Python `str` triple-quoted constant (rejected — long prose embedded in a code file reads worse on review, and editing it triggers a code change for what is really a content change); prompt as a Python module exporting a constant (rejected — same downside, plus implies it should be importable as Python rather than read by reviewers).

**Decision:** The drafter's system prompt lives in [src/prompts/drafter_system.md](src/prompts/drafter_system.md) as plain markdown. `DrafterService.__init__` reads it from disk at construction time and holds the resulting string for the lifetime of the service. The path is overridable via the `system_prompt_path` constructor argument so tests can swap in a fixture prompt if needed.

**Consequences:** A reviewer reads the prompt the same way they read any other piece of methodology in the repo — as a markdown document with headers and examples, rendered by GitHub. Editing the prompt is a content change, not a code change, and shows up cleanly in `git diff`. The cost is one extra file read at service construction; negligible. The pattern generalizes if more Claude-backed services are added later — they each get their own `.md` under `src/prompts/`.

---

## ADR-010 — Drafter uses Anthropic tool-use to force structured output

**Status:** Accepted (2026-05-19)

**Context:** The drafter must return a structured object the pipeline can store in the audit log and (on approval) apply as a partial field override. There are three real ways to coerce structure out of a Claude call: ask for JSON in the prompt and parse the text, use the SDK's response_format-style JSON mode if applicable, or use tool-use with the schema defined as a tool's `input_schema` and force the model to call that tool. Each has tradeoffs in robustness, schema enforceability, and audit-log shape.

Alternatives considered: free-form text + "respond as JSON" instructions + manual parse (rejected — model occasionally wraps JSON in prose or markdown fences, requiring brittle post-processing; no schema enforcement on the model side); JSON-mode (text response constrained to valid JSON, no schema enforcement) (rejected — still no schema enforcement, and the Anthropic SDK pattern that maps best onto pydantic schemas is tool-use).

**Decision:** Use Anthropic's tool-use mechanism. A single tool named `draft_resolution` is registered with `input_schema` derived from `DrafterOutput.model_json_schema()`. The call sets `tool_choice={"type": "tool", "name": "draft_resolution"}`, forcing the model to invoke the tool rather than respond in free text. The drafter then reads the `tool_use` content block, validates its `.input` payload through `DrafterOutput.model_validate`, and returns the resulting object.

**Consequences:** The schema is enforced by both Anthropic's server (the model is steered toward the declared shape) and pydantic at the boundary (the final `model_validate` is the gate). Adding a field to `DrafterOutput` automatically updates the tool schema — no second place to keep in sync. The system prompt ends with the literal "Always call the draft_resolution tool. Never respond outside it." as belt-and-suspenders. The cost is a slight coupling to the Anthropic tool-use API surface; if we ever wanted provider-portable code, this would need an adapter. That tradeoff is the right one for a glass-box prototype targeted at the Anthropic stack.

---

## ADR-011 — Drafter fails loud on parse errors; no retry, no degraded fallback

**Status:** Accepted (2026-05-19)

**Context:** When the Anthropic call comes back with a response the drafter cannot parse — no `tool_use` block, malformed input, pydantic validation failure — the service has to decide what to do. Three obvious paths: retry once with a softened prompt, fall back to returning a partial / text-only DrafterOutput, or raise immediately and let the caller decide. The audience requirement from [ADR-003](#adr-003--structural-only-confidence-model-no-llm-self-reported-confidence) carries here: the audience is liable for the output and cannot trust silent recovery from AI weirdness.

Alternatives considered: silent retry with a tightened prompt (rejected — masks a real signal that the prompt or schema is drifting from the model's behavior, and turns one bad call into two); construct a degraded DrafterOutput from the text portion of the response with `proposed_action=REQUEST_CLARIFICATION` (rejected — invents fields the model did not produce, which is the exact opposite of glass-box).

**Decision:** Any parse failure raises `DrafterParseError`, with the raw response attached on `.raw_response` so the audit log can record exactly what came back. The drafter does not retry, does not degrade, and does not catch. The triage caller (built in the next Phase 3 prompt) is responsible for deciding what to do — typically: route the bill to Escalate with `routing_key=UNCATEGORIZED`, attach the raw response to the audit entry, and surface the failure to the back-office queue. Retry/backoff for transient network errors is a scale-doc concern, not a prototype one.

**Consequences:** Every drafter failure is visible at the boundary, in the audit log, with the raw response preserved. A prompt drift or schema mismatch shows up the first time it happens, not muffled inside an exponential-backoff loop. The cost is that a single bad API response causes a single bill to escalate — which is exactly the operational behavior the back-office teams need to see, since "Claude did something unexpected" is precisely the moment a human review is warranted.

---

## ADR-012 — Approval applies the correction directly; it does not re-run validation

**Status:** Accepted (2026-05-19)

**Context:** The human approval endpoint (`POST /bills/{audit_ref}/approve`) takes a DraftForHumanReview bill, applies the drafter's `proposed_correction` to a copy of the original `raw_payload`, constructs a Reading, and writes it. The natural question: should the corrected bill be re-fed through the pipeline (normalize → reconcile → validate) before the write, so the AutoResolve invariants are re-asserted on the corrected shape? Three real options surfaced: (a) re-run the full pipeline on approval and only write if it now AutoResolves, (b) re-run validation only (skip normalize/reconcile because the meter is already matched), or (c) trust the approval and write the corrected Reading directly without re-validation.

Re-running has a specific failure mode for this pipeline: the corrected bill is, by construction, *also* the bill that just triggered DraftForHumanReview. If validation re-runs, it will flag the same issue again (e.g., UNIT_MISMATCH if the correction didn't touch the unit, GAP if the period hasn't shifted) and the corrected bill will loop straight back into DraftForHumanReview — which the human just approved out of. The approval endpoint would refuse the write and the human would have no way to land it. The pipeline would be fighting the human review it was designed to gate on. Re-running validation also implicitly asserts that the *only* legitimate post-correction state is AutoResolve, which collapses the entire glass-box model — the human is the gate, not validation.

Alternatives considered: re-run full pipeline (rejected, see above); re-run validation only with a "skip-original-flag" carve-out (rejected — the carve-out is an invented constraint with no clean spec home, and the carve-out logic is more likely to be wrong than the human's read).

**Decision:** Approval does NOT re-run validation. The endpoint merges `proposed_correction` into a copy of the original `raw_payload`, validates each correction key against the known reading-level field set (`period_start`, `period_end`, `usage`, `usage_units`, `cost`, `currency`, `demand_kw`, `demand_spend`, `energy_exported`), constructs a Reading from the corrected payload, and persists it directly with `source_mode=DRAFTER_APPROVED`. The follow-up `AuditEntry` records both the original and the corrected payloads, linked to the original through `parent_bill_external_ref`. A reviewer reconstructing the chain sees exactly what was changed and on whose authority.

**Consequences:** The human approval has teeth — once the reviewer hits approve, the corrected reading lands. The audit trail is intact: before-state, drafter proposal, after-state, and the parent linkage are all visible. The cost: the system trusts the human to read what they're approving (the drafter's `basis_note` and `confidence_note` exist exactly for this), and there is no automated re-check that the corrected unit matches the meter. If a future need arises to re-validate (e.g., a final "no overlaps" guard), that becomes a narrow check inside the approval handler — not a pipeline re-run. Approval and re-validation are decoupled; either can grow without entangling the other.

---

## ADR-013 — Fail-loud on missing ANTHROPIC_API_KEY; load via `.env`

**Status:** Accepted (2026-05-20)

**Context:** The drafter is the AI surface of the prototype and the entire reason DraftForHumanReview exists as a triage route. The original lifespan in [src/main.py](src/main.py) read `ANTHROPIC_API_KEY` from the environment and, when it was unset, booted the app anyway with a `_logger.warning(...)` and `set_drafter(None)`. Triage on a DraftForHumanReview route would then return `drafter_output=None`, and the demo harness would walk every bill without ever firing the human-approval prompt — a silent degradation, exactly the failure mode the rest of this build is engineered against. The trigger was observing this in practice: `python scripts/demo.py --interactive` produced `outcome=draft-no-output` for two of six bills because the uvicorn process had no key in its env, and the warning was buried in stdout.

Separately, the workflow forced the user to re-`export ANTHROPIC_API_KEY=...` in every new shell. Not the right ergonomics for a prototype meant to be re-run during walkthroughs.

Alternatives considered:
- **Keep the warning, surface the failure later (e.g., 500 on the first DraftForHumanReview request).** Rejected — late failure preserves the silent-degradation pathway for AutoResolve and Escalate routes, and for the walkthrough demo the failure surfaces in the middle of a narrated run instead of before it starts.
- **Set the key as a permanent Windows user environment variable** (`setx`). Rejected — works, but the key lives outside the repo, invisible to anyone who clones it, OS-specific, and pollutes the global env. Not the 12-factor idiom.
- **Use uvicorn's `--env-file` flag.** Rejected — only the uvicorn entry point sees it; scripts and any future Python entry that imports the app would not.

**Decision:** Two changes:

1. **Auto-load `.env`.** `src/main.py` calls `dotenv.load_dotenv()` once at module import, before the lifespan reads `os.environ`. The file is gitignored. Real OS env vars take precedence over `.env` values (the python-dotenv default `override=False`), so production-style deployments that inject secrets through the environment are unaffected. `python-dotenv` is added as a direct runtime dep in [pyproject.toml](pyproject.toml) even though it arrives transitively via `uvicorn[standard]`. (A `.env.example` template was briefly checked in alongside this change and then removed as duplication — the env surface is small enough that the README quick-start and the startup `RuntimeError` together name everything an operator needs.)
2. **Hard-fail on missing key.** The lifespan raises `RuntimeError("ANTHROPIC_API_KEY is not set ...")` instead of logging a warning. uvicorn refuses to start; the demo's `_check_health` then surfaces a clear "could not reach .../health" message. The `draft-no-output` outcome becomes mechanically unreachable.

Tests are unaffected because every `TestClient(app)` in `tests/` is constructed without a context manager and Starlette only fires the lifespan inside one — verified by inspection (no `with TestClient(app)` exists in the suite).

**Consequences:** The drafter's presence becomes a startup-time invariant, not a runtime hope. The user sets the key once per clone in `.env` and never again. Anyone who clones the repo and tries to run the prototype gets an unambiguous, actionable error message on the first `uvicorn` attempt — naming both the env var and the `.env` file by name. The cost: the prototype now refuses to boot without a real key, which is exactly the point. A future production extension that needs to disable the drafter (e.g., for read-only inspection, or a degraded-mode operator dashboard) would split the AutoResolve / Escalate code paths from the drafter wiring at the dependency level, not at the lifespan — but that is not in scope here.

---

## Spec gaps observed

Gaps in [DESIGN.md](DESIGN.md) that surfaced during a build step and required either a decision or a clarification before proceeding. Per the ambiguity-handling rule in [CLAUDE.md](CLAUDE.md), inventing on ambiguous spec is forbidden — any gap encountered is logged here, optionally accompanied by a `TODO` in the code at the point of contact.

When a gap is resolved (DESIGN.md updated, or a human decision recorded), leave the entry in place and mark it `(resolved in <ref>)` rather than deleting; the history of where the spec wasn't quite right is itself useful.

- **DDL nullability is looser than pydantic required-ness for several fields.** Encountered while building [src/db/store.py](src/db/store.py) for Prompt 3. The schema spec in the prompt declared `sites.region`, `sites.portfolio_id`, `meters.landlord_or_tenant`, `readings.currency`, and `audit_entries.bill_external_ref` as nullable (`TEXT` without `NOT NULL`), but the corresponding pydantic models in [src/models/entities.py](src/models/entities.py) and [src/models/audit.py](src/models/audit.py) require all of these. Resolution chosen without surfacing because the asymmetry is benign in practice: stores always populate from the strict pydantic models, so NULL never reaches these columns; if it ever does (direct DB manipulation, or a future code path bypassing the model), the pydantic read-path validates and fails loudly rather than silently coercing. The schema follows the prompt-spec'd DDL exactly; tightening to `NOT NULL` would require an explicit DESIGN.md update. Flagging here so a future reader notices the pattern rather than discovering it via a read-time validation error. **(resolved in this prompt — DESIGN.md §4 updated, schema tightened, signature formalized)**
- **`add_reading(reading: Reading)` has no place for `ingested_at`, `source_mode`, or `batch_id`.** Encountered while building [src/db/store.py](src/db/store.py) for Prompt 3. The Prompt 3 signature is exactly `add_reading(reading: Reading) -> int`, but the readings table per DESIGN.md §4 has three additional columns describing how the reading entered the system. Resolution: the public signature still accepts a Reading as its only positional argument, with `source_mode`, `batch_id`, and `ingested_at` accepted as keyword-only with safe defaults (`source_mode="FIXTURE"`, `batch_id=None`, `ingested_at=datetime.now(UTC)`). Pipeline writes will pass these explicitly when the ingestion handler is built in Phase 2; fixture seeding gets the defaults. **(resolved in this prompt — DESIGN.md §4 updated, schema tightened, signature formalized: `source_mode` is now keyword-required with no default; fixture seeding passes `"FIXTURE"` explicitly)**
- **`name_mismatch` validation check is unreachable under strict three-key reconciliation.** Encountered while building [src/services/validation.py](src/services/validation.py) for the Phase 2 finale. DESIGN.md §4 "Validation Layer" lists `name_mismatch` (incoming `site_name` vs the matched meter's site via FK) as a structural check. The current [src/services/reconciliation.py](src/services/reconciliation.py) calls `MeterHistoryStore.find_meter`, which joins on all three keys including site name — any site-name mismatch therefore produces an unmatched meter (`METER_UNASSIGNED`), not a matched-meter-with-flag case. Resolution: omit `name_mismatch` from the validation service with a docstring note pointing here; the routing it would have triggered (FORMAT_MISMATCH on Escalate) is covered by the `METER_UNASSIGNED` route. A future change to relax reconciliation (resolve by meter+account and surface a name disagreement instead of a miss) would restore the check. Surfacing rather than inventing per the ambiguity-handling rule.
- **`ReconciledBill.matched_account` extension to support `generation_mismatch`.** Encountered while building [src/services/validation.py](src/services/validation.py) for the Phase 2 finale. The `GENERATION_MISMATCH` check needs `Account.generation_account`, but the original `ReconciledBill` only carried `matched_meter` (a `Meter` with `account_id` but no account object). Resolution: extended `ReconciledBill` with `matched_account: Optional[Account]`, added `MeterHistoryStore.get_account(account_id)`, and updated `reconcile()` to fetch the parent account alongside the meter. DESIGN.md §4 "Reconciliation Service" is updated to name this. No ADR — it's a refinement within the existing "reconciliation attaches joined context to the bill" pattern, not a new architectural choice.
- **Four additional DDL columns are nullable where pydantic is required.** Surfaced during the spec-tightening pass that resolved the original "DDL nullability is looser than pydantic" gap. Beyond the five named columns the prompt fixed (`sites.region`, `sites.portfolio_id`, `meters.landlord_or_tenant`, `readings.currency`, `audit_entries.bill_external_ref`), the following columns are also nullable in [src/db/schema.sql](src/db/schema.sql) but required on their pydantic counterparts: `accounts.site_id`, `meters.account_id`, `readings.meter_id` (three foreign-key columns that should arguably be `NOT NULL REFERENCES …`), and `audit_entries.source_mode` (required on `AuditEntry.source_mode`). Not auto-fixed per the prompt's "don't auto-fix beyond the five named above" instruction. The same principle applies (pydantic is the contract source of truth; the schema should mirror it); a follow-up tightening pass would change these in one commit alongside a DESIGN.md note that FK columns are `NOT NULL` whenever the parent relationship is required.
- **No dedicated `gap` routing key in the escalation taxonomy.** Surfaced while building [scripts/demo.py](scripts/demo.py) and [WALKTHROUGH.md](WALKTHROUGH.md) for the canonical-bill harness. [DESIGN.md §4](DESIGN.md) enumerates six routing keys (`connect_integrity`, `meter_unassigned`, `overlap`, `format_mismatch`, `inactive_meter`, `uncategorized`); a HIGH `GAP` flag has no dedicated key and falls through to `UNCATEGORIZED` per the fall-through clause in `_HIGH_FLAG_TO_ROUTING_KEY` in [src/services/triage.py](src/services/triage.py). The Phase 3 demo prompt asked for a `gap` routing key on Bill 3; resolution: surface the gap honestly in WALKTHROUGH.md rather than inventing the key, and route to `UNCATEGORIZED` consistent with the design. A future revision that adds the deferred AutoEstimate route (cut from the prototype, treated in the scale-to-production doc) is the natural place to also extend the routing taxonomy with `gap` — until then the visible `uncategorized` bucket is doing exactly what DESIGN.md says it should: making weak spots in the rule set legible rather than hiding them.
- **Unknown providers do not raise a QualityFlag.** Surfaced while building [scripts/demo_bills.json](scripts/demo_bills.json) Bill 5. [src/services/normalization.py](src/services/normalization.py) emits `provider_known=False` in the structural-signal dict when the parsed provider alias is not in the reference library, but no `QualityFlag` is raised, so triage on an otherwise-clean bill from an unknown provider would AutoResolve with the signal visible only in the structured payload. The demo combines the unknown-provider meter with a unit mismatch so the bill routes to DraftForHumanReview anyway and the drafter sees `provider_known=False` in its context. A production extension would treat unknown providers as a MEDIUM flag and route through DraftForHumanReview directly, feeding the onboarding queue. Logged here rather than fixing because that change is a real new validation behavior and belongs in a separate prompt with its own ADR.
- **Regional rules: granularity and EU currency default.** Encountered while building [src/services/reference.py](src/services/reference.py) for Phase 2 Prompt 1. DESIGN.md §4 specifies "a regional ruleset (US, EU)" with two regions; the Phase 2 prompt asks for three (`US-East`, `US-West`, `EU`) so the provider library can group by service territory. DESIGN.md §4 also does not specify a default currency per region. Resolution: introduce a `ReferenceRegion` enum local to the reference layer with the three prompt-requested values, keep `models.entities.Region` (US / EU) as the coarser tag on a Site, and default the EU regional currency to GBP because all three EU provider entries in the curated library (British Gas, Thames Water, EDF Energy UK arm) bill in GBP. Decimal/date conventions: US-East and US-West share `.` and `MM/DD/YYYY`; EU is `,` and `DD/MM/YYYY`. If DESIGN.md is later tightened to specify per-region currency defaults, this entry resolves and the constants in `reference.py` re-sync to spec. **(resolved in this prompt — DESIGN.md §4 updated, schema tightened, signature formalized: the three-region granularity, the two-region coarser tag on Site, and the EU→GBP default are now spec)**
