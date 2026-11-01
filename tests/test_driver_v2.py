"""Public SDK boundary and adversarial receipt tests. All native facts here are fixtures."""

from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

from xpedition_cli import __version__
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.query_page import query_page

# Use the real fake-SDK startup receipt and same journal, never bypass ownership.
pytest_plugins = ("test_session_ownership",)


def _worker_fixture(build="a" * 64):
    return {"version": __version__, "build_sha256": build, "fixture": True}


def _scope(tmp_path, monkeypatch):
    from xpedition_cli.audit import config_dir

    path = tmp_path / "scope.json"
    path.write_text(
        json.dumps(
            {
                "scope_id": "owned-offline-fixture",
                "tool": "xpedition-cli",
                "granted_by": "offline test fixture",
                "granted_at": "2026-10-06T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "session", "artifact", "change", "persist", "configure"],
                "dangerous": True,
                "roots": [str(tmp_path.resolve()), str(config_dir().resolve())],
                "outputs": [str(tmp_path.resolve())],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", str(path))


@pytest.mark.parametrize("invalid", ["suffix", "scale"])
def test_schematic_preview_rejects_invalid_inputs_before_confirmation(
    tmp_path, monkeypatch, invalid
):
    from pathlib import Path

    _scope(tmp_path, monkeypatch)
    source = Path(__file__).resolve().parent.parent / "examples/demo-sensor-board.json"
    design = tmp_path / "design.json"
    design.write_bytes(source.read_bytes())
    output = tmp_path / ("preview" if invalid == "suffix" else "preview.png")
    args = ["schematic", "plan", "preview", "--design", str(design), "--output", str(output)]
    if invalid == "scale":
        args += ["--scale", "7"]
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run([*args, "--dry-run"])
    assert result.exit_code == 2, result.envelope
    assert result.envelope["error"]["code"] == "E_VALIDATION"
    assert result.envelope["error"]["details"]["problem"]
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert not list(tmp_path.glob("preview*"))


def test_schematic_preview_exposes_layout_findings_before_artifact_write(tmp_path, monkeypatch):
    from pathlib import Path

    _scope(tmp_path, monkeypatch)
    source = Path(__file__).resolve().parent.parent / "examples/demo-sensor-board.json"
    design = json.loads(source.read_text(encoding="utf-8"))
    for sheet in design["sheets"]:
        for block in sheet["blocks"]:
            if block.get("refdes") == "U202":
                block["x"] = 960
    path = tmp_path / "design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    output = tmp_path / "preview.png"
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "schematic",
                "plan",
                "preview",
                "--design",
                str(path),
                "--output",
                str(output),
                "--dry-run",
            ]
        )
    assert result.exit_code == 0, result.envelope
    preview = result.envelope["data"]["preview"]["extensions"]["legacy_preview"]
    assert any(item["check"] == "DS-07" for item in preview["design_preview"]["issues"])
    assert not output.exists()


@pytest.mark.parametrize("command", ["schematic parts", "schematic nets", "schematic sheets"])
@pytest.mark.parametrize("more", [False, True])
def test_native_list_results_use_sdk_query_page_and_explicit_consistency(
    tmp_path, monkeypatch, command, more
):
    from xpedition_cli import driver

    project = tmp_path / "QA.prj"
    project.write_bytes(b"offline native fixture")

    def handler(options):
        return {
            "project": "Board1",
            "items": [{"name": "fixture"}],
            "count": 1,
            "has_more": more,
            "offset": 2,
            "next_offset": 3 if more else None,
        }

    monkeypatch.setattr(driver, "_handler", lambda declaration: handler)
    with runtime(state_dir=tmp_path / "state") as cli:
        output = cli.run([*command.split(), "--project", str(project)])
    assert output.envelope["ok"], output.envelope
    page = output.envelope["data"]
    assert page["count"] == 1 and page["snapshot"] is None
    assert page["snapshot_unavailable_reason"]
    assert page["consistency"]["mode"] == "best_effort"
    assert page["extensions"]["project"] == "Board1"
    assert ("next_offset" in page) is more


def test_generated_public_tree_is_the_reviewed_65_leaf_tree(tmp_path):
    with runtime(state_dir=tmp_path / "state") as cli:
        output = cli.run(["reference", "--index"])
    assert output.exit_code == 0
    data = output.envelope["data"]
    paths = {item["path"] for item in data["commands"]}
    assert len(paths) == 66
    assert data["version"] == __version__
    assert data["driver_profile"]["spec_version"] == "1.0.2"
    assert {
        "operation list",
        "operation show",
        "operation resolve",
        "pcb routing apply",
        "schematic package",
        "pcb checks result",
        "bom inspect",
        "bom export",
        "manufacturing verify",
    } <= paths
    assert not {"pcb outline", "pcb holes", "pcb trace", "library build", "pcb export"} & paths
    assert not any(other.startswith(path + " ") for path in paths for other in paths)


@pytest.mark.parametrize("status", ["observed", "not_observed"])
def test_public_library_check_keeps_registration_and_definition_scope_distinct(
    tmp_path, monkeypatch, status
):
    from xpedition_cli import driver

    lmc = tmp_path / "Library.lmc"
    lmc.write_bytes(b"fixture")
    monkeypatch.setattr(
        driver,
        "_handler",
        lambda declaration: (
            lambda options: {
                **query_page([], limit=50, offset=0),
                "clean": True,
                "registration": {"status": status, "fixture": True},
                "validation_scope": "definition consistency; "
                "LMC registration is reported separately",
            }
        ),
    )
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run(["library", "check", "--library", str(lmc)])
    assert result.exit_code == 0, result.envelope
    extension = result.envelope["data"]["extensions"]
    assert extension["registration"]["status"] == status
    assert extension["clean"] is True
    assert "definition consistency" in extension["validation_scope"]


@pytest.mark.parametrize(
    "prefix", [[], ["pcb"], ["pcb", "routing"], ["schematic", "plan"], ["operation"]]
)
def test_help_for_every_depth_is_served_without_adapter_calls(tmp_path, prefix):
    class NoAdapter:
        def run(self, call):
            pytest.fail("help must not observe an application")

    with runtime(NoAdapter(), state_dir=tmp_path / "state") as cli:
        output = cli.run([*prefix, "--help"])
        assert output.exit_code == 0
        if prefix:
            listed = [
                line.strip().split("  ")[0]
                for line in output.stdout.decode().splitlines()
                if line.startswith("  ")
            ]
            assert listed
            assert all(path.startswith(" ".join(prefix) + " ") for path in listed)


def test_all_nonobserve_commands_refuse_before_adapter_without_confirmation(tmp_path):
    class NoAdapter:
        def __init__(self):
            self.calls = []

        def run(self, call):
            self.calls.append("run")
            pytest.fail("unexpected read")

        def plan(self, call):
            self.calls.append("plan")
            pytest.fail("ungated planning")

        def apply(self, call, plan):
            self.calls.append("apply")
            pytest.fail("ungated effect")

    adapter = NoAdapter()
    with runtime(adapter, state_dir=tmp_path / "state") as cli:
        writes = [item for item in cli.reference["commands"] if item["type"] == "write"]
        for command in writes:
            argv = command["path"].split()
            for param in command["params"]:
                if not param.get("required"):
                    continue
                if param["type"] == "boolean":
                    argv.append("--" + param["name"])
                else:
                    value = str(
                        (
                            param.get("enum")
                            or [1 if param["type"] in {"integer", "number"} else "fixture"]
                        )[0]
                    )
                    argv.extend(["--" + param["name"], value])
            result = cli.run(argv)
            selected_kind = argv[argv.index("--kind") + 1] if "--kind" in argv else None
            expected = (
                "E_HUMAN_REQUIRED"
                if command["path"] == "session attach" and selected_kind == "schematic"
                else "E_CONFIRMATION_REQUIRED"
            )
            assert result.envelope["error"]["code"] == expected, command["path"]
            assert adapter.calls == []


def test_file_configuration_round_trip_uses_durable_operation_and_real_disk_readback(
    tmp_path, monkeypatch
):
    _scope(tmp_path, monkeypatch)
    args = [
        "kb",
        "bind",
        "--name",
        "qa",
        "--url",
        "https://example.com/qa",
        "--about",
        "offline fixture",
    ]
    with runtime(state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dangerous", "--dry-run"])
    assert preview.envelope["ok"], preview.envelope
    plan = preview.envelope["data"]["preview"]
    assert "handler_token" not in plan["extensions"]
    token = preview.envelope["data"]["confirm_token"]
    with runtime(state_dir=tmp_path / "state") as cli:
        applied = cli.run([*args, "--dangerous", "--confirm", token])
        shown = cli.run(["operation", "show", "--id", plan["operation_id"]])
    assert applied.envelope["ok"], applied.envelope
    receipt = applied.envelope["data"]["receipt"]
    assert receipt["snapshot_before"]["revision"] != receipt["snapshot_after"]["revision"]
    assert receipt["outcome"]["persistence"] == "saved"
    assert shown.envelope["data"]["operation_status"]["status"] == "succeeded"
    from xpedition_cli.audit import config_dir

    stored = json.loads((config_dir() / "knowledge-base.json").read_text(encoding="utf-8"))
    assert "qa" in json.dumps(stored)


def test_scope_denial_never_dispatches_or_creates_output(tmp_path, monkeypatch):
    from xpedition_cli.audit import config_dir

    config = config_dir()
    path = tmp_path / "scope.json"
    path.write_text(
        json.dumps(
            {
                "scope_id": "read-only-fixture",
                "tool": "xpedition-cli",
                "granted_by": "test fixture",
                "granted_at": "2026-10-06T00:00:00Z",
                "expires_at": None,
                "effects": ["observe"],
                "dangerous": False,
                "roots": [],
                "outputs": [],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", str(path))
    with runtime(state_dir=tmp_path / "state") as cli:
        result = cli.run(
            [
                "kb",
                "bind",
                "--name",
                "qa",
                "--url",
                "https://example.com",
                "--dangerous",
                "--dry-run",
            ]
        )
    assert result.envelope["error"]["code"] == "E_FORBIDDEN"
    assert not (config / "knowledge-base.json").exists()


def test_input_drift_does_not_replay_a_bound_plan(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    args = ["kb", "bind", "--name", "qa", "--url", "https://example.com"]
    with runtime(state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dangerous", "--dry-run"])
        from xpedition_cli.audit import config_dir

        current = config_dir() / "knowledge-base.json"
        current.write_text('{"documents": []}', encoding="utf-8")
        result = cli.run(
            [*args, "--dangerous", "--confirm", planned.envelope["data"]["confirm_token"]]
        )
    assert result.envelope["error"]["code"] == "E_CONFLICT"
    assert result.envelope["error"]["details"]["conflict"] == "state_drift"


def test_unknown_dispatch_is_durable_and_blocks_new_plans(tmp_path, monkeypatch):
    _scope(tmp_path, monkeypatch)
    from xpedition_cli._vendor.tool_driver_sdk import DriverError

    class LostReply(Adapter):
        def apply(self, call, plan):
            raise DriverError(
                "E_TIMEOUT", "fixture lost the reply after dispatch", dispatch_state="dispatched"
            )

    args = ["kb", "bind", "--name", "qa", "--url", "https://example.com"]
    with runtime(LostReply(), state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dangerous", "--dry-run"])
        result = cli.run(
            [*args, "--dangerous", "--confirm", planned.envelope["data"]["confirm_token"]]
        )
    assert result.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    operation = planned.envelope["data"]["preview"]["operation_id"]
    with runtime(state_dir=tmp_path / "state") as cli:
        shown = cli.run(["operation", "show", "--id", operation])
        blocked = cli.run([*args, "--dangerous", "--dry-run"])
    assert shown.envelope["data"]["operation_status"]["status"] == "unknown"
    assert blocked.envelope["error"]["details"]["conflict"] == "unresolved_operation"


@pytest.mark.parametrize("suite", ["cam", "dfm"])
def test_unimplemented_manufacturing_consumers_refuse_without_native_calls(tmp_path, suite):
    class NoNative:
        def invoke(self, *args, **kwargs):
            pytest.fail("A package consumer must not dispatch a native command")

    with runtime(Adapter(NoNative()), state_dir=tmp_path / "state") as cli:
        output = cli.run(["manufacturing", "verify", "--package", str(tmp_path), "--suite", suite])
    assert output.envelope["error"]["code"] == "E_UNSUPPORTED"
    assert not output.envelope["error"]["details"]["manufacturing_sign_off"]


def test_retained_check_read_never_reexecutes_and_flags_disk_drift(tmp_path):
    from xpedition_cli.cli import checks

    project = tmp_path / "owned.prj"
    project.write_bytes(b"offline check fixture project")
    report = checks._run(
        "pcb",
        {"project": str(project)},
        lambda options: {
            "completed": False,
            "verification": {"status": "incomplete"},
            "fixture": True,
        },
    )

    class NoNative:
        def invoke(self, *args, **kwargs):
            pytest.fail("Reading the retained record must not execute native work")

    args = ["pcb", "checks", "result", "--id", report["check_run_id"]]
    with runtime(Adapter(NoNative()), state_dir=tmp_path / "state") as cli:
        current = cli.run(args)
        project.write_bytes(b"changed offline project bytes")
        stale = cli.run(args)
    assert current.envelope["ok"], current.envelope
    assert stale.envelope["ok"], stale.envelope
    assert current.envelope["data"]["native_executed_by_result"] is False
    assert current.envelope["data"]["native_completion"] == "incomplete"
    assert current.envelope["data"]["stale"] is None
    assert stale.envelope["data"]["stale"] is True


def test_retained_check_ignores_frozen_extraction_path_but_detects_binary_change(
    tmp_path, monkeypatch
):
    from xpedition_cli.cli import checks

    project = tmp_path / "owned.prj"
    project.write_bytes(b"unchanged project")
    identity = {
        "frozen": True,
        "build_sha256": "a" * 64,
        "version": "2.0.0-dev.1",
        "client_bitness": 64,
        "module_path": str(tmp_path / "_MEI_one/native.py"),
    }
    monkeypatch.setattr(checks, "adapter_identity", lambda: dict(identity))
    report = checks._run("pcb", {"project": str(project)}, lambda p: {"completed": True})
    identity["module_path"] = str(tmp_path / "_MEI_two/native.py")

    class NoNative:
        def invoke(self, *a, **k):
            pytest.fail("Retained query dispatched a checker")

    with runtime(Adapter(NoNative()), state_dir=tmp_path / "state") as cli:
        same = cli.run(["pcb", "checks", "result", "--id", report["check_run_id"]])
        identity["build_sha256"] = "b" * 64
        changed = cli.run(["pcb", "checks", "result", "--id", report["check_run_id"]])
    assert same.envelope["data"]["stale"] is None
    assert changed.envelope["data"]["stale"] is True


def test_artifact_family_receipt_uses_changed_disk_bytes(tmp_path, monkeypatch):
    import hashlib

    from PIL import Image

    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    design = tmp_path / "design.json"
    design.write_text("{}", encoding="utf-8")
    output = tmp_path / "preview.png"
    siblings = [tmp_path / "preview-sheet1.png", tmp_path / "preview-sheet2.png"]

    def render(options):
        if options.get("dry_run"):
            return {"sheets": [1, 2], "issues": [], "fixture": True}
        for index, path in enumerate(siblings):
            Image.new("RGB", (24, 16), (index, 10, 20)).save(path)
        return {
            "pictures": [
                {"path": str(path), "sheet": index, "width": 24, "height": 16}
                for index, path in enumerate(siblings, 1)
            ]
        }

    monkeypatch.setattr(driver, "_handler", lambda command: render)
    args = ["schematic", "plan", "preview", "--design", str(design), "--output", str(output)]
    with runtime(state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        applied = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert applied.envelope["ok"], applied.envelope
    artifacts = applied.envelope["data"]["receipt"]["artifacts"]
    assert {item["path"] for item in artifacts} == {str(path) for path in siblings}
    for item in artifacts:
        assert (
            item["sha256"]
            == hashlib.sha256(
                next(path for path in siblings if str(path) == item["path"]).read_bytes()
            ).hexdigest()
        )


def test_unchanged_existing_output_does_not_prove_an_artifact_operation(tmp_path, monkeypatch):
    from PIL import Image

    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    design, output = tmp_path / "design.json", tmp_path / "preview.png"
    design.write_text("{}", encoding="utf-8")
    Image.new("RGB", (24, 16)).save(output)
    monkeypatch.setattr(
        driver,
        "_handler",
        lambda command: (
            lambda options: (
                {"sheets": [1], "issues": [], "fixture": True}
                if options.get("dry_run")
                else {"pictures": [{"path": str(output), "sheet": 1, "width": 24, "height": 16}]}
            )
        ),
    )
    args = ["schematic", "plan", "preview", "--design", str(design), "--output", str(output)]
    with runtime(state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        applied = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert applied.envelope["error"]["code"] == "E_VERIFY_INCOMPLETE"
    receipt = applied.envelope["error"]["details"]["receipt"]
    assert receipt["artifacts"] == []
    assert receipt["outcome"]["verification"]["status"] == "incomplete"


def test_partial_native_fixture_retains_actual_orphan_and_is_not_replayed(tmp_path, monkeypatch):
    from copy import deepcopy

    from xpedition_cli import driver
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    project = tmp_path / "owned.prj"
    project.write_bytes(b"offline native-state fixture")
    actual = {"fixture": True, "components": [], "unnamed_components": []}
    calls = []

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {
                    "application": {"pid": 4242, "active_project": str(project), "fixture": True},
                    "adapter": _worker_fixture(),
                }
            assert method == "snapshot"
            return deepcopy(actual)

    def fail_after_placement(options):
        if options.get("dry_run"):
            return {"preview": {"changes": []}, "confirm_token": "private-fixture-token"}
        calls.append("placed")
        actual["unnamed_components"].append(
            {"uid": "fixture-native-uid", "refdes": "", "x": 17, "y": 29}
        )
        raise CLIError("E_NATIVE_FAILURE", "fixture Refdes setter failed after placement")

    monkeypatch.setattr(driver, "_handler", lambda command: fail_after_placement)
    args = ["schematic", "package", "--project", str(project)]
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        token = planned.envelope["data"]["confirm_token"]
        applied = cli.run([*args, "--confirm", token])
        replay = cli.run([*args, "--confirm", token])
    assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN", applied.envelope
    receipt = applied.envelope["error"]["details"]["receipt"]
    assert receipt["outcome"]["change"] == "partial"
    assert receipt["outcome"]["persistence"] == "unknown"
    assert (
        receipt["snapshot_after"]["extensions"]["facts"]["native"]["unnamed_components"][0]["uid"]
        == "fixture-native-uid"
    )
    assert not replay.envelope["ok"]
    assert calls == ["placed"]


@pytest.mark.parametrize("domain", ["schematic", "pcb"])
def test_attach_plan_observes_active_identity_without_opening_requested_project(
    tmp_path, monkeypatch, domain, started
):
    from xpedition_cli import driver, native_process

    _scope(tmp_path, monkeypatch)
    requested = tmp_path / ("next.pcb" if domain == "pcb" else "next.prj")
    requested.write_bytes(b"offline attachment fixture")
    # Layout attach only binds an already open requested board; it does not
    # switch from another board. Designer arbitrary attachment stays forbidden.
    active = [str(requested) if domain == "pcb" else str(tmp_path / "current.prj")]
    observed_projects = []
    backend_calls, process_reads = [], []
    generation = dict(started.actual)
    monkeypatch.setattr(
        native_process,
        "process_identity",
        lambda selected, pid: process_reads.append((selected, pid)) or generation,
    )

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            backend_calls.append(method)
            if method == "identity":
                return {
                    "application": {
                        "pid": generation["pid"],
                        "active_project": active[0],
                        "fixture": True,
                        "document": {"open": True, "path": active[0], "status": "observed"},
                    },
                    "adapter": _worker_fixture(),
                }
            assert method == "snapshot" and active[0] == str(requested)
            observed_projects.append(params["project"])
            return {"project": active[0], "fixture": True}

    def attach(options):
        active[0] = str(requested)
        return {
            "attached": True,
            "fixture": True,
            "pid": generation["pid"],
            "process_identity": generation,
        }

    monkeypatch.setattr(driver, "_handler", lambda command: attach)
    args = [
        "session",
        "attach",
        "--kind",
        domain,
        "--pid",
        str(generation["pid"]),
        "--project",
        str(requested),
    ]
    monkeypatch.setattr(started.cli.adapter, "backend", NativeFixture())
    with nullcontext(started.cli) as cli:
        planned = cli.run([*args, "--dry-run"])
        if domain == "schematic":
            assert planned.envelope["error"]["code"] == "E_HUMAN_REQUIRED", planned.envelope
            assert planned.envelope["error"]["details"]["dispatch_state"] == "not_started"
            assert backend_calls == [] and process_reads == []
            assert not observed_projects and active[0] != str(requested)
            return
        assert planned.envelope["ok"], planned.envelope
        assert not observed_projects and active[0] == str(requested)
        original_validate = cli.validator.validate
        violations = []

        def checked(name, value, **kwargs):
            try:
                return original_validate(name, value, **kwargs)
            except Exception as error:
                violations.extend(getattr(error, "errors", [str(error)]))
                raise

        monkeypatch.setattr(cli.validator, "validate", checked)
        applied = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert observed_projects == [str(requested)]
    payload = applied.envelope.get("data") or applied.envelope["error"]["details"]
    assert "receipt" in payload, (applied.envelope, violations)
    receipt = payload["receipt"]
    assert receipt["snapshot_before"]["extensions"]["facts"]["application"][
        "active_project"
    ] == str(requested)
    assert receipt["snapshot_after"]["extensions"]["facts"]["native"]["project"] == str(requested)


def test_frozen_unpack_directory_is_transient_but_executable_and_build_are_bound():
    from xpedition_cli.driver import _digest, _stable

    first = {
        "identity_basis": "frozen_executable_bytes",
        "module_path": "fixture-unpack-one/native.pyc",
        "python_executable": "fixture/candidate.exe",
        "build_sha256": "a" * 64,
    }
    unpacked_again = {**first, "module_path": "fixture-unpack-two/native.pyc"}
    revision = _digest(_stable(first))
    assert revision == _digest(_stable(unpacked_again))
    assert revision != _digest(_stable({**unpacked_again, "build_sha256": "b" * 64}))
    assert revision != _digest(_stable({**unpacked_again, "python_executable": "other.exe"}))
    source = {**first, "identity_basis": "loaded_source_bytes"}
    assert _stable(source)["module_path"] == first["module_path"]


@pytest.fixture
def legacy_designer_metadata(monkeypatch):
    """Only the explicit fake backend/handler metadata regressions may bypass this gate."""
    from xpedition_cli import designer_startup

    monkeypatch.setattr(designer_startup, "require_dedicated_designer", lambda: None)


@pytest.mark.parametrize(
    "worker", [{"version": "1.0.0", "build_sha256": "a" * 64}, {"version": __version__}]
)
def test_plan_refuses_wrong_or_unidentified_worker_before_handler(
    tmp_path, monkeypatch, worker, legacy_designer_metadata
):
    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)

    class WrongWorker:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            return {"application": {"pid": 4242, "fixture": True}, "adapter": worker}

    monkeypatch.setattr(driver, "_handler", lambda command: pytest.fail("handler was reached"))
    with runtime(Adapter(WrongWorker()), state_dir=tmp_path / "state") as cli:
        output = cli.run(["session", "attach", "--kind", "schematic", "--pid", "4242", "--dry-run"])
    assert output.envelope["error"]["code"] == "E_BACKEND_UNAVAILABLE", output.envelope
    assert output.envelope["error"]["details"]["observed_adapter"] == worker


def test_worker_build_drift_refuses_confirm_before_apply(tmp_path, monkeypatch, started):
    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    worker = _worker_fixture()
    applied = []
    generation = dict(started.actual)

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            return {
                "application": {"pid": generation["pid"], "fixture": True},
                "adapter": dict(worker),
            }

    def attach(options):
        applied.append("unexpected apply")
        return {"attached": True, "fixture": True}

    monkeypatch.setattr(driver, "_handler", lambda command: attach)
    args = ["session", "attach", "--kind", "pcb", "--pid", str(generation["pid"])]
    monkeypatch.setattr(started.cli.adapter, "backend", NativeFixture())
    with nullcontext(started.cli) as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.envelope["ok"], planned.envelope
        worker["build_sha256"] = "b" * 64
        output = cli.run([*args, "--confirm", planned.envelope["data"]["confirm_token"]])
    assert output.envelope["error"]["code"] == "E_CONFLICT", output.envelope
    assert output.envelope["error"]["details"]["conflict"] == "state_drift"
    assert not applied


def test_public_session_start_timeout_after_launch_never_claims_not_applied(
    tmp_path, monkeypatch, legacy_designer_metadata
):
    from xpedition_cli import driver, native_process
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(native_process, "processes", lambda domain: list(launched))

    def owned_fixture_identity(domain, pid):
        assert domain == "schematic" and int(pid) == 4242
        return {
            "pid": 4242,
            "executable": "D:/licensed/viewdraw.exe",
            "creation_time_100ns": "133000000000000001",
        }

    monkeypatch.setattr(native_process, "process_identity", owned_fixture_identity)

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            return {"application": {"pid": 4242, "fixture": True}, "adapter": _worker_fixture()}

    def start(options):
        if options.get("dry_run"):
            return {"confirm_token": "fixture-plan", "changes": []}
        launched.append({"pid": 4242})
        raise CLIError(
            "E_TIMEOUT", "Post-launch health read timed out", {"native_query_only": True}
        )

    monkeypatch.setattr(driver, "_handler", lambda command: start)
    args = ["session", "start", "--kind", "schematic"]
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
        assert not result.envelope["ok"], result.envelope
        receipt = result.envelope["error"]["details"]["receipt"]
        assert receipt["outcome"]["change"] == "unknown"
        assert receipt["snapshot_after"]["extensions"]["facts"]["native_processes"] == launched


def test_confirm_identity_failure_preserves_native_error_before_dispatch(
    tmp_path, monkeypatch, started
):
    from xpedition_cli import driver
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    failing = False
    applied = []
    generation = dict(started.actual)

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            assert method == "identity"
            if failing:
                raise CLIError("E_NOT_FOUND", "Native attachment unavailable", {"hresult": -1})
            return {"application": {"pid": generation["pid"]}, "adapter": _worker_fixture()}

    monkeypatch.setattr(driver, "_handler", lambda command: lambda options: applied.append(True))
    args = ["session", "attach", "--kind", "pcb", "--pid", str(generation["pid"])]
    monkeypatch.setattr(started.cli.adapter, "backend", NativeFixture())
    with nullcontext(started.cli) as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        failing = True
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    error = result.envelope["error"]
    assert error["code"] == "E_NOT_FOUND", error
    assert error["details"]["dispatch_state"] == "not_started"
    assert error["details"]["hresult"] == -1
    assert not applied


@pytest.mark.parametrize("failure", [False, True])
def test_public_session_start_receipt_when_process_observation_is_unchanged(
    tmp_path, monkeypatch, failure
):
    from xpedition_cli import driver, native_process
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    monkeypatch.setattr(native_process, "processes", lambda domain: [])

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            raise CLIError("E_NOT_FOUND", "Automation not observable")

    def start(options):
        if failure:
            raise CLIError("E_TIMEOUT", "Launch result unavailable")
        return {"started": True}

    monkeypatch.setattr(driver, "_handler", lambda command: start)
    args = ["session", "start", "--kind", "pcb"]
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        original_validate = cli.validator.validate
        violations = []

        def checked(name, value, **kwargs):
            try:
                return original_validate(name, value, **kwargs)
            except Exception as error:
                violations.extend(getattr(error, "errors", [str(error)]))
                raise

        monkeypatch.setattr(cli.validator, "validate", checked)
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
        payload = result.envelope.get("data") or result.envelope["error"]["details"]
        assert "receipt" in payload, (result.envelope, violations)
        assert payload["receipt"]["outcome"]["verification"]["status"] == "incomplete"


def test_unbound_native_artifact_without_engineering_change_is_rejected(tmp_path, monkeypatch):
    from xpedition_cli import driver

    _scope(tmp_path, monkeypatch)
    project = tmp_path / "QA.prj"
    project.write_text("fixture", encoding="utf-8")
    output = tmp_path / "geometry.json"

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            if method == "board_geometry":
                return {"pcb": str(project.with_suffix(".pcb")), "model": {"fixture": True}}
            assert method == "snapshot"
            return {"components": [], "fixture": True}

    def artifact(options):
        output.write_text('{"fixture":true}', encoding="utf-8")
        return {"output": str(output)}

    monkeypatch.setattr(driver, "_handler", lambda command: artifact)
    args = ["pcb", "snapshot", "--project", str(project), "--output", str(output)]
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    error = result.envelope["error"]
    assert error["code"] == "E_VERIFY_FAILED", error
    receipt = error["details"]["receipt"]
    assert receipt["outcome"]["change"] == "not_applied"
    assert len(receipt["artifacts"]) == 1
    assert receipt["outcome"]["verification"]["status"] == "failed"


def test_lost_stdout_after_persisted_result_does_not_report_config_or_retry(monkeypatch, capsys):
    from xpedition_cli import driver

    calls = []

    class PersistedOutput:
        def write(self):
            calls.append("write-reply")
            raise BrokenPipeError("synthetic closed receiver")

    class FixtureCLI:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, argv):
            calls.append("dispatch-and-persist-once")
            return PersistedOutput()

    monkeypatch.setattr(driver, "runtime", FixtureCLI)
    assert driver.main(["fixture"]) == 1
    assert calls == ["dispatch-and-persist-once", "write-reply"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "operation show" in captured.err and "E_CONFIG" not in captured.err


def test_runtime_load_failure_still_emits_valid_predispatch_json(monkeypatch, capsys):
    from xpedition_cli import driver

    def unavailable():
        raise ValueError("private configuration value")

    monkeypatch.setattr(driver, "runtime", unavailable)
    assert driver.main([]) == 4
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["error"]["code"] == "E_CONFIG"
    assert body["error"]["details"]["dispatch_state"] == "not_started"
    assert "private configuration" not in captured.out


@pytest.mark.parametrize("reopened", [False, True])
def test_saved_native_result_survives_failed_independent_observation_and_new_query(
    tmp_path, monkeypatch, reopened
):
    from xpedition_cli import driver
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    project = tmp_path / "QA.prj"
    project.write_text("synthetic board fixture", encoding="utf-8")
    dispatched = []

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            if dispatched:
                raise CLIError("E_NOT_FOUND", "Post-save attachment unavailable")
            if method == "identity":
                return {"application": {"pid": 4242}, "adapter": _worker_fixture()}
            assert method == "snapshot"
            return {"components": [], "fixture": True}

    def move(options):
        if options.get("dry_run"):
            return {"confirm_token": "synthetic-token", "changes": []}
        dispatched.append("place-once")
        return {
            "applied": True,
            "saved": True,
            "persistence_verified": reopened,
            "after": {"x": 20.0, "y": 15.0, "placed": True},
            "fixture": True,
        }

    monkeypatch.setattr(driver, "_handler", lambda command: move)
    args = [
        "pcb",
        "placement",
        "move",
        "--project",
        str(project),
        "--refdes",
        "J1",
        "--to",
        "20,15",
        "--rotate",
        "90",
    ]
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        token = plan.envelope["data"]["confirm_token"]
        op = plan.envelope["data"]["preview"]["operation_id"]
        result = cli.run([*args, "--confirm", token])
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as fresh_cli:
        status = fresh_cli.run(["operation", "show", "--id", op]).envelope["data"][
            "operation_status"
        ]
        replay = fresh_cli.run([*args, "--confirm", token])
    assert not result.envelope["ok"] and not replay.envelope["ok"]
    assert dispatched == ["place-once"]
    receipt = status["receipt"]
    assert receipt, status
    assert receipt["outcome"]["execution"] == "completed"
    assert receipt["outcome"]["change"] == "applied"
    assert receipt["outcome"]["persistence"] == ("saved" if reopened else "unknown")
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["extensions"]["result"]["saved"] is True


def test_public_backup_human_guard_keeps_instructions_without_invalid_action_enum(
    tmp_path, monkeypatch
):
    from xpedition_cli import driver
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    project = tmp_path / "owned/QA.prj"
    project.parent.mkdir()
    project.write_text("offline backup fixture", encoding="utf-8")
    output = tmp_path / "saved.zip"

    def blocked(options):
        if options.get("dry_run"):
            return {"confirm_token": "fixture-plan", "changes": []}
        raise CLIError(
            "E_HUMAN_REQUIRED",
            "Library is not released",
            {
                "action": "Normally release the owned library",
                "write_attempted": False,
            },
        )

    monkeypatch.setattr(driver, "_handler", lambda command: blocked)
    args = ["project", "backup", "--project", str(project), "--output", str(output)]
    with runtime(state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        assert plan.envelope["ok"], plan.envelope
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    assert result.exit_code == 9, result.envelope
    error = result.envelope["error"]
    assert error["code"] == "E_HUMAN_REQUIRED"
    assert error["details"]["action"] == "other"
    assert error["details"]["action_instructions"] == "Normally release the owned library"
    assert error["details"]["dispatch_state"] == "not_started"
    assert "receipt" not in error["details"]
    assert not output.exists()


def test_native_action_error_keeps_partial_receipt_and_native_cause(tmp_path, monkeypatch):
    from xpedition_cli import driver
    from xpedition_cli.errors import CLIError

    _scope(tmp_path, monkeypatch)
    project = tmp_path / "owned/QA.prj"
    project.parent.mkdir()
    project.write_text("offline fixture", encoding="utf-8")
    output = tmp_path / "saved.zip"

    def failed(options):
        if options.get("dry_run"):
            return {"confirm_token": "fixture-plan", "changes": []}
        output.write_bytes(b"partial synthetic bytes")
        project.write_text("partially changed offline fixture", encoding="utf-8")
        raise CLIError(
            "E_SERVER",
            "Synthetic COM failure",
            {
                "action": "draw_text",
                "native": {"hresult_hex": "0x8002801D"},
                "completed": 3,
            },
        )

    monkeypatch.setattr(driver, "_handler", lambda command: failed)
    args = ["project", "backup", "--project", str(project), "--output", str(output)]
    with runtime(state_dir=tmp_path / "state") as cli:
        plan = cli.run([*args, "--dry-run"])
        result = cli.run([*args, "--confirm", plan.envelope["data"]["confirm_token"]])
    error = result.envelope["error"]
    assert error["code"] == "E_PARTIAL", error
    assert "contract_errors" not in error["details"]
    assert error["details"]["native_action"] == "draw_text"
    receipt = error["details"]["receipt"]
    assert receipt["outcome"]["change"] == "partial"
    assert (
        receipt["extensions"]["handler_error"]["details"]["native"]["hresult_hex"] == "0x8002801D"
    )
