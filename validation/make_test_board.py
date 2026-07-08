"""Build the validation board: a 30 mm ~50-ohm microstrip on 2-layer FR4.

Run with KiCad's python:
    "C:\\Program Files\\KiCad\\8.0\\bin\\python.exe" make_test_board.py [out.kicad_pcb]
"""
import os
import sys

import pcbnew
from pcbnew import FromMM, VECTOR2I

TRACE_W = 2.9   # ~50 ohm on 1.6 mm FR4 (er 4.5)
TRACE_Y = 10.0
X0, X1 = 5.0, 35.0
BOARD = (0.0, 0.0, 40.0, 20.0)


def _pad_fp(board, ref, x, y, net):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference(ref)
    fp.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
    pad = pcbnew.PAD(fp)
    pad.SetNumber("1")
    pad.SetShape(pcbnew.PAD_SHAPE_RECT)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetLayerSet(pad.SMDMask())
    pad.SetSize(VECTOR2I(FromMM(TRACE_W), FromMM(TRACE_W)))
    pad.SetPosition(fp.GetPosition())
    pad.SetNetCode(net.GetNetCode())
    fp.Add(pad)
    board.Add(fp)
    return pad


def make(path):
    board = pcbnew.NewBoard(path)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    board.Add(rf)
    board.Add(gnd)

    pad1 = _pad_fp(board, "P1", X0, TRACE_Y, rf)
    pad2 = _pad_fp(board, "P2", X1, TRACE_Y, rf)

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(X0), FromMM(TRACE_Y)))
    t.SetEnd(VECTOR2I(FromMM(X1), FromMM(TRACE_Y)))
    t.SetWidth(FromMM(TRACE_W))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(rf.GetNetCode())
    board.Add(t)

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


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(__file__), "microstrip_50ohm.kicad_pcb")
    make(out)
    print("wrote", out)
