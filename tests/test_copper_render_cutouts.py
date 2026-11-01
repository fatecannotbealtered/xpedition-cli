"""Native-style copper contours retain voids in independently decoded PNGs."""

import pytest
from PIL import Image

from xpedition_cli.board_render import COLOURS, model_from_dict, render


@pytest.mark.parametrize("outer", ["path", "circle"])
@pytest.mark.parametrize("side,layer", [("top", 1), ("bottom", 2)])
def test_copper_polygon_and_circular_voids_survive_png_decode(tmp_path, outer, side, layer):
    outer_shape = (
        {"path": [[1, 1, 0], [9, 1, 0], [9, 9, 0], [1, 9, 0], [1, 1, 0]]}
        if outer == "path"
        else {"circle": [5, 5, 4]}
    )
    plane = {
        "kind": "plane",
        "net": "A",
        "layer": layer,
        **outer_shape,
        "cutouts": [[[3, 3, 0], [4, 3, 0], [4, 4, 0], [3, 4, 0], [3, 3, 0]]],
        "circular_cutouts": [[6, 6, 0.5]],
    }
    model = model_from_dict({"layers": 2, "planes": [plane]})
    output = tmp_path / "copper-contours.png"
    receipt = render(model, output, scale=20, side=side, supersample=1, window=(0, 0, 10, 10))
    assert receipt["counts"]["planes"] == 1
    assert receipt["width"] == receipt["height"] == 200
    assert model.planes[0].cutouts == [list(map(tuple, plane["cutouts"][0]))]
    assert model.planes[0].circular_cutouts == [(6, 6, 0.5)]
    with Image.open(output) as png:
        assert png.format == "PNG"
        png.load()
        assert png.size == (200, 200)

        def pixel(x, y):
            # Explicit independent mm-to-image transform, including bottom view.
            u = 20 * (x if side == "top" else 10 - x)
            v = 20 * (10 - y)
            return png.getpixel((round(u), round(v)))

        background = COLOURS["background"][:3]
        assert pixel(3.5, 3.5) == background, "Polygon void must not be filled copper"
        assert pixel(6, 6) == background, "Circular void must not be filled copper"
        assert pixel(5, 5) != background, "The generated copper itself must remain visible"
        assert pixel(0.5, 0.5) == background, "Pixels outside the native contour remain clear"
        assert pixel(6.7, 6) != background, "A circular void must not erase adjacent copper"
