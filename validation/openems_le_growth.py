"""A microstrip board makes a mode that INCREASES late in a long run.

**This file is the harness of that problem, and it is not a part of the
plugin.** It uses only numpy and openEMS, and matplotlib for the picture.
It does NOT use KiCad, and it reads no file of this repository. Thus it
shows which part of the problem comes from the plugin, and which part comes
from openEMS.

The board is a microstrip line of 50 ohm on FR-4, in two half lines, with a
gap of 0.5 mm between them. A lumped element can bridge the gap, and the
controls keep it empty:

    port 1                                             port 2
      |                                                  |
    =========================|  |========================= F.Cu, z = 1.53
                             L = 10 nH, a box of 0.5 x 2.9 mm
    ---------------------------------------------------- FR-4, er 4.5
    ==================================================== B.Cu, z = 0

**The field decreases after the excitation. It stays at a level for some
tens of nanoseconds, and then it increases without a limit.** The mode must
have TWO conditions. It does not have to have an element, a gap in the
copper or a change of the timestep. (Measured on 2026-09-19, at the full
Courant step across 60 ns. The growth is the fit of the last 40%.)

**1. A column of REFINED mesh cells.** ONE strip with no gap in it, that
keeps the two mesh lines of a gap (`solidlines`), increases at +0.3863 /ns.
The SAME strip with no such lines (`solid`) decreases at -0.0152 across
60 ns and at -0.0059 across 120 ns. An OPEN gap of 2 mm (`gap2`) also
decreases. Its two lines are on the mesh, and they cause no small cell.
Thus the cells make the mode, and the copper does not.

**2. A PML near the copper in z.** A closed box of PEC on the 6 faces
(`allpec`) decreases at -0.0561 with the same mesh and the same timestep.
The same box with PEC copper (`allpec_pec`) also decreases. Thus the
engine, the mesh, the timestep and the ConductingSheet do not cause the
mode: an absorber must give the energy. A PML only on the z faces
(`zpml_only`, MUR at the sides) increases at +0.4288. A PML only at the
sides (`zpec`, PEC in z) increases at +0.4212. MUR on the 6 faces (`mur`)
increases 9 times slower.

**A user can move the STANDOFF in z**: the clear air plus the depth of the
PML, from the copper to the outer wall. 8 mm gives 0.37 to 0.41 /ns, 12 mm
gives 0.06 to 0.07, and 16 mm gives no growth. One mm of air and one mm of
depth have the same effect. **Only the z faces count.** `airz12` (12 mm of
air only in z) decreases. `airxy12` (12 mm only in x and y) increases at
the full rate.

All users can make this mesh:

- a step of 2.355 mm (lambda/10 at 6 GHz in the substrate);
- a line on each edge of the copper;
- 4 cells across the strip, and 4 cells in the substrate;
- an equidistant band of 8 cells for each PML.

**Two controls that one number held before.** `MARGIN` gave the air band
AND the cell of the PML at the same time (`MARGIN` mm of air, and a PML of
8 cells of `MARGIN`/8). Thus a run at 12 mm moved the two of them, and no
measurement could tell which one causes the mode. `AIR` and `PML_STEP` are
different values at this time. The `MARGIN` = m of before is `AIR` = m with
`PML_STEP` = m/8.

**Field probes show WHERE the mode is.** Each run writes the E-field at
9 points. They are above the gap, above the line, in each of the 3 PML
bands, and in a PML corner. They are also in the air below the top PML, and
in the substrate. All 9 give the SAME rate, thus it is one mode of all the
domain. It is in a column above the refined cells, and **the PML does not
decrease it**. At 2 mm in the top PML, it keeps 0.84 of the level
immediately out of the PML. A run that decreases gives 0.0011 between the
same two points.

Run it with the python that has openEMS:

    python openems_le_growth.py                 # the runs of RUNS
    python openems_le_growth.py L10 0.5 120     # one run of 120 ns
    python openems_le_growth.py table           # the table again, no run
    python openems_le_growth.py probes none 1   # where the mode is

The cases have an element (`L10`, `L1`, `R50`, `L10pec`, `L10cell2`,
`L10normal`) or an open gap (`none`). The controls change the copper and
its mesh (`short`, `solid`, `solidlines`, `gap2`, `gap02`, `grade12`,
`grade20`, `fine`, `finepml`), the air and the PML (`air8`, `air12`,
`air20`, `airz12`, `airxy12`, `pml12`, `pml16`, `margin8`, `both12`), the
boundary (`mur`, `zpec`, `zmur`, `zpml_only`, `allpec`, `allpec_pec`,
`allpec_solid`) and the loss of the substrate (`lossless`). Each run writes
its data to `out_growth_<case>_f<factor>/`. The picture and the traces go
to `out_growth_report/`.

**Before 2026-09-19, a defect of this file gave a false control.** A local
function with the name `metal` hid the parameter with the same name. Thus
the `none` case put a box of PEC in the gap, and it was the `short` case.
No run before that date had an OPEN gap.
"""
import glob
import os
import shutil
import sys

import numpy as np

# openEMS on Windows must have the DLLs of its binary directory before the
# import.
if os.name == "nt":
    for _d in (os.environ.get("OPENEMS_PATH"), r"C:\openEMS"):
        if _d and os.path.isdir(_d):
            os.add_dll_directory(_d)

from CSXCAD import ContinuousStructure                      # noqa: E402
from CSXCAD.SmoothMeshLines import SmoothMeshLines          # noqa: E402
from openEMS import openEMS                                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EPS0 = 8.8541878128e-12

# ------------------------------------------------------- the board, in mm
SUB_EPS, SUB_TAN, SUB_H = 4.5, 0.02, 1.53   # FR-4 of 1.53 mm
BOARD = (0.0, 40.0, -10.0, 10.0)            # x0, x1, y0, y1
W = 2.9                  # the width of the strip: about 50 ohm on this board
GAP = 0.5  # the gap between the two half strips
X_GAP = 20.0             # the face of the gap that is nearer to port 1
STRIP_X = (3.55, 36.45)  # the two ends of the copper
PORT_X = (5.0, 35.0)     # the feed point of each port
PORT_LEN = 9.0           # the length of the box of each port
AIR = 4.0                # the clear air between the board and the PML
PML_STEP = 0.5           # the cell of the PML band, thus its depth is 8 x it
Z0 = 50.0
F0, FC = 3.5e9, 2.5e9  # the excitation is from 1 GHz to 6 GHz
RES = 2.355              # lambda/10 at 6 GHz in the substrate
STRIP_CELLS = 4          # the cells across the strip
SUB_CELLS = 4            # the cells in the substrate
PML = 8                  # the cells of each PML band

TIME_NS = 120.0          # the simulated time of a run of the table
SEGMENTS = 200           # the segments of the envelope
# The part of the window that gives the RATE of the growth: the last 40%. A
# fit that starts at the turn also has the flat part of the trace. Thus its
# slope moves with the position of the turn, and two runs cannot be
# compared. The late fit is the asymptotic rate. It is the same to 0.2% at
# a timestep factor of 0.5 and of 0.25.
LATE_FRAC = 0.4

# The cases:
#
# - `comp` is the element in the gap;
# - `pec` makes the copper PEC;
# - `cells` is the number of MESH CELLS in series through the box of the
#   element;
# - `short` puts PEC in the gap, and `solid` removes the gap;
# - `air` is the clear air (one number, or one for each of x, y and z);
# - `pml_step` is the cell of the PML band;
# - `bc` is the boundary, and `tand` is the loss of the substrate.
ELEMENTS = {
    "L10": dict(comp={"L": 10e-9}),
    "L1": dict(comp={"L": 1e-9}),
    "R50": dict(comp={"R": 50.0}),
    "L10pec": dict(comp={"L": 10e-9}, pec=True),
    "L10cell2": dict(comp={"L": 10e-9}, cells=2),
    # the same board with the end criteria of a usual run: it stops
    # itself, long before the growth
    "L10normal": dict(comp={"L": 10e-9}, end=1e-4),
    # The three cases of the COPPER. They tell if the gap makes the mode.
    # The cases are an open gap, a box of PEC in the gap, and one strip
    # with no gap.
    "none": dict(),
    "short": dict(short=True),
    "solid": dict(solid=True),
    # ONE strip with no gap in it, that keeps the mesh lines of the gap. It
    # shows the difference between the COPPER and the MESH: `short` has the
    # two, and `solid` has no one of them.
    "solidlines": dict(solid=True, gaplines=True),
    # a gap of 2 mm, thus its cell is not much smaller than the
    # step of the mesh
    "gap2": dict(gap=2.0),
    # The AIR and the PML, which one number moved together before. Each
    # case holds an open gap, thus `none` is the control of all of them.
    "air8": dict(air=8.0),                     # more air, the same PML
    "air12": dict(air=12.0),
    "air20": dict(air=20.0),
    "airz12": dict(air=(4.0, 4.0, 12.0)),      # more air in z only
    "airxy12": dict(air=(12.0, 12.0, 4.0)),    # more air in x and y only
    "pml12": dict(pml_step=1.5),               # the same air, a PML of 12 mm
    "pml16": dict(pml_step=1.0),               # the same air, a PML of 8 mm
    "both12": dict(air=12.0, pml_step=1.5),    # what `MARGIN` = 12 was
    "margin8": dict(air=8.0, pml_step=1.0),    # what `MARGIN` = 8 is
    # a gap of 0.2 mm: the cell of the refinement is smaller again
    "gap02": dict(gap=0.2),
    # The GRADE of the mesh around the refinement. The cells stay the same
    # at the gap, and the ramp out of it changes. Thus these two cases show
    # the difference between the refinement and its grade.
    "grade12": dict(ratio=1.2),
    "grade20": dict(ratio=2.0),
    # **Is the limit in MILLIMETRES or in CELLS?** `fine` divides the step
    # of the mesh by two, and it keeps the air and the band. Thus the
    # standoff stays 8 mm, and it becomes two times as many cells.
    # `finepml` is the band of 8 cells of `res` at that preset.
    "fine": dict(res=0.5 * RES),
    "finepml": dict(res=0.5 * RES, pml_step=0.5 * RES),
    "mur": dict(bc="MUR"),                     # no PML at all
    # Only the z faces, which have the mode in `airz12`. PEC gives a wall
    # with no absorber. MUR gives an absorber that is not a PML. PMC is the
    # other wall.
    "zpec": dict(bc=["PML_8"] * 4 + ["PEC"] * 2),
    "zmur": dict(bc=["PML_8"] * 4 + ["MUR"] * 2),
    "zpml_only": dict(bc=["MUR"] * 4 + ["PML_8"] * 2),
    # **A CLOSED box of PEC on the 6 faces.** Such a model has no absorber,
    # thus nothing in it can give energy. A run that increases then shows
    # that the TIMESTEP is more than the limit of the mesh. It does not
    # show a fault of a boundary.
    "allpec": dict(bc="PEC"),
    "allpec_solid": dict(bc="PEC", solid=True),
    # the closed box, also with PEC copper. NOTHING in this model can give
    # energy. Thus a run that increases shows that the engine itself is not
    # stable on this mesh. Run `allpec` at a factor of 0.25 for the
    # question of the timestep.
    "allpec_pec": dict(bc="PEC", pec=True),
    "lossless": dict(tand=0.0),                # a substrate with no loss
}
# The runs of the table: the case, the timestep factor and the window in
# ns. Each window is long, because the growth of this board starts after
# some tens of nanoseconds. The two factors show that the rate does not
# follow the timestep. 1 nH runs at the FULL Courant step. L10pec removes
# the conducting sheet, and R50 removes the inductance. Each one of the
# five gives 0.42 /ns. The `none`, `short` and `solidlines` controls, which
# have no element, also give 0.42 /ns. `solid` is the one that DECREASES.
# It is the same strip as `solidlines`, but it keeps no small cell at the
# gap. Also run those four before you think that an element or a gap makes
# the mode.
RUNS = (("L10", 0.5, TIME_NS), ("L10", 0.25, TIME_NS), ("L1", 1.0, TIME_NS),
        ("L10pec", 0.5, TIME_NS), ("R50", 0.5, TIME_NS),
        ("none", 1.0, TIME_NS), ("short", 1.0, TIME_NS),
        ("solidlines", 1.0, TIME_NS), ("solid", 1.0, TIME_NS))

# The E-field probes, in mm. A word, and not a number, reads the domain.
# Thus a point in the air or in the PML keeps its position in the geometry
# when the air band moves. The name tells where the point is.
PROBES = (
    ("gap_air", (X_GAP + 0.5 * GAP, 0.0, "top+0.5")),  # above the gap
    ("strip_air", (10.0, 0.0, "top+0.5")),  # above the line
    ("air_top", (X_GAP + 0.5 * GAP, 0.0, "pml_hi-0.25")),  # below the top PML
    ("pml_top", (X_GAP + 0.5 * GAP, 0.0, "pml_hi+half")),   # in the top PML
    ("pml_bot", (X_GAP + 0.5 * GAP, 0.0, "lo+half")),  # below the ground
    ("pml_x", ("lo+half", 0.0, SUB_H)),  # after the board end
    ("pml_y", (X_GAP + 0.5 * GAP, "lo+half", SUB_H)),  # adjacent to the board
    ("pml_corner", ("lo+half", "lo+half", "lo+half")),      # a corner of 3 PMLs
    ("sub_gap", (X_GAP - 1.0, 0.0, 0.5 * SUB_H)),           # in the substrate
)


def _band(lo, hi, step):
    """Give the equidistant lines of the two PML bands of one axis."""
    return ([lo + i * step for i in range(PML + 1)]
            + [hi - i * step for i in range(PML + 1)])


def _rect(x0, x1, y0, y1):
    """Give the 4 corners of a rectangle, for AddLinPoly."""
    return np.array([[x0, x1, x1, x0], [y0, y0, y1, y1]])


def _domain(air, pml_step):
    """Give the domain and the PML faces of x, y and z, in mm.

    The board fills x0..x1, y0..y1 and 0..SUB_H. Outward from it are `air`
    mm of clear air, and then the PML of `PML` cells of `pml_step`.
    """
    x0, x1, y0, y1 = BOARD
    a = (air, air, air) if np.isscalar(air) else tuple(air)
    depth = PML * pml_step
    return [dict(lo=lo - ai - depth, hi=hi + ai + depth,
                 pml_lo=lo - ai, pml_hi=hi + ai)
            for (lo, hi), ai in zip(((x0, x1), (y0, y1), (0.0, SUB_H)), a)]


def _probe_pts(air, pml_step):
    """Give the points of `PROBES` for one geometry, in mm."""
    dom = dict(zip("xyz", _domain(air, pml_step)))
    half = 0.5 * PML * pml_step

    def one(v, axis):
        if not isinstance(v, str):
            return float(v)
        d = dom[axis]
        return {"top+0.5": SUB_H + 0.5,
                "pml_hi-0.25": d["pml_hi"] - 0.25,
                "pml_hi+half": d["pml_hi"] + half,
                "lo+half": d["lo"] + half}[v]

    return [(name, [one(v, a) for v, a in zip(pt, "xyz")])
            for name, pt in PROBES]


def build(comp=None, factor=1.0, pec=False, cells=1, time_ns=TIME_NS,
          end=1e-12, short=False, solid=False, air=AIR, pml_step=PML_STEP,
          bc="PML_8", tand=SUB_TAN, probes=True, gaplines=False,
          gap=GAP, ratio=1.4, res=RES):
    """Make the FDTD model of the board with `comp` in the gap.

    `cells` is the number of mesh cells in series through the box of the
    element: 1 is the box with no line in it. `end` is the end criteria.
    Its default value is so small that no run gets to it. The window then
    comes only from `time_ns`. `short` puts a box of PEC in the gap.
    `solid` makes the strip as ONE polygon with no gap. Those two controls
    tell if the gap causes the mode.
    """
    comp = comp or {}
    x0, x1, y0, y1 = BOARD
    dom = _domain(air, pml_step)
    fdtd = openEMS(NrTS=10 ** 7, EndCriteria=end)
    if factor < 1.0:
        fdtd.SetTimeStepFactor(factor)
    # A run of a constant TIME: each factor then has the same window.
    fdtd.SetMaxTime(time_ns * 1e-9)
    fdtd.SetGaussExcite(F0, FC)
    # `bc` is one name for the 6 faces, or a list of 6 in the sequence
    # xmin, xmax, ymin, ymax, zmin, zmax.
    fdtd.SetBoundaryCond(list(bc) if isinstance(bc, (list, tuple))
                         else [bc] * 6)
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)

    # the mesh: the board, the clear air and the band of each PML
    xs, ys, zs = [set(_band(d["lo"], d["hi"], pml_step)) for d in dom]
    # a line on each edge of the copper, and on each face of the element
    xs.update((x0, x1) + STRIP_X)
    if not solid or gaplines:
        xs.update((X_GAP, X_GAP + gap))
        # the lines IN the box of the element, for a case with more than
        # one cell in series
        xs.update(np.linspace(X_GAP, X_GAP + gap, cells + 1).tolist())
    xs.update((PORT_X[0], PORT_X[0] + PORT_LEN,
               PORT_X[1], PORT_X[1] - PORT_LEN))
    ys.update((y0, y1))
    ys.update(np.linspace(-0.5 * W, 0.5 * W, STRIP_CELLS + 1).tolist())
    zs.update(np.linspace(0.0, SUB_H, SUB_CELLS + 1).tolist())
    # **Grade outward from the edge of the strip, and put 2 lines at each
    # side of each copper plane.** SmoothMeshLines fills each interval
    # between two lines that do not move, and it does not look at the
    # adjacent intervals. Thus a cell of 0.725 mm at the strip can touch a
    # cell of 2.355 mm. The cell above the copper is as large as the full
    # step. These lines remove that step, as the mesh of the plugin does.
    #
    # **They do NOT stop the late growth of this board**, and a measurement
    # shows that. With `AIR` = 4 mm, the board increases across 120 ns with
    # these lines and without them, with an element and with no element.
    # The grade of `SmoothMeshLines` also does not stop it. 1.2 gives
    # +0.2489 /ns and 2.0 gives +0.2853, against +0.3739 at 1.4. Only the
    # STANDOFF in z moves it. `AIR` and `PML_STEP` have the same effect on
    # that standoff.
    for side in (-1, 1):
        pos, step = side * 0.5 * W, float(W) / STRIP_CELLS
        while step < res:
            pos += side * step
            ys.add(pos)
            step *= 1.4
    zstep = SUB_H / SUB_CELLS
    for z in (0.0, SUB_H):
        for k in (1, 2):
            zs.update((z + k * zstep, z - k * zstep))
    for axis, lines in zip("xyz", (xs, ys, zs)):
        grid.AddLine(axis, np.round(
            SmoothMeshLines(sorted(lines), res, ratio), 9))

    kappa = 2 * np.pi * F0 * EPS0 * SUB_EPS * tand
    csx.AddMaterial("sub", epsilon=SUB_EPS, kappa=kappa).AddBox(
        [x0, y0, 0.0], [x1, y1, SUB_H], priority=1)

    def sheet(name):
        if pec:
            return csx.AddMetal(name)
        return csx.AddConductingSheet(name, conductivity=5.8e7,
                                      thickness=35e-6)

    gnd, top = sheet("gnd"), sheet("top")
    gnd.AddLinPoly(_rect(x0, x1, y0, y1), "z", 0.0, 0, priority=10)
    if solid:
        top.AddLinPoly(_rect(STRIP_X[0], STRIP_X[1], -0.5 * W, 0.5 * W),
                       "z", SUB_H, 0, priority=10)
    else:
        top.AddLinPoly(_rect(STRIP_X[0], X_GAP, -0.5 * W, 0.5 * W),
                       "z", SUB_H, 0, priority=10)
        top.AddLinPoly(_rect(X_GAP + gap, STRIP_X[1], -0.5 * W, 0.5 * W),
                       "z", SUB_H, 0, priority=10)

    # The element: LEtype=1 is the SERIES topology, and `caps` gives the
    # box a PEC face at each end. The box is flat in z, on the plane of the
    # strip, and its current goes along x. An empty `comp` keeps the gap
    # open, or `short` gives it a box of metal: those are the controls.
    box = ([X_GAP, -0.5 * W, SUB_H], [X_GAP + gap, 0.5 * W, SUB_H])
    if comp:
        csx.AddLumpedElement("le", ny="x", caps=True, LEtype=1,
                             **comp).AddBox(*box, priority=15)
    elif short and not solid:
        csx.AddMetal("gapshort").AddBox(*box, priority=15)

    ports = [fdtd.AddMSLPort(
        i + 1, top, [PORT_X[i], -0.5 * W, SUB_H],
        [PORT_X[i] + sign * PORT_LEN, 0.5 * W, 0.0], "x", "z",
        excite=-1 if i == 0 else 0, FeedShift=RES,
        MeasPlaneShift=0.5 * PORT_LEN, Feed_R=Z0, priority=20)
        for i, sign in enumerate((1, -1))]

    # The E-field probes. A probe READS the field. It changes no cell and
    # it adds no mesh line. Thus the line count below is the same with the
    # probes and without them.
    if probes:
        for name, pt in _probe_pts(air, pml_step):
            csx.AddProbe("fp_" + name, 2).AddPoint(pt)

    print("mesh: %d x %d x %d lines"
          % tuple(grid.GetQtyLines(a) for a in "xyz"), flush=True)
    return fdtd, ports


def _envelope(t, u):
    """Give (the segments, the turn in ns, the growth in 1/ns)."""
    nseg = min(SEGMENTS, len(u) // 10)
    idx = [i for i in np.array_split(np.arange(len(u)), nseg) if len(i)]
    seg_t = np.array([t[i].mean() for i in idx])
    seg_u = np.array([u[i].max() for i in idx])
    ok = seg_u > 0
    seg_t, seg_u = seg_t[ok], seg_u[ok]
    j = int(np.argmin(seg_u))
    slope = (0.0 if j >= len(seg_t) - 3 else
             float(np.polyfit(seg_t[j:], np.log(seg_u[j:]), 1)[0]))
    return (seg_t, seg_u), float(seg_t[j]), slope


def envelope(path):
    """Give (t, |u|, the segments, the turn in ns, the growth in 1/ns).

    openEMS uses '%' as the comment mark of a port file, and it SUBSAMPLES
    the data. Thus column 0, the time, is the only x axis. The envelope
    goes into `SEGMENTS` segments, and the smallest of them is the turn of
    the trace. The slope of a line through the log of the segments after
    the turn is the growth.
    """
    fn = sorted(glob.glob(os.path.join(path, "port_ut_*")))[0]
    a = np.loadtxt(fn, comments=("%", "#"))
    t, u = a[:, 0] * 1e9, np.abs(a[:, 1])
    return (t, u) + _envelope(t, u)


def late_growth(t, u):
    """Give the growth of the LAST `LATE_FRAC` of the trace, in 1/ns.

    The fit is on the same part of the window for all runs. Thus you can
    compare two runs. A trace that decreases gives a value near zero or
    below it.
    """
    m = (t > t[-1] * (1.0 - LATE_FRAC)) & (u > 0)
    if m.sum() < 10:
        return 0.0
    return float(np.polyfit(t[m], np.log(u[m]), 1)[0])


def path_of(case, factor):
    """Give the directory of one run."""
    return os.path.join(HERE, "out_growth_%s_f%g" % (case, factor))


def analyse(case, factor, time_ns=TIME_NS):
    """Give the numbers of a run when its data is on the disk."""
    path = path_of(case, factor)
    t, u, seg, turn, _ = envelope(path)
    slope = late_growth(t, u)
    peak, end = u.max(), u[-len(u) // 10:].max()
    print("  %s: turn %.2f ns, growth %+.4f /ns (e-fold %s), end/peak %.3g"
          % (case, turn, slope,
             "%.2f ns" % (1.0 / slope) if slope > 0 else "none", end / peak),
          flush=True)
    return dict(case=case, factor=factor, time_ns=time_ns, t=t, u=u, seg=seg,
                turn=turn, slope=slope, peak=peak, end=end, path=path)


def run(case, factor, time_ns=TIME_NS):
    """Run one case and give its numbers."""
    path = path_of(case, factor)
    shutil.rmtree(path, ignore_errors=True)
    # Windows can keep the directory for a moment after rmtree, thus
    # `exist_ok`. openEMS Run(cleanup=True) removes the previous files.
    os.makedirs(path, exist_ok=True)
    print("\n=== %s at a timestep factor of %g, over %g ns ==="
          % (case, factor, time_ns), flush=True)
    fdtd = build(factor=factor, time_ns=time_ns, **ELEMENTS[case])[0]
    fdtd.Run(path, cleanup=True, engine="multithreaded", numThreads=4,
             verbose=1)
    return analyse(case, factor, time_ns)


def probe_rows(case, factor):
    """Give the numbers of each field probe of a run that is on the disk."""
    path = path_of(case, factor)
    kw = ELEMENTS[case]
    rows = []
    for name, pt in _probe_pts(kw.get("air", AIR),
                               kw.get("pml_step", PML_STEP)):
        fn = os.path.join(path, "fp_" + name)
        if not os.path.isfile(fn):
            continue
        a = np.loadtxt(fn, comments=("%", "#"))
        t = a[:, 0] * 1e9
        e = np.linalg.norm(a[:, 1:4], axis=1)
        rows.append(dict(name=name, pt=pt, t=t, e=e, peak=e.max(),
                         late=e[-len(e) // 10:].max(),
                         slope=late_growth(t, e), turn=_envelope(t, e)[1]))
    return rows


def probes(case, factor=1.0):
    """Give the growth and the late level of each field probe of a run.

    The probe files have the time and the 3 components of E. The table
    gives the magnitude: its peak, its level in the last tenth of the
    window, the growth of the last `LATE_FRAC` and the turn. **The point
    with the largest late level is where the mode is.**
    """
    rows = probe_rows(case, factor)
    print("\n%s at a factor of %g: the field probes" % (case, factor),
          flush=True)
    print("%-11s%22s%11s%11s%11s%11s%9s"
          % ("probe", "point (mm)", "peak V/m", "late V/m", "late/peak",
             "growth/ns", "turn ns"), flush=True)
    for r in rows:
        print("%-11s%22s%11.3g%11.3g%11.3g%+11.4f%9.2f"
              % (r["name"], "%.1f, %.1f, %.1f" % tuple(r["pt"]), r["peak"],
                 r["late"], r["late"] / r["peak"], r["slope"], r["turn"]),
              flush=True)
    if rows:
        top = max(rows, key=lambda r: r["late"])
        gap = [r for r in rows if r["name"] == "gap_air"]
        ref = gap[0]["late"] if gap else top["late"]
        print("the largest late level is at %s, and it is %.3g times the "
              "level over the gap" % (top["name"], top["late"] / max(ref,
                                                                     1e-300)),
              flush=True)
    return rows


def sparams(case, factor, freq=(1e9, 3e9, 6e9)):
    """Give |S11| and |S21| of a run that is on the disk.

    The function makes the model again, and it gives the port objects to
    `CalcPort`, which reads the files of the run. Thus it costs no FDTD run.
    A passive board cannot give back more power than it gets. Thus
    |S11|^2 + |S21|^2 must be 1 or less.
    """
    path = path_of(case, factor)
    # Keep `fdtd` in a name until the end, because it owns the CSX
    # structure of the ports. If the structure of a port is gone, the
    # interpreter stops with a crash in `ReadUIData`.
    fdtd, ports = build(**ELEMENTS[case])
    f = np.array(freq)
    for p in ports:
        p.CalcPort(path, f, ref_impedance=Z0)
    s11 = ports[0].uf_ref / ports[0].uf_inc
    s21 = ports[1].uf_ref / ports[0].uf_inc
    # Each print after CalcPort must have `flush`. The process can end
    # without a flush of the buffer of python. A line in the buffer is then
    # gone.
    print("\n%s at a factor of %g: the S-matrix of the run on the disk"
          % (case, factor), flush=True)
    print("%9s%10s%10s%12s" % ("f/GHz", "|S11|", "|S21|", "sum |S|^2"),
          flush=True)
    for i, fi in enumerate(f):
        print("%9.2f%10.3g%10.3g%12.3g"
              % (fi / 1e9, abs(s11[i]), abs(s21[i]),
                 abs(s11[i]) ** 2 + abs(s21[i]) ** 2), flush=True)
    return s11, s21


def picture(rows, out):
    """Make the plot of the traces of the runs, or tell why it cannot."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("no matplotlib: the picture is not drawn")
        return None
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for r in rows:
        lab = "%s, factor %g" % (r["case"], r["factor"])
        if r["slope"] > 0:
            lab += " (e-fold %.2f ns)" % (1.0 / r["slope"])
        # The ENVELOPE has the line and the label. |u| goes to zero at each
        # null of the trace. Thus the raw curve fills the picture with
        # spikes, and its zero values move the axis to 1e-30. A NaN keeps
        # such a point out of the line and out of the scale.
        line, = ax.semilogy(r["seg"][0], r["seg"][1], lw=1.4, label=lab)
        ax.semilogy(r["t"], np.where(r["u"] > 0, r["u"], np.nan),
                    lw=0.4, alpha=0.25, color=line.get_color())
    ax.set_xlabel("time (ns)")
    ax.set_ylabel("|u| of port 1 (V)")
    ax.set_title("openEMS: the port voltage of a microstrip with a gap "
                 "in the track")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    return out


def main(case=None, factor=None, time_ns=None):
    # "probes" gives the table of the field probes of one run that is on
    # the disk, and it starts no FDTD run.
    if case == "probes":
        probes(factor or "none", float(time_ns or 1.0))
        return
    # "table" gives the table and the picture again from the data of the
    # runs that are on the disk. It starts no FDTD run.
    runs = list(RUNS) if case in (None, "table") else [
        (case, float(factor or 0.5), float(time_ns or TIME_NS))]
    rows = [(analyse if case == "table" else run)(c, f, t)
            for c, f, t in runs]
    rep = os.path.join(HERE, "out_growth_report")
    os.makedirs(rep, exist_ok=True)
    print("\n%-10s%8s%9s%9s%10s%11s%11s"
          % ("case", "factor", "window", "turn", "growth", "e-fold",
             "end/peak"), flush=True)
    for r in rows:
        print("%-10s%8g%6g ns%7.2f ns%7.4f/ns%8s%11.3g"
              % (r["case"], r["factor"], r["time_ns"], r["turn"], r["slope"],
                 "%.2f ns" % (1.0 / r["slope"]) if r["slope"] > 0 else "none",
                 r["end"] / r["peak"]), flush=True)
        src = sorted(glob.glob(os.path.join(r["path"], "port_ut_*")))[0]
        shutil.copy(src, os.path.join(
            rep, "port_ut_%s_f%g" % (r["case"], r["factor"])))
    print("\"turn\" is the smallest point of the envelope, and \"growth\" "
          "is the fit of the last %g%% of the window, which is the "
          "asymptotic rate." % (100 * LATE_FRAC), flush=True)
    picture(rows, os.path.join(rep, "growth.png"))
    # The S-matrix of the first run and of the last one, from the data on
    # the disk. A run that goes after the turn gives back more power than
    # it gets, and the engine writes no message for it.
    seen = []
    for c, fa, _ in (runs[0], runs[-1]):
        if (c, fa) not in seen:
            seen.append((c, fa))
            sparams(c, fa)
    print("\nthe traces and the picture are in %s" % rep, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:])
