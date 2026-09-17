"""Compare the pinned original checker with the working tree, using synthetic data.

Run from a git checkout: python scripts/benchmark-routing.py
No Xpedition, real project, or geometry file is read. Timings are microbenchmarks,
not native CLI measurements. Differential assertions protect finding order as well
as contents; CI uses deterministic operation-count tests for complexity.
"""

from __future__ import annotations

import ast
import json
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xpedition_cli import routing_plan as current

BASE = "2a66f2dc52c95a7165c9cc675ed01b0c65d8e7b5"
raw = subprocess.check_output(["git", "show", f"{BASE}:xpedition_cli/routing_plan.py"], text=True)
original_tree = ast.parse(raw)
function = next(
    node
    for node in original_tree.body
    if isinstance(node, ast.FunctionDef) and node.name == "check_plan"
)
namespace = dict(vars(current))
exec(compile(ast.Module(body=[function], type_ignores=[]), "pinned_check_plan", "exec"), namespace)
original = namespace["check_plan"]

rng = random.Random(8675309)
for case in range(200):
    board = current.Board({})
    board.segments = [
        (
            (rng.uniform(0, 10), rng.uniform(0, 10)),
            (rng.uniform(0, 10), rng.uniform(0, 10)),
            rng.choice((1, 2)),
            rng.choice(("A", "B", "GND")),
            0.254,
            -1,
        )
        for _ in range(8)
    ]
    board.vias = [
        ((rng.uniform(0, 10), rng.uniform(0, 10)), rng.choice(("A", "B")), -1) for _ in range(3)
    ]
    board.pads = [((1, 1, 2, 2), "A", 1, "U1"), ((4, 4, 5, 5), "B", 2, "U2")]
    items = [
        {
            "net": rng.choice(("A", "B", "GND")),
            "layer": rng.choice((1, 2)),
            "points": [
                (rng.uniform(0, 10), rng.uniform(0, 10)),
                (rng.uniform(0, 10), rng.uniform(0, 10)),
            ],
        }
        for _ in range(4)
    ]
    items += [
        {
            "kind": "via",
            "net": rng.choice(("A", "B")),
            "at": (rng.uniform(0, 10), rng.uniform(0, 10)),
        }
        for _ in range(2)
    ]
    assert current.check_plan(items, board) == original(items, board), (
        f"Differential case {case} changed"
    )

rows = []
for count in (1000, 3000, 10000):
    board = current.Board({})
    board.segments = [((0, float(i)), (1, float(i)), 1, "SAME", 0.254, -1) for i in range(count)]
    items = [{"net": "SAME", "layer": 1, "points": [(0, 0), (1, 0)]}]
    samples = {}
    for name, function in (("baseline", original), ("patched", current.check_plan)):
        timings = []
        for _ in range(5):
            started = time.perf_counter()
            assert function(items, board) == []
            timings.append((time.perf_counter() - started) * 1000)
        samples[name] = timings
    before, after = (statistics.median(samples[key]) for key in ("baseline", "patched"))
    rows.append(
        {
            "existing_segments": count,
            "new_segments": 1,
            "baseline_median_ms": before,
            "patched_median_ms": after,
            "speedup": before / after,
            "samples_ms": samples,
        }
    )
print(
    json.dumps(
        {
            "baseline_commit": BASE,
            "python": sys.version,
            "platform": platform.platform(),
            "fixture": "same-net, no pads/vias; loop overhead only",
            "differential_cases": 200,
            "results": rows,
        },
        indent=2,
    )
)
