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
- [x] **Wired to entities (iteration 6).** `history_glue.py`: thin HA
  glue — fetches 24h/7d/30d history for the configured
  `pump_power_entity` (and optional `grid_power_entity`) via
  `homeassistant.components.recorder.history.get_significant_states`,
  adapts `State` objects into the plain dicts `cost.py` expects, and
  calls the pure functions. `IdegisCostCoordinator` (coordinator.py)
  polls this every 15 min — independent of the chlorinator's own push
  updates, since it depends on the pump entity's history, not on
  anything the chlorinator reports. New sensors in `sensor.py`:
  `recommended_filtration_minutes_today`, `filtration_cost_24h/7d/30d`,
  `filtration_solar_pct_30d`. `config_flow.py`'s options flow gained
  `pump_power_entity`/`grid_power_entity` (optional `EntitySelector`s,
  domain `sensor`, device_class `power`).
- [x] **Rough edge fixed (iteration 8)**: `recommendation` and `cost`
  are now computed independently inside
  `IdegisCostCoordinator._async_update_data` — the recommendation
  (needs only the live temperature) is always computed; `cost` stays
  `None` only until `pump_power_entity` is actually set.
  `recommended_filtration_minutes_today` now has a real value from
  first setup, with no energy wiring required.
- [x] **Resolved with Sergio (2026-11-25)**: the chemistry-calibrated
  ~56 min/day default was never what he actually wanted running — he
  confirmed the real policy is seasonal: **1h of daily filtration in
  summer, 1h once a week in winter**. `recommendation.py` gained
  `compute_seasonal_recommendation(today)` (summer = June-Sept, winter
  weekly session fixed on Mondays — both reasonable defaults, not
  something he specified down to the day/month, flag if he wants them
  adjusted) and `history_glue.compute_recommendation_now` now calls
  that instead of the chemistry model. The chemistry
  model/`RecommendationInputs`/`compute_recommendation` stay in the
  module only for the addon-formula parity tests
  (`tests/test_component_recommendation.py`) — they no longer feed the
  sensor.
- [ ] `history_glue.py` has no dedicated unit test (same tradeoff as
  `cloud_proxy.py`/`panel.py`: it's pure HA-history-fetch plumbing, not
  logic). `tests/test_component_smoke.py` covers the "no pump entity
  configured yet -> sensors exist but stay `unknown`" path; a real
  recorder-backed history fixture is a further test to add.

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
- [x] **Scope gap closed (iteration 7)**: the panel now also shows
  24h history sparklines (pH/salinity/water temperature, pure inline
  SVG — no charting library, fetched once via `hass.callApi("GET",
  "history/period/...")`, the same REST endpoint Lovelace's own
  history-graph card uses, refreshed at most every 5 min), the five
  cost/recommendation sensors wired in phase δ, and an "active alerts"
  section reading HA's own `persistent_notification.*` entities (the
  same sink `notify_dispatch.py` writes to when routed to
  `persistent_notification.create`).
- [x] `tests/test_panel_assets.py` (new): pure text/syntax checks on the
  JS file (no `homeassistant` needed, unlike the rest of the smoke
  suite) — guards against an accidental truncation or a silently
  removed section; `node --check` for syntax if `node` is on PATH
  (skips otherwise).
- [x] **Gap closed (iteration 8)**: `alerts.py`'s `AlertEngine` now
  exposes `active_alerts` (the full, current `Alert` objects, not just
  the newly-triggered ones `evaluate()` returns), kept up to date on
  every call. A new `sensor.idegis_chlorinator_active_alerts` entity
  (`sensor.py`'s `IdegisActiveAlertsSensor`) surfaces its count as the
  state and the full list (id + message) as an attribute;
  `__init__.py` now calls `alert_engine.evaluate()` synchronously in the
  coordinator-update listener (only the actual `notify.*` call stays
  async) so the sensor is never a push-cycle behind. The panel now reads
  this sensor directly instead of scanning
  `persistent_notification.*` — shows every active alert regardless of
  which `notify_service` it's routed to.
  `tests/test_component_alerts.py` gained a pure-logic test for
  `active_alerts` tracking current-vs-just-fired state correctly;
  `tests/test_component_smoke.py` gained an end-to-end test that it
  clears on recovery.
- [ ] E2E tests: `tests/test_dashboard_e2e.py` still targets the *old
  add-on's* dashboard (`addon/rootfs/opt/idegis/static/`), not this
  native panel — left untouched since it's Sergio's own in-progress file
  (uncommitted changes predating this loop). A Playwright suite for the
  native panel, if wanted, should be a new file, not an edit to that
  one.

### Phase ζ — notification routing

- [x] `alerts.py`: pure (no `homeassistant` import) edge-triggered
  condition checks — pH out of range, salinity out of range, chlorinator
  stopped reporting ("session stale"), and filtration coverage under
  target. `AlertEngine` tracks which alerts are currently active so each
  one fires once on the transition into the condition, not on every
  push update, and can fire again after clearing. Tested in
  `tests/test_component_alerts.py` (12 cases): disabled-check and
  healthy-reading paths never fire (false-positive guard), edge-trigger
  behaviour, multiple simultaneous alerts.
- [x] `notify_dispatch.py`: thin HA layer — calls whatever existing
  `notify.*` service the user configured
  (`hass.services.async_call(domain, service, {...})`); the integration
  never reimplements a transport.
- [x] `config_flow.py`'s `IdegisOptionsFlow`: lets the user enable each
  condition, tune its thresholds, and set one shared `notify_service`
  string (`"domain.service"`, e.g. `notify.telegram_jarvis` or
  `persistent_notification.create`). Options change -> entry reload so
  the new `AlertConfig` takes effect.
- [x] `__init__.py` wires it end to end: a coordinator listener runs
  `AlertEngine.evaluate()` against every push update and dispatches new
  alerts. `tests/test_component_smoke.py` extended with a true
  end-to-end case: a synthetic low-pH coordinator update, routed through
  a *real* `persistent_notification.create` call, asserted to actually
  create the HA notification (not just evaluate true in isolation).
- [ ] **Known gap**: the filtration-coverage check
  (`check_filtration_coverage`) is implemented and tested in isolation,
  but `AlertEngine.evaluate()` only runs it when both
  `actual_filtration_minutes_today` and `recommended_filtration_minutes_today`
  are passed in — and nothing calls it with those yet, since there is no
  wired source for "actual minutes filtered today" (needs the activity
  log, same gap noted in `state.py`'s docstring). It will never fire
  until that's wired, by design (no false "under target" alerts from
  made-up data).
- [ ] Only one shared `notify_service` for all alert types right now,
  not per-alert routing. Revisit if Sergio wants e.g. pH alerts via
  Telegram but a stale-session alert via a push notification instead.

### Phase η — polish and HACS default

- Add mode C as fallback.
- [x] Full translations (en, es) — `strings.json`/`translations/{en,es}.json`
  cover config flow, options flow, and every entity name.
- [x] Unit tests + GitHub Actions CI — 93 local tests (pure logic) + a
  `component` CI job running the real-HA smoke suite.
- **HACS readiness check (iteration 8)**:
  - [x] `hacs.json` present and valid (`render_readme`, `homeassistant`
    min version, `country`, `hide_default_branch: true`,
    `zip_release: false`).
  - [x] `LICENSE` (MIT) and `LICENSE-docs` (CC-BY-SA 4.0) present.
  - [x] `repository.yaml` present.
  - [x] README at repo root.
  - [ ] **Blocking gap found, not fixed here**: `hide_default_branch:
    true` + `zip_release: false` means HACS installs from **GitHub
    releases**, not the default branch. The only tag in the repo is
    `v0.0.9` (the pre-rewrite prototype) — there is no release matching
    the current `0.7.0` custom_component. Cutting a GitHub release is a
    public, visible action (shows up in the repo's Releases tab,
    potentially triggers notifications for anyone watching), so it's
    left for Sergio to request explicitly rather than done
    autonomously by this loop.
  - [ ] Submit to HACS default repositories — out of scope until the
    above release gap is closed and Sergio has used the integration for
    real (HACS default submission expects some evidence of real-world
    use, not just passing tests).

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
