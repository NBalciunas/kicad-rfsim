"""Run a model through the solver of the rig that is running.

The rigs of `validation/common/rig_*.py` test the same things in the two
solvers. A small file in `validation/openems/` or `validation/emerge/`
selects the solver and the folder of the results with `use()`, and it then
starts the rig. The rig calls `run()` for each board. Thus a rig holds the
board, the theory and the checks one time, and the two solvers get the same
tests.

Only the runner and its interpreter are different
(`solverenv.SOLVER_INFO`). `openems_runner.py` runs in the venv of openEMS,
and `emerge_runner.py` in the venv of EMerge. The two write the same
`results.sNp`. The line ports of both write `lines.json`; a run with no line
port writes none, thus `lines()` gives None.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # the repository
# RFSIM_PLUGINS names the plugins directory to measure, thus a rig can run
# against a copy of the code that holds a different rule.
PLUGINS = os.environ.get("RFSIM_PLUGINS") or os.path.join(ROOT, "plugins")
sys.path.insert(0, PLUGINS)

import solverenv  # noqa: E402

RUNNERS = {k: v["runner"] for k, v in solverenv.SOLVER_INFO.items()}
# The solver of the rig, and the folder that gets its `out_*`. `use()`
# sets the two.
SOLVER = "openems"
OUT = os.path.join(os.path.dirname(HERE), "openems")
# The frequencies that EMerge solves when a rig gives none. The rigs read
# the frequency of a notch, thus they get more points than a preset gives.
FEM_POINTS = 31
# The lines of the log that a rig shows.
KEEP = ("lumped", "ERROR", "WARNING", "timesteps")


def use(solver, out):
    """Select the solver and the folder of the results."""
    global SOLVER, OUT
    if solver not in RUNNERS:
        raise SystemExit("no solver %r: use one of %s"
                         % (solver, ", ".join(RUNNERS)))
    SOLVER, OUT = solver, out


def out(name):
    """Give the path of the folder `name` of this solver."""
    return os.path.join(OUT, name)


def python():
    """Give the interpreter of the solver.

    openEMS v0.0.36 can run in the Python of KiCad, thus openEMS falls back
    to it. The other solvers need their own venv (the README).
    """
    py = solverenv.SOLVER_INFO[SOLVER]["python"]()
    if py:
        return py
    if SOLVER == "openems":
        return sys.executable
    raise SystemExit("no Python for %s: make its venv as the README tells"
                     % solverenv.SOLVER_INFO[SOLVER]["name"])


def run(model, outdir, tag, runner=None, keep=KEEP):
    """Write model.json into `outdir`, run the solver, and give its log.

    `runner` is the path of a different openEMS runner, for a rig that
    measures a changed rule (`rig_feature`). The log is the text of stdout
    and stderr together. The lines that hold a word of `keep` go to the
    console.
    """
    if runner and SOLVER != "openems":
        raise SystemExit("a changed runner is for openEMS only")
    os.makedirs(outdir, exist_ok=True)
    s = model["settings"]
    s["solver"] = SOLVER
    if SOLVER == "emerge":
        s.setdefault("fem_points", FEM_POINTS)
    path = os.path.join(outdir, "model.json")
    with open(path, "w") as fh:
        json.dump(model, fh, indent=1)
    script = runner or os.path.join(PLUGINS, RUNNERS[SOLVER])
    log = subprocess.run([python(), script, path, outdir],
                         capture_output=True)
    # Read the BYTES. openEMS writes characters that are not UTF-8 on this
    # console, and `text=True` then gives a truncated log.
    text = (log.stdout + log.stderr).decode("utf-8", "replace")
    if log.returncode != 0:
        print(text[-3000:])
        raise SystemExit("the %s run of %s stopped with an error"
                         % (SOLVER, tag))
    for line in text.splitlines():
        if any(k in line for k in keep):
            print("  " + line.strip())
    return text


def lines(outdir):
    """Give the line data of lines.json, or None when the run wrote none."""
    path = os.path.join(outdir, "lines.json")
    if not os.path.isfile(path):
        return None
    with open(path) as fh:
        return json.load(fh)
