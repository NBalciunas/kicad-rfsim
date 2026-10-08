"""Run `rig_states` with openEMS.

The rig in `validation/common/rig_states.py` holds the board, the theory
and the checks, and its docstring tells what it tests. The two solvers run the
same rig. This file selects openEMS, and its results go into
`validation/openems/out_*`.

Start it with the python of KiCad 10. It uses pcbnew, and it starts
the solver itself:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_states_openems.py [mesh]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))

import rigsolve  # noqa: E402

rigsolve.use("openems", HERE)

import rig_states  # noqa: E402

if __name__ == "__main__":
    rig_states.main(*(sys.argv[1:] or ["coarse"]))
