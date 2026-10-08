"""Push the coaxial feed of a through-hole pad through the SOLVER: the
probe-fed patch of kicad-rfgen, fed from below.

A connector below the board (an SMA) has its pin in a plated hole. The
pin goes up through the substrate to the patch, and the outer conductor
touches the ground around the clearance of the hole. The port of such a pad
is the coaxial feed (`solverenv.coax_box`): a lumped port across the gap
between the pad and the ground, flat on B.Cu. The current goes up to the
patch in the barrel of the hole (`board_reader._barrels`).

**The control is the probe of the openEMS tutorial of a patch**: the same
patch with an SMD pad of the pin dimension on F.Cu, a SOLID ground below
it, and a lumped port from the plane to the patch. A coaxial feed with a
small pad in a small clearance ("small") is almost that geometry. Thus the
two must give the same resonance AND the same resistance at it. The
closed form of the patch gives all of them a common scale.

**The pad of kicad-rfgen is NOT that geometry.** Its pad on B.Cu is 4.286
mm, in a hole of 7 mm in the ground below the patch. The two solvers agree
that this feed gives the patch about 1.85 times the resistance of the
probe, and about 1 nH more of series inductance (openEMS 132 ohm and 1.86
nH, EMerge 149 ohm and 1.84 nH, against 67 and 80 ohm for the probe, at
the coarse preset). The rig gives that ratio, and it tests only the
frequency of that feed.

Measured on 2026-10-06 (R0 is the resistance at the resonance, L the
series inductance of the pin, from a fit of Z):

    feed      EMerge coarse       openEMS coarse      openEMS medium
    small     80.8 ohm  0.83 nH   85.9 ohm            89.5 ohm  1.24 nH
    control   80.3 ohm  0.82 nH   67.4 ohm            73.4 ohm  0.66 nH
    rfgen    149.0 ohm  1.84 nH  132.0 ohm  1.86 nH  138.2 ohm  1.90 nH

**In EMerge the small feed IS the probe**, to 0.6% in R0 and to 0.01 nH.
**In openEMS the two lumped ports put R0 on the two sides of that value**:
+11% for the small feed (flat, across the gap) and -9% for the control
(vertical, a box of 2 x 2 cells), at medium. Thus the limits of the two
solvers are not the same, as for `rig_via`.

The resonance (the largest Re(Z)) against the closed form: EMerge -5.0% to
-4.2%. openEMS read -9.3% to -8.3% at coarse and -8.0% to -6.6% at medium
before the 1/3 - 2/3 rule at a long copper edge (`openems_runner.EDGE_THIRDS`):
a line ON the radiating edge made the patch electrically longer. With the
rule it reads -4.5% to -3.7% at coarse, -4.4% to -3.4% at medium and -4.6%
to -3.5% at fine. The resistances above are from before the rule; the rule
makes them 7% to 11% larger at coarse (the probe 74.8 ohm, the small feed
92.6 ohm), and the ratio of the small feed to the probe stays 1.24.

The stages:

- `extract` (no solver): the pad gets the coaxial feed on B.Cu, on the
  axis away from the centre, with the gap of the clearance, and a barrel
  of the drill. A through-hole pad with no ground around it stops
  `extract` with a message (the guard).
- `single`: the pad of kicad-rfgen, the small coaxial feed and the
  control, one feed each.
- `dual`: the two feeds of the dual-polarized patch. The isolation, the
  symmetry of the two feeds, and the co-pol and the cross-pol of Ludwig 3
  of each one.

Run it with the python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\openems\\run_probe_openems.py [mesh] [stage]
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\emerge\\run_probe_emerge.py [mesh] [stage]
"""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigsolve  # noqa: E402

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import make_test_board as mtb  # noqa: E402

C0 = 299792458.0
F_START, F_STOP = 2.0e9, 3.0e9
# The clear air around the board. A patch radiates, thus the NF2FF box
# must be in clear air and not near the copper.
MARGIN = 15.0
# The small coaxial feed: a pad of 1.7 mm (a ring of 0.215 mm around the
# drill of 1.27 mm) in a clearance of r 1.1 mm, thus a gap of 0.25 mm.
SMALL = (1.7, 1.1)
# The feeds of `single` must give the same resonance to this part. They
# are different only at the feed, and the cavity of the patch sets the
# frequency.
FEED_TOL = 0.02
# The small coaxial feed against the control: the largest Re(Z), which
# is the resistance of the patch at the feed. A series reactance (the pin)
# does not move it. EMerge gives the two to 0.6%. The two lumped ports of
# openEMS are 11% above and 9% below that value at medium, and 28% apart
# at coarse (the table above). The limit of openEMS still finds a short,
# an open, and the factor of 1.9 of the pad of kicad-rfgen.
R_TOL = {"openems": 0.35, "emerge": 0.10}
# Each resonance against the closed form of the patch (the transmission
# line model of Hammerstad, with the fringe of each radiating edge). That
# model is for a large ground, and this ground is 4.8 mm wider than the
# patch at each side. The two solvers read the patch 3.4% to 5.0% low.
F0_TOL = {"openems": 0.06, "emerge": 0.06}
# The feed must couple to the patch: the dip of S11 at the resonance.
DIP_MAX = -6.0
# `dual`: the isolation of the two feeds at the resonance, the symmetry of
# the two (they are the same feed, turned 90 degrees), and the XPD of
# each one at its main lobe. A feed on the axis of the patch is on the
# null of the orthogonal mode, thus a correct model gives a high XPD.
ISO_MAX = -15.0
SYM_F, SYM_DB = 0.005, 1.0
XPD_MIN = 15.0


def patch_f0(length, width, h, er):
    """Give the resonance of a rectangular patch in Hz (the transmission
    line model). The dimensions are in mm."""
    eeff = 0.5 * (er + 1) + 0.5 * (er - 1) / math.sqrt(1 + 12 * h / width)
    dl = (0.412 * h * (eeff + 0.3) * (width / h + 0.264)
          / ((eeff - 0.258) * (width / h + 0.8)))
    return C0 / (2.0 * (length + 2.0 * dl) * 1e-3 * math.sqrt(eeff))


def board(mesh, feed, dual=False, ground=True):
    """Give (the model, the folder, the tag) of a board. `feed` is "coax"
    (the pad of kicad-rfgen), "small" (a coaxial feed with `SMALL`) or
    "pad" (the probe of the tutorial)."""
    tag = "%s%s" % (feed, "_dual" if dual else "")
    outdir = rigsolve.out("out_probe_%s_%s" % (tag, mesh))
    os.makedirs(outdir, exist_ok=True)
    size, clear = SMALL if feed == "small" else (mtb.PROBE_PAD,
                                                  mtb.PROBE_CLEAR)
    brd, pads = mtb.make_probe_patch(os.path.join(outdir, "probe.kicad_pcb"),
                                     feed="pad" if feed == "pad" else "coax",
                                     dual=dual, ground=ground, pad=size,
                                     clear=clear)
    model = board_reader.extract(brd, pads, MARGIN, f_stop=F_STOP,
                                 mesh=mesh)
    return model, outdir, tag


def closed_form(model):
    d = model["dielectric_layers"]
    h = sum(x["z_top"] - x["z_bottom"] for x in d)
    return patch_f0(mtb.PATCH, mtb.PATCH, h, d[0]["epsilon"]), h, d[0]["epsilon"]


def settings(mesh, f_field, excite=None):
    s = {"f_start": F_START, "f_stop": F_STOP, "f_field": f_field,
         "z0": 50.0, "margin_mm": MARGIN, "mesh": mesh, "n_freq": 401,
         "max_timesteps": 300000, "end_criteria": 1e-4}
    if excite:
        s["excite"] = excite
    return s


def touchstone(path):
    """Give (f, S) of a Touchstone v1 file of 1 or 2 ports."""
    rows = np.loadtxt(path, comments=("!", "#"))
    f = rows[:, 0]
    v = rows[:, 1::2] + 1j * rows[:, 2::2]
    n = int(round(math.sqrt(v.shape[1])))
    S = np.zeros((len(f), n, n), complex)
    if n == 1:
        S[:, 0, 0] = v[:, 0]
    else:  # 2 ports: S11 S21 S12 S22, by column
        S[:, 0, 0], S[:, 1, 0], S[:, 0, 1], S[:, 1, 1] = v.T
    return f, S


def dip(f, s):
    """Give (the frequency, the depth in dB) of the deepest point of |s|,
    with a parabola through the three points at it."""
    db = 20 * np.log10(np.maximum(np.abs(s), 1e-12))
    i = int(np.clip(np.argmin(db), 1, len(db) - 2))
    y0, y1, y2 = db[i - 1:i + 2]
    den = y0 - 2 * y1 + y2
    k = 0.5 * (y0 - y2) / den if den else 0.0
    return float(f[i] + k * (f[1] - f[0])), float(db.min())


def stage_extract(mesh):
    print("extract: the coaxial feed, its barrel and the guard\n")
    fails = []
    model, _, _ = board(mesh, "coax")
    p = model["ports"][0]
    gap = (p["coax"].get("B.Cu", {}).get("r_out", 0)
           - p["coax"].get("B.Cu", {}).get("r_in", 0))
    print("   port 1: %s on %s, sides %s, the axis %s, a gap of %.3f mm"
          % (p["type"], p["coax_side"], sorted(p["coax"]),
             p["coax"].get("B.Cu", {}).get("dir"), gap))
    if p["type"] != "coax" or p["coax_side"] != "B.Cu":
        fails.append("the through-hole pad is a %s port on %s, and not a "
                     "coaxial feed on B.Cu" % (p["type"], p["coax_side"]))
    if "F.Cu" in p["coax"]:
        fails.append("F.Cu got a coaxial feed, but the pad is in the patch "
                     "there")
    if abs(gap - (mtb.PROBE_CLEAR - mtb.PROBE_PAD / 2)) > 0.02:
        fails.append("the gap is %.3f mm, and the clearance gives %.3f"
                     % (gap, mtb.PROBE_CLEAR - mtb.PROBE_PAD / 2))
    if p["coax"].get("B.Cu", {}).get("dir") != [1, 0]:
        fails.append("the feed at +x of the centre is not on +x")
    barrels = [v for v in model["vias"]
               if abs(v["x"] - p["x"]) < 1e-6 and abs(v["y"] - p["y"]) < 1e-6]
    print("   the barrel: %s" % barrels)
    if len(barrels) != 1 or abs(barrels[0]["r"] - mtb.PROBE_DRILL / 2) > 1e-6:
        fails.append("the pad has %d barrel(s) of the drill, and not 1"
                     % len(barrels))
    model, _, _ = board(mesh, "coax", dual=True)
    q = model["ports"][1]
    print("   port 2 of the dual feed: the axis %s"
          % q["coax"].get("B.Cu", {}).get("dir"))
    if q["coax"].get("B.Cu", {}).get("dir") != [0, -1]:
        fails.append("the second feed is not on its own axis, away from "
                     "the centre")
    try:
        board(mesh, "coax", ground=False)
        fails.append("a through-hole pad with no ground did not stop "
                     "extract()")
    except ValueError as e:
        print("   the guard: %s" % str(e).splitlines()[0])
    model, _, _ = board(mesh, "pad")
    if model["ports"][0]["type"] != "lumped" or model["ports"][0]["drill"]:
        fails.append("the SMD pad of the control is not a lumped port")
    return fails


def run(mesh, feed, dual, f_field):
    model, outdir, tag = board(mesh, feed, dual)
    model["settings"] = settings(mesh, f_field)
    t0 = time.time()
    txt = rigsolve.run(model, outdir, tag)
    unused = [ln.strip() for ln in txt.splitlines() if "Unused primitive" in ln]
    n = len(model["ports"])
    f, S = touchstone(os.path.join(outdir, "results.s%dp" % n))
    out = {"f": f, "S": S, "unused": unused, "outdir": outdir,
           "min": (time.time() - t0) / 60.0, "ff": {}}
    for k in range(n):
        path = os.path.join(outdir, "farfield_p%d.json" % (k + 1))
        if os.path.isfile(path):
            with open(path) as fh:
                out["ff"][k + 1] = json.load(fh)
    return out


def stage_single(mesh):
    print("\nsingle: the coaxial feed against the probe of the tutorial\n")
    fails = []
    model, _, _ = board(mesh, "pad")
    f0, h, er = closed_form(model)
    print("   the closed form: %.4f GHz (a patch of %.3f mm on %.3f mm, "
          "er %g)" % (f0 / 1e9, mtb.PATCH, h, er))
    res = {}
    for feed in ("coax", "small", "pad"):
        r = run(mesh, feed, False, f0)
        f = r["f"]
        z = 50.0 * (1 + r["S"][:, 0, 0]) / (1 - r["S"][:, 0, 0])
        # The resonance is the largest Re(Z): a series reactance of the
        # pin moves the dip of S11, and not this point.
        i = int(np.argmax(z.real))
        fr, rmax = float(f[i]), float(z.real[i])
        fd, depth = dip(f, r["S"][:, 0, 0])
        res[feed] = (fr, rmax)
        print("   %-5s the resonance %.4f GHz (%+.1f%% against the closed "
              "form), Re(Z) %.1f ohm there; S11 %.1f dB at %.4f GHz (%.1f "
              "min)" % (feed, fr / 1e9, 100 * (fr / f0 - 1), rmax, depth,
                        fd / 1e9, r["min"]))
        if r["unused"]:
            fails.append("%s: the solver did not use %d primitive(s): %s"
                         % (feed, len(r["unused"]), r["unused"][0]))
        if abs(fr / f0 - 1) > F0_TOL[rigsolve.SOLVER]:
            fails.append("%s: the resonance is %+.1f%% from the closed form, "
                         "and the limit is %g%%"
                         % (feed, 100 * (fr / f0 - 1),
                            100 * F0_TOL[rigsolve.SOLVER]))
        if depth > DIP_MAX:
            fails.append("%s: S11 has its dip at %.1f dB, thus the feed does "
                         "not couple to the patch (the limit is %g dB)"
                         % (feed, depth, DIP_MAX))
    fp, rp = res["pad"]
    for feed in ("small", "coax"):
        fc, rc = res[feed]
        print("   %-5s against the control: %+.2f%% in frequency, %.2f times "
              "the resistance" % (feed, 100 * (fc / fp - 1), rc / rp))
        if abs(fc / fp - 1) > FEED_TOL:
            fails.append("%s gives the resonance %+.2f%% from the control, "
                         "and the limit is %g%%"
                         % (feed, 100 * (fc / fp - 1), 100 * FEED_TOL))
    rs = res["small"][1]
    if abs(rs / rp - 1) > R_TOL[rigsolve.SOLVER]:
        fails.append("the small coaxial feed gives %.2f times the resistance "
                     "of the control, and the limit is %g%%: the feed is not "
                     "the probe" % (rs / rp, 100 * R_TOL[rigsolve.SOLVER]))
    return fails, res["coax"][0]


def stage_dual(mesh, f_res=None):
    print("\ndual: the two feeds of the dual-polarized patch\n")
    fails = []
    if f_res is None:
        prev = rigsolve.out("out_probe_coax_%s" % mesh)
        path = os.path.join(prev, "results.s1p")
        if os.path.isfile(path):
            # the resonance of `single`: the largest Re(Z)
            f, S = touchstone(path)
            f_res = float(f[int(np.argmax(((1 + S[:, 0, 0])
                                           / (1 - S[:, 0, 0])).real))])
        else:
            model, _, _ = board(mesh, "pad")
            f_res = closed_form(model)[0]
    r = run(mesh, "coax", True, f_res)
    f, S = r["f"], r["S"]
    f1, d1 = dip(f, S[:, 0, 0])
    f2, d2 = dip(f, S[:, 1, 1])
    i = int(np.argmin(np.abs(f - 0.5 * (f1 + f2))))
    iso = 20 * np.log10(abs(S[i, 1, 0]))
    print("   port 1: %.4f GHz, %.1f dB; port 2: %.4f GHz, %.1f dB (%.1f min)"
          % (f1 / 1e9, d1, f2 / 1e9, d2, r["min"]))
    print("   the isolation at %.4f GHz: S21 %.1f dB" % (f[i] / 1e9, iso))
    if iso > ISO_MAX:
        fails.append("S21 is %.1f dB at the resonance, and the limit is %g dB"
                     % (iso, ISO_MAX))
    if abs(f1 / f2 - 1) > SYM_F or abs(d1 - d2) > SYM_DB:
        fails.append("the two feeds are not the same: %+.2f%% and %+.1f dB"
                     % (100 * (f1 / f2 - 1), d1 - d2))
    for k, want in ((1, 0.0), (2, 90.0)):
        ff = r["ff"].get(k)
        if not ff or "xpd_dB" not in ff:
            fails.append("port %d has no co-pol and cross-pol" % k)
            continue
        print("   port %d: Dmax %.2f dBi, Ludwig 3 at %g deg, XPD %.1f dB at "
              "theta %g, phi %g (f = %.3f GHz)"
              % (k, ff["Dmax_dBi"], ff["ludwig3_ref_deg"], ff["xpd_dB"],
                 ff["main_lobe_deg"][0], ff["main_lobe_deg"][1],
                 ff["f_hz"] / 1e9))
        if ff["ludwig3_ref_deg"] != want:
            fails.append("port %d is polarized at %g deg, and the feed gives "
                         "%g" % (k, ff["ludwig3_ref_deg"], want))
        if ff["xpd_dB"] < XPD_MIN:
            fails.append("port %d has an XPD of %.1f dB, and the limit is "
                         "%g dB" % (k, ff["xpd_dB"], XPD_MIN))
        # Co and cross are the two parts of Abs: their powers add.
        g = ff["grid3d"]
        tot = 10 ** (np.asarray(g["D_co_dBi"]) / 10) \
            + 10 ** (np.asarray(g["D_cross_dBi"]) / 10)
        err = np.max(np.abs(10 * np.log10(tot) - np.asarray(g["D_dBi"])))
        if err > 0.01:
            fails.append("port %d: co + cross is %.3f dB from Abs" % (k, err))
    return fails


def main(mesh="coarse", stage="all"):
    fails = stage_extract(mesh)
    f_res = None
    if stage in ("all", "single"):
        more, f_res = stage_single(mesh)
        fails += more
    if stage in ("all", "dual"):
        fails += stage_dual(mesh, f_res)
    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the probe validation FAILED")
    print("PASS: the coaxial feed of a through-hole pad (%s)" % stage)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
