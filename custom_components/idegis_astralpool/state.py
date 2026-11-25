"""state.py — pure, HA-independent state machine for the cloud-MITM receiver.

Ported and trimmed from ``addon/rootfs/opt/idegis/capturer.py``'s
``State``/``proxy_handler``. Deliberately has **no** ``homeassistant``
import (stdlib + ``.codec`` only) so it can be unit-tested without
installing Home Assistant — see ``tests/test_cloud_proxy_state.py``.
``cloud_proxy.py`` wraps this with the actual HTTP view/upstream call.

What this keeps from the addon's design:
  * B0/B1/... chunk concatenation before splitting fields.
  * Sticky per-field last-known values (``sticky_fields``), so sensors
    stay populated between writes that only carry a subset of fields.
  * Session aggregation (min/max/avg over a contiguous run of writes),
    closed on idle timeout — this is what feeds "last session avg"
    sensors. Per feedback_idegis_use_session_avgs (project memory): point
    values read the device's internal stagnant chamber and give falsely
    low pH; sensors must prefer the session aggregate, never the raw
    point value.

What is intentionally NOT ported yet (tracked in docs/09-roadmap.md):
  * Pump-power correlation / trusted-measurement filtering
    (``sample_flow_ok`` / ``trusted_measurements``) — needs a live HA
    entity reference, done in a later iteration.
  * JSONL on-disk history persistence — the coordinator's in-memory
    history is enough for sensors; long-term history can reuse HA's own
    recorder instead of a bespoke file store.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

from .codec import decode_fields, split_b0, summarise_measurements

# Real Idegis/AstralPool cloud backend (Incapsula/Imperva-fronted),
# confirmed in docs/08-cloud-api-discovery.md. The chlorinator only ever
# reaches it because the LAN DNS override for api.idegis.net points at
# this HA instance first; the proxy view relays every request so the
# device keeps working exactly as if talking to the cloud directly.
UPSTREAM_HOST = "45.60.153.189"
UPSTREAM_HOST_HEADER = "api.idegis.net"

MAX_HISTORY = 500
SESSION_IDLE_TIMEOUT_S = 1800  # 30 min fallback-only idle close.
ONLINE_TIMEOUT_S = 120

VIEW_URL_READ = "/api/idegis_astralpool/interface/read.php"
VIEW_URL_WRITE = "/api/idegis_astralpool/interface/write.php"


def _empty_session() -> dict[str, Any]:
    return {"start_ts": None, "last_ts": None, "n_writes": 0, "measurements": {}}


class IdegisState:
    """In-memory, per-config-entry state fed by the proxy view."""

    def __init__(self) -> None:
        self.history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self.read_count = 0
        self.write_count = 0
        self.last_seen: datetime | None = None
        self.sticky_fields: dict[str, str] = {}
        self.current_session: dict[str, Any] = _empty_session()
        self.last_session_closed: dict[str, Any] | None = None
        self.device_id: str | None = None
        self.session_token: str | None = None

    def ingest(self, record: dict[str, Any]) -> None:
        self.history.append(record)
        ts = datetime.fromisoformat(record["ts"])
        self.last_seen = ts

        fields = record.get("fields") or {}
        if fields:
            if not self.device_id:
                self.device_id = fields.get("__prefix__")
            if not self.session_token:
                self.session_token = fields.get("TD")

        endpoint = record.get("endpoint")
        if endpoint == "read":
            self.read_count += 1
        elif endpoint == "write":
            self.write_count += 1
            if fields:
                for k, v in fields.items():
                    if k.startswith("__"):
                        continue
                    self.sticky_fields[k] = v

                measurements = summarise_measurements(decode_fields(fields))
                if measurements:
                    self._roll_session(ts)
                    session = self.current_session
                    session["last_ts"] = ts
                    if session["start_ts"] is None:
                        session["start_ts"] = ts
                    session["n_writes"] += 1
                    for name, payload in measurements.items():
                        val = payload.get("value")
                        if val is None:
                            continue
                        agg = session["measurements"].setdefault(
                            name,
                            {
                                "n": 0, "sum": 0.0, "min": val, "max": val,
                                "last": val, "unit": payload.get("unit"),
                            },
                        )
                        agg["n"] += 1
                        agg["sum"] += val
                        agg["min"] = min(agg["min"], val)
                        agg["max"] = max(agg["max"], val)
                        agg["last"] = val

    def _roll_session(self, ts: datetime) -> None:
        last = self.current_session.get("last_ts")
        if last is None:
            return
        if (ts - last).total_seconds() < SESSION_IDLE_TIMEOUT_S:
            return
        self.last_session_closed = self._snapshot_session(
            self.current_session, self._carry_from(self.sticky_fields)
        )
        self.current_session = _empty_session()

    @staticmethod
    def _carry_from(sticky: dict[str, str]) -> dict[str, Any]:
        return summarise_measurements(decode_fields(sticky))

    @staticmethod
    def _snapshot_session(
        session: dict[str, Any], carry: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "start_ts": session["start_ts"].isoformat() if session["start_ts"] else None,
            "last_ts": session["last_ts"].isoformat() if session["last_ts"] else None,
            "duration_s": (
                (session["last_ts"] - session["start_ts"]).total_seconds()
                if session["start_ts"] and session["last_ts"] else None
            ),
            "n_writes": session["n_writes"],
            "measurements": {},
        }
        for name, agg in session["measurements"].items():
            n = agg["n"]
            out["measurements"][name] = {
                "n": n,
                "avg": agg["sum"] / n if n else None,
                "min": agg["min"],
                "max": agg["max"],
                "last": agg["last"],
                "unit": agg.get("unit"),
            }
        if carry:
            for name, mv in carry.items():
                if name in out["measurements"]:
                    continue
                val = mv.get("value")
                if val is None:
                    continue
                out["measurements"][name] = {
                    "n": 0, "avg": val, "min": val, "max": val,
                    "last": val, "unit": mv.get("unit"), "carried": True,
                }
        return out

    def close_session_if_idle(self, now: datetime) -> None:
        session = self.current_session
        if session.get("last_ts") is None or session["n_writes"] == 0:
            return
        if (now - session["last_ts"]).total_seconds() >= SESSION_IDLE_TIMEOUT_S:
            self.last_session_closed = self._snapshot_session(
                session, self._carry_from(self.sticky_fields)
            )
            self.current_session = _empty_session()

    def snapshot(self) -> dict[str, Any]:
        """Flat dict consumed by the coordinator / entities."""
        now = datetime.now(timezone.utc)
        seconds_since = (
            (now - self.last_seen).total_seconds() if self.last_seen else None
        )
        return {
            "online": seconds_since is not None and seconds_since < ONLINE_TIMEOUT_S,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "seconds_since_last_seen": seconds_since,
            "read_count": self.read_count,
            "write_count": self.write_count,
            "device_id": self.device_id,
            "session_token": self.session_token,
            "live_measurements": summarise_measurements(
                decode_fields(self.sticky_fields)
            ),
            "last_session": (
                self._snapshot_session(
                    self.current_session, self._carry_from(self.sticky_fields)
                )
                if self.current_session["n_writes"]
                else self.last_session_closed
            ),
        }


def extract_b0(query: dict[str, str]) -> str | None:
    """Concatenate chunked B0/B1/B2/... query params, in order."""
    chunks: list[tuple[int, str]] = [
        (int(k[1]), v) for k, v in query.items()
        if len(k) == 2 and k[0] == "B" and k[1].isdigit()
    ]
    chunks.sort()
    return "".join(v for _, v in chunks) if chunks else None


def build_record(
    *, method: str, path: str, query: dict[str, str], remote: str | None,
    upstream_status: int | None, upstream_body: bytes,
) -> dict[str, Any]:
    """Pure helper (no I/O) so it's directly unit-testable."""
    endpoint = "read" if path.endswith("read.php") else (
        "write" if path.endswith("write.php") else None
    )
    b0 = extract_b0(query)
    fields = split_b0(b0) if b0 else None
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "client": remote,
        "method": method,
        "path": path,
        "endpoint": endpoint,
        "b0": b0,
        "h": query.get("H"),
        "fields": fields,
        "upstream_status": upstream_status,
        "response_size_bytes": len(upstream_body),
    }
