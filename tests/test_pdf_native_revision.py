"""Native source census permutations do not invalidate an unchanged PDF witness."""

import copy

import pytest

from xpedition_cli.schematic_pdf_artifacts import _native_digest


def source():
    return {
        "revision": "native",
        "metadata": {"backend": "native_xpedition", "source": "Viewdraw.Application"},
        "components": [
            {
                "uid": "owner1",
                "refdes": "X903",
                "pins": [{"number": "1", "net": "READY"}, {"number": "2", "net": "GND"}],
            },
            {"uid": "owner2", "refdes": "R57", "pins": [{"number": "1", "net": "READY"}]},
        ],
        "unnamed_components": [{"uid": "symbol-77", "refdes": "", "pins": []}],
        "nets": [{"name": "READY", "sheets": [9, 1, 4]}, {"name": "GND", "sheets": [4]}],
        "sheets": [{"number": 9, "name": "OtherName.9"}, {"number": 1, "name": "OtherName.1"}],
        "connections": [{"net": "READY", "pins": ["X903.1", "R57.1"]}],
        "ordered_path": [[0, 0], [1, 0], [1, 1]],
    }


def test_reopened_net_sheet_membership_and_component_order_are_not_source_changes():
    before = source()
    after = copy.deepcopy(before)
    for key in ("components", "sheets", "nets", "connections"):
        after[key].reverse()
    after["nets"][1]["sheets"].reverse()
    after["components"][1]["pins"].reverse()
    after["connections"][0]["pins"].reverse()
    assert _native_digest(before) == _native_digest(after)
    assert before == source()


@pytest.mark.parametrize("field", ["uid", "net", "sheets", "path", "foreign_format"])
def test_real_source_changes_remain_different(field):
    before, after = source(), source()
    if field == "uid":
        after["components"][1]["uid"] = "replacement-owner"
    elif field == "net":
        after["components"][1]["pins"][0]["net"] = "WRONG"
    elif field == "sheets":
        after["nets"][0]["sheets"].pop()
    elif field == "path":
        after["ordered_path"].reverse()
    else:
        after["unnamed_components"][0]["format"] = {"hidden": True}
    assert _native_digest(before) != _native_digest(after)


@pytest.mark.parametrize(
    "fault", ["duplicate_uid", "missing_uid", "duplicate_pin", "duplicate_sheet"]
)
def test_ambiguous_pdf_source_census_is_refused(fault):
    value = source()
    if fault == "duplicate_uid":
        value["components"][1]["uid"] = value["components"][0]["uid"]
    elif fault == "missing_uid":
        del value["components"][1]["uid"]
    elif fault == "duplicate_pin":
        value["components"][0]["pins"].append(copy.deepcopy(value["components"][0]["pins"][0]))
    else:
        value["nets"][0]["sheets"].append(9)
    with pytest.raises(ValueError):
        _native_digest(value)


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_retained_singleton_symbols_are_a_stable_pdf_source_after_reopen_reordering():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_retained_unnamed_singleton_changes_still_invalidate_pdf_source():
    """Original historical coverage is not reproduced in this snapshot."""
    pass


@pytest.mark.skip(reason="Private recorded fixture omitted from public snapshot")
def test_unproved_singleton_pin_census_cannot_become_a_pdf_source_witness():
    """Original historical coverage is not reproduced in this snapshot."""
    pass
