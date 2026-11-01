"""Bind a public closed copy or leave-closed checkpoint to a first Designer open.

No import activates CAD. Capsules contain claims to recheck, never a verified
flag, reader address or capability. The owner supplies its original handle
reader and trusted configuration root; all SDK access remains read-only.
Legacy copy_* wire fields denote the source ID/digests in both branches.
source_kind is reconstructed from the original SDK command, never trusted.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import re
import sqlite3
import stat
import time
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import native_process as P
from . import native_release_preflight as W
from . import project_backup as B
from . import project_isolated_copy as I
from . import resources
from ._vendor.tool_driver_sdk.scope import file_path_of
from ._vendor.tool_driver_sdk.store import StateStore, target_key
from ._vendor.tool_driver_sdk.validation import ContractValidator
from .errors import CLIError
from .pcb_document_guard import _TEXT_COLUMNS, read_active_records

PROTOCOL = "designer-registered-project-first-open-v2"
VENDOR = Path(__file__).resolve().parent / "_vendor/spec"
MIN_SECONDS = 30.0
MAX_SECONDS = 90.0
BUDGET_PROFILE = "registered-project-whole-observation-v1"
MAX_PROJECT_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"op-[A-Za-z0-9_-]{1,100}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_SEAL = object()
_HISTORICAL_FIELDS = {
    "protocol",
    "source_kind",
    "project",
    "copy_operation_id",
    "open_operation_id",
    "scope_id",
    "scope_sha256",
    "owner_epoch_sha256",
    "copy_record_sha256",
    "copy_receipt_sha256",
    "registration_sha256",
    "genesis_sha256",
    "library_closure_sha256",
    "target_tree_sha256",
    "target_directory_identity",
    "target_project_identity",
}
_FIELDS = _HISTORICAL_FIELDS | {"observation_budget"}
_ACTIVE_OBSERVATION = ContextVar("first_open_active_observation", default=None)
_LAST_OBSERVATION = ContextVar("first_open_last_observation", default=None)


def observation_budget_for(source_kind, files, total_bytes):
    """Allocate time, never authority, from bounded original receipt claims.

    Copy verification already reads its source tree and target tree; both paths
    also perform our two target observations and complete archive CRC/hash reads.
    This is a fixed work estimate, not an assertion of disk throughput. All
    original byte/count checks and independent source verification still apply.
    """
    if (
        source_kind not in {"closed_copy", "closed_checkpoint"}
        or type(files) is not int
        or not 1 <= files <= I.MAX_FILES
        or type(total_bytes) is not int
        or not 1 <= total_bytes <= I.MAX_EXPANDED_BYTES
    ):
        _fail("first_open_budget_input_resource_limit")
    tree_passes = 4 if source_kind == "closed_copy" else 2
    estimated_bytes = total_bytes * (tree_passes + 2)
    estimated_files = files * tree_passes
    seconds = 20 + (estimated_bytes + 64 * 1024**2 - 1) // (64 * 1024**2)
    seconds += (estimated_files + 999) // 1000
    return {
        "profile": BUDGET_PROFILE,
        "source_kind": source_kind,
        "inputs": {"files": files, "project_bytes": total_bytes},
        "work_estimate": {
            "whole_tree_passes": tree_passes,
            "whole_archive_member_passes": 2,
            "read_bytes": estimated_bytes,
            "file_observations": estimated_files,
        },
        "total_seconds": min(MAX_SECONDS, max(MIN_SECONDS, float(seconds))),
        "limits": {
            "files_and_directories": I.MAX_FILES,
            "project_bytes": I.MAX_EXPANDED_BYTES,
            "archive_bytes": I.MAX_ARCHIVE_BYTES,
            "minimum_seconds": MIN_SECONDS,
            "maximum_seconds": MAX_SECONDS,
        },
        "deadline_basis": "cooperative_checks_with_enclosing_owner_request_deadline",
    }


def last_observation_report():
    """Elapsed/phase evidence outside the equality-bound source capsule."""
    return copy.deepcopy(_LAST_OBSERVATION.get())


class _Observation:
    def __init__(self):
        self.started = self.phase_started = time.monotonic()
        self.phase = "input_validation"
        self.phases = {}
        self.budget = None

    @property
    def deadline(self):
        return self.started + (self.budget["total_seconds"] if self.budget else MIN_SECONDS)

    def point(self, phase):
        now = time.monotonic()
        self.phases[self.phase] = self.phases.get(self.phase, 0.0) + now - self.phase_started
        self.phase, self.phase_started = phase, now
        self.check()

    def check(self):
        if time.monotonic() > self.deadline:
            _fail("first_open_observation_budget_exhausted")

    def report(self, status):
        now = time.monotonic()
        phases = dict(self.phases)
        phases[self.phase] = phases.get(self.phase, 0.0) + now - self.phase_started
        return {
            "profile": BUDGET_PROFILE,
            "budget": copy.deepcopy(self.budget),
            "elapsed_seconds": round(max(0.0, now - self.started), 6),
            "phase_elapsed_seconds": {k: round(max(0.0, v), 6) for k, v in phases.items()},
            "phase": self.phase,
            "status": status,
            "failed_phase": None if status == "completed" else self.phase,
        }


def _point(phase):
    observation = _ACTIVE_OBSERVATION.get()
    if observation is not None:
        observation.point(phase)


def _budget_inputs(source_kind, result):
    files = result.get("files")
    if source_kind == "closed_checkpoint":
        return observation_budget_for(source_kind, files, result.get("bytes"))
    saved = result.get("closed_copy") or {}
    written = saved.get("written_files")
    genesis = (saved.get("genesis") or {}).get("file_manifest")
    if (
        type(written) is not list
        or type(genesis) is not dict
        or type(files) is not int
        or not 1 <= files <= I.MAX_FILES
        or len(written) != files
        or len(genesis) != files
        or any(type(row) is not dict or type(row.get("bytes")) is not int for row in written)
        or any(not 0 <= row["bytes"] <= I.MAX_EXPANDED_BYTES for row in written)
    ):
        _fail("closed_copy_budget_observation_incomplete")
    return observation_budget_for(source_kind, files, sum(row["bytes"] for row in written))


def _fail(reason, code="E_CONFLICT", *, operation_id=None):
    observation = _ACTIVE_OBSERVATION.get()
    raise CLIError(
        code,
        "Registered project first opening could not be independently verified",
        {
            "reason": reason,
            "write_attempted": False,
            "native_request_dispatched": False,
            "state_write_attempted": False,
            **({"related_operation_id": operation_id} if operation_id else {}),
            **(
                {"first_open_observation": observation.report("refused")}
                if observation is not None
                else {}
            ),
        },
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _plain(value, *, existing=True):
    if not isinstance(value, (str, Path)):
        _fail("plain_local_path_required")
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")) or ".." in path.parts:
        _fail("plain_local_path_required")
    for ancestor in reversed((path, *path.parents)):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            if existing:
                raise
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("reparse_source_or_target_refused")
    return path.resolve(strict=existing)


def _identity(path):
    info = path.lstat()
    if type(info.st_dev) is not int or type(info.st_ino) is not int or info.st_ino <= 0:
        _fail("physical_file_identity_unavailable")
    return [info.st_dev, info.st_ino]


def _tree(root, deadline):
    pending, result, directories, total = [root], {}, set(), 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as stream:
            entries = list(itertools.islice(stream, I.MAX_FILES + 1))
        if len(entries) > I.MAX_FILES:
            _fail("target_tree_count_limit")
        for entry in entries:
            if time.monotonic() > deadline:
                _fail("target_observation_budget_exhausted")
            path = _plain(directory / entry.name)
            path.relative_to(root)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(path.relative_to(root).as_posix())
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    _fail("target_hardlink_or_shared_file_refused")
                total += info.st_size
                if total > I.MAX_EXPANDED_BYTES:
                    _fail("target_tree_byte_limit")
                row = I._record(path)
                result[path.relative_to(root).as_posix()] = {
                    "bytes": row["bytes"],
                    "sha256": row["sha256"],
                    "file_id": _identity(path),
                }
            else:
                _fail("target_tree_nonregular_entry")
            if len(directories) + len(result) > I.MAX_FILES:
                _fail("target_tree_count_limit")
    if len({name.casefold() for name in result}) != len(result):
        _fail("target_tree_case_ambiguity")
    return result, directories


@contextmanager
def _sdk_reader(trusted_config_root):
    root = _plain(trusted_config_root)
    directory = _plain(root / "driver-v2")
    path = _plain(directory / "operations.sqlite3")
    if not stat.S_ISREG(path.lstat().st_mode):
        _fail("trusted_sdk_store_not_regular")
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro", uri=True, timeout=5, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    # The original SDK get/_query decode is retained. Supplying its already-open
    # read-only connection avoids schema creation, pruning or a new operation log.
    store = StateStore(directory)
    store._connection = connection
    try:
        yield store, ContractValidator(VENDOR)
    finally:
        store.close()


def _bounded_record(store, operation_id):
    names = _TEXT_COLUMNS
    row = (
        store.connection()
        .execute(
            "SELECT "
            + ",".join(f"COALESCE(octet_length({name}),0) AS n_{name}" for name in names)
            + " FROM operations WHERE operation_id=?",
            (operation_id,),
        )
        .fetchone()
    )
    if row is None or sum(row) > 64 * 1024 * 1024:
        _fail("original_copy_record_missing_or_over_limit")
    return StateStore.get(store, operation_id)


def _declarations():
    path = resources.locate("contract/driver-v2/product_contract.json")
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        _fail("capability_declaration_byte_limit")
    return {row["path"]: row for row in json.loads(raw)["reference"]["commands"]}


def observe_owned_designer_scene(*, own_generation_reader):
    """Two complete OS-only inventories; only original held live generations fit."""
    if not callable(own_generation_reader):
        _fail("original_handle_generation_reader_required")

    def expected():
        rows = own_generation_reader()
        if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
            _fail("original_handle_generation_set_incomplete")
        roles, live = set(), []
        for row in rows:
            if (
                type(row) is not dict
                or set(row) != {"role", "domain", "process_identity", "alive"}
                or row["role"] not in {"worker", "launcher"}
                or row["role"] in roles
                or row["domain"] != "schematic"
                or type(row["alive"]) is not bool
                or not P.same_identity(row["process_identity"], row["process_identity"])
            ):
                _fail("original_handle_generation_set_incomplete")
            roles.add(row["role"])
            if row["role"] == "worker" and row["alive"] is not True:
                _fail("original_worker_generation_ended")
            if row["alive"]:
                live.append(copy.deepcopy(row["process_identity"]))
        if "worker" not in roles or len({row["pid"] for row in live}) != len(live):
            _fail("original_handle_generation_set_incomplete")
        return sorted(live, key=lambda row: row["pid"])

    def process_rows(domain):
        rows = list(itertools.islice(P.processes(domain), 65))
        if len(rows) > 64:
            _fail("native_writer_inventory_limit")
        return rows

    before = expected()
    scenes = []
    for _ in range(2):
        scene = {
            domain: W._inventory(domain, process_rows, W.process_generation)
            for domain in ("schematic", "pcb", "library")
        }
        if scene["pcb"] or scene["library"] or len(scene["schematic"]) != len(before):
            _fail("foreign_or_incomplete_native_writer_scene")
        for row, original in zip(scene["schematic"], before, strict=True):
            actual = {
                "pid": row["pid"],
                "executable": row["executable"],
                "creation_time_100ns": row["birth_filetime_100ns"],
            }
            if not P.same_identity(actual, original):
                _fail("native_writer_generation_changed")
        scenes.append(scene)
    if scenes[0] != scenes[1] or expected() != before:
        _fail("native_writer_scene_changed_during_observation")
    return {
        "allowed_live_generations": before,
        "schematic": scenes[0]["schematic"],
        "layout": [],
        "library": [],
    }


def _declared_first_open_resources(record, plan, extension):
    """Recognize an old unknown's intended resources, never its execution result."""
    capsule = extension.get("designer_project_source")
    route = extension.get("designer_project_open_route")
    if capsule is None and route is None:
        return None
    target = plan["target"]
    # Historical unknowns remain physical resource declarations. Their old
    # exact shapes never become acceptable current admission capabilities.
    fields = set(capsule) if isinstance(capsule, dict) else set()
    shapes = (
        (_FIELDS, _HISTORICAL_FIELDS)
        if isinstance(capsule, dict) and capsule.get("protocol") == PROTOCOL
        else (_HISTORICAL_FIELDS - {"source_kind"},)
    )
    hashes = {name for name in fields if name.endswith("_sha256")}
    generation = extension.get("process_identity") or {}
    base_extensions = (plan.get("base_snapshot") or {}).get("extensions") or {}
    facts = base_extensions.get("facts") or {}
    application = facts.get("application") or {}
    declared_application = extension.get("application") or {}
    document = application.get("document") or {}
    image = (application.get("public_fields") or {}).get("FullName")
    if (
        record.get("command") != "session attach"
        or record.get("kind") != "single"
        or route != "owned-registered-project-first-open-v1"
        or target.get("mode") != "session"
        or target.get("engine_id") != "xpedition-schematic"
        or extension.get("domain") != "schematic"
        or plan.get("can_apply") is not True
        or type(capsule) is not dict
        or fields not in shapes
        or capsule.get("protocol") not in {PROTOCOL, "designer-closed-copy-first-open-v1"}
        or (
            capsule.get("protocol") == PROTOCOL
            and capsule.get("source_kind") not in {"closed_copy", "closed_checkpoint"}
        )
        or not _ID.fullmatch(str(capsule.get("copy_operation_id")))
        or not _ID.fullmatch(str(record.get("operation_id")))
        or capsule.get("copy_operation_id") == record.get("operation_id")
        or capsule.get("open_operation_id") != record.get("operation_id")
        or extension.get("source_operation_id") != record.get("operation_id")
        or not isinstance(record.get("scope_id"), str)
        or not record["scope_id"]
        or capsule.get("scope_id") != record.get("scope_id")
        or plan.get("authorization_scope_id") != record.get("scope_id")
        or any(not _SHA.fullmatch(str(capsule.get(name))) for name in hashes)
        or record.get("effects") != plan.get("effects")
        or not set(record.get("effects") or []) <= {"session", "persist"}
        or "session" not in (record.get("effects") or [])
        or type(generation) is not dict
        or set(generation) != {"pid", "executable", "creation_time_100ns"}
        or type(generation.get("pid")) is not int
        or generation["pid"] <= 0
        or not P.same_identity(generation, facts.get("os_process_identity") or {})
        or str(generation["pid"]) != target.get("instance_id")
        or not Path(str(generation.get("executable", ""))).is_absolute()
        or not isinstance(generation.get("creation_time_100ns"), str)
        or Path(generation["executable"]).name.lower() != "viewdraw.exe"
        or application.get("pid") != generation["pid"]
        or application.get("domain") not in {None, "schematic"}
        or not isinstance(image, str)
        or not P.same_identity({**generation, "executable": image}, generation)
        or document.get("status") != "observed"
        or document.get("open") is not False
        or document.get("path") is not None
        or declared_application.get("pid") != generation["pid"]
        or (declared_application.get("document") or {}).get("status") != "observed"
        or (declared_application.get("document") or {}).get("open") is not False
        or (declared_application.get("document") or {}).get("path") is not None
        or not P.same_identity(
            {
                **generation,
                "executable": (declared_application.get("public_fields") or {}).get("FullName"),
            },
            generation,
        )
    ):
        _fail(
            "active_first_open_resource_declaration_incomplete",
            operation_id=record.get("operation_id"),
        )
    path = _plain(capsule["project"], existing=False)
    locator = file_path_of(target.get("locator", ""))
    if (
        path.suffix.lower() != ".prj"
        or not locator
        or any(
            _plain(value, existing=False) != path
            for value in (target.get("object_id"), locator, extension.get("project"))
        )
    ):
        _fail("active_first_open_declared_path_inconsistent", operation_id=record["operation_id"])
    identities = [capsule["target_project_identity"], capsule["target_directory_identity"]]
    if (
        any(
            type(value) is not list
            or len(value) != 2
            or any(type(number) is not int for number in value)
            or value[0] < 0
            or value[1] <= 0
            for value in identities
        )
        or identities[0] == identities[1]
        or identities[0][0] != identities[1][0]
    ):
        _fail(
            "active_first_open_physical_declaration_incomplete", operation_id=record["operation_id"]
        )
    return {"project": path, "directory": path.parent, "identities": identities}


def _conflict_guard(store, validator, declarations, project, resources_map, operation_id):
    paths = {os.path.normcase(str(project))}
    ids = {tuple(row["file_id"]) for row in resources_map.values()}
    root = project.parent
    ids.add(tuple(_identity(root)))
    for record in read_active_records(store, exclude_operation_id=operation_id):
        plan, receipt = record.get("plan"), record.get("receipt")
        declared = declarations.get(record.get("command"))
        if not isinstance(plan, dict) or declared is None:
            _fail("active_operation_authority_incomplete")
        validator.validate("plan", plan)
        target = plan["target"]
        extension = target.get("extensions") or {}
        if (
            record.get("operation_id") != plan.get("operation_id")
            or record.get("plan_id") != plan.get("plan_id")
            or record.get("capability_id") != declared["driver"]["capability_id"]
            or record.get("capability_id") != plan.get("capability_id")
            or record.get("target_key") != target_key(target)
            or extension.get("command") != record.get("command")
        ):
            _fail("active_operation_authority_incomplete")
        candidates = []
        declared_open = _declared_first_open_resources(record, plan, extension)
        if declared_open is not None:
            if any(
                tuple(value) in ids for value in declared_open["identities"]
            ) or root.is_relative_to(declared_open["directory"]):
                _fail(
                    "unresolved_first_open_on_same_physical_project",
                    operation_id=record["operation_id"],
                )
            candidates.append(str(declared_open["project"]))
        if target.get("mode") == "file":
            native_path = file_path_of(target.get("locator", ""))
            if not native_path or _plain(native_path, existing=False) != _plain(
                target["object_id"], existing=False
            ):
                _fail("active_operation_target_inconsistent")
            candidates.append(native_path)
            base = (plan.get("base_snapshot") or {}).get("extensions") or {}
            disk = (base.get("facts") or {}).get("target")
            if (
                isinstance(disk, dict)
                and disk.get("path")
                and _plain(disk["path"], existing=False) != _plain(native_path, existing=False)
            ):
                _fail("active_operation_disk_target_inconsistent")
            preview = (plan.get("extensions") or {}).get("legacy_preview") or {}
            if (
                record["command"] == "project create"
                and preview.get("path")
                and _plain(preview["path"], existing=False) != _plain(native_path, existing=False)
            ):
                _fail("active_operation_request_target_inconsistent")
        for key in ("project", "save_project"):
            if extension.get(key):
                candidates.append(extension[key])
        if receipt is not None:
            validator.validate("receipt", receipt)
            if (
                receipt.get("operation_id") != record["operation_id"]
                or receipt.get("target") != target
                or receipt.get("plan_id") != record.get("plan_id")
                or receipt.get("capability_id") != record.get("capability_id")
                or receipt.get("authorization_scope_id") != record.get("scope_id")
                or receipt.get("effects") != record.get("effects")
                or receipt.get("snapshot_before") != plan.get("base_snapshot")
            ):
                _fail("active_operation_receipt_inconsistent")
        snapshots = (
            [
                plan.get("base_snapshot"),
                *(receipt.get(key) for key in ("snapshot_before", "snapshot_after")),
            ]
            if receipt
            else [plan.get("base_snapshot")]
        )
        observed_documents = 0
        for snapshot in snapshots:
            application = (((snapshot or {}).get("extensions") or {}).get("facts") or {}).get(
                "application"
            ) or {}
            document = application.get("document") or {}
            if document.get("open") is True:
                if document.get("status") != "observed" or not document.get("path"):
                    _fail("active_native_document_proof_incomplete")
                candidates.append(document["path"])
                if not _plain(document["path"], existing=False).exists():
                    _fail(
                        "active_native_physical_file_unavailable",
                        operation_id=record["operation_id"],
                    )
                observed_documents += 1
        if target.get("mode") == "session" and not observed_documents and declared_open is None:
            # Empty session-only Quit cannot mutate a physical project. A native
            # engineering target without a complete document proof stays blocked.
            document = (extension.get("application") or {}).get("document") or {}
            if not (
                record["command"] == "session close"
                and not extension.get("project")
                and not extension.get("save_project")
                and document.get("open") is False
                and document.get("status") == "observed"
            ):
                _fail("active_native_document_proof_incomplete")
        if not candidates and target.get("mode") != "session":
            _fail("active_operation_physical_target_incomplete")
        for value in candidates:
            path = _plain(value, existing=False)
            if os.path.normcase(str(path)) in paths or path == root or path.is_relative_to(root):
                _fail(
                    "unresolved_operation_on_first_open_project",
                    operation_id=record["operation_id"],
                )
            if path.exists() and tuple(_identity(path)) in ids:
                _fail(
                    "unresolved_operation_on_same_physical_file",
                    operation_id=record["operation_id"],
                )


class VerifiedClosedCopyOpen:
    __slots__ = ("_seal", "_binding")

    def __init__(self, seal, binding):
        if seal is not _SEAL:
            _fail("opaque_first_open_proof_required")
        self._seal, self._binding = seal, copy.deepcopy(binding)

    @property
    def project(self):
        return self._binding["project"]

    @property
    def safe_binding(self):
        return copy.deepcopy(self._binding)

    def __repr__(self):
        return "<VerifiedRegisteredProjectFirstOpen>"


def _checkpoint_manifest(project, result, plan, receipt, deadline):
    """Re-read the actual checkpoint archive; producer manifests are only claims."""
    preview = (plan.get("extensions") or {}).get("legacy_preview") or {}
    params = preview.get("params") or {}
    release = result.get("release") or {}
    if (
        params.get("leave_closed") is not True
        or _plain(params.get("project")) != project
        or _plain(result.get("project")) != project
        or result.get("requested_leave_closed") is not True
        or result.get("actual_closed_without_reopen") is not True
        or result.get("closed_and_reopened") is not False
        or result.get("complete") is not True
        or result.get("skipped") != []
        or release.get("quiescent") is not True
    ):
        _fail("successful_leave_closed_checkpoint_required")
    archive = _plain(result.get("backup"))
    if archive.is_relative_to(project.parent) or archive.suffix.lower() != ".zip":
        _fail("checkpoint_archive_must_be_outside_project")
    archive_pin = I._record(archive)
    if archive_pin["bytes"] > I.MAX_ARCHIVE_BYTES:
        _fail("checkpoint_archive_byte_limit")
    after_id = (receipt.get("snapshot_after") or {}).get("id")
    pins = [
        row
        for row in receipt.get("artifacts", [])
        if isinstance(row, dict)
        and _plain(row.get("path")) == archive
        and row.get("source_snapshot_id") == after_id
    ]
    if len(pins) != 1 or pins[0].get("sha256") != archive_pin["sha256"]:
        _fail("checkpoint_archive_not_bound_to_source_receipt")
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if (
            not 2 <= len(infos) <= I.MAX_FILES + 1
            or sum(info.file_size for info in infos) > I.MAX_EXPANDED_BYTES
        ):
            _fail("checkpoint_archive_resource_limit")
        for info in infos:
            I._safe_member(info)
        names = [info.filename for info in infos]
        _point("checkpoint_archive_crc")
        if len({name.casefold() for name in names}) != len(names) or bundle.testzip() is not None:
            _fail("checkpoint_archive_crc_or_member_ambiguity")
        manifest_info = bundle.getinfo(B.MANIFEST)
        if manifest_info.file_size > 4 * 1024 * 1024:
            _fail("checkpoint_manifest_byte_limit")
        metadata = json.loads(bundle.read(B.MANIFEST))
        members = [name for name in names if name != B.MANIFEST]
        expected = metadata.get("sha256")
        if (
            metadata.get("project") != project.name
            or metadata.get("all_files") is not True
            or metadata.get("skipped") != []
            or metadata.get("files") != len(members)
            or type(expected) is not dict
            or set(expected) != set(members)
            or project.name not in members
            or "database/icdb.dat" not in members
            or bundle.getinfo(project.name).file_size > MAX_PROJECT_BYTES
            or bundle.getinfo("database/icdb.dat").file_size <= 0
            or result.get("files") != len(members)
        ):
            _fail("checkpoint_archive_is_not_complete_own_project")
        if metadata.get("source") is not None and _plain(metadata["source"]) != project.parent:
            _fail("checkpoint_archive_source_root_mismatch")
        _point("checkpoint_archive_member_hashes")
        actual, total = {}, 0
        for name in members:
            if time.monotonic() > deadline:
                _fail("checkpoint_archive_observation_budget")
            digest, size = hashlib.sha256(), 0
            with bundle.open(name) as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
                    size += len(block)
                    if time.monotonic() > deadline:
                        _fail("checkpoint_archive_observation_budget")
            if not _SHA.fullmatch(str(expected[name])) or digest.hexdigest() != expected[name]:
                _fail("checkpoint_archive_manifest_hash_mismatch")
            actual[name] = digest.hexdigest()
            total += size
        if metadata.get("bytes") != total or result.get("bytes") != total:
            _fail("checkpoint_archive_byte_census_mismatch")
    _point("checkpoint_archive_stability")
    if I._record(archive) != archive_pin:
        _fail("checkpoint_archive_changed_during_observation")
    return actual, {
        "kind": "closed-project-checkpoint",
        "project": I._record(project),
        "archive": archive_pin,
        "file_manifest": actual,
        # These are historical source observations, not assertions that Save ran.
        "release_observation": copy.deepcopy(release),
        "source_persistence_observation": receipt["outcome"]["persistence"],
    }


def _derive(
    *,
    project,
    source_operation_id,
    editing_scope,
    sdk_operation_id,
    owner_epoch_sha256,
    trusted_config_root,
    own_generation_reader,
):
    from .designer_owner_dispatch import validate_editing_scope

    if (
        not _ID.fullmatch(str(source_operation_id))
        or not _ID.fullmatch(str(sdk_operation_id))
        or not _SHA.fullmatch(str(owner_epoch_sha256))
    ):
        _fail("first_open_operation_or_owner_binding_invalid")
    scope = validate_editing_scope(editing_scope)
    project = _plain(project)
    if project.suffix.lower() != ".prj" or not str(project).isascii():
        _fail("first_open_project_path_unsupported")
    if project.stat().st_size > MAX_PROJECT_BYTES:
        _fail("first_open_project_text_byte_limit")
    scope.require(effects=["session"], paths=[(str(project), "persist")])
    observation = _ACTIVE_OBSERVATION.get()
    _point("original_sdk_source")
    with _sdk_reader(trusted_config_root) as (store, validator):
        record = _bounded_record(store, source_operation_id)
        source_command = record.get("command")
        if source_command not in {"project create", "project backup"}:
            _fail("registered_project_source_command_unsupported", "E_UNSUPPORTED")
        plan, receipt = record.get("plan"), record.get("receipt")
        if not isinstance(plan, dict) or not isinstance(receipt, dict):
            _fail("closed_copy_source_plan_or_receipt_missing")
        validator.validate("plan", plan)
        validator.validate("receipt", receipt)
        declarations = _declarations()
        capability = declarations[source_command]["driver"]["capability_id"]
        result = (receipt.get("extensions") or {}).get("result") or {}
        readback = (receipt.get("extensions") or {}).get("file_readback") or {}
        target = plan["target"]
        outcome = receipt.get("outcome") or {}
        if (
            record.get("status") != "succeeded"
            or record.get("resolution") is not None
            or record.get("kind") != "single"
            or record.get("operation_id") != source_operation_id
            or plan.get("operation_id") != source_operation_id
            or receipt.get("operation_id") != source_operation_id
            or record.get("plan_id") != plan.get("plan_id")
            or record.get("plan_id") != receipt.get("plan_id")
            or record.get("capability_id") != capability
            or plan.get("capability_id") != capability
            or receipt.get("capability_id") != capability
            or record.get("scope_id") != scope.scope_id
            or plan.get("authorization_scope_id") != scope.scope_id
            or receipt.get("authorization_scope_id") != scope.scope_id
            or record.get("effects") != plan.get("effects")
            or record.get("effects") != receipt.get("effects")
            or receipt.get("target") != target
            or receipt.get("snapshot_before") != plan.get("base_snapshot")
            or validator.receipt_rule(receipt).get("ok") is not True
            or outcome.get("execution") != "completed"
            or outcome.get("persistence")
            not in ({"saved"} if source_command == "project create" else {"saved", "unchanged"})
            or (outcome.get("verification") or {}).get("status")
            not in (
                {"passed"} if source_command == "project create" else {"passed", "not_applicable"}
            )
            or _plain(target["object_id"]) != project
            or target.get("mode") != "file"
            or target.get("engine_id") != "xpedition-cli-files"
            or file_path_of(target.get("locator", "")) != str(project)
            or (target.get("extensions") or {}).get("command") != source_command
            or record.get("target_key") != target_key(target)
            or set(record.get("effects") or [])
            != set(declarations[source_command]["driver"]["effects"])
        ):
            _fail("original_registered_project_sdk_binding_incomplete")
        source_kind = "closed_copy" if source_command == "project create" else "closed_checkpoint"
        observation.budget = _budget_inputs(source_kind, result)
        observation.check()
        deadline = observation.deadline
        _point("writer_scene_initial")
        observe_owned_designer_scene(own_generation_reader=own_generation_reader)
        if source_command == "project create":
            _point("closed_copy_source_and_target")
            if (
                result.get("creation_method") != "closed_backup_copy"
                or result.get("created") is not True
                or result.get("opened") is not False
                or result.get("native_open_verified") is not False
                or type(result.get("native_calls_dispatched")) is not int
                or result["native_calls_dispatched"] != 0
                or result.get("file_copy_verified") is not True
                or readback.get("valid") is not True
                or readback.get("complete") is not True
            ):
                _fail("original_closed_copy_sdk_binding_incomplete")
            saved = result.get("closed_copy") or {}
            registration = (saved.get("registration") or {}).get("data") or {}
            if (registration.get("origin_project") or {}).get(
                "bytes", MAX_PROJECT_BYTES + 1
            ) > MAX_PROJECT_BYTES:
                _fail("first_open_source_project_text_byte_limit")
            if _plain((registration.get("target_project") or {}).get("path")) != project:
                _fail("registered_target_project_mismatch")
            independent = I.verify_copy(result)
            if not independent.get("valid") or not independent.get("complete"):
                _fail("closed_copy_source_or_current_target_changed")
            genesis = saved["genesis"]["file_manifest"]
            if (
                readback.get("actual_target_manifest") != genesis
                or saved.get("actual_target_manifest") != genesis
                or independent.get("actual_target_manifest") != genesis
            ):
                _fail("closed_copy_genesis_proofs_disagree")
        else:
            _point("checkpoint_archive_binding")
            genesis, registration = _checkpoint_manifest(project, result, plan, receipt, deadline)
        _point("target_tree_initial")
        tree, directories = _tree(project.parent, deadline)
        if observation.budget["inputs"] != {
            "files": len(tree),
            "project_bytes": sum(row["bytes"] for row in tree.values()),
        }:
            _fail("source_budget_inputs_disagree_with_complete_target")
        if {name: row["sha256"] for name, row in tree.items()} != genesis:
            _fail("current_target_differs_from_closed_copy_genesis")
        expected_directories = {
            parent.as_posix()
            for name in genesis
            for parent in Path(name).parents
            if str(parent) != "."
        }
        if not expected_directories.issubset(directories) or (
            source_kind == "closed_copy" and directories != expected_directories
        ):
            _fail("current_target_directory_set_changed")
        _point("library_closure")
        text, _encoding = I._decode(project.read_bytes())
        closure = I._library_closure(text, project.parent, genesis)
        _point("active_unknown_physical_guard")
        _conflict_guard(store, validator, declarations, project, tree, sdk_operation_id)
        _point("target_tree_repeat")
        if _tree(project.parent, deadline) != (tree, directories):
            _fail("target_changed_during_first_open_observation")
        _point("writer_scene_final")
        observe_owned_designer_scene(own_generation_reader=own_generation_reader)
        _point("source_sdk_repeat")
        if _bounded_record(store, source_operation_id) != record:
            _fail("source_sdk_record_changed_during_observation")
        _point("scope_final")
        validate_editing_scope(editing_scope)
        if time.monotonic() > deadline:
            _fail("first_open_observation_budget_exhausted")
        return {
            "protocol": PROTOCOL,
            "source_kind": source_kind,
            "project": str(project),
            "copy_operation_id": source_operation_id,
            "open_operation_id": sdk_operation_id,
            "scope_id": scope.scope_id,
            "scope_sha256": editing_scope["scope_sha256"],
            "owner_epoch_sha256": owner_epoch_sha256,
            "copy_record_sha256": _digest(record),
            "copy_receipt_sha256": _digest(receipt),
            "registration_sha256": _digest(registration),
            "genesis_sha256": _digest(genesis),
            "library_closure_sha256": _digest(closure),
            "target_tree_sha256": _digest(tree),
            "target_directory_identity": _identity(project.parent),
            "target_project_identity": _identity(project),
            "observation_budget": copy.deepcopy(observation.budget),
        }


def build_closed_copy_open_binding(
    *,
    project,
    source_operation_id,
    editing_scope,
    sdk_operation_id,
    owner_epoch_sha256,
    trusted_config_root,
    own_generation_reader,
):
    observation = _Observation()
    active_token = _ACTIVE_OBSERVATION.set(observation)
    _LAST_OBSERVATION.set(None)
    status = "refused"
    try:
        result = _derive(
            project=project,
            source_operation_id=source_operation_id,
            editing_scope=editing_scope,
            sdk_operation_id=sdk_operation_id,
            owner_epoch_sha256=owner_epoch_sha256,
            trusted_config_root=trusted_config_root,
            own_generation_reader=own_generation_reader,
        )
        observation.check()
        status = "completed"
        return result
    except CLIError as error:
        error.details = {
            **(error.details or {}),
            "first_open_observation": observation.report("refused"),
        }
        raise
    except Exception:
        _fail("first_open_source_evidence_incomplete")
    finally:
        _LAST_OBSERVATION.set(observation.report(status))
        _ACTIVE_OBSERVATION.reset(active_token)


def verify_closed_copy_open_binding(
    capsule,
    *,
    project,
    editing_scope,
    sdk_operation_id,
    owner_epoch_sha256,
    trusted_config_root,
    own_generation_reader,
):
    _LAST_OBSERVATION.set(None)
    if type(capsule) is not dict or set(capsule) != _FIELDS or capsule.get("protocol") != PROTOCOL:
        _fail("exact_first_open_capsule_required")
    derived = build_closed_copy_open_binding(
        project=project,
        source_operation_id=capsule["copy_operation_id"],
        editing_scope=editing_scope,
        sdk_operation_id=sdk_operation_id,
        owner_epoch_sha256=owner_epoch_sha256,
        trusted_config_root=trusted_config_root,
        own_generation_reader=own_generation_reader,
    )
    comparison_started = time.monotonic()
    matches = derived == capsule
    comparison_seconds = max(0.0, time.monotonic() - comparison_started)
    report = last_observation_report()
    report.update(
        status="completed" if matches else "refused",
        phase="capsule_comparison",
        failed_phase=None if matches else "capsule_comparison",
    )
    report["elapsed_seconds"] = round(report["elapsed_seconds"] + comparison_seconds, 6)
    report["phase_elapsed_seconds"]["capsule_comparison"] = round(comparison_seconds, 6)
    _LAST_OBSERVATION.set(report)
    if not matches:
        try:
            _fail("first_open_capsule_or_current_evidence_changed")
        except CLIError as error:
            error.details["first_open_observation"] = copy.deepcopy(report)
            raise
    return VerifiedClosedCopyOpen(_SEAL, derived)
