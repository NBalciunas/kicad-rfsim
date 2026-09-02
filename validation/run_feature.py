"""Measure `runner.POLY_FEATURE_CELLS` against a closed form.

The rule gives N cells across a copper feature that is narrower than
`res` and that no port covers. **No board of `validation/` could see it
before this file.** Every line of every other board carries a port, and
`_feature_lines` skips a feature that holds a mesh line already, thus the
port rule (`MSL_STRIP_CELLS`) made those cells and not this one.

**The board.** A through microstrip of 2.9 mm along x, with a port at
each end, and ONE open stub of `W_STUB` that goes in y from the middle
of that line. The stub is narrower than `res` at every preset, and its
width lies on the x axis, where the two ports put their lines at their
own ends only. Thus `_feature_lines` alone meshes the stub, and the
number of cells across it is the value under test.

**The measurement is a FREQUENCY, and a DIFFERENCE of two of them.** An
open stub is a quarter-wave resonator, thus |S21| has a notch at

    f = c / (4 * (l + dl) * sqrt(eps_eff))

`dl` is the open end of the stub, and the plane of the T junction is not
the edge of the through line either. Both are UNKNOWN, and both are the
same for two stubs that differ in LENGTH alone. Thus two lengths give

    sqrt(eps_eff) = c * (1/f1 - 1/f2) / (4 * (l1 - l2))

and every constant end effect cancels. This is the method of
`run_shunt.py`: a frequency carries no scale error, and a difference
carries no offset.

The closed form is Hammerstad and Jensen for the static value, with the
dispersion of Kirschning and Jansen at the frequency of the notch. P16
says that a STATIC closed form cannot decide a narrow line, thus this
file does not use one.

Run it with the python of the solver, or with the python of KiCad (it
needs no pcbnew, and it starts the solver itself):

    C:\\openEMS\\venv\\Scripts\\python.exe run_feature.py [coarse|medium]

Each run takes about a minute, and the file makes 2 runs for each value
of the ladder.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# RFSIM_PLUGINS names the plugins directory to measure, thus this file
# can run against a copy of the code that holds a different rule. This
# is the A/B of NOTES.md: copy `plugins/` outside the repository, change
# the one rule, and compare the two answers on the same board.
PLUGINS = os.environ.get("RFSIM_PLUGINS") or os.path.join(
    os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import solverenv  # noqa: E402

C0 = 299792458.0
ER = 4.5                 # the substrate of every board of validation/
H_SUB = 1.53             # mm
W_LINE = 2.9             # the through line, 50 ohm on this stackup
W_STUB = 0.4             # the feature under test: narrower than res
# Two lengths, measured from the lower edge of the through line to the
# open end. Both notches must stay inside the sweep with a margin, and
# the third harmonic of the longer one must stay OUT of it.
L1, L2 = 10.0, 18.0
F_START, F_STOP, N_FREQ = 1e9, 6e9, 601
NOTCH_MIN_DB = -10.0     # a dip that is not deeper than this is no notch
FIT_HALF_WIDTH = 8       # bins at each side of the minimum, for the fit
# The ladder of the value under test. 1 means NO line inside the
# feature, which is the state that the rule exists to correct.
#
# **The merge tolerance caps this ladder, and the cap is not the same on
# every board.** `_feature_lines` gives NO line when the cells that it
# would make are smaller than `tol` of `_mesh`, because `_merge_close`
# removes such a line again. A stub of 0.4 mm at the coarse preset has
# tol = 0.181 mm, thus 3 cells (0.133 mm) fall under it and the feature
# goes back to ONE cell with a warning. `realizable()` asks the mesh
# itself which values of the ladder a board can carry, thus the file
# measures those and does not run the solver for the others.
CELLS = (1, 2, 3, 4, 6)
# The tolerance on eps_eff against the closed form. Kirschning and Jansen
# give about 1% on this geometry, and the FDTD adds the cells across the
# strip and the two ends of the stub. A rule that stands 10% away from
# the theory does not model this line.
EPS_TOL = 0.10


# ----------------------------------------------------------------- theory
def eps_eff_static(w, h, er):
    """Hammerstad and Jensen, the static effective permittivity."""
    u = w / h
    a = (1.0 + np.log((u ** 4 + (u / 52.0) ** 2) / (u ** 4 + 0.432)) / 49.0
         + np.log(1.0 + (u / 18.1) ** 3) / 18.7)
    b = 0.564 * ((er - 0.9) / (er + 3.0)) ** 0.053
    return (er + 1) / 2.0 + (er - 1) / 2.0 * (1.0 + 10.0 / u) ** (-a * b)


def z0_static(w, h, er):
    """Hammerstad and Jensen, the static impedance of the line."""
    u = w / h
    f = 6.0 + (2.0 * np.pi - 6.0) * np.exp(-(30.666 / u) ** 0.7528)
    z01 = 376.730313 / (2.0 * np.pi) * np.log(f / u + np.sqrt(1.0 + (2.0 / u) ** 2))
    return z01 / np.sqrt(eps_eff_static(w, h, er))


def eps_eff_f(w, h, er, f_hz):
    """Kirschning and Jansen: eps_eff at a frequency, not at DC.

    P16 says that a static closed form cannot decide a narrow line,
    because the eps_eff of a run stands over the static value. This
    correction is the part of that gap which the THEORY explains.
    """
    u = w / h
    fn = f_hz / 1e9 * h                      # GHz*mm
    p1 = (0.27488 + (0.6315 + 0.525 / (1.0 + 0.0157 * fn) ** 20) * u
          - 0.065683 * np.exp(-8.7513 * u))
    p2 = 0.33622 * (1.0 - np.exp(-0.03442 * er))
    p3 = 0.0363 * np.exp(-4.6 * u) * (1.0 - np.exp(-(fn / 38.7) ** 4.97))
    p4 = 1.0 + 2.751 * (1.0 - np.exp(-(er / 15.916) ** 8))
    p = p1 * p2 * ((0.1844 + p3 * p4) * fn) ** 1.5763
    e0 = eps_eff_static(w, h, er)
    return er - (er - e0) / (1.0 + p)


def open_end_mm(w, h, er):
    """The extra length of an open end (Hammerstad and Bekkadal).

    The difference of two lengths removes it. This file reports it to
    show its size, and it does not use it in the result.
    """
    u = w / h
    e = eps_eff_static(w, h, er)
    return h * 0.412 * (e + 0.3) / (e - 0.258) * (u + 0.262) / (u + 0.813)


# ------------------------------------------------------------------ board
def board_model(l_stub, mesh, w_stub=W_STUB):
    """Give the model.json of the stub board, with no KiCad at all.

    The board reader is not part of what this file measures, thus the
    model comes from arithmetic. The stackup and the through line are
    those of `microstrip_50ohm.kicad_pcb`.
    """
    x0, x1 = 0.0, 40.0
    y0, y1 = -40.0, 0.0
    yc = -10.0                       # the axis of the through line
    hw = 0.5 * W_LINE
    line = [[3.55, yc - hw], [36.45, yc - hw], [36.45, yc + hw], [3.55, yc + hw]]
    # The stub goes UP to the far edge of the through line, thus the two
    # sheets meet over an area and not on one line, and the edge where
    # they meet lies exactly on an edge that the line already has. An
    # overlap that ends INSIDE the line would leave an edge of its own,
    # and `_feature_lines` would read the copper between that edge and
    # the edge of the line as a narrow feature that no board has.
    # `l_stub` stays the length from the lower edge of the through line,
    # and the part inside the line is the same in both runs: the
    # difference removes it with the other end effects.
    xc = 20.0
    top = yc + hw
    bot = yc - hw - l_stub
    stub = [[xc - 0.5 * w_stub, bot], [xc + 0.5 * w_stub, bot],
            [xc + 0.5 * w_stub, top], [xc - 0.5 * w_stub, top]]
    ground = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    margin = 4.0
    port = dict(layer="F.Cu", ref_layer="B.Cu", ref_layer2=None, height=None,
                asymmetry=0.0, gap=None, width=W_LINE, length=W_LINE,
                track_width=W_LINE, type="msl", copper_run=None, y=yc)
    return {
        "version": 1,
        "stackup_source": "default",
        "copper_layers": [{"name": "F.Cu", "z": H_SUB, "thickness": 0.035},
                          {"name": "B.Cu", "z": 0.0, "thickness": 0.035}],
        "dielectric_layers": [{"name": "dielectric 1", "z_top": H_SUB,
                               "z_bottom": 0.0, "epsilon": ER,
                               "loss_tangent": 0.02}],
        "region": {"x0": x0 - margin - 0.05, "x1": x1 + margin + 0.05,
                   "y0": y0 - margin - 0.05, "y1": y1 + margin + 0.05},
        "board_rect": {"x0": x0, "x1": x1, "y0": y0, "y1": y1},
        "polygons": {"F.Cu": [line, stub], "B.Cu": [ground]},
        "vias": [],
        "ports": [dict(port, number=1, label="P1", x=5.0, direction=[1, 0]),
                  dict(port, number=2, label="P2", x=35.0, direction=[-1, 0])],
        "lumped_elements": [],
        "warnings": [],
        "settings": {"f_start": F_START, "f_stop": F_STOP, "z0": 50.0,
                     "margin_mm": margin, "mesh": mesh, "n_freq": N_FREQ,
                     "max_timesteps": 300000, "end_criteria": 1e-4,
                     "lumped": False, "excite": [1]},
    }


# ------------------------------------------------------------------ solve
def runner_with(cells, root):
    """Give the path of a runner.py whose POLY_FEATURE_CELLS is `cells`.

    The value is a constant of the module, thus a run cannot change it
    from the model. This copies `plugins/` beside the work and rewrites
    that ONE line, which is the method that NOTES.md gives for an A/B
    against a different runner.
    """
    dst = os.path.join(root, "plugins_%d" % cells)
    # Copy every time. A copy that stays behind measures the code of
    # the run that made it, thus a change to `plugins/` would go
    # unseen and the rig would give the old answer with no message.
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    for fn in os.listdir(PLUGINS):
        if fn.endswith(".py"):
            shutil.copy(os.path.join(PLUGINS, fn), dst)
    path = os.path.join(dst, "runner.py")
    with open(path, "rb") as fh:
        data = fh.read()
    new, n = re.subn(rb"(?m)^POLY_FEATURE_CELLS = \d+",
                     b"POLY_FEATURE_CELLS = %d" % cells, data)
    if n != 1:
        raise SystemExit("could not set POLY_FEATURE_CELLS in %s" % path)
    with open(path, "wb") as fh:
        fh.write(new)
    return path


def realizable(cells, l_stub, mesh, w_stub, root):
    """Tell how many cells the mesh really puts across the stub.

    The rule and the merge tolerance argue with each other, thus the
    only reliable answer comes from `_mesh` itself. This costs no solver
    run: it is the fast path of NOTES.md, "the mesh rules in seconds".
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "runner_c%d" % cells, runner_with(cells, root))
    r = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(r)
    model = board_model(l_stub, mesh, w_stub)
    eps = max(d["epsilon"] for d in model["dielectric_layers"])
    res = (r.C0 / model["settings"]["f_stop"] / np.sqrt(eps) * 1e3
           / r.RES_DIV[mesh])
    xs = r._mesh(model, r._port_geometry(model, res), res)[0]
    lo, hi = 20.0 - 0.5 * w_stub, 20.0 + 0.5 * w_stub
    inside = [v for v in xs if lo - 1e-9 < v < hi + 1e-9]
    return max(1, len(inside) - 1), res


def solve(cells, l_stub, mesh, w_stub, root):
    """Run one case and give (f, S21)."""
    tag = "c%d_l%g_w%g" % (cells, l_stub, w_stub)
    outdir = os.path.join(root, "out_feature_%s_%s" % (tag, mesh))
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "model.json")
    with open(path, "w") as fh:
        json.dump(board_model(l_stub, mesh, w_stub), fh, indent=1)
    py = solverenv.solver_python() or sys.executable
    log = subprocess.run([py, runner_with(cells, root), path, outdir],
                         capture_output=True, text=True)
    if log.returncode != 0:
        print(log.stdout[-2000:])
        print(log.stderr[-1000:])
        raise SystemExit("solver failed for %s" % tag)
    for line in log.stdout.splitlines():
        if "mesh:" in line or "ERROR" in line or "timesteps" in line:
            print("     " + line.strip())
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    return rows[:, 0], rows[:, 3] + 1j * rows[:, 4]


def notch(f, s21):
    """Give (the frequency of the deepest dip, its depth in dB).

    A parabola through the bins at each side of the minimum gives the
    frequency between two bins. The step of the sweep is 8.3 MHz, thus
    the fit is worth about one part in 500 of the result.
    """
    db = 20.0 * np.log10(np.abs(s21) + 1e-15)
    i = int(np.argmin(db))
    lo = max(0, i - FIT_HALF_WIDTH)
    hi = min(len(f), i + FIT_HALF_WIDTH + 1)
    if hi - lo < 3:
        return f[i], db[i]
    a, b, _ = np.polyfit(f[lo:hi], db[lo:hi], 2)
    f0 = -b / (2.0 * a) if a > 0 else f[i]
    if not f[lo] <= f0 <= f[hi - 1]:
        f0 = f[i]
    return f0, db[i]


# ------------------------------------------------------------------- main
def main(mesh="coarse", w_stub=W_STUB):
    w_stub = float(w_stub)
    # A run against another plugins directory keeps its own output, thus
    # the copies of `runner_with()` of the two do not mix.
    tag = "" if PLUGINS.endswith("plugins") else "_" + os.path.basename(PLUGINS)
    root = os.path.join(HERE, "out_feature_%s_w%g%s" % (mesh, w_stub, tag))
    os.makedirs(root, exist_ok=True)
    print("the stub is %.2f mm wide on a substrate of %.2f mm, er %.1f, at "
          "the %s preset" % (w_stub, H_SUB, ER, mesh))
    print("the closed form: eps_eff(static) = %.4f, Z0 = %.1f ohm, the open "
          "end adds %.3f mm"
          % (eps_eff_static(w_stub, H_SUB, ER), z0_static(w_stub, H_SUB, ER),
             open_end_mm(w_stub, H_SUB, ER)))
    print("the two stubs are %.1f mm and %.1f mm" % (L1, L2))

    # Ask the mesh which values of the ladder this board can carry,
    # before any solver runs.
    ladder, skipped = [], []
    for cells in CELLS:
        got, res = realizable(cells, L1, mesh, w_stub, root)
        if got == cells:
            ladder.append((cells, res))
        else:
            skipped.append((cells, got))
    if not ladder:
        raise SystemExit("no value of the ladder is realizable on this board")
    print("res = %.4f mm; the mesh carries %s cell(s) across the stub"
          % (ladder[0][1], ", ".join(str(c) for c, _ in ladder)))
    if skipped:
        print("the merge tolerance refuses %s (each one falls back to 1 "
              "cell), thus this board cannot measure them"
              % ", ".join("%d" % c for c, _ in skipped))
    print()

    print("%6s %11s %11s %9s %9s %8s" % ("cells", "notch 1", "notch 2",
                                         "eps_eff", "theory", "error"))
    rows = []
    for cells, _res in ladder:
        fs, ds = [], []
        for l_stub in (L1, L2):
            f, s21 = solve(cells, l_stub, mesh, w_stub, root)
            f0, depth = notch(f, s21)
            if depth > NOTCH_MIN_DB:
                raise SystemExit(
                    "the stub of %.1f mm gives no notch (the deepest dip is "
                    "%.1f dB at %.4f GHz): the resonance is outside the "
                    "sweep, or the stub does not conduct"
                    % (l_stub, depth, f0 / 1e9))
            fs.append(f0)
            ds.append(depth)
        # Every constant end effect cancels in the difference.
        root_eps = C0 * (1.0 / fs[0] - 1.0 / fs[1]) / (4.0 * (L1 - L2) * 1e-3)
        eps = root_eps ** 2
        # The theory at the MEAN of the two notches: the two resonances
        # sit at different frequencies, thus the dispersion is not the
        # same for them. The line below the table gives that residual.
        f_mid = 0.5 * (fs[0] + fs[1])
        th = eps_eff_f(w_stub, H_SUB, ER, f_mid)
        rows.append((cells, fs[0], fs[1], eps, th, ds))
        print("%6d %8.4f GHz %8.4f GHz %9.4f %9.4f %+7.1f%%"
              % (cells, fs[0] / 1e9, fs[1] / 1e9, eps, th,
                 100.0 * (eps / th - 1.0)))

    print("\nthe theory moves %.4f to %.4f over the two notch frequencies: "
          "that spread is the residual of the difference method"
          % (eps_eff_f(w_stub, H_SUB, ER, rows[0][2]),
             eps_eff_f(w_stub, H_SUB, ER, rows[0][1])))
    for a, b in zip(rows, rows[1:]):
        print("from %d cells to %d cells the answer moves %+.2f%%"
              % (a[0], b[0], 100.0 * (b[3] / a[3] - 1.0)))
    shipped = [r for r in rows if r[0] == 2]
    if shipped:
        err = shipped[0][3] / shipped[0][4] - 1.0
        print("POLY_FEATURE_CELLS = 2 gives %+.1f%% against the closed form"
              % (100.0 * err))
        if abs(err) > EPS_TOL:
            raise SystemExit(
                "FAIL: the shipped value gives %+.1f%% against the closed "
                "form, and this file asks for %.0f%%. Read the table above: "
                "the value that stops moving is the value to ship."
                % (100.0 * err, 100.0 * EPS_TOL))
    print("PASS")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
