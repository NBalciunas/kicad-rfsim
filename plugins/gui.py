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
# The rows of the R/L/C parts that the dialog shows without a scroll.
# Each row is about 29 px tall, and the dialog is already 1053 px tall
# with one part on a screen of 1920x1080.
MAX_PART_ROWS = 6
CUSTOM_PKG = "Custom"       # the user gives the ESL and the ESR
NO_PARASITICS = "No parasitics"   # an ideal element: no ESL and no ESR
KIND_NAMES = {"R": "Resistor", "C": "Capacitor", "L": "Inductor"}
# The name of the quantity, for the label in front of the value of a part.
KIND_QUANTITY = {"R": "Resistance", "C": "Capacitance", "L": "Inductance"}
# The first entry of the type choice, for a part whose refdes does not
# say what it is (a diode, a ferrite bead, a footprint of your own).
UNKNOWN_KIND = "Unknown"
KIND_ORDER = [None] + list(KIND_NAMES)   # the index in the type choice
# The unit of the field that the user fills in. A number and no prefix,
# in the same way as the ESR and the ESL fields. The value goes into
# model.json in SI units.
ENTRY_UNITS = {"R": "ohm", "C": "pF", "L": "nH"}
ENTRY_SCALE = {"R": 1.0, "C": 1e-12, "L": 1e-9}


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


def _port_subregion_bounds(ports, margin_mm):
    """Give the rectangular export bounds derived from the selected ports."""
    if not ports:
        return None
    x0 = min(p["x"] - 0.5 * p["length"] for p in ports)
    x1 = max(p["x"] + 0.5 * p["length"] for p in ports)
    y0 = min(p["y"] - 0.5 * p["width"] for p in ports)
    y1 = max(p["y"] + 0.5 * p["width"] for p in ports)
    margin = 2.0 * float(margin_mm)
    return x0 - margin, x1 + margin, y0 - margin, y1 + margin


def _element_in_port_subregion(element, ports, margin_mm):
    """Tell whether a lumped-element box intersects the port subregion."""
    bounds = _port_subregion_bounds(ports, margin_mm)
    if bounds is None:
        return False
    rx0, rx1, ry0, ry1 = bounds
    start, stop = element["start"], element["stop"]
    ex0, ex1 = sorted((start[0], stop[0]))
    ey0, ey1 = sorted((start[1], stop[1]))
    return ex1 >= rx0 and ex0 <= rx1 and ey1 >= ry0 and ey0 <= ry1


def _port_subregion_text(ports, margin_mm, lumped=()):
    """Give the approximate port-bbox export bounds shown in the dialog."""
    bounds = _port_subregion_bounds(ports, margin_mm)
    if bounds is None:
        return "No selected ports."
    rx0, rx1, ry0, ry1 = bounds
    margin = 2.0 * float(margin_mm)
    x0, x1, y0, y1 = rx0 + margin, rx1 - margin, ry0 + margin, ry1 - margin
    refs = []
    for element in lumped:
        if _element_in_port_subregion(element, ports, margin_mm):
            refs.append(element["ref"])
    chosen = ", ".join(refs) if refs else "none"
    return ("Port bounds: X %.2f..%.2f mm, Y %.2f..%.2f mm\n"
            "Export bounds: X %.2f..%.2f mm, Y %.2f..%.2f mm\n"
            "R/L/C candidates in export: %s"
            % (x0, x1, y0, y1, rx0, rx1, ry0, ry1, chosen))


# The er and the tan d of each preset. **FR-4 must agree with
# `board_reader.DEF_EPSILON`**, which is the value that the plugin uses
# when the board file holds no stackup. The two were 4.2 here and 4.5
# there until 2026-08-05, thus the SAME board gave one substrate
# through the dialog and another through a run with no GUI.
# The three laminates after FR-4 are the usual low-loss choices of a
# fabricator. Their values are the DATASHEET values at 10 GHz, and FR-4
# is at 1 MHz: the er of FR-4 falls to about 4.3 at 5 GHz, thus a run in
# the GHz band deserves a value that the laminate of your fabricator
# gives.
#
# **Two Dk values exist for a Rogers laminate, and these are the
# PROCESS values.** Rogers measures them with a clamped stripline at
# 10 GHz, and it publishes a larger "design Dk" for a MICROSTRIP: about
# 3.66 for RO4350B and 3.55 for RO4003C. A microstrip that this plugin
# simulates with the process value reads about 5% high in impedance.
# The design values are not here, because nobody has measured which one
# agrees with this solver: refer to the todo list.
SUBSTRATE_PRESETS = [("FR-4", 4.5, 0.02),
                     ("Rogers RO4350B", 3.48, 0.0037),
                     ("Rogers RO4003C", 3.38, 0.0027),
                     # The values are those of RT/duroid 5880, which is
                     # the usual PTFE laminate. Another PTFE differs: er
                     # goes from 2.1 to 2.6 with the glass in it.
                     ("PTFE", 2.20, 0.0009),
                     ("Custom", None, None)]
# The colours of the top view. The preview in the settings dialog and the
# view in the results window both use them.
CU_COLORS = {"F.Cu": ("tab:red", 0.8), "B.Cu": ("tab:blue", 0.45)}


def _port_choices(p):
    """Give (label, value) for the port types that the geometry permits.

    A lumped port always operates. Each de-embedded port needs a feed
    direction (a track, or the manual "Feed" control). A CPW port also
    needs a coplanar gap. A stripline port needs a plane above the strip
    and a plane below it. Each of those two entries carries its measured
    value in brackets: a value in brackets comes from the board, and the
    user cannot change it. Both values keep three decimals. The two
    entries are adjacent, thus a different count of decimals looks
    arbitrary. Three decimals are also necessary: a stackup that comes
    from mil gives 0.127 mm and 0.254 mm, and two decimals make the two
    values equal. The label of the port row names what the geometry does
    not give.
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
        ax.set_title("Board Layout")


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, ports, default_outdir, lumped=(), preview=None,
                 packages=None, esr=None, on_view_geometry=None):
        wx.Dialog.__init__(self, parent, title="RFsim")
        # {the code of the package: the ESL in H} and {the type of the
        # part: the ESR in ohm}. board_reader keeps both tables, thus
        # there is one source of truth. This module must not import it:
        # board_reader imports pcbnew.
        self._packages = dict(packages or {})
        self._esr = dict(esr or {})
        self._pkg_values = []
        self.part_rows = []
        self._on_view_geometry = on_view_geometry
        self._build(ports, default_outdir, lumped, preview)

    def _build(self, ports, default_outdir, lumped, preview=None):
        top = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="RFsim v1.1")
        title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        top.Add(title, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 10)
        # A thumbnail of the simulation is better than a logo. It shows
        # which pads became ports, which R/L/C parts the plugin found,
        # and how far the domain goes. The code draws it at the end of
        # _build, because it needs self.margin. If matplotlib fails, the
        # icon replaces the thumbnail.
        self._preview_model = preview
        self._preview_ports = ports
        self._preview_lumped = tuple(lumped)
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
            num.SetToolTip("The number assigns the port.")
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
                           "excited port.")
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
            # de-embedded port becomes possible.
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
                # Windows sends it no mouse event. Thus these two carry
                # one for the day that they become enabled, and the
                # LABELS beside them carry the same text, which is what
                # a user can actually reach.
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
            lbox = section("Lumped Elements")
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
            # row to row. A row reads as a sentence:
            #
            #   Element "R1"  [Resistor v]  Resistance: [50 v] ohm
            #       ... Parasitics: [0402 Package v] ESR: [] ESL: [] [x] Model
            #
            # Column 5 is an empty column that GROWS, thus the part
            # stays at the left and the parasitics stay at the right end.
            #
            # The rows go into a SCROLLED window, and their parent is
            # that window and not the dialog. Each row is about 29 px
            # tall, thus a board with many parts made a dialog that was
            # taller than the screen: 1053 px with one part on a screen
            # of 1920x1080, which is already the full height. _fit_rows
            # gives the window its height limit after the rows exist.
            #
            # The parent is the static box and not the dialog: wx gives a
            # warning for a window of a wxStaticBoxSizer that is a child
            # of the dialog, and a scrolled window is a real container.
            self.part_area = wx.ScrolledWindow(lbox.GetStaticBox(),
                                               style=wx.VSCROLL)
            self.part_area.SetScrollRate(0, 10)
            lg = wx.FlexGridSizer(cols=15, vgap=6, hgap=8)
            lg.AddGrowableCol(5, 1)
            self.part_area.SetSizer(lg)
            lbox.Add(self.part_area, 0, wx.ALL | wx.EXPAND, 6)
            pane = self.part_area
            mid = wx.ALIGN_CENTER_VERTICAL
            for e in lumped:
                i = len(self.para_rows)
                pkg = e.get("package")
                kind = e.get("type") or None
                cb = wx.CheckBox(pane, label="Model")
                # A part whose type the board does not give starts OFF.
                # Thus a diode, a ferrite bead or a footprint of your own
                # changes no simulation until the user selects a type and
                # gives a value.
                cb.SetValue(kind is not None)
                ch = wx.Choice(pane, choices=labels)
                # A package that the code did not read gives "Custom" for
                # a part that the board describes: the parasitics of an
                # R, an L or a C are on by default, and _ESL_DEFAULT_NH
                # is the value. A part with NO type gets **"No
                # parasitics"**: the code knows nothing about its body,
                # thus it must not invent an ESL for it.
                start_pkg = (pkg if pkg in names
                             else CUSTOM_PKG if kind else NO_PARASITICS)
                ch.SetSelection(names.index(start_pkg))
                esl0 = "%g" % (1e9 * (e.get("esl") or 0.0))
                esr0 = "%g" % (e.get("esr") or 0.0)
                if start_pkg == NO_PARASITICS:
                    esl0 = esr0 = "0"
                esl = wx.TextCtrl(pane, value=esl0, size=(55, -1))
                esr = wx.TextCtrl(pane, value=esr0, size=(55, -1))
                # The refdes gives the type and the Value field gives the
                # number, and the row SHOWS what the parser read. But
                # both controls stay open: the user knows the part, and
                # the board does not always say what it is. A refdes
                # that names no type starts at "Unknown" with an empty
                # value, because the Value field of a diode holds a part
                # number and not a quantity.
                kinds = wx.Choice(pane, choices=[UNKNOWN_KIND]
                                  + list(KIND_NAMES.values()), size=(110, -1))
                kinds.SetSelection(KIND_ORDER.index(kind) if kind in KIND_ORDER
                                   else 0)
                # The unit of the field is fixed (ohm, nH or pF), in the
                # same way as the ESR and the ESL fields. Thus the user
                # gives a number and no prefix, and the unit follows the
                # TYPE alone and not the size of the value.
                value = wx.TextCtrl(pane, value=_entry_text(kind,
                                                            e.get("value")),
                                    size=(90, -1))
                value.Enable(kind is not None)
                qty = wx.StaticText(pane, label=_qty_label(kind))
                uni = wx.StaticText(pane, label=ENTRY_UNITS.get(kind, ""))
                lg.Add(wx.StaticText(pane, label='Element "%s"' % e["ref"]),
                       0, mid)
                lg.Add(kinds, 0, mid)
                lg.Add(qty, 0, mid | wx.LEFT, 6)
                lg.Add(value, 0, mid)
                lg.Add(uni, 0, mid)
                lg.Add((0, 0))   # the empty column that grows
                lg.Add(wx.StaticText(pane, label="Parasitics:"), 0, mid)
                lg.Add(ch, 0, mid)
                # R before L, in the sequence of "RLC". There is no third
                # field: a series capacitance is not a parasitic of
                # these parts.
                for label, ctrl, unit in (("ESR:", esr, "ohm"),
                                          ("ESL:", esl, "nH")):
                    lg.Add(wx.StaticText(pane, label=label), 0, mid | wx.LEFT, 6)
                    lg.Add(ctrl, 0, mid)
                    lg.Add(wx.StaticText(pane, label=unit), 0, mid)
                lg.Add(cb, 0, mid | wx.LEFT, 12)
                # A preset writes the ESL with ChangeValue, which sends no
                # EVT_TEXT. Thus the choice stays on the package. An edit
                # by the user moves the choice to Custom. The substrate
                # presets use the same method.
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
                self.para_rows.append((e["ref"], cb, ch, esl, esr))
                # The controls of the PART itself. They stay beside
                # para_rows, thus the code that reads the parasitics does
                # not change.
                # "esl0" and "esr0" are the values that come back when a
                # row leaves "No parasitics", which writes 0 over them.
                self.part_rows.append({"ref": e["ref"], "kind": kinds,
                                       "value": value, "qty": qty,
                                       "unit": uni, "last_pkg": start_pkg,
                                       "esl0": esl0, "esr0": esr0})
            # A lumped inductor makes the FDTD unstable at the full
            # Courant step, thus the runner divides the step by
            # sqrt(L[nH]) and multiplies the number of steps by the same
            # value. The run time goes up with it, and nothing said so
            # before this label: a user who typed 100 nH got a run that
            # was 10 times longer with no message.
            # The parent is the static box, in the same way as the window
            # of the rows: one sizer cannot hold two different parents.
            self.lumped_warn = wx.StaticText(lbox.GetStaticBox(), label="")
            self.lumped_warn.SetForegroundColour(wx.Colour(150, 90, 0))
            lbox.Add(self.lumped_warn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        sbox = section("Substrate")
        sg = grid_in(sbox)
        self.preset = row(sg, "Presets:", wx.Choice(
            self, choices=[p[0] for p in SUBSTRATE_PRESETS]))
        self.preset.SetSelection(0)
        # The default values are the FR-4 preset: refer to
        # SUBSTRATE_PRESETS. 1.6 mm and 35 um (1 oz) go with them.
        self.er = row(sg, "er:", wx.TextCtrl(self, value="4.5"))
        self.tand = row(sg, "Loss tangent:", wx.TextCtrl(self, value="0.02"))
        self.h = row(sg, "Substrate thickness:",
                     wx.TextCtrl(self, value="1.6"), "mm")
        self.cu_t = row(sg, "Copper thickness:",
                        wx.TextCtrl(self, value="0.035"), "mm")
        # **The dialog does NOT read the stackup of the board.** It fills
        # the four fields from the FR-4 preset and nothing else, thus a
        # user always sees the same start and gives the values that the
        # board needs. A version that filled them from the
        # `(stackup ...)` block of the file, with a label under the
        # fields that named the source, went in and came out again on
        # 2026-08-05 at the request of the owner. B7 holds that work, and
        # problem 8 is the reason to do it one day: a Rogers board
        # simulates as FR4 until the user types the values.
        #
        # `model["stackup_source"]` stays in the model: it tells a reader
        # of model.json where the substrate came from, and B7 needs it.
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

        domain_tabs = wx.Notebook(self)
        subregion_tab = wx.Panel(domain_tabs)
        subregion_sizer = wx.BoxSizer(wx.VERTICAL)
        self.port_focused_subregion = wx.CheckBox(
            subregion_tab, label="Export port-focused rectangular subregion")
        self.port_focused_subregion.SetValue(False)
        self.port_focused_subregion.SetToolTip(
            "Use the selected port-pad bounding box plus the domain margin; "
            "only intersecting copper, vias, and R/L/C parts are exported.")
        subregion_sizer.Add(self.port_focused_subregion, 0, wx.ALL, 8)
        self.subregion_info = wx.StaticText(
            subregion_tab, label=_port_subregion_text(
                ports, self.margin.GetValue(), self._preview_lumped))
        subregion_sizer.Add(self.subregion_info, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        subregion_tab.SetSizer(subregion_sizer)
        domain_tabs.AddPage(subregion_tab, "Subregion")
        top.Add(domain_tabs, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        # A structure with a high Q rings for a long time. The run then
        # stops at the step limit before the energy comes down to the end
        # criteria, and the S-parameters are not correct. The two limits
        # were constant at 300k and 1e-4 before this.
        #
        # The three fields go on ONE row, in the same way as the two
        # frequencies. Three rows of the grid would make the dialog
        # 84 px taller, and it is already 1053 px with one part: refer
        # to MAX_PART_ROWS and to problem 10 of NOTES.
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
        close = wx.Button(self, wx.ID_CANCEL, "Close")
        view = wx.Button(self, label="View Exported Geometry")
        load = wx.Button(self, label="Load Settings")
        save = wx.Button(self, label="Save Settings")
        # The rows must have their size before the dialog takes its own.
        self._fit_rows()
        # **The WHOLE dialog scrolls.** The rows of the parts scrolled
        # since 2026-08-05, and that stopped the dialog from GROWING
        # without a limit; it did not make it fit. The dialog is 1084 px
        # tall with ONE part, and a screen of 1920x1080 gives about
        # 1040 px of client area, thus the Run button was under the edge
        # of the screen on a usual machine (problem 10).
        #
        # Every control above is a child of the dialog, thus this moves
        # them into a scrolled body afterwards, in the place of a change
        # to each of the 60 constructors. The Run button stays OUTSIDE
        # the body: a button that scrolls out of view is the defect that
        # this corrects.
        body = wx.ScrolledWindow(self, style=wx.VSCROLL)
        body.SetScrollRate(0, 12)
        for child in list(self.GetChildren()):
            if child not in (body, close, load, save, view, run):
                child.Reparent(body)
        body.SetSizer(top)
        body.FitInside()
        # A wx.ScrolledWindow does NOT give the size of its sizer as its
        # best size, thus `Fit()` alone collapses the dialog to its
        # minimum. Give the body the size of the content for the fit,
        # and make it small again straight after: `_fit_to_screen` must
        # be free to cut the height, and `SetSize` cannot go under the
        # minimum size of a window.
        content = top.GetMinSize()
        body.SetInitialSize(content)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(body, 1, wx.EXPAND)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        actions.Add(close, 0, wx.ALL, 12)
        actions.Add(load, 0, wx.ALL, 12)
        actions.Add(save, 0, wx.ALL, 12)
        actions.Add(view, 0, wx.ALL, 12)
        actions.Add(run, 0, wx.ALL, 12)
        outer.Add(actions, 0, wx.ALIGN_CENTER_HORIZONTAL)
        self.SetSizer(outer)
        self.Fit()
        body.SetMinSize((content.GetWidth(), 120))
        self.SetMinSize((520, 240))
        self._fit_to_screen()
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        view.Bind(wx.EVT_BUTTON, self._on_view)
        load.Bind(wx.EVT_BUTTON, self._on_load_settings)
        save.Bind(wx.EVT_BUTTON, self._on_save_settings)
        # The preview needs self.margin and the rows. Thus draw it
        # last, and keep it in agreement with the two controls.
        if self._prev_fig is not None:
            for evt in (wx.EVT_SPINCTRLDOUBLE, wx.EVT_TEXT):
                self.margin.Bind(evt, self._on_preview_change)
            self._redraw_preview()
        self.margin.Bind(wx.EVT_SPINCTRLDOUBLE, self._on_subregion_change)
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
            # FigureCanvasWxAgg gives the native pixel size of the
            # figure as its minimum size for wx, and this clips the
            # figure. Thus give the canvas a small minimum size.
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

    def _on_close(self, evt):
        self.EndModal(wx.ID_CANCEL)

    def _on_view(self, evt):
        if self._on_view_geometry is None:
            wx.MessageBox("Geometry preview is unavailable.", "RFsim", wx.ICON_ERROR)
            return
        try:
            self._on_view_geometry(self.get_settings())
        except Exception as exc:
            wx.MessageBox(str(exc), "RFsim geometry preview", wx.ICON_ERROR)

    def _on_save_settings(self, evt):
        dlg = wx.FileDialog(self, "Save RFsim settings", wildcard="RFsim settings (*.json)|*.json",
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            with open(dlg.GetPath(), "w", encoding="utf-8") as fh:
                json.dump(self.get_settings(), fh, indent=2)
        dlg.Destroy()

    def _on_load_settings(self, evt):
        dlg = wx.FileDialog(self, "Load RFsim settings", wildcard="RFsim settings (*.json)|*.json",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        try:
            with open(dlg.GetPath(), encoding="utf-8") as fh:
                saved = json.load(fh)
            self._apply_settings(saved)
        except (OSError, ValueError, TypeError) as exc:
            wx.MessageBox("Could not load settings: %s" % exc, "RFsim", wx.ICON_ERROR)
        finally:
            dlg.Destroy()

    def _apply_settings(self, saved):
        """Apply scalar settings and component choices saved by this dialog."""
        for ctrl, key, scale in ((self.f_start, "f_start", 1e9),
                                 (self.f_stop, "f_stop", 1e9),
                                 (self.f_field, "f_field", 1e9),
                                 (self.z0, "z0", 1.0), (self.er, "er", 1.0),
                                 (self.tand, "tand", 1.0), (self.h, "h", 1.0),
                                 (self.cu_t, "cu_t", 1.0)):
            if key in saved:
                ctrl.ChangeValue("%g" % (float(saved[key]) / scale))
        self.margin.SetValue(float(saved.get("margin_mm", self.margin.GetValue())))
        self.port_focused_subregion.SetValue(bool(saved.get(
            "port_focused_subregion", self.port_focused_subregion.GetValue())))
        self.max_steps.ChangeValue(str(saved.get("max_timesteps", self.max_steps.GetValue())))
        self.end_crit.ChangeValue("%g" % float(saved.get("end_criteria", self.end_crit.GetValue())))
        tsf = saved.get("time_step_factor")
        self.tsf.ChangeValue("" if tsf is None else "%g" % float(tsf))
        if saved.get("mesh") in MESH_LEVELS:
            self.mesh.SetSelection(MESH_LEVELS.index(saved["mesh"]))
        thread = saved.get("threads")
        self.threads.SetSelection(int(thread) if thread else 0)
        self.outdir.SetPath(str(saved.get("outdir", self.outdir.GetPath())))
        saved_parts = saved.get("lumped_parasitics", {})
        for index, (ref, check, package, esl, esr) in enumerate(self.para_rows):
            part = saved_parts.get(ref)
            if not part:
                continue
            check.SetValue(bool(part.get("model", check.GetValue())))
            package_name = part.get("package")
            if package_name in self._pkg_values:
                package.SetSelection(self._pkg_values.index(package_name))
            esl.ChangeValue("%g" % (float(part.get("esl", 0.0)) * 1e9))
            esr.ChangeValue("%g" % float(part.get("esr", 0.0)))
            kind = part.get("type")
            if kind in KIND_ORDER:
                self.part_rows[index]["kind"].SetSelection(KIND_ORDER.index(kind))
            value = part.get("value")
            if value is not None and kind in ENTRY_SCALE:
                self.part_rows[index]["value"].ChangeValue("%g" % (float(value) / ENTRY_SCALE[kind]))
        self._on_lumped(None)
        self._on_subregion_change(wx.CommandEvent())
        self._redraw_preview()

    def _on_subregion_change(self, evt):
        self.subregion_info.SetLabel(_port_subregion_text(
            self._preview_ports, self.margin.GetValue(), self._preview_lumped))
        self.Layout()
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
        """Put "Port N" and its problem tag into the label of each port.

        N is the number that the choice of that row gives now, and not the
        number of the selection. Two rows can hold the same number for a
        short time; _on_ok refuses that. A tag names what stops every
        de-embedded type: "[No Track]". A coplanar gap that is absent gets
        no tag. It stops the CPW type alone, and a board that is not a CPW
        is the usual case. The type choice already shows the measured gap,
        or leaves the CPW entry out.
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
        meaning. Thus the fields of a row go off with its Model checkbox.
        """
        for i, (_, cb, ch, esl, esr) in enumerate(self.para_rows):
            # A part that the model does not contain has no parasitics.
            # "No parasitics" makes an ideal element, thus its two fields
            # have no meaning either.
            on = cb.GetValue()
            ch.Enable(on)
            for c in (esl, esr):
                c.Enable(on and self._pkg_of(ch) != NO_PARASITICS)
            # The type stays on with the Model off, thus the user can
            # select it first and model the part after it. The value
            # needs a type: the unit comes from it.
            self.part_rows[i]["value"].Enable(
                on and self._kind_of(i) is not None)
        self._update_lumped_warning()
        self._redraw_preview()
        if evt is not None:
            evt.Skip()

    def _fit_rows(self):
        """Give the window of the part rows its height limit.

        The dialog was 1053 px tall with ONE part on a screen of
        1920x1080, and each part after it added 29 px. Thus a board with
        15 parts made a dialog that no screen shows. The window now stops
        at MAX_PART_ROWS rows, or at one quarter of the screen if that is
        less, and the rest of the rows come with the scroll bar.
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
        # The scroll bar takes some width, thus the rows do not lose a
        # column when it appears.
        w = best.GetWidth() + (wx.SystemSettings.GetMetric(
            wx.SYS_VSCROLL_X) if best.GetHeight() > cap else 0)
        area.SetMinSize((w, int(min(best.GetHeight(), cap)) + 2))
        area.FitInside()
        self._one_row = one

    def _fit_to_screen(self):
        """Hold the dialog inside the screen, and let the body scroll.

        `Fit()` gives the dialog the height of all its content, and that
        is more than a screen of 1920x1080 gives even with ONE part.
        This cuts the height to the client area of the display; the
        scrolled body then shows a scroll bar, and the Run button stays
        visible because it is outside that body.
        """
        try:
            area = wx.Display().GetClientArea()
        except Exception:
            return
        w, h = self.GetSize()
        # Keep a little space for the frame of the window itself.
        avail = max(240, area.GetHeight() - 40)
        if h > avail:
            self.SetSize((w, avail))
            self.Layout()

    def _update_lumped_warning(self):
        """Show what a lumped inductor costs in run time.

        `runner._time_step_factor` divides the timestep by sqrt(L[nH])
        and multiplies the number of timesteps by the same value. Thus
        the run time goes up with the square root of the inductance, and
        the user must see it BEFORE the run and not after it.
        """
        label = getattr(self, "lumped_warn", None)
        if label is None:
            return
        ind = []
        for i, (_, cb, ch, esl, _) in enumerate(self.para_rows):
            if not cb.GetValue():
                continue
            if self._kind_of(i) == "L":
                ind.append(self._part_value(i) or 0.0)
            ind.append(self._para_value(ch, esl, 1e-9) or 0.0)
        nh = 1e9 * max(ind or [0.0])
        text = ""
        if nh > 1.0:
            text = ("An inductor of %g nH divides the timestep by %.1f, "
                    "thus the run takes about %.1f times longer."
                    % (nh, nh ** 0.5, nh ** 0.5))
        if label.GetLabel() != text:
            label.SetLabel(text)
            label.Wrap(560)
            self.Layout()

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
        # A part that the user models needs a type and a value. A part
        # whose refdes does not give the type starts with "Unknown" and
        # with its Model checkbox off, thus this test speaks only when
        # the user turned that part on and gave it nothing.
        for i, r in enumerate(self.part_rows):
            if not self.para_rows[i][1].GetValue():
                continue
            if (self.port_focused_subregion.GetValue()
                    and not _element_in_port_subregion(
                        self._preview_lumped[i], self._preview_ports,
                        self.margin.GetValue())):
                continue
            if self._kind_of(i) is None:
                wx.MessageBox(
                    'Element "%s" has no type. Select Resistor, Capacitor '
                    "or Inductor, or clear its Model checkbox."
                    % r["ref"], "RFsim", wx.ICON_ERROR)
                return
            v = self._part_value(i)
            if v is None or v <= 0:
                wx.MessageBox(
                    'Element "%s" needs a value in %s: a positive number.'
                    % (r["ref"], ENTRY_UNITS[self._kind_of(i)]),
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
        """Put the values of the package into the fields of that row.

        Each write uses ChangeValue, which sends no EVT_TEXT. Thus the
        choice stays where the user put it: SetValue would send the
        event and _on_para_edit would move the row to "Custom".

        "No parasitics" writes 0 into the two fields and greys them, thus
        the row shows exactly what the solver gets: an ideal element. The
        values come back when the row takes a package again - the ESL
        from the preset, and the ESR from the type of the part.
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
        field for the value goes on. "Unknown" turns it off again, and
        the text stays: the value of the user comes back if the user
        selects a type again. The NUMBER does not change with the type,
        thus 50 becomes 50 ohm, 50 nH or 50 pF. The unit beside it says
        which one.
        """
        r = self.part_rows[i]
        kind = self._kind_of(i)
        r["qty"].SetLabel(_qty_label(kind))
        r["unit"].SetLabel(ENTRY_UNITS.get(kind, ""))
        r["value"].Enable(kind is not None
                          and self.para_rows[i][1].GetValue())
        # The ESR of the body comes from the TYPE of the part, in the
        # same way as it does for a part that the board describes. A row
        # that is on "No parasitics" keeps its 0 and takes this value
        # when it goes back to a package. ChangeValue sends no EVT_TEXT,
        # thus the package choice of that row stays where it is.
        r["esr0"] = "%g" % self._esr.get(kind, 0.0)
        if self._pkg_of(self.para_rows[i][2]) != NO_PARASITICS:
            self.para_rows[i][4].ChangeValue(r["esr0"])
        self._update_lumped_warning()
        self.Layout()

    def _part_value(self, i):
        """Give the value of a row in SI units, or give None.

        Every row is open, thus the value of every row comes from its
        field: model.json then holds what the dialog showed, and it
        stays the one source of truth. The field starts with the value
        that the Value field of the part gave.
        """
        kind = self._kind_of(i)
        try:
            return float(self.part_rows[i]["value"].GetValue()) \
                * ENTRY_SCALE[kind]
        except (ValueError, KeyError):
            return None

    def _on_para_edit(self, i, evt):
        """Move the choice of that row to Custom when the user types.

        Select CUSTOM_PKG by its index, and not the LAST entry:
        `_pkg_values` ends with "Custom" and then "No parasitics". The
        last entry gave "No parasitics", thus `_para_value` gave 0 for
        the ESR and for the ESL and the part became IDEAL, with no
        message.
        """
        ch = self.para_rows[i][2]
        ch.SetSelection(self._pkg_values.index(CUSTOM_PKG))
        self._update_lumped_warning()
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
            "port_focused_subregion": self.port_focused_subregion.GetValue(),
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
            # Every row gives its type and its value, also a row that
            # the user did not touch: the dialog SHOWS what the parser
            # read, thus what the dialog shows is what model.json holds.
            # "type" is None for a row that stays at "Unknown", and
            # rfsim.py then drops that part.
            "lumped_parasitics": {
                ref: {"model": cb.GetValue(),
                      "package": self._pkg_of(ch),
                      "esl": self._para_value(ch, esl, 1e-9),
                      "esr": self._para_value(ch, esr),
                      "type": self._kind_of(i),
                      "value": self._part_value(i)}
                for i, (ref, cb, ch, esl, esr) in enumerate(self.para_rows)},
            "outdir": self.outdir.GetPath(),
            "n_freq": 401,
            "max_timesteps": int(float(self.max_steps.GetValue())),
            "end_criteria": float(self.end_crit.GetValue()),
            # An empty field gives None. `runner._time_step_factor` then
            # selects the value from the largest inductance of the model,
            # and a value here has priority over it.
            "time_step_factor": (float(self.tsf.GetValue())
                                 if self.tsf.GetValue().strip() else None),
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
            if tag in ff.get("cuts", {}):    # "(Phi=0)" and similar: a 2D cut
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
        """Draw the top view of the model.

        The preview of the settings dialog uses the same function.
        """
        _draw_board(ax, self.model)

    def _plot_lines(self, ax):
        """Draw the real part and the imaginary part of Z0 of each port.

        These values come from the voltage probes and the current probes
        of a de-embedded port. Thus they are the impedance of the real
        track on the real stackup, and not the reference impedance of the
        system. A lumped port has no line, thus it is not in this view.

        A good line gives an almost real Z0: Im(Z0) stays near zero and
        it is a little below it. A large Im(Z0) shows a bad extraction,
        or a lossy line.

        The formula divides by the field at the measurement plane. Thus
        the values are noisy where the excitation has little energy,
        usually at the two ends of the sweep. The limits of the axis use
        percentiles, and not the extreme values.
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
            # One port needs no tag in the legend, because the name of the
            # port adds nothing.
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
        """Give the z of the plane that the field dump lies on, in mm.

        `runner.build` puts the dump at the middle of the substrate
        between the layer of the EXCITED port and the layer below it:
        `0.5 * (z_top + z_ref)`. The title of the view names the value,
        because a field picture with no plane is a picture of nothing.
        """
        z_of = {c["name"]: c["z"] for c in self.model["copper_layers"]}
        ports = self.model["ports"]
        p = next((q for q in ports if q["number"] == port), ports[0])
        return 0.5 * (z_of[p["layer"]] + z_of[p["ref_layer"]])

    def _plot_field(self, kind, port=None):
        """Show an animation of the wave on the mid-plane of the substrate.

        The picture is the size of the field vector at one phase, in the
        style of CST: a scale from zero to the largest value that the
        animation reaches. A block of text at the left gives the
        frequency, the phase and that largest value.

        The values are the values of openEMS for its excitation, which
        has an amplitude of 1. Thus V/m and A/m are correct units, but
        the size of the input signal, and not 1 W, sets the size of the
        numbers.
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
            """The size of the real field vector at the phase of frame i."""
            return np.linalg.norm(
                np.real(F * np.exp(2j * np.pi * i / frames)), axis=-1)

        # The scale is fixed for the whole animation: a scale that moves
        # with the frame makes every frame look the same. The colour
        # runs smoothly from zero to the largest value: a scale of
        # steps, as the bar of CST, makes bands of one colour that look
        # like large pixels.
        vmax = max(float(mag(i).max()) for i in range(frames)) or 1.0
        ticks = np.linspace(0.0, vmax, 10)
        mesh = ax.pcolormesh(x, y, mag(0), cmap="jet", vmin=0.0, vmax=vmax,
                             shading="gouraud")
        bar = self.figure.colorbar(mesh, ax=ax, shrink=0.85, ticks=ticks)
        # The unit sits over the bar, as CST, and not at its side: a
        # word that stands up needs the reader to turn their head.
        bar.ax.set_title(unit, fontsize=9)
        # Each mark carries its own value, and the two ends of the bar
        # carry theirs. A common factor above the bar, which is the
        # default, hides how large a step is.
        bar.ax.set_yticklabels(["%.3g" % t for t in ticks], fontsize=7)

        top = self.model["ports"][0]["layer"]
        for poly in self.model["polygons"].get(top, []):
            ax.plot([p[0] for p in poly] + [poly[0][0]],
                    [p[1] for p in poly] + [poly[0][1]], color="0.2", lw=0.6)
        # **The ports, in the same lime as the Board layout view.** The
        # field views are the pictures that leave the tool, and a
        # reviewer of 2026-08-03 read the port at the wrong place
        # because no picture showed it.
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

        # The block of numbers at the left. The PLANE goes with them:
        # it is the plane that `runner.build` dumps, the middle of the
        # substrate between the layer of the excited port and the layer
        # below it, and a field picture with no plane shows nothing.
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
        dBi in a range of 30 dB. +z is the normal of the board. A block
        of text at the left gives the numbers, in the style of CST.
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
        ax.set_title("Farfield (f=%g GHz)%s" % (ff["f_hz"] / 1e9, ptag),
                     fontsize=10)
        sm = cm.ScalarMappable(norm=norm, cmap=cm.jet)
        sm.set_array([])
        # The marks of the default carry round numbers, and they stop
        # before the two ends of the bar: a reader then cannot see the
        # value at the top, which is Dmax, or the value at the floor.
        ticks = np.linspace(rmin, float(D.max()), 9)
        bar = self.figure.colorbar(sm, ax=ax, shrink=0.65, ticks=ticks)
        bar.ax.set_yticklabels(["%.1f" % t for t in ticks], fontsize=7)
        bar.ax.set_title("dBi", fontsize=9)   # over the bar, as CST

        # The block of numbers at the left. The radiation efficiency is
        # the radiated power over the ACCEPTED power. The total
        # efficiency also counts the power that the mismatch reflects,
        # thus it is the radiation efficiency times 1 - |Snn|^2.
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

        This is the part of the total efficiency that the reflection of
        the port takes away. Port 1 is the port of a run that excited
        one port only.
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
