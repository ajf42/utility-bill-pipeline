# Walkthrough — Six Canonical Bills

This document mirrors the demonstration harness in [scripts/demo.py](scripts/demo.py). Each section walks one canonical utility bill through the pipeline (ingest → normalize → reconcile → validate → triage → audit, with the human approval loop where applicable) and explains what the architecture is doing at that step. The six cases are deliberately chosen to span every triage route and every load-bearing decision in [DESIGN.md](DESIGN.md).

The intended reader is a process-and-operations leader, not an engineer. The system is the upstream stage of a utility ingestion pipeline: messy incoming data on the left, a triage decision and an audit receipt on the right, with a Claude-drafted resolution attached on the cases where a human reviewer is the right gate.

> **Note on captured outputs.** The drafter outputs shown below are illustrative captures from a sample run against the live Anthropic API. The natural-language fields (`basis_note`, `confidence_note`, `draft_email_body`) vary between runs — that is expected. The structured fields (`proposed_action`, `proposed_correction`, `draft_email_recipient_type`) are deterministic given the same input, the same system prompt, and the same meter context.

---

## Bill 1 — Baseline clean

The happy path. A normal monthly bill on a known meter resolves immediately, fires no high-severity flags, and routes to AutoResolve. This is the case the rest of the demo is the exception to.

```json
{
  "period_start": "2026-05-01",
  "period_end":   "2026-05-31",
  "usage":        30100,
  "usage_units":  "kWh",
  "currency":     "USD",
  "cost":         3612.0,
  "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
  "account_number":  "LT-ELEC-001",
  "site_name":       "Liberty Tower"
}
```

**Expected route:** `AUTO_RESOLVE`
**Flags:** none
**Outcome:** audit entry written; in production this is the path that would persist a reading to the readings table.

What the architecture is showing here: the pipeline's job is not to flag everything. The structural-quality model from [ADR-003](DECISIONS.md#adr-003--structural-only-confidence-model-no-llm-self-reported-confidence) is liberal about flagging when something looks off — but when nothing does, the bill flows through. Auditability does not require friction.

---

## Bill 2 — Unit mismatch (ccf on a kWh meter)

The same Liberty Tower main electric meter as Bill 1, but the upload row claims `ccf` for `usage_units`. The meter is locked to `kWh`. This is the demo highlight — the canonical fixable HIGH flag, where Claude as a drafting assistant earns its place in the stack.

```json
{
  "period_start": "2026-05-01",
  "period_end":   "2026-05-31",
  "usage":        30100,
  "usage_units":  "ccf",
  "currency":     "USD",
  "cost":         3612.0,
  "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M1)",
  "account_number":  "LT-ELEC-001",
  "site_name":       "Liberty Tower"
}
```

**Expected route:** `DRAFT_FOR_HUMAN_REVIEW`
**Flags:** `UNIT_MISMATCH` (HIGH)
**Outcome (auto-approve mode):** the drafter's `proposed_correction` is applied; a reading is written with `source_mode=DRAFTER_APPROVED` and a follow-up audit entry is linked to the original via `parent_bill_external_ref`.

### Drafter output (captured)

```
proposed_action       : CONVERT_UNIT
proposed_correction   : {"usage_units": "kWh"}
email recipient       : INTERNAL_TEAM
email subject         : Likely unit-label error on ConEd bill for Liberty Tower, May 2026

basis_note:
    The matched meter MSR.(ConEd)(LT-ELEC-001):(M1) is locked to kWh and
    its six most recent monthly readings (Sep 2025 - Apr 2026) report
    usage between 28,400 and 35,200 kWh. The incoming row reports 30,100
    units, which is consistent with the historical kWh range but is
    labeled "ccf." ConEd is a known provider in the reference library and
    reports electric usage in kWh on every prior reading. The most likely
    explanation is a label-only error on the upload row; the underlying
    numeric value is plausible as-is. CONVERT_UNIT proposes a relabel
    rather than a numeric conversion.

confidence_note:
    Assumes the numeric value 30,100 is correct and only the units label is
    wrong; the email body asks the upload owner to confirm before approval.

draft email body:
    --------------------------------------------------------------
    Hi team,

    A reading came in for Liberty Tower's main electric meter
    (MSR.(ConEd)(LT-ELEC-001):(M1)) covering May 1 - May 31, 2026 with
    usage labeled as 30,100 ccf. The meter is locked to kWh in the
    reference store and the last six monthly readings on this meter
    report between 28,400 and 35,200 kWh, so the numeric value is
    consistent with the meter's history. The "ccf" label is the part
    that does not fit.

    Before we approve the relabel from ccf to kWh, could you confirm
    that this was a labeling error on the upload row rather than a
    genuine change in how the meter is being read? If it was a label
    error, we can apply the correction and the May reading will land
    cleanly. If something else is going on, please flag and we will
    pause this row.

    Thanks,
    Back-office team
    --------------------------------------------------------------
```

What the architecture is showing here: the drafter does not decide. It proposes. The `proposed_correction` field is the machine-applicable part — the system applies it iff a human approves. The `basis_note` is the receipt the reviewer reads. The `confidence_note` is the place the model has to admit what it is unsure about. Per [ADR-011](DECISIONS.md#adr-011--drafter-fails-loud-on-parse-errors-no-retry-no-degraded-fallback) the drafter fails loud rather than degrading silently; per [ADR-012](DECISIONS.md#adr-012--approval-applies-the-correction-directly-it-does-not-re-run-validation) approval applies the correction directly rather than re-running validation (which would loop back into the same flag).

---

## Bill 3 — Gap detected

A bill arrives for July 2026 on Liberty Tower's secondary electric meter (M2), whose most recent reading ended on 2026-04-30. The ~62-day gap exceeds the high-severity threshold of seven days. Gap detection is the first heuristic that exercises real stateful behavior — the validation service had to look at this meter's prior readings to even notice.

```json
{
  "period_start": "2026-07-01",
  "period_end":   "2026-07-31",
  "usage":        5800,
  "usage_units":  "kWh",
  "currency":     "USD",
  "cost":         696.0,
  "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(M2)",
  "account_number":  "LT-ELEC-001",
  "site_name":       "Liberty Tower"
}
```

**Expected route:** `ESCALATE`
**Flags:** `GAP` (HIGH)
**Routing key:** `UNCATEGORIZED` *(see "Spec gap" note below)*
**Outcome:** escalation. In production this would route to the back-office data-quality queue. The deferred AutoEstimate route (cut from the prototype per [DESIGN.md §5](DESIGN.md), treated in the scale-to-production document) is what handles fillable gaps with a statistical estimate that the operator one-click approves.

**Spec gap.** [DESIGN.md §4](DESIGN.md) names six routing keys: `connect_integrity`, `meter_unassigned`, `overlap`, `format_mismatch`, `inactive_meter`, `uncategorized`. There is no dedicated `gap` routing key — gap escalations fall into the visible `uncategorized` bucket, which by design is left visible so weak spots in the rule set surface to the back-office team rather than being silently swallowed. A future revision that adds AutoEstimate would also extend the routing taxonomy with a `gap` key. Logged in [DECISIONS.md "Spec gaps observed"](DECISIONS.md#spec-gaps-observed).

What the architecture is showing here: stateful detection requires the reconciliation step. The pipeline could not flag this gap without first having matched the meter and pulled its prior context — which is exactly why reconciliation is its own pipeline stage rather than a side effect of validation.

---

## Bill 4 — Overlap

Pacific Plaza's main electric meter has an April 2026 reading on file (period 04-01 to 04-30). A new bill arrives for the period 04-15 to 05-14. The two overlap by sixteen days. Any non-empty overlap is high-severity.

```json
{
  "period_start": "2026-04-15",
  "period_end":   "2026-05-14",
  "usage":        27500,
  "usage_units":  "kWh",
  "currency":     "USD",
  "cost":         4675.0,
  "meter_id_string": "MSR.(PGE)(PP-ELEC-001):(M1)",
  "account_number":  "PP-ELEC-001",
  "site_name":       "Pacific Plaza"
}
```

**Expected route:** `ESCALATE`
**Flags:** `OVERLAP` (HIGH)
**Routing key:** `OVERLAP`
**Outcome:** escalation. In production this would route to the data-quality queue with a clear note that a prior reading covers part of this period; reconciliation requires picking one.

What the architecture is showing here: overlap is high-severity by default because two readings claiming the same period for the same meter break Measurabl's downstream invariants — the canonical readings table is append-only, and overlapping rows would propagate into ENERGY STAR submissions, GRESB packets, and customer dashboards. Auto-applying a correction to either reading would silently rewrite history; escalation is the only safe move.

---

## Bill 5 — Unknown provider + unit mismatch

A small natural-gas meter on Liberty Tower's gas account is billed by "GreenfieldCoop," a provider name that is not in the reference library. The bill also reports `kWh` against a `therms`-locked meter. The unit mismatch routes to DraftForHumanReview; the drafter sees the unknown provider in its context and can flag the onboarding need in its `basis_note`.

```json
{
  "period_start": "2026-05-01",
  "period_end":   "2026-05-31",
  "usage":        620,
  "usage_units":  "kWh",
  "currency":     "USD",
  "cost":         806.0,
  "meter_id_string": "MSR.(GreenfieldCoop)(LT-GAS-002):(M2)",
  "account_number":  "LT-GAS-002",
  "site_name":       "Liberty Tower"
}
```

**Expected route:** `DRAFT_FOR_HUMAN_REVIEW`
**Flags:** `UNIT_MISMATCH` (HIGH)
**Outcome (auto-approve mode):** drafter proposes a unit relabel; on approval, a reading lands with `source_mode=DRAFTER_APPROVED` and the unknown-provider note in the audit trail.

### Drafter output (captured)

```
proposed_action       : REQUEST_CLARIFICATION
proposed_correction   : (empty - external info needed)
email recipient       : PROPERTY_MANAGER
email subject         : Provider "GreenfieldCoop" and unit mismatch on Liberty Tower gas meter

basis_note:
    Meter MSR.(GreenfieldCoop)(LT-GAS-002):(M2) is locked to therms and
    the last four monthly readings on this meter are in the 720-1,800
    therms range. The incoming row reports 620 in kWh, which does not
    match the meter's locked unit. The provider alias "GreenfieldCoop"
    parsed from the meter ID is not present in the reference library;
    this is a small regional supplier that the prototype has not yet
    onboarded. Because both the unit and the provider need a human
    judgment call (the relabel may not be a simple ccf->therms case,
    and the onboarding entry needs a typical-units decision), the
    drafter is requesting clarification rather than proposing a
    machine-applicable correction.

confidence_note:
    Two open questions -- (1) is the 620 figure a misreport in kWh or a
    correctly-quantified value in therms, and (2) does GreenfieldCoop bill
    in therms or in ccf -- both need a property-manager-level answer.

draft email body:
    --------------------------------------------------------------
    Hello,

    We received a reading for the Liberty Tower gas meter billed by
    GreenfieldCoop covering May 2026. The upload row reports 620 kWh,
    but the meter on file is configured for therms and prior readings
    from January through April are between 720 and 1,800 therms.

    Two questions:

    1. Is the 620 value correct as-is and labeled wrong (should be
       therms), or was the reading taken in different units that
       happened to be entered as kWh?

    2. We do not yet have GreenfieldCoop in our provider reference
       library. Could you confirm whether they bill in therms (which
       is what our meter is set to) or in ccf? This affects how we
       handle future bills from them automatically.

    If you can confirm both items we can land this reading and add
    GreenfieldCoop to the onboarding queue for the next library refresh.

    Thanks,
    Back-office team
    --------------------------------------------------------------
```

What the architecture is showing here: the drafter is honest about what it cannot fix without a human. `proposed_correction` is left empty deliberately — there is no safe machine-applicable answer because the unit is one ambiguity and the unknown provider is another, and either one alone is not enough information to settle the bill. The email is the action; the system waits.

**Spec gap.** The current pipeline emits `provider_known=False` in the normalization service's structural-signal dict but does not raise a `QualityFlag` for it. So an unknown provider on a *clean* bill (everything else valid) AutoResolves with the signal visible only in the structured payload. Logged in [DECISIONS.md "Spec gaps observed"](DECISIONS.md#spec-gaps-observed). A production extension would treat unknown providers as a MEDIUM flag and route through DraftForHumanReview so the onboarding queue is fed directly.

---

## Bill 6 — Inactive meter

A bill comes in for a meter that exists in the store but is flagged inactive. `INACTIVE_METER` is high-severity and not fixable by the drafter — either the bill is misrouted (it should hit a different meter) or the meter needs reactivation. Either path is a human decision.

```json
{
  "period_start": "2026-05-01",
  "period_end":   "2026-05-31",
  "usage":        28900,
  "usage_units":  "kWh",
  "currency":     "USD",
  "cost":         3468.0,
  "meter_id_string": "MSR.(ConEd)(LT-ELEC-001):(OLD-M0)",
  "account_number":  "LT-ELEC-001",
  "site_name":       "Liberty Tower"
}
```

**Expected route:** `ESCALATE`
**Flags:** `INACTIVE_METER` (HIGH); a co-firing `GAP` HIGH is also present because the meter's last reading is from June 2024.
**Routing key:** `INACTIVE_METER`
**Outcome:** escalation. In production this would route to the account-status queue.

What the architecture is showing here: not every issue is fixable by the model, and the system is honest about that. The routing key is the operationally meaningful signal — "send this to the team that owns inactive-meter resolution" — not a generic "something went wrong." Per [ADR-004](DECISIONS.md#adr-004--three-route-triage-autoresolve--draftforhumanreview--escalate) every escalation carries a routing key for exactly this reason: Matt's back-office team is multiple teams, and the routing has to mean something on its way out.

---

## Summary

| # | Label                                          | Route                  | Flags | Outcome                                        |
|---|------------------------------------------------|------------------------|-------|------------------------------------------------|
| 1 | Baseline clean                                 | AUTO_RESOLVE           | 0     | audit recorded                                 |
| 2 | Unit mismatch (ccf on a kWh meter)             | DRAFT_FOR_HUMAN_REVIEW | 1     | drafter proposes relabel; approved → reading   |
| 3 | Gap detected                                   | ESCALATE               | 1     | escalated → UNCATEGORIZED (no `gap` key yet)   |
| 4 | Overlap                                        | ESCALATE               | 1     | escalated → OVERLAP                            |
| 5 | Unknown provider + unit mismatch               | DRAFT_FOR_HUMAN_REVIEW | 1     | drafter requests clarification (no auto-fix)   |
| 6 | Inactive meter                                 | ESCALATE               | 2     | escalated → INACTIVE_METER                     |

Every row above has a corresponding `AuditEntry` row in SQLite, queryable by `bill_external_ref`. On approved DraftForHumanReview cases, a second linked entry records the approval, the applied correction, and the resulting `reading_id`; the link is `AuditEntry.parent_bill_external_ref`. The audit trail is the deliverable Matt's teams reconstruct against months later.

---

## What this demo is not

This is the **architecture**, not the production capability surface. The prototype is twenty focused hours against a deliberate cut list, and the cuts are catalogued in DESIGN.md §5: PDF extraction, AutoEstimate, the statistical-anomaly heuristic, intermittent-meter detection, the full XLSX template parser with conditional optionality, the cross-extraction-agreement confidence model, the tariff-aware reference store, and the AI-driven reference-library expansion engine. Each of those is named in the scale-to-production companion document with the architectural treatment it would receive there.

The prototype's job is to prove the spine: messy input on the left, structured triage with audit on the right, and a Claude call sitting exactly where it earns its place — not as a decision-maker, but as a drafting assistant gated by human review. The methodology artifacts ([CLAUDE.md](CLAUDE.md), [DECISIONS.md](DECISIONS.md), [TASKS.md](TASKS.md)) document how that spine was reasoned about — they are not deliverables-for-show, they are the operational tooling a distributed engineering team would actually use to maintain and extend this system.
