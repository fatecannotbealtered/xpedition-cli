from __future__ import annotations

import copy
import json

import pytest

from xpedition_cli.errors import CLIError
from xpedition_cli.pin_assignment import assess, read_assignments, read_snapshot, run, validate_argv


def snapshot():
    return {
        "project": "fixture",
        "revision": "R1",
        "components": [
            {
                "refdes": "J1",
                "pins": [
                    {"number": "01", "net": "OLD"},
                    {"number": "1", "net": None},
                    {"number": "A1"},
                    {"number": "B2"},
                ],
            },
            {"refdes": "U1", "pins": [{"number": "2", "net": "OLD"}]},
        ],
        "connections": [
            {"net": "OLD", "pins": ["J1.01", "U1.2"]},
            {"net": "SIG", "pins": ["J1.A1"]},
        ],
    }


def rows(text="J1,01,NEW\nJ1,1,+3V3\nJ1,A1,SIG\nJ1,B2,X\n"):
    return read_assignments(("refdes,pin,net\n" + text).encode())


def test_exact_ids_actions_peers_and_unknown():
    value = assess(snapshot(), rows())
    assert [r["action"] for r in value["items"]] == ["reassign", "connect", "noop", "blocked"]
    assert value["items"][0]["peer_sample"] == ["U1.2"]
    assert value["items"][0]["requires_isolation_review"]
    assert value["items"][1]["observed_known"] and value["items"][1]["observed_net"] is None
    assert not value["items"][3]["observed_known"]
    assert not value["valid"] and not value["execution"]["supported"]
    assert "confirm_token" not in value and "operations" not in value


def test_plan_and_post_observation_check_are_separate():
    assignments = read_assignments(b"refdes,pin,net,expected_net\nJ1,01,NEW,OLD\n")
    original = snapshot()
    assert assess(original, assignments)["valid"]
    assert assess(original, assignments, check=True)["matches"] is False
    observed = snapshot()
    observed["components"][0]["pins"][0]["net"] = "NEW"
    observed["connections"][0]["pins"] = ["U1.2"]
    observed["connections"].append({"net": "NEW", "pins": ["J1.01"]})
    assert assess(observed, assignments, check=True)["matches"] is True
    assert not assess(observed, assignments)["valid"]


@pytest.mark.parametrize(
    "text",
    [
        "refdes,pin,net\nJ1,01,X\nJ1,01,Y\n",
        "refdes,pin,net\nJ1,01,\n",
        "refdes,pin,net\nJ1,01,X,extra\n",
        "refdes,pin,net,net\nJ1,01,X,Y\n",
        "refdes,pin\nJ1,01\n",
        "refdes,pin,net\n",
        "refdes,pin,net\n J1,01,X\n",
        'refdes,pin,net\nJ1,01,"bad\nname"\n',
        'refdes,pin,net\nJ1,01,"unterminated',
        "refdes,pin,net,bogus\nJ1,01,X,Y\n",
    ],
)
def test_invalid_csv_never_silently_skips(text):
    with pytest.raises(CLIError):
        read_assignments(text.encode())


def test_bom_cjk_power_and_bus_names_are_not_filtered():
    assignments = read_assignments(
        "\ufeffrefdes,pin,net\nJ1,01,信号[3]\nJ1,1,+3V3\nJ1,A1,$AUTO\n".encode()
    )
    assert [r["net"] for r in assignments] == ["信号[3]", "+3V3", "$AUTO"]


@pytest.mark.parametrize(
    "edit,code",
    [
        (
            lambda s: s["components"].append(copy.deepcopy(s["components"][0])),
            "component_ambiguous",
        ),
        (
            lambda s: s["components"][0]["pins"].append({"number": "01", "net": "OLD"}),
            "pin_ambiguous",
        ),
        (
            lambda s: s["connections"].append({"net": "OTHER", "pins": ["J1.01"]}),
            "conflicting_connectivity",
        ),
        (lambda s: s["components"][0]["pins"][0].update(net=None), "conflicting_connectivity"),
    ],
)
def test_ambiguous_or_inconsistent_evidence_blocks(edit, code):
    value = snapshot()
    edit(value)
    result = assess(value, rows("J1,01,NEW\n"))
    assert result["issues"][0]["code"] == code and not result["valid"]


def test_absence_of_connections_is_not_proof_of_disconnection():
    value = snapshot()
    value.pop("connections")
    result = assess(value, rows("J1,A1,SIG\n"), check=True)
    assert result["matches"] is None and not result["valid"]


def test_missing_components_pins_and_empty_pin_arrays():
    for assignments in (rows("X1,1,N\n"), rows("J1,9,N\n")):
        assert not assess(snapshot(), assignments)["valid"]
    value = snapshot()
    value["components"][0]["pins"] = []
    assert not assess(value, rows("J1,01,OLD\n"))["valid"]


def test_all_rows_assessed_before_paging_and_inputs_not_mutated():
    value, assignments = snapshot(), rows()
    before = copy.deepcopy((value, assignments))
    page = assess(value, assignments, limit=1)
    assert page["summary"]["requested"] == 4 and not page["valid"]
    assert page["count"] == 1 and page["next_offset"] == 1
    assert (value, assignments) == before
    assert assess(value, assignments, offset=999)["offset"] == 4


def test_peers_are_bounded_without_losing_total():
    value = snapshot()
    value["connections"][0]["pins"] += [f"U{i}.A1" for i in range(2, 30)]
    row = assess(value, rows("J1,01,NEW\n"))["items"][0]
    assert row["other_observed_pins_on_net"] == 29
    assert len(row["peer_sample"]) == 8 and row["peer_sample_truncated"]


@pytest.mark.parametrize(
    "data",
    [
        b"{}",
        b"[]",
        b'{"project":"x","project":"y"}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b"\xff",
        b'{"ok":false,"schema_version":"1.0","data":{}}',
    ],
)
def test_bad_snapshots_rejected(data):
    with pytest.raises(CLIError):
        read_snapshot(data)


def test_envelope_and_known_partial_snapshots():
    value = snapshot()
    assert (
        read_snapshot(json.dumps({"ok": True, "schema_version": "1.0", "data": value}).encode())
        == value
    )
    for flag in ("has_more", "truncated", "incomplete"):
        with pytest.raises(CLIError):
            read_snapshot(json.dumps({**value, flag: True}).encode())


@pytest.mark.parametrize(
    "args",
    [
        ["--backend", "native_xpedition"],
        ["--confirm", "secret"],
        ["--dry-run"],
        ["--input", "--file", "x"],
        ["--input=x", "--input=y"],
        ["--limit", "-1"],
        ["--output", "x"],
        ["--json", "--format", "json"],
    ],
)
def test_strict_command_flag_boundary(args):
    with pytest.raises(CLIError):
        validate_argv(["schematic", "pin-plan", *args])


def test_inline_dash_path_and_zero_offset():
    validate_argv(
        ["--compact", "schematic", "pin-plan", "--input=-x", "--file", "y", "--offset", "0"]
    )


def test_file_hashes_and_bounded_inputs(tmp_path, monkeypatch):
    import xpedition_cli.pin_assignment as module

    path = tmp_path / "snapshot.json"
    csv = tmp_path / "pins.csv"
    path.write_text(json.dumps(snapshot()))
    csv.write_text("refdes,pin,net\nJ1,01,NEW\n")
    opts = {"input": str(path), "file": str(csv)}
    result = run(("schematic", "pin-plan"), opts)
    assert len(result["source"]["snapshot_sha256"]) == 64
    assert result["source"]["freshness"] == "not_checked"
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 5)
    with pytest.raises(CLIError):
        run(("schematic", "pin-plan"), opts)


def test_explicit_empty_expected_means_unconnected():
    assignments = read_assignments(b"refdes,pin,net,expected_net\nJ1,1,X,\n")
    assert assess(snapshot(), assignments)["valid"]
    assignments[0]["pin"] = "01"
    assert not assess(snapshot(), assignments)["valid"]
