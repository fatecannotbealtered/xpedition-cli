"""Synthetic native execution: selected via deletion cannot save damaged protection."""

import copy
import json

import pytest
from test_unroute_native import Collection, native_case
from test_unroute_public import remove

from xpedition_cli import native_com_adapter as A


def via_native_case(tmp_path, monkeypatch, fault=None):
    app, params, events = native_case(tmp_path, monkeypatch)
    state = app.ActiveDocument.state
    model = state["geometry"]["model"]
    for uid, net, y in [("201", "A", 0), ("202", "B", 3)]:
        via = {
            "kind": "via",
            "uid": uid,
            "net": net,
            "padstack": "ROUND",
            "center_mm": [2.5, y],
            "drill_mm": 0.3,
            "shape_type": 1,
            "plated": True,
            "span": [1, 2],
            "pad_geometry": [{"layer": layer, "diameter_mm": 0.6} for layer in (1, 2)],
        }
        model["vias"].append(via)
        model["holes"].append([2.5, y, 0.3])
        model["via_holes"].append({"uid": uid, "net": net, "geometry": [2.5, y, 0.3]})
        state["routing_objects"].append(
            {"kind": "via", "uid": uid, "net": net, "geometry": copy.deepcopy(via)}
        )
    # The mechanical hole coincides with the selected via's hole. Deleting one
    # exact native-owned observation must leave this other observation intact.
    model["holes"].append([2.5, 0, 0.3])

    def delete_observed_via(doc, uid):
        current = doc.state["geometry"]["model"]
        owned = next(h for h in current["via_holes"] if h["uid"] == uid)
        current["holes"].remove(owned["geometry"])
        current["via_holes"].remove(owned)
        current["vias"] = [v for v in current["vias"] if v["uid"] != uid]
        doc.state["routing_objects"] = [
            row for row in doc.state["routing_objects"] if (row["kind"], row["uid"]) != ("via", uid)
        ]

    class Via:
        def __init__(self, row, doc):
            from types import SimpleNamespace

            self.doc = doc
            self.UniqueId = row["uid"]
            self.Net = SimpleNamespace(
                Name=row["net"], UniqueId="11" if row["net"] == "A" else "12"
            )

        def Delete(self):
            events.append("delete:" + self.UniqueId)
            delete_observed_via(self.doc, self.UniqueId)
            if not any(row["net"] == self.Net.Name for row in self.doc.state["routing_objects"]):
                remove(self.doc.state, {self.Net.Name})
            current = self.doc.state["geometry"]["model"]
            if fault == "foreign_via":
                delete_observed_via(self.doc, "202")
            elif fault == "mechanical_hole":
                current["holes"].remove([2.5, 0, 0.3])
            elif fault == "component_hole":
                current["holes"].remove([0, 0, 1])

    monkeypatch.setattr(
        type(app.ActiveDocument),
        "Vias",
        property(
            lambda doc: Collection(
                Via(row, doc) for row in doc.state["routing_objects"] if row["kind"] == "via"
            )
        ),
    )
    return app, params, events


@pytest.mark.parametrize("fault", [None, "foreign_via", "mechanical_hole", "component_hole"])
def test_selected_via_execute_save_guard(tmp_path, monkeypatch, fault):
    """Invoke the real native handler, not compare() alone, with owned via refs."""
    app, params, events = via_native_case(tmp_path, monkeypatch, fault)
    preview = A._unroute_nets(params, object())
    assert not events
    assert preview["to_delete"] == {"traces": 1, "vias": 1}
    selected = preview["unroute"]["selected_objects"]
    assert [(row["kind"], row["uid"]) for row in selected] == [("trace", "91"), ("via", "201")]
    options = {**params, "apply": True, "expected": preview["unroute"]["fingerprint"]}
    if fault:
        with pytest.raises(A.AdapterError) as caught:
            A._unroute_nets(options, object())
        assert caught.value.code == "E_PARTIAL"
        result = caught.value.details["native_result"]
    else:
        result = A._unroute_nets(options, object())

    assert events[:2] == ["delete:91", "delete:201"]
    lifecycle = {step: events.count(step) for step in ("save", "close", "reopen")}
    assert lifecycle == dict.fromkeys(lifecycle, 0 if fault else 1)
    assert result["delete_attempts"] == [
        {"uid": "91", "kind": "trace", "net": "A", "returned": True},
        {"uid": "201", "kind": "via", "net": "A", "returned": True},
    ]
    assert result["protection"]["protection_valid"] is (fault is None)
    assert result["persistence_state"] == ("unknown" if fault else "saved")
    assert result["saved"] is (fault is None)
    assert result["persistence_verified"] is (fault is None)
    assert result["change_status"] == ("partial" if fault else "applied")
    assert app.ActiveDocument is not None
    current = app.ActiveDocument.state
    assert not [row for row in current["routing_objects"] if row["net"] == "A"]
    removed = {(row["kind"], row["uid"]) for row in result["removed_objects"]}
    assert removed == {("trace", "91"), ("via", "201")} | (
        {("via", "202")} if fault == "foreign_via" else set()
    )
    if fault:
        assert events == ["delete:91", "delete:201"]
        assert all(result["stages"][step] == "not_started" for step in lifecycle)
        assert result["post_write_error"]["failed_checks"]
    else:
        before = preview["unroute"]["before"]["geometry"]
        after = current["geometry"]
        assert before["components"] == after["components"]
        assert after["model"]["holes"] == [[0, 0, 1], [2.5, 3, 0.3], [2.5, 0, 0.3]]
        assert after["model"]["vias"] == [before["model"]["vias"][1]]
        assert after["model"]["traces"] == [before["model"]["traces"][1]]
        assert current == result["state_after"]
    (tmp_path / "execution-evidence.json").write_text(
        json.dumps(
            {
                "source": "synthetic native objects; real unroute execute handler",
                "fault": fault,
                "events": events,
                "lifecycle_call_counts": lifecycle,
                "result": result,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
