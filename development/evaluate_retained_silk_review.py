"""Recompute offline draft evidence from hash-bound retained native observations.

No CAD, SDK, operation journal, restricted vendor input or project is opened.
The six Place frames contain text/pose observations only; they cannot establish
intermediate full-board copper protection. The Move frames contain full states.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xpedition_cli import placement  # noqa: E402
from xpedition_cli import placement_silk_equivalence as draft  # noqa: E402
from xpedition_cli import placement_text_witness as witness  # noqa: E402


def main():
    root, destination = (Path(arg).absolute() for arg in sys.argv[1:])
    if destination.exists():
        raise SystemExit("Evidence exists; do not overwrite a previous evaluation")
    inputs = {}

    def load(relative):
        path = root / relative
        data = path.read_bytes()
        inputs[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        return json.loads(data)

    for name in ("source-precision-manifest-01.json", "component-move-manifest-01.json"):
        manifest = load("evidence/" + name)
        for row in manifest["artifacts"]:
            if row["path"].startswith(
                (
                    "evidence/source-precision-moves-01/phase-",
                    "evidence/component-move-proof-01/phase-",
                )
            ):
                data = (root / row["path"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != row["sha256"] or len(data) != row["bytes"]:
                    raise SystemExit("Retained native frame differs from its original manifest")

    move_before = load("evidence/component-move-proof-01/phase-00.json")
    move_after = load("evidence/component-move-proof-01/phase-01.json")
    plan = placement.plan_placement(
        move_after["request"],
        [move_before["pose"]],
        protection_state=move_before["protected_material"],
    )
    strict = placement.verify_batch_state(plan, move_after["protected_material"])
    plan["state_guard"] = draft.DRAFT_GUARD
    full = placement.verify_batch_state(plan, move_after["protected_material"])
    if strict["valid"] or not full["valid"]:
        raise SystemExit("Unexpected retained Move comparison; preserve the failure")
    phases = []
    previous = load("evidence/source-precision-moves-01/phase-00.json")
    for index in range(1, 7):
        frame = load(f"evidence/source-precision-moves-01/phase-{index:02}.json")
        uid = next(
            uid for uid, row in frame["texts"].items() if row["raw"]["refdes"] == frame["name"]
        )
        expected = witness.transform(previous["texts"][uid]["raw"], frame["old"], frame["target"])
        observed = frame["texts"][uid]["raw"]
        want, got = expected["stroke_array_mm"], observed["stroke_array_mm"]
        residuals = []
        for i in range(1, len(want), 3):
            delta = [Fraction(got[i + axis]) - Fraction(want[i + axis]) for axis in (0, 1)]
            if not draft._within_vertex_budget(*delta):
                raise SystemExit("Retained Place sample exceeds fixed draft budget")
            residuals.append([[v.numerator, v.denominator] for v in delta])
        extents = [
            Fraction(a) - Fraction(e)
            for a, e in zip(observed["extents_mm"], expected["extents_mm"], strict=True)
        ]
        if any(abs(value) > Fraction(1, 100000) for value in extents):
            raise SystemExit("Retained Place envelope exceeds fixed draft budget")
        semantic_expected = witness.transform_semantics(
            previous["texts"][uid]["semantics"], frame["old"], frame["target"]
        )
        semantic_diff = witness.semantic_difference(
            [semantic_expected],
            [frame["texts"][uid]["semantics"]],
            [previous["texts"][uid]["semantics"]],
            [
                {
                    "id": frame["name"],
                    "changed": True,
                    "before": frame["old"],
                    "target": frame["target"],
                }
            ],
        )
        if semantic_diff is not None or any(
            previous["texts"][key] != value for key, value in frame["texts"].items() if key != uid
        ):
            raise SystemExit("Retained Place semantic or non-target text change")
        phases.append(
            {
                "step": index,
                "refdes": frame["name"],
                "direction": frame["direction"],
                "point_residuals_mm_fraction": residuals,
                "extent_residuals_mm_fraction": [[v.numerator, v.denominator] for v in extents],
                "topology": draft._topology(want, got),
                "semantic_difference": semantic_diff,
                "scope": "text and poses only; no intermediate complete "
                "copper/connectivity observation",
            }
        )
        previous = copy.deepcopy(frame)
    for name, record in inputs.items():
        data = (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise SystemExit("Retained input changed during evaluation")
    destination.write_text(
        json.dumps(
            {
                "kind": "offline evaluation of retained native evidence; no new native acceptance",
                "contract": draft.CONTRACT,
                "source_inputs": inputs,
                "full_move_strict_v5": strict,
                "full_move_draft_geometry": full,
                "place_frames": phases,
                "clearance": "unverified",
                "public_apply_enabled": False,
                "old_operation_modified": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "move_geometry_valid": full["valid"],
                "place_frames": len(phases),
                "qualification": full["qualification"],
                "output": str(destination),
            }
        )
    )


if __name__ == "__main__":
    main()
