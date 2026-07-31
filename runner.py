"""The independent openEMS runner: it changes model.json into a Touchstone
file.

The runner runs outside KiCad. The plugin starts it as a subprocess, or
you can start it manually:

    python runner.py model.json output_dir

Thus a crash of the solver cannot stop KiCad, and you can test the
simulation without the GUI. The runner imports only numpy, CSXCAD and
openEMS. It does not import pcbnew or wx.

The runner excites each port in sequence. N ports give N runs, which fill
the full S-matrix.
"""
import glob
import json
import os
import shutil
import sys

import solverenv  # the directory of this file is sys.path[0] for a script

# On Windows, the python extensions of openEMS and CSXCAD need the DLLs
# from the binary directory of openEMS. (openEMS v0.37 and later also give
# a CSXCAD_INSTALL_PATH environment variable, but add_dll_directory alone
# is enough. This is a test result on v0.37.0-rc1.)
if os.name == "nt":
    for _d in solverenv.openems_dirs():
        if os.path.isdir(_d):
            os.add_dll_directory(_d)

import numpy as np

C0 = 299792458.0
EPS0 = 8.8541878128e-12
RES_DIV = {"coarse": 10.0, "medium": 20.0, "fine": 40.0}  # cells per wavelength


def _has_lumped_rlc():
    """Tell if this CSXCAD can do lumped inductors and series RLC.

    LEtype came with the lumped RLC work: openEMS PR #121, which is in
    v0.37 and in the later v0.0.36-N nightly builds. An older CSXCAD has
    no LEtype and refuses the keyword. Thus this test controls the guard
    for the inductors and the kwargs for AddLumpedElement.
    """
    from CSXCAD import CSProperties
    return hasattr(CSProperties.CSPropLumpedElement, "SetLEtype")


def _time_step_factor(model):
    """Give the timestep factor that keeps the run stable, or give None.

    A lumped inductor makes the FDTD unstable if the timestep is too
    large. On the geometry of validation/run_rlc.py, 1 nH is stable at
    the full Courant step, but 10, 100 and 300 nH diverge to NaN. The
    largest stable factor is near 1.8/sqrt(L[nH]). Thus 1/sqrt(L[nH])
    keeps a margin of about 1.8.

    This is an approximation from one geometry only. The true criterion
    also includes the cell size and the box of the element. Thus a run
    can diverge. The runner finds this condition and tells the user to
    set settings["time_step_factor"], which has priority over this value.
    """
    s = model["settings"]
    if s.get("time_step_factor"):
        return float(s["time_step_factor"])
    if not s.get("lumped", True):
        return None
    ind = [e["value"] for e in model.get("lumped_elements", [])
           if e["type"] == "L" and e["value"] > 0]
    if not ind:
        return None
    return min(1.0, 1.0 / (max(ind) * 1e9) ** 0.5)


def _diverged(sim_path):
    """Give the name of a port file that contains NaN.

    NaN shows that the FDTD run diverged. Do this test: openEMS writes
    '-nan(ind)' into the time-domain data of the port, and CalcPort then
    stops with an unclear "could not convert string to float" ValueError.
    """
    for fn in sorted(glob.glob(os.path.join(sim_path, "port_ut_*"))):
        with open(fn) as fh:
            if "nan" in fh.read().lower():
                return os.path.basename(fn)
    return None


def _merge_close(vals, tol):
    """Sort the coordinates and merge those that are nearer than tol.

    This prevents very thin mesh cells.
    """
    vals = sorted(vals)
    out = [vals[0]]
    for v in vals[1:]:
        if v - out[-1] < tol:
            out[-1] = 0.5 * (out[-1] + v)
        else:
            out.append(v)
    return out


def _port_geometry(model, res):
    """Calculate the boxes and the planes of the ports.

    The result contains floats only. The mesh needs these values, thus
    this function runs first.
    """
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
    """Give the fixed lines of the outer PML band of 8 cells.

    The function gives the lines for the two ends of one axis.
    """
    step = margin / 8.0
    return ([lo + i * step for i in range(9)]
            + [hi - i * step for i in range(9)])


def _mesh(model, ports, res):
    """Give the lists of mesh lines (x, y, z) from the geometry and `res`.

    The domain is the region from the extraction. The outer `margin` on
    each of the 6 faces has exactly 8 cells and becomes the PML_8
    absorber. A band of clear air with the same thickness stays between
    the structure and the absorber.
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
    for e in model.get("lumped_elements", []):
        # Hold the box of the element. A part that is less than 1 mm long
        # must not move with the cells.
        xs.update((e["start"][0], e["stop"][0]))
        ys.update((e["start"][1], e["stop"][1]))

    board_top = model["copper_layers"][0]["z"]
    zs = set(_pml_band(-2.0 * margin, board_top + 2.0 * margin, margin))
    for c in model["copper_layers"]:
        zs.add(c["z"])
    for d in model["dielectric_layers"]:
        # 4 cells or more in each dielectric layer
        zs.update(np.linspace(d["z_bottom"], d["z_top"], 5).tolist())

    tol = min(res / 8.0, margin / 20.0)
    return (_merge_close(xs, tol), _merge_close(ys, tol),
            _merge_close(zs, min(tol, 0.05)))


def build(model, excite_idx, res, want_ff=False):
    """Make a new FDTD model and CSX model, with port `excite_idx` excited."""
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS

    s = model["settings"]
    f0 = 0.5 * (s["f_start"] + s["f_stop"])
    fc = 0.5 * (s["f_stop"] - s["f_start"])
    # A smaller timestep needs more steps for the same simulated time.
    # Thus the code increases the number of steps by the same ratio.
    tsf = _time_step_factor(model)
    nrts = s["max_timesteps"]
    if tsf and tsf < 1.0:
        nrts = int(nrts / tsf)
    fdtd = openEMS(NrTS=nrts, EndCriteria=s["end_criteria"])
    if tsf and tsf < 1.0:
        fdtd.SetTimeStepFactor(tsf)
        print("[rfsim] timestep factor %.3g (lumped inductor stability), "
              "max steps %d" % (tsf, nrts), flush=True)
    fdtd.SetGaussExcite(f0, fc)
    # MUR showed a slow increase of the energy at late times on this
    # setup. PML_8 with an absorber band of exactly 8 cells is stable.
    fdtd.SetBoundaryCond(["PML_8"] * 6)
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)  # drawing unit: mm

    ports_geo = _port_geometry(model, res)
    from CSXCAD.SmoothMeshLines import SmoothMeshLines
    # Round the smooth mesh lines. A line at 1.5300000000000002 does not
    # touch a copper sheet with no thickness at exactly 1.53, and openEMS
    # then gives "unused primitive".
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
                                      thickness=max(c["thickness"], 1e-4) * 1e-3)
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

    if s.get("lumped", True):
        le_kw = {"LEtype": 1} if _has_lumped_rlc() else {}
        for e in model.get("lumped_elements", []):
            if e["type"] == "R" and e["value"] == 0:  # 0 ohm = a short circuit
                csx.AddMetal("short_" + e["ref"]).AddBox(
                    e["start"], e["stop"], priority=15)
                unit = "ohm (short)"
            else:
                # LEtype=1 (series) is the topology of a part that has 2
                # terminals and that bridges a gap in a track. openEMS
                # also needs it for a lumped inductor. The components
                # that you do not give are NaN, not 0. Thus an element
                # with one component is the same with the two topologies.
                # validation/run_rlc.py shows this against the theory.
                # The topology becomes important with the package
                # parasitics, which put R, L and C in one element.
                csx.AddLumpedElement("le_" + e["ref"], ny=e["ny"], caps=True,
                                     **dict(le_kw,
                                            **{e["type"]: e["value"]})).AddBox(
                    e["start"], e["stop"], priority=15)
                unit = {"R": "ohm", "L": "H", "C": "F"}[e["type"]]
            print("[rfsim] lumped %s: %s=%g %s (%s-axis) at z=%.3f"
                  % (e["ref"], e["type"], e["value"], unit, e["ny"],
                     e["start"][2]), flush=True)

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

    ff = None
    if want_ff:
        # Dump the E field and the H field on the mid-plane of the
        # substrate, below the port that the run drives. The frequency is
        # the "Define at" value of the user, or the center frequency for
        # an old model. The files are small, but they are enough for the
        # wave animations of the GUI.
        f_dump = s.get("f_field") or f0
        g0 = ports_geo[excite_idx]
        z_cut = 0.5 * (g0["z_top"] + g0["z_ref"])
        r = model["region"]
        for name, dt in (("Ef", 10), ("Hf", 11)):
            # dump_mode=1 interpolates to the mesh nodes. The default
            # value (0) dumps the raw Yee values, which draw H one half
            # of a cell away from the copper.
            dump = csx.AddDump(name, dump_type=dt, dump_mode=1, file_type=1,
                               frequency=[f_dump])
            dump.AddBox([r["x0"], r["y0"], z_cut], [r["x1"], r["y1"], z_cut])

        # The NF2FF box is in the band of clear air between the structure
        # and the PML: the edge of the domain plus 1.5 times the margin.
        # The recording frequency is the same.
        from openEMS.nf2ff import nf2ff
        margin = s["margin_mm"]
        board_top = model["copper_layers"][0]["z"]
        inset = 1.5 * margin
        ff = nf2ff(csx, "nf2ff",
                   [r["x0"] + inset, r["y0"] + inset, -2.0 * margin + inset],
                   [r["x1"] - inset, r["y1"] - inset,
                    board_top + 2.0 * margin - inset],
                   frequency=[f_dump])

    print("[rfsim] mesh: %d x %d x %d lines" % tuple(
        grid.GetQtyLines(a) for a in "xyz"), flush=True)
    return fdtd, ports, ff


def _farfield(outdir, ff, sim_path, port1, freq, suffix=""):
    """Calculate the NF2FF far field at the recorded frequency.

    The recorded frequency is the "Define at" value. The result goes into
    farfield{suffix}.json. There are three cuts, in the style of CST:
    theta sweeps at phi=0 and phi=90, and an azimuth sweep at theta=90.
    Each cut is in absolute dBi. The peak of each slice is the Dmax of
    the engine for that grid of angles.
    """
    f_ff = ff.freq[0]
    print("[rfsim] NF2FF%s at %.3f GHz..." % (suffix, f_ff / 1e9), flush=True)
    theta = np.arange(-180.0, 180.1, 2.0)
    phi_az = np.arange(0.0, 360.1, 2.0)
    center = [0.5 * (a + b) * 1e-3 for a, b in zip(ff.start, ff.stop)]
    res = ff.CalcNF2FF(sim_path, f_ff, theta, [0.0, 90.0], center=center)
    res_az = ff.CalcNF2FF(sim_path, f_ff, [90.0], phi_az.tolist(),
                          center=center, outfile="nf2ff_az.h5")
    theta3 = np.arange(0.0, 180.1, 5.0)   # the full sphere at 5 deg: 3D balloon
    phi3 = np.arange(0.0, 360.1, 5.0)
    res3 = ff.CalcNF2FF(sim_path, f_ff, theta3.tolist(), phi3.tolist(),
                        center=center, outfile="nf2ff_3d.h5")

    def d_dbi(r):
        En = np.maximum(r.E_norm[0] / np.max(r.E_norm[0]), 1e-6)
        return 20.0 * np.log10(En) + 10.0 * np.log10(float(r.Dmax[0]))

    D = d_dbi(res)                                      # (Ntheta, 2)
    D_az = d_dbi(res_az)[0]                             # (Nphi,)
    Dmax = float(res.Dmax[0])
    Prad = float(res.Prad[0])
    i_f = int(np.argmin(np.abs(freq - f_ff)))
    P_in = float(0.5 * np.real(port1.uf_tot[i_f] * np.conj(port1.if_tot[i_f])))
    eff = 100.0 * Prad / P_in if P_in > 0 else None
    with open(os.path.join(outdir, "farfield%s.json" % suffix), "w") as fh:
        json.dump({
            "f_hz": f_ff,
            "cuts": {
                "Phi=0": {"angle_deg": theta.tolist(),
                          "D_dBi": D[:, 0].tolist()},
                "Phi=90": {"angle_deg": theta.tolist(),
                           "D_dBi": D[:, 1].tolist()},
                "Theta=90": {"angle_deg": phi_az.tolist(),
                             "D_dBi": D_az.tolist()},
            },
            "grid3d": {"theta_deg": theta3.tolist(),
                       "phi_deg": phi3.tolist(),
                       "D_dBi": d_dbi(res3).tolist()},
            "Dmax_dBi": 10.0 * np.log10(Dmax), "Prad_W": Prad,
            "P_in_W": P_in, "efficiency_pct": eff,
        }, fh, indent=1)
    print("[rfsim] far-field: Dmax %.1f dBi, radiated %.1f%% of input power"
          % (10.0 * np.log10(Dmax), eff if eff is not None else -1), flush=True)


def write_touchstone(path, freq, S, z0):
    """Write a Touchstone v1 file.

    A file for 1 or 2 ports has one line for each frequency. The columns
    of a 2-port file are in the usual sequence S11 S21 S12 S22. A file
    for 3 ports or more is row-major, with a maximum of 4 pairs on a line.
    """
    n = S.shape[1]
    with open(path, "w") as fh:
        fh.write("! rfsim (KiCad + openEMS)\n# HZ S RI R %g\n" % z0)
        for i, f in enumerate(freq):
            if n <= 2:
                vals = ([S[i, 0, 0]] if n == 1 else
                        [S[i, 0, 0], S[i, 1, 0], S[i, 0, 1], S[i, 1, 1]])
                fh.write("%.6e %s\n" % (f, " ".join(
                    "%.9e %.9e" % (v.real, v.imag) for v in vals)))
                continue
            fh.write("%.6e" % f)
            for j in range(n):
                if j:
                    fh.write("\n           ")  # one line for each matrix row
                for k in range(n):
                    if k and k % 4 == 0:
                        fh.write("\n           ")  # start a line after 4 pairs
                    v = S[i, j, k]
                    fh.write(" %.9e %.9e" % (v.real, v.imag))
            fh.write("\n")


def main(model_path, outdir):
    with open(model_path) as fh:
        model = json.load(fh)
    for w in model.get("warnings", []):
        print("[rfsim] WARNING: %s" % w, flush=True)
    s = model["settings"]
    n = len(model["ports"])
    if n < 1:
        raise SystemExit("expected at least 1 port, got %d" % n)

    # A lumped inductor needs openEMS v0.37 or later, which has lumped
    # RLC. An older engine writes "Lumped Element R or C not specified!
    # skipping" and models an open circuit. Thus refuse to run, because
    # the results would be incorrect.
    if s.get("lumped", True):
        bad = [e["ref"] for e in model.get("lumped_elements", [])
               if e["type"] == "L"]
        if bad and not _has_lumped_rlc():
            raise SystemExit(
                "[rfsim] ERROR: %s: this openEMS build cannot simulate "
                "lumped inductors — it silently drops them, so results "
                "would be wrong (open circuit at the part). Set the value "
                "to DNP, or run the solver under a Python 3.13/3.14 "
                "interpreter with openEMS >= v0.37 (see README, "
                "\"Installation\")." % ", ".join(bad))

    # Remove the results of a previous run from this output directory. An
    # excN folder, or a farfield.json that this run does not write again,
    # looks current to the GUI. This occurs with a different set of
    # excited ports, or after a far field that failed.
    for d in glob.glob(os.path.join(outdir, "exc*")):
        shutil.rmtree(d, ignore_errors=True)
    for fpath in glob.glob(os.path.join(outdir, "farfield*.json")):
        try:
            os.remove(fpath)
        except OSError:
            pass

    eps_max = max(d["epsilon"] for d in model["dielectric_layers"])
    lam_min = C0 / s["f_stop"] / np.sqrt(eps_max) * 1e3  # mm
    res = lam_min / RES_DIV[s["mesh"]]
    print("[rfsim] mesh resolution: %.3f mm (%s)" % (res, s["mesh"]), flush=True)

    freq = np.linspace(s["f_start"], s["f_stop"], s.get("n_freq", 401))
    S = np.zeros((len(freq), n, n), dtype=complex)
    # Excite only the ports that the user selected. Each port is a full
    # FDTD run. The S-columns of the other ports stay zero. A default
    # model, or an old model, excites all the ports.
    nums = [p["number"] for p in model["ports"]]
    want = set(s.get("excite") or nums)
    exc = [i for i, num in enumerate(nums) if num in want] or [0]
    for step, k in enumerate(exc):
        sim_path = os.path.join(outdir, "exc%d" % (k + 1))
        print("[rfsim] === excitation %d/%d (port %d) ==="
              % (step + 1, len(exc), k + 1), flush=True)
        fdtd, ports, ff = build(model, k, res, want_ff=True)
        fdtd.Run(sim_path, cleanup=True)
        bad = _diverged(sim_path)
        if bad:
            tsf = _time_step_factor(model) or 1.0
            raise SystemExit(
                "[rfsim] ERROR: the FDTD run diverged — NaN in %s.\n"
                "A lumped inductor is the usual cause: it needs a "
                "sub-Courant timestep. This run used time_step_factor "
                "%.3g; set a smaller \"time_step_factor\" in the model's "
                "settings (e.g. %.3g) and re-run." % (bad, tsf, tsf / 2.0))
        for p in ports:
            p.CalcPort(sim_path, freq, ref_impedance=s["z0"])
        for j in range(n):
            S[:, j, k] = ports[j].uf_ref / ports[k].uf_inc
        if ff is not None:
            try:
                _farfield(outdir, ff, sim_path, ports[k], freq,
                          "_p%d" % (k + 1))
            except Exception as e:
                print("[rfsim] WARNING: far-field (port %d) failed: %s"
                      % (k + 1, e), flush=True)

    out = os.path.join(outdir, "results.s%dp" % n)
    write_touchstone(out, freq, S, s["z0"])
    for k in exc:  # show only the columns that this run calculated
        for j in range(n):
            mag = 20 * np.log10(np.maximum(np.abs(S[:, j, k]), 1e-12))
            print("[rfsim] S%d%d: %.1f .. %.1f dB"
                  % (j + 1, k + 1, mag.min(), mag.max()), flush=True)
    print("[rfsim] wrote %s" % out, flush=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python runner.py model.json output_dir")
    os.makedirs(sys.argv[2], exist_ok=True)
    main(sys.argv[1], sys.argv[2])
