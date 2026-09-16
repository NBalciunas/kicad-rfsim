"""B30: what makes the FAST growth of a lumped inductor, on ONE variable.

A lumped inductor on the reference board grows in two different ways, and
an earlier measurement separated them:

  - a **FAST** growth with an e-fold near 0.4 ns, which the runner
    refuses. `_feature_lines` reads the gap between the two pads of a
    part as a narrow copper feature and puts a line at the middle of it.
    That line falls INSIDE the box of the element, thus the element
    covers 2 cells in series where it could cover 1;
  - a **SLOW** mode with an e-fold near 6 ns, which no timestep corrects
    and which stays after the fast one is gone.

**The rule behind the fast half is not pinned.** A matrix that changed
the BOX said that 4 cells in series are as good as 1, thus "more cells in
series is worse" is not the rule. Every box variant moves more than one
thing: the length of the box sets `tol` (`_mesh` takes 0.25 x min(box)),
thus it moves the mesh of the WHOLE board, and it moves the physical gap
as well.

**This file moves ONE thing.** It holds the box, `tol` and the board, and
it changes the count of mesh lines INSIDE the box alone:

    N cells in series  <->  N-1 lines inside the box, equally spaced

A copy of `plugins/` carries that change, thus the code that ships is not
touched. The copy reads two variables of the environment:

    RFSIM_B30_CELLS   force exactly N cells in series (0: leave the mesh)
    RFSIM_B30_CAPS    "0" gives the element `caps=False`

Two stages, and each one gives the growth of the trace:

  1. `ladder`  - the count of cells in series: 1, 2, 3, 4 and 6, at a
     FIXED box and `tol`. This is the measurement that names the rule.
  2. `matrix`  - the three rows that an earlier session did not run:
     er 2.2, er 10.2 and caps=False, each one with the line and without
     it.

Run it with the python of KiCad 10 or with the python of the solver. It
needs `out_rlc_L1_coarse/model.json`, thus run `run_rlc.py coarse` first:

    C:\\openEMS\\venv\\Scripts\\python.exe run_b30.py [ladder|matrix|all]

Each run is short (about 4 ns of simulated time), because the fast growth
reaches its turn inside 2 ns. The slow mode needs tens of nanoseconds and
this file does not measure it.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins"))
sys.path.insert(0, HERE)

import runner  # noqa: E402
import run_stability as rs  # noqa: E402

# The copy of `plugins/` that carries the hook. It goes beside the
# output, thus a run does not touch the code that ships.
COPY = os.path.join(HERE, "out_b30_plugins")

# **The TURN of the trace names the fast mode, and the e-fold does not.**
# A board with the fast growth turns at about 2 ns. A board without it
# decays to the end of the window and its turn lands near that end. Thus
# a turn under this limit is the fast mode.
#
# Do NOT read the growth column of an ACCEPTED row as the slow mode.
# `envelope` fits the segments AFTER the turn, thus a turn that lands
# near the end of the window leaves 3 or 4 segments for the fit and the
# slope that comes out is noise. The slow mode needs a window of tens of
# nanoseconds, which `run_stability.py slow` carries.
FAST_TURN_NS = 4.0

# The steps of one run, in the units of `run_stability.STEPS`: `build()`
# raises the count by 1/factor, thus this is the SIMULATED time and not
# the work. **6000 is too few, and the reason is not the physics.**
# openEMS writes the port trace every quarter of the Nyquist rate, thus
# 6000 steps at the factor of a 10 nH inductor gives 193 rows and
# `envelope` asks for 200. 12000 gives about 8 ns and 385 rows: the fast
# growth turns at about 2 ns, thus the window holds the turn and 6
# e-folds of it, and a board with no fast growth still only DECAYS over
# that window, because the slow mode turns at about 8 ns.
STEPS = 12000

# The hook. `build()` looks up `_mesh` as a global at the moment of the
# call, thus a new global of that name replaces it.
HOOK = '''

# ---------------------------------------------------------------- B30 hook
# Appended by validation/run_b30.py. It is NOT part of the plugin.
_B30_CELLS = int(os.environ.get("RFSIM_B30_CELLS", "0"))
if _B30_CELLS:
    _b30_mesh = _mesh

    def _mesh(model, ports, res):
        """Give the mesh, then force N cells in series at every element.

        The box keeps its faces and `tol` keeps its value, thus the
        board outside the box does not move. Only the lines strictly
        inside the box change.
        """
        out = list(_b30_mesh(model, ports, res))
        for e in model.get("lumped_elements", []):
            axis = 0 if e["ny"] == "x" else 1
            lo, hi = sorted((e["start"][axis], e["stop"][axis]))
            eps = 1e-9
            keep = [v for v in out[axis] if v <= lo + eps or v >= hi - eps]
            step = (hi - lo) / _B30_CELLS
            keep += [lo + i * step for i in range(1, _B30_CELLS)]
            out[axis] = sorted(keep)
        return tuple(out)
'''


def make_copy():
    """Give a copy of `plugins/` that carries the hook."""
    src = os.path.join(os.path.dirname(HERE), "plugins")
    shutil.rmtree(COPY, ignore_errors=True)
    shutil.copytree(src, COPY,
                    ignore=shutil.ignore_patterns("__pycache__", "assets"))
    path = os.path.join(COPY, "runner.py")
    with open(path, "r", newline="") as fh:
        text = fh.read()
    # `caps=True` stands once in the file, at the lumped element.
    if text.count("caps=True") != 1:
        raise SystemExit("runner.py holds %d 'caps=True', expected 1"
                         % text.count("caps=True"))
    text = text.replace(
        "caps=True",
        'caps=(os.environ.get("RFSIM_B30_CAPS", "1") != "0")')
    # **The hook goes BEFORE the `__main__` block and not at the end of
    # the file.** `runner.py` calls `main()` from that block, thus a hook
    # under it replaces `_mesh` after the whole run is over and every row
    # of the matrix then gives the SAME number. That failure is silent:
    # the rows look like a result.
    mark = '\nif __name__ == "__main__":'
    if text.count(mark) != 1:
        raise SystemExit("runner.py holds %d '__main__' blocks, expected 1"
                         % text.count(mark))
    text = text.replace(mark, HOOK + mark)
    with open(path, "w", newline="") as fh:
        fh.write(text)
    selftest()
    return COPY


def selftest():
    """Stop unless the hook really moves the mesh. No solver run.

    A hook that does nothing gives a full matrix of equal rows, and such
    a matrix reads as a measurement. Thus the file proves the hook first.
    """
    code = (
        "import json,os,sys;import numpy as np;import runner as R\n"
        "m=json.load(open(sys.argv[1]));s=m['settings']\n"
        "res=(R.C0/s['f_stop']/np.sqrt(max(d['epsilon'] for d in "
        "m['dielectric_layers']))*1e3)/R.RES_DIV[s['mesh']]\n"
        "g=R._port_geometry(m,res);xs=R._mesh(m,g,res)[0]\n"
        "e=m['lumped_elements'][0];a=0 if e['ny']=='x' else 1\n"
        "lo,hi=sorted((e['start'][a],e['stop'][a]))\n"
        "print(len([v for v in xs if lo-1e-9<v<hi+1e-9])-1)\n")
    src = os.path.join(HERE, "out_rlc_L1_coarse", "model.json")
    py = rs.solverenv.solver_python() or sys.executable
    got = {}
    for n in (1, 4):
        env = dict(os.environ, RFSIM_B30_CELLS=str(n), PYTHONPATH=COPY)
        p = subprocess.run([py, "-c", code, src], capture_output=True,
                           env=env, cwd=COPY)
        out = p.stdout.decode("utf-8", "replace").strip().splitlines()
        got[n] = out[-1] if out else "?"
    if got[1] != "1" or got[4] != "4":
        raise SystemExit(
            "the hook does not move the mesh: RFSIM_B30_CELLS=1 gave %s "
            "cell(s) and =4 gave %s. Every row of the matrix would be "
            "equal. Look at where the hook sits in the copy of runner.py."
            % (got[1], got[4]))
    print("hook OK: 1 and 4 cells in series reach the mesh\n")


def measure(src, lnh, cells=0, caps=True, er=None, steps=None, tmp=None):
    """Run one row. Give (the cause, the turn ns, the growth 1/ns)."""
    os.environ["RFSIM_B30_CELLS"] = str(cells)
    os.environ["RFSIM_B30_CAPS"] = "1" if caps else "0"
    model = rs.variant(src, lnh * 1e-9, er=er, steps=steps or STEPS)
    factor = runner._time_step_factor(
        {"settings": {"lumped": True},
         "lumped_elements": [{"type": "L", "value": lnh * 1e-9}]})
    cause, _ = rs.solve(model, factor, tmp)
    env = rs.envelope(tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    if env is None:
        return cause, factor, float("nan"), float("nan")
    turn, slope, _ = env
    return cause, factor, turn, slope


def efold(slope):
    """Give the e-fold time of a growth, or give None for a decay."""
    return (1.0 / slope) if slope and slope > 0 else None


def row(name, cause, factor, turn, slope):
    e = efold(slope)
    print("%-34s %7.3f %9.2f %11.4f %9s  %s"
          % (name, factor, turn, slope,
             "%.2f" % e if e else "-",
             "REFUSED (%s)" % cause if cause else "accepted"))


def head(title):
    print("\n%s\n" % title)
    print("A turn near the end of the window means the trace only DECAYED.")
    print("The growth of such a row is a fit over 3 or 4 segments, thus it "
          "is noise\nand NOT the slow mode. Read the turn and the runner.\n")
    print("%-34s %7s %9s %11s %9s  %s"
          % ("row", "factor", "turn ns", "growth /ns", "e-fold", "the runner"))


def ladder_stage(src, tmp, lnh=10):
    """The count of cells in series, at a FIXED box and `tol`."""
    head("the cells in series at a FIXED box and tol, %d nH" % lnh)
    out = []
    for n in (1, 2, 3, 4, 6):
        cause, f, turn, slope = measure(src, lnh, cells=n, tmp=tmp)
        row("%d cell(s) in series" % n, cause, f, turn, slope)
        out.append((n, cause, turn))
    # The mesh that ships, for the same board and the same value.
    cause, f, turn, slope = measure(src, lnh, cells=0, tmp=tmp)
    row("the mesh that ships", cause, f, turn, slope)
    out.append(("ships", cause, turn))
    return out


def matrix_stage(src, tmp, lnh=10):
    """The three rows that the earlier session did not run."""
    head("the rows that did not run, %d nH, with the line and without it"
         % lnh)
    rows = [("a substrate of er 2.2", dict(er=2.2)),
            ("a substrate of er 10.2", dict(er=10.2)),
            ("caps=False", dict(caps=False))]
    out = []
    for name, kw in rows:
        for cells, what in ((0, "the mesh that ships"), (1, "1 cell")):
            cause, f, turn, slope = measure(src, lnh, cells=cells,
                                            tmp=tmp, **kw)
            row("%s, %s" % (name, what), cause, f, turn, slope)
            out.append((name, what, cause, slope))
    return out


def verdict(ladder):
    """Say whether the count of cells in series names the fast growth.

    A row carries the fast mode when the runner refused it for a growth,
    or when its trace turns early. Both are robust inside this window.
    """
    fast = [n for n, cause, turn in ladder
            if cause == "growth" or (turn == turn and turn < FAST_TURN_NS)]
    ok = [n for n, _, _ in ladder if n not in fast]
    print("\nthe FAST mode (refused, or a turn under %.1f ns): %s"
          % (FAST_TURN_NS, ", ".join(str(n) for n in fast) or "none"))
    print("no fast mode: %s" % (", ".join(str(n) for n in ok) or "none"))
    if fast and 1 not in fast and "ships" in fast:
        print("\nthe count of cells in series MOVES it: the mesh that ships "
              "grows fast\nand 1 cell does not. A mesh rule that keeps a "
              "lumped element on ONE cell\nin series removes the fast half "
              "of B30 on this board.")
    elif not fast:
        print("\nNO row grew fast, thus this board does not reproduce the "
              "fast mode\ntoday. Read the factor and the value before you "
              "read anything else.")
    else:
        print("\nthe count alone does NOT name it. The rule is still open, "
              "and a change\nto the mesh has no support yet.")


def main(stage="all"):
    src = os.path.join(HERE, "out_rlc_L1_coarse", "model.json")
    if not os.path.isfile(src):
        raise SystemExit("run `run_rlc.py coarse` first: this file needs "
                         "its model.json")
    rs.PLUGINS = make_copy()
    tmp = os.path.join(HERE, "out_b30_tmp")
    ladder = []
    try:
        if stage in ("all", "ladder"):
            ladder = ladder_stage(src, tmp)
        if stage in ("all", "matrix"):
            matrix_stage(src, tmp)
        if ladder:
            verdict(ladder)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["all"]))
