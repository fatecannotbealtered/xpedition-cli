"""The page sizes of an exported schematic PDF, so a clipped export is caught by a check.

A sheet whose border is larger than its page loses what does not fit when `sch2pdf`
prints it, and nothing else reports it: a landscape C border on a portrait C page
lost the title block of every sheet, and the first sign was a person opening the PDF.
`sch2pdf` writes an uncompressed PDF 1.4 with a `/MediaBox` on every page, in page
order, which is enough to read each page's size and orientation back.
"""

from __future__ import annotations

import re
from typing import Any

POINTS_PER_INCH = 72.0
MM_PER_INCH = 25.4
MEDIA_BOX = re.compile(rb"/MediaBox\s*\[\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\]")


def pages(data: bytes) -> list[dict[str, Any]]:
    """Each page's size from its `/MediaBox`, in the order the file holds them."""
    found = []
    for number, match in enumerate(MEDIA_BOX.finditer(data), start=1):
        x0, y0, x1, y1 = (float(value) for value in match.groups())
        width, height = abs(x1 - x0), abs(y1 - y0)
        orientation = "landscape" if width > height else "portrait" if height > width else "square"
        found.append(
            {
                "page": number,
                "width_pt": round(width, 1),
                "height_pt": round(height, 1),
                "width_mm": round(width / POINTS_PER_INCH * MM_PER_INCH, 1),
                "height_mm": round(height / POINTS_PER_INCH * MM_PER_INCH, 1),
                "orientation": orientation,
            }
        )
    return found


def warnings(found: list[dict[str, Any]]) -> list[str]:
    """What a page's size says about the export. Every border the planner draws is
    landscape, so a portrait page means a landscape sheet was printed onto a page too
    narrow for it; a sheet drawn by hand on a portrait border is the exception."""
    return [
        f"page {page['page']} is portrait ({page['width_pt']:g} x {page['height_pt']:g} pt): "
        "a landscape border on it is clipped -- the sheet's page size is the portrait one"
        for page in found
        if page["orientation"] == "portrait"
    ]
