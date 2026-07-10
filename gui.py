"""wxPython dialogs: simulation settings, solver log, results plots."""
import glob
import json
import os
import re
import subprocess
import threading

import wx

PORT_TYPES = [("Lumped", "lumped"), ("Microstrip (MSL)", "msl")]
MESH_LEVELS = ["coarse", "medium", "fine"]
SUBSTRATE_PRESETS = [("FR-4", 4.2, 0.02),
                     ("Rogers RO4350B", 3.48, 0.0037),
                     ("Custom", None, None)]


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, ports, default_outdir, lumped=()):
        wx.Dialog.__init__(self, parent, title="RFsim")
        self._build(ports, default_outdir, lumped)

    def _build(self, ports, default_outdir, lumped):
        top = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="RFsim v1.0")
        title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                              wx.FONTWEIGHT_BOLD))
        top.Add(title, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 10)
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
        # field dumps (E/H animation) + far-field are computed at this freq
        self.f_field = row(fg, "Define at:",
                           wx.TextCtrl(self, value="2.4"), "GHz")

        pbox = section("Port")
        pg = grid_in(pbox)
        self.z0 = row(pg, "Port impedance:",
                      wx.TextCtrl(self, value="50"), "ohm")
        self.port_choices = []
        self.port_order = []
        self.port_excite = []
        nums = [str(i + 1) for i in range(len(ports))]
        for i, p in enumerate(ports):
            note = "" if p["direction"] else "  [no track: lumped only]"
            num = wx.Choice(self, choices=nums)
            num.SetSelection(i)
            num.Enable(len(ports) > 1)
            ch = wx.Choice(self, choices=[t[0] for t in PORT_TYPES])
            ch.SetSelection(0)
            ch.Enable(bool(p["direction"]))
            exc = wx.CheckBox(self, label="Excite")
            exc.SetValue(True)
            h = wx.BoxSizer(wx.HORIZONTAL)
            h.Add(num, 0, wx.RIGHT, 8)
            h.Add(ch, 1, wx.EXPAND)
            h.Add(exc, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
            pg.Add(wx.StaticText(self, label="%s%s" % (p["label"], note)),
                   0, wx.ALIGN_CENTER_VERTICAL)
            pg.Add(h, 0, wx.EXPAND)
            self.port_choices.append(ch)
            self.port_order.append(num)
            self.port_excite.append(exc)
        if len(ports) > 1:
            pbox.Add(wx.StaticText(
                self, label="The number assigns the port (excited in that "
                "order; port 1 drives the field/far-field views). Excite = "
                "drive this port (one FDTD run each); uncheck ports whose "
                "S-columns you don't need."),
                0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.lumped = None
        if lumped:
            refs = ", ".join(e["ref"] for e in lumped)
            self.lumped = wx.CheckBox(
                self, label="Model SMD R/L/C parts as lumped elements "
                "(%d found: %s)" % (len(lumped), refs))
            self.lumped.SetValue(True)
            pbox.Add(self.lumped, 0, wx.ALL, 6)

        sbox = section("Substrate")
        sg = grid_in(sbox)
        self.preset = row(sg, "Presets:", wx.Choice(
            self, choices=[p[0] for p in SUBSTRATE_PRESETS]))
        self.preset.SetSelection(0)
        # defaults: 1.6 mm FR4, 35 um (1 oz) copper
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

    def _on_preset(self, evt):
        _, er, tand = SUBSTRATE_PRESETS[self.preset.GetSelection()]
        if er is not None:  # ChangeValue: no EVT_TEXT, stays on the preset
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
            if (not (0 < fa < fb) or not (fa <= fd <= fb) or z0 <= 0
                    or er < 1 or tand < 0 or h <= 0 or cu_t <= 0):
                raise ValueError
        except ValueError:
            wx.MessageBox(
                "Check frequency / impedance / substrate values.\n"
                "('Define at' must lie inside the sweep range.)",
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
            "mesh": MESH_LEVELS[self.mesh.GetSelection()],
            "port_types": [PORT_TYPES[c.GetSelection()][1]
                           for c in self.port_choices],
            "order": [c.GetSelection() + 1 for c in self.port_order],
            # excite carries FINAL port numbers (after renumbering), which is
            # what the runner matches against
            "excite": sorted(num.GetSelection() + 1
                             for num, cb in zip(self.port_order,
                                                self.port_excite)
                             if cb.GetValue()),
            "lumped": bool(self.lumped and self.lumped.GetValue()),
            "outdir": self.outdir.GetPath(),
            "n_freq": 401,
            "max_timesteps": 300000,  # ponytail: fixed cap; expose if high-Q
            "end_criteria": 1e-4,     # structures need longer ringdown
        }


class RunDialog(wx.Dialog):
    """Runs the solver subprocess, streaming its output into a log window."""

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
    """openEMS FD dump -> (x_mm, y_mm, complex F[y, x, 3], f_hz). No wx needed."""
    import h5py
    import numpy as np
    with h5py.File(h5_path, "r") as f:
        mesh = f["Mesh"]
        x, y = np.asarray(mesh["x"]), np.asarray(mesh["y"])
        fd = f["FieldData"]["FD"]
        f_hz = float(fd.attrs["frequency"][0])
        F = np.asarray(fd["f0_real"]) + 1j * np.asarray(fd["f0_imag"])
    if float(x.max() - x.min()) < 1.0:  # meters -> mm (domains are > 1 mm)
        x, y = x * 1e3, y * 1e3
    F = np.squeeze(F)                   # drop the length-1 z-plane axis
    if F.shape[0] == 3:                 # component axis first -> last
        F = np.moveaxis(F, 0, -1)
    if F.shape[:2] == (len(x), len(y)):
        F = np.swapaxes(F, 0, 1)
    return x, y, F, f_hz


def _lobe_stats(ang_deg, D):
    """Main-lobe direction, 3 dB width and side-lobe level of a closed cut."""
    import numpy as np
    ang = np.asarray(ang_deg, float)
    d = np.asarray(D, float)
    if abs((ang[-1] - ang[0]) - 360.0) < 1e-6:  # closed cut: drop dup endpoint
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
    # main lobe = walk to the first local minimum on each side; the rest of
    # the pattern holds the side lobes
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
    """Plot viewer for the produced Touchstone file (needs skrf+matplotlib)."""

    def __init__(self, parent, touchstone_path):
        import matplotlib
        matplotlib.use("WXAgg", force=False)
        # KiCad's bundled wxPython lacks the compiled wx.svg._nanosvg
        # extension; matplotlib's wx backend imports wx.svg but never uses
        # it, so stub it out when the real module is broken.
        try:
            import importlib
            importlib.import_module("wx.svg")
        except ImportError:
            import sys
            import types
            sys.modules["wx.svg"] = types.ModuleType("wx.svg")
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
        # per-excitation outputs: excN/[EH]f.h5 + farfield_pN.json for every
        # excited port N; plain farfield.json = legacy single-farfield runs
        self.field_h5s = {}  # (kind, port) -> h5 path
        for k in ("E", "H"):
            for hit in glob.glob(os.path.join(self.outdir, "exc*",
                                              k + "f.h5")):
                p = int(re.search(r"exc(\d+)", hit).group(1))
                self.field_h5s[(k, p)] = hit
        self._ff = {}  # port (0 = legacy/unknown) -> farfield dict
        for path in glob.glob(os.path.join(self.outdir, "farfield*.json")):
            m = re.search(r"farfield_p(\d+)", os.path.basename(path))
            try:
                with open(path) as fh:
                    self._ff[int(m.group(1)) if m else 0] = json.load(fh)
            except Exception:
                pass
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
        # default min size = figure's native 800x550: the sizer then can't
        # shrink the canvas and the bottom axis gets clipped instead
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
        # the canvas only adopts its sizer-given size after a size event;
        # without this the figure paints at its native size and the bottom
        # axis label is clipped until the user resizes the window
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
        elif sel.startswith(("E-Field", "H-Field")):
            self._plot_field(ax, sel[0], pnum)
        elif sel.startswith("Farfield"):
            ax.remove()
            ff = self._ff[pnum if pnum in self._ff else sorted(self._ff)[0]]
            ptag = " (Port %d)" % pnum if pnum else ""
            tag = base[base.rfind("(") + 1:-1]
            if tag in ff.get("cuts", {}):    # "(Phi=0)" etc. -> 2D cut
                self._plot_farfield(ff, tag, ptag)
            else:                            # bare "(f=xx GHz)" -> 3D balloon
                self._plot_farfield3d(ff, ptag)
        elif sel.startswith("S-Parameters"):
            phase = sel.endswith("[Phase]")
            for j in range(net.nports):
                for k in range(net.nports):
                    if not np.any(np.abs(net.s[:, j, k]) > 1e-9):
                        continue  # port k not excited -> column not computed
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
                    continue  # port i not excited -> S_ii not computed
                net.plot_s_smith(m=i, n=i, ax=ax, draw_labels=grid)
                grid = False
            for ln in ax.get_lines():  # skrf labels "name, S11" -> "S11"
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
        else:  # group delay: all computed transmission pairs in one graph
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
        """Top view of the simulated model: B.Cu blue, F.Cu red, ports green."""
        from matplotlib.patches import Patch
        m = self.model
        colors = {"F.Cu": ("tab:red", 0.8), "B.Cu": ("tab:blue", 0.45)}
        handles = []
        for c in reversed(m["copper_layers"]):  # bottom first, F.Cu on top
            name = c["name"]
            col, alpha = colors.get(name, ("0.5", 0.5))
            polys = m["polygons"].get(name, [])
            for poly in polys:
                ax.fill([p[0] for p in poly], [p[1] for p in poly],
                        color=col, alpha=alpha, linewidth=0)
            if polys:
                handles.append(Patch(color=col, alpha=alpha, label=name))
        for v in m["vias"]:
            ax.plot(v["x"], v["y"], "o", color="k", ms=3)
        br, rg = m["board_rect"], m["region"]
        ax.plot([br["x0"], br["x1"], br["x1"], br["x0"], br["x0"]],
                [br["y0"], br["y0"], br["y1"], br["y1"], br["y0"]],
                color="0.25", lw=1.2)
        ax.plot([rg["x0"], rg["x1"], rg["x1"], rg["x0"], rg["x0"]],
                [rg["y0"], rg["y0"], rg["y1"], rg["y1"], rg["y0"]],
                "--", color="0.6", lw=0.8)
        for p in m["ports"]:
            hw, hl = p["width"] / 2.0, p["length"] / 2.0
            ax.fill([p["x"] - hl, p["x"] + hl, p["x"] + hl, p["x"] - hl],
                    [p["y"] - hw, p["y"] - hw, p["y"] + hw, p["y"] + hw],
                    color="lime")
            ax.annotate("P%d" % p["number"], (p["x"], p["y"]),
                        ha="center", va="bottom", xytext=(0, 5),
                        textcoords="offset points", fontsize=9,
                        fontweight="bold", color="darkgreen")
        handles.append(Patch(color="lime", label="ports"))
        les = (m.get("lumped_elements", [])
               if m["settings"].get("lumped", True) else [])
        for e in les:
            (x0, y0), (x1, y1) = e["start"][:2], e["stop"][:2]
            ax.fill([x0, x1, x1, x0], [y0, y0, y1, y1], color="green",
                    zorder=5)
            # pad-to-pad line: the gap box alone is sub-mm, invisible at
            # board zoom — the line shows what the element connects
            (px0, py0), (px1, py1) = e.get(
                "pads", (e["start"][:2], e["stop"][:2]))
            ax.plot([px0, px1], [py0, py1], "-o", color="green", lw=2,
                    ms=5, zorder=5)
            ax.annotate(e["ref"], (0.5 * (px0 + px1), 0.5 * (py0 + py1)),
                        ha="center", va="bottom", xytext=(0, 6),
                        textcoords="offset points", fontsize=9,
                        fontweight="bold", color="darkgreen", zorder=6)
        if les:
            from matplotlib.lines import Line2D
            handles.append(Line2D([], [], color="green", lw=2, marker="o",
                                  ms=5, label="R/L/C"))
        from matplotlib.lines import Line2D
        handles.append(Line2D([], [], color="0.25", lw=1.2,
                              label="Board edge"))
        handles.append(Line2D([], [], ls="--", color="0.6", label="Domain"))
        ax.legend(handles=handles, loc="upper right", fontsize=8)
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.set_title("Board layout")
        ax.set_aspect("equal")

    def _plot_field(self, ax, kind, port=None):
        """Traveling-wave animation on the substrate mid-plane.

        Same signed red/blue view for both fields: E shows E_z, H shows the
        dominant in-plane H component (H loops around the trace, so its
        vertical part is ~zero on this plane).
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
            comp = F[..., 2]                 # vertical E under the trace
        else:
            hx, hy = np.abs(F[..., 0]).max(), np.abs(F[..., 1]).max()
            comp = F[..., 0] if hx >= hy else F[..., 1]  # dominant in-plane H
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
        """Transparent 3D directivity balloon with the PCB as a reference plate.

        radius/color = dBi over a 30 dB range, +z = board normal.
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
        fc[..., 3] = 0.3  # transparent balloon so the board shows through
        ax.plot_surface(X, Y, Z, facecolors=fc, rstride=1, cstride=1,
                        linewidth=0, antialiased=False, shade=False)
        m = float(R.max()) or 1.0

        # PCB reference plate at the origin, oriented as in Board layout
        # (+z = board normal). Scale is a display choice: the far field is
        # notionally infinitely far, so the board is a size-less orientation
        # marker -> largest board dim ~= half the balloon radius.
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
        """One polar directivity cut in absolute dBi (CST-style)."""
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
        ax.set_rlabel_position(270)  # dBi numbers along the right, CST-style
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
