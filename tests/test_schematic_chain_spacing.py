"""The real dual-RC preview's cramped run, with an explicit bounded spacing choice.

The input reproduces preliminary-v0.json (native EXE preview retained externally
with 10 DS-17 findings). These are planner/public-process tests, not CAD evidence.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from xpedition_cli import schematic_layout as L

ROOT = Path(__file__).resolve().parents[1]


def dual_rc() -> dict:
    blocks = []
    for channel, row, top, refs in (
        ("A", 560, 630, ("J1", "R1", "C1", "J2")),
        ("B", 320, 390, ("J3", "R2", "C2", "J4")),
    ):
        incoming, resistor, capacitor, outgoing = refs
        blocks += [
            {
                "kind": "text",
                "text": f"CHANNEL {channel}  -  SYNTHETIC RC",
                "x": 110,
                "y": top,
                "size": 11,
            },
            {
                "kind": "connector",
                "refdes": incoming,
                "symbol": "HDR",
                "value": "HDR2",
                "x": 180,
                "y": row,
                "pins": {"1": f"label:{channel}_IN", "2": "gnd"},
            },
            {
                "kind": "chain",
                "x": 430,
                "y": row,
                "path": [
                    f"label:{channel}_IN",
                    {"refdes": resistor, "symbol": "RES", "value": "10k"},
                    f"label:{channel}_OUT",
                    {"refdes": capacitor, "symbol": "CAP", "value": "100n"},
                    "gnd",
                ],
            },
            {
                "kind": "connector",
                "refdes": outgoing,
                "symbol": "HDR",
                "value": "HDR2",
                "x": 900,
                "y": row,
                "pins": {"1": f"label:{channel}_OUT", "2": "gnd"},
            },
        ]
    return {
        "title": "DUAL RC TOOL ACCEPTANCE",
        "sheet_size": "A4",
        "status": "SYNTHETIC - NOT QUALIFIED",
        "revision": "T0",
        "date": "2026-10-17",
        "symbols": {
            "HDR": {
                "kind": "box",
                "right": [["1", "1"], ["2", "2"]],
                "pintypes": {"1": "ANALOG", "2": "ANALOG"},
            }
        },
        "sheets": [
            {
                "number": 1,
                "title": "TWO INDEPENDENT SIGNAL CHANNELS",
                "description": "Input left; output right; common GND only. R=10k, C=100n.",
                "blocks": blocks,
                "notes": [
                    "Synthetic values; no filter, EMC, current or manufacturing qualification.",
                    "All16 physical pins used; no NC. PCB routing subject to supported "
                    "effective-rule proof.",
                ],
            }
        ],
    }


def runs(design):
    return [b for b in design["sheets"][0]["blocks"] if b["kind"] in ("chain", "ladder")]


def one_run(kind="chain", **fields):
    return {
        "sheet_size": "A4",
        "sheets": [
            {
                "blocks": [
                    {
                        "kind": kind,
                        "x": 400,
                        "y": 600,
                        "path": [
                            "label:IN",
                            {"refdes": "R1", "symbol": "RES", "value": "10k"},
                            "label:OUT",
                            {"refdes": "C1", "symbol": "CAP", "value": "100n"},
                            "gnd",
                        ],
                        **fields,
                    }
                ]
            }
        ],
    }


def test_dual_rc_real_planner_failure_and_explicit_120_spacing():
    design = dual_rc()
    original = copy.deepcopy(design)
    cramped = L.plan(design)
    assert len(cramped.issues) == 10
    assert {i["check"] for i in cramped.issues} == {"DS-17"}
    assert any(
        i["object"] == "label A_OUT" and i.get("with") == "C1 (lines)" for i in cramped.issues
    )
    for block in runs(design):
        block["pitch"] = 120
    roomy = L.plan(design)
    assert roomy.issues == []
    assert (
        cramped.nets
        == roomy.nets
        == {
            "A_IN": {"J1.1", "R1.1"},
            "A_OUT": {"R1.2", "C1.1", "J2.1"},
            "B_IN": {"J3.1", "R2.1"},
            "B_OUT": {"R2.2", "C2.1", "J4.1"},
            "GND": {"J1.2", "J2.2", "C1.2", "J3.2", "J4.2", "C2.2"},
        }
    )
    assert roomy.links == cramped.links == []
    assert roomy.no_connects == cramped.no_connects == []
    assert len(roomy.parts) == 8
    for plan in (cramped, roomy):
        assert {
            (p["refdes"], p["orientation"])
            for p in plan.parts
            if p["refdes"].startswith(("R", "C"))
        } == {("R1", 0), ("C1", 0), ("R2", 0), ("C2", 0)}
    # Only the requested run spacing changed; no input or existing text is auto-edited.
    for block in runs(original):
        block["pitch"] = 120
    assert design == original


@pytest.mark.parametrize("kind", ["chain", "ladder"])
@pytest.mark.parametrize("flip", [False, True])
def test_spacing_preserves_pin_direction_and_net_identity(kind, flip):
    base = one_run(kind)
    for part in runs(base)[0]["path"][1::2]:
        part["flip"] = flip
    explicit_default = copy.deepcopy(base)
    runs(explicit_default)[0]["pitch"] = 60
    assert L.plan(base) == L.plan(explicit_default)
    wider = copy.deepcopy(base)
    runs(wider)[0]["pitch"] = 120
    old, new = L.plan(base), L.plan(wider)
    assert old.nets == new.nets
    head, tail = ("2", "1") if flip else ("1", "2")
    assert new.nets == {
        "IN": {f"R1.{head}"},
        "OUT": {f"R1.{tail}", f"C1.{head}"},
        "GND": {f"C1.{tail}"},
    }
    orientations = {p["refdes"]: p["orientation"] for p in new.parts}
    assert set(orientations.values()) == {
        ((1 if flip else 3) if kind == "ladder" else (2 if flip else 0))
    }
    r1 = next(p for p in new.parts if p["refdes"] == "R1")
    c1 = next(p for p in new.parts if p["refdes"] == "C1")
    if kind == "ladder":
        assert (r1["x"], r1["y"], c1["x"], c1["y"]) == (400, 540, 400, 420)
    else:
        assert (r1["x"], r1["y"], c1["x"], c1["y"]) == (460, 600, 580, 600)
    # Each labelled wire still terminates on the same physical pin, now at a longer
    # distance from its node; no endpoint is swapped by a spacing-only change.
    for plan in (old, new):
        first_wire = next(op for op in plan.ops if op["op"] == "wire")
        assert first_wire["end"] == f"R1.{head}"
        assert first_wire["points"][0] == [400, 600]
        assert all(
            x % 10 == 0 and y % 10 == 0
            for op in plan.ops
            if op["op"] == "wire"
            for x, y in op["points"]
        )


@pytest.mark.parametrize("pitch", [60, 80, 100, 120, 200])
def test_legal_integer_grid_pitch(pitch):
    assert len(L.plan(one_run(pitch=pitch)).parts) == 2


@pytest.mark.parametrize(
    "pitch",
    [
        True,
        False,
        None,
        "120",
        120.0,
        float("nan"),
        float("inf"),
        0,
        -60,
        59,
        61,
        70,
        4681,
        10**100,
    ],
)
def test_illegal_pitch_refuses_before_placing_parts(pitch, monkeypatch):
    monkeypatch.setattr(L._SheetPlanner, "part", lambda *a, **k: pytest.fail("not dispatched"))
    with pytest.raises(L.DesignError, match="pitch must be an integer"):
        L.plan(one_run(pitch=pitch))


def test_huge_run_budget_refuses_before_placing_parts(monkeypatch):
    design = one_run()
    block = runs(design)[0]
    block["path"] = ["label:IN"]
    for n in range(100):
        block["path"] += [{"refdes": f"R{n}", "symbol": "RES"}, f"label:N{n}"]
    monkeypatch.setattr(L._SheetPlanner, "part", lambda *a, **k: pytest.fail("not dispatched"))
    with pytest.raises(L.DesignError, match="sheet-length budget"):
        L.plan(design)


@pytest.mark.parametrize("key", ["node_pitch", "pitc", "spacing"])
def test_unknown_spacing_fields_are_not_silently_ignored(key):
    with pytest.raises(L.DesignError, match="unsupported fields"):
        L.plan(one_run(**{key: 120}))


def test_pitch_on_part_is_not_silently_ignored():
    design = one_run()
    runs(design)[0]["path"][1]["pitch"] = 120
    with pytest.raises(L.DesignError, match="part has unsupported fields"):
        L.plan(design)


def test_larger_spacing_keeps_drawable_boundary_reminders():
    design = one_run(pitch=120, x=1000)
    result = L.plan(design)
    assert any(i["check"] == "DS-07" for i in result.issues)


def process_preview(tmp_path, monkeypatch, design):
    from test_driver_v2 import _scope

    _scope(tmp_path, monkeypatch)
    source = tmp_path / "design.json"
    source.write_text(json.dumps(design), encoding="utf-8")
    target = tmp_path / "plan.png"
    exe = os.environ.get("XPEDITION_CLI_PROCESS_EXE")
    prefix = [exe] if exe else [sys.executable, "-m", "xpedition_cli"]
    result = subprocess.run(
        [
            *prefix,
            "schematic",
            "plan",
            "preview",
            "--design",
            str(source),
            "--output",
            str(target),
            "--dry-run",
            "--compact",
        ],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert not target.exists(), "preview must not generate an image or touch CAD"
    return result.returncode, json.loads(result.stdout)


def test_public_process_previews_real_rc_spacing_choice(tmp_path, monkeypatch):
    design = dual_rc()
    code, body = process_preview(tmp_path, monkeypatch, design)
    assert code == 0 and body["ok"] is True
    legacy = body["data"]["preview"]["extensions"]["legacy_preview"]
    assert len(legacy["design_preview"]["issues"]) == 10
    for block in runs(design):
        block["pitch"] = 120
    code, body = process_preview(tmp_path, monkeypatch, design)
    assert code == 0 and body["ok"] is True
    legacy = body["data"]["preview"]["extensions"]["legacy_preview"]
    assert legacy["design_preview"]["issues"] == []


@pytest.mark.parametrize("pitch", [70, True, 10**100])
def test_public_process_rejects_invalid_pitch_without_token(tmp_path, monkeypatch, pitch):
    code, body = process_preview(tmp_path, monkeypatch, one_run(pitch=pitch))
    assert code == 2 and body["ok"] is False
    assert body["error"]["code"] == "E_VALIDATION"
    assert body["error"]["details"]["dispatch_state"] == "not_started"
    assert "confirm_token" not in body.get("data", {})
