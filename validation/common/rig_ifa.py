"""Push a published antenna through the SOLVER: the meander inverted-F
antenna of TI SWRA117D (AN043), which TI measured on the CC2511 dongle.

**The board.** Table 1 of the note gives the copper of the antenna. The
reference design of TI (SWRC062) gives the remaining data. Its readme gives FR4
with er 4.5, layers of 0.25, 0.50 and 0.25 mm, and 35 um of copper. Its Gerber
files give the ground on layer 2, the board, and the two pours of layer 1. The
Gerber file and Table 1 agree to 0.01 mm. The model has simple outlines for the
pours, and 10 vias. It has no layer below the ground, no connector, no cable
and no part.

**The dip of S11**, against 2.48 GHz, -24 dB and a band of about 240 MHz from
the note (free space):

    solver    mesh       dip         depth     -10 dB band
    openEMS   coarse     2.320 GHz   -19.0 dB  2.228 to 2.440 GHz
    openEMS   medium     2.345 GHz   -24.3 dB  2.252 to 2.462 GHz
    openEMS   fine       2.312 GHz   -18.4 dB  2.232 to 2.408 GHz
    openEMS   ultrafine  2.285 GHz   -15.2 dB  2.215 to 2.368 GHz
    EMerge    coarse     2.312 GHz   -32.3 dB  2.223 to 2.430 GHz
    EMerge    medium     2.305 GHz   -27.5 dB  2.223 to 2.415 GHz

**The two solvers read 5.5 to 7.9% below the note.** The depth and the band
are near the note, and the cause of the frequency gap is not known. The note
gives these limits on its own data. IE3D gives 2.43 and 2.51 GHz at two meshes.
Plastic around the dongle gives 2.40 GHz. The position of the cable moves the
dip by 5 to 10 MHz. On a first board with a simple ground, er 4.2 moved the dip
by +1.5%, and a ground of 24 mm moved it by +1.7%. Layers 3 and 4 have almost no
copper near the antenna. These changes do not explain the gap, in openEMS:

    the change                         coarse      medium      fine
    none                               2.320 GHz   2.345 GHz   2.312 GHz
    the track and the pad of the feed  2.327 GHz   2.355 GHz   2.293 GHz
    er 4.2                             2.354 GHz   2.335 GHz   2.346 GHz
    the track, the pad and er 4.2      2.361 GHz   2.345 GHz   2.326 GHz

A shunt C of about 0.8 pF at the port moves the dip of openEMS to 2.47 GHz, with
no new run, but EMerge goes only to 2.43 GHz. The semi-rigid cable can add
capacitance, but the note does not give a value. **`F_TOL` is thus a limit
against a change of the result. It does not show that the frequency is
correct.**

**A small change of the mesh moves this board.** Four lines at a pour moved by
0.1 mm or less at ultrafine, and the dip moved from 2.310 GHz and -18.5 dB to
2.294 GHz and -15.7 dB. Ultrafine is near the limit of the dip and of the depth,
thus a change of the mesh can stop the rig at that preset.

**The test pins.** The note measures 2.50 GHz and -18.7 dB with no test pins,
thus the dip goes up by 20 MHz and it is 5.3 dB less deep. The models move in
the same direction:

    solver    mesh     dip with no pins  depth     the change
    openEMS   coarse   2.327 GHz         -16.5 dB  +6.6 MHz, +2.6 dB
    openEMS   medium   2.351 GHz         -19.8 dB  +6.6 MHz, +4.5 dB
    openEMS   fine     2.318 GHz         -16.2 dB  +5.6 MHz, +2.2 dB
    EMerge    coarse   2.313 GHz         -24.0 dB  +1.1 MHz, +8.2 dB

**The far field at 2.45 GHz.** The axes of the note are X = +y, Y = -x and Z =
+z of the model, from Figure 6 and from the figure of the dimensions. The three
cuts of the runner are then the three planes of the note. The horizontal trace
is in the plane of the cut, and the vertical trace is normal to it.

    item               the note     openEMS              EMerge
    XY, H above V      8.0 dB       37.1 to 37.2 dB      35.4 to 36.3 dB
    XZ, H above V      3.1 dB       16.0 to 16.7 dB      15.8 to 16.8 dB
    YZ, V above H      6.5 dB       22.0 to 23.9 dB      22.3 dB
    XY, H nulls        28, 195 deg  352, 184 to 186 deg  352, 186 deg
    Dmax               see below    2.33 to 2.89 dBi     2.30 to 2.38 dBi
    efficiency         not given    92 to 107%           76 to 83%

The larger polarization of each plane is the one of the note, thus the current
goes along the X axis, on the ground. The models have much less cross-pol than
the plots. An actual chamber, the connector and the feed can give that
difference. The rig tests the sign and not the value. The rig does not test the
efficiency, and the cause of the difference between the solvers is not known.

**The rig does not compare the gain.** The note gives 4.5 dB in XY and 5.3 dB
in XZ and in YZ, from a CC2511 at a nominal 0 dBm. The two solvers give a Dmax
of 2.30 to 2.89 dBi, and a pattern with the two nulls of a dipole
has about 2.15 dBi. A gain cannot be more than the directivity. Thus the level
of the note has an error, or the power was more than 0 dBm. The
plots give the peak of each trace as a value in handwriting. Some of their
scales cannot be read, thus the rig reads no full trace.

Run it with the python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\openems\\run_ifa_openems.py [mesh] [all|nominal|pins]
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" validation\\emerge\\run_ifa_emerge.py [mesh] [all|nominal|pins]
"""
import functools
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rigsolve  # noqa: E402

import numpy as np  # noqa: E402

import board_reader  # noqa: E402
import make_test_board as mtb  # noqa: E402
from rig_probe import dip, touchstone  # noqa: E402

F_START, F_STOP = 2.0e9, 3.0e9
F_FIELD = 2.45e9    # the patterns of the note are at 2450 MHz
MARGIN = 15.0
# The dip of S11 that the note measured in free space (Figure 2), and the
# limit around it. The limit holds the gap that the docstring tells.
F_MEASURED = 2.48e9
F_TOL = 0.08
DIP_MAX = -15.0     # the note measures -24 dB, and -18.7 dB with no test pins
BAND_MIN = 150e6    # the note measures about 240 MHz
# A short antenna with the current on its ground has about the directivity of
# a dipole, 2.15 dBi.
DMAX_RANGE = (1.0, 4.0)
# The polarization that the note gives as the larger in each plane, and the
# smallest difference that it gives (3.1 dB, in XZ). "h" is in the plane of the
# cut, and "v" is normal to it.
PLANES = (("XY", "Theta=90", "E_phi", "E_theta", 8.0),
          ("XZ", "Phi=90", "E_theta", "E_phi", 3.1),
          ("YZ", "Phi=0", "E_phi", "E_theta", 6.5))
POL_MIN = 3.0
# The two nulls of the horizontal trace of Figure 8, read from the plot in the
# angle of the note, and the limit. The model has no connector and no cable.
NULLS_MEASURED = (28.0, 195.0)
NULL_TOL = 40.0


def board(mesh, pins=True, tag="nominal"):
    outdir = rigsolve.out("out_ifa_%s_%s" % (tag, mesh))
    os.makedirs(outdir, exist_ok=True)
    brd, pads = mtb.make_ifa(os.path.join(outdir, "ifa.kicad_pcb"), pins)
    model = board_reader.extract(brd, pads, MARGIN, f_stop=F_STOP, mesh=mesh)
    return model, outdir


def settings(mesh):
    return {"f_start": F_START, "f_stop": F_STOP, "f_field": F_FIELD,
            "z0": 50.0, "margin_mm": MARGIN, "mesh": mesh, "n_freq": 401,
            "max_timesteps": 300000, "end_criteria": 1e-4}


def band(f, s, level=-10.0):
    """Give the edges of the band around the dip where |s| is below
    `level` dB, or None."""
    db = 20 * np.log10(np.maximum(np.abs(s), 1e-12))
    i = int(np.argmin(db))
    if db[i] > level:
        return None
    lo, hi = i, i
    while lo > 0 and db[lo - 1] < level:
        lo -= 1
    while hi < len(db) - 1 and db[hi + 1] < level:
        hi += 1
    return float(f[lo]), float(f[hi])


def stage_extract(mesh):
    print("extract: the board of the note\n")
    fails = []
    model, _ = board(mesh)
    p = model["ports"][0]
    print("   port 1: %s, the reference %s, source of the stackup %s"
          % (p["type"], p["ref_layer"], model["stackup_source"]))
    print("   the dielectrics: %s"
          % [round(d["z_top"] - d["z_bottom"], 3)
             for d in model["dielectric_layers"]])
    print("   the vias: %s" % model["vias"])
    if p["type"] != "lumped" or p["ref_layer"] != "In1.Cu":
        fails.append("the feed is a %s port on %s, and not a lumped port to "
                     "the ground on In1.Cu" % (p["type"], p["ref_layer"]))
    if model["stackup_source"] != "file":
        fails.append("the stackup did not come from the board file")
    n_vias = len(mtb.GB_VIAS) + 1
    if len(model["vias"]) != n_vias:
        fails.append("the board has %d vias, and not %d"
                     % (len(model["vias"]), n_vias))
    pts = mtb.ifa_outline()
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    w = max(xs) - min(xs) + mtb.IFA_D1 + mtb.IFA_D3
    h = max(ys) - min(ys) + mtb.IFA_D2
    print("   the antenna with its margins: %.2f x %.2f mm "
          "(the note: 15.2 x 5.7)" % (w, h))
    if abs(w - 15.2) > 1e-6 or abs(h - 5.7) > 1e-6:
        fails.append("the outline is %.2f x %.2f mm, and the note gives "
                     "15.2 x 5.7" % (w, h))
    return fails


def pol_db(cut, name):
    """Give one field component of a cut in dBi."""
    e = np.abs(np.asarray(cut[name]["re"]) + 1j * np.asarray(cut[name]["im"]))
    t = np.abs(np.asarray(cut["E_theta"]["re"])
               + 1j * np.asarray(cut["E_theta"]["im"]))
    p = np.abs(np.asarray(cut["E_phi"]["re"])
               + 1j * np.asarray(cut["E_phi"]["im"]))
    total = np.maximum(np.sqrt(t ** 2 + p ** 2), 1e-30)
    return np.asarray(cut["D_dBi"]) + 20 * np.log10(np.maximum(e / total,
                                                               1e-9))


def nulls(cut):
    """Give the angles of the note of the two deepest nulls of the horizontal
    trace in the board plane. The X axis of the note goes from the ground to
    the antenna (+y here), its angle goes to -Y (+x here), thus it is
    90 - phi."""
    db = pol_db(cut, "E_phi")[:-1]
    phi = np.asarray(cut["angle_deg"])[:-1]
    n = len(db)
    low = [i for i in range(n) if db[i] < db[i - 1]
           and db[i] < db[(i + 1) % n]]
    low = sorted(sorted(low, key=lambda i: db[i])[:2],
                 key=lambda i: (90.0 - phi[i]) % 360.0)
    return [float((90.0 - phi[i]) % 360.0) for i in low]


@functools.lru_cache(maxsize=None)  # "all" asks for the nominal run two times
def run(mesh, **kw):
    model, outdir = board(mesh, **kw)
    model["settings"] = settings(mesh)
    t0 = time.time()
    rigsolve.run(model, outdir, "ifa")
    f, S = touchstone(os.path.join(outdir, "results.s1p"))
    s = S[:, 0, 0]
    fd, depth = dip(f, s)
    edges = band(f, s)
    print("   %s: S11 %.1f dB at %.4f GHz (%+.1f%% against the note), -10 dB "
          "band %s (%.1f min)"
          % (kw.get("tag", "nominal"), depth, fd / 1e9,
             100 * (fd / F_MEASURED - 1),
             "%.3f to %.3f GHz" % (edges[0] / 1e9, edges[1] / 1e9)
             if edges else "none", (time.time() - t0) / 60.0), flush=True)
    with open(os.path.join(outdir, "farfield_p1.json")) as fh:
        ff = json.load(fh)
    return fd, depth, edges, ff


def stage_nominal(mesh):
    print("\nnominal: the antenna against the note\n")
    fails = []
    fd, depth, edges, ff = run(mesh)
    if abs(fd / F_MEASURED - 1) > F_TOL:
        fails.append("the dip is %+.1f%% from the note, and the limit is %g%%"
                     % (100 * (fd / F_MEASURED - 1), 100 * F_TOL))
    if depth > DIP_MAX:
        fails.append("the dip is %.1f dB, and the limit is %g dB"
                     % (depth, DIP_MAX))
    if not edges or edges[1] - edges[0] < BAND_MIN:
        fails.append("the -10 dB band is smaller than %g MHz"
                     % (BAND_MIN / 1e6))
    print("   Dmax %.2f dBi at %.3f GHz" % (ff["Dmax_dBi"], ff["f_hz"] / 1e9))
    if not DMAX_RANGE[0] <= ff["Dmax_dBi"] <= DMAX_RANGE[1]:
        fails.append("Dmax is %.2f dBi, and the limit is %g to %g dBi"
                     % ((ff["Dmax_dBi"],) + DMAX_RANGE))
    for name, key, big, small, ti in PLANES:
        cut = ff["cuts"][key]
        diff = pol_db(cut, big).max() - pol_db(cut, small).max()
        print("   %s: %s is %.1f dB above %s (the note: %.1f dB)"
              % (name, big, diff, small, ti))
        if diff < POL_MIN:
            fails.append("%s: %s is %.1f dB above %s, and the limit is %g dB"
                         % (name, big, diff, small, POL_MIN))
    found = nulls(ff["cuts"]["Theta=90"])
    print("   the nulls in XY: %s deg (the note: %s)"
          % (found, list(NULLS_MEASURED)))
    for want in NULLS_MEASURED:
        off = min(abs((a - want + 180.0) % 360.0 - 180.0) for a in found)
        if off > NULL_TOL:
            fails.append("no null within %g deg of %g deg in XY"
                         % (NULL_TOL, want))
    return fails


def stage_pins(mesh):
    """The note measures a higher and a less deep dip with no test pins."""
    print("\npins: the board with and with no test pins\n")
    fails = []
    fd, depth, _, _ = run(mesh)
    fd2, depth2, _, _ = run(mesh, pins=False, tag="nopins")
    print("   the change: %+.1f MHz, %+.1f dB (the note: +20 MHz, +5.3 dB)"
          % ((fd2 - fd) / 1e6, depth2 - depth))
    if fd2 < fd:
        fails.append("the dip with no test pins is lower, by %.1f MHz"
                     % ((fd - fd2) / 1e6))
    if depth2 < depth:
        fails.append("the dip with no test pins is deeper, by %.1f dB"
                     % (depth - depth2))
    return fails


def main(mesh="coarse", stage="all"):
    fails = stage_extract(mesh)
    if stage in ("all", "nominal"):
        fails += stage_nominal(mesh)
    if stage in ("all", "pins"):
        fails += stage_pins(mesh)
    print("\n" + "=" * 62)
    for m in fails:
        print("FAIL: %s" % m)
    if fails:
        raise SystemExit("the IFA validation FAILED")
    print("PASS: the antenna of TI SWRA117D (%s)" % stage)


if __name__ == "__main__":
    main(*(sys.argv[1:] or ["coarse"]))
