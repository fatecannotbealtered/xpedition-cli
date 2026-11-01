"""Real public SDK/handlers/backend and PDF verifier, with a fake native RPC boundary."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_driver_v2 import _scope, _worker_fixture
from test_schematic_pdf_artifacts import write_pdf

from xpedition_cli import driver
from xpedition_cli import native_com_adapter as N
from xpedition_cli.backends.native_xpedition import NativeBackend


class FakeRPCBackend(NativeBackend):
    """Test-only JSON boundary; this fixture does not qualify dedicated owner admission."""


def _record(refdes, uid, *, pins=None):
    return {
        "refdes": refdes,
        "uid": uid,
        "sheet": 1,
        "x": 100,
        "y": 200,
        "pins": pins or [],
        "attributes": {"Value": "protected"},
        "identity_observation_coverage": {"complete": True, "errors": []},
        "pin_observation_coverage": {"complete": True, "errors": []},
    }


@pytest.fixture
def pdf_case(tmp_path, monkeypatch):
    # The product decorator explicitly preserves non-product adapter boundaries.
    # Keep producer/artifact assertions real within the fake RPC fixture; owner
    # admission is tested separately, never bypassed in the product backend.
    monkeypatch.setattr(driver, "NativeBackend", FakeRPCBackend)
    _scope(tmp_path, monkeypatch)
    project = tmp_path / "HeldOutController.prj"
    project.write_bytes(b"synthetic independently named source project")
    output = tmp_path / "whole-schematic.pdf"
    home = tmp_path / "licensed-fixture"
    executable = home / "common/win64/bin/sch2pdf.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"synthetic pinned converter executable")
    adapter_exe = tmp_path / "fake-native-rpc.exe"
    adapter_exe.write_bytes(b"synthetic RPC endpoint, never executed")
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(adapter_exe))
    monkeypatch.setattr(
        NativeBackend,
        "status",
        lambda self: {
            "available": True,
            "sdd_home": str(home),
            "backend": "native_xpedition",
        },
    )
    monkeypatch.setattr(N, "_configure_environment", lambda: home)
    application = {
        "pid": 4242,
        "domain": "schematic",
        "document": {"open": True, "path": str(project), "status": "observed"},
    }
    native = {
        "project": "ControllerDisplay",
        "revision": "native",
        "sheets": [
            {"number": 1, "name": "ControllerDisplay.1"},
            {"number": 2, "name": "ControllerDisplay.2"},
        ],
        "components": [_record("U47", "source-47", pins=[{"number": "1", "net": "SIGNAL_47"}])],
        "unnamed_components": [_record("", "frame-unique")],
        "nets": [{"name": "SIGNAL_47", "sheets": [1]}],
        "connections": [],
        "metadata": {
            "backend": "native_xpedition",
            "domain": "schematic",
            "source": "Viewdraw.Application",
        },
    }
    behavior = {"fault": None}
    calls = []

    def run(argv, **kwargs):
        if Path(argv[0]).resolve() == executable.resolve():
            calls.append({"kind": "producer", "argv": list(argv), "timeout": kwargs["timeout"]})
            assert kwargs["timeout"] <= 585
            assert not kwargs.get("shell")
            pages = 1 if behavior["fault"] == "missing_page" else 2
            write_pdf(argv[argv.index("-a") + 1], pages=pages)
            return subprocess.CompletedProcess(
                argv,
                5 if behavior["fault"] == "producer_error" else 0,
                b"Printed ControllerDisplay Sheet 1\nPrinted ControllerDisplay Sheet 2\n",
                b"",
            )
        assert Path(argv[0]).resolve() == adapter_exe.resolve(), "Unexpected external process"
        request = json.loads(kwargs["input"])
        method, params = request["method"], request["params"]
        calls.append({"kind": "rpc", "method": method, "params": copy.deepcopy(params)})
        if method == "identity":
            result = {"application": copy.deepcopy(application), "adapter": _worker_fixture()}
        elif method == "snapshot":
            assert params["observe_only"] is True and params["start"] is False
            assert params["pid"] == application["pid"]
            result = copy.deepcopy(native)
        elif method == "export_pdf":
            assert params["artifact_generation"].startswith("op-")
            assert params["_pdf_expectation"]["generation_id"] == params["artifact_generation"]
            try:
                result = N._export_pdf(params)
            except N.AdapterError as error:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(
                        {
                            "ok": False,
                            "error": {
                                "code": error.code,
                                "message": str(error),
                                "details": error.details,
                            },
                        }
                    ),
                    "",
                )
            if behavior["fault"] == "wrong_input":
                result["artifact"]["project"] = str(tmp_path / "foreign.prj")
            if behavior["fault"] == "raw_native_drift":
                native["components"][0]["attributes"]["Value"] = "unexpected change"
            if behavior["fault"] == "source_changed":
                project.write_bytes(b"changed source after producer returned")
            if behavior["fault"] == "wrong_generation":
                result["artifact"]["generation_id"] = "op-prior-generation"
        else:
            raise AssertionError("Unexpected native method: " + method)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "data": result}), "")

    monkeypatch.setattr(N.subprocess, "run", run)
    args = ["schematic", "export", "--project", str(project), "--output", str(output)]
    return SimpleNamespace(
        project=project,
        output=output,
        executable=executable,
        native=native,
        application=application,
        behavior=behavior,
        calls=calls,
        args=args,
        state=tmp_path / "operations",
    )


def _plan(cli, case, extra=()):
    result = cli.run([*case.args, *extra, "--dry-run", "--quiet"])
    assert result.envelope["ok"], result.envelope
    return result.envelope["data"]


def _receipt(result):
    return result.envelope.get("data", result.envelope.get("error", {}).get("details", {}))[
        "receipt"
    ]


def test_pdf_sdk_proves_generation_native_census_and_sealed_receipt(pdf_case):
    case = pdf_case
    with driver.runtime(state_dir=case.state) as cli:
        planned = _plan(cli, case)
        preview = planned["preview"]
        expected = preview["extensions"]["schematic_pdf_artifact"]
        assert preview["target"]["mode"] == "session"
        assert preview["target"]["engine_id"] == "xpedition-schematic"
        assert expected["generation_id"] == preview["operation_id"]
        assert expected["producer"]["path"] == str(case.executable)
        assert [item["number"] for item in expected["sheet_census"]] == [1, 2]
        assert not case.output.exists()
        assert not any(item["kind"] == "producer" for item in case.calls)
        done = cli.run([*case.args, "--confirm", planned["confirm_token"], "--quiet"])
        assert done.envelope["ok"], done.envelope
        receipt = _receipt(done)
        assert receipt == cli._store.get(receipt["operation_id"])["receipt"]
        checked = receipt["extensions"]["file_readback"]
        assert checked["artifact_structure"] == checked["native_protection"] == "passed"
        assert checked["decoded"]["page_count"] == 2
        assert "pixel rendering" in checked["not_checked"]
        assert receipt["extensions"]["persistence_independently_reopened"] is False
        assert receipt["outcome"]["change"] == "not_applied"
        original = copy.deepcopy(receipt)
        count = len(case.calls)
        replay = cli.run([*case.args, "--confirm", planned["confirm_token"], "--quiet"])
        assert not replay.envelope["ok"]
        assert len(case.calls) == count
        observed = cli.run(["operation", "show", "--id", receipt["operation_id"]])
        assert observed.envelope["data"]["operation_status"]["receipt"] == original
    assert len([item for item in case.calls if item["kind"] == "producer"]) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_input",
        "wrong_generation",
        "missing_page",
        "producer_error",
        "raw_native_drift",
        "source_changed",
    ],
)
def test_pdf_sdk_rejects_output_or_native_protection_failures(pdf_case, fault):
    case = pdf_case
    with driver.runtime(state_dir=case.state) as cli:
        planned = _plan(cli, case)
        case.behavior["fault"] = fault
        rejected = cli.run([*case.args, "--confirm", planned["confirm_token"], "--quiet"])
        assert not rejected.envelope["ok"], rejected.envelope
        receipt = _receipt(rejected)
        assert receipt["outcome"]["verification"]["status"] != "passed"
        assert receipt == cli._store.get(receipt["operation_id"])["receipt"]
        assert receipt["extensions"]["result"]["artifact"]["generation_id"]
        if fault == "producer_error":
            assert receipt["extensions"]["result"]["exit_code"] == 5
        if fault in {"raw_native_drift", "source_changed"}:
            assert receipt["extensions"]["file_readback"]["valid"] is False
    assert len([item for item in case.calls if item["kind"] == "producer"]) == 1


def test_pdf_sdk_identical_old_output_is_not_current_generation_proof(pdf_case):
    case = pdf_case
    write_pdf(case.output)
    original = case.output.read_bytes()
    with driver.runtime(state_dir=case.state) as cli:
        planned = _plan(cli, case, ("--replace",))
        rejected = cli.run(
            [*case.args, "--replace", "--confirm", planned["confirm_token"], "--quiet"]
        )
        assert not rejected.envelope["ok"], rejected.envelope
        receipt = _receipt(rejected)
        checks = receipt["extensions"]["file_readback"]["checks"]
        assert (
            next(item for item in checks if item["id"] == "fresh-output-bytes")["passed"] is False
        )
        assert case.output.read_bytes() == original


@pytest.mark.parametrize("source", ["project", "producer"])
def test_pdf_sdk_changed_input_before_confirmation_never_calls_producer(pdf_case, source):
    case = pdf_case
    with driver.runtime(state_dir=case.state) as cli:
        planned = _plan(cli, case)
        (case.project if source == "project" else case.executable).write_bytes(
            b"changed after preview"
        )
        refused = cli.run([*case.args, "--confirm", planned["confirm_token"], "--quiet"])
    assert refused.envelope["error"]["code"] == "E_CONFLICT", refused.envelope
    assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert not any(item["kind"] == "producer" for item in case.calls)
    assert not case.output.exists()


@pytest.mark.parametrize(
    "fault",
    [
        "missing_census",
        "missing_anonymous",
        "missing_pin_coverage",
        "duplicate_uid",
        "wrong_project",
    ],
)
def test_pdf_sdk_incomplete_source_refuses_before_artifact_dispatch(pdf_case, fault):
    case = pdf_case
    if fault == "missing_census":
        case.native["sheets"] = []
    elif fault == "missing_anonymous":
        case.native.pop("unnamed_components")
    elif fault == "missing_pin_coverage":
        case.native["components"][0]["pin_observation_coverage"]["complete"] = False
    elif fault == "duplicate_uid":
        case.native["unnamed_components"][0]["uid"] = case.native["components"][0]["uid"]
    else:
        case.application["document"]["path"] = str(case.project.with_name("other.prj"))
    with driver.runtime(state_dir=case.state) as cli:
        refused = cli.run([*case.args, "--dry-run", "--quiet"])
    assert not refused.envelope["ok"], refused.envelope
    assert refused.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert not any(item["kind"] == "producer" for item in case.calls)
    assert not case.output.exists()
