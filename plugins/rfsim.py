"""The RFsim action plugin: it simulates the S-parameters of the selected
pads with openEMS."""
import importlib.util
import json
import os
import subprocess
import sys

import pcbnew
import wx

from . import board_reader, gui, solverenv

NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def _kicad_python():
    """Give the python.exe of KiCad (sys.executable can be pcbnew.exe)."""
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python") and os.path.isfile(exe):
        return exe
    for c in (os.path.join(os.path.dirname(exe), "python.exe"),
              os.path.join(sys.prefix, "python.exe"),
              os.path.join(sys.prefix, "bin", "python.exe")):
        if os.path.isfile(c):
            return c
    return "python"


def _solver_missing(exe):
    """Give the modules that runner.py needs but that `exe` does not have.

    The subprocess uses find_spec, which does not import the extensions.
    Thus an absent openEMS DLL does not look like an absent package.
    """
    code = ("import importlib.util as u\n"
            "print(','.join(m for m in ('numpy', 'h5py', 'CSXCAD', 'openEMS')\n"
            "               if u.find_spec(m) is None))")
    try:
        r = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                           timeout=60, creationflags=NO_WINDOW)
    except Exception as e:
        return ["(cannot run %s: %s)" % (exe, e)]
    if r.returncode != 0:
        return ["(probe failed: %s)" % (r.stderr or "").strip()[-200:]]
    return [m for m in r.stdout.strip().split(",") if m]


class RFSimPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "RFsim"
        self.category = "RF tools"
        self.description = ("Simulate S-parameters of the selected pad(s) "
                            "with openEMS")
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__),
                                           "assets", "icon.png")

    def Run(self):
        try:
            self._run()
        except ValueError as e:  # a setup problem: show a message, not a traceback
            wx.MessageBox(str(e), "RFsim", wx.ICON_ERROR)
        except Exception:
            import traceback
            wx.MessageBox(traceback.format_exc(), "RFsim error", wx.ICON_ERROR)

    def _run(self):
        # The results window runs in the Python of KiCad. The solver runs
        # in its own interpreter, because openEMS v0.37 and later have no
        # cp311 wheel. Thus the code examines the two sets of packages
        # one after the other.
        solver_py = solverenv.solver_python() or _kicad_python()
        gui_missing = [m for m in ("skrf", "matplotlib", "h5py")
                       if importlib.util.find_spec(m) is None]
        solver_missing = _solver_missing(solver_py)
        if gui_missing or solver_missing:
            msg = []
            if gui_missing:
                msg.append("Missing in KiCad's Python (results window): %s"
                           % ", ".join(gui_missing))
            if solver_missing:
                msg.append("Missing in the solver Python\n%s\n%s"
                           % (solver_py, ", ".join(solver_missing)))
            wx.MessageBox("\n\n".join(msg)
                          + "\n\nSee the plugin README for install "
                            "instructions.", "RFsim", wx.ICON_ERROR)
            return

        board = pcbnew.GetBoard()
        pads = board_reader.selected_pads(board)
        if len(pads) < 1:
            wx.MessageBox(
                "Select at least one pad to run a simulation.",
                "RFsim", wx.ICON_INFORMATION)
            return

        # Get the port data for the preview in the dialog. The margin has
        # no effect on this data.
        preview = board_reader.extract(board, pads, 1.0)
        default_out = os.path.join(
            os.path.dirname(board.GetFileName()) or os.getcwd(), "rfsim_results")
        dlg = gui.SettingsDialog(None, preview["ports"], default_out,
                                 preview.get("lumped_elements", []),
                                 preview=preview)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        settings = dlg.get_settings()
        dlg.Destroy()

        port_types = settings.pop("port_types")
        # the numbers from the dialog: pad i becomes port order[i], and
        # the types move with the pads
        order = settings.pop("order")
        pads = [p for _, p in sorted(zip(order, pads), key=lambda t: t[0])]
        port_types = [t for _, t in
                      sorted(zip(order, port_types), key=lambda t: t[0])]
        outdir = settings.pop("outdir")
        substrate = {k: settings.pop(k) for k in ("er", "tand", "h", "cu_t")}

        model = board_reader.extract(board, pads, settings["margin_mm"],
                                     substrate)
        if model["warnings"]:
            wx.MessageBox("\n\n".join(model["warnings"]),
                          "RFsim", wx.ICON_WARNING)
        for p, t in zip(model["ports"], port_types):
            p["type"] = t
        model["settings"] = settings

        os.makedirs(outdir, exist_ok=True)
        model_path = os.path.join(outdir, "model.json")
        with open(model_path, "w") as fh:
            json.dump(model, fh, indent=1)

        runner = os.path.join(os.path.dirname(__file__), "runner.py")
        cmd = [solver_py, runner, model_path, outdir]
        run = gui.RunDialog(None, cmd)
        ok = run.ShowModal() == wx.ID_OK
        run.Destroy()
        if ok:
            s2p = os.path.join(outdir, "results.s%dp" % len(pads))
            gui.ResultsFrame(None, s2p).Show()
