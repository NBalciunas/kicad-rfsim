"""Measure the inductance of one via against a closed formula.

A via goes through the solver on the shunt board and on the zone board. But
the two boards do not measure the barrel without other parts. This file
does.

**The board.** The through microstrip of `run_atten.py` (2.9 mm on 1.53 mm
of er 4.5), with 30 mm between the two ports. ONE via is at the middle of
the line, from F.Cu down to the plane on B.Cu. The via is a shunt inductance to
ground. The substrate has no loss.

**The observable.** A second board with no via gives only the line. Its
S21 has the phase of the 21 mm between the two measurement planes. The path
from one plane to the via and back is the same 21 mm. Thus the S-parameters
of the via board, divided by that S21, are the S-parameters of the via at
its own plane. They have no length of line in them and no value of eps_eff.

First, the file renormalizes the two boards to the impedance that the port
measures on the line with no via. The cause is that the line is 47 ohm and
not 50 at the coarse preset. A shunt admittance Y between two lines of
Z0 gives

    S21 = 2 / (2 + Y Z0)          S11 = -Y Z0 / (2 + Y Z0)

Thus each one of the two gives Y, and L = -1 / (w Im Y). The file gets the
median of each in F_BAND, and the answer is the mean of the two medians.
The two agree to about 15% on each row below.

**The closed formula** is Goldfarb and Pucel (1991), for the via ground of
a microstrip:

    L = mu0 / (2 pi) * (h ln((h + sqrt(r^2 + h^2)) / r)
                        + 1.5 (r - sqrt(r^2 + h^2)))

**The mesh sets the result of this measurement.** openEMS makes a metal
cylinder into PEC on the edges of the Yee grid when their NODE is in the
barrel. `_mesh` puts a line at the centre of the via and at x - r and x +
r, and the same on y. Thus the barrel can have 5 nodes: the centre and
4 nodes ON its surface.

**A node on the surface counts only when the arithmetic of the doubles puts
it there.** 20.2 - 20.0 is 0.1999999999999993, which is not larger than
0.2. 20.3 - 20.0 is 0.3000000000000007, which is larger than 0.3. Thus a
via of r = 0.2 mm is 5 PEC edges, and a via of r = 0.3 mm is ONE. That edge
is the centre, which is a thin wire. The PEC dump of openEMS
(`debug_pec=True`) shows the same counts. `_merge_close` does the same to a
small via at the coarse preset. There, `tol` is 0.18 mm. Thus the lines of
a via of r = 0.15 mm merge, and the barrel keeps 1 edge.

Measured on 2026-09-14, the mean of the two estimates against the closed
formula, with the edges of the barrel:

    r (mm)   coarse          medium
    0.15     +54%  1 edge    +31%  3 edges
    0.20     +13%  5 edges   +14%  5 edges
    0.30    +155%  1 edge   +158%  1 edge
    0.50     +16%  5 edges   +17%  5 edges

The same runs with the four surface lines 1 ppm IN the barrel give +19% for
r = 0.30 at coarse and +20% at medium. They give +8% for r = 0.15 at
medium. Each one has 5 edges. Thus a barrel of 5 edges is +8% to +20% above
the closed formula, and a barrel of fewer edges is +31% to +158%. VIA_TOL
is between the two. **This file FAILS at this time, and the cause of the
failure is the mesh.** Read the edge count of a row before its number.

Run it with the python of the solver, or with the python of KiCad (it does
not use pcbnew, and it starts the solver itself):

    C:\\openEMS\\venv\\Scripts\\python.exe run_via.py [coarse|medium]

The file makes 5 runs of about 20 seconds each at the coarse preset.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# RFSIM_PLUGINS names the plugins directory to measure, thus this file
# can run against a copy of the code that holds a different mesh rule.
PLUGINS = os.environ.get("RFSIM_PLUGINS") or os.path.join(
    os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import runner  # noqa: E402
import solverenv  # noqa: E402
from run_atten import H_SUB, line_model  # noqa: E402

# The radii of the barrel, in mm: the drills of 0.3, 0.4, 0.6 and 1.0 mm
# that a board usually has.
RADII = (0.15, 0.2, 0.3, 0.5)
D_PORTS = 30.0  # the ports are at x = 5 and x = 35
X_VIA, Y_VIA = 20.0, -10.0      # the middle of that line
# The band of the median. The via is 4% of a wavelength long at 5 GHz, thus
# it continues to be a lumped inductance there.
F_BAND = (1e9, 5e9)
# The tolerance against the closed formula. It is above the +8% to +20% of
# each barrel of 5 edges. It is below the +31% to +158% of each barrel of
# fewer edges.
VIA_TOL = 0.25


def l_goldfarb_pucel_nh(r, h=H_SUB):
    """Give the inductance of a via ground in nH, from r and h in mm."""
    s = np.sqrt(r * r + h * h)
    return 0.2 * (h * np.log((h + s) / r) + 1.5 * (r - s))


def board(r, mesh):
    """Give the line of `run_atten.py` with one via of radius r, or none."""
    m = line_model(D_PORTS, mesh)
    for d in m["dielectric_layers"]:
        d["loss_tangent"] = 0.0
    m["vias"] = ([{"x": X_VIA, "y": Y_VIA, "r": r, "z0": 0.0, "z1": H_SUB}]
                 if r else [])
    return m


def barrel_edges(model):
    """Give the count of grid nodes in the barrel of the via.

    This is the arithmetic of the engine: the lines of `_mesh`, smoothed
    and rounded as `build()` does it. A node counts when its distance from
    the axis is not larger than r. No solver runs.
    """
    from CSXCAD.SmoothMeshLines import SmoothMeshLines
    s = model["settings"]
    eps = max(d["epsilon"] for d in model["dielectric_layers"])
    res = runner.C0 / s["f_stop"] / np.sqrt(eps) * 1e3 / runner.RES_DIV[s["mesh"]]
    xs, ys, _ = runner._mesh(model, runner._port_geometry(model, res), res)
    xs = np.round(SmoothMeshLines(xs, res, 1.4), 9)
    ys = np.round(SmoothMeshLines(ys, res, 1.4), 9)
    v = model["vias"][0]
    return sum(1 for x in xs for y in ys
               if np.hypot(x - v["x"], y - v["y"]) <= v["r"])


def solve(tag, model, root):
    """Run one board. Give (f, S11, S21, the Z0 of port 2 or None)."""
    outdir = os.path.join(root, tag)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "model.json")
    with open(path, "w") as fh:
        json.dump(model, fh, indent=1)
    py = solverenv.solver_python() or sys.executable
    log = subprocess.run([py, os.path.join(PLUGINS, "runner.py"), path,
                          outdir], capture_output=True)
    # Read the BYTES: openEMS writes characters that are not UTF-8 on
    # this console, and `text=True` then gives a truncated log.
    out = log.stdout.decode("utf-8", "replace")
    if log.returncode != 0:
        print(out[-2000:])
        print(log.stderr.decode("utf-8", "replace")[-1000:])
        raise SystemExit("solver failed for %s" % tag)
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    z = None
    lines = os.path.join(outdir, "lines.json")
    if os.path.isfile(lines):
        with open(lines) as fh:
            ports = json.load(fh)["ports"]
        if "2" in ports:
            z = np.asarray(ports["2"]["Z0_real"])
    return (rows[:, 0], rows[:, 1] + 1j * rows[:, 2],
            rows[:, 3] + 1j * rows[:, 4], z)


def renormalize(s11, s21, zl, z0=50.0):
    """Give (S11, S21) of a symmetric 2-port against the impedance zl.

    `zl` has no imaginary part. The board is symmetric about the via, thus
    S22 = S11 and S12 = S21. Thus one excitation gives the full matrix.
    """
    g = (zl - z0) / (zl + z0)
    a, b = [], []
    eye = np.eye(2)
    for k in range(len(s11)):
        s = np.array([[s11[k], s21[k]], [s21[k], s11[k]]])
        sp = (s - g[k] * eye) @ np.linalg.inv(eye - g[k] * s)
        a.append(sp[0, 0])
        b.append(sp[1, 0])
    return np.array(a), np.array(b)


def main(mesh="coarse"):
    root = os.path.join(HERE, "out_via_%s" % mesh)
    os.makedirs(root, exist_ok=True)
    print("one via at the middle of a line of %.0f mm, on %.2f mm of er %.1f, "
          "at the %s preset" % (D_PORTS, H_SUB, board(0, mesh)[
              "dielectric_layers"][0]["epsilon"], mesh))
    f, l11, l21, zl = solve("line", board(0, mesh), root)
    if zl is None:
        raise SystemExit("the line with no via gave no impedance in lines.json")
    t11, t21 = renormalize(l11, l21, zl)
    w = 2.0 * np.pi * f
    band = (f >= F_BAND[0]) & (f <= F_BAND[1])
    print("the line alone: Z0 %.2f ohm at the middle of the band, |S21| %.3f "
          "to %.3f after the renormalization\n"
          % (float(np.median(zl[band])), np.abs(t21).min(), np.abs(t21).max()))

    print("%6s %6s %9s %11s %11s %9s %8s" % ("r mm", "edges", "GP nH",
                                             "from S21", "from S11", "mean",
                                             "error"))
    fails = []
    for r in RADII:
        model = board(r, mesh)
        edges = barrel_edges(model)
        fv, v11, v21, _ = solve("via_r%g" % r, model, root)
        if not np.allclose(fv, f):
            raise SystemExit("the two sweeps do not share a frequency axis")
        v11, v21 = renormalize(v11, v21, zl)
        # The phase of the line comes out: the path to the via and back
        # is the path from plane to plane.
        s21_via, s11_via = v21 / t21, v11 / t21
        y21 = 2.0 * (1.0 - s21_via) / s21_via
        y11 = -2.0 * s11_via / (1.0 + s11_via)
        l21 = np.median(-zl[band] / (w[band] * np.imag(y21[band]))) * 1e9
        l11 = np.median(-zl[band] / (w[band] * np.imag(y11[band]))) * 1e9
        got = 0.5 * (l21 + l11)
        want = l_goldfarb_pucel_nh(r)
        err = got / want - 1.0
        print("%6.2f %6d %9.4f %11.4f %11.4f %9.4f %+7.0f%%"
              % (r, edges, want, l21, l11, got, 100.0 * err))
        if abs(err) > VIA_TOL:
            fails.append("r = %.2f mm reads %+.0f%% against Goldfarb and "
                         "Pucel, and its barrel holds %d grid node(s)%s"
                         % (r, 100.0 * err, edges,
                            "" if edges >= 5 else ": the mesh keeps the via "
                            "as a thin wire and not as a barrel"))

    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the via validation FAILED")
    print("PASS: every via stands within %.0f%% of the closed form"
          % (100.0 * VIA_TOL))


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
