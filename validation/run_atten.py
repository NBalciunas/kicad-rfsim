"""Measure the attenuation of a line from TWO lengths (B19).

`_line_data` gives the attenuation from ONE line, and that number is
noise: Im(beta) goes negative under 2.2 GHz and it peaks at 79.8 dB/m at
5.5 GHz (the log of 2026-08-05 (5)). The cause is the conditioning of
that extraction, and no change inside `_line_data` corrects it.

**Two lines that differ in LENGTH alone give the attenuation with no
such problem.** This is the method of `run_shunt.py` and of
`run_feature.py`: |S21| carries the loss of the line AND the loss of the
two ports, the ports are the same on both boards, thus the difference
keeps the line and removes everything else:

    alpha (dB/m) = (|S21_short| - |S21_long|) dB / (l_long - l_short)

The two boards differ in ONE number: the distance between the two ports.
The width, the stackup, the port geometry and the mesh rules are the
same, thus every constant loss cancels.

**The closed form, and there are TWO of them.** The copper carries a
conductivity and the substrate carries a loss, thus the theory has two
terms:

  - the conductor, alpha_c = 8.686 * Rs / (Z0 w), with the surface
    resistance Rs = sqrt(pi f mu0 / kappa) of a sheet that is thicker
    than the skin depth (35 um against 1.2 um at 3 GHz);
  - the dielectric, alpha_d = 8.686 * (pi f / c) * er / sqrt(eps_eff)
    * (eps_eff - 1) / (er - 1) * tan d.

**The dielectric term needs the loss that the CODE makes, and that is
not the loss that a datasheet gives.** `build()` writes ONE fixed
conductivity for the substrate, kappa = 2 pi f0 eps0 er tan d, at the
CENTRE frequency of the sweep. A fixed kappa is a loss tangent that
falls as 1/f, thus the attenuation of the model is almost FLAT with the
frequency, where a real substrate of constant tan d rises with it. The
two agree at f0 and nowhere else. `alpha_diel` therefore takes the
tan d that the model really has, and `main` prints the constant-tan d
curve beside it to show the size of the difference.

eps_eff comes from Hammerstad and Jensen with the dispersion of
Kirschning and Jansen, which `run_feature.py` measures against a
frequency on this same stackup.

**The ripple is the error of this rig.** A line that is not exactly 50
ohm reflects at each end, thus |S21| ripples with the length, and the
difference of two lengths keeps that ripple. The file reports the mean
over `F_FIT` and the spread of the ratio, which is the size of it.

Run it with the python of the solver, or with the python of KiCad (it
needs no pcbnew, and it starts the solver itself):

    C:\\openEMS\\venv\\Scripts\\python.exe run_atten.py [coarse|medium]

The file makes 2 runs, and the long board is the one that costs.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# RFSIM_PLUGINS names the plugins directory to measure, thus this file
# can run against a copy of the code that holds a different rule.
PLUGINS = os.environ.get("RFSIM_PLUGINS") or os.path.join(
    os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import solverenv  # noqa: E402
from run_feature import C0, ER, H_SUB, W_LINE, eps_eff_f, z0_static  # noqa: E402

MU0 = 4.0e-7 * np.pi
KAPPA = 5.8e7            # the conductivity that `build()` gives the copper
CU_T = 0.035             # mm, the thickness of the sheet
TAND = 0.02              # the loss tangent of the substrate of validation/

# The distance between the two ports, in mm. The difference is what the
# measurement divides by, thus it must be large enough that the loss of
# the line stands over the ripple: 80 mm gives about 0.73 dB at 3 GHz.
D_SHORT, D_LONG = 20.0, 100.0
F_START, F_STOP, N_FREQ = 1e9, 6e9, 501
# The band of the fit. Under 1.5 GHz the loss is small and the ripple is
# a large part of it; over 5.5 GHz the sweep runs into the end of the
# excitation.
F_FIT = (1.5e9, 5.5e9)
# The tolerance against the closed form of the MODEL, over the band of
# the fit. The two terms are worth about 12 dB/m together here, and the
# spread that the file reports is the ripple of a line whose return loss
# is only -11 to -20 dB.
ALPHA_TOL = 0.15


# ----------------------------------------------------------------- theory
def alpha_diel(f, w, h, er, tand, f0=None):
    """The dielectric loss of a microstrip, in dB/m.

    `f0` gives the loss that the CODE makes and not the loss of a
    datasheet: `build()` fixes ONE conductivity at the centre frequency
    of the sweep, thus the loss tangent of the model falls as f0/f. Give
    f0 = None for the constant tan d of a real substrate.
    """
    e = eps_eff_f(w, h, er, f)
    if f0 is not None:
        tand = tand * f0 / f
    return (8.685889638 * np.pi * f / C0 * er / np.sqrt(e)
            * (e - 1.0) / (er - 1.0) * tand)


def alpha_cond(f, w, h, er, kappa=KAPPA, t_mm=CU_T):
    """The conductor loss of a wide microstrip, in dB/m.

    Rs is the surface resistance of the sheet. Under the frequency where
    the skin depth reaches the thickness the sheet is thin, thus this
    takes the larger of the skin value and the DC sheet resistance. At
    3 GHz the skin depth is 1.2 um against a sheet of 35 um, thus the
    skin value is the one that counts over this whole sweep.
    """
    f = np.asarray(f, float)
    rs_skin = np.sqrt(np.pi * f * MU0 / kappa)
    rs_dc = 1.0 / (kappa * t_mm * 1e-3)
    rs = np.maximum(rs_skin, rs_dc)
    return 8.685889638 * rs / (z0_static(w, h, er) * w * 1e-3)


# ------------------------------------------------------------------ board
def line_model(d_ports, mesh):
    """A straight through microstrip with the two ports `d_ports` apart.

    Everything except the length of the line is the same on both boards
    of the pair, thus the difference of the two |S21| holds the line and
    nothing else. The stackup and the width are those of
    `microstrip_50ohm.kicad_pcb`, in the same way as `run_feature.py`.
    """
    hw = 0.5 * W_LINE
    x_p1, x_p2 = 5.0, 5.0 + d_ports
    x0, x1 = 0.0, x_p2 + 5.0
    y0, y1 = -40.0, 0.0
    yc = -10.0
    # The line goes half a width past each port, which is the geometry
    # of every other board of validation/.
    line = [[x_p1 - hw, yc - hw], [x_p2 + hw, yc - hw],
            [x_p2 + hw, yc + hw], [x_p1 - hw, yc + hw]]
    ground = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    margin = 4.0
    port = dict(layer="F.Cu", ref_layer="B.Cu", ref_layer2=None, height=None,
                asymmetry=0.0, gap=None, width=W_LINE, length=W_LINE,
                track_width=W_LINE, type="msl", copper_run=None, y=yc)
    return {
        "version": 1,
        "stackup_source": "default",
        "copper_layers": [{"name": "F.Cu", "z": H_SUB, "thickness": CU_T},
                          {"name": "B.Cu", "z": 0.0, "thickness": CU_T}],
        "dielectric_layers": [{"name": "dielectric 1", "z_top": H_SUB,
                               "z_bottom": 0.0, "epsilon": ER,
                               "loss_tangent": TAND}],
        "region": {"x0": x0 - margin - 0.05, "x1": x1 + margin + 0.05,
                   "y0": y0 - margin - 0.05, "y1": y1 + margin + 0.05},
        "board_rect": {"x0": x0, "x1": x1, "y0": y0, "y1": y1},
        "polygons": {"F.Cu": [line], "B.Cu": [ground]},
        "vias": [],
        "ports": [dict(port, number=1, label="P1", x=x_p1, direction=[1, 0]),
                  dict(port, number=2, label="P2", x=x_p2, direction=[-1, 0])],
        "lumped_elements": [],
        "warnings": [],
        "settings": {"f_start": F_START, "f_stop": F_STOP, "z0": 50.0,
                     "margin_mm": margin, "mesh": mesh, "n_freq": N_FREQ,
                     "max_timesteps": 300000, "end_criteria": 1e-4,
                     "lumped": False, "excite": [1]},
    }


# ------------------------------------------------------------------ solve
def solve(d_ports, mesh, root):
    """Run one board and give (f, S11, S21)."""
    outdir = os.path.join(root, "out_atten_d%g_%s" % (d_ports, mesh))
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "model.json")
    with open(path, "w") as fh:
        json.dump(line_model(d_ports, mesh), fh, indent=1)
    py = solverenv.solver_python() or sys.executable
    log = subprocess.run([py, os.path.join(PLUGINS, "runner.py"), path,
                          outdir], capture_output=True)
    # Read the BYTES: openEMS writes characters that are not UTF-8 on
    # this console, and `text=True` then kills the reader thread of
    # subprocess and gives a truncated log. `run_stability.solve` does
    # the same.
    out = log.stdout.decode("utf-8", "replace")
    err = log.stderr.decode("utf-8", "replace")
    if log.returncode != 0:
        print(out[-2000:])
        print(err[-1000:])
        raise SystemExit("solver failed for d = %g mm" % d_ports)
    for line in out.splitlines():
        if "mesh:" in line or "ERROR" in line or "timesteps" in line:
            print("     " + line.strip())
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    return (rows[:, 0], rows[:, 1] + 1j * rows[:, 2],
            rows[:, 3] + 1j * rows[:, 4])


def db(x):
    return 20.0 * np.log10(np.abs(x) + 1e-15)


# ------------------------------------------------------------------- main
def main(mesh="coarse"):
    root = os.path.join(HERE, "out_atten_%s" % mesh)
    os.makedirs(root, exist_ok=True)
    dl_m = (D_LONG - D_SHORT) * 1e-3
    print("two lines of %.1f mm and %.1f mm between the ports, %.2f mm wide "
          "on a substrate of %.2f mm, er %.1f, tan d %.3f, at the %s preset"
          % (D_SHORT, D_LONG, W_LINE, H_SUB, ER, TAND, mesh))
    print("the difference is %.1f mm; Z0 of the closed form is %.1f ohm"
          % (D_LONG - D_SHORT, z0_static(W_LINE, H_SUB, ER)))

    out = {}
    for d in (D_SHORT, D_LONG):
        print("  the line of %.1f mm:" % d)
        out[d] = solve(d, mesh, root)
    f = out[D_SHORT][0]
    if not np.allclose(f, out[D_LONG][0]):
        raise SystemExit("the two sweeps do not share a frequency axis")

    s11 = np.maximum(db(out[D_SHORT][1]), db(out[D_LONG][1]))
    alpha = (db(out[D_SHORT][2]) - db(out[D_LONG][2])) / dl_m
    # The centre frequency at which `build()` fixes the conductivity of
    # the substrate. The loss of the MODEL follows that value, and a
    # real substrate of constant tan d does not.
    f_mid = 0.5 * (F_START + F_STOP)
    th_c = alpha_cond(f, W_LINE, H_SUB, ER)
    th = alpha_diel(f, W_LINE, H_SUB, ER, TAND, f0=f_mid) + th_c
    th_real = alpha_diel(f, W_LINE, H_SUB, ER, TAND) + th_c

    print("\n%8s %10s %10s %11s %11s %10s %10s"
          % ("f (GHz)", "S21 short", "S21 long", "alpha", "the model",
             "a real tan d", "worst S11"))
    for f0 in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5):
        i = int(np.argmin(np.abs(f - f0 * 1e9)))
        print("%8.2f %9.3f %9.3f %6.1f dB/m %6.1f dB/m %6.1f dB/m %7.1f dB"
              % (f[i] / 1e9, db(out[D_SHORT][2])[i], db(out[D_LONG][2])[i],
                 alpha[i], th[i], th_real[i], s11[i]))

    m = (f >= F_FIT[0]) & (f <= F_FIT[1])
    ratio = alpha[m] / th[m]
    mean, spread = float(np.mean(ratio)), float(np.std(ratio))
    print("\nover %.1f to %.1f GHz the measurement is %.2f times the "
          "loss of the MODEL, and the ripple gives a spread of %.2f"
          % (F_FIT[0] / 1e9, F_FIT[1] / 1e9, mean, spread))
    r2 = alpha[m] / th_real[m]
    print("against a constant tan d it is %.2f times, with a spread of %.2f. "
          "That larger spread is the fixed conductivity of `build()`, which "
          "holds the loss of the model near its value at %.2f GHz"
          % (float(np.mean(r2)), float(np.std(r2)), f_mid / 1e9))
    print("the worst |S11| of the band is %.1f dB, thus the reflection that "
          "makes the ripple" % np.max(s11[m]))
    neg = int(np.sum(alpha[m] < 0))
    print("%d of %d points of the band are NEGATIVE, and a passive line "
          "cannot be one of them" % (neg, int(np.sum(m))))

    if neg:
        raise SystemExit(
            "FAIL: the attenuation goes negative inside the band of the fit, "
            "thus the ripple is larger than the loss. Make D_LONG longer, or "
            "match the line better.")
    if abs(mean - 1.0) > ALPHA_TOL:
        raise SystemExit(
            "FAIL: the measurement is %.2f times the closed form and this "
            "file asks for 1 +- %.2f. Read the table above: a ratio that "
            "is the same at every frequency is a scale error, and one that "
            "moves with the frequency is the rig or the loss model."
            % (mean, ALPHA_TOL))
    print("PASS")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
