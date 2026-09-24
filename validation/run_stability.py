"""Measure the stability boundary of a lumped inductor, on MORE THAN ONE
geometry.

`runner._time_step_factor` gives `LE_STAB_MARGIN / sqrt(L[nH])`. The law
comes from ONE geometry (the board of `run_rlc.py` at the coarse preset).
This file finds its MARGIN on a board that is not that one.

Run it with the python of KiCad 10, which starts the venv of the solver
itself:

    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_stability.py

**The file measures two different failures, and it tells which one.**

1. **The ladder** (`boundary`) finds the largest factor where the run does
   not diverge FAST. Each run is short (`STEPS` timesteps, with the end
   criteria off), and the matrix is about 15 minutes long.
2. **The slow step** (`survival`) gives the answer that the ladder cannot
   give. Some boards have a mesh with a column of REFINED cells. Such a
   board also has a mode that increases with an e-fold of some nanoseconds,
   and **no timestep corrects it**. The element does not make that mode,
   and the break in the copper also does not make it. The PML near the
   copper in z gives the energy. The small cells of a gap, a narrow track
   or an element box let the mode stay.

   The same board with NO element increases with an e-fold of 6.9 ns,
   against 14.1 ns at 1 nH. The same board with no small cell there
   decreases for 120 ns. The mode comes into view only after tens of
   nanoseconds. Thus a ladder of 6000 steps calls such a board STABLE.
   The slow step runs the model at the factor that the RULE gives, and
   it runs it after the turn of the trace. It compares the time of that
   turn with the time when a USUAL run gets to its end criteria. That
   ratio is the margin of a user.

This is not a test for each change. Run it again when you change
`LE_STAB_MARGIN`, `_time_step_factor`, or a mesh rule that moves the cells
at a lumped element.

**What the matrix changes, and why.** The mesh preset without other changes
is not sufficient. The two faces of an element box are anchors of the mesh
with no lines between them. Thus the BOX sets the smallest cell of the
board when the preset becomes coarse. The coarse preset and the medium
preset then give the same Courant step. The box and the thickness of the
board are the two values that move it.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)

import runner  # noqa: E402
import solverenv  # noqa: E402

# The steps of ONE run of the ladder. `build()` multiplies the count by
# 1/factor. Thus each rung has the same SIMULATED time: 6000 steps is about
# 4 ns on the reference board. That is sufficient for the FAST divergence,
# which gets to NaN in some hundreds of steps. It is much too short for the
# slow mode. The slow step below measures that mode.
STEPS = 6000
# The ladder of the bisection. Each step is about 1.4 times the next one.
# Thus a boundary is accurate to one step, and not more.
LADDER = (1.0, 0.7, 0.5, 0.35, 0.25, 0.18, 0.12, 0.09, 0.06, 0.04)
L_NH = (1, 10, 100)
# The margin that the rule must keep on EACH geometry of the matrix. The
# bare law 1/sqrt(L) gives about 0.9 on the thickest board, thus no margin.
# With LE_STAB_MARGIN = 0.5, that geometry keeps 1.8x. One ladder step is
# 1.4, thus this limit is the resolution of the measurement.
MARGIN_MIN = 1.35

# ----------------------------------------------------------- the slow step
#
# The steps of a run of the slow step, in the same units as STEPS. The
# simulated time is STEPS_SLOW times the Courant step of the board.
# 45000 steps is about 30 ns on the reference board. There, the slow mode
# turns at about 5 ns, and it increases with an e-fold near 5 ns. Thus the
# window has the turn and about 5 e-folds after it. That is sufficient to
# find the difference between a growth and the noise of the decrease.
STEPS_SLOW = 45000
# The inductances of the slow step. 100 nH is not here, because its factor
# is 0.07. Thus `build()` gives it 14 times more steps than 1 nH for the
# same simulated time. That is about 20 minutes for ONE run. Give "slow100"
# on the command line to add it.
L_NH_SLOW = (1, 10)
# The envelope of the trace goes into this many segments, and the
# smallest of them is the turn.
SEGMENTS = 200
# A run of the slow step must get to its end criteria this many times
# BEFORE the turn of the trace, or more. A user run stops at the end
# criteria. Thus this ratio is the margin that a user run keeps against the
# growth. The 8 runs of the 0603 board stopped between 5.8 ns and 49 ns,
# against a turn at about 45 ns. That is a ratio near 1, and one run in
# three then ended in the growth.
SURVIVAL_MIN = 2.0


def thicken(model, k):
    """Scale the board in z by `k`, thus the cells at the element become
    larger."""
    for c in model["copper_layers"]:
        c["z"] *= k
    for d in model["dielectric_layers"]:
        d["z_top"] *= k
        d["z_bottom"] *= k
    for v in model.get("vias", []):
        v["z0"] *= k
        v["z1"] *= k
    for e in model.get("lumped_elements", []):
        e["start"][2] *= k
        e["stop"][2] *= k
    return model


def set_epsilon(model, er):
    """Give the substrate a different permittivity.

    This moves the mesh and also the physics. `res` follows 1/sqrt(er).
    Thus a large er gives a smaller mesh and a smaller Courant step. The
    cells of the dielectric rule follow it.

    **The domain also follows it.** From 2026-09-20, the PML band is 8
    cells of `res`. Thus a model with a new er must have a new depth and a
    new region, and `extract` makes them together. With the region of the
    previous er, the band does not agree with its mesh. The board is then
    not the board that a user gets.
    """
    old_pml = model.get("pml_mm")
    for d in model["dielectric_layers"]:
        d["epsilon"] = er
    if old_pml:
        s = model["settings"]
        pml = solverenv.pml_depth(solverenv.mesh_res(s["f_stop"], er,
                                                     s["mesh"]))
        grow = pml - old_pml
        r = model["region"]
        for k, sign in (("x0", -1), ("x1", 1), ("y0", -1), ("y1", 1)):
            r[k] += sign * grow
        model["pml_mm"] = pml
    return model


def variant(src, l_h, box=None, thick=None, er=None, steps=None):
    """Give the model of one row of the matrix, with the inductor in it."""
    with open(src) as fh:
        model = json.load(fh)
    if thick:
        thicken(model, thick)
    if er:
        set_epsilon(model, er)
    e = model["lumped_elements"][0]
    e.update(type="L", value=l_h, esl=0.0, esr=0.0, package="Custom")
    if box is not None:
        # Make the box shorter ALONG the current, and keep its center.
        i = 0 if e["ny"] == "x" else 1
        lo, hi = sorted((e["start"][i], e["stop"][i]))
        c = 0.5 * (lo + hi)
        e["start"][i], e["stop"][i] = c - 0.5 * box, c + 0.5 * box
    # The end criteria must not stop the run before the divergence shows.
    model["settings"].update(max_timesteps=steps or STEPS,
                             end_criteria=1e-12, excite=[1], lumped=True,
                             parasitics=False)
    model["settings"].pop("time_step_factor", None)
    return model


def solve(model, factor, tmp):
    """Run one model at `factor`. Give (the cause of a refusal, the log).

    The cause is None when the runner accepted the run, and it is "nan",
    "growth" or "failed" when the runner stopped. The output directory
    stays, thus the caller can read the port data before it removes it.
    """
    model["settings"]["time_step_factor"] = factor
    shutil.rmtree(tmp, ignore_errors=True)
    # **Tell why the directory stays**, and do not give a traceback for it.
    # A run that somebody stopped keeps its solver process. That process
    # holds the port files open, and Windows then refuses to remove them.
    if os.path.isdir(tmp):
        raise SystemExit(
            "cannot remove %s. A run that was stopped leaves its solver "
            "behind, and that process holds the files of this directory. "
            "Stop the python that runs runner.py, then start this file "
            "again." % tmp)
    os.makedirs(tmp)
    path = os.path.join(tmp, "model.json")
    with open(path, "w") as fh:
        json.dump(model, fh)
    py = solverenv.solver_python() or sys.executable
    p = subprocess.run([py, os.path.join(PLUGINS, "runner.py"), path, tmp],
                       capture_output=True)
    # Read the bytes: openEMS writes characters that are not UTF-8 on
    # this console.
    txt = (p.stdout + p.stderr).decode("utf-8", "replace").lower()
    if p.returncode == 0:
        return None, txt
    # `_diverged` gives the two causes different advice. Thus this file
    # also keeps them apart. **Before 2026-09-01, this file did not see a
    # growth.** The test looked for "diverged" and for "nan". The message of
    # a growth has no such text. Thus such a run read as STABLE.
    if "not stable" in txt or "grew and did not decay" in txt:
        return "growth", txt
    # Match the words of the message, and not only "nan". Other words, for
    # example "resonance", contain "nan".
    if "diverged" in txt:
        return "nan", txt
    return "failed", txt


def stable(model, factor, tmp):
    """Give True when the runner accepts the run of `model` at `factor`."""
    cause = solve(model, factor, tmp)[0]
    shutil.rmtree(tmp, ignore_errors=True)
    return cause is None


def envelope(tmp):
    """Give (the turn in ns, the growth in 1/ns, the last time in ns).

    The envelope goes into `SEGMENTS` segments, and the smallest of them is
    the turn of the trace. The slope of a line through the log of the
    segments after the turn is the growth. The port file uses '%' as its
    comment mark, and it is SUBSAMPLED. Thus column 0 is the only correct x
    axis.
    """
    names = sorted(glob.glob(os.path.join(tmp, "exc*", "port_ut_*")))
    if not names:
        return None
    try:
        a = np.loadtxt(names[0], comments=("%", "#"))
    except (ValueError, OSError):
        return None                      # a NaN run holds no table
    if a.ndim != 2 or len(a) < 200:
        return None  # not sufficient rows for a decision
    t, u = a[:, 0] * 1e9, np.abs(a[:, 1])
    # Each segment must have some rows of its own. Thus a short trace gets
    # fewer segments, and not thinner segments.
    nseg = min(SEGMENTS, len(u) // 10)
    idx = [i for i in np.array_split(np.arange(len(u)), nseg) if len(i)]
    seg_t = np.array([t[i].mean() for i in idx])
    seg_u = np.array([u[i].max() for i in idx])
    ok = seg_u > 0
    seg_t, seg_u = seg_t[ok], seg_u[ok]
    j = int(np.argmin(seg_u))
    if j >= len(seg_t) - 3:
        return seg_t[j], 0.0, t[-1]  # the trace decreases at the end
    slope = float(np.polyfit(seg_t[j:], np.log(seg_u[j:]), 1)[0])
    return seg_t[j], slope, t[-1]


def last_time_ns(tmp):
    """Give the time of the last row of the port data, in ns.

    A run that gets to its end criteria at a short time writes some tens of
    rows. That is not sufficient for `envelope`. Thus this reads only the
    end.
    """
    names = sorted(glob.glob(os.path.join(tmp, "exc*", "port_ut_*")))
    if not names:
        return float("nan")
    try:
        a = np.loadtxt(names[0], comments=("%", "#"))
    except (ValueError, OSError):
        return float("nan")
    if a.ndim != 2 or not len(a):
        return float("nan")
    return float(a[-1, 0]) * 1e9


def survival(src, kw, lnh, tmp):
    """Give (the turn in ns, the end of a usual run in ns, the growth).

    Two runs of the SAME model at the factor that the rule gives:

    - one with an end criteria that no run can get to. Thus it goes to
      `STEPS_SLOW`, and its trace shows the turn;
    - one with the end criteria of a user run. Thus it stops where a user
      run stops.

    The ratio of the two times is the margin that a user keeps.
    """
    long_model = variant(src, lnh * 1e-9, steps=STEPS_SLOW, **kw)
    factor = runner._time_step_factor(long_model)
    cause, _ = solve(long_model, factor, tmp)
    env = envelope(tmp)
    shutil.rmtree(tmp, ignore_errors=True)

    real = variant(src, lnh * 1e-9, steps=STEPS_SLOW, **kw)
    real["settings"]["end_criteria"] = 1e-4
    solve(real, factor, tmp)
    end_ns = last_time_ns(tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return dict(factor=factor, cause=cause,
                turn_ns=env[0] if env else float("nan"),
                growth=env[1] if env else float("nan"),
                window_ns=env[2] if env else float("nan"),
                end_ns=end_ns)


def boundary(model, tmp):
    """Give the largest factor of the ladder that stays finite."""
    for f in LADDER:
        if stable(model, f, tmp):
            return f
    return None


def slow_stage(rows, tmp, l_values):
    """The second step: the longest run before the mode controls it.

    The ladder cannot give this answer. It gives each rung the same short
    window, and the slow mode must have tens of nanoseconds. Also, no
    factor of the ladder corrects that mode, because its rate does not
    follow the timestep.
    """
    print("\nthe slow mode: the turn of the trace against the end of a "
          "normal run, at the factor that the rule gives, over a window of "
          "%d steps\n" % STEPS_SLOW)
    print("%-24s%6s%9s%11s%10s%11s%9s"
          % ("geometry", "L", "factor", "window", "turn", "a real run",
             "margin"))
    worst = (None, 1e9)
    for name, path, kw in rows:
        for lnh in l_values:
            r = survival(path, kw, lnh, tmp)
            if r["turn_ns"] != r["turn_ns"]:
                print("%-24s%6g%9.3f   the long run holds too few "
                      "rows to judge: raise STEPS_SLOW"
                      % (name, lnh, r["factor"] or 1.0))
                continue
            if r["end_ns"] != r["end_ns"] or not r["end_ns"] > 0:
                print("%-24s%6g%9.3f   the run with the real end "
                      "criteria wrote no port data"
                      % (name, lnh, r["factor"] or 1.0))
                continue
            margin = r["turn_ns"] / r["end_ns"]
            grew = r["growth"] > 0 and r["cause"] == "growth"
            print("%-24s%6g%9.3f%8.1f ns%7.1f ns%8.1f ns%8.1fx%s"
                  % (name, lnh, r["factor"] or 1.0, r["window_ns"],
                     r["turn_ns"], r["end_ns"], margin,
                     "  GROWTH" if grew else ""))
            if margin < worst[1]:
                worst = ("%s, %g nH" % (name, lnh), margin)
    print("\n\"turn\" is the smallest point of the envelope: after it the "
          "field stops decaying.\n\"a real run\" is the same model with the "
          "end criteria of 1e-4, which is where a\nuser run stops. The "
          "margin is the ratio of the two.")
    if worst[0] is None:
        print("no row gave both numbers: no slow mode is visible over %d "
              "steps, or the runs are too short to judge" % STEPS_SLOW)
        return True
    print("the smallest margin is %.1fx on \"%s\"" % (worst[1], worst[0]))
    if worst[1] < SURVIVAL_MIN:
        print("FAIL: a normal run ends inside %.1fx of the turn on \"%s\", "
              "against the %.1fx that this file asks for. A run that stops "
              "after the turn gives an S-matrix that is flat near 1, and "
              "`_diverged` calls it a growth."
              % (worst[1], worst[0], SURVIVAL_MIN))
        print("**This stage cannot pass until the slow mode is "
              "corrected.** No value of LE_STAB_MARGIN and no "
              "time_step_factor moves the rate of that mode, thus do NOT "
              "make SURVIVAL_MIN smaller to get a green line: the margin "
              "above is the one that a user really has.")
        return False
    print("PASS: a normal run ends at least %.1fx before the turn"
          % SURVIVAL_MIN)
    return True


def ladder_stage(src, med, tmp):
    """The first step: the largest factor where a run stays finite.

    This measures only the FAST divergence. A run of `STEPS` steps is about
    4 ns. Thus a mode that must have tens of nanoseconds does not show
    here, for all factors. The slow step measures that mode.
    """
    rows = [
        ("the reference (coarse)", src, {}),
        ("the medium preset", med, {}),
        ("a box of 0.2 mm", src, {"box": 0.2}),
        ("a box of 1.0 mm", src, {"box": 1.0}),
        ("a board of 3.2 mm", src, {"thick": 2.0}),
        ("a board of 6.4 mm", src, {"thick": 4.0}),
        # A different permittivity moves the mesh AND the physics: `res`
        # follows 1/sqrt(er). PTFE at 2.2 and a ceramic at 10.2 are the
        # two ends of what a user puts on a board.
        ("a substrate of er 2.2", src, {"er": 2.2}),
        ("a substrate of er 10.2", src, {"er": 10.2}),
    ]
    print("the largest stable time_step_factor, %d steps, ladder to 1.4\n"
          % STEPS)
    head = "".join("%14s" % ("%g nH" % v) for v in L_NH)
    print("%-24s%s%10s" % ("geometry", head, "worst"))
    worst = (None, 1e9)
    capped = False
    for name, path, kw in rows:
        cells, margins = [], []
        for lnh in L_NH:
            f = boundary(variant(path, lnh * 1e-9, **kw), tmp)
            rule = runner._time_step_factor(
                {"settings": {"lumped": True},
                 "lumped_elements": [{"type": "L", "value": lnh * 1e-9}]})
            if f is None:
                cells.append("%14s" % "<0.04")
                margins.append((0.0, False))
            else:
                # The ladder starts at 1.0, and openEMS accepts no larger
                # factor. Thus a boundary AT 1.0 is a lower limit, and its
                # margin is also a lower limit: mark it with ">=".
                lim = f >= LADDER[0]
                capped = capped or lim
                cells.append("%14s" % ("%s%g (%s%.1fx)"
                                       % (">=" if lim else "", f,
                                          ">=" if lim else "", f / rule)))
                margins.append((f / rule, lim))
        # The worst geometry comes from the points that the ladder
        # MEASURED. A point at the top of the ladder only says "1.4x or
        # more", thus it cannot name the worst geometry.
        real = [m for m, lim in margins if not lim]
        m = min(real) if real else min(m for m, _ in margins)
        if m < worst[1]:
            worst = (name, m)
        print("%-24s%s%9.1fx" % (name, "".join(cells), m))

    print("\nthe rule is LE_STAB_MARGIN / sqrt(L[nH]), with "
          "LE_STAB_MARGIN = %g" % runner.LE_STAB_MARGIN)
    if capped:
        print("\">=\" marks a run that is stable at the FULL Courant step: "
              "the ladder\ncannot go above 1.0, thus its margin is a lower "
              "limit and not a measurement.")
    print("the worst MEASURED geometry is \"%s\" at %.1fx" % worst)
    if worst[1] < MARGIN_MIN:
        print("FAIL: the margin falls to %.1fx on \"%s\", against %.1fx "
              "that this file asks for. A lumped inductor can then "
              "diverge on a board that a user makes. Make "
              "LE_STAB_MARGIN smaller."
              % (worst[1], worst[0], MARGIN_MIN))
        return False
    print("PASS: the rule keeps a margin of %.1fx or more on %d geometries"
          % (MARGIN_MIN, len(rows)))
    return True


def main(stage="all"):
    src = os.path.join(HERE, "out_rlc_L1_coarse", "model.json")
    med = os.path.join(HERE, "out_rlc_L1_medium", "model.json")
    if not os.path.isfile(src) or not os.path.isfile(med):
        raise SystemExit("run `run_rlc.py coarse` and `run_rlc.py medium` "
                         "first: this file needs their model.json")
    tmp = os.path.join(HERE, "out_stability_tmp")
    ok = True
    if stage in ("all", "fast"):
        ok = ladder_stage(src, med, tmp)
    if stage in ("all", "slow", "slow100"):
        l_values = L_NH_SLOW + ((100,) if stage == "slow100" else ())
        # The slow mode comes from the ELEMENT and not from the board. Thus
        # the reference geometry gives the answer for the family. A
        # geometry of its own costs one long run for each row.
        ok = slow_stage([("the reference (coarse)", src, {})], tmp,
                        l_values) and ok
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["all"]))
