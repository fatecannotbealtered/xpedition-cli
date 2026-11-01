"""Authorize only holes independently linked to a selected native via UID."""

import copy

import pytest

from xpedition_cli.task_readback import verify_task


def state():
    return {
        "components": [],
        "model": {
            "holes": [[20, 15, 1], [22.54, 15, 1], [45, 45, 2]],
            "via_holes": [],
            "vias": [],
            "traces": [],
            "unrouted": [],
            "connectivity": {
                "complete": True,
                "nets": [
                    {"net": n, "uid": "n" + n, "routed": True, "opens": 0} for n in ["A", "B"]
                ],
            },
        },
    }


def via(g, uid="7", net="A", center=(30, 30)):
    hole = [*center, 0.3]
    g["model"]["holes"].append(hole.copy())
    g["model"]["via_holes"].append({"uid": uid, "net": net, "geometry": hole.copy()})
    g["model"]["vias"].append(
        {
            "uid": uid,
            "net": net,
            "center_mm": list(center),
            "drill_mm": 0.3,
            "shape_type": 1,
            "plated": True,
            "padstack": "VIA",
            "span": [1, 4],
            "kind": "via",
            "circle": [*center, 0.3],
        }
    )


def verify(old, new):
    def snap(g):
        return {"extensions": {"facts": {"cell_refresh": {"geometry": g}}}}

    return verify_task(
        "pcb routing auto",
        {"nets": "A", "layers": "1,4"},
        {"geometry_after": new, "saved": True, "persistence_verified": True},
        snap(old),
        snap(new),
        {},
    )


@pytest.mark.parametrize("change", ["add", "remove", "none"])
def test_selected_native_via_add_remove_and_unchanged_are_allowed(change):
    old, new = state(), state()
    if change != "add":
        via(old)
    if change != "remove":
        via(new)
    assert verify(old, new)["valid"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "drill",
        "position",
        "unowned",
        "foreign_net",
        "wrong_uid",
        "duplicate_uid",
        "j1",
        "mechanical",
        "unassociated_hole",
        "padstack_missing",
        "slot",
        "unplated",
    ],
)
def test_holes_cannot_be_exempted_by_coordinate_or_affected_net_alone(fault):
    old, new = state(), state()
    via(new)
    if fault == "drill":
        new["model"]["holes"][-1][2] = 0.4
        new["model"]["via_holes"][-1]["geometry"][2] = 0.4
    if fault == "position":
        new["model"]["holes"][-1][0] += 0.1
        new["model"]["via_holes"][-1]["geometry"][0] += 0.1
    if fault == "unowned":
        new["model"]["via_holes"] = []
    if fault == "foreign_net":
        new["model"]["vias"][-1]["net"] = "B"
        new["model"]["via_holes"][-1]["net"] = "B"
    if fault == "wrong_uid":
        new["model"]["via_holes"][-1]["uid"] = "8"
    if fault == "duplicate_uid":
        via(new)
    if fault == "j1":
        new["model"]["holes"][0][2] = 1.1
    if fault == "mechanical":
        new["model"]["holes"][2][0] = 44
    if fault == "unassociated_hole":
        new["model"]["holes"].append([30, 30, 0.3])
    if fault == "padstack_missing":
        new["model"]["vias"][-1]["padstack"] = None
    if fault == "slot":
        new["model"]["vias"][-1]["shape_type"] = 10
    if fault == "unplated":
        new["model"]["vias"][-1]["plated"] = False
    assert verify(old, new)["valid"] is False


def test_existing_out_of_scope_via_remains_protected():
    old = state()
    via(old, net="B")
    new = copy.deepcopy(old)
    new["model"]["holes"][-1][2] += 0.1
    assert verify(old, new)["valid"] is False
