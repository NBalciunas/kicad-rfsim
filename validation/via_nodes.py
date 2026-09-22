"""The grid nodes that stand INSIDE the barrel of every via of this folder.

openEMS makes a metal cylinder into PEC on the edges of the Yee grid
whose NODE lies in the barrel. Thus a barrel with no node inside writes
"Unused primitive (type: Cylinder)" and conducts nothing, and a barrel
with ONE node is a thin wire and not a barrel. `_mesh` gives each via
three lines on each axis, the centre and the two surfaces, and the two
surface lines go 1 ppm in (`VIA_SURFACE`). The three are ANCHORS of
`_merge_close`: a copper edge within `tol` of a via line moves to that
line, where the mean of the two took the node out of the barrel before,
and the merge keeps the higher RANK when two anchors stand that near.

This file makes the mesh of every `out_*/model.json` of this folder and
counts two things:

  - **the axis cases.** One via on one axis is one case, thus 51 vias
    give 102. A case HOLDS its three lines when each one stands on the
    coordinate that `_mesh` gives it. A case that lost a line tells
    which lines remain, in units of the radius from the centre.
  - **the nodes.** The pairs of x and y lines whose distance from the
    centre of the via is under its radius. That count is what the engine
    sees, and `run_via.py` measures what it costs: a barrel of 5 nodes
    reads +8% to +20% in inductance against Goldfarb and Pucel. A barrel
    of 1 node reads -16% when that node stands at the AXIS of the via
    and +54% when it stands 0.106 mm off it, thus the count alone does
    not give the error.

It needs numpy and the `model.json` files that the rigs write, and it
starts no solver: run it after a change to a mesh rule, with
`mesh_diff.py` for the lines of the whole board.

**Today** (2026-09-21, and `test_ports.py` holds the rule): 97 of the
102 axis cases keep their three lines, and no barrel holds fewer than 3
nodes (5 hold 3, 40 hold 5, 4 hold 6 and 2 hold 10). The mesh that
merged a via line with a copper edge held 76 cases, and 11 barrels of 51
held 2 nodes. The 5 cases that lose a line are the x axis of the 0402
and the 0603 shunt boards, where a FACE of an element box stands 0.02 mm
from a surface line of the via: a face outranks a via, thus the via
gives up that line and keeps its centre.

    C:\\openEMS\\venv\\Scripts\\python.exe via_nodes.py [-v] [revision|path]

The argument names a second copy of the runner, in the way of
`mesh_diff.py`: a git revision, or the path of a `runner.py`. With no
argument the file measures the runner of this checkout. `-v` lists every
case that lost a line, with the board that holds it.
"""
import glob
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mesh_diff as md  # noqa: E402  (it holds the loader and the mesh call)


def inside(lines, centre, radius):
    """Give the lines inside the barrel, in units of the radius."""
    return sorted((v - centre) / radius for v in lines
                  if abs(v - centre) < radius)


def main(*argv):
    verbose = "-v" in argv
    rest = [a for a in argv if not a.startswith("-")]
    models = sorted(glob.glob(os.path.join(HERE, "out_*", "model.json")))
    if not models:
        raise SystemExit("no out_*/model.json in %s: run the rigs first"
                         % HERE)
    with tempfile.TemporaryDirectory() as tmp:
        src = (md.other_runner(rest[0], tmp) if rest
               else os.path.join(md.PLUGINS, "runner.py"))
        r = md.load(src, "runner_under_test")
    # A runner from before the nudge of 2026-09-16 has no VIA_SURFACE,
    # and its surface lines stand ON the barrel.
    surface = getattr(r, "VIA_SURFACE", 1.0)

    cases, held, lost, nodes, rows = 0, 0, {}, {}, []
    boards = 0
    for path in models:
        name = os.path.basename(os.path.dirname(path))
        with open(path) as fh:
            model = json.load(fh)
        if not model.get("vias"):
            continue
        boards += 1
        xs, ys, _ = md.mesh_of(r, model)
        for v in model["vias"]:
            seen = {}
            for axis, lines, c in (("x", xs, v["x"]), ("y", ys, v["y"])):
                off = inside(lines, c, v["r"])
                seen[axis] = off
                cases += 1
                if all(any(abs(o - w) < 1e-6 for o in off)
                       for w in (-surface, 0.0, surface)):
                    held += 1
                    continue
                key = tuple(round(o, 3) for o in off)
                lost[key] = lost.get(key, 0) + 1
                rows.append((name, axis, v["r"], key))
            n = sum(1 for a in seen["x"] for b in seen["y"]
                    if a * a + b * b < 1.0)
            nodes[n] = nodes.get(n, 0) + 1

    print("the runner of %s, on the %d boards that hold a via\n"
          % (rest[0] if rest else "this checkout", boards))
    print("%d axis cases, %d hold their three lines" % (cases, held))
    for key, n in sorted(lost.items(), key=lambda kv: -kv[1]):
        print("   %2d x %s" % (n, ", ".join("%+.3f" % o for o in key)
                               or "NO line inside the barrel"))
    print("\nthe nodes in the barrel, over %d vias:" % sum(nodes.values()))
    for n, count in sorted(nodes.items()):
        print("   %2d node(s): %d via(s)%s"
              % (n, count, "   <-- it conducts NOTHING" if not n else
                 "   <-- a thin wire" if n == 1 else ""))
    if verbose:
        print("\nthe cases that lost a line:")
        for name, axis, radius, key in rows:
            print("   %-34s %s r=%.2f  %s"
                  % (name, axis, radius, ", ".join("%+.3f" % o for o in key)
                     or "none"))
    elif rows:
        print("\n-v lists each one of the %d cases with its board" % len(rows))


if __name__ == "__main__":
    main(*sys.argv[1:])
