from __future__ import annotations

import json
import random
from collections.abc import Sequence

import pytest
from fakes import schematic_snapshot

from xpedition_cli import query_page as paging
from xpedition_cli.errors import CLIError


def legacy(items, query=None, limit=None, offset=0):
    rows = [
        item
        for item in items
        if not query
        or str(query).casefold() in json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()
    ]
    start = min(offset, len(rows))
    end = len(rows) if limit is None else min(len(rows), start + limit)
    selected = rows[start:end]
    return {
        "items": selected,
        "count": len(selected),
        "offset": start,
        "next_offset": end if end < len(rows) else None,
        "has_more": end < len(rows),
    }


def test_seeded_differential_covers_sparse_queries_and_page_boundaries():
    rng = random.Random(1942)
    for _ in range(800):
        rows = [
            {
                "id": str(i),
                "name": rng.choice(("Straße", "电源", "VCC", "GND", "")),
                "nested": {"value": rng.choice((None, False, 0, "Mixed CASE"))},
            }
            for i in range(rng.randrange(0, 80))
        ]
        args = {
            "query": rng.choice((None, "", "vcc", "STRASSE", "电", "false", "value", "missing")),
            "offset": rng.randrange(0, 100),
            "limit": rng.choice((None, 0, 1, 4, 15)),
        }
        expected = legacy(rows, **args)
        assert paging.query_page(rows, **args) == expected
        assert paging.query_page(iter(rows), **args) == expected


def test_one_result_needs_two_matching_serializations_not_ten_thousand(monkeypatch):
    original = json.dumps
    calls = []

    def count(item, **kwargs):
        calls.append(item)
        return original(item, **kwargs)

    monkeypatch.setattr(paging.json, "dumps", count)
    rows = [{"name": "match", "id": str(i)} for i in range(10000)]
    page = paging.query_page(rows, query="match", limit=1)
    assert page["items"] == [rows[0]]
    assert page["has_more"] and len(calls) == 2


def test_sequence_page_does_not_iterate_or_copy_the_whole_source():
    class SliceOnly(Sequence):
        def __len__(self):
            return 1000000

        def __getitem__(self, key):
            assert isinstance(key, slice), "full iteration is not allowed"
            assert key.start == 400 and key.stop == 402
            return [400, 401]

    assert paging.query_page(SliceOnly(), offset=400, limit=2) == {
        "items": [400, 401],
        "count": 2,
        "offset": 400,
        "next_offset": 402,
        "has_more": True,
    }


def test_generator_is_consumed_only_through_lookahead():
    def source():
        yield {"name": "match"}
        yield {"name": "match"}
        raise AssertionError("requested page must not consume the tail")

    assert paging.query_page(source(), query="match", limit=1)["has_more"]


def test_zero_limit_and_clamped_offset_are_legacy_compatible():
    assert paging.query_page([1], limit=0) == legacy([1], limit=0)
    assert paging.query_page([1], offset=9, limit=0) == legacy([1], offset=9, limit=0)
    assert paging.query_page(iter([1]), offset=9, limit=0) == legacy([1], offset=9, limit=0)


@pytest.mark.parametrize(
    "options", [{"limit": -1}, {"offset": -1}, {"limit": True}, {"limit": "1"}, {"offset": None}]
)
def test_invalid_page_parameters_fail_before_consumption(options):
    def source():
        raise AssertionError("invalid pagination must not consume input")
        yield

    with pytest.raises(CLIError) as error:
        paging.query_page(source(), **options)
    assert error.value.code == "E_VALIDATION"


def test_cli_queries_scan_records_once_and_stop_at_page(cli, adapter, project, monkeypatch):
    design = schematic_snapshot()
    part = design["components"][2]
    design["components"] = [
        {**part, "refdes": f"R{i}", "description": "QUERY_MATCH"} for i in range(200)
    ]
    adapter.on("snapshot", lambda params: design)
    original = paging.json.dumps
    calls = 0

    def count(item, *args, **kwargs):
        nonlocal calls
        if isinstance(item, dict) and item.get("description") == "QUERY_MATCH":
            calls += 1
        return original(item, *args, **kwargs)

    monkeypatch.setattr(paging.json, "dumps", count)
    code, envelope = cli(
        "schematic",
        "components",
        "--project",
        str(project),
        "--query",
        "QUERY_MATCH",
        "--limit",
        "1",
    )
    assert code == 0, envelope
    result = envelope["data"]
    assert result["count"] == 1 and result["has_more"] is True
    assert calls == 2


def test_cli_page_metadata_remains_stable(cli, adapter, project):
    code, envelope = cli(
        "schematic",
        "components",
        "--project",
        str(project),
        "--query",
        "RES-4K7",
        "--limit",
        "1",
        "--offset",
        "1",
    )
    assert code == 0, envelope
    data = envelope["data"]
    assert (data["count"], data["offset"], data["next_offset"], data["has_more"]) == (
        1,
        1,
        None,
        False,
    )
    assert "items" in data["_untrusted"]
