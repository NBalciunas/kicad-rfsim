"""A full test of a lumped element: a series resistor of 50 ohm in a
microstrip of 50 ohm.

For an ideal part with Z0 = 50, S21 = 2*Z0/(2*Z0+R) = -3.5 dB and
S11 = R/(R+2*Z0) = -9.5 dB. If the simulation ignored the resistor, the
gap would be an open circuit: S11 near 0 dB and S21 much lower. The
asserts below tell the two conditions apart.

Run this file with the python of KiCad 10. It needs pcbnew, and it starts
the solver itself:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_lumped.py [coarse|medium|fine]
"""
import json
import os
import subprocess
import sys

import pcbnew
from pcbnew import FromMM, VECTOR2I

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)

import board_reader  # noqa: E402
import solverenv  # noqa: E402

TRACE_W = 2.9   # about 50 ohm on FR4 of 1.6 mm
Y = 10.0
BOARD = (0.0, 0.0, 40.0, 20.0)


def _pad(fp, num, x, y, w, h, net):
    pad = pcbnew.PAD(fp)
    pad.SetNumber(num)
    pad.SetShape(pcbnew.PAD_SHAPE_RECT)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetLayerSet(pad.SMDMask())
    pad.SetSize(VECTOR2I(FromMM(w), FromMM(h)))
    pad.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
    pad.SetNetCode(net.GetNetCode())
    fp.Add(pad)
    return pad


def _fp(board, ref, val=""):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference(ref)
    fp.SetValue(val)
    fp.SetPosition(VECTOR2I(0, 0))
    board.Add(fp)
    return fp


def _track(board, x0, x1, net):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(x0), FromMM(Y)))
    t.SetEnd(VECTOR2I(FromMM(x1), FromMM(Y)))
    t.SetWidth(FromMM(TRACE_W))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(net.GetNetCode())
    board.Add(t)


def make(path, ref="R1", val="50"):
    """Make a microstrip of 50 ohm that has a gap of 0.5 mm.

    One R/L/C part bridges the gap.
    """
    board = pcbnew.NewBoard(path)
    rf1 = pcbnew.NETINFO_ITEM(board, "RF1")
    rf2 = pcbnew.NETINFO_ITEM(board, "RF2")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    for n in (rf1, rf2, gnd):
        board.Add(n)

    pad1 = _pad(_fp(board, "P1"), "1", 5.0, Y, TRACE_W, TRACE_W, rf1)
    pad2 = _pad(_fp(board, "P2"), "1", 35.0, Y, TRACE_W, TRACE_W, rf2)
    # The series part has rectangular pads at [19, 20] and [20.5, 21.5].
    # They make a clean copper gap of 0.5 mm in [20, 20.5]. The lumped
    # element must bridge this gap.
    r = _fp(board, ref, val)
    _pad(r, "1", 19.5, Y, 1.0, TRACE_W, rf1)
    _pad(r, "2", 21.0, Y, 1.0, TRACE_W, rf2)

    # The tracks stop before the pads. Thus their round ends do not go
    # into the gap.
    _track(board, 5.0, 18.0, rf1)
    _track(board, 22.0, 35.0, rf2)

    x0, y0, x1, y1 = BOARD
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for i in range(4):
        e = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(VECTOR2I(FromMM(corners[i][0]), FromMM(corners[i][1])))
        e.SetEnd(VECTOR2I(FromMM(corners[(i + 1) % 4][0]),
                          FromMM(corners[(i + 1) % 4][1])))
        e.SetLayer(pcbnew.Edge_Cuts)
        e.SetWidth(FromMM(0.1))
        board.Add(e)

    z = pcbnew.ZONE(board)
    z.SetLayer(pcbnew.B_Cu)
    z.SetNetCode(gnd.GetNetCode())
    z.Outline().NewOutline()
    for cx, cy in corners:
        z.Outline().Append(FromMM(cx), FromMM(cy))
    board.Add(z)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    pcbnew.SaveBoard(path, board)
    return board, [pad1, pad2]


def main(mesh="medium"):
    outdir = os.path.join(HERE, "out_lumped_" + mesh)
    os.makedirs(outdir, exist_ok=True)
    board, pads = make(os.path.join(outdir, "series_r.kicad_pcb"))

    margin = 4.0
    model = board_reader.extract(board, pads, margin_mm=margin)
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
    model_path = os.path.join(outdir, "model.json")
    with open(model_path, "w") as fh:
        json.dump(model, fh, indent=1)

    runner = os.path.join(PLUGINS, "runner.py")
    solver_py = solverenv.solver_python() or sys.executable
    print("solver python:", solver_py)
    subprocess.check_call([solver_py, runner, model_path, outdir])

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
