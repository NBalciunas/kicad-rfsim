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


def _default_stackup(board, cu_names):
    """Uniform FR4 stackup from board thickness when no stackup is saved."""
    total = pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness()) or 1.6
    n = len(cu_names)
    diel_t = max(total - n * DEF_CU_T, 0.1) / (n - 1)
    items = []
    for k, name in enumerate(cu_names):
        items.append({"kind": "copper", "name": name, "thickness": DEF_CU_T})
        if k < n - 1:
            items.append({"kind": "dielectric", "name": "dielectric %d" % (k + 1),
                          "thickness": diel_t, "epsilon": DEF_EPSILON,
                          "loss_tangent": DEF_LOSS_TAN})
    return items


def _stackup(board):
    """Physical stackup, top to bottom. Returns (copper_layers, dielectric_layers).

    Copper is treated as a zero-thickness sheet sitting on dielectric
    boundaries; its real thickness is kept as metadata for the loss model.
    z=0 is the bottom copper plane. Stackup properties come from the saved
    board file (save the board after editing Board Setup > Physical Stackup).
    """
    cu_ids = list(board.GetEnabledLayers().CuStack())
    cu_names = [_lname(lid) for lid in cu_ids]
    id_of = dict(zip(cu_names, cu_ids))

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
# rings are polygonized with plain math below.

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


def _add_track(ps, t):
    if isinstance(t, pcbnew.PCB_ARC):
        c, r = t.GetCenter(), t.GetRadius()
        a0 = math.atan2(t.GetStart().y - c.y, t.GetStart().x - c.x)
        sweep = math.radians(t.GetArcAngle().AsDegrees())
        n = max(2, int(abs(sweep) / math.radians(10)))
        pts = [(c.x + r * math.cos(a0 + sweep * i / n),
                c.y + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            _add_outline(ps, _stadium_pts(ax, ay, bx, by, t.GetWidth()))
    else:
        a, b = t.GetStart(), t.GetEnd()
        _add_outline(ps, _stadium_pts(a.x, a.y, b.x, b.y, t.GetWidth()))


def _copper_polys(board, layer_id, region, max_err):
    """All copper on one layer inside `region`, as simple (fractured) polygons."""
    ps = pcbnew.SHAPE_POLY_SET()
    for t in board.GetTracks():
        if not (t.IsOnLayer(layer_id)
                and t.GetBoundingBox().Intersects(region)):
            continue
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
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.IsOnLayer(layer_id) and pad.GetBoundingBox().Intersects(region):
                pad.TransformShapeToPolygon(ps, layer_id, 0, max_err)
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
    return polys


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


def extract(board, pads, margin_mm):
    """Board -> plain dict: stackup, copper polygons, vias, ports.

    Geometry is cropped to the bounding box of the port pads inflated by
    margin_mm. Coordinates in mm, y flipped, z=0 at board bottom.
    """
    copper_layers, diel_layers = _stackup(board)
    max_err = int(getattr(board.GetDesignSettings(), "m_MaxError", 5000))

    first = pads[0].GetBoundingBox()
    region = pcbnew.BOX2I(first.GetPosition(), first.GetSize())
    for p in pads[1:]:
        region.Merge(p.GetBoundingBox())
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
    for c in copper_layers:
        polys = _copper_polys(board, c["id"], region, max_err)
        if polys:
            polygons[c["name"]] = polys

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

    for c in copper_layers:
        c.pop("id")
    return {
        "copper_layers": copper_layers,
        "dielectric_layers": diel_layers,
        "region": rect_mm(region),
        "board_rect": rect_mm(diel_box),
        "polygons": polygons,
        "vias": vias,
        "ports": [_port(board, p, i + 1, copper_layers)
                  for i, p in enumerate(pads)],
    }
