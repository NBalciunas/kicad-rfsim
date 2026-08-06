"""Push a ground zone that has a HOLE through the SOLVER.

Backlog item 5, and the gap that "What is not tested" held the longest:
the extraction from a zone with holes is tested (the demo board with 6
layers and 444 vias), and no such polygon ever reached openEMS.
`Fracture` changes a hole into a slit with no width, and the value of
that trick was an ASSUMPTION:

- a slit that CLOSES joins the copper across the hole. The plane is then
  solid, the hole does nothing, and every number looks correct;
- a slit that OPENS too far cuts the plane in two, and the return
  current goes around the cut.

**A hole at the SIDE of the line cannot tell the two apart.** It moves
the impedance by less than the scatter of the mesh, thus a slit that
closed and a slit that worked give the same number. This board puts a
void of 8 x 6 mm DIRECTLY under the line, where the return current
flows: a working slit must then raise the impedance by a large step,
and a closed slit gives no step at all.

The measurement is the DIFFERENCE of two runs of the same board, in the
same way as `run_shunt.py`: `void=True` and `void=False`. Everything
else is identical, the three vias of the other net included.

Run it with the python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_zone_holes.py [mesh]
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import make_test_board  # noqa: E402
import solverenv  # noqa: E402

# **The observable is the REFLECTION, and not Z0.** A de-embedded port
# measures the line where the PORT is, and the void is in the middle of
# the line: the probes of the port never pass over it, thus Z0 moves by
# under 1 ohm whether the void works or not. The void is a discontinuity
# in the middle of a matched line, and a discontinuity reflects.
#
# Measured on 2026-08-05 at the coarse preset: the solid pour gives S11
# −11.5 dB and the void gives −3.3 dB. −3.3 dB is 47% of the power back.
# A slit that CLOSED would leave the plane solid, thus S11 would not
# move at all. 5 dB is far above the scatter of this board and far under
# the step that the run gives.
DS11_MIN = 5.0
# The plane must still carry the return current around the void. A slit
# that opened too far would take the copper away and break the line: the
# power would then go nowhere, and not into the reflection.
S21_MIN = -4.0
# The copper that the void removes, against the 8 x 6 mm of the outline.
# This guards the size of the slit in the EXTRACTION: too little and the
# filler kept no void, too much and the slit takes copper with it.
AREA_MIN, AREA_MAX = 40.0, 56.0


def run(mesh, void):
    tag = "void" if void else "solid"
    outdir = os.path.join(HERE, "out_zone_%s_%s" % (tag, mesh))
    os.makedirs(outdir, exist_ok=True)
    board, pads = make_test_board.make_zone_holes(
        os.path.join(outdir, "zone.kicad_pcb"), void=void)
    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin)
    for p in model["ports"]:
        p["type"] = "msl"
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "z0": 50.0, "margin_mm": margin,
        "mesh": mesh, "n_freq": 401, "max_timesteps": 300000,
        "end_criteria": 1e-4, "excite": [1],
    }
    path = os.path.join(outdir, "model.json")
    with open(path, "w") as fh:
        json.dump(model, fh, indent=1)

    polys = model["polygons"].get("B.Cu", [])
    area = sum(abs(sum(q[i][0] * q[(i + 1) % len(q)][1]
                       - q[(i + 1) % len(q)][0] * q[i][1]
                       for i in range(len(q)))) / 2.0 for q in polys)
    print("   %-5s B.Cu: %d polygon(s), %d points, %.1f mm2 of copper"
          % (tag, len(polys), sum(len(q) for q in polys), area))

    py = solverenv.solver_python() or sys.executable
    out = subprocess.run([py, os.path.join(PLUGINS, "runner.py"),
                          path, outdir], capture_output=True)
    txt = (out.stdout + out.stderr).decode("utf-8", "replace")
    if out.returncode:
        print(txt[-3000:])
        raise SystemExit("the %s run failed" % tag)
    unused = [ln.strip() for ln in txt.splitlines() if "Unused primitive" in ln]
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    s11 = 20 * np.log10(np.abs(rows[:, 1] + 1j * rows[:, 2]) + 1e-12)
    s21 = 20 * np.log10(np.abs(rows[:, 3] + 1j * rows[:, 4]) + 1e-12)
    with open(os.path.join(outdir, "lines.json")) as fh:
        lines = json.load(fh)
    freq = np.asarray(lines["freq_hz"], float)
    band = (freq >= 2e9) & (freq <= 5e9)
    z = float(np.median(np.asarray(lines["ports"]["1"]["Z0_real"],
                                   float)[band]))
    print("   %-5s Z0 %.1f ohm   S11 max %.1f dB   S21 min %.1f dB"
          % (tag, z, s11.max(), s21.min()))
    return {"z": z, "s11": s11.max(), "s21": s21.min(), "unused": unused,
            "area": area, "polys": len(polys)}


def main(mesh="coarse"):
    print("a ground zone with a void, against the same board with none\n")
    solid = run(mesh, False)
    void = run(mesh, True)

    fails = []
    # 1. The void must be IN the extracted model, and it must be the
    #    RIGHT size. The filler of KiCad decides this, and a board with
    #    no void would make the whole test meaningless.
    lost = solid["area"] - void["area"]
    print("\n   the void removes %.1f mm2 of copper (8 x 6 mm = 48)" % lost)
    if not AREA_MIN <= lost <= AREA_MAX:
        fails.append("the pour lost %.1f mm2, outside %g to %g. The filler "
                     "kept no void, or the slit took copper with it."
                     % (lost, AREA_MIN, AREA_MAX))
    # 2. No primitive may go unused. That is the signal of the via defect
    #    of 2026-08-03 (10): a polygon that does not become metal.
    for r, tag in ((solid, "solid"), (void, "void")):
        if r["unused"]:
            fails.append("%s: openEMS did not use %d primitive(s): %s"
                         % (tag, len(r["unused"]), r["unused"][0]))
    # 3. The DIFFERENCE, in the reflection. A slit that closed leaves the
    #    plane solid, thus S11 does not move.
    ds11 = void["s11"] - solid["s11"]
    print("   S11 goes from %.1f dB to %.1f dB: a step of %+.1f dB"
          % (solid["s11"], void["s11"], ds11))
    print("   Z0 at the PORT: %.1f ohm against %.1f ohm. It barely moves, "
          "and that is\n   correct: the probes of the port are at the end "
          "of the line and the void\n   is in the middle. The reflection is "
          "the observable here." % (void["z"], solid["z"]))
    if ds11 < DS11_MIN:
        fails.append(
            "the void moved S11 by %+.1f dB, and %g dB is the least that a "
            "true void gives. The slit of the hole CLOSED: the copper "
            "joined across it and the plane is solid in the model."
            % (ds11, DS11_MIN))
    # 4. The plane must still carry the return current AROUND the void.
    if void["s21"] < S21_MIN:
        fails.append("the board with the void gives S21 %.1f dB: the power "
                     "goes nowhere, thus the slit took the plane away "
                     "instead of making a hole in it" % void["s21"])

    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the zone-void validation FAILED")
    print("PASS: a zone with a void runs through the solver. The slit of the\n"
          "hole OPENS (the void moves S11 by %+.1f dB) and it does not take "
          "the\nplane away (S21 stays at %.1f dB, thus the return current "
          "goes around)." % (ds11, void["s21"]))


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
