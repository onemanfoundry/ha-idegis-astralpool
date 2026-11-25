/**
 * idegis-astralpool-panel — native HA sidebar panel for the Idegis /
 * AstralPool chlorinator integration.
 *
 * Unlike the capturer add-on's dashboard (addon/rootfs/opt/idegis/static/),
 * which fetched its own REST API, this panel is a plain HA custom panel:
 * it reads entity state straight from the `hass` object HA injects into
 * every panel_custom element (`this.hass = hass` on every state change),
 * the same way any Lovelace card would. No network fetch of its own,
 * *except* for the 24h history charts, which go through `hass.callApi`
 * (the same REST history endpoint Lovelace's own history-graph card
 * uses) since point-in-time state alone can't draw a trend.
 *
 * Visual language (dark "comic" palette, chunky borders + solid drop
 * shadows) matches the add-on dashboard and ha-energy-optimizer, per
 * Sergio's "same visual bar as the other home-grown apps" requirement —
 * see docs/09-roadmap.md phase epsilon.
 *
 * Iteration 7 added: 24h history sparklines (pH/salinity/temperature,
 * pure inline SVG — no charting library, same "no external dependencies"
 * stance as the rest of this panel) and the cost/recommendation sensors
 * wired in iteration 6 (history_glue.py).
 *
 * Iteration 8: the active-alerts section now reads
 * `sensor.idegis_chlorinator_active_alerts` directly — AlertEngine's own
 * active set (alerts.py), exposed as a sensor attribute — instead of
 * inferring it from `persistent_notification.*` state. That inference
 * only worked for alerts actually routed to
 * `persistent_notification.create`; this shows every active alert
 * regardless of which `notify_service` it's routed to.
 */
const SPARK_W = 280;
const SPARK_H = 56;

class IdegisAstralpoolPanel extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._history = null; // { ph: [[ts, val], ...], salinity: [...], water_temperature: [...] }
    this._historyLoading = false;
    this._historyFetchedAt = 0;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this._render();
    if (first) this._loadHistory();
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

  // sensor.idegis_chlorinator_active_alerts (iteration 8) carries the
  // full list in its `alerts` attribute, straight from AlertEngine's own
  // active set — no longer inferred from persistent_notification state,
  // so this also shows alerts routed only to e.g. Telegram.
  _activeAlerts(e) {
    const sensor = e.active_alerts;
    if (!sensor) return [];
    return sensor.attributes.alerts || [];
  }

  _fmt(state, digits) {
    if (!state || state.state === "unknown" || state.state === "unavailable") return "—";
    const num = Number(state.state);
    if (Number.isNaN(num)) return state.state;
    const unit = state.attributes && state.attributes.unit_of_measurement;
    const rounded = digits != null ? num.toFixed(digits) : num;
    return unit ? `${rounded} ${unit}` : `${rounded}`;
  }

  // ── History (24h sparklines) ─────────────────────────────────────────
  // Fetches once on first hass set, then refreshes at most every 5 min —
  // this is a REST round trip, unlike every other value on this panel.
  async _loadHistory() {
    if (!this._hass || this._historyLoading) return;
    const now = Date.now();
    if (now - this._historyFetchedAt < 5 * 60 * 1000) return;
    this._historyLoading = true;
    try {
      const entityIds = [
        "sensor.idegis_chlorinator_ph",
        "sensor.idegis_chlorinator_salinity",
        "sensor.idegis_chlorinator_water_temperature",
      ].filter((id) => this._hass.states[id]);
      if (!entityIds.length) return;
      const start = new Date(now - 24 * 60 * 60 * 1000).toISOString();
      const path = `history/period/${start}?filter_entity_id=${entityIds.join(",")}&minimal_response`;
      const raw = await this._hass.callApi("GET", path);
      const byEntity = {};
      for (const series of raw || []) {
        if (!series.length) continue;
        const entityId = series[0].entity_id || series[0].a?.entity_id;
        if (!entityId) continue;
        const key = entityId.split(".")[1].replace("idegis_chlorinator_", "");
        byEntity[key] = series
          .map((p) => {
            const ts = new Date(p.last_changed || p.lu * 1000 || p.last_updated).getTime();
            const val = Number(p.state ?? p.s);
            return Number.isFinite(val) ? [ts, val] : null;
          })
          .filter(Boolean);
      }
      this._history = byEntity;
      this._historyFetchedAt = now;
      this._render();
    } catch (err) {
      // History is a nice-to-have on an otherwise live-state panel: a
      // failed fetch (e.g. recorder not fully started yet) just means no
      // sparklines this render, never a broken panel.
      console.warn("idegis-astralpool-panel: history fetch failed", err); // eslint-disable-line no-console
    } finally {
      this._historyLoading = false;
    }
  }

  _sparkline(points, color) {
    if (!points || points.length < 2) {
      return `<div class="sparkline-empty">not enough history yet</div>`;
    }
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    const pad = 4;
    const path = points
      .map(([x, y], i) => {
        const px = pad + ((x - minX) / spanX) * (SPARK_W - 2 * pad);
        const py = SPARK_H - pad - ((y - minY) / spanY) * (SPARK_H - 2 * pad);
        return `${i === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`;
      })
      .join(" ");
    return `
      <svg viewBox="0 0 ${SPARK_W} ${SPARK_H}" width="100%" height="${SPARK_H}">
        <path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
      </svg>
      <div class="sparkline-range"><span>${ys[0].toFixed(2)}</span><span>min ${minY.toFixed(2)} · max ${maxY.toFixed(2)}</span><span>${ys[ys.length - 1].toFixed(2)}</span></div>
    `;
  }

  _render() {
    if (!this._hass) return;
    const e = this._entities();
    const online = e.online && e.online.state === "on";
    const alerts = this._activeAlerts(e);
    const h = this._history || {};

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
        .grid.wide { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
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
        .sparkline-empty { font-size: 12px; color: var(--m); padding: 18px 0; text-align: center; }
        .sparkline-range { display: flex; justify-content: space-between; font-size: 11px; color: var(--m); margin-top: 4px; }
        .alert-card {
          background: #2a1313; border: 3px solid var(--r); border-radius: 12px;
          box-shadow: 4px 4px 0 var(--line); padding: 12px 16px; display: flex;
          align-items: baseline; gap: 10px;
        }
        .alert-card .dot { color: var(--r); font-size: 18px; }
        .alert-card .title { font-weight: 700; font-size: 13px; }
        .alert-card .msg { color: var(--m); font-size: 12px; }
        .no-alerts { color: var(--g); font-size: 13px; padding: 4px 0; }
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
          <h3>Active alerts</h3>
          ${alerts.length
            ? alerts
                .map(
                  (a) => `
            <div class="alert-card">
              <span class="dot">●</span>
              <div>
                <div class="title">${a.id}</div>
                <div class="msg">${a.message || ""}</div>
              </div>
            </div>`
                )
                .join("")
            : `<div class="no-alerts">No active alerts.</div>`}
        </section>

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
          <h3>24h history</h3>
          <div class="grid wide">
            <div class="card"><h2>pH</h2>${this._sparkline(h.ph, "var(--a)")}</div>
            <div class="card"><h2>Salinity</h2>${this._sparkline(h.salinity, "var(--y)")}</div>
            <div class="card"><h2>Water temperature</h2>${this._sparkline(h.water_temperature, "var(--g)")}</div>
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
          <h3>Filtration &amp; cost</h3>
          <div class="grid">
            <div class="card"><h2>Recommended minutes today</h2><div class="value">${this._fmt(e.recommended_filtration_minutes_today, 0)}</div></div>
            <div class="card"><h2>Cost (24h)</h2><div class="value">${this._fmt(e.filtration_cost_24h, 2)}</div></div>
            <div class="card"><h2>Cost (7d)</h2><div class="value">${this._fmt(e.filtration_cost_7d, 2)}</div></div>
            <div class="card"><h2>Cost (30d)</h2><div class="value">${this._fmt(e.filtration_cost_30d, 2)}</div></div>
            <div class="card"><h2>Solar share (30d)</h2><div class="value">${this._fmt(e.filtration_solar_pct_30d, 0)}</div></div>
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
