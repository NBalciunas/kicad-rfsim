"""Extract stackup + copper geometry from a pcbnew BOARD into a plain dict.

This is the only module that touches pcbnew for geometry. Its output is
JSON-serializable: floats in mm, right-handed coordinates (y is flipped
relative to KiCad's y-down screen coordinates), z=0 at the board bottom.
Swap this module out for the IPC API on KiCad 9+ without touching the
simulation side.
"""
import math
import os
import re

import pcbnew

PM_FAST = pcbnew.SHAPE_POLY_SET.PM_FAST
# RF-sane defaults when the board has no explicit stackup: FR4.
DEF_EPSILON, DEF_LOSS_TAN, DEF_CU_T = 4.5, 0.02, 0.035

# SI multipliers, case-sensitive (m=milli vs M=mega). 'r'/'R' = decimal marker.
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
       "r": 1.0, "R": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
# which prefix letters are legal per part kind (avoids reading a cap's 'p'
# as pico on a resistor, etc.)
_PREFIX = {"R": "rRkKMG", "C": "pnuµ", "L": "pnuµm"}
_DNP = {"dnp", "dnf", "dni", "dnl", "nc", "n/a", "na", "-", "",
        "nopop", "no pop", "?"}


def _parse_value(text, kind):
    """'4k7'/'4.7k'/'100nF'/'3n3' -> float in SI (ohm/H/F), or None.

    Handles the RKM 'letter as decimal point' convention (4R7 = 4.7 ohm,
    3n3 = 3.3 nH/nF). kind is 'R', 'L' or 'C'. Unparseable/DNP -> None.
    """
    if not text:
        return None
    tok = text.strip().split()[0] if text.strip() else ""  # drop " 1%" etc.
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
    """Selected pads in deterministic order (footprint ref, pad number)."""
    pads = [p for fp in board.GetFootprints() for p in fp.Pads() if p.IsSelected()]
    pads.sort(key=lambda p: (p.GetParentFootprint().GetReference(), p.GetNumber()))
    return pads


def _stackup_from_file(path):
    """Parse the (stackup ...) block of a saved .kicad_pcb.

    KiCad 8's SWIG bindings do not wrap BOARD_STACKUP, so the file is the
    only scriptable source of dielectric properties. Returns a top-to-bottom
    list of {kind, name, thickness, epsilon, loss_tangent} or None.
    """
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    i = text.find("(stackup")
    if i < 0:
        return None
    depth, in_str, j = 0, False, i
    while j < len(text):  # find balanced end, ignoring parens in strings
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

    tree = parse(1)[0]  # skip leading '('
    items = []
    for node in tree:
        if not (isinstance(node, list) and node and node[0] == "layer"):
            continue
        props = {c[0]: c[1:] for c in node[2:] if isinstance(c, list) and c}
        typ = props.get("type", [""])[0].lower()
        # dielectric sublayers: sum every numeric thickness in the node
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
    """n copper sheets separated by equal dielectric layers (total diel_total)."""
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
    """Uniform FR4 stackup from board thickness when no stackup is saved."""
    total = pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness()) or 1.6
    return _uniform_stackup(cu_names, total - len(cu_names) * DEF_CU_T,
                            DEF_EPSILON, DEF_LOSS_TAN, DEF_CU_T)


def _stackup(board, substrate=None):
    """Physical stackup, top to bottom. Returns (copper_layers, dielectric_layers).

    Copper is treated as a zero-thickness sheet sitting on dielectric
    boundaries; its real thickness is kept as metadata for the loss model.
    z=0 is the bottom copper plane. Stackup properties come from the saved
    board file (save the board after editing Board Setup > Physical Stackup).
    """
    cu_ids = list(board.GetEnabledLayers().CuStack())
    cu_names = [_lname(lid) for lid in cu_ids]
    id_of = dict(zip(cu_names, cu_ids))

    if substrate:  # explicit user values win over the board file
        items = _uniform_stackup(cu_names, substrate["h"], substrate["er"],
                                 substrate["tand"], substrate["cu_t"])
    else:
        items = _stackup_from_file(board.GetFileName())
        if items:
            file_cu = [it["name"] for it in items if it["kind"] == "copper"]
            if file_cu != cu_names:  # stale/odd file -> defaults
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


# KiCad 8 SWIG only exposes TransformShapeToPolygon usably for PAD (the
# other overloads demand an unwrapped ERROR_LOC enum), so tracks/arcs/via
# rings and graphic shapes are polygonized with plain math below.

def _add_outline(ps, pts):
    ps.NewOutline()
    for x, y in pts:
        ps.Append(int(x), int(y))


def _circle_pts(cx, cy, r, n=32):
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _stadium_pts(ax, ay, bx, by, w, n=8):
    """Round-capped segment (track) polygon."""
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
                 math.radians(t.GetArcAngle().AsDegrees()), t.GetWidth())
    else:
        a, b = t.GetStart(), t.GetEnd()
        _add_outline(ps, _stadium_pts(a.x, a.y, b.x, b.y, t.GetWidth()))


def _stroke(ps, pts, width):
    """Closed stroked outline as a chain of stadium polygons."""
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        _add_outline(ps, _stadium_pts(ax, ay, bx, by, width))


def _add_shape(ps, s):
    """Graphic shape drawn on a copper layer (antennas, logos, ...)."""
    t = s.GetShape()
    if t == pcbnew.SHAPE_T_POLY:
        poly = s.GetPolyShape()
        if s.IsFilled():
            # ponytail: ignores the outline stroke width around a filled
            # poly; add it back if a fab-matched simulation ever needs it
            ps.BooleanAdd(poly, PM_FAST)
        else:
            for i in range(poly.OutlineCount()):
                ol = poly.Outline(i)
                _stroke(ps, [(ol.CPoint(j).x, ol.CPoint(j).y)
                             for j in range(ol.PointCount())], s.GetWidth())
    elif t == pcbnew.SHAPE_T_RECT:
        pts = [(c.x, c.y) for c in s.GetRectCorners()]
        if s.IsFilled():
            _add_outline(ps, pts)
        else:
            _stroke(ps, pts, s.GetWidth())
    elif t == pcbnew.SHAPE_T_CIRCLE:
        c, r, w = s.GetCenter(), s.GetRadius(), s.GetWidth()
        if s.IsFilled():
            _add_outline(ps, _circle_pts(c.x, c.y, r + w / 2.0))
        else:  # ring
            ring = pcbnew.SHAPE_POLY_SET()
            _add_outline(ring, _circle_pts(c.x, c.y, r + w / 2.0))
            hole = pcbnew.SHAPE_POLY_SET()
            _add_outline(hole, _circle_pts(c.x, c.y, max(r - w / 2.0, 0)))
            ring.BooleanSubtract(hole, PM_FAST)
            ps.BooleanAdd(ring, PM_FAST)
    elif t == pcbnew.SHAPE_T_SEGMENT:
        a, b = s.GetStart(), s.GetEnd()
        _add_outline(ps, _stadium_pts(a.x, a.y, b.x, b.y, s.GetWidth()))
    elif t == pcbnew.SHAPE_T_ARC:
        c = s.GetCenter()
        a0 = math.atan2(s.GetStart().y - c.y, s.GetStart().x - c.x)
        _add_arc(ps, c, s.GetRadius(), a0,
                 math.radians(s.GetArcAngle().AsDegrees()), s.GetWidth())
    elif t == pcbnew.SHAPE_T_BEZIER:
        s.RebuildBezierToSegmentsPointsList(5000)  # 5 um max error
        pts = [(p.x, p.y) for p in s.GetBezierPoints()]
        if len(pts) >= 3 and s.IsFilled():
            _add_outline(ps, pts)
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            _add_outline(ps, _stadium_pts(ax, ay, bx, by, s.GetWidth()))
    # text: no safe polygonization in KiCad 8 SWIG -> warned in extract()


def _copper_polys(board, layer_id, region, max_err):
    """Copper on one layer inside `region` -> (fractured polygons, n_clipped).

    n_clipped counts non-zone items sticking out past the region boundary:
    their cut ends terminate into the absorber like a matched load, which
    silently fakes good S-parameters if the cut copper was the structure
    under test (seen live with a meander antenna at default margin).
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
                pos = t.GetPosition()
                _add_outline(ps, _circle_pts(pos.x, pos.y, t.GetWidth() / 2.0))
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
        ps.BooleanAdd(zn.GetFilledPolysList(layer_id), PM_FAST)
    ps.Simplify(PM_FAST)

    rect = pcbnew.SHAPE_POLY_SET()
    _add_outline(rect, ((region.GetLeft(), region.GetTop()),
                        (region.GetRight(), region.GetTop()),
                        (region.GetRight(), region.GetBottom()),
                        (region.GetLeft(), region.GetBottom())))
    ps.BooleanIntersection(rect, PM_FAST)
    # ponytail: Fracture turns holes into zero-width slits; CSXCAD rasterizes
    # these fine. Switch to explicit hole subtraction if artifacts ever show.
    ps.Fracture(PM_FAST)

    polys = []
    for i in range(ps.OutlineCount()):
        ol = ps.Outline(i)
        pts = [[_mm(ol.CPoint(j).x), -_mm(ol.CPoint(j).y)]
               for j in range(ol.PointCount())]
        if len(pts) >= 3:
            polys.append(pts)
    return polys, n_clipped


def _feed_direction(board, pad):
    """Axis-snapped direction of the track leaving the pad, or (None, None).

    Only the direction is inferred (needed to orient an MSL port); the port
    *type* is always the user's explicit choice.
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
    dx, dy = d.x, -d.y  # flip y to right-handed
    if abs(dx) >= abs(dy):
        direction = [1 if dx > 0 else -1, 0]
    else:
        direction = [0, 1 if dy > 0 else -1]
    return direction, _mm(width)


def _lumped_elements(board, region, copper_layers, skip_refs):
    """Detect 2-pad R/L/C parts in `region` -> (elements, warnings).

    Each element is a box bridging the gap between its two pads, oriented
    along the nearest Cartesian axis (openEMS conducts along one axis).
    Values are parsed to SI. Parts in `skip_refs` (they carry a port pad)
    and unparseable/off-layer parts are skipped with a warning.
    """
    z_of = {c["name"]: c["z"] for c in copper_layers}
    elements, warnings = [], []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        kind = ref[:1].upper()
        if kind not in ("R", "L", "C") or ref in skip_refs:
            continue
        pads = list(fp.Pads())
        if len(pads) != 2 or not fp.GetBoundingBox().Intersects(region):
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
        if dx >= dy:  # element along x, spanning the gap between pad inner edges
            ny = "x"
            lo, hi = (b1, b2) if c1[0] <= c2[0] else (b2, b1)
            g0, g1 = _mm(lo.GetRight()), _mm(hi.GetLeft())
            c = 0.5 * (c1[1] + c2[1])
            hw = 0.5 * min(_mm(b1.GetHeight()), _mm(b2.GetHeight()))
            start, stop = [g0, c - hw, z], [g1, c + hw, z]
        else:         # element along y (world y is flipped vs screen)
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
    """Board -> plain dict: stackup, copper polygons, vias, ports.

    Geometry is cropped to the bounding box of the port pads inflated by
    margin_mm. Coordinates in mm, y flipped, z=0 at board bottom.
    substrate overrides the board stackup with a uniform one:
    {"er", "tand", "h" (total dielectric mm), "cu_t" (mm)}.
    """
    copper_layers, diel_layers = _stackup(board, substrate)
    max_err = int(getattr(board.GetDesignSettings(), "m_MaxError", 5000))

    first = pads[0].GetBoundingBox()
    region = pcbnew.BOX2I(first.GetPosition(), first.GetSize())
    for p in pads[1:]:
        region.Merge(p.GetBoundingBox())
    # Auto-fit the whole board (Edge.Cuts): antennas kept getting cropped by
    # pads-bbox-sized domains. ponytail: whole-board domain; bring back
    # selection-bbox cropping if huge boards ever make this too slow.
    brd = board.GetBoardEdgesBoundingBox()
    if brd.GetWidth() > 0 and brd.GetHeight() > 0:
        region.Merge(brd)
    # 2x margin: inner band = clear air, outer band = PML absorber. Copper is
    # cropped at the outer edge so cut planes/traces run through the PML and
    # terminate quasi-matched instead of reflecting off an open end.
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

    # Text on copper cannot be polygonized through KiCad 8's SWIG (every
    # route needs the unwrapped ERROR_LOC enum or crashes) -> warn instead
    # of silently dropping copper.
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
                "modeled (KiCad 8 API cannot convert text to copper)"
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
    # A port needs a ground return: ref-layer copper overlapping any part of
    # the pad (antenna feeds sit at the ground pour edge, so demanding the
    # pad *center* is too strict). ponytail: bbox overlap test, upgrade to
    # point-in-polygon if odd-shaped pours false-positive.
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

    # SMD R/L/C parts as lumped elements (a part carrying a port pad is the
    # port, not a separate element -> skip its footprint).
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


if __name__ == "__main__":  # value-parser self-check: python board_reader.py
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
