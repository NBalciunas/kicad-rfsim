"""The RFsim action plugin: it simulates the S-parameters of the selected
pads with openEMS."""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime

import pcbnew
import wx

from . import board_reader, gui, solverenv

NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def _preview_geometry(board, pads, settings):
    """Export the current dialog geometry to XML and open AppCSXCAD."""
    settings = dict(settings)
    port_types = settings.pop("port_types")
    port_feed = settings.pop("port_feed")
    order = settings.pop("order")
    pads = [p for _, p in sorted(zip(order, pads), key=lambda item: item[0])]
    port_types = [p for _, p in sorted(zip(order, port_types), key=lambda item: item[0])]
    port_feed = [p for _, p in sorted(zip(order, port_feed), key=lambda item: item[0])]
    outdir = settings.pop("outdir")
    substrate = {key: settings.pop(key) for key in ("er", "tand", "h", "cu_t")}
    parasitics = settings.pop("lumped_parasitics", None) or {}
    subregion = bool(settings.pop("port_focused_subregion", False))
    model = board_reader.extract(board, pads, settings["margin_mm"], substrate,
                                 full_board=not subregion)
    for element in model["lumped_elements"]:
        chosen = parasitics.get(element["ref"])
        if chosen:
            element.update(package=chosen["package"], esl=chosen["esl"],
                           esr=chosen["esr"])
            if chosen.get("type"):
                element["type"] = chosen["type"]
            if chosen.get("value") is not None:
                element["value"] = chosen["value"]
    model["lumped_elements"] = [
        element for element in model["lumped_elements"]
        if parasitics.get(element["ref"], {}).get("model", True)
        and element.get("type") and element.get("value") is not None]
    for port, port_type, feed in zip(model["ports"], port_types, port_feed):
        port["type"] = port_type
        if feed and not port["direction"]:
            port["direction"], port["track_width"] = feed
            key = {(1, 0): "+x", (-1, 0): "-x", (0, 1): "+y", (0, -1): "-y"}[tuple(port["direction"])]
            port["gap"] = (port.get("gaps") or {}).get(key)
            port["copper_run"] = board_reader.copper_run(
                model["polygons"].get(port["layer"], []), port["x"], port["y"], port["direction"])
    model["settings"] = settings
    os.makedirs(outdir, exist_ok=True)
    model_path = os.path.join(outdir, "geometry_preview_model.json")
    geometry_path = os.path.join(outdir, "geometry_preview.xml")
    with open(model_path, "w", encoding="utf-8") as fh:
        json.dump(model, fh, indent=1)
    runner = os.path.join(os.path.dirname(__file__), "runner.py")
    solver_py = solverenv.solver_python() or _kicad_python()
    cmd = [solver_py, runner, "--geometry", model_path, geometry_path]
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or "Geometry export failed.")
    viewer = next((os.path.join(directory, "AppCSXCAD.exe")
                   for directory in solverenv.openems_dirs()
                   if os.path.isfile(os.path.join(directory, "AppCSXCAD.exe"))), None)
    if viewer is None:
        raise RuntimeError("AppCSXCAD.exe was not found under OPENEMS_PATH or C:\\openEMS.")
    subprocess.Popen([viewer, geometry_path], creationflags=NO_WINDOW)


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


def _run_output_dir(base_dir):
    """Create a timestamped result directory without overwriting prior runs."""
    stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    candidate = os.path.join(base_dir, stamp)
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(base_dir, "%s_%02d" % (stamp, suffix))
        suffix += 1
    os.makedirs(candidate)
    return candidate


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
                                 preview=preview,
                                 packages=board_reader.package_presets(),
                                 esr=board_reader.esr_presets(),
                                 on_view_geometry=lambda settings: _preview_geometry(
                                     board, pads, settings),
                                 on_open_results=lambda path: gui.ResultsFrame(
                                     None, path).Show())
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        settings = dlg.get_settings()
        dlg.Destroy()

        port_types = settings.pop("port_types")
        port_feed = settings.pop("port_feed")
        # the numbers from the dialog: pad i becomes port order[i], and
        # the types and the manual feeds move with the pads
        order = settings.pop("order")
        pads = [p for _, p in sorted(zip(order, pads), key=lambda t: t[0])]
        port_types = [t for _, t in
                      sorted(zip(order, port_types), key=lambda t: t[0])]
        port_feed = [f for _, f in
                     sorted(zip(order, port_feed), key=lambda t: t[0])]
        outdir = settings.pop("outdir")
        separate_run_folder = bool(settings.pop("separate_run_folder", True))
        if separate_run_folder:
            os.makedirs(outdir, exist_ok=True)
            outdir = _run_output_dir(outdir)
        substrate = {k: settings.pop(k) for k in ("er", "tand", "h", "cu_t")}
        # The parasitics of each R/L/C part, from the rows of the dialog.
        # They go into the elements below, and not into the settings:
        # model.json must hold the values that the solver uses.
        para = settings.pop("lumped_parasitics", None) or {}

        subregion = bool(settings.pop("port_focused_subregion", False))
        model = board_reader.extract(board, pads, settings["margin_mm"],
                         substrate, full_board=not subregion)
        for e in model["lumped_elements"]:
            v = para.get(e["ref"])
            if v:
                e.update(package=v["package"], esl=v["esl"], esr=v["esr"])
                # A part whose refdes does not give the type comes back
                # from extract() with type None and value None. The user
                # selected them in the dialog, thus they go in here. A
                # part that the board describes keeps its own values,
                # and the dialog gives None for both.
                if v.get("type"):
                    e["type"] = v["type"]
                if v.get("value") is not None:
                    e["value"] = v["value"]
        # A part whose Model checkbox is off does not go into the model at
        # all. Its pads stay in the copper, thus the gap between them stays
        # open. This is the same result as the old checkbox for all the
        # parts, and the runner needs no test of its own.
        model["lumped_elements"] = [
            e for e in model["lumped_elements"]
            if para.get(e["ref"], {}).get("model", True)
            and e.get("type") and e.get("value") is not None]
        for p, t, f in zip(model["ports"], port_types, port_feed):
            p["type"] = t
            if f and not p["direction"]:
                # The manual feed of the dialog: the pad has no track,
                # and the user gave the direction and the width of a
                # line that the board draws as a shape or as a polygon.
                # The gap of that direction comes from extract(), thus a
                # drawn CPW keeps its measured gap.
                p["direction"], p["track_width"] = f
                key = {(1, 0): "+x", (-1, 0): "-x", (0, 1): "+y",
                       (0, -1): "-y"}[tuple(p["direction"])]
                p["gap"] = (p.get("gaps") or {}).get(key)
                # `extract()` measured the copper run for the direction
                # of a TRACK, and this pad had none. Measure it for the
                # direction that the user gave, or the runner cannot cap
                # the length of the port (problem 13).
                p["copper_run"] = board_reader.copper_run(
                    model["polygons"].get(p["layer"], []),
                    p["x"], p["y"], p["direction"])
                if (t in ("msl", "cpw", "stripline")
                        and not board_reader.copper_along(
                            model["polygons"].get(p["layer"], []),
                            p["x"], p["y"], p["direction"])):
                    model["warnings"].append(
                        "Port %d: no copper along the manual feed "
                        "direction. The %s port adds its own strip there, "
                        "so the simulated board differs from the real "
                        "one. Check the direction, or draw the feed line."
                        % (p["number"], t))
        if model["warnings"]:
            wx.MessageBox("\n\n".join(model["warnings"]),
                          "RFsim", wx.ICON_WARNING)
        model["settings"] = settings
        model["run_output_dir"] = outdir

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
