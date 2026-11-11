"""The board picture of pcb render (plan/board_render.py), drawn with the standard library only."""
from __future__ import annotations

import struct
import unittest
import zlib

from xpedition_cli.adapter.plan import board_render as R

OUTLINE = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]


def pixels(png: bytes) -> tuple[int, int, bytes]:
    width, height = struct.unpack(">II", png[16:24])
    data = png[33:]
    idat = b""
    while data:
        length = struct.unpack(">I", data[:4])[0]
        kind, body = data[4:8], data[8:8 + length]
        if kind == b"IDAT":
            idat += body
        data = data[12 + length:]
    raw = zlib.decompress(idat)
    rows = b"".join(raw[r * (width * 3 + 1) + 1:(r + 1) * (width * 3 + 1)] for r in range(height))
    return width, height, rows


def at(png: bytes, x: int, y: int) -> tuple[int, int, int]:
    width, _height, rows = pixels(png)
    offset = (y * width + x) * 3
    return tuple(rows[offset:offset + 3])


class RenderTest(unittest.TestCase):
    def test_a_valid_png_of_the_board_and_its_margin(self):
        png, width, height = R.render(R.Picture(outline=OUTLINE, layer_count=2), scale=10)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual((width, height), (120, 70))  # 10 x 5 mm and a 1 mm margin, at 10 px/mm
        self.assertEqual(pixels(png)[:2], (120, 70))
        self.assertEqual(at(png, 2, 2), R.BACKGROUND)

    def test_top_copper_is_red_and_the_bottom_view_mirrors_it(self):
        picture = R.Picture(outline=OUTLINE, layer_count=2, pads=[(1, R.polygon_of(circle=[2.0, 2.5, 0.5]))])
        top, _w, _h = R.render(picture, scale=10)
        bottom, _w, _h = R.render(picture, side="bottom", scale=10)
        self.assertEqual(at(top, 30, 35), R.COPPER["top"])  # (2, 2.5) mm with the 1 mm margin
        far = at(bottom, 120 - 31, 35)  # mirrored left to right, and dimmer: the top is the far side now
        self.assertNotEqual(far, R.BACKGROUND)
        self.assertGreater(far[0], far[2])

    def test_vias_traces_and_openings_are_drawn(self):
        picture = R.Picture(outline=OUTLINE, layer_count=2, traces=[(1, [(1.0, 1.0), (9.0, 1.0)], 0.3)], vias=[(5.0, 4.0, 0.3)],
                            openings=[("top", (6.0, 2.0, 8.0, 3.0))])
        png, _w, _h = R.render(picture, scale=10)
        self.assertEqual(at(png, 60, 20), R.DRILL)  # the via's drill at (5, 4) mm
        self.assertEqual(at(png, 50, 50), R.COPPER["top"])  # the top trace at y 1 mm
        self.assertEqual(at(png, 75, 30), R.OPENING)  # the opening's top edge at y 3 mm

    def test_a_cutout_shows_through_the_copper(self):
        plane = (1, [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)])
        slot = [(4.0, 2.0), (6.0, 2.0), (6.0, 3.0), (4.0, 3.0)]
        png, _w, _h = R.render(R.Picture(outline=OUTLINE, layer_count=2, planes=[plane], cutouts=[slot]), scale=10)
        self.assertEqual(at(png, 60, 35), R.BACKGROUND)  # inside the slot at (5, 2.5) mm
        self.assertNotEqual(at(png, 30, 35), R.BACKGROUND)  # the plane beside it

    def test_the_counts_are_of_what_is_drawn(self):
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        picture = R.Picture(outline=OUTLINE, layer_count=4, pads=[(1, square), (4, square)], planes=[(2, square)],
                            traces=[(1, [(0.0, 0.0), (1.0, 0.0)], 0.2), (3, [(0.0, 0.0), (1.0, 0.0)], 0.2)], vias=[(5.0, 4.0, 0.3)],
                            openings=[("top", (6.0, 2.0, 8.0, 3.0)), ("bottom", (1.0, 1.0, 2.0, 2.0))])
        self.assertEqual(R.drawn(picture, layers=[1]), {"pads": 1, "traces": 1, "vias": 1, "planes": 0, "plane_copper": 0, "openings": 1})
        self.assertEqual(R.drawn(picture, side="bottom"), {"pads": 2, "traces": 2, "vias": 1, "planes": 1, "plane_copper": 0, "openings": 1})

    def test_the_plane_copper_layout_made_is_drawn_with_its_holes_instead_of_the_shape(self):
        shape = (1, [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)])
        copper = (1, [[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)], [(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 3.0)]])
        picture = R.Picture(outline=OUTLINE, layer_count=2, planes=[shape], copper=[copper])
        png, _w, _h = R.render(picture, scale=10)
        self.assertNotEqual(at(png, 15, 35), R.BACKGROUND)  # copper at (1, 1.5) mm
        self.assertEqual(at(png, 30, 30), R.BACKGROUND)  # its hole at (2.5, 2.5) mm
        self.assertEqual(at(png, 85, 30), R.BACKGROUND)  # the shape alone, past the copper at x 8 mm, is not drawn
        self.assertEqual(R.drawn(picture)["plane_copper"], 1)
        self.assertEqual(R.drawn(picture)["planes"], 1)  # the shape is there, drawn as its copper

    def test_a_huge_board_is_scaled_down(self):
        _png, width, height = R.render(R.Picture(outline=[(0, 0), (500, 0), (500, 100), (0, 100)], layer_count=2), scale=20)
        self.assertLessEqual(max(width, height), R.MAX_PIXELS)


if __name__ == "__main__":
    unittest.main()
