"""Read the stackup and the copper geometry of a pcbnew BOARD into a dict.

Only this module uses pcbnew for geometry. You can write its result to a
JSON file: floats in mm, right-handed coordinates (the y axis points in
the opposite direction to the y axis of the screen coordinates of KiCad),
and z=0 at the bottom of the board. The module uses the pcbnew SWIG
bindings of KiCad 10. The IPC API is not an alternative yet: IPC has no
function that makes polygons from tracks, arcs or text (refer to
NOTES.md, "The IPC API").
"""
import math
import os
import re

import pcbnew

# the default values if the board has no stackup: FR4
DEF_EPSILON, DEF_LOSS_TAN, DEF_CU_T = 4.5, 0.02, 0.035

# The SI multipliers. The letter case is important (m = milli, M = mega).
# The letters 'r' and 'R' show the position of the decimal point.
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
       "r": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
# The permitted prefix letters for each type of part. They prevent an
# incorrect result, for example a 'p' on a resistor read as pico.
_PREFIX = {"R": "rRkKMG", "C": "pnuµ", "L": "pnuµm"}
_DNP = {"dnp", "dnf", "dni", "dnl", "nc", "n/a", "na", "-", "",
        "nopop", "no pop", "?"}


def _parse_value(text, kind):
    """Change '4k7', '4.7k', '100nF' or '3n3' into a float in SI units.

    The units are ohm, H or F. `kind` is 'R', 'L' or 'C'. The function
    obeys the RKM convention, where a letter shows the position of the
    decimal point (4R7 = 4.7 ohm, 3n3 = 3.3 nH or 3.3 nF). If the text is
    DNP, or if the function cannot read the text, it gives None.
    """
    if not text:
        return None
    tok = text.strip().split()[0] if text.strip() else ""  # remove " 1%" etc.
    tok = tok.replace(",", ".").replace("Ω", "").replace("Ω", "")
    for u in ("ohm", "OHM", "Ohm"):
        tok = tok.replace(u, "")
    if tok.lower() in _DNP:
        return None
    if kind == "C" and tok[-1:].lower() == "f":
        tok = tok[:-1]
    elif kind == "L" and tok[-1:].lower() == "h":
        tok = tok[:-1]
    if not tok:
        return None
    for i, ch in enumerate(tok):
        if ch in _PREFIX[kind]:
            left, right = tok[:i], tok[i + 1:]
            num = (left + "." + right) if right else (left or "0")
            try:
                return float(num) * _SI[ch]
            except ValueError:
                return None
    try:
        return float(tok)
    except ValueError:
        return None


def _lname(layer_id):
    return pcbnew.BOARD.GetStandardLayerName(layer_id)


def _mm(v):
    return round(pcbnew.ToMM(int(v)), 5)


def selected_pads(board):
    """Give the selected pads in a constant sequence.

    The sequence is the footprint reference, then the pad number.
    """
    pads = [p for fp in board.GetFootprints() for p in fp.Pads() if p.IsSelected()]
    pads.sort(key=lambda p: (p.GetParentFootprint().GetReference(), p.GetNumber()))
    return pads


def _stackup_from_file(path):
    """Read the (stackup ...) block of a .kicad_pcb file.

    No SWIG version of KiCad (8, 9 or 10) gives access to BOARD_STACKUP.
    Thus the file is the only source of the dielectric properties that a
    script can use. The function gives a list from the top layer to the
    bottom layer, with the keys kind, name, thickness, epsilon and
    loss_tangent. If the file has no stackup, the function gives None.
    """
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    i = text.find("(stackup")
    if i < 0:
        return None
    depth, in_str, j = 0, False, i
    while j < len(text):  # find the end parenthesis; ignore those in strings
        c = text[j]
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
        j += 1
    tokens = re.findall(r'\(|\)|"[^"]*"|[^\s()"]+', text[i:j + 1])

    def parse(k):
        node = []
        while k < len(tokens):
            t = tokens[k]
            if t == "(":
                child, k = parse(k + 1)
                node.append(child)
            elif t == ")":
                return node, k + 1
            else:
                node.append(t.strip('"'))
                k += 1
        return node, k

    tree = parse(1)[0]  # do not include the first '('
    items = []
    for node in tree:
        if not (isinstance(node, list) and node and node[0] == "layer"):
            continue
        props = {c[0]: c[1:] for c in node[2:] if isinstance(c, list) and c}
        typ = props.get("type", [""])[0].lower()
        # a dielectric can have sublayers: add all the thickness values
        # in the node
        thick = sum(float(c[1]) for c in node[2:]
                    if isinstance(c, list) and c and c[0] == "thickness")
        if typ == "copper":
            items.append({"kind": "copper", "name": node[1],
                          "thickness": thick or DEF_CU_T})
        elif typ in ("core", "prepreg"):
            items.append({
                "kind": "dielectric", "name": node[1],
                "thickness": thick,
                "epsilon": float(props.get("epsilon_r", [DEF_EPSILON])[0]),
                "loss_tangent": float(props.get("loss_tangent",
                                                [DEF_LOSS_TAN])[0]),
            })
    return items or None


def _uniform_stackup(cu_names, diel_total, eps, tand, cu_t):
    """Make n copper sheets with dielectric layers of equal thickness.

    diel_total is the total thickness of the dielectric.
    """
    n = len(cu_names)
    diel_t = max(diel_total, 0.1) / (n - 1)
    items = []
    for k, name in enumerate(cu_names):
        items.append({"kind": "copper", "name": name, "thickness": cu_t})
        if k < n - 1:
            items.append({"kind": "dielectric", "name": "dielectric %d" % (k + 1),
                          "thickness": diel_t, "epsilon": eps,
                          "loss_tangent": tand})
    return items


def _default_stackup(board, cu_names):
    """Make a uniform FR4 stackup from the thickness of the board.

    Use this function if the board file contains no stackup.
    """
    total = pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness()) or 1.6
    return _uniform_stackup(cu_names, total - len(cu_names) * DEF_CU_T,
                            DEF_EPSILON, DEF_LOSS_TAN, DEF_CU_T)


def _stackup(board, substrate=None):
    """Give the physical stackup from the top to the bottom.

    The result is (copper_layers, dielectric_layers). Copper is a sheet
    with no thickness on a boundary of the dielectric, but the real
    thickness stays in the data for the loss model. z=0 is the plane of
    the bottom copper. The properties of the stackup come from the board
    file. Thus you must save the board after you change
    Board Setup > Physical Stackup.
    """
    cu_ids = list(board.GetEnabledLayers().CuStack())
    cu_names = [_lname(lid) for lid in cu_ids]
    id_of = dict(zip(cu_names, cu_ids))

    if substrate:  # the values from the user have priority over the file
        items = _uniform_stackup(cu_names, substrate["h"], substrate["er"],
                                 substrate["tand"], substrate["cu_t"])
    else:
        items = _stackup_from_file(board.GetFileName())
        if items:
            file_cu = [it["name"] for it in items if it["kind"] == "copper"]
            if file_cu != cu_names:  # the file does not agree: use defaults
                items = None
        if not items:
            items = _default_stackup(board, cu_names)

    z = sum(it["thickness"] for it in items if it["kind"] == "dielectric")
    copper, diel = [], []
    for it in items:
        if it["kind"] == "copper":
            copper.append({"name": it["name"], "id": id_of[it["name"]],
                           "z": round(z, 6), "thickness": it["thickness"]})
        else:
            diel.append({
                "name": it["name"],
                "z_top": round(z, 6),
                "z_bottom": round(z - it["thickness"], 6),
                "epsilon": max(1.0, it["epsilon"]),
                "loss_tangent": max(0.0, it["loss_tangent"]),
            })
            z -= it["thickness"]
    return copper, diel


# The code below makes polygons from the tracks, the arcs, the via rings
# and the graphic shapes with simple mathematics. This is a result of
# KiCad 8: there, all the TransformShapeToPolygon functions but the
# function of PAD needed the ERROR_LOC enum, which SWIG did not wrap.
# KiCad 10 has pcbnew.ERROR_INSIDE. Thus
# BOARD.ConvertBrdLayerToPolygonalContours can replace all of this code
# and can also include the text. Refer to the backlog in NOTES.md.

def _add_outline(ps, pts):
    ps.NewOutline()
    for x, y in pts:
        ps.Append(int(x), int(y))


def _circle_pts(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _stadium_pts(ax, ay, bx, by, w, n=8):
    """Make the polygon of a track: a segment that has round ends."""
    r = w / 2.0
    if math.hypot(bx - ax, by - ay) < 1:
        return _circle_pts(ax, ay, r)
    th = math.atan2(by - ay, bx - ax)
    pts = [(bx + r * math.cos(th - math.pi / 2 + math.pi * i / n),
            by + r * math.sin(th - math.pi / 2 + math.pi * i / n))
           for i in range(n + 1)]
    pts += [(ax + r * math.cos(th + math.pi / 2 + math.pi * i / n),
             ay + r * math.sin(th + math.pi / 2 + math.pi * i / n))
            for i in range(n + 1)]
    return pts


def _add_arc(ps, c, r, a0, sweep, width):
    n = max(2, int(abs(sweep) / math.radians(10)))
    pts = [(c.x + r * math.cos(a0 + sweep * i / n),
            c.y + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        _add_outline(ps, _stadium_pts(ax, ay, bx, by, width))


def _add_track(ps, t):
    if isinstance(t, pcbnew.PCB_ARC):
        c = t.GetCenter()
        a0 = math.atan2(t.GetStart().y - c.y, t.GetStart().x - c.x)
        _add_arc(ps, c, t.GetRadius(), a0,
                 math.radians(t.GetAngle().AsDegrees()), t.GetWidth())
    else:
        a, b = t.GetStart(), t.GetEnd()
        _add_outline(ps, _stadium_pts(a.x, a.y, b.x, b.y, t.GetWidth()))


def _stroke(ps, pts, width):
    """Make a closed outline from a chain of track polygons."""
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        _add_outline(ps, _stadium_pts(ax, ay, bx, by, width))


def _add_shape(ps, s):
    """Add a graphic shape on a copper layer (an antenna, a logo, ...)."""
    t = s.GetShape()
    if t == pcbnew.SHAPE_T_POLY:
        poly = s.GetPolyShape()
        if s.IsSolidFill():
            # ponytail: this code ignores the width of the outline around
            # a filled polygon. Add the width if a simulation must agree
            # with the fabrication data.
            ps.BooleanAdd(poly)
        else:
            for i in range(poly.OutlineCount()):
                ol = poly.Outline(i)
                _stroke(ps, [(ol.CPoint(j).x, ol.CPoint(j).y)
                             for j in range(ol.PointCount())], s.GetWidth())
    elif t == pcbnew.SHAPE_T_RECT:
        pts = [(c.x, c.y) for c in s.GetRectCorners()]
        if s.IsSolidFill():
            _add_outline(ps, pts)
        else:
            _stroke(ps, pts, s.GetWidth())
    elif t == pcbnew.SHAPE_T_CIRCLE:
        c, r, w = s.GetCenter(), s.GetRadius(), s.GetWidth()
        if s.IsSolidFill():
            _add_outline(ps, _circle_pts(c.x, c.y, r + w / 2.0))
        else:  # a ring
            ring = pcbnew.SHAPE_POLY_SET()
            _add_outline(ring, _circle_pts(c.x, c.y, r + w / 2.0))
            hole = pcbnew.SHAPE_POLY_SET()
            _add_outline(hole, _circle_pts(c.x, c.y, max(r - w / 2.0, 0)))
            ring.BooleanSubtract(hole)
            ps.BooleanAdd(ring)
    elif t == pcbnew.SHAPE_T_SEGMENT:
        a, b = s.GetStart(), s.GetEnd()
        _add_outline(ps, _stadium_pts(a.x, a.y, b.x, b.y, s.GetWidth()))
    elif t == pcbnew.SHAPE_T_ARC:
        c = s.GetCenter()
        a0 = math.atan2(s.GetStart().y - c.y, s.GetStart().x - c.x)
        _add_arc(ps, c, s.GetRadius(), a0,
                 math.radians(s.GetArcAngle().AsDegrees()), s.GetWidth())
    elif t == pcbnew.SHAPE_T_BEZIER:
        s.RebuildBezierToSegmentsPointsList(5000)  # maximum error of 5 um
        pts = [(p.x, p.y) for p in s.GetBezierPoints()]
        if len(pts) >= 3 and s.IsSolidFill():
            _add_outline(ps, pts)
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            _add_outline(ps, _stadium_pts(ax, ay, bx, by, s.GetWidth()))
    # This function makes no polygons from text. extract() gives a warning.


def _copper_polys(board, layer_id, region, max_err):
    """Give the copper of one layer in `region`: (polygons, n_clipped).

    The function fractures the polygons. n_clipped is the number of items
    that are not zones and that go across the boundary of the region. The
    absorber terminates their cut ends like a matched load. Thus, if the
    cut copper is the structure under test, the S-parameters look good
    but they are incorrect. This occurred with a meander antenna at the
    default margin.
    """
    n_clipped = 0

    def crosses(bb):
        return not (bb.GetLeft() >= region.GetLeft()
                    and bb.GetRight() <= region.GetRight()
                    and bb.GetTop() >= region.GetTop()
                    and bb.GetBottom() <= region.GetBottom())

    ps = pcbnew.SHAPE_POLY_SET()
    for t in board.GetTracks():
        if not (t.IsOnLayer(layer_id)
                and t.GetBoundingBox().Intersects(region)):
            continue
        n_clipped += crosses(t.GetBoundingBox())
        if isinstance(t, pcbnew.PCB_VIA):
            try:
                flashed = t.FlashLayer(int(layer_id))
            except Exception:
                flashed = True
            if flashed:
                # A via of KiCad 10 has one padstack for each layer.
                # Thus you must ask for the annular ring layer by layer.
                # PCB_VIA.GetWidth() without a layer also causes a
                # debug assert.
                pos = t.GetPosition()
                _add_outline(ps, _circle_pts(pos.x, pos.y,
                                             t.GetWidth(int(layer_id)) / 2.0))
        else:
            _add_track(ps, t)
    for d in board.GetDrawings():
        if (isinstance(d, pcbnew.PCB_SHAPE) and d.IsOnLayer(layer_id)
                and d.GetBoundingBox().Intersects(region)):
            n_clipped += crosses(d.GetBoundingBox())
            _add_shape(ps, d)
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.IsOnLayer(layer_id) and pad.GetBoundingBox().Intersects(region):
                n_clipped += crosses(pad.GetBoundingBox())
                pad.TransformShapeToPolygon(ps, layer_id, 0, max_err)
        for it in fp.GraphicalItems():
            if (isinstance(it, pcbnew.PCB_SHAPE) and it.IsOnLayer(layer_id)
                    and it.GetBoundingBox().Intersects(region)):
                n_clipped += crosses(it.GetBoundingBox())
                _add_shape(ps, it)
    for zn in board.Zones():
        if zn.GetIsRuleArea() or not zn.IsOnLayer(layer_id) or not zn.IsFilled():
            continue
        ps.BooleanAdd(zn.GetFilledPolysList(layer_id))
    ps.Simplify()

    rect = pcbnew.SHAPE_POLY_SET()
    _add_outline(rect, ((region.GetLeft(), region.GetTop()),
                        (region.GetRight(), region.GetTop()),
                        (region.GetRight(), region.GetBottom()),
                        (region.GetLeft(), region.GetBottom())))
    ps.BooleanIntersection(rect)
    # ponytail: Fracture changes the holes into slits that have no width.
    # CSXCAD makes correct raster data from them. Change to a subtraction
    # of the holes if you see artifacts.
    ps.Fracture()

    polys = []
    for i in range(ps.OutlineCount()):
        ol = ps.Outline(i)
        pts = [[_mm(ol.CPoint(j).x), -_mm(ol.CPoint(j).y)]
               for j in range(ol.PointCount())]
        if len(pts) >= 3:
            polys.append(pts)
    return polys, n_clipped


def _feed_direction(board, pad):
    """Give the direction of the track that goes out of the pad.

    The direction is on the x axis or on the y axis. The function also
    gives the width of the track. If there is no track, the function
    gives (None, None). The function finds the direction only, which
    orients an MSL port. The user always selects the *type* of the port.
    """
    bbox = pad.GetBoundingBox()
    best = None
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or t.GetNetCode() != pad.GetNetCode():
            continue
        if not t.IsOnLayer(pad.GetLayer()):
            continue
        for a, b in ((t.GetStart(), t.GetEnd()), (t.GetEnd(), t.GetStart())):
            if bbox.Contains(a):
                d = b - a
                length = d.EuclideanNorm()
                if best is None or length > best[0]:
                    best = (length, d, t.GetWidth())
                break
    if best is None:
        return None, None
    _, d, width = best
    dx, dy = d.x, -d.y  # change the sign of y for right-handed coordinates
    if abs(dx) >= abs(dy):
        direction = [1 if dx > 0 else -1, 0]
    else:
        direction = [0, 1 if dy > 0 else -1]
    return direction, _mm(width)


def _lumped_elements(board, region, copper_layers, skip_refs):
    """Find the R/L/C parts that have 2 pads in `region`.

    The result is (elements, warnings). Each element is a box that
    bridges the gap between the two pads of the part. The box is parallel
    to the nearest Cartesian axis, because openEMS conducts along one
    axis only. The function changes the values to SI units. It ignores
    the parts in `skip_refs`, which hold a port pad. It gives a warning
    for a part that has an unknown value, or that is not on one copper
    layer.
    """
    z_of = {c["name"]: c["z"] for c in copper_layers}
    elements, warnings = [], []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        kind = ref[:1].upper()
        if kind not in ("R", "L", "C") or ref in skip_refs:
            continue
        if not fp.GetBoundingBox().Intersects(region):
            continue  # not in the simulated area: ignore it, with no warning
        # The terminals are the pads that have a *number*. Many real
        # footprints have more copper pads with no number, for mechanical
        # strength or for paste relief. These pads cannot hold a net and
        # they are not terminals. But a simple test of len(Pads()) == 2
        # refuses the complete part. This occurred with a KiLib
        # SMD_2terminal_chip_molded resistor: 4 pads, 2 of them with no
        # number. _copper_polys still simulates the copper of the pads
        # that have no number.
        pads = sorted((p for p in fp.Pads() if p.GetNumber()),
                      key=lambda p: p.GetNumber())
        if len(pads) != 2:
            # Before, this code was quiet. Thus "found nothing, said
            # nothing" was the most difficult failure to diagnose. Give a
            # warning only if the value is also correct. Then a default
            # "REF**", or a connector with a name that starts with R,
            # stays quiet. But a real 50-ohm part that has 3 terminals
            # gives a warning.
            if _parse_value(fp.GetValue(), kind) is not None:
                warnings.append(
                    "%s (value \"%s\") has %d numbered pad(s), not 2 -> not "
                    "modeled. A lumped element bridges exactly two terminals."
                    % (ref, fp.GetValue(), len(pads)))
            continue
        if any(p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD for p in pads):
            warnings.append("%s: not an SMD part (THT barrel not modeled) "
                            "-> not modeled" % ref)
            continue
        layer = _lname(pads[0].GetLayer())
        if layer not in z_of or _lname(pads[1].GetLayer()) != layer:
            warnings.append("%s: pads not both on one copper layer -> not "
                            "modeled" % ref)
            continue
        val = _parse_value(fp.GetValue(), kind)
        if val is None:
            warnings.append("%s: value \"%s\" not understood -> not modeled"
                            % (ref, fp.GetValue()))
            continue
        b1, b2 = pads[0].GetBoundingBox(), pads[1].GetBoundingBox()
        c1 = (_mm(b1.Centre().x), -_mm(b1.Centre().y))
        c2 = (_mm(b2.Centre().x), -_mm(b2.Centre().y))
        dx, dy = abs(c2[0] - c1[0]), abs(c2[1] - c1[1])
        z = z_of[layer]
        # The element is on the x axis or on the y axis. It bridges the
        # gap between the inner edges of the two pads. The y axis of the
        # model points in the opposite direction to the y of the screen.
        if dx >= dy:
            ny = "x"
            lo, hi = (b1, b2) if c1[0] <= c2[0] else (b2, b1)
            g0, g1 = _mm(lo.GetRight()), _mm(hi.GetLeft())
            c = 0.5 * (c1[1] + c2[1])
            hw = 0.5 * min(_mm(b1.GetHeight()), _mm(b2.GetHeight()))
            start, stop = [g0, c - hw, z], [g1, c + hw, z]
        else:
            ny = "y"
            lo, hi = (b1, b2) if c1[1] <= c2[1] else (b2, b1)
            g0, g1 = -_mm(lo.GetTop()), -_mm(hi.GetBottom())
            c = 0.5 * (c1[0] + c2[0])
            hw = 0.5 * min(_mm(b1.GetWidth()), _mm(b2.GetWidth()))
            start, stop = [c - hw, g0, z], [c + hw, g1, z]
        if g1 - g0 <= 0:
            warnings.append("%s: pads overlap (no gap to bridge) -> not "
                            "modeled" % ref)
            continue
        if min(dx, dy) > 0.25 * max(dx, dy, 1e-9):
            warnings.append("%s is placed off-axis; approximated as a %s-axis "
                            "element" % (ref, ny))
        elements.append({"ref": ref, "type": kind, "value": val, "ny": ny,
                         "layer": layer, "start": start, "stop": stop,
                         "pads": [list(c1), list(c2)]})
    return elements, warnings


def _port(board, pad, number, copper_layers):
    layer_name = _lname(pad.GetLayer())
    names = [c["name"] for c in copper_layers]
    if layer_name not in names:
        raise ValueError("Pad of port %d is not on a copper layer in the stackup"
                         % number)
    idx = names.index(layer_name)
    if len(names) < 2:
        raise ValueError("Board needs at least 2 copper layers (signal + reference)")
    ref = names[idx - 1] if idx == len(names) - 1 else names[idx + 1]

    bbox = pad.GetBoundingBox()
    direction, track_w = _feed_direction(board, pad)
    ext_x = _mm(bbox.GetWidth())
    ext_y = _mm(bbox.GetHeight())
    along_y = bool(direction and direction[0] == 0)
    fp = pad.GetParentFootprint()
    return {
        "number": number,
        "label": "%s pad %s (%s)" % (fp.GetReference(), pad.GetNumber(),
                                     pad.GetNetname() or "no net"),
        "x": _mm(bbox.Centre().x),
        "y": -_mm(bbox.Centre().y),
        "layer": layer_name,
        "ref_layer": ref,
        "width": ext_x if along_y else ext_y,     # extent across the feed
        "length": ext_y if along_y else ext_x,    # extent along the feed
        "direction": direction,
        "track_width": track_w,
        "type": "lumped",  # overwritten from the settings dialog
    }


def extract(board, pads, margin_mm, substrate=None):
    """Change a board into a dict: stackup, copper polygons, vias, ports.

    The function crops the geometry to the bounding box of the port pads
    plus margin_mm. The coordinates are in mm, the y axis points up, and
    z=0 is at the bottom of the board. If you give `substrate`, it
    replaces the stackup of the board with a uniform stackup. Its keys
    are "er", "tand", "h" (the total dielectric thickness in mm) and
    "cu_t" (in mm).
    """
    copper_layers, diel_layers = _stackup(board, substrate)
    max_err = int(getattr(board.GetDesignSettings(), "m_MaxError", 5000))

    first = pads[0].GetBoundingBox()
    region = pcbnew.BOX2I(first.GetPosition(), first.GetSize())
    for p in pads[1:]:
        region.Merge(p.GetBoundingBox())
    # Fit the domain to the full board (Edge.Cuts). Before, a domain that
    # had the size of the pad bbox cut the antennas. ponytail: the domain
    # is the full board. Use the bbox of the selection again if very large
    # boards make this operation too slow.
    brd = board.GetBoardEdgesBoundingBox()
    if brd.GetWidth() > 0 and brd.GetHeight() > 0:
        region.Merge(brd)
    # Use 2 times the margin: the inner band is clear air and the outer
    # band is the PML absorber. The code crops the copper at the outer
    # edge. Thus the cut planes and tracks go through the PML and
    # terminate almost matched. They do not reflect from an open end.
    region.Inflate(pcbnew.FromMM(2.0 * margin_mm))

    brd_box = board.GetBoardEdgesBoundingBox()
    diel_box = region.Intersect(brd_box)
    if diel_box.GetWidth() <= 0 or diel_box.GetHeight() <= 0:
        diel_box = region

    def rect_mm(b):
        return {"x0": _mm(b.GetLeft()), "x1": _mm(b.GetRight()),
                "y0": -_mm(b.GetBottom()), "y1": -_mm(b.GetTop())}

    polygons = {}
    clipped = {}
    for c in copper_layers:
        polys, n_clip = _copper_polys(board, c["id"], region, max_err)
        if polys:
            polygons[c["name"]] = polys
        if n_clip:
            clipped[c["name"]] = n_clip

    # _copper_polys does not model the text on copper. Give a warning; do
    # not remove the copper with no message. KiCad 10 can correct this: it
    # has ERROR_INSIDE, and ConvertBrdLayerToPolygonalContours includes
    # the text. Refer to the backlog in NOTES.md.
    warnings = []
    if clipped:
        warnings.append(
            "Copper on %s extends beyond the simulation domain and is cut "
            "at the boundary. If it's part of the structure under test, "
            "increase the domain margin." % "/".join(clipped))
    cu_ids = {c["id"] for c in copper_layers}
    texts = list(board.GetDrawings())
    for fp in board.GetFootprints():
        texts += list(fp.GraphicalItems()) + [fp.Reference(), fp.Value()]
    for it in texts:
        if (isinstance(it, pcbnew.PCB_TEXT) and it.IsVisible()
                and it.GetLayer() in cu_ids
                and it.GetBoundingBox().Intersects(region)):
            warnings.append(
                "text \"%s\" on %s is inside the simulated area but NOT "
                "modeled as copper"
                % (it.GetShownText(True), _lname(it.GetLayer())))

    z_of = {c["name"]: c["z"] for c in copper_layers}
    vias = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) and t.GetBoundingBox().Intersects(region):
            pos = t.GetPosition()
            top = _lname(t.TopLayer())
            bot = _lname(t.BottomLayer())
            vias.append({
                "x": _mm(pos.x), "y": -_mm(pos.y),
                "r": _mm(t.GetDrillValue()) / 2.0,
                "z0": z_of.get(bot, 0.0),
                "z1": z_of.get(top, copper_layers[0]["z"]),
            })

    ports = [_port(board, p, i + 1, copper_layers) for i, p in enumerate(pads)]
    # A port needs a ground return: copper on the reference layer that
    # touches any part of the pad. An antenna feed is at the edge of the
    # ground pour, thus a test on the *center* of the pad is too strict.
    # ponytail: this is a test of the bounding boxes. Change it to a
    # point-in-polygon test if pours with unusual shapes give incorrect
    # results.
    for p, pad in zip(ports, pads):
        bb = pad.GetBoundingBox()
        px0, px1 = _mm(bb.GetLeft()), _mm(bb.GetRight())
        py0, py1 = -_mm(bb.GetBottom()), -_mm(bb.GetTop())
        for poly in polygons.get(p["ref_layer"], []):
            xs = [pt[0] for pt in poly]
            ys = [pt[1] for pt in poly]
            if (min(xs) <= px1 and max(xs) >= px0
                    and min(ys) <= py1 and max(ys) >= py0):
                break
        else:
            raise ValueError(
                "Port %d (%s): no copper on reference layer %s under the "
                "pad.\nThe port drives the pad against %s, so a ground "
                "plane/pour on %s must reach at least the edge of the pad "
                "(for PCB antennas the pour edge typically sits right at "
                "the feed pad). Add/extend a filled zone there, refill "
                "zones, then re-run." % (p["number"], p["label"],
                                         p["ref_layer"], p["ref_layer"],
                                         p["ref_layer"]))

    # Model the SMD R/L/C parts as lumped elements. A part that holds a
    # port pad is the port, not a different element. Thus ignore its
    # footprint.
    port_refs = {pad.GetParentFootprint().GetReference() for pad in pads}
    lumped, le_warn = _lumped_elements(board, region, copper_layers, port_refs)
    warnings += le_warn

    for c in copper_layers:
        c.pop("id")
    return {
        "copper_layers": copper_layers,
        "dielectric_layers": diel_layers,
        "region": rect_mm(region),
        "board_rect": rect_mm(diel_box),
        "polygons": polygons,
        "vias": vias,
        "ports": ports,
        "lumped_elements": lumped,
        "warnings": warnings,
    }


if __name__ == "__main__":  # self-test of the value parser: python board_reader.py
    _CASES = [
        ("10k", "R", 10e3), ("4R7", "R", 4.7), ("1k5", "R", 1500.0),
        ("2.2k", "R", 2200.0), ("100", "R", 100.0), ("0", "R", 0.0),
        ("1M", "R", 1e6), ("50", "R", 50.0), ("4.7 1%", "R", 4.7),
        ("1.2pF", "C", 1.2e-12), ("100nF", "C", 100e-9), ("3n3", "C", 3.3e-9),
        ("0.1uF", "C", 0.1e-6), ("4p7", "C", 4.7e-12), ("22p", "C", 22e-12),
        ("3.3nH", "L", 3.3e-9), ("4n7", "L", 4.7e-9), ("1uH", "L", 1e-6),
        ("DNP", "R", None), ("", "C", None), ("xyz", "L", None),
    ]
    for _t, _k, _want in _CASES:
        _got = _parse_value(_t, _k)
        _ok = (_want is None and _got is None) or (
            _got is not None and abs(_got - _want) <= 1e-15 + 1e-6 * abs(_want))
        assert _ok, "%r/%s -> %r, want %r" % (_t, _k, _got, _want)
    print("parser OK (%d cases)" % len(_CASES))
