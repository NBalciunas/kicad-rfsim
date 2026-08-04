"""Measure a body ESL from the FREQUENCY of a notch, not from an amplitude.

The board is a microstrip of 50 ohm with one capacitor in SHUNT to
ground (`run_lumped.make_shunt`). The capacitor, its pads and the via to
the plane make a SERIES resonance, thus |S21| has a deep notch at

    f0 = 1 / (2*pi*sqrt(L_total*C))

and the notch gives the inductance back:

    L_total = 1 / ((2*pi*f0)**2 * C)

L_total is the body ESL of the part PLUS the loop of the board (the
pads, the gap and the via). The board part of it is the same in every
run. Thus the ESL of the body comes from a DIFFERENCE of two runs:

    ESL(measured) = L_total(with the ESL) - L_total(with no ESL)

**Why a difference of frequencies, and not one absolute value.** The log
of 2026-08-04 (6) measured the extraction of an absolute value on the
series board: it carries 10 to 20 ohm of systematic error which the mesh
does not remove, against 3.1 ohm for an 0402 ESL at 2 GHz. That
systematic is a scale error of the amplitude, and no scale error can
move a frequency. This file is the method that remains.

The tool asserts three things:

1. Each run gives a deep notch (the resonance exists).
2. The board inductance is the same for two DIFFERENT capacitors. It is
   a property of the board, thus it must not follow the value of the
   part. This is the control of the method.
3. The measured ESL agrees with the value that went in, for 0.25 nH and
   for 1.0 nH.

`run_shunt.py <mesh> packages` does item 6b: it measures each entry of
`board_reader._ESL_NH` on the land pattern of its own package.

Run this file with the python of KiCad 10. It needs pcbnew, and it
starts the solver itself:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_shunt.py [coarse|medium|fine] [packages]
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import solverenv  # noqa: E402
import run_lumped  # noqa: E402  (this file uses its board builder)

Z0 = 50.0
C_NOM = 10e-12          # the notch of this board is then near 1.5 GHz
C_CONTROL = 4.7e-12     # the control: the same board, another capacitor
ESL_CASES = (0.25e-9, 1.0e-9)   # the two bodies that the tool must measure
# The sweep holds the notch of every case with a wide margin at each end.
# A parabola needs points at the two sides of the minimum.
F_START, F_STOP, N_FREQ = 0.5e9, 5e9, 601
FIT_HALF_WIDTH = 8      # the bins at each side of the minimum, for the fit
NOTCH_MIN_DB = -10.0    # a notch that is not deeper than this is not a notch
L_BOARD_RANGE = (0.2e-9, 5e-9)  # the pads, the gap and a via of 1.6 mm
# The tolerances come from the runs of 2026-08-04, at the coarse preset
# and at the medium one. The control gave 1.0% at each preset, and the
# largest error of an ESL was 2.3% (0.006 nH on 0.25 nH). Thus each
# tolerance below keeps a margin of 5 times or more against a
# measurement, and a real failure is much larger than that: an ESL that
# the solver drops gives 100%.
# The multithreaded engine is not bit-exact, thus two runs of the same
# model give notches that differ by as much as 0.2%, and an ESL that
# differs by about 0.01 nH. Read no more than that from the result.
CONTROL_TOL = 0.10      # L_board must not follow the value of the capacitor
ESL_TOL = 0.15          # of the nominal value
ESL_FLOOR_H = 0.03e-9   # ...but never a tolerance smaller than this
# The two-part board of `two()`: a lumped inductor that is an element of
# its own reads about 5% high, thus its tolerance is not the tolerance of
# an ESL. Refer to the log of 2026-08-04 (10).
L_PART_TOL = 0.15
# |S11|^2 + |S21|^2 at the notch. The branch has no resistance in these
# runs, thus the power that does not go through must come back, and only
# the copper, the dielectric and the radiation take a little. A run that
# gives less than this is not a measurement: an inductor of 5 nH gave
# 0.951 at coarse and 0.917 at medium, and its value then moved from
# +5.1% to +16.2%. An inductor of 2 nH gives 0.975 and reads +5% at each
# preset. This guard is what tells the two conditions apart.
POWER_MIN = 0.95


def _in_poly(polys, x, y):
    """Tell if (x, y) is inside any polygon of `polys` (the ray rule)."""
    for poly in polys:
        inside = False
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % n]
            if (y0 > y) != (y1 > y):
                xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                if x < xc:
                    inside = not inside
        if inside:
            return True
    return False


def _check_gaps_open(model, centers):
    """Test that no copper bridges any gap of `centers`.

    The lesson of the log of 2026-08-04 (5): a round track end went past a
    pad and into the gap, and the board then measured the copper and not
    the part. Test the board BEFORE you read a number from it. The ring
    of a via can do the same on a small land: refer to
    `run_lumped.make_shunt`.
    """
    polys = model["polygons"].get("F.Cu", [])
    assert polys, "the model holds no copper on F.Cu"
    for gx, gy in centers:
        assert not _in_poly(polys, gx, -gy), \
            "copper bridges the gap of the part at (%.3f, %.3f) mm" % (gx, gy)


def _check_gap_is_open(model, pkg=None):
    """Test the one gap of the board of `make_shunt`."""
    _check_gaps_open(model, [run_lumped.shunt_gap_center(pkg)])


def notch(f, s21):
    """Give (the frequency of the notch, its depth in dB).

    The minimum bin is not exact enough: the notch of this board moves by
    about 10% for an ESL of 0.25 nH, and the bins are 7.5 MHz. Thus the
    tool fits a parabola to the LINEAR magnitude around the minimum,
    which is smooth there, and takes the vertex of it. It does not fit
    the dB values: a deep notch is a cusp in dB.

    Magnitudes only, and no phase: the reference plane of each port is
    about 15 mm from the part, and that line rotates the phase. A matched
    line with no loss cannot change |S21|, thus it cannot move the notch.
    """
    mag = np.abs(s21)
    i = int(np.argmin(mag))
    lo, hi = i - FIT_HALF_WIDTH, i + FIT_HALF_WIDTH + 1
    assert lo >= 0 and hi <= len(f), \
        "the notch is at the edge of the sweep (%.3f GHz)" % (f[i] / 1e9)
    a, b, _ = np.polyfit(f[lo:hi], mag[lo:hi], 2)
    assert a > 0, "the fit of the notch is not a minimum"
    return -0.5 * b / a, 20 * np.log10(mag[i] + 1e-12)


def inductance(f0, cval):
    """Give the inductance that a series resonance at f0 with cval implies."""
    return 1.0 / ((2 * np.pi * f0) ** 2 * cval)


def simulate(tag, cval, esl, mesh, pkg=None):
    """Make the board, extract it, and solve it. Give (f, S11, S21)."""
    outdir = os.path.join(HERE, "out_shunt_%s_%s" % (tag, mesh))
    os.makedirs(outdir, exist_ok=True)
    board, pads = run_lumped.make_shunt(
        os.path.join(outdir, "shunt_c.kicad_pcb"), pkg=pkg)

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin)
    for p in model["ports"]:
        p["type"] = "msl"
    les = model["lumped_elements"]
    assert len(les) == 1 and les[0]["type"] == "C" and les[0]["ny"] == "y", les
    _check_gap_is_open(model, pkg)
    # The value and the body come from this file, and not from the board:
    # the runs must differ in the ESL ALONE. The ESR stays 0, thus the
    # depth of the notch also stays a property of the board only.
    les[0]["value"] = cval
    les[0]["esl"] = esl
    les[0]["esr"] = 0.0
    les[0]["package"] = "Custom"
    model["settings"] = {
        "f_start": F_START, "f_stop": F_STOP, "z0": Z0, "margin_mm": margin,
        "mesh": mesh, "n_freq": N_FREQ, "max_timesteps": 300000,
        "end_criteria": 1e-4, "lumped": True, "parasitics": True,
        "excite": [1],   # port 1 only: this test needs S11 and S21 only
    }
    return _solve(outdir, model, tag)


def measure(tag, cval, esl, mesh, pkg=None):
    """Run one case and give (f0, depth in dB, L_total)."""
    print("\n=== %s: C=%.3g pF, body ESL=%.3g nH%s ==="
          % (tag, cval * 1e12, esl * 1e9, ", %s land" % pkg if pkg else ""))
    f, _, s21 = simulate(tag, cval, esl, mesh, pkg)
    f0, depth = notch(f, s21)
    ltot = inductance(f0, cval)
    print("   notch %.4f GHz, %.1f dB -> L_total %.4f nH"
          % (f0 / 1e9, depth, ltot * 1e9))
    return f0, depth, ltot


def _solve(outdir, model, tag):
    """Write model.json, run the solver, and give (f, S11, S21)."""
    model_path = os.path.join(outdir, "model.json")
    with open(model_path, "w") as fh:
        json.dump(model, fh, indent=1)
    runner = os.path.join(PLUGINS, "runner.py")
    solver_py = solverenv.solver_python() or sys.executable
    log = subprocess.run([solver_py, runner, model_path, outdir],
                         capture_output=True, text=True)
    if log.returncode != 0:
        print(log.stdout[-3000:])
        print(log.stderr[-2000:])
        raise SystemExit("solver failed for %s" % tag)
    for line in log.stdout.splitlines():
        if "lumped" in line or "ERROR" in line or "WARNING" in line:
            print("  " + line.strip())
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    return (rows[:, 0], rows[:, 1] + 1j * rows[:, 2],
            rows[:, 3] + 1j * rows[:, 4])


def two(mesh="coarse"):
    """Measure TWO parts that have an effect on each other.

    Item 7 of the roadmap. Every other board here holds ONE element, thus
    two boxes near each other, and the series topology under interaction,
    had no test at all.

    The board (`run_lumped.make_shunt2`) puts a C and an L in SERIES from
    the line to ground. The pair resonates at
    `1/(2*pi*sqrt((L + L_board)*C))`, thus the two parts together give a
    FREQUENCY. This is on purpose. A matching network is the circuit that
    the roadmap names, but its observable is a return loss - an
    AMPLITUDE - and the log of 2026-08-04 (6) measured what an amplitude
    is worth on this path: 10 to 20 ohm of systematic error. A resonance
    of the pair tests the same interaction with the observable that the
    log of (8) showed to be reliable.

    The control is the same difference as everywhere here: the second run
    makes the inductor a 0 ohm RESISTOR, which `runner.build` puts into
    the model as a box of METAL. Thus the geometry does not change at
    all, the branch keeps the same loop, and the difference of the two
    notches is the inductor alone. That run also tests the 0 ohm path,
    which no other test reaches.
    """
    cval = 4.7e-12
    # TWO values of the inductor, and not one. A lumped inductor of its
    # own reads about 8% HIGH on this mesh (the log of 2026-08-04 (10)),
    # and one value alone cannot say whether an error is proportional to
    # the inductor or a constant of the geometry. Two values can.
    # 2 nH and 1 nH. An inductor of 5 nH is OUT of the regime where this
    # board measures: refer to POWER_MIN.
    cases = [("short", "R", 0.0), ("l2", "L", 2e-9), ("l1", "L", 1e-9)]
    out = {}
    for tag, kind, value in cases:
        print("\n=== two parts: C=%.3g pF, %s=%.3g %s ==="
              % (cval * 1e12, "L" if kind == "L" else "L1 as a short",
                 value * (1e9 if kind == "L" else 1),
                 "nH" if kind == "L" else "ohm"))
        outdir = os.path.join(HERE, "out_shunt2_%s_%s" % (tag, mesh))
        os.makedirs(outdir, exist_ok=True)
        board, pads = run_lumped.make_shunt2(
            os.path.join(outdir, "shunt2.kicad_pcb"))
        margin = 4.0
        model = board_reader.extract(board, pads, margin_mm=margin)
        for p in model["ports"]:
            p["type"] = "msl"
        les = {e["ref"]: e for e in model["lumped_elements"]}
        assert set(les) == {"C1", "L1"}, sorted(les)
        _check_gaps_open(model, run_lumped.shunt2_gap_centers())
        les["C1"].update(value=cval, esl=0.0, esr=0.0, package="Custom")
        les["L1"].update(type=kind, value=value, esl=0.0, esr=0.0,
                         package="Custom")
        model["settings"] = {
            "f_start": F_START, "f_stop": F_STOP, "z0": Z0,
            "margin_mm": margin, "mesh": mesh, "n_freq": N_FREQ,
            "max_timesteps": 300000, "end_criteria": 1e-4, "lumped": True,
            "parasitics": True, "excite": [1],
        }
        f, s11, s21 = _solve(outdir, model, tag)
        f0, depth = notch(f, s21)
        ltot = inductance(f0, cval)
        i = int(np.argmin(np.abs(s21)))
        power = abs(s11[i]) ** 2 + abs(s21[i]) ** 2
        print("   notch %.4f GHz, %.1f dB -> L %.4f nH (sum|S|^2 %.3f)"
              % (f0 / 1e9, depth, ltot * 1e9, power))
        out[tag] = (f0, depth, ltot, power)

    fails = []
    for tag, (f0, depth, _, power) in out.items():
        if depth > NOTCH_MIN_DB:
            fails.append("%s: the notch is only %.1f dB deep" % (tag, depth))
        # The guard that tells a measurement from a run that only looks
        # like one. Refer to POWER_MIN.
        if power < POWER_MIN:
            fails.append("%s: sum|S|^2 is %.3f at the notch, thus this run "
                         "does not measure an inductance" % (tag, power))
    l_board = out["short"][2]
    print("\n   the branch with a short: %.4f nH (notch %.4f GHz)"
          % (l_board * 1e9, out["short"][0] / 1e9))
    print("\n   L in/nH   notch/GHz   branch/nH   L measured/nH   error    "
          "excess/nH")
    excess = []
    for tag, kind, lval in cases:
        if kind != "L":
            continue
        f0, _, l_branch, _ = out[tag]
        got = l_branch - l_board
        excess.append(got - lval)
        print("   %7.2f     %7.4f    %8.4f   %13.4f   %+5.1f%%   %+8.4f"
              % (lval * 1e9, f0 / 1e9, l_branch * 1e9, got * 1e9,
                 100 * (got - lval) / lval, (got - lval) * 1e9))
        if f0 >= out["short"][0]:
            fails.append("%s: the inductor did not move the notch DOWN: %.4f "
                         "against %.4f GHz"
                         % (tag, f0 / 1e9, out["short"][0] / 1e9))
        if abs(got - lval) > max(L_PART_TOL * lval, ESL_FLOOR_H):
            fails.append("the pair gives %.4f nH for an inductor of %.4f nH"
                         % (got * 1e9, lval * 1e9))
    # Each value reads HIGH, and the excess follows the inductor and not
    # the geometry: +0.100 nH on 2 nH and +0.376 nH on 5 nH at the coarse
    # preset. Two probes excluded the two other explanations - the
    # timestep and a branch inductance that follows the frequency - thus
    # the excess belongs to the lumped inductor itself. Refer to the log
    # of 2026-08-04 (10). The test holds the SIGN, because a value that
    # reads LOW would be a different defect.
    print("\n   the excess is %+.4f nH and %+.4f nH: a lumped inductor of "
          "its own reads high" % (excess[0] * 1e9, excess[1] * 1e9))
    if min(excess) < -ESL_FLOOR_H:
        fails.append("an inductor reads LOW (%.4f nH): the excess of this "
                     "board is positive on every measurement so far"
                     % (min(excess) * 1e9))

    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the two-part validation FAILED")
    print("PASS: two parts in series give the inductor of the pair back")


def packages(mesh="coarse"):
    """Measure the ESL table of `board_reader`, one package at a time.

    Item 6b of the roadmap. Each entry of the table is a different
    package, thus each one has a different land pattern AND a different
    L_board. The tool runs each package two times, with no ESL and with
    the ESL of the table, and takes the difference.

    **What this gives, and what it does not.** It gives L_board for each
    land pattern, which any comparison against a real part needs first,
    and it holds the full path (the extraction, the mesh and the solver)
    against the table for 8 geometries, from a gap of 0.18 mm to one of
    4.70 mm. It does NOT say that the values of the table are the
    correct values for a real part: that needs the S-parameters of a
    manufacturer, and this file has no such data.
    """
    table = board_reader.package_presets()
    order = sorted(table, key=lambda p: run_lumped.shunt_land(p)[2])
    rows, fails = [], []
    for pkg in order:
        pad_l, pad_w, gap = run_lumped.shunt_land(pkg)
        esl = table[pkg]
        # One package that fails must not discard the 14 runs before it.
        # The usual cause is a notch that goes out of the sweep: a large
        # land has a large L_board, thus a lower notch.
        try:
            f_a, d_a, l_board = measure("pkg%s_0" % pkg, C_NOM, 0.0, mesh, pkg)
            f_b, d_b, l_tot = measure("pkg%s_esl" % pkg, C_NOM, esl, mesh, pkg)
        except AssertionError as exc:
            print("   ERROR: %s" % exc)
            fails.append("%s: %s" % (pkg, exc))
            continue
        got = l_tot - l_board
        rows.append((pkg, pad_l, pad_w, gap, l_board, esl, got))
        for tag, d in (("no ESL", d_a), ("with the ESL", d_b)):
            if d > NOTCH_MIN_DB:
                fails.append("%s (%s): the notch is only %.1f dB deep"
                             % (pkg, tag, d))
        if abs(got - esl) > max(ESL_TOL * esl, ESL_FLOOR_H):
            fails.append("%s: the table says %.2f nH and the run gives "
                         "%.4f nH" % (pkg, esl * 1e9, got * 1e9))

    print("\n  package   pad (mm)      gap    L_board/nH   table/nH   "
          "measured/nH   error")
    for pkg, pad_l, pad_w, gap, l_board, esl, got in rows:
        print("  %-7s %5.3f x %5.3f  %5.3f   %8.4f   %7.2f   %10.4f   %+.1f%%"
              % (pkg, pad_l, pad_w, gap, l_board * 1e9, esl * 1e9, got * 1e9,
                 100 * (got - esl) / esl))
    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the ESL table FAILED")
    print("PASS: the tool gives each entry of the ESL table back, on the "
          "land pattern of its own package")


def main(mesh="coarse", mode="method"):
    if mode == "packages":
        return packages(mesh)
    if mode == "two":
        return two(mesh)
    fails = []
    cases = [("ideal", C_NOM, 0.0), ("control", C_CONTROL, 0.0)]
    cases += [("esl%03d" % round(e * 1e12), C_NOM, e) for e in ESL_CASES]
    out = {}
    for tag, cval, esl in cases:
        f0, depth, ltot = measure(tag, cval, esl, mesh)
        out[tag] = (cval, esl, f0, depth, ltot)
        if depth > NOTCH_MIN_DB:
            fails.append("%s: the notch is only %.1f dB deep" % (tag, depth))

    l_board = out["ideal"][4]
    if not L_BOARD_RANGE[0] < l_board < L_BOARD_RANGE[1]:
        fails.append("the board inductance %.3f nH is outside %.2f..%.2f nH"
                     % (l_board * 1e9, L_BOARD_RANGE[0] * 1e9,
                        L_BOARD_RANGE[1] * 1e9))
    # The control: the same board with another capacitor must give the
    # same board inductance. If it does not, the number is not an
    # inductance of the board and nothing below it means anything.
    l_ctrl = out["control"][4]
    d_ctrl = abs(l_ctrl - l_board) / l_board
    print("\ncontrol: L_board %.4f nH at %.3g pF against %.4f nH at %.3g pF "
          "(%.1f%%)" % (l_board * 1e9, C_NOM * 1e12, l_ctrl * 1e9,
                        C_CONTROL * 1e12, 100 * d_ctrl))
    if d_ctrl > CONTROL_TOL:
        fails.append("L_board follows the value of the capacitor (%.1f%% > "
                     "%.0f%%): the notch does not measure an inductance"
                     % (100 * d_ctrl, 100 * CONTROL_TOL))

    print("\n   body ESL in   notch/GHz   L_total/nH   ESL measured/nH   error")
    for tag, cval, esl in cases:
        if not esl:
            continue
        _, _, f0, _, ltot = out[tag]
        got = ltot - l_board
        tol = max(ESL_TOL * esl, ESL_FLOOR_H)
        print("   %9.2f     %7.4f     %8.4f   %13.4f   %+.1f%%"
              % (esl * 1e9, f0 / 1e9, ltot * 1e9, got * 1e9,
                 100 * (got - esl) / esl))
        if abs(got - esl) > tol:
            fails.append("ESL %.2f nH measured as %.4f nH (tolerance %.3f nH)"
                         % (esl * 1e9, got * 1e9, tol * 1e9))

    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the shunt validation FAILED")
    print("PASS: the notch of a shunt part measures a body ESL")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
