"""Tests of the port geometry and the mesh, with no KiCad and no solver.

`runner` imports CSXCAD and openEMS only in its functions. Thus
`_port_geometry` and `_mesh` use only numpy, and this file runs in some
seconds. One test calls `build()`, which uses CSXCAD, but no engine runs.
Run it with the python of the solver:

    C:\\openEMS\\venv\\Scripts\\python.exe test_ports.py

These tests hold the rules that the four port types obey. A failure here
shows an incorrect model BEFORE a run of some minutes shows an incorrect
number.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins"))

import runner  # noqa: E402
import solverenv  # noqa: E402

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
    """A via must have a mesh line at its CENTER, and its surface lines go
    IN.

    openEMS makes a metal primitive into PEC on the edges of the Yee grid.
    Thus a mesh NODE must be in the barrel. With only the two edge lines,
    the nodes are on the surface of the cylinder. openEMS then writes
    "Unused primitive (type: Cylinder)", and no current flows in the via.
    The planes then have no connection, and the run gives an incorrect
    impedance with no error message.

    **A node on the surface counts only when the doubles put it in the
    barrel.** For that cause, the two surface lines go 1 ppm in
    (`runner.VIA_SURFACE`). 12.3 - 12.0 = 0.3000000000000007 is OUT OF r =
    0.3. Thus the barrel of a 0.6 mm drill held ONE PEC edge, and it read
    +155% in inductance against Goldfarb and Pucel. A barrel of 5 edges
    reads +13% to +20%. The nudge is 0.3 nm on this via, thus it cannot
    move the geometry.
    """
    m = model("stripline")
    m["vias"] = [{"x": 12.0, "y": -7.0, "r": 0.3, "z0": 0.0, "z1": 1.46}]
    xs, ys, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
    for lines, c, axis in ((xs, 12.0, "x"), (ys, -7.0, "y")):
        assert near(lines, c), "no mesh line at the via center on %s" % axis
        assert near(lines, c - 0.3) and near(lines, c + 0.3), \
            "no mesh line at the via edges on %s" % axis
        for want in (c - 0.3, c + 0.3):
            got = min(lines, key=lambda v: abs(v - want))
            assert abs(got - c) < 0.3, \
                "the surface line at %s=%.9f stands %.12f mm from the " \
                "centre, thus ON or outside the barrel of r = 0.3" \
                % (axis, got, abs(got - c))
    print("via center line OK (and the surface lines stand inside)")


def test_via_lines_are_anchors():
    """A copper edge near a via moves to the via, and not the via to it.

    `_merge_close` merges two lines that are nearer to each other than
    `tol` at their MEAN. Thus a copper edge near a via moved the line of
    that via out of the barrel. 19 of the 102 axis cases of the boards of
    `validation/` did not keep a line for that cause. The 0402 and the 0603
    land of the ESL table were two of them. At this time, the three lines
    of a via are anchors, as the two faces of an element box are. The
    copper edge then moves. No cell becomes smaller, because the edge is
    nearer than `tol` to a line of the mesh.

    **The face of an element has a higher rank than a via.** A face that
    moves can CLOSE the gap of the part and give a piece of track with no
    message. A via that does not keep one line keeps the others. The second
    half of this test holds that sequence.
    """
    c, r = 12.0, 0.3
    # The edge of a pad 5 microns from the surface line of the via. That is
    # less than all values of `tol` that this board can give.
    edge = c + r - 0.005
    m = model("stripline")
    m["vias"] = [{"x": c, "y": -7.0, "r": r, "z0": 0.0, "z1": 1.46}]
    m["polygons"]["F.Cu"] = [[[edge, -7.6], [edge + 2.0, -7.6],
                              [edge + 2.0, -6.4], [edge, -6.4]]]
    xs, _, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
    for want in (c - r * runner.VIA_SURFACE, c, c + r * runner.VIA_SURFACE):
        assert near(xs, want, 1e-9), \
            "the via lost its line at x=%.9f to the copper edge" % want
    assert not near(xs, edge, 1e-9), \
        "the copper edge at x=%.6f kept its own line, thus the merge made " \
        "a cell of 5 microns" % edge
    # The same via, and then a face of an element box near the SAME surface
    # line. The face keeps its value, and the via removes that one line.
    # That is the sequence of the ranks.
    m["polygons"].pop("F.Cu")
    m["lumped_elements"] = [
        {"ref": "C1", "type": "C", "value": 1e-12, "ny": "x", "layer": "F.Cu",
         "start": [edge, -7.6, 1.46], "stop": [edge + 0.5, -6.4, 1.46]}]
    xs, _, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
    assert near(xs, edge, 1e-9), \
        "the face of the element box moved: the gap of the part can close"
    assert near(xs, c, 1e-9), "the via lost the line at its centre"
    print("via lines are anchors OK (a copper edge yields, an element "
          "face does not)")


def test_flat_and_vertical_ports():
    """Only a microstrip port goes down to the reference plane.

    openEMS refuses a CPW port and a stripline port when the start and the
    stop are different in the direction of exc_dir. exc_dir is z for all
    three de-embedded types.
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
    """A de-embedded type changes to lumped when the geometry does not let
    it be used."""
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

    A user can edit model.json by hand. Such a file can have numbers that
    are not 1..N in list sequence. An index by number then reads the
    incorrect port, or it goes after the end of the list, with no message.
    """
    m = model("msl")
    m["ports"][0]["number"], m["ports"][1]["number"] = 7, 3
    g = runner._port_geometry(m, RES)
    # The two ports are 30 mm apart, thus the cap is 0.3 * 30 = 9 mm.
    for k in (0, 1):
        assert abs(g[k]["msl_len"] - 9.0) < 1e-9, \
            "port %d length %g, want the 9 mm cap" % (k, g[k]["msl_len"])
    print("port length cap OK")


def test_port_length_is_capped_by_the_copper_run():
    """A de-embedded port must not go across the end of its feed line.

    The length is `max(3*w, 6*res)`, and `6*res` is 14 mm at the coarse
    preset. A short feed line is shorter than that. The measurement plane
    is at the MIDDLE of the port, thus it is then in the patch that the
    line feeds. Also, each de-embedded port adds a metal strip above its
    box, and that strip goes out across the end of the copper.
    `board_reader.copper_run` measures the run, and the runner caps the
    length with 0.8 of it.
    """
    # No cap: the copper goes farther than the limit of the measurement.
    m = model("msl", copper_run=None)
    m["ports"] = [m["ports"][0]]          # 1 port, thus no 2-port cap
    g = runner._port_geometry(m, RES)[0]
    free = g["msl_len"]
    assert free > 13.0, "the free length is %.2f mm, want 6*res" % free

    # A feed line of 5 mm: the port must stop in it.
    m = model("msl", copper_run=5.0)
    m["ports"] = [m["ports"][0]]
    g = runner._port_geometry(m, RES)[0]
    assert abs(g["msl_len"] - 4.0) < 1e-9, \
        "a copper run of 5 mm gives a port of %.3f mm, want 4.0" % g["msl_len"]
    assert g["msl_len"] < free, "the cap did not make the port shorter"
    # The measurement plane is at the middle, thus it must stay a
    # sufficient distance in the copper.
    assert 0.5 * g["msl_len"] < 5.0, "the measurement plane is past the end"

    # A run that is LONGER than the free length must change nothing.
    m = model("msl", copper_run=40.0)
    m["ports"] = [m["ports"][0]]
    g = runner._port_geometry(m, RES)[0]
    assert abs(g["msl_len"] - free) < 1e-9, \
        "a long copper run must not shorten the port: %.3f" % g["msl_len"]
    print("port length cap by the copper run OK (5 mm run -> 4.0 mm port)")


def test_strip_cells():
    """A stripline and a microstrip must have cells ACROSS the strip.

    Before, only the CPW branch of _mesh made them. The step across the
    strip then came from the wavelength. The stripline of 0.6 mm of
    validation/ is narrower than one cell of 2.355 mm at the coarse preset.
    The port measured 19.4 ohm against 38.9 ohm from the theory. With the
    cells, it gives 39.2 ohm at the SAME preset.

    A microstrip got the rule on 2026-08-05. Its track of 2.9 mm is WIDER
    than one coarse cell. Thus its error was smaller, and it looked like
    the usual mesh error: 44.3 ohm at coarse and 47.2 at medium, against
    50.0 from the theory. With the cells, the same board gives 49.3 ohm at
    coarse and at medium.
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
    """A CPW port must have cells ABOVE and BELOW the plane of the line.

    A CPW has no plane below the strip that holds the field. The field goes
    from the strip across the two gaps. Thus it is at its largest in about
    one gap width from the surface. Before, only the dielectric rule set
    the step: 0.38 mm on a board of 1.6 mm, against a gap of 0.3 mm. The
    capacitance was then about 27% too large, and the impedance 21% too
    small, at EACH preset.
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
        # The band holds 1.5 times the gap cell, and not only the gap cell.
        # A line of the dielectric rule can be between two lines of this
        # chain. _merge_close then merges the two nearest lines. That moves
        # a line by less than the merge tolerance.
        assert max(steps) <= 1.5 * want, \
            "a cell of %g mm on the %s side, want the gap cell %g mm" \
            % (max(steps), side, want)

    # The chain must NOT move the line of a copper plane. _merge_close uses
    # the mean of two lines that are near each other. Thus a line of this
    # chain that is near a plane pulls the plane off its own z. openEMS
    # then writes "Unused primitive (type: LinPoly)", and that copper is
    # not metal. The vias of 2026-08-03 (10) had the same failure.
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
    """Give the mesh lines that are ON or IN the box of element 0."""
    m = dict(m, settings=dict(m["settings"], margin_mm=margin))
    xs, ys, _ = runner._mesh(m, runner._port_geometry(m, res), res)
    e = m["lumped_elements"][0]
    out = []
    for lines, k in ((xs, 0), (ys, 1)):
        lo, hi = e["start"][k], e["stop"][k]
        out.append([v for v in lines if lo - 1e-9 <= v <= hi + 1e-9])
    return e, out


def test_lumped_element_keeps_its_cells():
    """The box of a lumped element must hold a cell, at each mesh.

    _mesh adds only the two faces of the box. _merge_close merged the lines
    that are nearer to each other than min(res/8, margin/20). Thus an
    element with a box smaller than that tolerance kept ONE line, and its
    box had no cell. The copper of the two pads then touches on that line:
    the gap CLOSES, and the part becomes a piece of track. A run of the
    solver measured it. A series 50 ohm in a gap of 0.15 mm gave S21
    -0.16 dB, against -4.50 dB with the correction. openEMS gave no
    warning.
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


def test_one_cell_in_series_across_a_lumped_element():
    """The gap of a part keeps ONE cell along the current.

    The gap between the two pads is a narrow copper feature. Thus
    `_feature_lines` put a line at the middle of it, and the element was on
    2 cells in series. **2 is the ONE bad count** for the stability of a
    lumped inductor. The reference board at 10 nH turns at 2.09 ns with 2
    cells, and the runner refuses the run. It accepts 1, 3, 4 and 6 cells,
    and it accepts 1 cell at er 2.2, 4.5 and 10.2. The count in the box
    does not change the value that the engine models: 0.3105 nH against
    0.3104 nH on the shunt board. Thus the removal of that cell costs
    nothing.

    The rule uses the gap of the element on ITS OWN layer and along its own
    axis. A feature of a different layer is a track or a slot, and it keeps
    its line. A feature that goes out of the box also keeps its line.

    The count of lines IN a box is not all of the test of this rule. The
    grade of a port puts lines adjacent to the strip. Thus the box of a
    part across the track also holds them. The rule itself comes first.
    Then comes a series part in the track, which is the board of
    `run_rlc.py`. Its box is on the axis of the line, where the grade of
    the port makes no line.
    """
    a, b = 20.0, 20.5                       # the gap between two pads
    res, tol = 2.355, 0.125
    got, _ = runner._feature_lines([a, b], [a, b], res, tol)
    assert near(got, 0.5 * (a + b)), \
        "a gap that no element covers must keep its middle line: %s" \
        % sorted(got)
    got, _ = runner._feature_lines([a, b], [a, b], res, tol,
                                   one_cell=[(a, b)])
    assert not got, \
        "the gap of an element took a line, thus 2 cells in series: %s" \
        % sorted(got)
    # The copper adjacent to the box is a feature of its own.
    got, _ = runner._feature_lines([a - 0.3, a], [a - 0.3, a], res, tol,
                                   one_cell=[(a, b)])
    assert near(got, a - 0.15), \
        "a feature beside the box lost its line: %s" % sorted(got)
    # A series part in the track. The mesh of HEAD put a line at
    # 20.25 here. `mesh_diff.py` lists that line for each board of
    # `run_rlc.py`.
    m = model("msl")
    e = lumped(0.5, 0.5)                    # ny="x", the gap 20.0 to 20.5
    m["lumped_elements"] = [e]
    xs, _, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
    lo, hi = sorted((e["start"][0], e["stop"][0]))
    inside = [v for v in xs if lo - 1e-9 <= v <= hi + 1e-9]
    assert len(inside) == 2, \
        "the box of the part holds %d mesh line(s), want the 2 faces " \
        "alone, thus ONE cell in series: %s" % (len(inside), inside)
    print("one cell in series OK (the rule, and a series part with 2 faces)")


def test_shunt_element_and_its_via():
    """A part in SHUNT keeps its cells, and its via keeps its center line.

    The board of `run_lumped.make_shunt` puts a via in the ground copper
    below the part. Thus the lines of the via and the faces of the element
    are on the same axis. Two rules must be true TOGETHER:

    - the faces of the box do not move (the anchors, refer to the test
      above);
    - the via keeps a line at its center. If not, no current flows in it,
      and openEMS writes "Unused primitive".

    The cases are the land patterns of the libraries of KiCad 10, which
    `run_lumped.SHUNT_LAND` holds. This file keeps its own copy of the
    numbers, because it must not import pcbnew. The 0201 gap of 0.18 mm is
    the smallest box that the boards here make.

    **Why the via is not IN the ground pad.** The annular ring of a via is
    copper in the model. A drill of 0.6 mm gives a ring of 0.9 mm. That is
    wider than the 0.46 mm pad of an 0201, thus the ring can bridge a gap
    of 0.18 mm. The via goes SHUNT_VIA_DROP below the pad for all packages.
    Thus the distance from the via to the face of the box is a full pad
    plus that drop. No tolerance of the merge can get to it. This test
    holds that distance.
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

    The board of `run_lumped.make_shunt2` puts a C and an L in series from
    the line to ground. Thus two boxes are on the y axis, with one pad
    between them. `_mesh` gives the faces of ALL elements to `_merge_close`
    as anchors. This test holds that the second element keeps its cells.

    The distances go down to 0.20 mm. That is smaller than the pads between
    two parts on a usual board. The tolerance comes from one quarter of the
    smallest box. Thus a box of 0.5 mm gives 0.125 mm, and the two elements
    stay apart.
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
    """The limit for the elements must not make a very small mesh.

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


def test_a_feature_stays_on_one_layer():
    """Two edges on two DIFFERENT layers make no narrow feature.

    `_feature_lines` puts a mesh line in the space between two straight
    copper edges that are nearer than `res`. That space is a track, a gap
    between two pads, or a slot. An F.Cu edge and a B.Cu edge hold no such
    space, because the copper of two layers does not touch in the plane.
    Until 2026-09-01, the rule used the edges of all layers together. It
    put a line at y = -20.3875 on the 2512 land of `run_shunt.py`, between
    an F.Cu edge at -20.775 and a B.Cu edge at -20.000. `mesh_diff.py`
    lists the two boards that the rule moved.

    The same two edges on ONE layer are a gap of 0.775 mm, and that gap
    must get its line. Thus the test cannot be satisfactory with a rule
    that gives no line.
    """
    lo, hi = -20.775, -20.000

    def rect(y0, y1):
        return [[10.0, y0], [30.0, y0], [30.0, y1], [10.0, y1]]

    for below, above, want in (("F.Cu", "B.Cu", False),
                               ("F.Cu", "F.Cu", True)):
        m = model("msl")
        m["polygons"] = dict(m["polygons"])
        m["polygons"].setdefault(below, []).append(rect(-25.0, lo))
        m["polygons"].setdefault(above, []).append(rect(hi, -15.0))
        _, ys, _ = runner._mesh(m, runner._port_geometry(m, RES), RES)
        got = [y for y in ys if lo + 1e-9 < y < hi - 1e-9]
        if want:
            assert near(got, 0.5 * (lo + hi)), \
                "a gap of %.3f mm on %s got no line: %s" % (hi - lo, below, got)
        else:
            assert not got, \
                "an edge on %s and an edge on %s made a feature: lines %s" \
                % (below, above, got)
    print("a feature stays on one layer OK (F.Cu + B.Cu: no line; "
          "F.Cu + F.Cu: the line at %.4f)" % (0.5 * (lo + hi)))


def test_a_series_rlc_part_is_one_element():
    """A series RLC part goes to openEMS as ONE element with LEtype=1.

    The part must give the SAME element as a capacitor with its ESR and its
    ESL, because `run_shunt.py` measures that element through the solver. A
    part with no C must give the element of an inductor with its DCR. The
    test builds each model with `runner.build`, and it compares the XML
    that CSXCAD writes. No engine runs, but `build` uses CSXCAD. Thus run
    this file with the python of the solver.

    The timestep rule must count the L of such a part. A large L that the
    rule does not see diverges.
    """
    import tempfile
    import xml.etree.ElementTree as ET

    def element(**part):
        m = model("msl")
        e = lumped(0.5, 0.6)
        e.update(ref="X1", package="Custom", esl=0.0, esr=0.0)
        e.update(part)
        m["lumped_elements"] = [e]
        m["settings"] = dict(m["settings"], f_start=1e9, f_stop=6e9,
                             z0=50.0, mesh="coarse", max_timesteps=1000,
                             end_criteria=1e-4, lumped=True, parasitics=True)
        fdtd = runner.build(m, 0, RES)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "csx.xml")
            fdtd.GetCSX().Write2XML(path)
            found = [dict(p.attrib) for p in ET.parse(path).getroot().iter()
                     if p.get("Name") == "le_X1"]
        assert len(found) == 1, "want one element le_X1, got %d" % len(found)
        found[0].pop("ID", None)
        return found[0]

    rlc = element(type="RLC", value=None, r=0.5, l=1e-9, c=1e-11)
    cap = element(type="C", value=1e-11, esl=1e-9, esr=0.5)
    assert rlc == cap, "the series RLC %s against the capacitor %s" % (rlc, cap)
    assert float(rlc.get("LEtype", "nan")) == 1.0, rlc
    rl = element(type="RLC", value=None, r=0.5, l=1e-9, c=None)
    ind = element(type="L", value=1e-9, esr=0.5)
    assert rl == ind, "the series RL %s against the inductor %s" % (rl, ind)
    factor = runner._time_step_factor(
        {"settings": {"lumped": True, "parasitics": True},
         "lumped_elements": [{"type": "RLC", "value": None, "l": 100e-9}]})
    assert factor is not None \
        and abs(factor - runner.LE_STAB_MARGIN / 10.0) < 1e-9, factor
    print("a series RLC part is one element OK (%s; 100 nH gives the "
          "factor %.3g)" % (", ".join("%s=%s" % kv for kv in sorted(
              rlc.items())), factor))


def test_an_inductor_with_an_epc_is_two_elements():
    """An EPC makes a SECOND element, on the other half of the land.

    Two lumped elements cannot share one box. The engine gives the cells of
    a box to only one property, and the other one has no effect and gives
    no message. Thus the part keeps one half of the land across the
    current, and the EPC gets the other half. The two are in parallel,
    because the two bridge the same gap.

    **The EPC element uses `LEtype=0`, and not the `LEtype=1` of all the
    other elements.** The same geometry with `LEtype=1` on this element put
    the self-resonance of a 10 nH inductor 30% LOW. `LEtype=0` put it in 1%
    of the correct value on a wide land.

    **The line that divides the land is a line that the mesh has.** Thus
    the mesh does not move. A new line there makes a smaller cell, and it
    costs the timestep of all the board. A land of ONE cell has no such
    line. The part then keeps all the land, and it gets a warning.
    """
    import tempfile
    import xml.etree.ElementTree as ET

    def boxes(width, y0=-10.0, epc=0.28e-12):
        m = model("msl")
        e = lumped(0.5, width, y0=y0, ny="x")
        e.update(ref="L1", type="L", value=1e-8, package="Custom",
                 esl=0.0, esr=0.1, epc=epc)
        m["lumped_elements"] = [e]
        m["settings"] = dict(m["settings"], f_start=1e9, f_stop=6e9,
                             z0=50.0, mesh="coarse", max_timesteps=1000,
                             end_criteria=1e-4, lumped=True, parasitics=True)
        fdtd = runner.build(m, 0, RES)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "csx.xml")
            fdtd.GetCSX().Write2XML(path)
            root = ET.parse(path).getroot()
            out = {}
            for prop in root.iter():
                if prop.get("Name") in ("le_L1", "epc_L1"):
                    # A Box holds its two corners in P1 and P2, and not
                    # in its own attributes.
                    box = [[float(c.get("Y")) for c in b]
                           for b in prop.iter("Box")]
                    out[prop.get("Name")] = (dict(prop.attrib), box[0])
        return out

    # A land of 2.9 mm holds 4 cells, thus each box keeps 2 of them.
    got = boxes(2.9)
    assert set(got) == {"le_L1", "epc_L1"}, \
        "want the part and its EPC, got %s" % sorted(got)
    assert float(got["epc_L1"][0]["LEtype"]) == 0, \
        "the EPC must take LEtype=0, got %s" % got["epc_L1"][0].get("LEtype")
    assert float(got["le_L1"][0]["LEtype"]) == 1, "the part keeps LEtype=1"
    assert abs(float(got["epc_L1"][0]["C"]) - 0.28e-12) < 1e-18, \
        "the EPC lost its value: %s" % got["epc_L1"][0].get("C")
    # The two boxes TOUCH on one line, and together they fill the land. One
    # cell of the land between them moves the self-resonance to -16%.
    part, epc = got["le_L1"][1], got["epc_L1"][1]
    assert abs(part[1] - epc[0]) < 1e-9, \
        "the two boxes do not abut: %g and %g" % (part[1], epc[0])
    assert abs(part[0] - (-10.0)) < 1e-9 and abs(epc[1] - (-7.1)) < 1e-9, \
        "the pair does not cover the land: %g..%g" % (part[0], epc[1])
    # A land that holds ONE cell cannot be divided. The part keeps all the
    # land, and the run tells it. The cells across the strip of this port
    # are 0.15 mm apart. Thus a land of 0.13 mm between two of them has no
    # line in it. Its own two faces are anchors, and they get the two
    # adjacent lines.
    got = boxes(0.13, y0=-10.14)
    assert set(got) == {"le_L1"}, \
        "a land of one cell must keep the part alone, got %s" % sorted(got)
    print("an inductor with an EPC is two elements OK (LEtype=0 beside "
          "LEtype=1, and one cell keeps the part alone)")


def test_an_open_part_leaves_the_grid():
    """A part that is an open at all frequencies of the sweep does not go
    into the grid.

    `solverenv.smallest_z` gives the smallest |Z| of a series branch in the
    sweep with no samples, because the reactance only increases with the
    frequency. A sampled sweep can miss a sharp series resonance. It can
    then call a part an open when the part is a short at one frequency.
    `solverenv.is_open` compares the value with `OPEN_Z_RATIO` times z0.

    Such a part gives NO element in the XML of `build`, and its gap stays
    open. The element in the grid read S21 about 5 dB BELOW the bare gap at
    1/600 of the Courant step. An inductor with an EPC keeps only the EPC,
    on the classic path, because at GHz a choke on a board is that
    capacitance. A part that is not an open goes into the grid as before.
    With `open_as_gap` = False, all parts go into the grid as before. The
    timestep rule must not use the same parts that `build` does not use.
    """
    import tempfile
    import xml.etree.ElementTree as ET

    # The rule itself, on cases with a known answer.
    z = solverenv.smallest_z
    assert abs(z(0, 90e-6, None, 1e9, 6e9) - 2e9 * math.pi * 90e-6) < 1e-6
    assert abs(z(0, None, 1e-12, 1e9, 6e9) - 1 / (12e9 * math.pi * 1e-12)) \
        < 1e-9, "a C is at its smallest at the HIGH end"
    # 10 nH with 0.1 pF resonates at 5.03 GHz, in the sweep. The smallest
    # |Z| is then equal to R, and no sample can give that accurately.
    assert z(1.0, 10e-9, 0.1e-12, 1e9, 6e9) == 1.0
    assert not solverenv.is_open(None, None, None, 1e9, 6e9, 50.0), \
        "a branch with no component is not an open"

    def elements(**part):
        s = part.pop("settings", {})
        m = model("msl")
        e = lumped(0.5, 0.6)
        e.update(ref="X1", package="Custom", esl=0.0, esr=0.0)
        e.update(part)
        m["lumped_elements"] = [e]
        m["settings"] = dict(m["settings"], f_start=1e9, f_stop=6e9,
                             z0=50.0, mesh="coarse", max_timesteps=1000,
                             end_criteria=1e-4, lumped=True, parasitics=True)
        m["settings"].update(s)
        fdtd = runner.build(m, 0, RES)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "csx.xml")
            fdtd.GetCSX().Write2XML(path)
            found = {p.get("Name"): dict(p.attrib)
                     for p in ET.parse(path).getroot().iter()
                     if p.get("Name", "").endswith("_X1")}
        return found, runner._time_step_factor(m)

    got, tsf = elements(type="L", value=90e-6)
    assert got == {} and tsf is None, \
        "90 uH is an open over 1 to 6 GHz: no element and no factor, got " \
        "%s and %s" % (sorted(got), tsf)
    got, tsf = elements(type="RLC", value=None, r=50.0, l=90e-6, c=100e-12)
    assert got == {} and tsf is None, "the RLC of the screenshot: %s" % got
    got, tsf = elements(type="L", value=90e-6, epc=0.2e-12)
    assert set(got) == {"epc_X1"} and tsf is None, \
        "an open choke keeps its EPC alone, got %s" % sorted(got)
    assert float(got["epc_X1"].get("LEtype", 0)) == 0.0, \
        "a lone C takes the classic path (LEtype 0): %s" % got["epc_X1"]
    got, tsf = elements(type="L", value=10e-9)
    assert set(got) == {"le_X1"} and tsf is not None, \
        "10 nH carries the signal and stays in the grid"
    got, tsf = elements(type="L", value=90e-6,
                        settings={"open_as_gap": False})
    assert set(got) == {"le_X1"} and abs(tsf - 0.5 / 300) < 1e-12, \
        "open_as_gap False puts the part in the grid as before"
    print("an open part leaves the grid OK (no element, no factor; the EPC "
          "of a choke stays alone)")


def test_the_run_says_what_it_chose():
    """`_decisions` names each decision that the run makes BY ITSELF.

    The user gives the values. The run makes these decisions:

    - if a part goes into the grid;
    - the path of openEMS for the part;
    - the timestep of the board, and its cause.

    It also changes a port to a lumped port when that is necessary. Each
    line must name the decision AND its cause. The cause of the timestep
    must name the part that set it. Before, the log gave "lumped inductor
    stability", also when a RESISTOR set the factor.

    The number of the timestep and its cause come from ONE function,
    `_time_step_choice`. Thus they cannot disagree.
    """
    def lines(part, port=None, **s):
        m = model("msl", **(port or {}))
        e = lumped(0.5, 0.6)
        e.update(ref="X1", package="Custom", esl=0.0, esr=0.0)
        e.update(part)
        m["lumped_elements"] = [e]
        m["settings"] = dict(m["settings"], f_start=1e9, f_stop=6e9,
                             z0=50.0, mesh="coarse", max_timesteps=300000,
                             end_criteria=1e-4, lumped=True, parasitics=True)
        m["settings"].update(s)
        got = runner._decisions(m, RES)
        f, why = runner._time_step_choice(m)
        assert f == runner._time_step_factor(m), \
            "the reason and the number must come from one place"
        assert "The timestep is " + why in got, got
        return got

    got = lines(dict(type="L", value=90e-6))
    assert "an open circuit from 1 to 6 GHz" in got[0] and "565 kohm" in got[0] \
        and "its gap stays open" in got[0], got
    assert got[-1] == ("The timestep is the full Courant step, because no "
                       "element must have a smaller step"), got
    got = lines(dict(type="L", value=10e-9))
    assert "the series path (LEtype 1), because it has an inductance" \
        in got[0], got
    assert "set by the inductance of X1, 10 nH (0.5/sqrt(L[nH]))" in got[1], got
    assert got[2].startswith("The step limit is 1897366"), got
    # A resistor on the series path is there for the ESL of its body, and
    # its resistance sets the factor. The log must tell the two. 90 ohm
    # keeps the 0603 body, because that body moves |Z| by 2.2% at 6 GHz.
    got = lines(dict(type="R", value=90.0, esl=0.5e-9, package="0603"))
    assert got[0].startswith("X1 (R 90 ohm + L 500 pH, with the parasitics "
                             "of its 0603 body)"), got
    assert "because the ESL of its body is an inductance" in got[0], got
    assert ("set by the resistance of X1 in a series branch, 90 ohm "
            "(5.8/sqrt(R[ohm]))") in got[1], got
    # **P21**: 1 kohm with the same body removes the body, tells why, and
    # uses the classic path at the full step.
    got = lines(dict(type="R", value=1000.0, esl=0.5e-9, package="0603"))
    assert got[0].startswith("X1 (R 1 kohm, without the ESL 500 pH of its "
                             "0603 body, which changes |Z| by 0.018% or "
                             "less from 1 to 6 GHz (the limit is 2%))"), got
    assert "the classic path (LEtype 0)" in got[0], got
    assert got[-1] == ("The timestep is the full Courant step, because no "
                       "element must have a smaller step"), got
    got = lines(dict(type="R", value=1000.0, esl=0.5e-9, package="0603"),
                keep_idle_body=True)
    assert "the series path (LEtype 1)" in got[0], \
        "keep_idle_body keeps the body for a rig: %s" % got
    got = lines(dict(type="R", value=1000.0), parasitics=False)
    assert "the classic path (LEtype 0)" in got[0], got
    got = lines(dict(type="L", value=10e-9), time_step_factor=0.3)
    assert ("0.3, from the Timestep field of the settings, which is more "
            "important than the rule (the rule gives 0.1581") in got[1], got
    got = lines(dict(type="L", value=90e-6, epc=2.81e-12))
    assert "only its EPC of 2.81 pF stays, on the classic path" in got[0], got
    got = lines(dict(type="L", value=10e-9), port={"direction": None})
    assert got[0].startswith("Port 1 is a lumped port, and not a msl, because it has "
                             "no track"), got
    got = lines(dict(type="R", value=None))
    assert "X1 is not modelled" in got[0], got

    # **The position of an EPC comes from the MESH.** Thus `main` gets it
    # after the first build. The land of the EPC test: 2.9 mm divides, and
    # 0.13 mm at the offset of that test holds one cell and cannot.
    def epc_lines(width, y0):
        m = model("msl")
        e = lumped(0.5, width, y0=y0, ny="x")
        e.update(ref="L1", type="L", value=1e-8, package="Custom",
                 esl=0.0, esr=0.1, epc=0.28e-12)
        m["lumped_elements"] = [e]
        m["settings"] = dict(m["settings"], f_start=1e9, f_stop=6e9,
                             z0=50.0, mesh="coarse", max_timesteps=1000,
                             end_criteria=1e-4, lumped=True, parasitics=True)
        fdtd = runner.build(m, 0, RES)[0]
        return runner._epc_decisions(m, fdtd.GetCSX().GetGrid())

    got = epc_lines(2.9, -10.0)
    assert len(got) == 1 and "is adjacent to the part" in got[0] \
        and "280 fF" in got[0], got
    got = epc_lines(0.13, -10.14)
    assert len(got) == 1 and "is removed" in got[0], got
    # The notes of `board_reader` (a part at an angle, a package that can
    # be metric) come FIRST, and not in the warning box before the run.
    m = model("msl")
    m["settings"] = dict(m["settings"], f_start=1e9, f_stop=6e9, z0=50.0,
                         mesh="coarse", max_timesteps=300000,
                         end_criteria=1e-4, lumped=False)
    m["notes"] = ["R3 is at an angle, thus RFsim models it as an element on "
                  "the x axis"]
    got = runner._decisions(m, RES)
    assert got[0] == m["notes"][0], got
    print("the run says what it chose OK (the path, the open, the source "
          "of the timestep, a port that falls back, where the EPC went, "
          "the notes of the reader)")


def test_a_body_that_changes_nothing_stays_out():
    """P21: a body goes into the element only where it changes the part.

    A resistor or a capacitor with a body is two components or more. Thus
    it uses the series path of openEMS. That path changes S21 much more
    than the body models: 1.67 dB at 200 ohm and 5.47 dB at 1 kohm. The ESL
    of an 0603 body changes S21 by 0.017 and 0.0013 dB. Thus a body that
    moves |Z| by `PARASITIC_MIN` or less in the sweep stays out. The part
    then uses the classic path.

    **The rule is accurate at a resonance.** A capacitor can have its
    series resonance with its ESL in the sweep. It then has |Z| = ESR
    there, thus it keeps its body. No sample sets the result.
    """
    idle, effect = solverenv.body_is_idle, solverenv.parasitic_effect
    # An 0603 body of 0.5 nH on a resistor, 1 to 6 GHz: the boundary is
    # 5 w_stop ESL = 94.2 ohm.
    body = {"L": 0.5e-9}
    assert not idle(dict(body, R=90.0), "R", 1e9, 6e9)
    assert idle(dict(body, R=95.0), "R", 1e9, 6e9)
    assert idle(dict(body, R=1000.0), "R", 1e9, 6e9)
    assert abs(effect(dict(body, R=1000.0), "R", 1e9, 6e9) - 1.776e-4) < 1e-6
    assert not idle(dict(body, R=1000.0), "R", 1e9, 100e9), \
        "the sweep decides: at 100 GHz the same body is 314 ohm"
    # 10 pF and 0.5 nH resonate at 2.25 GHz. That is in 1 to 6 GHz, but not
    # at an edge. The parabola finds it.
    cap = {"C": 10e-12, "L": 0.5e-9, "R": 0.05}
    assert effect(cap, "C", 2.2e9, 2.3e9) > 0.9
    assert not idle(cap, "C", 1e9, 6e9)
    # 1 pF with an ESR of 0.1 ohm and no ESL: the ESR is 0.4% of |Z| at
    # 6 GHz. Thus the capacitor uses the classic path.
    assert idle({"C": 1e-12, "R": 0.1}, "C", 1e9, 6e9)
    # An inductor always uses the series path. A part with no body has
    # nothing to remove.
    assert not idle({"L": 10e-9, "R": 1e-6}, "L", 1e9, 6e9)
    assert not idle({"R": 1000.0}, "R", 1e9, 6e9)

    # The timestep rule and the element of `build` read the same branch.
    def part(**p):
        e = lumped(0.5, 0.6)
        e.update(ref="X1", package="0603", esr=0.0, **p)
        return e
    s = {"f_start": 1e9, "f_stop": 6e9, "z0": 50.0}
    assert runner._components(part(type="R", value=1000.0, esl=0.5e-9),
                              True, s) == {"R": 1000.0}
    assert runner._components(part(type="R", value=50.0, esl=0.5e-9),
                              True, s) == {"R": 50.0, "L": 0.5e-9}
    m = {"settings": dict(s, lumped=True, parasitics=True),
         "lumped_elements": [part(type="R", value=1000.0, esl=0.5e-9)]}
    assert runner._time_step_factor(m) is None, \
        "a resistor with no body costs no timestep"
    m["lumped_elements"] = [part(type="R", value=50.0, esl=0.5e-9)]
    assert abs(runner._time_step_factor(m) - 0.5 / 0.5 ** 0.5) < 1e-9, \
        "50 ohm keeps its body and the factor of its ESL"
    # A 0 ohm link is a box of metal (a short): it has no body, and it sets
    # no timestep. The body rule must not divide by zero.
    assert runner._components(part(type="R", value=0.0, esl=0.5e-9),
                              True, s) == {"R": 0.0}
    m["lumped_elements"] = [part(type="R", value=0.0, esl=0.5e-9)]
    assert runner._time_step_factor(m) is None, \
        "a 0 ohm link costs no timestep"
    assert solverenv.parasitic_effect({"R": 0.0, "L": 0.5e-9}, "R",
                                      1e9, 6e9) == float("inf")
    print("a body that changes nothing stays out OK (0.018% for 1 kohm; a "
          "resonance inside the sweep keeps it)")


def test_the_mesh_says_what_it_chose():
    """B56: the decisions of the mesh also go into `_decisions`.

    `_mesh` fills a list when it gets one, and it then prints nothing.
    `main` builds one time for each excitation, and run.log had the same
    warning each time. With no list, it prints, and a rig uses that output.
    """
    import contextlib
    import io
    m = model("msl")
    m["vias"] = [{"x": 12.0, "y": -7.0, "r": 0.01, "z0": 0.0, "z1": 1.46}]
    m["settings"] = dict(m["settings"], f_start=1e9, f_stop=6e9, z0=50.0,
                         mesh="coarse", max_timesteps=1000, lumped=True)
    notes, out = [], io.StringIO()
    with contextlib.redirect_stdout(out):
        runner._mesh(m, runner._port_geometry(m, RES, quiet=True), RES, notes)
    assert not out.getvalue(), "a list must stop the print: %r" % out.getvalue()
    assert len(notes) == 1 and notes[0].startswith(
        "1 via(s) have a radius less than"), notes
    got = runner._decisions(m, RES)
    assert got[0] == notes[0], got
    with contextlib.redirect_stdout(out):
        runner._mesh(m, runner._port_geometry(m, RES, quiet=True), RES)
    assert "WARNING: 1 via(s) have a radius less than" in out.getvalue()
    print("the mesh says what it chose OK (a thin via)")


if __name__ == "__main__":
    test_via_center_line()
    test_via_lines_are_anchors()
    test_flat_and_vertical_ports()
    test_fallback_to_lumped()
    test_port_length_uses_list_position()
    test_port_length_is_capped_by_the_copper_run()
    test_strip_cells()
    test_cpw_z_cells()
    test_lumped_element_keeps_its_cells()
    test_one_cell_in_series_across_a_lumped_element()
    test_shunt_element_and_its_via()
    test_two_elements_near_each_other()
    test_lumped_element_does_not_wreck_the_mesh()
    test_a_feature_stays_on_one_layer()
    test_a_series_rlc_part_is_one_element()
    test_an_inductor_with_an_epc_is_two_elements()
    test_an_open_part_leaves_the_grid()
    test_the_run_says_what_it_chose()
    test_a_body_that_changes_nothing_stays_out()
    test_the_mesh_says_what_it_chose()
    print("PASS")
