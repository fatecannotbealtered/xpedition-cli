"""Finite151.0 native getter correspondence; no CAD or rule verdict is synthesized."""

import copy

import pytest
from test_drc_proximity_records import PROXIMITY

from xpedition_cli import drc_engine as D
from xpedition_cli import library_read as R


def report():
    return D.violation_record(R.parse_records(PROXIMITY)[0])


def native():
    return {
        "kind": "Proximity",
        "x": 4.23,
        "y": 29.63,
        "object_collection_complete": True,
        "proximity_observation": {
            "source": "IMGCPCBHazard151.propertyget",
            "unit": "mm",
            "complete": True,
            "errors": [],
            "first_object_position_mm": [5.08, 30.48],
            "second_object_position_mm": [3.87, 26.84],
            "actual_clearance_mm": 0.0,
            "required_clearance_mm": 0.0,
        },
        "object_details": [
            {
                "type_name": "IMGCPCBPin",
                "uid": "108",
                "component_uid": "104",
                "layer": None,
                "is_smd": False,
                "refdes": "J1",
                "net": "A_IN",
                "position_mm": [5.08, 30.48],
                "position_source": "IMGCPCBPin151.PositionX/PositionY(EPcbUnitMM4)",
                "layer_material_observation": {
                    "source": "native_pin_owned_pad_material_mask151",
                    "unit": "mm",
                    "layer": 1,
                    "complete": True,
                    "unread": [],
                    "pads": [{"name": "RECT1.7X1.7", "layer": 1, "geometry": [{"filled": True}]}],
                },
            },
            {
                "type_name": "IMGCPCBPlacementOutline",
                "uid": "105",
                "component_uid": "104",
                "layer": 1,
                "refdes": "J1",
                "net": None,
                "position_mm": None,
                "placement_geometry_observation": {
                    "source": "IMGCPCBPlacementOutline151.Geometry.PointsArray",
                    "unit": "mm",
                    "complete": True,
                    "errors": [],
                    "position_getter_supported": False,
                    "cutout_count": 0,
                    "points_array_mm": [
                        [3.87, 6.29, 6.29, 3.87, 3.87],
                        [26.84, 26.84, 32.3, 32.3, 26.84],
                        [0] * 5,
                    ],
                    "rectangle_bounds_mm": [3.87, 26.84, 6.29, 32.3],
                    "rectangle_min_corner_mm": [3.87, 26.84],
                    "native_extrema_mm": [3.87, 26.84, 6.29, 32.3],
                },
            },
        ],
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_full_formal_getters_and_independent_objects_can_correspond(reverse):
    h = native()
    if reverse:
        observation = h["proximity_observation"]
        observation["first_object_position_mm"], observation["second_object_position_mm"] = (
            observation["second_object_position_mm"],
            observation["first_object_position_mm"],
        )
        h["object_details"].reverse()
    result = D.compare_hazards([report()], [h])
    assert result["valid"]
    witness = result["matches"][0]["proximity_correspondence"]
    assert witness["actual_clearance_mm"] == 0 and witness["pin_uid"] == "108"
    assert witness["CLEARANCE_semantics_verified"] is False
    assert "proximity_CLEARANCE_semantics" in result["not_verified"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_observation",
        "wrong_source",
        "unknown_units",
        "incomplete",
        "contradictory_errors",
        "wrong_first_position",
        "wrong_second_position",
        "negative_actual",
        "nan_required",
        "wrong_hazard_position",
        "wrong_kind",
        "wrong_net",
        "unknown_type",
        "unknown_layer",
        "wrong_object_position",
        "wrong_owner",
        "missing_uid",
        "duplicate_uid",
        "missing_object",
        "outline_fake_position",
        "outline_arc",
        "outline_cutout",
        "outline_wrong_corner",
        "pad_material_incomplete",
        "pad_material_wrong_layer",
        "pad_material_wrong_name",
        "collection_incomplete",
    ],
)
def test_partial_or_wrong_native_objects_do_not_match_the_pair(fault):
    h = native()
    o = h["proximity_observation"]
    if fault == "missing_observation":
        h.pop("proximity_observation")
    elif fault == "wrong_source":
        o["source"] = "report-derived"
    elif fault == "unknown_units":
        o["unit"] = None
    elif fault == "incomplete":
        o["complete"] = False
    elif fault == "contradictory_errors":
        o["errors"] = [{"field": "actual_clearance_mm"}]
    elif fault == "wrong_first_position":
        o["first_object_position_mm"][0] += 1
    elif fault == "wrong_second_position":
        o["second_object_position_mm"][1] += 1
    elif fault == "negative_actual":
        o["actual_clearance_mm"] = -1
    elif fault == "nan_required":
        o["required_clearance_mm"] = float("nan")
    elif fault == "wrong_hazard_position":
        h["x"] += 1
    elif fault == "wrong_kind":
        h["kind"] = "PartialNets"
    elif fault == "wrong_net":
        h["object_details"][0]["net"] = "SYNTHETIC_OTHER_NET"
    elif fault == "unknown_type":
        h["object_details"][1]["type_name"] = None
    elif fault == "unknown_layer":
        h["object_details"][1]["layer"] = None
    elif fault == "wrong_object_position":
        h["object_details"][0]["position_mm"][0] += 1
    elif fault == "wrong_owner":
        h["object_details"][1]["component_uid"] = "999"
    elif fault == "missing_uid":
        h["object_details"][0]["uid"] = None
    elif fault == "duplicate_uid":
        h["object_details"][1]["uid"] = "108"
    elif fault == "missing_object":
        h["object_details"].pop()
    elif fault == "outline_fake_position":
        h["object_details"][1]["position_mm"] = [3.87, 26.84]
    elif fault == "outline_arc":
        h["object_details"][1]["placement_geometry_observation"]["points_array_mm"][2][0] = 0.1
    elif fault == "outline_cutout":
        h["object_details"][1]["placement_geometry_observation"]["cutout_count"] = 1
    elif fault == "outline_wrong_corner":
        h["object_details"][1]["placement_geometry_observation"]["rectangle_min_corner_mm"] = [0, 0]
    elif fault == "pad_material_incomplete":
        h["object_details"][0]["layer_material_observation"]["complete"] = False
    elif fault == "pad_material_wrong_layer":
        h["object_details"][0]["layer_material_observation"]["layer"] = 2
    elif fault == "pad_material_wrong_name":
        h["object_details"][0]["layer_material_observation"]["pads"][0]["name"] = "wrong"
    elif fault == "collection_incomplete":
        h["object_collection_complete"] = False
    result = D.compare_hazards([report()], [h])
    assert not result["valid"] and not result["matches"]


def test_two_identical_records_need_two_independent_hazards():
    result = D.compare_hazards([report(), copy.deepcopy(report())], [native()])
    assert not result["valid"] and len(result["matches"]) == 1


def test_raw_report_clearance_is_not_silently_called_actual_or_required():
    r = report()
    r["native_clearance"] = [1234]
    result = D.compare_hazards([r], [native()])
    assert result["valid"]
    witness = result["matches"][0]["proximity_correspondence"]
    assert witness["native_CLEARANCE_values"] == [1234]
    assert witness["actual_clearance_mm"] == witness["required_clearance_mm"] == 0
    assert witness["CLEARANCE_semantics_verified"] is False
