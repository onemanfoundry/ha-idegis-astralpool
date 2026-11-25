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


async def test_options_flow_saves_alert_config(hass):  # noqa: ANN001
    entry = MockConfigEntry(domain=DOMAIN, data={"mode": "cloud_mitm"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "ph_enabled": True,
            "ph_min": 7.0,
            "ph_max": 7.6,
            "salinity_enabled": False,
            "salinity_min": 1.0,
            "salinity_max": 4.0,
            "session_stale_enabled": True,
            "session_stale_minutes": 60,
            "filtration_enabled": False,
            "filtration_min_coverage_pct": 70,
            "notify_service": "persistent_notification.create",
        },
    )
    await hass.async_block_till_done()
    assert result2["type"] == "create_entry"
    assert entry.options["ph_enabled"] is True
    assert entry.options["notify_service"] == "persistent_notification.create"


async def test_alert_dispatches_to_notify_service(hass):  # noqa: ANN001
    """End-to-end: a pH reading out of range, once alerts are enabled
    and routed to a real HA notify service, must actually create a
    persistent_notification — not just evaluate true in isolation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mode": "cloud_mitm"},
        options={
            "ph_enabled": True,
            "ph_min": 7.0,
            "ph_max": 7.6,
            "notify_service": "persistent_notification.create",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.idegis_astralpool.const import DOMAIN as IDEGIS_DOMAIN

    coordinator = hass.data[IDEGIS_DOMAIN][entry.entry_id]["cloud_mitm"]
    # Simulate a low-pH reading arriving through the coordinator, the
    # same way push_update() would after a real cloud-MITM write.
    coordinator.async_set_updated_data(
        {
            "online": True,
            "last_seen": None,
            "seconds_since_last_seen": 1,
            "live_measurements": {"ph": {"value": 6.0, "unit": "pH"}},
            "last_session": None,
        }
    )
    await hass.async_block_till_done()

    notifications = hass.states.async_entity_ids("persistent_notification")
    assert notifications, "pH alert did not reach persistent_notification.create"

    active = hass.states.get("sensor.idegis_chlorinator_active_alerts")
    assert active is not None
    assert active.state == "1"
    assert active.attributes["alerts"][0]["id"] == "ph_low"


async def test_active_alerts_sensor_clears_on_recovery(hass):  # noqa: ANN001
    """alerts.py's AlertEngine.active_alerts (iteration 8) must reflect
    the *current* state, not just ever-triggered alerts: once pH is back
    in range the sensor must drop back to 0, mirroring
    tests/test_component_alerts.py's pure-logic edge-trigger coverage
    but through the real entity this time."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mode": "cloud_mitm"},
        options={"ph_enabled": True, "ph_min": 7.0, "ph_max": 7.6},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.idegis_astralpool.const import DOMAIN as IDEGIS_DOMAIN

    coordinator = hass.data[IDEGIS_DOMAIN][entry.entry_id]["cloud_mitm"]
    coordinator.async_set_updated_data(
        {
            "online": True,
            "last_seen": None,
            "seconds_since_last_seen": 1,
            "live_measurements": {"ph": {"value": 6.0, "unit": "pH"}},
            "last_session": None,
        }
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.idegis_chlorinator_active_alerts").state == "1"

    coordinator.async_set_updated_data(
        {
            "online": True,
            "last_seen": None,
            "seconds_since_last_seen": 1,
            "live_measurements": {"ph": {"value": 7.2, "unit": "pH"}},
            "last_session": None,
        }
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.idegis_chlorinator_active_alerts").state == "0"


async def test_cost_and_recommendation_decoupled(hass):  # noqa: ANN001
    """phase delta glue (history_glue.py + IdegisCostCoordinator):
    recommendation only needs the chlorinator's own live temperature, so
    it must have a real value with no pump_power_entity configured at
    all; cost genuinely needs pump history and stays unknown until
    that's set — the two must not be coupled behind one coordinator
    returning all-or-nothing."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mode": "cloud_mitm"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    rec_state = hass.states.get("sensor.idegis_chlorinator_recommended_filtration_minutes_today")
    assert rec_state is not None
    assert rec_state.state not in ("unknown", "unavailable")

    cost_state = hass.states.get("sensor.idegis_chlorinator_filtration_cost_24h")
    assert cost_state is not None
    assert cost_state.state == "unknown"  # no pump_power_entity configured yet
