"""Existing-pad resizing preserves the cell source and refuses shared changes."""

import copy

import pytest
from test_library_parts import RES as BASE_RES
from test_library_parts import _held as base_held
from test_library_parts import _spec

from xpedition_cli import library_parts as P
from xpedition_cli import library_read as R

RES = copy.deepcopy(BASE_RES)
RES["footprint"] = {
    "name": "RECT_FIXTURE",
    "height": 0.6,
    "pads": [
        {"pin": str(i), "x": x, "y": 0, "width": 1.025, "height": 1.4, "shape": "rect"}
        for i, x in [(1, -0.9125), (2, 0.9125)]
    ],
}


def _held(spec):
    library = base_held(spec)
    library.texts = {(key, spec["partition"]): text for key, text in P.plan(spec).texts().items()}
    return library


def test_pad_resize_preserves_existing_cell_and_pin_mapping():
    existing = _held(_spec(RES))
    cell = existing.cells[0]
    spec = _spec(RES)
    spec["parts"][0]["footprint"] = {
        "cell": cell["name"],
        "pad_dimensions": {
            p["number"]: {"copper": [1.225, 1.4], "mask": [1.325, 1.5], "paste": [1.225, 1.4]}
            for p in cell["pins"]
        },
    }
    before = copy.deepcopy(existing)
    plan = P.plan(spec, existing)
    texts = plan.texts()
    assert R.parse_cells(texts["cells"], spec["partition"]) == existing.cells
    assert not texts["parts"] and not plan.symbols
    assert plan.actions["cells"][0]["action"] == "refresh_dependencies"
    assert plan.replaces and all(r.startswith("pad ") for r in plan.replaces)
    parsed = R.parse_padstacks(texts["padstacks"])
    assert parsed["pads"] and parsed["padstacks"] == existing.padstacks["padstacks"]
    assert existing == before
    for name, row in parsed["pads"].items():
        assert name in existing.padstacks["pads"] and row["width"] in (1.225, 1.325)
    # Actual readback of dimension values; a repeated request has no replacement.
    existing.padstacks["pads"].update(parsed["pads"])
    assert not P.plan(spec, existing).replaces


@pytest.mark.parametrize("override", [False, True])
def test_readback_can_verify_existing_cell_dimensions(tmp_path, monkeypatch, override):
    import json

    from xpedition_cli import library_readback

    existing = _held(_spec(RES))
    cell = existing.cells[0]
    spec = _spec(RES)
    spec["parts"][0]["footprint"] = {
        "cell": cell["name"],
        "pad_dimensions": {
            p["number"]: {"copper": [1.225, 1.4], "mask": [1.325, 1.5], "paste": [1.225, 1.4]}
            for p in cell["pins"]
        },
    }
    desired = P.plan(spec, existing)
    existing.padstacks["pads"].update(R.parse_padstacks(desired.texts()["padstacks"])["pads"])
    options = {"project": str(tmp_path / "test.prj")}
    if override:
        options["partition"] = spec["partition"]
        spec["partition"] = "OtherInputPartition"
    path = tmp_path / "parts.json"
    path.write_text(json.dumps(spec))
    monkeypatch.setattr(
        library_readback, "load_library", lambda *a, **k: (existing, {"failed": [], "files": []})
    )
    result = library_readback.verify_definitions("library create", {**options, "file": str(path)})
    assert result["valid"], result


@pytest.mark.parametrize("fault", ["other_cell", "missing_pin", "conflict", "oblong"])
def test_pad_resize_rejects_undeclared_or_unsupported_changes(fault):
    existing = _held(_spec(RES))
    cell = existing.cells[0]
    spec = _spec(RES)
    sizes = {
        p["number"]: {"copper": [1.225, 1.4], "mask": [1.325, 1.5], "paste": [1.225, 1.4]}
        for p in cell["pins"]
    }
    spec["parts"][0]["footprint"] = {"cell": cell["name"], "pad_dimensions": sizes}
    if fault == "other_cell":
        existing.cells.append({**cell, "name": "protected_other_cell"})
    elif fault == "missing_pin":
        sizes.pop(next(iter(sizes)))
    elif fault == "conflict":
        sizes[next(iter(sizes))]["paste"] = [1.4, 1.4]
    else:
        for shape in existing.padstacks["pads"].values():
            shape["shape"] = "OBLONG"
    with pytest.raises(P.PartsFileError):
        P.plan(spec, existing)
