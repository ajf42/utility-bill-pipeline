"""Drift guard between DESIGN.md and CLAUDE.md.

DESIGN.md §8 is the single source of truth for the build rules, coding
patterns, sample-scenario convention, anonymization rule, and commit
convention. CLAUDE.md may summarize and reference §8, but must not
duplicate it verbatim — if it does, the two files drift silently the
moment §8 is edited.

This check encodes a list of canary phrases drawn from DESIGN.md §8 that
are distinctive enough that a working-memory summary would not legitimately
contain them verbatim. If any canary appears in CLAUDE.md, exit 1 with a
report. Otherwise exit 0.

Usage (from the repo root):

    python scripts/check_design_sync.py

The same check is also wired into pytest at ``tests/test_design_sync.py``,
so any ``pytest`` invocation that picks up the tests directory will run it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# Distinctive phrases from DESIGN.md §8. Each is long enough that a paraphrase
# or summary would not produce it by accident. Order is not significant.
CANARIES: list[str] = [
    "DECISIONS.md updated if the change involved an architectural or engineering decision",
    "CLAUDE.md updated if the change introduced new files, endpoints, services, or rules",
    "Do not consider a task complete until all eight steps are done.",
    "Dependency injection via FastAPI's `Depends`. Constructor injection in service classes.",
    "Thin controllers: route handlers accept the request, call a service, return the result.",
    "Services are stateless. State lives in dependency-injected stores.",
    "Error handling: services raise exceptions; route handlers catch and map to HTTP responses.",
    "Async wherever I/O happens, especially Anthropic API calls.",
    "Pydantic models for all DTOs and internal data passing.",
    "Sample scenarios are not isolated rows — they are multi-bill stories that demonstrate stateful behavior.",
    "If real utility bills are referenced in fixtures, all PII (account numbers, customer names, addresses) is stripped before being committed.",
]


def find_canaries(claude_md_text: str) -> list[str]:
    """Return the subset of canary phrases that appear verbatim in CLAUDE.md."""

    return [phrase for phrase in CANARIES if phrase in claude_md_text]


def main() -> int:
    if not CLAUDE_MD.exists():
        print(f"ERROR: {CLAUDE_MD} not found", file=sys.stderr)
        return 1

    text = CLAUDE_MD.read_text(encoding="utf-8")
    matches = find_canaries(text)

    if matches:
        print("FAIL: CLAUDE.md duplicates DESIGN.md §8 verbatim.")
        print()
        print("The following canary phrases are the source of truth in DESIGN.md")
        print("and must not be copied into CLAUDE.md (summary + pointer only).")
        print()
        for phrase in matches:
            print(f"  - {phrase!r}")
        print()
        print("Replace each verbatim occurrence with a short summary that")
        print("points to DESIGN.md §8. See the 'Build rules (summary)' section")
        print("of CLAUDE.md for the established pattern.")
        return 1

    print("OK: CLAUDE.md contains no verbatim DESIGN.md §8 canary phrases.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
