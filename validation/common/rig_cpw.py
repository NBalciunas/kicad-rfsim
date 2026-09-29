"""Validate the CPW port, the stripline port and the impedance readout.

The two solvers run this rig with the same boards. Start it with the
python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\openems\\run_cpw_openems.py [mesh] [cpw|stripline]
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\emerge\\run_cpw_emerge.py [mesh] [cpw|stripline]

**cpw** makes a grounded CPW of 30 mm. It has a line of 1.5 mm on F.Cu, a
ground at each side with a gap of 0.3 mm, and a full plane on B.Cu.

**stripline** makes a line of 30 mm on In1.Cu of a board with 4 layers,
between a plane on F.Cu and a plane on In2.Cu.

The test compares the measurement of the port against a closed formula.
Each type holds eps_eff AND the impedance of the line against the theory.
The eps_eff of a stripline must be equal to er. Thus that test is the most
accurate one in this directory.

A lumped port or a microstrip port on the same board gives a different
eps_eff. Thus this test stops with a failure if the new port changes to a
different type.

**EMerge has no de-embedded port**, thus no lines.json. Its eps_eff comes
from TWO lines, of 30 mm and of `LONG` mm, as in `rig_feature` and
`rig_atten`. The phase of S21 across the difference of the two lengths
gives sqrt(eps_eff), and the phase of the two lumped ports cancels. One
line gave +6.8% on the stripline, because the phase of the ports stays in
it. EMerge gives no Z0 of the line, thus only eps_eff has a test.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# The solver and the folder of the results come from `rigsolve.use()`
# in the file that starts this rig. `rigsolve` also puts plugins/ on
# the path.
import rigsolve  # noqa: E402
PLUGINS = rigsolve.PLUGINS

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import make_test_board  # noqa: E402
import solverenv  # noqa: E402

ER, H = 4.5, 1.53  # the FR4 default values of board_reader, 1.6 mm - 2 * 35 um
# The two types agree with the theory after the mesh corrections of
# 2026-08-03 (10) and 2026-08-04. For a stripline port, _mesh puts cells
# ACROSS the strip. For a CPW port, it puts cells ABOVE and BELOW the plane
# of the line. Thus the test holds the THEORY for the two types.
Z_TOL = {"stripline": 0.08, "cpw": 0.08}
# eps_eff of a stripline must be equal to er. The value is near +2%. Thus
# the band is 3% and not 2%. A small change of the mesh must not make the
# result of the test change at random.
E_TOL = {"stripline": 0.03, "cpw": 0.10}
C0 = 299792458.0
# The via fence of the CPW of EMerge: a lumped port refers to B.Cu only,
# and the coplanar grounds then float. A wave port sees the two grounds
# on its face, thus the two solvers test the same board with no fence.
FENCE = False
# The line of the board, and the second line of EMerge.
LENGTH, LONG = 30.0, 60.0


def _agm(a, b):
    """Give the arithmetic-geometric mean."""
    for _ in range(40):
        a, b = 0.5 * (a + b), np.sqrt(a * b)
    return a


def _k_ratio(k):
    """Give K(k)/K'(k), the ratio of the complete elliptic integrals.

    K(k) = pi / (2 * AGM(1, k')). Thus the ratio does not use a special
    function: K(k)/K'(k) = AGM(1, k) / AGM(1, k').
    """
    return _agm(1.0, k) / _agm(1.0, np.sqrt(1.0 - k * k))


def cpwg_theory(w, s, h, er):
    """Give (Z0, eps_eff) of a conductor-backed CPW.

    The formula is the usual one for a CPW that has a ground plane below
    it (Simons, "Coplanar Waveguide Circuits, Components and Systems").
    """
    a, b = 0.5 * w, 0.5 * w + s
    k0 = a / b
    k3 = np.tanh(np.pi * a / (2 * h)) / np.tanh(np.pi * b / (2 * h))
    r0, r3 = _k_ratio(k0), _k_ratio(k3)
    eps_eff = (1.0 + er * r3 / r0) / (1.0 + r3 / r0)
    return 60.0 * np.pi / np.sqrt(eps_eff) / (r0 + r3), eps_eff


def stripline_theory(w, b, t, er):
    """Give (Z0, eps_eff) of a symmetric stripline.

    `b` is the distance between the two planes. The formula is the formula
    for a wide strip (W/b > 0.35) of IPC-2141. A stripline has the
    dielectric on all its sides, thus eps_eff is equal to er.
    """
    u = 1.0 / (1.0 - t / b)
    cf = (1.0 / np.pi) * (2.0 * u * np.log(u + 1.0)
                          - (u - 1.0) * np.log(u * u - 1.0))
    return 94.15 / (np.sqrt(er) * (w * u / b + cf)), er


def line_model(kind, mesh, outdir, length=LENGTH):
    """Make the board of `kind` with a line of `length` mm, and give
    (the model, Z0 of the theory, eps_eff of the theory).

    The CPW of EMerge gets a row of vias along each coplanar ground,
    because its lumped ports refer to B.Cu only: refer to
    `make_test_board.make_cpw`.
    """
    os.makedirs(outdir, exist_ok=True)
    if kind == "cpw":
        board, pads = make_test_board.make_cpw(
            os.path.join(outdir, "cpw_50ohm.kicad_pcb"), length=length,
            fence=FENCE and rigsolve.SOLVER != "openems")
    else:
        board, pads = make_test_board.make_stripline(
            os.path.join(outdir, "stripline.kicad_pcb"), length=length)

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin,
                                 f_stop=6e9, mesh=mesh)
    for p in model["ports"]:
        p["type"] = kind
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "f_field": 3.5e9, "z0": 50.0,
        "margin_mm": margin, "mesh": mesh, "n_freq": 401,
        "max_timesteps": 300000, "end_criteria": 1e-4,
    }

    p0 = model["ports"][0]
    w = p0["track_width"]
    if kind == "cpw":
        print("ports: %d, measured gap %s mm, line width %s mm"
              % (len(model["ports"]), [p["gap"] for p in model["ports"]], w))
        assert all(p["gap"] for p in model["ports"]), \
            "no coplanar gap found: the port cannot be a CPW"
        z_th, e_th = cpwg_theory(w, p0["gap"], H, ER)
    else:
        print("ports: %d, strip to each plane %s mm, line width %s mm"
              % (len(model["ports"]), [p["height"] for p in model["ports"]], w))
        assert all(p["height"] for p in model["ports"]), \
            "no plane above and below: the port cannot be a stripline"
        z_th, e_th = stripline_theory(w, 2.0 * p0["height"], 0.035, ER)
    return model, z_th, e_th


def s21_of(outdir):
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"),
                      comments=("!", "#"))
    return rows[:, 0], rows[:, 3] + 1j * rows[:, 4]


def eps_two_lengths(kind, mesh, outdir, e_th):
    """Give eps_eff from the line of `outdir` and a line of LONG mm.

    The difference of the two phases of S21 has only the extra line. Its
    slope is sqrt(eps_eff) * w * dl / c.
    """
    out2 = rigsolve.out("out_%s_%s_l%g" % (kind, mesh, LONG))
    model, _, _ = line_model(kind, mesh, out2, LONG)
    rigsolve.run(model, out2, os.path.basename(out2))
    f, s_short = s21_of(outdir)
    f2, s_long = s21_of(out2)
    if not np.allclose(f, f2):
        raise SystemExit("the two sweeps do not share a frequency axis")
    dphi = np.unwrap(np.angle(s_long / s_short))
    root = -dphi * C0 / (2 * np.pi * f * (LONG - LENGTH) * 1e-3)
    band = (f >= 2e9) & (f <= 5e9)  # not the ends: they have noise
    e = float(np.median(root[band] ** 2))
    print("eps_eff from the lines of %g mm and %g mm: %.3f (theory %.2f, "
          "%+.1f%%)" % (LENGTH, LONG, e, e_th, 100.0 * (e / e_th - 1.0)))
    return e


def main(mesh="coarse", kind="cpw"):
    outdir = rigsolve.out("out_%s_%s" % (kind, mesh))
    model, z_th, e_th = line_model(kind, mesh, outdir)
    rigsolve.run(model, outdir, os.path.basename(outdir))

    # The two solvers write lines.json from the port of the line: the
    # probes of openEMS, and the mode of the wave port of EMerge. A port
    # that fell back to a lumped port writes none, and then two lengths
    # give eps_eff.
    if rigsolve.lines(outdir) is None:
        e = eps_two_lengths(kind, mesh, outdir, e_th)
        assert abs(e / e_th - 1.0) < E_TOL[kind], \
            "eps_eff does not agree with the theory: the line carries the " \
            "incorrect mode"
        print("VALIDATED AGAINST THEORY: eps_eff (the port gives no Z0).")
    else:
        check_lines(outdir, z_th, e_th, kind)
    power_check(outdir)


def check_lines(outdir, z_th, e_th, kind):
    """Hold the Z0 and the eps_eff of lines.json against the theory."""
    with open(os.path.join(outdir, "lines.json")) as fh:
        lines = json.load(fh)
    freq = np.asarray(lines["freq_hz"], float)
    band = (freq >= 2e9) & (freq <= 5e9)  # not the ends: they have noise
    assert lines["ports"], "the run wrote no line data: the port fell back"
    for num, d in sorted(lines["ports"].items(), key=lambda t: int(t[0])):
        z = float(np.median(np.asarray(d["Z0_real"], float)[band]))
        e = float(np.median(np.asarray(d["eps_eff"], float)[band]))
        print("port %s: eps_eff %.2f (theory %.2f, %+.0f%%)   "
              "Z0 %.1f ohm (theory %.1f, %+.0f%%)"
              % (num, e, e_th, 100.0 * (e / e_th - 1.0),
                 z, z_th, 100.0 * (z / z_th - 1.0)))
        # eps_eff shows that the port measures the correct mode. This test
        # is strict, and for a stripline it is almost fully accurate.
        assert abs(e / e_th - 1.0) < E_TOL[kind], \
            "eps_eff does not agree with the theory: the port measures the " \
            "incorrect mode"
        assert abs(z / z_th - 1.0) < Z_TOL[kind], (
            "Z0 %.1f ohm does not agree with the theory %.1f ohm "
            "(%+.0f%%, band %.0f%%)"
            % (z, z_th, 100.0 * (z / z_th - 1.0), 100.0 * Z_TOL[kind]))
    print("VALIDATED AGAINST THEORY: eps_eff and Z0.")


def power_check(outdir):
    """A passive line cannot give out more power than it gets."""
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    m11 = np.abs(rows[:, 1] + 1j * rows[:, 2])
    m21 = np.abs(rows[:, 3] + 1j * rows[:, 4])
    print("S11 max: %.1f dB   S21 min: %.1f dB"
          % (20 * np.log10(m11.max() + 1e-12), 20 * np.log10(m21.min() + 1e-12)))
    # These lines are not matched to 50 ohm, thus S21 decreases. A limit on
    # S21 without other limits gives no data. But the lines have a small
    # loss. Thus the power must stay in the two ports: |S11|^2 + |S21|^2 =
    # 1.
    power = m11 ** 2 + m21 ** 2
    print("power |S11|^2+|S21|^2: %.3f .. %.3f (a passive line cannot go "
          "above 1)" % (power.min(), power.max()))
    # The measurements of 2026-08-04: the CPW board gives 1.049 at the
    # coarse preset and 0.995 at the medium preset. The stripline board
    # gives 1.00. Thus a value above 1.05 gives a message, and a value
    # above 1.15 is a defect. The stripline with the defect gave 1.71 and
    # 1.88.
    if power.max() > 1.05:
        print("NOTE: the power goes a little above 1. The usual cause is the "
              "mesh near the line, and the coarse preset sits at 1.05.")
    assert power.max() <= 1.15, (
        "the power goes to %.3f. A passive line cannot give out more power "
        "than it takes in, thus the S-parameters of this port are incorrect "
        "and not only inexact. The mesh across the line is the usual "
        "cause." % power.max())
    print("PASS (eps_eff%s, and the power)"
          % (" and Z0" if rigsolve.SOLVER == "openems" else ""))


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
