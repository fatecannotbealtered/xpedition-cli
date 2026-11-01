"""The 2-D tests the silkscreen checks share: a line, a filled shape or a disc against
a pad or a box."""

from __future__ import annotations

from xpedition_cli.shapes import Outline


def _square(x1: float, y1: float, x2: float, y2: float) -> dict:
    return {"path": [[x1, y1, 0], [x2, y1, 0], [x2, y2, 0], [x1, y2, 0], [x1, y1, 0]]}


PAD = Outline(_square(0, 0, 1, 1))


def test_a_line_crosses_a_pad_by_its_width() -> None:
    across = Outline({"path": [[-1, 0.5, 0], [2, 0.5, 0]], "width": 0.12})
    assert across.crosses(PAD)
    # 0.05 mm beside the pad: half of a 0.12 mm line still reaches it
    beside = Outline({"path": [[-1, 1.05, 0], [2, 1.05, 0]], "width": 0.12})
    assert beside.crosses(PAD)
    # 0.1 mm beside it, it does not
    clear = Outline({"path": [[-1, 1.1, 0], [2, 1.1, 0]], "width": 0.12})
    assert not clear.crosses(PAD)
    # a line that ends inside the pad crosses it too
    assert Outline({"path": [[0.5, 0.5, 0], [0.5, 3, 0]], "width": 0.1}).crosses(PAD)


def test_round_pads_rings_and_filled_shapes() -> None:
    disc = Outline({"circle": [0, 0, 0.5]})
    assert Outline({"path": [[-1, 0.5, 0], [1, 0.5, 0]], "width": 0.12}).crosses(disc)
    assert not Outline({"path": [[-1, 0.6, 0], [1, 0.6, 0]], "width": 0.12}).crosses(disc)
    # a silkscreen circle drawn as a line around a pad, clear of it, and across it
    ring = Outline({"circle": [0.5, 0.5, 1.2], "width": 0.12})
    assert not ring.crosses(PAD)
    assert Outline({"circle": [0.5, 0.5, 0.6], "width": 0.12}).crosses(PAD)
    # a filled shape overlapping the pad, and one only touching its edge
    assert Outline({**_square(0.9, 0.9, 2, 2), "width": 0}).crosses(PAD)
    assert not Outline({**_square(1, 0, 2, 1), "width": 0}).crosses(PAD)
    assert Outline({"circle": [1.2, 0.5, 0.3]}).crosses(PAD)


def test_a_shape_reaches_into_a_box_by_its_width_or_area() -> None:
    line = Outline({"path": [[0, 0, 0], [4, 0, 0]], "width": 0.2})
    assert line.reaches((1, -0.05, 2, 1))
    assert not line.reaches((1, 0.2, 2, 1))
    assert PAD.reaches((0.5, 0.5, 3, 3)) and not PAD.reaches((1, 1, 3, 3))
    assert Outline({"circle": [0, 0, 1]}).reaches((0.5, 0.5, 2, 2))
