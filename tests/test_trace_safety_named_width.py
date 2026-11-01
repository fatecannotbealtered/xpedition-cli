"""Named minimum observations are discoverable, without enabling local rules or writes."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_trace_safety import ITEM, trace_state
from test_unroute_native import Collection

from xpedition_cli import board_trace as T
from xpedition_cli import native_com_adapter as A


def named_rules():
    return {
        "scheme": "(Master)",
        "local_applicability_verified": False,
        "net_class_observation": {
            "complete": True,
            "enumeration_complete": True,
            "membership_complete": True,
        },
        "net_classes": [
            {
                "name": "Default",
                "nets": ["A", "B"],
                "membership_observation": {"complete": True},
                "widths": {"1": {"minimum": 0.254}},
            }
        ],
    }


@pytest.mark.parametrize(
    "width,status,delta",
    [
        (0.25, "below_observed_named_minimum", -0.004),
        (0.254, "equal_observed_named_minimum", 0),
        (0.3, "above_observed_named_minimum", 0.046),
    ],
)
def test_named_width_comparison_keeps_units_source_and_never_enables_apply(width, status, delta):
    state = trace_state()
    state["rules"] = named_rules()
    item = {**ITEM, "width": width}
    before = copy.deepcopy(state)
    plan = T.bind(state, item, [], A)
    compared = plan["named_width_comparison"]
    assert compared["status"] == status
    assert compared["source_member"] == "NetClass.MinTraceWidth"
    assert compared["source_dispid"] == 7
    assert compared["source_invoke_kind"] == "PROPERTYGET"
    assert compared["scheme"] == "(Master)"
    assert (compared["net"], compared["net_uid"], compared["class_name"]) == ("A", "11", "Default")
    assert compared["class_observation_indices"] == [0]
    assert (compared["layer"], compared["unit"], compared["native_unit_argument"]) == (1, "mm", 4)
    assert compared["requested_width_mm"] == width
    assert compared["observed_minimum_width_mm"] == 0.254
    assert compared["delta_mm"] == pytest.approx(delta)
    assert compared["reasons"] == []
    assert compared["local_applicability_verified"] is False
    assert plan["rules"]["local_applicability_verified"] is False
    assert plan["can_apply"] is False
    assert plan["request"] == item and state == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_classes",
        "missing_membership",
        "unknown_membership",
        "repeated_class",
        "repeated_membership",
        "wrong_net",
        "missing_name",
        "missing_layer",
        "missing_minimum",
        "non_master",
        "missing_scheme",
        "nan",
        "infinite",
        "zero",
        "negative",
        "boolean",
        "numeric_string",
        "huge_integer",
    ],
)
def test_named_width_missing_ambiguous_and_invalid_values_remain_unknown(fault):
    rules = named_rules()
    row = rules["net_classes"][0]
    if fault == "missing_classes":
        del rules["net_classes"]
    elif fault == "missing_membership":
        del row["nets"]
    elif fault == "unknown_membership":
        rules["net_classes"].append({"name": "Unread", "nets": None})
    elif fault == "repeated_class":
        rules["net_classes"].append(copy.deepcopy(row))
    elif fault == "repeated_membership":
        row["nets"].append("A")
    elif fault == "wrong_net":
        row["nets"] = ["B"]
    elif fault == "missing_name":
        del row["name"]
    elif fault == "missing_layer":
        row["widths"] = {"2": {"minimum": 0.254}}
    elif fault == "missing_minimum":
        row["widths"]["1"] = {"typical": 0.254}
    elif fault == "non_master":
        rules["scheme"] = "Other"
    elif fault == "missing_scheme":
        del rules["scheme"]
    else:
        row["widths"]["1"]["minimum"] = {
            "nan": float("nan"),
            "infinite": float("inf"),
            "zero": 0,
            "negative": -0.254,
            "boolean": True,
            "numeric_string": "0.254",
            "huge_integer": 10**1000,
        }[fault]
    compared = T._named_width_comparison(rules, {**ITEM, "width": 0.25}, "11")
    assert compared["status"] == "unknown" and compared["reasons"]
    assert compared["delta_mm"] is None
    assert compared["local_applicability_verified"] is False
    json.dumps(compared, allow_nan=False)


@pytest.mark.parametrize("requested", [float("nan"), float("inf"), True, 0, 10**1000])
def test_invalid_requested_width_remains_json_safe_even_without_class_data(requested):
    compared = T._named_width_comparison({}, {**ITEM, "width": requested}, "11")
    assert compared["status"] == "unknown"
    assert compared["requested_width_mm"] is None
    assert "the requested width is invalid or non-finite" in compared["reasons"]
    json.dumps(compared, allow_nan=False)


@pytest.mark.parametrize("unread_minimum", [False, True])
def test_actual_named_class_reader_retains_comparison_or_explicit_unknown(
    monkeypatch, unread_minimum
):
    native_class = NS(Name="Default", Nets=Collection([NS(Name="A"), NS(Name="B")]))
    doc = NS()
    calls = []

    def propget(owner, member, *args):
        calls.append((member, args))
        if owner is doc:
            assert (member, args) == (24, ("*",))
            return Collection([native_class])
        assert owner is native_class and args == (1, "(Master)", 4)
        if member == 7 and unread_minimum:
            raise A.AdapterError("E_NATIVE_FAILURE", "owned missing minimum getter")
        return 0.254

    monkeypatch.setattr(A, "_propget", propget)
    observation = {}
    rules = {
        **named_rules(),
        "net_classes": A._board_net_classes(doc, 1, observation=observation),
        "net_class_observation": observation,
    }
    state = trace_state()
    state["rules"] = rules
    compared = T.bind(state, {**ITEM, "width": 0.25}, [], A)["named_width_comparison"]
    assert (7, (1, "(Master)", 4)) in calls
    assert compared["status"] == ("unknown" if unread_minimum else "below_observed_named_minimum")
    assert compared["observed_minimum_width_mm"] == (None if unread_minimum else 0.254)
    assert bool(compared["reasons"]) is unread_minimum


def _public_main():
    import test_trace_safety as fixtures
    from test_trace_safety_process import _synthetic_main

    original = fixtures.native_case

    def native_case(*args, **kwargs):
        result = original(*args, **kwargs)
        result[0].ActiveDocument.state["rules"] = named_rules()
        return result

    fixtures.native_case = native_case
    return _synthetic_main()


def test_public_process_exposes_named_minimum_and_reference_without_native_write(tmp_path):
    from test_trace_safety_process import ROOT, run_process

    # Reuse the established CLI scope/environment recipe, while injecting only
    # owned synthetic native observations in the fresh source CLI subprocess.
    reference = run_process(
        tmp_path, "reference", "--command", "pcb routing trace", synthetic=False
    )
    schema = reference["schemas"]["pcb_trace"]
    assert "named_width_comparison" in schema["trace_plan_shape"]
    assert "status" in schema["named_width_comparison_shape"]
    assert "current effective local compliance" in schema["named_width_comparison_boundary"]
    bootstrap = (
        "import sys; "
        f"sys.path.insert(0, {str(ROOT / 'tests')!r}); "
        "from test_trace_safety_named_width import _public_main; "
        "raise SystemExit(_public_main())"
    )
    args = [
        "pcb",
        "routing",
        "trace",
        "--project",
        str(tmp_path / "owned.pcb"),
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
    ]
    env = {
        **os.environ,
        "XPEDITION_CLI_CONFIG_DIR": str(tmp_path / "config"),
        "XPEDITION_NATIVE_COMMAND": str(tmp_path / "no-live-adapter.exe"),
        "XPEDITION_TRACE_PROCESS_FIXTURE": str(tmp_path),
        "XPEDITION_TRACE_PROCESS_CASE": "copper",
        "XPEDITION_CLI_SCOPE_FILE": str(tmp_path / "process-scope.json"),
    }
    output = subprocess.run(
        [sys.executable, "-c", bootstrap, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    body = json.loads(output.stdout)
    assert output.returncode == 0 and body["ok"] is True, (body, output.stderr)
    preview = body["data"]["preview"]
    plan = preview["extensions"]["legacy_preview"]["trace"]
    assert plan["named_width_comparison"]["status"] == "below_observed_named_minimum"
    assert plan["named_width_comparison"]["requested_width_mm"] == 0.25
    assert plan["named_width_comparison"]["observed_minimum_width_mm"] == 0.254
    assert preview["can_apply"] is False and plan["can_apply"] is False
    assert plan["rules"]["local_applicability_verified"] is False
    events = json.loads((Path(tmp_path) / "synthetic-trace-native-events.json").read_text())
    assert events["events"] == []
    assert not any(row["method"] == "hand_route" and row["apply"] for row in events["calls"])
