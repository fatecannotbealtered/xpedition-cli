from __future__ import annotations

import pytest

from xpedition_cli.native_com_adapter import AdapterError, parse_points, plan_items


def test_parse_points_reads_pairs_from_text_and_lists() -> None:
    assert parse_points("34.2,36.0 36.5,36 ; 36.5,40.5") == [
        (34.2, 36.0),
        (36.5, 36.0),
        (36.5, 40.5),
    ]
    assert parse_points([[1, 2], (3.5, 4)]) == [(1.0, 2.0), (3.5, 4.0)]
    assert parse_points("10,20", 1) == [(10.0, 20.0)]
    with pytest.raises(AdapterError) as one:
        parse_points("10,20")
    assert one.value.code == "E_VALIDATION"
    with pytest.raises(AdapterError) as bad:
        parse_points("10;20")
    assert bad.value.code == "E_USAGE"
    with pytest.raises(AdapterError):
        parse_points(None)


def test_plan_items_normalises_traces_and_vias_in_order() -> None:
    items = plan_items(
        {
            "items": [
                {"kind": "trace", "net": "SCL", "layer": 4, "width": 0.3, "points": "1,1 2,2"},
                {"net": "GND", "at": [2, 2]},
            ],
            "traces": [{"net": "SDA", "points": [[0, 0], [1, 0]]}],
            "vias": [{"net": "SDA", "x": 1, "y": 0, "padstack": "026VIA"}],
        }
    )
    assert [item["kind"] for item in items] == ["trace", "via", "trace", "via"]
    assert items[0] == {
        "kind": "trace",
        "net": "SCL",
        "layer": 4,
        "width": 0.3,
        "points": [(1.0, 1.0), (2.0, 2.0)],
    }
    assert items[1] == {"kind": "via", "net": "GND", "at": (2.0, 2.0), "padstack": None}
    assert items[2]["layer"] == 1 and items[2]["width"] == 0.254
    assert items[3]["padstack"] == "026VIA" and items[3]["at"] == (1.0, 0.0)


def test_plan_items_refuses_bad_plans() -> None:
    with pytest.raises(AdapterError) as empty:
        plan_items({"items": []})
    assert empty.value.code == "E_VALIDATION"
    with pytest.raises(AdapterError) as no_net:
        plan_items({"traces": [{"points": "0,0 1,1"}]})
    assert no_net.value.code == "E_USAGE"
    with pytest.raises(AdapterError) as width:
        plan_items({"traces": [{"net": "A", "width": 0, "points": "0,0 1,1"}]})
    assert width.value.code == "E_VALIDATION"
    with pytest.raises(AdapterError) as kind:
        plan_items({"items": [{"kind": "arc", "net": "A"}]})
    assert kind.value.code == "E_USAGE"
    with pytest.raises(AdapterError):
        plan_items([])
