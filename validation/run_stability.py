"""Measure the stability boundary of a lumped inductor, on MORE THAN ONE
geometry.

`runner._time_step_factor` gives `LE_STAB_MARGIN / sqrt(L[nH])`. The law
comes from ONE geometry (the board of `run_rlc.py` at the coarse preset),
and the question that this file answers is what its MARGIN is on a board
that is not that one.

Run it with the python of KiCad 10, which starts the venv of the solver
itself:

    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_stability.py

Each run is short (STEPS timesteps, with the end criteria off), but the
file makes many of them: about 15 minutes for the full matrix. This is
not a test for every change. Run it again when you change
`LE_STAB_MARGIN`, `_time_step_factor`, or a mesh rule that moves the
cells at a lumped element.

**What the matrix varies, and why.** The mesh preset alone is not
enough: the two faces of an element box are anchored mesh lines with
nothing between them, thus the BOX sets the smallest cell of the board
as soon as the preset becomes coarse, and the coarse preset and the
medium one then give the same Courant step. The box and the thickness of
the board are the two things that do move it.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)

import runner  # noqa: E402
import solverenv  # noqa: E402

STEPS = 6000
# The ladder of the bisection. Each step is about 1.4 times the next
# one, thus a boundary is exact to one step and no further.
LADDER = (1.0, 0.7, 0.5, 0.35, 0.25, 0.18, 0.12, 0.09, 0.06, 0.04)
L_NH = (1, 10, 100)
# The margin that the rule must keep on EVERY geometry of the matrix.
# The bare law 1/sqrt(L) gives 1.0 on the thickest board, which is no
# margin at all; with LE_STAB_MARGIN it gives about 1.5. One ladder step
# is 1.4, thus this limit is the resolution of the measurement.
MARGIN_MIN = 1.35


def thicken(model, k):
    """Scale the board in z by `k`, thus the cells at the element grow."""
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


def variant(src, l_h, box=None, thick=None):
    """Give the model of one row of the matrix, with the inductor in it."""
    with open(src) as fh:
        model = json.load(fh)
    if thick:
        thicken(model, thick)
    e = model["lumped_elements"][0]
    e.update(type="L", value=l_h, esl=0.0, esr=0.0, package="Custom")
    if box is not None:
        # Make the box shorter ALONG the current, and keep its center.
        i = 0 if e["ny"] == "x" else 1
        lo, hi = sorted((e["start"][i], e["stop"][i]))
        c = 0.5 * (lo + hi)
        e["start"][i], e["stop"][i] = c - 0.5 * box, c + 0.5 * box
    # The end criteria must not stop the run before the divergence shows.
    model["settings"].update(max_timesteps=STEPS, end_criteria=1e-12,
                             excite=[1], lumped=True, parasitics=False)
    return model


def stable(model, factor, tmp):
    """Give True when the run of `model` at `factor` stays finite."""
    model["settings"]["time_step_factor"] = factor
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    path = os.path.join(tmp, "model.json")
    with open(path, "w") as fh:
        json.dump(model, fh)
    py = solverenv.solver_python() or sys.executable
    p = subprocess.run([py, os.path.join(PLUGINS, "runner.py"), path, tmp],
                       capture_output=True)
    # The runner stops with a message when it finds NaN. Read the bytes:
    # openEMS writes characters that are not UTF-8 on this console.
    txt = (p.stdout + p.stderr).decode("utf-8", "replace").lower()
    shutil.rmtree(tmp, ignore_errors=True)
    return "diverged" not in txt and "nan" not in txt


def boundary(model, tmp):
    """Give the largest factor of the ladder that stays finite."""
    for f in LADDER:
        if stable(model, f, tmp):
            return f
    return None


def main():
    src = os.path.join(HERE, "out_rlc_L1_coarse", "model.json")
    med = os.path.join(HERE, "out_rlc_L1_medium", "model.json")
    if not os.path.isfile(src) or not os.path.isfile(med):
        raise SystemExit("run `run_rlc.py coarse` and `run_rlc.py medium` "
                         "first: this file needs their model.json")
    rows = [
        ("the reference (coarse)", src, {}),
        ("the medium preset", med, {}),
        ("a box of 0.2 mm", src, {"box": 0.2}),
        ("a box of 1.0 mm", src, {"box": 1.0}),
        ("a board of 3.2 mm", src, {"thick": 2.0}),
        ("a board of 6.4 mm", src, {"thick": 4.0}),
    ]
    tmp = os.path.join(HERE, "out_stability_tmp")
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
                # The ladder starts at 1.0 and openEMS takes no larger
                # factor. Thus a boundary AT 1.0 is a lower limit, and
                # its margin is a lower limit too: mark it with ">=".
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
        raise SystemExit(
            "FAIL: the margin falls to %.1fx on \"%s\", against %.1fx that "
            "this file asks for. A lumped inductor can then diverge on a "
            "board that a user makes. Make LE_STAB_MARGIN smaller."
            % (worst[1], worst[0], MARGIN_MIN))
    print("PASS: the rule keeps a margin of %.1fx or more on %d geometries"
          % (MARGIN_MIN, len(rows)))


if __name__ == "__main__":
    main()
