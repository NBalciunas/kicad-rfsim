"""The mesh of every board of this folder, against the mesh of a second runner.

A change to `runner._mesh` moves the mesh lines of some boards and not of
others, and one run of the solver costs minutes for each board. This file
costs no run: it loads two copies of `runner.py`, calls `_mesh` of each one
on every `out_*/model.json` of this folder, and lists each board whose
lines move, with the lines that each copy holds alone. Run it after each
change to a mesh rule and BEFORE the rigs: a board that this file does not
list keeps its mesh, thus its reference number cannot move.

The second runner comes from git (HEAD by default), or from a file:

    C:\\openEMS\\venv\\Scripts\\python.exe mesh_diff.py
    C:\\openEMS\\venv\\Scripts\\python.exe mesh_diff.py e1a5be1^
    C:\\openEMS\\venv\\Scripts\\python.exe mesh_diff.py C:\\copy\\plugins\\runner.py

**The one-layer rule of `_feature_lines` is the example.** Before it,
`_mesh` pooled the copper edges of every layer, thus an F.Cu edge and a
B.Cu edge that stand 0.775 mm apart read as one narrow feature. Against
the commit before that rule (the second command above), this file lists
the two boards of the 2512 land of `run_shunt.py packages` and nothing
else: each one holds a y line at -20.3875 mm that no copper explains, and
the rule removes it.

The file needs numpy and the `model.json` files that the rigs write. It
imports no CSXCAD, no openEMS and no pcbnew.
"""
import contextlib
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGINS = os.path.join(ROOT, "plugins")
sys.path.insert(0, PLUGINS)   # each copy of runner.py imports `solverenv`

import numpy as np  # noqa: E402

# Two lines nearer than this are the same line. `_merge_close` rounds to
# 1e-9 mm and `board_reader` to 1e-5 mm, thus a line that moves by less
# than this did not move.
SAME_MM = 1e-6


def load(path, name):
    """Import one copy of runner.py under a module name of its own."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def other_runner(arg, tmp):
    """Give the path of the second runner.py: a file, or a git revision."""
    if os.path.isfile(arg):
        return arg
    p = subprocess.run(["git", "show", "%s:plugins/runner.py" % arg],
                       cwd=ROOT, capture_output=True)
    if p.returncode:
        raise SystemExit("git cannot give plugins/runner.py at %r: %s"
                         % (arg, p.stderr.decode("utf-8", "replace").strip()))
    path = os.path.join(tmp, "runner_%s.py" % "".join(
        c if c.isalnum() else "_" for c in arg))
    with open(path, "wb") as fh:
        fh.write(p.stdout)
    return path


def mesh_of(r, model):
    """Give the three line lists of `model` under the runner module `r`.

    `res` comes from the formula of `main()`, with the constants of that
    runner. The warnings of `_mesh` go nowhere: one warning for each
    board hides the differences, which are what this file prints.
    """
    model = json.loads(json.dumps(model))   # neither copy sees the other
    s = model["settings"]
    eps = max(d["epsilon"] for d in model["dielectric_layers"])
    res = r.C0 / s["f_stop"] / np.sqrt(eps) * 1e3 / r.RES_DIV[s["mesh"]]
    with contextlib.redirect_stdout(io.StringIO()):
        return r._mesh(model, r._port_geometry(model, res), res)


def only_in(a, b):
    """Give the lines of `a` that have no line of `b` within SAME_MM."""
    b = np.sort(np.asarray(b, float))
    out = []
    for v in a:
        i = int(np.searchsorted(b, v))
        near = [b[j] for j in (i - 1, i) if 0 <= j < len(b)]
        if not near or min(abs(v - w) for w in near) > SAME_MM:
            out.append(float(v))
    return out


def main(arg="HEAD"):
    models = sorted(glob.glob(os.path.join(HERE, "out_*", "model.json")))
    if not models:
        raise SystemExit("no out_*/model.json in %s: run the rigs first"
                         % HERE)
    with tempfile.TemporaryDirectory() as tmp:
        here = load(os.path.join(PLUGINS, "runner.py"), "runner_here")
        there = load(other_runner(arg, tmp), "runner_there")

    print("the mesh of this checkout against %s, on %d boards\n"
          % (arg, len(models)))
    same, moved, failed = 0, 0, []
    for path in models:
        name = os.path.basename(os.path.dirname(path))
        with open(path) as fh:
            model = json.load(fh)
        try:
            a = mesh_of(here, model)
            b = mesh_of(there, model)
        except Exception as exc:   # a model that one copy cannot read
            failed.append((name, "%s: %s" % (type(exc).__name__, exc)))
            continue
        rows = []
        for axis, la, lb in zip("xyz", a, b):
            plus, minus = only_in(la, lb), only_in(lb, la)
            if plus or minus or len(la) != len(lb):
                rows.append("   %s: %d lines here, %d there%s%s" % (
                    axis, len(la), len(lb),
                    "; only here: %s" % ", ".join("%.4f" % v for v in plus)
                    if plus else "",
                    "; only there: %s" % ", ".join("%.4f" % v for v in minus)
                    if minus else ""))
        if rows:
            moved += 1
            print(name)
            print("\n".join(rows))
        else:
            same += 1
    for name, why in failed:
        print("%s\n   could not make the mesh: %s" % (name, why))
    print("\n%d boards: %d keep the same three line lists, %d move, %d "
          "could not run" % (len(models), same, moved, len(failed)))


if __name__ == "__main__":
    main(*sys.argv[1:])
