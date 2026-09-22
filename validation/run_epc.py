"""Measure the self-resonance of an inductor against 1 / (2 pi sqrt(LC)).

An inductor row of the dialog takes the SRF of the part, and the plugin
then models it as DCR + L in parallel with the capacitance that stands
with the value at that frequency. The two cannot share one box: the
engine gives the cells of a box to ONE property and the other one is
silent, with no message. Thus `runner._epc_split` divides the land of the
part ACROSS the current, at a line that the mesh holds already, and the
EPC goes on the other half with `LEtype=0`.

**The observable is a FREQUENCY.** A parallel LC is a high impedance at
its resonance, thus a part in series gives a deep NOTCH of |S21| there,
and the notch stands where 1 / (2 pi sqrt(LC)) says. No scale error of
the amplitude can move it.

**The board is the microstrip of `run_lumped.make` with the PADS of an
0402 land** (0.540 mm along the current, 0.640 mm across, a gap of
0.480 mm), because the count of the cells across the land decides this
measurement and a real 0402 land holds 2 of them where the 2.9 mm strip
of `run_rlc.py` holds 4. The shunt board of `run_shunt.py packages` holds
the only other real land of this folder and it CANNOT measure this: a
part of 10 nH stands at 188 ohm at 3 GHz, thus it is nearly an open
already and its anti-resonance moves |S21| by 0.06 dB.

**The DCR damps the tank and it does not move the notch.** A parallel LC
with no loss rings for hundreds of nanoseconds, thus the end criteria
never arrives and a window that CUTS the ring gives an S-matrix with GAIN
in it. 1 ohm gives Q = 188 at 3 GHz and the run then ends in 1.7 ns,
where R*R*C/L moves the notch by 3e-5 of itself.

Measured on 2026-09-21, with 10 nH and an EPC of 0.28 pF (thus 3.008 GHz):

    the land                        the notch     against the closed form
    2 cells (an 0402 or an 0603)    3.270 GHz     +8.7%
    4 cells (a 1206, a 2512)        3.025 GHz     +0.6%

The notch always stands HIGH, because the two boxes share the mesh line
of their joint and each element scales its value over the node lines of
its own box, the shared one included. The control, which is the same
board with no EPC, only FALLS over the band.

Run it with the python of KiCad. It needs pcbnew, and it starts the
solver itself:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" run_epc.py [mesh]
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGINS = os.path.join(os.path.dirname(HERE), "plugins")
sys.path.insert(0, PLUGINS)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

import pcbnew  # noqa: E402
import board_reader  # noqa: E402
import solverenv  # noqa: E402
import run_lumped as rl  # noqa: E402

L_NH = 10.0
EPC_PF = 0.28           # 10 nH with 0.28 pF resonates at 3.008 GHz
DCR = 1.0               # ohm: it damps the tank, and it cannot move the notch
MARGIN = 8.0
# The KiCad land of an 0402: the pad is 0.540 mm along the current and
# 0.640 mm across it, and the two pads leave a gap of 0.480 mm.
PAD_L, PAD_W, GAP = 0.540, 0.640, 0.480
X_MID = 20.0
# The notch of a land of 2 cells stood +8.7% high on 2026-09-21, and a
# land of 4 cells +0.6%. The limit holds the first one with a margin,
# in the way that VIA_TOL holds a barrel of 5 edges.
EPC_TOL = 0.15
# The control must only FALL: a board with no EPC has no resonance in
# the band, thus its deepest point stands at the end of the sweep.
BAND = (1.5e9, 5.0e9)


def make_board(path):
    """The board of `run_lumped.make`, with the pads of an 0402 land."""
    board = pcbnew.NewBoard(path)
    nets = {}
    for name in ("RF1", "RF2", "GND"):
        nets[name] = pcbnew.NETINFO_ITEM(board, name)
        board.Add(nets[name])
    p1 = rl._pad(rl._fp(board, "P1"), "1", 5.0, rl.Y, rl.TRACE_W, rl.TRACE_W,
                 nets["RF1"])
    p2 = rl._pad(rl._fp(board, "P2"), "1", 35.0, rl.Y, rl.TRACE_W, rl.TRACE_W,
                 nets["RF2"])
    fp = rl._fp(board, "L1", "10n")
    x = X_MID - 0.5 * GAP - 0.5 * PAD_L
    rl._pad(fp, "1", x, rl.Y, PAD_L, PAD_W, nets["RF1"])
    rl._pad(fp, "2", 2 * X_MID - x, rl.Y, PAD_L, PAD_W, nets["RF2"])
    # **The end of a track is ROUND, with the radius of half its width.**
    # A track that stops at the edge of the pad puts that cap 1.45 mm
    # further and the copper then BRIDGES the gap of the part: the first
    # board of this file gave ONE polygon and |S21| of -0.2 dB over the
    # whole sweep, with the element in a gap that no longer existed. The
    # track stops one cap before the gap, thus the cap ends in the pad.
    end = X_MID - 0.5 * GAP - 0.5 * rl.TRACE_W - 0.1
    rl._track(board, 5.0, end, nets["RF1"])
    rl._track(board, 2 * X_MID - end, 35.0, nets["RF2"])
    rl._outline_and_plane(board, nets["GND"])
    pcbnew.SaveBoard(path, board)
    return board, [p1, p2]


def simulate(outdir, mesh, epc):
    """Make the board, model it with or without the EPC, and solve it.

    The function gives (freq, S21) and the count of the mesh cells across
    the land of the part.
    """
    os.makedirs(outdir, exist_ok=True)
    board, pads = make_board(os.path.join(outdir, "epc_0402.kicad_pcb"))
    model = board_reader.extract(board, pads, margin_mm=MARGIN, f_stop=6e9,
                                 mesh=mesh)
    for p in model["ports"]:
        p["type"] = "msl"
    model["settings"] = {
        "f_start": 1e9, "f_stop": 6e9, "z0": 50.0, "margin_mm": MARGIN,
        "mesh": mesh, "n_freq": 501, "max_timesteps": 300000,
        "end_criteria": 1e-4, "lumped": True, "excite": [1]}
    les = model["lumped_elements"]
    assert len(les) == 1 and les[0]["ref"] == "L1", les
    les[0].update(type="L", value=L_NH * 1e-9, esl=0.0, esr=DCR,
                  package="Custom", epc=epc)
    path = os.path.join(outdir, "model.json")
    with open(path, "w") as fh:
        json.dump(model, fh, indent=1)

    solver_py = solverenv.solver_python() or sys.executable
    log = subprocess.run([solver_py, os.path.join(PLUGINS, "runner.py"),
                          path, outdir], capture_output=True, text=True)
    if log.returncode != 0:
        print(log.stdout[-3000:])
        print(log.stderr[-2000:])
        raise SystemExit("the solver failed")
    for line in log.stdout.splitlines():
        if ("lumped" in line or "ERROR" in line or "WARNING" in line
                or "timesteps" in line):
            print("  " + line.strip())
    rows = np.loadtxt(os.path.join(outdir, "results.s2p"), comments=("!", "#"))
    return rows[:, 0], rows[:, 3] + 1j * rows[:, 4]


def cells_across(outdir):
    """Give the mesh lines inside the land, across the current."""
    import runner
    with open(os.path.join(outdir, "model.json")) as fh:
        model = json.load(fh)
    s = model["settings"]
    eps = max(d["epsilon"] for d in model["dielectric_layers"])
    res = (runner.C0 / s["f_stop"] / np.sqrt(eps) * 1e3
           / runner.RES_DIV[s["mesh"]])
    lines = runner._mesh(model, runner._port_geometry(model, res), res)
    e = model["lumped_elements"][0]
    k = 1 if e["ny"] == "x" else 0
    lo, hi = sorted((e["start"][k], e["stop"][k]))
    return [v for v in lines[k] if lo - 1e-9 <= v <= hi + 1e-9]


def db(x):
    return 20 * np.log10(np.abs(x) + 1e-12)


def notch(f, s21):
    """Give the frequency of the deepest point of |S21| inside the band."""
    keep = (f > BAND[0]) & (f < BAND[1])
    y, x = db(s21)[keep], f[keep]
    i = int(np.argmin(y))
    return x[i], y[i]


def main(mesh="coarse"):
    want = 1.0 / (2 * np.pi * np.sqrt(L_NH * 1e-9 * EPC_PF * 1e-12))
    print("an inductor of %g nH with an EPC of %g pF on the land of an 0402, "
          "at the %s preset" % (L_NH, EPC_PF, mesh))
    print("the closed form gives the self-resonance at %.3f GHz\n"
          % (want / 1e9))
    got = {}
    for name, epc in (("no EPC", None), ("the EPC", EPC_PF * 1e-12)):
        print("=== %s ===" % name)
        outdir = os.path.join(HERE, "out_epc_%s_%s"
                              % ("none" if epc is None else "epc", mesh))
        got[name] = simulate(outdir, mesh, epc)
        if epc is None:
            lines = cells_across(outdir)
            print("  the land holds %d cell(s) across the current: %s"
                  % (len(lines) - 1, ", ".join("%.4f" % v for v in lines)))

    print("\n   f/GHz   no EPC   the EPC")
    f = got["the EPC"][0]
    for ft in np.arange(1.0, 6.01, 0.5) * 1e9:
        i = int(np.argmin(np.abs(f - ft)))
        print("   %5.2f  %7.2f  %8.2f"
              % (f[i] / 1e9, db(got["no EPC"][1])[i],
                 db(got["the EPC"][1])[i]))

    f_epc, depth = notch(*got["the EPC"])
    f_none, _ = notch(*got["no EPC"])
    err = f_epc / want - 1.0
    print("\nthe notch stands at %.3f GHz, thus %+.1f%% against the closed "
          "form" % (f_epc / 1e9, 100 * err))
    fails = []
    if abs(err) > EPC_TOL:
        fails.append("the self-resonance is %+.1f%% out, against the %d%% "
                     "that this file permits" % (100 * err, 100 * EPC_TOL))
    if depth > -15.0:
        fails.append("the notch is only %.1f dB deep, thus the parallel LC "
                     "does not block: read the log for an element that the "
                     "engine did not take" % depth)
    # The control must hold no resonance of its own near the notch, or
    # the board and not the EPC made it.
    if abs(f_none - f_epc) < 0.1 * want:
        fails.append("the board with NO EPC has its deepest point at %.3f "
                     "GHz as well, thus the notch is not the element"
                     % (f_none / 1e9))
    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the EPC validation FAILED")
    print("PASS: the self-resonance stands within %d%% of the closed form"
          % (100 * EPC_TOL))


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
