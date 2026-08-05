"""Tests of the settings dialog, with no display.

`wx.App(False)` builds the dialog and the tests read the controls back.
The board comes from a FILE, thus the tests take the path of a user:
LoadBoard -> extract() -> SettingsDialog -> get_settings().

These tests hold the rules of the rows of the R/L/C parts. The dialog is
the one place where a user can give the parasitics of a part by hand, and
a defect there goes into `model.json` and gives an incorrect simulation
with no message.

Run it with the python of KiCad, because it needs pcbnew and wx:

    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" test_dialog.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins"))

import pcbnew  # noqa: E402
import wx  # noqa: E402

import board_reader  # noqa: E402
import gui  # noqa: E402

BOARD = os.path.join(HERE, "out_lumped_coarse", "series_r.kicad_pcb")


def fire(ctrl, evt_type):
    """Send the event that a user makes, because SetValue sends none."""
    ev = wx.CommandEvent(evt_type.typeId, ctrl.GetId())
    ev.SetEventObject(ctrl)
    ctrl.GetEventHandler().ProcessEvent(ev)


def dialog(extra=()):
    """Give a dialog for the board of run_lumped.py, which holds one R.

    `extra` holds more elements, for the tests of a part whose type the
    board does not give.
    """
    board = pcbnew.LoadBoard(BOARD)
    pads = [p for fp in board.GetFootprints()
            if fp.GetReference() in ("P1", "P2") for p in fp.Pads()]
    assert len(pads) == 2, "the board must give the 2 pads of the ports"
    pre = board_reader.extract(board, pads, 1.0)
    assert [e["ref"] for e in pre["lumped_elements"]] == ["R1"], \
        "extract() must find R1 on a board that comes from a file"
    les = list(pre["lumped_elements"]) + list(extra)
    d = gui.SettingsDialog(None, pre["ports"], HERE, les, preview=pre,
                           packages=board_reader.package_presets(),
                           esr=board_reader.esr_presets())
    return d


def unknown(ref="D1"):
    """Give an element of a part whose refdes does not give the type."""
    board = pcbnew.LoadBoard(BOARD)
    pads = [p for fp in board.GetFootprints()
            if fp.GetReference() in ("P1", "P2") for p in fp.Pads()]
    e = board_reader.extract(board, pads, 1.0)["lumped_elements"][0]
    return dict(e, ref=ref, type=None, value=None, package=None,
                esl=0.0, esr=0.0)


def test_preset_holds_the_package():
    """A preset writes the ESL, and the row STAYS on the package.

    A preset uses ChangeValue, which sends no EVT_TEXT. SetValue would
    send it, and the row would go to "Custom" immediately.
    """
    d = dialog()
    _, _, ch, esl, _ = d.para_rows[0]
    ch.SetSelection(ch.GetStrings().index("0402 Package"))
    fire(ch, wx.EVT_CHOICE)
    assert d._pkg_of(ch) == "0402", \
        "the row left the package: %r" % d._pkg_of(ch)
    assert abs(float(esl.GetValue()) - 0.25) < 1e-9, \
        "the preset did not write the ESL: %r" % esl.GetValue()
    para = d.get_settings()["lumped_parasitics"]["R1"]
    assert abs(para["esl"] - 0.25e-9) < 1e-21, para
    assert para["package"] == "0402", para
    d.Destroy()
    print("preset holds the package OK")


def test_edit_gives_custom_and_not_no_parasitics():
    """An edit of a field moves the row to Custom, and keeps the value.

    _on_para_edit selected `GetCount() - 1`, and the LAST entry of that
    choice is "No parasitics"; "Custom" is the entry before it. Thus a
    value that the user typed became an IDEAL element: _para_value gives
    0 for "No parasitics", and model.json then held 0 and 0 with no
    message.
    """
    for i, ctrl_name in enumerate(("esl", "esr")):
        d = dialog()
        _, _, ch, esl, esr = d.para_rows[0]
        ctrl = esl if ctrl_name == "esl" else esr
        ctrl.SetValue("0.9")
        fire(ctrl, wx.EVT_TEXT)
        assert d._pkg_of(ch) == gui.CUSTOM_PKG, \
            "an edit of the %s moved the row to %r, not to Custom" \
            % (ctrl_name.upper(), d._pkg_of(ch))
        s = d.get_settings()
        para = s["lumped_parasitics"]["R1"]
        want = 0.9e-9 if ctrl_name == "esl" else 0.9
        assert abs(para[ctrl_name] - want) < 1e-21, \
            "the %s of the user did not reach the model: %r" \
            % (ctrl_name.upper(), para)
        assert s["parasitics"], "an edited row must keep the parasitics on"
        assert ctrl.IsEnabled(), "the field of an edited row must stay on"
        d.Destroy()
    print("an edit gives Custom OK (ESL and ESR)")


def test_no_parasitics_shows_zero_and_greys_the_fields():
    """"No parasitics" writes 0 into the two fields and greys them.

    Thus the row shows exactly what the solver gets: an ideal element.
    The values come back when the row takes a package again, the ESL
    from the preset and the ESR from the type of the part.
    """
    d = dialog()
    _, _, ch, esl, esr = d.para_rows[0]
    ch.SetSelection(ch.GetStrings().index(gui.NO_PARASITICS))
    fire(ch, wx.EVT_CHOICE)
    s = d.get_settings()
    para = s["lumped_parasitics"]["R1"]
    assert para["esl"] == 0.0 and para["esr"] == 0.0, para
    assert not s["parasitics"], "the only row is ideal, thus parasitics is off"
    assert esl.GetValue() == "0" and esr.GetValue() == "0", \
        "the fields must show 0: %r %r" % (esl.GetValue(), esr.GetValue())
    assert not esl.IsEnabled() and not esr.IsEnabled(), \
        "the fields of an ideal element must go grey"
    assert d._pkg_of(ch) == gui.NO_PARASITICS, \
        "the write of the 0 must not move the row to Custom"
    # Back to a package: the values come back, and the row is not ideal.
    ch.SetSelection(ch.GetStrings().index("0402 Package"))
    fire(ch, wx.EVT_CHOICE)
    assert abs(float(esl.GetValue()) - 0.25) < 1e-9, esl.GetValue()
    assert esl.IsEnabled() and esr.IsEnabled(), \
        "the fields must come back with the package"
    d.Destroy()
    print("no parasitics OK (0 in the fields, grey, and it comes back)")


def test_model_off_removes_the_part():
    """"Model" off greys its row, and rfsim.py then removes that part."""
    d = dialog()
    _, cb, ch, esl, esr = d.para_rows[0]
    cb.SetValue(False)
    fire(cb, wx.EVT_CHECKBOX)
    s = d.get_settings()
    assert s["lumped_parasitics"]["R1"]["model"] is False, s
    assert not s["lumped"], "no part has Model, thus lumped must be off"
    assert not ch.IsEnabled() and not esl.IsEnabled() and not esr.IsEnabled(), \
        "the row of a part that has no Model must go grey"
    d.Destroy()
    print("model off OK")


def test_unknown_part_starts_off_and_empty():
    """A part with no type shows "Unknown", and its Model is OFF.

    A diode, a ferrite bead or a footprint of your own has 2 terminals
    and no type. `extract()` gives it now, thus the user can model it as
    an R, an L or a C. It must change no simulation until the user does
    that: the Model checkbox is off and the field for the value is off.
    """
    d = dialog([unknown("D1")])
    r = d.part_rows[1]
    assert d._kind_of(1) is None, "D1 does not start as Unknown"
    # The code knows nothing about the body of a part with no type, thus
    # it must not invent an ESL for it.
    assert d._pkg_of(d.para_rows[1][2]) == gui.NO_PARASITICS, \
        d._pkg_of(d.para_rows[1][2])
    assert d.para_rows[1][3].GetValue() == "0" \
        and d.para_rows[1][4].GetValue() == "0", "the two fields must show 0"
    assert r["kind"].GetStringSelection() == gui.UNKNOWN_KIND, \
        r["kind"].GetStringSelection()
    assert r["kind"].IsEnabled(), "the type of an unknown part must be open"
    assert not d.para_rows[1][1].GetValue(), "Model must start OFF for D1"
    assert not r["value"].IsEnabled(), \
        "the value has no meaning before the type"
    assert r["qty"].GetLabel() == "Value:", r["qty"].GetLabel()
    # The row of the part that the board describes: it SHOWS what the
    # parser read, its Model is on, and both controls are open.
    r0 = d.part_rows[0]
    assert d._kind_of(0) == "R", d._kind_of(0)
    assert r0["kind"].IsEnabled() and r0["value"].IsEnabled(), \
        "the type and the value of a part must stay open"
    assert r0["value"].GetValue() == "50", r0["value"].GetValue()
    assert r0["qty"].GetLabel() == "Resistance:", r0["qty"].GetLabel()
    assert r0["unit"].GetLabel() == "ohm", r0["unit"].GetLabel()
    assert d.para_rows[0][1].GetValue(), "Model must stay ON for R1"
    d.Destroy()
    print("unknown part starts off OK")


def test_unknown_part_takes_a_type_and_a_value():
    """A type gives the quantity, the unit, the field and the ESR."""
    d = dialog([unknown("D1")])
    r = d.part_rows[1]
    cb = d.para_rows[1][1]
    cb.SetValue(True)
    fire(cb, wx.EVT_CHECKBOX)
    r["kind"].SetSelection(gui.KIND_ORDER.index("L"))
    fire(r["kind"], wx.EVT_CHOICE)
    assert d._kind_of(1) == "L", d._kind_of(1)
    assert r["qty"].GetLabel() == "Inductance:", r["qty"].GetLabel()
    assert r["unit"].GetLabel() == "nH", r["unit"].GetLabel()
    assert r["value"].IsEnabled(), "the field must go on with the type"
    # The row starts at "No parasitics", thus the ESR field keeps its 0
    # and stays grey. The type only says which value comes back when the
    # row takes a package.
    esr = d.para_rows[1][4]
    assert esr.GetValue() == "0" and not esr.IsEnabled(), esr.GetValue()
    r["value"].SetValue("12")
    s = d.get_settings()
    got = s["lumped_parasitics"]["D1"]
    assert got["type"] == "L", got
    assert abs(got["value"] - 12e-9) < 1e-21, got   # nH -> SI
    assert got["model"] is True, got
    assert got["esl"] == 0.0 and got["esr"] == 0.0, got
    # A package gives the body its values: the ESL from the preset and
    # the ESR from the TYPE that the user selected.
    ch = d.para_rows[1][2]
    ch.SetSelection(ch.GetStrings().index("0603 Package"))
    fire(ch, wx.EVT_CHOICE)
    assert abs(float(esr.GetValue())
               - board_reader.esr_presets()["L"]) < 1e-12, esr.GetValue()
    assert abs(float(d.para_rows[1][3].GetValue()) - 0.35) < 1e-9, \
        d.para_rows[1][3].GetValue()
    # A part that the board describes gives its own type and value:
    # what the dialog shows is what model.json holds.
    fixed = s["lumped_parasitics"]["R1"]
    assert fixed["type"] == "R" and abs(fixed["value"] - 50.0) < 1e-9, fixed
    d.Destroy()
    print("unknown part takes a type and a value OK")


def test_unknown_part_without_a_value_cannot_run():
    """_on_ok refuses a part that the user models and does not describe."""
    d = dialog([unknown("D1")])
    cb = d.para_rows[1][1]
    cb.SetValue(True)
    fire(cb, wx.EVT_CHECKBOX)
    # _on_ok speaks through wx.MessageBox, which needs a person. Take
    # the text instead, thus the test runs with no display.
    stopped = []
    old_box = wx.MessageBox

    def fake_box(msg, *a, **k):
        stopped.append(msg)
        return wx.OK
    wx.MessageBox = fake_box
    try:
        d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert stopped and "no type" in stopped[-1], stopped
        # Now give it a type, but no value.
        r = d.part_rows[1]
        r["kind"].SetSelection(gui.KIND_ORDER.index("C"))
        fire(r["kind"], wx.EVT_CHOICE)
        d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert "needs a value in pF" in stopped[-1], stopped
    finally:
        wx.MessageBox = old_box
    d.Destroy()
    print("a modelled part with no type or no value is refused OK")


def test_the_rows_scroll_and_the_dialog_stops_growing():
    """A board with many parts must not make a dialog taller than a screen.

    Measured on 2026-08-04: 1053 px with ONE part, and 29 px more for
    each part after it. Thus the dialog went past a screen of 1920x1080
    with one part, and past 2560x1440 at 15 parts. The rows are in a
    scrolled window now, thus the height stops at MAX_PART_ROWS rows.
    """
    heights = {}
    for n in (1, 5, 15, 20):
        d = dialog([unknown("D%d" % k) for k in range(n - 1)])
        assert len(d.part_rows) == n, "want %d rows, got %d" % (n,
                                                                len(d.part_rows))
        heights[n] = d.GetSize().GetHeight()
        d.Destroy()
    print("   the dialog: " + ", ".join("%d part(s) %d px" % (n, h)
                                        for n, h in sorted(heights.items())))
    # 15 parts and 20 parts are both above MAX_PART_ROWS, thus the two
    # dialogs must be the same height: the rows after the limit go
    # behind the scroll bar. 5 parts is below the limit, thus that
    # dialog is permitted to be shorter.
    grow = abs(heights[20] - heights[15])
    assert grow <= 2, \
        "the dialog grows by %d px from 15 parts to 20: the rows do not " \
        "scroll" % grow
    assert heights[20] <= heights[1] + (gui.MAX_PART_ROWS + 1) * 40, \
        "the dialog is %d px with 20 parts, against %d px with one" \
        % (heights[20], heights[1])
    print("the rows scroll OK (the height stops at %d px)" % heights[20])


def test_the_whole_dialog_scrolls_and_keeps_the_run_button():
    """A short dialog must still show the Run button.

    The rows of the parts scrolled since 2026-08-05, and the dialog was
    still 1084 px tall with ONE part against about 1040 px of client
    area on a screen of 1920x1080 (problem 10). The whole dialog scrolls
    now, and the Run button is OUTSIDE the scrolled body: a button that
    scrolls out of view is the defect that the scroll must not make.
    """
    d = dialog([unknown("D%d" % k) for k in range(9)])
    # Search the children of THIS dialog. `wx.Window.FindWindowById` is
    # a STATIC method in wxPython: it searches every window of the
    # process and it gives back the button of a dialog that an earlier
    # test made and did not destroy yet.
    run = [c for c in d.GetChildren()
           if isinstance(c, wx.Button) and c.GetId() == wx.ID_OK]
    assert run, "the Run button must be a child of the dialog, not of " \
                "the scrolled body: a button that scrolls away is of no use"
    run = run[0]
    for height in (1000, 700, 400):
        d.SetSize((d.GetSize().GetWidth(), height))
        d.Layout()
        bottom = run.GetPosition().y + run.GetSize().GetHeight()
        client = d.GetClientSize().GetHeight()
        assert bottom <= client + 1, \
            "at %d px the Run button ends at %d px, past the client area " \
            "of %d px" % (height, bottom, client)
    # The body must scroll, or the controls above simply disappear.
    scrolls = [c for c in d.GetChildren()
               if isinstance(c, wx.ScrolledWindow) and c is not d.part_area]
    assert scrolls, "the dialog has no scrolled body"
    assert scrolls[0].GetScrollPixelsPerUnit()[1] > 0, \
        "the body of the dialog does not scroll in y"
    d.Destroy()
    print("the whole dialog scrolls OK (the Run button stays at 400 px)")


def test_the_inductor_warning_follows_the_value():
    """A lumped inductor multiplies the run time, and the dialog says so.

    `runner._time_step_factor` gives min(1, 1/sqrt(L[nH])), thus 100 nH
    divides the timestep by 10 and multiplies the number of steps by 10.
    Nothing said this before: the user saw a run that was 10 times
    longer, with no message.
    """
    d = dialog([unknown("L9")])
    assert d.lumped_warn.GetLabel() == "", \
        "a board with no inductor must give no warning"
    r = d.part_rows[1]
    r["kind"].SetSelection(gui.KIND_ORDER.index("L"))
    fire(r["kind"], wx.EVT_CHOICE)
    d.para_rows[1][1].SetValue(True)
    d._on_lumped(None)
    r["value"].SetValue("0.5")          # 0.5 nH: the timestep does not move
    assert d.lumped_warn.GetLabel() == "", \
        "0.5 nH keeps the full timestep, thus it needs no warning: %r" \
        % d.lumped_warn.GetLabel()
    r["value"].SetValue("100")          # 100 nH: 10 times more timesteps
    text = d.lumped_warn.GetLabel()
    assert "10.0 times longer" in text, text
    d.Destroy()
    print("the inductor warning OK (%s)" % text)


def test_a_part_with_r_l_and_c_together():
    """The "rfsim" field of a footprint gives R, L and C in ONE element.

    A PIN diode that is off is C_T in series with L_s and R_s, and no
    refdes of R, L or C describes it. openEMS puts the three components
    of one element in series (LEtype=1), thus the engine could always do
    this; the limit was the way in which the plugin reads a part. The
    row SHOWS the three values and does not let the user change them:
    the footprint is the source, thus the board and the simulation
    cannot disagree.
    """
    rlc = {"R": 1.5, "L": 0.6e-9, "C": 0.3e-12}
    e = dict(unknown("D1"), rlc=rlc, type="RLC",
             package=board_reader.CUSTOM_RLC_PKG, esl=0.0, esr=0.0)
    d = dialog([e])
    r = d.part_rows[1]
    assert r["rlc"] == rlc, r.get("rlc")
    assert r["kind"].GetStringSelection() == gui.RLC_KIND, \
        r["kind"].GetStringSelection()
    assert not r["kind"].IsEnabled(), "the type of such a row must be fixed"
    shown = r["value"].GetValue()
    for want in ("1.5 ohm", "0.6 nH", "0.3 pF"):
        assert want in shown, "the row does not show %s: %r" % (want, shown)
    assert not r["value"].IsEditable(), "the values come from the footprint"
    # _on_ok must NOT refuse it: it has no type and no single value.
    old_box, stopped = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (stopped.append(msg), wx.OK)[1]
    try:
        d.para_rows[1][1].SetValue(True)      # Model on
        d._on_lumped(None)
        d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert not stopped, "the dialog refused an rfsim part: %s" % stopped
    finally:
        wx.MessageBox = old_box
    d.Destroy()
    print("a part with R, L and C together OK (%s)" % shown)


def test_the_substrate_comes_from_the_board():
    """The dialog fills er, tan d, h and cu_t from the stackup.

    Problem 8: a Rogers board simulated as FR4 until 2026-08-05, because
    the dialog started at its own default values and said nothing. The
    values must come from the FILE only: the fallback of board_reader is
    FR4 as well, and to fill the fields from that would show the default
    values of the code as if the board gave them.
    """
    board = pcbnew.LoadBoard(BOARD)
    pads = [p for fp in board.GetFootprints()
            if fp.GetReference() in ("P1", "P2") for p in fp.Pads()]
    pre = board_reader.extract(board, pads, 1.0)
    src = pre.get("stackup_source")
    assert src in ("file", "default"), src
    d = dialog()
    got = (d.er.GetValue(), d.tand.GetValue(), d.h.GetValue(),
           d.cu_t.GetValue())
    note = d.stack_note.GetLabel()
    if src == "file":
        d0 = pre["dielectric_layers"][0]
        assert float(got[0]) == d0["epsilon"], (got, d0)
        assert float(got[1]) == d0["loss_tangent"], (got, d0)
        assert float(got[3]) == pre["copper_layers"][0]["thickness"], got
        assert "stackup of the board" in note, note
    else:
        # No stackup in the file: the fields keep the defaults of the
        # dialog, and the label SAYS that the board gave nothing.
        assert got == ("4.2", "0.02", "1.6", "0.035"), got
        assert "no stackup" in note, note
    # get_settings must give what the fields show, whatever the source.
    s = d.get_settings()
    assert s["er"] == float(got[0]) and s["tand"] == float(got[1]), s
    assert s["h"] == float(got[2]) and s["cu_t"] == float(got[3]), s
    d.Destroy()
    print("the substrate comes from the board OK (source %r, er %s)"
          % (src, got[0]))


def test_the_run_limits_reach_the_settings():
    """The two limits of the run and the timestep factor are controls now.

    They were constant at 300k and 1e-4 in get_settings, thus a
    structure with a high Q stopped too early and nothing said so.
    """
    d = dialog()
    s = d.get_settings()
    assert s["max_timesteps"] == 300000 and s["end_criteria"] == 1e-4, s
    # An EMPTY timestep factor must give None, and not 0: the runner
    # then selects the value from the largest inductance of the model.
    assert s["time_step_factor"] is None, s["time_step_factor"]
    d.max_steps.SetValue("50000")
    d.end_crit.SetValue("1e-5")
    d.tsf.SetValue("0.25")
    s = d.get_settings()
    assert s["max_timesteps"] == 50000, s["max_timesteps"]
    assert s["end_criteria"] == 1e-5, s["end_criteria"]
    assert s["time_step_factor"] == 0.25, s["time_step_factor"]

    # _on_ok must refuse a value that the solver cannot use.
    old_box, stopped = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (stopped.append(msg), wx.OK)[1]
    try:
        for ctrl, bad in ((d.max_steps, "0"), (d.end_crit, "5"),
                          (d.tsf, "2.0"), (d.tsf, "not a number")):
            good = ctrl.GetValue()
            ctrl.SetValue(bad)
            d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
            assert stopped, "the dialog accepted %r" % bad
            stopped.clear()
            ctrl.SetValue(good)
    finally:
        wx.MessageBox = old_box
    d.Destroy()
    print("the run limits OK (max timesteps, end criteria, timestep factor)")


if __name__ == "__main__":
    app = wx.App(False)
    test_preset_holds_the_package()
    test_edit_gives_custom_and_not_no_parasitics()
    test_no_parasitics_shows_zero_and_greys_the_fields()
    test_model_off_removes_the_part()
    test_unknown_part_starts_off_and_empty()
    test_unknown_part_takes_a_type_and_a_value()
    test_unknown_part_without_a_value_cannot_run()
    test_the_rows_scroll_and_the_dialog_stops_growing()
    test_the_whole_dialog_scrolls_and_keeps_the_run_button()
    test_the_inductor_warning_follows_the_value()
    test_a_part_with_r_l_and_c_together()
    test_the_substrate_comes_from_the_board()
    test_the_run_limits_reach_the_settings()
    print("PASS")
