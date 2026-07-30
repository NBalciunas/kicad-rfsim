"""Where openEMS lives, and which Python should run the solver.

Imported from both sides of the process split (the pcbnew plugin and the
standalone runner), so it must stay free of pcbnew, wx and numpy.
"""
import os


def openems_dirs():
    """Candidate openEMS install dirs, best first. May not exist."""
    here = os.path.dirname(os.path.abspath(__file__))
    return [d for d in (
        os.environ.get("OPENEMS_PATH"),
        # <kicad>/3rdparty/openEMS when the plugin sits in .../plugins/rfsim
        os.path.abspath(os.path.join(here, "..", "..", "openEMS")),
        r"C:\openEMS",
    ) if d]


def solver_python():
    """Interpreter that should run runner.py, or None to keep the caller's.

    runner.py imports only numpy/h5py/CSXCAD/openEMS — never pcbnew or wx —
    so it can run under a different Python than KiCad's. That is *required*
    for openEMS >= v0.37, which ships cp313/cp314 wheels only while KiCad 8,
    9 and 10 all bundle Python 3.11. Search order:

      1. $RFSIM_PYTHON
      2. a `venv` beside the openEMS install (what the README sets up)
      3. None -> caller keeps its own interpreter, which is right for
         openEMS v0.0.36 (cp311 wheel, but no lumped inductors)
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
