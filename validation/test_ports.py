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

    A user can edit model.json by hand. Such a file can hold numbers
    that are not 1..N in list order. An index by number then reads the
    wrong port, or goes past the end of the list, with no message.
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
    """A stripline and a microstrip need cells ACROSS the strip.

    Before, only the CPW branch of _mesh made them. The step across the
    strip then came from the wavelength: the stripline of 0.6 mm of
    validation/ is narrower than one cell of 2.355 mm at the coarse
    preset, and the port measured 19.4 ohm against 38.9 ohm from the
    theory. With the cells it gives 39.2 ohm at the SAME preset.

    A microstrip got the rule on 2026-08-05. Its track of 2.9 mm is
    WIDER than one coarse cell, thus its error was smaller and it looked
    like the usual mesh error: 44.3 ohm at coarse and 47.2 at medium
    against 50.0 from the theory. With the cells the same board gives
    49.3 ohm at coarse and at medium.
    """
    for kind, w in (("stripline", 0.6), ("msl", 2.9)):
        m = model(kind, track_width=w, width=w, length=w)
        _, ys, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
        c, hw = -10.0, 0.5 * w
        # A microstrip has its own count: refer to MSL_STRIP_CELLS.
        want = runner._strip_cells(kind)
        inside = [y for y in ys if c - hw - 1e-9 <= y <= c + hw + 1e-9]
        assert len(inside) >= want - 1, \
            "%s: only %d lines across the strip, want %d or more" \
            % (kind, len(inside), want - 1)
        # The mesh must grade outward, and not jump from a strip cell
        # to res.
        out = sorted(y for y in ys if c + hw < y < c + hw + 4.0 * RES)
        steps = [b - a for a, b in zip([c + hw] + out, out)]
        assert steps and max(steps) < RES, \
            "%s: the mesh jumps to the full step at the edge of the strip" \
            % kind
        print("%s strip cells OK (%d across, %d graded outward)"
              % (kind, len(inside), len(out)))


def test_cpw_z_cells():
    """A CPW port needs cells ABOVE and BELOW the plane of the line.

    A CPW has no plane below the strip that holds the field: the field
    goes from the strip across the two gaps, thus it is at its largest
    within about one gap width of the surface. With the step of the
    dielectric rule alone (0.38 mm on a board of 1.6 mm, against a gap of
    0.3 mm) the capacitance came out about 27% too large and the
    impedance 21% too small, at EVERY preset.
    """
    m = model("cpw", gap=0.3)
    _, _, zs = runner._mesh(m, runner._port_geometry(m, RES), RES)
    gap, z_strip = 0.3, 0.973333
    want = gap / runner.CPW_GAP_CELLS
    for side in (-1, 1):
        # The band of one gap width at that side of the plane of the line.
        out = sorted((z for z in zs if 1e-9 < side * (z - z_strip) <= gap),
                     reverse=side < 0)
        steps = [abs(b - a) for a, b in zip([z_strip] + out, out)]
        assert len(out) >= runner.CPW_GAP_CELLS, \
            "only %d lines within one gap on the %s side, want %d" \
            % (len(out), side, runner.CPW_GAP_CELLS)
        # The band holds 1.5 times the gap cell and not exactly the gap
        # cell: a line of the dielectric rule can fall between two lines
        # of this chain, and _merge_close then joins the two that are the
        # nearest. That moves a line by less than the merge tolerance.
        assert max(steps) <= 1.5 * want, \
            "a cell of %g mm on the %s side, want the gap cell %g mm" \
            % (max(steps), side, want)

    # The chain must NOT move the line of a copper plane. _merge_close
    # takes the mean of two lines that are near each other, thus a line
    # of this chain that lands beside a plane pulls the plane off its own
    # z. openEMS then writes "Unused primitive (type: LinPoly)" and that
    # copper is not metal, in the same way as the vias of 2026-08-03 (10).
    for c in m["copper_layers"]:
        assert near(zs, c["z"], 1e-9), \
            "the plane %s at z=%g has no mesh line" % (c["name"], c["z"])
    print("CPW z cells OK (%g mm above and below, planes kept)" % want)


def lumped(gap, width, x0=20.0, y0=-10.0, ny="x"):
    """Give an element box of `gap` along ny and `width` across it."""
    a, b = (gap, width) if ny == "x" else (width, gap)
    return {"ref": "C1", "type": "C", "value": 1e-12, "ny": ny,
            "layer": "In1.Cu", "package": "0402", "esl": 0.25e-9, "esr": 0.03,
            "start": [x0, y0, 0.973333], "stop": [x0 + a, y0 + b, 0.973333],
            "pads": [[x0 - 0.5, y0], [x0 + a + 0.5, y0]]}


def box_lines(m, res, margin=4.0):
    """Give the mesh lines that lie ON or IN the box of element 0."""
    m = dict(m, settings=dict(m["settings"], margin_mm=margin))
    xs, ys, _ = runner._mesh(m, runner._port_geometry(m, res), res)
    e = m["lumped_elements"][0]
    out = []
    for lines, k in ((xs, 0), (ys, 1)):
        lo, hi = e["start"][k], e["stop"][k]
        out.append([v for v in lines if lo - 1e-9 <= v <= hi + 1e-9])
    return e, out


def test_lumped_element_keeps_its_cells():
    """The box of a lumped element must hold a cell, at every mesh.

    _mesh adds the two faces of the box and nothing else, and
    _merge_close joined the lines that are nearer to each other than
    min(res/8, margin/20). Thus an element whose box was smaller than
    that tolerance kept ONE line and its box held no cell. The copper of
    the two pads then meets on that line: the gap CLOSES and the part
    becomes a piece of track. A run of the solver measured it - a series
    50 ohm in a gap of 0.15 mm gave S21 -0.16 dB, against -4.50 dB with
    the correction - and openEMS gave no warning at all.
    """
    m = model("msl")
    # (the gap of the part, the width, res, margin, the name)
    cases = [(0.50, 0.50, 2.355, 4.0, "0402 at 6 GHz, margin 4"),
             (0.30, 0.30, 2.355, 8.0, "0201 at 6 GHz, margin 8"),
             (0.20, 0.30, 2.355, 8.0, "01005 at 6 GHz, margin 8"),
             (0.50, 0.50, 7.070, 20.0, "0402 at 2 GHz, margin 20"),
             (0.30, 0.30, 7.070, 20.0, "0201 at 2 GHz, margin 20")]
    for gap, w, res, margin, name in cases:
        for ny in ("x", "y"):
            m["lumped_elements"] = [lumped(gap, w, ny=ny)]
            e, (in_x, in_y) = box_lines(m, res, margin)
            for k, inside, axis in ((0, in_x, "x"), (1, in_y, "y")):
                assert len(inside) >= 2, \
                    "%s (%s-axis): %d mesh line(s) in the box on %s, want 2 " \
                    "or more; the element is not in the model" \
                    % (name, ny, len(inside), axis)
                # The faces must stay where the part is. A face that the
                # merge MOVES makes the element larger or smaller than
                # the gap between the pads.
                for want in (e["start"][k], e["stop"][k]):
                    assert near(inside, want, 1e-9), \
                        "%s (%s-axis): the face at %s=%g moved" \
                        % (name, ny, axis, want)
    print("lumped element cells OK (%d cases, both axes)" % len(cases))


def test_shunt_element_and_its_via():
    """A part in SHUNT keeps its cells, and its via keeps its center line.

    The board of `run_lumped.make_shunt` puts a via in the ground copper
    below the part, thus the lines of the via and the faces of the
    element are on the same axis. Two rules must hold TOGETHER: the faces
    of the box do not move (the anchors, refer to the test above), and
    the via keeps a line at its center, or it conducts nothing and
    openEMS calls it an unused primitive.

    The cases are the land patterns of the libraries of KiCad 10, which
    `run_lumped.SHUNT_LAND` holds; this file keeps its own copy of the
    numbers, because it must not import pcbnew. The 0201 gap of 0.18 mm
    is the smallest box that any board here makes.

    **Why the via is not IN the ground pad.** The annular ring of a via
    is copper in the model. A drill of 0.6 mm gives a ring of 0.9 mm,
    which is wider than the 0.46 mm pad of an 0201, thus the ring would
    bridge a gap of 0.18 mm. The via goes SHUNT_VIA_DROP below the pad
    for every package, thus the distance from the via to the face of the
    box is a full pad plus that drop, and no tolerance of the merge can
    reach it. This test holds that distance.
    """
    drop = 0.75   # run_lumped.SHUNT_VIA_DROP
    # (the length of a pad, the gap, the name)
    cases = [(1.000, 0.50, "the land with no package"),
             (0.460, 0.18, "0201"),
             (0.540, 0.48, "0402"),
             (1.125, 1.80, "1206"),
             (1.225, 4.70, "2512")]
    for pad_l, gap, name in cases:
        m = model("msl")
        y0 = -12.7 - gap
        e = lumped(gap, 1.0, x0=19.5, y0=y0, ny="y")
        m["lumped_elements"] = [e]
        # The lower face of the box is the top edge of pad 2. The via is
        # one pad and one drop below it.
        vy = y0 - pad_l - drop
        m["vias"] = [{"x": 20.0, "y": vy, "r": 0.3, "z0": 0.0, "z1": 1.46}]
        e, (_, in_y) = box_lines(m, RES)
        assert len(in_y) >= 2, \
            "%s: %d mesh line(s) in the box on y, want 2 or more" \
            % (name, len(in_y))
        for want in (e["start"][1], e["stop"][1]):
            assert near(in_y, want, 1e-9), "%s: the face at y=%g moved" \
                % (name, want)
        _, ys, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
        assert near(ys, vy, 1e-9), \
            "%s: the via lost the line at its center, thus it conducts " \
            "nothing" % name
    print("shunt element and via OK (%d land patterns)" % len(cases))


def test_two_elements_near_each_other():
    """TWO element boxes on one axis must each keep their cells.

    The board of `run_lumped.make_shunt2` puts a C and an L in series
    from the line to ground, thus two boxes lie on the y axis with one
    pad between them. `_mesh` gives the faces of EVERY element to
    `_merge_close` as anchors, and this test holds that the second
    element does not lose its cells to the first.

    The separations go down to 0.20 mm, which is smaller than any pad
    that a real board has between two parts. The tolerance comes from
    one quarter of the smallest box, thus a box of 0.5 mm gives 0.125 mm
    and the two elements stay apart.
    """
    for sep in (2.00, 1.00, 0.50, 0.20):
        m = model("msl")
        gap = 0.5
        a = lumped(gap, 1.0, x0=19.5, y0=-12.7, ny="y")
        b = lumped(gap, 1.0, x0=19.5, y0=-12.7 - gap - sep, ny="y")
        b["ref"] = "L1"
        m["lumped_elements"] = [a, b]
        _, ys, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
        for e in (a, b):
            lo, hi = e["start"][1], e["stop"][1]
            inside = [v for v in ys if lo - 1e-9 <= v <= hi + 1e-9]
            assert len(inside) >= 2, \
                "a separation of %.2f mm: %s has %d mesh line(s) in its box" \
                % (sep, e["ref"], len(inside))
            for want in (lo, hi):
                assert near(ys, want, 1e-9), \
                    "a separation of %.2f mm: the face of %s at y=%g moved" \
                    % (sep, e["ref"], want)
    print("two elements near each other OK (4 separations)")


def test_lumped_element_does_not_wreck_the_mesh():
    """The clamp for the elements must not make a very fine mesh.

    The tolerance comes down to one quarter of the smallest box. Thus a
    small part must not multiply the number of lines of the full mesh.
    """
    m = model("msl")
    n0 = [len(a) for a in runner._mesh(m, runner._port_geometry(m, RES), RES)]
    m["lumped_elements"] = [lumped(0.2, 0.3)]
    n1 = [len(a) for a in runner._mesh(m, runner._port_geometry(m, RES), RES)]
    assert all(b <= a + 6 for a, b in zip(n0, n1)), \
        "the element changed the mesh from %s to %s lines" % (n0, n1)
    print("lumped element mesh cost OK (%s -> %s lines)" % (n0, n1))


if __name__ == "__main__":
    test_via_center_line()
    test_flat_and_vertical_ports()
    test_fallback_to_lumped()
    test_port_length_uses_list_position()
    test_strip_cells()
    test_cpw_z_cells()
    test_lumped_element_keeps_its_cells()
    test_shunt_element_and_its_via()
    test_two_elements_near_each_other()
    test_lumped_element_does_not_wreck_the_mesh()
    print("PASS")
