"""Registered partitions and selected native search membership are different facts."""

from types import SimpleNamespace

import pytest

from xpedition_cli import native_library_manager as manager


@pytest.mark.parametrize("included", [False, True])
def test_scheme_membership_reads_each_native_partition_without_mutating_it(included):
    partition = SimpleNamespace(Name="QA20261006")
    scheme = SimpleNamespace(
        Name="(Default)", IsPartitionInScheme=lambda item: included if item is partition else False
    )
    calls = []

    def item(index):
        calls.append(index)
        assert index == 1
        return scheme

    library = SimpleNamespace(
        SchemeList=SimpleNamespace(Count=1, Item=item),
        Partitions=lambda kind: [partition] if kind == manager.PARTITION_TYPES["cells"] else [],
    )
    result = manager.search_schemes(library)
    assert result["status"] == "observed" and calls == [1]
    assert result["items"][0]["partitions"]["cells"] == [
        {"name": "QA20261006", "included": included}
    ]


def test_unavailable_scheme_interface_preserves_unknown():
    result = manager.search_schemes(SimpleNamespace())
    assert result["status"] == "not_observed"
    assert result["items"] is None


def test_scheme_count_is_bounded_before_iteration():
    library = SimpleNamespace(SchemeList=SimpleNamespace(Count=10**9))
    result = manager.search_schemes(library)
    assert result["status"] == "not_observed"
    assert result["diagnostics"][0]["code"] == "E_CONFLICT"
