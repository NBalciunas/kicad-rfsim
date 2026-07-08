"""Standalone openEMS runner: model.json -> Touchstone file.

Runs outside KiCad (spawned as a subprocess by the plugin, or manually:
    python runner.py model.json output_dir
) so a solver crash never takes KiCad down, and the sim pipeline can be
tested headless. Imports only numpy/CSXCAD/openEMS — never pcbnew or wx.

Excites each port in turn (N ports -> N runs) to fill the full S-matrix.
"""
import json
import os
import sys

# The openEMS/CSXCAD python extensions need the openEMS binary DLLs on
# Windows. Look next to KiCad's 3rdparty dir (../../openEMS relative to this
# plugin), then OPENEMS_PATH, then C:\openEMS.
if os.name == "nt":
    _here = os.path.dirname(os.path.abspath(__file__))
    for _d in (os.environ.get("OPENEMS_PATH"),
               os.path.abspath(os.path.join(_here, "..", "..", "openEMS")),
               r"C:\openEMS"):
        if _d and os.path.isdir(_d):
            os.add_dll_directory(_d)

import numpy as np

C0 = 299792458.0
EPS0 = 8.8541878128e-12
RES_DIV = {"coarse": 10.0, "medium": 20.0, "fine": 40.0}  # cells per wavelength


def _merge_close(vals, tol):
    """Sort and merge coordinates closer than tol (avoids sliver mesh cells)."""
    vals = sorted(vals)
    out = [vals[0]]
    for v in vals[1:]:
        if v - out[-1] < tol:
            out[-1] = 0.5 * (out[-1] + v)
        else:
            out.append(v)
    return out


def _port_geometry(model, res):
    """Precompute port boxes/planes (pure floats, needed before meshing)."""
    z_of = {c["name"]: c["z"] for c in model["copper_layers"]}
    ports = []
    plist = model["ports"]
    for p in plist:
        z_top = z_of[p["layer"]]
        z_ref = z_of[p["ref_layer"]]
        g = dict(p, z_top=z_top, z_ref=z_ref)
        if p["type"] == "msl" and p["direction"]:
            w = p.get("track_width") or p["width"]
            length = max(3.0 * w, 6.0 * res)
            if len(plist) == 2:
                q = plist[1 - (p["number"] - 1)]
                dist = max(abs(q["x"] - p["x"]), abs(q["y"] - p["y"]))
                if dist > 0:
                    length = min(length, 0.3 * dist)
            d = p["direction"]
            if d[0]:
                start = [p["x"], p["y"] - w / 2, z_top]
                stop = [p["x"] + d[0] * length, p["y"] + w / 2, z_ref]
                g["prop_dir"] = "x"
            else:
                start = [p["x"] - w / 2, p["y"], z_top]
                stop = [p["x"] + w / 2, p["y"] + d[1] * length, z_ref]
                g["prop_dir"] = "y"
            g.update(start=start, stop=stop, msl_width=w, msl_len=length)
        else:
            if p["type"] == "msl":
                print("[rfsim] WARNING: port %d has no attached track; "
                      "falling back to lumped port" % p["number"], flush=True)
            g["type"] = "lumped"
            g["start"] = [p["x"] - p["length"] / 2, p["y"] - p["width"] / 2, z_ref]
            g["stop"] = [p["x"] + p["length"] / 2, p["y"] + p["width"] / 2, z_top]
        ports.append(g)
    return ports


def _pml_band(lo, hi, margin):
    """Fixed lines for the outer 8-cell PML band at both ends of an axis."""
    step = margin / 8.0
    return ([lo + i * step for i in range(9)]
            + [hi - i * step for i in range(9)])


def _mesh(model, ports, res):
    """Mesh line lists (x, y, z) from geometry hints + smoothing resolution.

    The domain equals the extraction region; its outer `margin` on every
    face is meshed as exactly 8 cells and used as PML_8, leaving an equally
    thick clear-air band between structure and absorber.
    """
    s = model["settings"]
    margin = s["margin_mm"]
    r = model["region"]
    xs = set(_pml_band(r["x0"], r["x1"], margin))
    ys = set(_pml_band(r["y0"], r["y1"], margin))
    for polys in model["polygons"].values():
        for poly in polys:
            px = [pt[0] for pt in poly]
            py = [pt[1] for pt in poly]
            xs.update((min(px), max(px)))
            ys.update((min(py), max(py)))
    for v in model["vias"]:
        xs.update((v["x"] - v["r"], v["x"] + v["r"]))
        ys.update((v["y"] - v["r"], v["y"] + v["r"]))
    for g in ports:
        xs.update((g["start"][0], g["stop"][0], g["x"]))
        ys.update((g["start"][1], g["stop"][1], g["y"]))

    board_top = model["copper_layers"][0]["z"]
    zs = set(_pml_band(-2.0 * margin, board_top + 2.0 * margin, margin))
    for c in model["copper_layers"]:
        zs.add(c["z"])
    for d in model["dielectric_layers"]:
        # >=4 cells across every dielectric layer
        zs.update(np.linspace(d["z_bottom"], d["z_top"], 5).tolist())

    tol = min(res / 8.0, margin / 20.0)
    return (_merge_close(xs, tol), _merge_close(ys, tol),
            _merge_close(zs, min(tol, 0.05)))


def build(model, excite_idx, res):
    """Fresh FDTD + CSX model with port `excite_idx` excited."""
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS

    s = model["settings"]
    f0 = 0.5 * (s["f_start"] + s["f_stop"])
    fc = 0.5 * (s["f_stop"] - s["f_start"])
    fdtd = openEMS(NrTS=s["max_timesteps"], EndCriteria=s["end_criteria"])
    fdtd.SetGaussExcite(f0, fc)
    # MUR showed slow late-time energy growth on this setup; PML_8 with an
    # explicitly meshed 8-cell absorber band is stable.
    fdtd.SetBoundaryCond(["PML_8"] * 6)
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)  # drawing unit: mm

    ports_geo = _port_geometry(model, res)
    from CSXCAD.SmoothMeshLines import SmoothMeshLines
    # round smoothed lines: a line at 1.5300000000000002 misses a
    # zero-thickness copper sheet at exactly 1.53 -> "unused primitive"
    for axis, lines in zip("xyz", _mesh(model, ports_geo, res)):
        grid.AddLine(axis, np.round(SmoothMeshLines(lines, res, 1.4), 9))

    br = model["board_rect"]
    for i, d in enumerate(model["dielectric_layers"]):
        kappa = 2 * np.pi * f0 * EPS0 * d["epsilon"] * d["loss_tangent"]
        mat = csx.AddMaterial("diel%d" % i, epsilon=d["epsilon"], kappa=kappa)
        mat.AddBox([br["x0"], br["y0"], d["z_bottom"]],
                   [br["x1"], br["y1"], d["z_top"]], priority=1)

    copper_prop = {}
    for c in model["copper_layers"]:
        prop = csx.AddConductingSheet("cu_" + c["name"], conductivity=5.8e7,
                                      thickness=max(c["thickness"], 0.005) * 1e-3)
        copper_prop[c["name"]] = prop
        for poly in model["polygons"].get(c["name"], []):
            pts = np.array(poly).T  # shape (2, N)
            prop.AddLinPoly(pts, "z", c["z"], 0, priority=10)

    if model["vias"]:
        via_metal = csx.AddMetal("vias")
        for v in model["vias"]:
            via_metal.AddCylinder([v["x"], v["y"], v["z0"]],
                                  [v["x"], v["y"], v["z1"]],
                                  v["r"], priority=10)

    ports = []
    for i, g in enumerate(ports_geo):
        excite = (i == excite_idx)
        if g["type"] == "msl":
            ports.append(fdtd.AddMSLPort(
                g["number"], copper_prop[g["layer"]], g["start"], g["stop"],
                g["prop_dir"], "z", excite=-1 if excite else 0,
                FeedShift=res, MeasPlaneShift=0.5 * g["msl_len"],
                Feed_R=s["z0"], priority=20))
        else:
            ports.append(fdtd.AddLumpedPort(
                g["number"], s["z0"], g["start"], g["stop"], "z",
                excite=1.0 if excite else 0, priority=20))
        print("[rfsim] port %d: %s at (%.2f, %.2f) %s" % (
            g["number"], g["type"], g["x"], g["y"],
            "dir " + g.get("prop_dir", "-") if g["type"] == "msl" else ""),
            flush=True)

    print("[rfsim] mesh: %d x %d x %d lines" % tuple(
        grid.GetQtyLines(a) for a in "xyz"), flush=True)
    return fdtd, ports


def write_touchstone(path, freq, S, z0):
    n = S.shape[1]
    with open(path, "w") as fh:
        fh.write("! rfsim (KiCad + openEMS)\n# HZ S RI R %g\n" % z0)
        for i, f in enumerate(freq):
            if n == 1:
                vals = [S[i, 0, 0]]
            else:  # .s2p column order: S11 S21 S12 S22
                vals = [S[i, 0, 0], S[i, 1, 0], S[i, 0, 1], S[i, 1, 1]]
            fh.write("%.6e %s\n" % (
                f, " ".join("%.9e %.9e" % (v.real, v.imag) for v in vals)))


def main(model_path, outdir):
    with open(model_path) as fh:
        model = json.load(fh)
    s = model["settings"]
    n = len(model["ports"])
    if not 1 <= n <= 2:
        raise SystemExit("expected 1 or 2 ports, got %d" % n)

    eps_max = max(d["epsilon"] for d in model["dielectric_layers"])
    lam_min = C0 / s["f_stop"] / np.sqrt(eps_max) * 1e3  # mm
    res = lam_min / RES_DIV[s["mesh"]]
    print("[rfsim] mesh resolution: %.3f mm (%s)" % (res, s["mesh"]), flush=True)

    freq = np.linspace(s["f_start"], s["f_stop"], s.get("n_freq", 401))
    S = np.zeros((len(freq), n, n), dtype=complex)
    for k in range(n):
        sim_path = os.path.join(outdir, "exc%d" % (k + 1))
        print("[rfsim] === excitation %d/%d ===" % (k + 1, n), flush=True)
        fdtd, ports = build(model, k, res)
        fdtd.Run(sim_path, cleanup=True)
        for p in ports:
            p.CalcPort(sim_path, freq, ref_impedance=s["z0"])
        for j in range(n):
            S[:, j, k] = ports[j].uf_ref / ports[k].uf_inc

    out = os.path.join(outdir, "results.s%dp" % n)
    write_touchstone(out, freq, S, s["z0"])
    s11 = 20 * np.log10(np.maximum(np.abs(S[:, 0, 0]), 1e-12))
    print("[rfsim] S11: %.1f .. %.1f dB" % (s11.min(), s11.max()), flush=True)
    if n == 2:
        s21 = 20 * np.log10(np.maximum(np.abs(S[:, 1, 0]), 1e-12))
        print("[rfsim] S21: %.1f .. %.1f dB" % (s21.min(), s21.max()), flush=True)
    print("[rfsim] wrote %s" % out, flush=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python runner.py model.json output_dir")
    os.makedirs(sys.argv[2], exist_ok=True)
    main(sys.argv[1], sys.argv[2])
