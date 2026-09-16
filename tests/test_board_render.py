from __future__ import annotations

import math
from pathlib import Path

from xpedition_cli import board_render
from xpedition_cli.board_render import Model, Shape, Text, flatten, model_from_dict


def test_flatten_turns_arc_centres_into_segments_the_signed_way() -> None:
    # a quarter circle from (10, 0) to (0, 10) about the origin: a negative radius
    # goes counter-clockwise, the short way here
    ccw = flatten([(10, 0, 0), (0, 0, -10), (0, 10, 0)])
    assert ccw[0] == (10, 0) and ccw[-1] == (0, 10)
    assert len(ccw) > 5
    middle = ccw[len(ccw) // 2]
    assert middle[0] > 0 and middle[1] > 0  # through the first quadrant
    assert math.isclose(math.hypot(*middle), 10, rel_tol=1e-6)
    # a positive radius goes clockwise, the long way round for these end points
    cw = flatten([(10, 0, 0), (0, 0, 10), (0, 10, 0)])
    assert len(cw) > len(ccw)
    assert any(x < 0 for x, _y in cw)
    # rows without a radius are plain corners
    assert flatten([(0, 0, 0), (5, 0, 0), (5, 5, 0)]) == [(0, 0), (5, 0), (5, 5)]


def test_model_from_dict_round_trips_the_adapter_records() -> None:
    model = model_from_dict(
        {
            "layers": 4,
            "outline": [
                {"path": [[0, 0, 0], [30, 0, 0], [30, 20, 0], [0, 20, 0]], "kind": "outline"}
            ],
            "pads": [{"circle": [5, 5, 0.6], "layer": 1, "net": "GND"}],
            "traces": [{"path": [[5, 5, 0], [15, 5, 0]], "layer": 4, "width": 0.5}],
            "planes": [
                {
                    "path": [[1, 1, 0], [29, 1, 0], [29, 19, 0], [1, 19, 0]],
                    "layer": 2,
                    "cutouts": [[[5, 5, 0], [6, 5, 0], [6, 6, 0]]],
                }
            ],
            "holes": [[5, 5, 0.3]],
            "silk": [{"path": [[2, 2, 0], [8, 2, 0]], "side": "top", "width": 0.12}],
            "texts": [{"x": 15, "y": 10, "text": "U1", "height": 1.0, "orientation": 90}],
        }
    )
    assert model.layers == 4
    assert model.pads[0].circle == (5, 5, 0.6)
    assert model.traces[0].layer == 4 and model.traces[0].width == 0.5
    assert model.planes[0].cutouts == [[(5, 5, 0), (6, 5, 0), (6, 6, 0)]]
    assert model.texts[0].orientation == 90
    assert model.bounds() == (0, 0, 30, 20)


def _sample() -> Model:
    return Model(
        layers=2,
        outline=[
            Shape(path=[(0, 0, 0), (40, 0, 0), (40, 30, 0), (0, 30, 0), (0, 0, 0)], kind="outline")
        ],
        pads=[
            Shape(path=[(4, 4, 0), (6, 4, 0), (6, 6, 0), (4, 6, 0)], layer=1, kind="pad"),
            Shape(circle=(30, 20, 0.8), path=[], layer=2, kind="pad"),
        ],
        vias=[Shape(circle=(20, 15, 0.33), path=[], layer=1, kind="via")],
        traces=[
            Shape(path=[(5, 5, 0), (20, 5, 0), (20, 15, 0)], layer=1, width=0.5, kind="trace"),
            Shape(path=[(20, 15, 0), (30, 20, 0)], layer=2, width=0.25, kind="trace"),
        ],
        planes=[
            Shape(
                path=[(1, 1, 0), (39, 1, 0), (39, 29, 0), (1, 29, 0)],
                layer=2,
                cutouts=[[(18, 13, 0), (22, 13, 0), (22, 17, 0), (18, 17, 0)]],
                kind="plane",
            )
        ],
        holes=[(20, 15, 0.3), (36, 26, 2.2)],
        silk=[
            Shape(
                path=[(3, 3, 0), (7, 3, 0), (7, 7, 0), (3, 7, 0), (3, 3, 0)],
                width=0.12,
                kind="silk",
            )
        ],
        texts=[
            Text(x=5, y=8, text="R1", height=1.0),
            Text(x=30, y=25, text="J1", height=1.2, orientation=90),
        ],
    )


def test_render_writes_a_png_with_copper_silk_and_outline(tmp_path: Path) -> None:
    from PIL import Image

    output = tmp_path / "board.png"
    result = board_render.render(_sample(), output, scale=10.0, side="top")
    assert output.is_file()
    assert result["counts"] == {
        "pads": 2,
        "vias": 1,
        "traces": 2,
        "planes": 1,
        "holes": 2,
        "silk": 1,
        "texts": 2,
    }
    image = Image.open(output).convert("RGB")
    assert (image.width, image.height) == (result["width"], result["height"])
    assert image.width == 440 and image.height == 340  # 40 mm + 2 mm margins at 10 px/mm

    def px(x_mm: float, y_mm: float) -> tuple[int, ...]:
        return image.getpixel((int((x_mm + 2) * 10), int((30 - y_mm + 2) * 10)))

    top_trace = px(12, 5)
    assert top_trace[0] > 150 and top_trace[2] < 100  # red top copper
    plane = px(10, 25)
    assert plane[2] > plane[0]  # blue-ish bottom plane, dimmed
    cut = px(19, 14)
    assert cut[2] < plane[2]  # the cutout shows the background again
    hole = px(36, 26)
    assert max(hole) < 40  # drill is dark
    silk = px(5, 3)
    assert min(silk) > 180  # light silkscreen line
    background = px(2, 28)
    assert background[2] > background[0]


def test_render_bottom_side_mirrors_the_board(tmp_path: Path) -> None:
    from PIL import Image

    output = tmp_path / "bottom.png"
    board_render.render(_sample(), output, scale=10.0, side="bottom")
    image = Image.open(output).convert("RGB")
    # the bottom pad at x=30 appears mirrored at x=10 from the left, in blue, in front
    pixel = image.getpixel((int((40 - 30 + 2) * 10), int((30 - 20 + 2) * 10)))
    assert pixel[2] > 150 and pixel[0] < 120
