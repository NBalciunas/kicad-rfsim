"""Validate all three lumped element types against closed-form theory.

A single series R, L or C bridges a 0.5 mm gap in a 50-ohm microstrip.
For a series impedance Z between two Z0 lines:

    S21 = 2*Z0 / (2*Z0 + Z)        S11 = Z / (Z + 2*Z0)

so each type has a distinct, unmistakable signature over 1..5 GHz:

    R = 50 ohm : |S21| flat at -3.5 dB
    L = 10 nH  : |S21| FALLS   -1.4 -> -10.4 dB   (Z = jwL grows with f)
    C = 1 pF   : |S21| RISES   -5.5 ->  -0.4 dB   (Z = 1/jwC shrinks with f)

The opposite slopes are the point: they cannot be faked by a dropped
element (which reads as an open, |S21| far down and falling the other way)
nor by mixing up L and C. The slope is also mesh-robust, which the absolute
magnitude is not: at the coarse preset the 2.9 mm trace is only ~1 cell
wide, and the resulting parasitic series inductance inflates |Z| more and
more with frequency. So the analytic magnitude is asserted only at the low
end of the sweep, and the higher points are printed for inspection.

Inductors need openEMS >= v0.37; see README "Solver interpreter".

Run with KiCad 10's python (needs pcbnew; spawns the solver itself):
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_rlc.py [coarse|medium|fine]
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import solverenv  # noqa: E402
import run_lumped  # noqa: E402  (reuses its board builder)

Z0 = 50.0
# ref, value text, kind, SI value
CASES = [("R1", "50", "R", 50.0),
         ("L1", "10n", "L", 10e-9),
         ("C1", "1p", "C", 1e-12)]
REPORT_F = (1.5e9, 2e9, 3e9)    # printed for inspection
MAG_CHECK_F = 1.5e9             # only this one is asserted (see check())
SLOPE_F = (1e9, 5e9)            # where the L-vs-C slope is measured
MAG_TOL_DB = 2.5                # coarse mesh + pad parasitics
SLOPE_MIN_DB = 3.0              # required |S21| change across 1..5 GHz


def ideal(kind, value, f):
    """Analytic S11, S21 for a series R/L/C between two Z0 lines."""
    w = 2 * np.pi * f
    if kind == "R":
        Z = np.full(np.shape(f), value, dtype=complex)
    elif kind == "L":
        Z = 1j * w * value
    else:
        Z = 1.0 / (1j * w * value)
    return Z / (Z + 2 * Z0), 2 * Z0 / (2 * Z0 + Z)


def series_z_mag(kind, s21):
    """|Z| of an ideal series element, from |S21| alone.

    |S21| = 2*Z0/|2*Z0 + Z|, and inverting it needs Z's phase — which the
    element type supplies: a resistor is real, so |2*Z0 + R| = 2*Z0 + R,
    while L and C are imaginary, so |2*Z0 + jX| = sqrt(4*Z0^2 + X^2).

    Using magnitudes only makes this immune to the 50-ohm line between the
    port's de-embedding plane and the part: a matched lossless line is a
    pure phase shift and cannot change |S21|. The phase-based extraction
    Z = 2*Z0*(1-S21)/S21 is NOT usable here — the MSL reference planes sit
    ~10 mm from the part, several tenths of a wavelength, and it returns
    nonsense like a negative resistance.
    """
    m = np.abs(s21)
    if kind == "R":
        return 2 * Z0 * (1.0 / m - 1.0)
    return 2 * Z0 * np.sqrt(np.maximum(1.0 / m ** 2 - 1.0, 0.0))


def implied(kind, zmag, f):
    """Component value implied by |Z| (treats the element as ideal)."""
    w = 2 * np.pi * f
    if kind == "R":
        return zmag
    if kind == "L":
        return zmag / w
    return 1.0 / (w * np.maximum(zmag, 1e-9))


def db(x):
    return 20 * np.log10(np.abs(x) + 1e-12)


def simulate(ref, val, mesh):
    """Build, extract, solve. Returns (freq, S11, S21) as complex arrays."""
    outdir = os.path.join(HERE, "out_rlc_%s_%s" % (ref, mesh))
    os.makedirs(outdir, exist_ok=True)
    board, pads = run_lumped.make(
        os.path.join(outdir, "series_%s.kicad_pcb" % ref), ref, val)

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin)
    for p in model["ports"]:
        p["type"] = "msl"
    les = model["lumped_elements"]
    assert len(les) == 1 and les[0]["ref"] == ref, les
    print("  extracted: %s %s=%g (%s-axis)"
          % (les[0]["ref"], les[0]["type"], les[0]["value"], les[0]["ny"]))
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "z0": Z0, "margin_mm": margin,
        "mesh": mesh, "n_freq": 201, "max_timesteps": 300000,
        "end_criteria": 1e-4, "lumped": True,
        "excite": [1],   # only port 1: S11 + S21 is all this check needs
    }
    model_path = os.path.join(outdir, "model.json")
    with open(model_path, "w") as fh:
        json.dump(model, fh, indent=1)

    runner = os.path.join(os.path.dirname(HERE), "runner.py")
    solver_py = solverenv.solver_python() or sys.executable
    log = subprocess.run([solver_py, runner, model_path, outdir],
                         capture_output=True, text=True)
    if log.returncode != 0:
        print(log.stdout[-3000:])
        print(log.stderr[-2000:])
        raise SystemExit("solver failed for %s" % ref)
    for line in log.stdout.splitlines():
        if "lumped" in line or "ERROR" in line or "WARNING" in line:
            print("  " + line.strip())

    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    return (rows[:, 0], rows[:, 1] + 1j * rows[:, 2],
            rows[:, 3] + 1j * rows[:, 4])


def check(ref, val, kind, nominal, mesh):
    print("\n=== %s (%s%s) ===" % (ref, val, {"R": " ohm", "L": "H", "C": "F"}[kind]))
    f, s11, s21 = simulate(ref, val, mesh)
    i11, i21 = ideal(kind, nominal, f)
    zmag = series_z_mag(kind, s21)
    unit = {"R": "ohm", "L": "nH", "C": "pF"}[kind]
    scale = {"R": 1.0, "L": 1e9, "C": 1e12}[kind]

    print("   f/GHz  |S21| sim  ideal   |S11| sim  ideal   |Z|/ohm  implied %s"
          % unit)
    fails = []
    for ft in REPORT_F:
        i = int(np.argmin(np.abs(f - ft)))
        got, want = db(s21[i]), db(i21[i])
        print("   %5.2f   %8.2f  %6.2f   %8.2f  %6.2f   %7.1f  %8.2f"
              % (f[i] / 1e9, got, want, db(s11[i]), db(i11[i]),
                 zmag[i], implied(kind, zmag[i], f[i]) * scale))
        # Only the low-frequency point is held to the analytic magnitude:
        # higher up, the coarse mesh's parasitic series L (the trace is only
        # ~1 cell wide at lambda/10) dominates and inflates |Z|.
        if ft == MAG_CHECK_F and abs(got - want) > MAG_TOL_DB:
            fails.append("|S21| at %.2f GHz: %.2f dB vs ideal %.2f (>%.1f dB off)"
                         % (f[i] / 1e9, got, want, MAG_TOL_DB))

    # the decisive test: which way does |S21| slope across the band?
    lo = int(np.argmin(np.abs(f - SLOPE_F[0])))
    hi = int(np.argmin(np.abs(f - SLOPE_F[1])))
    slope = db(s21[hi]) - db(s21[lo])
    print("   |S21| %.1f->%.1f GHz: %+.2f dB" % (f[lo] / 1e9, f[hi] / 1e9, slope))
    if kind == "L" and slope > -SLOPE_MIN_DB:
        fails.append("inductor must BLOCK more at high f: slope %+.2f dB" % slope)
    if kind == "C" and slope < SLOPE_MIN_DB:
        fails.append("capacitor must PASS more at high f: slope %+.2f dB" % slope)
    if kind == "R" and abs(slope) > MAG_TOL_DB:
        fails.append("resistor should be flat: slope %+.2f dB" % slope)

    # value sanity, at the low frequency for the same reason
    i = int(np.argmin(np.abs(f - MAG_CHECK_F)))
    v = implied(kind, zmag[i], f[i])
    if not (0.5 * nominal < v < 2.0 * nominal):
        fails.append("implied %s %.4g at %.2f GHz outside 0.5x..2x of nominal "
                     "%.4g" % (kind, v, f[i] / 1e9, nominal))

    for m in fails:
        print("   FAIL: %s" % m)
    print("   %s" % ("PASS" if not fails else "FAIL"))
    return fails


def main(mesh="coarse", only=None):
    all_fails = {}
    for ref, val, kind, nominal in CASES:
        if only and ref != only:
            continue
        all_fails[ref] = check(ref, val, kind, nominal, mesh)
    print("\n" + "=" * 62)
    for ref, fails in all_fails.items():
        print("%-4s %s" % (ref, "PASS" if not fails else "FAIL (%d)" % len(fails)))
    if any(all_fails.values()):
        raise SystemExit("R/L/C validation FAILED")
    print("VALIDATED AGAINST THEORY: %s" % ", ".join(all_fails))


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
