"""A full test without the GUI of KiCad: board -> model.json -> openEMS ->
.s2p.

Run this file with the python of KiCad 10. This is the path of a per-user
installation. A machine-wide installation is in
"C:\\Program Files\\KiCad\\10.0\\bin".
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\openems\\run_headless_openems.py [coarse|medium|fine] [msl|lumped] [band]
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\emerge\\run_headless_emerge.py [coarse|medium|fine] [msl|lumped] [band]

A microstrip through line of about 50 ohm must give a good match and a low
insertion loss. The limits in the asserts are wide, because FDTD and FR4
have large tolerances.

**The band** is a key of `BANDS`, and "ghz" (1 to 6 GHz) is the default.
The three bands below it test the MHz band of a DC-DC converter. Use the
lumped port for them. In these bands the mesh step stops at
`solverenv.MAX_RES_MM`. Without the cap, `extract` stopped with an
OverflowError or with "no copper on the reference layer". With a cap of
10 mm, openEMS read S11 -3.2 dB at 1 GHz in "vhf" and -17.7 dB at 100 MHz
in "hf". In "mhz", the pulse of openEMS is longer than the step limit.
Thus the runner must stop with an error, and the rig tests that stop.
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

import board_reader  # noqa: E402
import make_test_board  # noqa: E402
import solverenv  # noqa: E402


# Each band: (f_start, f_stop, the largest S11 in dB, the smallest S21 in
# dB). The limits of the three low bands come from the two solvers at
# coarse with the lumped port, with some dB of margin.
BANDS = {
    "ghz": (1e9, 6e9, -7.0, -3.0),
    "vhf": (100e6, 1e9, -10.0, -0.5),
    "hf": (10e6, 100e6, -25.0, -0.1),
    "mhz": (1e6, 10e6, -40.0, -0.05),
}
# A part of the ERROR of `openems_runner.main` for a pulse that is longer
# than the step limit.
PULSE_STOP = "needs an excitation pulse"


def main(mesh="medium", port_type="msl", band="ghz"):
    fa, fb, s11_max, s21_min = BANDS[band]
    outdir = rigsolve.out("out_" + mesh + ("" if band == "ghz"
                                           else "_" + band))
    os.makedirs(outdir, exist_ok=True)
    board, pads = make_test_board.make(
        os.path.join(outdir, "microstrip_50ohm.kicad_pcb"))

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin,
                                 f_stop=fb, mesh=mesh)
    for p in model["ports"]:
        p["type"] = port_type
    model["settings"] = {
        "f_start": fa, "f_stop": fb, "z0": 50.0, "margin_mm": margin,
        "mesh": mesh, "n_freq": 401, "max_timesteps": 300000,
        "end_criteria": 1e-4,
    }
    print("model: %d F.Cu polys, %d B.Cu polys, %d ports, PML %.1f mm" % (
        len(model["polygons"].get("F.Cu", [])),
        len(model["polygons"].get("B.Cu", [])), len(model["ports"]),
        model["pml_mm"]))

    stop = band == "mhz" and rigsolve.SOLVER == "openems"
    log = rigsolve.run(model, outdir, os.path.basename(outdir),
                       expect=PULSE_STOP if stop else None)
    if stop:
        assert PULSE_STOP in log, "openEMS ran a pulse that it must refuse"
        print("the runner refused the pulse, as it must")
        print("PASS")
        return

    import numpy as np
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    s11 = 20 * np.log10(np.abs(rows[:, 1] + 1j * rows[:, 2]) + 1e-12)
    s21 = 20 * np.log10(np.abs(rows[:, 3] + 1j * rows[:, 4]) + 1e-12)
    print("S11 max: %.1f dB   S21 min: %.2f dB" % (s11.max(), s21.min()))
    assert s11.max() < s11_max, "poor match: S11 %.1f dB" % s11.max()
    assert s21.min() > s21_min, "excess loss: S21 %.2f dB" % s21.min()
    print("PASS")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["medium"]))
