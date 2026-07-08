"""wxPython dialogs: simulation settings, solver log, results plots."""
import os
import subprocess
import threading

import wx

PORT_TYPES = [("Lumped", "lumped"), ("Microstrip (MSL)", "msl")]
MESH_LEVELS = ["coarse", "medium", "fine"]


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, ports, default_outdir):
        wx.Dialog.__init__(self, parent, title="RFsim - openEMS simulation")
        self._build(ports, default_outdir)

    def _build(self, ports, default_outdir):
        grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        grid.AddGrowableCol(1)

        def row(label, ctrl):
            grid.Add(wx.StaticText(self, label=label),
                     0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 0, wx.EXPAND)
            return ctrl

        self.f_start = row("Start frequency (GHz)", wx.TextCtrl(self, value="1.0"))
        self.f_stop = row("Stop frequency (GHz)", wx.TextCtrl(self, value="6.0"))
        self.z0 = row("Port impedance (ohm)", wx.TextCtrl(self, value="50"))
        self.margin = row("Domain margin (mm)", wx.SpinCtrlDouble(
            self, min=2.0, max=25.0, initial=4.0, inc=0.5))
        self.mesh = row("Mesh resolution", wx.Choice(self, choices=MESH_LEVELS))
        self.mesh.SetSelection(1)

        self.port_choices = []
        for p in ports:
            note = "" if p["direction"] else "  [no track found: lumped only]"
            ch = row("Port %d: %s%s" % (p["number"], p["label"], note),
                     wx.Choice(self, choices=[t[0] for t in PORT_TYPES]))
            ch.SetSelection(0)
            ch.Enable(bool(p["direction"]))
            self.port_choices.append(ch)

        self.swap = None
        if len(ports) == 2:
            self.swap = wx.CheckBox(self, label="Swap port order")
            grid.Add(wx.StaticText(self, label=""))
            grid.Add(self.swap)

        self.outdir = row("Output directory", wx.DirPickerCtrl(
            self, path=default_outdir, style=wx.DIRP_USE_TEXTCTRL))

        top = wx.BoxSizer(wx.VERTICAL)
        top.Add(grid, 1, wx.ALL | wx.EXPAND, 12)
        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        self.FindWindowById(wx.ID_OK).SetLabel("Run Simulation")
        top.Add(btns, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(top)
        self.SetMinSize((520, -1))
        self.Fit()
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, evt):
        try:
            fa, fb = float(self.f_start.GetValue()), float(self.f_stop.GetValue())
            z0 = float(self.z0.GetValue())
            if not (0 < fa < fb) or z0 <= 0:
                raise ValueError
        except ValueError:
            wx.MessageBox("Check frequency range / impedance values.",
                          "RFsim", wx.ICON_ERROR)
            return
        evt.Skip()

    def get_settings(self):
        return {
            "f_start": float(self.f_start.GetValue()) * 1e9,
            "f_stop": float(self.f_stop.GetValue()) * 1e9,
            "z0": float(self.z0.GetValue()),
            "margin_mm": self.margin.GetValue(),
            "mesh": MESH_LEVELS[self.mesh.GetSelection()],
            "port_types": [PORT_TYPES[c.GetSelection()][1]
                           for c in self.port_choices],
            "swap": bool(self.swap and self.swap.GetValue()),
            "outdir": self.outdir.GetPath(),
            "n_freq": 401,
            "max_timesteps": 300000,  # ponytail: fixed cap; expose if high-Q
            "end_criteria": 1e-4,     # structures need longer ringdown
        }


class RunDialog(wx.Dialog):
    """Runs the solver subprocess, streaming its output into a log window."""

    def __init__(self, parent, cmd):
        wx.Dialog.__init__(self, parent, title="RFsim - solver running",
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


class ResultsFrame(wx.Frame):
    """Plot viewer for the produced Touchstone file (needs skrf+matplotlib)."""

    def __init__(self, parent, touchstone_path):
        import matplotlib
        matplotlib.use("WXAgg", force=False)
        from matplotlib.backends.backend_wxagg import (
            FigureCanvasWxAgg, NavigationToolbar2WxAgg)
        from matplotlib.figure import Figure
        import skrf

        wx.Frame.__init__(self, parent, title="RFsim results - %s"
                          % os.path.basename(touchstone_path), size=(820, 620))
        self.net = skrf.Network(touchstone_path)
        plots = ["S-parameters (dB)"]
        if self.net.nports >= 1:
            plots += ["Smith chart (S11)", "VSWR (port 1)"]
        if self.net.nports == 2:
            plots += ["Group delay (S21)"]
        self.choice = wx.Choice(self, choices=plots)
        self.choice.SetSelection(0)
        self.figure = Figure(figsize=(8, 5.5))
        self.canvas = FigureCanvasWxAgg(self, -1, self.figure)
        toolbar = NavigationToolbar2WxAgg(self.canvas)
        toolbar.Realize()

        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(self.choice, 0, wx.ALL, 6)
        s.Add(self.canvas, 1, wx.EXPAND)
        s.Add(toolbar, 0, wx.EXPAND)
        self.SetSizer(s)
        self.choice.Bind(wx.EVT_CHOICE, lambda e: self._plot())
        self._plot()

    def _plot(self):
        import numpy as np
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        net, f_ghz = self.net, self.net.f / 1e9
        sel = self.choice.GetStringSelection()

        if sel.startswith("S-parameters"):
            for j in range(net.nports):
                for k in range(net.nports):
                    ax.plot(f_ghz, net.s_db[:, j, k],
                            label="S%d%d" % (j + 1, k + 1))
            ax.set_ylabel("|S| (dB)")
            ax.legend()
        elif sel.startswith("Smith"):
            net.plot_s_smith(m=0, n=0, ax=ax)
        elif sel.startswith("VSWR"):
            mag = np.clip(np.abs(net.s[:, 0, 0]), 0, 0.999999)
            ax.plot(f_ghz, (1 + mag) / (1 - mag))
            ax.set_ylabel("VSWR")
            ax.set_ylim(1, min(20, ax.get_ylim()[1]))
        else:  # group delay
            phase = np.unwrap(np.angle(net.s[:, 1, 0]))
            gd = -np.gradient(phase, 2 * np.pi * net.f) * 1e9
            ax.plot(f_ghz, gd)
            ax.set_ylabel("Group delay (ns)")

        if not sel.startswith("Smith"):
            ax.set_xlabel("Frequency (GHz)")
            ax.grid(True, alpha=0.4)
        self.figure.tight_layout()
        self.canvas.draw()
