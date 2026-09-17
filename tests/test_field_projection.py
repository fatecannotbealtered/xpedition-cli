from __future__ import annotations

import copy

import pytest

from xpedition_cli.field_projection import project_fields


@pytest.fixture
def page():
    return {
        "items": [
            {"refdes": "R1", "value": "10k", "pins": [{"number": "1", "net": "VCC"}],
             "_untrusted": ["value"]},
            {"refdes": "C1", "value": "100n", "pins": [], "_untrusted": ["value"]},
        ],
        "count": 2, "offset": 10, "next_offset": 12, "has_more": True,
        "truncated": True, "_untrusted": ["items"], "unselected": "large payload",
    }


@pytest.mark.parametrize("selector", ["items.refdes", "items[].refdes"])
def test_projects_array_rows_and_keeps_control_data(page, selector):
    value = project_fields(page, selector)
    assert value["items"] == [
        {"refdes": "R1", "_untrusted": ["value"]},
        {"refdes": "C1", "_untrusted": ["value"]},
    ]
    assert value["_untrusted"] == ["items"]
    for key in ("count", "offset", "next_offset", "has_more", "truncated"):
        assert value[key] == page[key]
    assert "unselected" not in value


def test_nested_arrays_and_empty_arrays(page):
    value = project_fields(page, "items.refdes,items.pins.number")
    assert value["items"][0]["pins"] == [{"number": "1"}]
    assert value["items"][1]["pins"] == []


@pytest.mark.parametrize("selector", ["items,items.refdes", "items.refdes,items", "items,items"])
def test_parent_selection_wins_without_mutating_input(page, selector):
    original = copy.deepcopy(page)
    result = project_fields(page, selector)
    assert result["items"] == page["items"]
    assert page == original


def test_sparse_and_mixed_rows_keep_positions():
    result = project_fields({"items": [{"x": 1}, {}, None, "hidden", 42]}, "items.x")
    assert result == {"items": [{"x": 1}, {}, None, None, None]}


def test_null_is_not_a_missing_value():
    assert project_fields({"x": None}, "x") == {"x": None}


def test_unknown_paths_keep_legacy_omission_semantics(page):
    assert project_fields(page, "missing.field") == {}
    assert project_fields(page, "") is page
    assert project_fields(page, None) is page


def test_nested_paging_and_cursor_metadata():
    value = {"nested": {"items": [{"x": 1, "y": 2}], "count": 1,
                        "next_cursor": "page-2", "has_more": True, "_untrusted": ["items"]}}
    assert project_fields(value, "nested.items.x") == {
        "nested": {"items": [{"x": 1}], "count": 1,
                   "next_cursor": "page-2", "has_more": True, "_untrusted": ["items"]}
    }


def test_array_root_can_be_projected():
    assert project_fields([{"x": 1, "y": 2}], "x") == [{"x": 1}]


def test_unknown_nested_path_is_omitted_not_fabricated():
    assert project_fields({"x": 1}, "x.y") == {}
    assert project_fields({"x": {"a": 1}}, "x.b") == {}
