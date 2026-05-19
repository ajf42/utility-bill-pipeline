"""Canonical-bill demonstration harness.

Walks six curated bills through the running pipeline (ingest -> normalize
-> reconcile -> validate -> triage -> audit; with the human approval loop
on DraftForHumanReview cases) and prints the result in a form suitable
for a non-engineering reader.

This is the artifact for the May 26 walkthrough. The terminal output is
the show; ``WALKTHROUGH.md`` mirrors the same six cases as a standalone
portfolio document.

Usage:

    # Hands-off mode -- approves every drafted resolution automatically.
    python scripts/demo.py --auto-approve

    # Interactive mode -- pauses at each DraftForHumanReview case.
    python scripts/demo.py --interactive

Both modes assume:
- ``uvicorn src.main:app`` is running on ``http://localhost:8000``.
- ``ANTHROPIC_API_KEY`` is set in the uvicorn process (the demo itself
  does not call the API; the running app does).

The script resets the SQLite DB to a fresh fixture state at the start
(via ``python -m src.db.seed --reset``) so re-runs are deterministic
modulo the drafter's natural-language variability.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import httpx
except ImportError as exc:  # pragma: no cover - environment guard
    sys.stderr.write(
        "ERROR: httpx is required to run the demo. Install with `pip install httpx` "
        "or `pip install -e .[dev]`.\n"
    )
    raise SystemExit(1) from exc


BASE_URL = "http://localhost:8000"
DEMO_BILLS_PATH = Path(__file__).resolve().parent / "demo_bills.json"
TIMEOUT_SECONDS = 60.0  # Drafter calls run against the live API.


# Routing-key narrative — "which queue would this hit in production." The
# escalation taxonomy comes from DESIGN.md §4 "Triage Service"; this map
# turns the routing key into an operations-team-level sentence for the
# walkthrough audience.
_ROUTING_KEY_QUEUE_NOTE: dict[str, str] = {
    "METER_UNASSIGNED": (
        "onboarding queue (the meter triple did not resolve; needs site/meter "
        "assignment before a reading can land)"
    ),
    "OVERLAP": (
        "data-quality queue (a prior reading already covers part of this "
        "period; reconciliation requires picking one)"
    ),
    "INACTIVE_METER": (
        "account-status queue (the meter is marked inactive; either the bill "
        "is misrouted or the meter needs reactivation)"
    ),
    "FORMAT_MISMATCH": (
        "format-correction queue (currency / name / generation flag disagrees "
        "with the meter; the back-office team reconciles)"
    ),
    "CONNECT_INTEGRITY": (
        "Connect-integrity queue (would surface in production from the "
        "Connect-mode sync layer; not exercised by the prototype)"
    ),
    "DRAFTER_FAILURE": (
        "AI-failure queue (the drafter call could not be parsed; a reviewer "
        "looks at the raw response and the bill together)"
    ),
    "UNCATEGORIZED": (
        "the explicitly-visible uncategorized bucket -- per DESIGN.md, "
        "this bucket is left visible so weak spots in the rule set are seen, "
        "not hidden"
    ),
}


# ---------------------------------------------------------------------------
# Terminal formatting helpers
# ---------------------------------------------------------------------------


def _banner(label: str, narrative: str) -> None:
    bar = "=" * max(72, len(label) + 4)
    print()
    print(bar)
    print(f"  {label}")
    print(bar)
    print(f"  {narrative}")
    print()


def _print_raw_input(bill: dict[str, Any]) -> None:
    print("Raw input")
    print("---------")
    for key, value in bill.items():
        print(f"  {key:<18} {value}")
    print()


def _print_flags(flags: list[dict[str, Any]]) -> None:
    if not flags:
        print("Flags:        (none)")
        return
    print("Flags:")
    for flag in flags:
        print(f"  - [{flag['severity']:6}] {flag['type']}: {flag['description']}")


def _print_drafter_output(drafter: dict[str, Any]) -> None:
    print()
    print("Drafter output")
    print("--------------")
    print(f"  proposed_action       : {drafter['proposed_action']}")
    correction = drafter.get("proposed_correction") or {}
    print(f"  proposed_correction   : {correction or '(empty - external info needed)'}")
    print(f"  email recipient       : {drafter['draft_email_recipient_type']}")
    print(f"  email subject         : {drafter['draft_email_subject']}")
    print()
    print("  basis_note:")
    for line in _wrap(drafter["basis_note"], width=70, indent="    "):
        print(line)
    print()
    print("  confidence_note:")
    for line in _wrap(drafter["confidence_note"], width=70, indent="    "):
        print(line)
    print()
    print("  draft email body:")
    print("    " + "-" * 66)
    for line in _wrap(drafter["draft_email_body"], width=70, indent="    "):
        print(line)
    print("    " + "-" * 66)
    print()


def _wrap(text: str, *, width: int, indent: str) -> list[str]:
    import textwrap

    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        wrapped = textwrap.wrap(paragraph, width=width) or [""]
        for w in wrapped:
            lines.append(f"{indent}{w}")
    return lines


# ---------------------------------------------------------------------------
# Demo execution
# ---------------------------------------------------------------------------


def _reset_database() -> None:
    """Drop and re-seed the prototype DB via the existing seed CLI.

    Idempotent and explicit: the seed script's ``--reset`` flag deletes
    the DB file before re-seeding, so each demo run starts from a known
    fixture state. The subprocess inherits the working directory; the
    seed script writes to ``./prototype.db`` unless ``DB_PATH`` is set.
    """

    print("Resetting prototype DB to fixture state...")
    result = subprocess.run(
        [sys.executable, "-m", "src.db.seed", "--reset"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit("ERROR: seed script failed; aborting demo.")
    print(result.stdout.strip())
    print()


def _check_health(client: httpx.Client) -> None:
    try:
        response = client.get("/health", timeout=5.0)
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"ERROR: could not reach {BASE_URL}/health -- is uvicorn running?\n"
            f"Start it with:  uvicorn src.main:app --reload\n"
            f"Underlying error: {exc}"
        ) from exc
    if response.status_code != 200:
        raise SystemExit(
            f"ERROR: {BASE_URL}/health returned {response.status_code}; "
            "expected 200. Is the app healthy?"
        )


def _decide_action(interactive: bool) -> str:
    """Return one of: 'approve', 'reject', 'skip'.

    Interactive mode prompts; auto-approve mode always approves. The
    bare-enter default in interactive mode is Approve so the
    walkthrough hand-flows quickly when nothing surprises the operator.
    """

    if not interactive:
        return "approve"
    while True:
        choice = input("  [A]pprove / [R]eject / [S]kip  (Enter=Approve): ").strip().lower()
        if choice in {"", "a", "approve"}:
            return "approve"
        if choice in {"r", "reject"}:
            return "reject"
        if choice in {"s", "skip"}:
            return "skip"
        print("  (unrecognized choice; please type A, R, or S)")


def _run_one_bill(
    client: httpx.Client,
    case: dict[str, Any],
    *,
    interactive: bool,
) -> dict[str, Any]:
    """Post one canonical bill, narrate the result, and optionally act on
    DraftForHumanReview cases. Returns a summary dict for the closing table.
    """

    label = case["label"]
    narrative = case["narrative"]
    bill = case["bill"]

    _banner(label, narrative)
    _print_raw_input(bill)

    response = client.post("/bills", json=bill, timeout=TIMEOUT_SECONDS)
    if response.status_code != 200:
        print(f"  ! POST /bills returned {response.status_code}: {response.text}")
        return {
            "label": label,
            "route": "ERROR",
            "flag_count": 0,
            "outcome": f"HTTP {response.status_code}",
            "audit_ref": "-",
        }

    body = response.json()
    audit_ref = body["audit_ref"]
    triage = body["triage"]
    flags = body["validated"]["flags"]
    route = triage["route"]

    print(f"Route:        {route}")
    if triage.get("routing_key"):
        print(f"Routing key:  {triage['routing_key']}")
    _print_flags(flags)
    print(f"Audit ref:    {audit_ref}")

    outcome: str
    if route == "DRAFT_FOR_HUMAN_REVIEW":
        drafter_output = triage.get("drafter_output")
        if drafter_output is None:
            print(
                "\n  ! No drafter_output present. Is ANTHROPIC_API_KEY set in the "
                "uvicorn process?"
            )
            outcome = "draft-no-output"
        else:
            _print_drafter_output(drafter_output)
            action = _decide_action(interactive)
            outcome = _handle_review_action(client, audit_ref, action)
    elif route == "ESCALATE":
        routing_key = triage.get("routing_key", "UNCATEGORIZED")
        note = _ROUTING_KEY_QUEUE_NOTE.get(routing_key, "(no queue note registered)")
        print(f"\n  In production this would route to: {note}")
        outcome = f"escalated:{routing_key}"
    elif route == "AUTO_RESOLVE":
        # The prototype's AutoResolve path does not persist to the
        # readings table -- that wiring lands with Prompt 5. We surface
        # the route and let the audit entry stand as the receipt.
        outcome = "auto-resolved (audit recorded; no reading write in this prompt)"
        print(f"\n  Outcome: {outcome}")
    else:
        outcome = f"unknown-route:{route}"

    return {
        "label": label,
        "route": route,
        "flag_count": len(flags),
        "outcome": outcome,
        "audit_ref": audit_ref,
    }


def _handle_review_action(client: httpx.Client, audit_ref: str, action: str) -> str:
    if action == "approve":
        response = client.post(f"/bills/{audit_ref}/approve", timeout=TIMEOUT_SECONDS)
        if response.status_code != 200:
            print(f"  ! approve failed: {response.status_code} {response.text}")
            return f"approve-failed:{response.status_code}"
        body = response.json()
        print(
            f"  Approved. reading_id={body['reading_id']}  "
            f"follow-up audit_ref={body['audit_ref']}"
        )
        return f"approved (reading_id={body['reading_id']})"
    if action == "reject":
        response = client.post(
            f"/bills/{audit_ref}/reject",
            json={"rejection_reason": "demo: operator rejected drafted resolution"},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            print(f"  ! reject failed: {response.status_code} {response.text}")
            return f"reject-failed:{response.status_code}"
        body = response.json()
        print(f"  Rejected. follow-up audit_ref={body['audit_ref']}")
        return "rejected"
    print("  Skipped (no audit entry written by demo).")
    return "skipped"


def _print_summary(rows: list[dict[str, Any]]) -> None:
    print()
    print("=" * 110)
    print("Summary")
    print("=" * 110)
    headers = ("#", "Label", "Route", "Flags", "Outcome", "Audit ref")
    print(
        f"{headers[0]:<3} {headers[1]:<42} {headers[2]:<24} {headers[3]:<6} "
        f"{headers[4]:<36} {headers[5]}"
    )
    print("-" * 110)
    for i, row in enumerate(rows, start=1):
        print(
            f"{i:<3} {row['label'][:42]:<42} {row['route'][:24]:<24} "
            f"{row['flag_count']:<6} {row['outcome'][:36]:<36} {row['audit_ref'][:8]}"
        )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--auto-approve",
        action="store_true",
        help="Approve every DraftForHumanReview case automatically.",
    )
    mode.add_argument(
        "--interactive",
        action="store_true",
        help="Pause for [A]pprove/[R]eject/[S]kip on each DraftForHumanReview case.",
    )
    args = parser.parse_args(argv)

    cases = json.loads(DEMO_BILLS_PATH.read_text(encoding="utf-8"))

    _reset_database()

    with httpx.Client(base_url=BASE_URL) as client:
        _check_health(client)
        rows = [
            _run_one_bill(client, case, interactive=args.interactive)
            for case in cases
        ]

    _print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
