"""Read the stackup and the copper geometry of a pcbnew BOARD into a dict.

Only this module uses pcbnew for geometry. You can write its result to a
JSON file: floats in mm, right-handed coordinates (the y axis points in
the opposite direction to the y axis of the screen coordinates of KiCad),
and z=0 at the bottom of the board. The module uses the pcbnew SWIG
bindings of KiCad 10. The IPC API is not an alternative yet: IPC has no
function that makes polygons from tracks, arcs or text.
"""
import math
import os
import re

import pcbnew

# The version of the model dict, which `extract()` writes into
# model.json. The runner refuses a model that is NEWER than the version
# it knows, because a key that it does not read gives a silent and
# incorrect run, and not an error.
#
# Raise it when a change makes an OLD runner read a new model
# incorrectly. Do NOT raise it for a key that is only added: the runner
# reads each optional key with `.get(key, default)`, thus an old file
# still runs. The history:
#
#   1  2026-08-05  the first number. Every model.json before it has no
#      "version" key at all, and the runner reads that as version 1.
MODEL_VERSION = 1

# the default values if the board has no stackup: FR4
DEF_EPSILON, DEF_LOSS_TAN, DEF_CU_T = 4.5, 0.02, 0.035

# The SI multipliers. The letter case is important (m = milli, M = mega).
# The letters 'r'/'R', 'f'/'F' and 'h'/'H' show the position of the
# decimal point, thus they multiply by 1.
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
       "r": 1.0, "R": 1.0, "f": 1.0, "F": 1.0, "h": 1.0, "H": 1.0,
       "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
# **The prefixes are the SAME for R, L and C** (B23). Each type had its
# own set until 2026-08-06, thus a resistor of 5 milliohm, a capacitor
# of 1 mF and an inductor of 2.2 mH were all "not understood", and a
# user had to know which letter each type permits. 'K' is an alias of
# 'k'. The case rule stays: m = milli and M = mega, thus a resistor
# "1m" is 1 milliohm and NOT 1 Mohm. The dialog shows the number that
# this function read, thus the user sees which one it took.
_PREFIX = "pnuµmkKMGT"
# The mark of the UNIT. It shows the position of the decimal point and
# multiplies by 1, thus "4R7" is 4.7 ohm, "4F7" is 4.7 F and "4H7" is
# 4.7 H (F12). **Each type takes its OWN mark and no other one**,
# because the mark names the quantity: "4F7" on a resistor and "4H7" on
# a capacitor give no value.
#
# **An inductor refuses 'R' on purpose.** A real inductor with "4R7" on
# its body is 4.7 µH, and no rule here can know that from the text
# alone. Thus the value stays "not understood" and the user gives it in
# the dialog, which is better than 4.7 H with no message. A capacitor
# refuses it for the same reason.
_UNIT_MARK = {"R": "rR", "C": "fF", "L": "hH"}
_DNP = {"dnp", "dnf", "dni", "dnl", "nc", "n/a", "na", "-", "",
        "nopop", "no pop", "?"}

# The body ESL of a chip part with 2 terminals, in nH, against the code of
# the imperial package. These values are for the BODY only. They are
# smaller than the "mounted ESL" of a datasheet, because the FDTD model
# already contains the loop of the pads and the tracks: that copper is in
# the mesh. If you add the mounted value, you count the loop two times.
_ESL_NH = {"0201": 0.20, "0402": 0.25, "0603": 0.35, "0805": 0.45,
           "1206": 0.60, "1210": 0.70, "2010": 0.80, "2512": 0.90}
_ESL_DEFAULT_NH = 0.40  # a part whose package the code cannot read
# The series loss of the body: the ESR of a capacitor and the DCR of an
# inductor. A resistor gives its own value, thus it has no entry.
_ESR_OHM = {"C": 0.03, "L": 0.10}
# KiCad puts the imperial code first: "R_0402_1005Metric". Thus the first
# match is the correct one. The tests for a digit on each side prevent a
# match inside the metric code.
_PKG_RE = re.compile(r"(?<!\d)(%s)(?!\d)" % "|".join(_ESL_NH))
# These two codes are an imperial size AND a metric size, thus a name
# that holds one of them alone can be either. A library that puts the
# metric code first and does not write "Metric" gives an incorrect
# value, and not "unknown": "C_0603" is an imperial 0603 in the usual
# libraries, and a metric 0603 (= an imperial 0201) in some others. Each
# of the other metric codes (1005, 1608, 2012, 3216) is not in the
# table, thus it gives "unknown", which is safe. The name of KiCad always
# carries the metric code beside the imperial one, thus the absence of
# "Metric" is the signal.
_AMBIGUOUS_PKG = {"0402": ("01005", None), "0603": ("0201", 0.20)}

# The largest gap that the code accepts as a coplanar gap, in mm. A gap of
# a CPW on a PCB is usually 0.1 mm to 0.5 mm. Copper that is more distant
# than this limit is not a coplanar ground.
MAX_CPW_GAP = 2.0


def _parse_value(text, kind):
    """Change '4k7', '4.7k', '100nF' or '3n3' into a float in SI units.

    The units are ohm, H or F. `kind` is 'R', 'L' or 'C'. The function
    obeys the RKM convention, where a letter shows the position of the
    decimal point (4R7 = 4.7 ohm, 3n3 = 3.3 nH or 3.3 nF). If the text is
    DNP, or if the function cannot read the text, it gives None.

    **Every type takes every prefix** (`_PREFIX`): p, n, u/µ, m, k, M, G
    and T. The mark of the UNIT is the exception: a resistor takes
    'r'/'R', a capacitor takes 'f'/'F' and an inductor takes 'h'/'H',
    and no type takes the mark of another one (`_UNIT_MARK`).
    """
    if not text:
        return None
    words = text.strip().split()
    tok = words[0] if words else ""            # this removes " 1%" etc.
    # **The unit can be a word of its own**: "10 kOhm", "4.7 uF", "10 nH".
    # The first word is then a bare number, and the prefix goes away with
    # NO message: "10 kOhm" gave 10 ohm and "4.7 uF" gave 4.7 F. Thus
    # join the second word when it starts with a prefix or with a unit
    # letter. A tolerance starts with a DIGIT ("1%"), and a voltage
    # rating too ("25V"), thus neither one joins.
    if len(words) > 1 and words[1][:1] in _PREFIX + "rRfFhHoO":
        tok += words[1]
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
        if ch in _PREFIX or ch in _UNIT_MARK.get(kind, ""):
            left, right = tok[:i], tok[i + 1:]
            # A mark with NO digit on either side of it is not a
            # quantity: "R" alone names the type of the part and gives
            # no number. It gave 0.0 before, thus `build()` put a metal
            # SHORT across the two pads. "0R" and "R47" still carry a
            # number, thus they stay as they are.
            if not left and not right:
                return None
            num = (left + "." + right) if right else left
            try:
                return float(num) * _SI[ch]
            except ValueError:
                return None
    try:
        return float(tok)
    except ValueError:
        return None


def _unit_mark_only(text, kind):
    """Tell if the Value field holds the unit of `kind` and no number.

    A resistor whose Value field is "R" names its TYPE and gives no
    quantity, and "R 0402" is the same field with the package after it.
    The part is real, thus `extract` keeps it with no value: the dialog
    then shows it as a resistor with an empty field, and the user gives
    the number. A field that the parser cannot read ("xyz"), and DNP,
    are different. Those keep their warning and the part stays out.
    """
    if not text or not kind:
        return False
    words = text.strip().split()
    return bool(words) and words[0] in _UNIT_MARK.get(kind, "")


def _lname(layer_id):
    return pcbnew.BOARD.GetStandardLayerName(layer_id)


def _pad_layer_id(pad):
    """Give the id of the copper layer of a pad.

    PAD.GetLayer() gives F.Cu for each pad that comes from a file, also
    for a pad on an inner layer or on the back. Thus it cannot find a
    stripline. The layer set of the pad keeps the real layer. A pad on
    more than one copper layer, for example a through-hole pad, has no
    single layer: then use GetLayer(), as before.
    """
    cu = [lid for lid in pad.GetLayerSet().Seq() if pcbnew.IsCopperLayer(lid)]
    return cu[0] if len(cu) == 1 else pad.GetLayer()


def _pad_layer(pad):
    """Give the name of the copper layer of a pad."""
    return _lname(_pad_layer_id(pad))


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

    # `source` tells WHERE the values came from, and the dialog needs it:
    # it fills its substrate fields from a stackup that the FILE gives,
    # and it must not fill them from the FR4 default values, which would
    # look like the board and are only a fallback.
    if substrate:  # the values from the user have priority over the file
        source = "dialog"
        items = _uniform_stackup(cu_names, substrate["h"], substrate["er"],
                                 substrate["tand"], substrate["cu_t"])
    else:
        source = "file"
        items = _stackup_from_file(board.GetFileName())
        if items:
            file_cu = [it["name"] for it in items if it["kind"] == "copper"]
            if file_cu != cu_names:  # the file does not agree: use defaults
                items = None
        if not items:
            source = "default"
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
    return copper, diel, source


# The code below makes polygons from the tracks, the arcs, the via rings
# and the graphic shapes with simple mathematics. This is a result of
# KiCad 8: there, all the TransformShapeToPolygon functions but the
# function of PAD needed the ERROR_LOC enum, which SWIG did not wrap.
# KiCad 10 has pcbnew.ERROR_INSIDE. Thus
# BOARD.ConvertBrdLayerToPolygonalContours can replace all of this code
# and can also include the text.

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


def _ray_hits(px, py, axis, sign, polys):
    """Give the sorted distances from a point to the edges of `polys`.

    The ray starts at (px, py) and goes along `axis` (0 = x, 1 = y) in the
    direction of `sign`. The polygons are the copper of one layer, thus
    they are fractured and they do not cover each other.

    The function uses the half-open rule of the standard crossing test.
    Thus a ray through a vertex gives one hit, and not two, and the parity
    of the hits stays correct: an odd number of hits shows that the start
    point is inside the copper.
    """
    a, b = axis, 1 - axis
    p = (px, py)
    hits = []
    for poly in polys:
        prev = poly[-1]
        for cur in poly:
            if (prev[b] <= p[b]) != (cur[b] <= p[b]):
                t = prev[a] + ((p[b] - prev[b]) * (cur[a] - prev[a])
                               / (cur[b] - prev[b]))
                d = sign * (t - p[a])
                if d > 0:
                    hits.append(d)
            prev = cur
    return sorted(hits)


def _coplanar_gap(polys, x, y, direction):
    """Measure the gap between a feed line and the copper at its sides.

    A CPW port needs this value. The function sends a ray to each side of
    the line, at some positions along it. The first hit is the edge of the
    line, and the second hit is the edge of the copper on the other side
    of the gap. The result is the median of the measurements, in mm.

    The function gives None if the copper is not on the two sides, or if
    it is more distant than MAX_CPW_GAP. Then the structure is not a CPW.
    """
    if not direction or not polys:
        return None
    axis = 1 if direction[0] else 0  # the ray goes across the feed line
    dx, dy = direction
    # Take samples along the line and not at the pad. A pad is often wider
    # than the line. A sample that is not on copper (the line stops before
    # that point) gives an even number of hits, and the code ignores it.
    gaps = []
    for step in (0.4, 0.8, 1.2, 1.6, 2.0):
        px, py = x + dx * step, y + dy * step
        pair = []
        for sign in (1, -1):
            hits = _ray_hits(px, py, axis, sign, polys)
            if len(hits) % 2 == 0:  # the start point is not on copper
                break
            if len(hits) < 2 or hits[1] - hits[0] > MAX_CPW_GAP:
                break  # no copper at the side of the line: not a CPW
            pair.append(hits[1] - hits[0])
        if len(pair) == 2:
            gaps += pair
    if len(gaps) < 4:  # 2 sides at 2 positions or more
        return None
    gaps.sort()
    n = len(gaps)
    return round(0.5 * (gaps[(n - 1) // 2] + gaps[n // 2]), 5)


def copper_along(polys, x, y, direction):
    """Tell if copper lies along `direction` from the pad at (x, y).

    The dialog can give a manual feed direction for a pad that has no
    track (a feed line that the user drew as a shape or as a polygon).
    An MSL port adds its own strip over the port box. If the board has
    no copper there, the simulation then contains a line that the board
    does not have. The test takes the samples of _coplanar_gap: 0.4 mm
    to 2.0 mm from the pad.
    """
    if not direction or not polys:
        return False
    axis = 1 if direction[0] else 0
    dx, dy = direction
    on = 0
    for step in (0.4, 0.8, 1.2, 1.6, 2.0):
        # An odd number of hits shows that the sample point is inside
        # the copper.
        on += len(_ray_hits(x + dx * step, y + dy * step, axis, 1,
                            polys)) % 2
    return on >= 3


def copper_run(polys, x, y, direction, limit=60.0, step=0.5):
    """Give the distance that copper runs along `direction`, in mm.

    A de-embedded port is `max(3*w, 6*res)` long, and `6*res` is 14 mm
    or more at the coarse preset. A SHORT feed line is shorter than
    that: the measurement plane of the port then lies inside the patch
    that the line feeds, where the values of a line have no meaning, and
    the strip that the port adds goes out past the end of the copper
    (problem 13). The runner caps the length of the port with this
    value.

    The function walks along the direction and gives the distance to the
    LAST point that is still on copper. An odd number of ray hits shows
    that a point is inside the copper, which is the standard crossing
    test. The walk is more robust than one ray: `Fracture` divides the
    copper into polygons that share an edge, thus a single ray gives a
    hit where the copper does not in fact end.

    It gives None when the copper runs further than `limit`, and None
    when the pad itself is not on the copper of that layer.
    """
    if not direction or not polys:
        return None
    axis = 1 if direction[0] else 0
    dx, dy = direction

    def on_copper(d):
        return len(_ray_hits(x + dx * d, y + dy * d, axis, 1, polys)) % 2 == 1

    if not on_copper(0.0):
        return None
    d = 0.0
    while d + step <= limit:
        if not on_copper(d + step):
            # Refine the edge between the last point that is on copper
            # and the first that is not.
            lo, hi = d, d + step
            for _ in range(6):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if on_copper(mid) else (lo, mid)
            return round(lo, 4)
        d += step
    return None


def _touches(polys, box):
    """Tell if any polygon of `polys` overlaps the box (x0, y0, x1, y1).

    This is a test of the bounding boxes. An antenna feed pad is at the
    EDGE of the ground pour, thus a test on the center of the pad is too
    strict. Refer to the guard for the ground return in extract().
    """
    px0, py0, px1, py1 = box
    for poly in polys:
        xs = [pt[0] for pt in poly]
        ys = [pt[1] for pt in poly]
        if (min(xs) <= px1 and max(xs) >= px0
                and min(ys) <= py1 and max(ys) >= py0):
            return True
    return False


def _pad_box(pad):
    """Give the bounding box of a pad as (x0, y0, x1, y1) in model mm."""
    bb = pad.GetBoundingBox()
    return (_mm(bb.GetLeft()), -_mm(bb.GetBottom()),
            _mm(bb.GetRight()), -_mm(bb.GetTop()))


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
        if not t.IsOnLayer(_pad_layer_id(pad)):
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


def package_presets():
    """Give the body ESL of each package that the code knows, in H.

    The settings dialog makes its list of presets from this table. Thus
    the values stay in one place only.
    """
    return {k: v * 1e-9 for k, v in _ESL_NH.items()}


def esr_presets():
    """Give the body ESR of each type of part, in ohm.

    The dialog needs it for a part whose type the USER selects: the ESR
    comes from the type, in the same way as it does for a part that the
    refdes describes. The table stays in this module only.
    """
    return dict(_ESR_OHM)


def _package(name):
    """Give (the imperial package code, a warning) for a footprint name.

    The name is the library item name, for example "R_0402_1005Metric".
    The code is None when the name holds no size that the table knows.
    The warning is None, or the text of an AMBIGUOUS name: the value that
    such a name gives is incorrect, and not absent, thus the user must
    see it.
    """
    m = _PKG_RE.search(name or "")
    if not m:
        return None, None
    pkg = m.group(1)
    if pkg not in _AMBIGUOUS_PKG or "metric" in (name or "").lower():
        return pkg, None
    twin, twin_esl = _AMBIGUOUS_PKG[pkg]
    other = ("an imperial %s (ESL %.2f nH)" % (twin, twin_esl) if twin_esl
             else "an imperial %s, which this code does not know" % twin)
    return pkg, ('the footprint "%s" gives the size %s with no metric code '
                 'beside it, thus that size can be imperial or metric. The '
                 'model uses the imperial %s (ESL %.2f nH). A METRIC %s is '
                 '%s: give the values by hand in the dialog if the part is '
                 'that one.'
                 % (name, pkg, pkg, _ESL_NH[pkg], pkg, other))


def _parasitics(fp, kind):
    """Give (package, ESL in H, ESR in ohm, warning) for the body of a part.

    An ideal element gives incorrect results above about 1 GHz: the ESL of
    an 0402 capacitor puts its self-resonance inside a usual sweep. The
    solver puts these values in series with the value of the part.
    """
    try:
        name = fp.GetFPID().GetUniStringLibItemName()
    except Exception:
        name = None
    pkg, warn = _package(name)
    esl = _ESL_NH.get(pkg, _ESL_DEFAULT_NH) * 1e-9
    return pkg, esl, _ESR_OHM.get(kind, 0.0), warn


def _lumped_elements(board, region, copper_layers, skip_refs):
    """Find each part with 2 terminals in `region`.

    The result is (elements, warnings). Each element is a box that
    bridges the gap between the two pads of the part. The box is parallel
    to the nearest Cartesian axis, because openEMS conducts along one
    axis only. The function changes the values to SI units. It ignores
    the parts in `skip_refs`, which hold a port pad. It gives a warning
    for a part that has an unknown value, or that is not on one copper
    layer.

    A refdes that starts with R, L or C gives the TYPE of the part, and
    the Value field gives its value. **Each other part with 2 terminals
    also comes back**, with `type` = None and `value` = None: a diode, a
    ferrite bead, a crystal or a footprint of your own is a 2-terminal
    part that a user can model as an R, an L or a C. The dialog shows
    such a part as "Unknown" with its Model checkbox OFF, thus it changes
    no simulation until the user gives it a type and a value.
    """
    z_of = {c["name"]: c["z"] for c in copper_layers}
    elements, warnings = [], []
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        kind = ref[:1].upper()
        if kind not in ("R", "L", "C"):
            kind = None          # the user gives the type in the dialog
        if ref in skip_refs:
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
            # A part whose type the refdes does not give stays quiet
            # here: a connector, a mounting hole or a footprint of your
            # own has any number of pads, and a warning for each one is
            # noise. The same rule holds for the three tests below.
            if kind and _parse_value(fp.GetValue(), kind) is not None:
                warnings.append(
                    "%s (value \"%s\") has %d numbered pad(s), not 2 -> not "
                    "modeled. A lumped element bridges exactly two terminals."
                    % (ref, fp.GetValue(), len(pads)))
            continue
        if any(p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD for p in pads):
            if kind:
                warnings.append("%s: not an SMD part (THT barrel not modeled) "
                                "-> not modeled" % ref)
            continue
        layer = _pad_layer(pads[0])
        if layer not in z_of or _pad_layer(pads[1]) != layer:
            if kind:
                warnings.append("%s: pads not both on one copper layer -> not "
                                "modeled" % ref)
            continue
        # A part with no type has no value either: the Value field of a
        # diode holds a part number, and not a quantity. The dialog asks
        # the user for both.
        val = _parse_value(fp.GetValue(), kind) if kind else None
        if kind and val is None:
            # "R" alone names the type and no quantity. KEEP that part:
            # the dialog shows it as a resistor with an empty value, the
            # user gives the number, and the type stays open in the same
            # way as it is for every other row. A part that still has no
            # value goes no further than the dialog, because rfsim.py
            # takes only an element that holds a type AND a value.
            if _unit_mark_only(fp.GetValue(), kind):
                warnings.append(
                    "%s: value \"%s\" gives the type and no quantity -> give "
                    "the value in the dialog" % (ref, fp.GetValue()))
            else:
                warnings.append("%s: value \"%s\" not understood -> not "
                                "modeled" % (ref, fp.GetValue()))
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
            if kind:
                warnings.append("%s: pads overlap (no gap to bridge) -> not "
                                "modeled" % ref)
            continue
        if kind and min(dx, dy) > 0.25 * max(dx, dy, 1e-9):
            warnings.append("%s is placed off-axis; approximated as a %s-axis "
                            "element" % (ref, ny))
        pkg, esl, esr, pkg_warn = _parasitics(fp, kind)
        if pkg_warn:
            warnings.append("%s: %s" % (ref, pkg_warn))
        elements.append({"ref": ref, "type": kind, "value": val, "ny": ny,
                         "layer": layer, "start": start, "stop": stop,
                         "pads": [list(c1), list(c2)],
                         "package": pkg, "esl": esl, "esr": esr})
    return elements, warnings


def _port(board, pad, number, copper_layers):
    layer_name = _pad_layer(pad)
    names = [c["name"] for c in copper_layers]
    if layer_name not in names:
        raise ValueError("Pad of port %d is not on a copper layer in the stackup"
                         % number)
    idx = names.index(layer_name)
    if len(names) < 2:
        raise ValueError("Board needs at least 2 copper layers (signal + reference)")
    ref = names[idx - 1] if idx == len(names) - 1 else names[idx + 1]

    # A stripline needs a plane above the strip and a plane below it. Thus
    # the port must be on an inner layer. openEMS puts the voltage probes
    # at the same distance above and below, thus its model is a symmetric
    # stripline. The code gives the mean distance, and extract() gives a
    # warning if the two distances do not agree.
    z_of = {c["name"]: c["z"] for c in copper_layers}
    ref2, height, asym = None, None, 0.0
    if 0 < idx < len(names) - 1:
        ref2 = names[idx - 1]
        h_dn = z_of[layer_name] - z_of[names[idx + 1]]
        h_up = z_of[ref2] - z_of[layer_name]
        if h_up > 0 and h_dn > 0:
            height = round(0.5 * (h_up + h_dn), 6)
            asym = abs(h_up - h_dn) / (h_up + h_dn)
        else:
            ref2 = None

    bbox = pad.GetBoundingBox()
    direction, track_w = _feed_direction(board, pad)
    ext_x = _mm(bbox.GetWidth())
    ext_y = _mm(bbox.GetHeight())
    fp = pad.GetParentFootprint()
    return {
        "number": number,
        # The tooltip of the port row shows this: "R1 Pad 2 (GND)".
        "label": "%s Pad %s (%s)" % (fp.GetReference(), pad.GetNumber(),
                                     pad.GetNetname() or "no net"),
        "x": _mm(bbox.Centre().x),
        "y": -_mm(bbox.Centre().y),
        "layer": layer_name,
        "ref_layer": ref,
        "ref_layer2": ref2,      # the second plane of a stripline
        "height": height,        # strip to each plane, mm; None = no stripline
        "asymmetry": round(asym, 4),
        "gap": None,             # the coplanar gap; extract() measures it
        # On the axes of the board: length is the pad extent in x, and
        # width is the pad extent in y. The lumped port box of the runner
        # and the port marks of the GUI read them in that way.
        "width": ext_y,
        "length": ext_x,
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
    copper_layers, diel_layers, stack_src = _stackup(board, substrate)
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
    # the text.
    warnings = []
    # The stackup comes from the board FILE, thus a change in Board Setup
    # that the user did not save is NOT in the run: the run finishes and
    # it gives a number for the OLD substrate. **The silence is the
    # harm**, and not the extra save. SWIG gives no access to
    # BOARD_STACKUP (P12), thus the plugin cannot read the change; it can
    # say that the change may be there.
    #
    # `substrate` means that the user typed the values in the dialog,
    # thus the file is not the source and there is nothing to say.
    if not substrate:
        try:
            unsaved = bool(board.IsModified())
        except Exception:
            # A board that comes from LoadBoard has the method. Keep the
            # run alive for any binding that does not.
            unsaved = False
        if unsaved:
            warnings.append(
                "The board has unsaved changes. The substrate (er, tan d, "
                "thickness) is read from the SAVED file, so a change in "
                "Board Setup > Physical Stackup that you did not save is "
                "NOT in this run and the result will be wrong for it. Save "
                "the board and run again.")
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
    # Measure the coplanar gap of each port. The copper of the layer must
    # exist first, thus this operation comes after the extraction of the
    # polygons. A port that has a gap can use a CPW port.
    for p, pad in zip(ports, pads):
        polys_l = polygons.get(p["layer"], [])
        p["gap"] = _coplanar_gap(polys_l, p["x"], p["y"], p["direction"])
        # How far the copper runs from the pad along the feed. The
        # runner caps the length of a de-embedded port with it: refer to
        # `copper_run` and to problem 13. None means "further than the
        # limit", and the runner then uses its own length.
        p["copper_run"] = copper_run(polys_l, p["x"], p["y"], p["direction"])
        # A stripline needs copper on the two planes. _port reads the
        # stackup only, thus it gives a height for each strip on an inner
        # layer, also when the second plane is empty above the pad. The
        # port would then put its voltage probes into open board. The
        # guard below tests the reference layer; this test is for the
        # second plane.
        if p["height"] and not _touches(polygons.get(p["ref_layer2"], []),
                                        _pad_box(pad)):
            warnings.append(
                "Port %d (%s): no copper on %s above the pad, so this is "
                "not a stripline. The Stripline port type is not offered."
                % (p["number"], p["label"], p["ref_layer2"]))
            p["height"], p["ref_layer2"], p["asymmetry"] = None, None, 0.0
        if not p["direction"]:
            # The gap of each candidate direction, for the manual feed
            # of the dialog. A drawn CPW has no track, and the dialog
            # offers the CPW type only for a direction that has a gap.
            p["gaps"] = {key: _coplanar_gap(polys_l, p["x"], p["y"], d)
                         for key, d in (("+x", [1, 0]), ("-x", [-1, 0]),
                                        ("+y", [0, 1]), ("-y", [0, -1]))}
        if p["height"] and p["asymmetry"] > 0.25:
            warnings.append(
                "Port %d (%s): the strip is not centered between %s and %s "
                "(%.0f%% off). A Stripline port models a centered strip, so "
                "its reference plane is approximate."
                % (p["number"], p["label"], p["ref_layer2"], p["ref_layer"],
                   100.0 * p["asymmetry"]))
    # A port needs a ground return: copper on the reference layer that
    # touches any part of the pad. An antenna feed is at the edge of the
    # ground pour, thus a test on the *center* of the pad is too strict.
    # ponytail: this is a test of the bounding boxes. Change it to a
    # point-in-polygon test if pours with unusual shapes give incorrect
    # results.
    for p, pad in zip(ports, pads):
        if not _touches(polygons.get(p["ref_layer"], []), _pad_box(pad)):
            # A CPW carries its return current on the coplanar ground of
            # its own layer. Thus a board with no plane below the pad is
            # correct for a CPW port, and the guard must not stop it. A
            # drawn CPW has no track: then the gap of a candidate
            # direction counts too.
            g = p["gap"] or next((v for v in (p.get("gaps") or {}).values()
                                  if v), None)
            if g:
                warnings.append(
                    "Port %d (%s): no copper on reference layer %s, but "
                    "there is coplanar copper %.3f mm from the feed line. "
                    "Set this port to \"Coplanar (CPW)\": a Lumped or "
                    "Microstrip port has no return path here."
                    % (p["number"], p["label"], p["ref_layer"], g))
                continue
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
        "version": MODEL_VERSION,
        # "file", "default" or "dialog": refer to _stackup().
        "stackup_source": stack_src,
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
        # B23: the prefixes are the same for the three types. The first
        # column of each line is what the parser refused before it.
        ("5m", "R", 5e-3), ("0m5", "R", 5e-4), ("2G2", "R", 2.2e9),
        ("1T", "R", 1e12), ("1K5", "R", 1500.0), ("4p7", "R", 4.7e-12),
        ("1mF", "C", 1e-3), ("2m2F", "C", 2.2e-3), ("1kF", "C", 1e3),
        ("2.2mH", "L", 2.2e-3), ("10mH", "L", 1e-2), ("1kH", "L", 1e3),
        ("100p", "L", 100e-12), ("1G", "L", 1e9),
        # The case rule: m is milli and M is mega, on every type.
        ("1m", "R", 1e-3), ("1M", "L", 1e6),
        # The ohm mark stays on the resistor. An inductor marked "4R7"
        # is 4.7 µH on its package, thus the parser must NOT give 4.7 H.
        ("4R7", "L", None), ("4R7", "C", None), ("0R", "R", 0.0),
        # F12: the mark of the unit is not for the resistor alone. A
        # medial 'F' or 'H' gave None before 2026-08-20.
        ("4F7", "C", 4.7), ("1F5", "C", 1.5), ("4f7", "C", 4.7),
        ("4H7", "L", 4.7), ("2H2", "L", 2.2), ("4h7", "L", 4.7),
        # ...but each type takes its OWN mark and no other one.
        ("4F7", "L", None), ("4F7", "R", None),
        ("4H7", "C", None), ("4H7", "R", None),
        # A mark with no number is not a value. The trailing unit goes
        # away first, thus the token is empty and not "0" (compare 0R).
        ("F", "C", None), ("H", "L", None),
        # B24: the resistor took no such path, thus "R" alone gave 0.0
        # and `build()` put a metal SHORT across the two pads. A zero
        # ohm link still writes "0R", and it must keep its 0.0 above.
        ("R", "R", None), ("r", "R", None), ("R 0402", "R", None),
        ("n", "C", None), ("k", "R", None),
        # The unit as a word of its own. Each one of these gave the bare
        # number before 2026-08-06, thus 1000 times too much or too
        # little, with no message.
        ("10 kOhm", "R", 10e3), ("10 mOhm", "R", 1e-2),
        ("4.7 uF", "C", 4.7e-6), ("10 nH", "L", 10e-9),
        ("100 ohm", "R", 100.0), ("1 M", "R", 1e6),
        # A tolerance and a voltage rating must NOT join: they start
        # with a digit.
        ("4.7 1%", "R", 4.7), ("10u 25V", "C", 10e-6),
        ("4.7 kOhm 1%", "R", 4700.0), ("1 nF 50V", "C", 1e-9),
    ]
    for _t, _k, _want in _CASES:
        _got = _parse_value(_t, _k)
        _ok = (_want is None and _got is None) or (
            _got is not None and abs(_got - _want) <= 1e-15 + 1e-6 * abs(_want))
        assert _ok, "%r/%s -> %r, want %r" % (_t, _k, _got, _want)
    print("parser OK (%d cases)" % len(_CASES))

    # The (stackup ...) block of a board file. It is the source of the
    # "KiCad's Stackup" preset of the dialog (P8/F7/B7), thus a change
    # of this parser changes what a Rogers board simulates as.
    import tempfile
    _PCB = """(kicad_pcb (version 20241229)
      (setup
        (stackup
          (layer "F.Cu" (type "copper") (thickness 0.018))
          (layer "dielectric 1" (type "core") (thickness 0.508)
            (material "RO4350B") (epsilon_r 3.48) (loss_tangent 0.0037))
          (layer "B.Cu" (type "copper") (thickness 0.018))
          (copper_finish "None")
        )
      )
    )"""
    with tempfile.TemporaryDirectory() as _dir:
        _path = os.path.join(_dir, "t.kicad_pcb")
        with open(_path, "w", encoding="utf-8") as _fh:
            _fh.write(_PCB)
        _st = _stackup_from_file(_path)
        with open(_path, "w", encoding="utf-8") as _fh:
            _fh.write("(kicad_pcb (version 20241229) (setup))")
        _none = _stackup_from_file(_path)
    assert [it["kind"] for it in _st] == ["copper", "dielectric", "copper"], _st
    assert _st[1]["epsilon"] == 3.48 and _st[1]["loss_tangent"] == 0.0037, _st
    assert _st[1]["thickness"] == 0.508 and _st[0]["thickness"] == 0.018, _st
    # **A file with no stackup gives None, and NOT the FR4 fallback.**
    # The dialog tests this: it must not show the default values of the
    # code as if the board gave them.
    assert _none is None, _none
    print("stackup OK (a Rogers core, and a file with no stackup)")

    # The gap of a CPW: a strip of 1.0 mm wide on the x axis, with a
    # ground at each side. The gap is 0.2 mm.
    def _rect(x0, y0, x1, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

    _STRIP = _rect(0.0, -0.5, 20.0, 0.5)
    _CPW = [_STRIP, _rect(0.0, 0.7, 20.0, 5.0), _rect(0.0, -5.0, 20.0, -0.7)]
    _GEO = [
        ("cpw", _CPW, [1, 0], 0.2),
        ("cpw, the feed on the y axis",
         [_rect(-0.5, 0.0, 0.5, 20.0), _rect(0.7, 0.0, 5.0, 20.0),
          _rect(-5.0, 0.0, -0.7, 20.0)], [0, 1], 0.2),
        ("cpw, the feed in -x", _CPW, [-1, 0], None),   # the strip is at +x
        ("microstrip: no coplanar copper", [_STRIP], [1, 0], None),
        ("ground at one side only", _CPW[:2], [1, 0], None),
        ("the ground is too distant",
         [_STRIP, _rect(0.0, 3.0, 20.0, 5.0), _rect(0.0, -5.0, 20.0, -3.0)],
         [1, 0], None),
        ("no track", _CPW, None, None),
    ]
    for _name, _polys, _dir, _want in _GEO:
        _got = _coplanar_gap(_polys, 0.0, 0.0, _dir)
        _ok = (_want is None and _got is None) or (
            _got is not None and abs(_got - _want) < 1e-6)
        assert _ok, "%s -> %r, want %r" % (_name, _got, _want)
    # A ray that goes exactly through a vertex must give one hit only.
    assert len(_ray_hits(0.0, -0.5, 1, 1, [_STRIP])) == 1, "vertex counted 2x"
    # The copper test for a manual feed direction: the strip goes to +x
    # from the origin, thus +x is on copper and -x is empty board.
    assert copper_along([_STRIP], 0.0, 0.0, [1, 0]), "copper_along +x"
    assert not copper_along([_STRIP], 0.0, 0.0, [-1, 0]), "copper_along -x"
    assert not copper_along([_STRIP], 0.0, 0.0, None), "copper_along None"
    print("geometry OK (%d cases)" % (len(_GEO) + 4))

    # (the name, the code, does it give a warning?). A name that carries
    # the metric code is not ambiguous, also when the imperial code is
    # 0402 or 0603. A bare "C_0603" IS ambiguous: it can be an imperial
    # 0603 (0.35 nH) or a metric 0603, which is an imperial 0201
    # (0.20 nH). A bare code that is not also a metric size, such as
    # 1206, is not ambiguous.
    _PKGS = [("R_0402_1005Metric", "0402", False),
             ("C_0603_1608Metric", "0603", False),
             ("R_0201_0603Metric", "0201", False),
             ("L_1210_3225Metric", "1210", False),
             ("C_1005", None, False), ("SOT-23", None, False),
             ("R_2512_6332Metric", "2512", False),
             ("C_0603", "0603", True), ("R_0402", "0402", True),
             ("C_1206", "1206", False), ("", None, False)]
    for _name, _want, _warn in _PKGS:
        _got, _msg = _package(_name)
        assert _got == _want, "%s -> %r, want %r" % (_name, _got, _want)
        assert bool(_msg) == _warn, \
            "%s -> warning %r, want %s" % (_name, _msg, _warn)
    print("package OK (%d cases)" % len(_PKGS))
