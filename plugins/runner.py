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
# The port types that openEMS de-embeds. Each one gives the impedance of
# the line and the propagation constant. A lumped port does not.
TL_PORTS = ("msl", "cpw", "stripline")
# The number of mesh cells across each gap of a CPW port, and across
# the strip. The voltage probe of the port integrates E over the cells
# in the gap, thus the wavelength must not control that step.
CPW_GAP_CELLS = 4
CPW_STRIP_CELLS = 8


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
    # The package parasitics put an ESL in each element. A body ESL is
    # less than 1 nH, thus it keeps the factor at 1.0 and it costs no
    # extra steps. But the code must count it: the criterion is the
    # largest inductance in the model, whatever its source.
    ind = []
    for e in model.get("lumped_elements", []):
        if e["type"] == "L" and e["value"] > 0:
            ind.append(e["value"])
        if s.get("parasitics", True) and e.get("esl"):
            ind.append(e["esl"])
    if not ind:
        return None
    return min(1.0, 1.0 / (max(ind) * 1e9) ** 0.5)


def _cell_count(fdtd):
    """Give the number of mesh cells of a model that build() made."""
    grid = fdtd.GetCSX().GetGrid()
    n = 1
    for axis in "xyz":
        n *= grid.GetQtyLines(axis)
    return n


def _threads(s, cells):
    """Give the number of threads for the engine.

    settings["threads"] has priority. A value of None (the "Auto" item of
    the dialog) gives the value that this function calculates.

    openEMS keeps the "fastest" engine if the caller names no engine, and
    that engine leaves the largest part of the machine idle. The
    multithreaded engine gives one slice of the domain to each thread,
    and it synchronizes the threads at each timestep. Thus the slowest
    thread controls the speed of all the others, and a thread that gets a
    small slice costs more than it gives.

    The count must therefore follow the size of the model. These are
    measurements on an i7-12700K (8 performance cores, 4 efficiency
    cores), in MCells/s:

        cells      2 thr   4 thr   8 thr   12 thr
        62 k        27.6    35.4    28.4    20.8
        147 k       34.7    52.5    59.4    50.6
        504 k       44.2    74.3   105.9   104.9
        1.62 M      56.1    94.6   135.3   145.1

    Thus a small model is fastest at 4 threads, and 8 threads make it 20%
    slower. Above about 150 k cells, 8 threads is the best value that a
    usual desktop gives. More than 8 gives little: the efficiency cores
    take a slice of the same size as a performance core, and each
    performance core then waits for them at every timestep.
    """
    n = s.get("threads")
    if n:
        return max(1, int(n))
    cap = min(8, os.cpu_count() or 1)
    return min(cap, 4) if cells < 150000 else cap


def _parasitic_components(e):
    """Give the parasitic components of the body of a part.

    The engine puts the R, the L and the C of one element in series
    (LEtype=1). Thus:

    - A capacitor becomes ESR + ESL + C. This is the usual model of a real
      capacitor, and it puts the self-resonance at the correct frequency.
    - A resistor becomes R + ESL.
    - An inductor becomes DCR + L. A series element cannot make the
      parallel capacitance of a real inductor. Thus this model does not
      give the self-resonance of an inductor.

    The values are for the body of the part only. The mesh already
    contains the loop of the pads and the tracks.
    """
    out = {}
    esl, esr = e.get("esl") or 0.0, e.get("esr") or 0.0
    if e["type"] in ("R", "C") and esl > 0:
        out["L"] = esl
    if e["type"] in ("C", "L") and esr > 0:
        out["R"] = esr
    return out


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


def _merge_close(vals, tol, anchors=()):
    """Sort the coordinates and merge those that are nearer than tol.

    This prevents very thin mesh cells.

    A value in `anchors` does NOT move: the merged line takes the value
    of the anchor, and not the mean of the two. A lumped element is a box
    between two mesh lines and it has no line of its own inside, thus a
    face that the mean moves can leave the box with no cell at all.

    The failure is measured, and it is not an open circuit: the copper of
    the two pads meets on that one line, thus the gap CLOSES and the run
    gives a piece of line. A series 50 ohm in a 50 ohm line gave S21
    -0.16 dB in the place of -4.5 dB, and S11 -17.4 dB in the place of
    -10.3 dB. openEMS gives NO warning for it.
    """
    keep = set(round(a, 9) for a in anchors)
    vals = sorted(vals)
    out = [vals[0]]
    held = [round(vals[0], 9) in keep]
    for v in vals[1:]:
        if v - out[-1] < tol:
            if round(v, 9) in keep and not held[-1]:
                out[-1] = v          # the anchor takes the place of the mean
                held[-1] = True
            elif not held[-1]:
                out[-1] = 0.5 * (out[-1] + v)
        else:
            out.append(v)
            held.append(round(v, 9) in keep)
    return out


def _port_geometry(model, res):
    """Calculate the boxes and the planes of the ports.

    The result contains floats only. The mesh needs these values, thus
    this function runs first.

    A microstrip port goes from the strip plane down to the plane of the
    reference layer. A CPW port and a stripline port are FLAT: openEMS
    refuses a start and a stop that are different in the direction of
    exc_dir. Their return path is the coplanar copper, or the two planes.
    """
    z_of = {c["name"]: c["z"] for c in model["copper_layers"]}
    ports = []
    plist = model["ports"]
    for i_p, p in enumerate(plist):
        z_top = z_of[p["layer"]]
        z_ref = z_of[p["ref_layer"]]
        g = dict(p, z_top=z_top, z_ref=z_ref)
        # Each de-embedded port needs a track that gives the direction.
        # A CPW port also needs the gap, and a stripline port needs a
        # plane above the strip and a plane below it.
        need = {"cpw": "gap", "stripline": "height"}.get(p["type"])
        if (p["type"] in TL_PORTS and p["direction"]
                and (need is None or p.get(need))):
            d = p["direction"]
            # width and length are on the axes of the board. The extent
            # across the feed is width for a feed on x, and length for a
            # feed on y.
            w = p.get("track_width") or (p["width"] if d[0] else p["length"])
            length = max(3.0 * w, 6.0 * res)
            if len(plist) == 2:
                # The OTHER port, by list position. Do not index with
                # p["number"]: a hand-edited model.json can hold numbers
                # that are not 1..N in list order, and that gives the
                # wrong port with no message.
                q = plist[1 - i_p]
                dist = max(abs(q["x"] - p["x"]), abs(q["y"] - p["y"]))
                if dist > 0:
                    length = min(length, 0.3 * dist)
            # Only a microstrip port goes down to the reference plane.
            z_far = z_ref if p["type"] == "msl" else z_top
            if d[0]:
                start = [p["x"], p["y"] - w / 2, z_top]
                stop = [p["x"] + d[0] * length, p["y"] + w / 2, z_far]
                g["prop_dir"] = "x"
            else:
                start = [p["x"] - w / 2, p["y"], z_top]
                stop = [p["x"] + w / 2, p["y"] + d[1] * length, z_far]
                g["prop_dir"] = "y"
            g.update(start=start, stop=stop, msl_width=w, msl_len=length)
        else:
            if p["type"] in TL_PORTS:
                why = ("it has no attached track" if not p["direction"] else
                       "it has no coplanar gap" if p["type"] == "cpw" else
                       "it has no plane above and below the strip")
                print("[rfsim] WARNING: port %d (%s): %s; falling back to a "
                      "lumped port" % (p["number"], p["type"], why),
                      flush=True)
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
        # The CENTER line is necessary, and not only the two edges.
        # openEMS makes a metal primitive into PEC on the edges of the
        # Yee grid, thus a mesh NODE must lie inside the barrel. The two
        # edge lines put the nodes exactly on the surface of the cylinder
        # and leave the inside empty. openEMS then writes "Unused
        # primitive (type: Cylinder)" and the via conducts nothing: the
        # planes stay separate and the model is incorrect with no error.
        xs.update((v["x"] - v["r"], v["x"], v["x"] + v["r"]))
        ys.update((v["y"] - v["r"], v["y"], v["y"] + v["r"]))
    for g in ports:
        xs.update((g["start"][0], g["stop"][0], g["x"]))
        ys.update((g["start"][1], g["stop"][1], g["y"]))
        if g["type"] == "cpw":
            # The voltage probes of a CPW port go across the two gaps, and
            # the current probe goes around the strip. The E field has a
            # peak at each edge of the strip. Thus the mesh step across
            # the line must come from the gap, and not from the
            # wavelength: a gap of 0.3 mm with a step of 2.4 mm gives
            # an impedance that is about 30% too small.
            across = ys if g["prop_dir"] == "x" else xs
            c = g["y"] if g["prop_dir"] == "x" else g["x"]
            hw = 0.5 * g["msl_width"]
            for side in (-1, 1):
                for i in range(CPW_GAP_CELLS + 1):
                    across.add(c + side * (hw + g["gap"] * i / CPW_GAP_CELLS))
                half = max(1, CPW_STRIP_CELLS // 2)
                for i in range(1, half + 1):
                    across.add(c + side * hw * i / half)
                # Grade the mesh outward from the gap. SmoothMeshLines
                # cannot do this: it fills each interval between two fixed
                # lines separately, thus a cell of 0.08 mm can touch a
                # cell of 1 mm. Such a step reflects the wave and it makes
                # the impedance incorrect, and more lines in the full
                # domain do not correct it.
                pos = c + side * (hw + g["gap"])
                step = g["gap"] / CPW_GAP_CELLS
                while step < res:
                    pos += side * step
                    across.add(pos)
                    step *= 1.4
        elif g["type"] == "stripline":
            # A stripline needs the same treatment as the strip of a CPW
            # port. Before, only the CPW branch existed, thus the mesh
            # step across a stripline came from the WAVELENGTH: the strip
            # of 0.6 mm of validation/ is narrower than one cell of
            # 2.355 mm at the coarse preset. That board measured 19.4 ohm
            # against 38.9 ohm from IPC-2141; with these cells it gives
            # 39.2 ohm at the SAME preset, and the mesh grows only from
            # 57x39x48 lines to 57x57x48. The medium mesh gave 34.6 ohm
            # without them, which is how the mesh was found to be the
            # cause.
            across = ys if g["prop_dir"] == "x" else xs
            c = g["y"] if g["prop_dir"] == "x" else g["x"]
            hw = 0.5 * g["msl_width"]
            half = max(1, CPW_STRIP_CELLS // 2)
            for side in (-1, 1):
                for i in range(1, half + 1):
                    across.add(c + side * hw * i / half)
                # Grade outward from the edge of the strip, as the CPW
                # branch grades outward from the gap.
                pos, step = c + side * hw, hw / half
                while step < res:
                    pos += side * step
                    across.add(pos)
                    step *= 1.4
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
    # A CPW port and a stripline port put their current probe 2 mesh cells
    # above the strip and 2 below it. The air above the board has no
    # dielectric rule, thus its cells are as large as the full mesh step,
    # and the probe box then becomes very large and asymmetric. It then
    # measures a current that is much too large, and the impedance of the
    # line becomes much too small. Thus put some lines at each side of
    # each copper plane. The step is the step of the dielectric rule, thus
    # this operation makes no cell smaller and it costs no timestep.
    if model["dielectric_layers"]:
        step = 0.25 * min(d["z_top"] - d["z_bottom"]
                          for d in model["dielectric_layers"])
        for c in model["copper_layers"]:
            for k in (1, 2):
                zs.update((c["z"] + k * step, c["z"] - k * step))
    for g in ports:
        if g["type"] != "cpw" or not g["gap"]:
            continue
        # A CPW port also needs its own cells ABOVE and BELOW the plane of
        # the line, and their step must come from the GAP. The line of a
        # CPW has no plane below it that holds the field: the field goes
        # from the strip across the two gaps, thus it is at its largest
        # within about one gap width of the surface. The step of the rule
        # above comes from the thickness of the dielectric (0.38 mm on a
        # board of 1.6 mm), which is much larger than a usual gap of
        # 0.3 mm. The capacitance of the line then comes out about 27% too
        # large, and the impedance about 21% too small. The value does NOT
        # converge with the mesh preset, thus the fault does not look
        # like a mesh fault. The step is the step of the gap cells, thus
        # it makes no cell smaller than the y mesh of the gap already is.
        st = g["gap"] / CPW_GAP_CELLS
        for side in (-1, 1):
            # Stop at the next copper plane on that side. A line of this
            # chain that lands NEAR the plane is worse than no line at
            # all: _merge_close would join the two and MOVE the line of
            # the plane, and a copper sheet that has no line on it is not
            # metal. openEMS then writes "Unused primitive (type:
            # LinPoly)" and the plane conducts nothing, in the same way as
            # the vias of 2026-08-03 (10).
            nxt = [c["z"] for c in model["copper_layers"]
                   if side * (c["z"] - g["z_top"]) > 0]
            limit = (min(nxt) if side > 0 else max(nxt)) if nxt else None
            pos, step, n = g["z_top"], st, 0
            while True:
                pos += side * step
                if limit is not None and side * (pos - limit) > -0.25 * st:
                    break
                zs.add(pos)
                n += 1
                # Grade outward after the cells of the gap, as the branch
                # across the line does.
                if n >= CPW_GAP_CELLS:
                    step *= 1.4
                if step >= res:
                    break

    tol = min(res / 8.0, margin / 20.0)
    # The cells across a stripline strip are much smaller than the mesh
    # step. Thus the merge would remove them again, in the same way as it
    # would remove the lines of a CPW gap.
    strips = [g["msl_width"] for g in ports
              if g["type"] == "stripline" and g.get("msl_width")]
    if strips:
        tol = min(tol, 0.25 * min(strips) / CPW_STRIP_CELLS)
    gaps = [g["gap"] for g in ports if g["type"] == "cpw" and g["gap"]]
    if gaps:
        # A CPW gap is usually much smaller than the mesh step. Thus the
        # merge can remove the lines in the gap, and the voltage probes
        # of the port then measure across the wrong cells.
        tol = min(tol, 0.25 * min(gaps) / CPW_GAP_CELLS)
    # The cells above and below the plane of a CPW have the same step as
    # the cells in the gap. Thus the z merge needs the same tolerance, or
    # it removes them again.
    tol_z = min(tol, 0.05)
    if gaps:
        tol_z = min(tol_z, 0.25 * min(gaps) / CPW_GAP_CELLS)
    # The box of a lumped element has 2 lines only: its faces. Thus the
    # merge must keep them apart, in the same way as it keeps the lines
    # of a CPW gap apart. An element whose box is smaller than tol keeps
    # ONE line, the copper of its two pads then meets on that line, and
    # the part becomes a piece of track with no message. Measured at the
    # coarse preset with a margin of 4 mm, where tol is 0.200 mm: a
    # series 50 ohm in a gap of 0.15 mm gave S21 -0.16 dB, against
    # -4.50 dB with the clamp and -3.5 dB from the theory. A part with a
    # gap of 0.5 mm is not affected (-4.56 dB with and without it). The
    # anchors below then hold the two faces on their own coordinates,
    # because a face that the mean moves can also leave the box with no
    # cell.
    le_x, le_y = [], []
    for e in model.get("lumped_elements", []):
        le_x += [e["start"][0], e["stop"][0]]
        le_y += [e["start"][1], e["stop"][1]]
    boxes = [abs(b - a) for a, b in zip(le_x[::2], le_x[1::2])]
    boxes += [abs(b - a) for a, b in zip(le_y[::2], le_y[1::2])]
    boxes = [b for b in boxes if b > 0]
    if boxes:
        tol = min(tol, 0.25 * min(boxes))
    return (_merge_close(xs, tol, le_x), _merge_close(ys, tol, le_y),
            _merge_close(zs, tol_z))


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
    port_layers = {p["layer"] for p in model["ports"]}
    for c in model["copper_layers"]:
        polys = model["polygons"].get(c["name"], [])
        # A layer with no copper needs no property. A property with no
        # primitive makes openEMS print "No primitives found".
        if not polys and c["name"] not in port_layers:
            continue
        prop = csx.AddConductingSheet("cu_" + c["name"], conductivity=5.8e7,
                                      thickness=max(c["thickness"], 1e-4) * 1e-3)
        copper_prop[c["name"]] = prop
        for poly in polys:
            pts = np.array(poly).T  # shape (2, N)
            prop.AddLinPoly(pts, "z", c["z"], 0, priority=10)

    if model["vias"]:
        via_metal = csx.AddMetal("vias")
        for v in model["vias"]:
            via_metal.AddCylinder([v["x"], v["y"], v["z0"]],
                                  [v["x"], v["y"], v["z1"]],
                                  v["r"], priority=10)

    if s.get("lumped", True):
        rlc = _has_lumped_rlc()
        le_kw = {"LEtype": 1} if rlc else {}
        # The package parasitics need R, L and C together in one element,
        # thus they need the series topology of LEtype. An older engine
        # has no LEtype, and it removes an element that has an L without a
        # message. Thus the code drops the parasitics on such an engine.
        para = s.get("parasitics", True) and rlc
        if s.get("parasitics", True) and not rlc:
            print("[rfsim] WARNING: this openEMS build has no LEtype; the "
                  "package parasitics are OFF (ideal elements)", flush=True)
        for e in model.get("lumped_elements", []):
            # A part whose refdes does not give the type comes out of
            # the extraction with type None and value None, and the
            # dialog removes it when the user models nothing. A
            # model.json that a person edits can still hold one.
            if not e.get("type") or e.get("value") is None:
                print("[rfsim] WARNING: lumped %s has no type or no value; "
                      "not modeled (the gap between its pads stays open)"
                      % e.get("ref", "?"), flush=True)
                continue
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
                comp = {e["type"]: e["value"]}
                if para:
                    comp.update(_parasitic_components(e))
                csx.AddLumpedElement("le_" + e["ref"], ny=e["ny"], caps=True,
                                     **dict(le_kw, **comp)).AddBox(
                    e["start"], e["stop"], priority=15)
                unit = {"R": "ohm", "L": "H", "C": "F"}[e["type"]]
                extra = ["%s %g %s" % (k, v, {"R": "ohm", "L": "H",
                                              "C": "F"}[k])
                         for k, v in sorted(comp.items()) if k != e["type"]]
                if extra:
                    unit += " + %s (%s body)" % (
                        ", ".join(extra), e.get("package") or "unknown package")
            print("[rfsim] lumped %s: %s=%g %s (%s-axis) at z=%.3f"
                  % (e["ref"], e["type"], e["value"], unit, e["ny"],
                     e["start"][2]), flush=True)

    ports = []
    for i, g in enumerate(ports_geo):
        excite = (i == excite_idx)
        note = ""
        if g["type"] in TL_PORTS:
            # exc_dir is "z" for all three types. A microstrip port uses
            # it as the direction from the strip to the plane. A CPW port
            # and a stripline port use it only to find the plane of the
            # strip: their probes go across the gaps, or up and down.
            kw = dict(excite=(-1 if g["type"] == "msl" else 1) if excite else 0,
                      FeedShift=res, MeasPlaneShift=0.5 * g["msl_len"],
                      Feed_R=s["z0"], priority=20)
            metal = copper_prop[g["layer"]]
            args = (g["number"], metal, g["start"], g["stop"], g["prop_dir"],
                    "z")
            if g["type"] == "msl":
                ports.append(fdtd.AddMSLPort(*args, **kw))
            elif g["type"] == "cpw":
                ports.append(fdtd.AddCPWPort(*args, g["gap"], **kw))
                note = ", gap %.3f mm" % g["gap"]
            else:
                ports.append(fdtd.AddStripLinePort(*args, g["height"], **kw))
                note = ", %.3f mm to each plane" % g["height"]
            note = "dir " + g["prop_dir"] + note
        else:
            ports.append(fdtd.AddLumpedPort(
                g["number"], s["z0"], g["start"], g["stop"], "z",
                excite=1.0 if excite else 0, priority=20))
        print("[rfsim] port %d: %s at (%.2f, %.2f) %s" % (
            g["number"], g["type"], g["x"], g["y"], note), flush=True)

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


def _line_data(port, sim_path, freq):
    """Give the impedance of the line and eps_eff of a port, or give None.

    A de-embedded port (microstrip, CPW or stripline) has three voltage
    probes and two current probes along the line. From them, ReadUIData
    calculates `beta`, the propagation constant, and `Z_ref`, the
    impedance of the line. These are the values of the REAL track on the
    REAL stackup: KiCad has no other tool that gives them.

    CalcPort then writes the reference impedance of the system (usually
    50 ohm) over Z_ref, because the S-parameters need that value. Thus the
    caller must call this function BEFORE CalcPort.

    A lumped port has no line, thus it has no beta. Then the result is
    None.
    """
    port.ReadUIData(sim_path, freq)
    if not hasattr(port, "beta"):
        return None
    z = np.asarray(port.Z_ref, dtype=complex)
    beta = np.asarray(port.beta, dtype=complex)
    # The effective permittivity: eps_eff = (beta / k0)^2, and k0 = w/c0.
    k0 = 2.0 * np.pi * np.asarray(freq, dtype=float) / C0
    with np.errstate(divide="ignore", invalid="ignore"):
        eps = (np.real(beta) / k0) ** 2
    return {"Z0_real": np.real(z).tolist(),
            "Z0_imag": np.imag(z).tolist(),
            "eps_eff": np.where(np.isfinite(eps), eps, 0.0).tolist()}


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
    for fpath in (glob.glob(os.path.join(outdir, "farfield*.json"))
                  + [os.path.join(outdir, "lines.json")]):
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
    lines = {}  # the port number -> the impedance data of its line
    for step, k in enumerate(exc):
        sim_path = os.path.join(outdir, "exc%d" % (k + 1))
        print("[rfsim] === excitation %d/%d (port %d) ==="
              % (step + 1, len(exc), k + 1), flush=True)
        fdtd, ports, ff = build(model, k, res, want_ff=True)
        if step == 0:
            # The mesh is the same for each excitation. Thus calculate the
            # thread count one time, and tell the user which value it is.
            threads = _threads(s, _cell_count(fdtd))
            print("[rfsim] engine: multithreaded, %d thread(s)" % threads,
                  flush=True)
        fdtd.Run(sim_path, cleanup=True, engine="multithreaded",
                 numThreads=threads)
        bad = _diverged(sim_path)
        if bad:
            tsf = _time_step_factor(model) or 1.0
            raise SystemExit(
                "[rfsim] ERROR: the FDTD run diverged — NaN in %s.\n"
                "A lumped inductor is the usual cause: it needs a "
                "sub-Courant timestep. This run used time_step_factor "
                "%.3g; set a smaller \"time_step_factor\" in the model's "
                "settings (e.g. %.3g) and re-run." % (bad, tsf, tsf / 2.0))
        # Read the impedance of the line of the excited port first. The
        # wave of that port is the cleanest, and CalcPort replaces the
        # value some lines below.
        ld = _line_data(ports[k], sim_path, freq)
        if ld:
            lines[k + 1] = ld
            f_at = s.get("f_field") or 0.5 * (s["f_start"] + s["f_stop"])
            i_at = int(np.argmin(np.abs(freq - f_at)))
            print("[rfsim] port %d line: Z0 = %.1f%+.1fj ohm, eps_eff = %.2f "
                  "(at %.3f GHz)"
                  % (k + 1, ld["Z0_real"][i_at], ld["Z0_imag"][i_at],
                     ld["eps_eff"][i_at], freq[i_at] / 1e9), flush=True)
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

    if lines:
        with open(os.path.join(outdir, "lines.json"), "w") as fh:
            json.dump({"freq_hz": freq.tolist(), "ports": lines}, fh, indent=1)

    # A passive structure cannot give out more power than it takes in.
    # Thus the sum of |S|^2 down an excited column must not go above 1.
    # A value above 1 shows that the S-parameters are incorrect, and not
    # only inexact. On a CPW port or a stripline port the usual cause is
    # a mesh that is too coarse near the line: the measurement on the
    # stripline board gave sum|S|^2 = 1.71 at the coarse preset and 1.05
    # at the medium preset, and the CPW board gave 1.08 before it had
    # its cells above and below the plane of the line.
    for k in exc:
        power = np.sum(np.abs(S[:, :, k]) ** 2, axis=1)
        if power.max() > 1.05:
            print("[rfsim] WARNING: port %d gives out more power than it "
                  "takes in (max sum|S|^2 = %.2f). The S-parameters of this "
                  "port are NOT reliable. On a CPW port or a stripline port "
                  "the usual cause is a mesh that is too coarse across the "
                  "line. Run again at the medium or the fine preset."
                  % (k + 1, power.max()), flush=True)

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
