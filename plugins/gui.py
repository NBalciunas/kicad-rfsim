"""The wxPython dialogs: the settings of the simulation, the log of the
solver, and the plots of the results."""
import glob
import importlib
import json
import os
import re
import subprocess
import sys
import threading
import types

import wx

PORT_TYPES = [("Lumped Port", "lumped"), ("Microstrip (MSL) Port", "msl"),
              ("Coplanar (CPW) Port", "cpw"), ("Stripline Port", "stripline")]
MESH_LEVELS = ["coarse", "medium", "fine"]
CUSTOM_PKG = "Custom"       # the user gives the ESL and the ESR
NO_PARASITICS = "No parasitics"   # an ideal element: no ESL and no ESR
KIND_NAMES = {"R": "Resistor", "C": "Capacitor", "L": "Inductor"}
KIND_UNITS = {"R": "ohm", "C": "F", "L": "H"}
# The multipliers for a value that a person reads, the largest first.
_ENG = ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
        (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"))


SUBSTRATE_PRESETS = [("FR-4", 4.2, 0.02),
                     ("Rogers RO4350B", 3.48, 0.0037),
                     ("Custom", None, None)]
# The colours of the top view. The preview in the settings dialog and the
# view in the results window both use them.
CU_COLORS = {"F.Cu": ("tab:red", 0.8), "B.Cu": ("tab:blue", 0.45)}


def _badge(parent, text):
    """Give a small box that holds `text`, for the name or the value of a
    part.

    The function gives no size to the box. Add it to the grid with
    wx.EXPAND: then each box fills its column, thus all the boxes of one
    column have the same width. The colour comes from the system, thus it
    obeys a dark theme.
    """
    t = wx.StaticText(parent, label=text,
                      style=wx.BORDER_SIMPLE | wx.ALIGN_CENTRE_HORIZONTAL)
    t.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))
    return t


def _eng(value, unit):
    """Give a value with its multiplier, for example "4.7 kohm" or "3.3 nH"."""
    if value is None:
        return ""
    if value == 0:
        return "0 " + unit
    for scale, prefix in _ENG:
        if abs(value) >= scale:
            return "%g %s%s" % (value / scale, prefix, unit)
    return "%g %s" % (value, unit)


def _port_choices(p):
    """Give (label, value) for the port types that the geometry permits.

    A lumped port always operates. Each de-embedded port needs a feed
    direction (a track, or the manual "Feed" control). A CPW port also
    needs a coplanar gap, and its entry shows the measured gap. A
    stripline port needs a plane above the strip and a plane below it.
    The label of the port row names what the geometry does not give.
    """
    out = [PORT_TYPES[0]]
    if p.get("direction"):
        out.append(PORT_TYPES[1])
        if p.get("gap"):
            out.append(("%s [Coplanar Gap: %.2f mm]"
                        % (PORT_TYPES[2][0], p["gap"]), "cpw"))
        if p.get("height"):
            out.append(PORT_TYPES[3])
    return out


def _port_note(p):
    """Give what the geometry of a port ADDS.

    The label of the port holds the problem tags ("[No Track]"), and
    the CPW entry of the type choice holds the gap. Thus this text
    holds only the stripline distance.
    """
    if p.get("height"):
        return "stripline: %.3f mm to each plane" % p["height"]
    return ""


def _use_wxagg():
    """Select the wx backend of matplotlib, around a defect in wxPython.

    The wxPython of KiCad contains `wx/svg/`, but not the compiled
    `_nanosvg` extension. The wx backend of matplotlib imports `wx.svg`
    for a side effect only and never uses it. Thus this function replaces
    the module when the real module is defective. Use
    `importlib.import_module` for the test, never `import wx.svg`. That
    statement makes `wx` a *local* name here. Then, if the import fails,
    all the `wx.*` names after it stop with UnboundLocalError.
    """
    import matplotlib
    matplotlib.use("WXAgg", force=False)
    try:
        importlib.import_module("wx.svg")
    except ImportError:
        sys.modules["wx.svg"] = types.ModuleType("wx.svg")


def _draw_board(ax, model, compact=False, margin_mm=None, show_lumped=True):
    """Draw the top view of the model.

    B.Cu is blue, F.Cu is red, the ports are green and the R/L/C parts
    are dark green. The preview of the settings dialog and the "Board
    layout" view of the results window both use this function.

    compact       Make a thumbnail for the dialog: no axes, no title and
                  no legend. Thus the board fills the full canvas.
    margin_mm     Draw the domain from board_rect plus 2 times the
                  margin, and not from model["region"]. The dialog shows
                  the margin that the user selects now, not the margin of
                  the model.
    show_lumped   Draw the R/L/C parts or do not draw them. The dialog
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
    else:  # extract() increases the board bbox by 2 times the margin
        d = 2.0 * float(margin_mm)
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
        # The line from pad to pad: the box in the gap is less than 1 mm
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
        # No legend: the colours are clear near the ports, which have
        # labels, and the legend used one third of the width of the
        # board. Put the frame on the domain, and do not let matplotlib
        # scale it. Then the thumbnail always has the same frame,
        # whatever annotation goes out the furthest.
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
        ax.set_title("Board layout")


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, ports, default_outdir, lumped=(), preview=None,
                 packages=None):
        wx.Dialog.__init__(self, parent, title="RFsim")
        # {the code of the package: the ESL in H}. board_reader keeps the
        # table, thus there is one source of truth. This module must not
        # import it: board_reader imports pcbnew.
        self._packages = dict(packages or {})
        self._pkg_values = []
        self._build(ports, default_outdir, lumped, preview)

    def _build(self, ports, default_outdir, lumped, preview=None):
        top = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="RFsim v1.0")
        title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        top.Add(title, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 10)
        # A thumbnail of the simulation is better than a logo. It shows
        # which pads became ports, which R/L/C parts the plugin found,
        # and how far the domain goes. The code draws it at the end of
        # _build, because it needs self.margin. If matplotlib fails, the
        # icon replaces the thumbnail.
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
        self.port_types = []   # the values of the choices of each port
        self.port_order = []
        self.port_excite = []
        # (the direction choice, the width field) of a pad that has no
        # track, or None for a pad that has one.
        self.port_feed = []
        nums = [str(i + 1) for i in range(len(ports))]
        # The rows of the ports use their own grid, in the same way as the
        # lumped elements. Column 1 is empty and grows, thus the label
        # stays at the left and the controls stay at the right end.
        mid = wx.ALIGN_CENTER_VERTICAL
        prg = wx.FlexGridSizer(cols=6, vgap=6, hgap=8)
        prg.AddGrowableCol(1, 1)
        pbox.Add(prg, 0, wx.ALL | wx.EXPAND, 6)
        self.port_badges = []
        for i, p in enumerate(ports):
            num = wx.Choice(self, choices=nums)
            num.SetSelection(i)
            num.Enable(len(ports) > 1)
            num.SetToolTip("The number assigns the port. Port 1 drives "
                           "the field and far-field views.")
            # The type choice holds only the types that the geometry
            # permits; the label of the row names what the geometry does
            # not give. The fixed width fits the CPW entry with its gap:
            # the list can change with the feed direction, and the
            # control must not change its size with it.
            # _set_type_choices fills the control below.
            self.port_types.append([])
            ch = wx.Choice(self, size=(280, -1))
            exc = wx.CheckBox(self, label="Excite")
            exc.SetValue(True)
            exc.SetToolTip("Drive this port: one FDTD run for each "
                           "excited port. Uncheck ports whose S-columns "
                           "you don't need.")
            # _refresh_port_badges puts the text in. The pad, the
            # footprint and the net go into the tooltip.
            label = wx.StaticText(self, label="")
            label.SetToolTip(p["label"])
            self.port_badges.append(p)
            # The feed controls. A pad with a track shows the direction
            # and the width that the track gives, locked (off). A pad
            # with no track can still sit on a line that the user drew
            # as a shape or as a polygon (the feed of a patch antenna,
            # for example): the user then gives the two values, and a
            # de-embedded port becomes possible. Refer to NOTES.md,
            # backlog item 16.
            note = wx.BoxSizer(wx.HORIZONTAL)
            dch = wx.Choice(self, choices=["No Line", "+x (→)", "-x (←)",
                                           "+y (↑)", "-y (↓)"])
            wtc = wx.TextCtrl(self, size=(50, -1))
            if p.get("direction"):
                dch.SetSelection({(1, 0): 1, (-1, 0): 2, (0, 1): 3,
                                  (0, -1): 4}[tuple(p["direction"])])
                wtc.ChangeValue("%g" % (p.get("track_width")
                                        or min(p["width"], p["length"])))
                for c in (dch, wtc):
                    c.SetToolTip("The track at this pad gives this value")
                    c.Enable(False)
                self.port_feed.append(None)
            else:
                dch.SetSelection(0)
                dch.SetToolTip("Direction of the feed line at this pad, "
                               "as drawn in the preview above (+y points "
                               "up). Pick one to enable de-embedded port "
                               "types on copper drawn as shapes/polygons.")
                wtc.ChangeValue("%g" % min(p["width"], p["length"]))
                wtc.SetToolTip("Width of the feed line (the track width "
                               "a routed track would provide)")
                self.port_feed.append((dch, wtc))
                dch.Bind(wx.EVT_CHOICE, lambda evt, k=i: self._on_feed(k))
            note.Add(wx.StaticText(self, label="Feed:"), 0, mid)
            note.Add(dch, 0, mid | wx.LEFT, 4)
            note.Add(wx.StaticText(self, label="Width:"), 0,
                     mid | wx.LEFT, 6)
            note.Add(wtc, 0, mid | wx.LEFT, 4)
            note.Add(wx.StaticText(self, label="mm"), 0, mid | wx.LEFT, 2)
            extra = _port_note(p)
            if extra:
                note.Add(wx.StaticText(self, label=extra), 0,
                         mid | wx.LEFT, 8)
            prg.Add(label, 0, mid)
            prg.Add((0, 0))   # the empty column that grows
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
            # part of the port. There is no checkbox for all the parts:
            # each row has its own "Model" checkbox, in the same way as
            # the "Excite" checkbox of a port.
            lbox = section("Lumped elements")
            # One row for each part. The code reads the package from the
            # name of the footprint. A name that has no code gives
            # "Custom", and the user then puts in the values. "No
            # parasitics" gives an ideal element, thus each row can go off
            # by itself and there is no checkbox for all of them.
            # The choice SHOWS "0603 Package", but the value that goes into
            # model.json stays "0603": board_reader reads that code, and
            # the log of the solver prints it. Thus the labels and the
            # values are two lists, and _pkg_of() changes one into the
            # other. "Custom" and "No parasitics" show their own name.
            names = sorted(self._packages) + [CUSTOM_PKG, NO_PARASITICS]
            self._pkg_values = names
            labels = ["%s Package" % v for v in sorted(self._packages)]
            labels += [CUSTOM_PKG, NO_PARASITICS]
            # One grid for all the rows, and not one sizer for each row.
            # Thus each part of a row stays in a column that agrees from
            # row to row. The name of the box already says "Lumped
            # elements", thus a row does not say it again: it starts with
            # the type and the reference, in the same way as a port row
            # starts with the name of its pad. Column 2 is an empty
            # column that grows, thus it holds the two boxes at the left
            # and the parasitics at the right.
            lg = wx.FlexGridSizer(cols=12, vgap=6, hgap=8)
            lg.AddGrowableCol(2, 1)
            lbox.Add(lg, 0, wx.ALL | wx.EXPAND, 6)
            mid = wx.ALIGN_CENTER_VERTICAL
            for e in lumped:
                i = len(self.para_rows)
                pkg = e.get("package")
                cb = wx.CheckBox(self, label="Model")
                cb.SetValue(True)
                ch = wx.Choice(self, choices=labels)
                # A package that the code did not read gives "Custom", and
                # not "No parasitics": the parasitics are on by default.
                ch.SetSelection(names.index(pkg) if pkg in names
                                else names.index(CUSTOM_PKG))
                esl = wx.TextCtrl(self, value="%g" % (
                    1e9 * (e.get("esl") or 0.0)), size=(55, -1))
                esr = wx.TextCtrl(self, value="%g" % (e.get("esr") or 0.0),
                                  size=(55, -1))
                kind = e.get("type", "")
                # wx.EXPAND, and no ALIGN: then each box fills its column
                # and all the boxes of one column are the same width.
                lg.Add(_badge(self, '%s "%s"' % (KIND_NAMES.get(kind) or "Part",
                                                 e["ref"])), 0, wx.EXPAND)
                lg.Add(_badge(self, _eng(e.get("value"),
                                         KIND_UNITS.get(kind, ""))),
                       0, wx.EXPAND)
                lg.Add((0, 0))   # the empty column that grows
                lg.Add(wx.StaticText(self, label="Parasitics:"), 0, mid)
                lg.Add(ch, 0, mid)
                # R before L, in the sequence of "RLC". There is no third
                # field: a series capacitance is not a parasitic of these
                # parts. Refer to NOTES.md, "Package parasitics".
                for label, ctrl, unit in (("ESR:", esr, "ohm"),
                                          ("ESL:", esl, "nH")):
                    lg.Add(wx.StaticText(self, label=label), 0, mid | wx.LEFT, 6)
                    lg.Add(ctrl, 0, mid)
                    lg.Add(wx.StaticText(self, label=unit), 0, mid)
                lg.Add(cb, 0, mid | wx.LEFT, 12)
                # A preset writes the ESL with ChangeValue, which sends no
                # EVT_TEXT. Thus the choice stays on the package. An edit
                # by the user moves the choice to Custom. The substrate
                # presets use the same method.
                ch.Bind(wx.EVT_CHOICE, lambda evt, k=i: self._on_package(k))
                for c in (esl, esr):
                    c.Bind(wx.EVT_TEXT,
                           lambda evt, k=i: self._on_para_edit(k, evt))
                self.para_rows.append((e["ref"], cb, ch, esl, esr))
            lbox.Add(wx.StaticText(
                self, label="The values are for the part BODY only: the loop "
                "of the pads and the tracks is already in the mesh. A preset "
                "sets the ESL; the ESR comes from the type of the part. Pick "
                "\"No parasitics\" for an ideal element."),
                0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        sbox = section("Substrate")
        sg = grid_in(sbox)
        self.preset = row(sg, "Presets:", wx.Choice(
            self, choices=[p[0] for p in SUBSTRATE_PRESETS]))
        self.preset.SetSelection(0)
        # the default values: FR4 of 1.6 mm, copper of 35 um (1 oz)
        self.er = row(sg, "er:", wx.TextCtrl(self, value="4.2"))
        self.tand = row(sg, "Loss tangent:", wx.TextCtrl(self, value="0.02"))
        self.h = row(sg, "Substrate thickness:",
                     wx.TextCtrl(self, value="1.6"), "mm")
        self.cu_t = row(sg, "Copper thickness:",
                        wx.TextCtrl(self, value="0.035"), "mm")
        self.preset.Bind(wx.EVT_CHOICE, self._on_preset)
        for c in (self.er, self.tand):
            c.Bind(wx.EVT_TEXT, self._on_substrate_edit)

        rbox = section("Simulation")
        rg = grid_in(rbox)
        cpus = os.cpu_count() or 1
        self.threads = row(rg, "CPU threads:", wx.Choice(
            self, choices=["Auto"] + [str(i) for i in range(1, cpus + 1)]))
        self.threads.SetSelection(0)
        self.threads.SetToolTip(
            "Threads for the FDTD engine. Each thread takes one slice of "
            "the domain, and all the threads wait for the slowest one at "
            "each timestep. Thus a small model is fastest with few "
            "threads, and a large model with more. \"Auto\" reads the "
            "size of the mesh and selects the value.")
        self.mesh = row(rg, "Mesh resolution:", wx.Choice(
            self, choices=[m.capitalize() for m in MESH_LEVELS]))
        self.mesh.SetSelection(1)
        self.margin = row(rg, "Domain margin:", wx.SpinCtrlDouble(
            self, min=2.0, max=50.0, initial=4.0, inc=0.5), "mm")
        self.outdir = row(rg, "Output directory:", wx.DirPickerCtrl(
            self, path=default_outdir, style=wx.DIRP_USE_TEXTCTRL))

        run = wx.Button(self, wx.ID_OK, "Run Simulation")
        top.Add(run, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.ALL, 12)
        self.SetSizerAndFit(top)
        self.SetMinSize((520, -1))
        self.Fit()
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        # The preview needs self.margin and the rows. Thus draw it
        # last, and keep it in agreement with the two controls.
        if self._prev_fig is not None:
            for evt in (wx.EVT_SPINCTRLDOUBLE, wx.EVT_TEXT):
                self.margin.Bind(evt, self._on_preview_change)
            self._redraw_preview()
        if self.para_rows:
            for _, cb, _, _, _ in self.para_rows:
                cb.Bind(wx.EVT_CHECKBOX, self._on_lumped)
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
            # full figure, because there are no labels that need space.
            self._prev_fig = Figure(figsize=(4.9, 2.9))
            self._prev_canvas = FigureCanvasWxAgg(self, -1, self._prev_fig)
            # FigureCanvasWxAgg gives the native pixel size of the figure
            # as its minimum size for wx, and this clips the figure. Refer
            # to the wx problems in NOTES.md.
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
        """Update the port types that a manual feed direction permits.

        The gap of each candidate direction comes from extract(), in
        port["gaps"]. Thus a drawn CPW (a center line from a polygon,
        and not from a track) gets the CPW type for a direction that
        has copper at the two sides. The detector reads the geometry
        only, not the nets: that copper must really be ground.
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

        The selection stays on the same type when the new list still
        holds it; a type that went away falls back to Lumped. The
        control goes off when Lumped is the one entry.
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
        """Put "Port N" and its problem tags into the label of each port.

        N is the number that the choice of that row gives now, and not the
        number of the selection. Two rows can hold the same number for a
        short time; _on_ok refuses that. The tags name what the geometry
        does not give: "[No Track]", "[No Coplanar Gap]".
        """
        for badge, num, p in zip(self._port_badge_ctrls, self.port_order,
                                 self.port_badges):
            parts = ["Port %d" % (num.GetSelection() + 1)]
            if not p.get("direction"):
                parts.append("[No Track]")
                if not any((p.get("gaps") or {}).values()):
                    parts.append("[No Coplanar Gap]")
            elif not p.get("gap"):
                parts.append("[No Coplanar Gap]")
            badge.SetLabel(" ".join(parts))
        self.Layout()

    def _any_modelled(self):
        """Tell if the model contains one lumped element or more."""
        return any(cb.GetValue() for _, cb, _, _, _ in self.para_rows)

    def _on_lumped(self, evt):
        """Keep the controls of each row in agreement with its checkboxes.

        Parasitics of a part that the model does not contain have no
        meaning. Thus the fields of a row go off with its Model checkbox.
        """
        for _, cb, ch, esl, esr in self.para_rows:
            # A part that the model does not contain has no parasitics.
            # "No parasitics" makes an ideal element, thus its two fields
            # have no meaning either.
            on = cb.GetValue()
            ch.Enable(on)
            for c in (esl, esr):
                c.Enable(on and self._pkg_of(ch) != NO_PARASITICS)
        self._redraw_preview()
        if evt is not None:
            evt.Skip()

    def _redraw_preview(self):
        if self._prev_fig is None:
            return
        self._prev_fig.clear()
        ax = self._prev_fig.add_axes((0.01, 0.01, 0.98, 0.98))
        try:
            _draw_board(ax, self._preview_model, compact=True,
                        margin_mm=self.margin.GetValue(),
                        show_lumped=(not self.para_rows
                                     or self._any_modelled()))
        except Exception as e:  # the preview must never stop the dialog
            ax.set_axis_off()
            ax.text(0.5, 0.5, "preview unavailable\n%s" % e, ha="center",
                    va="center", fontsize=7, transform=ax.transAxes)
        self._prev_canvas.draw_idle()

    def _on_preset(self, evt):
        _, er, tand = SUBSTRATE_PRESETS[self.preset.GetSelection()]
        if er is not None:  # ChangeValue sends no EVT_TEXT: the preset stays
            self.er.ChangeValue(str(er))
            self.tand.ChangeValue(str(tand))

    def _on_substrate_edit(self, evt):
        self.preset.SetSelection(len(SUBSTRATE_PRESETS) - 1)  # Custom
        evt.Skip()

    def _on_ok(self, evt):
        try:
            fa, fb = float(self.f_start.GetValue()), float(self.f_stop.GetValue())
            fd = float(self.f_field.GetValue())
            z0 = float(self.z0.GetValue())
            er, tand = float(self.er.GetValue()), float(self.tand.GetValue())
            h, cu_t = float(self.h.GetValue()), float(self.cu_t.GetValue())
            para = [(float(esl.GetValue()), float(esr.GetValue()))
                    for _, _, _, esl, esr in self.para_rows]
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
        order = [c.GetSelection() for c in self.port_order]
        if sorted(order) != list(range(len(order))):
            wx.MessageBox("Each pad needs a unique port number.",
                          "RFsim", wx.ICON_ERROR)
            return
        if not any(cb.GetValue() for cb in self.port_excite):
            wx.MessageBox("Select at least one port to excite.",
                          "RFsim", wx.ICON_ERROR)
            return
        # A de-embedded type on a manual feed needs a direction and a
        # width. The type list already removes the de-embedded types
        # when the direction goes back to "No Line", thus this test
        # catches only a width that does not parse.
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
        evt.Skip()

    def _pkg_of(self, ch):
        """Give the package VALUE of a choice, and not its label.

        The choice shows "0603 Package"; the value is "0603".
        """
        return self._pkg_values[ch.GetSelection()]

    def _para_value(self, ch, ctrl, scale=1.0):
        """Give the value of a field in SI, or give 0 for "No parasitics".

        The text of the field does not change. Thus the value of the user
        comes back when the row takes a package again.
        """
        if self._pkg_of(ch) == NO_PARASITICS:
            return 0.0
        return float(ctrl.GetValue()) * scale

    def _on_package(self, i):
        """Put the ESL of the package into the field of that row.

        ChangeValue sends no EVT_TEXT, thus the choice stays on the
        package. Custom changes nothing: the values of the user stay.
        """
        _, _, ch, esl, _ = self.para_rows[i]
        pkg = self._pkg_of(ch)
        if pkg in self._packages:
            esl.ChangeValue("%g" % (1e9 * self._packages[pkg]))
        self._on_lumped(None)   # "No parasitics" turns the fields off

    def _on_para_edit(self, i, evt):
        """Move the choice of that row to Custom when the user types."""
        ch = self.para_rows[i][2]
        ch.SetSelection(ch.GetCount() - 1)
        evt.Skip()

    def get_settings(self):
        return {
            "f_start": float(self.f_start.GetValue()) * 1e9,
            "f_stop": float(self.f_stop.GetValue()) * 1e9,
            "f_field": float(self.f_field.GetValue()) * 1e9,
            "z0": float(self.z0.GetValue()),
            "er": float(self.er.GetValue()),
            "tand": float(self.tand.GetValue()),
            "h": float(self.h.GetValue()),
            "cu_t": float(self.cu_t.GetValue()),
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
            # "excite" holds the FINAL port numbers, after the change of
            # the numbers. The runner compares against these numbers.
            "excite": sorted(num.GetSelection() + 1
                             for num, cb in zip(self.port_order,
                                                self.port_excite)
                             if cb.GetValue()),
            "lumped": self._any_modelled(),
            "parasitics": any(self._pkg_of(ch) != NO_PARASITICS
                              for _, _, ch, _, _ in self.para_rows),
            # One entry for each R/L/C part. rfsim.py puts them into the
            # elements, thus model.json keeps the values that the solver
            # uses.
            # "Custom" goes through as it is. Thus the log of the solver
            # tells the difference between a value that the user selected
            # and a package that the code could not read.
            "lumped_parasitics": {
                ref: {"model": cb.GetValue(),
                      "package": self._pkg_of(ch),
                      "esl": self._para_value(ch, esl, 1e-9),
                      "esr": self._para_value(ch, esr)}
                for ref, cb, ch, esl, esr in self.para_rows},
            "outdir": self.outdir.GetPath(),
            "n_freq": 401,
            # ponytail: these two limits are constant. Put them in the
            # dialog if high-Q structures, which need a longer ringdown,
            # become usual.
            "max_timesteps": 300000,
            "end_criteria": 1e-4,
        }


class RunDialog(wx.Dialog):
    """Run the solver subprocess and show its output in a log window."""

    def __init__(self, parent, cmd):
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
            wx.CallAfter(self._append, text.replace("\r", "\n"))
        rc = self.proc.wait()
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

    The result is (x_mm, y_mm, complex F[y, x, 3], f_hz). This function
    does not need wx.
    """
    import h5py
    import numpy as np
    with h5py.File(h5_path, "r") as f:
        mesh = f["Mesh"]
        x, y = np.asarray(mesh["x"]), np.asarray(mesh["y"])
        fd = f["FieldData"]["FD"]
        f_hz = float(fd.attrs["frequency"][0])
        # openEMS v0.37 and later write one dataset of native complex
        # values, with a d_order attribute of 'NXYZ'. openEMS v0.0.36 and
        # earlier wrote a pair of float32 datasets, one for the real part
        # and one for the imaginary part. The code below reads the two
        # sequences of the axes.
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
    return x, y, F, f_hz


def _lobe_stats(ang_deg, D):
    """Give the direction of the main lobe, the width at 3 dB and the side
    lobe level of a closed cut."""
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
    # The main lobe goes to the first local minimum on each side. The side
    # lobes are in the remaining part of the pattern.
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
    """Show the plots of the Touchstone file. It needs skrf and matplotlib."""

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
        # comes from an old run that had one far field only.
        self.field_h5s = {}  # (kind, port) -> the h5 path
        for k in ("E", "H"):
            for hit in glob.glob(os.path.join(self.outdir, "exc*",
                                              k + "f.h5")):
                p = int(re.search(r"exc(\d+)", hit).group(1))
                self.field_h5s[(k, p)] = hit
        self._ff = {}  # port (0 = old or unknown) -> the far-field dict
        for path in glob.glob(os.path.join(self.outdir, "farfield*.json")):
            m = re.search(r"farfield_p(\d+)", os.path.basename(path))
            try:
                with open(path) as fh:
                    self._ff[int(m.group(1)) if m else 0] = json.load(fh)
            except Exception:
                pass
        # The impedance of the line of each de-embedded port. An old run,
        # or a run that has lumped ports only, writes no such file.
        self._lines = None
        try:
            with open(os.path.join(self.outdir, "lines.json")) as fh:
                self._lines = json.load(fh)
        except Exception:
            pass
        if self._lines and self._lines.get("ports"):
            plots.append("Line impedance")
        self._field = {}
        self._anim = None
        if self.model:
            s = self.model.get("settings", {})
            f_hz = s.get("f_field") or (0.5 * (s["f_start"] + s["f_stop"])
                                        if "f_start" in s else None)
            ftag = " (f=%g GHz)" % (f_hz / 1e9) if f_hz else ""
            plots.append("Board layout")
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
        self.choice = wx.Choice(self, choices=plots)
        self.choice.SetSelection(0)
        self.figure = Figure(figsize=(8, 5.5), layout="constrained")
        self.canvas = FigureCanvasWxAgg(self, -1, self.figure)
        # The default minimum size is the native size of the figure,
        # 800x550. The sizer then cannot make the canvas smaller, and it
        # clips the bottom axis.
        self.canvas.SetMinSize((320, 240))
        toolbar = NavigationToolbar2WxAgg(self.canvas)
        toolbar.Realize()

        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(self.choice, 0, wx.ALL, 6)
        s.Add(self.canvas, 1, wx.EXPAND)
        s.Add(toolbar, 0, wx.EXPAND)
        self.SetSizer(s)
        self.choice.Bind(wx.EVT_CHOICE, lambda e: self._plot())
        self._plot()
        # The canvas takes the size from the sizer only after a size
        # event. Without this call, the figure paints at its native size
        # and the label of the bottom axis stays clipped until the user
        # changes the size of the window. the window
        wx.CallAfter(self.SendSizeEvent)

    def _plot(self):
        import numpy as np
        if self._anim:
            self._anim.event_source.stop()
            self._anim = None
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        net, f_ghz = self.net, self.net.f / 1e9
        sel = self.choice.GetStringSelection()

        m = re.search(r" \(Port (\d+)\)$", sel)
        pnum, base = (int(m.group(1)), sel[:m.start()]) if m else (None, sel)

        if sel.startswith("Board layout"):
            self._plot_board(ax)
        elif sel.startswith("Line impedance"):
            self._plot_lines(ax)
        elif sel.startswith(("E-Field", "H-Field")):
            self._plot_field(ax, sel[0], pnum)
        elif sel.startswith("Farfield"):
            ax.remove()
            ff = self._ff[pnum if pnum in self._ff else sorted(self._ff)[0]]
            ptag = " (Port %d)" % pnum if pnum else ""
            tag = base[base.rfind("(") + 1:-1]
            if tag in ff.get("cuts", {}):    # "(Phi=0)" and similar: a 2D cut
                self._plot_farfield(ff, tag, ptag)
            else:                            # only "(f=xx GHz)": a 3D balloon
                self._plot_farfield3d(ff, ptag)
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
        """Draw the top view of the model.

        The preview of the settings dialog uses the same function.
        """
        _draw_board(ax, self.model)

    def _plot_lines(self, ax):
        """Draw the impedance of the line and eps_eff of each port.

        These values come from the voltage probes and the current probes
        of a de-embedded port. Thus they are the impedance of the real
        track on the real stackup, and not the reference impedance of the
        system. A lumped port has no line, thus it is not in this view.

        The formula divides by the field at the measurement plane. Thus
        the values are noisy where the excitation has little energy,
        usually at the two ends of the sweep. The limits of the axes use
        percentiles, and not the extreme values.
        """
        import numpy as np
        d = self._lines
        f_ghz = np.asarray(d["freq_hz"], float) / 1e9
        ax2 = ax.twinx()
        z_all, e_all = [], []
        for num in sorted(d["ports"], key=int):
            p = d["ports"][num]
            z, e = np.asarray(p["Z0_real"], float), np.asarray(p["eps_eff"],
                                                               float)
            z_all.append(z)
            e_all.append(e)
            ln, = ax.plot(f_ghz, z, label="Port %s: Re(Z0)" % num)
            ax2.plot(f_ghz, e, "--", lw=1.0, color=ln.get_color(),
                     label="Port %s: eps_eff" % num)

        def limits(vals, floor):
            lo, hi = np.percentile(np.concatenate(vals), [2, 98])
            pad = max(0.2 * (hi - lo), 0.05 * max(abs(hi), 1.0))
            return max(floor, lo - pad), hi + pad

        ax.set_ylim(*limits(z_all, 0.0))
        ax2.set_ylim(*limits(e_all, 1.0))
        ax.set_ylabel("Line impedance Re(Z0) / ohm")
        ax2.set_ylabel("Effective permittivity")
        ax2.grid(False)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=8)
        ax.set_title("Transmission line impedance")

    def _plot_field(self, ax, kind, port=None):
        """Show an animation of the wave on the mid-plane of the substrate.

        The two fields have the same red and blue view, which keeps the
        sign. E shows E_z. H shows the largest in-plane component of H,
        because H makes loops around the track and its vertical part is
        near zero on this plane.
        """
        import numpy as np
        from matplotlib.animation import FuncAnimation
        ports = sorted(p for k2, p in self.field_h5s if k2 == kind)
        if port is None:
            port = ports[0]
        key = (kind, port)
        if key not in self._field:
            self._field[key] = _load_field(self.field_h5s[key])
        x, y, F, f_hz = self._field[key]
        frames = 24

        if kind == "E":
            comp = F[..., 2]                 # the vertical E below the track
        else:
            hx, hy = np.abs(F[..., 0]).max(), np.abs(F[..., 1]).max()
            comp = F[..., 0] if hx >= hy else F[..., 1]  # the largest H
        lim = float(np.percentile(np.abs(comp), 99)) or 1.0
        mesh = ax.pcolormesh(x, y, np.real(comp), cmap="RdBu_r",
                             vmin=-lim, vmax=lim, shading="gouraud")

        top = self.model["ports"][0]["layer"]
        for poly in self.model["polygons"].get(top, []):
            ax.plot([p[0] for p in poly] + [poly[0][0]],
                    [p[1] for p in poly] + [poly[0][1]], color="0.2", lw=0.6)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.set_title("%s-Field (f=%g GHz)%s" % (
            kind, f_hz / 1e9,
            " (Port %d)" % port if len(ports) > 1 else ""))
        ax.set_aspect("equal")

        def step(i):
            ph = np.exp(2j * np.pi * i / frames)
            mesh.set_array(np.real(comp * ph).ravel())
            return (mesh,)

        self._anim = FuncAnimation(self.figure, step, frames=frames,
                                   interval=60, blit=False,
                                   cache_frame_data=False)

    def _plot_farfield3d(self, ff, ptag=""):
        """Show a transparent 3D balloon of the directivity.

        The PCB is a reference plate. The radius and the colour give the
        dBi in a range of 30 dB. +z is the normal of the board.
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

        ax = self.figure.add_subplot(111, projection="3d")
        norm = colors.Normalize(vmin=rmin, vmax=float(D.max()))
        fc = cm.jet(norm(D))
        fc[..., 3] = 0.3  # a transparent balloon: you can see the board
        ax.plot_surface(X, Y, Z, facecolors=fc, rstride=1, cstride=1,
                        linewidth=0, antialiased=False, shade=False)
        m = float(R.max()) or 1.0

        # The reference plate of the PCB is at the origin. Its
        # orientation is the orientation of the Board layout view, thus
        # +z is the normal of the board. The scale is only a display
        # parameter: the far field is at an infinite distance, thus the
        # board has no size and shows the orientation only. The largest
        # dimension of the board is about one half of the radius of the
        # balloon.
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
        ax.set_title("Farfield (f=%g GHz)%s" % (ff["f_hz"] / 1e9, ptag))
        sm = cm.ScalarMappable(norm=norm, cmap=cm.jet)
        sm.set_array([])
        self.figure.colorbar(sm, ax=ax, shrink=0.65, label="dBi")

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
