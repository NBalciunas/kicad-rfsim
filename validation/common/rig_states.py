"""Push the states of a part (F30) through the SOLVER: the phase that a
series capacitor gives a line, for three values of it.

A phase shifter is a set of states of its parts: a varactor at more than
one bias, or PIN diodes that are on and off. The plugin runs each state as
a run of its own (`solverenv.apply_state`), in a folder of its own, and the
results window shows the phase of each state LESS the phase of State 1.
The line, the pads and the ports are the same in each state, thus their
phase goes out of that difference.

The board is the series board of `rig_rlc`: one capacitor in a gap of
0.5 mm in a microstrip of 50 ohm, with no parasitics. For a series Z
between two lines of Z0:

    S21 = 2 Z0 / (2 Z0 + Z),   Z = 1 / (j w C)

Thus the phase difference of a state with C_k against State 1 with C_1 is
arg(S21(C_k) / S21(C_1)), with no line in it. At 1 GHz, 0.5 pF gives
+14.70 degrees and 2 pF -19.35 degrees against 1 pF.

The rig writes `solverenv.STATES_FILE` as the plugin does, thus
`gui.ResultsFrame` opens the runs as states.

Run it with the python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\openems\\run_states_openems.py [mesh]
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\emerge\\run_states_emerge.py [mesh]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigsolve  # noqa: E402

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import make_lumped_board  # noqa: E402
import solverenv  # noqa: E402

Z0 = 50.0
# (the name, C): State 1 is the value of the board, and the reference.
STATES = [("1 pF", 1e-12), ("0.5 pF", 0.5e-12), ("2 pF", 2e-12)]
# The sweep of `rig_rlc`, which that rig validates on this board. A sweep
# to 4 GHz at coarse gave sum|S|^2 = 8.3 on this board with no state at
# all: the microstrip ports of 9 mm (0.3 of the distance between them)
# then have the plane of the measurement 1 mm after the feed (B77).
CHECK_F = (1e9, 2e9, 3e9)
# The limit against the closed form, in degrees. Measured on 2026-10-06 at
# coarse, the largest errors are 2.32 degrees in openEMS and 2.47 in EMerge
# (0.5 pF, at 1 and 2 GHz). The magnitude difference is in 0.75 dB of the
# theory in the two. A state that the run did not apply gives 13.1 to 19.4
# degrees of error, thus the limit finds it.
PHASE_TOL = 3.0


def ideal_s21(c, f):
    return 2 * Z0 / (2 * Z0 + 1.0 / (2j * np.pi * f * c))


def main(mesh="coarse"):
    print("the states of a series capacitor: the phase difference against "
          "the closed form\n")
    root = rigsolve.out("out_states_%s" % mesh)
    os.makedirs(root, exist_ok=True)
    board, pads = make_lumped_board.make(
        os.path.join(root, "series_C1.kicad_pcb"), "C1", "1p")
    model = board_reader.extract(board, pads, margin_mm=4.0, f_stop=6e9,
                                 mesh=mesh)
    for p in model["ports"]:
        p["type"] = "msl"
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "f_field": 2e9, "z0": Z0,
        "margin_mm": 4.0, "mesh": mesh, "n_freq": 201,
        "max_timesteps": 300000, "end_criteria": 1e-4, "lumped": True,
        "parasitics": False, "excite": [1]}
    listed, s21 = [], []
    for k, (name, c) in enumerate(STATES, 1):
        st = {"name": name,
              "parts": {} if k == 1 else {"C1": {"value": c}}}
        folder = solverenv.STATE_DIR % k
        sdir = os.path.join(root, folder)
        rigsolve.run(solverenv.apply_state(model, st), sdir, name)
        listed.append({"name": name, "dir": folder, "parts": st["parts"]})
        rows = np.loadtxt(os.path.join(sdir, "results.s2p"),
                          comments=("!", "#"))
        f = rows[:, 0]
        s21.append(rows[:, 3] + 1j * rows[:, 4])
    with open(os.path.join(root, solverenv.STATES_FILE), "w") as fh:
        json.dump({"states": listed}, fh, indent=1)

    fails = []
    ref_i = ideal_s21(STATES[0][1], f)
    for k, (name, c) in enumerate(STATES[1:], 1):
        sim = np.degrees(np.unwrap(np.angle(s21[k]))
                         - np.unwrap(np.angle(s21[0])))
        want = np.degrees(np.angle(ideal_s21(c, f) / ref_i))
        dmag = 20 * np.log10(np.abs(s21[k]) / np.abs(s21[0]))
        wmag = 20 * np.log10(np.abs(ideal_s21(c, f) / ref_i))
        for fc in CHECK_F:
            i = int(np.argmin(np.abs(f - fc)))
            err = sim[i] - want[i]
            print("   %-6s %.1f GHz: %+7.2f deg (theory %+7.2f, %+5.2f), "
                  "%+6.2f dB (theory %+6.2f)"
                  % (name, f[i] / 1e9, sim[i], want[i], err, dmag[i],
                     wmag[i]))
            if abs(err) > PHASE_TOL:
                fails.append("%s at %.1f GHz: the phase difference is %+.2f "
                             "deg, and the theory gives %+.2f (the limit is "
                             "%g deg)" % (name, f[i] / 1e9, sim[i], want[i],
                                          PHASE_TOL))
    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the states validation FAILED")
    print("PASS: each state is a run of its own, and the phase difference "
          "of the states\nagrees with the closed form to %g degrees"
          % PHASE_TOL)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
