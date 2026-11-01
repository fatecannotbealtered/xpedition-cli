"""Finite Save/Close outputs and explicitly observed native session lifetimes.

The formal 151 Save method saves the Document using FullName. The finite paths
and HKP fields below are bounded observations from 054, not a private DB schema.
Only the five authenticated absent-from-source session artifacts may reappear
at Open. Six existing backup destinations may copy only their closed sources;
PCBLog may restore only its actual initial bytes. No helper deletes files.
Other Open/continuation inputs remain strict.
An authenticated nonempty source ForwardAnnotation report stays an exact input;
it never receives the absent-source marker's lifecycle permissions.
"""

from __future__ import annotations

import copy
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import stat
import struct
import zipfile
from pathlib import Path, PureWindowsPath

from . import board_routepass as R
from . import project_isolated_copy as I
from .cell_refresh import digest

CONTRACT = "component-exact-save-close-files-v6"
MAX_HKP_BYTES = 4 * 1024**2
MAX_BMP_BYTES = 8 * 1024**2
MAX_LOG_BYTES = 1024**2
MAX_AUTOSAVE_BYTES = 64 * 1024
MAX_AUTOSAVE_TAIL_BYTES = 16 * 1024
MAX_SOURCE_MANIFEST_BYTES = 8 * 1024**2
NOT_CHECKED = [
    "opaque UPF/JPF/LGC/LayoutDB field semantics",
    "complete effective-rule resolution and opaque native constraint caches",
    "native material/object/rule protection, saved reopen and final BatchDRC (separate checks)",
    "icdb storage and availability transitions (separate closed-window contract)",
]
_SHA = re.compile(r"[0-9a-f]{64}")
_INT = rb"(-?[0-9]+)"
_PAIR = rb"\(" + _INT + rb",[ \t]*" + _INT + rb"\)"
_EOL = rb"(?:\r?\n)?"
_GLOBAL_HKP_HEADERS = frozenset({".Global_Constants 8 2", ".Global_Constants 87 2"})


def _marker_paths(board, root):
    return [
        (board.parent / "AutoActive.lck", 4096),
        (board.parent / "Work" / "Layout_Temp" / "LayoutTemp.tmp", 4096),
        (root / "database" / "cdbsvr" / "sAddress.adr", 4096),
        (board.parent / "LogFiles" / "ForwardAnnotation.txt", 0),
        (board.parent / "LogFiles" / "PCBInfoFile.txt", 0),
    ]


def _backup_paths(board, username):
    root = board.parent
    return [
        (root / "Config" / "Graphics Settings.hkp", root / "Config" / "Graphics Settings.bak"),
        (root / "Config" / username / "UserSettings.upf", root / "Config" / "UserSettings.bak"),
        *[
            (root / "Layout" / name, root / "Work" / "BackupDatabase" / name)
            for name in ("LayoutDB.lyt", "LogicDB.lgc", "JobPrefsDB.jpf")
        ],
        (root / "Layout" / "JobPrefsDB.jpf", root / "Work" / "JobPrefsDB.bak"),
    ]


def _source_shape(proof, inputs):
    if not isinstance(proof, dict) or set(proof) != {"archive", "manifest"}:
        raise ValueError(
            "Current source proof requires actual archive and complete manifest, not verified flags"
        )
    if set(proof["archive"]) != {"path", "bytes", "sha256"} or _pin(proof["archive"]) != _pin(
        inputs["source_backup"]
    ):
        raise ValueError("Current source archive proof differs from the complete input archive pin")
    manifest = proof["manifest"]
    hashes = manifest.get("sha256")
    if (
        not isinstance(hashes, dict)
        or not hashes
        or len(hashes) > 10000
        or manifest.get("all_files") is not True
        or manifest.get("skipped") != []
        or type(manifest.get("files")) is not int
        or manifest["files"] != len(hashes)
        or type(manifest.get("bytes")) is not int
        or manifest["bytes"] < 0
        or manifest.get("project") != Path(inputs["project"]).name
        or len({name.casefold() for name in hashes}) != len(hashes)
        or any(
            not isinstance(value, str) or _SHA.fullmatch(value) is None for value in hashes.values()
        )
    ):
        raise ValueError("Current closed source manifest is incomplete")
    for name in hashes:
        I._safe_member(zipfile.ZipInfo(name))
    return copy.deepcopy(proof)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate current source metadata key")
        result[key] = value
    return result


def _source_archive_data(proof, inputs, board):
    proof = _source_shape(proof, inputs)
    archive = proof["archive"]
    verified = R._source_backup(archive["path"], Path(inputs["project"]), board)
    if _pin(verified) != archive:
        raise ValueError("Current source archive bytes differ from their authenticated input pin")
    relative = (
        (board.parent / "LogFiles" / "PCBAutoSave.txt")
        .relative_to(Path(inputs["project"]).parent)
        .as_posix()
    )
    prefix = None
    with zipfile.ZipFile(archive["path"]) as bundle:
        if bundle.getinfo("xpedition-cli-backup.json").file_size > MAX_SOURCE_MANIFEST_BYTES:
            raise ValueError("Current source manifest exceeds its byte budget")
        actual = json.loads(bundle.read("xpedition-cli-backup.json"), object_pairs_hook=_json_pairs)
        if actual != proof["manifest"]:
            raise ValueError("Supplied current source manifest differs from actual ZIP metadata")
        if relative in actual["sha256"]:
            if bundle.getinfo(relative).file_size > MAX_AUTOSAVE_BYTES:
                raise ValueError("Source autosave log exceeds its fixed byte budget")
            raw = bundle.read(relative)
            prefix = {
                "member": relative,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "raw_hex": raw.hex(),
            }
    if I._record(Path(archive["path"])) != archive:
        raise ValueError("Current source archive changed while deriving session evidence")
    return proof, prefix


def _source_prefix(prefix, proof, relative):
    if prefix is None or prefix.get("member") != relative:
        raise ValueError("Existing autosave log requires its retained closed-source bytes")
    raw = bytes.fromhex(prefix["raw_hex"])
    if (
        type(prefix["bytes"]) is not int
        or prefix["bytes"] != len(raw)
        or len(raw) > MAX_AUTOSAVE_BYTES
        or hashlib.sha256(raw).hexdigest() != prefix["sha256"]
        or proof["manifest"]["sha256"].get(relative) != prefix["sha256"]
    ):
        raise ValueError(
            "Historical source autosave bytes differ from the authenticated manifest member"
        )
    return raw


def _username(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", value)
        or value in {".", ".."}
        or value.endswith(".")
        or value.split(".")[0].lower()
        in {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10)),
        }
    ):
        raise ValueError("The current OS username must be one safe supported path component")
    return value


def _pin(row):
    result = {key: row[key] for key in ("path", "bytes", "sha256")}
    if (
        not isinstance(result["path"], str)
        or not Path(result["path"]).is_absolute()
        or type(result["bytes"]) is not int
        or result["bytes"] < 0
        or not isinstance(result["sha256"], str)
        or _SHA.fullmatch(result["sha256"]) is None
    ):
        raise ValueError("An actual absolute path/bytes/SHA-256 input pin is required")
    return result


def _inventory(inputs, root):
    R._dependency_identity(inputs)
    result = {}
    for collection in ("live_files", "live_unavailable", "non_rule_observed_files"):
        for row in inputs[collection]:
            path = Path(row["path"])
            relative = path.relative_to(root).as_posix()
            if ".." in path.parts or row["path"] in result:
                raise ValueError("File observations must have unique exact project paths")
            if collection == "non_rule_observed_files" and not R._non_rule_file(relative):
                raise ValueError("An unknown file cannot become a server journal")
            if collection != "live_unavailable":
                _pin(row)
            elif path != root / "database" / "icdb.dat":
                raise ValueError("Only the separately handled icdb may be unreadable")
            result[row["path"]] = {"collection": collection, "observation": copy.deepcopy(row)}
    return result


def _declaration(
    board,
    inputs,
    username,
    active_view_name,
    source_archive_proof=None,
    source_prefix=None,
    initial_save_logs=None,
    global_headers=None,
):
    board = Path(board)
    project = Path(inputs["project"])
    root = project.parent
    if not board.is_absolute() or ".." in board.parts or root not in board.parents:
        raise ValueError("An exact existing board inside the project is required")
    username = _username(username)
    inventory = _inventory(inputs, root)
    readable = {
        path: value["observation"]
        for path, value in inventory.items()
        if value["collection"] == "live_files"
    }
    if str(board) not in readable:
        raise ValueError("The existing primary descriptor must have an actual readable pin")
    files, mirrors, added, markers, backup_copies, open_outputs = [], [], [], [], [], []
    protected_source_reports = []
    source = (
        _source_shape(source_archive_proof, inputs) if source_archive_proof is not None else None
    )

    def add(path, role, policy, reason, *, original=False):
        row = readable.get(str(path))
        if row is None:
            return
        record = {
            "path": str(path),
            "role": role,
            "policy": policy,
            "may_create": False,
            "input_pin": _pin(row),
            "reason": reason,
        }
        if policy == "hkp_ui_fields":
            record["ui_field_scope"] = {
                "phases": ["save", "close"],
                "all_other_bytes": "exact",
                "active_view_name": active_view_name,
                "existing_fields": [
                    "created",
                    "ignore_selection_filter",
                    "split_view_height_1",
                    "split_view_height_2",
                    "bound active-view extrema and WindowPlacement",
                    "SPECIAL:Persistent3DViewData WindowPlacement",
                ],
                "global_window_placement": {
                    "section_header": (global_headers or {}).get(
                        str(path), ".Global_Constants 8 2"
                    ),
                    "block_header": "..WindowPlacement",
                    "presence": "one existing complete five-field block on both sides, or absent",
                    "flags": "uint32, exact 0x plus eight hexadecimal digits",
                    "showCmd": "uint32, exact 0x plus eight hexadecimal digits",
                    "ptMinPosition": "two signed int32 decimal coordinates",
                    "ptMaxPosition": "two signed int32 decimal coordinates",
                    "rcNormalPosition": "four signed int32 decimal coordinates",
                    "syntax_outside_numeric_values": "exact",
                },
            }
        files.append(record)
        if not original:
            added.append(copy.deepcopy(record))

    add(
        board,
        "primary_board",
        "opaque_storage",
        "Native document descriptor storage",
        original=True,
    )
    for name in ("LayoutDB.lyt", "LogicDB.lgc", "JobPrefsDB.jpf"):
        first, second = board.parent / "Layout" / name, board.parent / "Work" / "Layout_Temp" / name
        present = [str(path) in readable for path in (first, second)]
        if any(present) and not all(present):
            raise ValueError("Both existing members of a native DB mirror pair are required")
        if all(present):
            if any(
                readable[str(first)][key] != readable[str(second)][key]
                for key in ("bytes", "sha256")
            ):
                raise ValueError("Existing native DB mirrors differ before the plan")
            mirrors.append({"first": str(first), "second": str(second)})
            for path in (first, second):
                add(
                    path,
                    "native_document_storage",
                    "opaque_storage",
                    "Exact existing native Save storage; private fields are not interpreted",
                    original=name == "LayoutDB.lyt",
                )
    for path in (
        board.parent / "Config" / "Graphics Settings.hkp",
        board.parent / "Config" / username / "Graphics Settings.hkp",
    ):
        add(
            path,
            "bounded_native_ui_settings",
            "hkp_ui_fields",
            "Only the declared UI numeric fields may change; all other bytes stay exact",
        )
    add(
        board.parent / "Config" / username / "UserSettings.upf",
        "opaque_native_user_preferences",
        "opaque_storage",
        "Exact current-user Save storage; not proven UI-only or complete rule preservation",
    )
    add(
        board.parent / "Config" / "ScreenShot.bmp",
        "native_document_thumbnail",
        "bmp_thumbnail",
        "Existing bounded standard BMP thumbnail; no engineering correctness claim",
    )
    add(
        board.parent / "LogFiles" / "PCBLogFile.txt",
        "native_document_save_log",
        "save_log",
        "Existing bounded actual native log; both original texts are retained",
    )
    log_path = str(board.parent / "LogFiles" / "PCBLogFile.txt")
    initial_logs = {} if initial_save_logs is None else copy.deepcopy(initial_save_logs)
    if set(initial_logs) != ({log_path} if log_path in readable else set()):
        raise ValueError("The actual initial PCBLogFile raw bytes must be retained")
    if log_path in initial_logs:
        record = initial_logs[log_path]
        raw = bytes.fromhex(record["raw_hex"])
        if (
            set(record) != {"pin", "raw_hex", "log"}
            or record["pin"] != _pin(readable[log_path])
            or len(raw) != record["pin"]["bytes"]
            or hashlib.sha256(raw).hexdigest() != record["pin"]["sha256"]
            or record["log"] != _log(raw)
        ):
            raise ValueError(
                "Initial PCBLogFile raw bytes differ from the actual declaration input pin"
            )
        open_outputs.append(copy.deepcopy(added[-1]))
        open_outputs[-1]["phases"] = ["open_exact_initial_restore"]
    for source_path, destination in _backup_paths(board, username):
        target = readable.get(str(destination))
        if target is None:
            continue  # An absent destination is never a new output permission.
        source_pin = readable.get(str(source_path))
        if source_pin is None:
            raise ValueError("Existing Open backup destination requires its exact readable source")
        mapping = {
            "source": str(source_path),
            "destination": str(destination),
            "source_input_pin": _pin(source_pin),
            "destination_input_pin": _pin(target),
        }
        backup_copies.append(mapping)
        row = {
            "path": str(destination),
            "role": "native_open_backup_copy",
            "policy": "open_backup_copy",
            "input_pin": _pin(target),
            "may_create": False,
            "source_path": str(source_path),
            "source_input_pin": _pin(source_pin),
            "phases": ["open"],
            "reason": "Open may copy only exact closed-source bytes into this pinned destination; "
            "opaque DB meaning is not interpreted",
        }
        files.append(row)
        open_outputs.append(copy.deepcopy(row))
    for path, maximum in _marker_paths(board, root):
        existing = readable.get(str(path))
        if existing is None:
            continue
        relative = path.relative_to(root).as_posix()
        # A retained, nonempty forward-annotation report is source history, not
        # an absent-source transient marker. It gains no output permission:
        # the complete inventory comparison protects its bytes in every phase.
        if (
            path == board.parent / "LogFiles" / "ForwardAnnotation.txt"
            and source is not None
            and relative in source["manifest"]["sha256"]
            and existing["bytes"] > 0
        ):
            if (
                existing["bytes"] > MAX_LOG_BYTES
                or existing["sha256"] != source["manifest"]["sha256"][relative]
            ):
                raise ValueError("Existing forward-annotation report differs from closed source")
            protected_source_reports.append(
                {
                    "path": str(path),
                    "input_pin": _pin(existing),
                    "source_archive": copy.deepcopy(source["archive"]),
                    "source_member": relative,
                    "source_manifest_sha256": digest(source["manifest"]),
                    "policy": "strict_protected_input_in_every_phase",
                    "allowed_output": False,
                    "phases": ["save", "close", "open", "continuation"],
                }
            )
            continue
        if source is None or relative.casefold() in {
            name.casefold() for name in source["manifest"]["sha256"]
        }:
            raise ValueError(
                "A session marker requires authenticated current closed-source absence: " + relative
            )
        if existing["bytes"] > maximum or (
            maximum == 0 and existing["sha256"] != hashlib.sha256(b"").hexdigest()
        ):
            raise ValueError(
                "A native session marker exceeds its exact budget or empty-report condition"
            )
        marker = {
            "path": str(path),
            "role": "native_session_artifact",
            "policy": "session_marker",
            "input_pin": _pin(existing),
            "max_bytes": maximum,
            "phases": {
                "save": "unchanged",
                "close": "may_disappear",
                "open": "absent_may_reappear",
            },
            "source_member_absent": {
                "archive": source["archive"],
                "member": relative,
                "manifest_sha256": digest(source["manifest"]),
            },
            "reason": "Native session lifetime observation; helper never deletes or unlocks files",
        }
        files.append(marker)
        markers.append(copy.deepcopy(marker))
    autosave = board.parent / "LogFiles" / "PCBAutoSave.txt"
    autosave_opening = None
    if str(autosave) in readable:
        if source is None:
            raise ValueError("Autosave output requires the authenticated current closed source")
        historical = _source_prefix(source_prefix, source, autosave.relative_to(root).as_posix())
        current = _pin(readable[str(autosave)])
        same = current["bytes"] == len(historical) and current["sha256"] == source_prefix["sha256"]
        empty = current["bytes"] == 0 and current["sha256"] == hashlib.sha256(b"").hexdigest()
        if not same and not empty:
            raise ValueError(
                "Initial autosave must equal historical source bytes or be exactly empty"
            )
        autosave_opening = {
            "source": copy.deepcopy(source_prefix),
            "source_log": _autosave(historical),
            "current": current,
            "transition": "source_equal" if same else "observed_empty_after_open",
            "bytes_equal": same,
            "tool_reset_dispatched": False,
        }
        add(
            autosave,
            "native_autosave_log",
            "autosave_log",
            "Historical cp936 log retained; Save/continuation exact, Close append, "
            "Open same-or-empty",
        )
        added[-1]["phases"] = ["close", "open_empty_reset"]
        files[-1]["phases"] = ["close", "open_empty_reset"]
        open_outputs.append(copy.deepcopy(added[-1]))
    if any(row["policy"] == "hkp_ui_fields" for row in files) and (
        not isinstance(active_view_name, str)
        or not active_view_name
        or len(active_view_name) > 1024
        or any(character in active_view_name for character in ('"', "\r", "\n", "\x00"))
    ):
        raise ValueError(
            "Existing HKP requires the caller's actual observed Document.ActiveView.Name"
        )
    result = {
        "contract": CONTRACT,
        "board": str(board),
        "project": str(project),
        "username": username,
        "active_view_name": active_view_name,
        "source_archive_proof": source,
        "autosave_source_prefix": copy.deepcopy(source_prefix),
        "autosave_initial_open": autosave_opening,
        "initial_save_logs": initial_logs,
        "input_files": copy.deepcopy(inputs),
        "files": files,
        "db_mirrors": mirrors,
        "allowed_output_files": added,
        "open_output_files": open_outputs,
        "open_backup_copies": backup_copies,
        "runtime_markers": markers,
        "protected_source_reports": protected_source_reports,
        "phases": ["save", "close"]
        if source is None
        else ["save", "close", "open", "continuation"],
        "scope": "Finite Save/Close outputs and authenticated session lifetimes; "
        "engineering acceptance remains separate",
        "sources": {
            "formal": "MGCPCB 151.0 Document.Save DISPID5 BOOL: Save the Document using FullName",
            "observed": "054 failed after Save: exact paths and UI deltas; not native acceptance",
            "session_lifetime": "061 separate normalQuit diff and 025 no-Cu closed ZIP absence; "
            "not a Doc.Close-only guarantee",
            "autosave_open": "081 observed historical source log to empty initial live log; "
            "not a tool reset or historical source-byte rewrite",
            "open_backups": "103 wrapper-aware analysis-v2: six existing copies equal closed "
            "source pins; PCBLog restores actual initial bytes; original outcome remains unknown",
            "global_window_placement": "183 raw HKP observation: only the existing exact "
            "Global_Constants five-field WindowPlacement; original outcome remains unknown. "
            "Win32 WINDOWPLACEMENT/GetWindowPlacement documents explain Windows field types "
            "and meaning, not an official HKP format or Save-time causal proof",
            "retained_forward_annotation": "Complete authenticated closed-source ZIP member "
            "and identical live input pin; source bytes remain strict, not interpreted or ignored",
        },
        "not_checked": copy.deepcopy(NOT_CHECKED),
    }
    result["fingerprint"] = digest(result)
    return result


def declare(board, inputs, *, active_view_name=None, source_archive_proof=None):
    """Declare only exact existing paths; getpass identity is never a CLI override."""
    source, prefix = (
        (None, None)
        if source_archive_proof is None
        else _source_archive_data(source_archive_proof, inputs, Path(board))
    )
    board = Path(board)
    username = getpass.getuser()
    readable = _inventory(inputs, Path(inputs["project"]).parent)
    global_headers = {}
    for path in (
        board.parent / "Config" / "Graphics Settings.hkp",
        board.parent / "Config" / username / "Graphics Settings.hkp",
    ):
        if str(path) in readable:
            pin = _pin(readable[str(path)]["observation"])
            global_headers[str(path)] = _hkp_global_header(_raw(pin, "hkp_ui_fields"))
    initial_logs = {}
    log_path = str(board.parent / "LogFiles" / "PCBLogFile.txt")
    if log_path in readable:
        pin = _pin(readable[log_path]["observation"])
        raw = _raw(pin, "save_log")
        initial_logs[log_path] = {"pin": pin, "raw_hex": raw.hex(), "log": _log(raw)}
    result = _declaration(
        board, inputs, username, active_view_name, source, prefix, initial_logs, global_headers
    )
    for mapping in result["open_backup_copies"]:
        for pin in (mapping["source_input_pin"], mapping["destination_input_pin"]):
            path = Path(pin["path"])
            if path != path.resolve() or I._record(path) != pin:
                raise ValueError(
                    "Open backup source/destination must be exact existing non-reparse pinned files"
                )
    # Unsupported original UI/log formats must stop the preview before mutation.
    for row in result["files"]:
        data = _raw(row["input_pin"], row["policy"])
        if row["policy"] == "hkp_ui_fields":
            header = row["ui_field_scope"]["global_window_placement"]["section_header"]
            _hkp(data, active_view_name, global_header=header)
        elif row["policy"] == "bmp_thumbnail":
            _bmp(data)
        elif row["policy"] == "save_log":
            _log(data)
        elif row["policy"] == "autosave_log":
            _autosave(data)
            if data not in (
                b"",
                _source_prefix(
                    prefix,
                    source,
                    Path(row["path"]).relative_to(Path(inputs["project"]).parent).as_posix(),
                ),
            ):
                raise ValueError(
                    "Initial autosave must equal historical source bytes or be exactly empty"
                )
    return result


def _validate_declaration(value):
    global_headers = {}
    for row in value["files"]:
        if row["policy"] == "hkp_ui_fields":
            header = row["ui_field_scope"]["global_window_placement"]["section_header"]
            if header not in _GLOBAL_HKP_HEADERS:
                raise ValueError("Unsupported declared HKP Global_Constants section syntax")
            global_headers[row["path"]] = header
    expected = _declaration(
        Path(value["board"]),
        value["input_files"],
        value["username"],
        value["active_view_name"],
        value["source_archive_proof"],
        value["autosave_source_prefix"],
        value["initial_save_logs"],
        global_headers,
    )
    if value != expected:
        raise ValueError("Save policy differs from its exact original file declaration")


def _hkp_global_header(raw):
    lines = [
        line
        for line in raw.splitlines(keepends=True)
        if re.match(rb"[ \t]*\.Global_Constants\b", line)
    ]
    if not lines:
        return ".Global_Constants 8 2"  # Preserve the existing declaration for an absent section.
    if len(lines) != 1:
        raise ValueError("HKP Global_Constants section is duplicated")
    for header in _GLOBAL_HKP_HEADERS:
        if re.fullmatch(re.escape(header.encode("ascii")) + _EOL, lines[0]):
            return header
    raise ValueError("Unsupported exact HKP Global_Constants section syntax")


def _hkp(raw, board_name, *, global_header=".Global_Constants 8 2"):
    if global_header not in _GLOBAL_HKP_HEADERS:
        raise ValueError("Unsupported declared HKP Global_Constants section syntax")
    if len(raw) > MAX_HKP_BYTES:
        raise ValueError("HKP exceeds its declared byte budget")
    raw.decode("utf-8-sig")
    lines = raw.splitlines(keepends=True)
    fields, counts = [], {}
    context = None
    global_sections = 0
    global_placement_lines = set()

    def replace(index, expression, name, *, bounds=(-2147483648, 2147483647), timestamp=False):
        if index >= len(lines):
            raise ValueError("Incomplete exact HKP UI field syntax: " + name)
        matched = re.fullmatch(expression, lines[index])
        if matched is None:
            raise ValueError("Unsupported exact HKP UI field syntax: " + name)
        values = [value.decode("ascii") for value in matched.groups()]
        if timestamp:
            dt.datetime.strptime(values[0], "%m/%d/%y %H:%M:%S")
        else:
            for value in values:
                number = int(value, 16) if value.startswith("0x") else int(value)
                if not bounds[0] <= number <= bounds[1]:
                    raise ValueError("HKP UI integer is outside its declared bounds")
        counts[name] = counts.get(name, 0) + 1
        if counts[name] != 1:
            raise ValueError("HKP UI field is duplicated: " + name)
        fields.append({"field": name, "line": index + 1, "values": values})
        line = lines[index]
        for group in range(len(matched.groups()), 0, -1):
            start, end = matched.span(group)
            line = line[:start] + b"<UI>" + line[end:]
        lines[index] = line

    for index, line in enumerate(list(lines)):
        if re.match(rb"[ \t]*\.(?!\.)", line):
            context = None  # Unknown top-level sections cannot inherit a permitted UI scope.
            section = re.fullmatch(rb'\.(view|generic_view) "([^"\r\n]+)"' + _EOL, line)
            if section:
                context = (section[1].decode("ascii"), section[2].decode("utf-8"))
            elif re.match(rb"[ \t]*\.Global_Constants\b", line):
                if not re.fullmatch(re.escape(global_header.encode("ascii")) + _EOL, line):
                    raise ValueError("Unsupported exact HKP Global_Constants section syntax")
                global_sections += 1
                if global_sections != 1:
                    raise ValueError("HKP Global_Constants section is duplicated")
                context = ("global", "Global_Constants")
        if (
            context == ("global", "Global_Constants")
            and index not in global_placement_lines
            and re.search(
                rb"![ \t]*(?:flags|showCmd|ptMinPosition|ptMaxPosition|\.\.rcNormalPosition)"
                + _EOL,
                line,
            )
        ):
            raise ValueError("HKP global placement field is outside its exact five-field block")
        if re.match(rb"^[ \t]*![ \t]*Created\b", line):
            replace(
                index,
                rb"[ \t]*![ \t]*Created ([0-9]{2}/[0-9]{2}/[0-9]{2} "
                rb"[0-9]{2}:[0-9]{2}:[0-9]{2})" + _EOL,
                "created",
                timestamp=True,
            )
        elif b'"Global.Option.IgnoreSelectionFilter"' in line:
            replace(
                index,
                rb'[ \t]*"Global.Option.IgnoreSelectionFilter" "([01])"' + _EOL,
                "ignore_selection_filter",
                bounds=(0, 1),
            )
        elif any(f'"[VirtualGlobal].iDC.SplitViewHeight.{n}"'.encode() in line for n in (1, 2)):
            number = 1 if b"SplitViewHeight.1" in line else 2
            replace(
                index,
                rb'[ \t]*"\[VirtualGlobal\]\.iDC\.SplitViewHeight\.'
                + str(number).encode()
                + rb'" "([0-9]+)"'
                + _EOL,
                "split_view_height_" + str(number),
                bounds=(0, 100000),
            )
        elif context == ("view", board_name) and re.fullmatch(rb"[ \t]*\.\.extrema" + _EOL, line):
            for offset, label in ((1, b"xmin, ymin"), (2, b"xmax, ymax")):
                replace(
                    index + offset,
                    rb"[ \t]*" + _PAIR + rb"[ \t]*! \(" + label + rb"\)" + _EOL,
                    "main.extrema." + str(offset),
                )
        elif context in (
            ("view", board_name),
            ("generic_view", "SPECIAL:Persistent3DViewData"),
            ("global", "Global_Constants"),
        ) and re.fullmatch(rb"[ \t]*\.\.WindowPlacement" + _EOL, line):
            prefix = {"view": "main", "generic_view": "persistent3d", "global": "global"}[
                context[0]
            ]
            if prefix == "global":
                global_placement_lines.update(range(index + 1, index + 6))
            for offset, label in ((1, b"flags"), (2, b"showCmd")):
                replace(
                    index + offset,
                    rb"[ \t]*(0x[0-9a-fA-F]{8})[ \t]*! " + label + _EOL,
                    prefix + ".placement." + label.decode(),
                    bounds=(0, 4294967295),
                )
            for offset, label in ((3, b"ptMinPosition"), (4, b"ptMaxPosition")):
                replace(
                    index + offset,
                    rb"[ \t]*" + _PAIR + rb"[ \t]*! " + label + _EOL,
                    prefix + ".placement." + label.decode(),
                )
            replace(
                index + 5,
                rb"[ \t]*" + _PAIR + rb"[ \t]+" + _PAIR + rb"[ \t]*! \.\.rcNormalPosition" + _EOL,
                prefix + ".placement.rect",
            )
    required = {"created", "ignore_selection_filter", "split_view_height_1", "split_view_height_2"}
    required |= {
        "main.extrema.1",
        "main.extrema.2",
        "main.placement.flags",
        "main.placement.showCmd",
        "main.placement.ptMinPosition",
        "main.placement.ptMaxPosition",
        "main.placement.rect",
    }
    if global_sections:
        required |= {
            "global.placement." + field
            for field in ("flags", "showCmd", "ptMinPosition", "ptMaxPosition", "rect")
        }
    if not required <= counts.keys():
        raise ValueError("HKP lacks the complete supported bound UI field syntax")
    return b"".join(lines), fields


def _bmp(raw):
    if not 54 <= len(raw) <= MAX_BMP_BYTES or raw[:2] != b"BM":
        raise ValueError("A bounded standard BMP thumbnail is required")
    size, reserved, offset = struct.unpack_from("<III", raw, 2)
    dib, width, height, planes, bits, compression, image_size = struct.unpack_from(
        "<IiiHHII", raw, 14
    )
    if (
        size != len(raw)
        or reserved != 0
        or offset != 54
        or dib != 40
        or planes != 1
        or bits not in (24, 32)
        or compression != 0
        or not 1 <= width <= 4096
        or not 1 <= abs(height) <= 4096
    ):
        raise ValueError("Unsupported BMP header, dimensions, depth or compression")
    pixels = ((width * bits + 31) // 32) * 4 * abs(height)
    if len(raw) != 54 + pixels or image_size not in (0, pixels):
        raise ValueError("BMP pixel length or trailing data is inconsistent")
    return {
        "width": width,
        "height": height,
        "bits_per_pixel": bits,
        "header_hex": raw[:54].hex(),
        "pixel_bytes": pixels,
    }


def _log(raw):
    if len(raw) > MAX_LOG_BYTES:
        raise ValueError("Native save log exceeds its declared byte budget")
    result = R._native_text(raw)
    if "Xpedition Layout" not in result["text"]:
        raise ValueError("Native save log lacks its actual product header")
    return result


def _autosave(raw):
    """Only the observed cp936 native blocks; this is not the PCBLogFile format."""
    if len(raw) > MAX_AUTOSAVE_BYTES:
        raise ValueError("Native autosave log exceeds its fixed byte budget")
    text = raw.decode("cp936")
    if (
        text.encode("cp936") != raw
        or any(ord(char) < 32 and char not in "\r\n\t" for char in text)
        or "\r" in text.replace("\r\n", "")
        or "\n" in text.replace("\r\n", "")
    ):
        raise ValueError("Autosave must be exact cp936 native CRLF text")
    lines = [line for line in text.split("\r\n") if line.strip()]
    if len(lines) % 4:
        raise ValueError("Autosave native blocks must retain title, divider, time and Job Name")
    months = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    entries = []
    for index in range(0, len(lines), 4):
        title, divider, stamp, job = lines[index : index + 4]
        title = title.strip(" ")
        if title not in {"自动保存日志文件", "自动保存日志已关闭"} or divider.strip(" ") != (
            "-" * (16 if title == "自动保存日志文件" else 18)
        ):
            raise ValueError("Unsupported native autosave title or divider")
        match = re.fullmatch(
            r"([0-9]{1,2}):([0-9]{2}) (AM|PM) ("
            + "|".join(weekdays)
            + r"), ("
            + "|".join(months)
            + r") ([0-9]{1,2}), ([0-9]{4})",
            stamp.strip(" "),
        )
        if match is None or not re.fullmatch(r"Job Name: [^\r\n]+", job):
            raise ValueError("Unsupported native autosave timestamp or Job Name syntax")
        hour, minute = int(match[1]), int(match[2])
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            raise ValueError("Native autosave clock is outside its valid range")
        observed = dt.datetime(
            int(match[7]),
            months.index(match[5]) + 1,
            int(match[6]),
            hour % 12 + (12 if match[3] == "PM" else 0),
            minute,
        )
        if weekdays[observed.weekday()] != match[4]:
            raise ValueError("Native autosave weekday and calendar date disagree")
        name = job[len("Job Name: ") :]
        if (
            not (Path(name).is_absolute() or PureWindowsPath(name).is_absolute())
            or ".." in Path(name).parts
            or ".." in PureWindowsPath(name).parts
        ):
            raise ValueError("Native autosave Job Name must be an actual absolute board path")
        entries.append({"title": title, "time": observed.isoformat(), "job_name": name})
    return {"encoding": "cp936", "text": text, "entries": entries, "_untrusted": ["text"]}


def _raw(row, policy):
    if policy not in {"hkp_ui_fields", "bmp_thumbnail", "save_log", "autosave_log"}:
        return None
    maximum = {
        "hkp_ui_fields": MAX_HKP_BYTES,
        "bmp_thumbnail": MAX_BMP_BYTES,
        "save_log": MAX_LOG_BYTES,
        "autosave_log": MAX_AUTOSAVE_BYTES,
    }[policy]
    path = Path(row["path"])
    before = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_file_attributes", 0) & 0x400
        or row["bytes"] > maximum
    ):
        raise ValueError("A bounded regular non-reparse policy file is required")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(maximum + 1)
        final = os.fstat(stream.fileno())
    after = path.stat(follow_symlinks=False)
    # Windows path.stat and fd fstat expose different ctime semantics. Compare
    # ctime within each API, while comparing identity/size/mtime across APIs.
    keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if (
        any(
            getattr(before, key) != getattr(item, key)
            for item in (opened, final, after)
            for key in keys
        )
        or before.st_ctime_ns != after.st_ctime_ns
        or opened.st_ctime_ns != final.st_ctime_ns
    ):
        raise ValueError("A policy file changed while its actual bytes were captured")
    if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError("Actual policy bytes differ from the complete input pin")
    return data


def observe(declaration, inputs):
    """Capture actual bounded UI/log bytes against the caller's complete raw input pins."""
    _validate_declaration(declaration)
    if _username(getpass.getuser()) != declaration["username"]:
        raise ValueError("Current OS username differs from the frozen Save declaration")
    inventory = _inventory(inputs, Path(declaration["project"]).parent)
    rows = []
    for declared in declaration["files"]:
        observed = inventory.get(declared["path"])
        if observed is None and declared["policy"] == "session_marker":
            rows.append({"path": declared["path"], "pin": None})
            continue
        if observed is None or observed["collection"] != "live_files":
            raise ValueError("A declared existing Save output disappeared or became unreadable")
        pin = _pin(observed["observation"])
        row = {"path": declared["path"], "pin": pin}
        data = _raw(pin, declared["policy"])
        if data is not None:
            row["raw_hex"] = data.hex()
        rows.append(row)
    result = {
        "contract": CONTRACT,
        "declaration_sha256": declaration["fingerprint"],
        "input_files_sha256": digest(inputs),
        "inputs": copy.deepcopy(inputs),
        "files": rows,
    }
    result["fingerprint"] = digest(result)
    return result


def _observation(declaration, observation):
    if (
        observation["contract"] != CONTRACT
        or observation["declaration_sha256"] != declaration["fingerprint"]
        or observation["input_files_sha256"] != digest(observation["inputs"])
        or observation["fingerprint"]
        != digest({key: value for key, value in observation.items() if key != "fingerprint"})
    ):
        raise ValueError("Policy observation differs from its complete retained raw facts")
    inventory = _inventory(observation["inputs"], Path(declaration["project"]).parent)
    rows = observation["files"]
    if [row["path"] for row in rows] != [row["path"] for row in declaration["files"]]:
        raise ValueError("Policy file observations are missing, duplicated or substituted")
    result = {}
    for declared, row in zip(declaration["files"], rows, strict=True):
        if declared["policy"] == "session_marker" and row.get("pin") is None:
            if declared["path"] in inventory or set(row) != {"path", "pin"}:
                raise ValueError("Session marker absence differs from the complete inventory")
            result[row["path"]] = row
            continue
        if inventory[row["path"]]["collection"] != "live_files" or row["pin"] != _pin(
            inventory[row["path"]]["observation"]
        ):
            raise ValueError("Policy observation does not bind its complete input inventory")
        if declared["policy"] not in {"opaque_storage", "session_marker", "open_backup_copy"}:
            raw = bytes.fromhex(row["raw_hex"])
            if (
                len(raw) != row["pin"]["bytes"]
                or hashlib.sha256(raw).hexdigest() != row["pin"]["sha256"]
            ):
                raise ValueError("Retained policy bytes do not bind their actual input pin")
        result[row["path"]] = row
    return inventory, result


def verify_phase(
    declaration,
    before_observation,
    after_observation,
    *,
    phase,
    closed_observation=None,
    source_archive_proof=None,
):
    """Pure recomputation; the caller separately verifies icdb and engineering state."""
    proof = {
        "contract": CONTRACT,
        "valid": False,
        "phase": phase,
        "checks": [],
        "files": [],
        "not_checked": copy.deepcopy(NOT_CHECKED),
    }
    try:
        if phase not in declaration["phases"]:
            raise ValueError("This finite output policy does not authorize that lifecycle phase")
        _validate_declaration(declaration)
        old_inputs, old = _observation(declaration, before_observation)
        new_inputs, new = _observation(declaration, after_observation)
        current_source = None
        closed_files = None
        if source_archive_proof is not None:
            current_source = _source_shape(source_archive_proof, after_observation["inputs"])
        if closed_observation is not None:
            _, closed_files = _observation(declaration, closed_observation)
        for key in ("source_backup", "project", "non_rule_exclusion_policy", "limitations"):
            if before_observation["inputs"].get(key) != after_observation["inputs"].get(key):
                if (
                    key == "source_backup"
                    and phase == "continuation"
                    and current_source is not None
                ):
                    proof["source_archive_transition"] = {
                        "before": copy.deepcopy(before_observation["inputs"][key]),
                        "after": copy.deepcopy(after_observation["inputs"][key]),
                        "current_authenticated_source": copy.deepcopy(current_source),
                        "raw_observations_normalized": False,
                    }
                    continue
                raise ValueError("Save/Close source or dependency observation contract changed")
        icdb = str(Path(declaration["project"]).parent / "database" / "icdb.dat")
        delegated, journals = [], []
        allowed = set(old)
        for path in sorted(old_inputs.keys() | new_inputs.keys()):
            first, second = old_inputs.get(path), new_inputs.get(path)
            if path == icdb:
                delegated.append(
                    {
                        "path": path,
                        "before": first,
                        "after": second,
                        "verified_by_this_policy": False,
                    }
                )
            elif path not in allowed and first != second:
                if all(
                    item is None or item["collection"] == "non_rule_observed_files"
                    for item in (first, second)
                ):
                    journals.append({"path": path, "before": first, "after": second})
                else:
                    raise ValueError(
                        "Unknown protected file changed, appeared or disappeared: " + path
                    )
        proof["delegated_icdb_observations"] = delegated
        proof["non_rule_file_changes"] = journals
        proof["protected_source_reports"] = []
        for report in declaration["protected_source_reports"]:
            path = report["path"]
            original = report["input_pin"]
            if any(
                inventory.get(path, {}).get("collection") != "live_files"
                or _pin(inventory[path]["observation"]) != original
                for inventory in (old_inputs, new_inputs)
            ):
                raise ValueError("Retained source report must remain an exact protected input")
            proof["protected_source_reports"].append(
                {**copy.deepcopy(report), "before": original, "after": original, "unchanged": True}
            )
        for pair in declaration["db_mirrors"]:
            for rows in (old, new):
                if any(
                    rows[pair["first"]]["pin"][key] != rows[pair["second"]]["pin"][key]
                    for key in ("bytes", "sha256")
                ):
                    raise ValueError("Native DB mirror bytes disagree within a phase observation")
        for declared in declaration["files"]:
            first, second = old[declared["path"]], new[declared["path"]]
            row = {
                "path": declared["path"],
                "role": declared["role"],
                "policy": declared["policy"],
                "before": first["pin"],
                "after": second["pin"],
                "changed": first["pin"] != second["pin"],
                "engineering_safety_proven": False,
            }
            policy = declared["policy"]
            if policy == "session_marker":
                for pin in (first["pin"], second["pin"]):
                    if pin is not None and (
                        _pin(pin) != pin
                        or pin["bytes"] > declared["max_bytes"]
                        or (
                            declared["max_bytes"] == 0
                            and pin["sha256"] != hashlib.sha256(b"").hexdigest()
                        )
                    ):
                        raise ValueError(
                            "Session marker exceeds its fixed budget or empty-report condition"
                        )
                if phase == "save" and (
                    first["pin"] != declared["input_pin"] or first["pin"] != second["pin"]
                ):
                    raise ValueError(
                        "Save cannot remove, recreate or modify a native session marker"
                    )
                if phase == "close" and second["pin"] is not None and first["pin"] != second["pin"]:
                    raise ValueError(
                        "Close may only retain a session marker exactly or observe its absence"
                    )
                if phase == "open" and first["pin"] is not None and first["pin"] != second["pin"]:
                    raise ValueError(
                        "Open may recreate only a marker proved absent in the closed observation"
                    )
                if phase == "continuation" and first["pin"] != second["pin"]:
                    relative = (
                        Path(declared["path"])
                        .relative_to(Path(declaration["project"]).parent)
                        .as_posix()
                    )
                    if (
                        closed_files is None
                        or closed_files[declared["path"]]["pin"] is not None
                        or current_source is None
                        or relative.casefold()
                        in {name.casefold() for name in current_source["manifest"]["sha256"]}
                    ):
                        raise ValueError(
                            "Continuation marker renewal lacks its authenticated closed absence"
                        )
                row["presence_transition"] = {
                    "before_present": first["pin"] is not None,
                    "after_present": second["pin"] is not None,
                    "tool_deletion_dispatched": False,
                    "source_absence": copy.deepcopy(declared["source_member_absent"]),
                }
            elif policy == "open_backup_copy":
                if phase != "open" and row["changed"]:
                    raise ValueError("Backup-copy destinations may change only during Open")
                if phase == "open":
                    source_path = declared["source_path"]
                    source_before = old_inputs.get(source_path)
                    source_after = new_inputs.get(source_path)
                    if (
                        source_before is None
                        or source_after is None
                        or source_before["collection"] != "live_files"
                        or source_after["collection"] != "live_files"
                    ):
                        raise ValueError(
                            "Open backup copy requires both actual readable source pins"
                        )
                    closed_source = _pin(source_before["observation"])
                    reopened_source = _pin(source_after["observation"])
                    source_unchanged = closed_source == reopened_source
                    copied = all(
                        second["pin"][key] == closed_source[key] for key in ("bytes", "sha256")
                    )
                    if row["changed"] and (not source_unchanged or not copied):
                        raise ValueError(
                            "Changed Open backup must equal its unchanged closed-source bytes"
                        )
                    row["copy_evidence"] = {
                        "closed_source": closed_source,
                        "after_open_source": reopened_source,
                        "closed_destination": first["pin"],
                        "after_open_destination": second["pin"],
                        "copy_required": row["changed"],
                        "destination_equals_closed_source": copied,
                        "source_unchanged": source_unchanged,
                        "engineering_safety_proven": False,
                    }
            elif (
                phase in ("open", "continuation")
                and row["changed"]
                and policy not in {"autosave_log", "save_log"}
            ):
                original_storage = {declaration["board"]}
                if phase == "open":
                    original_storage |= {
                        str(Path(declaration["board"]).parent / directory / "LayoutDB.lyt")
                        for directory in ("Layout", "Work/Layout_Temp")
                    }
                if declared["path"] not in original_storage:
                    raise ValueError(
                        "Open/continuation cannot rewrite a Save-only DB, Config or log file"
                    )
                row["separate_native_storage_verification_required"] = True
            if policy not in {"opaque_storage", "session_marker", "open_backup_copy"}:
                first_raw, second_raw = (
                    bytes.fromhex(first["raw_hex"]),
                    bytes.fromhex(second["raw_hex"]),
                )
                if policy == "hkp_ui_fields":
                    header = declared["ui_field_scope"]["global_window_placement"]["section_header"]
                    left, old_fields = _hkp(
                        first_raw, declaration["active_view_name"], global_header=header
                    )
                    right, new_fields = _hkp(
                        second_raw, declaration["active_view_name"], global_header=header
                    )
                    if left != right:
                        raise ValueError(
                            "HKP has changes beyond the exact declared UI numeric fields"
                        )
                    row["ui_fields"] = {"before": old_fields, "after": new_fields}
                elif policy == "bmp_thumbnail":
                    left, right = _bmp(first_raw), _bmp(second_raw)
                    if left != right:
                        raise ValueError("BMP changed its fixed thumbnail format or header")
                    row["bmp"] = {"before": left, "after": right}
                elif policy == "save_log":
                    initial = declaration["initial_save_logs"][declared["path"]]
                    transition = "unchanged" if not row["changed"] else "bounded_native_save_output"
                    if phase in ("open", "continuation") and row["changed"]:
                        if (
                            phase != "open"
                            or second["pin"] != declared["input_pin"]
                            or second_raw != bytes.fromhex(initial["raw_hex"])
                        ):
                            raise ValueError(
                                "Open PCBLogFile must equal the exact actual initial raw log"
                            )
                        transition = "restored_declaration_initial"
                    row["log"] = {"before": _log(first_raw), "after": _log(second_raw)}
                    row["log"]["transition"] = transition
                    row["log"]["initial_input_pin"] = copy.deepcopy(initial["pin"])
                    row["_untrusted"] = ["log.before.text", "log.after.text"]
                elif policy == "autosave_log":
                    left, right = _autosave(first_raw), _autosave(second_raw)
                    transition = "unchanged"
                    if phase == "open" and second_raw == b"":
                        transition = (
                            "unchanged" if first_raw == b"" else "observed_empty_after_open"
                        )
                    elif row["changed"]:
                        if phase != "close":
                            raise ValueError(
                                "Save/continuation exact; Open permits only an exact empty reset"
                            )
                        if not second_raw.startswith(first_raw):
                            raise ValueError(
                                "Close autosave must retain the actual before bytes as a prefix"
                            )
                        if len(second_raw) - len(first_raw) > MAX_AUTOSAVE_TAIL_BYTES:
                            raise ValueError(
                                "Close autosave append exceeds its bounded tail budget"
                            )
                        added = right["entries"][len(left["entries"]) :]
                        if (
                            not added
                            or right["entries"][: len(left["entries"])] != left["entries"]
                            or any(
                                Path(entry["job_name"]) != Path(declaration["board"])
                                for entry in added
                            )
                        ):
                            raise ValueError(
                                "Every added autosave block must bind the actual board Job Name"
                            )
                        transition = "appended_actual_before"
                    row["log"] = {
                        "before": left,
                        "after": right,
                        "transition": transition,
                        "appended_bytes": max(0, len(second_raw) - len(first_raw)),
                        "before_bytes": len(first_raw),
                        "after_bytes": len(second_raw),
                        "source_history_sha256": declaration["autosave_source_prefix"]["sha256"],
                        "tool_reset_dispatched": False,
                    }
                    row["_untrusted"] = ["log.before.text", "log.after.text"]
            proof["files"].append(row)
        proof["checks"] = [
            {"name": name, "passed": True}
            for name in (
                "exact existing declared paths with complete raw input pins",
                "all nondeclared protected readable files unchanged",
                "each native DB mirror agrees before and after",
                "bounded UI fields, real BMP and original native log texts recomputed",
            )
        ]
        proof["declaration_sha256"] = declaration["fingerprint"]
        proof["valid"] = True
    except (
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        OverflowError,
        UnicodeError,
        LookupError,
    ) as error:
        proof["reason"] = str(error)
    return proof
