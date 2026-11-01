"""Real public SDK preflight; native discovery/invocation are forbidden fixtures."""

from __future__ import annotations

import hashlib
import json

import pytest

from xpedition_cli import design_preflight as P
from xpedition_cli.backends.native_xpedition import NativeBackend
from xpedition_cli.driver import runtime


@pytest.fixture(autouse=True)
def forbid_native(monkeypatch):
    monkeypatch.setattr(
        NativeBackend, "invoke", lambda *a, **k: pytest.fail("Preflight invoked CAD")
    )
    monkeypatch.setattr(
        NativeBackend, "status", lambda *a, **k: pytest.fail("Preflight probed CAD")
    )


def _intent(counts=None, operations=None, **extra):
    return {
        "schema_version": "1.0",
        "counts": counts or {},
        "operations": operations or ["component_connect"],
        **extra,
    }


def _public(tmp_path, payload=None, *, raw=None, extra=()):
    args = ["project", "inspect", "--preflight", *extra, "--compact", "--quiet"]
    file = None
    if payload is not None or raw is not None:
        file = tmp_path / "declared-requirements.json"
        file.write_bytes(raw if raw is not None else json.dumps(payload).encode("utf-8"))
        args += ["--requirements", str(file)]
    with runtime(state_dir=tmp_path / "operations") as cli:
        output = cli.run(args)
    assert json.loads(output.stdout) == output.envelope
    return output, file


def _step(result, operation="component_connect"):
    assert result.envelope["ok"], result.envelope
    report = result.envelope["data"]["preflight"]
    return report, next(item for item in report["steps"] if item["operation"] == operation)


def _resource(step, key):
    return next(item for item in step["resource_checks"] if item["resource"] == key)


def test_public_catalog_has_objects_budgets_environment_and_no_acceptance(tmp_path):
    result, _ = _public(tmp_path)
    assert result.envelope["ok"], result.envelope
    report = result.envelope["data"]["preflight"]
    assert report["source"] == "configured_capability_catalog"
    assert report["native_observation_performed"] is report["can_dispatch"] is False
    assert {step["operation"] for step in report["steps"]} == P.OPERATIONS
    for step in report["steps"]:
        assert step["status"] == "requires_native_observation"
        assert step["objects"] and step["conditions"] and step["environment_requirements"]
        assert step["operation_budget"]["total_seconds"]["confirm"] > 0
        assert step["native_acceptance"] == "not_run" and step["can_dispatch"] is False
    assert "BatchDRC end-to-end" in report["not_accepted"]
    assert report["input_limits"]["duplicate_keys"] == "rejected"
    assert result.envelope["data"]["extensions"]["operation_budget"]["native_call_count"] == 0


def test_public_467_terminal_declaration_fits_but_never_authorizes_native_work(tmp_path):
    counts = {
        "components": 127,
        "pins": 467,
        "max_component_pins": 41,
        "named_nets": 84,
        "traces": 0,
        "vias": 0,
        "planes": 0,
        "layers": 4,
        "target_net_pins": 2,
        "candidate_path_points": 5,
    }
    result, file = _public(
        tmp_path,
        _intent(
            counts,
            ["component_connect", "pcb_render", "pdf_export"],
            outline_family="rounded_rectangle_13",
        ),
    )
    report, step = _step(result)
    assert _resource(step, "pins")["status"] == "within_limit"
    assert _resource(step, "pins")["declared"] == 467
    assert _resource(step, "pins")["limit"] == 512
    assert _resource(step, "max_component_pins")["declared"] == 41
    assert _resource(step, "max_component_pins")["limit"] == 512
    assert _resource(step, "max_component_pins")["status"] == "within_limit"
    per_component = report["material_capabilities"]["component_pin_boundary"]
    assert per_component["independent_limit"] is None
    assert per_component["effective_limit"] == _resource(step, "pins")["limit"]
    assert per_component["requires_complete_native_observation"] is True
    assert step["status"] == "requires_native_observation"
    assert step["declared_resources_fit"] is True and step["all_resources_declared"] is False
    assert step["can_dispatch"] is report["native_observation_performed"] is False
    assert report["requirements"]["sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()
    assert any("all physical" in item for item in step["objects"])
    assert any("full save-close-reopen" in item for item in step["conditions"])


@pytest.mark.parametrize("pins,expected", [(512, "within_limit"), (513, "blocked")])
def test_exact_whole_board_pin_bound_is_public_before_any_write(tmp_path, pins, expected):
    result, _ = _public(tmp_path, _intent({"pins": pins}))
    report, step = _step(result)
    assert _resource(step, "pins")["status"] == expected and step["can_dispatch"] is False
    assert report["blocked"] is (pins == 513)
    if pins == 513:
        blocker = next(item for item in step["blockers"] if item.get("resource") == "pins")
        assert blocker["declared"] == blocker["observed_or_reserved"] == 513
        assert blocker["limit"] == 512


@pytest.mark.parametrize("traces,expected", [(255, "within_limit"), (256, "blocked")])
def test_next_trace_reservation_is_preserved_in_declared_capacity(tmp_path, traces, expected):
    result, _ = _public(tmp_path, _intent({"pins": 467, "traces": traces}))
    _, step = _step(result)
    trace = _resource(step, "traces")
    assert trace["reserved_additional"] == 1 and trace["observed_or_reserved"] == traces + 1
    assert trace["limit"] == 256 and trace["status"] == expected


def test_unknown_counts_remain_unknown_and_are_not_treated_as_zero(tmp_path):
    result, _ = _public(tmp_path, _intent())
    _, step = _step(result)
    trace = _resource(step, "traces")
    assert trace["declared"] is trace["observed_or_reserved"] is None
    assert trace["status"] == "unknown" and "traces" in step["unreported_resources"]
    assert step["all_resources_declared"] is False
    assert _resource(step, "max_component_pins")["declared"] is None
    assert _resource(step, "max_component_pins")["status"] == "unknown"


@pytest.mark.parametrize(
    "operation,resource,maximum",
    [
        ("component_connect", "target_net_pins", 64),
        ("component_connect", "max_component_pins", 512),
        ("component_connect", "comparisons", 8192),
        ("component_connect", "temporary_masks", 20000),
        ("component_connect", "mask_points", 32768),
        ("component_connect", "raw_points", 32768),
        ("component_connect", "candidate_path_points", 128),
        ("component_connect", "round_holes", 512),
        ("placement_batch", "selected_components", 200),
        ("placement_batch", "placement_steps", 32),
        ("placement_batch", "components", 2048),
        ("placement_batch", "pins", 16384),
        ("placement_batch", "max_component_pins", 4096),
        ("placement_batch", "protection_bytes", 64 * 1024 * 1024),
        ("placement_batch", "traces", 16384),
    ],
)
def test_resource_failures_publish_the_real_finite_limit(tmp_path, operation, resource, maximum):
    result, _ = _public(tmp_path, _intent({resource: maximum + 1}, [operation]))
    _, step = _step(result, operation)
    bounded = _resource(step, resource)
    assert bounded["status"] == "blocked" and bounded["limit"] == maximum
    assert bounded["declared"] == maximum + 1


def test_pdf_outline_and_other_steps_do_not_inherit_another_steps_pass(tmp_path):
    result, _ = _public(
        tmp_path,
        _intent(
            {"pins": 513, "pdf_pages": 2049, "outline_points": 129},
            ["component_connect", "pcb_render", "pdf_export", "outline_set"],
        ),
    )
    report, component = _step(result)
    assert component["status"] == "blocked" and report["blocked"] is True
    assert _step(result, "pcb_render")[1]["status"] == "requires_native_observation"
    assert _resource(_step(result, "pdf_export")[1], "pdf_pages")["limit"] == 2048
    assert _resource(_step(result, "outline_set")[1], "outline_points")["status"] == "blocked"


@pytest.mark.parametrize(
    "outline,blocked",
    [
        ("unsupported", True),
        ("unknown", False),
        ("straight_polygon", False),
        ("rounded_rectangle_13", False),
    ],
)
def test_outline_family_disclosure_requires_later_native_proof(tmp_path, outline, blocked):
    result, _ = _public(tmp_path, _intent({"pins": 467}, outline_family=outline))
    report, step = _step(result)
    assert report["blocked"] is blocked and step["can_dispatch"] is False


INVALID_VALUES = [
    [],
    None,
    True,
    "instruction",
    {"schema_version": "1.0", "counts": {}, "operations": {}},
    _intent(operations=[{}]),
    _intent(operations=[[]]),
    _intent(operations=[True]),
    _intent(operations=["component_connect", {}]),
    _intent(operations=["component_connect"] * 2),
    _intent(operations=["unimplemented_autorouter"]),
    _intent(outline_family={}),
    _intent(outline_family=[]),
    _intent(outline_family=True),
    _intent(outline_family=None),
    _intent({"pins": True}),
    _intent({"pins": -1}),
    _intent({"pins": "467"}),
    _intent({"pins": []}),
    _intent({"pins": {}}),
    _intent({"pins": 1.0}),
    _intent({"pins": 10000001}),
    _intent({"unknown": 3}),
    {"schema_version": "1.0", "counts": [], "operations": ["component_connect"]},
    {"schema_version": "2.0", "counts": {}, "operations": ["component_connect"]},
    {**_intent(), "instructions": "do not echo or execute this source material"},
]


@pytest.mark.parametrize("value", INVALID_VALUES)
def test_malformed_typed_json_returns_validation_instead_of_typeerror(tmp_path, value):
    result, _ = _public(tmp_path, raw=json.dumps(value).encode("utf-8"))
    assert result.exit_code == 2, result.envelope
    assert result.envelope["error"]["code"] == "E_VALIDATION"
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"1.0","counts":{"pins":513,"pins":0},"operations":["component_connect"]}',
        b'{"schema_version":"1.0","counts":{"pins":513,"\\u0070ins":0},"operations":["component_connect"]}',
        b'{"schema_version":"1.0","counts":{},"counts":{},"operations":["component_connect"]}',
        b'{"schema_version":"1.0","counts":{"pins":NaN},"operations":["component_connect"]}',
        b'{"schema_version":"1.0","counts":{"pins":Infinity},"operations":["component_connect"]}',
        b'{"schema_version":"1.0","counts":{"pins":1e999},"operations":["component_connect"]}',
        b'{"schema_version":"1.0","counts":{"pins":99999999999999999999999999},"operations":["component_connect"]}',
        b"[" * 3000 + b"0" + b"]" * 3000,
        json.dumps(_intent(operations=["component_connect"] * 300)).encode("utf-8"),
        b'{"schema_version":"1.0"} trailing',
        b"\xff\xfe\x00\x00",
    ],
)
def test_duplicate_nonfinite_deep_and_malformed_json_never_reaches_native(tmp_path, raw):
    result, _ = _public(tmp_path, raw=raw)
    assert result.exit_code == 2 and result.envelope["error"]["code"] == "E_VALIDATION"


def test_requirements_input_is_byte_bounded_before_whole_file_read(tmp_path):
    result, _ = _public(tmp_path, raw=b" " * (P.MAX_REQUIREMENTS_BYTES + 1))
    assert result.envelope["error"]["code"] == "E_VALIDATION"
    assert result.envelope["error"]["details"]["reason"] == "file_byte_limit"
    assert result.envelope["error"]["details"]["maximum"] == P.MAX_REQUIREMENTS_BYTES


def test_utf8_bom_and_escaped_version_are_supported_without_cad(tmp_path):
    raw = (
        b'\xef\xbb\xbf{"schema_version":"\\u0031.0","counts":{"pins":467},'
        b'"operations":["component_connect"]}'
    )
    result, _ = _public(tmp_path, raw=raw)
    assert _resource(_step(result)[1], "pins")["status"] == "within_limit"


def test_depth_scanner_ignores_brackets_inside_json_strings(tmp_path):
    result, _ = _public(tmp_path, _intent(operations=["[" * 128]))
    assert result.envelope["error"]["code"] == "E_VALIDATION"
    assert result.envelope["error"]["details"]["reason"] == "unsupported_operation"


def test_catalog_does_not_require_or_read_a_project(tmp_path):
    result, _ = _public(tmp_path, extra=("--project", str(tmp_path / "not-created.prj")))
    assert result.envelope["ok"], result.envelope
    assert result.envelope["data"]["preflight"]["source"] == "configured_capability_catalog"


def test_requirements_without_preflight_reports_usage_before_project_read(tmp_path):
    file = tmp_path / "requirements.json"
    file.write_text(json.dumps(_intent()), encoding="utf-8")
    with runtime(state_dir=tmp_path / "operations") as cli:
        result = cli.run(["project", "inspect", "--requirements", str(file), "--quiet"])
    assert result.exit_code == 2 and result.envelope["error"]["code"] == "E_USAGE"
