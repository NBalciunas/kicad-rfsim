"""The openEMS runner, which is a process of its own: it changes model.json
into a Touchstone file.

The runner runs out of KiCad. The plugin starts it as a subprocess, or you
can start it manually:

    python runner.py model.json output_dir

Thus a crash of the solver cannot stop KiCad, and you can test the
simulation without the GUI. The runner imports only numpy, CSXCAD and
openEMS. It does not import pcbnew or wx.

The runner excites each port in sequence. N ports give N runs, which fill
the full S-matrix.
"""
import bisect
import glob
import json
import os
import shutil
import sys
import time
import warnings


def _show_warning(message, category, filename, lineno, file=None, line=None):
    """Write a python warning as one [rfsim] line.

    A library such as h5py writes its warning to stderr in the default
    format, which is two lines and has a file path in it. The log of the
    plugin shows the output of the runner. Thus give each warning the same
    prefix as the other messages of the runner.

    The name of the category is the only word for the level. A "WARNING:"
    in front of "UserWarning:" gives the same data two times.
    """
    print("[rfsim] %s: %s" % (category.__name__, message), flush=True)


warnings.showwarning = _show_warning

import solverenv  # the directory of this file is sys.path[0] for a script

# On Windows, the python extensions of openEMS and CSXCAD must have the
# DLLs from the binary directory of openEMS. (openEMS v0.37 and after also
# give a CSXCAD_INSTALL_PATH environment variable, but add_dll_directory is
# sufficient. This is a test result on v0.37.0-rc1.)
if os.name == "nt":
    for _d in solverenv.openems_dirs():
        if os.path.isdir(_d):
            os.add_dll_directory(_d)

import numpy as np

C0 = 299792458.0
EPS0 = 8.8541878128e-12
# Cells per wavelength. **The preset controls the step of the AIR and of
# the plain substrate only.** The copper gives its own lines. Thus a track
# that is narrower than the step also gets its cells: refer to `_mesh` and
# to `POLY_FEATURE_CELLS`. The ultrafine preset costs 8 times the cells of
# fine and half the timestep, thus about 16 times the work. It is for a
# structure with parts that are much smaller than the wavelength, such as a
# divider or a coupler. It is not for a first look. The rigs read
# `runner.RES_DIV`, and `solverenv` owns it. The dialog and `board_reader`
# use the same step to set the dimensions of the domain.
RES_DIV = solverenv.RES_DIV
# The port types that openEMS de-embeds. Each one gives the impedance of
# the line and the propagation constant. A lumped port does not.
TL_PORTS = ("msl", "cpw", "stripline")
# The number of mesh cells across each gap of a CPW port, and across the
# strip. The voltage probe of the port integrates E across the cells in the
# gap. Thus the wavelength must not control that step.
CPW_GAP_CELLS = 4
CPW_STRIP_CELLS = 8
# The same rule for the strip of a MICROSTRIP port, with its own number. A
# microstrip has no gap that sets the step. Thus these cells are the
# smallest cells of the board, and they control the timestep. Measured on
# 2026-08-05 on the 2.9 mm track of validation/, against 49.8 ohm from
# Hammerstad and Jensen:
#
#   cells   Z0 coarse   Z0 medium   cells in the model
#   none      44.3        47.2         60865   (it did not converge)
#   4         47.7        47.8         67445
#   8         49.3        49.3         80605
#
# 8 cells give the more accurate impedance, and 4 are the value here. With
# 8, `run_shunt.py` reads a body ESL that is incorrect at THE TWO presets.
# On 2026-09-14, the body of 0.25 nH read +23% at coarse, and the body of
# 1 nH read -20% at medium. With 4 cells, they read -1.5% / +6.7% and -2.9%
# / +0.6%.
#
# The error occurs only with the x lines AND the y lines of the 8-cell mesh
# together. Each set without the other keeps the two bodies in 11%. The
# lines in the element box change nothing. 4 cells keep that rig correct,
# and they also remove the error that does not converge. That is the
# function of a mesh rule. Increase this to 8 for a more accurate
# microstrip only on a board that has no lumped element.
MSL_STRIP_CELLS = 4
# The tolerance that makes an edge of a polygon STRAIGHT. `board_reader`
# rounds each coordinate to 1e-5 mm. Thus the two ends of a straight edge
# are FULLY equal, and all values below that grid are satisfactory.
FLAT_MM = 1e-7
# The surface lines of a via go this fraction of the radius IN the barrel.
# openEMS makes a metal primitive PEC on the edges of the Yee grid when
# their NODE is in the primitive. A node on the surface counts only when
# the doubles put it in the primitive. 20.2 - 20.0 = 0.1999999999999993 is
# in r = 0.2, but 20.3 - 20.0 = 0.3000000000000007 is out of r = 0.3.
#
# Thus the barrel of a 0.6 mm drill held ONE PEC edge. That is the drill of
# all the via boards here. It read +155% at coarse and +158% at medium in
# inductance against Goldfarb and Pucel. A barrel of 5 edges reads +13% to
# +20%. With the lines in the barrel, r = 0.3 mm reads +19% and +20%.
#
# 1 ppm of the radius is 0.3 nm on a 0.6 mm drill, thus it cannot move the
# geometry. It moves the node off the boundary and has no other effect. It
# must stay above the rounding of `build()`, which rounds each mesh line to
# 1e-9 mm. 1 ppm of a 0.1 mm radius is 0.1 nm, thus 100 times that
# rounding.
#
# **The three lines of a via are ANCHORS of `_merge_close`**, as the two
# faces of a lumped element box are. Before, a copper edge near a via line
# (nearer than `tol`) moved that line to the mean of the two. The node was
# then OUT OF the barrel. In this procedure, 19 of the 102 axis cases of
# `validation/` did not keep a line. The 0402 and the 0603 land of the ESL
# table were two of them. At this time, the copper edge moves to the via
# line. Thus no cell becomes smaller than the merge lets it be.
#
# **A via with a radius smaller than `tol` keeps only ONE edge**, because
# the merge attaches its own three lines. At coarse, `tol` is 0.18 mm, thus
# r = 0.15 mm stays a thin wire. The owner refused a smaller `tol`, because
# it makes the cells of all the board smaller.
#
# **The rank below sets WHICH of the three lines stays**, and it changed
# the number. The CENTRE wins, thus the node is at the axis of the barrel.
# `run_via.py coarse` then reads -16% against Goldfarb and Pucel. Before,
# the mean of the centre and a surface line put that node 0.106 mm off the
# axis, and it read +54%.
#
# The centre must also win for a different cause. Three anchors of one rank
# keep the FIRST, which is the surface line at x - r. A barrel with its
# only node 1 ppm from its wall can then have no node on the other axis.
# Such a barrel writes "Unused primitive", and no current flows in it.
VIA_SURFACE = 1.0 - 1e-6
# The rank of an anchor of `_merge_close`. A line of a higher rank does not
# move. A line of a lower rank that is nearer to it than `tol` goes away.
# Only these three rules make an anchor. All the other lines go to the
# mean, as before.
#
# **The face of an element has a higher rank than a via**, because the two
# failures are not equally large. A face that moves can CLOSE the gap of
# the part and give a piece of track with no message. (A series 50 ohm read
# S21 -0.16 dB and not -4.5 dB.) A via without one of its three lines keeps
# the other two. It then reads as a thinner barrel.
#
# This case occurs on a board. On the 0402 and the 0603 land of
# `run_shunt.py packages`, the surface line of the via is 0.02 mm from a
# face of the element box. Thus the via keeps only its centre on that axis.
# Those two boards did the same before the ranks, because a face was the
# only anchor at that time.
RANK_FACE = 2
RANK_VIA_CENTRE = 1
RANK_VIA_SURFACE = 0
# The number of mesh cells across a narrow copper feature that is not in a
# port. The feature is narrower than `res`: a track, a gap between two
# pads, or a slot. 2 cells put ONE line in the feature, and its two edge
# lines hold the width.
#
# **Two lines are not sufficient without other lines.** CSXCAD counts a
# point ON the boundary of a polygon as a point in the polygon. Thus
# current flows in the two edge lines, and the copper stays. But one cell
# across a track is the "none" row of the table at MSL_STRIP_CELLS. The
# microstrip of `validation/` gave 44.3 ohm at coarse and 47.2 at medium,
# against 49.8 ohm from Hammerstad and Jensen. It did NOT converge.
#
# **`validation/run_feature.py` measures this number** against a closed
# formula: the eps_eff of an open stub, from the notches of two lengths. A
# 1.50 mm stub at the coarse preset reads +3.9% at 1 cell, +1.0% at 2 cells
# and +0.4% at 3. A 0.40 mm stub at coarse reads +8.1% at 1 cell and +2.5%
# at 2. The answer converges at 2 to 3 cells, thus 2 is sufficient. The
# mesh does not grade outward from a feature, and `_feature_lines` gives
# the measurement for that.
POLY_FEATURE_CELLS = 2
# The margin and the law of the timestep rule are in `solverenv`. The
# DIALOG shows the cost of an inductor before a run, and it must not import
# this module. An import of the runner replaces `warnings.showwarning` for
# all the process. The name stays here for the rigs that read it.
LE_STAB_MARGIN = solverenv.LE_STAB_MARGIN
# `_diverged` calls a run NOT STABLE when the largest |u| of the last tenth
# of the port data is too large. The limit is this multiple of the SMALLEST
# point of the envelope of that trace.
#
# **Before 2026-09-20, the rule compared the end with the largest |u| of
# the FIRST HALF. A growth that starts late did not cause an alarm** (B42).
# A trace can decrease by a factor of 1e5 and then increase by a factor of
# 1e3. Its end is then below its head. The previous rule saw no problem
# there. The board of `validation/out_rlc_growth_late_2026-09-19/`
# increased with an e-fold of 6.9 ns, and it ended at 0.0038 of its head.
# The runner ACCEPTED its S-matrix. The minimum of the envelope is the turn
# of such a trace, and all of the trace after it is the growth.
#
# **The envelope, and not |u| itself.** |u| is zero at each null of the
# trace, thus the smallest raw sample is zero. `GROWTH_SEGMENTS` divides
# the trace into equal parts and gets the largest |u| of each one. The turn
# is the smallest of those between the excitation and the end.
#
# **The limit includes a null of a beat.** Two modes that beat give an
# envelope with a deep null in the middle. The trace then increases much
# above it, but the run is stable. A measurement examined the 132 port
# traces of the boards of `validation/` that their rigs ACCEPTED. The
# largest ratio is 93: the 2512 land with no ESL, and its envelope turns at
# 62% of the window. The subsequent ones are 37, 36 and 25, and all the
# other boards are below 15. The trace that MUST give an alarm gives 1.4e5.
# Thus 3000 is 32 times more than the worst run that must be accepted. It
# is 46 times less than the run that must be refused. That is the middle of
# that gap.
GROWTH_LIMIT = 3000.0
# The parts that make the envelope. 200 parts of a trace of 5000 rows have
# 25 samples each. Thus one part includes some periods of the excitation.
# Its largest |u| is a point of the envelope and not a sample of the
# carrier.
GROWTH_SEGMENTS = 200
# A trace this short has only the excitation. Thus its end gives no data
# about the decrease, and the test above does not run.
GROWTH_MIN_ROWS = 200
# The newest version of model.json that this runner can read. It must
# agree with `board_reader.MODEL_VERSION`, and the two files cannot
# import each other: board_reader imports pcbnew, and this file must not.
MODEL_VERSION = 3


def _strip_cells(port_type):
    """Give the number of mesh cells across the strip of a line port."""
    return MSL_STRIP_CELLS if port_type == "msl" else CPW_STRIP_CELLS


def _has_lumped_rlc():
    """Tell if this CSXCAD can do lumped inductors and series RLC.

    LEtype came with the lumped RLC work: openEMS PR #121, which is in
    v0.37 and in the subsequent v0.0.36-N nightly builds. An older CSXCAD
    has no LEtype and refuses the keyword. Thus this test controls the
    guard for the inductors and the kwargs for AddLumpedElement.
    """
    from CSXCAD import CSProperties
    return hasattr(CSProperties.CSPropLumpedElement, "SetLEtype")


def _element_ports(model):
    """Give the lumped elements that this run makes into PORTS.

    `settings["lumped_ports"]` has a list of refdes, or true for all the
    elements of the model. Such an element does NOT go into the grid. The
    run puts a port in the box of the element. After the run, a circuit
    formula puts the value of the part into the S-matrix.

    **A lumped inductor in the grid is what costs the run time.** It must
    have a timestep of `solverenv.time_step_factor` of the Courant step,
    and 90 uH divides that step by 600. A port has no such limit: the run
    keeps the full step for all values of the part. The value can also
    change with no new run, because it goes into the result after the
    solver.

    The cost is ONE more run for each element, because each port must have
    its own excitation.
    """
    want = model["settings"].get("lumped_ports")
    if not want:
        return []
    refs = None if want is True else set(want)
    # **`ref` is optional.** A model.json that a person writes, and the
    # rigs of validation/, give an element with no refdes. Such an element
    # cannot be NAMED in the list. Thus only `true` includes it.
    return [e for e in model.get("lumped_elements", [])
            if refs is None or e.get("ref") in refs]


def _ported_refs(model):
    """Give the refdes of the elements that became ports.

    It has None when such an element has no refdes. The callers test
    `e.get("ref")` against it for the same cause.
    """
    return {e.get("ref") for e in _element_ports(model)}


def _open_parts(model):
    """Give the refdes of the parts that are an OPEN at all frequencies of
    the sweep.

    `solverenv.is_open` holds the rule. The smallest |Z| of the series
    branch in the sweep is `OPEN_Z_RATIO` times the port impedance or more.
    **Such a part does not go into the grid.** Its gap stays open, and that
    is the correct answer: an ideal 90 uH has 0.4% of the admittance of the
    gap at 1 GHz. The element in the grid gave S21 about 5 dB BELOW the
    bare gap. The cause is that the series topology also cuts the
    displacement current of the gap. That incorrect answer used 31 minutes
    at 1/600 of the Courant step.

    The EPC of an inductor stays. At GHz, a choke on a board is much above
    its own self-resonance, and there it IS that capacitance. A C without
    other components uses the classic path, and it costs no timestep.

    `settings["open_as_gap"]` = False puts all parts into the grid as
    before, for a rig that must measure the element itself.
    """
    s = model["settings"]
    # A model with no sweep has no "open at all frequencies of the sweep".
    # A rig or a test that gets the timestep of a bare part gives no
    # f_start.
    if not s.get("open_as_gap", True) or not all(
            s.get(k) for k in ("f_start", "f_stop", "z0")):
        return set()
    para = s.get("parasitics", True)
    out = set()
    for e in model.get("lumped_elements", []):
        comp = _components(e, para, s)
        if solverenv.is_open(comp.get("R"), comp.get("L"), comp.get("C"),
                             s["f_start"], s["f_stop"], s["z0"]):
            out.add(e.get("ref"))
    return out


def _ports_of(model):
    """Give the ports of the run: the ports of the board first, and then
    one for each element that `_element_ports` removes from the grid.

    The element ports come LAST, thus the numbers of the ports of the board
    do not move. A model with no `lumped_ports` gives the same ports that
    it gave before, and the S-matrix keeps its shape.
    """
    ports = list(model["ports"])
    ref_layer = (model["ports"][0]["ref_layer"] if model["ports"]
                 else model["copper_layers"][-1]["name"])
    for i, e in enumerate(_element_ports(model)):
        # **The box and the axis of the element ARE the port.** The element
        # bridges the gap between two pads, thus the port has all that it
        # must have.
        #
        # `x` and `y` have a FACE of that box and not its centre. `_mesh`
        # puts a line on the start, the stop and the centre of each port.
        # The two faces of an element box are mesh lines at this time. A
        # line at the centre can give the box one more cell than the SAME
        # board with the element in the grid. The two runs then do not use
        # the same mesh.
        ports.append({
            "number": len(model["ports"]) + i + 1,
            "label": "%s (lumped element)" % e.get("ref"),
            "type": "element", "ref": e.get("ref"),
            "x": e["start"][0], "y": e["start"][1],
            "layer": e["layer"], "ref_layer": ref_layer,
            "start": list(e["start"]), "stop": list(e["stop"]),
            "exc_dir": e["ny"]})
    return ports


def _time_step_factor(model):
    """Give the timestep factor that keeps the run stable, or give None.

    A lumped inductor makes the FDTD not stable if the timestep is too
    large. On the geometry of validation/run_rlc.py, 1 nH is stable at the
    full Courant step, but 10, 100 and 300 nH diverge to NaN. The largest
    stable factor follows 1/sqrt(L[nH]) across 300 times in L.

    **The MARGIN of that law is not the same on all boards.** On
    2026-08-05, a measurement examined 6 geometries: the mesh preset, the
    box of the element and the thickness of the board.
    `validation/run_stability.py` has the measurement and gives the numbers
    again. The margin goes from 2.7 down to **1.0**. The worst case is a
    THICK board, because the cells at the element increase with the
    substrate. A board of 6.4 mm puts 1 nH ON the boundary. Thus
    `LE_STAB_MARGIN` divides the law, and the worst geometry then keeps a
    margin of about 1.5. `solverenv.time_step_factor` holds the law and the
    margin, and the dialog reads the same function.

    The mesh preset without other changes has no effect, and that is not
    luck. The two faces of the element box are anchors of the mesh with no
    lines between them. Thus the box itself sets the smallest cell of the
    board when the preset becomes coarse. A rule on the FACTOR uses the
    same geometry that controls the stability.

    This rule is an approximation. Thus `_diverged()` examines the port
    data after each run. It tells the user to set
    settings["time_step_factor"], which is more important than this value.

    **This factor does not correct all runs that are not stable.**
    `_diverged()` also finds a run that INCREASED and did not get to NaN,
    and no factor corrects that one: refer to its docstring.
    """
    return _time_step_choice(model)[0]


def _time_step_choice(model):
    """Give (the timestep factor or None, the cause of it in words).

    `_time_step_factor` gives the number and the log gives the words, and
    the two come from HERE. A log that names the incorrect part is worse
    than no log. The number is the one that `_time_step_factor` gave before
    the words were added.
    """
    s = model["settings"]
    if s.get("time_step_factor"):
        f = float(s["time_step_factor"])
        rule, why = _time_step_rule(model)
        return f, ("%.4g, from the Timestep field of the settings, "
                   "which is more important than the rule (the rule "
                   "gives %s)"
                   % (f, "%.4g, %s" % (rule, why) if rule
                      else "the full Courant step"))
    if not s.get("lumped", True):
        return None, "the full Courant step: no lumped element is modelled"
    f, why = _time_step_rule(model)
    return (f, "%.4g, %s" % (f, why)) if f else (
        None, "the full Courant step: %s" % why)


def _time_step_rule(model):
    """Give (the factor of the RULE or None, the words that tell its
    cause).

    Each candidate has the part and the law that give it. An element that
    is a port, or an open at all frequencies of the sweep, is not in the
    grid. Thus it gives no candidate.
    """
    s = model["settings"]
    para = s.get("parasitics", True)
    ported = _ported_refs(model) | _open_parts(model)
    cand = []
    for e in model.get("lumped_elements", []):
        ref = e.get("ref", "?")
        if ref in ported:
            continue
        if e.get("type") == "L" and (e.get("value") or 0) > 0:
            cand.append((solverenv.time_step_factor(e["value"] * 1e9),
                         "the inductance of %s, %g nH" % (ref,
                                                         e["value"] * 1e9)))
        # A series RLC part holds its inductance in `l` and not in `value`.
        # The rule must see it. A large L that the factor does not count
        # diverges, as a part of type L does.
        if e.get("type") == "RLC" and (e.get("l") or 0) > 0:
            cand.append((solverenv.time_step_factor(e["l"] * 1e9),
                         "the L of %s, %g nH" % (ref, e["l"] * 1e9)))
        # The ESL counts only where the element holds it. An inductor has
        # no ESL in its element. A body that does not change its part stays
        # out (`_components`).
        comp = _components(e, para, s)
        if e.get("type") in ("R", "C") and comp.get("L"):
            cand.append((solverenv.time_step_factor(comp["L"] * 1e9),
                         "the body ESL of %s, %g nH" % (ref, comp["L"] * 1e9)))
        # **A large resistance in a SERIES branch also sets the timestep**,
        # and the inductance of that branch has no effect on it.
        # `solverenv.series_r_factor` holds the law and the measurement. A
        # branch with ONE resistance does not use the series topology. Thus
        # it costs nothing, and that is the usual resistor of a board.
        if _le_topology(comp, True) and comp.get("R"):
            cand.append((solverenv.series_r_factor(comp["R"]),
                         "the resistance of %s in a series branch, %g ohm"
                         % (ref, comp["R"])))
    if not cand or min(cand)[0] >= 1.0:
        return None, "no element must have a smaller step"
    f, what = min(cand)
    # The law comes from the constants, thus it follows a new margin.
    law = ("%g/sqrt(L[nH])" % solverenv.LE_STAB_MARGIN if "nH" in what
           else "%g/sqrt(R[ohm])" % solverenv.LE_SERIES_R)
    return f, "set by %s (%s)" % (what, law)


def _decisions(model, res, rlc=True):
    """Give all the decisions that the run makes BY ITSELF, one line each.

    The user gives the values, and the run then makes these decisions:

    - for each lumped element, if it goes into the grid (an open at all
      frequencies of the sweep does not: `_open_parts`);
    - the path of openEMS that it uses (`_le_topology`). An R or a C with
      no other component uses the classic path. An inductance, or more than
      one component, uses the series path;
    - the parasitics that go in with it;
    - the timestep of all the board, and its cause.

    It also changes a port to a lumped port when its line is missing. The
    mesh also makes decisions: a narrow feature of ONE cell, and a via that
    is a thin wire (`_mesh`).

    **Each line names the decision AND its cause**, because nobody can
    check a decision with no cause. `main` prints the list one time and
    writes it to optimizations.log before the engine starts. `rlc` tells if the
    engine has the series topology. A previous engine does not have it, and
    it then removes the parasitics.
    """
    s = model["settings"]
    # The decisions of `board_reader` come first: a part at an angle, and
    # a package code that can be imperial or metric. They are not
    # problems, thus they are not in the warning box before the run.
    out = list(model.get("notes", []))
    z0 = s.get("z0") or 50.0
    band = "%g to %g GHz" % (s["f_start"] / 1e9, s["f_stop"] / 1e9)
    ports = _port_geometry(model, res, quiet=True)
    for g in ports:
        if g.get("fallback"):
            out.append("port %d: a lumped port, and not a %s" % (
                g["number"], g["fallback"]))
    mesh = []
    _mesh(model, ports, res, mesh)
    out += ["mesh: " + line for line in mesh]
    if not s.get("lumped", True):
        out.append("lumped elements: no element is modelled (all Model "
                   "boxes are clear), thus all gaps stay open")
    else:
        para = s.get("parasitics", True) and rlc
        ported = _ported_refs(model)
        opens = _open_parts(model)
        for e in model.get("lumped_elements", []):
            ref = e.get("ref", "?")
            if ref in ported:
                out.append("%s: a port is in its box (lumped_ports), and "
                           "its value goes into the S-matrix after the "
                           "run" % ref)
                continue
            if e.get("type") == "R" and e.get("value") == 0:
                out.append("%s: 0 ohm, thus a box of metal (a short)" % ref)
                continue
            comp = _components(e, para, s)
            if not comp:
                out.append("%s: not modelled, because it has no type or "
                           "no value. Its gap stays open." % ref)
                continue
            # The value of the part first, and then what its body adds.
            own = e.get("type") if e.get("type") in comp else None
            order = ([own] if own else []) + [k for k in "RLC"
                                              if k in comp and k != own]
            parts = " + ".join(
                "%s %s" % (k, _si(comp[k], {"R": "ohm", "L": "H",
                                            "C": "F"}[k])) for k in order)
            body = ""
            if e.get("type") in ("R", "C", "L") and len(comp) > 1:
                body = ", with the parasitics of its %s body" % (
                    e.get("package") or "unknown")
            full = _components(e, para)
            if len(full) > len(comp):
                # **Name what the run left out, and why** (P21).
                left = " + ".join("%s %s" % (
                    {"L": "ESL", "R": "ESR"}[k],
                    _si(full[k], {"R": "ohm", "L": "H"}[k]))
                    for k in "LR" if k in full and k not in comp)
                body = (", without the %s of its %s body, which changes "
                        "|Z| by %.2g%% or less from %s (the limit is "
                        "%g%%)"
                        % (left, e.get("package") or "unknown",
                           100 * solverenv.parasitic_effect(
                               full, e["type"], s["f_start"], s["f_stop"]),
                           band, 100 * solverenv.PARASITIC_MIN))
            z = solverenv.smallest_z(comp.get("R"), comp.get("L"),
                                     comp.get("C"), s["f_start"], s["f_stop"])
            if ref in opens:
                epc = (e.get("epc") or 0.0) if para else 0.0
                out.append(
                    "%s (%s%s): an open circuit from %s, because its smallest "
                    "|Z| is %s, %.0f times z0 (the limit is %g times). "
                    "Thus it is not in the grid, %s, and it costs no "
                    "timestep"
                    % (ref, parts, body, band, _si(z, "ohm"), z / z0,
                       solverenv.OPEN_Z_RATIO,
                       "only its EPC of %s stays, on the classic path"
                       % _si(epc, "F") if epc > 0
                       else "its gap stays open"))
                continue
            if _le_topology(comp, rlc):
                # Tell WHICH component causes the series path. A resistor
                # on it is there for the ESL of its body.
                why = ("it has an inductance" if e.get("type") in (
                    "L", "RLC") and "L" in comp else
                       "the ESL of its body is an inductance" if "L" in comp
                       else "it has %d components" % len(comp))
                path = "the series path (LEtype 1), because %s" % why
            else:
                path = ("the classic path (LEtype 0), because it is one R "
                        "or one C")
            out.append("%s (%s%s): in the grid, on %s. Its smallest |Z| "
                       "from %s is %s (%.2g times z0), thus it is not an "
                       "open"
                       % (ref, parts, body, path, band, _si(z, "ohm"),
                          z / z0))
            # The position of its EPC comes from the MESH. Thus `main`
            # tells it after the first build: `_epc_decisions`.
    f, why = _time_step_choice(model)
    out.append("timestep: " + why)
    if f and f < 1.0:
        out.append("step limit: %d, which is the %d of Max steps divided "
                   "by the factor, for the same simulated time. A run "
                   "usually stops before it, when its field becomes "
                   "stable."
                   % (_max_timesteps(model), s["max_timesteps"]))
    return out


def _epc_decisions(model, grid, rlc=True):
    """Give the result for the EPC of each inductor in the grid, in words.

    `_epc_split` finds it from the lines of the mesh. The EPC is adjacent
    to the part, on the other half of its land. If not, a land of ONE cell
    has no space, and the part keeps no self-resonance. It uses the grid.
    Thus `main` gets it after the first build and not before it.
    """
    s = model["settings"]
    para = s.get("parasitics", True) and rlc
    skip = _ported_refs(model) | _open_parts(model)
    out = []
    for e in model.get("lumped_elements", []):
        epc = (e.get("epc") or 0.0) if para else 0.0
        if epc <= 0 or e.get("type") != "L" or e.get("ref") in skip:
            continue
        split = _epc_split(grid, e)
        out.append("%s: its EPC of %s %s" % (
            e.get("ref", "?"), _si(epc, "F"),
            "is adjacent to it, on the other half of its land (the mesh "
            "line at %.4f mm divides the land)" % split if split is not None
            else "is removed, because its land has only one cell across "
                 "the current, and two elements cannot share a box. Thus "
                 "the part has no self-resonance. A finer mesh preset "
                 "gives the land more cells."))
    return out


def _si(v, unit):
    """Give a value with an SI prefix, for the log."""
    for div, p in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
                   (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"),
                   (1e-15, "f")):
        if abs(v) >= div:
            return "%.3g %s%s" % (v / div, p, unit)
    return "%.3g %s" % (v, unit)


def _max_timesteps(model):
    """Give the step limit that the engine receives.

    A smaller timestep must have more steps for the same simulated time.
    Thus `settings["max_timesteps"]` is a limit on the TIME and not on the
    count. `build()` gives this value to openEMS. `main()` uses the same
    number to tell if a run met its end criteria or stopped at the limit.
    """
    nrts = model["settings"]["max_timesteps"]
    tsf = _time_step_factor(model)
    return int(nrts / tsf) if tsf and tsf < 1.0 else nrts


def _cell_count(fdtd):
    """Give the number of mesh cells of a model that build() made."""
    grid = fdtd.GetCSX().GetGrid()
    n = 1
    for axis in "xyz":
        n *= grid.GetQtyLines(axis)
    return n


def _threads(s, cells):
    """Give the number of threads for the engine.

    settings["threads"] is more important. A value of None (the "Auto" item
    of the dialog) gives the value that this function calculates.

    openEMS keeps the "fastest" engine if the caller names no engine. That
    engine does not use the largest part of the machine. The multithreaded
    engine gives one slice of the domain to each thread. It makes the
    threads wait for each other at each timestep. Thus the slowest thread
    controls the speed of all the others. A thread that gets a small slice
    costs more than it gives.

    Thus the count must follow the dimensions of the model. These are
    measurements on an i7-12700K (8 performance cores, 4 efficiency cores),
    in MCells/s:

        cells      2 thr   4 thr   8 thr   12 thr
        62 k        27.6    35.4    28.4    20.8
        147 k       34.7    52.5    59.4    50.6
        504 k       44.2    74.3   105.9   104.9
        1.62 M      56.1    94.6   135.3   145.1

    Thus a small model is fastest at 4 threads, and 8 threads make it 20%
    slower. Above about 150 k cells, 8 threads is the best value that a
    usual desktop gives. More than 8 gives a small increase only. The
    efficiency cores get a slice with the same dimension as a performance
    core. Each performance core then waits for them at each timestep.
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

    - A capacitor becomes ESR + ESL + C. This is the usual model of a
      capacitor, and it puts the self-resonance at the correct frequency.
    - A resistor becomes R + ESL.
    - An inductor becomes DCR + L. A series element cannot make the
      parallel capacitance of an inductor. Thus this model does not give
      the self-resonance of an inductor.

    The values are for the body of the part only. The mesh contains the
    loop of the pads and the tracks.
    """
    out = {}
    esl, esr = e.get("esl") or 0.0, e.get("esr") or 0.0
    if e["type"] in ("R", "C") and esl > 0:
        out["L"] = esl
    if e["type"] in ("C", "L") and esr > 0:
        out["R"] = esr
    return out


def _components(e, para, s=None):
    """Give the R, L and C that `build` puts in ONE element, in SI.

    `para` tells if the package parasitics are on. The result is the
    keyword arguments of `AddLumpedElement`. `_le_topology` and
    `_time_step_factor` read the same result. Thus the rule of the timestep
    and the element of that rule always agree.

    `s` is the settings of the run. With a sweep in it, **a body that does
    not change its part stays out** (`solverenv.body_is_idle`). The part is
    then one R or one C, and it uses the classic path of openEMS. It does
    not use the series path, which costs more than the body models.
    `settings["keep_idle_body"]` = True keeps all bodies, for a rig that
    must measure the series path itself. With no `s`, the body always
    stays. `_decisions` compares against that result.

    An element that `build` does not model gives {}.
    """
    if e.get("type") == "RLC":
        return {k: float(e[k.lower()]) for k in "RLC" if e.get(k.lower())}
    if not e.get("type") or e.get("value") is None:
        return {}
    # A 0 ohm part is a box of metal (a short). `build` models no body for
    # it, thus its body sets no timestep.
    if e["type"] == "R" and e["value"] == 0:
        return {"R": 0.0}
    comp = {e["type"]: e["value"]}
    if para:
        comp.update(_parasitic_components(e))
    if (s and not s.get("keep_idle_body") and s.get("f_start")
            and s.get("f_stop") and solverenv.body_is_idle(
                comp, e["type"], s["f_start"], s["f_stop"])):
        return {e["type"]: e["value"]}
    return comp


def _le_topology(comp, rlc):
    """Give the LEtype of an element that holds `comp`.

    `LEtype` = 1 is the SERIES topology, which is an extension of openEMS.
    A branch must have it when it has more than one component. An inductor
    must always have it. The classic lumped element of openEMS knows only R
    and C. It removes an L with the message "Lumped Element R or C not
    specified! skipping", and that models an open circuit.

    **A branch with ONE R, or ONE C, must NOT use it.** The series
    extension integrates the current of the branch. A branch with no
    inductance gives that integrator no opposition, and it then diverges. A
    larger resistance diverges sooner. A measurement on the board of
    `run_rlc.py` at the coarse preset, on 2026-09-22, at the full Courant
    step, gave this result. 10, 50, 75 and 100 ohm are stable, and 150,
    200, 500 and 1000 ohm all get to NaN. The 1000 ohm run used 9.5 minutes
    and all of the step limit of 300000 steps to get to NaN. `_diverged`
    then named a lumped inductor, but that board has no inductor. A
    resistor of 150 ohm is a usual part: a series termination, or a
    pull-up.

    The classic path has no such limit, and `AddLumpedPort` uses it for its
    OWN resistor. The two paths do not give the same number.
    `validation/run_cosim.py` compared them with a 50 ohm element. The
    voltage probes moved by 0.9%, and the S-parameters by 0.22 dB. The
    classic path is a small distance NEARER to the closed formula of
    `run_rlc.py`: -3.81 dB against -3.84 dB, for -3.52 dB in theory.
    """
    return {"LEtype": 1} if rlc and (len(comp) > 1 or "L" in comp) else {}


def _run_length(sim_path):
    """Give (the timesteps of the run, the timestep, the subsample), or
    give None.

    openEMS writes its report to the console. It makes no file that this
    code can read after `Run`. Two files together give the answer:

    - `et` has the excitation, ONE row for each timestep. Thus the
      difference of its first two times is the timestep.
    - the port data is SUBSAMPLED, but its last row has the time of the
      last step that the engine wrote.

    Thus the count is accurate to the subsample interval. The caller also
    receives that interval.
    """
    et = os.path.join(sim_path, "et")
    uts = sorted(glob.glob(os.path.join(sim_path, "port_ut_*")))
    if not os.path.isfile(et) or not uts:
        return None
    try:
        e = np.loadtxt(et, comments=("%", "#"))
        u = np.loadtxt(uts[0], comments=("%", "#"))
    except (ValueError, OSError):
        return None                  # a NaN run: `_diverged` reports it
    if e.ndim != 2 or len(e) < 2 or u.ndim != 2 or len(u) < 2:
        return None
    dt = e[1, 0] - e[0, 0]
    if not dt > 0:
        return None
    steps = int(round(u[-1, 0] / dt))
    return steps, dt, max(1, int(round(steps / (len(u) - 1))))


def _report_end(sim_path, nrts, end_criteria):
    """Tell how the run ended: the end criteria, or the step limit.

    A run that stops at the limit is not complete. Its S-matrix then has
    the energy that stayed in the structure. That energy reads as a
    resonance that is not sufficiently deep, or as a ripple. openEMS gives
    no message that the user sees in the log of the plugin. A structure
    with a high Q is the usual cause.
    """
    end = _run_length(sim_path)
    if not end:
        return None
    steps, dt, every = end
    ns = steps * dt * 1e9
    if steps + every >= nrts:
        print("[rfsim] WARNING: the run stopped at the step limit of %d "
              "timesteps (%.1f ns), before its end criteria of %.3g. "
              "Thus the result is not complete. Increase \"Max steps\", "
              "or make \"End criteria\" larger."
              % (nrts, ns, end_criteria), flush=True)
        return ("stopped at the step limit of %d timesteps (%.1f ns), "
                "before its end criteria" % (nrts, ns))
    print("[rfsim] the run got to its end criteria of %.3g after %d of "
          "%d timesteps (%.1f ns)" % (end_criteria, steps, nrts, ns),
          flush=True)
    return ("got to its end criteria of %.3g after %d of %d timesteps "
            "(%.1f ns)"
            % (end_criteria, steps, nrts, ns))


def _diverged(sim_path):
    """Give (the name of a port file, the cause), or give None.

    The cause is "nan" or "growth", and the two have different advice.

    **NaN** comes from a timestep that is too long for a lumped inductor,
    and a shorter one corrects it. openEMS writes '-nan(ind)' into the
    time-domain data of the port. CalcPort then stops with an unclear
    "could not convert string to float" ValueError. Thus this test comes
    first, and it reads the file as text.

    **Growth** is a run that is not stable and that does not get to NaN.
    The field of a passive structure decreases after the excitation. It
    does not increase again. Thus a trace that ends much above the TURN of
    its own envelope did not decrease. Such a run writes a correct
    Touchstone file. Its S-matrix is flat near 1 at all frequencies of the
    sweep. Thus no other part of this code refuses it. A shorter timestep
    does NOT correct it, because the rate of that growth does not follow
    the timestep.
    """
    names = sorted(glob.glob(os.path.join(sim_path, "port_ut_*")))
    for fn in names:
        with open(fn) as fh:
            if "nan" in fh.read().lower():
                return os.path.basename(fn), "nan"
    for fn in names:
        # openEMS uses '%' as the comment mark, and it SUBSAMPLES the port
        # data. A run of 300000 timesteps writes about 7900 rows. Column
        # 0 is the time and column 1 is the voltage.
        try:
            a = np.loadtxt(fn, comments=("%", "#"))
        except (ValueError, OSError):
            continue  # not a table: CalcPort reads it
        if a.ndim != 2 or len(a) < GROWTH_MIN_ROWS:
            continue
        u = np.abs(a[:, 1])
        # The envelope: the largest |u| of each equal part of the trace.
        # The TURN is its lowest point between the excitation and the end.
        # The end is the largest |u| of the last tenth.
        #
        # **The code examines the envelope from the peak of the FIRST
        # HALF**, which is the excitation. A trace starts at zero and
        # increases with the pulse. Thus the minimum of all the envelope is
        # that increase, and all boards then give an alarm. **It stops
        # before the last tenth**, thus the end does not compare with
        # itself. A trace that decreases to the last row has its lowest
        # point there. It then gives a ratio of 1, for all the trace before
        # it.
        #
        # The peak of ALL the envelope cannot be the anchor. A trace that
        # increases ABOVE its own excitation has its largest point at the
        # end. The code then examines only that one point.
        nseg = min(GROWTH_SEGMENTS, len(u) // 10)
        env = np.array([c.max() for c in np.array_split(u, nseg) if len(c)])
        env = env[env > 0]
        if len(env) < 3:
            continue
        start = int(np.argmax(env[:max(1, len(env) // 2)]))
        stop = max(start + 1, len(env) - max(1, len(env) // 10))
        turn = env[start:stop].min()
        tail = u[-(len(u) // 10):].max()
        if tail > GROWTH_LIMIT * turn:
            return os.path.basename(fn), "growth"
    return None


def _merge_close(vals, tol, anchors=()):
    """Sort the coordinates and merge those that are nearer than tol.

    This prevents very thin mesh cells.

    `anchors` has (the value, the rank) of each line that must NOT move.
    The merged line gets the value of the anchor, and not the mean of the
    two. A lumped element is a box between two mesh lines, with no line of
    its own in it. Thus a face that the mean moves can give a box with no
    cell.

    A measurement shows the failure, and it is not an open circuit. The
    copper of the two pads touches on that one line. Thus the gap CLOSES,
    and the run gives a piece of line. A series 50 ohm in a 50 ohm line
    gave S21 -0.16 dB and not -4.5 dB, and S11 -17.4 dB and not -10.3 dB.
    openEMS gives NO warning for it. A via that does not keep a line has
    the same failure with no message, one step smaller. The node goes out
    of the barrel, and the via reads as a thinner one.

    **Two anchors nearer than `tol` cannot stay together**, because this
    function must prevent the cell between them. The HIGHER rank wins, and
    two anchors of one rank keep the first. Refer to RANK_FACE for the
    sequence and for its cause.
    """
    rank = {}
    for value, k in anchors:
        key = round(value, 9)
        rank[key] = max(k, rank.get(key, k))
    vals = sorted(vals)
    out = [vals[0]]
    held = [rank.get(round(vals[0], 9), -1)]
    for v in vals[1:]:
        k = rank.get(round(v, 9), -1)
        if v - out[-1] < tol:
            if k > held[-1]:
                out[-1] = v  # the anchor replaces the mean
                held[-1] = k
            elif held[-1] < 0:  # the two lines are not anchors
                out[-1] = 0.5 * (out[-1] + v)
        else:
            out.append(v)
            held.append(k)
    return out


def _feature_lines(edges, lines, res, tol, cells=POLY_FEATURE_CELLS,
                   one_cell=()):
    """Give the lines that divide each narrow feature of the copper.

    `edges` has the coordinates of the straight edges of the copper on one
    axis. `lines` has the mesh lines that the other rules made. Two
    adjacent edges hold a FEATURE: a track, a gap between two pads, or a
    slot. `SmoothMeshLines` fills the interval between two lines that do
    not move. Thus a feature that is not wider than `res` keeps its two
    edge lines and NOTHING between them. That is one cell across a track.
    Refer to POLY_FEATURE_CELLS for the cause that makes that number too
    small.

    **A feature that has a line keeps only that line.** Some lines are in a
    feature: the cells across the strip of a port, and the lines of a CPW
    gap. The two faces of a lumped element are also in a feature. A second
    line adjacent to them makes the mesh smaller for no result. This also
    keeps each board of `validation/` at the mesh that measured its number.

    **`one_cell` has the intervals that must keep ONE cell**: the gap of a
    lumped element, along the current of that element. The gap between the
    two pads of a part is a narrow feature. Thus this rule put a line at
    the middle of it, and the element was on 2 cells in series. 2 is the
    ONE bad count for the stability of a lumped inductor. The reference
    board at 10 nH turns at 2.09 ns with 2 cells, and the runner refuses
    the run. It accepts 1, 3, 4 and 6 cells, and it accepts 1 cell at er
    2.2, 4.5 and 10.2. The count in the box does not change the value that
    the engine models: 0.3105 nH against 0.3104 nH on the shunt board. Thus
    the removal of that cell costs nothing.

    When the cells of a feature are smaller than `tol`, the feature gets NO
    line. `_merge_close` removes such a line again. A cell that small also
    decreases the timestep of the full run. The usual cause is the two ends
    of a sliver that a boolean union makes. Such a feature keeps its two
    edge lines. Thus current continues to flow in a track, and a gap stays
    open. Only the width is then one cell. The result is (the lines, the
    width of each feature that keeps all its cells).

    **The mesh does NOT grade outward from a feature.** The cells that this
    rule makes are much smaller than `res`. Thus the adjacent cell is a
    step. For that cause, the port branches of `_mesh` grade away from a
    strip and from a CPW gap. A measurement on 2026-09-01 examined a grade
    here, and it is not worth its cost. With the 2 cells that
    POLY_FEATURE_CELLS gives, it moves the eps_eff of a 0.4 mm line by
    0.04%. A grade at EACH narrow feature moves the mesh of 44 of the 71
    boards of `validation/`, and it adds up to 76% more cells. A grade only
    where `tol` refuses the division moves 26 boards, and it adds up to
    81%. It gives about 5% ONLY at one cell across the feature, which is
    the result of `tol`. `validation/run_feature.py` has the measurement.
    """
    out, narrow = set(), []
    edges = sorted(edges)
    lines = sorted(lines)
    for a, b in zip(edges, edges[1:]):
        if b - a > res:
            continue                     # SmoothMeshLines divides it
        if any(lo - FLAT_MM <= a and b <= hi + FLAT_MM
               for lo, hi in one_cell):
            continue                     # the box of an element: 1 cell
        i = bisect.bisect_right(lines, a)
        if i < len(lines) and lines[i] < b:
            continue  # it has a line
        if (b - a) / cells <= tol:
            narrow.append(b - a)
            continue
        for k in range(1, cells):
            out.add(a + (b - a) * k / cells)
    return out, narrow


def _port_geometry(model, res, quiet=False):
    """Calculate the boxes and the planes of the ports.

    The result contains floats only. The mesh uses these values, thus this
    function runs first.

    A microstrip port goes from the strip plane down to the plane of the
    reference layer. A CPW port and a stripline port are FLAT: openEMS
    refuses a start and a stop that are different in the direction of
    exc_dir. Their return path is the coplanar copper, or the two planes.
    """
    z_of = {c["name"]: c["z"] for c in model["copper_layers"]}
    ports = []
    plist = _ports_of(model)
    # **The rules below count only the ports of the BOARD.** An element
    # port is in the box of a lumped element, and it is not one end of a
    # through line. Thus a board with 2 ports keeps 2 ports for all counts
    # of elements that the run removes from the grid. Without this, the
    # pair rule stops at the first element port. Each de-embedded port then
    # has no cap on its length, and the mesh moves. If you then compare the
    # two procedures, you measure the mesh and not the port.
    board = [p for p in plist if p["type"] != "element"]
    for i_p, p in enumerate(plist):
        z_top = z_of[p["layer"]]
        z_ref = z_of[p["ref_layer"]]
        g = dict(p, z_top=z_top, z_ref=z_ref)
        if p["type"] == "element":
            # The element gives the box and the axis. Thus no rule below
            # applies: no track gives the direction of this port, and no
            # copper run caps its length.
            ports.append(g)
            continue
        # Each de-embedded port must have a track that gives the direction.
        # A CPW port also must have the gap. A stripline port must have a
        # plane above the strip and a plane below it.
        need = {"cpw": "gap", "stripline": "height"}.get(p["type"])
        if (p["type"] in TL_PORTS and p["direction"]
                and (need is None or p.get(need))):
            d = p["direction"]
            # width and length are on the axes of the board. The dimension
            # across the feed is width for a feed on x, and length for a
            # feed on y.
            w = p.get("track_width") or (p["width"] if d[0] else p["length"])
            length = max(3.0 * w, 6.0 * res)
            # **Cap the length with the copper that goes along the feed.**
            # At the coarse preset, `6*res` is 14 mm or more. A short feed
            # line (the inset feed of a patch, for example) is shorter than
            # that. The measurement plane is at the middle of the port.
            # Thus it can be IN the patch, where the values of a line are
            # not correct. Also, each de-embedded port adds a metal strip
            # above its box. That strip can go out across the end of the
            # copper. The model then has a line that the board does not
            # have.
            #
            # The cap keeps a small part of the run free, because the port
            # must not touch the end of the copper. `copper_run` is None
            # when the copper goes farther than its limit. Then nothing
            # caps the length here.
            run = p.get("copper_run")
            if run and run > 0:
                length = min(length, 0.8 * run)
            if len(board) == 2:
                # The OTHER port, by list position. Do not use p["number"]
                # as the index. A model.json that a person changed can have
                # numbers that are not 1..N in list sequence. That gives
                # the incorrect port with no message. The element ports
                # come last, thus a board port keeps its index here.
                q = board[1 - i_p]
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
                why = ("it has no track" if not p["direction"] else
                       "it has no coplanar gap" if p["type"] == "cpw" else
                       "it has no plane above and below the strip")
                # The cause stays with the port. Thus `_decisions` can tell
                # it without a second copy of this test.
                g["fallback"] = "%s: %s" % (p["type"], why)
                if not quiet:
                    print("[rfsim] WARNING: port %d (%s): %s, thus it "
                          "changes to a lumped port"
                          % (p["number"], p["type"], why), flush=True)
            g["type"] = "lumped"
            g["start"] = [p["x"] - p["length"] / 2, p["y"] - p["width"] / 2, z_ref]
            g["stop"] = [p["x"] + p["length"] / 2, p["y"] + p["width"] / 2, z_top]
        ports.append(g)
    return ports


def _pml_band(lo, hi, depth):
    """Give the lines of the outer PML band of one axis, which do not move.

    `lo` and `hi` are the two ends of the domain. `depth` is the distance
    that the band goes in from each of them. The band keeps
    `solverenv.PML_CELLS` equidistant cells, thus its cell is `depth`/8.
    The absorber costs 8 cells for all depths.
    """
    n = solverenv.PML_CELLS
    step = float(depth) / n
    return ([lo + i * step for i in range(n + 1)]
            + [hi - i * step for i in range(n + 1)])


def _mesh(model, ports, res, notes=None):
    """Give the lists of mesh lines (x, y, z) from the geometry and `res`.

    **The mesh also makes decisions by itself**: a narrow feature that
    keeps ONE cell, and a via with a radius smaller than `tol`. With a list
    in `notes`, each decision goes into it as one line of words, and no
    text prints. With no list, each prints as a WARNING. A rig and the
    first build of a run use that result.

    The domain is the region from the extraction. The outer `pml` of each
    of the 6 faces has 8 cells and becomes the PML_8 absorber. A band of
    `margin` mm of clear air stays between the structure and it. `extract`
    made space for the two.

    **The depth comes from the model**, because the region must hold it. A
    model from before 2026-09-20 has no "pml_mm", and its region holds one
    margin of absorber. Thus the fallback gives such a model the mesh that
    it was made for.
    """
    s = model["settings"]
    margin = s["margin_mm"]
    pml = model.get("pml_mm") or margin
    r = model["region"]
    xs = set(_pml_band(r["x0"], r["x1"], pml))
    ys = set(_pml_band(r["y0"], r["y1"], pml))
    # A mesh line ON each straight edge of the copper.
    #
    # Before, this loop gave only the BOX of each polygon. Thus no edge IN
    # a polygon had a line. A structure that is smaller than `res` then
    # fell BETWEEN the lines. An example is a 2.45 GHz Wilkinson divider
    # with tracks of 0.265 mm, at the fine preset with a step of 0.589 mm.
    # It became 63 pieces of copper that touch nothing, and S21 read
    # -94.4 dB. **openEMS gives NO message for it.** It writes "Unused
    # primitive" only when ALL the primitive gets no edge. One large
    # polygon that does not keep its middle stays "used".
    #
    # A DIAGONAL edge and the segments of an arc give NO line. Such an edge
    # has no single coordinate. One line for each vertex of a circular pad
    # or of a curved zone multiplies the mesh for no result. The box of the
    # polygon stays for the same cause. A shape with only diagonal edges,
    # such as a diamond, keeps its two ends. The cost is small on a board,
    # because the copper of a PCB is rectilinear. The zone board of
    # `validation/` has 379 points and gives 10 x lines and 7 y lines.
    # **Each layer also keeps its own edges.** The union below makes the
    # mesh lines, and one line is for all layers. But a FEATURE is the
    # space between two edges of the SAME copper. An F.Cu edge and a B.Cu
    # edge that are near each other are not a narrow track, but the union
    # reads them as one. Thus `_feature_lines` uses one layer at a time.
    poly_x, poly_y = set(), set()
    layer_x, layer_y = {}, {}
    for name, polys in model["polygons"].items():
        lx = layer_x.setdefault(name, set())
        ly = layer_y.setdefault(name, set())
        for poly in polys:
            px = [pt[0] for pt in poly]
            py = [pt[1] for pt in poly]
            lx.update((min(px), max(px)))
            ly.update((min(py), max(py)))
            for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
                flat_x = abs(x1 - x0) < FLAT_MM
                flat_y = abs(y1 - y0) < FLAT_MM
                if flat_x and not flat_y:
                    lx.add(x0)
                elif flat_y and not flat_x:
                    ly.add(y0)
        poly_x |= lx
        poly_y |= ly
    xs |= poly_x
    ys |= poly_y
    via_x, via_y = [], []
    for v in model["vias"]:
        # The CENTER line is necessary, and not only the two edges. openEMS
        # makes a metal primitive into PEC on the edges of the Yee grid.
        # Thus a mesh NODE must be in the barrel. With no centre line, the
        # nodes are on the surface of the cylinder, and the barrel has no
        # node in it. openEMS then writes "Unused primitive (type:
        # Cylinder)", and no current flows in the via. The planes then have
        # no connection, and the model is incorrect with no error.
        #
        # **The two surface lines go 1 ppm IN the barrel** (`VIA_SURFACE`).
        # A node on the surface counts only when the doubles put it in the
        # barrel.
        #
        # **All three are anchors of the merge below.** A copper edge that
        # is nearer than `tol` to one of them moves to the via. The via
        # does not move to the mean of the two.
        r = v["r"] * VIA_SURFACE
        xs.update((v["x"] - r, v["x"], v["x"] + r))
        ys.update((v["y"] - r, v["y"], v["y"] + r))
        via_x += [(v["x"] - r, RANK_VIA_SURFACE), (v["x"], RANK_VIA_CENTRE),
                  (v["x"] + r, RANK_VIA_SURFACE)]
        via_y += [(v["y"] - r, RANK_VIA_SURFACE), (v["y"], RANK_VIA_CENTRE),
                  (v["y"] + r, RANK_VIA_SURFACE)]
    for g in ports:
        xs.update((g["start"][0], g["stop"][0], g["x"]))
        ys.update((g["start"][1], g["stop"][1], g["y"]))
        if g["type"] == "cpw":
            # The voltage probes of a CPW port go across the two gaps, and
            # the current probe goes around the strip. The E field has a
            # peak at each edge of the strip. Thus the mesh step across the
            # line must come from the gap, and not from the wavelength. A
            # gap of 0.3 mm with a step of 2.4 mm gives an impedance that
            # is about 30% too small.
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
                # cannot do this. It fills each interval between two lines
                # that do not move, and it does not look at the adjacent
                # intervals. Thus a cell of 0.08 mm can touch a cell of
                # 1 mm. Such a step causes a reflection of the wave, and it
                # makes the impedance incorrect. More lines in the full
                # domain do not correct it.
                pos = c + side * (hw + g["gap"])
                step = g["gap"] / CPW_GAP_CELLS
                while step < res:
                    pos += side * step
                    across.add(pos)
                    step *= 1.4
        elif g["type"] in ("stripline", "msl"):
            # A stripline and a microstrip must have the same procedure as
            # the strip of a CPW port. Before, only the CPW branch was in
            # the code. Thus the mesh step across the strip came from the
            # WAVELENGTH. The stripline of 0.6 mm of validation/ is
            # narrower than one cell of 2.355 mm at the coarse preset. That
            # board measured 19.4 ohm against 38.9 ohm from IPC-2141. With
            # these cells, it gives 39.2 ohm at the SAME preset. The mesh
            # increases only from 57x39x48 lines to 57x57x48. The medium
            # mesh gave 34.6 ohm without them, and that result showed that
            # the mesh was the cause.
            #
            # A microstrip is the same geometry with the return path below
            # it, and it got the rule on 2026-08-05. Its track of 2.9 mm is
            # WIDER than one coarse cell. Thus its error was smaller, and
            # it looked like the usual mesh error that converges. It gave
            # 44.3 ohm at coarse and 47.2 at medium, against 49.8 from
            # Hammerstad and Jensen. That was not the cause. With
            # MSL_STRIP_CELLS cells, the same board gives 47.7 at coarse
            # and 47.8 at medium. The 2.9 ohm between the two presets goes
            # away, and the rule must do that.
            across = ys if g["prop_dir"] == "x" else xs
            c = g["y"] if g["prop_dir"] == "x" else g["x"]
            hw = 0.5 * g["msl_width"]
            half = max(1, _strip_cells(g["type"]) // 2)
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
    zs = set(_pml_band(-(margin + pml), board_top + margin + pml, pml))
    for c in model["copper_layers"]:
        zs.add(c["z"])
    for d in model["dielectric_layers"]:
        # 4 cells or more in each dielectric layer
        zs.update(np.linspace(d["z_bottom"], d["z_top"], 5).tolist())
    # A CPW port and a stripline port put their current probe 2 mesh cells
    # above the strip and 2 below it. The air above the board has no
    # dielectric rule. Thus its cells are as large as the full mesh step,
    # and the probe box then becomes very large and not symmetric. It then
    # measures a current that is much too large, and the impedance of the
    # line becomes much too small. Thus put some lines at each side of each
    # copper plane. The step is the step of the dielectric rule. Thus this
    # operation makes no cell smaller, and it costs no timestep.
    if model["dielectric_layers"]:
        step = 0.25 * min(d["z_top"] - d["z_bottom"]
                          for d in model["dielectric_layers"])
        for c in model["copper_layers"]:
            for k in (1, 2):
                zs.update((c["z"] + k * step, c["z"] - k * step))
    for g in ports:
        if g["type"] != "cpw" or not g["gap"]:
            # A MICROSTRIP port does NOT use this rule, and a measurement
            # on 2026-08-05 shows the cause. On the validation board (a
            # track of 2.9 mm on a substrate of 1.53 mm), the same chain of
            # z lines moves Z0 by 0.05 ohm, which is 0.1%. It costs 8.6%
            # more cells. The plane below the strip holds the field, thus
            # the dielectric rule of 4 cells is sufficient. A NARROW line
            # gets its cells ACROSS the strip from `_feature_lines`. The
            # run gives a warning when `tol` stops that rule.
            continue
        # A CPW port also must have its own cells ABOVE and BELOW the plane
        # of the line, and their step must come from the GAP. The line of a
        # CPW has no plane below it that holds the field. The field goes
        # from the strip across the two gaps. Thus it is at its largest in
        # about one gap width from the surface. The step of the rule above
        # comes from the thickness of the dielectric (0.38 mm on a board of
        # 1.6 mm). That is much larger than a usual gap of 0.3 mm. The
        # capacitance of the line is then about 27% too large, and the
        # impedance about 21% too small. The value does NOT converge with
        # the mesh preset, thus the fault does not look like a mesh fault.
        # The step is the step of the gap cells. Thus it makes no cell
        # smaller than the y mesh of the gap.
        st = g["gap"] / CPW_GAP_CELLS
        for side in (-1, 1):
            # Stop at the subsequent copper plane on that side. A line of
            # this chain that is NEAR the plane is worse than no line.
            # _merge_close then merges the two and MOVES the line of the
            # plane. A copper sheet with no line on it is not metal.
            # openEMS then writes "Unused primitive (type: LinPoly)", and
            # no current flows in the plane. The vias of 2026-08-03 (10)
            # had the same failure.
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
    # The cells across the strip of a stripline port or of a microstrip
    # port are much smaller than the mesh step. Thus the merge can remove
    # them again, as it can remove the lines of a CPW gap.
    strips = [g["msl_width"] / _strip_cells(g["type"]) for g in ports
              if g["type"] in ("stripline", "msl") and g.get("msl_width")]
    if strips:
        tol = min(tol, 0.25 * min(strips))
    gaps = [g["gap"] for g in ports if g["type"] == "cpw" and g["gap"]]
    if gaps:
        # A CPW gap is usually much smaller than the mesh step. Thus the
        # merge can remove the lines in the gap. The voltage probes of the
        # port then measure across the incorrect cells.
        tol = min(tol, 0.25 * min(gaps) / CPW_GAP_CELLS)
    # The cells above and below the plane of a CPW have the same step as
    # the cells in the gap. Thus the z merge must use the same tolerance,
    # or it removes them again.
    tol_z = min(tol, 0.05)
    if gaps:
        tol_z = min(tol_z, 0.25 * min(gaps) / CPW_GAP_CELLS)
    # The box of a lumped element has 2 lines only: its faces. Thus the
    # merge must keep them apart, as it keeps the lines of a CPW gap apart.
    # When the box of an element is smaller than tol, the element keeps ONE
    # line. The copper of its two pads then touches on that line. The part
    # becomes a piece of track with no message.
    #
    # A measurement at the coarse preset with a margin of 4 mm (tol is
    # 0.200 mm) shows this. A series 50 ohm in a gap of 0.15 mm gave S21
    # -0.16 dB. With the limit, it gave -4.50 dB, and the theory gives
    # -3.5 dB. A part with a gap of 0.5 mm has no change (-4.56 dB with and
    # without it). The anchors below then hold the two faces on their own
    # coordinates. A face that the mean moves can also give a box with no
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
    # The number of CELLS in the box of an element does not change the
    # value that the engine models. A measurement on 2026-08-05 shows this
    # in the two directions.
    #
    # Along the current: a rule that removed the lines in the box changed
    # the shunt board from 2 cells back to 1. The body ESL from the notch
    # moved from 0.3105 nH to 0.3104 nH.
    #
    # Across the current: the cells across the strip of a microstrip port
    # changed the box of the series board from 2 cells to 8. `run_rlc.py`
    # gives R, L and C back against the closed formula as before.
    #
    # Thus openEMS scales R, L and C correctly across the box, and this
    # code has no rule for the cell count. Only the two FACES are
    # important, and the anchors above hold them.

    # The lines IN the narrow copper come last, for two causes. The rule
    # does not divide a feature that a port, a via or a lumped element
    # divides. Thus it must see all the other lines first. It also uses
    # `tol`, because a line that the merge removes again is worse than no
    # line. One layer at a time. `xs` / `ys` stay the POOLED lines: a
    # feature that a rule divides keeps that line, from all layers. The gap
    # of each element, along the current of that element and ON ITS OWN
    # LAYER. The rule below must not divide it: refer to `_feature_lines`.
    # The layer is a part of the key. The copper of a different layer can
    # have a feature in the same interval, and that feature keeps its line.
    le_gap = {}
    for e in model.get("lumped_elements", []):
        k = 0 if e["ny"] == "x" else 1
        le_gap.setdefault((e["layer"], k), []).append(
            sorted((e["start"][k], e["stop"][k])))
    narrow = []
    for name in sorted(layer_x):
        fx, narrow_x = _feature_lines(layer_x[name], xs, res, tol,
                                      one_cell=le_gap.get((name, 0), ()))
        fy, narrow_y = _feature_lines(layer_y[name], ys, res, tol,
                                      one_cell=le_gap.get((name, 1), ()))
        xs |= fx
        ys |= fy
        narrow += narrow_x + narrow_y
    chose = []
    if narrow:
        # **Give the dimension of the error, and not only the error.** "Use
        # a finer mesh preset" does not tell a user if the answer is 1% or
        # 10% incorrect. Thus it cannot help the user.
        #
        # The numbers come from a 0.30 mm line at the coarse preset, where
        # the step gives it only 1 cell. Against Hammerstad-Jensen with the
        # dispersion of Kirschning-Jansen, it reads +8.7% in eps_eff. It
        # reads 114.97 ohm against 128.15, thus -10.3% in Z0. The same
        # closed formula gives that eps_eff for a line of 1.7 mm. A 0.40 mm
        # feature at 1 cell gives 1.736 mm, which is almost the same number
        # for two different widths. Thus "the line is as wide as its cell".
        # For that cause, the count and not the width sets the error.
        chose.append(
            "%d copper feature(s) are narrower than %.4f mm (the "
            "narrowest is %.4f mm). They get 1 mesh cell and not %d, "
            "thus Z0 and eps_eff can have an error of about 10%%. Use a "
            "finer mesh preset."
            % (len(narrow), POLY_FEATURE_CELLS * tol, min(narrow),
               POLY_FEATURE_CELLS))
    # The merge attaches the three lines of a via with a radius smaller
    # than `tol`. The rank keeps the centre. The barrel is then ONE PEC
    # edge on its axis, which is a thin wire.
    thin = [v["r"] for v in model["vias"] if v["r"] * VIA_SURFACE < tol]
    if thin:
        chose.append(
            "%d via(s) have a radius less than %.4f mm (the smallest is "
            "%.4f mm), thus the mesh models them as thin wires. Use a "
            "finer mesh preset."
            % (len(thin), tol, min(thin)))
    if notes is None:
        for line in chose:
            print("[rfsim] WARNING: %s" % line, flush=True)
    else:
        notes += chose
    anchor_x = [(v, RANK_FACE) for v in le_x] + via_x
    anchor_y = [(v, RANK_FACE) for v in le_y] + via_y
    return (_merge_close(xs, tol, anchor_x), _merge_close(ys, tol, anchor_y),
            _merge_close(zs, tol_z))


def _epc_split(grid, e):
    """Give the mesh line that divides the land of an element in two.

    The EPC of an inductor is in PARALLEL with it. TWO lumped elements
    CANNOT share one box. The engine gives the cells of a box to only one
    property, and the other one has no effect and gives no message. The
    caps of the box and the `priority` of CSXCAD do not change this. The one
    that stays is the FIRST on the plain path, and the SECOND on the path
    of the RLC extension.

    Thus the part and its EPC are adjacent ACROSS the current, on the two
    half lands. They are in parallel, because the two bridge the same gap.

    **The line between them must be a line that the mesh has.** A new line
    there makes a smaller cell and costs the timestep of all the board. The
    faces of these two boxes are not anchors of `_merge_close`. Thus the
    line nearest to the middle of the land divides it, and the mesh does
    not move.

    **A land of ONE cell has no line in it. Thus it cannot have an EPC**,
    and this function gives None.

    On 2026-09-21, a measurement against 1/(2 pi sqrt(LC)) with 10 nH and
    0.28 pF gave the cost of the division. A land of 4 cells, thus 2 for
    each box, puts the notch +0.6% high. The land of an 0402 on a board
    (0.640 mm, 2 cells of 0.32 mm, thus ONE for each box) puts it +8.7%
    high. The two boxes must TOUCH. One cell of the land between them moves
    the notch to -16%.
    """
    k = 1 if e["ny"] == "x" else 0
    lo, hi = sorted((e["start"][k], e["stop"][k]))
    inside = [v for v in grid.GetLines("xy"[k]) if lo + 1e-9 < v < hi - 1e-9]
    if not inside:
        return None
    mid = 0.5 * (lo + hi)
    return min(inside, key=lambda v: abs(v - mid))


def build(model, excite_idx, res, want_ff=False, quiet=False):
    """Make a new FDTD model and CSX model, with port `excite_idx` excited.

    `quiet` stops the lines about the parts, the ports and the mesh. `main`
    builds one time for each excitation, and the model is the same each
    time. Thus those lines come only from the first build.
    """
    say = (lambda *a, **k: None) if quiet else print
    # The openEMS libraries have no signature. Thus Windows Smart App Control
    # can stop them. The default traceback does not tell the user what to do.
    try:
        from CSXCAD import ContinuousStructure
        from openEMS import openEMS
    except ImportError as e:
        if "Application Control policy" not in str(e):
            raise
        raise SystemExit(
            "[rfsim] ERROR: Windows Smart App Control stopped the "
            "openEMS libraries, because they have no signature. To "
            "correct this, open Windows Security > App & browser "
            "control, and set Smart App Control to Off.")

    s = model["settings"]
    f0 = 0.5 * (s["f_start"] + s["f_stop"])
    fc = 0.5 * (s["f_stop"] - s["f_start"])
    # A smaller timestep must have more steps for the same simulated time.
    # `_max_timesteps` holds that rule, because `main()` uses the same
    # number to tell how the run ended.
    tsf = _time_step_factor(model)
    nrts = _max_timesteps(model)
    fdtd = openEMS(NrTS=nrts, EndCriteria=s["end_criteria"])
    if tsf and tsf < 1.0:
        fdtd.SetTimeStepFactor(tsf)
        say("[rfsim] timestep factor: %s. Step limit: %d"
              % (_time_step_choice(model)[1], nrts), flush=True)
    fdtd.SetGaussExcite(f0, fc)
    # MUR showed a slow increase of the energy at late times on this setup.
    # PML_8 with an absorber band of 8 cells is stable.
    fdtd.SetBoundaryCond(["PML_8"] * 6)
    csx = ContinuousStructure()
    fdtd.SetCSX(csx)
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)  # the unit of the geometry: mm

    ports_geo = _port_geometry(model, res, quiet=quiet)
    from CSXCAD.SmoothMeshLines import SmoothMeshLines
    # Round the smooth mesh lines. A line at 1.5300000000000002 does not
    # touch a copper sheet with no thickness at 1.53, and openEMS then
    # gives "unused primitive".
    for axis, lines in zip("xyz", _mesh(model, ports_geo, res,
                                            [] if quiet else None)):
        grid.AddLine(axis, np.round(SmoothMeshLines(lines, res, 1.4), 9))

    br = model["board_rect"]
    for i, d in enumerate(model["dielectric_layers"]):
        kappa = 2 * np.pi * f0 * EPS0 * d["epsilon"] * d["loss_tangent"]
        mat = csx.AddMaterial("diel%d" % i, epsilon=d["epsilon"], kappa=kappa)
        mat.AddBox([br["x0"], br["y0"], d["z_bottom"]],
                   [br["x1"], br["y1"], d["z_top"]], priority=1)

    copper_prop = {}
    port_layers = {p["layer"] for p in _ports_of(model)}
    for c in model["copper_layers"]:
        polys = model["polygons"].get(c["name"], [])
        # A layer with no copper has no property. A property with no
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
        # `rlc` only tells that the ENGINE has the series topology. Each
        # element gets its own topology, and `_le_topology` selects it from
        # the components of that element. The package parasitics put R, L
        # and C together in one element. Thus they must have the series
        # topology of LEtype. An older engine has no LEtype, and it removes
        # an element that has an L without a message. Thus the code removes
        # the parasitics on such an engine.
        para = s.get("parasitics", True) and rlc
        if s.get("parasitics", True) and not rlc:
            say("[rfsim] WARNING: this openEMS version has no LEtype, "
                "thus the package parasitics are off (ideal elements)",
                flush=True)
        ported = _ported_refs(model)
        opens = _open_parts(model)
        for e in model.get("lumped_elements", []):
            # A PORT is in the box of this element. Thus the grid has no
            # value for it, and the run pays no timestep for it.
            if e.get("ref") in ported:
                say("[rfsim] lumped %s: a port is in its box, and the "
                    "value of the part goes into the S-matrix after the "
                    "run" % e["ref"], flush=True)
                continue
            # **An OPEN at all frequencies of the sweep stays out of the
            # grid**: refer to `_open_parts`. Its mesh lines stay, thus the
            # board keeps the mesh that it has with the part. The EPC of an
            # inductor goes in WITHOUT the part, across all the box. At
            # GHz, a choke on a board is that capacitance, and a C without
            # other components uses the classic path.
            if e.get("ref") in opens:
                comp = _components(e, para, s)
                z = solverenv.smallest_z(comp.get("R"), comp.get("L"),
                                         comp.get("C"), s["f_start"],
                                         s["f_stop"])
                epc = (e.get("epc") or 0.0) if para else 0.0
                if epc > 0:
                    csx.AddLumpedElement(
                        "epc_" + e["ref"], ny=e["ny"], caps=True,
                        **dict(_le_topology({"C": epc}, rlc),
                               C=epc)).AddBox(e["start"], e["stop"],
                                              priority=15)
                say("[rfsim] lumped %s: an open circuit at all frequencies of "
                    "the sweep (%.3g ohm or more, %.0f times z0). Thus "
                    "%s, and it costs no timestep" % (
                          e["ref"], z, z / s["z0"],
                          "only its EPC of %g pF stays" % (epc * 1e12)
                          if epc > 0 else "its gap stays open"),
                      flush=True)
                continue
            # **A SERIES RLC part gives its three components itself**, and
            # it has no `value`. It is ONE element with LEtype=1. A
            # capacitor with its ESR and its ESL makes the same element. It
            # gets no package parasitics: its R and its L are the body. A
            # component that the part does not give stays out of the
            # element, and the engine then does not include it in the
            # series.
            if e.get("type") == "RLC":
                comp = _components(e, para, s)
                if not comp:
                    say("[rfsim] WARNING: lumped %s is a series RLC with "
                        "no R, no L and no C, thus RFsim does not model "
                        "it (the gap between its pads stays open)"
                        % e.get("ref", "?"), flush=True)
                    continue
                csx.AddLumpedElement(
                    "le_" + e["ref"], ny=e["ny"], caps=True,
                    **dict(_le_topology(comp, rlc), **comp)).AddBox(
                    e["start"], e["stop"], priority=15)
                say("[rfsim] lumped %s: series RLC %s (%s-axis) at z=%.3f"
                      % (e["ref"], ", ".join(
                          "%s=%g %s" % (k, v, {"R": "ohm", "L": "H",
                                               "C": "F"}[k])
                          for k, v in sorted(comp.items())),
                         e["ny"], e["start"][2]), flush=True)
                continue
            # When the refdes of a part does not give the type, the part
            # comes out of the extraction with type None and value None.
            # The dialog removes it when the user models nothing. A
            # model.json that a person edits can continue to have one.
            if not e.get("type") or e.get("value") is None:
                say("[rfsim] WARNING: lumped %s has no type or no value, "
                    "thus RFsim does not model it (the gap between its "
                    "pads stays open)"
                      % e.get("ref", "?"), flush=True)
                continue
            if e["type"] == "R" and e["value"] == 0:  # 0 ohm = a short circuit
                csx.AddMetal("short_" + e["ref"]).AddBox(
                    e["start"], e["stop"], priority=15)
                unit = "ohm (short)"
            else:
                # The components that you do not give are NaN, not 0. Thus
                # a part and its parasitics make ONE element with the
                # series topology. `_le_topology` selects that topology
                # from the components. An R or a C without other components
                # must NOT use it: the series extension diverges on a
                # branch with no inductance in it.
                comp = _components(e, para, s)
                # **The EPC goes on the OTHER half of the land**, because
                # two elements cannot share a box: refer to `_epc_split`.
                # The part keeps the half that comes first, and its value
                # does not change. An element has the same value on a half
                # land as on a full land: a resistor of 50 ohm read +0.2%
                # on the half.
                epc = (e.get("epc") or 0.0) if para else 0.0
                start, stop = list(e["start"]), list(e["stop"])
                k = 1 if e["ny"] == "x" else 0
                split = _epc_split(grid, e) if epc > 0 else None
                if split is not None:
                    stop[k] = split
                csx.AddLumpedElement(
                    "le_" + e["ref"], ny=e["ny"], caps=True,
                    **dict(_le_topology(comp, rlc), **comp)).AddBox(
                    start, stop, priority=15)
                if split is not None:
                    c_start = list(e["start"])
                    c_start[k] = split
                    # **`LEtype=0` and NOT the topology of the part.**
                    # `LEtype` selects the topology of ALL an element. Thus
                    # a C without other components gives the same curve
                    # with the two values on a box of its own. Adjacent to
                    # a different element, it does NOT. The same geometry
                    # with `LEtype=1` on this element put the
                    # self-resonance 30% LOW. `LEtype=0` puts it in 1% of
                    # the correct value on a wide land.
                    csx.AddLumpedElement("epc_" + e["ref"], ny=e["ny"],
                                         caps=True, LEtype=0,
                                         C=epc).AddBox(
                        c_start, list(e["stop"]), priority=15)
                    f_res = 1.0 / (2 * np.pi * np.sqrt(e["value"] * epc)) \
                        if e["type"] == "L" and e["value"] else 0.0
                    say("[rfsim] lumped %s: EPC %g pF in parallel, on the "
                          "other half of its land%s" % (
                              e["ref"], epc * 1e12,
                              "; the self-resonance is near %.2f GHz"
                              % (f_res / 1e9) if f_res else ""), flush=True)
                elif epc > 0:
                    say("[rfsim] WARNING: the land of %s has only one "
                        "mesh cell across the current, thus it cannot "
                        "hold the EPC adjacent to the part. Two lumped "
                        "elements cannot share one box. %s keeps only "
                        "its value, and it has no self-resonance. Use a "
                        "finer mesh preset, which gives the land more "
                        "cells."
                          % (e["ref"], e["ref"]), flush=True)
                unit = {"R": "ohm", "L": "H", "C": "F"}[e["type"]]
                extra = ["%s %g %s" % (k, v, {"R": "ohm", "L": "H",
                                              "C": "F"}[k])
                         for k, v in sorted(comp.items()) if k != e["type"]]
                if extra:
                    unit += " + %s (%s body)" % (
                        ", ".join(extra), e.get("package") or "unknown package")
            say("[rfsim] lumped %s: %s=%g %s (%s-axis) at z=%.3f"
                  % (e["ref"], e["type"], e["value"], unit, e["ny"],
                     e["start"][2]), flush=True)

    ports = []
    for i, g in enumerate(ports_geo):
        excite = (i == excite_idx)
        note = ""
        if g["type"] in TL_PORTS:
            # exc_dir is "z" for all three types. A microstrip port uses it
            # as the direction from the strip to the plane. A CPW port and
            # a stripline port use it only to find the plane of the strip.
            # Their probes go across the gaps, or up and down.
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
            note = "direction " + g["prop_dir"] + note
        else:
            # **The axis comes from the port.** A lumped port of the board
            # is between a pad and the plane below it, thus its axis is z.
            # An element port is IN the plane of the board, on the axis of
            # the element that it replaces.
            ports.append(fdtd.AddLumpedPort(
                g["number"], s["z0"], g["start"], g["stop"],
                g.get("exc_dir", "z"),
                excite=1.0 if excite else 0, priority=20))
        say("[rfsim] port %d: %s at (%.2f, %.2f)%s" % (
            g["number"], g["type"], g["x"], g["y"],
            " " + note if note else ""), flush=True)

    ff = None
    # An element port excites the gap between two pads. Thus a far field of
    # that run gives no answer that a user can use. The views come from the
    # ports of the board.
    if want_ff and ports_geo[excite_idx]["type"] != "element":
        # Dump the E field and the H field on the middle plane of the
        # substrate, below the port that the run excites. The frequency is
        # the "Define at" value of the user, or the center frequency for a
        # previous model. The files are small, but they are sufficient for
        # the wave animations of the GUI.
        f_dump = s.get("f_field") or f0
        g0 = ports_geo[excite_idx]
        z_cut = 0.5 * (g0["z_top"] + g0["z_ref"])
        r = model["region"]
        # **The two boxes below are against the FACE of the PML and not
        # against the edge of the region.** At this time, the band is as
        # deep as 8 cells of `res`, and the region increased with it.
        # Before 2026-09-20, all models had a band as deep as the margin.
        # For those models, the two boxes give the same result as before.
        pml = model.get("pml_mm") or s["margin_mm"]
        for name, dt in (("Ef", 10), ("Hf", 11)):
            # dump_mode=1 interpolates to the mesh nodes. The default value
            # (0) dumps the raw Yee values, which show H one half of a cell
            # away from the copper.
            dump = csx.AddDump(name, dump_type=dt, dump_mode=1, file_type=1,
                               frequency=[f_dump])
            # The view holds the structure, the clear air and one margin of
            # the absorber, as it did before the band became deeper. A view
            # of all the band is almost all absorber.
            edge = pml - s["margin_mm"]
            dump.AddBox([r["x0"] + edge, r["y0"] + edge, z_cut],
                        [r["x1"] - edge, r["y1"] - edge, z_cut])

        # The NF2FF box is in the middle of the band of clear air between
        # the structure and the PML. That is the face of the absorber, and
        # then half of the margin in. The box records at the same
        # frequency.
        from openEMS.nf2ff import nf2ff
        margin = s["margin_mm"]
        board_top = model["copper_layers"][0]["z"]
        inset = pml + 0.5 * margin
        ff = nf2ff(csx, "nf2ff",
                   [r["x0"] + inset, r["y0"] + inset, -0.5 * margin],
                   [r["x1"] - inset, r["y1"] - inset,
                    board_top + 0.5 * margin],
                   frequency=[f_dump])

    say("[rfsim] mesh: %d x %d x %d lines" % tuple(
        grid.GetQtyLines(a) for a in "xyz"), flush=True)
    return fdtd, ports, ff


def _field_norm(sim_path, port, f_hz, z0):
    """Write the factor that scales the field dumps, in the style of CST.

    CST excites a port with a wave of 1 sqrt(W) peak, which is 0.5 W of
    incident power. It divides each monitor by the spectrum of the
    excitation. Phase 0 is then the peak of the incident wave at the port.
    The factor here is sqrt(0.5 / P_inc), and it removes the phase of the
    incident voltage. The FD dumps and the port probes use the same DFT (2
    * dt * sum), thus the spectrum of the pulse divides out.

    The incident power, and not the accepted power, sets the reference. It
    does not change with the load. Thus a port that sends back almost all
    of the power does not make the fields very large. The factor goes into
    field.json, adjacent to the dumps.
    """
    port.CalcPort(sim_path, np.array([f_hz]), ref_impedance=z0)
    p_inc = float(port.P_inc[0])
    u_inc = complex(port.uf_inc[0])
    c = np.sqrt(0.5 / p_inc) * np.conj(u_inc) / abs(u_inc)
    with open(os.path.join(sim_path, "field.json"), "w") as fh:
        json.dump({"f_hz": float(f_hz), "P_inc_W": p_inc,
                   "scale": [float(c.real), float(c.imag)]}, fh, indent=1)


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
    print("[rfsim] far field: Dmax %.1f dBi, radiated power %.1f%% of "
          "the input power"
          % (10.0 * np.log10(Dmax), eff if eff is not None else -1), flush=True)


def _line_data(port, sim_path, freq):
    """Give the impedance of the line and eps_eff of a port, or give None.

    A de-embedded port (microstrip, CPW or stripline) has three voltage
    probes and two current probes along the line. From them, ReadUIData
    calculates `beta`, the propagation constant, and `Z_ref`, the impedance
    of the line. These are the values of the ACTUAL track on the ACTUAL
    stackup: KiCad has no other tool that gives them.

    CalcPort then replaces Z_ref with the reference impedance of the system
    (usually 50 ohm), because the S-parameters use that value. Thus the
    caller must call this function BEFORE CalcPort.

    A lumped port has no line, thus it has no beta. Then the result is
    None.

    **Give this function a port that the run does NOT excite.** The three
    voltage probes are at three adjacent mesh lines. Thus they measure
    about 2 cells from the plane of the excitation, in the near field of
    the source. eps_eff then reads 6% to 34% high. For the same port at the
    far end of the same line, it reads 1% to 6% high. The caller holds that
    rule.
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
    # **Do not keep the imaginary part of `beta` as the attenuation.** It
    # looks correct and it costs one line, but the number that comes out is
    # NOISE. Measured on 2026-08-05, on the validation microstrip at the
    # coarse preset, as dB/m against the frequency:
    #
    #   1.0 GHz  -9.4    2.5 GHz   6.0    4.5 GHz  36.0
    #   1.5 GHz  -6.2    3.0 GHz  11.4    5.0 GHz  64.5
    #   2.0 GHz  -0.5    3.5 GHz  14.5    5.5 GHz  79.8
    #                    4.0 GHz  21.3    6.0 GHz  63.0
    #
    # A passive line cannot give a NEGATIVE attenuation, and the value
    # decreases again above 5.5 GHz. Re(Z0) and eps_eff stay stable across
    # the same sweep. Thus the mode and the geometry are correct, and only
    # the imaginary part is bad.
    #
    # The cause is the numerical condition of the formula, and this code
    # cannot correct it. `beta` comes from finite differences of the probes
    # along the line, across a span of about 9 mm. The PHASE turns a large
    # part of a wavelength across that span. Thus Re(beta) has a good
    # condition. The LOSS across the same span is 0.105 dB, which is 1.2%
    # of the amplitude. Thus the imaginary part is the difference of two
    # numbers that are almost equal. The loss of a line must have a
    # different procedure of measurement. Use two lines of different
    # lengths, or |S21| of a matched line across a long span.
    # `validation/run_atten.py` measures it with that procedure.
    return {"Z0_real": np.real(z).tolist(),
            "Z0_imag": np.imag(z).tolist(),
            "eps_eff": np.where(np.isfinite(eps), eps, 0.0).tolist()}


def write_touchstone(path, freq, S, z0):
    """Write a Touchstone v1 file.

    A file for 1 or 2 ports has one line for each frequency. The columns of
    a 2-port file are in the usual sequence S11 S21 S12 S22. A file for 3
    ports or more is in the sequence of the rows, with a maximum of 4 pairs
    on a line.
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
    # A model that is NEWER than this runner can have a key that gives a
    # new function to a value. The runner then reads the file and gives an
    # incorrect number with no message, which is worse than a stop. A model
    # with no "version" key comes from before 2026-08-05, and it is version
    # 1.
    version = int(model.get("version", 1))
    if version > MODEL_VERSION:
        raise SystemExit(
            "[rfsim] ERROR: %s is a version %d model, and this runner "
            "reads only version %d or lower. Update the plugin."
            % (os.path.basename(model_path), version, MODEL_VERSION))
    for w in model.get("warnings", []):
        print("[rfsim] WARNING: %s" % w, flush=True)
    s = model["settings"]
    # A part that is a port, or an open at all frequencies of the sweep, is
    # not in the grid. Thus the engine does not have to have lumped RLC for
    # it.
    ported = _ported_refs(model) | _open_parts(model)
    n = len(_ports_of(model))
    if n < 1:
        raise SystemExit("[rfsim] ERROR: the model has %d ports, and it "
                         "must have 1 or more." % n)

    # A lumped inductor must have openEMS v0.37 or after, which has lumped
    # RLC. An older engine writes "Lumped Element R or C not specified!
    # skipping" and models an open circuit. Thus refuse to run, because the
    # results are then incorrect.
    if s.get("lumped", True):
        # An element that became a port has no value in the grid. Thus the
        # engine does not have to have lumped RLC for it.
        bad = [e["ref"] for e in model.get("lumped_elements", [])
               if e["type"] == "L" and e.get("ref") not in ported]
        if bad and not _has_lumped_rlc():
            raise SystemExit(
                "[rfsim] ERROR: %s: this openEMS version cannot simulate "
                "lumped inductors. It removes them with no message, and "
                "the result is then incorrect. Use openEMS v0.37 or "
                "newer (see the README, \"Installation\"), or set the "
                "value to DNP." % ", ".join(bad))
        # A series RLC part must have the SERIES topology. An engine with
        # no LEtype has only the parallel topology. It puts R, L and C in
        # parallel, which is a different circuit and not a worse one.
        series = [e["ref"] for e in model.get("lumped_elements", [])
                  if e["type"] == "RLC" and e.get("ref") not in ported]
        if series and not _has_lumped_rlc():
            raise SystemExit(
                "[rfsim] ERROR: %s: this openEMS version has no series "
                "lumped element (LEtype). Thus it cannot simulate a "
                "Series RLC part. Use openEMS v0.37 or newer (see the "
                "README, \"Installation\")." % ", ".join(series))

    # Remove the results of a previous run from this output directory. An
    # excN folder, or a farfield.json that this run does not write again,
    # looks current to the GUI. This occurs with a different set of excited
    # ports, or after a far field that stopped with an error.
    for d in glob.glob(os.path.join(outdir, "exc*")):
        shutil.rmtree(d, ignore_errors=True)
    for fpath in (glob.glob(os.path.join(outdir, "farfield*.json"))
                  + [os.path.join(outdir, "lines.json")]):
        try:
            os.remove(fpath)
        except OSError:
            pass

    eps_max = max(d["epsilon"] for d in model["dielectric_layers"])
    res = solverenv.mesh_res(s["f_stop"], eps_max, s["mesh"])
    print("[rfsim] mesh step: %.3f mm (%s preset)" % (res, s["mesh"]),
          flush=True)

    # **Tell the decisions of the run ONE time, and keep them.** `build`
    # runs one time for each excitation, thus its own lines come again each
    # time. This list does not. It goes to optimizations.log BEFORE the engine
    # starts. Thus a run that stops with an error also keeps the list. The
    # lines of the engine and of the end of each run go after it.
    log_path = os.path.join(outdir, "optimizations.log")
    choices = _decisions(model, res, _has_lumped_rlc())
    print("[rfsim] === optimizations ===", flush=True)
    for line in choices:
        print("[rfsim] optimization: %s" % line, flush=True)

    def keep(line):
        """Add one line to optimizations.log, with "- " in front, thus the
        file is easy to read. A log must not stop a run."""
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("- " + line + "\n")
        except OSError:
            pass

    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("RFsim: optimizations\n%s\nmodel: "
                     "%s\nsweep: %g to %g GHz, z0 %g ohm, mesh %s\n\n"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), model_path,
                        s["f_start"] / 1e9, s["f_stop"] / 1e9, s["z0"],
                        s["mesh"]))
    except OSError:
        pass
    for line in choices:
        keep(line)

    freq = np.linspace(s["f_start"], s["f_stop"], s.get("n_freq", 401))
    S = np.zeros((len(freq), n, n), dtype=complex)
    # Excite only the ports that the user selected. Each port is a full
    # FDTD run. The S-columns of the other ports stay zero. A default
    # model, or a previous model, excites all the ports.
    nums = [p["number"] for p in _ports_of(model)]
    want = set(s.get("excite") or nums)
    exc = [i for i, num in enumerate(nums) if num in want] or [0]
    lines = {}  # the port number -> the impedance data of its line
    # The port number -> True when the reading in `lines` comes from the
    # run that excited that port. Such a reading is the bad one, thus a
    # subsequent run replaces it.
    line_exc = {}
    for step, k in enumerate(exc):
        sim_path = os.path.join(outdir, "exc%d" % (k + 1))
        print("[rfsim] === excitation %d/%d (port %d) ==="
              % (step + 1, len(exc), k + 1), flush=True)
        fdtd, ports, ff = build(model, k, res, want_ff=True, quiet=step > 0)
        if step == 0:
            # The mesh is the same for each excitation. Thus calculate the
            # thread count one time, and tell the user which value it is.
            cells = _cell_count(fdtd)
            threads = _threads(s, cells)
            print("[rfsim] engine: multithreaded, %d thread(s)" % threads,
                  flush=True)
            keep("engine: %d thread(s), %s" % (
                threads, "from the CPU threads of the settings"
                if s.get("threads")
                else "from the rule for %d cells (4 below 150 k cells, 8 "
                     "above)" % cells))
            for line in _epc_decisions(model, fdtd.GetCSX().GetGrid(),
                                       _has_lumped_rlc()):
                print("[rfsim] optimization: %s" % line, flush=True)
                keep(line)
        fdtd.Run(sim_path, cleanup=True, engine="multithreaded",
                 numThreads=threads)
        end = _report_end(sim_path, _max_timesteps(model),
                          s["end_criteria"])
        if end:
            keep("excitation of port %d: %s" % (k + 1, end))
        bad = _diverged(sim_path)
        if bad:
            name, cause = bad
            keep("excitation of port %d: stopped, %s" % (
                k + 1, "NaN in %s" % name if cause == "nan"
                else "the field grew again in %s" % name))
            tsf = _time_step_factor(model) or 1.0
            if cause == "nan":
                raise SystemExit(
                    "[rfsim] ERROR: the FDTD run diverged: %s has NaN "
                    "values. A large inductance or a large series "
                    "resistance must have a smaller timestep. This run "
                    "used a timestep factor of %.3g. Set a smaller "
                    "\"Timestep\" in the dialog (for example %.3g), and "
                    "run again." % (name, tsf, tsf / 2.0))
            raise SystemExit(
                "[rfsim] ERROR: the FDTD run is not stable. The port "
                "data of %s increased again after its lowest point, to "
                "more than %g times that point. The S-matrix of such a "
                "run is not correct: |S11| and |S21| are almost 1 at all "
                "frequencies. A smaller timestep does not correct this "
                "(this run used a timestep factor of %.3g). Increase "
                "\"Domain margin\" to move the absorber away from the "
                "copper. A smaller inductance also helps."
                % (name, GROWTH_LIMIT, tsf))
        # **Read the line of EACH de-embedded port, and keep the port that
        # this run did NOT excite.** The three voltage probes of a port are
        # at three adjacent mesh lines. Thus they measure the field about
        # 2 cells from the plane of the excitation, in the near field of
        # the source. `beta` then reads high, and eps_eff, which is its
        # square, reads much higher. Measured on a straight line at
        # 4 widths, against the phase of S21 across the distance between
        # the two measurement planes:
        #
        #   width    the excited port   a port 30 mm away
        #   0.30 mm  +13.9%             +4.7%
        #   0.60 mm  +34.3%             +2.5%
        #   1.00 mm  +29.2%             +2.1%
        #   2.90 mm   +6.2%             +1.4%
        #
        # The same extraction, the same mesh and the same run: only the
        # distance from the source moves. Thus the wave of the excited port
        # is the WORST one for this quantity, and not the best. The error
        # in Z0 is much smaller, because the errors of the two derivatives
        # divide out there. They multiply in beta.
        #
        # This costs nothing: CalcPort reads the same probe files for each
        # port some lines below. `_line_data` must come first, because
        # CalcPort replaces Z_ref with the reference impedance.
        f_at = s.get("f_field") or 0.5 * (s["f_start"] + s["f_stop"])
        i_at = int(np.argmin(np.abs(freq - f_at)))
        for j, p in enumerate(ports):
            ld = _line_data(p, sim_path, freq)
            if not ld:
                continue
            # A reading from a run that did not excite this port wins.
            # A one-port run has no such reading, thus it keeps its own.
            was_exc = (j == k)
            if j + 1 in lines and (was_exc or not line_exc[j + 1]):
                continue
            lines[j + 1] = ld
            line_exc[j + 1] = was_exc
            print("[rfsim] port %d line: Z0 = %.1f%+.1fj ohm, eps_eff = %.2f "
                  "(at %.3f GHz)%s"
                  % (j + 1, ld["Z0_real"][i_at], ld["Z0_imag"][i_at],
                     ld["eps_eff"][i_at], freq[i_at] / 1e9,
                     " [this run excites this port, thus eps_eff is too "
                     "high]"
                     if was_exc else ""), flush=True)
        for p in ports:
            p.CalcPort(sim_path, freq, ref_impedance=s["z0"])
        for j in range(n):
            S[:, j, k] = ports[j].uf_ref / ports[k].uf_inc
        if ff is not None:
            try:
                _farfield(outdir, ff, sim_path, ports[k], freq,
                          "_p%d" % (k + 1))
            except Exception as e:
                print("[rfsim] WARNING: the far field of port %d is not "
                      "available: %s"
                      % (k + 1, e), flush=True)
            # This comes after the far field. CalcPort at one frequency
            # replaces the port values that `_farfield` reads.
            try:
                _field_norm(sim_path, ports[k], ff.freq[0], s["z0"])
            except Exception as e:
                print("[rfsim] WARNING: the scale of the field views of "
                      "port %d is not available: %s" % (k + 1, e), flush=True)

    if lines:
        with open(os.path.join(outdir, "lines.json"), "w") as fh:
            json.dump({"freq_hz": freq.tolist(), "ports": lines}, fh, indent=1)

    # A passive structure cannot give out more power than it gets. Thus the
    # sum of |S|^2 down an excited column must not go above 1. A value
    # above 1 shows that the S-parameters are incorrect, and not only
    # inaccurate. On a CPW port or a stripline port, the usual cause is a
    # mesh that is too coarse near the line. On the stripline board, the
    # measurement gave sum|S|^2 = 1.71 at the coarse preset and 1.05 at the
    # medium preset. The CPW board gave 1.08 before it had its cells above
    # and below the plane of the line.
    for k in exc:
        power = np.sum(np.abs(S[:, :, k]) ** 2, axis=1)
        if power.max() > 1.05:
            print("[rfsim] WARNING: port %d gives out more power than it "
                  "gets (max sum|S|^2 = %.2f). Thus the S-parameters of "
                  "this port are not reliable. On a CPW port or a "
                  "stripline port, the usual cause is a mesh that is too "
                  "coarse across the line. Run again at a finer mesh "
                  "preset."
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
