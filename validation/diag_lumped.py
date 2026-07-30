"""Why isn't my R/L/C part being simulated as a lumped element?

Walks _lumped_elements' gates in order for every R*/L*/C* footprint and
reports the first one that rejects it. Several gates are deliberately quiet
in normal use, so this is the way to see them.

On the LIVE board, including unsaved edits — paste into pcbnew's
Tools > Scripting Console:

    import sys; sys.path.insert(0, r"<this folder>")
    import diag_lumped; diag_lumped.report()

On a saved file, from a shell:

    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" diag_lumped.py board.kicad_pcb
"""
import os
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import board_reader as br  # noqa: E402

_ATTR = {getattr(pcbnew, n): n for n in dir(pcbnew)
         if n.startswith("PAD_ATTRIB_")}


def report(board=None, margin_mm=4.0):
    """Print the gate-by-gate verdict for every R*/L*/C* footprint."""
    board = board or pcbnew.GetBoard()
    out = print
    copper, _ = br._stackup(board)
    z_of = {c["name"]: c["z"] for c in copper}
    out("stackup copper: %s" % list(z_of))

    brd = board.GetBoardEdgesBoundingBox()
    region = pcbnew.BOX2I(brd.GetPosition(), brd.GetSize())
    region.Inflate(pcbnew.FromMM(2.0 * margin_mm))
    out("region (margin %g mm): x %.2f..%.2f  y %.2f..%.2f mm"
        % (margin_mm, pcbnew.ToMM(region.GetLeft()),
           pcbnew.ToMM(region.GetRight()), pcbnew.ToMM(region.GetTop()),
           pcbnew.ToMM(region.GetBottom())))

    ports = {p.GetParentFootprint().GetReference()
             for p in br.selected_pads(board)}
    if ports:
        out("selected pads belong to: %s  (these become PORTS, not elements)"
            % sorted(ports))

    n = 0
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        kind = ref[:1].upper()
        if kind not in ("R", "L", "C"):
            continue
        n += 1
        allpads = list(fp.Pads())
        # only numbered pads are terminals; the rest is plain copper
        pads = sorted((p for p in allpads if p.GetNumber()),
                      key=lambda p: p.GetNumber())
        out("\n=== %s  value=%r  (%d pad(s), %d numbered) ==="
            % (ref, fp.GetValue(), len(allpads), len(pads)))
        for p in allpads:
            bb = p.GetBoundingBox()
            out("    pad %-4s %-18s layer=%-7s %.2f x %.2f mm%s"
                % (p.GetNumber() or '""',
                   _ATTR.get(p.GetAttribute(), p.GetAttribute()),
                   board.GetStandardLayerName(p.GetLayer()),
                   pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight()),
                   "" if p.GetNumber() else "   <- unnumbered, not a terminal"))

        if ref in ports:
            out("    SKIPPED: carries a selected pad, so it is a PORT")
            continue
        if not fp.GetBoundingBox().Intersects(region):
            bb = fp.GetBoundingBox()
            out("    REJECTED: outside the region (at x %.1f..%.1f y %.1f..%.1f)"
                % (pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetRight()),
                   pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetBottom())))
            continue
        if len(pads) != 2:
            out("    REJECTED: needs exactly 2 numbered pads, has %d"
                % len(pads))
            continue
        bad = [p.GetNumber() for p in pads
               if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD]
        if bad:
            out("    REJECTED: pad(s) %s are not SMD (THT barrel not modeled)"
                % bad)
            continue
        l0 = board.GetStandardLayerName(pads[0].GetLayer())
        l1 = board.GetStandardLayerName(pads[1].GetLayer())
        if l0 not in z_of or l1 != l0:
            out("    REJECTED: pad layers %r / %r not one stackup layer"
                % (l0, l1))
            continue
        val = br._parse_value(fp.GetValue(), kind)
        if val is None:
            out("    REJECTED: value %r not understood as %s" % (fp.GetValue(),
                                                                kind))
            continue
        b1, b2 = pads[0].GetBoundingBox(), pads[1].GetBoundingBox()
        c1 = (pcbnew.ToMM(b1.Centre().x), -pcbnew.ToMM(b1.Centre().y))
        c2 = (pcbnew.ToMM(b2.Centre().x), -pcbnew.ToMM(b2.Centre().y))
        dx, dy = abs(c2[0] - c1[0]), abs(c2[1] - c1[1])
        if dx >= dy:
            lo, hi = (b1, b2) if c1[0] <= c2[0] else (b2, b1)
            gap = pcbnew.ToMM(hi.GetLeft()) - pcbnew.ToMM(lo.GetRight())
            axis = "x"
        else:
            lo, hi = (b1, b2) if c1[1] <= c2[1] else (b2, b1)
            gap = -pcbnew.ToMM(hi.GetBottom()) - -pcbnew.ToMM(lo.GetTop())
            axis = "y"
        out("    value parses: %s = %g   axis %s   gap %.4f mm"
            % (kind, val, axis, gap))
        if gap <= 0:
            out("    REJECTED: pads overlap, no gap to bridge")
            continue
        if min(dx, dy) > 0.25 * max(dx, dy, 1e-9):
            out("    note: placed off-axis, will be approximated to %s" % axis)
        out("    ACCEPTED -> modelled as %s = %g" % (kind, val))

    out("\n%d R*/L*/C* footprint(s) examined" % n)
    els, warns = br._lumped_elements(board, region, copper, ports)
    out("_lumped_elements -> %d element(s): %s"
        % (len(els), [(e["ref"], e["type"], e["value"]) for e in els]))
    for w in warns:
        out("   WARNING: %s" % w)
    return els


if __name__ == "__main__":
    if len(sys.argv) > 1:
        report(pcbnew.LoadBoard(sys.argv[1]))
    else:
        raise SystemExit(__doc__)
