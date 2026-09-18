"""Reproducible local-query and reference-size measurements; no native calls.

Run from a full checkout: python scripts/benchmark-agent-reads.py
Timings exclude process startup, native snapshot acquisition and JSON output.
Byte counts describe compact reference data, not measured model tokens.
"""

from __future__ import annotations

import ast
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from xpedition_cli import main as cli  # noqa: E402
from xpedition_cli.reference_data import reference  # noqa: E402

BASE = "d42b226a5b1812b2937ded096bf618b8692c4f9a"
raw = subprocess.check_output(["git", "show", f"{BASE}:xpedition_cli/main.py"], encoding="utf-8")
nodes = [
    node
    for node in ast.parse(raw).body
    if isinstance(node, ast.FunctionDef) and node.name in {"_list_data", "_query_matches"}
]
assert len(nodes) == 2
namespace = dict(vars(cli))
exec(compile(ast.Module(body=nodes, type_ignores=[]), "pinned_list_data", "exec"), namespace)
original = namespace["_list_data"]
project = {"project": "synthetic", "revision": "R00"}
results = []
for count in (1000, 10000):
    items = [{"refdes": f"R{i}", "value": "QUERY_MATCH"} for i in range(count)]
    for query in (None, "QUERY_MATCH", "NO_MATCH"):
        options = {"query": query, "limit": 1, "offset": 0}
        assert original(project, items, options) == cli._list_data(project, items, options)
        samples = {}
        for name, function in (("baseline", original), ("patched", cli._list_data)):
            timings = []
            for _ in range(5):
                started = time.perf_counter()
                function(project, items, options)
                timings.append((time.perf_counter() - started) * 1000)
            samples[name] = timings
        results.append(
            {
                "records": count,
                "query": query,
                "limit": 1,
                "baseline_median_ms": statistics.median(samples["baseline"]),
                "patched_median_ms": statistics.median(samples["patched"]),
                "samples_ms": samples,
            }
        )

sizes = []
for selectors in ({}, {"command": "pcb move"}, {"domain": "schematic"}, {"schema": "context"}):
    data = reference(**selectors)
    sizes.append(
        {
            "selectors": selectors,
            "commands": len(data["commands"]),
            "schemas": len(data["schemas"]),
            "compact_data_bytes": len(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ),
        }
    )
print(
    json.dumps(
        {
            "baseline_commit": BASE,
            "python": sys.version,
            "platform": platform.platform(),
            "scope": "synthetic in-process query filtering and compact reference data bytes",
            "query_results": results,
            "reference_sizes": sizes,
            "tested_source_sha256": {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((ROOT / "xpedition_cli").rglob("*.py"))
            },
        },
        ensure_ascii=False,
        indent=2,
    )
)
