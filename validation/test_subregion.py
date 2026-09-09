"""Tests for the port-focused subregion bounds and R/L/C filtering.

Run with KiCad's Python, because ``gui`` imports wx:

    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" test_subregion.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins"))

import gui  # noqa: E402


PORTS = [
    {"x": 137.2325, "y": -85.0000, "width": 0.6, "length": 5.6},
    {"x": 148.0850, "y": -85.4050, "width": 0.875, "length": 0.25},
]


def element(ref, start, stop):
    """Give the minimal lumped-element geometry used by the GUI filter."""
    return {"ref": ref, "start": start, "stop": stop}


def test_port_bounds_include_the_pml_margin():
    """The subregion must use the pad union plus twice the GUI margin."""
    bounds = gui._port_subregion_bounds(PORTS, 3.5)
    want = (127.4325, 155.2100, -92.8425, -77.7000)
    assert bounds is not None
    for actual, expected in zip(bounds, want):
        assert abs(actual - expected) < 1e-9, (bounds, want)
    print("subregion bounds OK")


def test_filter_includes_local_part_and_excludes_r13():
    """A row outside the port region must not block export validation."""
    c6 = element("C6", [148.57, -87.60, 1.6], [149.53, -87.60, 1.6])
    r13 = element("R13", [180.00, -88.00, 1.6], [180.50, -88.00, 1.6])
    assert gui._element_in_port_subregion(c6, PORTS, 3.5)
    assert not gui._element_in_port_subregion(r13, PORTS, 3.5)
    print("subregion R/L/C filter OK")


if __name__ == "__main__":
    test_port_bounds_include_the_pml_margin()
    test_filter_includes_local_part_and_excludes_r13()
    print("PASS")