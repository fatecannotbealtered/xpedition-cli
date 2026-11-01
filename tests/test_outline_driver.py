"""Real SDK public outline chain; fake only its native JSON backend boundary."""

from __future__ import annotations

import copy

import pytest
from test_batch_material_validation import representative_board
from test_driver_v2 import _scope, _worker_fixture
from test_outline_readback import contours, facts, receipt

from xpedition_cli import outline_readback as O
from xpedition_cli.cli import common
from xpedition_cli.driver import Adapter, runtime
from xpedition_cli.errors import CLIError


class NativeBoundary:
    """Complete 127-component/467-terminal source, with independent fresh facts."""

    def __init__(self, board, mode="success"):
        self.board, self.mode, self.calls, self.apply_calls = board, mode, [], 0
        self.engineering_calls = []
        initial = contours(70, 150, 4) if mode == "noop" else contours()
        self.state = facts(initial, str(board))
        _, material = representative_board()
        material["stackup"] = copy.deepcopy(self.state["stackup"])
        self.state["protection"] = material
        self._sync_outline()
        names = {pin["net"] for row in material["components"] for pin in row["pins"]} | {"FOREIGN"}
        self.state["nets"] = [
            {"name": name, "uid": str(index + 901)} for index, name in enumerate(sorted(names))
        ]
        if mode == "missing_getter":
            self._missing_getter()

    def require_implemented(self):
        pass

    def _sync_outline(self):
        self.state["protection"]["model"]["outline"] = [
            {
                "kind": "outline",
                "path": [
                    list(row)
                    for row in zip(
                        *self.state["outlines"]["objects"]["board_outline"]["points_mm"],
                        strict=True,
                    )
                ],
            }
        ]

    def _missing_getter(self):
        self.state["outlines"]["complete"] = False
        self.state["outlines"]["objects"].pop("manufacturing_outline", None)
        self.state["outlines"]["errors"] = [
            {
                "object": "manufacturing_outline",
                "getter": "ManufacturingOutline",
                "observation_error": "AttributeError",
            }
        ]

    def _state_fields(self):
        return {
            key: copy.deepcopy(self.state[key])
            for key in ("identity", "outlines", "protection", "nets", "stackup")
        }

    def invoke(self, method, params, **kwargs):
        self.calls.append((method, copy.deepcopy(params), copy.deepcopy(kwargs)))
        if method == "identity":
            return {
                "application": {
                    "pid": 1203,
                    "document": {"open": True, "path": str(self.board)},
                    "fixture": True,
                },
                "adapter": _worker_fixture(),
            }
        if method == "snapshot":
            material = self.state["protection"]
            return {
                "components": copy.deepcopy(material["components"]),
                "pcb": {
                    "components": copy.deepcopy(material["components"]),
                    "tracks": copy.deepcopy(material["model"]["traces"]),
                    "vias": [],
                    "nets": copy.deepcopy(self.state["nets"]),
                },
                "fixture": True,
            }
        if method == "outline_state":
            assert params["observe_only"] is True
            assert params["start"] is False
            assert params["pid"] == 1203
            if self.mode == "readonly_failure":
                raise CLIError(
                    "E_NATIVE_FAILURE",
                    "Synthetic read-only outline getter did not return",
                    {"write_attempted": False, "native_query_only": True},
                )
            if self.mode == "after_readonly_failure" and self.apply_calls:
                raise CLIError(
                    "E_TIMEOUT",
                    "Synthetic independent after observation timed out",
                    {"write_attempted": False, "native_query_only": True},
                )
            state = self._state_fields()
            return {
                "pcb": str(self.board),
                **state,
                "state_guard": O.GUARD,
                "state_digest": O.digest(state),
                "write_attempted": False,
                "complete": state["outlines"]["complete"],
            }
        assert method == "board_outline", method
        try:
            expected = O.expectation(params, self.state["outlines"])
        except ValueError as error:
            raise CLIError(
                "E_UNSUPPORTED",
                "Synthetic native complete outline preflight unavailable",
                {"write_attempted": False},
            ) from error
        before = self._state_fields()
        answer = {
            "pcb": str(self.board),
            "identity": copy.deepcopy(self.state["identity"]),
            "before": before,
            "requested": expected["requested"],
            "outline_plan": expected,
            "state_guard": O.GUARD,
            "state_digest": O.digest(before),
            "applied": False,
            "saved": False,
            "write_attempted": False,
            "change_status": "not_applied",
            "persistence_state": "unchanged",
            "persistence_verified": False,
            "native_calls_returned": [],
            "lifecycle": {},
            "prompts": [],
        }
        if not params["apply"]:
            if self.mode == "preview_wrong_shape":
                answer["outline_plan"]["target"]["route_border"]["points_mm"][2][1] *= -1
            return answer
        assert params["state_guard"] == O.GUARD
        assert params["state_digest"] == answer["state_digest"]
        self.apply_calls += 1
        if expected["no_op"]:
            # Actual native execute's exact unchanged branch supplies this flag;
            # the SDK must retain its independent shape/material verification.
            answer.update(no_change=True, after=copy.deepcopy(before))
            return answer
        if self.mode == "forged_noop":
            answer.update(no_change=True, after=copy.deepcopy(before))
            return answer
        self.engineering_calls.extend(
            [
                "PutBoardOutline",
                "PutRouteBorder",
                "PutManufacturingOutline",
                "Save",
                "Close",
                "Open",
            ]
        )
        self.state["outlines"] = contours(70, 150, 4)
        if self.mode == "wrong_radius":
            self.state["outlines"] = contours(70, 150, 3)
        if self.mode == "companion_drift":
            target = self.state["outlines"]["objects"]["route_border"]
            target["points_mm"][2][1] *= -1
            target["points_mil"][2][1] *= -1
        if self.mode == "non_target_drift":
            self.state["protection"]["model"]["traces"][0]["net"] = "ACTUAL_FOREIGN_CHANGE"
        if self.mode == "reordered":
            self.state["protection"]["components"].reverse()
        if self.mode == "missing_after_getter":
            self._missing_getter()
        self._sync_outline()
        lifecycle = receipt(before)["lifecycle"]
        if self.mode == "save_false":
            lifecycle["save_returned"] = False
        if self.mode == "close_none":
            lifecycle["close_returned"] = None
        if self.mode == "wrong_pid":
            lifecycle["pid_after"] += 1
        if self.mode == "wrong_reopen":
            lifecycle["reopened_path"] = str(self.board.with_name("foreign.pcb"))
        answer.update(
            applied=True,
            saved=True,
            write_attempted=True,
            change_status="applied",
            persistence_state="saved",
            persistence_verified=True,
            lifecycle=lifecycle,
            native_calls_returned=receipt(before)["native_calls_returned"],
            after=self._state_fields(),
            verification={"valid": True, "persistence_verified": True},
        )
        return answer


def setup_case(tmp_path, monkeypatch, mode):
    _scope(tmp_path, monkeypatch)
    board = tmp_path / "unfixed-name-outline-729.pcb"
    board.write_bytes(b"offline native JSON boundary only")
    native = NativeBoundary(board, mode)
    monkeypatch.setattr(common, "NativeBackend", lambda: native)
    args = [
        "pcb",
        "outline",
        "set",
        "--project",
        str(board),
        "--width",
        "70",
        "--height",
        "150",
        "--radius",
        "4",
    ]
    return native, args


@pytest.mark.parametrize("mode", ["success", "reordered", "noop"])
def test_public_outline_complete_native_sdk_accepts_467_pin_lifecycle_or_exact_noop(
    tmp_path, monkeypatch, mode
):
    native, args = setup_case(tmp_path, monkeypatch, mode)
    with runtime(Adapter(native), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        assert not native.engineering_calls
        token = preview.envelope["data"]["confirm_token"]
        result = cli.run([*args, "--confirm", token])
        replay = cli.run([*args, "--confirm", token])
    assert result.envelope["ok"], result.envelope
    assert not replay.envelope["ok"]
    assert native.apply_calls == 1
    receipt_data = result.envelope["data"]["receipt"]
    after = receipt_data["snapshot_after"]["extensions"]["facts"]["outline_state"]
    assert sum(len(row["pins"]) for row in after["protection"]["components"]) == 467
    assert receipt_data["outcome"]["verification"]["status"] == "passed"
    assert receipt_data["extensions"]["file_readback"]["valid"] is True
    if mode == "noop":
        assert receipt_data["outcome"]["change"] == "not_applied"
        assert receipt_data["outcome"]["persistence"] == "unchanged"
        assert not native.engineering_calls
        assert receipt_data["extensions"]["persistence_independently_reopened"] is False
    else:
        assert receipt_data["outcome"]["change"] == "applied"
        assert receipt_data["outcome"]["persistence"] == "saved"
        assert native.engineering_calls.count("Save") == 1
        assert native.engineering_calls.count("Close") == 1
        assert native.engineering_calls.count("Open") == 1
        assert receipt_data["extensions"]["persistence_independently_reopened"] is True


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_radius",
        "companion_drift",
        "non_target_drift",
        "missing_after_getter",
        "save_false",
        "close_none",
        "wrong_pid",
        "wrong_reopen",
        "forged_noop",
    ],
)
def test_public_outline_native_success_echo_cannot_replace_independent_acceptance(
    tmp_path, monkeypatch, mode
):
    native, args = setup_case(tmp_path, monkeypatch, mode)
    with runtime(Adapter(native), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] in (
        {"E_OUTCOME_UNKNOWN", "E_VERIFY_INCOMPLETE"}
        if mode == "forged_noop"
        else {"E_VERIFY_INCOMPLETE"}
    )
    receipt_data = result.envelope["error"]["details"]["receipt"]
    assert receipt_data["outcome"]["verification"]["status"] == "incomplete"
    assert receipt_data["extensions"]["file_readback"]["valid"] is False
    assert native.apply_calls == 1


@pytest.mark.parametrize("mode", ["missing_getter", "preview_wrong_shape", "readonly_failure"])
def test_public_outline_incomplete_readonly_preflight_is_not_started_and_writes_nothing(
    tmp_path, monkeypatch, mode
):
    native, args = setup_case(tmp_path, monkeypatch, mode)
    with runtime(Adapter(native), state_dir=tmp_path / "state") as cli:
        result = cli.run([*args, "--dry-run"])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert native.apply_calls == 0
    assert not native.engineering_calls


def test_public_outline_confirm_observes_foreign_material_drift_before_any_write(
    tmp_path, monkeypatch
):
    native, args = setup_case(tmp_path, monkeypatch, "success")
    with runtime(Adapter(native), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        native.state["protection"]["model"]["traces"][0]["width"] = 0.381
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] in {"E_SNAPSHOT_DRIFT", "E_CONFLICT"}
    assert result.envelope["error"]["details"]["dispatch_state"] == "not_started"
    assert native.apply_calls == 0
    assert not native.engineering_calls


def test_public_outline_after_readonly_failure_retains_returned_native_evidence_without_replay(
    tmp_path, monkeypatch
):
    native, args = setup_case(tmp_path, monkeypatch, "after_readonly_failure")
    with runtime(Adapter(native), state_dir=tmp_path / "state") as cli:
        preview = cli.run([*args, "--dry-run"])
        assert preview.envelope["ok"], preview.envelope
        result = cli.run([*args, "--confirm", preview.envelope["data"]["confirm_token"]])
    assert not result.envelope["ok"], result.envelope
    assert result.envelope["error"]["code"] == "E_VERIFY_INCOMPLETE"
    receipt_data = result.envelope["error"]["details"]["receipt"]
    assert receipt_data["outcome"]["verification"]["status"] == "incomplete"
    assert receipt_data["extensions"]["native_reopen_reported"] is True
    assert receipt_data["extensions"]["save_acknowledged"] is True
    assert receipt_data["extensions"]["independent_readback_completed"] is False
    assert native.apply_calls == 1
    assert native.engineering_calls.count("Save") == 1
