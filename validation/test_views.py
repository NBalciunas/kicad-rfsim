"""Draw EVERY view of the results window, with no display.

The window draws 18 views or more, and until 2026-08-05 no test drew any
of them: a change to a plot was found by the eye, or it was not found at
all. A reviewer of 2026-08-03 read the field views as a quantity with no
scale, and the far field as a quantity with a unit, and both readings
came from a title and a colour bar that the code did not write.

This file builds `gui.ResultsFrame` from the output of a run that
exists, draws each entry of its list, and reads the title and the labels
back. It needs no display: `wx.App(False)` and the Agg canvas of
matplotlib are enough, and `figure.savefig` always gives what the canvas
holds.

Run it with the python of KiCad 10:
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" test_views.py

It needs `validation/out_coarse`, which `run_headless.py coarse` makes.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins"))

import wx  # noqa: E402

import gui  # noqa: E402

OUT = os.path.join(HERE, "out_coarse")


def frame():
    s2p = os.path.join(OUT, "results.s2p")
    if not os.path.isfile(s2p):
        raise SystemExit("run `run_headless.py coarse msl` first: this file "
                         "needs %s" % s2p)
    return gui.ResultsFrame(None, s2p)


def texts(fig):
    """Give every piece of text of a figure, as one lowercase string."""
    out = []
    for ax in fig.get_axes():
        out += [ax.get_title(), ax.get_xlabel(), ax.get_ylabel()]
        out += [t.get_text() for t in ax.texts]
    return " | ".join(t for t in out if t).lower()


def test_every_view_draws():
    """Each entry of the list must draw and leave text on the figure."""
    f = frame()
    names = list(f.choice.GetStrings())
    assert len(names) >= 10, "only %d views: the run wrote too little" % len(names)
    for i, name in enumerate(names):
        f.choice.SetSelection(i)
        f._plot()
        t = texts(f.figure)
        assert t, "the view %r drew no text at all" % name
    print("every view draws OK (%d views)" % len(names))
    f.Destroy()


def test_the_field_views_say_what_they_show():
    """The unit, the plane, the frequency, the phase and the maximum.

    The title said "E-Field (f=2.4 GHz)" and nothing else: not the
    plane, and the view had no colour bar. The numbers now sit
    in a block of text at the left, and the colour bar carries the unit
    alone.
    """
    f = frame()
    names = [n for n in f.choice.GetStrings()
             if n.startswith(("E-Field", "H-Field"))]
    assert names, "the run wrote no field dump"
    for name in names:
        f.choice.SetSelection(f.choice.GetStrings().index(name))
        f._plot()
        t = texts(f.figure)
        # The colour bar is an axes of its own, thus its label is in the
        # text of the figure.
        want = "v/m" if name.startswith("E") else "a/m"
        assert want in t, "%r has no colour bar with a unit: %s" % (name, t)
        assert "mid-plane" in t and "mm" in t, \
            "%r does not name the plane: %s" % (name, t)
        assert "ghz" in t, "%r does not name the frequency: %s" % (name, t)
        assert "phase:" in t, "%r does not give the phase: %s" % (name, t)
        assert "maximum:" in t, \
            "%r does not give the largest value: %s" % (name, t)
    print("the field views name the unit, the plane and the scale OK")
    f.Destroy()


def test_the_far_field_views_say_directivity():
    """The unit is dBi, and each view gives its numbers.

    A reviewer wrote that "the far-field is not a unitless quantity".
    The views show DIRECTIVITY, which is a ratio, thus dBi is correct.
    A cut names the quantity in its title and its axis. The 3D balloon
    gives the frequency, the two efficiencies and the directivity in a
    block of text. Both views keep the style of CST.
    """
    f = frame()
    names = [n for n in f.choice.GetStrings() if n.startswith("Farfield")]
    assert names, "the run wrote no far field"
    for name in names:
        f.choice.SetSelection(f.choice.GetStrings().index(name))
        f._plot()
        t = texts(f.figure)
        assert "dbi" in t, "%r does not name the unit: %s" % (name, t)
        if "(phi=" in name.lower() or "(theta=" in name.lower():
            assert "directivity" in t, \
                "%r does not name the quantity: %s" % (name, t)
        else:
            for want in ("frequency:", "rad. effic.", "tot. effic.", "dir."):
                assert want in t, \
                    "%r does not give %r: %s" % (name, want, t)
    print("the far-field views name dBi and give their numbers OK")
    f.Destroy()


def test_the_ports_are_on_the_field_views():
    """The field views are the pictures that leave the tool.

    A reviewer once assumed a lumped port at the edge of the substrate
    on B.Cu, because no picture showed where a port is.
    """
    f = frame()
    names = [n for n in f.choice.GetStrings()
             if n.startswith(("E-Field", "H-Field"))]
    for name in names:
        f.choice.SetSelection(f.choice.GetStrings().index(name))
        f._plot()
        # The figure holds the picture, the block of text at the left
        # and the colour bar. The picture is the one with the axis of x.
        ax = next(a for a in f.figure.get_axes()
                  if a.get_xlabel() == "x (mm)")
        marks = [t for t in ax.texts if t.get_text().startswith("P")]
        assert len(marks) == len(f.model["ports"]), \
            "%r marks %d ports, and the model holds %d" \
            % (name, len(marks), len(f.model["ports"]))
    print("the ports are drawn on the field views OK (%d ports)"
          % len(f.model["ports"]))
    f.Destroy()


def test_the_field_views_have_the_scale_of_cst():
    """The field of a matched line agrees with 0.5 W of incident power.

    CST drives a port with a wave of 1 sqrt(W) peak, which is 0.5 W, and
    the field views use the same reference. Into 50 ohm that is a voltage
    of sqrt(2 * 50 * 0.5) = 7.07 V peak, and |Ez| * h under the strip
    gives that voltage. The run of 2026-09-15 gave 7.04 V. An error of 2
    or of sqrt(2) in the scale moves the voltage by 41% or more.
    """
    import json
    import numpy as np
    with open(os.path.join(OUT, "model.json")) as fh:
        model = json.load(fh)
    z_of = {c["name"]: c["z"] for c in model["copper_layers"]}
    p1, p2 = model["ports"][:2]
    h = abs(z_of[p1["layer"]] - z_of[p1["ref_layer"]]) * 1e-3
    x, y, E, _ = gui._load_field(os.path.join(OUT, "exc1", "Ef.h5"))
    # The centre line of the strip, from 20% to 80% of the distance
    # between the ports. The mean removes most of the ripple that the
    # small mismatch makes.
    ts = np.linspace(0.2, 0.8, 121)
    ix = [int(np.argmin(np.abs(x - (p1["x"] + t * (p2["x"] - p1["x"])))))
          for t in ts]
    iy = [int(np.argmin(np.abs(y - (p1["y"] + t * (p2["y"] - p1["y"])))))
          for t in ts]
    v = float(np.mean([abs(E[j, i, 2]) for i, j in zip(ix, iy)])) * h
    z0 = model["settings"]["z0"]
    want = float(np.sqrt(2.0 * z0 * 0.5))
    assert abs(v / want - 1.0) < 0.1, \
        "the line carries %.2f V, and 0.5 W into %g ohm gives %.2f V" \
        % (v, z0, want)
    print("the field views have the scale of CST OK (%.2f V, and %.2f V "
          "from 0.5 W)" % (v, want))


if __name__ == "__main__":
    app = wx.App(False)
    test_every_view_draws()
    test_the_field_views_say_what_they_show()
    test_the_far_field_views_say_directivity()
    test_the_ports_are_on_the_field_views()
    test_the_field_views_have_the_scale_of_cst()
    print("PASS")
