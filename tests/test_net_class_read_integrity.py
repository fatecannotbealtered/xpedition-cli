"""Native-shaped failed reads must not establish absent class membership.

The external-worker cases exercise the public CLI process over owned synthetic
objects. With a final EXE they also extract its exact reader/comparator bytecode
into the external worker. They are neither CAD observations nor tests of the
running frozen internal COM worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_trace_safety import ITEM, native_case, trace_state

from xpedition_cli import board_trace as T
from xpedition_cli import native_com_adapter as A
from xpedition_cli.native_identity import adapter_identity

ROOT = Path(__file__).resolve().parents[1]


class NativeCollection:
    def __init__(self, values, fault=None):
        self.values, self.fault, self.count_reads = values, fault, 0
        self.item_indices = []

    @property
    def Count(self):
        self.count_reads += 1
        if self.fault == "count":
            raise RuntimeError("owned Count getter failed")
        if self.fault == "count_after" and self.count_reads > 1:
            return len(self.values) + 1
        if self.fault == "count_after_failure" and self.count_reads > 1:
            raise RuntimeError("owned second Count getter failed")
        if self.fault == "negative":
            return -1
        if self.fault == "invalid":
            return "2"
        return len(self.values)

    def Item(self, index):
        self.item_indices.append(index)
        if self.fault == "item" and index == len(self.values):
            raise RuntimeError("owned Item getter failed")
        if self.fault == "first_item" and index == 1:
            raise RuntimeError("owned first Item getter failed")
        if index < 1 or index > len(self.values):
            raise IndexError("native collection requires one-based indices")
        return self.values[index - 1]


class NativeNamed:
    def __init__(self, name, nets=None, fault=None):
        self._name, self._nets, self.fault = name, nets, fault

    @property
    def Name(self):
        if self.fault == "name":
            raise RuntimeError("owned Name getter failed")
        return self._name

    @property
    def Nets(self):
        if self.fault == "nets":
            raise A.AdapterError(
                "E_NATIVE_FAILURE", "owned Nets getter failed", {"owned_error_code": 731}
            )
        return self._nets


def actual_adapter_rules(monkeypatch, fault="complete"):
    """Invoke the actual reader, including its exception paths and class widths."""
    doc = object()
    second_members = NativeCollection(
        []
        if fault == "observed_empty"
        else [NativeNamed("B", fault="name" if fault == "member_name" else None)],
        {
            "member_count": "count",
            "member_item": "item",
            "member_count_changed": "count_after",
            "member_count_after_failure": "count_after_failure",
            "member_negative_count": "negative",
        }.get(fault),
    )
    classes = NativeCollection(
        [
            NativeNamed("Default", NativeCollection([NativeNamed("A")])),
            NativeNamed(
                "Other",
                second_members,
                {"nets": "nets", "class_name": "name"}.get(fault),
            ),
        ],
        {
            "class_count": "count",
            "class_item": "item",
            "class_first_item": "first_item",
            "class_count_changed": "count_after",
            "class_count_after_failure": "count_after_failure",
            "class_invalid_count": "invalid",
        }.get(fault),
    )
    if fault == "observed_empty_classes":
        classes.values = []
    elif fault == "class_first_item":
        classes.values[1].minimum = 0.3048

    def propget(owner, member, *args):
        if owner is doc:
            assert (member, args) == (A.LAYOUT_NET_CLASSES, ("*",))
            if fault == "classes":
                raise RuntimeError("owned NetClasses getter failed")
            return classes
        assert member in A.NET_CLASS_WIDTHS.values() and args == (1, "(Master)", 4)
        return getattr(owner, "minimum", 0.254)

    monkeypatch.setattr(A, "_propget", propget)
    observation = {}
    rows = A._board_net_classes(doc, 1, observation=observation)
    return (
        {
            "scheme": "(Master)",
            "local_applicability_verified": False,
            "net_classes": rows,
            "net_class_observation": observation,
        },
        classes,
        second_members,
    )


@pytest.mark.parametrize(
    "fault,location",
    [
        ("classes", "NetClasses"),
        ("class_count", "NetClasses.Count.before"),
        ("class_item", "NetClasses.Item[2]"),
        ("class_first_item", "NetClasses.Item[1]"),
        ("class_name", "NetClasses.Item[2].Name"),
        ("class_count_changed", "NetClasses.Count"),
        ("class_count_after_failure", "NetClasses.Count.after"),
        ("class_invalid_count", "NetClasses.Count.before"),
        ("nets", "NetClasses.Item[2].Nets"),
        ("member_count", "NetClasses.Item[2].Nets.Count.before"),
        ("member_item", "NetClasses.Item[2].Nets.Item[1]"),
        ("member_name", "NetClasses.Item[2].Nets.Item[1].Name"),
        ("member_count_changed", "NetClasses.Item[2].Nets.Count"),
        ("member_count_after_failure", "NetClasses.Item[2].Nets.Count.after"),
        ("member_negative_count", "NetClasses.Item[2].Nets.Count.before"),
    ],
)
def test_actual_failed_adapter_reads_cannot_establish_unique_membership(
    monkeypatch, fault, location
):
    rules, classes, members = actual_adapter_rules(monkeypatch, fault)
    state = trace_state()
    state["rules"] = rules
    plan = T.bind(state, {**ITEM, "width": 0.25}, [], A)
    compared = plan["named_width_comparison"]
    observation = rules["net_class_observation"]
    assert observation["complete"] is False and observation["membership_complete"] is False
    failures = observation["failures"]
    assert any(row["location"] == location for row in failures)
    assert all(row["type"] and row["message"] for row in failures)
    assert compared["status"] == "unknown" and compared["delta_mm"] is None
    assert compared["membership_complete"] is False
    assert compared["membership_observation"] == observation
    assert plan["can_apply"] is False and compared["local_applicability_verified"] is False
    assert 0 not in classes.item_indices and 0 not in members.item_indices
    if fault == "nets":
        assert failures[0]["code"] == "E_NATIVE_FAILURE"
        assert failures[0]["details"] == {"owned_error_code": 731}
    if fault == "class_item":
        assert [r["observation_index"] for r in rules["net_classes"]] == [1]
    if fault == "member_name":
        member_observation = rules["net_classes"][1]["membership_observation"]
        assert member_observation["collection"]["observed_count"] == 1
        assert member_observation["named_member_count"] == 0
    json.dumps(plan, allow_nan=False)


@pytest.mark.parametrize("fault", ["complete", "observed_empty", "observed_empty_classes"])
def test_actual_observed_empty_is_distinct_from_failed_collection(monkeypatch, fault):
    rules, _, _ = actual_adapter_rules(monkeypatch, fault)
    observation = rules["net_class_observation"]
    assert observation["complete"] and observation["enumeration_complete"]
    assert observation["membership_complete"] and observation["failures"] == []
    compared = T._named_width_comparison(rules, {**ITEM, "width": 0.25}, "11")
    assert compared["membership_complete"] is True
    if fault == "observed_empty_classes":
        assert compared["status"] == "unknown"
        assert (
            observation["collection"]["count_before"]
            == observation["collection"]["count_after"]
            == 0
        )
    else:
        assert compared["status"] == "below_observed_named_minimum"
        assert compared["delta_mm"] == pytest.approx(-0.004)
    if fault == "observed_empty":
        membership = rules["net_classes"][1]["membership_observation"]
        assert (
            membership["collection"]["count_before"] == membership["collection"]["count_after"] == 0
        )
        assert membership["named_members"] == []


def test_unread_first_class_preserves_second_class_identity_indices_and_width(monkeypatch):
    rules, classes, _ = actual_adapter_rules(monkeypatch, "class_first_item")
    assert classes.item_indices == [1, 2]
    assert len(rules["net_classes"]) == 1
    row = rules["net_classes"][0]
    assert row["observation_index"] == 2 and row["name"] == "Other"
    assert row["nets"] == ["B"] and row["widths"]["1"]["minimum"] == 0.3048
    assert row["membership_observation"]["named_members"] == [{"observation_index": 1, "name": "B"}]
    compared = T._named_width_comparison(rules, {**ITEM, "net": "B", "width": 0.25}, "12")
    assert compared["status"] == "unknown"
    assert compared["class_observation_indices"] == [0]
    assert compared["class_name"] == "Other"
    assert compared["observed_minimum_width_mm"] == 0.3048


def test_failed_member_item_attempts_consume_total_budget_across_classes(monkeypatch):
    """Low budget exercises the actual adapter loop, not a count-sized load."""
    monkeypatch.setattr(A, "_NET_CLASS_MEMBER_BUDGET", 2)
    first_members = NativeCollection([None, None])
    second_members = NativeCollection([None])
    calls = []

    def fail_first(index):
        calls.append(("first", index))
        raise RuntimeError(f"owned first failed Item[{index}]")

    def fail_second(index):
        calls.append(("second", index))
        raise RuntimeError(f"owned second failed Item[{index}]")

    first_members.Item, second_members.Item = fail_first, fail_second
    classes = NativeCollection(
        [NativeNamed("First", first_members), NativeNamed("Second", second_members)]
    )
    doc = object()
    monkeypatch.setattr(
        A, "_propget", lambda owner, member, *args: classes if owner is doc else 0.254
    )
    observation = {}
    rows = A._board_net_classes(doc, 1, observation=observation)
    compared = T._named_width_comparison(
        {"scheme": "(Master)", "net_classes": rows, "net_class_observation": observation},
        {**ITEM, "width": 0.25},
        "11",
    )
    assert calls == [("first", 1), ("first", 2)]
    assert observation["member_attempted_count"] == observation["member_budget"] == 2
    assert observation["member_budget_remaining"] == 0
    assert observation["complete"] is False and compared["status"] == "unknown"
    first = rows[0]["membership_observation"]["collection"]
    second = rows[1]["membership_observation"]["collection"]
    assert first["attempted_count"] == 2 and first["observed_count"] == 0
    assert second["attempted_count"] == second["observed_count"] == second["limit"] == 0
    assert [row["location"] for row in first["failures"]] == [
        "NetClasses.Item[1].Nets.Item[1]",
        "NetClasses.Item[1].Nets.Item[2]",
    ]
    assert second["failures"][0]["location"] == "NetClasses.Item[2].Nets.Count.before"
    assert "[0, 0]" in second["failures"][0]["message"]


def test_board_info_callsite_exposes_actual_member_failure_separately(monkeypatch, tmp_path):
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"owned synthetic board")
    doc = SimpleNamespace(
        LayerCount=1,
        Components=NativeCollection([]),
        Nets=NativeCollection([]),
        Traces=NativeCollection([]),
        Vias=NativeCollection([]),
        DisplaySchemes=(),
    )
    classes = NativeCollection(
        [
            NativeNamed("Default", NativeCollection([NativeNamed("A")])),
            NativeNamed("Unread", fault="nets"),
        ]
    )
    monkeypatch.setattr(A, "_layout_board_path", lambda params: board)
    monkeypatch.setattr(A, "_application", lambda *a, **k: SimpleNamespace(ActiveDocument=doc))
    monkeypatch.setattr(A, "_open_layout_document", lambda *a: (doc, []))
    monkeypatch.setattr(A, "_licensed_document", lambda *a: doc)
    monkeypatch.setattr(A, "_board_stackup", lambda *a: [])
    monkeypatch.setattr(A, "_board_keepouts", lambda *a: [])
    monkeypatch.setattr(A, "_cell_search_evidence", lambda *a: {})
    monkeypatch.setattr(A, "_propget", lambda owner, member, *a: classes if owner is doc else 0.254)
    result = A._board_info({"project": str(board)}, None)
    assert result["net_classes"][0]["nets"] == ["A"]
    assert result["net_class_observation"]["enumeration_complete"] is True
    assert result["net_class_observation"]["membership_complete"] is False
    assert result["net_class_observation"]["failures"][0]["location"] == "NetClasses.Item[2].Nets"
    assert "net_class_observation" in result["_untrusted"]


def test_public_discovery_exposes_completeness_errors_and_observed_empty(tmp_path):
    from test_trace_safety_process import run_process

    for command, schema_name in [("pcb routing trace", "pcb_trace"), ("pcb inspect", "pcb_info")]:
        reference = run_process(tmp_path, "reference", "--command", command, synthetic=False)
        assert "observed empty membership" in reference["command"]["description"]
        schema = reference["schemas"][schema_name]
        assert {"enumeration_complete", "membership_complete", "failures"} <= set(
            schema["net_class_observation_shape"]
        )
        assert {"location", "type", "message"} <= set(schema["net_class_read_failure_shape"])
        assert "count_before" in schema["net_class_collection_shape"]
        assert "count_after" in schema["net_class_collection_shape"]
        assert "complete" in schema["net_class_membership_shape"]
        boundary = schema[
            "named_width_comparison_boundary"
            if schema_name == "pcb_trace"
            else "net_class_observation_boundary"
        ]
        assert "Count=0" in boundary


def _load_compiled_reader_and_comparator(executable, folder):
    """Exercise exact shipped bytecode externally, without modifying a product entry point."""
    import PyInstaller
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(executable)
    entries = [name for name, row in archive.toc.items() if row[-1] == "z"]
    assert len(entries) == 1, "Expected the candidate's single embedded PYZ archive"
    pyz = archive.open_embedded_archive(entries[0])
    modules = {}
    for module in (A, T):
        code = pyz.extract(module.__name__)
        modules[module.__name__] = {
            "archive_bytecode_sha256": hashlib.sha256(
                pyz.extract(module.__name__, raw=True)
            ).hexdigest(),
            "checkout_source_path": str(Path(module.__file__).resolve()),
            "checkout_source_sha256": hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest(),
        }
        # Keep the existing module identity used by owned fixture objects. Only
        # the product module's exact packaged code is reexecuted in this worker.
        exec(code, module.__dict__)
    evidence = {
        "executable": str(Path(executable).resolve()),
        "exe_sha256": hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
        "archive": entries[0],
        "pyinstaller_version": PyInstaller.__version__,
        "python_version": sys.version,
        "modules": modules,
        "external_fixture_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "launcher_sha256": hashlib.sha256(
            (folder / "owned-json-worker.cmd").read_bytes()
        ).hexdigest()
        if (folder / "owned-json-worker.cmd").is_file()
        else None,
        "scope": "EXE bytecode over synthetic native shapes in external Python worker; "
        "other dependencies use the tested build-source checkout, not live COM",
    }
    (folder / "compiled-module-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )


def _external_worker():
    """Strict JSON worker fixture: never import/dispatch COM or accept apply=true."""
    patches = pytest.MonkeyPatch()
    request = json.load(sys.stdin)
    folder = Path(os.environ["XPEDITION_WIDTH_FIXTURE_DIR"])
    if os.environ.get("XPEDITION_CLI_PROCESS_EXE"):
        _load_compiled_reader_and_comparator(os.environ["XPEDITION_CLI_PROCESS_EXE"], folder)
    app, _, _, events = native_case(folder, patches, copper=True)
    rules, _, _ = actual_adapter_rules(patches, os.environ["XPEDITION_WIDTH_READ_FAULT"])
    app.ActiveDocument.state["rules"] = rules
    method, params = request["method"], request["params"]
    if method == "identity":
        result = {"application": {"pid": 4242, "synthetic": True}, "adapter": adapter_identity()}
    elif method == "snapshot":
        result = {"components": [], "fixture": True}
    elif method == "cell_refresh_state":
        assert params["strict"] and params["routing_objects"]
        result = app.ActiveDocument.state
    elif method == "hand_route":
        assert params["apply"] is False
        result = A._hand_route(params, None)
    else:
        raise AssertionError("Owned worker refuses method " + method)
    assert events == []
    with (folder / "worker-events.jsonl").open("a", encoding="utf-8") as output:
        output.write(
            json.dumps({"method": method, "apply": params.get("apply"), "events": events}) + "\n"
        )
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
    patches.undo()


@pytest.mark.skipif(os.name != "nt", reason="Windows final-candidate worker launcher")
@pytest.mark.parametrize(
    "fault,status",
    [
        ("complete", "below_observed_named_minimum"),
        ("nets", "unknown"),
        ("member_count", "unknown"),
    ],
)
def test_public_executable_reads_real_adapter_failure_chain(tmp_path, fault, status):
    """Final EXE runs its CLI; its extracted bytecode handles synthetic shapes externally."""
    from test_trace_safety_process import run_process

    # Creates the same public-scope recipe; reference is real executable discovery.
    reference = run_process(
        tmp_path, "reference", "--command", "pcb routing trace", synthetic=False
    )
    assert (
        "membership_observation"
        in reference["schemas"]["pcb_trace"]["named_width_comparison_shape"]
    )
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic owned board")
    launcher = tmp_path / "owned-json-worker.cmd"
    # Fixed trusted paths only; no CLI/user request text is inserted into shell code.
    assert not any(c in str(sys.executable) + str(__file__) for c in '%\r\n"')
    launcher.write_text(
        f'@echo off\r\n"{sys.executable}" "{Path(__file__).resolve()}"\r\n', encoding="utf-8"
    )
    prefix = (
        [os.environ["XPEDITION_CLI_PROCESS_EXE"]]
        if os.environ.get("XPEDITION_CLI_PROCESS_EXE")
        else [sys.executable, "-m", "xpedition_cli"]
    )
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(tmp_path / "config"),
        "XPEDITION_CLI_SCOPE_FILE": str(tmp_path / "process-scope.json"),
        "XPEDITION_NATIVE_COMMAND": str(launcher),
        "XPEDITION_WIDTH_FIXTURE_DIR": str(tmp_path),
        "XPEDITION_WIDTH_READ_FAULT": fault,
        "PYTHONPATH": str(ROOT),
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [
            *prefix,
            "pcb",
            "routing",
            "trace",
            "--project",
            str(board),
            "--net",
            "A",
            "--layer",
            "1",
            "--width",
            "0.25",
            "--points",
            "0,0 5,0",
            "--dry-run",
            "--compact",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=90,
        check=False,
    )
    (tmp_path / "cli-stdout.bin").write_bytes(result.stdout)
    (tmp_path / "cli-stderr.bin").write_bytes(result.stderr)
    (tmp_path / "cli-command.json").write_text(
        json.dumps(
            {
                "argv": result.args,
                "cwd": str(ROOT),
                "exit_code": result.returncode,
                "fixture_case": fault,
                "scope": "owned synthetic board/worker and isolated test journal; no live CAD",
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    body = json.loads(result.stdout)
    assert result.returncode == 0 and body["ok"], (body, result.stderr)
    preview = body["data"]["preview"]
    plan = preview["extensions"]["legacy_preview"]["trace"]
    assert plan["named_width_comparison"]["status"] == status
    assert preview["can_apply"] is False and plan["can_apply"] is False
    if fault != "complete":
        assert plan["named_width_comparison"]["membership_observation"]["failures"]
    calls = [
        json.loads(line) for line in (tmp_path / "worker-events.jsonl").read_text().splitlines()
    ]
    assert any(row["method"] == "hand_route" for row in calls)
    assert all(row["events"] == [] and row["apply"] is not True for row in calls)
    if os.environ.get("XPEDITION_CLI_PROCESS_EXE"):
        compiled = json.loads((tmp_path / "compiled-module-evidence.json").read_text())
        assert (
            compiled["exe_sha256"]
            == hashlib.sha256(
                Path(os.environ["XPEDITION_CLI_PROCESS_EXE"]).read_bytes()
            ).hexdigest()
        )
        assert set(compiled["modules"]) == {A.__name__, T.__name__}


if __name__ == "__main__":
    _external_worker()
