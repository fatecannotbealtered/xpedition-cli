"""Component CLI discovery and delegated payloads, with no native/SQLite access.

The private handler harness uses the actual parser and file confirmation gate.
These fake receiver plans are not native routing or public SDK acceptance.
"""

import copy
import hashlib
import json

import pytest
from test_board_components import connect_task

from xpedition_cli import board_components as C
from xpedition_cli import component_execution_budget as E
from xpedition_cli.cell_refresh import digest
from xpedition_cli.cli import pcb
from xpedition_cli.cli.registry import commands


@pytest.fixture
def component_case(tmp_path, monkeypatch):
    query = connect_task()[0]
    source = query["components"][0]
    target = query["components"][1]
    query_file, origin_file = tmp_path / "components.json", tmp_path / "origin.json"
    query_file.write_text(json.dumps(query), encoding="utf-8")
    origin = {"schema_version": 1, "isolated_copy": "synthetic receiver validates provenance"}
    origin_file.write_text(json.dumps(origin), encoding="utf-8")
    board = tmp_path / "owned.pcb"
    board.write_bytes(b"synthetic isolated fixture")

    class Backend:
        def __init__(self):
            self.calls = []
            self.plan_overrides = {}
            self.apply_overrides = {}

        def invoke(self, method, params, **kwargs):
            assert method == "hand_route"
            self.calls.append(copy.deepcopy(params))
            if params.get("mode") != "component-connect":
                assert params["apply"] is False
                return {
                    "trace": {
                        "fingerprint": "a" * 64,
                        "can_apply": False,
                        "blockers": ["legacy rule gap"],
                    }
                }
            request = params["component_connect_request"]
            pure = C.plan_connection(
                request["query"],
                first=request["first"],
                second=request["second"],
                points=request["points"],
                width_mm=request["width_mm"],
            )
            assert pure["structural_valid"]
            plan = {
                "fingerprint": digest(request),
                "can_apply": True,
                "blockers": [],
                "no_op": pure["no_op"],
                **self.plan_overrides,
            }
            if params["apply"]:
                assert params["expected"] == plan["fingerprint"]
                return {
                    "trace": plan,
                    "persistence_verified": True,
                    "protection": {"valid": True},
                    **self.apply_overrides,
                }
            return {
                "pcb": str(board),
                "trace": plan,
                # Synthetic receiver cost witness, not licensed timing evidence.
                "execution_resources": E.estimate(
                    0.01, 6, E.ENGINE_SECONDS, phase="preview", no_op=pure["no_op"]
                ),
            }

    backend = Backend()
    monkeypatch.setattr(pcb, "native", lambda: backend)
    args = [
        "pcb",
        "routing",
        "trace",
        "--project",
        str(board),
        "--mode",
        "component-connect",
        "--component-query",
        str(query_file),
        "--snapshot-id",
        query["snapshot_id"],
        "--from-component",
        source["component_id"],
        "--to-component",
        target["component_id"],
        "--from-node",
        source["node_ids"][0],
        "--to-node",
        target["node_ids"][0],
        "--isolated-origin",
        str(origin_file),
        "--recovery-backup",
        str(tmp_path / "new-closed-stage.zip"),
        "--net",
        query["target"]["net"],
        "--layer",
        str(query["target"]["layer"]),
        "--width",
        "0.254",
        "--points",
        "0,0 6,0",
    ]
    return query, query_file, origin, origin_file, backend, args


@pytest.mark.parametrize("fault", ["missing", "forged"])
def test_component_preview_requires_complete_recomputed_resource_report(
    cli, component_case, monkeypatch, fault
):
    *_, backend, args = component_case
    original_invoke = backend.invoke

    def malformed(method, params, **kwargs):
        response = original_invoke(method, params, **kwargs)
        if fault == "missing":
            response.pop("execution_resources", None)
        else:
            response["execution_resources"] = {
                **E.estimate(0.01, 6, E.ENGINE_SECONDS, phase="preview"),
                "estimated_seconds": 0.0,
            }
        return response

    monkeypatch.setattr(backend, "invoke", malformed)
    code, body = cli(*args, "--dry-run")
    assert code != 0 and body["error"]["code"] == "E_NATIVE_FAILURE"
    assert body["error"]["details"]["native_query_only"] is True
    assert body["error"]["details"]["write_attempted"] is False
    assert backend.calls and all(call["apply"] is False for call in backend.calls)


def change(args, flag, value):
    args = list(args)
    args[args.index(flag) + 1] = value
    return args


def test_discovery_keeps_existing_leaf_and_default_refusal(cli):
    code, body = cli("reference", "--command", "pcb routing trace")
    assert code == 0
    command = body["data"]["commands"][0]
    params = {p["name"]: p for p in command["params"]}
    assert params["mode"]["choices"] == ["trace", "component-connect"]
    assert {
        "component-query",
        "snapshot-id",
        "from-component",
        "to-component",
        "from-node",
        "to-node",
        "isolated-origin",
        "source-backup",
    } <= params.keys()
    assert "can_apply=false" in command["description"] and "no-ops" in command["description"]
    assert "Full effective-rule parsing" in command["description"]
    # The SDK contributes three operation leaves to the62 handler declarations.
    paths = {item.path for item in commands()} | {
        "operation list",
        "operation show",
        "operation resolve",
    }
    assert len(paths) == 65
    assert body["data"]["release_readiness"]["level"] == "unpublishable"
    assert {p["name"] for p in command["params"] if p.get("required")} >= {
        "net",
        "layer",
        "width",
        "points",
    }
    assert "file" not in params


@pytest.mark.parametrize("shape", ["direct", "envelope", "observation", "wrapped"])
def test_query_shapes_delegate_original_byte_pins_and_snapshot(cli, component_case, shape):
    query, query_file, origin, origin_file, backend, args = component_case
    if shape == "envelope":
        content = {
            "ok": True,
            "schema_version": "1",
            "data": {"observation": {"connectivity_components": query}},
        }
    elif shape == "observation":
        content = {"observation": {"connectivity_components": query}}
    elif shape == "wrapped":
        content = {"connectivity_components": query}
    else:
        content = query
    raw = json.dumps(content, indent=2).encode("utf-8")
    query_file.write_bytes(raw)
    code, body = cli(*args, "--dry-run")
    assert code == 0, body
    assert body["data"]["preview"]["mode"] == "component-connect"
    params = backend.calls[0]
    request = params["component_connect_request"]
    assert params["start"] is False and params["apply"] is False
    assert request["query"] == query and request["snapshot_id"] == query["snapshot_id"]
    assert request["net_uid"] == query["target"]["net_uid"]
    assert request["query_file"] == {
        "path": str(query_file.resolve()),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert request["isolated_origin"] == origin
    assert (
        request["isolated_origin_file"]["sha256"]
        == hashlib.sha256(origin_file.read_bytes()).hexdigest()
    )
    assert request["first"]["node_id"] == args[args.index("--from-node") + 1]


def test_real_handler_confirmation_delegates_exact_payload_once(cli, component_case):
    _, _, _, _, backend, args = component_case
    code, planned = cli(*args, "--dry-run")
    assert code == 0
    token = planned["data"]["confirm_token"]
    code, result = cli(*args, "--confirm", token)
    assert code == 0, result
    assert [r["apply"] for r in backend.calls] == [False, False, True]
    assert (
        backend.calls[0]["component_connect_request"]
        == backend.calls[-1]["component_connect_request"]
    )
    code, repeated = cli(*args, "--confirm", token)
    assert code != 0 and repeated["error"]["code"] == "E_CONFLICT"
    assert sum(r["apply"] is True for r in backend.calls) == 1


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--snapshot-id", "other snapshot"),
        ("--net", "not that net"),
        ("--layer", "2"),
        ("--from-component", "component:absent"),
        ("--from-node", "trace:absent"),
        ("--width", "nan"),
        ("--width", "0"),
        ("--points", "0,0 inf,1"),
        ("--points", "0,0 0,0"),
    ],
)
def test_invalid_or_mixed_snapshot_inputs_stop_before_native_preview(
    cli, component_case, flag, value
):
    *_, backend, args = component_case
    code, body = cli(*change(args, flag, value), "--dry-run")
    assert code != 0 and body["error"]["details"]["write_attempted"] is False
    assert backend.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"ok": False, "error": {}},
        {"connectivity_components": None},
        {"snapshot_id": "missing data"},
    ],
)
def test_invalid_query_envelope_stops_before_native_preview(cli, component_case, payload):
    _, query_file, _, _, backend, args = component_case
    query_file.write_text(json.dumps(payload), encoding="utf-8")
    code, body = cli(*args, "--dry-run")
    assert code != 0 and backend.calls == []
    assert body["error"]["details"]["write_attempted"] is False


def test_changed_snapshot_bytes_are_not_a_valid_stale_query(cli, component_case):
    query, query_file, _, _, backend, args = component_case
    query["components"][0]["node_ids"].append("unproven node")
    query_file.write_text(json.dumps(query), encoding="utf-8")
    code, body = cli(*args, "--dry-run")
    assert code != 0 and backend.calls == []
    assert body["error"]["code"] == "E_VALIDATION"


@pytest.mark.parametrize("field", ["verified", "rules_verified", "local_applicability_verified"])
def test_origin_cannot_supply_manual_verification(cli, component_case, field):
    _, _, origin, origin_file, backend, args = component_case
    origin[field] = True
    origin_file.write_text(json.dumps(origin), encoding="utf-8")
    code, _ = cli(*args, "--dry-run")
    assert code != 0 and backend.calls == []


def test_changed_origin_bytes_conflict_before_native_apply(cli, component_case):
    _, _, _, origin_file, backend, args = component_case
    _, planned = cli(*args, "--dry-run")
    origin_file.write_text(
        '{"schema_version": 1, "changed": "requires new preview"}', encoding="utf-8"
    )
    code, body = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code != 0 and body["error"]["code"] == "E_CONFLICT"
    assert all(r["apply"] is False for r in backend.calls)


def test_same_component_is_native_rechecked_noop_without_apply_or_save(cli, component_case):
    query, _, _, _, backend, args = component_case
    source = query["components"][0]
    args = change(
        change(args, "--to-component", source["component_id"]), "--to-node", source["node_ids"][0]
    )
    code, planned = cli(*args, "--dry-run")
    assert code == 0 and planned["data"]["preview"]["no_op"] is True
    code, body = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 0, body
    assert body["data"]["no_op"] is True and body["data"]["saved"] is False
    assert [r["apply"] for r in backend.calls] == [False, False]


@pytest.mark.parametrize(
    "overrides",
    [{"can_apply": False}, {"no_op": True}, {"fingerprint": "incomplete"}, {"blockers": None}],
)
def test_incomplete_or_blocked_preview_never_delegates_apply(cli, component_case, overrides):
    *_, backend, args = component_case
    backend.plan_overrides = overrides
    code, body = cli(*args, "--dry-run")
    if overrides == {"can_apply": False}:
        assert code == 0
        code, body = cli(*args, "--confirm", body["data"]["confirm_token"])
    assert code != 0 and all(r["apply"] is False for r in backend.calls)


def test_native_apply_verification_failure_retains_original_result(cli, component_case):
    *_, backend, args = component_case
    backend.apply_overrides = {
        "persistence_verified": False,
        "retained_native_fact": "saved reply known; reopen unknown",
    }
    _, planned = cli(*args, "--dry-run")
    code, body = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code != 0 and body["error"]["code"] == "E_VERIFY_INCOMPLETE"
    assert body["error"]["details"]["native_result"]["retained_native_fact"].startswith("saved")
    assert sum(r["apply"] is True for r in backend.calls) == 1


def test_legacy_trace_defaults_still_only_preview_blockers(cli, component_case):
    *_, backend, args = component_case
    keep = [
        "pcb",
        "routing",
        "trace",
        "--project",
        args[args.index("--project") + 1],
        "--net",
        "A",
        "--layer",
        "1",
        "--width",
        "0.254",
        "--points",
        "0,0 1,0",
    ]
    code, body = cli(*keep, "--dry-run")
    assert code == 0 and body["data"]["preview"]["can_apply"] is False
    assert len(backend.calls) == 1 and backend.calls[0]["apply"] is False
    assert "component_connect_request" not in backend.calls[0]


def test_component_flags_need_explicit_mode_and_no_legacy_override(cli, component_case):
    *_, backend, args = component_case
    for invalid in (change(args, "--mode", "trace"), [*args, "--dangerous"]):
        code, body = cli(*invalid, "--dry-run")
        assert code != 0 and body["error"]["code"] == "E_USAGE"
    assert backend.calls == []


def test_read_only_components_flag_survives_final_command_extensions(cli):
    code, body = cli("reference", "--command", "pcb inspect")
    assert code == 0
    command = body["data"]["commands"][0]
    params = {item["name"]: item for item in command["params"]}
    assert params["components"]["type"] == "boolean"
    assert command["effects"] == ["observe"]


def test_optional_source_backup_path_is_delegated(cli, component_case, tmp_path):
    *_, backend, args = component_case
    archive = tmp_path / "source.zip"
    archive.write_bytes(b"synthetic receiver validates the actual ZIP")
    code, body = cli(*args, "--source-backup", str(archive), "--dry-run")
    assert code == 0, body
    assert backend.calls[0]["source_backup"] == str(archive.resolve())


@pytest.mark.parametrize(
    "flag",
    [
        "--component-query",
        "--snapshot-id",
        "--from-component",
        "--to-component",
        "--isolated-origin",
    ],
)
def test_component_mode_specific_required_inputs_are_explicit(cli, component_case, flag):
    *_, backend, args = component_case
    position = args.index(flag)
    args = args[:position] + args[position + 2 :]
    code, body = cli(*args, "--dry-run")
    assert code != 0 and body["error"]["code"] == "E_USAGE" and backend.calls == []


@pytest.mark.parametrize(
    "raw", [b"", b"not json", b"\xff\xfeinvalid UTF8", b'{"extra":NaN}', b'{"extra":1e999}']
)
def test_invalid_json_bytes_and_nonfinite_values_never_reach_native(cli, component_case, raw):
    _, _, _, origin_file, backend, args = component_case
    origin_file.write_bytes(raw)
    code, body = cli(*args, "--dry-run")
    assert code != 0 and body["error"]["code"] == "E_VALIDATION" and backend.calls == []
