"""Offline preview bytes are delivery evidence, not CAD engineering acceptance."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from test_driver_v2 import _scope

from xpedition_cli.driver import runtime
from xpedition_cli.errors import CLIError
from xpedition_cli.preview_artifacts import expectation, verify


def _observed(paths):
    return [
        {
            "path": str(path),
            "exists": path.exists(),
            **(
                {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": path.stat().st_size,
                }
                if path.exists()
                else {}
            ),
        }
        for path in paths
    ]


def _fixture(tmp_path):
    output = tmp_path / "parts.png"
    expected = {
        "output": str(output),
        "images": [
            {"path": str(tmp_path / f"parts-{part}.png"), "part": part} for part in ("R", "C")
        ],
    }
    paths = [Path(item["path"]) for item in expected["images"]]
    for path in paths:
        Image.new("RGB", (24, 16), "white").save(path)
    result = {
        "pictures": [{**item, "width": 24, "height": 16} for item in expected["images"]],
        "count": 2,
    }
    return output, expected, paths, result


def test_multi_part_without_basename_is_valid_independent_png_delivery(tmp_path):
    output, expected, paths, result = _fixture(tmp_path)
    report = verify({"output": str(output)}, result, expected, [], _observed(paths))
    assert not output.exists()  # This is the shape of the retained native R4 failure.
    assert report["valid"]
    assert len(report["images"]) == 2
    assert "native library or CAD engineering acceptance" in report["not_checked"]


@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "duplicate",
        "extra",
        "missing-return",
        "escape",
        "relative",
        "alias",
        "missing-file",
        "corrupt",
        "truncated-pixels",
        "wrong-format",
        "wrong-size",
        "wrong-hash",
        "wrong-identity",
        "unchanged-file",
        "unexpected-sibling",
        "no-plan",
        "wrong-count",
    ],
)
def test_independent_preview_rejects_missing_corrupt_or_unbound_outputs(tmp_path, fault):
    output, expected, paths, result = _fixture(tmp_path)
    before = []
    if fault == "empty":
        result["pictures"] = []
    elif fault == "duplicate":
        result["pictures"].append(copy.deepcopy(result["pictures"][0]))
    elif fault == "extra":
        result["pictures"].append({"path": str(tmp_path / "parts-extra.png")})
    elif fault == "missing-return":
        result["pictures"].pop()
    elif fault == "escape":
        result["pictures"][0]["path"] = str(tmp_path.parent / paths[0].name)
    elif fault == "relative":
        result["pictures"][0]["path"] = paths[0].name
    elif fault == "alias":
        result["pictures"][0]["path"] = str(tmp_path / ".." / tmp_path.name / paths[0].name)
    elif fault == "missing-file":
        paths[0].unlink()
    elif fault == "corrupt":
        paths[0].write_bytes(b"not a PNG")
    elif fault == "truncated-pixels":
        paths[0].write_bytes(paths[0].read_bytes()[:-20])
    elif fault == "wrong-format":
        Image.new("RGB", (24, 16)).save(paths[0], "JPEG")
    elif fault == "wrong-size":
        result["pictures"][0]["width"] = 25
    elif fault == "wrong-identity":
        result["pictures"][0]["part"] = "other"
    elif fault == "unchanged-file":
        before = _observed([paths[0]])
    elif fault == "unexpected-sibling":
        extra = tmp_path / "parts-extra.png"
        Image.new("RGB", (24, 16)).save(extra)
        paths.append(extra)
    elif fault == "wrong-count":
        result["count"] = 9
    after = _observed(paths)
    if fault == "wrong-hash":
        after[0]["sha256"] = "0" * 64
    if fault == "no-plan":
        expected = None
    report = verify({"output": str(output)}, result, expected, before, after)
    assert not report["valid"], report
    assert any(not check["passed"] for check in report["checks"])


def test_frozen_inventory_rejects_sanitized_library_filename_collisions(tmp_path):
    path = tmp_path / "parts.json"
    path.write_text(json.dumps({"parts": [{"number": "a:b"}, {"number": "a?b"}]}))
    with pytest.raises(CLIError, match="collide"):
        expectation("library preview", {"file": str(path), "output": str(tmp_path / "x.png")}, {})


def _preview_input(tmp_path, kind):
    source = Path(__file__).resolve().parents[1]
    if kind == "library":
        from fakes import LIBRARY_PARTS

        path = tmp_path / "parts.json"
        path.write_text(json.dumps(LIBRARY_PARTS), encoding="utf-8")
        return ["library", "preview", "--file", str(path)], path
    path = tmp_path / "design.json"
    path.write_bytes((source / "examples/demo-sensor-board.json").read_bytes())
    return ["schematic", "plan", "preview", "--design", str(path)], path


@pytest.mark.parametrize("kind", ["library", "schematic"])
def test_public_runtime_preview_receipt_requires_only_independent_file_delivery(
    tmp_path, monkeypatch, kind
):
    _scope(tmp_path, monkeypatch)
    args, _ = _preview_input(tmp_path, kind)
    args += ["--output", str(tmp_path / "preview.png")]
    with runtime(state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        frozen = preview.envelope["data"]["preview"]["extensions"]["preview_artifacts"]
        assert frozen["images"]
        assert not list(tmp_path.glob("*.png"))
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert result.envelope["ok"], result.envelope
    receipt = result.envelope["data"]["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    assert receipt["outcome"]["persistence"] == "unchanged"
    assert receipt["extensions"]["persistence_independently_reopened"] is False
    verification = receipt["outcome"]["verification"]
    assert verification["checks"][0]["id"] == "preview-image-readback"
    assert all(item["required"] is False for item in verification["not_checked"])
    readback = receipt["extensions"]["file_readback"]
    assert readback["valid"] and readback["images"]


def test_public_preview_input_drift_refuses_before_image_generation(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    args, path = _preview_input(tmp_path, "library")
    args += ["--output", str(tmp_path / "preview.png")]
    with runtime(state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.envelope["ok"], planned.envelope
        path.write_bytes(path.read_bytes() + b" ")
        result = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert result.envelope["error"]["code"] == "E_CONFLICT"
    assert not list(tmp_path.glob("*.png"))


@pytest.mark.parametrize("kind", ["library", "schematic"])
def test_real_cli_process_preview_delivers_decodable_exact_image_set(tmp_path, monkeypatch, kind):
    _scope(tmp_path, monkeypatch)
    args, _ = _preview_input(tmp_path, kind)
    args += ["--output", str(tmp_path / "preview.png"), "--compact"]

    def call(flags):
        process = subprocess.run(
            [sys.executable, "-m", "xpedition_cli", *args, *flags],
            capture_output=True,
            env=os.environ.copy(),
            timeout=90,
            check=False,
        )
        envelope = json.loads(process.stdout.decode("utf-8"))
        assert process.returncode == 0, envelope
        assert envelope["ok"], envelope
        return envelope["data"]

    plan = call(["--dry-run"])
    data = call(["--confirm", plan["confirm_token"]])
    receipt = data["receipt"]
    assert receipt["outcome"]["verification"]["status"] == "passed"
    readback = receipt["extensions"]["file_readback"]
    assert {item["path"] for item in readback["images"]} == {
        item["path"] for item in plan["preview"]["extensions"]["preview_artifacts"]["images"]
    }
    for item in readback["images"]:
        assert hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
