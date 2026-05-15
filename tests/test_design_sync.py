"""Run the structural DESIGN.md <-> CLAUDE.md drift check as part of pytest.

See ``scripts/check_design_sync.py`` for the algorithm.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_design_sync.py"


def _load_check_module():
    spec = importlib.util.spec_from_file_location("check_design_sync", SCRIPT_PATH)
    assert spec and spec.loader, "could not load check_design_sync.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claude_md_has_no_structural_overlap_with_design_md_section_8():
    mod = _load_check_module()
    section_8 = mod.extract_section_8(mod.DESIGN_MD.read_text(encoding="utf-8"))
    claude_text = mod.CLAUDE_MD.read_text(encoding="utf-8")
    spans = mod.find_overlapping_spans(section_8, claude_text)
    assert spans == [], (
        f"CLAUDE.md contains {len(spans)} {mod.WINDOW}+ word span(s) of "
        "DESIGN.md §8 verbatim:\n  - "
        + "\n  - ".join(
            f"{end - start}-word: {witness!r}" for start, end, witness in spans
        )
    )


def test_finder_detects_synthetic_paraphrase_with_large_overlap():
    """Pin the structural behavior. A regression to the old cosmetic
    canary-list version of the check would not detect this synthetic copy
    (because the §8 phrase is being lifted into a different surrounding
    context, not paste-of-the-exact-canary-list), so this test would fail
    and surface the regression.
    """

    mod = _load_check_module()
    section_8 = mod.extract_section_8(mod.DESIGN_MD.read_text(encoding="utf-8"))

    synthetic_claude = (
        "# CLAUDE.md (synthetic, for test)\n\n"
        "## Build rules\n\n"
        "Some preamble that does not appear in DESIGN.md, followed by a "
        "verbatim lift: Sample scenarios are not isolated rows — they are "
        "multi-bill stories that demonstrate stateful behavior. To "
        "demonstrate gap detection, two bills on the same meter with a gap. "
        "That is the bug this test catches.\n"
    )

    spans = mod.find_overlapping_spans(section_8, synthetic_claude)
    assert spans, "expected the synthetic verbatim copy to be detected"
