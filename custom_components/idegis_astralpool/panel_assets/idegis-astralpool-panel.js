/**
 * idegis-astralpool-panel — native HA sidebar panel for the Idegis /
 * AstralPool chlorinator integration.
 *
 * Unlike the capturer add-on's dashboard (addon/rootfs/opt/idegis/static/),
 * which fetched its own REST API, this panel is a plain HA custom panel:
 * it reads entity state straight from the `hass` object HA injects into
 * every panel_custom element (`this.hass = hass` on every state change),
 * the same way any Lovelace card would. No network fetch of its own.
 *
 * Visual language (dark "comic" palette, chunky borders + solid drop
 * shadows) matches the add-on dashboard and ha-energy-optimizer, per
 * Sergio's "same visual bar as the other home-grown apps" requirement —
 * see docs/09-roadmap.md phase epsilon.
 *
 * Scope for this first version: live readings + last-session averages,
 * which is all the integration currently has sensors for (see
 * sensor.py). History charts, cost breakdown and the filtration
 * recommendation need their backend logic ported first (roadmap phase
 * gamma/zeta) before the panel can show them.
 */
class IdegisAstralpoolPanel extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._rendered = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  set panel(panel) {
    this._panel = panel;
  }

  connectedCallback() {
    this._render();
  }

  _entities() {
    const states = (this._hass && this._hass.states) || {};
    const prefix = "idegis_chlorinator";
    const out = {};
    for (const [entityId, state] of Object.entries(states)) {
      const domainOk = entityId.startsWith("sensor.") || entityId.startsWith("binary_sensor.");
      if (!domainOk || !entityId.includes(prefix)) continue;
      const key = entityId.split(".")[1].replace(`${prefix}_`, "");
      out[key] = state;
    }
    return out;
  }

  _fmt(state, digits) {
    if (!state || state.state === "unknown" || state.state === "unavailable") return "—";
    const num = Number(state.state);
    if (Number.isNaN(num)) return state.state;
    const unit = state.attributes && state.attributes.unit_of_measurement;
    const rounded = digits != null ? num.toFixed(digits) : num;
    return unit ? `${rounded} ${unit}` : `${rounded}`;
  }

  _render() {
    if (!this._hass) return;
    const e = this._entities();
    const online = e.online && e.online.state === "on";

    this._root.innerHTML = `
      <style>
        :host {
          --bg: #0f172a; --s: #1e293b; --s2: #273347; --line: #0b1220;
          --a: #38bdf8; --g: #4ade80; --y: #fbbf24; --r: #f87171;
          --t: #e2e8f0; --m: #94a3b8;
          --shadow: 4px 4px 0 var(--line);
          display: block; background: var(--bg); color: var(--t);
          font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
          min-height: 100vh;
        }
        main { max-width: 1000px; margin: 0 auto; padding: 20px; display: grid; gap: 16px; }
        h1 { font-size: 20px; margin: 0 0 4px; display: flex; align-items: center; gap: 10px; }
        .badge {
          font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
          border: 2px solid var(--line);
        }
        .badge.on { background: var(--g); color: #04210f; }
        .badge.off { background: var(--r); color: #210404; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; }
        .card {
          background: var(--s); border: 3px solid var(--line); border-radius: 12px;
          box-shadow: var(--shadow); padding: 14px 16px;
        }
        .card h2 {
          font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
          color: var(--m); margin: 0 0 6px;
        }
        .card .value { font-size: 26px; font-weight: 800; }
        .card .sub { font-size: 12px; color: var(--m); margin-top: 4px; }
        section h3 {
          font-size: 13px; color: var(--m); text-transform: uppercase;
          letter-spacing: .04em; margin: 0 0 10px;
        }
      </style>
      <main>
        <h1>
          🌊 Idegis / AstralPool
          <span class="badge ${online ? "on" : "off"}">${online ? "online" : "offline"}</span>
        </h1>
        <div class="sub">
          Last seen: ${e.last_seen ? this._fmt(e.last_seen) : "—"}
          · Device serial: ${e.device_serial ? this._fmt(e.device_serial) : "—"}
        </div>

        <section>
          <h3>Live readings</h3>
          <div class="grid">
            <div class="card"><h2>pH</h2><div class="value">${this._fmt(e.ph, 2)}</div></div>
            <div class="card"><h2>Salinity</h2><div class="value">${this._fmt(e.salinity, 2)}</div></div>
            <div class="card"><h2>Water temperature</h2><div class="value">${this._fmt(e.water_temperature, 1)}</div></div>
            <div class="card"><h2>Chlorine production</h2><div class="value">${this._fmt(e.production_percent, 0)}</div></div>
          </div>
        </section>

        <section>
          <h3>Last session (trusted averages)</h3>
          <div class="grid">
            <div class="card"><h2>pH avg</h2><div class="value">${this._fmt(e.last_session_ph_avg, 2)}</div></div>
            <div class="card"><h2>Salinity avg</h2><div class="value">${this._fmt(e.last_session_salinity_avg, 2)}</div></div>
            <div class="card"><h2>Temperature avg</h2><div class="value">${this._fmt(e.last_session_temperature_avg, 1)}</div></div>
            <div class="card"><h2>Production avg</h2><div class="value">${this._fmt(e.last_session_production_avg, 0)}</div></div>
            <div class="card">
              <h2>Duration</h2>
              <div class="value">${e.last_session_duration ? Math.round(Number(e.last_session_duration.state) / 60) : "—"}<span style="font-size:14px"> min</span></div>
              <div class="sub">${this._fmt(e.last_session_n_writes)} writes</div>
            </div>
          </div>
        </section>

        <section>
          <h3>Diagnostics</h3>
          <div class="grid">
            <div class="card"><h2>Read.php calls</h2><div class="value">${this._fmt(e.read_count)}</div></div>
            <div class="card"><h2>Write.php calls</h2><div class="value">${this._fmt(e.write_count)}</div></div>
            <div class="card"><h2>Seconds since last request</h2><div class="value">${this._fmt(e.seconds_since_last_seen, 0)}</div></div>
          </div>
        </section>
      </main>
    `;
  }
}

customElements.define("idegis-astralpool-panel", IdegisAstralpoolPanel);
