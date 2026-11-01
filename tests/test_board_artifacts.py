"""Public SDK artifact acceptance, with explicit synthetic native geometry."""

import copy
import hashlib
import json

import pytest
from PIL import Image, PngImagePlugin
from test_driver_v2 import _scope, _worker_fixture

from xpedition_cli.cli import pcb
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("kind", ["snapshot", "render"])
@pytest.mark.parametrize(
    "fault", [None, "old", "wrong_project", "empty", "corrupt", "failed", "wrong_geometry"]
)
def test_public_artifact_requires_fresh_decodable_current_geometry(
    tmp_path, monkeypatch, project, kind, fault
):
    _scope(tmp_path, monkeypatch)
    model = {
        "layers": 4,
        "pads": [],
        "components": [],
        "outline": [[0, 0], [10, 0], [10, 10], [0, 10]],
    }
    output = tmp_path / ("board.json" if kind == "snapshot" else "board.png")
    generated = []

    class Backend:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "snapshot":
                return {"components": [], "fixture": True}
            assert method in {"board_geometry", "render_board"}
            if not params.get("output"):
                return {"model": copy.deepcopy(model), "pcb": str(project.with_suffix(".pcb"))}
            if fault == "failed":
                raise CLIError("E_NATIVE_FAILURE", "synthetic generation failure")
            generated.append(method)
            provenance = {
                "generation_id": params.get("artifact_generation", "legacy"),
                "project": str(project.resolve()),
                "pcb": str(project.with_suffix(".pcb").resolve()),
                "source_geometry_sha256": digest(model),
                "kind": kind,
                "view": {"side": "top", "refdes": [], "scale": 20},
            }
            if fault == "old":
                provenance["generation_id"] = "earlier-operation"
            if fault == "wrong_project":
                provenance["project"] = str(project.with_name("wrong.prj"))
            if fault == "wrong_geometry":
                provenance["source_geometry_sha256"] = "0" * 64
            if kind == "snapshot":
                output.write_text(json.dumps({**model, "_provenance": provenance}))
            else:
                metadata = PngImagePlugin.PngInfo()
                metadata.add_text("xpedition", json.dumps(provenance))
                Image.new("RGB", (16, 16), "green").save(output, pnginfo=metadata)
            if fault in {"empty", "corrupt"}:
                output.write_bytes(b"" if fault == "empty" else b"corrupt")
            return {
                "path": str(output),
                "artifact": {
                    **provenance,
                    "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                },
                "pcb": str(project.with_suffix(".pcb")),
            }

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = ["pcb", kind, "--project", str(project), "--output", str(output)]
    with runtime(Adapter(backend), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        assert not generated and not output.exists()
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    if fault is None:
        assert result.envelope["ok"], result.envelope
        receipt = result.envelope["data"]["receipt"]
        assert receipt["outcome"]["verification"]["status"] == "passed"
        assert receipt["outcome"]["persistence"] == "unchanged"
    else:
        assert not result.envelope["ok"], result.envelope
