"""A full test without the GUI of KiCad: board -> model.json -> openEMS -> .s2p.

Run this file with the python of KiCad 10. This is the path of a per-user
installation. A machine-wide installation is in
"C:\\Program Files\\KiCad\\10.0\\bin".
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_headless.py [coarse|medium|fine]

A microstrip through line of about 50 ohm must give a good match and a low
insertion loss. The limits in the asserts are wide, because FDTD and FR4
both have large tolerances.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import board_reader  # noqa: E402
import make_test_board  # noqa: E402
import solverenv  # noqa: E402


def main(mesh="medium", port_type="msl"):
    outdir = os.path.join(HERE, "out_" + mesh)
    os.makedirs(outdir, exist_ok=True)
    board, pads = make_test_board.make(
        os.path.join(outdir, "microstrip_50ohm.kicad_pcb"))

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin)
    for p in model["ports"]:
        p["type"] = port_type
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "z0": 50.0, "margin_mm": margin,
        "mesh": mesh, "n_freq": 401, "max_timesteps": 300000,
        "end_criteria": 1e-4,
    }
    model_path = os.path.join(outdir, "model.json")
    with open(model_path, "w") as fh:
        json.dump(model, fh, indent=1)
    print("model: %d F.Cu polys, %d B.Cu polys, %d ports" % (
        len(model["polygons"].get("F.Cu", [])),
        len(model["polygons"].get("B.Cu", [])), len(model["ports"])))

    runner = os.path.join(os.path.dirname(HERE), "runner.py")
    solver_py = solverenv.solver_python() or sys.executable
    print("solver python:", solver_py)
    subprocess.check_call([solver_py, runner, model_path, outdir])

    import numpy as np
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    s11 = 20 * np.log10(np.abs(rows[:, 1] + 1j * rows[:, 2]) + 1e-12)
    s21 = 20 * np.log10(np.abs(rows[:, 3] + 1j * rows[:, 4]) + 1e-12)
    print("S11 max: %.1f dB   S21 min: %.1f dB" % (s11.max(), s21.min()))
    assert s11.max() < -7.0, "poor match: S11 %.1f dB" % s11.max()
    assert s21.min() > -3.0, "excess loss: S21 %.1f dB" % s21.min()
    print("PASS")


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["medium"]))
