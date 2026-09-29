"""A full test of a lumped element: a series resistor of 50 ohm in a
microstrip of 50 ohm.

For an ideal part with Z0 = 50, S21 = 2*Z0/(2*Z0+R) = -3.5 dB and S11 =
R/(R+2*Z0) = -9.5 dB. If the simulation ignores the resistor, the gap is an
open circuit: S11 near 0 dB and S21 much lower. The asserts below show the
difference between the two conditions.

Run this file with the python of KiCad 10. It uses pcbnew, and it starts
the solver itself:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\openems\\run_lumped_openems.py [coarse|medium|fine]
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\emerge\\run_lumped_emerge.py [coarse|medium|fine]
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
import solverenv  # noqa: E402
from make_lumped_board import make  # noqa: E402


def main(mesh="medium"):
    outdir = rigsolve.out("out_lumped_" + mesh)
    os.makedirs(outdir, exist_ok=True)
    board, pads = make(os.path.join(outdir, "series_r.kicad_pcb"))

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin,
                                 f_stop=6e9, mesh=mesh)
    for p in model["ports"]:
        p["type"] = "msl"
    les = model["lumped_elements"]
    print("lumped:", [(e["ref"], e["type"], e["value"], e["ny"]) for e in les])
    assert len(les) == 1 and les[0]["type"] == "R" \
        and les[0]["value"] == 50.0, les
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "z0": 50.0, "margin_mm": margin,
        "mesh": mesh, "n_freq": 201, "max_timesteps": 300000,
        "end_criteria": 1e-4, "lumped": True,
    }

    rigsolve.run(model, outdir, os.path.basename(outdir))

    import numpy as np
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    f = rows[:, 0]
    s11 = 20 * np.log10(np.abs(rows[:, 1] + 1j * rows[:, 2]) + 1e-12)
    s21 = 20 * np.log10(np.abs(rows[:, 3] + 1j * rows[:, 4]) + 1e-12)
    i = int(np.argmin(np.abs(f - 2e9)))  # the parasitics are small at a low f
    print("at %.2f GHz: S11=%.2f dB (ideal -9.5), S21=%.2f dB (ideal -3.5)"
          % (f[i] / 1e9, s11[i], s21[i]))
    assert -12.0 < s11[i] < -7.0, "S11 %.2f dB off ideal -9.5" % s11[i]
    assert -5.0 < s21[i] < -2.5, "S21 %.2f dB off ideal -3.5" % s21[i]
    print("PASS")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["medium"]))
