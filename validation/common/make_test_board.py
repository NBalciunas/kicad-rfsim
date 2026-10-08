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
# The grounded CPW: the closed formula gives 51.0 ohm for these dimensions,
# with eps_eff 2.88, on a dielectric of 1.53 mm and er 4.5.
CPW_W, CPW_GAP = 1.5, 0.3
SL_W = 0.6      # the strip of the stripline board, on In1.Cu of 4 layers
# The stitching vias of via_fence(), which no validation board calls. A CPW
# and a stripline on a board connect their reference conductors together.
# But a measurement showed that the stitching does NOT change the impedance
# on these boards: refer to via_fence(). The drill is large on purpose. The
# mesh puts a line at each side of a via and at its center. A small drill
# makes thin cells and a slow run.
VIA_DRILL, VIA_PITCH, VIA_OFFSET = 0.6, 3.0, 3.0


def _line_board(length):
    """Give (x of port 2, the outline) of a line of `length` mm.

    The default length of 30 mm gives X1 and BOARD. A rig of EMerge
    measures eps_eff from two lengths, thus it asks for a longer board.
    """
    x1 = X0 + length
    return x1, (BOARD[0], BOARD[1], x1 + (BOARD[2] - X1), BOARD[3])


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


def via_fence(board, net, offset=VIA_OFFSET, x1=X1):
    """Put a row of through vias at each side of the line, which goes from
    X0 to `x1`.

    The vias connect all the copper layers of `net` together. Call this
    function BEFORE the zone filler, thus each zone connects to them.

    Only the CPW board of EMerge calls it (`make_cpw(fence=True)`), and a
    board of openEMS does not use it to give the correct impedance. A
    stitched board and the board with no via give the SAME value at the
    medium mesh (34.3 against 34.6 ohm). A solid wall
    between the two ground conductors of a CPW gave the same result on
    2026-08-04. The error of the two boards was the MESH, and
    `runner._mesh` corrects it at this time.

    This function has two traps:

    - A THROUGH via puts an annular ring on ALL copper layers, and that
      includes the layer of the strip. `SetRemoveUnconnected(True)` with
      `SetKeepStartEnd(True)` removes the ring from a layer that has no
      copper of this net.
    - A via that is small against the mesh step makes thin cells adjacent
      to coarse cells, and that causes an error of its own.
    """
    x = X0 - 2.0
    while x <= x1 + 2.0 + 1e-9:
        for y in (TRACE_Y - offset, TRACE_Y + offset):
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
            v.SetDrill(FromMM(VIA_DRILL))
            v.SetWidth(FromMM(VIA_DRILL + 0.3))
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNetCode(net.GetNetCode())
            board.Add(v)
        x += VIA_PITCH


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


def make_zone_holes(path, void=True):
    """The microstrip board, with a HOLE in the ground zone below the line.

    A filled zone that has holes is the one geometry that got to `extract()`
    but did not get to the SOLVER. `Fracture` changes each hole into a slit
    with no width. Nobody showed that CSXCAD makes correct raster data from
    such a polygon. A slit that closes attaches the copper across the hole. A
    slit that opens too far cuts the plane. The board of
    `run_headless_openems.py` has a plain rectangle on B.Cu, thus it cannot
    show these errors.

    **The hole is DIRECTLY below the line, and it is large.** That is the
    function of the board. A hole at the side of the line changes the
    impedance by a very small quantity. Thus a slit that closed and a slit
    that is correct give the SAME number, and the test then shows nothing.
    The ground below the line has the return current. Thus a void there
    must increase the impedance by a large step. `void=False` gives the
    same board with NO hole, and that is the control. The difference of the
    two runs is the measurement.

    Three vias of a different net stay in the pour on the two boards. They
    make the small clearance holes of a usual board. The two boards are
    then the same in all other parts.
    """
    board = pcbnew.NewBoard(path)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    other = pcbnew.NETINFO_ITEM(board, "OTHER")
    for n in (rf, gnd, other):
        board.Add(n)

    pad1 = _pad_fp(board, "P1", X0, TRACE_Y, rf)
    pad2 = _pad_fp(board, "P2", X1, TRACE_Y, rf)

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(X0), FromMM(TRACE_Y)))
    t.SetEnd(VECTOR2I(FromMM(X1), FromMM(TRACE_Y)))
    t.SetWidth(FromMM(TRACE_W))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(rf.GetNetCode())
    board.Add(t)

    # The vias of the other net. They are BELOW the line in y, thus they do
    # not touch the track on F.Cu. Their annular ring on B.Cu makes the
    # pour keep a clearance, and that clearance is the hole.
    for x in (13.0, 20.0, 27.0):
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(VECTOR2I(FromMM(x), FromMM(TRACE_Y + 3.5)))
        v.SetDrill(FromMM(VIA_DRILL))
        v.SetWidth(FromMM(VIA_DRILL + 0.6))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNetCode(other.GetNetCode())
        board.Add(v)

    x0, y0, x1, y1 = BOARD
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    _edge_cuts(board, corners)
    z = _zone(board, pcbnew.B_Cu, gnd, corners)
    if void:
        # A hole in the OUTLINE of the zone, below the middle of the line.
        # The filler keeps it, thus the pour has a true void and not only a
        # clearance. 8 x 6 mm against a line of 2.9 mm: the return current
        # must go around it.
        hx0, hx1 = 0.5 * (X0 + X1) - 4.0, 0.5 * (X0 + X1) + 4.0
        hy0, hy1 = TRACE_Y - 3.0, TRACE_Y + 3.0
        h = z.Outline().NewHole(0)
        for cx, cy in ((hx0, hy0), (hx1, hy0), (hx1, hy1), (hx0, hy1)):
            z.Outline().Append(FromMM(cx), FromMM(cy), 0, h)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    pcbnew.SaveBoard(path, board)
    return board, [pad1, pad2]


# The probe-fed patch of kicad-rfgen ("Microstrip Patch Antenna (Dual
# Coaxial Feed)", its defaults): a square patch on F.Cu, a square ground
# on B.Cu, and the pin of an SMA through a plated hole. The pad is 4.286 mm
# with a drill of 1.27 mm (the pin), and the ground keeps a clearance of r
# 3.5 mm around it. The feed is 7.286 mm from the centre.
PATCH, GROUND = 28.314, 37.914
PROBE_PAD, PROBE_DRILL, PROBE_CLEAR = 4.286, 1.27, 3.5
PROBE_OFFSET = 7.286
PATCH_C = (30.0, 30.0)  # the centre of the board, in the mm of KiCad


def make_probe_patch(path, feed="coax", dual=False, ground=True,
                     pad=PROBE_PAD, clear=PROBE_CLEAR):
    """The patch of kicad-rfgen, with a probe feed from below.

    `feed` is "coax": the pad is a through-hole pad of `pad` mm, and the
    ground keeps a clearance of radius `clear` around it. Or it is "pad":
    an SMD pad of the pin dimension on F.Cu and a SOLID ground below it,
    which is the probe of the openEMS tutorial of a patch: a lumped port
    from the plane to the patch. A coaxial feed with a small pad and a
    small clearance is almost that probe. `dual` adds the second feed, on
    the y axis. `ground=False` leaves out the ground, for the guard.

    The patch is a filled shape with no net, as the copper of the
    footprint of kicad-rfgen. The ground is a zone of GND, thus the filler
    makes the clearance around each pad of the RF nets.
    """
    board = pcbnew.NewBoard(path)
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    board.Add(gnd)
    cx, cy = PATCH_C
    # KiCad has y down: the second feed of kicad-rfgen is at +7.286 in y,
    # below the centre on the screen.
    feeds = [(cx + PROBE_OFFSET, cy)] + ([(cx, cy + PROBE_OFFSET)]
                                         if dual else [])
    size = pad
    pads = []
    for i, (x, y) in enumerate(feeds):
        net = pcbnew.NETINFO_ITEM(board, "RF%d" % (i + 1))
        board.Add(net)
        fp = pcbnew.FOOTPRINT(board)
        fp.SetReference("J%d" % (i + 1))
        fp.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
        pad = pcbnew.PAD(fp)
        pad.SetNumber("1")
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        if feed == "coax":
            pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
            pad.SetLayerSet(pad.PTHMask())
            pad.SetSize(VECTOR2I(FromMM(size), FromMM(size)))
            pad.SetDrillSize(VECTOR2I(FromMM(PROBE_DRILL),
                                      FromMM(PROBE_DRILL)))
        else:
            pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            pad.SetLayerSet(pad.SMDMask())
            pad.SetSize(VECTOR2I(FromMM(PROBE_DRILL), FromMM(PROBE_DRILL)))
        pad.SetPosition(fp.GetPosition())
        pad.SetNetCode(net.GetNetCode())
        fp.Add(pad)
        board.Add(fp)
        pads.append(pad)

    patch = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
    patch.SetStart(VECTOR2I(FromMM(cx - PATCH / 2), FromMM(cy - PATCH / 2)))
    patch.SetEnd(VECTOR2I(FromMM(cx + PATCH / 2), FromMM(cy + PATCH / 2)))
    patch.SetFilled(True)
    patch.SetWidth(0)
    patch.SetLayer(pcbnew.F_Cu)
    board.Add(patch)

    h = GROUND / 2
    corners = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h),
               (cx - h, cy + h)]
    _edge_cuts(board, corners)
    if ground:
        z = _zone(board, pcbnew.B_Cu, gnd, corners)
        z.SetLocalClearance(FromMM(clear - size / 2))
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(path, board)
    return board, pads


def make_cpw(path, length=X1 - X0, fence=False):
    """Make the grounded CPW board: a line of 30 mm with a ground at each
    side, and a full plane on B.Cu. `length` gives a different line.

    `fence` puts a row of vias along each coplanar ground, down to B.Cu.
    **The board of EMerge must have it.** A lumped port refers to B.Cu
    only. Without the vias, the two coplanar grounds float, and the line
    carries a mode between a microstrip and a CPW: EMerge read eps_eff
    3.21 against 2.88 for a CPW. The CPW port of openEMS drives the mode
    of a CPW itself, and there the vias do not change Z0.

    The line and the two coplanar grounds are on F.Cu. Thus a CPW port is
    correct and a microstrip port is not: a microstrip port puts the
    reference plane at B.Cu only.
    """
    board = pcbnew.NewBoard(path)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    board.Add(rf)
    board.Add(gnd)

    xp2, outline = _line_board(length)
    pad1 = _pad_fp(board, "P1", X0, TRACE_Y, rf, size=CPW_W)
    pad2 = _pad_fp(board, "P2", xp2, TRACE_Y, rf, size=CPW_W)

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(X0), FromMM(TRACE_Y)))
    t.SetEnd(VECTOR2I(FromMM(xp2), FromMM(TRACE_Y)))
    t.SetWidth(FromMM(CPW_W))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNetCode(rf.GetNetCode())
    board.Add(t)

    x0, y0, x1, y1 = outline
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    _edge_cuts(board, corners)
    # The two coplanar grounds are filled graphic shapes, and not zones.
    # The zone filler keeps its own clearance from the copper of a
    # different net. Thus a zone gives a gap that is larger than CPW_GAP,
    # and that changes with the design rules. A validation board must
    # always give the same geometry. The plane on B.Cu below stays a zone.
    edge = 0.5 * CPW_W + CPW_GAP
    for ya, yb in ((y0, TRACE_Y - edge), (TRACE_Y + edge, y1)):
        r = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
        r.SetStart(VECTOR2I(FromMM(x0), FromMM(ya)))
        r.SetEnd(VECTOR2I(FromMM(x1), FromMM(yb)))
        r.SetLayer(pcbnew.F_Cu)
        r.SetFilled(True)
        r.SetWidth(0)
        board.Add(r)
    if fence:
        via_fence(board, gnd, x1=xp2)
    _zone(board, pcbnew.B_Cu, gnd, corners)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())

    pcbnew.SaveBoard(path, board)
    return board, [pad1, pad2]


def make_stripline(path, length=X1 - X0):
    """Make the stripline board: a line of 30 mm on In1.Cu, between a plane
    on F.Cu and a plane on In2.Cu. `length` gives a different line.

    A stripline is in the dielectric on all its sides. Thus its eps_eff
    must be equal to er. This board gives the most accurate test of the
    propagation constant that a port measures.
    """
    board = pcbnew.NewBoard(path)
    board.SetCopperLayerCount(4)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    board.Add(rf)
    board.Add(gnd)

    xp2, outline = _line_board(length)
    pads = []
    for ref, x in (("P1", X0), ("P2", xp2)):
        pad = _pad_fp(board, ref, x, TRACE_Y, rf, size=SL_W)
        # The layer SET must have In1.Cu, and not only the item layer.
        # SaveBoard writes the set. PAD.GetLayer() of a board that comes
        # from a file always gives F.Cu. LSET does not accept a list in
        # KiCad 10. Thus the code adds the layer to an empty set.
        lset = pcbnew.LSET()
        lset.AddLayer(pcbnew.In1_Cu)
        pad.SetLayerSet(lset)
        pads.append(pad)

    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(X0), FromMM(TRACE_Y)))
    t.SetEnd(VECTOR2I(FromMM(xp2), FromMM(TRACE_Y)))
    t.SetWidth(FromMM(SL_W))
    t.SetLayer(pcbnew.In1_Cu)
    t.SetNetCode(rf.GetNetCode())
    board.Add(t)

    x0, y0, x1, y1 = outline
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
