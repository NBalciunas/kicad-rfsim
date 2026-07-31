"""Make the validation boards: a microstrip and a grounded CPW.

Each board has 2 layers of FR4 and a line of 30 mm at about 50 ohm. Run
this file with the python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" make_test_board.py [out.kicad_pcb]
"""
import os
import sys

import pcbnew
from pcbnew import FromMM, VECTOR2I

TRACE_W = 2.9   # about 50 ohm on FR4 of 1.6 mm (er 4.5)
TRACE_Y = 10.0
X0, X1 = 5.0, 35.0
BOARD = (0.0, 0.0, 40.0, 20.0)
# The grounded CPW: the closed-form value of these dimensions is 51.0 ohm,
# with eps_eff 2.88, on a dielectric of 1.53 mm and er 4.5.
CPW_W, CPW_GAP = 1.5, 0.3
SL_W = 0.6      # the strip of the stripline board, on In1.Cu of 4 layers


def _pad_fp(board, ref, x, y, net, size=None):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference(ref)
    fp.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
    pad = pcbnew.PAD(fp)
    pad.SetNumber("1")
    pad.SetShape(pcbnew.PAD_SHAPE_RECT)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetLayerSet(pad.SMDMask())
    w = size or TRACE_W
    pad.SetSize(VECTOR2I(FromMM(w), FromMM(w)))
    pad.SetPosition(fp.GetPosition())
    pad.SetNetCode(net.GetNetCode())
    fp.Add(pad)
    board.Add(fp)
    return pad


def _edge_cuts(board, corners):
    for i in range(len(corners)):
        e = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(VECTOR2I(FromMM(corners[i][0]), FromMM(corners[i][1])))
        e.SetEnd(VECTOR2I(FromMM(corners[(i + 1) % len(corners)][0]),
                          FromMM(corners[(i + 1) % len(corners)][1])))
        e.SetLayer(pcbnew.Edge_Cuts)
        e.SetWidth(FromMM(0.1))
        board.Add(e)


def _zone(board, layer, net, corners):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNetCode(net.GetNetCode())
    z.Outline().NewOutline()
    for cx, cy in corners:
        z.Outline().Append(FromMM(cx), FromMM(cy))
    board.Add(z)
    return z


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
    _edge_cuts(board, corners)
    _zone(board, pcbnew.B_Cu, gnd, corners)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    pcbnew.SaveBoard(path, board)
    return board, [pad1, pad2]


def make_cpw(path):
    """Make the grounded CPW board: a line of 30 mm with a ground at each
    side, and a full plane on B.Cu.

    The line and the two coplanar grounds are on F.Cu. Thus a CPW port is
    correct and a microstrip port is not: a microstrip port puts the
    reference plane at B.Cu only.
    """
    board = pcbnew.NewBoard(path)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    board.Add(rf)
    board.Add(gnd)

    pad1 = _pad_fp(board, "P1", X0, TRACE_Y, rf, size=CPW_W)
    pad2 = _pad_fp(board, "P2", X1, TRACE_Y, rf, size=CPW_W)

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(X0), FromMM(TRACE_Y)))
    t.SetEnd(VECTOR2I(FromMM(X1), FromMM(TRACE_Y)))
    t.SetWidth(FromMM(CPW_W))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(rf.GetNetCode())
    board.Add(t)

    x0, y0, x1, y1 = BOARD
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    _edge_cuts(board, corners)
    # The two coplanar grounds are filled graphic shapes, and not zones.
    # The zone filler keeps its own clearance from the copper of another
    # net, thus a zone gives a gap that is larger than CPW_GAP and that
    # changes with the design rules. A validation board must always give
    # the same geometry. The plane on B.Cu below stays a real zone.
    edge = 0.5 * CPW_W + CPW_GAP
    for ya, yb in ((y0, TRACE_Y - edge), (TRACE_Y + edge, y1)):
        r = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
        r.SetStart(VECTOR2I(FromMM(x0), FromMM(ya)))
        r.SetEnd(VECTOR2I(FromMM(x1), FromMM(yb)))
        r.SetLayer(pcbnew.F_Cu)
        r.SetFilled(True)
        r.SetWidth(0)
        board.Add(r)
    _zone(board, pcbnew.B_Cu, gnd, corners)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    pcbnew.SaveBoard(path, board)
    return board, [pad1, pad2]


def make_stripline(path):
    """Make the stripline board: a line of 30 mm on In1.Cu, between a
    plane on F.Cu and a plane on In2.Cu.

    A stripline is inside the dielectric on all its sides. Thus its
    eps_eff must be exactly er, and this board gives the most exact test
    of the propagation constant that a port measures.
    """
    board = pcbnew.NewBoard(path)
    board.SetCopperLayerCount(4)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    board.Add(rf)
    board.Add(gnd)

    pads = []
    for ref, x in (("P1", X0), ("P2", X1)):
        pad = _pad_fp(board, ref, x, TRACE_Y, rf, size=SL_W)
        pad.SetLayer(pcbnew.In1_Cu)  # LSET does not take a list in KiCad 10
        pads.append(pad)

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(X0), FromMM(TRACE_Y)))
    t.SetEnd(VECTOR2I(FromMM(X1), FromMM(TRACE_Y)))
    t.SetWidth(FromMM(SL_W))
    t.SetLayer(pcbnew.In1_Cu)
    t.SetNetCode(rf.GetNetCode())
    board.Add(t)

    x0, y0, x1, y1 = BOARD
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    _edge_cuts(board, corners)
    # The two planes must touch the strip layer. Thus they are F.Cu and
    # In2.Cu, and not F.Cu and B.Cu.
    _zone(board, pcbnew.F_Cu, gnd, corners)
    _zone(board, pcbnew.In2_Cu, gnd, corners)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    pcbnew.SaveBoard(path, board)
    return board, pads


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(__file__), "microstrip_50ohm.kicad_pcb")
    make(out)
    print("wrote", out)
    cpw = os.path.join(os.path.dirname(out), "cpw_50ohm.kicad_pcb")
    make_cpw(cpw)
    print("wrote", cpw)
