"""Test the three types of lumped element against closed-form theory.

One series R, L or C bridges a gap of 0.5 mm in a microstrip of 50 ohm.
For a series impedance Z between two lines of Z0:

    S21 = 2*Z0 / (2*Z0 + Z)        S11 = Z / (Z + 2*Z0)

Thus each type has a different and clear signature from 1 to 5 GHz:

    R = 50 ohm : |S21| is flat at -3.5 dB
    L = 10 nH  : |S21| DECREASES  -1.4 -> -10.4 dB  (Z = jwL increases with f)
    C = 1 pF   : |S21| INCREASES  -5.5 ->  -0.4 dB  (Z = 1/jwC decreases with f)

The opposite slopes are the important result. An element that the
simulation ignores cannot make them: it is an open circuit, thus |S21| is
much lower and its slope goes in the other direction. You also cannot
confuse L and C. The mesh has only a small effect on the slope, but it has
a large effect on the absolute magnitude. At the coarse preset the track
of 2.9 mm is only about 1 cell wide, and the parasitic series inductance
then increases |Z| more and more with the frequency. Thus the test asserts
the analytic magnitude only at the low end of the sweep. It prints the
other points for examination.

An inductor needs openEMS v0.37 or later. Refer to the README,
"Installation".

Run this file with the python of KiCad 10. It needs pcbnew, and it starts
the solver itself:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_rlc.py [coarse|medium|fine]
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
import run_lumped  # noqa: E402  (this file uses its board builder again)

Z0 = 50.0
# ref, value text, kind, SI value
CASES = [("R1", "50", "R", 50.0),
         ("L1", "10n", "L", 10e-9),
         ("C1", "1p", "C", 1e-12)]
REPORT_F = (1.5e9, 2e9, 3e9)    # the tool prints these frequencies
MAG_CHECK_F = 1.5e9             # only this one has an assert (refer to check())
SLOPE_F = (1e9, 5e9)            # the tool measures the slope of L and C here
MAG_TOL_DB = 2.5                # for the coarse mesh and the pad parasitics
SLOPE_MIN_DB = 3.0              # the necessary change of |S21| from 1 to 5 GHz


def ideal(kind, value, f):
    """Give the analytic S11 and S21 for a series R/L/C between two Z0 lines."""
    w = 2 * np.pi * f
    if kind == "R":
        Z = np.full(np.shape(f), value, dtype=complex)
    elif kind == "L":
        Z = 1j * w * value
    else:
        Z = 1.0 / (1j * w * value)
    return Z / (Z + 2 * Z0), 2 * Z0 / (2 * Z0 + Z)


def series_z_mag(kind, s21):
    """Give |Z| of an ideal series element from |S21| only.

    |S21| = 2*Z0/|2*Z0 + Z|. To invert this equation you must know the
    phase of Z, and the type of the element gives it. A resistor is real,
    thus |2*Z0 + R| = 2*Z0 + R. L and C are imaginary, thus
    |2*Z0 + jX| = sqrt(4*Z0^2 + X^2).

    Magnitudes only make this calculation immune to the 50-ohm line
    between the de-embedding plane of the port and the part. A matched
    line with no loss changes the phase only, and it cannot change
    |S21|. Do NOT use the extraction from the phase,
    Z = 2*Z0*(1-S21)/S21. The reference planes of the MSL ports are about
    10 mm from the part, which is some tenths of a wavelength. That
    equation then gives incorrect values, for example a negative
    resistance.
    """
    m = np.abs(s21)
    if kind == "R":
        return 2 * Z0 * (1.0 / m - 1.0)
    return 2 * Z0 * np.sqrt(np.maximum(1.0 / m ** 2 - 1.0, 0.0))


def implied(kind, zmag, f):
    """Give the value of the component from |Z|.

    The calculation is correct only if the element is ideal.
    """
    w = 2 * np.pi * f
    if kind == "R":
        return zmag
    if kind == "L":
        return zmag / w
    return 1.0 / (w * np.maximum(zmag, 1e-9))


def db(x):
    return 20 * np.log10(np.abs(x) + 1e-12)


def simulate(ref, val, mesh):
    """Make the board, extract it, and solve it.

    The function gives (freq, S11, S21) as complex arrays.
    """
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
        "excite": [1],   # port 1 only: this test needs S11 and S21 only
    }
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
        raise SystemExit("solver failed for %s" % ref)
    # "timesteps" keeps the line that says HOW the run ended (B29). The
    # log gave the numbers of a run and no way to see whether it met its
    # end criteria or stopped at the limit, and a run that stops at the
    # limit measures a notch that is not yet at its full depth.
    for line in log.stdout.splitlines():
        if ("lumped" in line or "ERROR" in line or "WARNING" in line
                or "timesteps" in line):
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
        # The test holds only the low-frequency point to the analytic
        # magnitude. At a higher frequency, the parasitic series L of the
        # coarse mesh is larger than the element and increases |Z|. The
        # track is only about 1 cell wide at lambda/10.
        if ft == MAG_CHECK_F and abs(got - want) > MAG_TOL_DB:
            fails.append("|S21| at %.2f GHz: %.2f dB vs ideal %.2f (>%.1f dB off)"
                         % (f[i] / 1e9, got, want, MAG_TOL_DB))

    # The most important test: in which direction does |S21| slope?
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

    # a test of the value, at the low frequency for the same reason
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
