"""A part's picture: the symbol beside the footprint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import LIBRARY_PARTS

from xpedition_cli import library_parts as P
from xpedition_cli import library_render as V

PIL = pytest.importorskip("PIL")


def test_the_picture_shows_the_symbol_and_the_lands(tmp_path: Path) -> None:
    from PIL import Image

    view = P.views(P.plan(LIBRARY_PARTS))[1]
    output = tmp_path / "mcu.png"
    info = V.render(view, output)
    assert info["symbol_pins"] == 8 and info["lands"] == 8 and info["pin_numbers"] == 8
    assert info["extent_mm"][0] > 6 and output.is_file()
    image = Image.open(output).convert("RGB")
    assert (image.width, image.height) == (info["width"], info["height"])
    colours = {
        image.getpixel((x, y))
        for x in range(image.width // 2, image.width, 4)
        for y in range(0, image.height, 4)
    }
    # copper lands in red on the footprint side
    assert any(r > 180 and g < 100 and b < 100 for r, g, b in colours)
    with pytest.raises(FileExistsError):
        V.render(view, output)


def test_several_lands_of_one_pin_are_drawn_apart(tmp_path: Path) -> None:
    from PIL import Image

    spec = {
        "parts": [
            {
                "number": "FET",
                "prefix": "Q",
                "symbol": {"kind": "NMOS"},
                "footprint": {
                    "pads": [
                        {"pin": "1", "x": -1, "y": 0, "width": 0.6},
                        {"pin": "2", "x": 1, "y": -0.6, "width": 0.6},
                        {"pin": "3", "x": 1, "y": 0.6, "width": 0.6},
                        {"pin": "3", "x": 0, "y": 0.6, "width": 0.6},
                    ],
                    "height": 1,
                },
            }
        ]
    }
    info = V.render(P.views(P.plan(spec))[0], tmp_path / "fet.png")
    assert info["lands"] == 4 and info["pin_numbers"] == 3
    image = Image.open(info["path"]).convert("RGB")
    colours = {
        image.getpixel((x, y)) for x in range(0, image.width, 3) for y in range(0, image.height, 3)
    }
    # the lands of the shared pin are drawn orange, the others red
    assert any(r > 200 and 110 < g < 170 and b < 90 for r, g, b in colours)


def test_a_part_without_a_cell_or_symbol_still_draws(tmp_path: Path) -> None:
    info = V.render({"number": "X", "symbol": None, "cell": None}, tmp_path / "x.png")
    assert info["lands"] == 0 and info["symbol_pins"] == 0 and info["extent_mm"] is None
