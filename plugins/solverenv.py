"""The location of openEMS, the Python that must run the solver, and the
timestep rule of a lumped inductor.

The two sides of the process split import this module: the pcbnew plugin
and the independent runner. Thus it must not import pcbnew, wx or numpy.
Whatever the two sides must agree about goes here.
"""
import math
import os

# The safety margin of the timestep rule for a lumped inductor. The
# largest stable factor follows 1/sqrt(L[nH]), and the bare law has no
# margin at all on the worst geometry: a board of 6.4 mm with 100 nH is
# stable at 0.09 and the bare law gives 0.10.
#
# **0.5 comes from the mesh that keeps ONE cell in series.** That mesh
# moved the boundary of the worst geometry, a board of 6.4 mm: with 0.7
# it kept 1.3x at 100 nH and 1.6x at 10 nH, where the mesh of two cells
# in series gave 2.3x or more on every geometry of the matrix of
# `validation/run_stability.py`, and that file asks for 1.35x. With 0.5
# the same geometry keeps 1.8x and 2.2x, and every other one 3.2x or
# more.
#
# It costs 1/0.5 = 2 times more timesteps on a board that holds a 1 nH
# inductor and 20 times on a board with 100 nH, and nothing on a board
# whose largest inductance is 0.25 nH or less. The dialog shows that
# number before the run. `validation/run_stability.py` measures the
# margin again.
LE_STAB_MARGIN = 0.5


def time_step_factor(l_nh):
    """Give the portion of the Courant timestep that keeps a run stable.

    `l_nh` is the largest inductance of the model, in nH. A lumped
    inductor makes the FDTD unstable at the full Courant step, and the
    largest stable factor follows `LE_STAB_MARGIN / sqrt(L[nH])`. The
    factor stops at 1.0, because a board needs no step smaller than the
    Courant step: an inductance of 0.25 nH or less costs nothing.

    The runner sets the timestep with this value and it divides the step
    limit by the same value for the same simulated time. Thus 1/factor
    is the RUN TIME that the inductor costs, and the dialog shows that
    number before the run.
    """
    return min(1.0, LE_STAB_MARGIN / l_nh ** 0.5) if l_nh > 0 else 1.0


# ------------------------------------------------ the mesh and the domain
C0 = 299792458.0
# The step of the mesh is the wavelength in the fastest dielectric,
# divided by this number for each preset.
RES_DIV = {"coarse": 10.0, "medium": 20.0, "fine": 40.0, "ultrafine": 80.0}
# The cells of each PML band. openEMS grades the conductivity over the
# cells of the band, thus the band is 8 cells whatever its cell size and
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

    **The depth of the band, and not the clear air alone, is what keeps
    a board away from the absorber.** A board whose mesh holds a column
    of refined cells makes a mode that grows late in a run when the
    outer wall of the domain stands near the copper, and the air and the
    depth of the band trade one for one against it. The band was 8 cells
    of `margin_mm`/8 before 2026-09-20, thus the depth was the margin
    itself and the default of 4 mm put the wall 8 mm over the copper,
    which is 3.4 cells at the coarse preset. A cell of `res` gives 8
    cells of depth at every preset, and the standoff is then 9.7 cells
    at coarse, 11.4 at medium and 14.8 at fine.

    It costs NO cell: a band of 8 cells is 8 cells whatever its size.
    The domain becomes larger in millimetres, thus `board_reader.extract`
    must inflate the region by the margin PLUS this depth, and the copper
    that crosses the edge then still reaches the outer wall.

    **The cell is a hair under `res`.** `SmoothMeshLines` divides an
    interval that is LARGER than the step that it gets, and the mesh
    lines are rounded to 1e-9 mm. An interval of exactly `res` can
    therefore land 1e-9 over it and become TWO cells, and the band then
    holds 16 uneven cells in the place of 8. The shrink is 1e-6 of the
    depth, which is 2e-5 mm at the coarse preset.
    """
    return PML_CELLS * res * (1.0 - 1e-6)


def openems_dirs():
    """Give the possible openEMS install directories, the best one first.

    A directory in the list can be absent from the disk.
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
    Python of KiCad. openEMS v0.37 and later make this necessary: they
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
