"""Explicit, identity-bound silk text translations; no auto placement or resizing.

Formal 151.0 FabricationLayerText.Move is the only definition mutation here.
Strict complete material capture and an independent text inventory precede saving.
Projection reminders are advisory and deliberately retain missing coverage.
"""

from __future__ import annotations

import copy
import hashlib
import math
from decimal import Decimal
from pathlib import Path

from .cell_refresh import _canonical, active, capture, digest, strict_physical_model
from .errors import CLIError

GUARD = "explicit_silk_translation_v2"
MOVE_KEYS = {
    "refdes",
    "text_uid",
    "text",
    "type",
    "text_type",
    "side",
    "side_name",
    "layer",
    "layer_name",
    "from",
    "to",
}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _string(value, field, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(field + " is not an observed string")
    return value


def _integer(value, field):
    if type(value) is not int:
        raise ValueError(field + " is not an observed integer")
    return value


def _text_property(text, name):
    try:
        return getattr(text, name)
    except Exception as error:
        raise ValueError("Text." + name + " getter failed: " + type(error).__name__) from error


def validate(value):
    if not isinstance(value, dict) or set(value) != {"unit", "moves"} or value["unit"] != "mm":
        raise CLIError("E_VALIDATION", "labels file requires exactly unit=mm and moves")
    rows = value["moves"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
        raise CLIError("E_VALIDATION", "labels moves must contain 1 to 32 explicit text targets")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != MOVE_KEYS:
            raise CLIError(
                "E_VALIDATION", "Each labels move requires explicit identity, layer, from and to"
            )
        if (
            any(
                not isinstance(row[k], str) or not row[k].strip()
                for k in ("refdes", "text_uid", "text", "side_name")
            )
            or row["text_uid"] == "0"
            or row["text_uid"] in seen
            or type(row["type"]) is not int
            or row["type"] != 2
            or type(row["text_type"]) is not int
            or row["text_type"] != 1
            or row["text"] != row["refdes"]
            or row["side"] not in ("top", "bottom")
            or row["side_name"] != {"top": "Top", "bottom": "Bottom"}.get(row["side"])
            or type(row["layer"]) is not int
            or not isinstance(row["layer_name"], str)
        ):
            raise CLIError(
                "E_VALIDATION", "labels target must be a unique actual silkscreen reference text"
            )
        for key in ("from", "to"):
            if (
                not isinstance(row[key], list)
                or len(row[key]) != 2
                or not all(_finite(v) for v in row[key])
            ):
                raise CLIError(
                    "E_VALIDATION", "labels positions must be two finite millimetre coordinates"
                )
        if row["from"] == row["to"]:
            raise CLIError("E_VALIDATION", "labels move must change its explicit position")
        if any(
            (Decimal(str(target)) - Decimal(str(source))) % Decimal("0.0001") != 0
            for source, target in zip(row["from"], row["to"], strict=True)
        ):
            raise CLIError(
                "E_UNSUPPORTED",
                "Silk translation deltas require the observed 0.0001 mm stroke/envelope grid",
            )
        seen.add(row["text_uid"])
    return copy.deepcopy(value)


def _collection(owner, name, a):
    collection = getattr(owner, name)
    if callable(collection) and not hasattr(collection, "Count"):
        collection = collection()
    count = collection.Count
    if type(count) is not int or not 0 <= count <= 4096:
        raise ValueError("Unbounded or incomplete text collection: " + name)
    rows = list(a._items(collection, strict=True))
    if len(rows) != count or collection.Count != count:
        raise ValueError("Text enumeration count changed: " + name)
    return rows


def _uid(value):
    if type(value) not in (int, str) or not str(value).strip() or str(value) == "0":
        raise ValueError("Missing native UID")
    return str(value)


def _format(fmt, a):
    result = {
        name: getattr(fmt, name)
        for name in (
            "Font",
            "Bold",
            "Italic",
            "Underline",
            "Mirrored",
            "HorizontalJust",
            "VerticalJust",
            "AspectRatio",
            "Attributes",
            "LineSpacing",
        )
    }
    result.update(
        height=fmt.GetHeight(a.UNIT_MM),
        pen=fmt.GetPenWidth(a.UNIT_MM),
        orientation=fmt.Orientation,
    )
    if any(
        not _finite(result[k])
        for k in ("height", "pen", "orientation", "AspectRatio", "LineSpacing")
    ):
        raise ValueError("Text format is incomplete")
    if result["height"] <= 0 or result["pen"] <= 0:
        raise ValueError("Text format dimensions are invalid")
    if any(type(v) not in (str, int, float, bool) for v in result.values()):
        raise ValueError("Text format is not fully observable")
    _string(result["Font"], "Format.Font")
    for name in ("Bold", "Italic", "Underline", "Mirrored"):
        if type(result[name]) is not bool:
            raise ValueError("Format." + name + " is not an observed boolean")
    for name in ("HorizontalJust", "VerticalJust", "Attributes"):
        _integer(result[name], "Format." + name)
    return result


def _text(text, board, a, *, canonical=True):
    document_path = _string(text.Document.FullName, "Text.Document.FullName")
    if (
        not Path(document_path).is_absolute()
        or Path(document_path).resolve() != board.resolve()
        or text.IsValid() is not True
    ):
        raise ValueError("Text document identity or validity differs")
    owner = text.Component
    owner_row = None
    if owner is not None:
        owner_row = {
            "uid": _uid(owner.UniqueId),
            "refdes": _string(owner.RefDes, "Component.RefDes"),
            "placed": owner.Placed,
        }
        if not owner_row["refdes"] or type(owner_row["placed"]) is not bool:
            raise ValueError("Text owner is incomplete")
    parent = text.Parent
    if parent is None:
        raise ValueError("Text Parent is unavailable")
    # Parent is generic. Read UID/refdes only after the formal object class
    # establishes Component; read FullName only on a proven Document class.
    # Never invent text.FullName or equate Python wrapper identities.
    parent_class = _integer(parent.ObjectClass, "Parent.ObjectClass")
    parent_row = {"object_class": parent_class}
    if owner is not None and parent_class == _integer(owner.ObjectClass, "Component.ObjectClass"):
        parent_row.update(uid=_uid(parent.UniqueId), refdes=_string(parent.RefDes, "Parent.RefDes"))
        if parent_row["uid"] != owner_row["uid"] or parent_row["refdes"] != owner_row["refdes"]:
            raise ValueError("Text Parent and Component ownership differ")
    elif parent_class == _integer(text.Document.ObjectClass, "Document.ObjectClass"):
        parent_row["document"] = str(Path(_string(parent.FullName, "Parent.FullName")).resolve())
        if Path(parent_row["document"]) != board.resolve():
            raise ValueError("Text Parent document differs")
    else:
        raise ValueError("Text Parent relation is unsupported")
    side = _text_property(text, "Side")
    if type(side) is not int or side not in (1, 512):
        raise ValueError("Text actual side is unknown")
    side_name = _string(_text_property(text, "SideName"), "Text.SideName")
    if side_name != {1: "Top", 512: "Bottom"}[side]:
        raise ValueError("Text.Side and SideName are contradictory or unsupported")
    fabrication_type = _integer(_text_property(text, "Type"), "Text.Type")
    layer, layer_name = _text_property(text, "Layer"), _text_property(text, "LayerName")
    _integer(layer, "Text.Layer")
    _string(layer_name, "Text.LayerName", empty=True)
    # Formal151.0 help14548/14549 defines Layer/LayerName as soldermask's
    # associated stackup layer, not a silk/assembly layer identifier. Preserve
    # successful raw reads (including observed-99/empty), without interpreting
    # an undocumented negative sentinel or substituting a conductor layer.
    no_stackup_association = fabrication_type in (1, 2)
    if not no_stackup_association and (layer < 0 or not layer_name.strip()):
        raise ValueError("Text stackup association applicability is not established")
    box = list(a._extrema_mm(text))
    if len(box) != 4 or not all(_finite(v) for v in box) or box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("Text native world envelope is unavailable")
    raw_strokes = text.StrokeText(a.UNIT_MM)
    if (
        not isinstance(raw_strokes, (tuple, list))
        or len(raw_strokes) > 1 + 3 * 16384
        or not all(_finite(v) for v in raw_strokes)
    ):
        raise ValueError("Text native stroke array is unavailable or exceeds point budget")
    pen, strokes = a.stroke_rows(raw_strokes)
    if not strokes or sum(len(stroke) for stroke in strokes) > 16384:
        raise ValueError("Text native vector strokes are unavailable or unbounded")
    reserved = text.Reserved
    if type(reserved) is not bool or not isinstance(text.ReservedBy, str):
        raise ValueError("Text reservation state is unavailable")
    row = {
        "uid": _uid(text.UniqueId),
        "text": _string(text.TextString, "Text.TextString", empty=True),
        "type": fabrication_type,
        "text_type": _integer(text.TextType, "Text.TextType"),
        "side": "top" if side == 1 else "bottom",
        "side_code": side,
        "side_name": side_name,
        "layer": layer,
        "layer_name": layer_name,
        "fabrication_identity": {
            "type": fabrication_type,
            "side": side,
            "side_name": side_name,
            "basis": "formal Type/Side/SideName; not a conductor-layer number",
        },
        "stackup_layer_association": {
            "applicability": "not_applicable_to_assembly_or_silkscreen"
            if no_stackup_association
            else "applicable_to_soldermask"
            if fabrication_type == 16
            else "not_established_for_other_fabrication_type",
            "formal_basis": "151.0 FabricationLayerText.Layer/LayerName help14548/14549",
            "raw_layer": layer,
            "raw_layer_name": layer_name,
            "negative_value_meaning": "unverified" if layer < 0 else "not_inferred",
            "used_for_silk_identity": False,
        },
        "owner": owner_row,
        "parent": parent_row,
        "position": [text.GetPositionX(a.UNIT_MM), text.GetPositionY(a.UNIT_MM)],
        "format": _format(text.Format, a),
        "extents": box,
        "strokes": strokes,
        "stroke_pen": pen,
        # Decimal strings preserve the exact returned double for prediction;
        # _canonical's eight-decimal model policy must not erase rounding ties.
        "native_geometry_witness": {
            "extents_mm": [repr(v) for v in box],
            "stroke_array_mm": [repr(v) for v in raw_strokes],
        },
        "fix_lock": _integer(text.FixLock, "Text.FixLock"),
        "display_condition": _integer(text.DisplayCondition, "Text.DisplayCondition"),
        "reserved": reserved,
        "reserved_by": text.ReservedBy,
    }
    if not all(_finite(v) for v in row["position"]):
        raise ValueError("Text position is unavailable")
    if not canonical:
        # Placement needs these additional identity/format getters at native
        # precision. Their meaning is retained, not inferred or normalized.
        row.update(
            native_name=_string(_text_property(text, "Name"), "Text.Name", empty=True),
            object_class=_integer(_text_property(text, "ObjectClass"), "Text.ObjectClass"),
            original_layer=_integer(_text_property(text, "OriginalLayer"), "Text.OriginalLayer"),
            is_dimension_text=text.IsDimensionText(),
            is_flex_text_valid=text.IsFlexTextValid(),
        )
        row["format"].update(valid=text.Format.IsValid(), is_vector_font=text.Format.IsVectorFont())
        if (
            any(
                type(value) is not bool
                for value in (
                    row["is_dimension_text"],
                    row["is_flex_text_valid"],
                    row["format"]["valid"],
                    row["format"]["is_vector_font"],
                )
            )
            or row["format"]["valid"] is not True
        ):
            raise ValueError("Additional text identity or format getters are unavailable")
    return _canonical(row) if canonical else row


def inventory(doc, board, a):
    """Public discovery with per-native-object failures, never a fake empty list."""
    rows, failures, seen = [], [], set()
    total_points = 0
    count = count_after = None
    try:
        collection = doc.FabricationLayerTexts
        if callable(collection) and not hasattr(collection, "Count"):
            collection = collection()
        count = collection.Count
        if type(count) is not int or not 0 <= count <= 4096:
            raise ValueError("count unavailable or object budget exceeded")
        for index in range(1, count + 1):
            try:
                row = _text(collection.Item(index), board, a)
                total_points += sum(len(stroke) for stroke in row["strokes"])
                if total_points > 262144:
                    raise ValueError("Total text geometry point budget exceeded")
                if row["uid"] in seen:
                    raise ValueError("duplicate native UID")
                seen.add(row["uid"])
                rows.append({"observation_index": index - 1, **row})
            except Exception as error:
                failures.append(
                    {
                        "observation_index": index - 1,
                        "stage": "Item/Document/Component/Parent/UID/Type/Side/SideName/"
                        "Layer/LayerName/position/Format/"
                        "Extrema/StrokeText",
                        "cause": type(error).__name__,
                        "reason": str(error)
                        if isinstance(error, ValueError)
                        else "native getter unavailable",
                    }
                )
        count_after = collection.Count
        if type(count_after) is not int or count_after != count:
            raise ValueError("Text collection count changed during inventory")
    except Exception as error:
        failures.append(
            {
                "stage": "FabricationLayerTexts.Count",
                "cause": type(error).__name__,
                "reason": str(error)
                if isinstance(error, ValueError)
                else "native collection unavailable",
            }
        )
    return {
        "unit": "mm",
        "source": "formal 151.0 Document.FabricationLayerTexts and per-object public getters",
        "complete": not failures,
        "enumerated_count": count,
        "count_after": count_after,
        "observed_count": len(rows),
        "object_limit": 4096,
        "stroke_point_limit_per_text": 16384,
        "total_stroke_point_limit": 262144,
        "texts": rows,
        "unread_objects": failures,
        "limitations": [
            "Native UID is required; observation_index is not a persistent identity",
            "Only text extrema/vector strokes; not a font/manufacturing or pin1 semantic check",
            "Silk/assembly identity is actual Type+Side+SideName. Layer/LayerName are raw "
            "soldermask stackup-association observations, not silk conductor layers. "
            "The meaning of observed negative values such as-99 remains unverified.",
        ],
    }


def _application_identity(app):
    """The same strict process/application pin, independent of document-open state."""
    from .native_identity import application_identity
    from .native_process import processes
    from .native_release_preflight import _application_pin, _path, process_generation

    observed = application_identity(app, "pcb")
    pid = observed.get("pid")
    if type(pid) is not int or pid <= 0:
        raise ValueError("Application PID is incomplete")
    generation = process_generation(pid)
    if (
        generation.get("alive") is not True
        or generation.get("pid") != pid
        or not isinstance(generation.get("birth_filetime_100ns"), str)
        or not generation["birth_filetime_100ns"].isdecimal()
        or int(generation["birth_filetime_100ns"]) <= 0
        or not isinstance(generation.get("executable"), str)
        or not Path(generation["executable"]).is_absolute()
    ):
        raise ValueError("Application process generation is unavailable")
    # Validate only this retained target's OS row. Other observed Layout sessions
    # are not implicitly authorized or treated as this board's application.
    matches = [row for row in processes("pcb") if row.get("pid") == pid]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("executable"), str)
        or _path(matches[0]["executable"]) != _path(generation["executable"])
    ):
        raise ValueError("Target process image/inventory mapping is incomplete")
    pin = _application_pin(
        observed,
        "pcb",
        [{**matches[0], "executable": _path(generation["executable"])}],
    )
    return {"application": pin, "generation": generation}


def _identity(app, board):
    observed = _application_identity(app)
    actual_path = _string(app.ActiveDocument.FullName, "ActiveDocument.FullName")
    if not Path(actual_path).is_absolute() or Path(actual_path).resolve() != board.resolve():
        raise ValueError("Application active board differs")
    return {**observed, "board": str(board.resolve())}


def observe(doc, board, a, *, references=False, _capture_stage=None):
    if _capture_stage is None:
        raw_model = a._board_model(doc, strict=True)
        geometry = capture(doc, a, strict=True)
        if _canonical(strict_physical_model(raw_model, a)) != geometry["model"]:
            raise ValueError("Board geometry changed during observation")
    else:
        if references:
            raise ValueError("Shared query material cannot supply mutation references")
        from .native_observation_stage import material

        geometry = material(_capture_stage, doc, a, kind="complete_capture")
        raw_model = None
    rows, refs = [], {}
    total_points = 0
    for text in _collection(doc, "FabricationLayerTexts", a):
        row = _text(text, board, a)
        total_points += sum(len(stroke) for stroke in row["strokes"])
        if total_points > 262144:
            raise ValueError("Total text geometry point budget exceeded")
        if row["uid"] in refs:
            raise ValueError("Duplicate text UID")
        rows.append(row)
        refs[row["uid"]] = text
    state = {"geometry": geometry, "texts": sorted(rows, key=lambda r: r["uid"])}
    # A second inventory reconciles component-owned text identities. No object
    # may silently disappear from the document collection or move another owner.
    component_ids, owned_text_ids = set(), set()
    for component in _collection(doc, "Components", a):
        component_uid = _uid(component.UniqueId)
        if component_uid in component_ids:
            raise ValueError("Duplicate component UID")
        component_ids.add(component_uid)
        for text in _collection(component, "FabricationLayerTexts", a):
            uid = _uid(text.UniqueId)
            if uid in owned_text_ids:
                raise ValueError("Duplicate component text UID")
            owned_text_ids.add(uid)
            match = [r for r in rows if r["uid"] == uid]
            if len(match) != 1 or match[0]["owner"] != {
                "uid": _uid(component.UniqueId),
                "refdes": _string(component.RefDes, "Component.RefDes"),
                "placed": component.Placed,
            }:
                raise ValueError("Component/document text ownership inventories differ")
    expected_owned = {r["uid"] for r in rows if r["owner"] is not None}
    if expected_owned != owned_text_ids or any(
        r["owner"]["uid"] not in component_ids for r in rows if r["owner"] is not None
    ):
        raise ValueError("Document/component reverse text ownership inventories differ")
    if _capture_stage is not None:
        material(_capture_stage, doc, a, kind="complete_capture")
    return (state, refs, raw_model) if references else state


def _translated_native_geometry(row, dx, dy):
    """Predict only this text from raw native doubles, then apply reader precision."""
    from .native_com_adapter import stroke_rows

    witness = row["native_geometry_witness"]
    extents = [
        float(v) + (dx if index % 2 == 0 else dy) for index, v in enumerate(witness["extents_mm"])
    ]
    values = [float(v) for v in witness["stroke_array_mm"]]
    for index in range(1, len(values), 3):
        values[index] += dx
        values[index + 1] += dy
    _, strokes = stroke_rows(values)
    return extents, strokes


def _witness_valid(row):
    try:
        extents, strokes = _translated_native_geometry(row, 0, 0)
        return _canonical(extents) == row["extents"] and strokes == row["strokes"]
    except (ValueError, KeyError, TypeError, IndexError):
        return False


def translate(row, dx, dy):
    row = copy.deepcopy(row)
    extents, strokes = _translated_native_geometry(row, dx, dy)
    row["position"] = [row["position"][0] + dx, row["position"][1] + dy]
    row["extents"], row["strokes"] = extents, strokes
    return _canonical(row)


def translated_geometry(geometry, moves, texts):
    """Only exact UID-bound text world translation; no blanket text exclusions."""
    expected = copy.deepcopy(geometry)
    for move in moves:
        rows = [r for r in expected["model"]["texts"] if r.get("uid") == move["text_uid"]]
        if len(rows) != 1:
            raise ValueError("Selected text UID missing from complete geometry")
        row = rows[0]
        dx, dy = [b - a for a, b in zip(move["from"], move["to"], strict=True)]
        sources = [r for r in texts if r["uid"] == move["text_uid"]]
        if len(sources) != 1 or not _witness_valid(sources[0]):
            raise ValueError("Selected text raw native geometry witness is unavailable")
        extents, strokes = _translated_native_geometry(sources[0], dx, dy)
        row["x"], row["y"] = row["x"] + dx, row["y"] + dy
        row["extents"], row["strokes"] = [round(v, 4) for v in extents], strokes
    return _canonical(expected)


def compare(bound, actual):
    expected = copy.deepcopy(bound["before"])
    expected["geometry"] = translated_geometry(
        expected["geometry"], bound["request"]["moves"], expected["texts"]
    )
    actual = copy.deepcopy(actual)
    for move in bound["request"]["moves"]:
        rows = [r for r in expected["texts"] if r["uid"] == move["text_uid"]]
        if len(rows) != 1:
            return {"valid": False, "reason": "bound text inventory incomplete"}
        dx, dy = [b - a for a, b in zip(move["from"], move["to"], strict=True)]
        expected["texts"][expected["texts"].index(rows[0])] = translate(rows[0], dx, dy)
        # Retain raw before/after witnesses in receipts. For selected text only,
        # raw doubles are prediction provenance, not an extra precision claim.
        # Only the selected inventory envelope uses the model's explicit four-
        # decimal mm observation precision. Raw extrema remain in the receipt,
        # and actual witness consistency is checked before this projection.
        # Position, strokes, ownership and full Format remain exact at their
        # existing precision; non-target fields and witnesses remain exact.
        for state in (expected, actual):
            selected = [r for r in state["texts"] if r["uid"] == move["text_uid"]]
            if len(selected) != 1 or (state is actual and not _witness_valid(selected[0])):
                return {"valid": False, "reason": "target raw witness inventory incomplete"}
            selected[0]["extents"] = [round(value, 4) for value in selected[0]["extents"]]
            selected[0].pop("native_geometry_witness", None)
    checks = [
        {
            "name": "only UID-bound text world translations; all other physical geometry preserved",
            "passed": actual.get("geometry") == expected["geometry"],
        },
        {
            "name": "all text identity, owner, layer, format and non-target positions preserved",
            "passed": actual.get("texts") == expected["texts"],
        },
    ]
    return {
        "valid": all(r["passed"] for r in checks),
        "checks": checks,
        "engineering_complete": False,
        "drc_executed": False,
    }


def bind(state, request, files, identity, a):
    for move in request["moves"]:
        matches = [r for r in state["texts"] if r["uid"] == move["text_uid"]]
        row = matches[0] if len(matches) == 1 else {}
        if (
            row.get("owner", {}).get("refdes") != move["refdes"]
            or row.get("owner", {}).get("placed") is not True
            or row.get("position") != move["from"]
            or any(
                row.get(k) != move[k]
                for k in ("text", "type", "text_type", "side", "side_name", "layer", "layer_name")
            )
            or row.get("fix_lock") != 0
            or row.get("reserved") is not False
        ):
            raise a.AdapterError(
                "E_CONFLICT",
                "Explicit silk target or frozen from-position differs",
                {"write_attempted": False, "target": move, "observed": row},
            )
    plan = {"request": request, "before": state, "files": files, "identity": identity}
    # Validate geometry membership before a confirmation token can be issued.
    translated_geometry(state["geometry"], request["moves"], state["texts"])
    plan["fingerprint"] = digest(plan)
    return plan


def _files(params, board):
    return [
        {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted({Path(str(params["project"])).resolve(), board.resolve()})
    ]


def persistence_evidence(result, bound, fresh_state):
    """Receipt-side consistency check independent of producer success flags."""
    moves = bound.get("request", {}).get("moves", [])
    calls = result.get("native_calls_returned")
    stages = result.get("stages")
    expected_stages = {
        "move": "returned",
        "readback": "complete",
        "protection": "passed",
        "save": "acknowledged",
        "close": "confirmed",
        "reopen": "verified",
    }
    expected_names = ["Move"] * len(moves) + ["Save", "Close", "Open"]
    call_sequence = (
        isinstance(calls, list)
        and len(calls) == len(expected_names)
        and all(
            isinstance(row, dict)
            and row.get("call") == name
            and type(row.get("sequence")) is int
            and row["sequence"] == index + 1
            and row.get("dispatched") is True
            and row.get("returned") is True
            for index, (row, name) in enumerate(zip(calls, expected_names, strict=True))
        )
    )
    identities = result.get("reopen_evidence") or {}
    if not isinstance(identities, dict):
        identities = {}
    call_content = False
    if call_sequence:
        call_content = (
            all(
                row.get("text_uid") == move["text_uid"]
                and row.get("target_mm") == move["to"]
                and row.get("unit") == 4
                and row.get("return_kind") == "none"
                for row, move in zip(calls[: len(moves)], moves, strict=True)
            )
            # Formal151.0 IMGCPCBDocument.Save/Close both return VT_BOOL.
            # Later matching geometry cannot substitute for their replies.
            and all(row.get("return_kind") == "true" for row in calls[-3:-1])
            and calls[-2].get("active_document_none") is True
            and (calls[-1].get("return_kind") == "document_and_prompts")
        )
    checks = [
        {
            "name": "one returned native Move per target then one Save Close Open in order",
            "passed": bool(moves and call_sequence and call_content),
        },
        {
            "name": "all lifecycle stages actually completed",
            "passed": isinstance(stages, dict)
            and all(stages.get(k) == v for k, v in expected_stages.items()),
        },
        {
            "name": "fresh authenticated reopened board and same strict process identity",
            "passed": bool(
                identities.get("identity") == bound.get("identity")
                and identities.get("board") == bound.get("identity", {}).get("board")
                and identities.get("authenticated") is True
                and identities.get("observed_after_open_sequence") == len(expected_names)
                and fresh_state
                and identities.get("state_sha256") == digest(fresh_state)
                and result.get("state_reopened") == fresh_state
            ),
        },
    ]
    return {"valid": all(row["passed"] for row in checks), "checks": checks}


def _call(result, a, name, function, **details):
    event = {
        "call": name,
        "sequence": len(result["native_calls_returned"]) + 1,
        "dispatched": True,
        "returned": False,
        **details,
    }
    result["native_calls_returned"].append(event)
    value = a._native_call("labels." + name, function)
    event["returned"] = True
    event["return_kind"] = (
        "none"
        if value is None
        else "true"
        if value is True
        else "false"
        if value is False
        else type(value).__name__
    )
    return value, event


def execute(params, client, a):
    try:
        if any(params.get(k) is not None for k in ("refdes", "gap", "height", "min_height", "pen")):
            raise a.AdapterError("E_USAGE", "Explicit silk moves cannot combine auto/size options")
        request = validate(params.get("label_moves"))
        _, board, app, doc = active(params, client, a, require_project=False, quiet=False)
        identity = _identity(app, board)
        state, refs, raw_model = observe(doc, board, a, references=True)
        files = _files(params, board)
        plan = bind(state, request, files, identity, a)
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_UNSUPPORTED"),
            "Explicit silk preflight is incomplete; no write started",
            {
                "stage": "labels.preflight",
                "write_attempted": False,
                "cause": type(error).__name__,
                **(getattr(error, "details", {}) or {}),
            },
        ) from error
    from .layout_reminders import report

    projected = copy.deepcopy(raw_model)
    projected["texts"] = translated_geometry(
        {"model": projected}, request["moves"], state["texts"]
    )["model"]["texts"]
    result = {
        "pcb": str(board),
        "state_guard": GUARD,
        "state_digest": plan["fingerprint"],
        "label_moves": plan,
        "labels": [{**m, "x": m["to"][0], "y": m["to"][1]} for m in request["moves"]],
        "selected_refdes": [m["refdes"] for m in request["moves"]],
        "moved": 0,
        "resized": 0,
        "unplaced": [],
        "failures": [],
        "read_back": [],
        "prompts": [],
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "write_attempted": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "stages": dict.fromkeys(
            ("move", "readback", "protection", "save", "close", "reopen"), "not_started"
        ),
        "native_calls_returned": [],
        "reminders_before": report(raw_model),
        "reminders_projected": report(projected),
        "not_checked": [
            "Native DRC and manufacturing",
            "Exact font collisions beyond observed extrema/vector strokes",
            "Pin1 marker semantic identity; graphics are protected exactly",
            "Mask-opening relation where native pad envelopes are unsupported",
        ],
        "verification": {"valid": False, "persistence_verified": False},
        "_untrusted": ["pcb", "label_moves", "labels", "reminders_before", "reminders_projected"],
    }
    if not params.get("apply"):
        return result
    if params.get("state_guard") != GUARD or params.get("state_digest") != plan["fingerprint"]:
        raise a.AdapterError(
            "E_CONFLICT",
            "Silk input, identity or protected state drifted",
            {"write_attempted": False},
        )
    # Fresh same-reference authentication and physical proof immediately before
    # dispatch; no Open, activation, auto-placement, text format or GUI commands.
    if (
        _identity(app, board) != identity
        or observe(a._licensed_document(app), board, a) != state
        or _files(params, board) != files
    ):
        raise a.AdapterError(
            "E_CONFLICT",
            "Silk identity/state changed immediately before dispatch",
            {"write_attempted": False},
        )
    stages = result["stages"]
    try:
        stages["move"] = "started"
        for move in request["moves"]:
            result.update(
                write_attempted=True, change_status="unknown", persistence_state="unknown"
            )
            moved, _ = _call(
                result,
                a,
                "Move",
                lambda move=move: refs[move["text_uid"]].Move(*move["to"], a.UNIT_MM),
                text_uid=move["text_uid"],
                target_mm=move["to"],
                unit=a.UNIT_MM,
            )
            if moved is not None:
                raise a.AdapterError(
                    "E_VERIFY_INCOMPLETE", "Formal void silk Move returned an unexpected value"
                )
            result["moved"] += 1
        stages["move"], stages["readback"] = "returned", "started"
        current = observe(a._licensed_document(app), board, a)
        result.update(
            applied=True, change_status="partial", read_back=current["texts"], state_after=current
        )
        stages["readback"], stages["protection"] = "complete", "started"
        proof = compare(plan, current)
        result["verification"] = proof
        stages["protection"] = "passed" if proof["valid"] else "failed"
        if not proof["valid"] or _identity(app, board) != identity:
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE", "Silk readback/protection failed; Save/Close/Open stopped"
            )
        result["change_status"] = "applied"
        stages["save"] = "started"
        saved, _ = _call(result, a, "Save", doc.Save)
        # Formal151.0 Save (DISPID5/help11268) and Close (6/help11115)
        # declare VT_BOOL, unlike the void FabricationLayerText.Move.
        if saved is not True:
            raise a.AdapterError(
                "E_NATIVE_FAILURE" if saved is False else "E_VERIFY_INCOMPLETE",
                "Silk Save did not return declared BOOL=True; Close/Open stopped",
            )
        result["saved"] = True
        stages["save"], stages["close"] = "acknowledged", "started"
        closed, close_event = _call(result, a, "Close", lambda: doc.Close(True))
        close_event["active_document_none"] = app.ActiveDocument is None
        if closed is not True or not close_event["active_document_none"]:
            raise a.AdapterError(
                "E_VERIFY_INCOMPLETE", "Silk normal document closure is unconfirmed"
            )
        stages["close"], stages["reopen"] = "confirmed", "started"
        opened, open_event = _call(result, a, "Open", lambda: a._open_layout_document(app, board))
        if not isinstance(opened, tuple) or len(opened) != 2 or opened[0] is None:
            raise a.AdapterError("E_VERIFY_INCOMPLETE", "Silk normal Open returned incomplete data")
        open_event["return_kind"] = "document_and_prompts"
        reopened = a._licensed_document(app)
        reopened_identity = _identity(app, board)
        if (
            Path(str(reopened.FullName)).resolve() != board.resolve()
            or reopened_identity != identity
        ):
            raise a.AdapterError("E_CONFLICT", "Silk reopened application/document differs")
        after = observe(reopened, board, a)
        result["reopen_evidence"] = {
            "identity": reopened_identity,
            "board": str(board.resolve()),
            "authenticated": True,
            "observed_after_open_sequence": open_event["sequence"],
            "state_sha256": digest(after),
        }
        result["state_reopened"], result["geometry_after"] = after, after["geometry"]
        proof = compare(plan, after)
        result["persistence_verified"] = after == current and proof["valid"]
        stages["reopen"] = "verified" if result["persistence_verified"] else "mismatch"
        result["verification"] = {
            **proof,
            "valid": result["persistence_verified"],
            "persistence_verified": result["persistence_verified"],
        }
        if not result["persistence_verified"]:
            raise a.AdapterError("E_VERIFY_INCOMPLETE", "Silk saved/reopened state differs")
        if not persistence_evidence(result, plan, after)["valid"]:
            raise a.AdapterError("E_VERIFY_INCOMPLETE", "Silk lifecycle evidence is incomplete")
        result["persistence_state"] = "saved"
        return result
    except Exception as error:
        result["post_write_error"] = {
            "code": getattr(error, "code", "E_NATIVE_FAILURE"),
            "cause": type(error).__name__,
        }
        raise a.AdapterError(
            getattr(error, "code", "E_NATIVE_FAILURE"),
            "Silk lifecycle incomplete; retain original receipt and do not replay",
            {"write_attempted": result["write_attempted"], "native_result": result},
        ) from error
