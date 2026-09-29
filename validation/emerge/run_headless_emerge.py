"""Run `rig_headless` with EMerge.

The rig in `validation/common/rig_headless.py` holds the board, the theory and
the checks, and its docstring tells what it tests. The two solvers run the
same rig. This file selects EMerge, and its results go into
`validation/emerge/out_*`.

Start it with the python of KiCad 10. It uses pcbnew, and it starts
the solver itself:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_headless_emerge.py [coarse|medium|fine] [msl|lumped]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))

import rigsolve  # noqa: E402

rigsolve.use("emerge", HERE)

import rig_headless  # noqa: E402

if __name__ == "__main__":
    rig_headless.main(*(sys.argv[1:] or ["coarse"]))
