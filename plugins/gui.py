"""The wxPython dialogs: the settings of the simulation, the log of the
solver, and the plots of the results."""
import glob
import importlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import types

import wx

# The timestep rule of a lumped inductor, which the dialog shows as a cost
# in run time. **This module must NOT import `runner`**: an import of the
# runner replaces `warnings.showwarning` for all the process. `solverenv`
# imports only `os`. The plugin loads this file as a module of a package,
# and `test_dialog.py` loads it with the `plugins` directory on sys.path.
# Thus the import has the two alternatives.
try:
    from . import solverenv
except ImportError:  # a top-level module
    import solverenv

PORT_TYPES = [("Lumped Port", "lumped"), ("Microstrip (MSL) Port", "msl"),
              ("Coplanar (CPW) Port", "cpw"), ("Stripline Port", "stripline")]
MESH_LEVELS = ["coarse", "medium", "fine", "ultrafine"]
# The rows of the R/L/C parts that the dialog shows without a scroll. Each
# row is about 29 px tall. With one part, the dialog is 1053 px tall on a
# screen of 1920x1080.
MAX_PART_ROWS = 6
# The largest step limit that the dialog lets a run START with. The runner
# divides the step limit by the timestep factor for the same simulated
# time. Thus openEMS receives this number, and it is not the number in the
# "Max steps" field.
#
# **50 million is a decision of the owner** and not a measurement. At the
# default of 300000 steps, it refuses an inductance of more than about
# 6.9 uH. It lets 1 uH run, with 19 million steps. That is a run of some
# hours, thus a user can let a choke run during the night. The value of
# 90000 nH that started this rule causes 180 million steps. That is about
# 135 hours on the SMALL board of the rigs, and more on the board of a
# user. A user who wants such a run gives a "Timestep factor" of their own,
# which this rule reads.
MAX_DERIVED_STEPS = 50e6
CUSTOM_PKG = "Custom"       # the user gives the ESL and the ESR
NO_PARASITICS = "No parasitics"   # an ideal element: no ESL and no ESR
DECISIONS_VIEW = "Decisions"      # the view of decisions.log: text, no plot
# "Series RLC" is a type of its own, for a part that no single R, L or C
# can model. A PIN diode that is off is C_T in series with L_s and R_s. Its
# row has three fields and not one value, and it has no parasitics: its R
# and its L ARE the body.
RLC_KIND = "RLC"
KIND_NAMES = {"R": "Resistor", "C": "Capacitor", "L": "Inductor",
              RLC_KIND: "Series RLC"}
# The name of the quantity, for the label in front of the value of a part.
KIND_QUANTITY = {"R": "Resistance", "C": "Capacitance", "L": "Inductance"}
# The first entry of the type choice. It is for a part when the refdes does
# not tell the type (a diode, a ferrite bead, a footprint of your own).
UNKNOWN_KIND = "Unknown"
KIND_ORDER = [None] + list(KIND_NAMES)  # the index in the type choice
# The unit of the field that the user fills in. A number and no prefix, as
# in the ESR and the ESL fields. The value goes into model.json in SI
# units.
ENTRY_UNITS = {"R": "ohm", "C": "pF", "L": "nH"}
ENTRY_SCALE = {"R": 1.0, "C": 1e-12, "L": 1e-9}
# The three fields of a series RLC row, and the key of each one in
# model.json. Each field uses the unit of the single value of its type.
RLC_FIELDS = (("R", "r"), ("L", "l"), ("C", "c"))
# The field that gives an inductor its self-resonance. The user types the
# SRF, because the datasheet of each inductor gives THAT value. The code
# gives the capacitance that is in parallel with the value at that
# frequency: C = 1 / ((2 pi f)^2 L). An empty field keeps the part as it
# was. Thus this field changes no board that does not fill it in.
SRF_LABEL, SRF_UNIT = "SRF:", "GHz"


def _board_substrate_text(model):
    """Give the text of the four substrate fields that the BOARD gives.

    `model` is the preview from `board_reader.extract` with NO substrate
    argument. Thus its stackup came from the board: from the saved file, or
    from the board in memory. The function gives (er, tan d, h, cu_t) as
    text, or None when the board gives no stackup.

    **A source of "default" gives None**, and not the values. Those values
    are the FR4 fallback of `board_reader`. Thus they can show the defaults
    of the CODE as values of the board. That rule comes from the test of
    2026-08-05.

    A stackup holds each dielectric as a different layer, and the layers
    can be different. Thus a field shows all the values that the board
    gives, with " / " between them. A field is READ-ONLY with this preset,
    and the run uses the stackup layer by layer and not the text.
    """
    if not model or model.get("stackup_source") not in ("file", "memory"):
        return None
    diel = model.get("dielectric_layers") or []
    if not diel:
        return None

    def join(values):
        out = []
        for v in values:
            t = "%g" % v
            if t not in out:
                out.append(t)
        return " / ".join(out)

    cu = [c["thickness"] for c in model.get("copper_layers") or []]
    return (join(d["epsilon"] for d in diel),
            join(d["loss_tangent"] for d in diel),
            "%g" % sum(d["z_top"] - d["z_bottom"] for d in diel),
            join(cu) if cu else "")


def _qty_label(kind):
    """Give the label in front of the value of a part."""
    return KIND_QUANTITY.get(kind, "Value") + ":"


def _entry_text(kind, value_si):
    """Give the text of the value field: a number in the unit of the row.

    The unit is ohm, nH or pF. Thus 4.7 kohm becomes "4700" and 10 nH
    becomes "10". A part with no type has an empty field.
    """
    if kind not in ENTRY_SCALE or value_si is None:
        return ""
    return "%g" % (value_si / ENTRY_SCALE[kind])


# The er and the tan d of each preset. **FR-4 must agree with
# `board_reader.DEF_EPSILON`**, which is the value that the plugin uses
# when the board file has no stackup. Until 2026-08-05, the two were 4.2
# here and 4.5 there. Thus the SAME board got a substrate through the
# dialog and a different one through a run with no GUI. The laminates after
# FR-4 are the usual low-loss laminates of a fabricator. Their values are
# the DATASHEET values at 10 GHz, and FR-4 is at 1 MHz. The er of FR-4
# decreases to about 4.3 at 5 GHz. Thus a run in the GHz band must have the
# value that your fabricator gives for its laminate.
#
# **Each Rogers grade has two rows, because Rogers gives two Dk values.**
# The PROCESS Dk (3.48 for RO4350B, 3.38 for RO4003C) comes from a
# stripline test at 10 GHz. The DESIGN Dk (3.66 and 3.55) is larger,
# and Rogers gets it from microstrip lines. That value puts the field of a
# microstrip in a material constant. A 3D solver calculates that field
# itself. Thus the design value can count the same effect two times.
#
# The difference is not large. On a 50 ohm microstrip, Hammerstad and
# Jensen give a Z0 2.3% higher and a delay 2.2% shorter with the process
# value, at all thicknesses. No board of `validation/` is a Rogers board.
# Thus no measurement tells which value agrees with this solver, and the
# user selects. The two rows of a grade keep ONE tan d, because Rogers
# gives one value at 10 GHz.
#
# **The first entry is the board itself, and it is not a laminate.** It has
# no er and no tan d here. The values come from the `(stackup ...)` block
# of the board, LAYER BY LAYER, thus two numbers cannot hold them.
# `_board_substrate_text` makes the text of the four fields. `get_settings`
# gives None for the four, and that tells `extract()` to read the stackup
# itself.
BOARD_PRESET = "KiCad's Stackup"
SUBSTRATE_PRESETS = [(BOARD_PRESET, None, None),
                     ("FR-4", 4.5, 0.02),
                     ("Rogers RO4350B (stripline)", 3.48, 0.0037),
                     ("Rogers RO4350B (microstrip)", 3.66, 0.0037),
                     ("Rogers RO4003C (stripline)", 3.38, 0.0027),
                     ("Rogers RO4003C (microstrip)", 3.55, 0.0027),
                     # The values are those of RT/duroid 5880, which is the
                     # usual PTFE laminate. The er of a different PTFE is
                     # from 2.1 to 2.6, with the glass in it.
                     ("PTFE", 2.20, 0.0009),
                     ("Custom", None, None)]
# The colours of the top view. The preview in the settings dialog and the
# view in the results window use them.
CU_COLORS = {"F.Cu": ("tab:red", 0.8), "B.Cu": ("tab:blue", 0.45)}


def _port_choices(p):
    """Give (label, value) for the port types that the geometry lets you
    use.

    A lumped port always operates. Each de-embedded port must have a feed
    direction (a track, or the manual "Feed" control). A CPW port also must
    have a coplanar gap. A stripline port must have a plane above the strip
    and a plane below it. Each of those two entries shows its measured
    value in brackets. A value in brackets comes from the board, and the
    user cannot change it. The two values keep three decimals. The two
    entries are adjacent, thus a different count of decimals looks
    arbitrary. Three decimals are also necessary. A stackup that comes from
    mil gives 0.127 mm and 0.254 mm, and two decimals make the two values
    equal. The label of the port row names what the geometry does not give.
    """
    out = [PORT_TYPES[0]]
    if p.get("direction"):
        out.append(PORT_TYPES[1])
        if p.get("gap"):
            out.append(("%s [Coplanar Gap: %.3f mm]"
                        % (PORT_TYPES[2][0], p["gap"]), "cpw"))
        if p.get("height"):
            out.append(("%s [Strip to Plane: %.3f mm]"
                        % (PORT_TYPES[3][0], p["height"]), "stripline"))
    return out


def _use_wxagg():
    """Select the wx backend of matplotlib, around a defect in wxPython.

    The wxPython of KiCad contains `wx/svg/`, but not the `_nanosvg`
    extension that it must have. The wx backend of matplotlib imports
    `wx.svg` for a side effect only, and it does not use it. Thus this
    function replaces the module when the module is defective. Use
    `importlib.import_module` for the test, and do not use `import wx.svg`.
    That statement makes `wx` a *local* name here. Then, if the import
    stops with an error, all the `wx.*` names after it stop with
    UnboundLocalError.
    """
    import matplotlib
    matplotlib.use("WXAgg", force=False)
    try:
        importlib.import_module("wx.svg")
    except ImportError:
        sys.modules["wx.svg"] = types.ModuleType("wx.svg")


def _draw_board(ax, model, compact=False, margin_mm=None, show_lumped=True,
                pml_mm=None):
    """Show the top view of the model.

    B.Cu is blue, F.Cu is red, the ports are green and the R/L/C parts are
    dark green. The preview of the settings dialog and the "Board layout"
    view of the results window use this function.

    compact       Make a thumbnail for the dialog: no axes, no title and
                  no legend. Thus the board fills the full canvas.
    margin_mm     Show the domain from board_rect plus the margin and the
                  depth of the PML band, and not from model["region"].
                  The dialog shows the margin that the user selects at
                  this time, not the margin of the model.
    pml_mm        The depth of the band, adjacent to `margin_mm`. With
                  None, the depth is the margin. The plugin made that
                  depth before 2026-09-20.
    show_lumped   Show the R/L/C parts or do not show them. The dialog
                  gives False when no part has its "Model" checkbox.
    """
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    m = model
    handles = []
    for c in reversed(m["copper_layers"]):  # the bottom first, F.Cu on top
        name = c["name"]
        col, alpha = CU_COLORS.get(name, ("0.5", 0.5))
        polys = m["polygons"].get(name, [])
        for poly in polys:
            ax.fill([p[0] for p in poly], [p[1] for p in poly],
                    color=col, alpha=alpha, linewidth=0)
        if polys:
            handles.append(Patch(color=col, alpha=alpha, label=name))
    for v in m.get("vias", []):
        ax.plot(v["x"], v["y"], "o", color="k", ms=3)

    br = m["board_rect"]
    if margin_mm is None:
        rg = m["region"]
    else:  # extract() adds the clear air AND the band to the board bbox
        d = float(margin_mm) + float(margin_mm if pml_mm is None else pml_mm)
        rg = {"x0": br["x0"] - d, "x1": br["x1"] + d,
              "y0": br["y0"] - d, "y1": br["y1"] + d}
    ax.plot([br["x0"], br["x1"], br["x1"], br["x0"], br["x0"]],
            [br["y0"], br["y0"], br["y1"], br["y1"], br["y0"]],
            color="0.25", lw=1.2)
    ax.plot([rg["x0"], rg["x1"], rg["x1"], rg["x0"], rg["x0"]],
            [rg["y0"], rg["y0"], rg["y1"], rg["y1"], rg["y0"]],
            "--", color="0.6", lw=0.8)

    fs = 7 if compact else 9
    for p in m["ports"]:
        hw, hl = p["width"] / 2.0, p["length"] / 2.0
        ax.fill([p["x"] - hl, p["x"] + hl, p["x"] + hl, p["x"] - hl],
                [p["y"] - hw, p["y"] - hw, p["y"] + hw, p["y"] + hw],
                color="lime", zorder=4)
        ax.annotate("P%d" % p["number"], (p["x"], p["y"]),
                    ha="center", va="bottom", xytext=(0, 4),
                    textcoords="offset points", fontsize=fs,
                    fontweight="bold", color="darkgreen", zorder=6)
    handles.append(Patch(color="lime", label="ports"))

    les = (m.get("lumped_elements", []) if show_lumped
           and m.get("settings", {}).get("lumped", True) else [])
    for e in les:
        (x0, y0), (x1, y1) = e["start"][:2], e["stop"][:2]
        ax.fill([x0, x1, x1, x0], [y0, y0, y1, y1], color="green", zorder=5)
        # The line from pad to pad. The box in the gap is less than 1 mm
        # long, and you cannot see it at the zoom of the board. The line
        # shows what the element connects.
        (px0, py0), (px1, py1) = e.get("pads", (e["start"][:2], e["stop"][:2]))
        ax.plot([px0, px1], [py0, py1], "-o", color="green", lw=2, ms=4,
                zorder=5)
        ax.annotate(e["ref"], (0.5 * (px0 + px1), 0.5 * (py0 + py1)),
                    ha="center", va="bottom", xytext=(0, 5),
                    textcoords="offset points", fontsize=fs,
                    fontweight="bold", color="darkgreen", zorder=6)
    if les:
        handles.append(Line2D([], [], color="green", lw=2, marker="o", ms=4,
                              label="R/L/C"))
    handles.append(Line2D([], [], color="0.25", lw=1.2, label="Board edge"))
    handles.append(Line2D([], [], ls="--", color="0.6", label="Domain"))

    ax.set_aspect("equal")
    if compact:
        # No legend. The colours are clear near the ports, which have
        # labels, and the legend used one third of the width of the board.
        # Put the frame on the domain, and do not let matplotlib scale it.
        # Then the thumbnail always has the same frame, and an annotation
        # that goes far out does not change it.
        pad = 0.03 * max(rg["x1"] - rg["x0"], rg["y1"] - rg["y0"])
        ax.set_xlim(rg["x0"] - pad, rg["x1"] + pad)
        ax.set_ylim(rg["y0"] - pad, rg["y1"] + pad)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
    else:
        ax.legend(handles=handles, loc="upper right", fontsize=8)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.set_title("Board Layout")


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, ports, default_outdir, lumped=(), preview=None,
                 packages=None, esr=None):
        wx.Dialog.__init__(self, parent, title="RFsim")
        # {the code of the package: the ESL in H} and {the type of the
        # part: the ESR in ohm}. board_reader keeps the two tables, thus
        # there is one source of truth. This module must not import it:
        # board_reader imports pcbnew.
        self._packages = dict(packages or {})
        self._esr = dict(esr or {})
        self._pkg_values = []
        self.part_rows = []
        self._build(ports, default_outdir, lumped, preview)

    def _build(self, ports, default_outdir, lumped, preview=None):
        top = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="RFsim v1.2")
        title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        top.Add(title, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 10)
        # A thumbnail of the simulation is better than a logo. It shows
        # which pads became ports, which R/L/C parts the plugin found, and
        # the dimension of the domain. The code makes it at the end of
        # _build, because it uses self.margin. If matplotlib stops with an
        # error, the icon replaces the thumbnail.
        self._preview_model = preview
        self._prev_fig = None
        if not self._add_preview(top):
            icon = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
            if os.path.isfile(icon):
                top.Add(wx.StaticBitmap(self, bitmap=wx.Bitmap(icon)),
                        0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 4)

        def section(label):
            box = wx.StaticBoxSizer(wx.VERTICAL, self, label)
            top.Add(box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
            return box

        def grid_in(box):
            g = wx.FlexGridSizer(cols=2, vgap=4, hgap=8)
            g.AddGrowableCol(1)
            box.Add(g, 0, wx.ALL | wx.EXPAND, 6)
            return g

        def row(g, label, ctrl, unit=None):
            g.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            if unit:
                h = wx.BoxSizer(wx.HORIZONTAL)
                h.Add(ctrl, 1, wx.EXPAND)
                h.Add(wx.StaticText(self, label=unit), 0,
                      wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
                g.Add(h, 0, wx.EXPAND)
            else:
                g.Add(ctrl, 0, wx.EXPAND)
            return ctrl

        fbox = section("Frequency")
        fs = wx.BoxSizer(wx.HORIZONTAL)
        fs.Add(wx.StaticText(self, label="Start:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.f_start = wx.TextCtrl(self, value="1.0", size=(60, -1))
        fs.Add(self.f_start, 1, wx.LEFT, 4)
        fs.Add(wx.StaticText(self, label="GHz"), 0,
               wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
        fs.Add(wx.StaticText(self, label="Stop:"), 0,
               wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 16)
        self.f_stop = wx.TextCtrl(self, value="6.0", size=(60, -1))
        fs.Add(self.f_stop, 1, wx.LEFT, 4)
        fs.Add(wx.StaticText(self, label="GHz"), 0,
               wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
        fbox.Add(fs, 0, wx.ALL | wx.EXPAND, 6)
        fg = grid_in(fbox)
        # The solver calculates the field dumps, which give the E/H
        # animation, and the far field at this frequency.
        self.f_field = row(fg, "Define at:",
                           wx.TextCtrl(self, value="2.4"), "GHz")

        pbox = section("Port")
        pg = grid_in(pbox)
        self.z0 = row(pg, "Port impedance:",
                      wx.TextCtrl(self, value="50"), "ohm")
        self.port_choices = []
        self.port_types = []  # the values of the choices of each port
        self.port_order = []
        self.port_excite = []
        # (the direction choice, the width field) of a pad that has no
        # track, or None for a pad that has one.
        self.port_feed = []
        nums = [str(i + 1) for i in range(len(ports))]
        # The rows of the ports use their own grid, as the lumped elements
        # do. Column 1 is empty and becomes wider. Thus the label stays at
        # the left and the controls stay at the right end.
        mid = wx.ALIGN_CENTER_VERTICAL
        prg = wx.FlexGridSizer(cols=6, vgap=6, hgap=8)
        prg.AddGrowableCol(1, 1)
        pbox.Add(prg, 0, wx.ALL | wx.EXPAND, 6)
        self.port_badges = []
        for i, p in enumerate(ports):
            num = wx.Choice(self, choices=nums)
            num.SetSelection(i)
            num.Enable(len(ports) > 1)
            num.SetToolTip("The number assigns the port.")
            # The type choice holds only the types that the geometry lets
            # you use. The label of the row names what the geometry does
            # not give. The width does not change, and it has space for the
            # CPW entry with its gap. The list can change with the feed
            # direction, and the control must not change its dimension with
            # it. _set_type_choices fills the control below.
            self.port_types.append([])
            ch = wx.Choice(self, size=(280, -1))
            exc = wx.CheckBox(self, label="Excite")
            exc.SetValue(True)
            exc.SetToolTip("Drive this port: one FDTD run for each "
                           "excited port.")
            # _refresh_port_badges puts the text in. The pad, the
            # footprint and the net go into the tooltip.
            label = wx.StaticText(self, label="")
            label.SetToolTip(p["label"])
            self.port_badges.append(p)
            # The feed controls. A pad with a track shows the direction and
            # the width that the track gives, locked (off). A pad with no
            # track can be on a line that the user drew as a shape or as a
            # polygon. An example is the feed of a patch antenna. The user
            # then gives the two values, and a de-embedded port becomes
            # possible.
            note = wx.BoxSizer(wx.HORIZONTAL)
            dch = wx.Choice(self, choices=["No Line", "+x (→)", "-x (←)",
                                           "+y (↑)", "-y (↓)"])
            wtc = wx.TextCtrl(self, size=(50, -1))
            if p.get("direction"):
                dch.SetSelection({(1, 0): 1, (-1, 0): 2, (0, 1): 3,
                                  (0, -1): 4}[tuple(p["direction"])])
                wtc.ChangeValue("%g" % (p.get("track_width")
                                        or min(p["width"], p["length"])))
                # **A control that is OFF shows no tooltip**, because
                # Windows sends it no mouse event. Thus these two controls
                # have a tooltip for the day when they become on. The
                # adjacent LABELS show the same text, and a user can get to
                # that text.
                locked_tip = "The track at this pad gives this value."
                for c in (dch, wtc):
                    c.SetToolTip(locked_tip)
                    c.Enable(False)
                self.port_feed.append(None)
            else:
                locked_tip = None
                dch.SetSelection(0)
                dch.SetToolTip("Direction of the feed line at this pad.")
                wtc.ChangeValue("%g" % min(p["width"], p["length"]))
                wtc.SetToolTip("Width of the feed line.")
                self.port_feed.append((dch, wtc))
                dch.Bind(wx.EVT_CHOICE, lambda evt, k=i: self._on_feed(k))
            feed_lbl = wx.StaticText(self, label="Feed:")
            width_lbl = wx.StaticText(self, label="Width:")
            if locked_tip:
                for lbl in (feed_lbl, width_lbl):
                    lbl.SetToolTip(locked_tip)
            note.Add(feed_lbl, 0, mid)
            note.Add(dch, 0, mid | wx.LEFT, 4)
            note.Add(width_lbl, 0, mid | wx.LEFT, 6)
            note.Add(wtc, 0, mid | wx.LEFT, 4)
            note.Add(wx.StaticText(self, label="mm"), 0, mid | wx.LEFT, 2)
            prg.Add(label, 0, mid)
            prg.Add((0, 0))  # the empty column that becomes wider
            prg.Add(note, 0, mid)
            prg.Add(ch, 0, mid)
            prg.Add(num, 0, mid)
            prg.Add(exc, 0, mid | wx.LEFT, 12)
            self.port_choices.append(ch)
            self.port_order.append(num)
            self.port_excite.append(exc)
            self._set_type_choices(i, p)
            # The user can change the number of a port. Thus the label
            # must follow it, or it tells a number that is not correct.
            num.Bind(wx.EVT_CHOICE, self._on_port_number)
        self._port_badge_ctrls = [prg.GetItem(6 * i).GetWindow()
                                  for i in range(len(ports))]
        self._refresh_port_badges()

        # (ref, the Model checkbox, the package choice, the ESL, the ESR)
        self.para_rows = []
        if lumped:
            # These controls have their own box, because they are not a
            # part of the port. There is no checkbox for all the parts.
            # Each row has its own "Model" checkbox, as the "Excite"
            # checkbox of a port.
            lbox = section("Lumped Elements")
            # One row for each part. The code reads the package from the
            # name of the footprint. A name that has no code gives
            # "Custom", and the user then puts in the values. "No
            # parasitics" gives an ideal element. Thus each row can go off
            # by itself, and there is no checkbox for all of them. The
            # choice SHOWS "0603 Package", but the value that goes into
            # model.json stays "0603". board_reader reads that code, and
            # the log of the solver prints it. Thus the labels and the
            # values are two lists, and _pkg_of() changes one into the
            # other. "Custom" and "No parasitics" show their own name.
            names = sorted(self._packages) + [CUSTOM_PKG, NO_PARASITICS]
            self._pkg_values = names
            labels = ["%s Package" % v for v in sorted(self._packages)]
            labels += [CUSTOM_PKG, NO_PARASITICS]
            # One grid for all the rows, and not one sizer for each row.
            # Thus each part of a row stays in a column that agrees from
            # row to row. A row reads as a sentence:
            #
            #   Element "R1"  [Resistor v]  Resistance: [50 v] ohm
            #       ... Parasitics: [0402 Package v] ESR: [] ESL: [] [x] Model
            #   Element "D1"  [Series RLC v]  R: [] ohm  L: [] nH  C: [] pF
            #       ...                                              [x] Model
            #
            # Column 3 is an empty column that becomes WIDER. Thus the part
            # stays at the left, and the parasitics stay at the right end.
            #
            # The rows go into a SCROLLED window, and their parent is that
            # window and not the dialog. Each row is about 29 px tall. Thus
            # a board with many parts made a dialog that was taller than
            # the screen. With one part, the dialog was 1053 px on a screen
            # of 1920x1080, which is the full height. _fit_rows gives the
            # window its height limit after the code makes the rows.
            #
            # The parent is the static box and not the dialog. wx gives a
            # warning for a window of a wxStaticBoxSizer that is a child of
            # the dialog. A scrolled window is a container, thus it has no
            # such warning.
            self.part_area = wx.ScrolledWindow(lbox.GetStaticBox(),
                                               style=wx.VSCROLL)
            self.part_area.SetScrollRate(0, 10)
            lg = wx.FlexGridSizer(cols=16, vgap=6, hgap=8)
            lg.AddGrowableCol(3, 1)
            self.part_area.SetSizer(lg)
            lbox.Add(self.part_area, 0, wx.ALL | wx.EXPAND, 6)
            pane = self.part_area
            mid = wx.ALIGN_CENTER_VERTICAL
            for e in lumped:
                i = len(self.para_rows)
                pkg = e.get("package")
                kind = e.get("type") or None
                cb = wx.CheckBox(pane, label="Model")
                # When the board does not give the type of a part, the part
                # starts OFF. Thus a diode, a ferrite bead or a footprint
                # of your own changes no simulation until the user selects
                # a type and gives a value.
                cb.SetValue(kind is not None)
                ch = wx.Choice(pane, choices=labels)
                # A package that the code did not read gives "Custom" for a
                # part when the board gives the type. The parasitics of an
                # R, an L or a C are on by default, and _ESL_DEFAULT_NH is
                # the value. A part with NO type gets **"No parasitics"**.
                # The code knows nothing about its body, thus it must not
                # make an ESL for it.
                start_pkg = (pkg if pkg in names
                             else CUSTOM_PKG if kind else NO_PARASITICS)
                ch.SetSelection(names.index(start_pkg))
                esl0 = "%g" % (1e9 * (e.get("esl") or 0.0))
                esr0 = "%g" % (e.get("esr") or 0.0)
                if start_pkg == NO_PARASITICS:
                    esl0 = esr0 = "0"
                esl = wx.TextCtrl(pane, value=esl0, size=(55, -1))
                esr = wx.TextCtrl(pane, value=esr0, size=(55, -1))
                # The self-resonance of an inductor. It starts at 0, which
                # means no self-resonance: no table gives the
                # self-capacitance of a winding, and the code must not
                # invent one.
                srf = wx.TextCtrl(pane, value="%g" % (1e-9 * (e.get("srf")
                                                              or 0.0)),
                                  size=(55, -1))
                srf.SetToolTip("Self-resonant frequency of the inductor, "
                               "from its datasheet. 0 = no self-resonance.")
                # The refdes gives the type and the Value field gives the
                # number, and the row SHOWS what the parser read. But the
                # two controls stay open. The user knows the part, and the
                # board does not always tell what it is. A refdes that
                # names no type starts at "Unknown" with an empty value.
                # The cause is that the Value field of a diode has a part
                # number and not a quantity.
                kinds = wx.Choice(pane, choices=[UNKNOWN_KIND]
                                  + list(KIND_NAMES.values()), size=(110, -1))
                kinds.SetSelection(KIND_ORDER.index(kind) if kind in KIND_ORDER
                                   else 0)
                # The unit of the field does not change (ohm, nH or pF), as
                # in the ESR and the ESL fields. Thus the user gives a
                # number and no prefix. The unit follows only the TYPE, and
                # not the dimension of the value.
                value = wx.TextCtrl(pane, value=_entry_text(kind,
                                                            e.get("value")),
                                    size=(90, -1))
                value.Enable(kind is not None)
                qty = wx.StaticText(pane, label=_qty_label(kind))
                uni = wx.StaticText(pane, label=ENTRY_UNITS.get(kind, ""))
                # **The value and its label share ONE cell with the three
                # fields of a series RLC**, and the type shows one of the
                # two sets. Columns of their own cannot do it, because a
                # column is as wide as its widest row. The R, L and C of
                # one row then push the value field of all the other rows
                # away from its label. The label gets the width of the
                # longest quantity. Thus the value fields continue to agree
                # from row to row.
                qty.SetMinSize((max(pane.GetTextExtent(_qty_label(k))[0]
                                    for k in KIND_ORDER), -1))
                single = wx.BoxSizer(wx.HORIZONTAL)
                single.Add(qty, 0, mid)
                single.Add(value, 0, mid | wx.LEFT, 8)
                single.Add(uni, 0, mid | wx.LEFT, 8)
                triple = wx.BoxSizer(wx.HORIZONTAL)
                rlc = {}
                for k, _ in RLC_FIELDS:
                    field = wx.TextCtrl(pane, value="0", size=(55, -1))
                    field.SetToolTip("0 leaves %s out of the part." % k)
                    if rlc:
                        triple.AddSpacer(10)
                    triple.Add(wx.StaticText(pane, label="%s:" % k), 0, mid)
                    triple.Add(field, 0, mid | wx.LEFT, 4)
                    triple.Add(wx.StaticText(pane, label=ENTRY_UNITS[k]), 0,
                               mid | wx.LEFT, 4)
                    rlc[k] = field
                area = wx.BoxSizer(wx.HORIZONTAL)
                area.Add(single, 0, mid)
                area.Add(triple, 0, mid)
                # The cell keeps ONE width for the two sets, thus a change
                # of the type does not change the width of the rows.
                area.SetMinSize((max(single.CalcMin()[0],
                                     triple.CalcMin()[0]), -1))
                area.Show(triple, False)
                lg.Add(wx.StaticText(pane, label='Element "%s"' % e["ref"]),
                       0, mid)
                lg.Add(kinds, 0, mid)
                lg.Add(area, 0, mid | wx.LEFT, 6)
                lg.Add((0, 0))  # the empty column that becomes wider
                para_lbl = wx.StaticText(pane, label="Parasitics:")
                lg.Add(para_lbl, 0, mid)
                lg.Add(ch, 0, mid)
                # The controls that a series RLC row hides.
                para_ctrls = [para_lbl, ch]
                # R before L, in the sequence of "RLC". There is no third
                # field: a series capacitance is not a parasitic of
                # these parts.
                srf_ctrls = []
                for label, ctrl, unit in (("ESR:", esr, "ohm"),
                                          ("ESL:", esl, "nH"),
                                          (SRF_LABEL, srf, SRF_UNIT)):
                    p_lbl = wx.StaticText(pane, label=label)
                    p_uni = wx.StaticText(pane, label=unit)
                    lg.Add(p_lbl, 0, mid | wx.LEFT, 6)
                    lg.Add(ctrl, 0, mid)
                    lg.Add(p_uni, 0, mid)
                    # The SRF keeps a list of its own. `para_ctrls` holds
                    # what a Series RLC row hides, and the SRF hides for
                    # all types but an inductor.
                    if ctrl is srf:
                        srf_ctrls += [p_lbl, ctrl, p_uni]
                    else:
                        para_ctrls += [p_lbl, ctrl, p_uni]
                lg.Add(cb, 0, mid | wx.LEFT, 12)
                # A preset writes the ESL with ChangeValue, which sends no
                # EVT_TEXT. Thus the choice stays on the package. An edit
                # by the user moves the choice to Custom. The substrate
                # presets use the same procedure.
                ch.Bind(wx.EVT_CHOICE, lambda evt, k=i: self._on_package(k))
                for c in (esl, esr):
                    c.Bind(wx.EVT_TEXT,
                           lambda evt, k=i: self._on_para_edit(k, evt))
                kinds.Bind(wx.EVT_CHOICE, lambda evt, k=i: self._on_kind(k))
                # The value of an inductor changes the timestep, thus the
                # warning must follow the field.
                value.Bind(wx.EVT_TEXT,
                           lambda evt: (self._update_lumped_warning(),
                                        evt.Skip()))
                # The L of a series RLC changes the timestep in the same
                # manner.
                for field in rlc.values():
                    field.Bind(wx.EVT_TEXT,
                               lambda evt: (self._update_lumped_warning(),
                                            evt.Skip()))
                self.para_rows.append((e["ref"], cb, ch, esl, esr))
                # The controls of the PART itself. They stay adjacent to
                # para_rows, thus the code that reads the parasitics does
                # not change. "esl0" and "esr0" are the values that come
                # back when a row goes out of "No parasitics". "No
                # parasitics" replaces them with 0.
                self.part_rows.append({"ref": e["ref"], "kind": kinds,
                                       "value": value, "qty": qty,
                                       "unit": uni, "last_pkg": start_pkg,
                                       "esl0": esl0, "esr0": esr0,
                                       "area": area, "single": single,
                                       "triple": triple, "rlc": rlc,
                                       "srf": srf, "srf_ctrls": srf_ctrls,
                                       "para_ctrls": para_ctrls})
                for ctrl in srf_ctrls:
                    ctrl.Show(kind == "L")
            # A lumped inductor makes the FDTD not stable at the full
            # Courant step. Thus the runner sets the timestep to
            # `solverenv.time_step_factor` of it. It divides the step limit
            # by the same value for the same simulated time. The run time
            # increases with it. Before this label, no text told the user.
            # A user who typed 100 nH got a run that was 20 times longer
            # with no message. The parent is the static box, as for the
            # window of the rows: one sizer cannot hold two different
            # parents.
            self.lumped_warn = wx.StaticText(lbox.GetStaticBox(), label="")
            self.lumped_warn.SetForegroundColour(wx.Colour(150, 90, 0))
            lbox.Add(self.lumped_warn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sbox = section("Substrate")
        sg = grid_in(sbox)
        self.preset = row(sg, "Presets:", wx.Choice(
            self, choices=[p[0] for p in SUBSTRATE_PRESETS]))
        self.preset.SetToolTip(
            "%s uses the board's own stackup, layer by layer.\n"
            "Rogers: stripline = process Dk, microstrip = design Dk."
            % BOARD_PRESET)
        # The default values are the FR-4 preset: refer to
        # SUBSTRATE_PRESETS. 1.6 mm and 35 um (1 oz) go with them.
        self.er = row(sg, "er:", wx.TextCtrl(self, value="4.5"))
        self.tand = row(sg, "Loss tangent:", wx.TextCtrl(self, value="0.02"))
        self.h = row(sg, "Substrate thickness:",
                     wx.TextCtrl(self, value="1.6"), "mm")
        self.cu_t = row(sg, "Copper thickness:",
                        wx.TextCtrl(self, value="0.035"), "mm")
        self._sub_fields = (self.er, self.tand, self.h, self.cu_t)
        # **The board sets the start.** A board with a `(stackup ...)`
        # block in its file starts at `BOARD_PRESET`. Thus a Rogers board
        # does not simulate as FR4 with no message. A board with no stackup
        # starts at FR-4, as before: the fallback values of `board_reader`
        # must NOT look like the board.
        #
        # The dialog does not READ the file. `preview` is the model of
        # `board_reader.extract` with no substrate, thus its stackup is the
        # stackup of the board.
        #
        # An earlier version filled the four fields from the file and kept
        # them OPEN, with a label below them that named the source. The
        # code got that version, and the owner removed it again on
        # 2026-08-05. At this time, the fields are READ-ONLY with this
        # preset. Thus a value from the board cannot look like a value that
        # the user typed.
        self._board_substrate = _board_substrate_text(preview)
        self._saved_substrate = [c.GetValue() for c in self._sub_fields]
        self.preset.SetSelection(0 if self._board_substrate else 1)
        self._apply_preset()
        # `model["stackup_source"]` stays in the model: it tells a reader
        # of model.json where the substrate came from.
        self.preset.Bind(wx.EVT_CHOICE, self._on_preset)
        for c in (self.er, self.tand):
            c.Bind(wx.EVT_TEXT, self._on_substrate_edit)

        rbox = section("Simulation")
        rg = grid_in(rbox)
        cpus = os.cpu_count() or 1
        self.threads = row(rg, "CPU threads:", wx.Choice(
            self, choices=["Auto"] + [str(i) for i in range(1, cpus + 1)]))
        self.threads.SetSelection(0)
        self.threads.SetToolTip("Threads for the FDTD engine.")
        self.mesh = row(rg, "Mesh resolution:", wx.Choice(
            self, choices=[m.capitalize() for m in MESH_LEVELS]))
        self.mesh.SetSelection(1)
        self.margin = row(rg, "Domain margin:", wx.SpinCtrlDouble(
            self, min=2.0, max=50.0, initial=4.0, inc=0.5), "mm")
        # A structure with a high Q rings for a long time. The run then
        # stops at the step limit before the energy decreases to the end
        # criteria, and the S-parameters are not correct. Before this, the
        # two limits were constant at 300k and 1e-4.
        #
        # The three fields go on ONE row, as the two frequencies do. Three
        # rows of the grid make the dialog 84 px taller. With one part, it
        # is 1053 px tall, and a screen of 1920x1080 has about 1040 px of
        # client area: refer to MAX_PART_ROWS.
        self.max_steps = wx.TextCtrl(self, value="300000", size=(70, -1))
        self.max_steps.SetToolTip(
            "The run stops at this number of timesteps.")
        self.end_crit = wx.TextCtrl(self, value="1e-4", size=(55, -1))
        self.end_crit.SetToolTip(
            "The run stops when the energy comes down to this part of "
            "its maximum.")
        # An empty field gives None, and the runner then selects the
        # value itself from the largest inductance in the model.
        self.tsf = wx.TextCtrl(self, value="", size=(55, -1))
        self.tsf.SetToolTip(
            "The portion of the Courant time step used by the "
            "simulation. Leave this field empty to use the "
            "automatically calculated value.")
        lim = wx.BoxSizer(wx.HORIZONTAL)
        for label, ctrl in (("Max steps:", self.max_steps),
                            ("End criteria:", self.end_crit),
                            ("Timestep:", self.tsf)):
            if lim.GetChildren():
                lim.AddSpacer(12)
            lim.Add(wx.StaticText(self, label=label), 0,
                    wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
            lim.Add(ctrl, 0, wx.ALIGN_CENTER_VERTICAL)
        rg.Add(wx.StaticText(self, label="Run limits:"), 0,
               wx.ALIGN_CENTER_VERTICAL)
        rg.Add(lim, 0, wx.EXPAND)
        self.outdir = row(rg, "Output directory:", wx.DirPickerCtrl(
            self, path=default_outdir, style=wx.DIRP_USE_TEXTCTRL))

        run = wx.Button(self, wx.ID_OK, "Run Simulation")
        # **The Cancel button is not decoration: it stops a run.** Before,
        # the dialog had only the Run button, and the X of the title bar
        # then started the simulation. The default close handler of
        # wxWidgets looks for a button with wxID_CANCEL, then wxID_OK. It
        # sends a click to the first one that it finds. With no Cancel
        # button, it clicked Run. The EVT_CLOSE below makes the answer of
        # this dialog free of that procedure.
        cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
        # The rows must have their dimensions before the dialog gets its
        # own.
        self._fit_rows()
        # **ALL the dialog scrolls.** From 2026-08-05, the rows of the
        # parts scrolled. That stopped the dialog from an increase with no
        # limit, but it did not make it fit. The dialog is 1084 px tall
        # with ONE part. A screen of 1920x1080 gives about 1040 px of
        # client area. Thus the Run button was below the edge of the screen
        # on a usual machine.
        #
        # Each control above is a child of the dialog. Thus this moves them
        # into a scrolled body after the code makes them. This is easier
        # than a change to each of the 60 constructors. The two buttons
        # stay OUT OF the body. A button that scrolls out of view is the
        # defect that this corrects.
        body = wx.ScrolledWindow(self, style=wx.VSCROLL)
        body.SetScrollRate(0, 12)
        for child in list(self.GetChildren()):
            if child is not body and child is not run and child is not cancel:
                child.Reparent(body)
        body.SetSizer(top)
        body.FitInside()
        # A wx.ScrolledWindow does NOT give the dimensions of its sizer as
        # its best dimensions. Thus `Fit()` without other steps makes the
        # dialog as small as possible. Give the body the dimensions of the
        # content for the fit, and make it small again immediately after.
        # `_fit_to_screen` must be free to cut the height, and `SetSize`
        # cannot go below the minimum dimensions of a window.
        content = top.GetMinSize()
        body.SetInitialSize(content)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(body, 1, wx.EXPAND)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(run, 0, wx.RIGHT, 8)
        buttons.Add(cancel, 0)
        outer.Add(buttons, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 12)
        self.SetSizer(outer)
        self.Fit()
        body.SetMinSize((content.GetWidth(), 120))
        self.SetMinSize((520, 240))
        self._fit_to_screen()
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        # The preview uses self.margin and the rows. Thus make it last, and
        # keep it in agreement with the two controls.
        if self._prev_fig is not None:
            for evt in (wx.EVT_SPINCTRLDOUBLE, wx.EVT_TEXT):
                self.margin.Bind(evt, self._on_preview_change)
            # The depth of the PML band is 8 cells of the mesh step. Thus
            # the domain of the preview also follows these three. `_pml_mm`
            # reads them.
            for c in (self.f_stop, self.er):
                c.Bind(wx.EVT_TEXT, self._on_preview_change)
            self.mesh.Bind(wx.EVT_CHOICE, self._on_preview_change)
            self._redraw_preview()
        if self.para_rows:
            for _, cb, _, _, _ in self.para_rows:
                cb.Bind(wx.EVT_CHECKBOX, self._on_lumped)
            # The open rule and the rule of the body read the sweep and z0.
            # Thus the label also follows them.
            for c in (self.f_start, self.f_stop, self.z0):
                c.Bind(wx.EVT_TEXT, lambda evt: (
                    self._update_lumped_warning(), evt.Skip()))
            self._on_lumped(None)

    def _add_preview(self, top):
        """Make the thumbnail of the board layout.

        The function gives False if it cannot make the thumbnail. Then
        the caller shows the icon.
        """
        m = self._preview_model
        if not m or not m.get("polygons"):
            return False
        try:
            _use_wxagg()
            from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg
            from matplotlib.figure import Figure
            # No constrained layout: _redraw_preview puts the axes on the
            # full figure, because there are no labels that use space.
            self._prev_fig = Figure(figsize=(4.9, 2.9))
            self._prev_canvas = FigureCanvasWxAgg(self, -1, self._prev_fig)
            # FigureCanvasWxAgg gives the native pixel dimensions of the
            # figure as its minimum dimensions for wx, and this cuts the
            # figure. Thus give the canvas small minimum dimensions.
            self._prev_canvas.SetMinSize((480, 285))
        except Exception:
            self._prev_fig = None
            return False
        top.Add(self._prev_canvas, 0, wx.ALIGN_CENTER_HORIZONTAL
                | wx.TOP | wx.LEFT | wx.RIGHT, 4)
        return True

    def _on_preview_change(self, evt):
        self._redraw_preview()
        evt.Skip()

    def _on_port_number(self, evt):
        self._refresh_port_badges()
        evt.Skip()

    def _on_feed(self, k):
        """Update the port types that a manual feed direction lets you use.

        The gap of each candidate direction comes from extract(), in
        port["gaps"]. An example is a CPW that the user drew: a center
        line from a polygon, and not from a track. It gets the CPW type for
        a direction that has copper at the two sides. The detector reads only
        the geometry, not the nets: the user must make sure that the copper
        is ground.
        """
        p = self.port_badges[k]
        dch, _ = self.port_feed[k]
        sel = dch.GetSelection()
        key = ("+x", "-x", "+y", "-y")[sel - 1] if sel > 0 else None
        gap = (p.get("gaps") or {}).get(key) if key else None
        self._set_type_choices(k, dict(p, direction=[1, 0] if key else None,
                                       gap=gap))

    def _set_type_choices(self, k, p):
        """Fill the type choice of row k for the geometry in `p`.

        The selection stays on the same type when the new list has it. A
        type that went away changes to Lumped. The control goes off when
        Lumped is the one entry.
        """
        rows = _port_choices(p)
        ch = self.port_choices[k]
        old = self.port_types[k]
        cur = old[ch.GetSelection()] if old and ch.GetSelection() >= 0 \
            else "lumped"
        self.port_types[k] = [v for _, v in rows]
        ch.Set([label for label, _ in rows])
        ch.SetSelection(self.port_types[k].index(cur)
                        if cur in self.port_types[k] else 0)
        ch.Enable(len(rows) > 1)

    def _feed_of(self, i):
        """Give (direction, width in mm) of a manual feed, or give None.

        The result is None for a pad that has a track, for "No Line",
        and for a width that is not a positive number.
        """
        fc = self.port_feed[i]
        if not fc:
            return None
        dch, wtc = fc
        sel = dch.GetSelection()
        if sel <= 0:
            return None
        try:
            w = float(wtc.GetValue())
        except ValueError:
            return None
        if w <= 0:
            return None
        return ([[1, 0], [-1, 0], [0, 1], [0, -1]][sel - 1], w)

    def _refresh_port_badges(self):
        """Put "Port N" and its problem tag into the label of each port.

        N is the number that the choice of that row gives at this time, and
        not the number of the selection. Two rows can have the same number
        for a short time; _on_ok refuses that. A tag names what stops all
        the de-embedded types: "[No Track]". A coplanar gap that is missing
        gets no tag. It stops only the CPW type, and a board that is not a
        CPW is the usual case. The type choice shows the measured gap, or
        it does not show the CPW entry.
        """
        for badge, num, p in zip(self._port_badge_ctrls, self.port_order,
                                 self.port_badges):
            label = "Port %d" % (num.GetSelection() + 1)
            if not p.get("direction"):
                label += " [No Track]"
            badge.SetLabel(label)
        self.Layout()

    def _any_modelled(self):
        """Tell if the model contains one lumped element or more."""
        return any(cb.GetValue() for _, cb, _, _, _ in self.para_rows)

    def _on_lumped(self, evt):
        """Keep the controls of each row in agreement with its checkboxes.

        Parasitics of a part that the model does not contain have no
        function. Thus the fields of a row go off with its Model checkbox.
        """
        for i, (_, cb, ch, esl, esr) in enumerate(self.para_rows):
            # A part that the model does not contain has no parasitics. "No
            # parasitics" makes an ideal element, thus its two fields also
            # have no function.
            on = cb.GetValue()
            ch.Enable(on)
            for c in (esl, esr):
                c.Enable(on and self._pkg_of(ch) != NO_PARASITICS)
            # The type stays on with the Model off. Thus the user can
            # select it first and model the part after it. The value must
            # have a type, because the unit comes from it.
            self.part_rows[i]["value"].Enable(
                on and self._kind_of(i) is not None)
            for field in self.part_rows[i]["rlc"].values():
                field.Enable(on)
        self._update_lumped_warning()
        self._redraw_preview()
        if evt is not None:
            evt.Skip()

    def _fit_rows(self):
        """Give the window of the part rows its height limit.

        The dialog was 1053 px tall with ONE part on a screen of 1920x1080,
        and each subsequent part added 29 px. Thus a board with 15 parts
        made a dialog that no screen shows. At this time, the window stops
        at MAX_PART_ROWS rows, or at one quarter of the screen if that is
        less. The other rows come with the scroll bar.
        """
        area = getattr(self, "part_area", None)
        if area is None:
            return
        rows = max(1, len(self.part_rows))
        best = area.GetSizer().GetMinSize()
        one = best.GetHeight() / float(rows)
        try:
            screen = wx.Display().GetClientArea().GetHeight()
        except Exception:
            screen = 1080
        cap = max(2.0 * one, min(MAX_PART_ROWS * one, 0.25 * screen))
        # The scroll bar uses some width. Thus the rows keep all their
        # columns when it comes into view.
        w = best.GetWidth() + (wx.SystemSettings.GetMetric(
            wx.SYS_VSCROLL_X) if best.GetHeight() > cap else 0)
        area.SetMinSize((w, int(min(best.GetHeight(), cap)) + 2))
        area.FitInside()
        self._one_row = one

    def _fit_to_screen(self):
        """Keep the dialog in the screen, and let the body scroll.

        `Fit()` gives the dialog the height of all its content. That is
        more than a screen of 1920x1080 gives, also with ONE part. This
        cuts the height to the client area of the display. The scrolled
        body then shows a scroll bar. The Run button stays in view, because
        it is out of that body.
        """
        try:
            area = wx.Display().GetClientArea()
        except Exception:
            return
        w, h = self.GetSize()
        # Keep a small space for the frame of the window itself.
        avail = max(240, area.GetHeight() - 40)
        if h > avail:
            self.SetSize((w, avail))
            self.Layout()

    def _lumped_limit(self):
        """Give (the timestep factor, the source, the value, the row).

        The factor is the SMALLEST factor of all the elements. The two laws
        are not the same. An inductance gives its factor through
        `solverenv.time_step_factor`. A resistance in a series branch gives
        its factor through `solverenv.series_r_factor`.
        `runner._time_step_factor` uses the smaller of the same two. Thus
        the number here and the run agree. The label and the refusal of
        `_on_ok` read THIS function.

        **Each candidate has the SOURCE of its value** (B49). The largest
        value on a usual board is the BODY of a package that the user did
        not type. A text that names a part that is not on the board is
        worse than no text.

        A factor of 1.0 comes back with no source and no row.
        """
        cand = [(1.0, "", "", -1)]
        for i, (ref, cb, _, _, _) in enumerate(self.para_rows):
            if not cb.GetValue():
                continue
            # An OPEN at all frequencies of the sweep is not in the grid.
            # Thus it sets no timestep: `runner._open_parts`.
            if self._open_over_sweep(i)[0]:
                continue
            kind = self._kind_of(i)
            if kind == RLC_KIND:
                # The three fields ARE the part: a series RLC gets no
                # package parasitics. A text that is not a number counts as
                # nothing here, and `_on_ok` refuses it.
                try:
                    v = self._rlc_values(i)
                except ValueError:
                    continue
                nh = 1e9 * (v["l"] or 0.0)
                cand.append((solverenv.time_step_factor(nh),
                             "The L of %s" % ref, "%g nH" % nh, i))
                # The R uses the series law only when the branch has more
                # than ONE component. `runner._le_topology` holds that
                # rule.
                if (v["r"] or 0) > 0 and sum(
                        x is not None for x in v.values()) > 1:
                    cand.append((solverenv.series_r_factor(v["r"]),
                                 "The R of %s" % ref,
                                 "%g ohm" % v["r"], i))
                continue
            if kind == "L":
                nh = 1e9 * (self._part_value(i) or 0.0)
                cand.append((solverenv.time_step_factor(nh),
                             "The inductor %s" % ref, "%g nH" % nh, i))
            # The ESL counts only where the element holds it. An inductor
            # has no ESL in its element. A body that does not change its
            # part stays out, as in `runner._time_step_rule`.
            comp = self._row_components(i) or {}
            body = comp.get("L", 0.0) if kind in ("R", "C") else 0.0
            cand.append((solverenv.time_step_factor(1e9 * body),
                         "The body of %s" % ref,
                         "%g nH" % (1e9 * body), i))
            # A part with a body ESL is a branch of two components. Thus a
            # RESISTOR then also uses the series law. The ESR of a
            # capacitor and the DCR of an inductor are some ohms at most.
            # `series_r_factor` gives 1.0 below 34 ohm, thus they cannot
            # set this minimum. For that cause, they stay out.
            if kind == "R" and body > 0:
                cand.append((solverenv.series_r_factor(
                    self._part_value(i) or 0.0),
                    "The resistor %s" % ref,
                    "%g ohm" % (self._part_value(i) or 0.0), i))
        return min(cand)

    def _row_components(self, i, whole=False):
        """Give the R, L and C that the runner puts in ONE element for row
        `i`, in SI. Give None for a text that is not a number.

        It is `runner._components` with the parasitics of the row: the ESL
        of a resistor or a capacitor, and the ESR of a capacitor or an
        inductor. "No parasitics" gives 0 for the two. The open rule of the
        dialog and of the runner must read the same branch. If not, the
        dialog can tell "open" for a part that the run puts in the grid.
        `whole` keeps a body that the run does not include.
        """
        _, cb, ch, esl, esr = self.para_rows[i]
        kind = self._kind_of(i)
        try:
            if kind == RLC_KIND:
                v = self._rlc_values(i)
                return {"R": v["r"], "L": v["l"], "C": v["c"]}
            val = self._part_value(i)
        except ValueError:
            return None
        if kind is None or val is None:
            return None
        comp = {kind: val}
        body_l = self._para_value(ch, esl, 1e-9) or 0.0
        body_r = self._para_value(ch, esr) or 0.0
        if kind in ("R", "C") and body_l > 0:
            comp["L"] = body_l
        if kind in ("C", "L") and body_r > 0:
            comp["R"] = body_r
        # **A body that does not change its part stays out**, as in
        # `runner._components`: the part then uses the classic path.
        sweep = None if whole else self._sweep()
        if sweep and solverenv.body_is_idle(comp, kind, *sweep[:2]):
            return {kind: val}
        return comp

    def _idle_body(self, i):
        """Give True when the run does not include the body of row `i`.

        `solverenv.body_is_idle` holds the rule. `_row_components` then
        gives only the part.
        """
        comp = self._row_components(i)
        full = self._row_components(i, whole=True)
        return bool(comp and full and len(full) > len(comp))

    def _sweep(self):
        """Give (f_start, f_stop, z0) in Hz and ohm, or give None for a
        text that is not a number or a sweep that is not a sweep."""
        try:
            fa = float(self.f_start.GetValue()) * 1e9
            fb = float(self.f_stop.GetValue()) * 1e9
            z0 = float(self.z0.GetValue())
        except ValueError:
            return None
        return (fa, fb, z0) if 0 < fa <= fb and z0 > 0 else None

    def _open_over_sweep(self, i):
        """Give (True, the smallest |Z| of the sweep) when the part of row
        `i` is an open circuit at ALL frequencies of the sweep.

        `solverenv.is_open` holds the rule, and the runner also reads it:
        **such a part does not go into the grid.** Its gap stays open. The
        EPC of an inductor stays without the part, because at GHz a choke
        on a board IS that capacitance. It costs no timestep.
        """
        comp = self._row_components(i)
        sweep = self._sweep()
        if not comp or not sweep:
            return False, 0.0
        fa, fb, z0 = sweep
        r, l, c = comp.get("R"), comp.get("L"), comp.get("C")
        return (solverenv.is_open(r, l, c, fa, fb, z0),
                solverenv.smallest_z(r, l, c, fa, fb))

    def _derived_steps(self):
        """Give the step limit that openEMS receives, or give None.

        `runner._max_timesteps` divides "Max steps" by the timestep factor.
        Thus the SIMULATED time does not change, but the count changes. A
        "Timestep factor" that the user gives is more important than the
        rule, in the runner and thus here.
        """
        try:
            steps = float(self.max_steps.GetValue())
            text = self.tsf.GetValue().strip()
            factor = float(text) if text else self._lumped_limit()[0]
        except ValueError:
            return None
        return steps / factor if 0 < factor < 1.0 else steps

    def _update_lumped_warning(self):
        """Show what a lumped inductor costs in run time.

        `solverenv.time_step_factor` gives the part of the Courant timestep
        that keeps the run stable. The runner divides the step limit by
        that part for the same simulated time. Thus 1/factor is the run
        time that the inductor costs, and the user must see it BEFORE the
        run and not after it. The dialog and `runner._time_step_factor`
        read the ONE function. If not, the number here and the run do not
        agree.
        """
        label = getattr(self, "lumped_warn", None)
        if label is None:
            return
        factor, source, value, row = self._lumped_limit()
        cost = 1.0 / factor
        text = ""
        # The rule starts to cost at 0.25 nH. A body ESL of 0603 to 2512
        # (0.35 to 0.90 nH) costs 1.2 to 1.9 times. Below 1.05, the number
        # rounds to "1.0 times longer", which is a warning that gives no
        # data. Thus the label starts at 0.28 nH.
        #
        # **ONE limit for all sources**, by a decision of the owner on
        # 2026-09-21. A body costs the same run time as a value that the
        # user typed, thus it must show the same warning. The words tell
        # which one it is.
        if cost >= 1.05:
            # **The COUNT of steps, and not only the multiplier** (P20).
            # "600 times longer" does not tell if the run is one hour or
            # one week long. The step limit that openEMS receives tells it.
            steps = self._derived_steps()
            # **"At most", because the count is the LIMIT** (P20). A run
            # stops immediately when its field becomes stable. The 90 uH
            # run of 2026-09-22 stopped after 1.03 million of its 180
            # million.
            text = ("%s (%s) divides the timestep by %.1f, thus the run "
                    "takes about %.1f times longer%s."
                    % (source, value, cost, cost,
                       "" if steps is None
                       else ": at most %s timesteps" % _count_text(steps)))
        # **Say what the run does with an open part**, because the user
        # typed a value that the grid will not hold.
        for i, (ref, cb, _, _, _) in enumerate(self.para_rows):
            if not cb.GetValue():
                continue
            is_open, z = self._open_over_sweep(i)
            if is_open:
                epc = self._epc_value(i)
                text += ("%s%s is an open circuit over this sweep (%s): the "
                         "run %s, and it costs no run time."
                         % ("\n" if text else "", ref, _ohm_text(z),
                            "keeps its EPC of %.3g pF alone" % (epc * 1e12)
                            if epc else "leaves its gap open"))
        # **Tell which bodies the run does not include** (P21): ONE line
        # for all of them, because a board can have many pull-ups.
        idle = [ref for i, (ref, cb, _, _, _) in enumerate(self.para_rows)
                if cb.GetValue() and self._idle_body(i)]
        if idle:
            text += ("%s%s: the run leaves out the parasitics of the body, "
                     "which move |Z| by %g%% or less over this sweep."
                     % ("\n" if text else "", ", ".join(idle),
                        100 * solverenv.PARASITIC_MIN))
        if label.GetLabel() != text:
            label.SetLabel(text)
            label.Wrap(560)
            self.Layout()

    def _pml_mm(self):
        """Give the depth of the PML band that the run will use, in mm.

        The band is 8 cells of the mesh step. Thus it follows the top of
        the sweep, the mesh preset and the largest er. The preview shows
        the domain with it. It reads the SAME rule as `extract`, which sets
        the dimension of the region. The runner, which puts the cells, also
        reads it.
        """
        if self.uses_board_stackup():
            eps = max(d["epsilon"] for d
                      in self._preview_model["dielectric_layers"])
        else:
            eps = float(self.er.GetValue())
        return solverenv.pml_depth(solverenv.mesh_res(
            float(self.f_stop.GetValue()) * 1e9, eps,
            MESH_LEVELS[self.mesh.GetSelection()]))

    def _redraw_preview(self):
        if self._prev_fig is None:
            return
        self._prev_fig.clear()
        ax = self._prev_fig.add_axes((0.01, 0.01, 0.98, 0.98))
        try:
            _draw_board(ax, self._preview_model, compact=True,
                        margin_mm=self.margin.GetValue(),
                        pml_mm=self._pml_mm(),
                        show_lumped=(not self.para_rows
                                     or self._any_modelled()))
        except Exception as e:  # the preview must not stop the dialog
            ax.set_axis_off()
            ax.text(0.5, 0.5, "preview unavailable\n%s" % e, ha="center",
                    va="center", fontsize=7, transform=ax.transAxes)
        self._prev_canvas.draw_idle()

    def uses_board_stackup(self):
        """Is the substrate the stackup of the board, and not the fields?"""
        return (SUBSTRATE_PRESETS[self.preset.GetSelection()][0]
                == BOARD_PRESET)

    def _on_preset(self, evt):
        """A preset that the USER selected. It can refuse the selection."""
        if self.uses_board_stackup() and not self._board_substrate:
            wx.MessageBox(
                "This board gives no stackup.\n\n"
                "Open Board Setup > Physical Stackup in the PCB editor, "
                "give the dielectric its er and its loss tangent, and "
                "SAVE the board. Or select a preset and type the values.",
                "RFsim", wx.ICON_INFORMATION)
            self.preset.SetSelection(1)  # FR-4
        self._apply_preset()

    def _apply_preset(self):
        """Put the values of the selected preset into the four fields.

        The fields are READ-ONLY with `BOARD_PRESET`, because the run does
        not use their text. It uses the stackup of the board, layer by
        layer. The text that the user typed comes back when the user
        selects a different preset.
        """
        name, er, tand = SUBSTRATE_PRESETS[self.preset.GetSelection()]
        board = name == BOARD_PRESET
        if board and self._board_substrate:
            if self._sub_fields[0].IsEnabled():  # keep what the user typed
                self._saved_substrate = [c.GetValue() for c in self._sub_fields]
            for c, txt in zip(self._sub_fields, self._board_substrate):
                c.ChangeValue(txt)
        elif not self._sub_fields[0].IsEnabled():   # the board preset ends
            for c, txt in zip(self._sub_fields, self._saved_substrate):
                c.ChangeValue(txt)
        for c in self._sub_fields:
            c.Enable(not board)
        # ChangeValue sends no EVT_TEXT, thus the preset stays selected.
        if er is not None:
            self.er.ChangeValue(str(er))
            self.tand.ChangeValue(str(tand))

    def _on_substrate_edit(self, evt):
        self.preset.SetSelection(len(SUBSTRATE_PRESETS) - 1)  # Custom
        evt.Skip()

    def _on_close(self, evt):
        """Close the dialog with NO run: the X of the title bar, Alt+F4.

        `EndModal` must have a modal dialog, thus the test of `IsModal`.
        The tests and `capture_windows.py` make this dialog, and they do
        not show it modally.
        """
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    def _on_ok(self, evt):
        try:
            fa, fb = float(self.f_start.GetValue()), float(self.f_stop.GetValue())
            fd = float(self.f_field.GetValue())
            z0 = float(self.z0.GetValue())
            # With BOARD_PRESET, the four fields hold the text of the
            # stackup. That text can be "3.48 / 4.5" for a board with two
            # dielectrics. `float()` cannot read that, and it does not have
            # to: the run uses the stackup and not the fields.
            if self.uses_board_stackup():
                er, tand, h, cu_t = 4.5, 0.02, 1.6, 0.035
            else:
                er, tand = float(self.er.GetValue()), float(self.tand.GetValue())
                h, cu_t = float(self.h.GetValue()), float(self.cu_t.GetValue())
            # A series RLC row hides its ESR and its ESL, and the model
            # does not use them. Thus this test does not read them.
            para = [(float(esl.GetValue()), float(esr.GetValue()))
                    for i, (_, _, _, esl, esr) in enumerate(self.para_rows)
                    if self._kind_of(i) != RLC_KIND]
            if (not (0 < fa < fb) or not (fa <= fd <= fb) or z0 <= 0
                    or er < 1 or tand < 0 or h <= 0 or cu_t <= 0
                    or any(a < 0 or b < 0 for a, b in para)):
                raise ValueError
        except ValueError:
            wx.MessageBox(
                "Check frequency / impedance / substrate values.\n"
                "('Define at' must lie inside the sweep range. "
                "ESL and ESR must be numbers, and not negative.)",
                "RFsim", wx.ICON_ERROR)
            return
        # A part that the user models must have a type and a value. When
        # the refdes does not give the type, the part starts with "Unknown"
        # and with its Model checkbox off. Thus this test speaks only when
        # the user turned that part on and gave it nothing.
        for i, r in enumerate(self.part_rows):
            if not self.para_rows[i][1].GetValue():
                continue
            if self._kind_of(i) is None:
                wx.MessageBox(
                    'Element "%s" has no type. Select Resistor, Capacitor, '
                    "Inductor or Series RLC, or clear its Model checkbox."
                    % r["ref"], "RFsim", wx.ICON_ERROR)
                return
            if self._kind_of(i) == RLC_KIND:
                # Each field is 0 or a positive number. 0 (or an empty
                # field) removes that component from the part, thus a
                # series C of 0 pF is NOT an open circuit here. At least
                # one component must stay.
                try:
                    vals = list(self._rlc_values(i).values())
                except ValueError:
                    vals = [-1.0]
                if (all(v is None for v in vals)
                        or any(v is not None and v < 0 for v in vals)):
                    wx.MessageBox(
                        'Element "%s" is a Series RLC: give R in ohm, L in '
                        "nH or C in pF as positive numbers. 0 leaves that "
                        "component out, and at least one must stay."
                        % r["ref"], "RFsim", wx.ICON_ERROR)
                    return
                continue
            v = self._part_value(i)
            if v is None or v <= 0:
                wx.MessageBox(
                    'Element "%s" needs a value in %s: a positive number.'
                    % (r["ref"], ENTRY_UNITS[self._kind_of(i)]),
                    "RFsim", wx.ICON_ERROR)
                return
            # The SRF field is 0 or a positive number. 0 (or an empty
            # field) keeps the inductor as it was, with no self-resonance.
            text = r["srf"].GetValue().strip()
            if self._kind_of(i) == "L" and text:
                try:
                    ok = float(text) >= 0
                except ValueError:
                    ok = False
                if not ok:
                    wx.MessageBox(
                        'Element "%s": the SRF is the self-resonant '
                        "frequency of the inductor in GHz, from its "
                        "datasheet. Give a positive number, or 0 to model "
                        "the part with no self-resonance." % r["ref"],
                        "RFsim", wx.ICON_ERROR)
                    return
        order = [c.GetSelection() for c in self.port_order]
        if sorted(order) != list(range(len(order))):
            wx.MessageBox("Each pad needs a unique port number.",
                          "RFsim", wx.ICON_ERROR)
            return
        if not any(cb.GetValue() for cb in self.port_excite):
            wx.MessageBox("Select at least one port to excite.",
                          "RFsim", wx.ICON_ERROR)
            return
        # A de-embedded type on a manual feed must have a direction and a
        # width. The type list removes the de-embedded types when the
        # direction goes back to "No Line". Thus this test finds only a
        # width that is not a number.
        for i, (ch, vals) in enumerate(zip(self.port_choices,
                                           self.port_types)):
            if (self.port_feed[i] and vals[ch.GetSelection()] != "lumped"
                    and self._feed_of(i) is None):
                wx.MessageBox(
                    "Port %d: a de-embedded port needs a feed direction "
                    "and a positive width in mm."
                    % (self.port_order[i].GetSelection() + 1),
                    "RFsim", wx.ICON_ERROR)
                return
        # The three limits of the run. An empty timestep factor is
        # correct: the runner then selects the value itself.
        for ctrl, name, low, high in (
                (self.max_steps, "Max timesteps", 100, 1e9),
                (self.end_crit, "End criteria", 1e-12, 1.0),
                (self.tsf, "Timestep factor", 1e-3, 1.0)):
            text = ctrl.GetValue().strip()
            if ctrl is self.tsf and not text:
                continue
            try:
                v = float(text)
            except ValueError:
                v = None
            if v is None or not low <= v <= high:
                wx.MessageBox("%s must be a number from %g to %g."
                              % (name, low, high), "RFsim", wx.ICON_ERROR)
                return
        # **A run that cannot end must not start** (P20). The runner
        # divides the step limit by the timestep factor for the same
        # simulated time. Thus the number to examine is the number that
        # openEMS receives, and not the number in the field. An L of
        # 90000 nH causes 180 million steps for the 300000 of the default.
        # That is about 135 hours on the SMALL board of the rigs.
        #
        # A user who wants such a run can also give a "Timestep factor",
        # which `_derived_steps` reads. That user then accepts the risk of
        # a run that diverges.
        steps = self._derived_steps()
        if steps is not None and steps > MAX_DERIVED_STEPS:
            factor, source, value, row = self._lumped_limit()
            text = self.tsf.GetValue().strip()
            if text:
                factor = float(text)
            # **The alternative in the message keeps the SAFE timestep**
            # (P20, 2026-09-23). Before, the message pointed to the
            # "Timestep factor". For a large L, each factor that is
            # sufficient for this limit is on the stability boundary of
            # that L, and the run then diverges. A smaller "Max steps"
            # keeps the factor of the rule. The run also stops immediately
            # when its field becomes stable.
            fit = int(MAX_DERIVED_STEPS * factor)
            wx.MessageBox(
                "This run can take up to %s timesteps, and RFsim does not "
                "start a run whose limit is over %s.\n\n"
                "%s (%s) takes the timestep to 1/%.0f of the Courant "
                "step, thus the %s steps that you asked for become a "
                "limit of %s for the same simulated time.\n\n"
                "The limit is not the length. A run stops as soon as "
                "its field settles: a 90 uH run stopped after 1.03 "
                "million of its 180 million steps.\n\n"
                "To start it, give \"Max steps\" %d or less. That keeps "
                "the safe timestep, and the run still stops on its own "
                "when it settles."
                % (_count_text(steps), _count_text(MAX_DERIVED_STEPS),
                   source or "A lumped element", value, 1.0 / factor,
                   _count_text(float(self.max_steps.GetValue())),
                   _count_text(steps), fit),
                "RFsim", wx.ICON_ERROR)
            return
        evt.Skip()

    def _pkg_of(self, ch):
        """Give the package VALUE of a choice, and not its label.

        The choice shows "0603 Package"; the value is "0603".
        """
        return self._pkg_values[ch.GetSelection()]

    def _para_value(self, ch, ctrl, scale=1.0):
        """Give the value of a field in SI, or give 0 for "No parasitics".

        The text of the field does not change. Thus the value of the user
        comes back when the row gets a package again.
        """
        if self._pkg_of(ch) == NO_PARASITICS:
            return 0.0
        return float(ctrl.GetValue()) * scale

    def _on_package(self, i):
        """Put the values of the package into the fields of that row.

        Each write uses ChangeValue, which sends no EVT_TEXT. Thus the
        choice stays where the user put it. SetValue sends the event, and
        _on_para_edit then moves the row to "Custom".

        "No parasitics" writes 0 into the two fields and makes them grey.
        Thus the row shows what the solver gets: an ideal element. The
        values come back when the row gets a package again. The ESL comes
        from the preset, and the ESR comes from the type of the part.
        """
        r = self.part_rows[i]
        _, _, ch, esl, esr = self.para_rows[i]
        pkg = self._pkg_of(ch)
        if pkg == NO_PARASITICS:
            for c in (esl, esr):
                c.ChangeValue("0")
        else:
            if r["last_pkg"] == NO_PARASITICS:   # the row comes back
                esl.ChangeValue(r["esl0"])
                esr.ChangeValue(r["esr0"])
            if pkg in self._packages:
                esl.ChangeValue("%g" % (1e9 * self._packages[pkg]))
        r["last_pkg"] = pkg
        self._on_lumped(None)   # "No parasitics" turns the fields off

    def _kind_of(self, i):
        """Give the type letter of a row, or None for "Unknown"."""
        r = self.part_rows[i]
        return KIND_ORDER[r["kind"].GetSelection()]

    def _on_kind(self, i):
        """The user selected the type of a part.

        The label of the quantity and the unit follow the type, and the
        field for the value goes on. "Unknown" turns it off again, and the
        text stays. The value of the user comes back if the user selects a
        type again. The NUMBER does not change with the type, thus 50
        becomes 50 ohm, 50 nH or 50 pF. The adjacent unit tells which one.

        "Series RLC" shows its three fields, and not the value. It hides
        the parasitics. The texts stay in the hidden controls. Thus when
        the user selects a different type again, the row comes back as it
        was.
        """
        r = self.part_rows[i]
        kind = self._kind_of(i)
        r["qty"].SetLabel(_qty_label(kind))
        r["unit"].SetLabel(ENTRY_UNITS.get(kind, ""))
        r["value"].Enable(kind is not None
                          and self.para_rows[i][1].GetValue())
        # The ESR of the body comes from the TYPE of the part. This is the
        # same as for a part when the board gives the type. A row that is
        # on "No parasitics" keeps its 0. It gets this value when it goes
        # back to a package. ChangeValue sends no EVT_TEXT, thus the
        # package choice of that row does not change.
        r["esr0"] = "%g" % self._esr.get(kind, 0.0)
        if self._pkg_of(self.para_rows[i][2]) != NO_PARASITICS:
            self.para_rows[i][4].ChangeValue(r["esr0"])
        self._show_kind(i)
        for field in r["rlc"].values():
            field.Enable(self.para_rows[i][1].GetValue())
        self._update_lumped_warning()
        self.Layout()

    def _part_value(self, i):
        """Give the value of a row in SI units, or give None.

        All rows are open, thus the value of each row comes from its field.
        model.json then holds what the dialog showed, and it stays the one
        source of truth. The field starts with the value that the Value
        field of the part gave.
        """
        kind = self._kind_of(i)
        try:
            return float(self.part_rows[i]["value"].GetValue()) \
                * ENTRY_SCALE[kind]
        except (ValueError, KeyError):
            return None

    def _rlc_values(self, i):
        """Give {"r", "l", "c"} of a series RLC row in SI units.

        A field of 0, or an EMPTY field, gives None: that component is not
        in the part. A text that is not a number gives a ValueError.
        """
        out = {}
        for k, key in RLC_FIELDS:
            text = self.part_rows[i]["rlc"][k].GetValue().strip()
            v = float(text) if text else 0.0
            out[key] = v * ENTRY_SCALE[k] if v else None
        return out

    def _show_kind(self, i):
        """Show the single value of a row, or the three fields of a series
        RLC and no parasitics."""
        r = self.part_rows[i]
        kind = self._kind_of(i)
        rlc = kind == RLC_KIND
        r["area"].Show(r["single"], not rlc)
        r["area"].Show(r["triple"], rlc)
        for ctrl in r["para_ctrls"]:
            ctrl.Show(not rlc)
        # **The EPC is only for an inductor.** A capacitor HAS its
        # capacitance, and this field does not model the parallel
        # capacitance of a resistor. The text stays in the hidden field.
        # Thus when the user selects Inductor again, the row comes back as
        # it was.
        for ctrl in r["srf_ctrls"]:
            ctrl.Show(kind == "L")
        self.part_area.GetSizer().Layout()
        self.part_area.FitInside()

    def _part_settings(self, i):
        """Give the entry of one row for `lumped_parasitics`.

        A series RLC row gives "r", "l" and "c", and no value, no package
        and no parasitics: its three fields ARE the part. `_on_ok` does not
        read a row when its Model is off. Thus such a row can continue to
        have a text that is not a number. It then gives None for all three.
        """
        _, cb, ch, esl, esr = self.para_rows[i]
        kind = self._kind_of(i)
        if kind == RLC_KIND:
            try:
                vals = self._rlc_values(i)
            except ValueError:
                vals = {key: None for _, key in RLC_FIELDS}
            return dict(vals, model=cb.GetValue(), package=None, esl=0.0,
                        esr=0.0, type=kind, value=None)
        return {"model": cb.GetValue(),
                "package": self._pkg_of(ch),
                "esl": self._para_value(ch, esl, 1e-9),
                "esr": self._para_value(ch, esr),
                "epc": self._epc_value(i),
                "type": kind,
                "value": self._part_value(i)}

    def _epc_value(self, i):
        """Give the EPC of an inductor in farads, or give None.

        The field holds the SELF-RESONANCE in GHz, because a datasheet
        gives that number. The capacitance that is in parallel with the
        value at that frequency follows:

            C = 1 / ((2 pi f)^2 L)

        A row that is not an inductor gives None. A field of 0, an empty
        field, a text that is not a number and a part with no value also
        give None. The
        part then keeps the model that it had, which is DCR + L with no
        self-resonance.
        """
        if self._kind_of(i) != "L":
            return None
        text = self.part_rows[i]["srf"].GetValue().strip()
        if not text:
            return None
        try:
            f = float(text) * 1e9
        except ValueError:
            return None
        l = self._part_value(i)
        if f <= 0 or not l:
            return None
        return 1.0 / ((2 * 3.141592653589793 * f) ** 2 * l)

    def _on_para_edit(self, i, evt):
        """Move the choice of that row to Custom when the user types.

        Select CUSTOM_PKG by its index, and not the LAST entry.
        `_pkg_values` ends with "Custom" and then "No parasitics". The last
        entry gave "No parasitics", thus `_para_value` gave 0 for the ESR
        and for the ESL. The part then became IDEAL, with no message.
        """
        ch = self.para_rows[i][2]
        ch.SetSelection(self._pkg_values.index(CUSTOM_PKG))
        self._update_lumped_warning()
        evt.Skip()

    def get_settings(self):
        board = self.uses_board_stackup()
        return {
            "f_start": float(self.f_start.GetValue()) * 1e9,
            "f_stop": float(self.f_stop.GetValue()) * 1e9,
            "f_field": float(self.f_field.GetValue()) * 1e9,
            "z0": float(self.z0.GetValue()),
            # **None means "the stackup of the board"** (BOARD_PRESET).
            # `rfsim` then calls `extract()` with NO substrate. Thus the
            # `(stackup ...)` block of the file gives each layer its own
            # er, tan d and thickness. A number here replaces the block.
            "er": None if board else float(self.er.GetValue()),
            "tand": None if board else float(self.tand.GetValue()),
            "h": None if board else float(self.h.GetValue()),
            "cu_t": None if board else float(self.cu_t.GetValue()),
            "margin_mm": self.margin.GetValue(),
            # "Auto" is item 0 and it gives None: the runner then reads the
            # cell count and selects the value. Item i gives i threads.
            "threads": self.threads.GetSelection() or None,
            "mesh": MESH_LEVELS[self.mesh.GetSelection()],
            "port_types": [vals[c.GetSelection()] for c, vals
                           in zip(self.port_choices, self.port_types)],
            # The manual feed of each pad that has no track: (direction,
            # width in mm), or None. rfsim.py puts it into the port.
            "port_feed": [self._feed_of(i)
                          for i in range(len(self.port_choices))],
            "order": [c.GetSelection() + 1 for c in self.port_order],
            # "excite" holds the LAST port numbers, after the change of the
            # numbers. The runner compares against these numbers.
            "excite": sorted(num.GetSelection() + 1
                             for num, cb in zip(self.port_order,
                                                self.port_excite)
                             if cb.GetValue()),
            "lumped": self._any_modelled(),
            "parasitics": any(self._pkg_of(ch) != NO_PARASITICS
                              for i, (_, _, ch, _, _)
                              in enumerate(self.para_rows)
                              if self._kind_of(i) != RLC_KIND),
            # One entry for each R/L/C part. rfsim.py puts them into the
            # elements, thus model.json keeps the values that the solver
            # uses. "Custom" goes through as it is. Thus the log of the
            # solver shows the difference between a value that the user
            # selected and a package that the code could not read. Each row
            # gives its type and its value, also a row that the user did
            # not touch. The dialog SHOWS what the parser read. Thus what
            # the dialog shows is what model.json holds. "type" is None for
            # a row that stays at "Unknown", and rfsim.py then removes that
            # part. A series RLC row gives its three components, and no
            # value.
            "lumped_parasitics": {
                ref: self._part_settings(i)
                for i, (ref, _, _, _, _) in enumerate(self.para_rows)},
            "outdir": self.outdir.GetPath(),
            "n_freq": 401,
            "max_timesteps": int(float(self.max_steps.GetValue())),
            "end_criteria": float(self.end_crit.GetValue()),
            # An empty field gives None. `runner._time_step_factor` then
            # selects the value from the largest inductance of the model. A
            # value here is more important than that value.
            "time_step_factor": (float(self.tsf.GetValue())
                                 if self.tsf.GetValue().strip() else None),
        }


def _count_text(n):
    """Give a count of timesteps that a person can read.

    180000000 gives less data than "180 million", and the accurate number
    has no more data than the rule that made it.
    """
    for div, name in ((1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if n >= div:
            return "%.3g %s" % (n / div, name)
    return "%.0f" % n


def _ohm_text(z):
    """Give an impedance with a prefix, for a label."""
    for div, name in ((1e6, "Mohm"), (1e3, "kohm")):
        if z >= div:
            return "%.3g %s" % (z / div, name)
    return "%.3g ohm" % z


class RunDialog(wx.Dialog):
    """Run the solver subprocess and show its output in a log window.

    **The window closes when a run succeeds.** Thus its text is gone if it
    does not also go to a file: `log_path` receives each line that the
    window shows. The runner writes decisions.log itself. That file has
    only the decisions that the run made by itself.
    """

    def __init__(self, parent, cmd, log_path=None):
        wx.Dialog.__init__(self, parent, title="RFsim",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.log = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                               size=(700, 400))
        self.log.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.btn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(self.log, 1, wx.ALL | wx.EXPAND, 8)
        s.Add(self.btn, 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizerAndFit(s)

        # A log that cannot open must not stop the run.
        self._log_file = None
        if log_path:
            try:
                self._log_file = open(log_path, "w", encoding="utf-8")
            except OSError:
                self._log_file = None
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     creationflags=flags)
        self.Bind(wx.EVT_BUTTON, self._on_cancel, id=wx.ID_CANCEL)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        stream = self.proc.stdout
        while True:
            chunk = stream.read(256)
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace").replace("\r\n", "\n")
            text = text.replace("\r", "\n")
            if self._log_file:
                self._log_file.write(text)
            wx.CallAfter(self._append, text)
        rc = self.proc.wait()
        # Close the file BEFORE the dialog ends. Thus the caller can read
        # it immediately when ShowModal returns.
        if self._log_file:
            if rc:
                self._log_file.write("\n*** solver failed (exit code %s) "
                                     "***\n" % rc)
            self._log_file.close()
        wx.CallAfter(self._done, rc)

    def _append(self, text):
        self.log.AppendText(text)

    def _done(self, rc):
        if not self:
            return
        if rc == 0:
            self.EndModal(wx.ID_OK)
        else:
            self._append("\n*** solver failed (exit code %s) ***\n" % rc)
            self.btn.SetLabel("Close")

    def _on_cancel(self, evt):
        if self.proc.poll() is None:
            self.proc.kill()
        evt.Skip()


def _load_field(h5_path):
    """Read an FD dump of openEMS.

    The result is (x_mm, y_mm, complex F[y, x, 3], f_hz). The runner writes
    field.json adjacent to the dump, and F gets its factor. That is the
    scale of CST: an incident wave of 1 sqrt(W) peak (0.5 W) at the excited
    port. Phase 0 is at the peak of that wave. A run from before
    that file keeps the raw values of openEMS. This function does not use
    wx.
    """
    import h5py
    import numpy as np
    with h5py.File(h5_path, "r") as f:
        mesh = f["Mesh"]
        x, y = np.asarray(mesh["x"]), np.asarray(mesh["y"])
        fd = f["FieldData"]["FD"]
        f_hz = float(fd.attrs["frequency"][0])
        # openEMS v0.37 and after write one dataset of native complex
        # values, with a d_order attribute of 'NXYZ'. openEMS v0.0.36 and
        # before wrote a pair of float32 datasets: one for Re and one for
        # Im. The code below reads the two sequences of the axes.
        if "f0" in fd:
            F = np.asarray(fd["f0"])
        else:
            F = np.asarray(fd["f0_real"]) + 1j * np.asarray(fd["f0_imag"])
    if float(x.max() - x.min()) < 1.0:  # meters -> mm (a domain is > 1 mm)
        x, y = x * 1e3, y * 1e3
    F = np.squeeze(F)                   # remove the z axis: its length is 1
    if F.shape[0] == 3:                 # move the component axis to the end
        F = np.moveaxis(F, 0, -1)
    if F.shape[:2] == (len(x), len(y)):
        F = np.swapaxes(F, 0, 1)
    try:
        with open(os.path.join(os.path.dirname(h5_path), "field.json")) as fh:
            F = F * complex(*json.load(fh)["scale"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return x, y, F, f_hz


def _lobe_stats(ang_deg, D):
    """Give the direction of the primary lobe, the width at 3 dB and the
    side lobe level of a closed cut."""
    import numpy as np
    ang = np.asarray(ang_deg, float)
    d = np.asarray(D, float)
    if abs((ang[-1] - ang[0]) - 360.0) < 1e-6:  # closed cut: remove the copy
        ang, d = ang[:-1], d[:-1]
    n = len(d)
    step = abs(ang[1] - ang[0])
    i0 = int(np.argmax(d))
    peak = float(d[i0])
    li = 0
    while li < n - 1 and d[(i0 - li - 1) % n] >= peak - 3.0:
        li += 1
    ri = 0
    while ri < n - 1 and d[(i0 + ri + 1) % n] >= peak - 3.0:
        ri += 1
    width = min((li + ri) * step, 360.0)
    # The primary lobe goes to the first local minimum on each side. The
    # side lobes are in the other part of the pattern.
    lm = 0
    while lm < n - 1 and d[(i0 - lm - 1) % n] <= d[(i0 - lm) % n]:
        lm += 1
    rm = 0
    while rm < n - 1 and d[(i0 + rm + 1) % n] <= d[(i0 + rm) % n]:
        rm += 1
    mask = np.zeros(n, bool)
    for k in range(-lm, rm + 1):
        mask[(i0 + k) % n] = True
    sll = float(d[~mask].max() - peak) if not mask.all() else None
    return {"peak": peak, "dir": float(ang[i0]),
            "width": float(width), "sll": sll}


class ResultsFrame(wx.Frame):
    """Show the plots of the Touchstone file. It uses skrf and matplotlib."""

    def __init__(self, parent, touchstone_path):
        _use_wxagg()
        from matplotlib.backends.backend_wxagg import (
            FigureCanvasWxAgg, NavigationToolbar2WxAgg)
        from matplotlib.figure import Figure
        import skrf

        wx.Frame.__init__(self, parent, title="RFsim", size=(820, 620))
        self.net = skrf.Network(touchstone_path)
        import numpy as np
        plots = ["S-Parameters [Magnitude]", "S-Parameters [Phase]"]
        if self.net.nports >= 1:
            plots += ["Smith Chart", "VSWR"]
        if any(np.any(np.abs(self.net.s[:, j, k]) > 1e-9)
               for k in range(self.net.nports)
               for j in range(self.net.nports) if j != k):
            plots += ["Group delay"]

        self.outdir = os.path.dirname(os.path.abspath(touchstone_path))
        try:
            with open(os.path.join(self.outdir, "model.json")) as fh:
                self.model = json.load(fh)
        except Exception:
            self.model = None
        # The outputs of each excitation: excN/[EH]f.h5 and
        # farfield_pN.json for each excited port N. A plain farfield.json
        # comes from a previous run that had only one far field.
        self.field_h5s = {}  # (`kind`, port) -> the h5 path
        for k in ("E", "H"):
            for hit in glob.glob(os.path.join(self.outdir, "exc*",
                                              k + "f.h5")):
                p = int(re.search(r"exc(\d+)", hit).group(1))
                self.field_h5s[(k, p)] = hit
        self._ff = {}  # port (0 = previous or unknown) -> the far-field dict
        for path in glob.glob(os.path.join(self.outdir, "farfield*.json")):
            m = re.search(r"farfield_p(\d+)", os.path.basename(path))
            try:
                with open(path) as fh:
                    self._ff[int(m.group(1)) if m else 0] = json.load(fh)
            except Exception:
                pass
        # The impedance of the line of each de-embedded port. A previous
        # run, or a run that has only lumped ports, writes no such file.
        self._lines = None
        try:
            with open(os.path.join(self.outdir, "lines.json")) as fh:
                self._lines = json.load(fh)
        except Exception:
            pass
        if self._lines and self._lines.get("ports"):
            plots.append("Line Impedance")
        self._field = {}
        self._anim = None
        if self.model:
            s = self.model.get("settings", {})
            f_hz = s.get("f_field") or (0.5 * (s["f_start"] + s["f_stop"])
                                        if "f_start" in s else None)
            ftag = " (f=%g GHz)" % (f_hz / 1e9) if f_hz else ""
            plots.append("Board Layout")
            fports = sorted({p for _, p in self.field_h5s})
            for k in ("E", "H"):
                for p in fports:
                    if (k, p) in self.field_h5s:
                        plots.append("%s-Field%s%s" % (
                            k, ftag,
                            " (Port %d)" % p if len(fports) > 1 else ""))
            for p in sorted(self._ff):
                ff = self._ff[p]
                ptag = " (Port %d)" % p if len(self._ff) > 1 else ""
                for cut in ff.get("cuts", {}):
                    plots.append("Farfield (f=%g GHz) (%s)%s"
                                 % (ff["f_hz"] / 1e9, cut, ptag))
                if "grid3d" in ff:
                    plots.append("Farfield (f=%g GHz)%s"
                                 % (ff["f_hz"] / 1e9, ptag))
        # **The decisions of the run** (B56). The runner writes
        # decisions.log, and a user must not have to know the file. It is a
        # list of lines. Thus it shows as text and not as a plot.
        self._decisions = None
        try:
            with open(os.path.join(self.outdir, "decisions.log"),
                      encoding="utf-8") as fh:
                self._decisions = fh.read()
        except Exception:
            pass
        if self._decisions:
            plots.append(DECISIONS_VIEW)
        self.choice = wx.Choice(self, choices=plots)
        self.choice.SetSelection(0)
        self.figure = Figure(figsize=(8, 5.5), layout="constrained")
        self.canvas = FigureCanvasWxAgg(self, -1, self.figure)
        # The default minimum dimensions are the native dimensions of the
        # figure, 800x550. The sizer then cannot make the canvas smaller,
        # and it cuts the bottom axis.
        self.canvas.SetMinSize((320, 240))
        self.toolbar = NavigationToolbar2WxAgg(self.canvas)
        self.toolbar.Realize()
        self.text = wx.TextCtrl(self, value=self._decisions or "",
                                style=wx.TE_MULTILINE | wx.TE_READONLY
                                | wx.TE_DONTWRAP)
        self.text.Hide()

        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(self.choice, 0, wx.ALL, 6)
        s.Add(self.canvas, 1, wx.EXPAND)
        s.Add(self.text, 1, wx.EXPAND)
        s.Add(self.toolbar, 0, wx.EXPAND)
        self.SetSizer(s)
        self.choice.Bind(wx.EVT_CHOICE, lambda e: self._plot())
        self._plot()
        # The canvas gets its dimensions from the sizer only after an
        # EVT_SIZE. Without this call, the figure paints at its native
        # dimensions. The label of the bottom axis then stays cut until the
        # user changes the dimensions of the window.
        wx.CallAfter(self.SendSizeEvent)

    def _plot(self):
        import numpy as np
        if self._anim:
            self._anim.event_source.stop()
            self._anim = None
        self.figure.clear()
        sel = self.choice.GetStringSelection()
        text = sel == DECISIONS_VIEW
        self.text.Show(text)
        self.canvas.Show(not text)
        self.toolbar.Show(not text)
        self.Layout()
        if text:
            return
        ax = self.figure.add_subplot(111)
        net, f_ghz = self.net, self.net.f / 1e9

        m = re.search(r" \(Port (\d+)\)$", sel)
        pnum, base = (int(m.group(1)), sel[:m.start()]) if m else (None, sel)

        if sel.startswith("Board Layout"):
            self._plot_board(ax)
        elif sel.startswith("Line Impedance"):
            self._plot_lines(ax)
        elif sel.startswith(("E-Field", "H-Field")):
            ax.remove()
            self._plot_field(sel[0], pnum)
        elif sel.startswith("Farfield"):
            ax.remove()
            ff = self._ff[pnum if pnum in self._ff else sorted(self._ff)[0]]
            ptag = " (Port %d)" % pnum if pnum else ""
            tag = base[base.rfind("(") + 1:-1]
            if tag in ff.get("cuts", {}):  # a 2D cut, as "(Phi=0)"
                self._plot_farfield(ff, tag, ptag)
            else:                            # only "(f=xx GHz)": a 3D balloon
                self._plot_farfield3d(ff, ptag, pnum)
        elif sel.startswith("S-Parameters"):
            phase = sel.endswith("[Phase]")
            for j in range(net.nports):
                for k in range(net.nports):
                    if not np.any(np.abs(net.s[:, j, k]) > 1e-9):
                        continue  # port k is not excited: no data in column
                    v = (np.degrees(np.angle(net.s[:, j, k])) if phase
                         else net.s_db[:, j, k])
                    ax.plot(f_ghz, v, label="S%d%d" % (j + 1, k + 1))
            ax.set_title("S-Parameters [Phase]" if phase
                         else "S-Parameters [Magnitude]")
            ax.set_ylabel("°" if phase else "dB")
            ax.legend()
        elif sel.startswith("Smith"):
            grid = True
            for i in range(net.nports):
                if not np.any(np.abs(net.s[:, i, i]) > 1e-9):
                    continue  # port i is not excited: no data for S_ii
                net.plot_s_smith(m=i, n=i, ax=ax, draw_labels=grid)
                grid = False
            for ln in ax.get_lines():  # skrf writes "name, S11": use "S11"
                if ", S" in ln.get_label():
                    ln.set_label(ln.get_label().split(", ")[-1])
            ax.legend()
            ax.set_title("S-Parameters [Impedance View]")
        elif sel.startswith("VSWR"):
            for i in range(net.nports):
                s_ii = net.s[:, i, i]
                if not np.any(np.abs(s_ii) > 1e-9):
                    continue
                mag = np.clip(np.abs(s_ii), 0, 0.999999)
                ax.plot(f_ghz, (1 + mag) / (1 - mag),
                        label="Port %d" % (i + 1))
            ax.set_title("Voltage Standing Wave Ratio (VSWR)")
            ax.set_ylim(1, min(20, ax.get_ylim()[1]))
            ax.legend()
        else:  # the group delay: all the pairs that have data, in one graph
            for k in range(net.nports):
                for j in range(net.nports):
                    if j == k or not np.any(np.abs(net.s[:, j, k]) > 1e-9):
                        continue
                    phase = np.unwrap(np.angle(net.s[:, j, k]))
                    gd = -np.gradient(phase, 2 * np.pi * net.f) * 1e9
                    ax.plot(f_ghz, gd, label="S%d%d" % (j + 1, k + 1))
            ax.set_title("Group delay")
            ax.set_ylabel("Group delay / ns")
            ax.legend()

        if not sel.startswith(("Smith", "Board", "E-Field", "H-Field",
                               "Farfield")):
            ax.set_xlabel("Frequency / GHz")
            ax.grid(True, alpha=0.4)
        self.canvas.draw()

    def _plot_board(self, ax):
        """Show the top view of the model.

        The preview of the settings dialog uses the same function.
        """
        _draw_board(ax, self.model)

    def _plot_lines(self, ax):
        """Show Re(Z0) and Im(Z0) of each port.

        These values come from the voltage probes and the current probes of
        a de-embedded port. Thus they are the impedance of the actual track
        on the actual stackup, and not the reference impedance of the
        system. A lumped port has no line, thus it is not in this view.

        A good line gives a Z0 with almost no Im(Z0): Im(Z0) stays near
        zero, and a small distance below it. A large Im(Z0) shows a bad
        extraction, or a line with a large loss.

        The formula divides by the field at the measurement plane. Thus the
        values have noise where the excitation has a small energy, usually
        at the two ends of the sweep. The limits of the axis use
        percentiles, and not the largest and smallest values.
        """
        import numpy as np
        d = self._lines
        f_ghz = np.asarray(d["freq_hz"], float) / 1e9
        z_all = []
        nums = sorted(d["ports"], key=int)
        for num in nums:
            p = d["ports"][num]
            re, im = (np.asarray(p["Z0_real"], float),
                      np.asarray(p["Z0_imag"], float))
            z_all += [re, im]
            # One port does not have a tag in the legend, because the name
            # of the port adds nothing.
            tag = " (Port %s)" % num if len(nums) > 1 else ""
            ln, = ax.plot(f_ghz, re, label="Re(Z0)" + tag)
            ax.plot(f_ghz, im, "--", lw=1.0, color=ln.get_color(),
                    label="Im(Z0)" + tag)

        lo, hi = np.percentile(np.concatenate(z_all), [2, 98])
        pad = max(0.2 * (hi - lo), 0.05 * max(abs(hi), 1.0))
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_ylabel("Impedance / ohm")
        ax.legend(fontsize=8)
        ax.set_title("Line Impedance")

    def _dump_z(self, port):
        """Give the z of the plane of the field dump, in mm.

        `runner.build` puts the dump at the middle of the substrate between
        the layer of the EXCITED port and the layer below it: `0.5 * (z_top
        + z_ref)`. The title of the view names the value, because a field
        picture with no plane is a picture of nothing.
        """
        z_of = {c["name"]: c["z"] for c in self.model["copper_layers"]}
        ports = self.model["ports"]
        p = next((q for q in ports if q["number"] == port), ports[0])
        return 0.5 * (z_of[p["layer"]] + z_of[p["ref_layer"]])

    def _plot_field(self, kind, port=None):
        """Show an animation of the wave on the middle plane of the
        substrate.

        The picture is the magnitude of the field vector at one phase, in
        the style of CST. The scale goes from zero to the largest value of
        the animation. A block of text at the left gives the frequency, the
        phase and that largest value.

        The values have the scale of CST. The excited port gets an incident
        wave of 1 sqrt(W) peak, which is 0.5 W. Phase 0 is the peak of that
        wave. Thus you can compare the numbers of two runs.
        """
        import numpy as np
        from matplotlib.animation import FuncAnimation
        from matplotlib.patches import Rectangle
        ports = sorted(p for k2, p in self.field_h5s if k2 == kind)
        if port is None:
            port = ports[0]
        key = (kind, port)
        if key not in self._field:
            self._field[key] = _load_field(self.field_h5s[key])
        x, y, F, f_hz = self._field[key]
        frames = 24
        unit = "V/m" if kind == "E" else "A/m"

        gs = self.figure.add_gridspec(1, 2, width_ratios=[1.0, 3.2])
        info_ax = self.figure.add_subplot(gs[0])
        info_ax.axis("off")
        ax = self.figure.add_subplot(gs[1])

        def mag(i):
            """The magnitude of Re of the field vector at the phase of
            frame i."""
            return np.linalg.norm(
                np.real(F * np.exp(2j * np.pi * i / frames)), axis=-1)

        # The scale does not change during the animation. A scale that
        # moves with the frame makes all frames look the same. The colour
        # changes smoothly from zero to the largest value. A scale of
        # steps, as the bar of CST, makes bands of one colour that look
        # like large pixels.
        vmax = max(float(mag(i).max()) for i in range(frames)) or 1.0
        ticks = np.linspace(0.0, vmax, 10)
        mesh = ax.pcolormesh(x, y, mag(0), cmap="jet", vmin=0.0, vmax=vmax,
                             shading="gouraud")
        bar = self.figure.colorbar(mesh, ax=ax, shrink=0.85, ticks=ticks)
        # The unit is above the bar, as in CST, and not at its side. A word
        # that is vertical makes the reader turn their head.
        bar.ax.set_title(unit, fontsize=9)
        # Each mark shows its own value, and the two ends of the bar show
        # theirs. A shared factor above the bar, which is the default,
        # hides the dimension of a step.
        bar.ax.set_yticklabels(["%.3g" % t for t in ticks], fontsize=7)

        top = self.model["ports"][0]["layer"]
        for poly in self.model["polygons"].get(top, []):
            ax.plot([p[0] for p in poly] + [poly[0][0]],
                    [p[1] for p in poly] + [poly[0][1]], color="0.2", lw=0.6)
        # **The ports, in the same lime as the Board layout view.** The
        # field views are the pictures that go out of the tool. A reviewer
        # on 2026-08-03 read the port at an incorrect position, because no
        # picture showed it.
        for p in self.model["ports"]:
            hl, hw = 0.5 * p["length"], 0.5 * p["width"]
            ax.add_patch(Rectangle((p["x"] - hl, p["y"] - hw),
                                   2 * hl, 2 * hw, facecolor="none",
                                   edgecolor="lime", lw=1.2, zorder=5))
            ax.annotate("P%d" % p["number"], (p["x"], p["y"]),
                        color="lime", fontsize=8, ha="center", va="center",
                        zorder=6)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.set_title("%s-Field (f=%g GHz)%s"
                     % (kind, f_hz / 1e9,
                        " (Port %d)" % port if len(ports) > 1 else ""),
                     fontsize=10)
        ax.set_aspect("equal")

        # The block of numbers at the left. The PLANE goes with them. It is
        # the plane that `runner.build` dumps: the middle of the substrate
        # between the layer of the excited port and the layer below it. A
        # field picture with no plane shows nothing.
        head = ("Frequency: %.2f GHz\n" % (f_hz / 1e9))
        tail = ("\nMaximum: %.4g %s\nPlane: z=%.2f mm\n(substrate mid-plane)"
                % (vmax, unit, self._dump_z(port)))
        info = info_ax.text(0.0, 0.5, head + "Phase: 0\N{DEGREE SIGN}" + tail,
                            fontsize=9, va="center", ha="left",
                            linespacing=1.8)

        def step(i):
            mesh.set_array(mag(i).ravel())
            info.set_text(head + "Phase: %d\N{DEGREE SIGN}"
                          % round(360.0 * i / frames) + tail)
            return (mesh, info)

        self._anim = FuncAnimation(self.figure, step, frames=frames,
                                   interval=60, blit=False,
                                   cache_frame_data=False)

    def _plot_farfield3d(self, ff, ptag="", pnum=None):
        """Show a transparent 3D balloon of the directivity.

        The PCB is a reference plate. The radius and the colour give the
        dBi in a range of 30 dB. The +z axis is perpendicular to the board.
        A block of text at the left gives the numbers, in the style of CST.
        """
        import numpy as np
        from matplotlib import cm, colors
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        g = ff["grid3d"]
        th = np.radians(np.asarray(g["theta_deg"], float))[:, None]
        ph = np.radians(np.asarray(g["phi_deg"], float))[None, :]
        D = np.asarray(g["D_dBi"], float)
        rmin = float(D.max()) - 30.0
        R = np.maximum(D - rmin, 0.0)
        X = R * np.sin(th) * np.cos(ph)
        Y = R * np.sin(th) * np.sin(ph)
        Z = R * np.cos(th)

        gs = self.figure.add_gridspec(1, 2, width_ratios=[1.0, 3.2])
        info_ax = self.figure.add_subplot(gs[0])
        info_ax.axis("off")
        ax = self.figure.add_subplot(gs[1], projection="3d")
        norm = colors.Normalize(vmin=rmin, vmax=float(D.max()))
        fc = cm.jet(norm(D))
        fc[..., 3] = 0.3  # a transparent balloon: you can see the board
        ax.plot_surface(X, Y, Z, facecolors=fc, rstride=1, cstride=1,
                        linewidth=0, antialiased=False, shade=False)
        m = float(R.max()) or 1.0

        # The reference plate of the PCB is at the origin. Its orientation
        # is the orientation of the Board layout view. Thus the +z axis is
        # perpendicular to the board. The scale is only a display
        # parameter. The far field is at an infinite distance, thus the
        # board has no dimension, and it shows only the orientation. The
        # largest dimension of the board is about one half of the radius of
        # the balloon.
        br = self.model["board_rect"]
        cx, cy = 0.5 * (br["x0"] + br["x1"]), 0.5 * (br["y0"] + br["y1"])
        span = max(br["x1"] - br["x0"], br["y1"] - br["y0"], 1e-6)
        sc = 0.5 * m / span
        dz = 0.02 * m

        def plate(poly, z):
            return [((px - cx) * sc, (py - cy) * sc, z) for px, py in poly]

        for name, col, z in (("B.Cu", "tab:blue", -dz), ("F.Cu", "tab:red", dz)):
            polys = [plate(p, z) for p in self.model["polygons"].get(name, [])]
            if polys:
                ax.add_collection3d(Poly3DCollection(
                    polys, facecolor=col, edgecolor="none"))
        for p in self.model["ports"]:
            hw, hl = p["width"] / 2.0, p["length"] / 2.0
            corners = [(p["x"] - hl, p["y"] - hw), (p["x"] + hl, p["y"] - hw),
                       (p["x"] + hl, p["y"] + hw), (p["x"] - hl, p["y"] + hw)]
            ax.add_collection3d(Poly3DCollection(
                [plate(corners, 1.5 * dz)], facecolor="lime", edgecolor="none"))

        ax.set_xlim(-m, m)
        ax.set_ylim(-m, m)
        ax.set_zlim(-m, m)
        ax.set_box_aspect((1, 1, 1))
        ax.set_axis_off()
        ax.set_title("Farfield (f=%g GHz)%s" % (ff["f_hz"] / 1e9, ptag),
                     fontsize=10)
        sm = cm.ScalarMappable(norm=norm, cmap=cm.jet)
        sm.set_array([])
        # The default marks show round numbers, and they stop before the
        # two ends of the bar. A reader then cannot see the value at the
        # top, which is Dmax, or the value at the bottom.
        ticks = np.linspace(rmin, float(D.max()), 9)
        bar = self.figure.colorbar(sm, ax=ax, shrink=0.65, ticks=ticks)
        bar.ax.set_yticklabels(["%.1f" % t for t in ticks], fontsize=7)
        bar.ax.set_title("dBi", fontsize=9)  # above the bar, as in CST

        # The block of numbers at the left. The radiation efficiency is the
        # radiated power divided by the ACCEPTED power. The total
        # efficiency also counts the power that the mismatch sends back.
        # Thus it is the radiation efficiency times 1 - |Snn|^2.
        lines = ["Frequency: %.2f GHz" % (ff["f_hz"] / 1e9)]
        rad = ff.get("efficiency_pct")
        if rad:
            rad_db = 10.0 * np.log10(rad / 100.0)
            lines.append("Rad. Effic. : %.4f dB" % rad_db)
            mis = self._mismatch_db(ff["f_hz"], pnum)
            if mis is not None:
                lines.append("Tot. Effic. : %.4f dB" % (rad_db + mis))
        lines.append("Dir. : %.3f dBi" % float(D.max()))
        info_ax.text(0.0, 0.5, "\n".join(lines), fontsize=9,
                     va="center", ha="left", linespacing=1.8)

    def _mismatch_db(self, f_hz, pnum):
        """Give 10*log10(1 - |Snn|^2) at f_hz, or give None.

        This is the part of the total efficiency that the reflection of the
        port removes. Port 1 is the port of a run that excited only one
        port.
        """
        import numpy as np
        n = (pnum or 1) - 1
        net = self.net
        if net is None or n >= net.nports:
            return None
        i = int(np.argmin(np.abs(net.f - f_hz)))
        g2 = float(np.abs(net.s[i, n, n]) ** 2)
        return 10.0 * np.log10(max(1.0 - g2, 1e-12))

    def _plot_farfield(self, ff, cut, ptag=""):
        """Show one polar cut of the directivity in absolute dBi.

        The style is the style of CST.
        """
        import numpy as np
        c = ff["cuts"][cut]
        ang_deg = np.asarray(c["angle_deg"], float)
        D = np.asarray(c["D_dBi"], float)
        phi_cut = cut.startswith("Phi")

        gs = self.figure.add_gridspec(1, 2, width_ratios=[2.4, 1.0])
        ax = self.figure.add_subplot(gs[0], projection="polar")
        info_ax = self.figure.add_subplot(gs[1])
        info_ax.axis("off")

        peak = float(D.max())
        rmin = peak - 40.0
        ax.plot(np.radians(ang_deg), np.maximum(D, rmin), color="tab:red")
        ax.set_theta_zero_location("N")
        ax.set_thetagrids(range(0, 360, 30),
                          labels=[str(a) for a in range(0, 360, 30)])
        ax.set_rlabel_position(270)  # put the dBi numbers on the right, as CST
        ax.set_rlim(rmin, peak + 3)
        ax.set_rticks(np.arange(np.ceil(rmin / 10.0) * 10.0,
                                peak + 3, 10.0))
        ax.set_title("Farfield Directivity Abs (%s)%s" % (cut, ptag))
        ax.set_xlabel("%s / \N{DEGREE SIGN} vs. dBi"
                      % ("Theta" if phi_cut else "Phi"))

        st = _lobe_stats(ang_deg, D)
        lines = ["Frequency = %g GHz" % (ff["f_hz"] / 1e9),
                 "Main lobe magnitude = %.2f dBi" % st["peak"],
                 "Main lobe direction = %.1f deg." % st["dir"],
                 "Angular width (3 dB) = %.1f deg." % st["width"]]
        if st["sll"] is not None:
            lines.append("Side lobe level = %.1f dB" % st["sll"])
        info_ax.text(0.0, 0.5, "\n".join(lines), fontsize=9,
                     va="center", ha="left", linespacing=1.8)
