"""Public source contracts and the packaged copy-registration recipe, offline only."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
import test_component_connect_cli
import test_project_isolated_copy as factory_tests
from jsonschema import Draft202012Validator

from xpedition_cli import board_component_connect as N
from xpedition_cli import board_components as C
from xpedition_cli.cli import pcb
from xpedition_cli.cli.registry import commands
from xpedition_cli.main import parse_argv
from xpedition_cli.reference_data import reference

ROOT = Path(__file__).resolve().parents[1]
component_case = test_component_connect_cli.component_case


def test_public_trace_reference_exposes_exact_receiver_schema_and_boundaries(component_case):
    *_, args = component_case
    _, options = parse_argv(args)
    payload = pcb.component_connect_options(options, options["project"])[
        "component_connect_request"
    ]
    selected = reference(command="pcb routing trace")
    schema = selected["schemas"]["pcb_trace"]
    request_schema = schema["component_connect_request_schema"]
    Draft202012Validator.check_schema(request_schema)
    # The backend wire format serializes internal point tuples as JSON arrays.
    Draft202012Validator(request_schema).validate(json.loads(json.dumps(payload)))
    assert set(request_schema["required"]) == N._REQUEST_KEYS
    assert set(request_schema["properties"]) == N._REQUEST_KEYS
    assert request_schema["additionalProperties"] is False
    boundary = schema["component_connect_boundary"]
    for clause in (
        "before insertion",
        "below-minimum",
        "unreadable point sample",
        "Master observations never become full",
        "complete public closed-copy",
        "prior saved receipt",
        "no-op without mutation eligibility",
        "TransactionStart(1)",
        "PutTrace(...,None,0,4)",
        "TransactionEnd(True)",
        "first/last vertices",
        "four-decimal-mm grid",
        "annulus",
        "pre-End observation",
        "every old raw UID",
        "BOOL=True",
        "Five exact session-marker paths",
        "authenticated absence",
        "protected_source_reports",
        "strict protected input in every phase",
        "never an allowed output or removable marker",
        "No lock or marker is deleted",
        "at most 17 complete",
        "with 500ms only between attempts",
        "does not guarantee native file-handle release",
        "No Save, Close, Open or copper insertion is replayed",
        "six existing backups",
        "Every source must remain unchanged after Open",
        "Undeclared working",
        "Global_Constants.WindowPlacement",
        "without automatic",
        "passed native write",
    ):
        assert clause in boundary
    assert schema["component_connect_full_effective_rule_parser_proven"] is False
    assert "recovery_backup" in request_schema["required"]
    assert "recovery_plan" in schema["component_connect_plan_shape"]
    assert "save_policy" in schema["component_connect_plan_shape"]
    assert "closed_snapshot_sampling" in schema["component_connect_plan_shape"]
    assert "closed_tree" in schema["component_connect_persistence_shape"]
    assert "recovery_archive" in schema["component_connect_persistence_shape"]
    assert "actual completed-stage" in schema["component_connect_persistence_field_scope"]
    assert "Default" in schema["target_acceptance_boundary"]
    assert "can_apply=false" in schema["native_routing_boundary"]
    assert selected["release_readiness"]["level"] == "unpublishable"


@pytest.mark.parametrize("fault", ["override", "width", "layer", "selector", "pin", "snapshot"])
def test_public_receiver_schema_refuses_manual_override_and_malformed_fields(component_case, fault):
    *_, args = component_case
    _, options = parse_argv(args)
    request = pcb.component_connect_options(options, options["project"])[
        "component_connect_request"
    ]
    request = copy.deepcopy(request)
    if fault == "override":
        request["rules_verified"] = True
    elif fault == "width":
        request["width_mm"] = 0
    elif fault == "layer":
        request["layer"] = 0
    elif fault == "selector":
        request["first"]["verified"] = True
    elif fault == "pin":
        request["query_file"]["sha256"] = "unbound"
    else:
        request["snapshot_id"] = ""
    schema = reference(command="pcb routing trace")["schemas"]["pcb_trace"][
        "component_connect_request_schema"
    ]
    assert list(Draft202012Validator(schema).iter_errors(request))


def test_public_query_schema_describes_real_snapshot_without_write_authority(component_case):
    query, *_ = component_case
    selected = reference(command="pcb inspect")
    schema = selected["schemas"]["pcb_info"]
    assert set(schema["connectivity_components_shape"]) == set(query)
    assert schema["connectivity_component_shape"] == ["component_id", "node_ids"]
    assert "connectivity_components" in schema["observation_optional_fields"]
    assert query["contract"] == C.CONTRACT
    for clause in (
        "exact snapshot",
        "Same pin identity does not join",
        "never opens",
        "does not authorize",
    ):
        assert clause in schema["connectivity_component_boundary"]
    paths = {item.path for item in commands()} | {
        "operation list",
        "operation show",
        "operation resolve",
    }
    assert len(paths) == 65


def test_documented_public_factory_registration_has_full_origin_shape(cli, tmp_path):
    source, archive, target, files = factory_tests.fixture(tmp_path)
    args = [
        "project",
        "create",
        "--template",
        str(source),
        "--project",
        str(target),
        "--closed-copy",
        "--source-backup",
        str(archive),
    ]
    code, planned = cli(*args, "--dry-run")
    assert code == 0
    code, complete = cli(*args, "--confirm", planned["data"]["confirm_token"])
    assert code == 0
    producer = complete["data"]
    assert producer["native_calls_dispatched"] == 0 and producer["file_copy_verified"]
    origin = copy.deepcopy(producer["closed_copy"]["registration"]["data"])
    # This test checks identifier/schema shape only; the native receiver must
    # independently authenticate the original successful SDK journal operation.
    origin["copy_operation_id"] = "op-syntheticcopy0001"
    origin_schema = reference(command="pcb routing trace")["schemas"]["pcb_trace"][
        "component_connect_origin_schema"
    ]
    Draft202012Validator.check_schema(origin_schema)
    Draft202012Validator(origin_schema).validate(origin)
    manual = {**origin, "verified": True}
    assert list(Draft202012Validator(origin_schema).iter_errors(manual))
    missing_operation = {key: value for key, value in origin.items() if key != "copy_operation_id"}
    assert list(Draft202012Validator(origin_schema).iter_errors(missing_operation))
    assert origin["kind"] == "isolated-component-connect"
    assert origin["source_backup"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    relocation = origin["provenance"]["project_library_relocation"]
    assert relocation["from_root"] == str(source.parent) and relocation["to_root"] == str(
        target.parent
    )
    assert origin["genesis"]["file_manifest"] == producer["closed_copy"]["actual_target_manifest"]
    for name, raw in files.items():
        assert (source.parent / name).read_bytes() == raw
        if name != "Board.prj":
            assert (target.parent / name).read_bytes() == raw


def test_documented_public_factory_never_overwrites_existing_target(cli, tmp_path):
    source, archive, target, _ = factory_tests.fixture(tmp_path)
    target.parent.mkdir()
    marker = target.parent / "protected.txt"
    marker.write_bytes(b"original")
    code, _ = cli(
        "project",
        "create",
        "--template",
        str(source),
        "--project",
        str(target),
        "--closed-copy",
        "--source-backup",
        str(archive),
        "--dry-run",
    )
    assert code != 0 and marker.read_bytes() == b"original" and not target.exists()


def test_public_skill_preserves_candidate_and_unqualified_routing_boundaries():
    skill = (ROOT / "skills/xpedition-pcb/SKILL.md").read_text(encoding="utf-8")
    flat = " ".join(skill.split())
    assert "../xpedition-cli/SKILL.md" in skill
    assert "STOP CHECKPOINT" in skill
    assert "reference" in skill
    assert "unknown" in skill.lower()
    assert "Silkscreen remains disabled" in skill
    assert "fresh complete board snapshot" in flat
    assert "public closed-copy registration" in flat
    assert "old-copper protections" in flat
    assert "not a complete effective-rule proof" in flat
    assert "A same-group no-op is not evidence of a write" in flat
    assert "partial or unknown result stops" in flat
    scenarios = json.loads(
        (ROOT / "skills/xpedition-pcb/test-prompts.json").read_text(encoding="utf-8")
    )
    assert len(scenarios["scenarios"]) >= 3
