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
- [ ] `cloud_proxy.py`: run the MITM receiver (`/interface/{read,write}.php`)
  as an HA `HomeAssistantView`, so the integration needs no external
  capturer process for mode A.
- [ ] Coordinator fed from the in-process receiver instead of polling
  `http://<capturer>:8765/api/idegis/state`.
- [ ] Decoded fields surfaced as `sensor` / `binary_sensor` entities.
- [ ] Single-mode config_flow (mode A only).
- [ ] Manual install via "Custom repository" in HACS.

### Phase γ — feature parity with the Modbus map

- Implement mode B (Modbus RTU) using `pymodbus`.
- Add `number` entities for setpoints.
- Add `button` entities for alarm reset.
- Multi-mode config_flow with auto-detection.

### Phase ε — native panel (clean UI, no Lovelace)

- Register a `panel_custom` sidebar panel from `__init__.py`, serving
  assets via `hass.http.register_static_path` / a dedicated
  `HomeAssistantView`.
- Port the capturer add-on's dashboard (`addon/rootfs/opt/idegis/static/`
  — charts, session history, EN/ES i18n already built for v0.6.12) as the
  base of the panel instead of rewriting it from scratch.
- Panel reads live data straight from the coordinator/entities, no HTTP
  round-trip to a separate backend.
- E2E tests: extend `tests/test_dashboard_e2e.py`'s Playwright coverage to
  the new panel.

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
