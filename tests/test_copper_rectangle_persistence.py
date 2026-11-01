"""Real execute-path fixtures for the observed draft-rectangle winding change.

These synthetic objects exercise native observation, Save/Close/Open and the
public witness. No CAD engineering data is distributed or modified by the tests.
"""

import copy
import json

import pytest
from test_copper_native import NativeGeometry, apply_preview, native_case

from xpedition_cli import board_copper as C
from xpedition_cli import native_com_adapter as A


def changed_reopen(tmp_path, monkeypatch, change):
    app, params, events, calls = native_case(tmp_path, monkeypatch)
    original = A._open_layout_document

    def reopen(application, board):
        result = original(application, board)
        doc = app.ActiveDocument
        shape = doc.shapes[0]
        points = copy.deepcopy(shape.Geometry.path[:-1])
        if change in {
            "reverse",
            "rotate_reverse",
            "generated",
            "protected_hole",
            "uid",
            "net",
            "layer",
        }:
            points = [points[0], *points[:0:-1]]
        if change in {"rotate", "rotate_reverse"}:
            points = points[2:] + points[:2]
        if change == "crossed":
            points = [points[0], points[2], points[1], points[3]]
        if change == "diamond_same_bounds":
            points = [[1, 5, 0], [5, 1, 0], [9, 5, 0], [5, 9, 0]]
        if change == "extra_vertex":
            points.insert(1, [5, 1, 0])
        if change == "arc":
            points[1][2] = 0.25
        if change == "translated":
            for point in points:
                point[0] += 0.00000001
        shape.Geometry = NativeGeometry([*points, points[0]])
        if change == "cutout":
            shape.Geometry.Cutouts.append(NativeGeometry(circle=(5, 5, 0.25)))
        if change == "uid":
            shape.UniqueId = 302
        if change == "net":
            shape.Net = doc.nets[1]
        if change == "layer":
            shape.Layer = 1
        if change == "generated":
            doc.generated[0]._oleobj_.values[52] += 0.00000001
        if change == "protected_hole":
            doc.geometry["model"]["holes"][0][2] += 0.00000001
        return result

    monkeypatch.setattr(A, "_open_layout_document", reopen)
    return app, params, events, calls


@pytest.mark.parametrize("change", ["reverse", "rotate", "rotate_reverse"])
def test_rectangle_vertex_order_preserves_persistence_and_raw_witness(
    tmp_path, monkeypatch, change
):
    _, params, events, calls = changed_reopen(tmp_path, monkeypatch, change)
    request, _ = apply_preview(params, events)
    result = C.execute(request, object(), A)
    assert result["saved"] and result["persistence_verified"]
    assert result["persistence_state"] == "saved"
    before, after = result["state_before_save"], result["state_after"]
    assert before != after
    assert before["geometry"] == after["geometry"]
    assert before["plane_state"]["generated"] == after["plane_state"]["generated"]
    comparison = result["reopen_comparison"]
    assert comparison["equal"] is False
    assert comparison["persistence_equivalent"] is True
    assert comparison["equivalence_basis"] == "target_rectangle_vertex_order"
    assert comparison["before_sha256"] == C.digest(before)
    assert comparison["after_sha256"] == C.digest(after)
    assert events.count("save") == events.count("close") == events.count("reopen") == 1
    assert sum(isinstance(e, tuple) and e[0] == "create" for e in events) == 1
    (tmp_path / "rectangle-witness.json").write_text(
        json.dumps({"result": result, "events": events, "calls": calls}, indent=2),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "change",
    [
        "crossed",
        "diamond_same_bounds",
        "extra_vertex",
        "arc",
        "translated",
        "cutout",
        "uid",
        "net",
        "layer",
        "generated",
        "protected_hole",
    ],
)
def test_reopen_changes_beyond_target_rectangle_order_remain_unknown(tmp_path, monkeypatch, change):
    _, params, events, _ = changed_reopen(tmp_path, monkeypatch, change)
    request, _ = apply_preview(params, events)
    with pytest.raises(A.AdapterError) as caught:
        C.execute(request, object(), A)
    result = caught.value.details["native_result"]
    assert result["change_status"] == "applied" and result["saved"] is True
    assert result["persistence_verified"] is False
    assert result["persistence_state"] == "unknown"
    assert result["state_before_save"] != result["state_after"]
    assert events.count("save") == events.count("close") == events.count("reopen") == 1
    assert sum(isinstance(e, tuple) and e[0] == "create" for e in events) == 1
