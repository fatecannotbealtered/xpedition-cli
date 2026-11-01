"""Frozen hash metadata crosses original-ID recovery without becoming a path.

Only fresh temporary MAC files and synthetic generation observations are used.
No freezer, COM, CAD, real process lookup or historical runtime is accessed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_designer_frozen_binding import bundle  # noqa: F401 - synthetic package fixture
from test_designer_owner_recovery import closed_scene, no_native, scene  # noqa: F401

from xpedition_cli import designer_frozen_binding as F
from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_client as C
from xpedition_cli import designer_owner_recovery as R
from xpedition_cli.errors import CLIError


def _replace_binding(case, value):
    """Construct a fresh coherent historical fixture, never alter a real record."""
    case.source = copy.deepcopy(value)
    case.intent["source_sha256"] = case.source
    case.reference["source_bundle_sha256"] = R._digest(case.source)
    (case.root / "intent.json").write_bytes(R.L._pack(case.intent, case.key))
    for path in (
        R.pending_start_path(case.operation, case.config),
        R._locator_path(case.reference, case.config),
    ):
        private = json.loads(path.read_text(encoding="utf-8"))
        private["source_binding"] = case.source
        path.write_text(json.dumps(private), encoding="utf-8")


def _no_source_paths(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A frozen/historical source key was treated as a source-file path")

    monkeypatch.setattr(Path, "with_name", forbidden)


@pytest.mark.parametrize("generation", ["current", "previous12", "legacy11"])
def test_frozen_failed_start_recovery_retains_exact_current_or_historical_key_evidence(
    bundle,  # noqa: F811 - imported synthetic fixture
    scene,  # noqa: F811 - imported synthetic fixture
    monkeypatch,
    generation,
):
    value = F.frozen_source_binding()
    assert len(value) == len(F.OWNER_SOURCE_NAMES) + 3
    if generation != "current":
        value.pop("designer_dual_invoke.py")  # Previous 12-source/15-key bundle.
    if generation == "legacy11":
        value.pop("schematic_edit_protection.py")  # Original 11-source/14-key bundle.
    _replace_binding(scene, value)
    _no_source_paths(monkeypatch)
    record, state = copy.deepcopy(scene.record), copy.deepcopy(scene.state)
    proof = scene.observe()
    assert proof.matches(scene.state)
    assert proof.safe_summary["original_generations_absent"] is True
    assert scene.record == record and scene.state == state
    assert scene.opened == scene.closed == [101, 102, 103]
    assert (
        json.loads((scene.root / "intent.json").read_bytes())["payload"]["source_sha256"] == value
    )


def test_frozen_unknown_close_recovery_preserves_mac_chain_and_original_sdk(
    bundle,  # noqa: F811 - imported synthetic fixture
    closed_scene,  # noqa: F811 - imported synthetic fixture
    monkeypatch,
):
    case = closed_scene
    _replace_binding(case, F.frozen_source_binding())
    before = {path: path.read_bytes() for path in case.root.rglob("*") if path.is_file()}
    record = copy.deepcopy(case.close_record)
    _no_source_paths(monkeypatch)
    proof = case.observe()
    assert proof.safe_summary["protocol"] == R.CLOSED_PROTOCOL
    assert proof.safe_summary["original_close_operation_id"] == case.close_id
    assert proof.safe_summary["original_close_status"] == "unknown"
    assert case.close_record == record
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize(
    "key", ["@frozen/executable", "@frozen/payload_manifest", "@frozen/payload_tree"]
)
def test_historical_frozen_metadata_is_not_ignored_by_recovery(
    bundle,  # noqa: F811 - imported synthetic fixture
    scene,  # noqa: F811 - imported synthetic fixture
    monkeypatch,
    key,
):
    _replace_binding(scene, F.frozen_source_binding())
    scene.intent["source_sha256"][key] = "b" * 64
    (scene.root / "intent.json").write_bytes(R.L._pack(scene.intent, scene.key))
    _no_source_paths(monkeypatch)
    with pytest.raises(CLIError):
        scene.observe()
    assert scene.opened == [] and scene.record["status"] == "unknown"


@pytest.mark.parametrize("generation", ["previous12", "legacy11"])
@pytest.mark.parametrize("packaging", ["source", "frozen"])
def test_previous_source_graphs_remain_protected_but_never_admit_current_client(
    bundle,  # noqa: F811 - imported synthetic fixture
    scene,  # noqa: F811 - imported synthetic fixture
    monkeypatch,
    generation,
    packaging,
):
    value = F.frozen_source_binding()
    value.pop("designer_dual_invoke.py")
    if generation == "legacy11":
        value.pop("schematic_edit_protection.py")
    if packaging == "source":
        value = {name: digest for name, digest in value.items() if not name.startswith("@")}
    _replace_binding(scene, value)
    _no_source_paths(monkeypatch)
    record = copy.deepcopy(scene.record)
    proof = scene.observe()
    assert proof.matches(scene.state)
    assert scene.record == record and scene.record["status"] == "unknown"
    opened = list(scene.opened)
    checked = []
    private = json.loads(R._locator_path(scene.reference, scene.config).read_text(encoding="utf-8"))
    locator = C.OwnerLocator(C._SEAL, scene.reference, private, lambda: checked.append(True))
    monkeypatch.setattr(C, "_modules", lambda: O)
    with pytest.raises(CLIError):
        C._ticket(locator)
    assert checked == [True]
    assert scene.opened == opened  # Rejection performs no new native/process lookup.
    assert scene.record == record


@pytest.mark.parametrize("changed", [None, "locator_metadata", "current_executable"])
def test_reopened_client_ticket_revalidates_all_frozen_bytes_without_path_keys(
    bundle,  # noqa: F811 - imported synthetic fixture
    scene,  # noqa: F811 - imported synthetic fixture
    monkeypatch,
    changed,
):
    _replace_binding(scene, F.frozen_source_binding())
    checked = []
    private = json.loads(R._locator_path(scene.reference, scene.config).read_text(encoding="utf-8"))
    locator = C.OwnerLocator(C._SEAL, scene.reference, private, lambda: checked.append(True))
    monkeypatch.setattr(C, "_modules", lambda: O)
    _no_source_paths(monkeypatch)
    if changed == "locator_metadata":
        locator.private["source_binding"]["@frozen/payload_tree"] = "b" * 64
    elif changed == "current_executable":
        bundle.binary.write_bytes(b"changed synthetic executable")
    if changed:
        with pytest.raises((CLIError, F.FrozenBindingError)):
            C._ticket(locator)
    else:
        owner, ticket, root, key = C._ticket(locator)
        assert owner is O and root == scene.root and key == scene.key
        assert ticket.epoch == scene.intent["execution_epoch"]
    assert checked == [True]  # Original SDK/registry producer runs before address use.
    assert scene.opened == []  # No native command or process query is necessary.
