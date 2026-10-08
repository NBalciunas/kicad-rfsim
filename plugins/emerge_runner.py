"""The EMerge runner, which is a process of its own: it changes model.json
into a Touchstone file with the EMerge FEM solver.

This is an EXPERIMENT adjacent to the openEMS runner. It reads the same
model.json and writes the same results.sNp, thus the results window does
not change. Start it manually with:

    python emerge_runner.py model.json output_dir

EMerge is a frequency-domain solver. It solves the full S-matrix at each
frequency in ONE solve, thus there is no run for each port and no timestep.
It solves at `fem_points` frequencies only, and a vector fit gives the
`n_freq` points of the sweep. It also solves at the frequency of the field
views, and it writes the E-field and the H-field of each excited port in the
format of the openEMS dumps, and the far field in the format of
farfield_pN.json.

A port of type msl, cpw or stripline is a WAVE port (`_wave_port`): the
2D mode of the line on a face of the model, with its Z0 and its eps_eff in
lines.json. A lumped port, and a line port that has no track, are lumped
ports.

The limits of this model:

- The copper has no thickness: each side of a sheet has the surface
  impedance of copper. A via is PEC.
- A wave port of an open line (msl, cpw) still loses a small part of the
  power. A microstrip of 20 mm with PEC copper and no dielectric loss
  gives |S11|^2 + |S21|^2 = 0.969 to 0.996 from 1 to 6 GHz (0.92 to 0.96
  with the projection of EMerge, `_power_overlap`), with its lowest value
  near 1.5 GHz. The same stripline with no loss gives 1.000. The field of
  the open line is not the mode of the metal box of the face, and the
  port boundary absorbs the difference: about 1.7% of the power at 1.5
  and 2 GHz. The box also radiates about 1%. A finer mesh, a larger air
  box and a first-order absorber do not change it. A larger face moves
  the lowest value to a lower frequency.
"""
import glob
import json
import os
import shutil
import sys
import time

# EMerge writes characters that are not in cp1252, and the run dialog reads
# UTF-8.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import numpy as np

# The rules that the two runners share.
import solverenv

MM = 1e-3
# The maximum element size, as a part of the wavelength, for each preset.
# EMerge uses elements of the second order.
RESOLUTION = {"coarse": 0.25, "medium": 0.2, "fine": 0.15, "ultrafine": 0.1}
# The element size at the edges of the copper, as a part of the thinnest
# dielectric layer, for each preset.
EDGE_PART = {"coarse": 0.5, "medium": 0.35, "fine": 0.25, "ultrafine": 0.15}
# The edge size does not go below this value, thus a thin prepreg does not
# make a very large mesh on all the board.
EDGE_MIN_MM = 0.1
# The size in the box of a narrow gap or strip of a port, as a part of its
# width, for each preset (`_feature_boxes`). The CPW of the rigs (a gap of
# 0.3 mm) at the coarse preset read Z0 46.0, 47.2 and 47.7 ohm at 1, 0.5
# and 0.34 of the gap, in 66 k, 128 k and 231 k tetrahedra; openEMS reads
# 48.3 ohm and the closed form 51.0 ohm. eps_eff read 2.91 at each size.
FEATURE_PART = {"coarse": 0.5, "medium": 0.34, "fine": 0.25,
                "ultrafine": 0.2}
# The default count of solved frequencies for each preset.
FEM_POINTS = {"coarse": 11, "medium": 21, "fine": 31, "ultrafine": 41}
# The field views sample the plane at about this count of points. The
# samples come from the FEM solution, thus the count sets only the picture.
FIELD_POINTS = 60000
FIELD_STEP_MIN_MM = 0.05
# The sigma of the smoothing of the field views, as a part of the element
# at the copper edges (`_write_fields`).
FIELD_BLUR = 0.5
# The speed of light, in m/s.
C0_M = 299792458.0
# The impedance of free space, in ohm.
Z_FREE = 376.730313668
# The wave port (`_wave_port`): the largest air above the strip and the
# largest width at each side, in thicknesses of the substrate, and the
# depth of the void behind the face. The face also stays narrower and lower
# than half of the wavelength at f_stop, thus the metal box of the face
# carries no mode of its own. On the lossless microstrip of 20 mm of the
# rigs (1 to 6 GHz), a face of 6 x 5 lost 0.9% to 4.8% of the power and a
# face of 12 x 7.2 lost 0.25% to 2.6%. A face of 3 x 2.5 lost 9.6%.
WAVE_UP, WAVE_SIDE, WAVE_DEPTH_MM = 12.0, 10.0, 1.0
# The smallest air above the strip and width at each side, in thicknesses
# of the substrate, when the half wavelength is smaller.
WAVE_MIN = 1.0
# The TE10 cutoff of the face in air, as a part of f_stop. The substrate in
# the face lowers the cutoff by some percent. A face with its cutoff at
# f_stop (25 mm at 6 GHz) had a second mode at 6 GHz.
WAVE_CUTOFF = 1.1
# The mode search of a stripline port starts at this part above sqrt(er)
# (`_neff_guess`). At sqrt(er) itself, the shift of the eigen solve is the
# eigenvalue of the line.
WAVE_NEFF_TOP = 1.02
# The largest Ez/Exy and Hz/Hxy of a mode that is still quasi-TEM.
QTEM_LIMIT = 0.25
# The sides of the prism that stands for a via barrel. `run_via_emerge.py`
# at the coarse preset read +19% to +27% against Goldfarb and Pucel with 8
# sides, and +15% to +23% with 16 (the limit of the rig is 25%).
VIA_SIDES = 16


def say(text):
    print("[rfsim] %s" % text, flush=True)


def _impedance(comp, epc):
    """Give Z(f) of a series R-L-C branch, with `epc` in parallel.

    `comp` has the R, the L and the C in SI, from `solverenv.components`. A
    component that is not in `comp` is not in the branch.
    """
    r, l, c = comp.get("R", 0.0), comp.get("L", 0.0), comp.get("C", 0.0)

    def z(f):
        w = 2 * np.pi * f
        zs = r + 1j * w * l + (1.0 / (1j * w * c) if c else 0.0)
        if epc:
            zs = 1.0 / (1.0 / zs + 1j * w * epc) if zs else 0.0
        return zs
    return z


def _area(ring):
    """Give the signed area of a ring (the shoelace formula)."""
    n = len(ring)
    return 0.5 * sum(ring[i][0] * ring[(i + 1) % n][1]
                     - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))


def _rings(poly):
    """Give (the outline, the holes) of a fractured polygon.

    **KiCad keeps a pour with holes as ONE outline** (`Fracture`). The
    outline goes into each hole along a slit with no width, and it comes
    back along the same slit. openEMS rasterizes such a polygon correctly.
    gmsh makes a surface of it, and a slit with no width makes that
    surface degenerate. A ground zone with the clearances of three vias
    gave S11 = 0 dB and S21 = -51 dB: the plane was not in the model.

    A slit shows as a point that the outline visits two times. The
    shortest loop between two visits is a hole or a spike of the slit. A
    loop with an area is a hole, and a loop with no area is a spike. The
    loop leaves the outline each time, until no point repeats.
    """
    pts = [(round(x, 6), round(y, 6)) for x, y in poly]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    loops = []
    while True:
        seen, best = {}, None
        for j, q in enumerate(pts):
            if q in seen and (best is None
                              or j - seen[q] < best[1] - best[0]):
                best = (seen[q], j)
            seen[q] = j
        if best is None:
            break
        i, j = best
        loop = pts[i:j]
        if len(loop) >= 3 and abs(_area(loop)) > 1e-9:
            loops.append(loop)
        pts = pts[:i] + pts[j:]
    return pts, loops


def _feature_boxes(ports, z_of, edge, box, part=0.5):
    """Give the boxes of a fine mesh along the feed of each port, as
    (x0, y0, z0, x1, y1, z1, size) in mm.

    **Only the narrow feature gets the fine mesh, and not all its copper.**
    A coplanar gap of a port that is narrower than `edge` gets a slim box
    along the feed: as wide as the gap and as high as the gap, each with a
    margin of two cells. A strip that is narrower than `edge` gets the same
    box over the strip. The box goes from the far edge of the pad along the
    copper run of the feed; `box` is the air box, which caps a run that has
    no length.

    Measured on the CPW of the rigs (a gap of 0.3 mm): the size of the gap
    on all the copper edges of the layer used 24 GB of 32 GB for the line
    of 60 mm. One box over the strip and the two gaps, 4.3 mm high, gave
    109 k tetrahedra at 0.3 mm and 437 k at 0.15 mm.
    """
    out = []
    for p in ports:
        d = p.get("direction")
        if p.get("type") not in ("msl", "cpw", "stripline") or not d:
            continue
        w = p.get("track_width") or (p["width"] if d[0] else p["length"])
        gap = p.get("gap") or 0.0
        # (the offset across the feed, the width) of each narrow slot
        slots = []
        if gap and gap < edge:
            off = 0.5 * w + 0.5 * gap
            slots = [(-off, gap), (off, gap)]
        elif w < edge:
            slots = [(0.0, w)]
        pad = 0.5 * (p["length"] if d[0] else p["width"])
        run = p.get("copper_run") or 1e9
        s = d[0] or d[1]
        a = (p["x"] if d[0] else p["y"]) - s * pad
        b = (p["x"] if d[0] else p["y"]) + s * run
        lo, hi = sorted((a, b))
        lo = max(lo, box[0] if d[0] else box[1])
        hi = min(hi, box[2] if d[0] else box[3])
        z_top = z_of[p["layer"]]
        c = p["y"] if d[0] else p["x"]
        for off, width in slots:
            size = max(part * width, EDGE_MIN_MM)
            half = 0.5 * width + 2.0 * size
            dz = width + 2.0 * size
            if d[0]:
                out.append((lo, c + off - half, z_top - dz, hi,
                            c + off + half, z_top + dz, size))
            else:
                out.append((c + off - half, lo, z_top - dz,
                            c + off + half, hi, z_top + dz, size))
    return out


def _wave_port(p, z_of, box, f_max, layers):
    """Give the void and the face of the wave port of `p`, or give the
    cause why the port stays a lumped port.

    **A modal port of EMerge must be on a face of the model.** A port of the
    board is in the board, thus the runner cuts a VOID out of the air and
    the dielectric behind the port: `WAVE_DEPTH_MM` deep, against the feed
    direction, from the centre of the pad. The face of the void that looks
    along the feed is the port. Its other faces stay PEC, which is the
    default of EMerge for a face with no condition: that is the metal box of
    a wave port in a commercial FEM tool.

    The face goes across the strip, from the reference plane to
    `WAVE_UP` substrates above the strip (msl, cpw), or from plane to plane
    (stripline). It is `WAVE_SIDE` substrates wider than the strip at each
    side, and a cpw adds its gaps. The walls must not touch the field of
    the line, and they must not touch copper of an other net.

    **The face is also no wider and no higher than half of the wavelength
    at `WAVE_CUTOFF` x `f_max`**, in air for msl and cpw, and in the
    dielectric for a stripline, which fills its face. Then the TE10 mode of
    the metal box of the face is cut off, and the port has one mode.
    `WAVE_MIN` substrates is the lower limit.

    The result is (x0, y0, z0, x1, y1, z1) of the void in mm, and
    (origin, u, v) of the face; or a text with the cause.
    """
    d = p.get("direction")
    if p.get("type") not in ("msl", "cpw", "stripline"):
        return None
    if not d:
        return "it has no track"
    if p["type"] == "cpw" and not p.get("gap"):
        return "it has no coplanar gap"
    if p["type"] == "stripline" and not p.get("ref_layer2"):
        return "it has no plane above and below the strip"
    w = p.get("track_width") or (p["width"] if d[0] else p["length"])
    z_top, z_ref = z_of[p["layer"]], z_of[p["ref_layer"]]
    h = abs(z_top - z_ref)
    # Half of the wavelength at WAVE_CUTOFF x f_max, in mm, in air.
    lim = 0.5 * C0_M / (WAVE_CUTOFF * f_max) * 1e3
    if p["type"] == "stripline":
        z_lo, z_hi = sorted((z_ref, z_of[p["ref_layer2"]]))
        er = max([d["epsilon"] for d in layers
                  if d["z_bottom"] < z_hi and d["z_top"] > z_lo] or [1.0])
        lim /= np.sqrt(er)
        core = 0.5 * w
        g = z_hi - z_lo
    else:
        core = 0.5 * w + (p.get("gap") or 0.0)
        g = h
        up = max(min(WAVE_UP * h, lim - h), WAVE_MIN * h)
        up = up if z_top > z_ref else -up
        z_lo, z_hi = sorted((z_ref, z_top + up))
    half = core + max(min(WAVE_SIDE * g, 0.5 * lim - core), WAVE_MIN * g)
    z_lo, z_hi = max(z_lo, box[4]), min(z_hi, box[5])
    k = 0 if d[0] else 1
    s = d[k]
    c = (p["x"], p["y"])[k]           # the face: the centre of the pad
    x = (p["y"], p["x"])[k]           # the centre across the feed
    back = c - s * WAVE_DEPTH_MM
    lo, hi = sorted((c, back))
    xa, xb = max(x - half, box[1 - k]), min(x + half, box[3 - k])
    if k == 0:
        void = (lo, xa, z_lo, hi, xb, z_hi)
        face = ((c, xa, z_lo), (0.0, xb - xa, 0.0), (0.0, 0.0, z_hi - z_lo))
    else:
        void = (xa, lo, z_lo, xb, hi, z_hi)
        face = ((xa, c, z_lo), (xb - xa, 0.0, 0.0), (0.0, 0.0, z_hi - z_lo))
    return void, face


def _cut(em, obj, void):
    """Give `obj` less the box `void` (mm), when the two touch."""
    x0, y0, z0, x1, y1, z1 = void
    tool = em.geo.Box((x1 - x0) * MM, (y1 - y0) * MM, (z1 - z0) * MM,
                      position=(x0 * MM, y0 * MM, z0 * MM))
    try:
        return em.geo.remove(obj, tool)
    except Exception:
        return obj


def _port_plate(p, z_of):
    """Give the plate of a board port: (origin, u, v, width, height, sign).

    The plate goes from the reference layer to the pad, and it is normal to
    the feed. A port with a track stands at the edge of the pad that is
    opposite to the track. Thus all the copper of the line is on one side
    of it. A port with no track stands across the centre of the pad.
    """
    z_top, z_ref = z_of[p["layer"]], z_of[p["ref_layer"]]
    # `width` is the extent of the pad in y, and `length` in x. The plate
    # is as wide as the pad across the feed, because it stands on the pad.
    d = p.get("direction") or [0, 0]
    if d[1]:
        w = p["length"]
        origin = (p["x"] - w / 2, p["y"] - d[1] * p["width"] / 2)
        u = (w, 0.0, 0.0)
    else:
        w = p["width"]
        origin = (p["x"] - d[0] * p["length"] / 2, p["y"] - w / 2)
        u = (0.0, w, 0.0)
    h = abs(z_top - z_ref)
    return ((origin[0], origin[1], min(z_top, z_ref)), u, (0.0, 0.0, h),
            w, h, 1 if z_top > z_ref else -1)


# The fit must go through each solved point to this distance in |S|.
FIT_TOLERANCE = 0.02
# The fit must not give out more power than it gets, to this margin.
FIT_POWER_MAX = 1.02
# The pole counts that the fit tries, in this sequence. EMerge selects the
# count itself for "auto".
FIT_POLES = ("auto", 4, 6, 8, 10, 14, 20)


def _power_overlap():
    """Make EMerge give the S-parameters of a wave port from E x h*.

    **EMerge projects the field on the mode of the port with E . e***
    (`sparam.py`). That is the orthogonality of the modes of a guide with
    ONE dielectric. A microstrip has two, thus the field of the line is
    not the 2D mode exactly, and E . e* does not count the power of the
    difference. The projection with E x h* . n is the orthogonality of the
    modes of a guide with no loss and any dielectrics:

        b = int((E - q e) x h*) . n dA / int(e x h*) . n dA

    with q = 1 at the excited port. Measured on the lossless microstrip of
    20 mm of the rigs: 0.99 W of the 1 W went through the line at 1, 3.5
    and 6 GHz, and E . e* gave |S21|^2 = 0.963, 0.926 and 0.940. E x h* on
    the same solution gave 0.991, 0.963 and 0.986, and S21 = S12 to 3e-5.

    The two functions of EMerge are replaced in their module, which
    `_compute_s_data` reads at each call. A lumped port keeps the functions
    of EMerge. Give None, or the cause when this EMerge has other functions.
    """
    import inspect
    try:
        from emerge._emerge.physics.microwave import sparam
        from emerge._emerge.physics.microwave.bcs.port_bcs import ModalPort
        from emerge._emerge.mth.integrals import surface_integral
    except ImportError as e:
        return "EMerge has no module %s" % e.name
    want = {"sparam_field_power": ["nodes", "tri_vertices", "bc", "mode_nr",
                                   "active", "k0", "fieldf", "const",
                                   "gq_order"],
            "sparam_mode_power": ["nodes", "tri_vertices", "bc", "mode_nr",
                                  "k0", "const", "gq_order"]}
    for name, args in want.items():
        f = getattr(sparam, name, None)
        if f is None or list(inspect.signature(f).parameters) != args:
            return "its %s is not the one of EMerge 2.8.9" % name
    field0, mode0 = sparam.sparam_field_power, sparam.sparam_mode_power

    def overlap(nodes, tris, bc, mode_nr, k0, efun, const, order):
        p = nodes[:, tris[:, 0]]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        n = n / np.linalg.norm(n)

        def f(x, y, z):
            h = bc.port_mode_3d_global(x, y, z, k0, which="H",
                                       mode_nr=mode_nr)
            c = np.cross(np.asarray(efun(x, y, z)), np.conj(h), axis=0)
            return n[0] * c[0] + n[1] * c[1] + n[2] * c[2]
        return surface_integral(nodes, tris, f, const, gq_order=order)

    def field_power(nodes, tri_vertices, bc, mode_nr, active, k0, fieldf,
                    const, gq_order=4):
        if not isinstance(bc, ModalPort):
            return field0(nodes, tri_vertices, bc, mode_nr, active, k0,
                          fieldf, const, gq_order)
        q = 1.0 if active else 0.0

        def efun(x, y, z):
            e = bc.port_mode_3d_global(x, y, z, k0, which="E",
                                       mode_nr=mode_nr)
            return np.asarray(fieldf(x, y, z)) - q * e
        return overlap(nodes, tri_vertices, bc, mode_nr, k0, efun, const,
                       gq_order)

    def mode_power(nodes, tri_vertices, bc, mode_nr, k0, const, gq_order=4):
        if not isinstance(bc, ModalPort):
            return mode0(nodes, tri_vertices, bc, mode_nr, k0, const,
                         gq_order)

        def efun(x, y, z):
            return bc.port_mode_3d_global(x, y, z, k0, which="E",
                                          mode_nr=mode_nr)
        return overlap(nodes, tri_vertices, bc, mode_nr, k0, efun, const,
                       gq_order)

    sparam.sparam_field_power = field_power
    sparam.sparam_mode_power = mode_power
    return None


def _neff_guess(p, z_of, layers):
    """Give the effective index of the line mode of a wave port, from a
    closed form: the start of its mode search (`_mode_search`).

    msl: Hammerstad and Jensen, static. cpw: (er + 1) / 2, which is not the
    slowest mode: a grounded CPW with no vias has a mode of the strip and
    the top grounds against the plane (eps_eff 3.78 on the CPW of the rigs,
    against 2.88 for the CPW mode). stripline: er, a little above.
    """
    w = p.get("track_width") or (p["width"] if p["direction"][0]
                                 else p["length"])
    z0, z1 = sorted((z_of[p["layer"]], z_of[p["ref_layer"]]))
    if p["type"] == "stripline":
        z0, z1 = sorted((z0, z1, z_of[p["ref_layer2"]]))[::2]
    parts = [(min(d["z_top"], z1) - max(d["z_bottom"], z0), d["epsilon"])
             for d in layers if d["z_bottom"] < z1 and d["z_top"] > z0]
    t = sum(a for a, _ in parts)
    er = sum(a * e for a, e in parts) / t if t > 0 else 1.0
    if p["type"] == "stripline":
        return np.sqrt(er) * WAVE_NEFF_TOP
    if p["type"] == "cpw":
        return np.sqrt(0.5 * (er + 1.0))
    u = w / (z1 - z0)
    a = (1.0 + np.log((u ** 4 + (u / 52.0) ** 2) / (u ** 4 + 0.432)) / 49.0
         + np.log(1.0 + (u / 18.1) ** 3) / 18.7)
    b = 0.564 * ((er - 0.9) / (er + 3.0)) ** 0.053
    return np.sqrt((er + 1) / 2.0
                   + (er - 1) / 2.0 * (1.0 + 10.0 / u) ** (-a * b))


def _mode_search(mw, neff):
    """Make the mode solve of each wave port start at `neff` of its port
    number (`_neff_guess`).

    **EMerge 2.8.9 searches near sqrt((er_max + er_min) / 2)** of the face,
    and it overwrites the estimate of the port with that value. On a
    microstrip of FR-4 that is 1.66, between the line mode (1.89) and a mode
    of the metal box of the face (1.51 at 6 GHz). Port 1 of the via board
    then took the box mode at 6 GHz: Z0 3.6 ohm, and S21 = 0.
    """
    orig = mw.modal_analysis

    def modal(port, *args, **kw):
        n = neff.get(getattr(port, "port_number", None))
        if n and kw.get("target_kz") is None and kw.get("target_neff") is None:
            kw["target_neff"] = n
        return orig(port, *args, **kw)
    mw.modal_analysis = modal


def _write_lines(g, ports, wave, outdir):
    """Write lines.json from the modes of the wave ports: Z0 and eps_eff
    of each line at each solved frequency, as the openEMS runner does."""
    fs = np.squeeze(g.freq)
    Z = np.asarray(g.Z0)
    B = np.asarray(g.beta)
    k0 = 2 * np.pi * fs / C0_M
    out = {}
    for j, p in enumerate(ports):
        if p["number"] not in wave:
            continue
        z = Z[..., j].reshape(-1)
        eps = (np.real(B[..., j].reshape(-1)) / k0) ** 2
        out[str(p["number"])] = {"Z0_real": np.real(z).tolist(),
                                 "Z0_imag": np.imag(z).tolist(),
                                 "eps_eff": eps.tolist()}
    with open(os.path.join(outdir, "lines.json"), "w") as fh:
        json.dump({"freq_hz": fs.tolist(), "ports": out}, fh, indent=1)


def _fit(g, ports, freq):
    """Give the S-matrix at `freq` from the solved points, the pole count
    of the fit, and the cause when no fit is used.

    A vector fit gives a smooth, causal curve between the solved points.
    **A fit can go through each point and be incorrect between them.** On
    a CPW board with floating grounds, 11 points gave |S11| = +2.5 dB
    between two points, and each solved point was passive. Thus a fit is
    used only when it goes through the solved points and stays passive.

    **The pole count that EMerge selects ("auto") is not always a good
    one.** On the shunt boards of the rigs, "auto" went through each point
    to 0.001 and gave sum|S|^2 of 1.23, 5.6 and 13.2 between them. A fixed
    count of 4 to 20 passed on each of them. Thus the fit tries each count
    of FIT_POLES, and it keeps the first one that passes. When none
    passes, a linear interpolation of the complex values is the fallback.
    """
    n = len(ports)
    fs = np.squeeze(g.freq)
    raw = np.zeros((len(fs), n, n), dtype=complex)
    for i, pi in enumerate(ports):
        for j, pj in enumerate(ports):
            raw[:, i, j] = np.squeeze(g.S(pi, pj))
    if len(fs) < 4:
        causes = ["there are only %d solved points" % len(fs)]
    else:
        causes = []
        # **A fit with more poles than half of the points is a guess.** On
        # the stripline of `run_boards_emerge.py`, 14 poles on 11 points
        # went through each point and put S21 at -61 dB between two points
        # of -0.3 dB. No check at the points can see that.
        cap = (len(fs) - 1) // 2
        for poles in [p for p in FIT_POLES if p == "auto" or p <= cap]:
            S = np.zeros((len(freq), n, n), dtype=complex)
            at = np.zeros_like(raw)
            try:
                for i, pi in enumerate(ports):
                    for j, pj in enumerate(ports):
                        S[:, i, j] = g.model_S(pi, pj, freq, Npoles=poles,
                                               _warn=False)
                        at[:, i, j] = g.model_S(pi, pj, fs, Npoles=poles,
                                                _warn=False)
            except Exception as e:
                causes.append("%s poles: an error (%s)" % (poles, e))
                continue
            miss = np.max(np.abs(at - raw))
            power = np.max(np.sum(np.abs(S) ** 2, axis=1))
            if miss > FIT_TOLERANCE:
                causes.append("%s poles: %.3f from a solved point"
                              % (poles, miss))
            elif power > FIT_POWER_MAX:
                causes.append("%s poles: sum|S|^2 = %.2f" % (poles, power))
            else:
                return S, raw, poles, None
    S = np.zeros((len(freq), n, n), dtype=complex)
    for i in range(n):
        for j in range(n):
            S[:, i, j] = (np.interp(freq, fs, raw[:, i, j].real)
                          + 1j * np.interp(freq, fs, raw[:, i, j].imag))
    return S, raw, None, "; ".join(causes)


def _smooth_plane(fe, X, Y, z_cut, blur=0.0):
    """Give (Ex, Ey, Ez, Hx, Hy, Hz) on the points (X, Y) of the plane
    z_cut (mm), continuous from one tetrahedron to the next.

    **The elements of EMerge keep only the tangential field continuous**
    across a face. The normal part jumps, thus a cut through the mesh shows
    each tetrahedron as a triangle of its own, most at the copper edges,
    where the elements are small and the field changes fast. This gives
    the field at each vertex of the tetrahedra that the plane cuts, as the
    mean of the values at the centres of the tetrahedra of that vertex, and
    then the linear value between the 4 vertices at each point. Thus the
    detail follows the mesh: small elements keep a narrow gap. The mean is
    for each material alone, thus the jump of the normal E at a dielectric
    face stays. A point outside the mesh gives NaN.

    `blur` is the sigma of a Gauss filter at the end, in points of the
    grid: it takes the facets of the linear values away (`_write_fields`).
    """
    from emerge._emerge.const import MU0
    b = fe.basis
    nodes, tets = b.mesh.nodes, b.mesh.tets
    xs, ys = X.ravel() * MM, Y.ravel() * MM
    zs = np.full_like(xs, z_cut * MM)
    tmap = np.asarray(b.interpolate_index(xs, ys, zs)).astype(np.int64)
    ok = tmap >= 0
    cut = np.unique(tmap[ok])                       # the cut tetrahedra
    corner = tets[:, cut]                           # (4, T)
    # **The value of each tetrahedron at its centre**, which is its most
    # accurate point, and not at its vertices: a vertex on a copper edge
    # is singular, and the values of the tetrahedra there left spots.
    cen = nodes[:, corner].mean(axis=1)             # (3, T)
    const = 1 / (-1j * 2 * np.pi * fe.freq * (fe._dur * MU0))
    ev = np.array(b.interpolate(fe._field, cen[0], cen[1], cen[2], cut))
    hv = np.array(b.interpolate_curl(fe._field, cen[0], cen[1], cen[2],
                                     const, cut))
    ev, hv = np.tile(ev, 4), np.tile(hv, 4)         # the same at each vertex
    # One key for each (vertex, material).
    mat = np.unique(np.round(np.real(np.asarray(fe._der)[cut]), 6),
                    return_inverse=True)[1].ravel()
    key = corner.ravel() * (mat.max() + 1) + np.tile(mat, 4)
    ukey, inv = np.unique(key, return_inverse=True)
    cnt = np.bincount(inv)

    def mean(v):
        return (np.bincount(inv, v.real) + 1j * np.bincount(inv, v.imag)) / cnt

    ev = np.array([mean(c) for c in ev])            # (3, K)
    hv = np.array([mean(c) for c in hv])
    # the linear weights of each point in its tetrahedron
    t = tmap[ok]
    ti = np.searchsorted(cut, t)                    # the index of t in cut
    v = nodes[:, tets[:, t]]                        # (3, 4, P)
    A = np.transpose(v[:, 1:, :] - v[:, :1, :], (2, 0, 1))  # (P, 3, 3)
    p = np.array([xs[ok], ys[ok], zs[ok]]) - v[:, 0, :]
    lam = np.linalg.solve(A, p.T[..., None])[..., 0]        # (P, 3)
    w = np.column_stack([1 - lam.sum(1), lam])              # (P, 4)
    # the key of each vertex of the tetrahedron of each point
    k = tets[:, t] * (mat.max() + 1) + mat[ti]
    col = np.searchsorted(ukey, k)                          # (4, P)
    # **A median of 3 x 3 points** then takes the last spots at a copper
    # edge away. A median keeps an edge sharp, and it keeps each feature
    # of 2 points or more. On the patch of the rigs the peak went down by
    # 1.6%.
    from scipy import ndimage
    out = []
    for comp in list(ev) + list(hv):
        f = np.full(xs.shape, np.nan, dtype=complex)
        f[ok] = np.sum(comp[col] * w.T, axis=0)
        f = f.reshape(X.shape)
        g = np.nan_to_num(f)
        g = (ndimage.median_filter(g.real, 3)
             + 1j * ndimage.median_filter(g.imag, 3))
        if blur > 0.3:
            # The mean of the points IN the mesh alone: a hole (a void
            # or a via) must not pull its edge to 0.
            inside = ndimage.gaussian_filter((~np.isnan(f)).astype(float),
                                             blur)
            g = (ndimage.gaussian_filter(g.real, blur)
                 + 1j * ndimage.gaussian_filter(g.imag, blur))
            g = g / np.maximum(inside, 1e-6)
        out.append(np.where(np.isnan(f), np.nan, g))
    return out


def _write_fields(data, model, z_of, box, outdir, f_hz, power, edge):
    """Write excN/Ef.h5, excN/Hf.h5, the current views and excN/field.json
    for each excited port, in the format that `gui._load_field` reads.

    The plane is the middle of the substrate below the port, as in the
    openEMS runner. It covers the air box, which is the board and the
    margin. `power` is the incident power of a port of EMerge, thus
    sqrt(0.5 / power) gives the scale of CST: a wave of 1 sqrt(W) peak.

    **The view is smoothed by `FIELD_BLUR` of `edge`**, the element at the
    copper edges in mm. The linear values of `_smooth_plane` show each
    element as a facet: a patch of the owner (27 k tetrahedra, `edge` 0.56
    mm, a grid of 0.13 mm) had edges that wobbled and a mottled inside.
    A Gauss of 0.5 `edge` took that away and lowered the peak by 2%; 1
    `edge` lowered it by 8%. A sigma in mm, and not in points, is the
    same on a small board and on a large one.
    """
    import h5py
    s = model["settings"]
    nums = [p["number"] for p in model["ports"]]
    want = set(s.get("excite") or nums)
    fe = data.field.find(freq=f_hz)
    f_got = float(np.squeeze(fe.freq))
    x0, y0, x1, y1 = box
    step = max(((x1 - x0) * (y1 - y0) / FIELD_POINTS) ** 0.5,
               FIELD_STEP_MIN_MM)
    x = np.arange(x0, x1 + 0.5 * step, step)
    y = np.arange(y0, y1 + 0.5 * step, step)
    X, Y = np.meshgrid(x, y, indexing="ij")         # (Nx, Ny)
    # The planes of the H jump of a current view: a quarter of the element
    # at the copper edges from the sheet, and inside the thinnest layer.
    h_min = min(dl["z_top"] - dl["z_bottom"]
                for dl in model["dielectric_layers"])
    dz = max(min(0.25 * edge, 0.2 * h_min), 0.01)
    for i, p in enumerate(model["ports"]):
        if p["number"] not in want:
            continue
        z_cut = 0.5 * (z_of[p["layer"]] + z_of[p["ref_layer"]])
        fe.excite_port(p["number"])
        try:
            f6 = _smooth_plane(fe, X, Y, z_cut, FIELD_BLUR * edge / step)
        except Exception as e:  # the internals of another EMerge version
            say("the field view has no smoothing (%s: %s)"
                % (type(e).__name__, e))
            fe.interpolate(X * MM, Y * MM, np.full_like(X, z_cut) * MM)
            f6 = (fe.Ex, fe.Ey, fe.Ez, fe.Hx, fe.Hy, fe.Hz)
        d = os.path.join(outdir, "exc%d" % (i + 1))
        os.makedirs(d, exist_ok=True)

        def write(name, comps, z):
            # (3, Nx, Ny, 1), which is the sequence of the axes of openEMS.
            # A point outside the mesh gives NaN.
            F = np.nan_to_num(np.array(comps))[..., None]
            with h5py.File(os.path.join(d, name), "w") as fh:
                m = fh.create_group("Mesh")
                m["x"], m["y"], m["z"] = x, y, np.array([z])
                fd = fh.create_group("FieldData").create_group("FD")
                fd["f0"] = F.astype(np.complex64)
                fd.attrs["frequency"] = np.array([f_got])

        write("Ef.h5", f6[:3], z_cut)
        write("Hf.h5", f6[3:], z_cut)
        # **The current on the copper (F4)**: the sheet current is
        # K = z x (H above - H below), from the smoothed H at `dz` above
        # and below the layer. The layer of the port and its reference
        # planes, which carry the return path.
        for layer in solverenv.current_layers(p):
            zc = z_of[layer]
            try:
                up = _smooth_plane(fe, X, Y, zc + dz,
                                   FIELD_BLUR * edge / step)[3:]
                dn = _smooth_plane(fe, X, Y, zc - dz,
                                   FIELD_BLUR * edge / step)[3:]
            except Exception as e:
                say("the current view of %s is not available (%s: %s)"
                    % (layer, type(e).__name__, e))
                continue
            jump_x = np.nan_to_num(up[0]) - np.nan_to_num(dn[0])
            jump_y = np.nan_to_num(up[1]) - np.nan_to_num(dn[1])
            write(solverenv.CURRENT_PREFIX + layer + ".h5",
                  (-jump_y, jump_x, np.zeros_like(jump_x)), zc)
        with open(os.path.join(d, "field.json"), "w") as fh:
            json.dump({"f_hz": f_got, "P_inc_W": power,
                       "scale": [float(np.sqrt(0.5 / power)), 0.0]}, fh,
                      indent=1)
    return f_got, len(x) * len(y)


def _directivity(u, total):
    """Give 10 log10(4 pi u / total) in dBi, with a floor at -60 dBi."""
    return 10.0 * np.log10(np.maximum(4.0 * np.pi * u / total, 1e-6))


def _write_farfield(data, model, faces, origin, outdir, f_hz, power, raw,
                    fs):
    """Write farfield_pN.json for each excited port, in the format of the
    openEMS runner: three cuts in the style of CST, a grid of 5 degrees
    for the 3D view, Dmax, the radiated power and the efficiency.

    `faces` is the boundary of the air box, and the Stratton-Chu integral
    of EMerge goes across it. The directivity is 4 pi U / (the integral
    of U across the sphere), with U = |E|^2 of the far field. The integral
    comes from the grid of the 3D view, and it also gives the radiated
    power. **Do not use `Ptot` of EMerge for it**: that is the Poynting
    flux across the absorbing faces, and on the msl board it gave -3e-5 W
    and -1e-3 W, where the pattern gives 6.3e-3 W (openEMS: 1% of the
    input). The input power is the incident power less the power that the
    port sends back.
    """
    s = model["settings"]
    nums = [p["number"] for p in model["ports"]]
    want = set(s.get("excite") or nums)
    fe = data.field.find(freq=f_hz)
    f_got = float(np.squeeze(fe.freq))
    i_f = int(np.argmin(np.abs(np.asarray(fs) - f_got)))
    rad = np.pi / 180.0
    # the cuts of the openEMS runner: -180 to 180 in 2 degrees, where a
    # negative theta is the other half of the plane
    cut = np.arange(-180.0, 180.1, 2.0)
    az = np.arange(0.0, 360.1, 2.0)
    th3 = np.arange(0.0, 180.1, 5.0)
    ph3 = np.arange(0.0, 360.1, 5.0)
    out = []
    for i, p in enumerate(model["ports"]):
        if p["number"] not in want:
            continue
        fe.excite_port(p["number"])

        def u_of(theta_deg, phi_deg):
            """Give U, E_theta and E_phi at the angles (in degrees)."""
            th = np.asarray(theta_deg, float)
            ph = np.asarray(phi_deg, float)
            E, _, _ = fe.farfield(th * rad, ph * rad, faces, origin=origin)
            return (np.sum(np.abs(E) ** 2, axis=0).real,) + _spherical(
                E, th * rad, ph * rad)

        g3 = fe.farfield_3d(faces, th3 * rad, ph3 * rad, origin=origin)
        u3 = np.sum(np.abs(g3._E) ** 2, axis=0).real.T     # (Ntheta, Nphi)
        P3, T3 = np.meshgrid(ph3 * rad, th3 * rad)         # (Ntheta, Nphi)
        et3, ep3 = _spherical(np.transpose(g3._E, (0, 2, 1)), T3, P3)
        total = np.trapezoid(np.trapezoid(u3 * np.sin(th3 * rad)[:, None],
                                          ph3 * rad, axis=1), th3 * rad)
        cuts = {}
        for name, phi_pos in (("Phi=0", 0.0), ("Phi=90", 90.0)):
            ph = np.where(cut >= 0, phi_pos, phi_pos + 180.0)
            u, et, ep = u_of(np.abs(cut), ph)
            cuts[name] = {"angle_deg": cut.tolist(),
                          "D_dBi": _directivity(u, total).tolist(),
                          "_et": et, "_ep": ep, "_phi": ph}
        u, et, ep = u_of(np.full_like(az, 90.0), az)
        cuts["Theta=90"] = {"angle_deg": az.tolist(),
                            "D_dBi": _directivity(u, total).tolist(),
                            "_et": et, "_ep": ep, "_phi": az}
        d3 = _directivity(u3, total)
        # |E|^2 / (2 Z0) is U, thus the integral also gives the radiated
        # power.
        prad = float(total / (2.0 * Z_FREE))
        dmax = float(d3.max())
        p_in = power * (1.0 - abs(raw[i_f, i, i]) ** 2)
        eff = 100.0 * prad / p_in if p_in > 0 else None
        grid = {"theta_deg": th3.tolist(), "phi_deg": ph3.tolist(),
                "D_dBi": d3.tolist(), "_et": et3, "_ep": ep3,
                "_phi": ph3[None, :]}
        res = {"f_hz": f_got, "cuts": cuts, "grid3d": grid,
               "Dmax_dBi": dmax, "Prad_W": prad, "P_in_W": p_in,
               "efficiency_pct": eff}
        # The far field gets the scale of the field views (Stratton-Chu
        # gives rE in V): `solverenv.polarization`.
        try:
            ref, xpd, th, ph = solverenv.polarization(
                grid, cuts, ph3, np.sqrt(0.5 / power))
            res.update(ludwig3_ref_deg=ref, xpd_dB=xpd,
                       main_lobe_deg=[th, ph],
                       E_scale="rE in V, for 1 sqrt(W) peak incident")
        except Exception as e:
            say("WARNING: the co-pol and the cross-pol of the far field of "
                "port %d are not available: %s" % (i + 1, e))
            for g in [grid] + list(cuts.values()):
                for k in ("_et", "_ep", "_phi"):
                    g.pop(k, None)
        with open(os.path.join(outdir, "farfield_p%d.json" % (i + 1)),
                  "w") as fh:
            json.dump(res, fh, indent=1)
        out.append((i + 1, dmax, eff, res.get("xpd_dB"),
                    res.get("ludwig3_ref_deg")))
    return f_got, out


def _spherical(E, th, ph):
    """Give (E_theta, E_phi) of the Cartesian far field E (3, ...) at the
    angles `th` and `ph` (in radians, with the shape of E[0])."""
    ct, st, cp, sp = np.cos(th), np.sin(th), np.cos(ph), np.sin(ph)
    return (E[0] * ct * cp + E[1] * ct * sp - E[2] * st,
            -E[0] * sp + E[1] * cp)


def main(model_path, outdir):
    t0 = time.time()
    with open(model_path) as fh:
        model = json.load(fh)
    version = int(model.get("version", 1))
    if version > solverenv.MODEL_VERSION:
        raise SystemExit(
            "[rfsim] ERROR: %s is a version %d model, and this runner "
            "reads only version %d or lower. Update the plugin."
            % (os.path.basename(model_path), version, solverenv.MODEL_VERSION))
    for w in model.get("warnings", []):
        say("WARNING: %s" % w)
    s = model["settings"]
    ports = model["ports"]
    n = len(ports)
    if n < 1:
        raise SystemExit("[rfsim] ERROR: the model has %d ports, and it "
                         "must have 1 or more." % n)

    # The results of a previous run look current to the results window.
    for d in glob.glob(os.path.join(outdir, "exc*")):
        shutil.rmtree(d, ignore_errors=True)
    for fpath in (glob.glob(os.path.join(outdir, "farfield*.json"))
                  + [os.path.join(outdir, "lines.json")]):
        try:
            os.remove(fpath)
        except OSError:
            pass

    # EMerge writes its cache and its log adjacent to the script that calls
    # it. That is the plugin directory, thus send them to the output
    # directory.
    os.environ["EMERGE_BASE_PATH"] = outdir
    # The import writes a line with colour codes. The handler below gives
    # the lines of the run with no colour codes.
    os.environ["EMERGE_STD_LOGLEVEL"] = "WARNING"
    say("solver: EMerge (FEM). The first run compiles the code of EMerge, "
        "which can use some minutes.")
    import emerge as em
    from loguru import logger

    sim = em.Simulation("emerge")
    # **A line on a board is quasi-TEM, and EMerge must accept that.** The
    # microstrip of the rigs has Hz/Hxy = 0.071 at 3.5 GHz, because the
    # strip has air above it and dielectric below it. EMerge then calls the
    # mode TE (its limit is 0.05), and a TEM port refuses it. A port of a
    # board carries one mode, thus a larger limit is safe.
    sim.settings.qtem_limit = QTEM_LIMIT
    # One plain line for each message of EMerge, with no colour codes.
    logger.configure(handlers=[{
        "sink": sys.stdout, "level": "INFO", "colorize": False,
        "format": "[emerge] {message}"}])

    mesh = s.get("mesh", "medium")
    notes = ["The solver is EMerge (FEM), version %s" % em.__version__]

    # ---------------------------------------------------------- geometry
    z_of = {c["name"]: c["z"] for c in model["copper_layers"]}
    br = model["board_rect"]
    r = model["region"]
    # **The air box is ALL the region: the margin AND the PML band of
    # openEMS, as clear air.** EMerge has an absorbing boundary on the faces
    # of the box and no band. With the margin alone (4 mm, and 5 board
    # thicknesses above and below), the absorber was in the near field of
    # the copper and it took power: a line of 100 mm with NO loss lost 3%
    # to 4% of the power at 1 to 3 GHz. The attenuation of `rig_atten` then
    # read 0.80 times the theory, and 0.48 times at 1 GHz. With 20 mm of
    # air, the line with no loss read 0.0 +- 0.3 dB/m, and the line with
    # loss read the theory to 3% from 1 to 5 GHz. The band is 8 cells of
    # the step of the mesh, about 19 mm at the coarse preset to 6 GHz, and
    # the dialog shows it as the domain.
    pml = model.get("pml_mm") or s["margin_mm"]
    ax0, ay0, ax1, ay1 = r["x0"], r["y0"], r["x1"], r["y1"]
    # The field views keep the board and the margin, as the dumps of
    # openEMS do. A view of all the air is almost all empty.
    view = (ax0 + pml, ay0 + pml, ax1 - pml, ay1 - pml)
    zb = min(d["z_bottom"] for d in model["dielectric_layers"])
    zt = max(d["z_top"] for d in model["dielectric_layers"])
    # Air above and below the board: the same margin and band.
    hz = max(s["margin_mm"] + pml, 5.0 * (zt - zb))
    notes.append("The air box is %.1f x %.1f x %.1f mm, with an absorbing "
                 "boundary on its faces" % (ax1 - ax0, ay1 - ay0,
                                            zt - zb + 2 * hz))

    # The wave ports first: their voids cut the air and the dielectric.
    wave = {}
    abox = (ax0, ay0, ax1, ay1, zb - hz, zt + hz)
    for p in ports:
        got = _wave_port(p, z_of, abox, s["f_stop"],
                         model["dielectric_layers"])
        if isinstance(got, tuple):
            wave[p["number"]] = got
        elif got:
            notes.append("Port %d is a lumped port, because %s"
                         % (p["number"], got))
    # **A through-hole pad is a coaxial feed** (`solverenv.coax_box`): a
    # plate in the gap around the pad, on the layer of its side.
    coax, coax_on = {}, {}
    for p in ports:
        if p.get("type") != "coax":
            continue
        box = solverenv.coax_box(p, z_of)
        if not box:
            notes.append("Port %d is a lumped port, and not a coax, because "
                         "it has no gap on %s"
                         % (p["number"], p.get("coax_side") or "a side"))
            continue
        coax[p["number"]] = box
        coax_on.setdefault(p["coax_side"], []).append(box)
        notes.append("Port %d is a coaxial feed on %s: a lumped port across "
                     "the gap of %.3f mm between the through-hole pad and "
                     "the copper around it, on the %s axis and %.3f mm "
                     "wide"
                     % (p["number"], p["coax_side"], box["gap"], box["exc"],
                        box["width"]))

    def overlaps(a, b):
        return all(a[i] < b[i + 3] and b[i] < a[i + 3] for i in range(3))

    diels = []
    for i, d in enumerate(model["dielectric_layers"]):
        box = em.geo.Box((br["x1"] - br["x0"]) * MM,
                         (br["y1"] - br["y0"]) * MM,
                         (d["z_top"] - d["z_bottom"]) * MM,
                         position=(br["x0"] * MM, br["y0"] * MM,
                                   d["z_bottom"] * MM),
                         name="diel%d" % i)
        mat = em.Material(d["epsilon"], tand=d["loss_tangent"],
                          name="diel%d" % i)
        dbox = (br["x0"], br["y0"], d["z_bottom"], br["x1"], br["y1"],
                d["z_top"])
        for void, _ in wave.values():
            if overlaps(void, dbox):
                box = _cut(em, box, void)
        box.set_material(mat)
        diels.append(box)
    air = em.geo.Box((ax1 - ax0) * MM, (ay1 - ay0) * MM,
                     (zt - zb + 2 * hz) * MM,
                     position=(ax0 * MM, ay0 * MM, (zb - hz) * MM),
                     name="air")
    for void, _ in wave.values():
        air = _cut(em, air, void)
    air = air.background()

    copper = []
    for name, polys in model["polygons"].items():
        if not polys:
            continue
        z = z_of[name] * MM
        cs = em.GCS.displace(0, 0, z)
        parts = []
        for p in polys:
            outline, loops = _rings(p)
            if len(outline) < 3:
                continue
            surf = em.geo.XYPolygon(np.array(outline)[:, 0] * MM,
                                    np.array(outline)[:, 1] * MM).geo(cs)
            sign = np.sign(_area(outline))
            for loop in loops:
                piece = em.geo.XYPolygon(np.array(loop)[:, 0] * MM,
                                         np.array(loop)[:, 1] * MM).geo(cs)
                # A loop that turns the other way is a hole. A loop that
                # turns the same way is copper in a hole, which the
                # outline also went around.
                if np.sign(_area(loop)) != sign:
                    surf = em.geo.remove(surf, piece)
                else:
                    surf = em.geo.add(surf, piece)
            parts.append(surf)
        layer = em.geo.unite(*parts) if len(parts) > 1 else parts[0]
        # The copper of the model continues into the PML band of openEMS.
        # Cut it at the faces of the air box.
        clip = em.geo.XYPlate((ax1 - ax0) * MM, (ay1 - ay0) * MM,
                              position=(ax0 * MM, ay0 * MM, z))
        layer = em.geo.intersect(layer, clip)
        # The copper IN a void goes out: the void is not a part of the
        # model. The planes on the faces of the void stay, because they
        # are its walls.
        zc = z_of[name]
        for (x0, y0, z0, x1, y1, z1), _ in wave.values():
            if z0 < zc < z1:
                hole = em.geo.XYPlate((x1 - x0) * MM, (y1 - y0) * MM,
                                      position=(x0 * MM, y0 * MM, z))
                try:
                    layer = em.geo.remove(layer, hole)
                except Exception:
                    pass
        # The tab and the slot of each coaxial feed on this layer. The tab
        # makes the edge of a round pad straight at the plate of the port,
        # and the slot removes the copper around the pad from the plate. The
        # plate then touches the copper along its two faces, and it is on
        # no copper.
        for box in coax_on.get(name, []):
            x0, y0, x1, y1 = box["tab"]
            layer = em.geo.add(layer, em.geo.XYPlate(
                (x1 - x0) * MM, (y1 - y0) * MM, position=(x0 * MM, y0 * MM,
                                                          z)))
            (a0, b0, _), (a1, b1, _) = box["start"], box["stop"]
            layer = em.geo.remove(layer, em.geo.XYPlate(
                abs(a1 - a0) * MM, abs(b1 - b0) * MM,
                position=(min(a0, a1) * MM, min(b0, b1) * MM, z)))
        # **Copper with its conductivity, and not PEC** (5.8e7 S/m, as the
        # sheets of openEMS). EMerge gives a conducting sheet in the model
        # a ThinConductor: each side has the surface impedance of copper,
        # thus the line has the loss of its metal.
        layer.set_material(em.lib.COPPER)
        copper.append(layer)

    for v in model["vias"]:
        cyl = em.geo.Cylinder(v["r"] * MM, (v["z1"] - v["z0"]) * MM,
                              em.GCS.displace(v["x"] * MM, v["y"] * MM,
                                              v["z0"] * MM),
                              Nsections=VIA_SIDES)
        cyl.set_material(em.lib.PEC)

    port_geo = []
    for p in ports:
        if p["number"] in wave:
            o, u, v = wave[p["number"]][1]
            plate = em.geo.Plate(tuple(c * MM for c in o),
                                 tuple(c * MM for c in u),
                                 tuple(c * MM for c in v))
            wd = p.get("track_width") or (p["width"] if p["direction"][0]
                                          else p["length"])
            # (the plate, no width, the narrowest feature, no sign): a
            # wave port. The gap of a cpw is narrower than its strip.
            feat = min(v for v in (wd, p.get("gap")) if v)
            port_geo.append((plate, None, feat, None))
            notes.append("Port %d is a wave port (%s): the mode of the line "
                         "on a face %.1f mm wide and %.1f mm high, at the "
                         "centre of the pad" % (
                             p["number"], p["type"],
                             np.linalg.norm(u), np.linalg.norm(v)))
            continue
        box = coax.get(p["number"])
        if box:
            # A plate in the gap, flat on the copper of its side. `width`
            # is across the current, and `height` is along it.
            (x0, y0, z), (x1, y1, _) = box["start"], box["stop"]
            plate = em.geo.XYPlate(abs(x1 - x0) * MM, abs(y1 - y0) * MM,
                                   position=(min(x0, x1) * MM,
                                             min(y0, y1) * MM, z * MM))
            k = "xy".index(box["exc"])
            port_geo.append((plate, box["width"], box["gap"],
                             tuple(box["sign"] if i == k else 0
                                   for i in range(3))))
            continue
        o, u, v, w, h, sign = _port_plate(p, z_of)
        plate = em.geo.Plate(tuple(c * MM for c in o),
                             tuple(c * MM for c in u),
                             tuple(c * MM for c in v))
        port_geo.append((plate, w, h, (0, 0, sign)))

    elements = []
    shorts = []
    para = s.get("parasitics", True)
    if s.get("lumped", True):
        for e in model.get("lumped_elements", []):
            comp = solverenv.components(e, para)
            if not comp:
                say("WARNING: lumped %s has no type or no value, thus it "
                    "is not in the model (its gap stays open)"
                    % e.get("ref", "?"))
                continue
            x0, y0 = min(e["start"][0], e["stop"][0]), min(e["start"][1],
                                                            e["stop"][1])
            dx = abs(e["stop"][0] - e["start"][0])
            dy = abs(e["stop"][1] - e["start"][1])
            plate = em.geo.XYPlate(dx * MM, dy * MM,
                                   position=(x0 * MM, y0 * MM,
                                             e["start"][2] * MM))
            if comp == {"R": 0.0}:
                plate.set_material(em.lib.PEC)
                shorts.append(e["ref"])
                continue
            # `height` is along the current, and `width` is across it.
            height, width = (dx, dy) if e["ny"] == "x" else (dy, dx)
            epc = (e.get("epc") or 0.0) if para else 0.0
            elements.append((e, plate, width, height, _impedance(comp, epc)))
            say("lumped %s: %s%s (%s-axis)" % (
                e["ref"], ", ".join("%s=%g" % kv for kv in
                                    sorted(comp.items())),
                ", EPC=%g" % epc if epc else "", e["ny"]))
    if shorts:
        notes.append("%s: 0 ohm, thus a metal sheet" % ", ".join(shorts))

    sim.commit_geometry()

    # -------------------------------------------------------------- mesh
    points = int(s.get("fem_points") or FEM_POINTS.get(mesh, 21))
    sim.mw.set_resolution(RESOLUTION.get(mesh, 0.2))
    # The frequency of the field views is a solved point, thus its fields
    # come from the solver and not from a fit.
    fs = np.linspace(s["f_start"], s["f_stop"], points)
    f_field = s.get("f_field") or 0.5 * (s["f_start"] + s["f_stop"])
    if np.min(np.abs(fs - f_field)) > 1e-6 * f_field:
        fs = np.sort(np.append(fs, f_field))
    sim.mw.set_frequency(list(fs))
    h_min = min(d["z_top"] - d["z_bottom"] for d in model["dielectric_layers"])
    edge = max(EDGE_PART.get(mesh, 0.35) * h_min, EDGE_MIN_MM)
    for c in copper:
        sim.mesher.set_boundary_size(c, edge * MM)
    # **A narrow feature of a port gets its fine mesh in a box along its
    # feed** (`_feature_boxes`). The CPW of the rigs has a gap of 0.3 mm,
    # and the edge of the substrate rule is 0.77 mm. The field of the gap
    # then went into the substrate, and eps_eff read +9.9% against the
    # closed form. The gap as the size gives +2.5% (openEMS: +1%).
    import gmsh
    boxes = _feature_boxes(ports, z_of, edge, (ax0, ay0, ax1, ay1),
                           FEATURE_PART.get(mesh, 0.5))
    for x0, y0, z0, x1, y1, z1, size in boxes:
        f = gmsh.model.mesh.field.add("Box")
        for k, v in (("VIn", size), ("VOut", 1e3), ("XMin", x0),
                     ("XMax", x1), ("YMin", y0), ("YMax", y1),
                     ("ZMin", z0), ("ZMax", z1), ("Thickness", 4 * size)):
            gmsh.model.mesh.field.setNumber(f, k, v * MM)
        sim.mesher.mesh_fields.append(f)
    for plate, w, h, _ in port_geo:
        if w is None:
            # The face of a wave port: the copper edge. The box of a
            # narrow gap or strip goes across the face and refines it
            # there. A face at a quarter of the gap (0.1 mm on all its
            # 17 x 11 mm) gave 378 k tetrahedra on the CPW of the rigs,
            # and the mode solve then had no memory.
            size = edge
        else:
            size = max(min(w, h) / 3, EDGE_MIN_MM)
        sim.mesher.set_face_size(plate, size * MM)
    for _, plate, w, h, _ in elements:
        sim.mesher.set_face_size(plate, max(min(w, h) / 3, EDGE_MIN_MM) * MM)
    notes.append("The mesh uses %g of the wavelength in the volume and "
                 "%.3g mm at the copper edges (%s preset)"
                 % (RESOLUTION.get(mesh, 0.2), edge, mesh))
    for x0, y0, z0, x1, y1, z1, size in boxes:
        notes.append("The feed at (%.2f, %.2f) to (%.2f, %.2f) mm gets "
                     "%.3g mm, the size of its narrowest strip or gap"
                     % (x0, y0, x1, y1, size))
    sim.generate_mesh()
    say("mesh: %d tetrahedra (%.0f s)" % (sim.mesh.n_tets, time.time() - t0))

    # ------------------------------------------------ boundary conditions
    lports = []
    for i, (plate, w, h, direction) in enumerate(port_geo):
        if w is None:
            # quasi-TEM: the line has air and dielectric on its face, thus
            # the mode is solved again at each frequency
            lports.append(sim.mw.bc.ModalPort(
                plate, ports[i]["number"], modetype="TEM",
                mixed_materials=True))
            continue
        # `direction` goes from the reference to the pad: up from the
        # plane for a pad port, and across the gap for a coaxial feed.
        lports.append(sim.mw.bc.LumpedPort(
            plate, ports[i]["number"], width=w * MM, height=h * MM,
            direction=direction, Z0=s["z0"]))
    for _, plate, w, h, zf in elements:
        sim.mw.bc.LumpedElement(plate, zf, width=w * MM, height=h * MM)
    # **The absorber is on the six outer faces only.** The faces of a void
    # of a wave port are also faces of the air, and they must stay PEC.
    faces = air.boundary()
    tol = 1e-6
    edges = ((0, ax0 * MM), (0, ax1 * MM), (1, ay0 * MM), (1, ay1 * MM),
             (2, (zb - hz) * MM), (2, (zt + hz) * MM))
    keep = [tag for tag, cen in zip(faces.tags, faces.centers)
            if any(abs(cen[i] - v) < tol for i, v in edges)]
    outer = em.FaceSelection(keep)
    sim.mw.bc.AbsorbingBoundary(outer)

    # ------------------------------------------------------------- solve
    # The serial sweep uses PARDISO, which has its own threads. The
    # parallel sweep of EMerge uses SuperLU, which was 20 times slower on a
    # board of 28 k tetrahedra.
    if wave:
        _mode_search(sim.mw, {
            p["number"]: _neff_guess(p, z_of, model["dielectric_layers"])
            for p in ports if p["number"] in wave})
        cause = _power_overlap()
        notes.append("The S-parameters of a wave port come from E x h* of "
                     "the mode" if cause is None else
                     "The S-parameters of a wave port come from E . e* of "
                     "EMerge, because %s. They show a loss of some "
                     "percent that is not in the board" % cause)
    data = sim.mw.run_sweep()
    g = data.scalar.grid
    say("solve: %d frequencies (%.0f s)" % (len(g.freq), time.time() - t0))
    if wave:
        _write_lines(g, ports, wave, outdir)
        # The S-matrix of a wave port has the Z0 of its mode. The plugin
        # gives each port the z0 of the dialog, as openEMS does.
        g = g.renormalize(s["z0"])

    freq = np.linspace(s["f_start"], s["f_stop"], s.get("n_freq", 401))
    S, raw, poles, cause = _fit(g, [p["number"] for p in ports], freq)
    if not cause:
        notes.append("It solves %d frequencies, and a vector fit (%s "
                     "poles) gives the %d points of the sweep"
                     % (len(fs), poles, len(freq)))
    else:
        line = ("It solves %d frequencies. No vector fit is used (%s). "
                "Thus the sweep is a linear interpolation of the solved "
                "points. Set more frequencies for a smooth curve"
                % (len(fs), cause))
        say("WARNING: " + line)
        notes.append(line)

    try:
        f_got, count = _write_fields(data, model, z_of, view,
                                     outdir, f_field, lports[0].power, edge)
        notes.append("The field views are at %g GHz, on the middle plane of "
                     "the substrate below each excited port, from %d samples "
                     "of the FEM solution" % (f_got / 1e9, count))
        say("fields: %g GHz, %d samples (%.0f s)"
            % (f_got / 1e9, count, time.time() - t0))
    except Exception as e:
        say("WARNING: the field views are not available: %s" % e)

    try:
        centre = (0.5 * (ax0 + ax1) * MM, 0.5 * (ay0 + ay1) * MM,
                  0.5 * (zb + zt) * MM)
        f_got, got = _write_farfield(data, model, outer, centre, outdir,
                                     f_field, lports[0].power, raw,
                                     np.squeeze(g.freq))
        for num, dmax, eff, xpd, ref in got:
            say("far field of port %d: Dmax %.1f dBi, radiated power %.1f%% "
                "of the input power%s"
                % (num, dmax, eff if eff is not None else -1,
                   ", XPD %.1f dB (Ludwig 3 at %g deg)" % (xpd, ref)
                   if xpd is not None else ""))
        notes.append("The far field is at %g GHz, from the fields on the "
                     "faces of the air box (Stratton-Chu)" % (f_got / 1e9))
    except Exception as e:
        say("WARNING: the far field is not available: %s" % e)

    try:
        with open(os.path.join(outdir, "optimizations.log"), "w",
                  encoding="utf-8") as fh:
            fh.write("RFsim: optimizations\n%s\nmodel: %s\nsweep: %g to %g "
                     "GHz, z0 %g ohm, mesh %s\n\n"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), model_path,
                        s["f_start"] / 1e9, s["f_stop"] / 1e9, s["z0"],
                        mesh))
            for line in notes:
                fh.write("- " + line + "\n")
    except OSError:
        pass

    # The solved points with no fit, thus a user can compare the fit with
    # the data that it comes from.
    solverenv.write_touchstone(os.path.join(outdir, "solved.s%dp" % n),
                               np.squeeze(g.freq), raw, s["z0"])
    out = os.path.join(outdir, "results.s%dp" % n)
    solverenv.write_touchstone(out, freq, S, s["z0"])
    for k in range(n):
        for j in range(n):
            mag = 20 * np.log10(np.maximum(np.abs(S[:, j, k]), 1e-12))
            say("S%d%d: %.1f .. %.1f dB" % (j + 1, k + 1, mag.min(),
                                            mag.max()))
    say("wrote %s (%.0f s)" % (out, time.time() - t0))
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python emerge_runner.py model.json "
                         "output_dir")
    os.makedirs(sys.argv[2], exist_ok=True)
    main(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]))
