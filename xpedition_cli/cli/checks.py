"""Execute checks once and read retained evidence without rerunning a checker."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..audit import config_dir
from ..errors import CLIError
from ..native_identity import adapter_identity


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _binding(options: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(options.get("project") or "")).expanduser().resolve()
    return {
        "project": str(path),
        "project_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else None,
        "adapter": adapter_identity(),
        "cli_version": __version__,
    }


def _same_adapter(left, right):
    # Onefile executables extract to a new _MEI directory in every process.
    # Retain the complete identities as provenance, but compare the actual
    # immutable executable bytes instead of that temporary module locator.
    if all(x.get("frozen") and len(x.get("build_sha256") or "") == 64 for x in (left, right)):
        keys = ("version", "build_sha256", "client_bitness", "identity_basis", "frozen")
        return all(left.get(k) == right.get(k) for k in keys)
    return left == right


def _write(record: dict[str, Any]) -> None:
    folder = config_dir() / "checks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (record["check_run_id"] + ".json")
    content = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _run(domain: str, options: dict[str, Any], handler: Callable) -> dict[str, Any]:
    run_id = "check_" + uuid.uuid4().hex
    before = _binding(options)
    record = {
        "check_run_id": run_id,
        "domain": domain,
        "source": "candidate-check-invocation",
        "started_at": _utc(),
        "completed_at": None,
        "binding": before,
        "inputs": {
            k: v
            for k, v in options.items()
            if not k.startswith("_") and k not in {"confirm", "dry_run"}
        },
        "execution": "running",
        "native_completion": "unknown",
        "report": None,
        "coverage": ["designer-verification", "derived-netlist-rules"]
        if domain == "schematic"
        else ["layout-batch-drc", "derived-board-rules"],
    }
    _write(record)
    try:
        report = handler(options)
    except CLIError as error:
        known = (error.details or {}).get("native_result") or {}
        record.update(
            execution="unknown"
            if error.code in {"E_TIMEOUT", "E_PROJECT_INVALID", "E_OUTCOME_UNKNOWN"}
            else "failed",
            completed_at=_utc(),
            error={"code": error.code, "message": error.message, "details": error.details or {}},
            native_completion="completed" if known.get("completed") is True else "unknown",
        )
        _write(record)
        error.details = {
            **(error.details or {}),
            "check_run_id": run_id,
            "next_action": (
                f"read {domain} checks result --id {run_id}; "
                "inspect the owned session before retrying"
            ),
        }
        raise
    verification = report.get("verification") or {}
    native = report.get("designer_verification") if domain == "schematic" else report
    completed = bool(isinstance(native, dict) and native.get("completed") is True)
    record.update(
        execution="completed",
        completed_at=_utc(),
        report=report,
        native_completion="completed" if completed else "incomplete",
    )
    record["verification_status"] = verification.get("status", "incomplete")
    _write(record)
    return {
        **report,
        "check_run_id": run_id,
        "check_record": {k: v for k, v in record.items() if k != "report"},
        "_untrusted": [*(report.get("_untrusted") or []), "check_record"],
    }


def schematic_run(options: dict[str, Any]) -> dict[str, Any]:
    from .schematic import check

    return _run("schematic", options, check)


def pcb_run(options: dict[str, Any]) -> dict[str, Any]:
    from .pcb import check

    return _run("pcb", {**options, "no_run": False}, check)


def result(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("native_files"):
        if options.get("id"):
            raise CLIError("E_USAGE", "Use --id or --native-files, not both")
        if (
            options.get("_check_domain") or str(options.get("_command", "")).split(" ")[0]
        ) != "pcb":
            raise CLIError("E_USAGE", "--native-files is only supported by pcb checks result")
        return _historical_native_files(str(options["native_files"]))
    identifier = str(options.get("id") or "")
    if not re.fullmatch(r"check_[0-9a-f]{32}", identifier):
        raise CLIError("E_VALIDATION", "--id must be the check_run_id returned by checks run")
    path = config_dir() / "checks" / (identifier + ".json")
    if not path.is_file():
        raise CLIError("E_NOT_FOUND", "no retained check has this ID", {"check_run_id": identifier})
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CLIError("E_IO", "the retained check record cannot be read") from error
    expected_domain = options.get("_check_domain") or str(options.get("_command", "")).split(" ")[0]
    if expected_domain and record.get("domain") != expected_domain:
        raise CLIError(
            "E_CONFLICT", "this check belongs to another domain", {"domain": record.get("domain")}
        )
    binding = record["binding"]
    current = _binding({"project": binding["project"]})
    changed = current["project_file_sha256"] != binding["project_file_sha256"] or not _same_adapter(
        current["adapter"], binding["adapter"]
    )
    return {
        **record,
        "stale": True if changed else None,
        "native_executed_by_result": False,
        "freshness": "binding_changed"
        if changed
        else "disk_binding_matches_live_freshness_unknown",
        "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "limitations": [
            (
                "Reading the stored result never reruns the checker. A matching disk "
                "hash does not establish current unsaved live state."
            )
        ],
        "_untrusted": [name for name in ("binding", "inputs", "report", "error") if name in record],
    }


def _historical_native_files(filename: str) -> dict[str, Any]:
    """Hash-bound content inspection. Never calls an adapter or writes run/operation state."""
    from .. import drc_engine

    path = Path(filename).expanduser().resolve()
    try:
        manifest_bytes = path.read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, ValueError) as error:
        raise CLIError("E_IO", "The retained native-file manifest cannot be read") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise CLIError("E_VALIDATION", "The manifest must contain a files array")
    files: dict[tuple[str, str], dict[str, Any]] = {}
    for row in manifest["files"]:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("group"), str)
            or not isinstance(row.get("role"), str)
            or not isinstance(row.get("copy"), str)
            or not row["copy"]
            or type(row.get("bytes")) is not int
            or row["bytes"] <= 0
            or not isinstance(row.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        ):
            raise CLIError("E_VALIDATION", "Each retained file needs group/role/copy/bytes/sha256")
        key = row["group"], row["role"]
        if key in files:
            raise CLIError(
                "E_VALIDATION", "Duplicate retained native file role", {"role": list(key)}
            )
        copy = Path(row["copy"]).expanduser()
        if not copy.is_absolute():
            copy = path.parent / copy
        files[key] = {**row, "copy": str(copy.resolve())}
    required = {
        ("outputs_after_go", key)
        for key in ("report", "violations", "effective_rules", "effective_elements")
    } | {("input_configurations", key) for key in ("rules", "elements")}
    if required - files.keys():
        raise CLIError(
            "E_VALIDATION",
            "The manifest must bind requested rules/elements and all four native outputs",
            {"missing_roles": [list(key) for key in sorted(required - files.keys())]},
        )

    def verify():
        contents = {}
        for key, row in files.items():
            try:
                captured = drc_engine._read_contents(Path(row["copy"]))
            except OSError as error:
                raise CLIError(
                    "E_IO", "A retained native file cannot be read", {"role": list(key)}
                ) from error
            except ValueError as error:
                raise CLIError(
                    "E_CONFLICT", "A retained native file changed while reading"
                ) from error
            if (
                captured.record["bytes"] != row["bytes"]
                or captured.record["sha256"] != row["sha256"]
            ):
                raise CLIError(
                    "E_INTEGRITY", "Retained native file hash/length mismatch", {"role": list(key)}
                )
            contents[key] = captured
        try:
            current_manifest = path.read_bytes()
        except OSError as error:
            raise CLIError("E_IO", "The retained manifest became unreadable") from error
        if current_manifest != manifest_bytes:
            raise CLIError("E_CONFLICT", "The retained native-file manifest changed during reading")
        return contents

    verified_contents = verify()
    outputs = {
        key: verified_contents[("outputs_after_go", key)]
        for key in ("report", "violations", "effective_rules", "effective_elements")
    }
    try:
        # Deliberately NOT read_reports with an invented started_ns. That live
        # function still requires fresh files from its own invocation.
        report = drc_engine._parse_report_contents(outputs)
        requested = {
            key: [
                drc_engine._record_tree(r)
                for r in drc_engine.R.parse_records(
                    drc_engine.R.decode_text(verified_contents[("input_configurations", key)].data)
                )
            ]
            for key in ("rules", "elements")
        }
        for key, filetype in (("rules", "BATCH_DRC_FILE"), ("elements", "BATCH_DRC_E2E_FILE")):
            nodes = requested[key]
            if (
                not nodes
                or nodes[0]["keyword"] != "FILETYPE"
                or nodes[0]["values"] != [filetype]
                or sum(node["keyword"] == "FILETYPE" for node in nodes) != 1
            ):
                raise ValueError("Invalid requested " + key + " configuration header")
    except (OSError, ValueError) as error:
        raise CLIError(
            "E_VALIDATION",
            "Retained native report content is incomplete or unsupported",
            {"reason": str(error)},
        ) from error
    verify()
    return {
        "check_run_id": None,
        "domain": "pcb",
        "source": "historical-native-files",
        "execution": "historical_parse",
        "native_completion": "not_established_by_historical_parse",
        "native_executed_by_result": False,
        "report": report,
        "requested_configuration_records": requested,
        "binding": {
            "manifest": str(path),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "files": list(files.values()),
            "manifest_source_fields": {k: v for k, v in manifest.items() if k != "files"},
        },
        "freshness": "historical_not_checked",
        "stale": None,
        "record_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "hazard_content_verification": {"valid": False, "status": "not_performed"},
        "configuration_applicability": "not_established",
        "limitations": [
            "Only hash-bound historical bytes are parsed. No current run, native completion, "
            "live board binding or rule applicability is established.",
            "The original report DATE/TIME is retained verbatim as header values; it is not "
            "a new execution time or freshness proof.",
            "PARTIAL_NET is distinct from UNROUTED_NET. Record counts are not opens counts; "
            "PARTIAL_NET native hazard mapping remains unsupported.",
            "Requested and effective configurations remain separate. Parsing cannot normalize "
            "annular units, infer cover-layer applicability, change an old receipt or resolve "
            "unknown operations.",
        ],
        "_untrusted": ["report", "requested_configuration_records", "binding"],
    }
