"""codec.py — Idegis B0 payload codec.

Ported verbatim from ``addon/rootfs/opt/idegis/codec.py`` (same module,
validated against 1000+ captured requests — see that file's docstring for
the empirical evidence) plus the B0 field splitter from
``addon/rootfs/opt/idegis/capturer.py``. Kept as a single pure, stdlib-only
module so the integration does not need the standalone capturer add-on to
decode the cloud-MITM protocol (path A) by itself.

Field semantics (confirmed/strong):
    CI = constant 0 (channel index)
    TD = device serial (13 base-10 digits)
    LI = per-request session counter
    CD = Unix timestamp seconds (UTC)
    SG = water pH (decimal, no marker)
    IT = water salinity g/L (decimal, marker 'M')
    CY = water temperature °C (decimal, marker 'I')
    GY = production percent (%)
    9C / Jb / SI / YI / Y9 / DL / RB = single-digit booleans
    AJ / OI / OB / TB / NB / ND / MK = various counters
    YD = single uppercase letter, looks categorical (a, N, R seen)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Confirmed base-10 alphabet.
BASE10_MAP: dict[str, int] = {
    "a": 0, "b": 1, "c": 2, "d": 3, "e": 4,
    "f": 5, "U": 6, "V": 7, "W": 8, "X": 9,
}

DECIMAL_POINT = "g"

# B0 field/key-value separators (invariant, confirmed on the live protocol).
FIELD_SEP = "4fU0W430"
KV_SEP = "4fUX2d24"


def split_b0(b0: str) -> dict[str, str]:
    """Split a raw B0 payload into its field dict (plus ``__prefix__``)."""
    parts = b0.split(FIELD_SEP)
    out: dict[str, str] = {"__prefix__": parts[0]}
    for p in parts[1:]:
        if KV_SEP in p:
            k, _, v = p.partition(KV_SEP)
            out[k] = v
        else:
            out[f"__bare_{len(out)}__"] = p
    return out


def int_base10(s: str) -> int | None:
    """Decode a base-10-custom string to integer. Returns None on bad char."""
    if not s:
        return None
    try:
        return int("".join(str(BASE10_MAP[c]) for c in s))
    except KeyError:
        return None


def split_marker(s: str) -> tuple[str, str]:
    """Strip trailing chars that are neither base10 digits nor decimal point.
    These are unit markers (M, I, N, R, ...)."""
    i = len(s) - 1
    while i >= 0 and s[i] not in BASE10_MAP and s[i] != DECIMAL_POINT:
        i -= 1
    return s[: i + 1], s[i + 1 :]


def decode_decimal(s: str) -> float | None:
    """Decode a numeric string with optional 'g' decimal point."""
    if not s:
        return None
    parts = s.split(DECIMAL_POINT)
    if len(parts) == 1:
        v = int_base10(parts[0])
        return float(v) if v is not None else None
    if len(parts) > 2:
        return None
    left, right = parts
    L = int_base10(left) if left else 0
    R = int_base10(right) if right else 0
    if L is None or R is None:
        return None
    if not right:
        return float(L)
    return float(L) + R / (10 ** len(right))


def decode_value(raw: str) -> tuple[float | None, str]:
    """Generic decoder: returns (numeric_value_or_none, marker_string)."""
    if not raw:
        return None, ""
    num_part, marker = split_marker(raw)
    return decode_decimal(num_part), marker


# Semantic field map: how each known field should be interpreted.
FIELDS_INFO: dict[str, dict[str, Any]] = {
    "CI": {"type": "constant", "name": "channel_index", "unit": None},
    "TD": {"type": "device_id", "name": "device_serial", "unit": None},
    "LI": {"type": "counter", "name": "request_counter", "unit": None},
    "CD": {"type": "unix_timestamp", "name": "device_clock", "unit": None},
    "SG": {"type": "measure", "name": "ph", "unit": "pH"},
    "IT": {"type": "measure", "name": "salinity", "unit": "g/L"},
    "9C": {"type": "bool", "name": "flag_9c", "unit": None},
    "Jb": {"type": "bool", "name": "flag_Jb", "unit": None},
    "SI": {"type": "bool", "name": "flag_SI", "unit": None},
    "YI": {"type": "bool", "name": "flag_YI", "unit": None},
    "Y9": {"type": "bool", "name": "flag_Y9", "unit": None},
    "DL": {"type": "bool", "name": "flag_DL", "unit": None},
    "RB": {"type": "bool", "name": "flag_RB", "unit": None},
    "AJ": {"type": "counter", "name": "counter_AJ", "unit": None},
    "OI": {"type": "counter", "name": "counter_OI", "unit": None},
    "OB": {"type": "counter", "name": "counter_OB", "unit": None},
    "TB": {"type": "counter", "name": "counter_TB", "unit": None},
    "NB": {"type": "counter", "name": "counter_NB", "unit": None},
    "ND": {"type": "counter", "name": "counter_ND", "unit": None},
    "MK": {"type": "counter", "name": "counter_MK", "unit": None},
    "YD": {"type": "category", "name": "category_YD", "unit": None},
    "CY": {"type": "measure", "name": "temperature", "unit": "°C"},
    "9G": {"type": "unknown", "name": "field_9G", "unit": None},
    "GY": {"type": "measure", "name": "production_percent", "unit": "%"},
    "MG": {"type": "unknown", "name": "field_MG", "unit": None},
    "PG": {"type": "unknown", "name": "field_PG", "unit": None},
    "CJ": {"type": "unknown", "name": "field_CJ", "unit": None},
    "CK": {"type": "unknown", "name": "field_CK", "unit": None},
    "CC": {"type": "unknown", "name": "field_CC", "unit": None},
    "C7": {"type": "unknown", "name": "field_C7", "unit": None},
}


def decode_field(name: str, value: str) -> dict[str, Any]:
    """Decode a single field, returning rich metadata."""
    if not value:
        return {"name": name, "raw": value, "decoded": None}

    info = FIELDS_INFO.get(name, {})
    ftype = info.get("type", "unknown")
    num, marker = decode_value(value)

    out: dict[str, Any] = {
        "name": name,
        "raw": value,
        "type": ftype,
        "semantic_name": info.get("name", name),
        "unit": info.get("unit"),
        "decoded": num,
        "marker": marker or None,
    }

    if ftype == "unix_timestamp" and num is not None:
        try:
            dt = datetime.fromtimestamp(int(num), tz=timezone.utc)
            out["as_iso_utc"] = dt.isoformat()
        except (OSError, OverflowError, ValueError):
            pass
    elif ftype == "bool" and num is not None:
        out["bool"] = bool(int(num))
    elif ftype == "device_id" and num is not None:
        out["decoded"] = int(num)

    return out


def decode_fields(fields: dict[str, str] | None) -> dict[str, dict[str, Any]]:
    if not fields:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in fields.items():
        if k.startswith("__"):
            continue
        out[k] = decode_field(k, v)
    return out


def summarise_measurements(fields_decoded: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pull just the meaningful measurements out of a decoded fields dict."""
    out: dict[str, Any] = {}
    for k, fd in fields_decoded.items():
        if fd.get("type") == "measure" and fd.get("decoded") is not None:
            sem = fd.get("semantic_name", k)
            out[sem] = {"value": fd["decoded"], "unit": fd.get("unit")}
    return out


def decode_b0(b0: str) -> dict[str, dict[str, Any]]:
    """Convenience: split a raw B0 payload and decode every field in one call."""
    return decode_fields(split_b0(b0))
