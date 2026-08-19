"""Regression test: the three gauge element ids referenced by panel.js
must exist in index.html, and panel.js must actually call /api/metrics.
A pure string-membership check — this repo has no JS test runner — but it
catches the class of bug where an id gets renamed in one file and not the
other, which `render()`/`renderGauge()` would otherwise fail on silently
(getElementById returns null, .className throws)."""
from __future__ import annotations

from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
INDEX_HTML = (_APP_DIR / "templates" / "index.html").read_text()
PANEL_JS = (_APP_DIR / "static" / "panel.js").read_text()
PANEL_CSS = (_APP_DIR / "static" / "panel.css").read_text()

GAUGE_NAMES = ["cpu", "ram", "vram"]


def test_index_html_has_a_gauge_fill_and_value_element_per_metric():
    for name in GAUGE_NAMES:
        assert f'id="gauge-{name}-fill"' in INDEX_HTML
        assert f'id="gauge-{name}-value"' in INDEX_HTML


def test_panel_js_polls_metrics_endpoint():
    assert '"/api/metrics"' in PANEL_JS or "'/api/metrics'" in PANEL_JS


def test_panel_js_renders_each_gauge():
    assert '"gauge-${name}-fill"' in PANEL_JS or "'gauge-${name}-fill'" in PANEL_JS or "`gauge-${name}-fill`" in PANEL_JS
    assert '"gauge-${name}-value"' in PANEL_JS or "'gauge-${name}-value'" in PANEL_JS or "`gauge-${name}-value`" in PANEL_JS
    assert "renderGauge" in PANEL_JS


def test_panel_css_defines_gauge_state_classes():
    for state in ["ok", "warn", "bad", "unknown"]:
        assert f".gauge-fill.{state}" in PANEL_CSS
