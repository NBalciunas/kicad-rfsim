"""The location of openEMS, the Python that must run the solver, and the
timestep rule of a lumped inductor.

Two processes import this module: the pcbnew plugin, and the runner in its
own Python. Thus it must not import pcbnew, wx or numpy. Each value that
the two processes must agree about goes here.
"""
import math
import os

# The safety margin of the timestep rule for a lumped inductor. The largest
# stable factor follows 1/sqrt(L[nH]). The bare law has no margin on the
# worst geometry: a board of 6.4 mm with 100 nH is stable at 0.09, and the
# bare law gives 0.10.
#
# **0.5 comes from the mesh that keeps ONE cell in series.** That mesh
# moved the boundary of the worst geometry, a board of 6.4 mm. With 0.7,
# that board kept 1.3x at 100 nH and 1.6x at 10 nH. The mesh of two cells
# in series gave 2.3x or more on each geometry of the matrix of
# `validation/run_stability.py`, and that file sets a limit of 1.35x. With
# 0.5, the same geometry keeps 1.8x and 2.2x, and each other geometry keeps
# 3.2x or more.
#
# It costs 1/0.5 = 2 times more timesteps on a board with a 1 nH inductor,
# and 20 times on a board with 100 nH. It costs nothing on a board with no
# inductance larger than 0.25 nH. The dialog shows that number before the
# run. `validation/run_stability.py` measures the margin again.
LE_STAB_MARGIN = 0.5


def time_step_factor(l_nh):
    """Give the part of the Courant timestep that keeps a run stable.

    `l_nh` is the largest inductance of the model, in nH. A lumped inductor
    makes the FDTD not stable at the full Courant step. The largest stable
    factor follows `LE_STAB_MARGIN / sqrt(L[nH])`. The factor stops at 1.0,
    because a step smaller than the Courant step is not necessary on a
    board with no inductor. An inductance of 0.25 nH or less costs nothing.

    The runner sets the timestep with this value. It divides the step limit
    by the same value for the same simulated time. Thus 1/factor is the RUN
    TIME that the inductor costs, and the dialog shows that number before
    the run.
    """
    return min(1.0, LE_STAB_MARGIN / l_nh ** 0.5) if l_nh > 0 else 1.0


# The constant of the timestep rule of a SERIES branch that has a
# resistance. openEMS uses `Operator_Ext_LumpedRLC` for `LEtype` = 1. That
# extension integrates the current of the branch, and a large resistance
# makes the integrator diverge. The largest stable factor follows `k /
# sqrt(R[ohm])`. `validation/run_cosim.py` measured k on 2026-09-22 at the
# full Courant step:
#
#   | geometry             | 500 ohm | 1000 ohm | 2000 ohm |  k   |
#   |----------------------|---------|----------|----------|------|
#   | the reference 1.53mm |  0.50   |   0.35   |   0.25   | 11.1 |
#   | a board of 3.2 mm    |  0.35   |   0.25   |     -    |  7.8 |
#   | a board of 6.4 mm    |  0.35   |   0.25   |     -    |  7.8 |
#
# **The inductance has no effect on it**: 0.5 nH and 2 nH of ESL gave the
# same boundary at each resistance. A THICK board is the worst one, as it
# is for the inductor rule. Thus 7.8 is the bare law.
#
# 5.8 gives the worst geometry a margin of 1.35. That is the margin that
# `validation/run_stability.py` sets for the inductor rule, and it gives
# the reference board 1.9. The USUAL case also costs nothing. A 0603
# resistor of 50 ohm gets 0.82 here and 0.707 from its own ESL. Thus the
# inductor rule sets the factor, and the run time does not change. The rule
# starts to cost at 100 ohm (1.2 times). It prevents the divergence of a
# run at 150 ohm and more.
LE_SERIES_R = 5.8


def series_r_factor(r_ohm):
    """Give the part of the Courant timestep that a SERIES branch with this
    resistance keeps stable.

    `r_ohm` is the largest resistance in a branch with `LEtype` = 1, which
    is a branch that has more than one component. A branch with ONE
    resistance does not use that topology: refer to `runner._le_topology`.

    The factor stops at 1.0, thus a resistance of 34 ohm or less costs
    nothing.
    """
    return min(1.0, LE_SERIES_R / r_ohm ** 0.5) if r_ohm > 0 else 1.0


# A part blocks the track when its impedance is this many times the port
# impedance. S21 through the part goes as 2*z0/|Z|, thus 200 times is about
# -40 dB. **Such a part is an OPEN at all frequencies of the sweep. The
# grid must not hold it.**
#
# A lumped element of openEMS is not the ideal part that its value gives.
# The series topology also cuts the in-plane displacement current of the
# gap. Thus a 90 uH part in the grid read S21 about 5 dB BELOW the bare
# gap. That run used 31 minutes at 1/600 of the Courant step. An ideal
# 90 uH at 1 GHz has 0.4% of the admittance of that gap. Thus the bare gap
# is the correct answer, and the solver gives it in seconds. The dialog and
# the runner read this rule.
OPEN_Z_RATIO = 200.0


def smallest_z(r, l, c, f_start, f_stop):
    """Give the smallest |Z| of a series R-L-C branch in the sweep.

    The value is accurate, and it uses no samples. The reactance wL -
    1/(wC) only INCREASES with the frequency. Thus its magnitude has its
    smallest value at an edge of the sweep, or it is 0 where the series
    resonance is in the sweep. A sampled sweep can miss a sharp resonance.
    It can then call a part an open when the part is a short at one
    frequency.

    A component that is 0 or None is not in the branch. No L gives no wL,
    and no C gives no series capacitance (and not an infinite one).
    """
    def x(w):
        return (w * l if l else 0.0) - (1.0 / (w * c) if c else 0.0)
    xa, xb = x(2 * math.pi * f_start), x(2 * math.pi * f_stop)
    x_min = 0.0 if xa <= 0.0 <= xb else min(abs(xa), abs(xb))
    return math.hypot(r or 0.0, x_min)


def is_open(r, l, c, f_start, f_stop, z0):
    """Give True when a series R-L-C branch is an open at all frequencies
    of the sweep.

    A branch with no component is not an element, thus it is not an open.
    The caller does not include such a part, for a different cause.
    """
    if not (r or l or c):
        return False
    return smallest_z(r, l, c, f_start, f_stop) >= OPEN_Z_RATIO * z0


# A parasitic of the body goes into the element only where it changes |Z|
# of the part by MORE than this part of |Z|. One frequency of the sweep is
# sufficient. The owner selected the 2%. At z0 the series path of openEMS
# costs 0.09 dB, and 2% of |Z| is about 0.17 dB.
#
# **The rule is for the path and not for the parasitic.** A resistor or a
# capacitor with a body is two components or more. Thus it uses the series
# path of openEMS. That path changes S21 much more than the body does:
# 1.67 dB at 200 ohm and 5.47 dB at 1 kohm. The ESL of an 0603 body changes
# S21 by 0.017 dB and 0.0013 dB (`validation/run_cosim.py topology`). The
# path also gets a timestep factor of 0.41 and 0.18. The same resistor with
# no body uses the classic path, with no cost.
PARASITIC_MIN = 0.02


def parasitic_effect(comp, own, f_start, f_stop):
    """Give the largest change of |Z| in the sweep that the body causes.

    `comp` has the R, the L and the C of the branch in SI. `own` is the key
    of the part itself: "R" or "C". The value is a part of |Z| of the part
    ONLY, thus 0.02 is 2%.

    The value is accurate, and it uses no samples. The ratio of the two |Z|
    has its largest and smallest values at the edges of the sweep. For a
    capacitor, it also has one at ONE point in the sweep, because its square
    is a parabola in w^2. That point is near the series resonance of C and
    its ESL, where |Z| decreases to the ESR. Thus a capacitor keeps its ESL
    when that resonance is in the sweep. A sampled sweep can miss that
    resonance.
    """
    r = comp.get("R") or 0.0
    l = comp.get("L") or 0.0
    c = comp.get("C") or 0.0

    def ratio(w):
        x = w * l - (1.0 / (w * c) if c else 0.0)
        alone = comp[own] if own == "R" else 1.0 / (w * c)
        return math.hypot(r, x) / alone

    ws = [2 * math.pi * f_start, 2 * math.pi * f_stop]
    if own == "C" and l:
        # |Z|^2 (wC)^2 = (w^2 LC - 1)^2 + (R wC)^2 is a parabola in w^2.
        u = (2 * l * c - (r * c) ** 2) / (2 * (l * c) ** 2)
        if ws[0] ** 2 < u < ws[1] ** 2:
            ws.append(math.sqrt(u))
    return max(abs(ratio(w) - 1.0) for w in ws)


def body_is_idle(comp, own, f_start, f_stop):
    """Give True when the body of a part can stay out of its element.

    That is a resistor or a capacitor with a body that changes |Z| by
    `PARASITIC_MIN` or less in all the sweep. The part is then ONE
    component, thus it uses the classic path of openEMS. **All the body
    goes out, or all the body stays**: a part that keeps one parasitic uses
    the series path. The other parasitics then cost no more. An inductor
    always uses the series path, thus its DCR always stays.
    """
    if own not in ("R", "C") or len(comp) < 2:
        return False
    return parasitic_effect(comp, own, f_start, f_stop) <= PARASITIC_MIN


# ------------------------------------------------ the mesh and the domain
C0 = 299792458.0
# The step of the mesh is the wavelength in the fastest dielectric,
# divided by this number for each preset.
RES_DIV = {"coarse": 10.0, "medium": 20.0, "fine": 40.0, "ultrafine": 80.0}
# The cells of each PML band. openEMS grades the conductivity across the
# cells of the band. Thus the band is 8 cells for all cell dimensions, and
# it costs 8 cells on each of the 6 faces.
PML_CELLS = 8


def mesh_res(f_stop, eps_max, mesh):
    """Give the step of the mesh in mm.

    `f_stop` is the top of the sweep in Hz, `eps_max` is the largest
    permittivity of the stackup and `mesh` is a key of `RES_DIV`. The
    wavelength is the one in the fastest dielectric, thus the step holds
    everywhere on the board.
    """
    return C0 / f_stop / math.sqrt(eps_max) * 1e3 / RES_DIV[mesh]


def pml_depth(res):
    """Give the depth of one PML band in mm: `PML_CELLS` cells of `res`.

    **The depth of the band, and not only the clear air, keeps a board away
    from the absorber.** Some boards have a mesh with a column of refined
    cells. Such a board makes a mode that increases late in a run. This
    occurs when the outer wall of the domain is near the copper. One mm of
    air and one mm of depth of the band have the same effect against it.

    Before 2026-09-20, the band was 8 cells of `margin_mm`/8. Thus the
    depth was the margin, and the default of 4 mm put the wall 8 mm above
    the copper. That is 3.4 cells at the coarse preset. A cell of `res`
    gives 8 cells of depth at each preset. The standoff is then 9.7 cells
    at coarse, 11.4 at medium and 14.8 at fine.

    It costs NO cell: a band of 8 cells is 8 cells for all dimensions. The
    domain becomes larger in millimetres. Thus `board_reader.extract` must
    make the region larger by the margin PLUS this depth. The copper that
    goes across the edge then continues to the outer wall.

    **The cell is a very small distance less than `res`.**
    `SmoothMeshLines` divides an interval that is LARGER than the step that
    it gets. `build()` rounds the mesh lines to 1e-9 mm. Thus an interval
    of `res` can be 1e-9 larger than `res` and become TWO cells. The band
    then has 16 cells that are not equal, and not 8. The decrease is
    1e-6 of the depth, which is 2e-5 mm at the coarse preset.
    """
    return PML_CELLS * res * (1.0 - 1e-6)


def openems_dirs():
    """Give the possible openEMS install directories, the best one first.

    A directory in the list can be missing from the disk.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return [d for d in (
        os.environ.get("OPENEMS_PATH"),
        # <kicad>/3rdparty/openEMS if the plugin is in .../plugins/rfsim
        os.path.abspath(os.path.join(here, "..", "..", "openEMS")),
        r"C:\openEMS",
    ) if d]


def solver_python():
    """Give the interpreter that must run runner.py, or give None.

    runner.py imports only numpy, h5py, CSXCAD and openEMS. It does not
    import pcbnew or wx. Thus it can run in a different Python than the
    Python of KiCad. openEMS v0.37 and after make this necessary: they
    supply cp313 and cp314 wheels only, but KiCad 8, 9 and 10 all contain
    Python 3.11. The function looks in this sequence:

      1. $RFSIM_PYTHON
      2. a `venv` near the openEMS installation (the README makes it)
      3. None. Then the caller keeps its own interpreter. This is correct
         for openEMS v0.0.36, which has a cp311 wheel but no lumped
         inductors.
    """
    cfg = os.environ.get("RFSIM_PYTHON")
    if cfg:
        return cfg
    for d in openems_dirs():
        for sub in (("venv", "Scripts", "python.exe"),
                    ("venv", "bin", "python")):
            cand = os.path.join(d, *sub)
            if os.path.isfile(cand):
                return cand
    return None
