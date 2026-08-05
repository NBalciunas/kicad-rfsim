"""Draw the geometry of a lumped port for the README.

Problem 3: the documents do not show WHERE a port is. A reviewer of
2026-08-03 assumed a lumped port at the EDGE of the substrate on B.Cu.
It is at a PAD that the user selects, and it drives that pad against the
adjacent copper layer.

The numbers come from `runner._port_geometry` (the `else` branch) and
from `openEMS.ports.LumpedPort`, and NOT from memory:

    start = [x - length/2, y - width/2, z_ref]
    stop  = [x + length/2, y + width/2, z_top]

- In x and y the box is the FULL pad, from `pad.GetBoundingBox()`.
- In z it goes through the whole substrate, from the plane of the
  reference layer up to the plane of the layer of the pad.
- The feed resistor and the excitation fill the WHOLE box; the voltage
  probe is one line at the centre; the current probe is a horizontal
  plane at the mid-height.

Run it with the python of KiCad 10 (it needs matplotlib only):
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" make_port_picture.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "docs", "port-geometry.png")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle, FancyArrow  # noqa: E402

# The board of validation/: FR4 of 1.53 mm between F.Cu and B.Cu, and a
# pad of 2.0 x 2.0 mm at the end of a track of 2.9 mm.
H_SUB, PAD, TRACK = 1.53, 2.0, 2.9


CU = "#b87333"      # the copper of the pad
CU_PALE = "#d29a5f"  # the copper of the track
SUB = "#dfe6b0"     # the substrate


def side_view(ax):
    """The z of the port: the WHOLE substrate, under the pad."""
    ax.add_patch(Rectangle((-4.4, 0), 13.4, H_SUB, facecolor=SUB,
                           edgecolor="#9aa668", zorder=0))
    ax.text(8.8, H_SUB / 2, "substrate", fontsize=8, ha="right",
            va="center", color="#5f6636")
    ax.add_patch(Rectangle((-4.4, -0.11), 13.4, 0.11, facecolor=CU,
                           edgecolor="none"))
    ax.add_patch(Rectangle((0, H_SUB), PAD, 0.11, facecolor=CU,
                           edgecolor="none"))
    ax.add_patch(Rectangle((PAD, H_SUB), 6.9, 0.11, facecolor=CU_PALE,
                           edgecolor="none"))

    # The port box fills the substrate under the pad.
    ax.add_patch(Rectangle((0, 0), PAD, H_SUB, facecolor="lime", alpha=0.32,
                           edgecolor="green", lw=1.8, zorder=3))
    # The excitation and the 50 ohm feed resistor fill the whole box.
    for k in range(3):
        ax.add_patch(FancyArrow(0.5 + k * 0.5, H_SUB - 0.18, 0,
                                -(H_SUB - 0.42), width=0.012,
                                head_width=0.11, head_length=0.15,
                                color="darkgreen", zorder=4))
    # The two probes.
    ax.plot([PAD / 2, PAD / 2], [0, H_SUB], color="blue", lw=2.2, zorder=5)
    ax.plot([0, PAD], [H_SUB / 2, H_SUB / 2], color="red", lw=1.7, ls="--",
            zorder=5)

    # The labels, each one in a clear band and with a leader line.
    lbl = dict(fontsize=8, arrowprops=dict(arrowstyle="-", lw=0.7,
                                           color="0.45"))
    ax.annotate("the PAD that you select", xy=(PAD / 2, H_SUB + 0.11),
                xytext=(-4.3, H_SUB + 1.05), color="#7a4a1f",
                fontweight="bold", **lbl)
    ax.annotate("track on F.Cu", xy=(PAD + 3.0, H_SUB + 0.11),
                xytext=(PAD + 2.2, H_SUB + 1.05), color="#7a4a1f", **lbl)
    ax.annotate("B.Cu: the reference layer,\nthe ADJACENT copper layer",
                xy=(-1.6, -0.11), xytext=(-4.3, -1.35), color="#7a4a1f",
                **lbl)
    ax.annotate("the port box: the full pad in x and y,\n"
                "and the whole substrate in z",
                xy=(PAD, H_SUB * 0.62), xytext=(PAD + 1.4, H_SUB * 0.92),
                color="green", **lbl)
    ax.annotate("the excitation (E in −z) and the\n50 ohm feed resistor fill "
                "the WHOLE box", xy=(1.0, H_SUB * 0.45),
                xytext=(PAD + 1.4, -0.62), color="darkgreen", **lbl)
    ax.annotate("V probe: ONE line at the centre",
                xy=(PAD / 2, H_SUB * 0.22), xytext=(PAD + 1.4, -1.35),
                color="blue", **lbl)
    ax.annotate("I probe: a plane at the mid-height",
                xy=(0.35, H_SUB / 2), xytext=(-4.3, 0.62), color="red",
                **lbl)
    ax.set_xlim(-4.6, 9.2)
    ax.set_ylim(-1.9, H_SUB + 1.5)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("A lumped port, from the side", fontsize=10)


def top_view(ax):
    """The x and y of the port: the BOUNDING BOX of the pad."""
    ax.add_patch(Rectangle((PAD / 2, -TRACK / 2), 5.2, TRACK,
                           facecolor=CU_PALE, edgecolor="none"))
    ax.add_patch(plt.Circle((PAD / 2, 0), PAD / 2, facecolor=CU,
                            edgecolor="none"))
    ax.add_patch(Rectangle((0, -PAD / 2), PAD, PAD, facecolor="lime",
                           alpha=0.32, edgecolor="green", lw=1.8))
    lbl = dict(fontsize=8, arrowprops=dict(arrowstyle="-", lw=0.7,
                                           color="0.45"))
    ax.annotate("the port box is the BOUNDING BOX\nof the pad, and not its "
                "shape", xy=(0, PAD / 2), xytext=(-1.1, PAD / 2 + 1.15),
                color="green", **lbl)
    ax.annotate("a round pad becomes a square\nof the same diameter",
                xy=(PAD / 2, -PAD / 2), xytext=(-1.1, -PAD / 2 - 1.5),
                color="0.35", **lbl)
    ax.annotate("track", xy=(4.4, 0), xytext=(4.4, TRACK / 2 + 0.5),
                color="#7a4a1f", ha="center", **lbl)
    ax.set_xlim(-1.3, 7.4)
    ax.set_ylim(-3.1, 3.1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("The same port, from above", fontsize=10)


def main():
    fig, (ax, ax2) = plt.subplots(
        1, 2, figsize=(11.0, 4.2), gridspec_kw={"width_ratios": [1.5, 1.0]})
    side_view(ax)
    top_view(ax2)
    fig.suptitle("Where a port is: at the pad that you select, driven "
                 "against the adjacent copper layer", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUT, dpi=110)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
