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


if __name__ == "__main__":
    app = wx.App(False)
    test_preset_holds_the_package()
    test_edit_gives_custom_and_not_no_parasitics()
    test_no_parasitics_shows_zero_and_greys_the_fields()
    test_model_off_removes_the_part()
    test_unknown_part_starts_off_and_empty()
    test_unknown_part_takes_a_type_and_a_value()
    test_unknown_part_without_a_value_cannot_run()
    print("PASS")
