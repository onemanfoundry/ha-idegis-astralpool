"""Regression tests for custom_components/idegis_astralpool/state.py.

state.py is the HA-independent core of the native cloud-MITM receiver
(see its module docstring): B0 chunk extraction, record building, sticky
fields, and session aggregation. Ported/trimmed from the addon's
``State``/``proxy_handler`` (addon/rootfs/opt/idegis/capturer.py).

Key regression this guards: feedback_idegis_use_session_avgs (project
memory) — a past bug used the point/sticky value instead of the session
average, and the point value reads the device's internal stagnant
chamber giving falsely low pH. ``test_live_vs_session_avg_differ_and_both_available``
below exercises exactly that split.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT_DIR = ROOT / "custom_components" / "idegis_astralpool"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def codec():
    return _load("component_idegis_codec_for_state", COMPONENT_DIR / "codec.py")


@pytest.fixture(scope="session")
def state_mod(codec):
    # state.py does `from .codec import ...` — since we're loading it
    # standalone (no package context), register a top-level alias first.
    import sys
    sys.modules.setdefault("idegis_astralpool_codec_shim", codec)
    spec = importlib.util.spec_from_file_location(
        "component_idegis_state", COMPONENT_DIR / "state.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Patch the relative import before exec: state.py says
    # `from .codec import ...`; without package context that fails, so we
    # inject the already-loaded codec module under the exact name Python
    # looks for once we strip the relative-import mechanism.
    src = spec.loader.get_source("component_idegis_state")
    src = src.replace("from .codec import", "from idegis_astralpool_codec_shim import")
    exec(compile(src, str(COMPONENT_DIR / "state.py"), "exec"), mod.__dict__)
    return mod


def _query(b0: str, h: str = "deadbeef") -> dict[str, str]:
    return {"B0": b0, "H": h}


REAL_B0 = (
    "JS4fUX2d24UWcVbXJYfYfd"
    "4fU0W430TD4fUX2d24cbabWbcaXXaba"
    "4fU0W430CI4fUX2d24a"
    "4fU0W430LI4fUX2d24aacad"
    "4fU0W430CD4fUX2d24bVWadfbWaa"
    "4fU0W430SG4fUX2d24aVgfW"
)


def test_extract_b0_single_chunk(state_mod):
    assert state_mod.extract_b0({"B0": "hello", "H": "x"}) == "hello"


def test_extract_b0_multi_chunk_ordered(state_mod):
    # Chunk digits may arrive out of dict-insertion order; must sort by digit.
    assert state_mod.extract_b0({"B2": "C", "B0": "A", "B1": "B"}) == "ABC"


def test_extract_b0_none_when_absent(state_mod):
    assert state_mod.extract_b0({"H": "x"}) is None


def test_build_record_classifies_endpoint(state_mod):
    rec = state_mod.build_record(
        method="GET", path="/interface/write.php", query=_query(REAL_B0),
        remote="192.168.1.84", upstream_status=200, upstream_body=b"ack",
    )
    assert rec["endpoint"] == "write"
    assert rec["fields"]["TD"] == "cbabWbcaXXaba"
    assert rec["b0"] == REAL_B0


def test_build_record_read_endpoint(state_mod):
    rec = state_mod.build_record(
        method="GET", path="/interface/read.php", query={"H": "x"},
        remote="192.168.1.84", upstream_status=200, upstream_body=b"",
    )
    assert rec["endpoint"] == "read"
    assert rec["fields"] is None


def test_ingest_counts_reads_and_writes(state_mod):
    st = state_mod.IdegisState()
    st.ingest(state_mod.build_record(
        method="GET", path="/interface/read.php", query={}, remote=None,
        upstream_status=200, upstream_body=b"",
    ))
    st.ingest(state_mod.build_record(
        method="GET", path="/interface/write.php", query=_query(REAL_B0),
        remote=None, upstream_status=200, upstream_body=b"",
    ))
    assert st.read_count == 1
    assert st.write_count == 1
    assert st.device_id == "JS4fUX2d24UWcVbXJYfYfd"
    assert st.session_token == "cbabWbcaXXaba"


def _write_with_ph(b0_ph_raw: str) -> dict[str, str]:
    # Build a minimal B0 carrying only SG (ph) so tests don't need the full
    # real payload for every sample.
    return _query(
        "JSprefix4fU0W430TD4fUX2d24serial4fU0W430SG4fUX2d24" + b0_ph_raw
    )


def test_live_vs_session_avg_differ_and_both_available(state_mod):
    """Regression for feedback_idegis_use_session_avgs: the live/sticky
    value must track the LAST write, while the session avg accounts for
    every sample in between — they must be able to disagree, and the
    session aggregate's avg must be the arithmetic mean, not the last
    value nor the sticky value."""
    st = state_mod.IdegisState()
    # Three writes with pH raw values: 'aVgfW' (~7? let's just use known
    # decodable values from decode_value tests) -> use simple whole-number
    # pH-like values via base10 alphabet: a=0 b=1 c=2 d=3 e=4 f=5 U=6 V=7 W=8 X=9
    # "fg" -> value 'f'=5 decimal point -> just use plain ints: "f" = 5, "U"=6, "a"=0
    for raw in ("U", "f", "a"):  # decodes to 6, 5, 0 respectively
        st.ingest(state_mod.build_record(
            method="GET", path="/interface/write.php",
            query=_write_with_ph(raw), remote=None,
            upstream_status=200, upstream_body=b"",
        ))

    snap = st.snapshot()
    live_ph = snap["live_measurements"]["ph"]["value"]
    assert live_ph == 0.0  # sticky = last write ("a" -> 0)

    session_avg = snap["last_session"]["measurements"]["ph"]["avg"]
    assert session_avg == pytest.approx((6.0 + 5.0 + 0.0) / 3)
    assert session_avg != live_ph


def test_session_closes_on_idle_and_carries_forward(state_mod, monkeypatch):
    st = state_mod.IdegisState()
    st.ingest(state_mod.build_record(
        method="GET", path="/interface/write.php",
        query=_write_with_ph("f"), remote=None,
        upstream_status=200, upstream_body=b"",
    ))
    assert st.current_session["n_writes"] == 1

    future = datetime.now(timezone.utc) + timedelta(
        seconds=state_mod.SESSION_IDLE_TIMEOUT_S + 10
    )
    st.close_session_if_idle(future)

    assert st.current_session["n_writes"] == 0
    assert st.last_session_closed is not None
    assert st.last_session_closed["measurements"]["ph"]["avg"] == 5.0


def test_snapshot_online_flag(state_mod):
    st = state_mod.IdegisState()
    assert st.snapshot()["online"] is False
    st.ingest(state_mod.build_record(
        method="GET", path="/interface/read.php", query={}, remote=None,
        upstream_status=200, upstream_body=b"",
    ))
    assert st.snapshot()["online"] is True
