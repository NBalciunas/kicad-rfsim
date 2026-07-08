"""RFsim action plugin: selected pads -> openEMS S-parameter simulation."""
import importlib.util
import json
import os
import sys

import pcbnew
import wx

from . import board_reader, gui


def _python_exe():
    """KiCad's bundled python.exe (sys.executable may be pcbnew.exe)."""
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python") and os.path.isfile(exe):
        return exe
    for c in (os.path.join(os.path.dirname(exe), "python.exe"),
              os.path.join(sys.prefix, "python.exe"),
              os.path.join(sys.prefix, "bin", "python.exe")):
        if os.path.isfile(c):
            return c
    return "python"


class RFSimPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "RFsim: S-parameters (openEMS)"
        self.category = "RF tools"
        self.description = ("Simulate S-parameters of the selected pad(s) "
                            "with openEMS")
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__),
                                           "assets", "icon.png")

    def Run(self):
        try:
            self._run()
        except Exception:
            import traceback
            wx.MessageBox(traceback.format_exc(), "RFsim error", wx.ICON_ERROR)

    def _run(self):
        missing = [m for m in ("openEMS", "CSXCAD", "skrf", "matplotlib", "h5py")
                   if importlib.util.find_spec(m) is None]
        if missing:
            wx.MessageBox(
                "Missing python packages in KiCad's environment: %s\n\n"
                "See the plugin README for install instructions."
                % ", ".join(missing), "RFsim", wx.ICON_ERROR)
            return

        board = pcbnew.GetBoard()
        pads = board_reader.selected_pads(board)
        if not 1 <= len(pads) <= 2:
            wx.MessageBox(
                "Select one or two pads first (click / shift-click in the "
                "PCB editor), then run RFsim.\nSelected pads: %d" % len(pads),
                "RFsim", wx.ICON_INFORMATION)
            return

        # Preview port info (margin-independent) for the dialog.
        preview = board_reader.extract(board, pads, 1.0)
        default_out = os.path.join(
            os.path.dirname(board.GetFileName()) or os.getcwd(), "rfsim_results")
        dlg = gui.SettingsDialog(None, preview["ports"], default_out)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        settings = dlg.get_settings()
        dlg.Destroy()

        if settings.pop("swap"):
            pads.reverse()
        port_types = settings.pop("port_types")
        outdir = settings.pop("outdir")

        model = board_reader.extract(board, pads, settings["margin_mm"])
        for p, t in zip(model["ports"], port_types):
            p["type"] = t
        model["settings"] = settings

        os.makedirs(outdir, exist_ok=True)
        model_path = os.path.join(outdir, "model.json")
        with open(model_path, "w") as fh:
            json.dump(model, fh, indent=1)

        runner = os.path.join(os.path.dirname(__file__), "runner.py")
        cmd = [_python_exe(), runner, model_path, outdir]
        run = gui.RunDialog(None, cmd)
        ok = run.ShowModal() == wx.ID_OK
        run.Destroy()
        if ok:
            s2p = os.path.join(outdir, "results.s%dp" % len(pads))
            gui.ResultsFrame(None, s2p).Show()
