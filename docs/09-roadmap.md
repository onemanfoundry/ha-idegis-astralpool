# 09 · Roadmap toward the HACS integration

The end goal is a polished `custom_components/idegis_astralpool/` that
HACS users install in one click and Home Assistant configures via a UI
wizard.

## Target layout

```
custom_components/idegis_astralpool/
├── __init__.py            integration setup / unload
├── manifest.json          domain, version, requirements, dependencies
├── config_flow.py         UI wizard: choose mode (A / B / C) and pair
├── coordinator.py         single DataUpdateCoordinator
├── const.py
├── cloud_proxy.py         mode A: HTTP receiver + B0 decoder
├── modbus_rtu.py          mode B: Modbus RTU client (over ESP32 / direct USB)
├── poolstation_client.py  mode C: Poolstation cloud (fallback)
├── codec.py               B0 payload codec + H hash
├── sensor.py
├── binary_sensor.py
├── number.py
├── switch.py
├── button.py
└── translations/
    ├── en.json
    └── es.json
```

## Operating modes (config_flow options)

| Mode | When to pick it |
|---|---|
| **A · Cloud-MITM local** | You can override DNS on your router for `api.idegis.net` and run a small HTTP listener on Home Assistant or a sidecar host. |
| **B · Modbus RTU via ESP32 / USB** | You have wired an RS485 transceiver into the Idegis Modbus connector. Full bidirectional control, no cloud. |
| **C · Poolstation cloud** | Quick start. You accept the Fluidra cloud dependency. |

## Target: a self-sufficient native app, not just entities

The integration is meant to absorb what the `addon/` (capturer) and
`addon-pool-brain/` add-ons do today, not just poll them:
- Its own cloud-MITM receiver (no separate capturer process needed for
  mode A).
- A sidebar panel registered by the integration itself (`panel_custom`),
  serving its own charts/history/alerts UI — not Lovelace, not an add-on
  ingress iframe.
- Notification routing configured in the integration's own
  config/options flow: which conditions (pH out of range, low chlorine,
  session failure, filtration error…) fire, and which existing HA
  `notify.*` services (telegram_bot, mail/smtp, mobile_app) they go to.
  The integration never reimplements a notification transport itself.

The two add-ons stay as the reference implementation and stay running in
production until the integration reaches functional parity *and* that is
confirmed by tests — see "Add-on deprecation plan" below. No production
switchover without Sergio's explicit go-ahead.

## Phases

### Phase α — research (done)

- Decode the `B0` payload from a corpus of captured requests.
- Validate the `H` MD5 formula.
- Prove the reverse proxy approach in CT104 with a couple of decoded
  fields.

### Phase β — minimum viable integration (in progress)

- [x] Port the validated `codec.py` (B0 decode + field splitter) into
  `custom_components/idegis_astralpool/codec.py`, with a regression
  suite (`tests/test_component_codec.py`) pinned against the addon's
  copy and a real captured payload.
- [x] `state.py`: HA-independent port of the addon's `State`/session
  aggregation (sticky fields, session min/max/avg, idle close), unit
  tested in `tests/test_component_state.py` without needing
  `homeassistant` installed.
- [x] `cloud_proxy.py`: the MITM receiver (`/interface/{read,write}.php`)
  as two `HomeAssistantView`s (`requires_auth = False`) registered under
  HA's own HTTP server at `/api/idegis_astralpool/interface/*`, so the
  only thing the LAN DNS override / nginx vhost needs to point at is this
  HA instance instead of the add-on's `:8765`. Still a transparent
  reverse-proxy to the real Idegis cloud, same design as the addon.
- [x] Coordinator (`coordinator.py`) is push-fed (`update_interval=None`,
  `push_update()` called from the view) instead of polling an external
  URL.
- [x] Decoded fields surfaced as `sensor` / `binary_sensor` entities,
  rewritten against the new state snapshot shape (old prototype's
  `age_seconds`/`last_fields`/`pump_power_w`/etc. keys from the external
  capturer's JSON API don't apply any more).
- [x] Single-mode config_flow (mode A only, no external host/port to
  configure since there's nothing external to probe any more).
- [x] `tests/test_component_smoke.py`: first test that actually imports
  and runs the custom_component against a real HA core
  (`pytest-homeassistant-custom-component`) — config_flow, setup_entry,
  view registration, entity creation, unload. New CI job `component` in
  `.github/workflows/ci.yaml` runs it; nothing did before this.
- [ ] Manual install via "Custom repository" in HACS (needs a tagged
  release once phase β is otherwise feature-complete).

Not ported yet from the addon (tracked for phase γ or later, see
`state.py`'s module docstring): pump-power correlation / trusted-reading
filtering (needs a live HA entity reference), and long-term on-disk
history (the in-memory deque is enough for current sensors; long-term
history should probably ride HA's own recorder instead of a bespoke
JSONL store).

### Phase γ — feature parity with the Modbus map

- Implement mode B (Modbus RTU) using `pymodbus`.
- Add `number` entities for setpoints.
- Add `button` entities for alarm reset.
- Multi-mode config_flow with auto-detection.

### Phase δ — electricity cost + filtration recommendation

- [x] `cost.py`: pure port of the addon's `cost_breakdown` /
  `kwh_from_history` / `tariff_period` / `tariff_price` (v0.6.11, Spain
  2.0TD geometry, solar/grid attribution). No `homeassistant` import —
  takes plain history dicts, does no I/O itself. Tested in
  `tests/test_component_cost.py`, cross-checked field-by-field against
  the addon's own function.
- [x] `recommendation.py`: pure port of `_compute_recommendation` /
  `_temp_multiplier` (chlorine-demand vs turnover, whichever is larger).
  Tested in `tests/test_component_recommendation.py` for formula parity
  with the addon **and** for the calibration itself: this module's
  defaults deliberately do **not** reuse the addon's shipped
  `config.yaml` values (`target_production_pct: 40`,
  `chlorine_demand_ppm_per_day: 0.6`, `min_turnovers_per_day: 0.75`,
  `net_n_clean_installed: false`) because those are the input documented
  as already known to overshoot to ~139 min/day for this installation
  (project memory `feedback_filtration_defaults_overshoot`). Instead it
  uses the calibrated reading from `project_filtration_target`
  (`target_production_pct=100`, `chlorine_demand_ppm_per_day=0.25`,
  `min_turnovers_per_day=0.5`, `net_n_clean_installed=True`), which
  computes to ~56 min/day — a test pins this to the 45-65 min band.
- [ ] **Not wired to entities yet.** Both modules are pure logic + tests
  only; neither is called from `sensor.py`, the coordinator, or exposed
  in `config_flow`/`options_flow`. Wiring `cost.py` needs an options_flow
  step to pick the pump-power and (optional) grid-power entities and a
  way to fetch their HA history; wiring `recommendation.py` needs the
  same calibrated numbers to become overridable options instead of
  hardcoded module constants — **Sergio should confirm or correct the
  calibrated defaults above before they're exposed anywhere he'd act on
  them for real automations.**

### Phase ε — native panel (clean UI, no Lovelace)

- [x] `panel.py`: registers a `panel_custom` sidebar panel from
  `__init__.py` ("Idegis / AstralPool", `mdi:pool`). The web component's
  JS module is served through a dedicated `HomeAssistantView`
  (`panel.py`'s `IdegisPanelJSView`) rather than
  `hass.http.register_static_path`, so it's read through code the smoke
  suite exercises instead of a static-path API whose signature drifts
  across HA core versions.
- [x] `panel_assets/idegis-astralpool-panel.js`: a **plain HA custom
  panel**, not a port of the add-on's dashboard fetch-based JS. It reads
  `hass.states` directly (same mechanism any Lovelace card uses) — no
  HTTP round-trip of its own. Dark "comic" palette matched to the addon
  dashboard / `ha-energy-optimizer` for visual consistency.
- [x] `tests/test_component_smoke.py` extended: asserts the panel JS view
  and the `frontend_panels` registration both exist after setup.
- [ ] **Scope gap, tracked explicitly**: this first version only shows
  what `sensor.py` currently has (live readings + last-session
  averages + diagnostics) — no charts, no multi-session history, no
  electricity cost, no filtration recommendation. Those need their
  backend logic ported first (phase δ's `cost_breakdown` /
  `_compute_recommendation`) before the panel can show them; the add-on
  dashboard's chart code (`app.js`'s SVG rendering) can then be reused
  once there is a data source to feed it from inside the component.
- [ ] E2E tests: extend `tests/test_dashboard_e2e.py`'s Playwright
  coverage to the new panel once it has more than static-looking cards
  (worth doing once charts land, not for this bare version).

### Phase ζ — notification routing

- Options flow: pick which alert conditions are active (pH out of range,
  low chlorine, chlorinator session failure, filtration error, …) and
  which `notify.*` service each one is routed to.
- Alert rules live in the integration (condition → event), transport is
  always an existing HA `notify.*` service — never reimplemented here.
- Regression tests with synthetic condition sequences asserting the
  expected `notify.*` call (and the absence of one when nothing is wrong).

### Phase η — polish and HACS default

- Add mode C as fallback.
- Full translations (en, es).
- Unit tests + GitHub Actions CI.
- Submit to HACS default repositories.

## Add-on deprecation plan

The `addon/` (capturer) and `addon-pool-brain/` add-ons are the reference
implementation and stay in production, untouched, until **all** of these
are true:
1. The integration's codec, cost, and filtration-recommendation logic
   (ported in phases β/γ) pass the same regression fixtures the add-ons
   do, with no behavioural drift.
2. The native panel (phase ε) has E2E parity with the current ingress
   dashboard.
3. Notification routing (phase ζ) covers every alert the add-ons
   currently only report passively in the dashboard.

Once all three hold, the plan is: install the integration alongside the
add-ons, run both in parallel for a period Sergio sets, then only on his
explicit go-ahead disable the add-ons and remove the DNS/nginx MITM
plumbing that currently points at them. This is a production system
change and is never executed unilaterally.

## Out of scope

- Bombs of features unrelated to chlorinator control (e.g. pool cleaner
  control, water heater integration). Those belong in other integrations.
- Anything specific to industrial Multi-Tec units (Tecno Connect line).
- Anything specific to AstralPool Halo (different platform).
