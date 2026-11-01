from __future__ import annotations

import copy
import json

import pytest
from fakes import LIBRARY_PARTS

from xpedition_cli import library_parts as P
from xpedition_cli import library_read as R
from xpedition_cli import library_readback as proof


@pytest.mark.parametrize("fault", [None, "missing", "symbol", "drill", "export"])
def test_definition_readback_rejects_incomplete_or_changed_native_records(
    tmp_path, monkeypatch, fault
):
    spec = copy.deepcopy(LIBRARY_PARTS)
    path = tmp_path / "parts.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    expected = P.plan(spec)
    texts = expected.texts()
    actual = R.Library()
    actual.parts = R.parse_parts(texts["parts"], expected.partition)
    actual.cells = R.parse_cells(texts["cells"], expected.partition)
    actual.padstacks = R.parse_padstacks(texts["padstacks"])
    actual.symbols = [
        R.parse_symbol(text, expected.partition, name) for name, text in expected.symbols.items()
    ]
    if fault == "missing":
        actual.parts.clear()
    elif fault == "symbol":
        actual.symbols.clear()
    elif fault == "drill":
        actual.padstacks["pads"].clear()
    metadata = {"failed": [{"error": "fixture failed"}] if fault == "export" else [], "files": []}

    def read(options, project, **kwargs):
        assert kwargs["fresh"] is True
        return actual, metadata

    monkeypatch.setattr(proof, "load_library", read)
    observed = proof.verify_definitions(
        "library create", {"project": str(tmp_path / "P.prj"), "file": str(path)}
    )
    assert observed["valid"] is (fault is None), observed
