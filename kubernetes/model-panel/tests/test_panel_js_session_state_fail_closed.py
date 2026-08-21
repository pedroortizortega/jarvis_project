"""Phase 13 / task 13.2: `panel.js` regression for the new
`backend_unreachable` / `unreachable` session states (F-2, design "Verified
Findings"). Pure string-membership check — this repo has no JS test
runner (same pattern as `test_static_metrics_ui.py`) — confirming
`sessionStateClass` still needs **zero code change**: it defaults to
`"bad"` for anything that isn't explicitly `valid`/`expiring_soon`/
`rate_limited`, so the new states render red with no JS edit required.
"""

from __future__ import annotations

import re
from pathlib import Path

PANEL_JS = (Path(__file__).resolve().parent.parent / "app" / "static" / "panel.js").read_text()


def _extract_function(js_source: str, name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\)\s*{{", js_source)
    assert match, f"{name} not found in panel.js"
    start = match.end() - 1
    depth = 0
    for i in range(start, len(js_source)):
        if js_source[i] == "{":
            depth += 1
        elif js_source[i] == "}":
            depth -= 1
            if depth == 0:
                return js_source[start : i + 1]
    raise AssertionError(f"unterminated function body for {name}")


def test_session_state_class_still_defaults_to_bad_for_unknown_states():
    body = _extract_function(PANEL_JS, "sessionStateClass")
    # The new states are never explicitly named — proving the allow-list
    # shape (explicit ok/warn states, implicit bad default) is unchanged.
    assert "backend_unreachable" not in body
    assert '"unreachable"' not in body and "'unreachable'" not in body
    assert 'return "bad"' in body or "return 'bad'" in body


def test_session_state_class_explicit_allow_list_unchanged():
    body = _extract_function(PANEL_JS, "sessionStateClass")
    assert '"valid"' in body
    assert '"expiring_soon"' in body
    assert '"rate_limited"' in body
