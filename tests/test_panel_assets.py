"""Sanity checks on the native panel's JS asset.

Pure text/syntax checks, no `homeassistant` needed — this is the one
piece of the panel that's cheap to guard without a browser: catches an
accidental truncation or a removed section (history/cost/alerts) that
`tests/test_component_smoke.py`'s "the route exists" check wouldn't."""
from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

PANEL_JS = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "idegis_astralpool"
    / "panel_assets"
    / "idegis-astralpool-panel.js"
)


def test_panel_js_defines_the_custom_element():
    text = PANEL_JS.read_text()
    assert 'customElements.define("idegis-astralpool-panel"' in text


@pytest.mark.parametrize(
    "needle",
    [
        "Active alerts",
        "24h history",
        "Filtration &amp; cost",
        "Recommended minutes today",
        "Last session (trusted averages)",
    ],
)
def test_panel_js_renders_expected_sections(needle):
    assert needle in PANEL_JS.read_text()


def test_panel_js_is_syntactically_valid():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available in this environment")
    result = subprocess.run(
        [node, "--check", str(PANEL_JS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
