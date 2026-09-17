from __future__ import annotations

from xpedition_cli import routing_plan as routing


def test_single_trace_does_not_visit_old_old_pairs():
    class CountedLayer(int):
        comparisons = 0

        def __ne__(self, other):
            type(self).comparisons += 1
            return int(self) != other

    board = routing.Board({})
    size = 3000
    layer = CountedLayer(1)
    board.segments = [
        ((0.0, float(i)), (1.0, float(i)), layer, "SAME", 0.254, -1) for i in range(size)
    ]
    result = routing.check_plan([{"net": "SAME", "layer": 1, "points": [(0, 0), (1, 0)]}], board)
    assert result == []
    # Deterministic complexity guard, not a flaky wall-clock threshold.
    assert CountedLayer.comparisons <= size


def test_unchanged_board_has_no_old_old_geometry_work(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("old-old geometry must not be checked")

    for helper in ("_dist_segments", "_dist_point_segment", "_dist_point_rect"):
        monkeypatch.setattr(routing, helper, unexpected)
    board = routing.Board({})
    board.segments = [((0, 0), (1, 0), 1, "A", 0.254, -1), ((0, 0), (1, 0), 1, "B", 0.254, -1)]
    board.vias = [((0, 0), "A", -1), ((0, 0), "B", -1)]
    assert routing.check_plan([], board) == []


def test_new_old_and_new_new_conflicts_are_still_reported():
    board = routing.Board({})
    board.segments = [((0, 0), (3, 0), 1, "OLD", 0.254, -1)]
    items = [
        {"net": "A", "layer": 1, "points": [(1, -1), (1, 1)]},
        {"net": "B", "layer": 1, "points": [(0, 0.1), (2, 0.1)]},
    ]
    problems = routing.check_plan(items, board)
    assert any("items -1/0" in item for item in problems)
    assert any("items -1/1" in item for item in problems)
    assert any("items 0/1" in item for item in problems)


def test_new_via_is_checked_against_old_segments_and_vias():
    board = routing.Board({})
    board.segments = [((0, 0), (2, 0), 1, "OLD", 0.254, -1)]
    board.vias = [((1, 0), "OLD", -1)]
    problems = routing.check_plan([{"kind": "via", "net": "NEW", "at": (1, 0.1)}], board)
    assert any("vs item -1" in item for item in problems)
    assert any("vias 0/-1" in item for item in problems)
    assert any("vias -1/0" in item for item in problems)
