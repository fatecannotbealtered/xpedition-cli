"""Compare reopened native library exports with the bound write inputs."""

from __future__ import annotations

from pathlib import Path

from . import library_import as I
from . import library_parts as P
from . import library_read as R
from .cli.library import _library_path, load_library


def verify_definitions(command, options):
    """Read native database files anew; never accept a setter acknowledgement."""
    project = Path(options["project"]).resolve()
    actual, metadata = load_library(options, project, fresh=True, keep_texts=True)
    differences = []
    if command == "library create":
        spec = P.load(Path(options["file"]))
        if options.get("partition"):
            spec = {**spec, "partition": str(options["partition"]).strip()}
        # Referenced cells/pads are inputs, while symbols/parts must still be
        # independently generated from the request for comparison.
        expected = P.plan(
            spec,
            R.Library(
                parts=actual.parts,
                cells=actual.cells,
                padstacks=actual.padstacks,
                texts=actual.texts,
                failed=actual.failed,
            ),
        )
        compared = P.plan(spec, actual)
        actions = compared.actions
        differences.extend(compared.replaces)
        for name, text in expected.symbols.items():
            reference = f"{expected.partition}:{name}"
            symbol = R.parse_symbol(text, expected.partition, name)
            if I._symbol_content(symbol) != I._symbol_content(actual.find_symbol(reference)):
                differences.append("symbol " + reference)
        stacks = R.parse_padstacks(expected.texts()["padstacks"])
        for kind, table in stacks.items():
            for name, definition in table.items():
                if not _equivalent(definition, actual.padstacks.get(kind, {}).get(name)):
                    differences.append(f"{kind} {name}")
        numbers = [part["number"] for part in expected.parts]
    elif command == "library import":
        source, source_meta = load_library(
            options,
            None,
            lmc=_library_path(options, "from", command),
            fresh=True,
            keep_texts=True,
        )
        metadata["failed"] += source_meta["failed"]
        numbers = I.numbers(str(options["parts"]).split(","))
        compared = I.plan(source, actual, numbers)
        actions = {
            kind: getattr(compared, kind) for kind in ("parts", "symbols", "cells", "padstacks")
        }
    else:
        raise ValueError("No native library readback for " + command)
    differences.extend(
        f"{kind} {item.get('number') or item.get('reference') or item.get('name')}"
        for kind, items in actions.items()
        for item in items
        if item["action"] not in {"keep", "use"}
    )
    findings = [
        finding
        for number in numbers
        for row in actual.find_parts(number)
        for finding in R.check_part(actual, row)
    ]
    valid = (
        not differences
        and not metadata["failed"]
        and not any(finding["severity"] == "high" for finding in findings)
    )
    return {
        "valid": valid,
        "expected": {"parts": numbers, "actions": "all definitions present and identical"},
        "observed": {
            "actions": actions,
            "differences": sorted(set(differences)),
            "failed_exports": metadata["failed"],
            "findings": findings,
            "export_files": metadata["files"],
            "parts": [row for number in numbers for row in actual.find_parts(number)],
            "registration": metadata.get("registration", {"status": "not_observed"}),
        },
        "source": "fresh native database converters and independently read symbol files",
        "units": "mm",
        "tolerance_mm": 0.001,
        "not_checked": [
            "LMC partition registration/commit and packager consumption",
            "PCB instances and their synchronization",
            "CAM/DFM",
        ],
    }


def _equivalent(expected, actual):
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and expected.keys() == actual.keys()
            and all(_equivalent(value, actual[key]) for key, value in expected.items())
        )
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(actual, (list, tuple))
            and len(expected) == len(actual)
            and all(_equivalent(left, right) for left, right in zip(expected, actual, strict=True))
        )
    if type(expected) in {int, float} and type(actual) in {int, float}:
        return abs(expected - actual) < 0.001
    return expected == actual
