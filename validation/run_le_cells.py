"""The cause of the FAST growth of a lumped inductor, on ONE variable.

A lumped inductor on the reference board increases in two different
procedures. An earlier measurement found the difference between them:

  - a **FAST** growth with an e-fold near 0.4 ns, which the runner refuses.
    `_feature_lines` reads the gap between the two pads of a part as a
    narrow copper feature, and it puts a line at the middle of it. That
    line is IN the box of the element. Thus the element is on 2 cells in
    series, and 1 cell is possible;
  - a **SLOW** mode with an e-fold near 6 ns. No timestep corrects it, and
    it stays after the fast mode goes away.

**This file does not show the difference between the two counts at this
time. After 2026-09-20, that is the correct result.** The mesh moved the
absorber away from the copper (B30), and the fast mode went away with it.
B51 tried five boards to find one that continues to have the mode. These
were the reference, a board of 3.2 mm, a board of 6.4 mm, a box of 1.0 mm,
and the thick board with the large box. No board shows a difference between
1 cell and 2 (B51). The rule is only from the measurement of 2026-09-16.

**The code holds the rule from 2026-09-16.** `_feature_lines` gets the gap
of an element, on its own layer and along its own axis. It gives that gap
NO line. Thus the box has 1 cell in series, and this file is the regression
test of that rule. The row "the mesh that ships" must give the same result
as the row of 1 cell.

**A change of the box cannot find the rule.** A matrix that changed the BOX
showed that 4 cells in series are as good as 1. Thus "more cells in series
is worse" is not the rule. Each change of the box moves more than one
value. The length of the box sets `tol` (`_mesh` uses 0.25 x min(box)).
Thus it moves the mesh of ALL the board, and it also moves the physical
gap.

**This file moves ONE value.** It keeps the box, `tol` and the board. It
changes only the count of mesh lines IN the box:

    N cells in series  <->  N-1 lines in the box, with equal distances

A copy of `plugins/` has that change, thus the code that ships does not
change. The copy reads two variables of the environment:

    RFSIM_LE_CELLS   N cells in series (0: no change to the mesh)
    RFSIM_LE_CAPS    "0" gives the element `caps=False`

Two steps, and each one gives the growth of the trace:

  1. `ladder`  - the count of cells in series: 1, 2, 3, 4 and 6, with the
     same box and `tol`. This is the measurement that finds the rule.
  2. `matrix`  - the three rows that an earlier session did not run: er
     2.2, er 10.2 and caps=False, each one with the line and without it.

Run it with the python of KiCad 10 or with the python of the solver. It
uses `out_rlc_L1_coarse/model.json`, thus run `run_rlc.py coarse` first:

    C:\\openEMS\\venv\\Scripts\\python.exe run_le_cells.py [ladder|matrix|all]

Each run is short (about 4 ns of simulated time), because the fast growth
gets to its turn in 2 ns. The slow mode must have tens of nanoseconds, and
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

# The copy of `plugins/` with the hook. It goes adjacent to the output,
# thus a run does not touch the code that ships.
COPY = os.path.join(HERE, "out_le_cells_plugins")

# **The TURN of the trace finds the fast mode, and the e-fold does not.** A
# board with the fast growth turns at about 2 ns. A board without it
# decreases to the end of the window, and its turn is near that end. Thus a
# turn that is less than this limit is the fast mode.
#
# Do NOT read the growth column of an ACCEPTED row as the slow mode.
# `envelope` fits the segments AFTER the turn. When a turn is near the end
# of the window, only 3 or 4 segments stay for the fit. The slope that
# comes out is then noise. The slow mode must have a window of tens of
# nanoseconds, and `run_stability.py slow` has such a window.
FAST_TURN_NS = 4.0
# ...and the growth of that row must also be FAST. The e-fold of the fast
# mode is 0.3 to 0.5 ns, and the e-fold of the slow mode is about 6 ns.
# Thus a slope of 1 /ns is between them, with a factor of 6 at each side.
# **The turn without the slope is not sufficient.** A row that only
# DECREASES turns where its trace touches the floor, and that point moves
# with the timestep. At the factor of a 10 nH inductor with
# `LE_STAB_MARGIN` = 0.5, the row of 3 cells turns at 3.34 ns with an
# e-fold of 8.2 ns. Thus it decreases, and the runner accepts it.
FAST_SLOPE = 1.0

# The steps of one run, in the units of `run_stability.STEPS`. `build()`
# multiplies the count by 1/factor. Thus this is the SIMULATED time and not
# the work. **6000 is too small, and the cause is not the physics.**
# openEMS writes the port trace at each quarter of the Nyquist rate. Thus
# 6000 steps at the factor of a 10 nH inductor give 193 rows, and
# `envelope` must have 200. 12000 gives about 8 ns and 385 rows. The fast
# growth turns at about 2 ns, thus the window has the turn and 6 e-folds of
# it. A board with no fast growth only DECREASES in that window, because
# the slow mode turns at about 8 ns.
STEPS = 12000

# The hook. `build()` finds `_mesh` as a global at the moment of the
# call, thus a new global of that name replaces it.
HOOK = '''

# ---------------------------------------------------------------- the hook
# Appended by validation/run_le_cells.py. It is NOT part of the plugin.
_LE_CELLS = int(os.environ.get("RFSIM_LE_CELLS", "0"))
if _LE_CELLS:
    _le_mesh = _mesh

    def _mesh(model, ports, res):
        """Give the mesh, then force N cells in series at every element.

        The box keeps its faces and `tol` keeps its value, thus the
        board outside the box does not move. Only the lines strictly
        inside the box change.
        """
        out = list(_le_mesh(model, ports, res))
        for e in model.get("lumped_elements", []):
            axis = 0 if e["ny"] == "x" else 1
            lo, hi = sorted((e["start"][axis], e["stop"][axis]))
            eps = 1e-9
            keep = [v for v in out[axis] if v <= lo + eps or v >= hi - eps]
            step = (hi - lo) / _LE_CELLS
            keep += [lo + i * step for i in range(1, _LE_CELLS)]
            out[axis] = sorted(keep)
        return tuple(out)
'''


def make_copy():
    """Give a copy of `plugins/` with the hook."""
    src = os.path.join(os.path.dirname(HERE), "plugins")
    shutil.rmtree(COPY, ignore_errors=True)
    shutil.copytree(src, COPY,
                    ignore=shutil.ignore_patterns("__pycache__", "assets"))
    path = os.path.join(COPY, "runner.py")
    with open(path, "r", newline="") as fh:
        text = fh.read()
    # `caps=True` is at the two `AddLumpedElement` calls of `build()`: the
    # series RLC part and all the other types. The hook changes the two.
    # Thus a board of the two types obeys RFSIM_LE_CAPS.
    if text.count("caps=True") != 2:
        raise SystemExit("runner.py holds %d 'caps=True', expected 2"
                         % text.count("caps=True"))
    text = text.replace(
        "caps=True",
        'caps=(os.environ.get("RFSIM_LE_CAPS", "1") != "0")')
    # **The hook goes BEFORE the `__main__` block and not at the end of the
    # file.** `runner.py` calls `main()` from that block. Thus a hook below
    # it replaces `_mesh` after all the run is complete. All rows of the
    # matrix then give the SAME number. That failure gives no message: the
    # rows look like a result.
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
    """Stop if the hook does not move the mesh. No solver run.

    A hook that does nothing gives a full matrix of equal rows, and such
    a matrix reads as a measurement. Thus the file checks the hook first.
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
        env = dict(os.environ, RFSIM_LE_CELLS=str(n), PYTHONPATH=COPY)
        p = subprocess.run([py, "-c", code, src], capture_output=True,
                           env=env, cwd=COPY)
        out = p.stdout.decode("utf-8", "replace").strip().splitlines()
        got[n] = out[-1] if out else "?"
    if got[1] != "1" or got[4] != "4":
        raise SystemExit(
            "the hook does not move the mesh: RFSIM_LE_CELLS=1 gave %s "
            "cell(s) and =4 gave %s. Every row of the matrix would be "
            "equal. Look at where the hook sits in the copy of runner.py."
            % (got[1], got[4]))
    print("hook OK: 1 and 4 cells in series reach the mesh\n")


def measure(src, lnh, cells=0, caps=True, er=None, steps=None, tmp=None):
    """Run one row. Give (the cause, the turn ns, the growth 1/ns)."""
    os.environ["RFSIM_LE_CELLS"] = str(cells)
    os.environ["RFSIM_LE_CAPS"] = "1" if caps else "0"
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
    """Give the e-fold time of a growth, or give None for a decrease."""
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
        out.append((n, cause, turn, slope))
    # The mesh that ships, for the same board and the same value.
    cause, f, turn, slope = measure(src, lnh, cells=0, tmp=tmp)
    row("the mesh that ships", cause, f, turn, slope)
    out.append(("ships", cause, turn, slope))
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
    """Tell if the count of cells in series causes the fast growth.

    A row has the fast mode when the runner refused it for a growth. It
    also has the fast mode when its trace turns at a short time AND
    increases fast after the turn. The decision uses three rows: 2 cells
    have the mode, and 1 cell and the mesh that ships do not.
    """
    fast = [n for n, cause, turn, slope in ladder
            if cause == "growth"
            or (turn == turn and turn < FAST_TURN_NS
                and slope == slope and slope > FAST_SLOPE)]
    ok = [n for n, _, _, _ in ladder if n not in fast]
    print("\nthe FAST mode (refused, or a turn under %.1f ns with an "
          "e-fold under %.1f ns): %s"
          % (FAST_TURN_NS, 1.0 / FAST_SLOPE,
             ", ".join(str(n) for n in fast) or "none"))
    print("no fast mode: %s" % (", ".join(str(n) for n in ok) or "none"))
    if 2 in fast and "ships" not in fast and 1 not in fast:
        print("\nPASS: the mesh that ships keeps ONE cell in series and "
              "it does not grow\nfast, while 2 cells still do. That is the "
              "rule of `_feature_lines` at work.")
    elif "ships" in fast:
        print("\nFAIL: the mesh that ships grows fast, thus it holds 2 "
              "cells in series.\n`_feature_lines` must give the gap of an "
              "element no line: the rule is not\nin the code, or it does "
              "not reach this board.")
    elif not fast:
        print("\nNO row grew fast, and that is the verdict of every board that was tried"
              "\nsince 2026-09-20: the PML band is 8 cells of `res` now (B30), thus the"
              "\nabsorber no longer stands near the copper. The same board gave 2 cells"
              "\nan e-fold of 0.31 ns on the mesh of 2026-09-16 and it gives 5.51 ns now,"
              "\nthus that absorber fed the FAST mode as well as the slow one. Five"
              "\nboards were tried for one that still carries it, and NONE separates 1"
              "\ncell from 2 (B51). **This is NOT a failure of the rule**, which costs"
              "\nnothing and stays in the code: it says that this file cannot measure"
              "\nthe rule any more.")
    else:
        print("\nRead the rows: 2 cells in series are the ONE bad count, "
              "and the mesh\nthat ships must behave as the row of 1 cell.")


def main(stage="all"):
    src = os.path.join(HERE, "out_rlc_L1_coarse", "model.json")
    if not os.path.isfile(src):
        raise SystemExit("run `run_rlc.py coarse` first: this file needs "
                         "its model.json")
    rs.PLUGINS = make_copy()
    tmp = os.path.join(HERE, "out_le_cells_tmp")
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
