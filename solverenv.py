"""The location of openEMS, and the Python that must run the solver.

The two sides of the process split import this module: the pcbnew plugin
and the independent runner. Thus it must not import pcbnew, wx or numpy.
"""
import os


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
