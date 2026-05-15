"""Run the DESIGN.md <-> CLAUDE.md drift check as part of pytest.

See ``scripts/check_design_sync.py`` for what is checked and why.
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


def test_claude_md_contains_no_design_md_section_8_canaries():
    mod = _load_check_module()
    text = mod.CLAUDE_MD.read_text(encoding="utf-8")
    matches = mod.find_canaries(text)
    assert matches == [], (
        "CLAUDE.md duplicates DESIGN.md §8 verbatim. Offending phrases:\n  - "
        + "\n  - ".join(matches)
    )
