"""Smoke tests that actually import/run the custom_component under a real
Home Assistant core, using pytest-homeassistant-custom-component.

Until this file existed, nothing in CI ever imported
``custom_components/idegis_astralpool/*.py`` — only manifest.json's shape
was checked (tests/validate_repo.py) and the Python source was never
executed against real `homeassistant` APIs. That's exactly the gap that
let the old prototype's `coordinator.host`/`coordinator.port` references
linger in sensor.py/binary_sensor.py after the coordinator stopped having
those attributes (caught by hand during this rewrite, not by CI).

Needs the `homeassistant` + `pytest-homeassistant-custom-component`
packages, which is why this file is skipped unless they're installed
(see tests/validate_repo.py and docs/09-roadmap.md — CI installs them in
a dedicated job; locally this degrades to a skip rather than a failure).
"""
from __future__ import annotations

import pathlib
import shutil

import pytest

pytest.importorskip("homeassistant")
pytest_homeassistant = pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT_SRC = ROOT / "custom_components" / "idegis_astralpool"
DOMAIN = "idegis_astralpool"


@pytest.fixture(autouse=True)
def _link_custom_component(hass):  # noqa: ANN001
    """pytest-homeassistant-custom-component looks for custom_components/
    under the test's working dir; symlink ours in for the duration of the
    test so `hass.config_entries.async_setup` can find the integration."""
    target = pathlib.Path(hass.config.config_dir) / "custom_components" / DOMAIN
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            target.symlink_to(COMPONENT_SRC, target_is_directory=True)
        except OSError:
            shutil.copytree(COMPONENT_SRC, target)
    yield


async def test_config_flow_creates_entry(hass):  # noqa: ANN001
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result2["type"] == "create_entry"
    assert result2["data"]["mode"] == "cloud_mitm"


async def test_setup_entry_registers_views_and_entities(hass):  # noqa: ANN001
    entry = MockConfigEntry(domain=DOMAIN, data={"mode": "cloud_mitm"})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.idegis_chlorinator_online") is not None
    assert hass.states.get("sensor.idegis_chlorinator_last_seen") is not None

    found = [
        r for r in hass.http.app.router.routes()
        if "idegis_astralpool/interface" in str(r.resource)
    ]
    assert found, "cloud-MITM receiver views were not registered"

    panel_js_routes = [
        r for r in hass.http.app.router.routes()
        if "idegis_astralpool/panel" in str(r.resource)
    ]
    assert panel_js_routes, "panel JS view was not registered"
    assert "idegis_astralpool" in hass.data.get("frontend_panels", {})


async def test_unload_entry(hass):  # noqa: ANN001
    entry = MockConfigEntry(domain=DOMAIN, data={"mode": "cloud_mitm"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
