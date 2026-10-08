"""The RFsim plugin of KiCad: it simulates the S-parameters of the selected
pads with openEMS or EMerge (`solverenv.SOLVER_INFO`)."""
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


def _solver_env(solver):
    """Give the interpreter of `solver`, the runner script and its
    modules (`solverenv.SOLVER_INFO`)."""
    info = solverenv.SOLVER_INFO[solver]
    exe = info["python"]() or _kicad_python()
    return exe, info["runner"], info["modules"]


def _solver_state():
    """Give {solver: None when it can run, or the cause when it cannot}.

    The plugin needs one solver and not all, thus the dialog greys out a
    solver with a cause. A subprocess for each solver uses find_spec, which
    does not import the extensions: a missing openEMS DLL does not look like
    a missing package. The subprocesses run at the same time, thus the
    dialog waits for the slowest one and not for all of them.
    """
    probes = {}
    for key in solverenv.SOLVERS:
        exe, _, mods = _solver_env(key)
        code = ("import importlib.util as u\n"
                "print(','.join(m for m in %r\n"
                "               if u.find_spec(m) is None))" % (tuple(mods),))
        try:
            probes[key] = (exe, subprocess.Popen(
                [exe, "-c", code], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW))
        except Exception as e:
            probes[key] = (exe, "cannot run %s: %s" % (exe, e))
    state = {}
    for key, (exe, p) in probes.items():
        if isinstance(p, str):
            state[key] = p
            continue
        try:
            out, err = p.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
            state[key] = "%s did not answer in 60 s" % exe
            continue
        missing = [m for m in out.strip().split(",") if m]
        if p.returncode != 0:
            state[key] = "the probe of %s failed: %s" % (
                exe, (err or "").strip()[-200:])
        elif missing:
            state[key] = "%s has no %s" % (exe, ", ".join(missing))
        else:
            state[key] = None
    return state


class RFSimPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "RFsim"
        self.category = "RF tools"
        self.description = ("Simulate S-parameters of the selected pad(s) "
                            "with openEMS or EMerge.")
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
            wx.MessageBox(traceback.format_exc(), "RFsim", wx.ICON_ERROR)

    def _run(self):
        # The results window runs in the Python of KiCad. Each solver runs
        # in its own interpreter, because openEMS v0.37 and after have no
        # cp311 wheel and EMerge has no cp314 wheel. Thus the code examines
        # the packages of KiCad here, and those of each solver before the
        # dialog, which greys out a solver that cannot run.
        gui_missing = [m for m in ("skrf", "matplotlib", "h5py")
                       if importlib.util.find_spec(m) is None]
        if gui_missing:
            wx.MessageBox("Missing in KiCad's Python (results window): %s"
                          "\n\nSee the plugin README for install "
                          "instructions." % ", ".join(gui_missing),
                          "RFsim", wx.ICON_ERROR)
            return

        board = pcbnew.GetBoard()
        pads = board_reader.selected_pads(board)
        if len(pads) < 1:
            wx.MessageBox(
                "Select at least one pad to run a simulation.",
                "RFsim", wx.ICON_INFORMATION)
            return

        solvers = _solver_state()
        if all(solvers.values()):
            wx.MessageBox(
                "No solver is installed. RFsim needs one of them:\n\n%s\n\n"
                "See the plugin README for install instructions." % "\n".join(
                    "%s: %s" % (solverenv.SOLVER_INFO[k]["name"], why)
                    for k, why in solvers.items()),
                "RFsim", wx.ICON_ERROR)
            return

        # The stackup comes from the saved file or from the board in
        # memory. When the two are different, the user selects one before
        # the dialog opens. The dialog does not keep its settings, thus the
        # values stay the same when a user stops here to save.
        live = False
        stale = board_reader.unsaved_stackup(board)
        if stale:
            ask = wx.MessageDialog(None, stale, "RFsim",
                                   wx.YES_NO | wx.CANCEL | wx.ICON_WARNING)
            ask.SetYesNoCancelLabels("Use the new values",
                                     "Use the saved values", "Cancel")
            answer = ask.ShowModal()
            ask.Destroy()
            if answer == wx.ID_CANCEL:
                return
            live = answer == wx.ID_YES

        # Get the port data for the preview in the dialog. The margin has
        # no effect on this data.
        preview = board_reader.extract(board, pads, 1.0, live_stackup=live)
        default_out = os.path.join(
            os.path.dirname(board.GetFileName()) or os.getcwd(), "rfsim_results")
        dlg = gui.SettingsDialog(None, preview["ports"], default_out,
                                 preview.get("lumped_elements", []),
                                 preview=preview,
                                 packages=board_reader.package_presets(),
                                 esr=board_reader.esr_presets(),
                                 solvers=solvers)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        settings = dlg.get_settings()
        dlg.Destroy()

        # The dialog lets only a solver that can run through.
        solver_py, script, _ = _solver_env(settings["solver"])

        port_types = settings.pop("port_types")
        port_feed = settings.pop("port_feed")
        # the numbers from the dialog: pad i becomes port order[i], and the
        # types and the manual feeds move with the pads
        order = settings.pop("order")
        pads = [p for _, p in sorted(zip(order, pads), key=lambda t: t[0])]
        port_types = [t for _, t in
                      sorted(zip(order, port_types), key=lambda t: t[0])]
        port_feed = [f for _, f in
                     sorted(zip(order, port_feed), key=lambda t: t[0])]
        outdir = settings.pop("outdir")
        substrate = {k: settings.pop(k) for k in ("er", "tand", "h", "cu_t")}
        # **None is the "KiCad's Stackup" preset of the dialog.** The
        # substrate goes to extract() as None. Thus the (stackup ...) block
        # of the board gives each layer its own er, tan d and thickness. It
        # is the block of the saved file, or of the board in memory if the
        # user selected the new values above. model["stackup_source"]
        # becomes "file" or "memory".
        if any(v is None for v in substrate.values()):
            substrate = None
        # The parasitics of each R/L/C part, from the rows of the dialog.
        # They go into the elements below, and not into the settings:
        # model.json must hold the values that the solver uses.
        para = settings.pop("lumped_parasitics", None) or {}

        # `f_stop` and `mesh` set the dimension of the PML band. Thus the
        # domain holds the clear air AND the absorber: refer to
        # `solverenv.pml_depth`.
        model = board_reader.extract(board, pads, settings["margin_mm"],
                                     substrate, live_stackup=live,
                                     f_stop=settings["f_stop"],
                                     mesh=settings["mesh"],
                                     subregion=settings.get("subregion",
                                                            False))
        for e in model["lumped_elements"]:
            v = para.get(e["ref"])
            if v:
                e.update(package=v["package"], esl=v["esl"], esr=v["esr"],
                         epc=v.get("epc"))
                # When the refdes of a part does not give the type, the
                # part comes back from extract() with type None and value
                # None. The user selected them in the dialog, thus they go
                # in here. A part that the board gives keeps its own
                # values, and the dialog gives None for the two.
                if v.get("type"):
                    e["type"] = v["type"]
                if v.get("value") is not None:
                    e["value"] = v["value"]
                # A series RLC part gives R, L and C and no single value.
                # The three are only in the model: the board file does not
                # change, and `board_reader` reads no new data.
                if e["type"] == "RLC":
                    e.update(value=None, r=v.get("r"), l=v.get("l"),
                             c=v.get("c"))
        # When the Model checkbox of a part is off, the part does not go
        # into the model. Its pads stay in the copper, thus the gap between
        # them stays open. This is the same result as the checkbox of
        # before for all the parts. The runner does not have a test of its
        # own for it.
        model["lumped_elements"] = [
            e for e in model["lumped_elements"]
            if para.get(e["ref"], {}).get("model", True)
            and e.get("type")
            and (e.get("value") is not None or e["type"] == "RLC")]
        for p, t, f in zip(model["ports"], port_types, port_feed):
            # A coaxial feed gives its side in the value: "coax:B.Cu".
            t, _, side = t.partition(":")
            p["type"] = t
            if side:
                p["coax_side"] = side
            if f and not p["direction"]:
                # The manual feed of the dialog: the pad has no track. The
                # user gave the direction and the width of a line that the
                # board shows as a shape or as a polygon. The gap of that
                # direction comes from extract(). Thus a CPW that the user
                # drew keeps its measured gap.
                p["direction"], p["track_width"] = f
                key = {(1, 0): "+x", (-1, 0): "-x", (0, 1): "+y",
                       (0, -1): "-y"}[tuple(p["direction"])]
                p["gap"] = (p.get("gaps") or {}).get(key)
                # `extract()` measured the copper run for the direction
                # of a TRACK, and this pad had none. Measure it for the
                # direction that the user gave, or the runner cannot cap
                # the length of the port.
                p["copper_run"] = board_reader.copper_run(
                    model["polygons"].get(p["layer"], []),
                    p["x"], p["y"], p["direction"])
                if (t in ("msl", "cpw", "stripline")
                        and not board_reader.copper_along(
                            model["polygons"].get(p["layer"], []),
                            p["x"], p["y"], p["direction"])):
                    model["warnings"].append(
                        "Port %d: there is no copper along the manual "
                        "feed direction. The %s port adds its own strip "
                        "there, thus the simulated board is different "
                        "from the board in KiCad. Examine the direction, "
                        "or add the feed line to the board."
                        % (p["number"], t))
        if model["warnings"]:
            wx.MessageBox("\n\n".join(model["warnings"]),
                          "RFsim", wx.ICON_WARNING)
        states = settings.pop("states", None)
        model["settings"] = settings

        os.makedirs(outdir, exist_ok=True)
        runner = os.path.join(os.path.dirname(__file__), script)
        if states:
            # **F30: one run for each state, each in a folder of its own.**
            # A state changes the values of some parts, and nothing else
            # (`solverenv.apply_state`). STATES_FILE lists the folders for
            # the results window, State 1 first: the reference.
            cmd, listed = [], []
            for k, st in enumerate(states, 1):
                folder = solverenv.STATE_DIR % k
                sdir = os.path.join(outdir, folder)
                os.makedirs(sdir, exist_ok=True)
                path = os.path.join(sdir, "model.json")
                with open(path, "w") as fh:
                    json.dump(solverenv.apply_state(model, st), fh, indent=1)
                cmd.append(("State %d of %d: %s" % (k, len(states),
                                                    st["name"]),
                            [solver_py, runner, path, sdir]))
                listed.append({"name": st["name"], "dir": folder,
                               "parts": st["parts"]})
            with open(os.path.join(outdir, solverenv.STATES_FILE), "w") as fh:
                json.dump({"states": listed}, fh, indent=1)
            first = os.path.join(outdir, solverenv.STATE_DIR % 1)
        else:
            model_path = os.path.join(outdir, "model.json")
            with open(model_path, "w") as fh:
                json.dump(model, fh, indent=1)
            cmd = [solver_py, runner, model_path, outdir]
            first = outdir
        # run.log keeps all the text that the window shows, because the
        # window closes immediately when a run succeeds.
        run = gui.RunDialog(None, cmd,
                            log_path=os.path.join(outdir, "run.log"))
        ok = run.ShowModal() == wx.ID_OK
        run.Destroy()
        if ok:
            s2p = os.path.join(first, "results.s%dp" % len(pads))
            gui.ResultsFrame(None, s2p).Show()
