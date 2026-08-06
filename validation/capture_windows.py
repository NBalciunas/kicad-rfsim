"""Capture the windows of the GUI to PNG files, with no display needed.

The capture of 2026-08-04 (3) found problem 15, which the eye did not:
the sequence of the pictures showed the package choice fall back to "No
parasitics" after an edit, and a person who clicks reads that as a thing
that they did. That harness stayed in the scratchpad of a session, thus
the next change to the dialog had nothing to compare against. This file
is that harness, in `validation/`.

It is a TOOL and not a test: it writes pictures and asserts nothing.
`test_dialog.py` and `test_views.py` hold the assertions. Use this when
you change the LAYOUT, which no assertion covers: capture before the
change and after it, and look at the two sets.

    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" capture_windows.py [outdir]

**Use `PrintWindow` and not a `ScreenDC` blit.** PrintWindow takes the
content of the window itself, thus another window cannot cover it and
the position on the screen has no effect. Two cautions: `Show()` the
window and do some rounds of `wx.Yield()` and `Update()` first (one
`Yield` is not enough), and a window that a handler HID gives a blank
picture.
"""
import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins"))

import pcbnew  # noqa: E402
import wx  # noqa: E402

import board_reader  # noqa: E402
import gui  # noqa: E402

BOARD = os.path.join(HERE, "out_lumped_coarse", "series_r.kicad_pcb")
RESULTS = os.path.join(HERE, "out_coarse", "results.s2p")
PW_RENDERFULLCONTENT = 2


def shot(win, path):
    """Write the content of `win` to `path` as a PNG."""
    win.Show()
    for _ in range(6):     # one Yield is not enough: the canvas needs more
        wx.Yield()
        win.Update()
    u = ctypes.windll.user32
    # The argtypes are necessary: a handle is larger than an int, and
    # ctypes stops with "int too long to convert" without them.
    u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    size = win.GetSize()
    bmp = wx.Bitmap(size.GetWidth(), size.GetHeight())
    dc = wx.MemoryDC(bmp)
    u.PrintWindow(win.GetHandle(), dc.GetHandle(), PW_RENDERFULLCONTENT)
    dc.SelectObject(wx.NullBitmap)
    bmp.ConvertToImage().SaveFile(path, wx.BITMAP_TYPE_PNG)
    print("   wrote %s (%d x %d)" % (os.path.basename(path),
                                     size.GetWidth(), size.GetHeight()))


def dialog(extra=()):
    board = pcbnew.LoadBoard(BOARD)
    pads = [p for fp in board.GetFootprints()
            if fp.GetReference() in ("P1", "P2") for p in fp.Pads()]
    pre = board_reader.extract(board, pads, 1.0)
    les = list(pre["lumped_elements"]) + list(extra)
    return gui.SettingsDialog(None, pre["ports"], HERE, les, preview=pre,
                              packages=board_reader.package_presets(),
                              esr=board_reader.esr_presets())


def unknown(ref):
    board = pcbnew.LoadBoard(BOARD)
    pads = [p for fp in board.GetFootprints()
            if fp.GetReference() in ("P1", "P2") for p in fp.Pads()]
    e = board_reader.extract(board, pads, 1.0)["lumped_elements"][0]
    return dict(e, ref=ref, type=None, value=None, package=None,
                esl=0.0, esr=0.0)


def capture_dialog(out):
    """The settings dialog, in the states that a layout change moves."""
    print("the settings dialog:")
    cases = [
        ("dialog-1-part", []),
        ("dialog-5-parts", [unknown("D%d" % k) for k in range(4)]),
        ("dialog-20-parts", [unknown("D%d" % k) for k in range(19)]),
    ]
    for name, extra in cases:
        d = dialog(extra)
        shot(d, os.path.join(out, name + ".png"))
        d.Destroy()
    # The state that the capture of 2026-08-04 (3) found problem 15 in:
    # an edit of the ESL must move the row to "Custom".
    d = dialog()
    _, _, ch, esl, _ = d.para_rows[0]
    ch.SetSelection(ch.GetStrings().index("0402 Package"))
    ev = wx.CommandEvent(wx.EVT_CHOICE.typeId, ch.GetId())
    ev.SetEventObject(ch)
    ch.GetEventHandler().ProcessEvent(ev)
    shot(d, os.path.join(out, "dialog-preset-0402.png"))
    esl.SetValue("0.9")
    ev = wx.CommandEvent(wx.EVT_TEXT.typeId, esl.GetId())
    ev.SetEventObject(esl)
    esl.GetEventHandler().ProcessEvent(ev)
    shot(d, os.path.join(out, "dialog-after-esl-edit.png"))
    print("   the last two pictures are the sequence of problem 15: the "
          "second\n   one must show \"Custom\", and not \"No parasitics\".")
    d.Destroy()


def capture_results(out):
    """Every view of the results window."""
    if not os.path.isfile(RESULTS):
        print("the results window: SKIPPED (run `run_headless.py coarse msl`)")
        return
    print("the results window:")
    f = gui.ResultsFrame(None, RESULTS)
    f.Show()
    for i, name in enumerate(f.choice.GetStrings()):
        f.choice.SetSelection(i)
        f._plot()
        wx.Yield()
        safe = "".join(c if c.isalnum() else "-" for c in name).strip("-")
        # The figure holds the picture, thus savefig needs no window at
        # all and it gives the same result on every machine.
        f.figure.savefig(os.path.join(out, "view-%02d-%s.png" % (i, safe)),
                         dpi=90)
    print("   wrote %d views with figure.savefig"
          % len(f.choice.GetStrings()))
    f.Destroy()


def main(out=None):
    out = out or os.path.join(HERE, "out_capture")
    os.makedirs(out, exist_ok=True)
    print("the pictures go into %s\n" % out)
    capture_dialog(out)
    capture_results(out)
    print("\ndone. Compare this directory against the one from before your "
          "change.")


if __name__ == "__main__":
    app = wx.App(False)
    main(*(sys.argv[1:] or []))
