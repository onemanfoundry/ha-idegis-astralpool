"""Regression tests for the custom_component's own codec module.

``custom_components/idegis_astralpool/codec.py`` is a port of the already
validated ``addon/rootfs/opt/idegis/codec.py`` (plus the B0 field splitter
from ``capturer.py``), so the integration can decode the cloud-MITM
protocol on its own without depending on the standalone capturer add-on.

This suite has two jobs:
  1. Prove the ported module decodes a real captured B0 payload
     (docs/08-cloud-api-discovery.md) exactly as expected.
  2. Prove it stays byte-for-byte equivalent to the addon's codec on a
     corpus of real + synthetic field values, so a future change to one
     copy can't silently diverge from the other while both still exist.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT_CODEC = ROOT / "custom_components" / "idegis_astralpool" / "codec.py"
ADDON_CODEC = ROOT / "addon" / "rootfs" / "opt" / "idegis" / "codec.py"

# Real write.php payload captured 2026-06-02, documented in
# docs/08-cloud-api-discovery.md.
REAL_B0 = (
    "JS4fUX2d24UWcVbXJYfYfd"
    "4fU0W430TD4fUX2d24cbabWbcaXXaba"
    "4fU0W430CI4fUX2d24a"
    "4fU0W430LI4fUX2d24aacad"
    "4fU0W430CD4fUX2d24bVWadfbWaa"
    "4fU0W430SG4fUX2d24aVgfW"
)


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def component_codec():
    return _load("component_idegis_codec", COMPONENT_CODEC)


@pytest.fixture(scope="session")
def addon_codec():
    return _load("addon_idegis_codec", ADDON_CODEC)


def test_real_payload_prefix_and_invariants(component_codec):
    fields = component_codec.split_b0(REAL_B0)
    assert fields["__prefix__"] == "JS4fUX2d24UWcVbXJYfYfd"
    assert fields["TD"] == "cbabWbcaXXaba"
    assert fields["CI"] == "a"
    assert fields["LI"] == "aacad"
    assert fields["CD"] == "bVWadfbWaa"
    assert fields["SG"] == "aVgfW"


def test_real_payload_ci_decodes_to_zero(component_codec):
    decoded = component_codec.decode_b0(REAL_B0)
    assert decoded["CI"]["decoded"] == 0.0


def test_real_payload_ph_in_pool_range(component_codec):
    """SG decodes to a plausible salt-chlorinated pool pH (5.72-7.51 seen)."""
    decoded = component_codec.decode_b0(REAL_B0)
    ph = decoded["SG"]["decoded"]
    assert ph is not None
    assert 5.0 <= ph <= 8.0


@pytest.mark.parametrize(
    "raw",
    [
        "bVWaedfcbc",  # known-good timestamp sample from the module docstring
        "aVgfW",
        "aVgUa",
        "adbgcI",
        "a",
        "aacad",
        "aacff",
        "",
    ],
)
def test_decode_value_matches_addon(component_codec, addon_codec, raw):
    assert component_codec.decode_value(raw) == addon_codec.decode_value(raw)


def test_decode_b0_matches_addon_field_by_field(component_codec, addon_codec):
    # addon_codec.decode_fields() takes a field dict; split_b0() itself only
    # lives in capturer.py on the addon side, so split with the component's
    # (identical) splitter and decode with each module's own decode_fields.
    split_fields = component_codec.split_b0(REAL_B0)
    component_fields = component_codec.decode_fields(split_fields)
    addon_fields = addon_codec.decode_fields(split_fields)
    assert component_fields.keys() == addon_fields.keys()
    for key in component_fields:
        assert component_fields[key] == addon_fields[key], key


def test_known_timestamp_sample(component_codec):
    """CD='bVWaedfcbc' -> 1780435212 -> 2026-06-02 23:20:12 UTC (validated in
    the module docstring against real wall-clock capture time)."""
    value, _marker = component_codec.decode_value("bVWaedfcbc")
    assert value == 1780435212.0


def test_unknown_char_returns_none(component_codec):
    assert component_codec.int_base10("Z") is None
    assert component_codec.decode_value("")[0] is None
