"""Isolated, one-shot component connection through the formal151.0 transaction.

File provenance, physical partitions, native DRC acknowledgement and persistence
are separate evidence. There is no effective-rule parser or client verification
override. A failure never triggers automatic End, Save, Close, Open or replay.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import time
import zipfile
from decimal import Decimal
from pathlib import Path

from . import board_components as C
from . import board_labels
from . import board_routepass as R
from . import component_closed_recovery as K
from . import component_execution_budget as EB
from . import component_net0_reopen as NR
from . import component_save_policy as SP
from . import native_pad_mask as M
from .audit import config_dir
from .cell_refresh import active, digest
from .component_copy_origin import verify_origin
from .native_component_transaction import SOURCE, put_trace_transaction

GUARD = "isolated-single-net-two-components-explicit-trace151-v2"
CLOSED_PROXY_RELEASE = {
    "boundary": "Close returned actual BOOL True and ActiveDocument was None",
    "local_references_dropped": ["doc", "net"],
    "application_retained": True,
    "mechanism": "Drop closed child Python references; no bare Release or CoUninitialize",
    "external_child_lifetimes": "Unproven; complete closed file reads must still succeed",
}
CLOSED_SNAPSHOT_SAMPLING = {
    "contract": "bounded-complete-closed-sampling-v2",
    "max_attempts": 17,
    "between_attempts_ms": 500,
    "read_only": True,
    "observation": "K.observe_closed complete two-pass hashes",
    "native_writes_replayed": False,
    "files_deleted_or_unlocked": False,
    "atomic_snapshot": False,
}
MAX_JSON_BYTES = 64 * 1024 * 1024
_REQUEST_KEYS = {
    "snapshot_id",
    "net",
    "net_uid",
    "layer",
    "first",
    "second",
    "width_mm",
    "points",
    "query",
    "query_file",
    "isolated_origin",
    "isolated_origin_file",
    "recovery_backup",
}


def limitations():
    return {
        "scope": "registered isolated copy; one actual net, one signal layer, two material groups",
        "rules": "TransactionStart(epcbDRCModeDRC=1) and TransactionEnd(True) are native "
        "modified-data DRC acknowledgement, not a complete effective-rule parser",
        "drc": "No BatchDRC is dispatched here. Existing batch configuration or directional "
        "hazards do not establish full effective-rule applicability or manufacturing approval",
        "atomicity": "fresh observable state/file pins; no exclusive lock or opaque cache freeze",
        "failure": (
            "unknown/partial outcomes retained; no automatic cleanup, retry or rollback claim"
        ),
        "not_checked": [
            "complete rule resolution",
            "thermal/tie",
            "manufacturing sign-off",
            "native database and current-user UPF internal semantics",
            "complete private rule/cache semantics of opaque NativeSave stores",
        ],
        "rule_protection_scope": (
            "Exact observed rules are compared; complete private rule/cache semantics are unproven"
        ),
    }


def _sha(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("A lowercase SHA-256 observation is required")
    return value


def _file(path, *, maximum=None):
    resolved = Path(str(path)).resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError("A retained, regular evidence file is required")
    size = resolved.stat().st_size
    if maximum is not None and size > maximum:
        raise ValueError("Retained evidence exceeds the bounded read budget")
    data = resolved.read_bytes()
    return {
        "path": str(resolved),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }, data


def _pinned_file(pin, *, maximum=None):
    if not isinstance(pin, dict) or set(pin) - {"path", "bytes", "sha256"}:
        raise ValueError("A file descriptor must contain path, optional bytes and SHA-256")
    observed, data = _file(pin["path"], maximum=maximum)
    if "bytes" in pin and type(pin["bytes"]) is not int:
        raise ValueError("Pinned byte count must be an observed integer")
    if observed["sha256"] != _sha(pin["sha256"]) or (
        "bytes" in pin and pin["bytes"] != observed["bytes"]
    ):
        raise ValueError("Retained evidence file changed since its bytes were pinned")
    return observed, data


def _json_file(pin):
    observed, data = _pinned_file(pin, maximum=MAX_JSON_BYTES)
    parsed = json.loads(data.decode("utf-8-sig"))
    if not isinstance(parsed, dict):
        raise ValueError("Retained JSON evidence must be an object")
    return observed, parsed


def _query_snapshot(value):
    if value.get("contract") == C.CONTRACT:
        return value
    if value.get("ok") is True:
        value = value["data"]
    if "observation" in value:
        value = value["observation"]
    return value["connectivity_components"]


def validate_request(request):
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS:
        raise ValueError(
            "Component connection requires the complete explicit request and retained evidence"
        )
    if not isinstance(request["net"], str) or not request["net"].strip():
        raise ValueError("One actual native net name is required")
    C._uid(request["net_uid"])
    recovery = request["recovery_backup"]
    if recovery is not None and (not isinstance(recovery, str) or not Path(recovery).is_absolute()):
        raise ValueError("Recovery backup must be an explicit absolute new ZIP path or null")
    if type(request["layer"]) is not int or not 1 <= request["layer"] <= 128:
        raise ValueError("One actual conductor layer is required")
    _query_snapshot(request["query"])
    for point in request["points"]:
        for coordinate in point:
            C._number(coordinate)
            if Decimal(str(coordinate)).normalize().as_tuple().exponent < -4:
                raise ValueError("New path coordinates require at most four decimal places in mm")
    return copy.deepcopy(request)


def _native_accounting(snapshot):
    """Sufficient bounded relation, independent of net names or member counts."""
    groups = snapshot["components"]
    owners = {}
    for index, group in enumerate(groups):
        for node_id in group["node_ids"]:
            node = next(row for row in snapshot["nodes"] if row["node_id"] == node_id)
            if node["kind"] == "pin":
                owners.setdefault((node["component_uid"], node["pin_uid"]), set()).add(index)
    return (
        bool(groups)
        and snapshot["native_connectivity"]["opens"] == len(groups) - 1
        and snapshot["native_connectivity"]["routed"] is (len(groups) == 1)
        and all(len(value) == 1 for value in owners.values())
        and all(
            any(
                node.get("pin_uid")
                for node in snapshot["nodes"]
                if node["node_id"] in group["node_ids"]
            )
            for group in groups
        )
    )


def _named_minima(state, request):
    rows = []
    for net_class in state.get("rules", {}).get("net_classes", []):
        if request["net"] not in net_class.get("nets", []):
            continue
        value = net_class.get("widths", {}).get(str(request["layer"]), {}).get("minimum")
        if (
            net_class.get("membership_observation", {}).get("complete") is True
            and type(value) in (int, float)
            and math.isfinite(value)
            and value > 0
        ):
            rows.append(
                {
                    "class": net_class.get("name"),
                    "scheme": "(Master)",
                    "layer": request["layer"],
                    "minimum_mm": value,
                    "source": "observed NetClass.MinTraceWidth(layer,(Master),unit4)",
                }
            )
    return rows


def _genesis_manifest(origin, archive, project, board):
    """Derive copied file identities from the complete backup, never a verified flag."""
    origin_project = Path(origin["origin_project"]["path"]).resolve()
    root, target_root = origin_project.parent, project.parent
    relative_board = Path(origin["origin_board"]["path"]).resolve().relative_to(root).as_posix()
    if (
        board.relative_to(target_root).as_posix() != relative_board
        or project.name != origin_project.name
    ):
        raise ValueError(
            "The isolated target must retain the source project/board relative identity"
        )
    with zipfile.ZipFile(archive) as source:
        meta = json.loads(source.read("xpedition-cli-backup.json"))
        manifest = copy.deepcopy(meta["sha256"])
        if (
            manifest[origin_project.name] != origin["origin_project"]["sha256"]
            or manifest[relative_board] != origin["origin_board"]["sha256"]
        ):
            raise ValueError("Origin project/board hashes do not bind the complete closed backup")
        provenance = origin["provenance"]
        if provenance.get("kind") != "closed-backup-copy":
            raise ValueError("Only complete closed-backup-copy provenance is supported")
        rebase = provenance.get("project_rebase")
        if rebase is not None:
            if (
                set(rebase) != {"encoding", "from_root", "to_root"}
                or rebase["encoding"] not in ("utf-8", "utf-16")
                or Path(rebase["from_root"]).resolve() != root
                or Path(rebase["to_root"]).resolve() != target_root
            ):
                raise ValueError(
                    "Project rebase must name the exact source/target roots and encoding"
                )
            text = source.read(origin_project.name).decode(rebase["encoding"])
            for before, after in (
                (str(root), str(target_root)),
                (root.as_posix(), target_root.as_posix()),
            ):
                text = text.replace(before, after)
            manifest[project.name] = hashlib.sha256(text.encode(rebase["encoding"])).hexdigest()
        genesis = origin["genesis"]
        if (
            genesis.get("file_manifest") != manifest
            or genesis.get("project_sha256") != manifest[project.name]
            or genesis.get("board_sha256") != manifest[relative_board]
        ):
            raise ValueError(
                "Genesis hashes must derive from the complete copy and exact project-only rebase"
            )
    return manifest, relative_board


def descriptor_open_transition(genesis_bytes, current_bytes, genesis_pin, current_pin):
    """Permit exact bytes or one decimal session-ID replacement, nothing else."""
    evidence = {
        "genesis": {**copy.deepcopy(genesis_pin), "raw_hex": genesis_bytes.hex()},
        "current": {**copy.deepcopy(current_pin), "raw_hex": current_bytes.hex()},
        "byte_equal": genesis_bytes == current_bytes,
        "field_changes": [],
        "scope": "descriptor bytes only; no private database or engineering equivalence claim",
    }
    if evidence["byte_equal"]:
        return evidence
    pattern = re.compile(rb'(?m)^[ \t]*KEY iCDBSessionID "([0-9]+)"[ \t]*(?=\r?$)')
    old, new = list(pattern.finditer(genesis_bytes)), list(pattern.finditer(current_bytes))
    if len(old) != 1 or len(new) != 1:
        raise ValueError("Descriptor change requires one unique decimal KEY iCDBSessionID field")
    left, right = old[0], new[0]
    if (
        genesis_bytes[: left.start(1)] != current_bytes[: right.start(1)]
        or genesis_bytes[left.end(1) :] != current_bytes[right.end(1) :]
    ):
        raise ValueError("Descriptor has changes beyond the single iCDBSessionID decimal value")
    evidence["field_changes"] = [
        {
            "key": "iCDBSessionID",
            "before": left.group(1).decode("ascii"),
            "after": right.group(1).decode("ascii"),
            "genesis_digit_span": list(left.span(1)),
            "current_digit_span": list(right.span(1)),
        }
    ]
    return evidence


def _descriptor_bytes(pin, board):
    data = bytes.fromhex(pin["raw_hex"])
    if (
        Path(pin["path"]).resolve() != Path(board).resolve()
        or type(pin["bytes"]) is not int
        or len(data) != pin["bytes"]
        or len(data) > 1024 * 1024
        or hashlib.sha256(data).hexdigest() != _sha(pin["sha256"])
    ):
        raise ValueError("Descriptor raw bytes do not match their complete retained file pin")
    return data


def _sdk_component_receipt(envelope, project, board):
    """Accept only the complete original successful SDK producer from its own journal."""
    if envelope.get("ok") is True:
        data = envelope.get("data")
        receipt = data.get("receipt") if isinstance(data, dict) else None
    else:
        receipt = envelope
    if (
        not isinstance(receipt, dict)
        or not {"operation_id", "target", "outcome", "extensions"} <= receipt.keys()
    ):
        raise ValueError(
            "Continuation requires a complete SDK receipt, not a cropped native result"
        )
    operation_id = receipt["operation_id"]
    if not isinstance(operation_id, str) or not re.fullmatch(
        r"op[-_][a-zA-Z0-9]{8,80}", operation_id
    ):
        raise ValueError("The original SDK operation identity is incomplete")
    journal = config_dir() / "driver-v2" / "operations.sqlite3"
    if not journal.is_file() or journal.is_symlink():
        raise ValueError("The original continuous operation journal is unavailable")
    try:
        with sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT operation_id,command,status,scope_id,receipt FROM operations "
                "WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
    except sqlite3.Error as error:
        raise ValueError(
            "The original component operation cannot be read: " + type(error).__name__
        ) from error
    if row is None or row["command"] != "pcb routing trace" or row["status"] != "succeeded":
        raise ValueError("The original successful component operation is absent")
    stored = json.loads(row["receipt"] or "null")
    if not isinstance(stored, dict) or digest(stored) != digest(receipt):
        raise ValueError("The supplied SDK receipt differs from its original stored producer")
    outcome, extensions = receipt["outcome"], receipt["extensions"]
    native = extensions["result"]
    if (
        receipt["operation_id"] != row["operation_id"]
        or receipt["authorization_scope_id"] != row["scope_id"]
        or Path(receipt["target"]["object_id"]).resolve() != Path(project).resolve()
        or outcome.get("execution") != "completed"
        or outcome.get("persistence") != "saved"
        or outcome.get("verification", {}).get("status") != "passed"
        or extensions.get("file_readback", {}).get("valid") is not True
        or native.get("trace", {}).get("guard") != GUARD
        or native["trace"]["board"] != str(Path(board).resolve())
        or not verify_saved_receipt(native)["valid"]
    ):
        raise ValueError(
            "Original SDK authority, independent readback or saved native lifecycle is incomplete"
        )
    return native, {
        "operation_id": operation_id,
        "receipt_sha256": digest(receipt),
        "source": "existing original continuous journal: successful component SDK producer",
    }


def _post_open_genesis(origin, manifest, project, board, inputs, current_pin, current_bytes):
    relative_board = board.relative_to(project.parent).as_posix()
    with zipfile.ZipFile(origin["source_backup"]["path"]) as archive:
        genesis_bytes = archive.read(relative_board)
    genesis_pin = {
        "archive": copy.deepcopy(origin["source_backup"]),
        "member": relative_board,
        "bytes": len(genesis_bytes),
        "sha256": hashlib.sha256(genesis_bytes).hexdigest(),
    }
    if genesis_pin["sha256"] != manifest[relative_board]:
        raise ValueError("Descriptor raw genesis bytes differ from the original verified copy")
    if max(len(genesis_bytes), len(current_bytes)) > 1024 * 1024:
        raise ValueError("Descriptor byte evidence exceeds its bounded 1MiB size")
    descriptor = descriptor_open_transition(genesis_bytes, current_bytes, genesis_pin, current_pin)
    rows = {}
    for collection in ("live_files", "non_rule_observed_files", "live_unavailable"):
        for row in inputs[collection]:
            relative = Path(row["path"]).relative_to(project.parent).as_posix()
            if relative in rows:
                raise ValueError("Post-Open dependency inventory has duplicate relative paths")
            rows[relative] = {"collection": collection, **copy.deepcopy(row)}
    board_root = board.parent.relative_to(project.parent).as_posix().lower()

    def critical(relative):
        lower, parts = relative.lower(), relative.lower().split("/")
        return (
            lower.startswith("library/")
            or lower.startswith(board_root + "/layout/")
            or (
                lower.startswith(board_root + "/config/")
                and not lower.endswith(".bak")
                and "bak" not in parts
            )
        )

    checked, differences = [], []
    for relative in sorted(set(manifest) | set(rows)):
        before, after = manifest.get(relative), rows.get(relative)
        if critical(relative):
            if before is None or after is None or after.get("sha256") != before:
                raise ValueError(
                    "Readable critical post-Open dependency differs from genesis: " + relative
                )
            checked.append({"relative_path": relative, "sha256": before})
        if after is None or after.get("sha256") != before:
            differences.append(
                {
                    "relative_path": relative,
                    "genesis_sha256": before,
                    "current": after,
                    "policy": "single_descriptor_session_field"
                    if relative == relative_board
                    else "observed_post_open_difference",
                    "engineering_safety_proven": False,
                }
            )
    return {
        "descriptor": descriptor,
        "critical_genesis_files": checked,
        "all_genesis_differences": differences,
        "critical_scope": (
            "Library/**, board Layout/**, active Config/** excluding *.bak and bak directories"
        ),
        "boundary": (
            "Other Open-stage outputs and backups are retained as differences, not declared "
            "harmless; all current inputs freeze for this plan"
        ),
    }


def _allowed_storage_outputs(board, inputs):
    board = Path(board).resolve()
    pinned = {row["path"]: row for row in inputs["live_files"]}
    outputs = [
        {
            "path": str(board),
            "role": "primary_board",
            "may_create": False,
            "reason": (
                "Existing isolated native board descriptor; complete native protection "
                "remains required"
            ),
        }
    ]
    for path in (
        board.parent / "Layout" / "LayoutDB.lyt",
        board.parent / "Work" / "Layout_Temp" / "LayoutDB.lyt",
    ):
        if str(path) in pinned:
            outputs.append(
                {
                    "path": str(path),
                    "role": "native_copper_storage",
                    "may_create": False,
                    "input_pin": copy.deepcopy(pinned[str(path)]),
                    "reason": (
                        "Predeclared existing LayoutDB storage for this finite copper edit; "
                        "file role alone proves no engineering safety"
                    ),
                }
            )
    return outputs


def storage_dependency_changes(before, after, plan):
    """Map only the exact predeclared existing native storage files locally."""
    expected = _allowed_storage_outputs(plan["board"], plan["input_files"])
    if plan["allowed_output_files"] != expected:
        return {
            "valid": False,
            "reason": "Native storage output declaration differs from original pins",
        }
    mapped = [{**row, "role": "primary_board"} for row in expected]
    result = R.dependency_changes(before, after, mapped)
    result["allowed_output_files"] = copy.deepcopy(expected)
    result["storage_role_is_engineering_proof"] = False
    return result


def _allowed_closed_outputs(board, inputs, save_policy=None):
    board, project = Path(board), Path(inputs["project"])
    icdb = str(project.parent / "database" / "icdb.dat")
    rows = [
        {"collection": key, "observation": copy.deepcopy(row)}
        for key in ("live_files", "live_unavailable")
        for row in inputs[key]
        if row["path"] == icdb
    ]
    if len(rows) != 1:
        raise ValueError("One existing project database/icdb.dat must be explicitly observed")
    return [
        *_allowed_storage_outputs(board, inputs),
        *(
            copy.deepcopy(save_policy["declaration"]["allowed_output_files"])
            if save_policy is not None
            else []
        ),
        {
            "path": icdb,
            "role": "native_icdb_storage",
            "may_create": False,
            "input_observation": rows[0],
            "reason": (
                "Existing native persistence storage; unavailable source-only bytes remain "
                "unknown until the complete closed-window observation"
            ),
        },
    ]


def _lifecycle_diff_outputs(plan, phase):
    """Classify finite runtime rows without treating their role as acceptance."""
    if phase == "continuation":
        outputs = [plan["closed_output_files"][0], plan["closed_output_files"][-1]]
    elif phase == "open":
        outputs = [
            *plan["allowed_output_files"],
            *plan["save_policy"]["declaration"]["open_output_files"],
            plan["closed_output_files"][-1],
        ]
    else:
        outputs = plan["closed_output_files"]
    return [*outputs, *plan["save_policy"]["declaration"]["runtime_markers"]]


def closed_dependency_changes(
    before,
    after,
    plan,
    *,
    require_closed=False,
    continuation=False,
    save_phase=None,
    before_observation=None,
    after_observation=None,
    closed_observation=None,
    source_archive_proof=None,
):
    """Keep icdb visibility facts separate from all other protected file changes."""
    expected = _allowed_closed_outputs(plan["board"], plan["input_files"], plan["save_policy"])
    if plan["closed_output_files"] != expected:
        return {"valid": False, "reason": "Closed storage declaration differs from original pins"}
    icdb = expected[-1]["path"]
    normalized, observed = [], []
    for value in (before, after):
        R._dependency_identity(value)
        current = copy.deepcopy(value)
        found = []
        for key in ("live_files", "live_unavailable"):
            for row in value[key]:
                if row["path"] == icdb:
                    found.append({"collection": key, "observation": copy.deepcopy(row)})
            current[key] = [row for row in current[key] if row["path"] != icdb]
        if len(found) != 1:
            return {"valid": False, "reason": "Existing icdb visibility observation is incomplete"}
        current["live_file_count"] -= 1
        normalized.append(current)
        observed.append(found[0])
    if require_closed and observed[1]["collection"] != "live_files":
        return {"valid": False, "reason": "Closed icdb bytes must be completely readable"}
    policy_proof = None
    allowed = plan["allowed_output_files"][:1] if continuation else plan["allowed_output_files"]
    if save_phase is not None:
        if (
            before_observation is None
            or after_observation is None
            or before_observation.get("inputs") != before
            or after_observation.get("inputs") != after
        ):
            return {
                "valid": False,
                "reason": "Save-policy raw observations do not bind phase inputs",
            }
        policy_proof = SP.verify_phase(
            plan["save_policy"]["declaration"],
            before_observation,
            after_observation,
            phase=save_phase,
            **(
                {
                    "closed_observation": closed_observation,
                    "source_archive_proof": source_archive_proof,
                }
                if continuation
                else {}
            ),
        )
        if save_phase in ("save", "close"):
            allowed = expected[:-1]
        if policy_proof.get("valid") is True:
            if save_phase == "open":
                allowed = [
                    *allowed,
                    *copy.deepcopy(plan["save_policy"]["declaration"]["open_output_files"]),
                ]
            # The finite SP reader proves these precise lifecycle transitions.
            # R's general dependency policy continues to reject all removals.
            runtime_paths = {
                row["path"] for row in plan["save_policy"]["declaration"]["runtime_markers"]
            }
            for value in normalized:
                for collection in ("live_files", "live_unavailable", "non_rule_observed_files"):
                    value[collection] = [
                        row for row in value[collection] if row["path"] not in runtime_paths
                    ]
                value["live_file_count"] = sum(
                    len(value[key])
                    for key in ("live_files", "live_unavailable", "non_rule_observed_files")
                )
            if continuation:
                normalized[0]["source_backup"] = copy.deepcopy(normalized[1]["source_backup"])
    mapped = [{**row, "role": "primary_board"} for row in allowed]
    delta = R.dependency_changes(*normalized, mapped)
    delta["allowed_output_files"] = copy.deepcopy(allowed)
    comparable = all(row["collection"] == "live_files" for row in observed)
    equal = observed[0]["observation"] == observed[1]["observation"] if comparable else None
    return {
        "valid": (
            delta.get("valid") is True
            and not (continuation and comparable and not equal)
            and (policy_proof is None or policy_proof.get("valid") is True)
        ),
        "protected_dependency_changes": delta,
        "icdb": {
            "path": icdb,
            "before": observed[0],
            "after": observed[1],
            "bytes_comparable": comparable,
            "bytes_equal": equal,
            "storage_role_is_engineering_proof": False,
            "unknown_live_bytes_are_equal": False,
        },
        "allowed_output_files": [*copy.deepcopy(allowed), copy.deepcopy(expected[-1])],
        "save_policy": policy_proof,
        "not_checked": [
            "complete private rule/cache semantics of opaque NativeSave stores",
            "actual live icdb bytes whenever the raw observation is unavailable",
        ],
    }


def _closed_inputs(closed, template):
    result = copy.deepcopy(template)
    root = Path(closed["root"])
    old_rows = {
        row["path"]: row
        for key in ("live_files", "non_rule_observed_files")
        for row in template[key]
    }
    result["live_files"], result["non_rule_observed_files"] = [], []
    for row in closed["files"]:
        name = Path(row["path"]).relative_to(root).as_posix()
        collection = "non_rule_observed_files" if R._non_rule_file(name) else "live_files"
        current = copy.deepcopy(row)
        prior = old_rows.get(row["path"], {})
        if all(prior.get(key) == row[key] for key in ("path", "bytes", "sha256")) and (
            "text_observation" in prior
        ):
            current["text_observation"] = copy.deepcopy(prior["text_observation"])
        result[collection].append(current)
    result["live_unavailable"] = []
    result["live_file_count"] = closed["file_count"]
    R._dependency_identity(result)
    return result


def _recovery_plan(request, project, board, inputs, origin):
    paths = [
        row["path"]
        for collection in ("live_files", "non_rule_observed_files", "live_unavailable")
        for row in inputs[collection]
    ]
    paths.extend(
        [
            inputs["source_backup"]["path"],
            request["query_file"]["path"],
            request["isolated_origin_file"]["path"],
            origin["source_backup"]["path"],
        ]
    )
    if origin["continuation_receipt"] is not None:
        paths.append(origin["continuation_receipt"]["path"])
    return K.plan_recovery(
        request["recovery_backup"],
        project,
        board,
        protected_roots=(project.parent, Path(origin["origin_project"]["path"]).parent),
        input_paths=paths,
    )


def _source_archive_proof(inputs):
    """Retain the actual current ZIP metadata after its producer was authenticated."""
    archive = {key: inputs["source_backup"][key] for key in ("path", "bytes", "sha256")}
    with zipfile.ZipFile(archive["path"]) as bundle:
        manifest = json.loads(bundle.read("xpedition-cli-backup.json"))
    return {"archive": archive, "manifest": manifest}


def _continuation_files(prior, inputs, source_archive_proof):
    """Use only the previous SDK producer's exact same-window closed archive."""
    archive = prior["recovery_archive"]
    archive_proof = K.verify_archive(archive)
    if archive_proof.get("valid") is not True:
        raise ValueError("Previous same-window recovery archive is incomplete or changed")
    if source_archive_proof != {
        "archive": archive_proof["archive"],
        "manifest": archive_proof["manifest"],
    }:
        raise ValueError("Continuation source absence proof differs from the original closed ZIP")
    if any(
        inputs["source_backup"].get(key) != archive["archive"][key]
        for key in ("path", "bytes", "sha256")
    ):
        raise ValueError("Continuation source-backup must be the exact previous same-window ZIP")
    prior_inputs = prior["reopened_inputs"]
    declaration = prior["trace"]["save_policy"]["declaration"]
    current_policy_observation = SP.observe(declaration, inputs)
    delta = closed_dependency_changes(
        prior_inputs,
        inputs,
        prior["trace"],
        continuation=True,
        save_phase="continuation",
        before_observation=prior["reopened_policy_observation"],
        after_observation=current_policy_observation,
        closed_observation=prior["closed_policy_observation"],
        source_archive_proof=source_archive_proof,
    )
    if delta.get("valid") is not True:
        raise ValueError("Continuation protected files differ from original SDK reopened inputs")
    return {
        "dependency_changes": delta,
        "current_policy_observation": current_policy_observation,
        "full_file_diff": full_file_diff(
            prior_inputs,
            inputs,
            _lifecycle_diff_outputs(prior["trace"], "continuation"),
        ),
        "prior_recovery_archive": copy.deepcopy(prior_inputs["source_backup"]),
        "current_recovery_archive": copy.deepcopy(inputs["source_backup"]),
        "same_window_archive": copy.deepcopy(archive["archive"]),
        "archive_verification": archive_proof,
        "closed_icdb_observation": copy.deepcopy(prior["closed_dependency_changes"]["icdb"]),
    }


def isolation_provenance(request, project, board, inputs):
    """Read immutable copy provenance and optional prior saved receipt chain."""
    origin_file, origin = _json_file(request["isolated_origin_file"])
    if origin != request["isolated_origin"]:
        raise ValueError("Parsed isolation origin differs from the pinned evidence bytes")
    allowed = {
        "schema_version",
        "kind",
        "origin_project",
        "origin_board",
        "source_backup",
        "target_project",
        "target_board",
        "genesis",
        "provenance",
        "continuation_receipt",
        "copy_operation_id",
    }
    if (
        set(origin) - allowed
        or type(origin.get("schema_version")) is not int
        or origin.get("schema_version") != 1
        or origin.get("kind") != "isolated-component-connect"
    ):
        raise ValueError(
            "Unsupported isolation registration; manual verification fields are refused"
        )
    project, board = Path(project).resolve(), Path(board).resolve()
    if any(set(origin[key]) != {"path"} for key in ("target_project", "target_board")):
        raise ValueError(
            "Target registration binds paths; mutable current SHA or verified flags are refused"
        )
    if (
        Path(origin["target_project"]["path"]).resolve() != project
        or Path(origin["target_board"]["path"]).resolve() != board
        or project.parent not in board.parents
    ):
        raise ValueError(
            "Registered target project/board does not equal the actual isolated document"
        )
    source_project, _ = _pinned_file(origin["origin_project"])
    source_board, _ = _pinned_file(origin["origin_board"])
    source_root = Path(source_project["path"]).parent
    if (
        source_root == project.parent
        or source_root in project.parents
        or project.parent in source_root.parents
    ):
        raise ValueError("Target and origin project trees must be disjoint")
    source_archive, _ = _pinned_file(origin["source_backup"], maximum=1024**3)
    source = R._source_backup(
        source_archive["path"], Path(source_project["path"]), Path(source_board["path"])
    )
    copy_proof = verify_origin(origin, project, board)
    manifest = copy_proof["genesis"]["file_manifest"]
    relative_board = board.relative_to(project.parent).as_posix()
    anchor = digest({key: value for key, value in origin.items() if key != "continuation_receipt"})
    target_project, _ = _file(project)
    target_board, target_board_bytes = _file(board)
    current_closed_source = _source_archive_proof(inputs)
    post_open, continuation_proof, continuation_state_sha256 = None, None, None
    if "continuation_receipt" not in origin:
        if target_project["sha256"] != manifest[project.name]:
            raise ValueError("Initial target project does not equal registered copied genesis")
        post_open = _post_open_genesis(
            origin, manifest, project, board, inputs, target_board, target_board_bytes
        )
        current_source = inputs["source_backup"]
        if current_source["sha256"] != source["sha256"]:
            raise ValueError(
                "Initial current recovery archive must be the registered closed origin backup"
            )
        prior = None
    else:
        prior_file, envelope = _json_file(origin["continuation_receipt"])
        prior, continuation_proof = _sdk_component_receipt(envelope, project, board)
        if prior["trace"]["origin"]["anchor_sha256"] != anchor or prior["trace"]["board"] != str(
            board
        ):
            raise ValueError(
                "Continuation requires this copy's complete verifiable prior saved receipt"
            )
        if prior["saved_project_file"]["sha256"] != target_project["sha256"]:
            raise ValueError("Current target differs from its last fully verified saved receipt")
        continuation_files = _continuation_files(prior, inputs, current_closed_source)
        with zipfile.ZipFile(inputs["source_backup"]["path"]) as current:
            metadata = json.loads(current.read("xpedition-cli-backup.json"))
            backup_board_bytes = current.read(relative_board)
        if metadata["sha256"][project.name] != target_project["sha256"]:
            raise ValueError(
                "Continuation recovery archive does not contain the current saved board/project"
            )
        prior_pin = prior["save_to_reopen_descriptor"]["current"]
        prior_bytes = _descriptor_bytes(prior_pin, board)
        backup_pin = {
            "archive": copy.deepcopy(inputs["source_backup"]),
            "member": relative_board,
            "bytes": len(backup_board_bytes),
            "sha256": hashlib.sha256(backup_board_bytes).hexdigest(),
        }
        if backup_pin["sha256"] != metadata["sha256"][relative_board]:
            raise ValueError("Current closed-backup descriptor member hash differs")
        backup_transition = descriptor_open_transition(
            prior_bytes, backup_board_bytes, prior_pin, backup_pin
        )
        post_open = {
            "descriptor": descriptor_open_transition(
                backup_board_bytes, target_board_bytes, backup_pin, target_board
            ),
            "same_window_archive": copy.deepcopy(prior["recovery_archive"]["archive"]),
            "protected_files_chain": continuation_files,
        }
        continuation_proof["reopened_to_backup_descriptor"] = backup_transition
        continuation_proof["files"] = continuation_files
        continuation_state_sha256 = digest(R.design_state(prior["state_reopened"]))
        prior = prior_file
    return {
        "registration_file": origin_file,
        "anchor_sha256": anchor,
        "origin_project": source_project,
        "origin_board": source_board,
        "source_backup": source,
        "current_closed_source": current_closed_source,
        "target_project": str(project),
        "target_board": str(board),
        "genesis_sha256": digest(manifest),
        "public_copy_evidence": copy_proof,
        "post_open_genesis_evidence": post_open,
        "continuation_receipt": prior,
        "continuation_sdk_evidence": continuation_proof,
        "continuation_state_sha256": continuation_state_sha256,
        "live_inputs": R.input_files(
            source_project["path"], source_board["path"], source_archive["path"]
        ),
    }


def _fingerprint(plan):
    basis = copy.deepcopy(plan)
    basis.pop("fingerprint", None)
    basis["before"] = R.design_state(basis["before"])
    # Snapshot source_state includes only a diagnostic temporary-mask transcript.
    for key in ("source_state_sha256", "snapshot_data_sha256"):
        basis["snapshot"].pop(key, None)
    basis["input_files"] = R._dependency_identity(basis["input_files"])
    basis["origin"]["live_inputs"] = R._dependency_identity(basis["origin"]["live_inputs"])
    post_open = basis["origin"]["post_open_genesis_evidence"]
    if "all_genesis_differences" in post_open:
        post_open["all_genesis_differences"] = [
            row
            for row in post_open["all_genesis_differences"]
            if not R._non_rule_file(row["relative_path"])
        ]
    chains = [post_open.get("protected_files_chain")]
    sdk_chain = basis["origin"].get("continuation_sdk_evidence")
    if sdk_chain is not None:
        chains.append(sdk_chain["files"])
    normalized_chains = set()
    for chain in chains:
        if chain is not None and id(chain) not in normalized_chains:
            normalized_chains.add(id(chain))
            diff = chain["full_file_diff"]
            diff["changes"] = [
                row
                for row in diff["changes"]
                if row["policy"] != "separately_observed_server_journal"
            ]
            diff.pop("before_file_count", None)
            diff.pop("after_file_count", None)
            observation = chain.get("current_policy_observation")
            if observation is not None:
                observation["inputs"] = R._dependency_identity(observation["inputs"])
                for key in ("fingerprint", "declaration_sha256", "input_files_sha256"):
                    observation.pop(key, None)
            policy = chain["dependency_changes"].get("save_policy")
            if policy is not None:
                policy["non_rule_file_changes"] = []
    if basis.get("save_policy") is not None:
        declaration = basis["save_policy"]["declaration"]
        observation = basis["save_policy"]["before_observation"]
        declaration["input_files"] = R._dependency_identity(declaration["input_files"])
        observation["inputs"] = R._dependency_identity(observation["inputs"])
        declaration.pop("fingerprint", None)
        observation.pop("fingerprint", None)
        observation.pop("declaration_sha256", None)
        observation.pop("input_files_sha256", None)
        basis["save_policy"]["before_proof"].pop("declaration_sha256", None)
    if basis.get("candidate") is not None:
        try:
            from .native_component_candidate import fingerprint_basis

            basis["candidate"] = fingerprint_basis(basis["candidate"])
        except ImportError:
            pass
    return digest(basis)


def bind(state, request, inputs, a, identity=None, *, candidate=None, active_view_name=None):
    """Produce a reviewable isolated proposal; malformed evidence never dispatches."""
    request = validate_request(request)
    board = Path(str(identity["board"])).resolve()
    project = Path(inputs["project"]).resolve() if "project" in inputs else None
    if project is None:
        raise ValueError("The current dependency observation must include its project path")
    query_file, retained = _json_file(request["query_file"])
    query = _query_snapshot(retained)
    if query != request["query"]:
        raise ValueError("Component query differs from its retained evidence bytes")
    C._snapshot(query)
    material = M.observe_components(
        state, {"net": request["net"], "net_uid": request["net_uid"], "layer": request["layer"]}
    )
    current = C.build_snapshot(
        state, material, board_id=str(board), net_id=request["net_uid"], layer=request["layer"]
    )
    if (
        not current["valid"]
        or current["snapshot_id"] != request["snapshot_id"]
        or query["snapshot_id"] != current["snapshot_id"]
        or query["target"] != current["target"]
        or query["protection_sha256"] != current["protection_sha256"]
        or query["raw_traces"] != current["raw_traces"]
        or query["routing_objects"] != current["routing_objects"]
        or query["rule_observation_sha256"] != current["rule_observation_sha256"]
    ):
        raise ValueError(
            "The retained component query no longer binds the actual complete board state"
        )
    connection = C.plan_connection(
        current,
        first=request["first"],
        second=request["second"],
        points=request["points"],
        width_mm=request["width_mm"],
    )
    if not connection["structural_valid"]:
        raise ValueError(connection["reason"])
    origin = isolation_provenance(request, project, board, inputs)
    if origin["continuation_state_sha256"] is not None and origin[
        "continuation_state_sha256"
    ] != digest(R.design_state(state)):
        raise ValueError("Current native design differs from the original SDK reopened state")
    library = identity.get("application", {}).get("type_library", {})
    blockers = []
    save_policy = None
    if not connection["no_op"]:
        try:
            declaration = SP.declare(
                board,
                inputs,
                active_view_name=active_view_name,
                source_archive_proof=origin["current_closed_source"],
            )
            observed = SP.observe(declaration, inputs)
            before_proof = SP.verify_phase(declaration, observed, observed, phase="save")
            save_policy = {
                "declaration": declaration,
                "before_observation": observed,
                "before_proof": before_proof,
                "active_view_source": "Document.ActiveView.Name"
                if active_view_name is not None
                else None,
            }
            if before_proof.get("valid") is not True:
                raise ValueError(before_proof.get("reason", "Existing output policy is incomplete"))
        except (KeyError, TypeError, ValueError, OSError) as error:
            save_policy = None
            blockers.append(
                {
                    "code": "E_SAVE_POLICY_INCOMPLETE",
                    "message": "Existing Save output syntax or mirror proof is incomplete",
                    "reason": str(error),
                }
            )
    recovery_plan = None
    if not connection["no_op"]:
        if request["recovery_backup"] is None:
            blockers.append(
                {
                    "code": "E_RECOVERY_BACKUP_REQUIRED",
                    "message": "Declare a new same-window recovery ZIP path before writing",
                }
            )
        else:
            recovery_plan = _recovery_plan(request, project, board, inputs, origin)
    if library.get("major") != 151 or library.get("minor") != 0:
        blockers.append({"code": "E_UNSUPPORTED", "message": "Actual Automation151.0 is required"})
    if type(a.UNIT_MM) is not int or a.UNIT_MM != 4:
        blockers.append(
            {"code": "E_UNSUPPORTED", "message": "Explicit native mm unit4 is required"}
        )
    if not connection["no_op"] and not _native_accounting(current):
        blockers.append(
            {
                "code": "E_NATIVE_TOPOLOGY_INCOMPLETE",
                "message": (
                    "Native opens must correspond to pin-bearing physical groups; cross-group "
                    "regions of one pin or pure copper accounting is not established for writes"
                ),
            }
        )
    minima = _named_minima(state, request)
    if not connection["no_op"]:
        if not minima:
            blockers.append(
                {
                    "code": "E_NAMED_WIDTH_OBSERVATION_INCOMPLETE",
                    "message": "Actual named target-class/layer minimum is unavailable",
                }
            )
        elif any(request["width_mm"] < row["minimum_mm"] for row in minima):
            blockers.append(
                {
                    "code": "E_KNOWN_MINIMUM_WIDTH_VIOLATION",
                    "message": "Requested width is below an actual observed named minimum",
                }
            )
        # The scratch-material verifier is an independent dependency. Until its
        # complete native-source verifier is integrated, no supplied dict can
        # serve as an eligibility override.
        if candidate is None or not _candidate_valid(state, current, connection, candidate):
            blockers.append(
                {
                    "code": "E_CANDIDATE_MATERIAL_INCOMPLETE",
                    "message": (
                        "Pre-write full-width candidate contact and separation evidence is missing"
                    ),
                }
            )
    result = {
        "guard": GUARD,
        "board": str(board),
        "project": str(project),
        "before": copy.deepcopy(state),
        "request": request,
        "snapshot": current,
        "connection": connection,
        "target": current["target"],
        "identity": copy.deepcopy(identity),
        "input_files": copy.deepcopy(inputs),
        "origin": origin,
        "query_file": query_file,
        "can_apply": not blockers and not connection["no_op"],
        "no_op": connection["no_op"],
        "blockers": blockers,
        "native_transaction": copy.deepcopy(SOURCE),
        "named_minimum_observations": minima,
        "candidate": copy.deepcopy(candidate),
        "allowed_output_files": _allowed_storage_outputs(board, inputs),
        "closed_output_files": _allowed_closed_outputs(board, inputs, save_policy),
        "save_policy": save_policy,
        "recovery_plan": recovery_plan,
        "closed_snapshot_sampling": None
        if connection["no_op"]
        else copy.deepcopy(CLOSED_SNAPSHOT_SAMPLING),
        "limits": limitations(),
    }
    result["fingerprint"] = _fingerprint(result)
    return result


def _candidate_valid(state, snapshot, connection, candidate):
    try:
        from .native_component_candidate import verify

        return verify(state, snapshot, connection, candidate).get("valid") is True
    except (ImportError, KeyError, TypeError, ValueError, IndexError):
        return False


def _prepare_plan(doc, state, request, inputs, a, identity):
    from .routing_rule_observation import observe_named_widths

    def with_point_rules(plan):
        observation = observe_named_widths(
            doc,
            {
                "net": request["net"],
                "net_uid": request["net_uid"],
                "layer": request["layer"],
                "points": request["points"],
            },
            a,
        )
        plan["location_rule_observation"] = observation
        sampled = observation["named_widths"]
        if not plan["no_op"] and sampled.get("observation_complete") is not True:
            plan["blockers"].append(
                {
                    "code": "E_OBSERVED_POINT_RULES_INCOMPLETE",
                    "message": "Each declared vertex must have a readable scheme and named widths",
                }
            )
            plan["can_apply"] = False
        below = [
            row
            for row in sampled["points"]
            if type(row["values_mm"].get("minimum")) in (int, float)
            and request["width_mm"] < row["values_mm"]["minimum"]
        ]
        if not plan["no_op"] and below:
            plan["blockers"].append(
                {
                    "code": "E_OBSERVED_POINT_MINIMUM_WIDTH_VIOLATION",
                    "message": "Requested width is below an observed point-scheme named minimum",
                    "point_indices": [row["point_index"] for row in below],
                }
            )
            plan["can_apply"] = False
        # Samples and any additional refusal are bound to the same native plan;
        # complete samples still do not establish applicability or grant eligibility.
        plan["fingerprint"] = _fingerprint(plan)
        return plan

    active_view_name = None
    if any(Path(row["path"]).name == "Graphics Settings.hkp" for row in inputs["live_files"]):
        active_view_name = a._native_call(
            "component_connect.ActiveView.Name", lambda: doc.ActiveView.Name
        )
        if not isinstance(active_view_name, str) or not 1 <= len(active_view_name) <= 1024:
            raise ValueError("Actual Document.ActiveView.Name is unavailable or outside its budget")
    preliminary = bind(state, request, inputs, a, identity, active_view_name=active_view_name)
    if preliminary["no_op"] or any(
        row["code"] != "E_CANDIDATE_MATERIAL_INCOMPLETE" for row in preliminary["blockers"]
    ):
        return with_point_rules(preliminary)
    try:
        from .native_component_candidate import evaluate
    except ImportError:
        return with_point_rules(preliminary)
    candidate = evaluate(doc, a, state, preliminary["snapshot"], preliminary["connection"])
    return with_point_rules(
        bind(
            state,
            request,
            inputs,
            a,
            identity,
            candidate=candidate,
            active_view_name=active_view_name,
        )
    )


def _inputs(params, board, request):
    archive = params.get("source_backup") or request["isolated_origin"]["source_backup"]["path"]
    return {
        **R.input_files(params["project"], board, archive),
        "project": str(Path(params["project"]).resolve()),
    }


def _observe(doc, a, request, app=None):
    return R.observe_components(
        doc,
        a,
        {"net": request["net"], "layer": request["layer"]},
        **(
            {"_identity_reader": lambda: board_labels._identity(app, Path(str(doc.FullName)))}
            if app is not None
            else {}
        ),
    )


def _observe_before_transaction(doc, a, request, verified_state, app=None):
    """Fresh complete design/raw reads; reuse masks only after exact input equality."""
    return R.observe_components_for_save(
        doc,
        a,
        {"net": request["net"], "layer": request["layer"]},
        verified_state,
        **(
            {"_identity_reader": lambda: board_labels._identity(app, Path(str(doc.FullName)))}
            if app is not None
            else {}
        ),
    )


def _after_proof(plan, state, *, reopen_comparison=None):
    request = plan["request"]
    material = M.observe_components(
        state, {"net": request["net"], "net_uid": request["net_uid"], "layer": request["layer"]}
    )
    proof = C.verify_connection(
        plan["snapshot"],
        state,
        material,
        plan=plan["connection"],
        board_id=plan["board"],
        reopen_comparison=reopen_comparison,
    )
    if (
        not proof["valid"]
        or proof.get("requested_geometry_matches") is not True
        or proof.get("native_consistent") is not True
    ):
        raise ValueError(
            "Actual material merge, exact path/width or native opens decrement is incomplete: "
            + str(proof)
        )
    return proof


def _reopen_lifecycle(plan, result):
    """Extract actual one-shot events, never a client Boolean equivalence flag."""
    expected = [
        "Net.UniqueId",
        "TransactionStart",
        "PutTrace",
        "Trace.UniqueId",
        "TransactionEnd",
        "Save",
        "Close",
        "Open",
    ]
    events = result["native_calls_returned"]
    if (
        plan.get("no_op") is not False
        or [row["call"] for row in events] != expected
        or not all(row.get("attempted") is True and row.get("returned") is True for row in events)
        or events[5]["reply"] != {"type": "bool", "value": True}
        or events[6]["reply"] != {"type": "bool", "value": True}
        or events[6].get("active_document_none") is not True
        or events[7].get("return_kind") != "document_and_prompts"
        or events[7].get("licensed_document_path") != plan["board"]
        or events[7].get("actual_identity") != plan["identity"]
        or result.get("closed_proxy_release") != CLOSED_PROXY_RELEASE
    ):
        raise ValueError("One native Save/Close/Empty/ExactOpen with unchanged owner is required")

    def process(identity):
        return {
            "pid": identity["application"]["pid"],
            "creation_time_100ns": identity["generation"]["birth_filetime_100ns"],
            "executable": identity["generation"]["executable"],
        }

    return {
        "save_count": 1,
        "save_returned": True,
        "close_count": 1,
        "close_returned": True,
        "closed_observed": True,
        "open_count": 1,
        "open_returned_document": True,
        "expected_path": plan["board"],
        "reopened_path": events[7]["licensed_document_path"],
        "process_before": process(plan["identity"]),
        "process_after": process(events[7]["actual_identity"]),
    }


def _reopen_comparison(plan, result, reopened_state=None):
    before = result["state_after"]
    after = result["state_reopened"] if reopened_state is None else reopened_state
    request = {key: plan["request"][key] for key in ("net", "net_uid", "layer")}
    # An unchanged state still follows the original strict path. The new
    # lifecycle DTO is required only for an actual observed Net0 substitution.
    lifecycle = (
        _reopen_lifecycle(plan, result) if R.design_state(before) != R.design_state(after) else None
    )
    return NR.compare_reopened(before, after, request, lifecycle)


def _reopen_comparison_evidence(comparison):
    if comparison is None:
        return None
    return {
        key: copy.deepcopy(value) for key, value in comparison.items() if key != "reference_state"
    }


def _trace_identity_evidence(plan, result, proof):
    """Separate the pre-End return object from the complete post-End copper diff."""
    returned = C._uid(result["trace_uid"])
    old_uids = {row["uid"] for row in plan["snapshot"]["routing_objects"]}
    if returned in old_uids:
        raise ValueError("PutTrace returned an existing routing UID; preservation is unproved")
    created = proof["created_trace_uids"]
    if len(created) != 1 or proof.get("requested_geometry_matches") is not True:
        raise ValueError("Exactly one actual new trace must match the complete explicit path")
    return {
        "put_trace_returned_uid_before_end": returned,
        "actual_created_trace_uids": copy.deepcopy(created),
        "returned_uid_present_after_end": returned in created,
        "new_uid_reassigned_at_end": returned != created[0],
        "basis": (
            "Pre-End Trace.UniqueId transcript; post-End UID independently derived "
            "from complete raw routing diff"
        ),
    }


def _event(result, a, name, function):
    event = {"call": name, "attempted": True, "returned": False}
    result["native_calls_returned"].append(event)
    value = a._native_call("component_connect." + name, function)
    event.update(
        returned=True,
        reply={"type": type(value).__name__, "value": value}
        if value is None or type(value) in (bool, int, str)
        else {"type": type(value).__name__},
    )
    return value, event


def full_file_diff(before, after, allowed_output_files):
    """Retain every observed file/read failure; classify without claiming safety."""

    def inventory(inputs):
        rows = {}
        for key in ("live_files", "non_rule_observed_files", "live_unavailable"):
            for row in inputs.get(key, []):
                if row["path"] in rows:
                    raise ValueError("Full dependency diff has a duplicated file identity")
                rows[row["path"]] = {"collection": key, "observation": copy.deepcopy(row)}
        return rows

    old, new = inventory(before), inventory(after)
    declared = {row["path"]: row["role"] for row in allowed_output_files}
    changes = []
    for path in sorted(old.keys() | new.keys()):
        left, right = old.get(path), new.get(path)
        if left == right:
            continue
        policy = (
            "declared_existing_native_copper_storage"
            if declared.get(path) == "native_copper_storage"
            else "declared_existing_native_icdb_storage"
            if declared.get(path) == "native_icdb_storage"
            else "declared_save_side_effect:" + declared[path]
            if path in declared and declared[path] != "primary_board"
            else "declared_existing_primary_board"
            if path in declared
            else "separately_observed_server_journal"
            if all(
                row is None or row["collection"] == "non_rule_observed_files"
                for row in (left, right)
            )
            else "protected_dependency_or_unavailable_read"
        )
        changes.append(
            {
                "path": path,
                "before": left,
                "after": right,
                "policy": policy,
                "engineering_safety_proven": False,
            }
        )
    return {
        "changes": changes,
        "before_file_count": len(old),
        "after_file_count": len(new),
        "boundary": (
            "Storage observations and declared file policy; actual native copper/rule "
            "protection is independently required"
        ),
    }


def _source_inputs(plan, result, phase):
    origin = plan["origin"]
    current = R.input_files(
        origin["origin_project"]["path"],
        origin["origin_board"]["path"],
        origin["source_backup"]["path"],
    )
    result.setdefault("source_input_observations", {})[phase] = current
    result.setdefault("source_full_file_diffs", {})[phase] = full_file_diff(
        origin["live_inputs"], current, []
    )
    if R._dependency_identity(origin["live_inputs"]) != R._dependency_identity(current):
        raise ValueError("The registered original source dependencies changed")
    return current


def _error_fact(error):
    return {
        "code": getattr(error, "code", "E_VERIFY_INCOMPLETE"),
        "message": getattr(error, "message", str(error)),
        "details": copy.deepcopy(getattr(error, "details", {})),
        "cause": type(error).__name__,
    }


def _closed_owner(plan):
    owner = {key: copy.deepcopy(plan["identity"][key]) for key in ("application", "generation")}
    if any(not isinstance(value, dict) or not value for value in owner.values()):
        raise ValueError("Closed sampling requires complete original application/generation pins")
    return owner


def _observe_closed_stable(plan, app, result):
    """Only retry complete file observations, retaining every failed sample."""
    contract = plan["closed_snapshot_sampling"]
    if contract != CLOSED_SNAPSHOT_SAMPLING or digest(contract) != digest(CLOSED_SNAPSHOT_SAMPLING):
        raise ValueError("Closed read sampling differs from its frozen bounded contract")
    expected_owner = _closed_owner(plan)
    evidence = {"contract": copy.deepcopy(contract), "attempts": []}
    result["closed_snapshot_sampling"] = evidence
    for index in range(1, contract["max_attempts"] + 1):
        started = time.monotonic()
        row = {
            "index": index,
            "stage": "closed_snapshot",
            "wait_before_ms": 0 if index == 1 else contract["between_attempts_ms"],
            "observe_started": False,
            "guard": {},
        }
        evidence["attempts"].append(row)
        try:
            if index > 1:
                time.sleep(contract["between_attempts_ms"] / 1000)
            active_document = app.ActiveDocument
            row["guard"]["active_document"] = {
                "type": type(active_document).__name__,
                "is_none": active_document is None,
            }
            if active_document is not None:
                raise ValueError("Closed sampling stopped because ActiveDocument is not None")
            observed_owner = board_labels._application_identity(app)
            row["guard"]["application_identity"] = copy.deepcopy(observed_owner)
            if observed_owner != expected_owner:
                raise ValueError("Closed sampling application or process generation drifted")
        except Exception as error:
            row.update(
                outcome="guard_failed",
                error=_error_fact(error),
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            )
            result["closed_snapshot_failure"] = copy.deepcopy(row)
            raise
        row["observe_started"] = True
        try:
            closed = K.observe_closed(Path(plan["project"]), Path(plan["board"]))
        except K.RecoveryError as error:
            row.update(
                outcome="failed",
                error=_error_fact(error),
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            )
            if index == contract["max_attempts"]:
                result["closed_snapshot_failure"] = copy.deepcopy(row)
                raise
            continue
        except Exception as error:
            row.update(
                outcome="observation_error",
                error=_error_fact(error),
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            )
            result["closed_snapshot_failure"] = copy.deepcopy(row)
            raise
        row.update(
            outcome="succeeded",
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            closed_tree_contract=closed["contract"],
            closed_tree_fingerprint=closed["fingerprint"],
            file_count=closed["file_count"],
            total_bytes=closed["total_bytes"],
        )
        return closed
    raise ValueError("Closed sampling exhausted without a complete tree")


def _verify_closed_sampling(result, plan):
    """Recompute bounded ordering, owner guards and the final complete tree binding."""
    if plan["closed_snapshot_sampling"] != CLOSED_SNAPSHOT_SAMPLING or digest(
        plan["closed_snapshot_sampling"]
    ) != digest(CLOSED_SNAPSHOT_SAMPLING):
        raise ValueError("Closed sampling contract differs from the original bounded declaration")
    evidence = result["closed_snapshot_sampling"]
    if evidence["contract"] != plan["closed_snapshot_sampling"]:
        raise ValueError("Closed sampling receipt differs from its frozen contract")
    rows = evidence["attempts"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= CLOSED_SNAPSHOT_SAMPLING["max_attempts"]:
        raise ValueError("Closed sampling attempt count is empty or exceeds the declared bound")
    expected_guard = {
        "active_document": {"type": "NoneType", "is_none": True},
        "application_identity": _closed_owner(plan),
    }
    for index, row in enumerate(rows, 1):
        wait = 0 if index == 1 else CLOSED_SNAPSHOT_SAMPLING["between_attempts_ms"]
        if (
            type(row["index"]) is not int
            or row["index"] != index
            or row["stage"] != "closed_snapshot"
            or type(row["wait_before_ms"]) is not int
            or row["wait_before_ms"] != wait
            or type(row.get("elapsed_ms")) not in (int, float)
            or not math.isfinite(row["elapsed_ms"])
            or row["elapsed_ms"] < 0
            or row["observe_started"] is not True
            or row["guard"] != expected_guard
            or row["guard"]["active_document"]["is_none"] is not True
        ):
            raise ValueError("Closed sampling order, read-only wait or owner guard differs")
        if index < len(rows):
            error = row["error"]
            if (
                row["outcome"] != "failed"
                or set(error) != {"code", "message", "details", "cause"}
                or not isinstance(error["code"], str)
                or not error["code"].startswith("E_")
                or not isinstance(error["message"], str)
                or not error["message"]
                or not isinstance(error["details"], dict)
                or error["cause"] != "RecoveryError"
                or any(key.startswith("closed_tree_") for key in row)
            ):
                raise ValueError("Earlier closed samples do not retain their exact failure form")
        elif (
            row["outcome"] != "succeeded"
            or "error" in row
            or row["closed_tree_contract"] != K.CONTRACT
            or row["closed_tree_fingerprint"] != result["closed_tree"]["fingerprint"]
            or type(row["file_count"]) is not int
            or row["file_count"] != result["closed_tree"]["file_count"]
            or type(row["total_bytes"]) is not int
            or row["total_bytes"] != result["closed_tree"]["total_bytes"]
        ):
            raise ValueError("The last successful sample does not bind the complete closed tree")
    if "closed_snapshot_failure" in result:
        raise ValueError("A terminal closed sampling failure cannot become saved success")


def execute(params, client, a):
    """Adapter entry: fresh preview or one exact transaction and strict lifecycle."""
    execution_started = time.monotonic()
    try:
        request = validate_request(params["component_connect_request"])
        _, board, app, doc = active(params, client, a, require_project=False)
        identity = board_labels._identity(app, board)
        read_started = time.monotonic()
        state = _observe(doc, a, request, app)
        sampled_seconds = max(0.0, time.monotonic() - read_started)
        observed_read_seconds = sampled_seconds
        inputs = _inputs(params, board, request)
        plan = _prepare_plan(doc, state, request, inputs, a, identity)
        resource_report = EB.estimate(
            sampled_seconds,
            EB.MINIMUM_FULL_READS if not params.get("apply") else EB.MINIMUM_FULL_READS - 1,
            EB.ENGINE_SECONDS
            if not params.get("apply")
            else max(0.0, EB.ENGINE_SECONDS - (time.monotonic() - execution_started)),
            phase="preview" if not params.get("apply") else "after_initial_observation",
            no_op=plan["no_op"],
            spent_non_read_seconds=0.0
            if not params.get("apply")
            else max(0.0, time.monotonic() - execution_started - observed_read_seconds),
        )
    except Exception as error:
        raise a.AdapterError(
            getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "Component preflight incomplete; no transaction or persistence dispatched",
            {"write_attempted": False, "cause": str(error)},
        ) from error
    result = {
        "pcb": str(board),
        "trace": plan,
        "state_guard": GUARD,
        "state_digest": plan["fingerprint"],
        "no_op": plan["no_op"],
        "applied": False,
        "saved": False,
        "persistence_verified": False,
        "write_attempted": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "native_calls_returned": [],
        "stages": dict.fromkeys(
            (
                "preflight",
                "readback",
                "protection",
                "save",
                "close",
                "closed_snapshot",
                "recovery_archive",
                "reopen",
            ),
            "not_started",
        ),
        "limits": limitations(),
        "execution_resources": resource_report,
        "execution_resource_checks": [resource_report],
        "can_apply": plan["can_apply"] and not resource_report["blocked"],
        "blockers": [
            *plan["blockers"],
            *([EB.blocker(resource_report)] if resource_report["blocked"] else []),
        ],
    }
    if not params.get("apply") or plan["no_op"]:
        if plan["no_op"]:
            result["protection"] = {"valid": True, "no_op": True}
        return result
    if resource_report["blocked"]:
        raise a.AdapterError(
            "E_UNSUPPORTED",
            "Complete native observations exceed the fixed apply budget before writing.",
            {
                "write_attempted": False,
                "execution_resource_guard": EB.GUARD,
                "resource_budget": resource_report,
                "native_result": result,
            },
        )
    try:
        if not plan["can_apply"] or params.get("expected") != plan["fingerprint"]:
            raise ValueError("Component request is blocked or its frozen fingerprint changed")
        read_started = time.monotonic()
        fresh = _observe(a._licensed_document(app), a, request, app)
        observed_seconds = max(0.0, time.monotonic() - read_started)
        sampled_seconds = max(sampled_seconds, observed_seconds)
        observed_read_seconds += observed_seconds
        fresh_inputs = _inputs(params, board, request)
        fresh_plan = _prepare_plan(
            doc, fresh, request, fresh_inputs, a, board_labels._identity(app, board)
        )
        if fresh_plan["fingerprint"] != plan["fingerprint"]:
            raise ValueError(
                "State, native identity or recovery/evidence files drifted before transaction"
            )
        read_started = time.monotonic()
        final_state = _observe_before_transaction(a._licensed_document(app), a, request, fresh, app)
        observed_seconds = max(0.0, time.monotonic() - read_started)
        sampled_seconds = max(sampled_seconds, observed_seconds)
        observed_read_seconds += observed_seconds
        result["state_before_transaction"] = final_state
        result["before_transaction_observation_basis"] = (
            "fresh complete native design/raw inputs; prior masks may be reused only "
            "after exact raw input equality"
        )
        if R.design_state(final_state) != R.design_state(fresh):
            raise ValueError(
                "Complete native design changed after candidate cleanup; transaction stopped"
            )
        result["stages"]["preflight"] = "complete"
        final_inputs = _inputs(params, board, request)
        result["before_transaction_inputs"] = final_inputs
        result["before_transaction_identity"] = board_labels._identity(app, board)
        _pinned_file(request["query_file"], maximum=MAX_JSON_BYTES)
        _pinned_file(request["isolated_origin_file"], maximum=MAX_JSON_BYTES)
        if request["isolated_origin"].get("continuation_receipt") is not None:
            _pinned_file(request["isolated_origin"]["continuation_receipt"], maximum=MAX_JSON_BYTES)
        _source_inputs(plan, result, "before_transaction")
        if (
            R._dependency_identity(final_inputs) != R._dependency_identity(fresh_inputs)
            or result["before_transaction_identity"] != identity
        ):
            raise ValueError("Owner or protected evidence files drifted after candidate cleanup")
        net = a._find_net(doc, request["net"])
        if str(net.Name) != request["net"] or str(net.UniqueId) != request["net_uid"]:
            raise ValueError("Exact native net object no longer matches the frozen target")
        resource_report = EB.estimate(
            sampled_seconds,
            3,
            max(0.0, EB.ENGINE_SECONDS - (time.monotonic() - execution_started)),
            phase="before_transaction",
            spent_non_read_seconds=max(
                0.0, time.monotonic() - execution_started - observed_read_seconds
            ),
        )
        result["execution_resources"] = resource_report
        result["execution_resource_checks"].append(resource_report)
        if resource_report["blocked"]:
            result.update(
                can_apply=False, blockers=[*plan["blockers"], EB.blocker(resource_report)]
            )
            raise a.AdapterError(
                "E_UNSUPPORTED",
                "Remaining complete observations do not fit before transaction.",
                {
                    "write_attempted": False,
                    "execution_resource_guard": EB.GUARD,
                    "resource_budget": resource_report,
                },
            )
        result.update(change_status="unknown", persistence_state="unknown")
        put_trace_transaction(
            doc,
            net,
            {
                "layer": request["layer"],
                "width": request["width_mm"],
                "points": request["points"],
                "unit": "mm",
            },
            a,
            result=result,
        )
        if result["net_uid"] != request["net_uid"]:
            raise ValueError("Transaction native owning-net identity differs")
        result["put_trace_returned_uid_before_end"] = result["trace_uid"]
        current = _observe(a._licensed_document(app), a, request, app)
        result.update(
            state_after=current,
            applied=R.design_state(current) != R.design_state(state),
            change_status="partial",
        )
        result["stages"]["readback"] = "complete"
        proof = _after_proof(plan, current)
        result["verification"] = proof
        result["trace_identity_evidence"] = _trace_identity_evidence(plan, result, proof)
        result["actual_created_trace_uids"] = copy.deepcopy(proof["created_trace_uids"])
        post_inputs = _inputs(params, board, request)
        result["post_transaction_inputs"] = post_inputs
        _source_inputs(plan, result, "post_transaction")
        result.setdefault("full_file_diffs", {})["transaction"] = full_file_diff(
            inputs, post_inputs, plan["allowed_output_files"]
        )
        delta = storage_dependency_changes(inputs, post_inputs, plan)
        result["dependency_change_evidence"] = delta
        if not delta["valid"] or board_labels._identity(app, board) != identity:
            raise ValueError("Protected dependency or native owner changed after transaction")
        result["stages"]["protection"] = "passed"
        result["protection"] = {**proof, "dependency_changes": delta}
        if params.get("persist", True) is not True:
            result.update(change_status="applied", persistence_state="unsaved")
            return result
        fresh_before_save = _observe(a._licensed_document(app), a, request, app)
        pre_save_inputs = _inputs(params, board, request)
        result["state_before_save"] = fresh_before_save
        result["pre_save_inputs"] = pre_save_inputs
        result["full_file_diffs"]["pre_save"] = full_file_diff(post_inputs, pre_save_inputs, [])
        _source_inputs(plan, result, "pre_save")
        if (
            R.design_state(fresh_before_save) != R.design_state(current)
            or R._dependency_identity(pre_save_inputs) != R._dependency_identity(post_inputs)
            or board_labels._identity(app, board) != identity
        ):
            raise ValueError("Fresh native material or protected inputs drifted before Save")
        _after_proof(plan, fresh_before_save)
        result["pre_save_policy_observation"] = SP.observe(
            plan["save_policy"]["declaration"], pre_save_inputs
        )
        if (
            SP.verify_phase(
                plan["save_policy"]["declaration"],
                result["pre_save_policy_observation"],
                result["pre_save_policy_observation"],
                phase="save",
            ).get("valid")
            is not True
        ):
            raise ValueError("Fresh Save output syntax or DB mirror proof is incomplete")
        K.verify_plan(plan["recovery_plan"])
        result["stages"]["save"] = "started"
        saved, _ = _event(result, a, "Save", doc.Save)
        if saved is not True:
            raise ValueError("Save requires actual BOOL=True; Close/Open stopped")
        result["saved"], result["stages"]["save"] = True, "acknowledged"
        result["saved_board_file"], saved_descriptor_bytes = _file(board)
        result["save_descriptor_pin"] = {
            **result["saved_board_file"],
            "raw_hex": saved_descriptor_bytes.hex(),
        }
        result["saved_project_file"], _ = _file(plan["project"])
        result["after_save_inputs"] = _inputs(params, board, request)
        result["after_save_policy_observation"] = SP.observe(
            plan["save_policy"]["declaration"], result["after_save_inputs"]
        )
        _source_inputs(plan, result, "after_save")
        result["full_file_diffs"]["save"] = full_file_diff(
            pre_save_inputs, result["after_save_inputs"], _lifecycle_diff_outputs(plan, "save")
        )
        result["save_dependency_changes"] = closed_dependency_changes(
            pre_save_inputs,
            result["after_save_inputs"],
            plan,
            save_phase="save",
            before_observation=result["pre_save_policy_observation"],
            after_observation=result["after_save_policy_observation"],
        )
        if not result["save_dependency_changes"]["valid"]:
            raise ValueError("Protected dependencies changed on Save; Close/Open stopped")
        result["stages"]["close"] = "started"
        closed, close_event = _event(result, a, "Close", lambda: doc.Close(True))
        close_event["active_document_none"] = app.ActiveDocument is None
        if closed is not True or not close_event["active_document_none"]:
            raise ValueError(
                "Close requires actual BOOL=True and empty ActiveDocument; Open stopped"
            )
        result["stages"]["close"] = "confirmed"
        net, doc = None, None
        result["closed_proxy_release"] = copy.deepcopy(CLOSED_PROXY_RELEASE)
        result["stages"]["closed_snapshot"] = "started"
        K.verify_plan(plan["recovery_plan"])
        result["closed_tree"] = _observe_closed_stable(plan, app, result)
        result["closed_inputs"] = _closed_inputs(result["closed_tree"], result["after_save_inputs"])
        result["closed_policy_observation"] = SP.observe(
            plan["save_policy"]["declaration"], result["closed_inputs"]
        )
        result["closed_dependency_changes"] = closed_dependency_changes(
            result["after_save_inputs"],
            result["closed_inputs"],
            plan,
            require_closed=True,
            save_phase="close",
            before_observation=result["after_save_policy_observation"],
            after_observation=result["closed_policy_observation"],
        )
        result["full_file_diffs"]["close"] = full_file_diff(
            result["after_save_inputs"],
            result["closed_inputs"],
            _lifecycle_diff_outputs(plan, "close"),
        )
        _source_inputs(plan, result, "closed")
        if not result["closed_dependency_changes"]["valid"]:
            raise ValueError("Closed tree contains unreadable or undeclared protected changes")
        result["closed_board_file"], closed_descriptor_bytes = _file(board)
        if (
            next(row for row in result["closed_tree"]["files"] if row["path"] == str(board))
            != (result["closed_board_file"])
        ):
            raise ValueError("Closed descriptor changed after the complete stable observation")
        result["closed_descriptor_pin"] = {
            **result["closed_board_file"],
            "raw_hex": closed_descriptor_bytes.hex(),
        }
        result["save_to_closed_descriptor"] = descriptor_open_transition(
            saved_descriptor_bytes,
            closed_descriptor_bytes,
            result["saved_board_file"],
            result["closed_board_file"],
        )
        result["stages"]["closed_snapshot"] = "verified"
        result["stages"]["recovery_archive"] = "started"
        try:
            result["recovery_archive"] = K.seal(plan["recovery_plan"], result["closed_tree"])
        except K.RecoveryError as error:
            result["recovery_archive_failure"] = copy.deepcopy(error.details)
            raise
        result["stages"]["recovery_archive"] = "verified"
        result["stages"]["reopen"] = "started"
        opened, open_event = _event(result, a, "Open", lambda: a._open_layout_document(app, board))
        if (
            not isinstance(opened, tuple)
            or len(opened) != 2
            or opened[0] is None
            or type(opened[0]) in (bool, int, float, str, dict, list, tuple)
            or not isinstance(opened[1], list)
            or any(not isinstance(row, dict) for row in opened[1])
            or Path(str(opened[0].FullName)).resolve() != board.resolve()
        ):
            raise ValueError("Open must return an actual document and prompt records")
        reopened = a._licensed_document(app)
        open_event.update(
            return_kind="document_and_prompts",
            licensed_document_path=str(Path(str(reopened.FullName)).resolve()),
            actual_identity=board_labels._identity(app, board),
        )
        if (
            Path(str(reopened.FullName)).resolve() != board.resolve()
            or open_event["actual_identity"] != identity
        ):
            raise ValueError("Reopened actual document/owner differs")
        after = _observe(reopened, a, request, app)
        result["state_reopened"] = after
        result["save_returned_board_file"] = result["saved_board_file"]
        result["reopened_board_file"], reopened_descriptor_bytes = _file(board)
        result["save_to_reopen_descriptor"] = descriptor_open_transition(
            saved_descriptor_bytes,
            reopened_descriptor_bytes,
            result["saved_board_file"],
            result["reopened_board_file"],
        )
        result["closed_to_reopen_descriptor"] = descriptor_open_transition(
            closed_descriptor_bytes,
            reopened_descriptor_bytes,
            result["closed_board_file"],
            result["reopened_board_file"],
        )
        result["reopened_inputs"] = _inputs(params, board, request)
        result["reopened_policy_observation"] = SP.observe(
            plan["save_policy"]["declaration"], result["reopened_inputs"]
        )
        _source_inputs(plan, result, "reopened")
        result["full_file_diffs"]["close_open"] = full_file_diff(
            result["after_save_inputs"],
            result["reopened_inputs"],
            _lifecycle_diff_outputs(plan, "close_open"),
        )
        result["reopen_dependency_changes"] = closed_dependency_changes(
            result["closed_inputs"],
            result["reopened_inputs"],
            plan,
            save_phase="open",
            before_observation=result["closed_policy_observation"],
            after_observation=result["reopened_policy_observation"],
        )
        result["full_file_diffs"]["open"] = full_file_diff(
            result["closed_inputs"],
            result["reopened_inputs"],
            _lifecycle_diff_outputs(plan, "open"),
        )
        if not result["reopen_dependency_changes"]["valid"]:
            raise ValueError("Protected dependencies changed on Close/Open")
        comparison = _reopen_comparison(plan, result)
        _after_proof(plan, after, reopen_comparison=comparison)
        if comparison is not None:
            result["net0_reopen_comparison"] = _reopen_comparison_evidence(comparison)
        result.update(change_status="applied", persistence_state="saved", persistence_verified=True)
        result["stages"]["reopen"] = "verified"
        if not verify_saved_receipt(result)["valid"]:
            raise ValueError("The complete transaction/persistence receipt is inconsistent")
        return result
    except Exception as error:
        if (
            isinstance(error, a.AdapterError)
            and getattr(error, "details", {}).get("execution_resource_guard") == EB.GUARD
            and result["write_attempted"] is False
        ):
            error.details = {**error.details, "write_attempted": False, "native_result": result}
            raise
        try:
            failed_inputs = _inputs(params, board, request)
            result["failure_input_observation"] = failed_inputs
            result.setdefault("full_file_diffs", {})["failure_from_before"] = full_file_diff(
                inputs,
                failed_inputs,
                plan["closed_output_files"]
                if result["stages"]["save"] != "not_started"
                else plan["allowed_output_files"],
            )
            _source_inputs(plan, result, "failure_from_before")
        except Exception as read_error:
            result["failure_input_observation_error"] = {
                "cause": type(read_error).__name__,
                "message": str(read_error),
            }
        result["post_write_error"] = {
            **_error_fact(error),
            "stage": next(
                (key for key, state in result["stages"].items() if state == "started"), None
            ),
        }
        raise a.AdapterError(
            getattr(error, "code", "E_VERIFY_INCOMPLETE"),
            "Component connection incomplete; retain facts, no automatic persistence or replay",
            {"write_attempted": result["write_attempted"], "native_result": result},
        ) from error


def verify_saved_receipt(result):
    """Recompute saved evidence; producer completion booleans alone are not input."""
    try:
        plan = result["trace"]
        if plan["guard"] != GUARD or plan["fingerprint"] != _fingerprint(plan):
            raise ValueError("Frozen connection plan fingerprint differs")
        _verify_closed_sampling(result, plan)
        if result.get("transaction_source") != SOURCE:
            raise ValueError("The declared formal native transaction contract differs")
        if not _candidate_valid(
            plan["before"], plan["snapshot"], plan["connection"], plan["candidate"]
        ):
            raise ValueError("Pre-write candidate material source/contact evidence is incomplete")
        if result["before_transaction_identity"] != plan["identity"] or R._dependency_identity(
            result["before_transaction_inputs"]
        ) != R._dependency_identity(plan["input_files"]):
            raise ValueError("Final pre-transaction owner or protected dependencies differ")
        request = plan["request"]
        final_state = result["state_before_transaction"]
        if (
            R.design_state(final_state) != R.design_state(plan["before"])
            or M.verify_component_evidence(
                final_state,
                {"net": request["net"], "net_uid": request["net_uid"], "layer": request["layer"]},
            ).get("valid")
            is not True
        ):
            raise ValueError("Complete pre-transaction native design/raw evidence is incomplete")
        for name in (
            "Net.UniqueId",
            "TransactionStart",
            "PutTrace",
            "Trace.UniqueId",
            "TransactionEnd",
        ):
            if result["counters"][name] != {"attempted": 1, "returned": 1}:
                raise ValueError("The one-shot transaction counters are incomplete or repeated")
        expected = [
            "Net.UniqueId",
            "TransactionStart",
            "PutTrace",
            "Trace.UniqueId",
            "TransactionEnd",
            "Save",
            "Close",
            "Open",
        ]
        events = result["native_calls_returned"]
        if [row["call"] for row in events] != expected or not all(
            row.get("attempted") is True and row.get("returned") is True for row in events
        ):
            raise ValueError(
                "The one-shot native transaction/persistence call sequence is incomplete"
            )
        for index in (1, 4, 5, 6):
            if events[index]["reply"] != {"type": "bool", "value": True}:
                raise ValueError("Required native BOOL=True acknowledgement is absent")
        if (
            events[0]["reply"]["value"] != result["net_uid"]
            and str(events[0]["reply"]["value"]) != result["net_uid"]
        ):
            raise ValueError("Native net identity transcript differs")
        if (
            events[3]["reply"]["value"] != result["trace_uid"]
            and str(events[3]["reply"]["value"]) != result["trace_uid"]
        ):
            raise ValueError("Native trace identity transcript differs")
        if (
            events[6].get("active_document_none") is not True
            or events[7].get("return_kind") != "document_and_prompts"
            or events[7].get("licensed_document_path") != plan["board"]
            or events[7].get("actual_identity") != plan["identity"]
            or result.get("closed_proxy_release") != CLOSED_PROXY_RELEASE
        ):
            raise ValueError("Close/Open actual ownership evidence is incomplete")
        after_proof = _after_proof(plan, result["state_after"])
        identity_evidence = _trace_identity_evidence(plan, result, after_proof)
        if (
            result.get("trace_identity_evidence") != identity_evidence
            or result.get("put_trace_returned_uid_before_end") != result["trace_uid"]
            or result.get("actual_created_trace_uids") != after_proof["created_trace_uids"]
        ):
            raise ValueError(
                "Pre-End returned identity and post-End actual created copper facts differ"
            )
        _after_proof(plan, result["state_before_save"])
        comparison = _reopen_comparison(plan, result)
        if result.get("net0_reopen_comparison") != _reopen_comparison_evidence(comparison):
            raise ValueError("Retained default-reference reopening comparison differs")
        _after_proof(plan, result["state_reopened"], reopen_comparison=comparison)
        if R.design_state(result["state_after"]) != R.design_state(result["state_before_save"]):
            raise ValueError("Complete reopened protected state differs")
        for first, second in (
            (plan["input_files"], result["post_transaction_inputs"]),
            (result["post_transaction_inputs"], result["pre_save_inputs"]),
        ):
            if not storage_dependency_changes(first, second, plan)["valid"]:
                raise ValueError("Protected dependency lifecycle differs")
        archive = result["recovery_archive"]
        archive_proof = K.verify_archive(archive)
        recovery = plan["recovery_plan"]
        if (
            archive_proof.get("valid") is not True
            or recovery["archive_path"] != plan["request"]["recovery_backup"]
            or archive["archive"]["path"] != recovery["archive_path"]
            or archive["closed_tree"] != result["closed_tree"]
            or result["closed_tree"]["root"] != str(Path(plan["project"]).parent)
            or result["closed_tree"]["project"] != plan["project"]
            or result["closed_tree"]["board"] != plan["board"]
            or result["closed_inputs"]
            != _closed_inputs(result["closed_tree"], result["after_save_inputs"])
        ):
            raise ValueError("Same-window recovery archive does not bind the complete closed tree")
        if (
            plan["save_policy"]["before_observation"]["inputs"] != plan["input_files"]
            or plan["save_policy"]["declaration"]["source_archive_proof"]
            != plan["origin"]["current_closed_source"]
            or plan["origin"]["current_closed_source"]["archive"]
            != {
                key: plan["input_files"]["source_backup"][key]
                for key in ("path", "bytes", "sha256")
            }
        ):
            raise ValueError("Frozen initial save-policy observation does not bind original inputs")
        initial_policy = SP.verify_phase(
            plan["save_policy"]["declaration"],
            plan["save_policy"]["before_observation"],
            plan["save_policy"]["before_observation"],
            phase="save",
        )
        if (
            initial_policy.get("valid") is not True
            or initial_policy != plan["save_policy"]["before_proof"]
        ):
            raise ValueError("Initial output policy was not proved before the native transaction")
        for key, first, second, closed_required, save_phase, first_obs, second_obs in (
            (
                "save_dependency_changes",
                result["pre_save_inputs"],
                result["after_save_inputs"],
                False,
                "save",
                result["pre_save_policy_observation"],
                result["after_save_policy_observation"],
            ),
            (
                "closed_dependency_changes",
                result["after_save_inputs"],
                result["closed_inputs"],
                True,
                "close",
                result["after_save_policy_observation"],
                result["closed_policy_observation"],
            ),
            (
                "reopen_dependency_changes",
                result["closed_inputs"],
                result["reopened_inputs"],
                False,
                "open",
                result["closed_policy_observation"],
                result["reopened_policy_observation"],
            ),
        ):
            expected_delta = closed_dependency_changes(
                first,
                second,
                plan,
                require_closed=closed_required,
                save_phase=save_phase,
                before_observation=first_obs,
                after_observation=second_obs,
            )
            if expected_delta.get("valid") is not True or result[key] != expected_delta:
                raise ValueError("Closed-window storage/visibility dependency proof differs")
        if R._dependency_identity(result["post_transaction_inputs"]) != R._dependency_identity(
            result["pre_save_inputs"]
        ):
            raise ValueError("Protected pre-save source observation drifted")
        for key, first, second, permitted in (
            (
                "transaction",
                plan["input_files"],
                result["post_transaction_inputs"],
                plan["allowed_output_files"],
            ),
            ("pre_save", result["post_transaction_inputs"], result["pre_save_inputs"], []),
            (
                "save",
                result["pre_save_inputs"],
                result["after_save_inputs"],
                _lifecycle_diff_outputs(plan, "save"),
            ),
            (
                "close",
                result["after_save_inputs"],
                result["closed_inputs"],
                _lifecycle_diff_outputs(plan, "close"),
            ),
            (
                "open",
                result["closed_inputs"],
                result["reopened_inputs"],
                _lifecycle_diff_outputs(plan, "open"),
            ),
            (
                "close_open",
                result["after_save_inputs"],
                result["reopened_inputs"],
                _lifecycle_diff_outputs(plan, "close_open"),
            ),
        ):
            if result["full_file_diffs"][key] != full_file_diff(first, second, permitted):
                raise ValueError(
                    "The retained full file diff differs from its complete observations"
                )
        for phase in (
            "before_transaction",
            "post_transaction",
            "pre_save",
            "after_save",
            "closed",
            "reopened",
        ):
            observed_source = result["source_input_observations"][phase]
            if R._dependency_identity(observed_source) != R._dependency_identity(
                plan["origin"]["live_inputs"]
            ) or result["source_full_file_diffs"][phase] != full_file_diff(
                plan["origin"]["live_inputs"], observed_source, []
            ):
                raise ValueError("Original source dependency protection is incomplete")
        board_rows = {row["path"]: row for row in result["reopened_inputs"]["live_files"]}
        saved_rows = {row["path"]: row for row in result["after_save_inputs"]["live_files"]}
        closed_rows = {row["path"]: row for row in result["closed_inputs"]["live_files"]}
        saved_descriptor_bytes = _descriptor_bytes(result["save_descriptor_pin"], plan["board"])
        closed_descriptor_bytes = _descriptor_bytes(result["closed_descriptor_pin"], plan["board"])
        reopened_descriptor_bytes = _descriptor_bytes(
            result["save_to_reopen_descriptor"]["current"], plan["board"]
        )
        transition = descriptor_open_transition(
            saved_descriptor_bytes,
            reopened_descriptor_bytes,
            result["saved_board_file"],
            result["reopened_board_file"],
        )
        if (
            saved_rows.get(plan["board"]) != result["saved_board_file"]
            or board_rows.get(plan["board"]) != result["reopened_board_file"]
            or board_rows.get(plan["project"]) != result["saved_project_file"]
            or closed_rows.get(plan["board"]) != result["closed_board_file"]
            or transition != result["save_to_reopen_descriptor"]
            or result["save_to_closed_descriptor"]
            != descriptor_open_transition(
                saved_descriptor_bytes,
                closed_descriptor_bytes,
                result["saved_board_file"],
                result["closed_board_file"],
            )
            or result["closed_to_reopen_descriptor"]
            != descriptor_open_transition(
                closed_descriptor_bytes,
                reopened_descriptor_bytes,
                result["closed_board_file"],
                result["reopened_board_file"],
            )
            or result["save_descriptor_pin"]
            != {**result["saved_board_file"], "raw_hex": saved_descriptor_bytes.hex()}
        ):
            raise ValueError(
                "Saved/reopened descriptor bytes do not bind their independent input inventories"
            )
        if (
            result.get("persistence_state") != "saved"
            or result.get("persistence_verified") is not True
            or result.get("saved") is not True
        ):
            raise ValueError("Saved lifecycle is incomplete")
        return {
            "valid": True,
            "scope": "isolated transaction, physical protection and saved lifecycle",
        }
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return {"valid": False, "reason": str(error)}


def verify_receipt(result, bound, fresh_state):
    """SDK readback: compare retained facts with the actual fresh complete state."""
    try:
        if "state" in fresh_state and (
            fresh_state.get("board") != bound["board"]
            or fresh_state.get("identity") != bound["identity"]
        ):
            raise ValueError("Fresh component facts do not bind the actual board and native owner")
        if bound.get("no_op") is True:
            fresh = fresh_state.get("state", fresh_state)
            if (
                bound.get("guard") != GUARD
                or bound["fingerprint"] != _fingerprint(bound)
                or result["trace"]["fingerprint"] != bound["fingerprint"]
                or result.get("no_op") is not True
                or result.get("native_calls_returned") != []
                or result.get("write_attempted") is not False
                or result.get("applied") is not False
                or result.get("saved") is not False
                or result.get("persistence_verified") is not False
                or any(
                    key in result
                    for key in ("closed_tree", "recovery_archive", "net0_reopen_comparison")
                )
                or any(
                    row.get("attempted") or row.get("returned")
                    for row in result.get("counters", {}).values()
                )
                or R.design_state(fresh) != R.design_state(bound["before"])
            ):
                raise ValueError(
                    "No-op requires zero native writes and exactly unchanged full state"
                )
            return {"valid": True, "no_op": True, "persistence_verified": False}
        evidence = verify_saved_receipt(result)
        if not evidence["valid"]:
            return evidence
        if (
            bound.get("guard") != GUARD
            or bound["fingerprint"] != _fingerprint(bound)
            or result["trace"]["fingerprint"] != bound["fingerprint"]
        ):
            raise ValueError("The retained bound operation differs from its native receipt")
        fresh = fresh_state.get("state", fresh_state)
        comparison = _reopen_comparison(bound, result, fresh)
        if result.get("net0_reopen_comparison") != _reopen_comparison_evidence(comparison):
            raise ValueError("Fresh default reference does not match the owned reopened state")
        _after_proof(bound, fresh, reopen_comparison=comparison)
        if R.design_state(fresh) != R.design_state(result["state_reopened"]):
            raise ValueError("Fresh full native state differs from the accepted reopened state")
        return {
            "valid": True,
            "physical_partition_and_protection": True,
            "persistence_verified": True,
            "effective_rule_parser": False,
        }
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return {"valid": False, "reason": str(error)}
