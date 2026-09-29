"""Run the boards of the openEMS rigs through the EMerge solver.

EMerge is an experiment (`plugins/emerge_runner.py`). This file makes the
same boards as the openEMS rigs, with the builders of `validation/common/`,
and it runs each one through EMerge. Each case has a check that EMerge can
pass today:

  msl        a 50 ohm microstrip through line: a good match, a low loss,
             and 7.07 V across the substrate in the E-field view
  series_r   a series 50 ohm resistor: S11 = -9.5 dB, S21 = -3.5 dB (ideal)
  shunt_c    10 pF in shunt to a via: a deep notch in |S21| near 1.2 GHz
  cpw        a grounded CPW with no via on its top grounds: passive only
  stripline  a stripline on 4 layers: a good match, a low loss
  patch      an inset-fed patch antenna for 2.4 GHz, with no KiCad board:
             a dip in S11 near 2.4 GHz, and a far field that is a patch
             (Dmax 5 to 9 dBi, the beam at broadside, most of the input
             power radiated)

**A line port is a wave port in EMerge**, and the patch has a lumped
port. Each case also tests that no column of the S-matrix gives out more
power than it gets.

Run it with the python of KiCad 10, which starts the venv of EMerge itself
(`solverenv.emerge_python`):
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_boards_emerge.py [mesh] [case ...]

The first run on a machine compiles the code of EMerge for some minutes.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # the repository
PLUGINS = os.path.join(ROOT, "plugins")
sys.path.insert(0, PLUGINS)
# the board builders, which need no solver
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import make_lumped_board  # noqa: E402
import make_test_board  # noqa: E402
import solverenv  # noqa: E402

MARGIN = 4.0
Z0 = 50.0
# The largest sum|S|^2 of a column that a passive board can give, with a
# small margin for the mesh.
POWER_MAX = 1.02
# The field views have the scale of CST: a wave of 0.5 W. On a matched line
# of Z0 that is a peak voltage of sqrt(2 * 0.5 * Z0), and Ez * h at the
# middle of the line gives it. 10% holds the mesh and the small mismatch.
LINE_V_TOL = 0.10

# The patch: FR-4 of 1.53 mm with a low loss, thus the radiation and not
# the substrate takes the power. W and L come from the transmission-line
# model of the patch for 2.4 GHz (er 4.5), and the inset from
# R(y0) = R_edge cos^2(pi y0 / L) with R_edge of about 310 ohm.
PATCH_W, PATCH_L, PATCH_INSET = 37.7, 29.1, 10.7
PATCH_H, PATCH_ER, PATCH_TAND = 1.53, 4.5, 0.002
FEED_W, FEED_GAP, FEED_LEN = 2.9, 1.0, 12.0
# The air box must be a part of a wavelength away from the patch for the
# far field: 30 mm is 0.24 wavelength at 2.4 GHz.
PATCH_MARGIN = 30.0


def _board(case, outdir):
    """Give (board, pads, port type, f_start, f_stop) of a case."""
    path = os.path.join(outdir, case + ".kicad_pcb")
    if case == "msl":
        return make_test_board.make(path) + ("msl", 1e9, 6e9)
    if case == "series_r":
        return make_lumped_board.make(path) + ("msl", 1e9, 6e9)
    if case == "shunt_c":
        return make_lumped_board.make_shunt(path) + ("msl", 0.5e9, 5e9)
    if case == "cpw":
        return make_test_board.make_cpw(path) + ("cpw", 1e9, 6e9)
    if case == "stripline":
        return make_test_board.make_stripline(path) + ("stripline", 1e9, 6e9)
    raise SystemExit("unknown case %r: use one of %s" % (case,
                                                          ", ".join(CASES)))


def patch_model(mesh):
    """Give the model.json of the patch, with no KiCad board.

    The patch is centred on the origin, and its radiating edges are
    parallel to x. The feed comes from -y into an inset, and the port is at
    the end of the feed. y points up, as in each model.json.
    """
    hw, hl = 0.5 * PATCH_W, 0.5 * PATCH_L
    nx = 0.5 * FEED_W + FEED_GAP          # half of the width of the notch
    top = -hl + PATCH_INSET               # the end of the inset
    patch = [[-hw, -hl], [-nx, -hl], [-nx, top], [nx, top], [nx, -hl],
             [hw, -hl], [hw, hl], [-hw, hl]]
    y0 = -hl - FEED_LEN                   # the end of the feed
    fw = 0.5 * FEED_W
    # The feed goes 0.5 mm into the patch, thus the two polygons overlap.
    feed = [[-fw, y0], [fw, y0], [fw, top + 0.5], [-fw, top + 0.5]]
    br = {"x0": -hw - 15.0, "x1": hw + 15.0, "y0": y0 - 2.0,
          "y1": hl + 15.0}
    m = PATCH_MARGIN
    return {
        "version": solverenv.MODEL_VERSION, "stackup_source": "default",
        "copper_layers": [{"name": "F.Cu", "z": PATCH_H, "thickness": 0.035},
                          {"name": "B.Cu", "z": 0.0, "thickness": 0.035}],
        "dielectric_layers": [{"name": "dielectric 1", "z_top": PATCH_H,
                               "z_bottom": 0.0, "epsilon": PATCH_ER,
                               "loss_tangent": PATCH_TAND}],
        # The runner of EMerge keeps `margin_mm` of the region as air.
        "region": {"x0": br["x0"] - 2 * m, "x1": br["x1"] + 2 * m,
                   "y0": br["y0"] - 2 * m, "y1": br["y1"] + 2 * m},
        "board_rect": br,
        "polygons": {"F.Cu": [patch, feed],
                     "B.Cu": [[[br["x0"], br["y0"]], [br["x1"], br["y0"]],
                               [br["x1"], br["y1"]], [br["x0"], br["y1"]]]]},
        "vias": [], "lumped_elements": [], "warnings": [],
        "ports": [{"number": 1, "label": "the feed", "x": 0.0,
                   "y": y0 + fw, "layer": "F.Cu", "ref_layer": "B.Cu",
                   "width": FEED_W, "length": FEED_W, "direction": [0, 1],
                   "track_width": FEED_W, "type": "lumped", "gap": None,
                   "copper_run": None, "ref_layer2": None, "height": None,
                   "asymmetry": 0.0}],
        "settings": {"f_start": 1.8e9, "f_stop": 3.0e9, "f_field": 2.4e9,
                     "z0": Z0, "margin_mm": m, "mesh": mesh, "n_freq": 401,
                     "fem_points": 25, "lumped": False, "solver": "emerge"},
    }


def read_touchstone(path):
    """Give (f, S) of a file of 1 or 2 ports that `write_touchstone`
    wrote."""
    rows = np.loadtxt(path, comments=("!", "#"), ndmin=2)
    n = int(round(((rows.shape[1] - 1) / 2) ** 0.5))
    v = rows[:, 1::2] + 1j * rows[:, 2::2]
    S = np.empty((len(rows), n, n), dtype=complex)
    if n == 1:
        S[:, 0, 0] = v[:, 0]
    else:  # the sequence of a 2-port file: S11 S21 S12 S22
        S[:, 0, 0], S[:, 1, 0], S[:, 0, 1], S[:, 1, 1] = v.T
    return rows[:, 0], S


def simulate(case, mesh):
    """Make the board, extract it, and solve it with EMerge. Give (f, S,
    the output directory)."""
    outdir = os.path.join(HERE, "out_%s_%s" % (case, mesh))
    os.makedirs(outdir, exist_ok=True)
    if case == "patch":
        model = patch_model(mesh)
    else:
        board, pads, kind, f_start, f_stop = _board(case, outdir)
        model = board_reader.extract(board, pads, margin_mm=MARGIN,
                                     f_stop=f_stop, mesh=mesh)
        for p in model["ports"]:
            p["type"] = kind
        for e in model["lumped_elements"]:
            # The ideal part, as in the openEMS rigs: no body.
            e.update(esl=0.0, esr=0.0, package="Custom")
        model["settings"] = {
            "f_start": f_start, "f_stop": f_stop, "z0": Z0,
            "margin_mm": MARGIN, "mesh": mesh, "n_freq": 401, "lumped": True,
            "parasitics": False, "solver": "emerge",
        }
    model_path = os.path.join(outdir, "model.json")
    with open(model_path, "w") as fh:
        json.dump(model, fh, indent=1)

    py = solverenv.emerge_python()
    if not py:
        raise SystemExit("no EMerge venv: set RFSIM_EMERGE_PYTHON, or make "
                         "C:\\emerge\\venv with `pip install emerge`")
    log = subprocess.run([py, os.path.join(PLUGINS, "emerge_runner.py"),
                          model_path, outdir], capture_output=True,
                         encoding="utf-8", errors="replace")
    for line in log.stdout.splitlines():
        if line.startswith("[rfsim]"):
            print("   " + line)
    if log.returncode:
        print(log.stdout[-2000:], log.stderr[-2000:])
        raise SystemExit("%s: the EMerge run stopped with an error" % case)
    n = len(model["ports"])
    f, S = read_touchstone(os.path.join(outdir, "results.s%dp" % n))
    return f, S, outdir


def db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


def line_voltage(outdir):
    """Give |Ez| * h at the middle of the line of the msl board, from the
    E-field view of port 1, with the scale that the results window uses."""
    import gui  # the loader of the results window
    with open(os.path.join(outdir, "model.json")) as fh:
        model = json.load(fh)
    x, y, F, _ = gui._load_field(os.path.join(outdir, "exc1", "Ef.h5"))
    p1, p2 = model["ports"][:2]
    i = int(np.argmin(np.abs(y - p1["y"])))
    j = int(np.argmin(np.abs(x - 0.5 * (p1["x"] + p2["x"]))))
    d = model["dielectric_layers"][0]
    return abs(F[i, j, 2]) * (d["z_top"] - d["z_bottom"]) * 1e-3


def check_patch(f, s11, outdir):
    """Give the checks of the patch that it does not pass."""
    fails = []
    i = int(np.argmin(s11))
    print("   S11 dip at %.3f GHz, %.1f dB" % (f[i] / 1e9, s11[i]))
    if not (2.1e9 < f[i] < 2.7e9 and s11[i] < -6.0):
        fails.append("no dip of -6 dB near 2.4 GHz")
    with open(os.path.join(outdir, "farfield_p1.json")) as fh:
        ff = json.load(fh)
    cut = ff["cuts"]["Phi=0"]
    peak = cut["angle_deg"][int(np.argmax(cut["D_dBi"]))]
    print("   far field at %.2f GHz: Dmax %.1f dBi, the beam at theta %g "
          "(Phi=0), %.0f%% of the input power radiated"
          % (ff["f_hz"] / 1e9, ff["Dmax_dBi"], peak, ff["efficiency_pct"]))
    if not 5.0 < ff["Dmax_dBi"] < 9.0:
        fails.append("Dmax %.1f dBi is not the Dmax of a patch"
                     % ff["Dmax_dBi"])
    if abs(peak) > 30.0:
        fails.append("the beam is at theta %g, not at broadside" % peak)
    if not ff["efficiency_pct"] or ff["efficiency_pct"] < 40.0:
        fails.append("only %s%% of the input power radiates"
                     % ff["efficiency_pct"])
    return fails


def check(case, f, S, outdir):
    """Give the list of the checks that the case does not pass."""
    fails = []
    s11 = db(S[:, 0, 0])
    s21 = db(S[:, 1, 0]) if S.shape[1] > 1 else None
    power = np.max(np.sum(np.abs(S) ** 2, axis=1))
    print("   max sum|S|^2 of a column: %.3f" % power)
    if power > POWER_MAX:
        fails.append("the board gives out more power than it gets "
                     "(%.3f)" % power)
    if case in ("msl", "stripline"):
        print("   S11 max %.1f dB, S21 min %.2f dB" % (s11.max(), s21.min()))
        if s11.max() > -10.0:
            fails.append("poor match: S11 %.1f dB" % s11.max())
        if s21.min() < (-1.5 if case == "msl" else -3.5):
            fails.append("excess loss: S21 %.2f dB" % s21.min())
    if case == "msl":
        v, want = line_voltage(outdir), (2 * 0.5 * Z0) ** 0.5
        print("   E-field view: |Ez| * h = %.2f V at the middle of the line "
              "(%.2f V from 0.5 W)" % (v, want))
        if abs(v / want - 1.0) > LINE_V_TOL:
            fails.append("the E-field view gives %.2f V, not %.2f V"
                         % (v, want))
    elif case == "series_r":
        i = int(np.argmin(np.abs(f - 2e9)))
        print("   at %.2f GHz: S11 %.2f dB (ideal -9.54), S21 %.2f dB "
              "(ideal -3.52)" % (f[i] / 1e9, s11[i], s21[i]))
        if not -12.0 < s11[i] < -7.0:
            fails.append("S11 %.2f dB, far from -9.5" % s11[i])
        if not -5.0 < s21[i] < -2.5:
            fails.append("S21 %.2f dB, far from -3.5" % s21[i])
    elif case == "patch":
        fails += check_patch(f, s11, outdir)
    elif case == "shunt_c":
        i = int(np.argmin(s21))
        print("   notch at %.3f GHz, %.1f dB" % (f[i] / 1e9, s21[i]))
        if not 0.8e9 < f[i] < 1.8e9:
            fails.append("the notch is at %.3f GHz, not near 1.2"
                         % (f[i] / 1e9))
        if s21[i] > -20.0:
            fails.append("the notch is only %.1f dB deep" % s21[i])
    return fails


CASES = ("msl", "series_r", "shunt_c", "cpw", "stripline", "patch")


def main(argv):
    mesh = "coarse"
    if argv and argv[0] in solverenv.RES_DIV:
        mesh = argv.pop(0)
    cases = argv or list(CASES)
    bad = 0
    for case in cases:
        print("=== %s (%s) ===" % (case, mesh))
        f, S, outdir = simulate(case, mesh)
        fails = check(case, f, S, outdir)
        for why in fails:
            print("   FAIL: " + why)
        bad += bool(fails)
        print("   %s" % ("PASS" if not fails else "FAIL"))
    print("PASS" if not bad else "FAIL (%d of %d cases)" % (bad, len(cases)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
