"""Cumulative native contour budget across different supported shape kinds."""

import math
from types import SimpleNamespace

import pytest

from xpedition_cli.endpoint_material import _native_contour


def test_circle_after_full_polygon_point_inventory_remains_over_budget():
    points = [
        [math.cos(2 * math.pi * i / 127), math.sin(2 * math.pi * i / 127), 0] for i in range(127)
    ]
    points.append(points[0])
    polygon = SimpleNamespace(
        IsValid=lambda: True,
        IsCircle=lambda: False,
        IsClosed=lambda: True,
        IsArced=lambda: False,
        rows=[list(row) for row in zip(*points, strict=True)],
    )
    circle = SimpleNamespace(IsValid=lambda: True, IsCircle=lambda: True)
    adapter = SimpleNamespace(
        _propget=lambda geometry, key, unit: geometry.rows if key == 1 else 1.0
    )
    budget = {"contours": 0, "points": 0}
    for _ in range(32):
        _native_contour(polygon, adapter, budget)
    assert budget == {"contours": 32, "points": 4096}
    with pytest.raises(ValueError, match="4096-point budget"):
        _native_contour(circle, adapter, budget)
