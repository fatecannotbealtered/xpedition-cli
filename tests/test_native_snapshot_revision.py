import copy

import pytest

from xpedition_cli.driver import DriverError, _stable
from xpedition_cli.native_snapshot_revision import canonical


def native():
    components = [
        {
            "uid": "uid-b",
            "refdes": "X17",
            "pins": [{"number": "2", "net": "SIG"}, {"number": "0", "net": "0"}],
            "x": 2,
        },
        {"uid": "uid-a", "refdes": "Q94", "pins": [{"number": "1", "net": "SIG"}], "x": 3},
    ]
    return {
        "revision": "native",
        "metadata": {"backend": "native_xpedition", "source": "Viewdraw.Application"},
        "components": components,
        "unnamed_components": [{"uid": "mark", "refdes": "", "pins": []}],
        "nets": [{"name": "SIG", "sheets": [2, 1]}, {"name": "0", "sheets": [1]}],
        "connections": [{"net": "SIG", "pins": ["X17.2", "Q94.1"]}],
        "sheets": [{"number": 2, "name": "Handset.2"}, {"number": 1, "name": "Handset.1"}],
        "ordered_path": [[0, 0], [1, 0], [1, 1]],
    }


def test_reopen_native_enumeration_permutation_is_revision_neutral():
    before = native()
    after = copy.deepcopy(before)
    for name in ("components", "unnamed_components", "nets", "sheets", "connections"):
        after[name].reverse()
    for row in after["components"]:
        row["pins"].reverse()
    after["nets"][1]["sheets"].reverse()
    after["connections"][0]["pins"].reverse()
    assert _stable(after) == _stable(before)
    assert before == native()  # no alteration of the original evidence


@pytest.mark.parametrize("change", ["uid", "refdes", "position", "net", "path", "foreign_field"])
def test_real_non_target_changes_remain_revision_changes(change):
    before = native()
    after = copy.deepcopy(before)
    if change == "uid":
        after["components"][1]["uid"] = "replacement"
    elif change == "refdes":
        after["components"][1]["refdes"] = "Q95"
    elif change == "position":
        after["components"][1]["x"] += 0.1
    elif change == "net":
        after["components"][1]["pins"][0]["net"] = "OTHER"
    elif change == "path":
        after["ordered_path"].reverse()
    else:
        after["unnamed_components"][0]["format"] = {"font": "changed"}
    assert _stable(before) != _stable(after)


@pytest.mark.parametrize(
    "bad",
    [
        "uid_missing",
        "uid_zero",
        "uid_duplicate",
        "refdes_duplicate",
        "refdes_missing",
        "pin_duplicate",
        "pin_missing",
        "unnamed_collision",
        "unnamed_reference",
        "net_duplicate",
        "sheet_duplicate",
        "connection_duplicate",
        "member_duplicate",
    ],
)
def test_ambiguous_identities_fail_before_dispatch(bad):
    value = native()
    first, second = value["components"]
    if bad == "uid_missing":
        del first["uid"]
    elif bad == "uid_zero":
        first["uid"] = 0
    elif bad == "uid_duplicate":
        second["uid"] = first["uid"]
    elif bad == "refdes_duplicate":
        second["refdes"] = first["refdes"]
    elif bad == "refdes_missing":
        del first["refdes"]
    elif bad == "pin_duplicate":
        first["pins"].append(copy.deepcopy(first["pins"][0]))
    elif bad == "pin_missing":
        del first["pins"][0]["number"]
    elif bad == "unnamed_collision":
        value["unnamed_components"][0]["uid"] = first["uid"]
    elif bad == "unnamed_reference":
        value["unnamed_components"][0]["refdes"] = first["refdes"]
    elif bad == "net_duplicate":
        value["nets"].append(copy.deepcopy(value["nets"][0]))
    elif bad == "sheet_duplicate":
        value["sheets"].append({"number": 1, "name": "Other.1"})
    elif bad == "connection_duplicate":
        value["connections"].append(copy.deepcopy(value["connections"][0]))
    else:
        value["connections"][0]["pins"].append("Q94.1")
    with pytest.raises(DriverError) as failure:
        _stable(value)
    assert failure.value.code == "E_NATIVE_FAILURE"
    assert failure.value.details["cause_code"] == "E_VERIFY_INCOMPLETE"
    assert failure.value.details["native_query_only"] is True
    assert failure.value.details["write_attempted"] is False


def test_layout_snapshot_census_keeps_duplicate_basic_copper_records():
    value = native()
    value["metadata"]["source"] = "MGCPCB.ExpeditionPCBApplication"
    value["pcb"] = {
        "components": copy.deepcopy(value["components"]),
        "nets": [{"name": "SIG"}],
        "tracks": [{"net": "SIG", "layer": 1}] * 2,
        "vias": [],
        "footprints": ["B", "A"],
    }
    changed = copy.deepcopy(value)
    changed["pcb"]["components"].reverse()
    changed["pcb"]["footprints"].reverse()
    assert _stable(value) == _stable(changed)
    changed["pcb"]["tracks"].pop()
    assert _stable(value) != _stable(changed)
    assert len(canonical(value)["pcb"]["tracks"]) == 2


def test_unrecognized_diagnostic_does_not_gain_inventory_semantics():
    value = {"components": [{"uid": None}], "points": [[1, 0], [0, 0]]}
    assert _stable(value) == value


def attribute_native():
    value = native()
    component = value["components"][0]
    component["attribute_display"] = [
        {
            "name": "Value",
            "value": "10k",
            "display": {
                "x": 337,
                "y": 344,
                "font": "native",
                "size": 10,
                "origin": 3,
                "visible": True,
            },
        },
        {
            "name": "Part Number",
            "value": "RealLibraryPN",
            "display": {
                "x": 337,
                "y": 322,
                "font": "native",
                "size": 6,
                "origin": 1,
                "visible": False,
            },
        },
    ]
    component["attribute_display_coverage"] = {
        "attribute_count": 2,
        "enumeration_complete": True,
        "errors": [],
        "truncated": False,
        "exact_font_envelope_observed": False,
    }
    return value


def test_complete_attribute_display_permutation_keeps_full_rows_revision_neutral():
    before = attribute_native()
    after = copy.deepcopy(before)
    after["components"][0]["attribute_display"].reverse()
    assert _stable(before) == _stable(after)
    assert before == attribute_native()
    assert canonical(before)["components"][1]["attribute_display"] == sorted(
        before["components"][0]["attribute_display"], key=lambda row: row["name"]
    )


@pytest.mark.parametrize("field", ["value", "x", "y", "font", "size", "origin", "visible"])
def test_real_attribute_value_or_display_changes_still_drift(field):
    before = attribute_native()
    after = copy.deepcopy(before)
    row = after["components"][0]["attribute_display"][0]
    if field == "value":
        row[field] = "22k"
    elif field in {"x", "y", "size", "origin"}:
        row["display"][field] += 1
    elif field == "visible":
        row["display"][field] = False
    else:
        row["display"][field] = "another-native-font"
    after["components"][0]["attribute_display"].reverse()
    assert _stable(before) != _stable(after)


@pytest.mark.parametrize("fault", ["duplicate", "case_ambiguous", "missing"])
def test_ambiguous_attribute_display_identity_rejects_instead_of_choosing_order(fault):
    value = attribute_native()
    rows = value["components"][0]["attribute_display"]
    if fault == "missing":
        del rows[0]["name"]
    else:
        rows[1]["name"] = rows[0]["name"].lower() if fault == "case_ambiguous" else rows[0]["name"]
    with pytest.raises(DriverError):
        _stable(value)


def test_incomplete_attribute_display_observation_does_not_gain_order_neutrality():
    before = attribute_native()
    before["components"][0]["attribute_display_coverage"]["enumeration_complete"] = False
    after = copy.deepcopy(before)
    after["components"][0]["attribute_display"].reverse()
    assert _stable(before) != _stable(after)
    assert (
        canonical(before)["components"][1]["attribute_display"]
        == before["components"][0]["attribute_display"]
    )


def unnamed_singletons():
    value = native()
    value["unnamed_components"] = [
        {
            "uid": uid,
            "refdes": "",
            "pins": [{"number": "", "net": net, "x": x, "y": 7, "no_connect": False}],
            "pin_observation_coverage": {"complete": True, "errors": []},
            "identity_observation_coverage": {"complete": True, "errors": []},
            "format": {"font": "unchanged"},
        }
        for uid, net, x in (("ground-owner-b", "ANALOG_RETURN", 5), ("ground-owner-a", "0", 6))
    ]
    return value


def test_complete_unnamed_singletons_keep_blank_numbers_across_reopen_permutation():
    before = unnamed_singletons()
    original = copy.deepcopy(before)
    after = copy.deepcopy(before)
    for name in ("components", "unnamed_components", "nets", "sheets", "connections"):
        after[name].reverse()
    after["components"][1]["pins"].reverse()
    result = canonical(before)
    assert _stable(before) == _stable(after)
    assert {row["uid"]: row for row in result["unnamed_components"]} == {
        row["uid"]: row for row in original["unnamed_components"]
    }
    assert all(row["pins"][0]["number"] == "" for row in result["unnamed_components"])
    assert before == original


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_retained_105_singleton_ground_symbols_are_not_rewritten_or_rejected():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.parametrize(
    "fault",
    [
        "named_blank",
        "missing_number",
        "none_number",
        "whitespace_number",
        "missing_owner_uid",
        "zero_owner_uid",
        "duplicate_owner_uid",
        "two_blank_pins",
        "mixed_pins",
        "missing_pin_coverage",
        "incomplete_pin_coverage",
        "pin_getter_error",
        "missing_identity_coverage",
        "incomplete_identity_coverage",
        "identity_getter_error",
        "layout_source",
    ],
)
def test_blank_number_exception_requires_complete_unambiguous_designer_singleton(fault):
    value = unnamed_singletons()
    owner = value["unnamed_components"][0]
    pin = owner["pins"][0]
    if fault == "named_blank":
        value["components"][0]["pins"][0]["number"] = ""
    elif fault == "missing_number":
        del pin["number"]
    elif fault in {"none_number", "whitespace_number"}:
        pin["number"] = None if fault == "none_number" else " "
    elif fault == "missing_owner_uid":
        del owner["uid"]
    elif fault == "zero_owner_uid":
        owner["uid"] = 0
    elif fault == "duplicate_owner_uid":
        value["unnamed_components"][1]["uid"] = owner["uid"]
    elif fault in {"two_blank_pins", "mixed_pins"}:
        owner["pins"].append({**pin, "number": "" if fault == "two_blank_pins" else "1"})
    elif fault == "missing_pin_coverage":
        del owner["pin_observation_coverage"]
    elif fault == "missing_identity_coverage":
        del owner["identity_observation_coverage"]
    elif fault == "incomplete_pin_coverage":
        owner["pin_observation_coverage"]["complete"] = False
    elif fault == "incomplete_identity_coverage":
        owner["identity_observation_coverage"]["complete"] = False
    elif fault == "pin_getter_error":
        owner["pin_observation_coverage"]["errors"] = ["incomplete native pin enumeration"]
    elif fault == "identity_getter_error":
        owner["identity_observation_coverage"]["errors"] = ["native owner identity unread"]
    else:
        value["metadata"]["source"] = "MGCPCB.ExpeditionPCBApplication"
    with pytest.raises(ValueError):
        canonical(value)


@pytest.mark.parametrize(
    "change",
    ["owner_uid", "number", "pin_uid", "net", "geometry", "format", "no_connect", "deleted_pin"],
)
def test_unnamed_singleton_real_fields_are_never_ignored(change):
    before, after = unnamed_singletons(), unnamed_singletons()
    owner = after["unnamed_components"][0]
    pin = owner["pins"][0]
    if change == "owner_uid":
        owner["uid"] = "new-owner"
    elif change == "number":
        pin["number"] = "1"
    elif change == "pin_uid":
        pin["uid"] = "changed-pin"
    elif change == "net":
        pin["net"] = "OTHER"
    elif change == "geometry":
        pin["x"] += 0.000001
    elif change == "format":
        owner["format"]["font"] = "different"
    elif change == "no_connect":
        pin["no_connect"] = True
    else:
        owner["pins"].clear()
    assert _stable(before) != _stable(after)
