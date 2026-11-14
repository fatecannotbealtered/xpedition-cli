"""Text on the board's silkscreen: ``silkscreen-text create`` and ``silkscreen-text delete``.

A text belongs to the board, not to a part, and is known by its words and where it lies: the top or
bottom silkscreen, or a user draft layer that a team plots as silkscreen (``user:<layer>``); the
target key is ``/silkscreen-text/<where>/<text>``. Before it is placed, its box is worked out from its words,
height and rotation, and checked where the board house would clip or hide it (over a pad, a bare
hole or a soldermask opening, on another text, under a part), where it leaves the board or crosses
a cutout, and where it would crack on a flex board's bend.
"""
from __future__ import annotations

import math
from typing import Any

from .._driver import AdapterError
from . import base
from . import layout as L
from . import layout_output as O
from .base import Context
from .layout_edit import FAB_COVER_LAYER, FAB_SOLDERMASK, _openings, _overlaps, _side
from .layout_parts import SIDES
from .plan import board_metrics
from .plan import openings as OP
from .plan import stitching as ST
from .platform import com

FAB_SILKSCREEN = 2  # EPcbFabricationType epcbFabSilkscreen
FONT = "VeriBest Gerber 0"
HEIGHT_MM = 1.0
PEN_MM = 0.15
CHAR_WIDTH = 1.0  # a character's advance per mm of height in that font, a little over what Layout reports (0.98 to 0.99)
ABOVE, BELOW = 0.75, 0.95  # how far the box reaches above and below its centre, per mm of height: below holds the underscores and
# descenders (Layout reported 0.74 and 1.01 mm with the pen for a 1 mm text)
BLOCKING = {"label_over_pad": "runs over {other}, where the board house clips the ink off the copper",
            "label_overlap": "overlaps {other}",
            "label_under_part": "lies under {other}, hidden once that part is fitted"}
TEXTS_LISTED = 20
NEW = "<the text to place>"  # the owner the text to place is given in the checks, so that only its own findings are taken
Box = tuple[float, float, float, float]


def place_of(side: str, user_layer: str | None) -> str:
    """Where a text lies: its side's silkscreen, or ``user:<layer>``."""
    return f"user:{user_layer}" if user_layer else side


def text_key(place: str, text: str) -> str:
    return f"/silkscreen-text/{L.segment(place)}/{L.segment(text)}"


def board_texts(doc: Any) -> list[tuple[Any, str, str]]:
    """The board's own texts on the silkscreen and on the user draft layers, not a part's: (text
    object, words, where)."""
    out = []
    for text in L.collection(doc, "FabricationLayerTexts"):
        try:
            if int(text.Type) != FAB_SILKSCREEN or text.Component is not None:
                continue
            out.append((text, str(text.TextString), SIDES.get(int(text.Side), "top")))
        except Exception:  # noqa: BLE001 - a text that cannot be read is left alone
            continue
    for text in L.collection(doc, "UserLayerTexts"):
        try:
            if text.Component is not None:
                continue
            out.append((text, str(text.TextString), place_of("", L.name_of(text.UserLayer).strip())))
        except Exception:  # noqa: BLE001 - a text that cannot be read is left alone
            continue
    return out


def find_text(doc: Any, place: str, words: str) -> Any | None:
    found = [text for text, string, where in board_texts(doc) if string == words and where == place]
    if len(found) > 1:
        raise AdapterError("E_AMBIGUOUS", f"{_where_text(place).capitalize()} holds {len(found)} texts {words!r}; delete them in Layout "
                           "and place one again.", dispatch_state="not_started")
    return found[0] if found else None


def _where_text(place: str) -> str:
    return f"the user layer {place[5:]}" if place.startswith("user:") else f"the {place} silkscreen"


def position(text: Any) -> dict[str, Any]:
    """Where a text stands: its anchor in mm and its rotation."""
    return {"x_mm": round(float(text.GetPositionX(L.UNIT_MM)), 4), "y_mm": round(float(text.GetPositionY(L.UNIT_MM)), 4),
            "rotation_deg": round(float(text.Format.Orientation or 0.0) % 360.0, 4)}


def text_at(doc: Any, place: str, words: str) -> dict[str, Any]:
    """Where a board text stands: the reader ``read_keys`` uses."""
    text = find_text(doc, place, words)
    return {"exists": False} if text is None else {"exists": True, **position(text)}


def user_layers(doc: Any) -> list[str]:
    """The names of the board's user draft layers."""
    return [L.name_of(layer).strip() for layer in L.collection(doc, "UserLayers")]


def listed(doc: Any) -> list[dict[str, Any]]:
    """The board's own texts as pcb get lists them."""
    out = []
    for text, words, place in board_texts(doc)[:TEXTS_LISTED]:
        row: dict[str, Any] = {"text": words}
        row.update({"user_layer": place[5:]} if place.startswith("user:") else {"side": place})
        try:
            row["extents_mm"] = [round(v, 4) for v in L.extrema_mm(text)]
        except Exception:  # noqa: BLE001 - its box cannot be read
            pass
        out.append(row)
    return out


def corners(x: float, y: float, words: str, height: float, pen: float, rotation: float) -> list[tuple[float, float]]:
    """The text's box centred on (x, y), turned by its rotation: its four corners, a little outside
    the box Layout reports once it is placed."""
    half_w = len(words) * height * CHAR_WIDTH / 2 + pen / 2
    top, bottom = ABOVE * height + pen / 2, -(BELOW * height + pen / 2)
    turn = math.radians(rotation)
    c, s = math.cos(turn), math.sin(turn)
    return [(round(x + dx * c - dy * s, 4), round(y + dx * s + dy * c, 4)) for dx, dy in
            ((-half_w, bottom), (half_w, bottom), (half_w, top), (-half_w, top))]


def _box(points: list[tuple[float, float]]) -> Box:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _request(ctx: Context) -> dict[str, Any]:
    words = str(ctx.inputs.get("text") or "")
    if not words.strip() or any(ord(ch) < 32 for ch in words):
        raise AdapterError("E_VALIDATION", "--text is one line of words.", dispatch_state="not_started")
    side, code = _side(ctx)
    height = HEIGHT_MM if ctx.inputs.get("height_mm") is None else float(ctx.inputs["height_mm"])
    pen = PEN_MM if ctx.inputs.get("pen_width_mm") is None else float(ctx.inputs["pen_width_mm"])
    if not 0.1 <= height <= 20 or not 0.01 <= pen <= height / 2:
        raise AdapterError("E_VALIDATION", "--height is 0.1 to 20 mm, and --pen-width at least 0.01 mm and at most half the height.",
                           dispatch_state="not_started")
    rotation = float(ctx.inputs.get("rotation_deg") or 0.0) % 360.0
    if min(rotation % 90.0, 90.0 - rotation % 90.0) > 1e-6:
        raise AdapterError("E_VALIDATION", "--rotation is 0, 90, 180 or 270 degrees: the checks measure the text by the box Layout "
                           "gives it, square to the board.", dispatch_state="not_started")
    user_layer = str(ctx.inputs.get("user_layer") or "").strip() or None
    return {"text": words, "side": side, "code": code, "user_layer": user_layer, "place": place_of(side, user_layer),
            "x": float(base.need_input(ctx, "x_mm")),
            "y": float(base.need_input(ctx, "y_mm")), "height": height, "pen": pen, "rotation": round(rotation / 90.0) * 90.0 % 360.0}


def _problems(doc: Any, request: dict[str, Any], shape: list[tuple[float, float]]) -> tuple[list[str], list[str]]:
    """What stops the text where it would stand, and what only makes it harder to read."""
    model = O.board_model(doc, full=True)
    if model is None or not model["outline"]:
        raise AdapterError("E_UNSUPPORTED", O.UNIT_NOTE + " Placing a text also needs the board outline.", dispatch_state="not_started")
    box = _box(shape)
    side = request["side"]
    blocked: list[str] = []
    notes: list[str] = []
    outline = ST.polygon(model["outline"][0].get("path"), model["outline"][0].get("circle"))
    inside = all(OP.inside(p, outline) for p in shape) and not any(
        OP._crossing(shape[i - 1], shape[i], outline[k - 1], outline[k]) for i in range(4) for k in range(len(outline)))
    if not inside:
        blocked.append("it reaches past the board outline")
    for cut in model.get("cutouts") or []:
        record = cut.get("shape") or {}
        ring = ST.polygon(record.get("path"), record.get("circle")) if record else []
        if ring and OP._to_region(shape, [ring], 0.0)[0] == 0.0:
            x0, y0 = min(p[0] for p in ring), min(p[1] for p in ring)
            blocked.append(f"it crosses the cutout at {x0:.2f},{y0:.2f} mm")
    mine = {"text": request["text"], "kind": "text", "side": side, "refdes": NEW, "height": request["height"], "pen": request["pen"],
            "extents": list(box)}
    texts = [t for t in model["texts"] if not (t.get("refdes") is None and t.get("text") == request["text"] and t.get("side") == side)]
    if request["user_layer"]:  # the layer's other texts print on that side too
        for text, words, place in board_texts(doc):
            if place == request["place"] and words != request["text"]:
                try:
                    texts.append({"text": words, "kind": "text", "side": side, "refdes": None, "extents": list(L.extrema_mm(text))})
                except Exception:  # noqa: BLE001 - a text whose box cannot be read is left out
                    continue
    for finding in board_metrics.label_rules({**model, "texts": [*texts, mine]}):
        ours = finding.get("refdes") == NEW
        theirs = finding.get("rule") == "label_overlap" and finding.get("other") == request["text"] and not ours
        if not (ours or theirs):
            continue
        rule = finding.get("rule")
        if rule in BLOCKING:
            other = finding.get("text") if theirs else finding.get("other")
            blocked.append("it " + BLOCKING[rule].format(other=other))
        elif rule in ("label_small", "label_over_silk"):
            notes.append(str(finding.get("finding")))
    for kind, fab in (("soldermask", FAB_SOLDERMASK), ("coverlay", FAB_COVER_LAYER)):
        for _gfx, opening in _openings(doc, fab, request["code"]):
            if _overlaps(box, opening):
                blocked.append(f"it runs over the {kind} opening at {opening[0]:g},{opening[1]:g} to {opening[2]:g},{opening[3]:g} mm, "
                               "where the board house clips the ink off the bare copper")
    for zone in O.bend_boxes(doc):
        if _overlaps(box, zone):
            blocked.append(f"it lies in the bend zone {zone[0]:g},{zone[1]:g} to {zone[2]:g},{zone[3]:g} mm, where ink cracks as the board "
                           "bends")
    return list(dict.fromkeys(blocked)), notes


def _user_layer(doc: Any, name: str | None) -> Any | None:
    """The user draft layer of that name, or None when no name is given."""
    if not name:
        return None
    for layer in L.collection(doc, "UserLayers"):
        if L.name_of(layer).strip() == name:
            return layer
    names = ", ".join(user_layers(doc)) or "none"
    raise AdapterError("E_NOT_FOUND", f"The board has no user draft layer {name!r}; its user layers are: {names}.",
                       dispatch_state="not_started")


class SilkscreenTextCreate(L.ReadsBack):
    """Place a line of text on the board's silkscreen, centred on a point: ``PutFabricationLayerText``."""

    command = "silkscreen-text create"

    def preview(self, ctx: Context) -> dict[str, Any]:
        request = _request(ctx)
        path = ctx.document_path()
        shape = corners(request["x"], request["y"], request["text"], request["height"], request["pen"], request["rotation"])

        def look(app: Any, doc: Any) -> tuple[dict[str, Any] | None, list[str], list[str]]:
            _user_layer(doc, request["user_layer"])
            text = find_text(doc, request["place"], request["text"])
            now = position(text) if text is not None else None
            return (now, [], []) if now is not None else (None, *_problems(doc, request, shape))

        now, blocked, notes = L.in_layout(ctx, look, budget_s=L.READ_BUDGET_S, what="reading the board around the text")
        where = _where_text(request["place"])
        domain = text_key(request["place"], request["text"])
        target = [base.target(path, "text", domain, label=f"text {request['text']!r} on {where}")]
        box = _box(shape)
        if now is not None:
            same = abs(now["x_mm"] - request["x"]) < 0.001 and abs(now["y_mm"] - request["y"]) < 0.001
            if same:
                return base.preview(f"{where.capitalize()} holds {request['text']!r} at ({request['x']:g}, {request['y']:g}) mm already; "
                                    "nothing changes.", target, [])
            return base.preview(f"Place {request['text']!r} on {where}.", target, [], blocked_by=[
                f"{where} holds {request['text']!r} at ({now['x_mm']:g}, {now['y_mm']:g}) mm already; delete it with silkscreen-text "
                "delete first, or place other words"])
        printed = f", printed on the {request['side']}" if request["user_layer"] else ""
        action = (f"Place {request['text']!r} on {where}{printed}, {request['height']:g} mm high, centred on "
                  f"({request['x']:g}, {request['y']:g}) mm at {request['rotation']:g} degrees, covering about {box[0]:.2f},{box[1]:.2f} "
                  f"to {box[2]:.2f},{box[3]:.2f} mm.")
        if notes:
            action += " " + "; ".join(notes) + "."
        return base.preview(action + " The board is not saved.", target,
                            [base.change(base.key(path, domain), "exists", False, True)], blocked_by=blocked or None)

    def apply(self, ctx: Context) -> dict[str, Any]:
        request = _request(ctx)

        def work(app: Any, doc: Any) -> dict[str, Any]:
            layer = _user_layer(doc, request["user_layer"])
            existing = find_text(doc, request["place"], request["text"])
            base_result = {"text": request["text"], "side": request["side"]}
            if request["user_layer"]:
                base_result["user_layer"] = request["user_layer"]
            if existing is not None:
                return base.facts("not_applied", "unchanged", result={**base_result, "created": False, **position(existing)})
            try:
                if layer is not None:
                    text = doc.PutUserLayerText(request["text"], request["x"], request["y"], layer, request["height"], request["rotation"],
                                                request["pen"], FONT, 0, 0, 0, None, L.UNIT_MM, 0)
                else:
                    text = doc.PutFabricationLayerText(request["text"], request["x"], request["y"], FAB_SILKSCREEN, request["code"],
                                                       request["height"], request["rotation"], request["pen"], FONT, 0, 0, 0, None,
                                                       L.UNIT_MM, 0)
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "placing the text", com.call_state(exc)) from None
            result = {**base_result, "created": True, "height_mm": request["height"]}
            try:
                result.update(**position(text))
                x0, y0, x1, y1 = L.extrema_mm(text)
                if x0 <= x1 and y0 <= y1:  # inside the write's step Layout may give a new text no extents yet: an empty box
                    result["extents_mm"] = [round(v, 4) for v in (x0, y0, x1, y1)]
            except Exception:  # noqa: BLE001 - the read-back reads it again
                pass
            return base.facts("applied", "unchanged", result=result)

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="placing the text")


class SilkscreenTextDelete(L.ReadsBack):
    """Delete a text of the board's silkscreen, found by its words and side."""

    command = "silkscreen-text delete"

    def _found(self, ctx: Context, doc: Any) -> tuple[str, str, Any]:
        words = str(ctx.inputs.get("text") or "")
        side, _code = _side(ctx)
        user_layer = str(ctx.inputs.get("user_layer") or "").strip() or None
        _user_layer(doc, user_layer)
        place = place_of(side, user_layer)
        text = find_text(doc, place, words)
        if text is None:
            others = sorted({string for _t, string, where in board_texts(doc) if where == place})[:10]
            raise AdapterError("E_NOT_FOUND", f"{_where_text(place).capitalize()} holds no text {words!r} of the board's own"
                               + (f"; it holds {', '.join(repr(o) for o in others)}" if others else "") + ".",
                               dispatch_state="not_started")
        return words, place, text

    def preview(self, ctx: Context) -> dict[str, Any]:
        path = ctx.document_path()
        words, place, values = L.in_layout(ctx, lambda app, doc: (lambda f: (f[0], f[1], position(f[2])))(self._found(ctx, doc)),
                                           budget_s=L.READ_BUDGET_S, what="finding the text")
        domain = text_key(place, words)
        where = _where_text(place)
        return base.preview(f"Delete {words!r} from {where}, at ({values['x_mm']:g}, {values['y_mm']:g}) mm. The board is not saved.",
                            [base.target(path, "text", domain, label=f"text {words!r} on {where}")],
                            [base.change(base.key(path, domain), "exists", True, False)])

    def apply(self, ctx: Context) -> dict[str, Any]:
        def work(app: Any, doc: Any) -> dict[str, Any]:
            words, place, text = self._found(ctx, doc)
            values = position(text)
            try:
                text.Delete()
            except Exception as exc:  # noqa: BLE001 - classified
                raise L.com_error(exc, "deleting the text", com.call_state(exc)) from None
            result = {"text": words, "x_mm": values["x_mm"], "y_mm": values["y_mm"]}
            result.update({"user_layer": place[5:]} if place.startswith("user:") else {"side": place})
            return base.facts("applied", "unchanged", result=result)

        return L.in_layout(ctx, work, budget_s=L.WRITE_BUDGET_S, write=True, what="deleting the text")


HANDLERS: list[base.Handler] = [SilkscreenTextCreate(), SilkscreenTextDelete()]
