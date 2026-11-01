"""Validate board artifacts independently of the producer's acknowledgement."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .cell_refresh import digest


def provenance(params, board, model, kind):
    return {
        "generation_id": params.get("artifact_generation"),
        "project": str(Path(params["project"]).resolve()),
        "pcb": str(Path(board).resolve()),
        "kind": kind,
        "source_geometry_sha256": digest(model),
        "units": "mm",
    }


def finish(path, binding):
    return {**binding, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def verify(command, options, result, observation, generation_id):
    """Decode fresh bytes and compare their binding against another native read."""
    path = Path(options["output"]).resolve()
    checks = []

    def check(name, passed):
        checks.append({"id": name, "passed": bool(passed)})

    try:
        from PIL import Image

        data = path.read_bytes()
        check("nonempty", bool(data))
        check(
            "producer-byte-hash",
            hashlib.sha256(data).hexdigest() == result.get("artifact", {}).get("sha256"),
        )
        if command == "pcb snapshot":
            model = json.loads(data)
            binding = model.pop("_provenance")
            check("snapshot-geometry", model == observation["model"])
        else:
            with Image.open(path) as picture:
                check("png-format", picture.format == "PNG")
                binding = json.loads(picture.info["xpedition"])
                picture.verify()
            with Image.open(path) as picture:
                picture.load()  # Decode every pixel, not just the header.
                check("png-pixels", picture.width > 0 and picture.height > 0)
            from .cli.common import comma_list

            view = binding.get("view") or {}
            check("render-side", view.get("side") == (options.get("side") or "top"))
            check("render-selection", view.get("refdes") == comma_list(options, "refdes"))
            if options.get("scale") is not None:
                check("render-scale", view.get("scale") == float(options["scale"]))
        check("current-generation", binding.get("generation_id") == generation_id)
        check(
            "requested-project",
            Path(binding["project"]).resolve() == Path(options["project"]).resolve(),
        )
        check(
            "observed-board", Path(binding["pcb"]).resolve() == Path(observation["pcb"]).resolve()
        )
        check("artifact-kind", binding.get("kind") == command.split()[1])
        check(
            "current-geometry",
            binding.get("source_geometry_sha256") == digest(observation["model"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        checks.append({"id": "artifact-decode", "passed": False, "error": type(error).__name__})
    return {
        "valid": bool(checks) and all(c["passed"] for c in checks),
        "checks": checks,
        "source": "independently reopened bytes and fresh native geometry",
        "scope": "artifact identity, freshness, integrity and current geometry binding",
        "not_checked": ["design intent", "native DRC", "visual acceptance"],
    }
