"""What a soldermask opening bares, and where an opening fits that bares one net's copper alone.

An opening bares the copper under it on its side's outer layer. One meant to bare a net, such as
ground for a grounding spring or the conductive glue of a stiffener, must keep a margin from every
other net's copper, so that the mask's registration cannot bare it too and the spring or the glue
short it. ``exposure`` measures an opening against the copper; ``fit`` finds the largest rectangle
inside a region that lies on the net's copper and keeps the margin from all other copper, on a grid.

All lengths are in mm; a polygon is a list of points, a region of copper a list of rings, the first
its outline and the rest its holes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]
Ring = list[Point]
CELL_MM = 0.05  # the grid of fit
MAX_CELLS = 250_000  # a larger region is gridded coarser
MIN_SIDE_MM = 0.3  # the smallest opening fit offers


@dataclass
class LayerCopper:
    """The copper of one layer: regions (plane copper and pads), traces and vias, each with its net."""

    regions: list[tuple[str, list[Ring]]] = field(default_factory=list)
    traces: list[tuple[str, float, list[Point]]] = field(default_factory=list)  # (net, width, centre line)
    vias: list[tuple[str, Point, float]] = field(default_factory=list)  # (net, centre, pad radius)


def _box(points: list[Point]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _gap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def inside(point: Point, ring: Ring) -> bool:
    x, y = point
    hit = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i - 1], ring[i]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _in_region(point: Point, rings: list[Ring]) -> bool:
    return inside(point, rings[0]) and not any(inside(point, hole) for hole in rings[1:])


def _closest(p: Point, a: Point, b: Point) -> Point:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return a[0] + dx * t, a[1] + dy * t


def _crossing(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) < 1e-15:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / denominator
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / denominator
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return a[0] + r[0] * t, a[1] + r[1] * t
    return None


def _segments_apart(a: Point, b: Point, c: Point, d: Point) -> tuple[float, Point]:
    """The distance between two segments and the point of the second nearest the first."""
    hit = _crossing(a, b, c, d)
    if hit is not None:
        return 0.0, hit
    best = (math.inf, c)
    for p, (q0, q1), on_second in ((a, (c, d), True), (b, (c, d), True), (c, (a, b), False), (d, (a, b), False)):
        q = _closest(p, q0, q1)
        distance = math.dist(p, q)
        if distance < best[0]:
            best = (distance, q if on_second else p)
    return best


def _edges(ring: Ring, near: tuple[float, float, float, float], reach: float) -> list[tuple[Point, Point]]:
    out = []
    for i in range(len(ring)):
        a, b = ring[i - 1], ring[i]
        if (min(a[0], b[0]) > near[2] + reach or max(a[0], b[0]) < near[0] - reach
                or min(a[1], b[1]) > near[3] + reach or max(a[1], b[1]) < near[1] - reach):
            continue
        out.append((a, b))
    return out


def _to_region(polygon: Ring, rings: list[Ring], reach: float) -> tuple[float, Point]:
    """How far a region of copper is from the polygon (0 where they overlap), and its nearest point."""
    box = _box(polygon)
    if _gap(box, _box(rings[0])) > reach:
        return math.inf, rings[0][0]
    for p in polygon:
        if _in_region(p, rings):
            return 0.0, p
    for p in rings[0]:
        if inside(p, polygon):
            return 0.0, p
    best: tuple[float, Point] = (math.inf, rings[0][0])
    sides = [(polygon[i - 1], polygon[i]) for i in range(len(polygon))]
    for ring in rings:
        for c, d in _edges(ring, box, reach):
            for a, b in sides:
                distance, at = _segments_apart(a, b, c, d)
                if distance < best[0]:
                    best = (distance, at)
                    if distance == 0.0:
                        return best
    return best


def _to_line(polygon: Ring, line: list[Point], width: float, reach: float) -> tuple[float, Point]:
    """How far a trace's copper is from the polygon (0 where they overlap), and its nearest point."""
    box = _box(polygon)
    best: tuple[float, Point] = (math.inf, line[0])
    sides = [(polygon[i - 1], polygon[i]) for i in range(len(polygon))]
    for p in line:
        if inside(p, polygon):
            return 0.0, p
    for c, d in zip(line, line[1:], strict=False):
        if _gap(box, _box([c, d])) > reach + width / 2:
            continue
        for a, b in sides:
            distance, at = _segments_apart(a, b, c, d)
            distance = max(0.0, distance - width / 2)
            if distance < best[0]:
                best = (distance, at)
    return best


def _to_disc(polygon: Ring, centre: Point, radius: float) -> tuple[float, Point]:
    if inside(centre, polygon):
        return 0.0, centre
    distance = min(math.dist(centre, _closest(centre, polygon[i - 1], polygon[i])) for i in range(len(polygon)))
    return max(0.0, distance - radius), centre


def exposure(polygon: Ring, copper: LayerCopper, margin: float) -> dict[str, list]:
    """The nets whose copper the opening bares (``bared``), and for each net the nearest its copper
    comes to the opening within the margin (``near``: net, gap and where), the bared at gap 0."""
    nearest: dict[str, tuple[float, Point]] = {}

    def keep(net: str, found: tuple[float, Point]) -> None:
        if (found[0] == 0.0 or found[0] < margin - 1e-9) and found[0] < nearest.get(net, (math.inf,))[0]:
            nearest[net] = found

    for net, rings in copper.regions:
        keep(net, _to_region(polygon, rings, margin))
    for net, width, line in copper.traces:
        keep(net, _to_line(polygon, line, width, margin))
    for net, centre, radius in copper.vias:
        if _gap(_box(polygon), (centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius)) <= margin:
            keep(net, _to_disc(polygon, centre, radius))
    near = [{"net": net, "gap_mm": round(gap, 3), "at_mm": [round(at[0], 3), round(at[1], 3)]}
            for net, (gap, at) in sorted(nearest.items(), key=lambda item: (item[1][0], item[0]))]
    return {"bared": sorted(net for net, (gap, _at) in nearest.items() if gap == 0.0), "near": near}


# ------------------------------------------------------------------ the grid


class _Grid:
    def __init__(self, box: tuple[float, float, float, float], cell: float) -> None:
        self.x0, self.y0 = box[0], box[1]
        self.cell = cell
        self.nx = max(1, int(math.ceil((box[2] - box[0]) / cell)))
        self.ny = max(1, int(math.ceil((box[3] - box[1]) / cell)))

    def new(self) -> bytearray:
        return bytearray(self.nx * self.ny)

    def centre(self, col: int, row: int) -> Point:
        return self.x0 + (col + 0.5) * self.cell, self.y0 + (row + 0.5) * self.cell

    def fill_rings(self, cells: bytearray, rings: list[Ring]) -> None:
        """Mark the cells whose centre lies in the region (even-odd over all its rings)."""
        edges = [(ring[i - 1], ring[i]) for ring in rings for i in range(len(ring))]
        top = self.y0 + self.ny * self.cell
        edges = [(a, b) for a, b in edges if not (max(a[1], b[1]) < self.y0 or min(a[1], b[1]) > top) and a[1] != b[1]]
        for row in range(self.ny):
            y = self.y0 + (row + 0.5) * self.cell
            xs = sorted(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for a, b in edges if (a[1] > y) != (b[1] > y))
            for k in range(0, len(xs) - 1, 2):
                first = max(0, int(math.ceil((xs[k] - self.x0) / self.cell - 0.5)))
                last = min(self.nx - 1, int(math.floor((xs[k + 1] - self.x0) / self.cell - 0.5)))
                for col in range(first, last + 1):
                    cells[row * self.nx + col] = 1

    def fill_line(self, cells: bytearray, line: list[Point], width: float,
                  near: tuple[float, float, float, float] | None = None) -> None:
        half = width / 2
        for a, b in zip(line, line[1:], strict=False):
            if near is not None and _gap(near, _box([a, b])) > half:
                continue
            self._fill_near(cells, (min(a[0], b[0]) - half, min(a[1], b[1]) - half, max(a[0], b[0]) + half, max(a[1], b[1]) + half),
                            lambda p, a=a, b=b: math.dist(p, _closest(p, a, b)) <= half)

    def fill_disc(self, cells: bytearray, centre: Point, radius: float) -> None:
        self._fill_near(cells, (centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius),
                        lambda p: math.dist(p, centre) <= radius)

    def _fill_near(self, cells: bytearray, box: tuple[float, float, float, float], hit) -> None:
        c0 = max(0, int(math.floor((box[0] - self.x0) / self.cell)))
        c1 = min(self.nx - 1, int(math.floor((box[2] - self.x0) / self.cell)))
        r0 = max(0, int(math.floor((box[1] - self.y0) / self.cell)))
        r1 = min(self.ny - 1, int(math.floor((box[3] - self.y0) / self.cell)))
        for row in range(r0, r1 + 1):
            for col in range(c0, c1 + 1):
                if not cells[row * self.nx + col] and hit(self.centre(col, row)):
                    cells[row * self.nx + col] = 1

    def grown(self, cells: bytearray, reach: float) -> bytearray:
        """The marked cells grown by ``reach``: every cell within it of a marked one, measured by a
        3-4 chamfer distance in two passes (a step across counts 3, a diagonal one 4), with the
        reach widened by the 6 % that distance can read long, so that no cell within it is missed."""
        nx, ny = self.nx, self.ny
        far = 1 << 30
        d = [0 if value else far for value in cells]
        for row in range(ny):
            base = row * nx
            for col in range(nx):
                i = base + col
                v = d[i]
                if not v:
                    continue
                if col and d[i - 1] + 3 < v:
                    v = d[i - 1] + 3
                if row:
                    up = i - nx
                    if d[up] + 3 < v:
                        v = d[up] + 3
                    if col and d[up - 1] + 4 < v:
                        v = d[up - 1] + 4
                    if col + 1 < nx and d[up + 1] + 4 < v:
                        v = d[up + 1] + 4
                d[i] = v
        for row in range(ny - 1, -1, -1):
            base = row * nx
            for col in range(nx - 1, -1, -1):
                i = base + col
                v = d[i]
                if not v:
                    continue
                if col + 1 < nx and d[i + 1] + 3 < v:
                    v = d[i + 1] + 3
                if row + 1 < ny:
                    down = i + nx
                    if d[down] + 3 < v:
                        v = d[down] + 3
                    if col + 1 < nx and d[down + 1] + 4 < v:
                        v = d[down + 1] + 4
                    if col and d[down - 1] + 4 < v:
                        v = d[down - 1] + 4
                d[i] = v
        limit = 3 * reach / self.cell * 1.06
        return bytearray(1 if value <= limit else 0 for value in d)

    def shrunk(self, cells: bytearray) -> bytearray:
        """The marked cells whose four neighbours are marked too."""
        out = self.new()
        nx = self.nx
        for row in range(1, self.ny - 1):
            for col in range(1, nx - 1):
                i = row * nx + col
                if cells[i] and cells[i - 1] and cells[i + 1] and cells[i - nx] and cells[i + nx]:
                    out[i] = 1
        return out


def _largest(grid: _Grid, cells: bytearray, min_cells: int = 1) -> tuple[int, int, int, int] | None:
    """The largest rectangle of marked cells with both sides at least ``min_cells`` long: first
    column, first row, last column, last row. Each rectangle as tall as a column of cells reaches
    and as wide as it stays that tall is tried, which holds every rectangle at its largest."""
    heights = [0] * grid.nx
    best, found = 0.0, None
    for row in range(grid.ny):
        for col in range(grid.nx):
            heights[col] = heights[col] + 1 if cells[row * grid.nx + col] else 0
        stack: list[int] = []
        for col in range(grid.nx + 1):
            height = heights[col] if col < grid.nx else 0
            while stack and heights[stack[-1]] >= height:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                area = heights[top] * (col - left)
                if heights[top] >= min_cells and col - left >= min_cells and area > best:
                    best, found = area, (left, row - heights[top] + 1, col - 1, row)
            stack.append(col)
    return found


def fit(region: Ring, copper: LayerCopper, net: str, margin: float, min_side: float = MIN_SIDE_MM,
        avoid: list[tuple[float, float, float, float]] | None = None) -> tuple[float, ...] | None:
    """The largest rectangle (x1, y1, x2, y2) inside the region that lies on the net's copper and
    keeps the margin from every other net's copper and from the boxes to ``avoid``, or None where
    none has sides of ``min_side``.

    The rectangle runs through the centres of the grid's outer cells it holds. Every other net's
    copper marks each cell within half a cell's diagonal of it, so that a trace thinner than a cell
    is not missed, and those cells grow by the margin and a cell and a half: a point of the
    rectangle lies within half a diagonal of a cell it holds, so the margin holds. The grid reaches
    past the region by the margin and two cells, for copper just outside it."""
    box = _box(region)
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)
    cell = max(CELL_MM, math.sqrt(area / MAX_CELLS))
    slack = margin + 2 * cell
    reach = (box[0] - slack, box[1] - slack, box[2] + slack, box[3] + slack)
    grid = _Grid(reach, cell)
    half = cell * math.sqrt(2) / 2
    within, own, other = grid.new(), grid.new(), grid.new()
    grid.fill_rings(within, [region])
    for name, rings in copper.regions:
        if _gap(reach, _box(rings[0])) > 0:
            continue
        if name == net:
            grid.fill_rings(own, rings)
            continue
        grid.fill_rings(other, rings)
        for ring in rings:  # a sliver thinner than a cell
            grid.fill_line(other, [*ring, ring[0]], 2 * half, near=reach)
    for name, width, line in copper.traces:
        if name == net:
            grid.fill_line(own, line, width, near=reach)
        else:
            grid.fill_line(other, line, width + 2 * half, near=reach)
    for name, centre, radius in copper.vias:
        if name == net:
            grid.fill_disc(own, centre, radius)
        else:
            grid.fill_disc(other, centre, radius + half)
    for zone in avoid or []:
        grid.fill_rings(other, [rect_ring(zone)])
        grid.fill_line(other, [*rect_ring(zone), (zone[0], zone[1])], 2 * half, near=reach)
    clear = grid.grown(other, margin + 1.5 * cell)
    on = grid.shrunk(own)
    cells = bytearray(1 if within[i] and on[i] and not clear[i] else 0 for i in range(len(within)))
    found = _largest(grid, cells, int(math.ceil(min_side / grid.cell)) + 1)
    if found is None:
        return None
    c0, r0, c1, r1 = found
    (x1, y1), (x2, y2) = grid.centre(c0, r0), grid.centre(c1, r1)  # the rectangle through the outer cells' centres
    return math.ceil(x1 * 1000) / 1000, math.ceil(y1 * 1000) / 1000, math.floor(x2 * 1000) / 1000, math.floor(y2 * 1000) / 1000


def rect_ring(rect: tuple[float, ...]) -> Ring:
    x1, y1, x2, y2 = rect
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
