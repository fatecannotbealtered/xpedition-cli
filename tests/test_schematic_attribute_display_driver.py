"""Offline public receipts require the driver's own attribute display observation."""

from __future__ import annotations

import copy
import ctypes
import json

import pytest

from xpedition_cli import __version__, driver
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.schematic_attribute_display import project_display


@pytest.fixture(autouse=True)
def no_native_entry(monkeypatch):
    from xpedition_cli import native_com_adapter

    hits = []

    def forbidden(*args, **kwargs):
        hits.append(True)
        pytest.fail("Independent receipt fixtures cannot activate native CAD/COM")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden)
    monkeypatch.setattr(ctypes, "OleDLL", forbidden)
    monkeypatch.setattr(native_com_adapter, "_import_com", forbidden)
    yield
    assert hits == []


def _design():
    refs = ["U1", "U2", "R1", "R2", "R3", "R4", "C1", "C2"]
    design = {
        "project": "offline-fixture",
        "revision": "native",
        "metadata": {"fixture": True},
        "sheets": [{"name": "sheet1"}, {"name": "sheet2"}],
        "components": [],
        "unnamed_components": [],
        "nets": [{"name": "SIGNAL"}, {"name": "GND"}],
        "connections": [
            {"net": "SIGNAL", "pins": [f"{ref}.1" for ref in refs]},
            {"net": "GND", "pins": [f"{ref}.2" for ref in refs]},
        ],
    }
    for index, ref in enumerate(refs):
        owner = {"refdes": ref, "uid": f"fixture-{ref}", "sheet": index % 2 + 1}
        component = {
            **owner,
            "internal_part_no": "fixture-part",
            "value": "4.7k",
            "package": "0402",
            "x": 100 + index * 50,
            "y": 200,
            "pins": [{"number": "1", "net": "SIGNAL"}, {"number": "2", "net": "GND"}],
            "pin_observation_coverage": {"complete": True, "errors": []},
            "identity_observation_coverage": {"complete": True, "errors": []},
            "attributes": {"Value": "4.7k", "Part Label": ref, "Part Number": "fixture-part"},
            "attribute_display": [],
            "attribute_display_coverage": {
                "names": [
                    "Name",
                    "Value",
                    "Part Label",
                    "Part Name",
                    "Part Number",
                    "Ref Designator",
                ],
                "enumeration_complete": True,
                "errors": [],
                "other_text_graphics_observed": False,
            },
        }
        for name, value in component["attributes"].items():
            component["attribute_display"].append(
                {
                    "name": name,
                    "value": value,
                    "owner": dict(owner),
                    "display": {
                        "x": 100 + index * 50,
                        "y": 220,
                        "visible": 0,
                        "size": 10,
                        "orientation": 0,
                        "origin": 1,
                        "units": "sheet_unit",
                    },
                    "getter_errors": {},
                }
            )
        # A failed optional getter on a protected, non-target record is retained.
        del component["attribute_display"][2]["display"]["size"]
        component["attribute_display"][2]["getter_errors"] = {"size": "fixture unavailable"}
        design["components"].append(component)
    return design


def _scope(tmp_path, monkeypatch):
    from xpedition_cli.audit import config_dir

    scope = tmp_path / "scope.json"
    scope.write_text(
        json.dumps(
            {
                "scope_id": "attribute-display-offline-fixture",
                "tool": "xpedition-cli",
                "granted_by": "offline fixture",
                "granted_at": "2026-10-06T00:00:00Z",
                "expires_at": None,
                "effects": ["observe", "session", "change", "persist"],
                "dangerous": True,
                "roots": [str(tmp_path.resolve()), str(config_dir().resolve())],
                "outputs": [str(tmp_path.resolve())],
                "destinations": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XPEDITION_CLI_SCOPE_FILE", str(scope))


def _run(
    tmp_path,
    monkeypatch,
    mutate=None,
    *,
    name="PartLabel",
    display=True,
    after_snapshot_error=False,
    reopened=True,
    mutate_result=None,
    mutate_identity=None,
    prepare_identity=None,
):
    _scope(tmp_path, monkeypatch)
    project = tmp_path / "owned.prj"
    project.write_bytes(b"offline attribute-display fixture")
    operation = {"type": "set_property", "refdes": "R1", "name": name, "value": "RC label"}
    if display:
        operation["display"] = {"x": 233, "y": 377, "visible": True}
    steps = [{**operation, "sheet": 1}]
    changes = tmp_path / "display.json"
    changes.write_text(json.dumps({"operations": [operation]}), encoding="utf-8")
    actual = _design()
    expected = copy.deepcopy(actual)
    if display:
        project_display(expected["components"][2], operation)
    observed_calls = []
    dispatches = []
    document = {
        "open": True,
        "path": str(project),
        "designs": ["Board1"],
        "status": "observed",
        "source": "IsProjectOpened/GetProjectData public getters",
    }
    application = {
        "pid": 4242,
        "active_project": str(project),
        "fixture": True,
        "document": document,
    }
    if prepare_identity:
        prepare_identity(application)

    class NativeFixture:
        def invoke(self, method, params, **kwargs):
            if method == "identity":
                return {
                    "application": copy.deepcopy(application),
                    "adapter": {"version": __version__, "build_sha256": "a" * 64, "fixture": True},
                }
            assert method == "snapshot"
            assert params["observe_only"] is True
            observed_calls.append(copy.deepcopy(actual))
            if dispatches and after_snapshot_error:
                from xpedition_cli.errors import CLIError

                raise CLIError("E_NATIVE_FAILURE", "fixture independent readback failed")
            return copy.deepcopy(actual)

    def handler(options):
        if options.get("dry_run"):
            return {
                "preview": {"changes": [], "steps": steps},
                "confirm_token": "private-fixture-token",
            }
        dispatches.append(True)
        actual.clear()
        actual.update(copy.deepcopy(expected))
        if mutate:
            mutate(actual)
        result = {
            "saved": True,
            "save_acknowledged": True,
            "observed": copy.deepcopy(expected),
            "verification": {
                "valid": True,
                "complete": True,
                "display_verified": True,
                "protection_verified": True,
                "persistence_verified": True,
            },
            "persistence_verified": True,
        }
        if reopened:
            result["lifecycle"] = {
                "source": "designer_save_close_reopen",
                "save_count": 1,
                "close_count": 1,
                "reopen_count": 1,
                "save_acknowledged": True,
                "closed_observed": True,
                "close_returned": True,
                "before_identity": copy.deepcopy(document),
                "reopened_identity": copy.deepcopy(document),
                "before_pid": 4242,
                "reopened_pid": 4242,
            }
            result["read_back_after_reopen"] = copy.deepcopy(expected)
        if mutate_result:
            mutate_result(result)
        if mutate_identity:
            mutate_identity(application)
        return result

    monkeypatch.setattr(driver, "_handler", lambda command: handler)
    args = ["schematic", "edit", "--project", str(project), "--file", str(changes)]
    with runtime(Adapter(NativeFixture()), state_dir=tmp_path / "state") as cli:
        planned = cli.run([*args, "--dry-run"])
        assert planned.envelope["ok"], planned.envelope
        token = planned.envelope["data"]["confirm_token"]
        applied = cli.run([*args, "--confirm", token])
        replay = cli.run([*args, "--confirm", token])
    payload = applied.envelope.get("data") or applied.envelope["error"]["details"]
    assert len(observed_calls) == 3
    assert dispatches == [True], applied.envelope
    assert not replay.envelope["ok"]
    return applied, payload["receipt"], expected


@pytest.mark.parametrize("name", ["Value", "Part Label", "PartLabel"])
def test_display_receipt_verifies_reopen_and_fresh_protected_state(tmp_path, monkeypatch, name):
    applied, receipt, expected = _run(tmp_path, monkeypatch, name=name)
    assert applied.envelope["ok"], applied.envelope
    outcome = receipt["outcome"]
    assert outcome["verification"]["status"] == "passed"
    assert outcome["verification"]["coverage"] == "complete"
    assert outcome["change"] == "applied" and outcome["persistence"] == "saved"
    assert receipt["snapshot_after"]["extensions"]["facts"]["native"] == expected
    readback = receipt["extensions"]["file_readback"]
    assert readback["source"] == "driver.after_observation.snapshot"
    assert readback["valid"] is readback["complete"] is True
    assert readback["display_verified"] is readback["protection_verified"] is True
    assert readback["persistence_verified"] is True
    assert receipt["extensions"]["result"]["save_acknowledged"] is True
    assert receipt["extensions"]["persistence_independently_reopened"] is True
    assert all(check["status"] == "passed" for check in outcome["verification"]["checks"])
    limitations = {row["id"] for row in outcome["verification"]["not_checked"]}
    assert "native_erc" in limitations and "independent_reopen_persistence" not in limitations
    assert "native_font_family_and_exact_text_envelope" in limitations
    assert all(row["required"] is False for row in outcome["verification"]["not_checked"])


def _target(snapshot):
    return snapshot["components"][2]["attribute_display"][1]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: _target(s)["display"].update(x=234), id="requested-x"),
        pytest.param(lambda s: _target(s)["display"].update(y=378), id="requested-y"),
        pytest.param(lambda s: _target(s)["display"].update(visible=0), id="requested-visibility"),
        pytest.param(lambda s: _target(s).update(value="wrong"), id="requested-value"),
        pytest.param(lambda s: _target(s)["display"].update(size=11), id="target-size-protected"),
        pytest.param(
            lambda s: _target(s)["display"].update(origin=2), id="target-origin-protected"
        ),
        pytest.param(lambda s: s["components"][7].update(uid="replaced"), id="eighth-identity"),
        pytest.param(lambda s: s["components"][7].update(x=777), id="eighth-placement"),
        pytest.param(lambda s: s["components"][7]["pins"][1].update(net="SIGNAL"), id="pin-net"),
        pytest.param(
            lambda s: s["components"][7]["attributes"].update(Extra="changed"), id="attribute"
        ),
        pytest.param(
            lambda s: s["components"][7]["attribute_display"][2]["display"].update(x=778),
            id="non-target-part-number-display",
        ),
        pytest.param(lambda s: s["connections"][0]["pins"].pop(), id="connection"),
        pytest.param(lambda s: s["nets"].pop(), id="net"),
        pytest.param(lambda s: s["sheets"].pop(), id="sheet"),
        pytest.param(lambda s: s["unnamed_components"].append({"uid": "orphan"}), id="unnamed"),
        pytest.param(lambda s: s["components"].pop(), id="component-deleted"),
        pytest.param(
            lambda s: s["components"].append(copy.deepcopy(s["components"][0])), id="duplicate"
        ),
    ],
)
def test_display_receipt_rejects_handler_success_when_fresh_observation_disagrees(
    tmp_path, monkeypatch, mutate
):
    applied, receipt, expected = _run(tmp_path, monkeypatch, mutate)
    assert not applied.envelope["ok"]
    assert receipt["outcome"]["verification"]["status"] in {"failed", "incomplete"}
    assert receipt["outcome"]["verification"]["coverage"] == "partial"
    assert receipt["extensions"]["file_readback"]["valid"] is False
    assert receipt["extensions"]["result"]["observed"] == expected
    assert receipt["snapshot_after"]["extensions"]["facts"]["native"] != expected
    assert receipt["extensions"]["file_readback"]["issues"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: _target(s)["display"].pop("x"), id="missing-position"),
        pytest.param(lambda s: _target(s)["display"].update(orientation=90), id="non-horizontal"),
        pytest.param(lambda s: _target(s).update(getter_errors={"x": "failed"}), id="getter-error"),
        pytest.param(lambda s: s["components"][2].pop("attribute_display"), id="missing-display"),
        pytest.param(lambda s: s.pop("connections"), id="missing-protection"),
        pytest.param(
            lambda s: s["components"][7].pop("pin_observation_coverage"),
            id="missing-non-target-pin-coverage",
        ),
        pytest.param(
            lambda s: s["components"][7]["pin_observation_coverage"].update(complete=False),
            id="partial-non-target-pin-coverage",
        ),
        pytest.param(
            lambda s: s["components"][7]["attribute_display_coverage"].update(
                enumeration_complete=False
            ),
            id="partial-non-target-attribute-enumeration",
        ),
        pytest.param(
            lambda s: s["components"][7].pop("attribute_display_coverage"),
            id="missing-non-target-attribute-coverage",
        ),
        pytest.param(
            lambda s: s["components"][2].update(attribute_display=None),
            id="null-target-attribute-records",
        ),
        pytest.param(
            lambda s: s["components"][2].update(attribute_display={"Value": "malformed"}),
            id="nonlist-target-attribute-records",
        ),
    ],
)
def test_display_receipt_keeps_missing_getters_incomplete(tmp_path, monkeypatch, mutate):
    _, receipt, _ = _run(tmp_path, monkeypatch, mutate)
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["extensions"]["file_readback"]["complete"] is False
    assert receipt["outcome"]["persistence"] == "unknown"


def test_ordinary_unsupported_property_stays_unknown_under_independent_driver_scope(
    tmp_path, monkeypatch
):
    _, receipt, _ = _run(tmp_path, monkeypatch, name="Tolerance", display=False)
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["outcome"]["verification"]["scope"] == (
        "independent requested postconditions and operation-aware protected records; "
        "all observed records across before-save/reopen/fresh snapshots"
    )
    assert receipt["outcome"]["verification"]["coverage"] == "partial"
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["extensions"]["result"]["persistence_verified"] is True
    assert receipt["extensions"]["file_readback"]["valid"] is False
    assert receipt["extensions"]["file_readback"]["persistence_verified"] is False
    assert receipt["extensions"]["persistence_independently_reopened"] is False


def test_display_reopen_claim_does_not_save_when_independent_readback_fails(tmp_path, monkeypatch):
    applied, receipt, _ = _run(tmp_path, monkeypatch, after_snapshot_error=True)
    assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["extensions"]["native_reopen_reported"] is True
    assert receipt["extensions"]["independent_readback_completed"] is False


def test_display_does_not_accept_a_persistence_flag_without_lifecycle(tmp_path, monkeypatch):
    applied, receipt, _ = _run(tmp_path, monkeypatch, reopened=False)
    assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert receipt["outcome"]["verification"]["status"] == "incomplete"
    assert receipt["outcome"]["persistence"] == "unknown"
    readback = receipt["extensions"]["file_readback"]
    assert readback["display_verified"] is readback["protection_verified"] is True
    assert readback["persistence_verified"] is False


def test_display_persistence_comes_from_evidence_instead_of_handler_flag(tmp_path, monkeypatch):
    applied, receipt, _ = _run(
        tmp_path,
        monkeypatch,
        mutate_result=lambda result: result.update(persistence_verified=False),
    )
    assert applied.envelope["ok"], applied.envelope
    assert receipt["extensions"]["result"]["persistence_verified"] is False
    assert receipt["extensions"]["file_readback"]["persistence_verified"] is True
    assert receipt["outcome"]["persistence"] == "saved"


@pytest.mark.parametrize(
    "mutate_result",
    [
        pytest.param(lambda r: r["lifecycle"].update(save_count=0), id="no-save"),
        pytest.param(lambda r: r["lifecycle"].update(close_count=0), id="no-close"),
        pytest.param(lambda r: r["lifecycle"].update(reopen_count=0), id="no-reopen"),
        pytest.param(lambda r: r["lifecycle"].update(reopen_count=2), id="repeated-reopen"),
        pytest.param(lambda r: r["lifecycle"].update(reopen_count=True), id="boolean-counter"),
        pytest.param(lambda r: r["lifecycle"].update(source="handler-echo"), id="wrong-source"),
        pytest.param(lambda r: r["lifecycle"].update(closed_observed=False), id="not-closed"),
        pytest.param(lambda r: r["lifecycle"].update(close_returned=False), id="close-rejected"),
        pytest.param(
            lambda r: r["lifecycle"].update(save_acknowledged=False), id="lifecycle-no-save-ack"
        ),
        pytest.param(lambda r: r.update(save_acknowledged=False), id="no-save-ack"),
        pytest.param(lambda r: r["lifecycle"].pop("before_identity"), id="missing-before-identity"),
        pytest.param(
            lambda r: r["lifecycle"].update(reopened_pid=4243), id="different-reopened-pid"
        ),
        pytest.param(
            lambda r: r["lifecycle"]["reopened_identity"].update(path="wrong.prj"),
            id="different-reopened-project",
        ),
        pytest.param(
            lambda r: r["lifecycle"]["reopened_identity"].update(designs=["WrongDesign"]),
            id="different-reopened-design",
        ),
        pytest.param(lambda r: r.pop("read_back_after_reopen"), id="missing-reopened-snapshot"),
        pytest.param(
            lambda r: r["read_back_after_reopen"]["components"][7]["pins"][1].update(net="SIGNAL"),
            id="reopened-pin-net-disagrees",
        ),
    ],
)
def test_display_reopen_evidence_cannot_be_replaced_by_success_flags(
    tmp_path, monkeypatch, mutate_result
):
    applied, receipt, _ = _run(tmp_path, monkeypatch, mutate_result=mutate_result)
    assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert receipt["outcome"]["persistence"] == "unknown"
    assert receipt["outcome"]["verification"]["status"] in {"failed", "incomplete"}
    assert receipt["extensions"]["file_readback"]["persistence_verified"] is False
    assert receipt["extensions"]["persistence_independently_reopened"] is False


@pytest.mark.parametrize(
    "mutate_identity",
    [
        pytest.param(lambda a: a.pop("document"), id="missing-live-project-getters"),
        pytest.param(lambda a: a["document"].update(open=False), id="live-project-closed"),
        pytest.param(lambda a: a["document"].update(path="wrong.prj"), id="live-other-project"),
        pytest.param(
            lambda a: a["document"].update(designs=["WrongDesign"]), id="live-other-design"
        ),
        pytest.param(
            lambda a: a["document"].update(source="handler-echo"), id="live-getter-source"
        ),
    ],
)
def test_display_reopen_requires_independent_project_identity(
    tmp_path, monkeypatch, mutate_identity
):
    applied, receipt, _ = _run(tmp_path, monkeypatch, mutate_identity=mutate_identity)
    assert applied.envelope["error"]["code"] == "E_OUTCOME_UNKNOWN"
    assert receipt["outcome"]["persistence"] == "unknown"
    readback = receipt["extensions"]["file_readback"]
    assert readback["persistence_verified"] is False
    assert readback["persistence_readback"]["identity_verified"] is False
