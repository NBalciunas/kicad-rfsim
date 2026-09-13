"""Touchstone comparison view used by RFsim's results viewer."""
import os
import sys
import types

import wx


def _use_wxagg():
    """Work around KiCad's wxPython build without the optional nanosvg module."""
    import matplotlib
    matplotlib.use("WXAgg", force=False)
    try:
        __import__("wx.svg")
    except ImportError:
        sys.modules["wx.svg"] = types.ModuleType("wx.svg")


class ResultsComparisonFrame(wx.Frame):
    """Overlay matching S-parameters from two or more Touchstone files."""

    def __init__(self, parent, paths):
        _use_wxagg()
        from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg, NavigationToolbar2WxAgg
        from matplotlib.figure import Figure
        import skrf

        wx.Frame.__init__(self, parent, title="RFsim Results Comparison", size=(980, 700))
        paths = [os.path.abspath(path) for path in paths]
        root = os.path.commonpath(paths)
        if not os.path.isdir(root):
            root = os.path.dirname(root)
        self.networks = [(os.path.relpath(path, root), skrf.Network(path))
                         for path in paths]
        self.view = wx.Choice(self, choices=["S-Parameters [Magnitude]", "S-Parameters [Phase]"])
        self.view.SetSelection(0)
        self.figure = Figure(figsize=(9, 6), layout="constrained")
        self.canvas = FigureCanvasWxAgg(self, -1, self.figure)
        self.canvas.SetMinSize((360, 260))
        toolbar = NavigationToolbar2WxAgg(self.canvas)
        toolbar.Realize()
        close = wx.Button(self, wx.ID_CLOSE, "Close")

        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(self.view, 0, wx.ALL, 6)
        root.Add(self.canvas, 1, wx.EXPAND)
        root.Add(toolbar, 0, wx.EXPAND)
        root.Add(close, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 6)
        self.SetSizer(root)
        self.view.Bind(wx.EVT_CHOICE, lambda evt: self._plot())
        close.Bind(wx.EVT_BUTTON, self._on_close)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self._plot()

    def _on_close(self, evt):
        if self:
            self.Destroy()

    def _plot(self):
        import numpy as np

        self.figure.clear()
        axis = self.figure.add_subplot(111)
        phase = self.view.GetStringSelection().endswith("[Phase]")
        common_ports = min(network.nports for _, network in self.networks)
        for run_index, (label, network) in enumerate(self.networks):
            linestyle = ("-", "--", ":", "-.")[run_index % 4]
            for row in range(common_ports):
                for column in range(common_ports):
                    values = (np.degrees(np.angle(network.s[:, row, column]))
                              if phase else network.s_db[:, row, column])
                    axis.plot(network.f / 1e9, values, linestyle=linestyle,
                              label="%s: S%d%d" % (label, row + 1, column + 1))
        axis.set_title("S-Parameters [Phase]" if phase else "S-Parameters [Magnitude]")
        axis.set_xlabel("Frequency / GHz")
        axis.set_ylabel("degrees" if phase else "dB")
        axis.grid(True, alpha=0.4)
        axis.legend(fontsize=7, ncols=2)
        self.canvas.draw()
