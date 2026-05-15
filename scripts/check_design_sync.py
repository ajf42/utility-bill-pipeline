"""Structural drift guard between DESIGN.md and CLAUDE.md.

DESIGN.md §8 is the single source of truth for the build rules, coding
patterns, sample-scenario convention, anonymization rule, and commit
convention. CLAUDE.md may summarize and reference §8 — what it must not do
is duplicate a substantial run of §8 verbatim or near-verbatim, because
the two will drift the moment §8 is edited.

This check parses §8 out of DESIGN.md at runtime, normalizes both files
(case, whitespace, markdown formatting, smart quotes and dashes), and
slides a 12-word window over §8. If any window appears in CLAUDE.md after
the same normalization, the check fails. The window size is the
substantial-copy threshold: short phrases that legitimately co-occur
(section titles, references to §8, common terminology) pass through;
copies of one or more whole bullets do not.

Because the canaries are derived from DESIGN.md at runtime, the check
adapts automatically when §8 is edited — there is no hardcoded list to go
stale.

Usage (from the repo root):

    python scripts/check_design_sync.py

Exit 0 if clean, 1 if drift detected. Also wired into pytest at
``tests/test_design_sync.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN_MD = REPO_ROOT / "DESIGN.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

WINDOW = 12

_SMART_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"'}
_DASHES = {"—": " ", "–": " "}  # em, en -> space


def extract_section_8(design_md_text: str) -> str:
    """Slice DESIGN.md §8 out of the full document.

    Boundaries are the markdown headings ``^## 8.`` (start) and ``^## 9.``
    (end). Raises if either boundary is missing — that's a structural
    change the drift check should not silently accept.
    """

    match = re.search(
        r"^##\s+8\.\s.*?(?=^##\s+9\.)",
        design_md_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise RuntimeError(
            "Could not locate DESIGN.md §8 (expected '## 8. ...' heading "
            "followed by '## 9. ...'). DESIGN.md structure may have changed; "
            "update extract_section_8 in scripts/check_design_sync.py."
        )
    return match.group(0)


def _strip_markdown(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*+([^*]+)\*+", r"\1", text)
    text = re.sub(r"_+([^_]+)_+", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", " ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", " ", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#+\s*", " ", text, flags=re.MULTILINE)
    return text


def normalize(text: str) -> list[str]:
    """Lowercase, fold smart punctuation, strip markdown, tokenize.

    Tokens are runs of ASCII alphanumerics. Punctuation is dropped entirely
    so that "passing." and "passing" compare equal and so that markdown
    bullet/heading markers cannot survive into the token stream.
    """

    for smart, plain in _SMART_QUOTES.items():
        text = text.replace(smart, plain)
    for dash, plain in _DASHES.items():
        text = text.replace(dash, plain)
    text = _strip_markdown(text)
    text = text.lower()
    return re.findall(r"[a-z0-9]+", text)


def find_overlapping_spans(
    design_section: str,
    claude_text: str,
    window: int = WINDOW,
) -> list[tuple[int, int, str]]:
    """Find runs of ``design_section`` that appear verbatim in ``claude_text``.

    Both inputs go through :func:`normalize` before comparison. The return
    value is a list of ``(start_index, end_index, witness)`` tuples where
    indices are positions in the normalized §8 token list and ``witness``
    is the matched run joined by single spaces. Adjacent overlapping
    window hits are coalesced into the largest contiguous span.
    """

    design_tokens = normalize(design_section)
    claude_blob = " ".join(normalize(claude_text))

    if len(design_tokens) < window:
        return []

    hit_starts: list[int] = []
    for i in range(len(design_tokens) - window + 1):
        ngram = " ".join(design_tokens[i : i + window])
        if ngram in claude_blob:
            hit_starts.append(i)

    if not hit_starts:
        return []

    spans: list[tuple[int, int, str]] = []
    run_start = hit_starts[0]
    prev = hit_starts[0]
    for s in hit_starts[1:]:
        if s == prev + 1:
            prev = s
            continue
        end = prev + window
        spans.append((run_start, end, " ".join(design_tokens[run_start:end])))
        run_start = s
        prev = s
    end = prev + window
    spans.append((run_start, end, " ".join(design_tokens[run_start:end])))
    return spans


def main() -> int:
    if not DESIGN_MD.exists():
        print(f"ERROR: {DESIGN_MD} not found", file=sys.stderr)
        return 1
    if not CLAUDE_MD.exists():
        print(f"ERROR: {CLAUDE_MD} not found", file=sys.stderr)
        return 1

    section_8 = extract_section_8(DESIGN_MD.read_text(encoding="utf-8"))
    claude_text = CLAUDE_MD.read_text(encoding="utf-8")
    spans = find_overlapping_spans(section_8, claude_text)

    if spans:
        print("FAIL: CLAUDE.md contains substantial overlap with DESIGN.md §8.")
        print()
        print(
            f"Each span below is a contiguous run of {WINDOW}+ normalized words "
            "from DESIGN.md §8"
        )
        print("that also appears verbatim in CLAUDE.md after the same normalization.")
        print("Replace each with a short summary and a pointer to DESIGN.md §8.")
        print()
        for start, end, witness in spans:
            length = end - start
            print(f"  - {length}-word span: {witness!r}")
        return 1

    print(f"OK: CLAUDE.md contains no {WINDOW}+ word overlap with DESIGN.md §8.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
