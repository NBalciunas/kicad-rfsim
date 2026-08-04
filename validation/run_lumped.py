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
# The shunt board of make_shunt(): the part is below the line at
# SHUNT_X, and a via takes the ground side of it down to the plane.
SHUNT_X = 20.0
SHUNT_PAD = 1.0         # the pads of the part with no package, square
SHUNT_GAP = 0.5         # the copper gap that the part bridges
SHUNT_OVERLAP = 0.25    # pad 1 goes this far into the track
SHUNT_DRILL = 0.6       # a small drill makes thin cells and a slow run
SHUNT_VIA_DROP = 0.75   # the via, below the lower edge of pad 2
# The land pattern of each package, from the libraries of KiCad 10
# (Resistor_SMD.pretty, `R_<code>_<metric>Metric`): the length of one pad
# ALONG the axis of the part, its width ACROSS that axis, and the gap
# between the two pads. The gap is 2*offset - length, from the same file.
# Item 6b needs these, because each entry of `board_reader._ESL_NH` is a
# different package, and L_board is a different number for each one.
SHUNT_LAND = {"0201": (0.46, 0.40, 0.18),
              "0402": (0.54, 0.64, 0.48),
              "0603": (0.80, 0.95, 0.85),
              "0805": (1.025, 1.40, 0.80),
              "1206": (1.125, 1.75, 1.80),
              "1210": (1.125, 2.65, 1.80),
              "2010": (1.225, 2.65, 3.40),
              "2512": (1.225, 3.35, 4.70)}


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


def _outline_and_plane(board, gnd):
    """Cut the board to BOARD and fill the plane on B.Cu.

    Call this function LAST: the zone filler must see every via, or a via
    does not connect to the plane.
    """
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

    _outline_and_plane(board, gnd)
    pcbnew.SaveBoard(path, board)
    return board, [pad1, pad2]


def _shunt_ground(board, gnd, rect_top, pad_bottom, pad_w):
    """Put the ground copper below the last pad, and the via to the plane.

    `rect_top` is the top edge of that pad, thus the rectangle cannot
    change the gap above it. `pad_bottom` is its lower edge, and the via
    goes SHUNT_VIA_DROP below that.

    The rectangle is a filled graphic shape and not a zone, in the same
    way as the coplanar ground of `make_test_board.make_cpw`: a zone
    keeps a clearance of its own from the copper of another net, and a
    validation board must always give the same geometry.
    """
    via_y = pad_bottom + SHUNT_VIA_DROP
    w = max(pad_w, SHUNT_DRILL + 0.6)
    r = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
    r.SetStart(VECTOR2I(FromMM(SHUNT_X - 0.5 * w), FromMM(rect_top)))
    r.SetEnd(VECTOR2I(FromMM(SHUNT_X + 0.5 * w), FromMM(via_y + 0.5 * w)))
    r.SetLayer(pcbnew.F_Cu)
    r.SetFilled(True)
    r.SetWidth(0)
    board.Add(r)

    v = pcbnew.PCB_VIA(board)
    v.SetPosition(VECTOR2I(FromMM(SHUNT_X), FromMM(via_y)))
    v.SetDrill(FromMM(SHUNT_DRILL))
    v.SetWidth(FromMM(SHUNT_DRILL + 0.3))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetNetCode(gnd.GetNetCode())
    board.Add(v)
    return via_y


def shunt_land(pkg=None):
    """Give (the length of a pad, its width, the gap) of the shunt part.

    `pkg` is an imperial package code of SHUNT_LAND, or None for the
    square pads that the first shunt board used.
    """
    return SHUNT_LAND.get(pkg, (SHUNT_PAD, SHUNT_PAD, SHUNT_GAP))


def shunt_gap_center(pkg=None):
    """Give (x, y) of the middle of the gap of the shunt part, in mm.

    The coordinates are those of KiCad: y points DOWN. run_shunt.py tests
    this point against the copper of the model.
    """
    pad_l, _, gap = shunt_land(pkg)
    return SHUNT_X, Y + 0.5 * TRACE_W - SHUNT_OVERLAP + pad_l + 0.5 * gap


def make_shunt(path, ref="C1", val="10p", pkg=None):
    """Make a microstrip of 50 ohm that has one part in SHUNT to ground.

    The line is continuous. The part is below it: pad 1 goes
    SHUNT_OVERLAP into the track, and pad 2 is one gap lower. The ground
    side continues below pad 2 as a rectangle of copper, and a through
    via at SHUNT_VIA_DROP below pad 2 takes that copper to the plane on
    B.Cu.

    `pkg` gives the land pattern of a package (SHUNT_LAND). With no
    package the pads are square and 1.0 mm, which is the board of the log
    of 2026-08-04 (8).

    The part and that via then make a SERIES resonance to ground, and
    |S21| has a deep notch at it. A frequency is immune to a scale error
    of the amplitude; an absolute value is not. Refer to run_shunt.py,
    which measures a body ESL from the notch.

    **The via is not in pad 2**, and this is not a choice of style. The
    annular ring of a via is copper in the model
    (`board_reader._copper_polys` flashes it on each layer). A drill of
    0.6 mm gives a ring of 0.9 mm, which is wider than the 0.46 mm pad of
    an 0201 land: the ring would go 0.22 mm past the pad, the gap is
    0.18 mm, and the ring would BRIDGE the part. To make the drill follow
    the pad in the place of this would give a drill of 0.1 mm on an 0201,
    thus thin cells and a slow run. The via goes below the pad for every
    package, thus one rule covers all of them and the mesh stays coarse.
    """
    pad_l, pad_w, gap = shunt_land(pkg)
    board = pcbnew.NewBoard(path)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    for n in (rf, gnd):
        board.Add(n)

    pad1 = _pad(_fp(board, "P1"), "1", 5.0, Y, TRACE_W, TRACE_W, rf)
    pad2 = _pad(_fp(board, "P2"), "1", 35.0, Y, TRACE_W, TRACE_W, rf)
    # The line is one track from pad to pad. Its round ends are at the
    # two port pads, thus they cannot go into the gap of the part.
    _track(board, 5.0, 35.0, rf)

    # Pad 1 overlaps the edge of the track. Two polygons that only touch
    # can leave a sliver between them, and the copper of the part must
    # meet the line with no such doubt.
    yg = shunt_gap_center(pkg)[1]
    y1, y2 = yg - 0.5 * (pad_l + gap), yg + 0.5 * (pad_l + gap)
    part = _fp(board, ref, val)
    _pad(part, "1", SHUNT_X, y1, pad_w, pad_l, rf)
    _pad(part, "2", SHUNT_X, y2, pad_w, pad_l, gnd)

    _shunt_ground(board, gnd, y2 - 0.5 * pad_l, y2 + 0.5 * pad_l, pad_w)
    _outline_and_plane(board, gnd)
    pcbnew.SaveBoard(path, board)
    return board, [pad1, pad2]


def shunt2_gap_centers():
    """Give [(x, y), (x, y)] for the two gaps of make_shunt2(), in mm.

    The coordinates are those of KiCad: y points DOWN.
    """
    y = Y + 0.5 * TRACE_W - SHUNT_OVERLAP + SHUNT_PAD + 0.5 * SHUNT_GAP
    return [(SHUNT_X, y), (SHUNT_X, y + SHUNT_PAD + SHUNT_GAP)]


def make_shunt2(path, refs=("C1", "L1"), vals=("4.7p", "5n")):
    """Make a 50 ohm line with TWO parts in series, from the line to ground.

    The geometry of `make_shunt` with one more pad and one more gap: the
    line, part 1, a middle pad, part 2, the ground pad, and the via to
    the plane. The two element boxes are then SHUNT_PAD apart, which is
    the first board here that puts two lumped elements near each other.

    A C and an L in series to ground make a notch in |S21| at
    `1/(2*pi*sqrt((L + L_board)*C))`. Thus the two parts together give a
    FREQUENCY, and the test of the pair does not depend on an amplitude.
    Refer to run_shunt.py, mode `two`.
    """
    board = pcbnew.NewBoard(path)
    rf = pcbnew.NETINFO_ITEM(board, "RF")
    mid = pcbnew.NETINFO_ITEM(board, "MID")
    gnd = pcbnew.NETINFO_ITEM(board, "GND")
    for n in (rf, mid, gnd):
        board.Add(n)

    pad1 = _pad(_fp(board, "P1"), "1", 5.0, Y, TRACE_W, TRACE_W, rf)
    pad2 = _pad(_fp(board, "P2"), "1", 35.0, Y, TRACE_W, TRACE_W, rf)
    _track(board, 5.0, 35.0, rf)

    # The three pads of the branch: one on the line, one between the two
    # parts, and one on ground. The middle pad is pad 2 of the first part
    # AND pad 1 of the second: two pads at the same place, because each
    # footprint must have exactly 2 numbered pads of its own.
    (_, g1), (_, g2) = shunt2_gap_centers()
    y_top = g1 - 0.5 * (SHUNT_PAD + SHUNT_GAP)
    y_mid = g1 + 0.5 * (SHUNT_PAD + SHUNT_GAP)
    y_bot = g2 + 0.5 * (SHUNT_PAD + SHUNT_GAP)
    a = _fp(board, refs[0], vals[0])
    _pad(a, "1", SHUNT_X, y_top, SHUNT_PAD, SHUNT_PAD, rf)
    _pad(a, "2", SHUNT_X, y_mid, SHUNT_PAD, SHUNT_PAD, mid)
    b = _fp(board, refs[1], vals[1])
    _pad(b, "1", SHUNT_X, y_mid, SHUNT_PAD, SHUNT_PAD, mid)
    _pad(b, "2", SHUNT_X, y_bot, SHUNT_PAD, SHUNT_PAD, gnd)

    _shunt_ground(board, gnd, y_bot - 0.5 * SHUNT_PAD,
                  y_bot + 0.5 * SHUNT_PAD, SHUNT_PAD)
    _outline_and_plane(board, gnd)
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
