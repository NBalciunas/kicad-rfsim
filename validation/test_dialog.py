"""Tests of the settings dialog, with no display.

`wx.App(False)` builds the dialog, and the tests read the controls back.
The board comes from a FILE. Thus the tests use the path of a user:
LoadBoard -> extract() -> SettingsDialog -> get_settings().

These tests hold the rules of the rows of the R/L/C parts. The dialog is
the one location where a user can give the parasitics of a part by hand. A
defect there goes into `model.json`, and it gives an incorrect simulation
with no message.

Run it with the python of KiCad, because it uses pcbnew and wx:

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


# The board of these tests holds NO (stackup ...) block, thus
# `extract()` gives "default" and the dialog starts at FR-4. This is
# the model of a board that HAS one: a Rogers RO4350B of 0.508 mm.
# `dialog(stackup=...)` puts it into the preview, which is the only
# thing that the dialog reads.
ROGERS = {
    "stackup_source": "file",
    "dielectric_layers": [{"name": "dielectric 1", "z_top": 0.508,
                           "z_bottom": 0.0, "epsilon": 3.48,
                           "loss_tangent": 0.0037}],
    "copper_layers": [{"name": "F.Cu", "z": 0.508, "thickness": 0.018},
                      {"name": "B.Cu", "z": 0.0, "thickness": 0.018}],
}


def dialog(extra=(), stackup=None):
    """Give a dialog for the board of run_lumped.py, which has one R.

    `extra` has more elements, for the tests of a part when the board does
    not give its type. `stackup` replaces the stackup keys of the preview,
    for the tests of the "KiCad's Stackup" preset.
    """
    board = pcbnew.LoadBoard(BOARD)
    pads = [p for fp in board.GetFootprints()
            if fp.GetReference() in ("P1", "P2") for p in fp.Pads()]
    assert len(pads) == 2, "the board must give the 2 pads of the ports"
    pre = board_reader.extract(board, pads, 1.0)
    assert [e["ref"] for e in pre["lumped_elements"]] == ["R1"], \
        "extract() must find R1 on a board that comes from a file"
    les = list(pre["lumped_elements"]) + list(extra)
    if stackup:
        pre.update(stackup)
    d = gui.SettingsDialog(None, pre["ports"], HERE, les, preview=pre,
                           packages=board_reader.package_presets(),
                           esr=board_reader.esr_presets())
    return d


def unknown(ref="D1"):
    """Give an element of a part when the refdes does not give the type."""
    board = pcbnew.LoadBoard(BOARD)
    pads = [p for fp in board.GetFootprints()
            if fp.GetReference() in ("P1", "P2") for p in fp.Pads()]
    e = board_reader.extract(board, pads, 1.0)["lumped_elements"][0]
    return dict(e, ref=ref, type=None, value=None, package=None,
                esl=0.0, esr=0.0)


def test_preset_holds_the_package():
    """A preset writes the ESL, and the row STAYS on the package.

    A preset uses ChangeValue, which sends no EVT_TEXT. SetValue sends it,
    and the row then goes to "Custom" immediately.
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
    """"No parasitics" writes 0 into the two fields and makes them grey.

    Thus the row shows what the solver gets: an ideal element. The values
    come back when the row gets a package again. The ESL comes from the
    preset, and the ESR comes from the type of the part.
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

    A diode, a ferrite bead or a footprint of your own has 2 terminals and
    no type. `extract()` gives it at this time. Thus the user can model it
    as an R, an L or a C. It must change no simulation until the user does
    that: the Model checkbox is off, and the field for the value is off.
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
    # The row of the part when the board gives the type. It SHOWS what the
    # parser read, its Model is on, and the two controls are open.
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
    # The row starts at "No parasitics", thus the ESR field keeps its 0 and
    # stays grey. The type only tells which value comes back when the row
    # gets a package.
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
    # When the board gives the type of a part, the part gives its own type
    # and value. What the dialog shows is what model.json holds.
    fixed = s["lumped_parasitics"]["R1"]
    assert fixed["type"] == "R" and abs(fixed["value"] - 50.0) < 1e-9, fixed
    d.Destroy()
    print("unknown part takes a type and a value OK")


def test_unknown_part_without_a_value_cannot_run():
    """_on_ok refuses a part that the user models and does not give values
    for."""
    d = dialog([unknown("D1")])
    cb = d.para_rows[1][1]
    cb.SetValue(True)
    fire(cb, wx.EVT_CHECKBOX)
    # _on_ok speaks through wx.MessageBox, which must have a person. Get
    # the text, and not the message box. Thus the test runs with no
    # display.
    stopped = []
    old_box = wx.MessageBox

    def fake_box(msg, *a, **k):
        stopped.append(msg)
        return wx.OK
    wx.MessageBox = fake_box
    try:
        d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert stopped and "no type" in stopped[-1], stopped
        # Then give it a type, but no value.
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

    Measured on 2026-08-04: 1053 px with ONE part, and 29 px more for each
    subsequent part. Thus the dialog was taller than a screen of 1920x1080
    with one part, and taller than 2560x1440 at 15 parts. At this time, the
    rows are in a scrolled window. Thus the height stops at MAX_PART_ROWS
    rows.
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
    # 15 parts and 20 parts are more than MAX_PART_ROWS. Thus the two
    # dialogs must have the same height, and the rows after the limit go
    # behind the scroll bar. 5 parts is less than the limit, thus that
    # dialog can be shorter.
    grow = abs(heights[20] - heights[15])
    assert grow <= 2, \
        "the dialog grows by %d px from 15 parts to 20: the rows do not " \
        "scroll" % grow
    assert heights[20] <= heights[1] + (gui.MAX_PART_ROWS + 1) * 40, \
        "the dialog is %d px with 20 parts, against %d px with one" \
        % (heights[20], heights[1])
    print("the rows scroll OK (the height stops at %d px)" % heights[20])


def test_the_whole_dialog_scrolls_and_keeps_the_run_button():
    """A short dialog must also show the Run button.

    From 2026-08-05, the rows of the parts scrolled. But the dialog was
    1084 px tall with ONE part. A screen of 1920x1080 has about 1040 px of
    client area. At this time, ALL the dialog scrolls, and the Run button
    is OUT OF the scrolled body. A button that scrolls out of view is the
    defect that the scroll must not make.
    """
    d = dialog([unknown("D%d" % k) for k in range(9)])
    # Examine the children of THIS dialog. `wx.Window.FindWindowById` is a
    # STATIC method in wxPython. It examines all the windows of the
    # process. Thus it can give the button of a dialog that an earlier test
    # made and did not remove.
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
    # The body must scroll, or the controls above go out of view.
    scrolls = [c for c in d.GetChildren()
               if isinstance(c, wx.ScrolledWindow) and c is not d.part_area]
    assert scrolls, "the dialog has no scrolled body"
    assert scrolls[0].GetScrollPixelsPerUnit()[1] > 0, \
        "the body of the dialog does not scroll in y"
    d.Destroy()
    print("the whole dialog scrolls OK (the Run button stays at 400 px)")


def test_the_inductor_warning_follows_the_value():
    """A lumped inductor multiplies the run time, and the dialog tells it.

    `solverenv.time_step_factor` gives min(1, `LE_STAB_MARGIN`/sqrt(L
    [nH])) with the margin of 0.5. The runner divides the step limit by
    that factor for the same simulated time. Thus 100 nH sets the timestep
    to 0.05, and the run is 20 times longer. The dialog must read the SAME
    function as the runner. Before 2026-09-16, the label gave 10.0 times,
    because it did not include the margin.

    The label starts at 1.05 times, which is 0.28 nH. Below that, the
    number rounds to "1.0 times longer", and it gives no data. Above it, a
    body ESL also counts, and a 2512 body of 0.90 nH costs 1.9 times.
    Before, no text told the user this. The user saw a run that was 20
    times longer, with no message.

    **From 2026-09-21, the label names the SOURCE of the largest value and
    its part** (B49): "The body of R1", "The inductor L9" or "The L of X1".
    Before, it gave "An inductor of 0.4 nH" for a package body. Thus it
    named a part that is not on the board. ONE limit of 1.05 times is
    correct for all sources, by a decision of the owner. A body costs the
    same run time as a value that the user typed.
    """
    d = dialog([unknown("L9")])
    # **The body of a part also counts.** The R1 of this board has the
    # 0.40 nH of an unknown package. The runner divides the timestep for
    # it, thus the label must tell it.
    assert "1.3 times longer" in d.lumped_warn.GetLabel(), \
        "a body of 0.40 nH costs 1.3 times: %r" % d.lumped_warn.GetLabel()
    # **The label must name the SOURCE and the part** (B49). It said "An
    # inductor of 0.4 nH" before 2026-09-21, and the board holds NO
    # inductor: 0.40 nH is the body of an unknown package on R1.
    first = d.lumped_warn.GetLabel()
    assert first.startswith("The body of R1 "),         "the label must name the body and its part: %r" % first
    # With no parasitics the board holds no inductance at all.
    ch = d.para_rows[0][2]
    ch.SetSelection(ch.GetStrings().index(gui.NO_PARASITICS))
    fire(ch, wx.EVT_CHOICE)
    assert d.lumped_warn.GetLabel() == "", \
        "a board with no inductance must give no warning: %r" \
        % d.lumped_warn.GetLabel()
    r = d.part_rows[1]
    r["kind"].SetSelection(gui.KIND_ORDER.index("L"))
    fire(r["kind"], wx.EVT_CHOICE)
    d.para_rows[1][1].SetValue(True)
    d._on_lumped(None)
    r["value"].SetValue("0.2")          # 0.2 nH: the full timestep
    assert d.lumped_warn.GetLabel() == "", \
        "0.2 nH keeps the full timestep, thus it needs no warning: %r" \
        % d.lumped_warn.GetLabel()
    r["value"].SetValue("0.9")          # the body of a 2512: 1.9 times
    got = d.lumped_warn.GetLabel()
    assert "1.9 times longer" in got, got
    # The same number from a part of type L reads differently: the
    # words follow the SOURCE.
    assert got.startswith("The inductor "),         "a part of type L must be named as an inductor: %r" % got
    r["value"].SetValue("100")          # 100 nH: 20 times more steps
    text = d.lumped_warn.GetLabel()
    assert "20.0 times longer" in text, text
    d.Destroy()
    print("the inductor warning OK (%s)" % text)


def test_the_label_follows_a_resistance_as_well():
    """B53: a large resistance sets the timestep, and the label tells it.

    `solverenv.series_r_factor` gives `LE_SERIES_R`/sqrt(R[ohm]) for a
    branch that has more than one component. `runner._time_step_factor`
    uses the smaller of that factor and the factor of the inductance. **The
    dialog must use the same two.** If not, the number on the label and the
    number of the run do not agree.

    R1 of this board has the 0.40 nH body of an unknown package. Thus its
    branch has an R and an L, and the R counts. A resistor with NO
    parasitics is one component. It stays on the classic path of openEMS,
    and it costs nothing for all values.
    """
    d = dialog([unknown("D1")])
    r = d.part_rows[0]
    r["value"].SetValue("70")
    d._on_lumped(None)
    got = d.lumped_warn.GetLabel()
    # 5.8/sqrt(70) = 0.693, thus 1.4 times, against the 1.3 times of the
    # 0.40 nH body without the resistance. The resistance sets the minimum.
    # The body moves |Z| of 70 ohm by 2.3% at 6 GHz, thus it stays.
    assert "1.4 times longer" in got, got
    assert got.startswith("The resistor R1 (70 ohm)"), \
        "the label must name the resistor and its value: %r" % got
    # **P21**: at 1000 ohm, the same body moves |Z| by 0.011%. Thus the run
    # removes it, and the resistor uses the classic path. The label tells
    # that, and it gives no cost.
    r["value"].SetValue("1000")
    got = d.lumped_warn.GetLabel()
    assert got == ("R1: the run leaves out the parasitics of the body, "
                   "which move |Z| by 2% or less over this sweep."), got
    # The SWEEP sets the result. At 100 GHz, the body is 251 ohm, 3.1% of
    # |Z|, and it comes back with the cost of the series path.
    # 5.8/sqrt(1000) = 0.183, thus 5.5 times.
    d.f_stop.SetValue("100")
    got = d.lumped_warn.GetLabel()
    assert got.startswith("The resistor R1 (1000 ohm)") \
        and "5.5 times longer" in got, got
    d.f_stop.SetValue("6")
    # With no parasitics the branch holds ONE component, thus no series
    # topology and no limit at all.
    ch = d.para_rows[0][2]
    ch.SetSelection(ch.GetStrings().index(gui.NO_PARASITICS))
    fire(ch, wx.EVT_CHOICE)
    assert d.lumped_warn.GetLabel() == "", \
        "a lone resistor costs no timestep: %r" % d.lumped_warn.GetLabel()
    d.Destroy()
    print("the label follows a resistance OK (%s)" % got)


def test_an_open_part_leaves_the_grid():
    """A part that is an open at all frequencies of the sweep costs no run
    time.

    The screenshot that started P20 had the values 50 ohm, 90000 nH and
    100 pF in series. They are 565 kohm at 1 GHz. Thus the part blocks the
    track at all frequencies of the sweep. `solverenv.is_open` tells this,
    and the runner does not put such a part in the grid: its gap stays
    open. That is the correct answer, and not a short cut.

    The element in the grid read S21 about 5 dB BELOW the bare gap. The
    cause is that the series topology of openEMS also cuts the displacement
    current of the gap. That run used 31 minutes at 1/600 of the Courant
    step.

    Thus the label names the part and what the run does with it. The cost
    of that part is gone, and `_on_ok` starts the run. An inductor with a
    self-resonance keeps only its EPC, because at GHz a choke on a board IS
    that capacitance.
    """
    d = dialog([unknown("D1")])
    r = rlc_row(d)
    old_box, stopped = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (stopped.append(msg), wx.OK)[1]
    try:
        for k, c in r["rlc"].items():
            c.SetValue({"R": "50", "L": "90000", "C": "100"}[k])
        d._on_lumped(None)
        text = d.lumped_warn.GetLabel()
        assert "D1 is an open circuit over this sweep (565 kohm)" in text, \
            text
        assert "leaves its gap open" in text and "no run time" in text, text
        # The cost of the part is gone: what stays is the body of R1.
        assert "600" not in text, \
            "an open part must not cost the timestep: %r" % text
        del stopped[:]
        d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert not stopped, "an open part must not stop the run: %r" % stopped
        # A choke with a self-resonance keeps its EPC: the Series RLC has
        # no SRF field, thus this row becomes an inductor.
        r["kind"].SetSelection(gui.KIND_ORDER.index("L"))
        fire(r["kind"], wx.EVT_CHOICE)
        r["value"].SetValue("90000")
        r["srf"].SetValue("0.01")           # 10 MHz, which gives 2.81 pF
        d._on_lumped(None)
        text = d.lumped_warn.GetLabel()
        assert "keeps its EPC of 2.81 pF alone" in text, text
    finally:
        wx.MessageBox = old_box
    d.Destroy()
    print("an open part leaves the grid OK (%s)" % text.splitlines()[-1])


def test_the_dialog_refuses_a_run_that_cannot_finish():
    """P20: a run with a LIMIT above `MAX_DERIVED_STEPS` does not start.

    At this time, an open part does not get to this rule. A part that gets
    to it costs much, and it continues to let a signal through. For
    example, 10 uH in a sweep from 0.1 GHz is 6.3 kohm at the low end,
    which is 126 times z0. Thus it stays in the grid. Its factor of 0.005
    makes the 300000 steps of the default a limit of 60 million.

    **The count is a LIMIT, and not the length.** A run stops immediately
    when its field becomes stable. The 90 uH run of 2026-09-22 stopped
    after 1.03 million of its 180 million. Thus the label gives "at most".
    The alternative that the message gives is a smaller "Max steps", and
    that keeps the SAFE factor of the rule. Before, the message named the
    "Timestep factor". For a large L, each factor that is sufficient for
    the limit is on the stability boundary of that L.
    """
    d = dialog([unknown("D1")])
    d.f_start.SetValue("0.1")
    r = rlc_row(d)
    old_box, stopped = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (stopped.append(msg), wx.OK)[1]
    ok_evt = lambda: d._on_ok(
        wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
    try:
        for k, c in r["rlc"].items():
            c.SetValue({"R": "50", "L": "10000", "C": "100"}[k])
        d._on_lumped(None)
        text = d.lumped_warn.GetLabel()
        assert "200.0 times longer: at most 60 million timesteps" in text, \
            "the label must give the count as a LIMIT: %r" % text
        assert "open circuit" not in text, \
            "126 times z0 is not an open: %r" % text
        del stopped[:]
        ok_evt()
        assert stopped, "a limit of 60 million steps must not start"
        msg = stopped[0]
        assert "up to 60 million" in msg and "limit is not the length" in msg, \
            msg
        assert '"Max steps" 250000 or less' in msg, \
            "the message must give the Max steps that fits: %r" % msg
        assert "Timestep factor" not in msg, \
            "the message must not send the user to a factor that " \
            "diverges: %r" % msg
        # **The alternative works.** 250000 steps at 0.005 is a limit of
        # 50 million, which is the limit itself.
        d.max_steps.SetValue("250000")
        del stopped[:]
        ok_evt()
        assert not stopped, "the Max steps of the message must start: %r" \
            % stopped
        # A "Timestep factor" of the user continues to be more important,
        # as in the runner. This is true also when the message does not
        # name it.
        d.max_steps.SetValue("300000")
        d.tsf.SetValue("0.5")
        del stopped[:]
        ok_evt()
        assert not stopped, stopped
    finally:
        wx.MessageBox = old_box
    d.Destroy()
    print("a run that cannot finish OK (a limit, and Max steps as the way "
          "out)")


def test_the_run_window_keeps_its_log():
    """RunDialog writes each line that it shows to run.log.

    The window closes immediately when a run succeeds. Thus before this
    file, its log was gone. Nobody could read again the decisions of the
    run, the thread count and how each excitation ended. The file must be
    complete and CLOSED when ShowModal returns. A run that stops with an
    error must also tell it in the file.
    """
    import tempfile
    tmp = tempfile.mkdtemp()
    for code, rc in (("print('[rfsim] decision: X1 on the CLASSIC path')",
                      0),
                     ("import sys; print('[rfsim] ERROR: NaN'); sys.exit(3)",
                      3)):
        path = os.path.join(tmp, "run.log")
        d = gui.RunDialog(None, [sys.executable, "-c", code], log_path=path)
        if rc:
            # A run that stops with an error keeps its window open, with a
            # Close button.
            wx.CallLater(1500, lambda: d.EndModal(wx.ID_CANCEL))
        d.ShowModal()
        d.Destroy()
        text = open(path, encoding="utf-8").read()
        assert ("CLASSIC path" in text) if rc == 0 else (
            "ERROR: NaN" in text and "exit code 3" in text), text
    print("the run window keeps its log OK (run.log, and a failed run says "
          "so)")


def test_the_substrate_starts_at_fr4():
    """The dialog starts at FR-4, and it does NOT read the stackup.

    A version filled the four fields from the `(stackup ...)` block of the
    board file. It came into the code and went out again on 2026-08-05, at
    the decision of the owner. At this time, the "KiCad's Stackup" preset
    does that work. These conditions must stay true:

    - the fields start at the FR-4 preset for a board with NO stackup;
    - that preset must agree with the fallback of `board_reader`. If not,
      the SAME board gets a substrate through the dialog and a different
      one through a run with no GUI.
    """
    d = dialog()
    got = (d.er.GetValue(), d.tand.GetValue(), d.h.GetValue(),
           d.cu_t.GetValue())
    assert got == ("4.5", "0.02", "1.6", "0.035"), got
    assert float(got[0]) == board_reader.DEF_EPSILON, (
        "the dialog default er %s does not agree with the FR4 of "
        "board_reader (%s): one board would then give two substrates"
        % (got[0], board_reader.DEF_EPSILON))
    # This board gives NO stackup, thus the dialog starts at FR-4 and
    # not at "KiCad's Stackup", which is the first entry.
    assert d.preset.GetStringSelection() == "FR-4",         d.preset.GetStringSelection()
    assert gui.SUBSTRATE_PRESETS[0][0] == gui.BOARD_PRESET
    # Each preset must write its two values, and "Custom" must write none.
    # It is the entry where a value that the user types moves the row.
    # "KiCad's Stackup" on a board with no stackup speaks and goes back to
    # FR-4. Thus wx.MessageBox does not use a person here.
    old_box, said = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (said.append(msg), wx.OK)[1]
    try:
        for i, (name, er, tand) in enumerate(gui.SUBSTRATE_PRESETS):
            d.preset.SetSelection(i)
            d._on_preset(None)
            if name == gui.BOARD_PRESET:
                assert said, "a board with no stackup must say so"
                assert d.preset.GetStringSelection() == "FR-4", \
                    "the selection must go back to a preset that HAS values"
                continue
            if er is None:
                continue
            assert float(d.er.GetValue()) == er, (name, d.er.GetValue())
            assert float(d.tand.GetValue()) == tand, (name, d.tand.GetValue())
    finally:
        wx.MessageBox = old_box
    # get_settings must give what the fields show.
    d.preset.SetSelection(1)  # FR-4
    d._on_preset(None)
    s = d.get_settings()
    assert s["er"] == 4.5 and s["tand"] == 0.02, s
    assert s["h"] == 1.6 and s["cu_t"] == 0.035, s
    d.Destroy()
    print("the substrate starts at FR-4 OK (%d presets)"
          % len(gui.SUBSTRATE_PRESETS))


def test_the_rogers_grades_have_two_rows():
    """Each Rogers grade gives a stripline row and a microstrip row.

    Rogers gives two Dk values for one laminate. It measures the process Dk
    with a stripline test. The design Dk is larger, and it is for a
    microstrip. The list has the two values, and a row must write its OWN
    value. Two rows with one name and two numbers are easy to confuse. The
    tan d is the same in the two rows of a grade.
    """
    names = [p[0] for p in gui.SUBSTRATE_PRESETS]
    d = dialog()
    for grade, strip, micro, tand in (("RO4350B", 3.48, 3.66, 0.0037),
                                      ("RO4003C", 3.38, 3.55, 0.0027)):
        for kind, er in (("stripline", strip), ("microstrip", micro)):
            name = "Rogers %s (%s)" % (grade, kind)
            assert name in names, "no row %r in %s" % (name, names)
            d.preset.SetSelection(names.index(name))
            d._on_preset(None)
            s = d.get_settings()
            assert (s["er"], s["tand"]) == (er, tand), \
                "%s gives er %s and tan d %s" % (name, s["er"], s["tand"])
    d.Destroy()
    print("the Rogers grades have two rows OK (stripline and microstrip)")


def test_the_run_limits_reach_the_settings():
    """The two limits of the run and the timestep factor are controls at
    this time.

    They were constant at 300k and 1e-4 in get_settings. Thus a structure
    with a high Q stopped before its field became stable, and no text told
    the user.
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


def test_the_kicad_stackup_preset_gives_the_board():
    """A board that HAS a stackup starts at "KiCad's Stackup".

    The four fields then show what the board gives, and they are read-only.
    `get_settings` gives None for the four. None is the signal for `rfsim`:
    it calls `extract()` with no substrate. Thus the stackup of the file
    gives each layer its own values.
    """
    d = dialog(stackup=ROGERS)
    assert d.preset.GetStringSelection() == gui.BOARD_PRESET, \
        "a board with a stackup must not start at FR-4"
    assert d.uses_board_stackup()
    got = (d.er.GetValue(), d.tand.GetValue(), d.h.GetValue(),
           d.cu_t.GetValue())
    assert got == ("3.48", "0.0037", "0.508", "0.018"), got
    for c in (d.er, d.tand, d.h, d.cu_t):
        assert not c.IsEnabled(), \
            "a value of the BOARD must not look like a value of the user"
    s = d.get_settings()
    assert (s["er"], s["tand"], s["h"], s["cu_t"]) == (None,) * 4, s
    # _on_ok must not refuse the dialog: it must NOT read the fields.
    old_box, stopped = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (stopped.append(msg), wx.OK)[1]
    try:
        d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
        assert not stopped, stopped
    finally:
        wx.MessageBox = old_box

    # A different preset replaces it. The fields come back with the values
    # that they had before the board preset.
    d.preset.SetSelection(1)  # FR-4
    d._on_preset(None)
    assert not d.uses_board_stackup()
    got = (d.er.GetValue(), d.tand.GetValue(), d.h.GetValue(),
           d.cu_t.GetValue())
    assert got == ("4.5", "0.02", "1.6", "0.035"), got
    for c in (d.er, d.tand, d.h, d.cu_t):
        assert c.IsEnabled(), "the fields must be open again"
    s = d.get_settings()
    assert (s["er"], s["h"]) == (4.5, 1.6), s
    # Then go back to the board preset.
    d.preset.SetSelection(0)
    d._on_preset(None)
    assert d.er.GetValue() == "3.48" and not d.er.IsEnabled()
    d.Destroy()

    # Two dielectrics that are different. ONE field cannot hold two
    # numbers. Thus it shows the two, and the run uses the stackup layer by
    # layer.
    two = {"stackup_source": "file",
           "dielectric_layers": [
               dict(ROGERS["dielectric_layers"][0]),
               {"name": "dielectric 2", "z_top": 1.016, "z_bottom": 0.508,
                "epsilon": 4.5, "loss_tangent": 0.02}],
           "copper_layers": ROGERS["copper_layers"]}
    d = dialog(stackup=two)
    assert d.er.GetValue() == "3.48 / 4.5", d.er.GetValue()
    assert d.tand.GetValue() == "0.0037 / 0.02", d.tand.GetValue()
    assert d.h.GetValue() == "1.016", d.h.GetValue()   # the two together
    assert d.cu_t.GetValue() == "0.018", d.cu_t.GetValue()  # one value only
    assert d.get_settings()["er"] is None
    d.Destroy()

    # The stackup of the board in memory fills the preset in the same
    # manner. The user selects it when it has a change that is not saved.
    d = dialog(stackup=dict(ROGERS, stackup_source="memory"))
    assert d.uses_board_stackup(), "a stackup from memory is a stackup"
    assert d.er.GetValue() == "3.48" and not d.er.IsEnabled(), d.er.GetValue()
    d.Destroy()
    print("the KiCad's Stackup preset OK (read-only, and None in the "
          "settings)")


def test_the_x_of_the_dialog_does_not_start_the_run():
    """Close the window, and NO simulation starts.

    The dialog had only the Run button, and its ID is wxID_OK. The default
    close handler of wxWidgets looks for a button with wxID_CANCEL, then
    wxID_OK. It sends a click to the first one that it finds. Thus the X of
    the title bar clicked Run Simulation, and the run started. `Close()`
    here makes the same event as the X.

    This test shows the dialog modally, and the other tests do not. The
    cause is that the defect is in the ANSWER of `ShowModal`, and not in a
    control.
    """
    d = dialog()
    ran = []
    d.Bind(wx.EVT_BUTTON, lambda e: (ran.append(1), e.Skip()), id=wx.ID_OK)
    wx.CallAfter(d.Close)
    assert d.ShowModal() == wx.ID_CANCEL, \
        "the X of the dialog started the simulation"
    assert not ran, "the X clicked the Run button"
    d.Destroy()

    # The Cancel button is what the close handler must find first.
    d = dialog()
    cancel = [c for c in d.GetChildren()
              if isinstance(c, wx.Button) and c.GetId() == wx.ID_CANCEL]
    assert cancel, "the dialog has no Cancel button, thus the X clicks Run"
    wx.CallAfter(fire, cancel[0], wx.EVT_BUTTON)
    assert d.ShowModal() == wx.ID_CANCEL
    d.Destroy()

    # The Run button must also continue to start the run.
    d = dialog()
    run = [c for c in d.GetChildren()
           if isinstance(c, wx.Button) and c.GetId() == wx.ID_OK][0]
    wx.CallAfter(fire, run, wx.EVT_BUTTON)
    assert d.ShowModal() == wx.ID_OK, "the Run button does not start the run"
    d.Destroy()
    print("the X and Cancel give ID_CANCEL OK (Run still gives ID_OK)")


def rlc_row(d, i=1):
    """Give row i as a Series RLC part with its Model on."""
    r = d.part_rows[i]
    cb = d.para_rows[i][1]
    cb.SetValue(True)
    fire(cb, wx.EVT_CHECKBOX)
    r["kind"].SetSelection(gui.KIND_ORDER.index(gui.RLC_KIND))
    fire(r["kind"], wx.EVT_CHOICE)
    return r


def test_a_series_rlc_row_shows_r_l_and_c_and_no_parasitics():
    """"Series RLC" gives the row three fields and no parasitics.

    A part that no single type can model gets this type. A PIN diode that
    is off is C_T in series with L_s and R_s. Its row shows R, L and C, and
    not the one value. It shows no package choice and no ESR or ESL,
    because its R and its L ARE the body. The values are only in the model.
    `get_settings` gives them in SI. Each field starts at 0. A field of 0
    gives None, and that component is then not in the part.
    """
    d = dialog([unknown("D1")])
    r = d.part_rows[1]
    assert not any(c.IsShown() for c in r["rlc"].values()), \
        "a row must start with the one value field"
    rlc_row(d)
    assert all(c.GetValue() == "0" for c in r["rlc"].values()),         "each field must start at 0"
    assert d._kind_of(1) == gui.RLC_KIND, d._kind_of(1)
    assert r["kind"].GetStringSelection() == "Series RLC", \
        r["kind"].GetStringSelection()
    for k, c in r["rlc"].items():
        assert c.IsShown() and c.IsEnabled(), "the %s field must be open" % k
    assert not r["value"].IsShown() and not r["qty"].IsShown(), \
        "a series RLC has no single value"
    assert not any(c.IsShown() for c in r["para_ctrls"]), \
        "a series RLC shows no package, no ESR and no ESL"
    # The row of R1 keeps its value and its parasitics.
    r0 = d.part_rows[0]
    assert r0["value"].IsShown() and all(c.IsShown()
                                         for c in r0["para_ctrls"])
    r["rlc"]["R"].SetValue("1.5")
    r["rlc"]["L"].SetValue("0.6")
    s = d.get_settings()
    got = s["lumped_parasitics"]["D1"]
    assert got["type"] == gui.RLC_KIND and got["value"] is None, got
    assert abs(got["r"] - 1.5) < 1e-12, got
    assert abs(got["l"] - 0.6e-9) < 1e-21, got      # nH -> SI
    assert got["c"] is None, "a C of 0 must leave C out: %r" % got
    assert (got["package"], got["esl"], got["esr"]) == (None, 0.0, 0.0), got
    assert got["model"] is True, got
    # A return to a single type gives the row back as it was, and the
    # three fields keep their text for the next time.
    r["kind"].SetSelection(gui.KIND_ORDER.index("C"))
    fire(r["kind"], wx.EVT_CHOICE)
    assert r["value"].IsShown() and all(c.IsShown() for c in r["para_ctrls"])
    assert not any(c.IsShown() for c in r["rlc"].values())
    assert r["rlc"]["R"].GetValue() == "1.5", r["rlc"]["R"].GetValue()
    assert "r" not in d.get_settings()["lumped_parasitics"]["D1"], \
        "a capacitor row must not carry the fields of a series RLC"
    d.Destroy()
    print("a series RLC row OK (R, L and C in SI, no parasitics, and back)")


def test_a_series_rlc_part_needs_a_positive_component():
    """_on_ok refuses a series RLC with no component, a negative value, or
    a text.

    Each field is 0 or a positive number. 0 (or an empty field) leaves that
    component out, and at least one component must stay. The L of the part
    changes the timestep as an inductor does, thus the warning follows it.
    """
    d = dialog([unknown("D1")])
    r = rlc_row(d)
    old_box, stopped = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (stopped.append(msg), wx.OK)[1]
    try:
        for fields, ok in (({}, False), ({"C": "0"}, False),
                           ({"R": "0", "L": "0", "C": "0"}, False),
                           ({"R": "-1"}, False), ({"L": "abc"}, False),
                           ({"R": "1.5", "C": "0"}, True),
                           ({"C": "0.3"}, True),
                           ({"R": "1.5", "L": "0.6", "C": "0.3"}, True)):
            for k, c in r["rlc"].items():
                c.SetValue(fields.get(k, ""))
            del stopped[:]
            d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
            if ok:
                assert not stopped, "%s was refused: %s" % (fields, stopped)
            else:
                assert stopped and "Series RLC" in stopped[-1], \
                    "%s was accepted: %s" % (fields, stopped)
    finally:
        wx.MessageBox = old_box
    assert "1.5 times longer" in d.lumped_warn.GetLabel(), \
        "0.6 nH costs 1.5 times the run time: %r" % d.lumped_warn.GetLabel()
    r["rlc"]["L"].SetValue("100")
    text = d.lumped_warn.GetLabel()
    assert "20.0 times longer" in text, text
    # **The third source of the label** (B49): the L of a Series RLC.
    # A part of type L reads "The inductor ..." and a package body
    # reads "The body of ...".
    assert text.startswith("The L of "),         "the L of a Series RLC must be named as such: %r" % text
    d.Destroy()
    print("a series RLC part needs a component OK (0 leaves one out, and "
          "its L warns)")


def test_the_srf_field_belongs_to_an_inductor():
    """The SRF field shows only for an inductor, and it gives the EPC.

    An inductor has a capacitance in PARALLEL with its winding. Thus it has
    a self-resonance, and above it the part is no longer an inductor. No
    table gives that capacitance. Only the S-parameters of the part can
    make it different from the land. Thus the field gets the
    SELF-RESONANCE, which the datasheet of each inductor gives. The dialog
    gives the capacitance that resonates with the value at that frequency:

        C = 1 / ((2 pi f)^2 L)

    The field starts at 0. A field of 0 (or an empty field) keeps the part
    as it was: DCR + L, with no self-resonance and no second element.
    """
    d = dialog([unknown("D1")])
    r = d.part_rows[1]
    cb = d.para_rows[1][1]
    cb.SetValue(True)
    fire(cb, wx.EVT_CHECKBOX)
    for kind, shown in (("R", False), ("C", False), ("L", True),
                        (gui.RLC_KIND, False)):
        r["kind"].SetSelection(gui.KIND_ORDER.index(kind))
        fire(r["kind"], wx.EVT_CHOICE)
        assert all(c.IsShown() == shown for c in r["srf_ctrls"]),             "the SRF field must %sshow for %s" % ("" if shown else "not ", kind)
    # 10 nH that resonates at 3 GHz gives 0.2815 pF.
    r["kind"].SetSelection(gui.KIND_ORDER.index("L"))
    fire(r["kind"], wx.EVT_CHOICE)
    assert r["srf"].GetValue() == "0", "the SRF must start at 0"
    r["value"].SetValue("10")
    r["srf"].SetValue("3")
    got = d.get_settings()["lumped_parasitics"]["D1"]
    want = 1.0 / ((2 * 3.141592653589793 * 3e9) ** 2 * 10e-9)
    assert abs(got["epc"] - want) < 1e-18,         "the SRF of 3 GHz on 10 nH gives %r, want %g" % (got["epc"], want)
    # An empty field gives no EPC. A row that is not an inductor also gives
    # no EPC. The part then keeps the model that it had.
    for text in ("0", ""):
        r["srf"].SetValue(text)
        assert d.get_settings()["lumped_parasitics"]["D1"]["epc"] is None
    r["srf"].SetValue("3")
    r["kind"].SetSelection(gui.KIND_ORDER.index("C"))
    fire(r["kind"], wx.EVT_CHOICE)
    assert d.get_settings()["lumped_parasitics"]["D1"]["epc"] is None
    # A text that is not a number, or a negative number, stops the run.
    r["kind"].SetSelection(gui.KIND_ORDER.index("L"))
    fire(r["kind"], wx.EVT_CHOICE)
    old_box, stopped = wx.MessageBox, []
    wx.MessageBox = lambda msg, *a, **k: (stopped.append(msg), wx.OK)[1]
    try:
        for text, ok in (("abc", False), ("0", True), ("-1", False),
                         ("", True), ("3", True)):
            r["srf"].SetValue(text)
            del stopped[:]
            d._on_ok(wx.CommandEvent(wx.EVT_BUTTON.typeId, wx.ID_OK))
            if ok:
                assert not stopped, "%r was refused: %s" % (text, stopped)
            else:
                assert stopped and "SRF" in stopped[-1],                     "%r was accepted: %s" % (text, stopped)
    finally:
        wx.MessageBox = old_box
    print("the SRF field OK (an inductor alone, SI from the datasheet, "
          "and the refusals)")


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
    test_the_label_follows_a_resistance_as_well()
    test_an_open_part_leaves_the_grid()
    test_the_dialog_refuses_a_run_that_cannot_finish()
    test_the_run_window_keeps_its_log()
    test_the_substrate_starts_at_fr4()
    test_the_rogers_grades_have_two_rows()
    test_the_run_limits_reach_the_settings()
    test_the_kicad_stackup_preset_gives_the_board()
    test_the_x_of_the_dialog_does_not_start_the_run()
    test_a_series_rlc_row_shows_r_l_and_c_and_no_parasitics()
    test_a_series_rlc_part_needs_a_positive_component()
    test_the_srf_field_belongs_to_an_inductor()
    print("PASS")
