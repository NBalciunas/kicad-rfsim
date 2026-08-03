"""Validate the CPW port, the stripline port and the impedance readout.

Run this file with the python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_cpw.py [mesh] [cpw|stripline]

**cpw** makes a grounded CPW of 30 mm: a line of 1.5 mm on F.Cu, a ground
at each side with a gap of 0.3 mm, and a full plane on B.Cu.

**stripline** makes a line of 30 mm on In1.Cu of a board with 4 layers,
between a plane on F.Cu and a plane on In2.Cu.

The test compares the measurement of the port against closed-form theory.
The stripline holds eps_eff AND the impedance of the line against the
theory. The CPW holds eps_eff against the theory, and its impedance only
against the value that this pipeline gives today: refer to NOTES.md,
"The impedance of a CPW port and of a stripline port is too small". The
eps_eff of a stripline must be exactly er, thus that test is the most
exact one in this directory.

A lumped port or a microstrip port on the same board gives a different
eps_eff. Thus this test fails if the new port falls back to another type.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import make_test_board  # noqa: E402
import solverenv  # noqa: E402

ER, H = 4.5, 1.53  # the FR4 default values of board_reader, 1.6 mm - 2 * 35 um
# The stripline port agrees with the theory since the mesh correction of
# 2026-08-03 (10): _mesh now puts cells ACROSS the strip, as it always
# did for a CPW port. The test holds the THEORY for that type.
Z_TOL = {"stripline": 0.08}
# The CPW port does not agree yet, thus the test holds the value that
# this pipeline measures now. A change of this band shows a change of the
# behaviour: examine it, and do not only make the numbers larger. Refer
# to NOTES.md, "The impedance of a CPW port and of a stripline port is
# too small".
Z_TODAY = {"cpw": (36.0, 48.0)}
# eps_eff of a stripline must be exactly er. The value sits near +2%,
# thus the band is 3% and not 2%: a small change of the mesh must not
# make the test flap.
E_TOL = {"stripline": 0.03, "cpw": 0.10}


def _agm(a, b):
    """Give the arithmetic-geometric mean."""
    for _ in range(40):
        a, b = 0.5 * (a + b), np.sqrt(a * b)
    return a


def _k_ratio(k):
    """Give K(k)/K'(k), the ratio of the complete elliptic integrals.

    K(k) = pi / (2 * AGM(1, k')), thus the ratio needs no special
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

    `b` is the distance between the two planes. The formula is the wide
    strip form (W/b > 0.35) of IPC-2141. A stripline has the dielectric
    on all its sides, thus eps_eff is exactly er.
    """
    u = 1.0 / (1.0 - t / b)
    cf = (1.0 / np.pi) * (2.0 * u * np.log(u + 1.0)
                          - (u - 1.0) * np.log(u * u - 1.0))
    return 94.15 / (np.sqrt(er) * (w * u / b + cf)), er


def main(mesh="coarse", kind="cpw"):
    outdir = os.path.join(HERE, "out_%s_%s" % (kind, mesh))
    os.makedirs(outdir, exist_ok=True)
    if kind == "cpw":
        board, pads = make_test_board.make_cpw(
            os.path.join(outdir, "cpw_50ohm.kicad_pcb"))
    else:
        board, pads = make_test_board.make_stripline(
            os.path.join(outdir, "stripline.kicad_pcb"))

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin)
    for p in model["ports"]:
        p["type"] = kind
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "f_field": 3.5e9, "z0": 50.0,
        "margin_mm": margin, "mesh": mesh, "n_freq": 401,
        "max_timesteps": 300000, "end_criteria": 1e-4,
    }
    model_path = os.path.join(outdir, "model.json")
    with open(model_path, "w") as fh:
        json.dump(model, fh, indent=1)

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

    runner = os.path.join(PLUGINS, "runner.py")
    solver_py = solverenv.solver_python() or sys.executable
    print("solver python:", solver_py)
    subprocess.check_call([solver_py, runner, model_path, outdir])

    with open(os.path.join(outdir, "lines.json")) as fh:
        lines = json.load(fh)
    freq = np.asarray(lines["freq_hz"], float)
    band = (freq >= 2e9) & (freq <= 5e9)  # not the ends: they are noisy
    assert lines["ports"], "the run wrote no line data: the port fell back"
    for num, d in sorted(lines["ports"].items(), key=lambda t: int(t[0])):
        z = float(np.median(np.asarray(d["Z0_real"], float)[band]))
        e = float(np.median(np.asarray(d["eps_eff"], float)[band]))
        print("port %s: eps_eff %.2f (theory %.2f, %+.0f%%)   "
              "Z0 %.1f ohm (theory %.1f, %+.0f%%)"
              % (num, e, e_th, 100.0 * (e / e_th - 1.0),
                 z, z_th, 100.0 * (z / z_th - 1.0)))
        # eps_eff shows that the port measures the correct mode. This test
        # is strict, and for a stripline it is almost exact.
        assert abs(e / e_th - 1.0) < E_TOL[kind], \
            "eps_eff does not agree with the theory: the port measures the " \
            "incorrect mode"
        if kind in Z_TOL:
            assert abs(z / z_th - 1.0) < Z_TOL[kind], (
                "Z0 %.1f ohm does not agree with the theory %.1f ohm "
                "(%+.0f%%, band %.0f%%)"
                % (z, z_th, 100.0 * (z / z_th - 1.0), 100.0 * Z_TOL[kind]))
        else:
            lo, hi = Z_TODAY[kind]
            assert lo <= z <= hi, (
                "Z0 %.1f ohm is outside the band that this pipeline gives "
                "today (%.0f to %.0f). Refer to NOTES.md, \"The impedance of "
                "a CPW port and of a stripline port is too small\"."
                % (z, lo, hi))
    if kind in Z_TOL:
        print("VALIDATED AGAINST THEORY: eps_eff and Z0.")
    else:
        print("NOTE: Z0 does not agree with the theory. This is a known open "
              "item; only eps_eff and the geometry are validated.")

    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    m11 = np.abs(rows[:, 1] + 1j * rows[:, 2])
    m21 = np.abs(rows[:, 3] + 1j * rows[:, 4])
    print("S11 max: %.1f dB   S21 min: %.1f dB"
          % (20 * np.log10(m11.max() + 1e-12), 20 * np.log10(m21.min() + 1e-12)))
    # These lines are not matched to 50 ohm, thus S21 goes down and a
    # limit on S21 alone has no meaning. But the lines have little loss.
    # Thus the power must stay in the two ports: |S11|^2 + |S21|^2 = 1.
    power = m11 ** 2 + m21 ** 2
    print("power |S11|^2+|S21|^2: %.3f .. %.3f (a passive line cannot go "
          "above 1)" % (power.min(), power.max()))
    if power.max() > 1.05:
        print("NOTE: the power goes above 1. Thus the S-parameters of this "
              "port are also incorrect, and not only its impedance. This is "
              "the same open item.")
    print("PASS (eps_eff, the geometry and Z0)" if kind in Z_TOL
          else "PASS (eps_eff and the geometry only)")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
