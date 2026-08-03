"""Tests of the port geometry and the mesh, with no KiCad and no solver.

`runner` imports CSXCAD and openEMS inside its functions only. Thus
`_port_geometry` and `_mesh` need numpy alone, and this file runs in some
seconds. Run it with the python of the solver:

    C:\\openEMS\\venv\\Scripts\\python.exe test_ports.py

These tests hold the rules that the four port types obey. A failure here
shows an incorrect model BEFORE a run of some minutes shows an incorrect
number.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins"))

import runner  # noqa: E402

RES = 2.355  # the coarse mesh of the validation boards


def model(port_type, **port):
    """Give a model with 2 ports on a board with 4 layers."""
    p = {"number": 1, "label": "P1 pad 1", "x": 5.0, "y": -10.0,
         "layer": "In1.Cu", "ref_layer": "In2.Cu", "ref_layer2": "F.Cu",
         "height": 0.486667, "asymmetry": 0.0, "gap": None,
         "width": 0.6, "length": 0.6, "direction": [1, 0],
         "track_width": 0.6, "type": port_type}
    p.update(port)
    q = dict(p, number=2, x=35.0, direction=[-1, 0])
    return {
        "copper_layers": [{"name": "F.Cu", "z": 1.46, "thickness": 0.035},
                          {"name": "In1.Cu", "z": 0.973333, "thickness": 0.035},
                          {"name": "In2.Cu", "z": 0.486667, "thickness": 0.035},
                          {"name": "B.Cu", "z": 0.0, "thickness": 0.035}],
        "dielectric_layers": [
            {"name": "d1", "z_top": 1.46, "z_bottom": 0.973333,
             "epsilon": 4.5, "loss_tangent": 0.02},
            {"name": "d2", "z_top": 0.973333, "z_bottom": 0.486667,
             "epsilon": 4.5, "loss_tangent": 0.02},
            {"name": "d3", "z_top": 0.486667, "z_bottom": 0.0,
             "epsilon": 4.5, "loss_tangent": 0.02}],
        "region": {"x0": -8.05, "x1": 48.05, "y0": -28.05, "y1": 8.05},
        "board_rect": {"x0": -0.05, "x1": 40.05, "y0": -20.05, "y1": 0.05},
        "polygons": {"In1.Cu": [[[4.7, -10.3], [35.3, -10.3],
                                 [35.3, -9.7], [4.7, -9.7]]]},
        "vias": [],
        "ports": [p, q],
        "lumped_elements": [],
        "settings": {"margin_mm": 4.0},
    }


def near(lines, want, tol=1e-6):
    return any(abs(v - want) < tol for v in lines)


def test_via_center_line():
    """A via needs a mesh line at its CENTER, and not only at its edges.

    openEMS makes a metal primitive into PEC on the edges of the Yee
    grid. Thus a mesh NODE must lie inside the barrel. With the two edge
    lines alone, the nodes sit exactly on the surface of the cylinder,
    openEMS writes "Unused primitive (type: Cylinder)" and the via
    conducts NOTHING. The planes then stay separate, and the run gives
    an incorrect impedance with no error message.
    """
    m = model("stripline")
    m["vias"] = [{"x": 12.0, "y": -7.0, "r": 0.3, "z0": 0.0, "z1": 1.46}]
    xs, ys, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
    for lines, c, axis in ((xs, 12.0, "x"), (ys, -7.0, "y")):
        assert near(lines, c), "no mesh line at the via center on %s" % axis
        assert near(lines, c - 0.3) and near(lines, c + 0.3), \
            "no mesh line at the via edges on %s" % axis
    print("via center line OK")


def test_flat_and_vertical_ports():
    """Only a microstrip port goes down to the reference plane.

    openEMS refuses a CPW port and a stripline port whose start and stop
    are different in the direction of exc_dir, which is z for all three
    de-embedded types.
    """
    z_strip, z_ref = 0.973333, 0.486667
    for kind, want_z in (("msl", z_ref), ("cpw", z_strip),
                         ("stripline", z_strip)):
        m = model(kind, gap=0.3)
        g = runner._port_geometry(m, RES)[0]
        assert g["type"] == kind, "%s fell back to %s" % (kind, g["type"])
        assert g["start"][2] == z_strip, "%s: start is not on the strip" % kind
        assert abs(g["stop"][2] - want_z) < 1e-9, \
            "%s: stop z is %g, want %g" % (kind, g["stop"][2], want_z)
    print("flat/vertical port boxes OK")


def test_fallback_to_lumped():
    """A de-embedded type falls back when the geometry does not permit it."""
    for kind, bad in (("msl", {"direction": None}),
                      ("cpw", {"gap": None}),
                      ("stripline", {"height": None}),
                      ("cpw", {"direction": None, "gap": 0.3})):
        g = runner._port_geometry(model(kind, **bad), RES)[0]
        assert g["type"] == "lumped", \
            "%s with %s did not fall back" % (kind, bad)
    # A lumped port box is the full pad, from the reference plane up to
    # the plane of the pad.
    g = runner._port_geometry(model("msl", direction=None), RES)[0]
    assert g["start"][2] == 0.486667 and g["stop"][2] == 0.973333, \
        "the lumped box does not span the substrate"
    print("fallback to lumped OK")


def test_port_length_uses_list_position():
    """The 2-port length cap must use the list position, not the number.

    NOTES.md tells the user to edit model.json by hand. Such a file can
    hold numbers that are not 1..N in list order. An index by number then
    reads the wrong port, or goes past the end of the list, with no
    message.
    """
    m = model("msl")
    m["ports"][0]["number"], m["ports"][1]["number"] = 7, 3
    g = runner._port_geometry(m, RES)
    # The two ports are 30 mm apart, thus the cap is 0.3 * 30 = 9 mm.
    for k in (0, 1):
        assert abs(g[k]["msl_len"] - 9.0) < 1e-9, \
            "port %d length %g, want the 9 mm cap" % (k, g[k]["msl_len"])
    print("port length cap OK")


def test_strip_cells():
    """A stripline needs cells ACROSS the strip, as a CPW port has.

    Before, only the CPW branch of _mesh made them. The step across a
    stripline then came from the wavelength: the strip of 0.6 mm of
    validation/ is narrower than one cell of 2.355 mm at the coarse
    preset, and the port measured 19.4 ohm against 38.9 ohm from the
    theory. With the cells it gives 39.2 ohm at the SAME preset.
    """
    m = model("stripline")
    _, ys, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
    c, hw = -10.0, 0.3
    inside = [y for y in ys if c - hw - 1e-9 <= y <= c + hw + 1e-9]
    assert len(inside) >= runner.CPW_STRIP_CELLS - 1, \
        "only %d lines across the strip, want %d or more" \
        % (len(inside), runner.CPW_STRIP_CELLS - 1)
    # The mesh must grade outward, and not jump from 0.075 mm to res.
    out = sorted(y for y in ys if c + hw < y < c + hw + 4.0 * RES)
    steps = [b - a for a, b in zip([c + hw] + out, out)]
    assert steps and max(steps) < RES, \
        "the mesh jumps to the full step at the edge of the strip"
    print("stripline strip cells OK (%d across, %d graded outward)"
          % (len(inside), len(out)))


if __name__ == "__main__":
    test_via_center_line()
    test_flat_and_vertical_ports()
    test_fallback_to_lumped()
    test_port_length_uses_list_position()
    test_strip_cells()
    print("PASS")
