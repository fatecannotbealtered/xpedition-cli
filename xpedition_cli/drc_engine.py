"""Installed MGCPCBEngines DRC: explicit configurations and fresh native reports."""

from __future__ import annotations

import hashlib
import math
import os
import re
import stat
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from . import library_read as R
from .cell_refresh import digest

MAX_NATIVE_FILE_BYTES = 32 * 1024 * 1024


class ReportBindingError(ValueError):
    """Retain consumed report observations when selected configuration refuses."""

    def __init__(self, message, observation):
        super().__init__(message)
        self.observation = observation


def _plain_path(path):
    """Do not follow a report/configuration symlink or Windows junction."""
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError("Native DRC paths must not traverse reparse points")
    return path


@dataclass(frozen=True)
class _FileContents:
    path: str
    data: bytes
    mtime_ns: int
    file_identity: tuple[int, int]

    @property
    def record(self):
        # The identity is derived from exactly the immutable bytes consumed by
        # parsing, never from another opening of the source path.
        return {
            "path": self.path,
            "exists": True,
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "bytes": len(self.data),
            "mtime_ns": self.mtime_ns,
            "file_identity": {
                "device": str(self.file_identity[0]),
                "inode": str(self.file_identity[1]),
            },
        }


def _read_contents(path, *, reserve=None):
    path = _plain_path(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_NATIVE_FILE_BYTES:
            raise ValueError("Native DRC file is not regular or exceeds the byte limit")
        read_bound = before.st_size + 1
        if reserve is not None:
            reserve(read_bound)  # Charge an attempted read before it can fail.
        data = stream.read(read_bound)
        after = os.fstat(stream.fileno())
        named = _plain_path(path).stat()
    keys = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_dev", "st_ino")
    handle_changes = [k for k in keys if getattr(before, k) != getattr(after, k)]
    # CPython/Windows can expose different ctime semantics through fstat and
    # path stat (observed here: handle mtime versus path creation time). Compare
    # ctime only within the same handle API. File ID, size and mtime still bind
    # the named path exactly; no numeric timestamp tolerance is introduced.
    path_keys = ("st_size", "st_mtime_ns", "st_dev", "st_ino")
    path_changes = [k for k in path_keys if getattr(named, k) != getattr(after, k)]
    if (
        len(data) > MAX_NATIVE_FILE_BYTES
        or len(data) != after.st_size
        or handle_changes
        or path_changes
    ):
        raise ValueError(
            "Native report/configuration changed while capturing its bytes; handle fields="
            + repr(handle_changes)
            + "; path fields="
            + repr(path_changes)
        )
    return _FileContents(str(path), data, after.st_mtime_ns, (after.st_dev, after.st_ino))


def _selected_contents(row):
    snapshot = _read_contents(Path(row["path"]))
    if snapshot.record["sha256"] != row["sha256"] or snapshot.record["bytes"] != row["bytes"]:
        raise ValueError("Staged DRC input changed after selection")
    return snapshot


def file_record(path):
    path = _plain_path(path)
    if not path.exists():
        return {"path": str(path), "exists": False}
    return _read_contents(path).record


def configurations(params, board):
    result = {}
    for key, candidates, filetype in (
        ("rules", ["BatchDrc.dat", "(Final DRC).drc"], "BATCH_DRC_FILE"),
        ("elements", ["BatchDrcE2E.dat", "(Final DRC).e2e"], "BATCH_DRC_E2E_FILE"),
    ):
        path = (
            Path(params[key]).absolute()
            if params.get(key)
            else next(
                (
                    board.parent / "Config" / n
                    for n in candidates
                    if (board.parent / "Config" / n).is_file()
                ),
                board.parent / "Config" / candidates[0],
            )
        )
        captured = _read_contents(path)
        record = captured.record
        if not R.decode_text(captured.data).startswith(".FILETYPE " + filetype):
            raise ValueError(f"A supported explicit {key} configuration is required: {path}")
        result[key] = record
    return result


def outputs(board):
    return {
        key: board.parent / name
        for key, name in {
            "report": "LogFiles/Drc.txt",
            "violations": "Work/DrcViolations.dat",
            "effective_rules": "Config/BatchDrc.dat",
            "effective_elements": "Config/BatchDrcE2E.dat",
        }.items()
    }


def validate_output_paths(paths, board):
    """Bind every live output role to its expected location under this board."""
    expected = outputs(_plain_path(board))
    if set(paths) != set(expected):
        raise ValueError("Native DRC output roles do not match the bound board")
    for role, path in paths.items():
        if _plain_path(path) != _plain_path(expected[role]):
            raise ValueError("Native DRC output location differs from the bound board: " + role)


def planned_side_effects(board):
    """Name known 151.0 checker outputs without treating other changes as safe."""
    board = Path(board)
    known = outputs(board)
    rows = [
        ("report", known["report"], True),
        ("violations", known["violations"], True),
        ("effective_rules", known["effective_rules"], True),
        ("effective_elements", known["effective_elements"], True),
        ("staged_inputs", board.parent / "Work/xpedition-cli/native-drc", True),
        ("component_type_log", board.parent / "LogFiles/DrcCompTypeLog.txt", True),
        ("automation_log", board.parent / "Work/tempAutomationLog.txt", True),
        ("autosave_log", board.parent / "LogFiles/PCBAutoSave.txt", True),
        ("board_info_log", board.parent / "LogFiles/PCBInfoFile.txt", True),
        ("board_log", board.parent / "LogFiles/PCBLogFile.txt", True),
    ]
    return [
        {"role": role, "path": str(path), "effect": "artifact", "observe_bytes": observed}
        for role, path, observed in rows
    ]


# Observed, version-bound 151.0 Chinese report markers. Unknown profiles stay incomplete.
_RULE_MARKERS = {
    "PARTCOVERLAYER": "检查缺少的元件覆盖层焊盘",
    "VIACOVERLAYER": "检查缺少的通孔覆盖层焊盘",
    "UNPLACED_COMPONENTS": "检查未放置的元件",
    "TRACEHANGERS": "检查导线悬挂项目",
    "TRACELOOPS": "检查导线回路",
    "TRACEWIDTHS": "检查线宽",
    "SINGLEPOINTNETS": "检查单点网络",
    "UNROUTEDPARTIALNETS": "检查非平面未布线/部分布线的网络",
    "PLANENETS": "检查平面未布线/部分布线的网络",
    "PLANEISLANDS": "检查平面覆铜岛",
    "DANGLINGVIASJUMPERS": "检查悬空过孔/跳线",
    "UNROUTEDPINS": "检查未布线的管脚",
    "ROUTEDNONPLATEDPINS": "检查已布线的非电镀管脚",
    "VIASUNDERSMDPADS": "检查 SMD 焊盘下的过孔",
    "VIASUNDERTOPPLACEOUTLINES": "检查顶面放置边框下的过孔",
    "VIASUNDERBOTPLACEOUTLINES": "检查底面放置边框下的过孔",
}
_RULE_GROUPS = {
    "NETCLASSCLEARANCESANDRULES": "网络类安全间距和规则",
    "PLANESCLEARANCESANDRULES": "平面安全间距和规则",
    "NONNETCLASSELETOELERULES": "非网络类元素到元素安全间距和规则",
    "CONNECTANDSPECIALRULES": "连接性和特殊规则",
}


def _rule_execution(report, effective, selected, *, engine_version=None, selected_contents=None):
    supported = engine_version == "MGCPCBEngines.DRC.151"
    result = {
        "scope": "observed 151.0 Chinese report markers and explicitly listed comparison pairs",
        "profile": "151.0-zh" if supported else "unverified",
        "observations": [],
        "unverified_rules": [],
        "selected_rules_verified": bool(selected),
        "report_only_checks": [],
    }
    settings = (
        R.parse_records(
            R.decode_text(
                (selected_contents or {"rules": _selected_contents(selected["rules"])})[
                    "rules"
                ].data
            )
        )
        if selected
        else []
    )
    requested = {
        r.keyword: [x.values for x in settings if x.keyword == r.keyword] for r in settings
    }
    configured = {
        r["name"]: [x["values"] for x in effective if x["name"] == r["name"]]
        for r in effective
        if "." not in r["name"]
        and not r["name"].startswith(("DISABLE", "ENABLE"))
        and r["values"] in (["YES"], ["NO"])
    }
    names = sorted(
        set(configured)
        | {
            r.keyword
            for r in settings
            if r.values in (["YES"], ["NO"]) and not r.keyword.startswith(("DISABLE", "ENABLE"))
        }
    )
    for name in names:
        expected = configured.get(name, [[]])
        expected = expected[0][0] if len(expected) == 1 and len(expected[0]) == 1 else None
        ask = requested.get(name, [])
        requested_value = ask[0][0] if len(ask) == 1 and len(ask[0]) == 1 else None
        markers, comparisons = [], []
        if supported and name in _RULE_MARKERS:
            pattern = r"^\s*" + re.escape(_RULE_MARKERS[name]) + r"\s*:\s*(YES|NO)\s*$"
            markers = re.findall(pattern, report, re.M)
        if supported and name in _RULE_GROUPS:
            title = _RULE_GROUPS[name]
            headings = list(
                re.finditer(r"^\s*" + re.escape(title) + r"\s*\n\s*-+\s*\n", report, re.M)
            )
            if len(headings) == 1:
                tail = report[headings[0].end() :]
                boundaries = [
                    m.start()
                    for t in _RULE_GROUPS.values()
                    for m in re.finditer(r"^\s*" + re.escape(t) + r"\s*\n\s*-+", tail, re.M)
                ]
                section = tail[: min(boundaries)] if boundaries else tail
                comparisons = re.findall(r"^\s*(\S[^\r\n]* TO [^\r\n]+)\s*$", section, re.M)
                if "未执行接近度检查" in section:
                    markers = ["NO"]
                elif comparisons or (
                    name == "CONNECTANDSPECIALRULES" and re.search(r"检查[^\n]+:\s*YES", section)
                ):
                    markers = ["YES"]
        actual = markers[0] if len(markers) == 1 else None
        consistent = actual in {"YES", "NO"} and actual == expected
        execution = (
            "performed" if actual == "YES" else "not_performed" if actual == "NO" else "unverified"
        )
        reason = (
            (
                "Native report explicitly describes execution; object population "
                "coverage is not established"
            )
            if actual == "YES"
            else "Native nonexecution does not establish nonapplicability"
            if actual == "NO"
            else (
                "Missing, ambiguous, unsupported-version/language or uncorrelated native "
                "execution evidence"
            )
        )
        row = {
            "rule": name,
            "kind": "group" if name in _RULE_GROUPS else "rule",
            "configured": expected,
            "reported": actual,
            "report_markers": markers,
            "explicitly_selected": bool(ask),
            "status": "consistent" if consistent else "unverified",
            "requested": {"value": requested_value, "input_ref": (selected or {}).get("rules")},
            "effective": {"value": expected},
            "execution": {
                "status": execution,
                "comparisons": comparisons,
                "source": "Drc.txt content; invocation freshness is verified separately",
            },
            "applicability": {"status": "unverified", "reason": reason, "evidence_refs": []},
            "source": "native report content and selected/effective files",
        }
        result["observations"].append(row)
        if not consistent:
            result["unverified_rules"].append(name)
        if ask and (
            requested_value == "YES"
            and (not consistent or actual != "YES")
            or requested_value == "NO"
            and (expected != "NO" or actual == "YES")
            or requested_value not in {"YES", "NO"}
        ):
            result["selected_rules_verified"] = False
    if supported:
        for label in ("检查缺失的元件", "检查 EP 元器件风险", "检查已布线的平面管脚"):
            markers = re.findall(
                r"^\s*" + re.escape(label) + r"\s*:\s*(YES|NO|是|否)\s*$", report, re.M
            )
            if markers:
                result["report_only_checks"].append(
                    {
                        "label": label,
                        "markers": markers,
                        "configuration_key": None,
                        "applicability": "unverified",
                        "reason": "Report-only extra; no proven mapping to a selected setting",
                    }
                )
    return result


def read_reports(
    paths,
    before,
    started_ns,
    *,
    setup_started_ns=None,
    selected=None,
    layer_count=None,
    engine_version=None,
    expected_board=None,
):
    if expected_board is not None:
        validate_output_paths(paths, expected_board)
    contents = {key: _read_contents(p) for key, p in paths.items()}
    records = {key: value.record for key, value in contents.items()}
    # Identical bytes may be a valid repeat; each native result must nevertheless
    # have a fresh write timestamp inside this invocation. A stale zero is invalid.
    for key in ("report", "violations", "effective_rules", "effective_elements"):
        row = records[key]
        lower_bound = (
            (setup_started_ns or started_ns) if key.startswith("effective_") else started_ns
        )
        if not row.get("bytes") or row["mtime_ns"] < lower_bound or row == before.get(key):
            raise ValueError("No fresh native " + key)
    binding_failure = None
    try:
        parsed = _parse_report_contents(
            contents,
            selected=selected,
            selected_snapshots={
                key: _selected_contents(selected[key]) for key in ("rules", "elements")
            }
            if selected
            else None,
            layer_count=layer_count,
            engine_version=engine_version,
        )
    except ReportBindingError as error:
        binding_failure = error
        raise
    finally:
        # A parse/configuration refusal must not bypass the report-set check.
        # These observations remain unqualified even when their files are stable.
        checking_role = None
        try:
            if expected_board is not None:
                validate_output_paths(paths, expected_board)
            for key, snapshot in contents.items():
                checking_role = key
                if _read_contents(paths[key]).record != snapshot.record:
                    raise ValueError("Native DRC output changed during report-set capture: " + key)
        except (OSError, ValueError) as error:
            if binding_failure is not None:
                binding_failure.observation.update(
                    output_set_stability="failed",
                    configuration_error={
                        "type": type(binding_failure).__name__,
                        "message": str(binding_failure),
                    },
                    output_set_stability_error={
                        "type": type(error).__name__,
                        "message": str(error),
                        "role": checking_role,
                    },
                )
                raise ReportBindingError(
                    str(binding_failure) + "; final report-set check failed: " + str(error),
                    binding_failure.observation,
                ) from error
            raise
        if binding_failure is not None:
            binding_failure.observation.update(
                content_freshness="current_invocation_files_verified",
                output_set_stability="verified_after_parse_refusal",
            )
    return {**parsed, "content_freshness": "current_invocation_files_verified"}


def _violation_source(text):
    """Strict line structure plus exact decoded top-level blocks, not live evidence."""
    blocks, current, previous_level = [], None, 0
    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        line = R._strip_comment(raw).strip()
        if not line:
            if current is not None:
                current["raw_text"] += raw
            continue
        match = re.fullmatch(r"(\.+)([A-Za-z0-9_]+)(.*)", line)
        if match is None:
            raise ValueError(f"Damaged native violation line: {number}")
        level, values = len(match[1]), match[3]
        if (
            level > previous_level + 1
            or level > 32
            or values.count('"') % 2
            or values.count("(") != values.count(")")
        ):
            raise ValueError(f"Damaged native violation structure: {number}")
        if level == 1:
            current = {"line": number, "raw_text": "", "keyword": match[2]}
            blocks.append(current)
        if current is None:
            raise ValueError(f"Damaged native violation root: {number}")
        current["raw_text"] += raw
        previous_level = level
    return blocks


def _record_tree(record):
    def json_value(value):
        if isinstance(value, (list, tuple)):
            return [json_value(item) for item in value]
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nonfinite native record value")
        return value

    return {
        "keyword": record.keyword,
        "values": json_value(record.values),
        "children": [_record_tree(child) for child in record.children],
    }


def parse_reports(paths, *, selected=None, layer_count=None, engine_version=None):
    """Parse retained content only. Callers cannot infer current execution/freshness.

    Native execution MUST call read_reports, which keeps its timestamp/prior-file
    gates before entering here. The public historical reader separately verifies
    its manifest bytes and explicitly does not establish native completion.
    """
    return _parse_report_contents(
        {key: _read_contents(path) for key, path in paths.items()},
        selected=selected,
        selected_snapshots={key: _selected_contents(selected[key]) for key in ("rules", "elements")}
        if selected
        else None,
        layer_count=layer_count,
        engine_version=engine_version,
    )


def _parse_report_contents(
    contents, *, selected=None, selected_snapshots=None, layer_count=None, engine_version=None
):
    """Consume captured bytes only; this function never reopens any source path."""
    records = {key: value.record for key, value in contents.items()}
    if selected and selected_snapshots is None:
        raise ValueError("Captured selected configuration bytes are required")
    raw_report = contents["report"].data
    try:
        report, report_encoding = raw_report.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        # The retained151.0 Chinese original is CP936. Decode this observed
        # profile strictly, including when inspecting the same bytes off Windows.
        report, report_encoding = raw_report.decode("cp936"), "cp936"
    if "\ufffd" in report:
        raise ValueError("Damaged native report text encoding")
    totals = re.findall(r"DRC[^\r\n]*:\s*(\d+)\s*$", report, re.M)
    if len(totals) != 1 or not re.search(r"^\s*DRC\s*\r?\n\s*===", report):
        raise ValueError("Incomplete or unsupported native DRC report")
    violation_text = R.decode_text(contents["violations"].data)
    if "\ufffd" in violation_text:
        raise ValueError("Damaged native violation text encoding")
    blocks = _violation_source(violation_text)
    native = R.parse_records(violation_text)
    header_names = {"FILETYPE", "VERSION", "DATE", "TIME"}
    if any(sum(r.keyword == key for r in native) != 1 for key in header_names):
        raise ValueError("Duplicate or missing native violation report header")
    headers = {r.keyword: r.values for r in native if r.keyword in header_names}
    hazards = [r for r in native if r.keyword not in headers]
    if headers.get("FILETYPE") != ["DRCFILE"] or not all(
        len(headers[k]) == 1 and isinstance(headers[k][0], str) and headers[k][0]
        for k in ("VERSION", "DATE", "TIME")
    ):
        raise ValueError("Invalid native violation report header")
    if headers["VERSION"] != ["2002"]:
        raise ValueError("Unsupported native violation coordinate format")
    if len(hazards) != int(totals[0]):
        raise ValueError("Native report total and violation record count disagree")
    parsed_violations = [violation_record(record) for record in hazards]
    hazard_blocks = [row for row in blocks if row["keyword"] not in header_names]
    for record, parsed, block in zip(hazards, parsed_violations, hazard_blocks, strict=True):
        if not _supported_violation(record, parsed):
            raise ValueError("Unsupported or incomplete native violation record: " + record.keyword)
        parsed.update(
            source_line=block["line"],
            raw_record=block["raw_text"],
            record_tree=_record_tree(record),
        )
    settings = R.parse_records(R.decode_text(contents["effective_rules"].data))
    if not settings or settings[0].values != ["BATCH_DRC_FILE"]:
        raise ValueError("Effective rule configuration is missing or damaged")
    elements = R.parse_records(R.decode_text(contents["effective_elements"].data))
    if not elements or elements[0].values != ["BATCH_DRC_E2E_FILE"]:
        raise ValueError("Effective element rules are missing or damaged")

    def flatten(nodes, prefix=""):
        rows = []
        for r in nodes:
            name = prefix + r.keyword
            rows.append({"name": name, "values": r.values})
            qualifier = "[" + ",".join(str(v) for v in r.values) + "]" if r.values else ""
            rows += flatten(r.children, name + qualifier + ".")
        return rows

    effective = flatten(settings)
    switches = [
        r
        for r in effective
        if "." not in r["name"] and not r["name"].startswith(("DISABLE", "ENABLE"))
    ]
    enabled = [r["name"] for r in switches if r["values"] == ["YES"]]
    excluded = [r["name"] for r in switches if r["values"] == ["NO"]]
    execution = _rule_execution(
        report,
        effective,
        selected,
        engine_version=engine_version,
        selected_contents=selected_snapshots,
    )
    execution["sources"] = {
        "report": records["report"],
        "effective_rules": records["effective_rules"],
        "selected_rules": (selected or {}).get("rules"),
    }
    enabled = [r for r in enabled if r not in execution["unverified_rules"]]
    excluded = [r for r in excluded if r not in execution["unverified_rules"]]
    annular = annular_unit_observation(
        report,
        R.parse_records(R.decode_text(selected_snapshots["rules"].data))
        if selected_snapshots
        else [],
        settings,
        engine_version=engine_version,
    )
    try:
        binding = (
            _configuration_binding_contents(selected, contents, layer_count, selected_snapshots)
            if selected
            else {"verified": False, "reason": "Selected input not supplied"}
        )
    except ValueError as error:
        raise ReportBindingError(
            str(error),
            {
                "files": records,
                "annular_unit_binding": annular,
                "parsed_violation_count": len(hazards),
                "execution_qualified": False,
            },
        ) from error
    return {
        "files": records,
        "count": len(hazards),
        "record_counts": dict(Counter(r.keyword for r in hazards)),
        "kinds": [r.keyword for r in hazards],
        "violations": parsed_violations,
        "reported_header": headers,
        "raw_report": report,
        "report_text_encoding": report_encoding,
        "parsed_content_complete": True,
        "content_freshness": "not_evaluated_by_parser",
        "rules_checked": enabled,
        "excluded_rules": excluded,
        "rule_execution_verification": execution,
        "effective_settings": effective,
        "effective_elements": flatten(elements),
        "switch_interpretation": (
            "Root positive switches only; negative switches and structural layer properties "
            "remain raw, not inferred as ignored rules. Every claimed rule/group requires "
            "fresh supported native report execution evidence. Missing, contradictory, "
            "unknown-version/language and engine-added switches without correlation remain "
            "unverified. NO is nonexecution, not proven nonapplicability. Group comparisons "
            "cover only the report's listed pairs; see rule_execution_verification."
        ),
        "rule_config_sha256": digest(
            {k: records[k]["sha256"] for k in ("effective_rules", "effective_elements")}
        ),
        "configuration_binding": binding,
        "annular_unit_binding": annular,
    }


def annular_unit_observation(report, selected_nodes, effective_nodes, *, engine_version=None):
    """Keep file scalars separate from explicitly labelled report distances.

    BASE_UNITS does not by itself prove MINIMUMANNULARRINGVALUE serialization.
    In particular,124460/4.9 must not be accepted as an inferred nm/mil pair.
    """
    fields = ("VERSION", "BASE_UNITS", "MINIMUMANNULARRING", "MINIMUMANNULARRINGVALUE")

    def raw(nodes):
        return {name: [list(n.values) for n in nodes if n.keyword == name] for name in fields}

    display = []
    if engine_version == "MGCPCBEngines.DRC.151":
        for match in re.finditer(r"^\s*环形距离\s*:\s*(.*?)\s*$", report, re.M):
            token = match[1]
            parsed = re.fullmatch(r"([+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(th|mm)", token)
            row = {"raw": token, "status": "unverified"}
            if parsed:
                value, unit = parsed.groups()
                factor = Fraction(127, 5000) if unit == "th" else Fraction(1)
                mm = Fraction(value) * factor
                row.update(
                    status="labelled_report_value_only",
                    value=value,
                    unit=unit,
                    value_mm_fraction=[mm.numerator, mm.denominator],
                )
            display.append(row)
    return {
        "status": "unverified",
        "selected_raw_fields": raw(selected_nodes),
        "effective_raw_fields": raw(effective_nodes),
        "display_observations": display,
        "display_field_binding": "recognized_label"
        if len(display) == 1
        else "missing_or_ambiguous_label",
        "literal_question_mark_runs": len(re.findall(r"\?{3,}", report)),
        "native_file_value_unit": "unverified",
        "file_to_display_binding_verified": False,
        "can_authorize_rule_write": False,
        "reason": "No authenticated version-bound annular file serialization and display binding",
        "boundary": "Raw equality binds selected bytes only; labelled report values "
        "do not prove file units",
    }


def _xy(record):
    if record is None:
        return None
    values = record.values
    # DRCFILE version 2002 in the observed 151 engine: integer nanometres.
    # Calibration: J1's frozen 2.54 mm pitch is 2540000 in these records.
    coordinates = values[0] if len(values) == 1 and isinstance(values[0], tuple) else None
    if (
        coordinates is None
        or len(coordinates) != 2
        or any(type(v) not in (int, float) for v in coordinates)
    ):
        return None
    result = [float(v) / 1_000_000 for v in coordinates]
    return result if result is not None and all(math.isfinite(v) for v in result) else None


def violation_record(record):
    objects = []
    groups = []
    for entry in record.children:
        if entry.keyword == "OBJECT":
            groups.append((entry.keyword, entry))
        elif entry.keyword in {"OBJECT_1", "OBJECT_2"}:
            groups.extend(
                (entry.keyword, group) for group in entry.children if group.keyword == "OBJECT"
            )
    for group_name, group in groups:
        for obj in group.children:
            fields = {r.keyword: r.values for r in obj.children}
            objects.append(
                {
                    "kind": obj.keyword,
                    "qualifier": obj.values,
                    "refdes": (fields.get("REF_DES") or [None])[0],
                    "net": (fields.get("NET") or [None])[0],
                    "name": (fields.get("NAME") or [None])[0],
                    "layer": (fields.get("LYR") or [None])[0],
                    "net_class": (fields.get("NETCLASS") or [None])[0],
                    "position_mm": _xy(obj.first("XY")),
                    "group": group_name,
                    "side": (fields.get("SIDE") or [None])[0],
                }
            )
    position = record.first("VIO_XY") if record.keyword == "PROXIMITY" else record.first("XY")
    result = {"kind": record.keyword, "position_mm": _xy(position), "objects": objects}
    if record.keyword == "PROXIMITY":
        clearance = record.first("CLEARANCE")
        result["native_clearance"] = clearance.values if clearance is not None else None
    return result


def _supported_violation(record, parsed):
    critical = {"XY", "REF_DES", "NET", "NAME", "LYR", "SIDE", "NETCLASS"}
    for entry in record.children:
        groups = (
            [entry]
            if entry.keyword == "OBJECT"
            else [
                group
                for group in entry.children
                if entry.keyword in {"OBJECT_1", "OBJECT_2"} and group.keyword == "OBJECT"
            ]
        )
        for group in groups:
            for obj in group.children:
                counts = Counter(row.keyword for row in obj.children if row.keyword in critical)
                if any(count > 1 for count in counts.values()):
                    return False
    if parsed["position_mm"] is None or not parsed["objects"]:
        return False
    if any(obj["position_mm"] is None or not obj["refdes"] for obj in parsed["objects"]):
        return False
    if record.keyword in {"UNROUTED_NET", "PARTIAL_NET"}:
        supported = (
            len([r for r in record.children if r.keyword == "XY"]) == 1
            and len([r for r in record.children if r.keyword == "OBJECT"]) == 1
            and all(
                not r.keyword.startswith("OBJECT") or r.keyword == "OBJECT" for r in record.children
            )
            and all(obj["net"] for obj in parsed["objects"])
        )
        if record.keyword == "PARTIAL_NET":
            # Observed151.0 DRCFILE2002 PAD record only. Its type is not an
            # opens count or a formally established native hazard mapping.
            if not supported:
                return False
            obj = record.first("OBJECT").children
            fields = obj[0].children if len(obj) == 1 else []
            required = {"LYR", "NAME", "NET", "NETCLASS", "REF_DES", "XY"}
            return (
                supported
                and len(obj) == 1
                and all(
                    len([r for r in fields if r.keyword == key and len(r.values) == 1]) == 1
                    for key in required
                )
                and len(parsed["objects"]) == 1
                and parsed["objects"][0]["kind"] == "PAD"
                and parsed["objects"][0]["qualifier"] == ["PART"]
                and all(
                    isinstance(parsed["objects"][0][key], str) and parsed["objects"][0][key]
                    for key in ("refdes", "net", "name", "net_class")
                )
                and type(parsed["objects"][0]["layer"]) in (int, float)
                and parsed["objects"][0]["layer"] >= 1
                and math.isfinite(parsed["objects"][0]["layer"])
                and float(parsed["objects"][0]["layer"]).is_integer()
            )
        return supported
    if record.keyword == "PROXIMITY":
        groups = [r for r in record.children if r.keyword in {"OBJECT_1", "OBJECT_2"}]
        clearance = [r for r in record.children if r.keyword == "CLEARANCE"]
        return (
            len([r for r in record.children if r.keyword == "VIO_XY"]) == 1
            and len(clearance) == 1
            and len(clearance[0].values) == 1
            and type(clearance[0].values[0]) in (int, float)
            and math.isfinite(clearance[0].values[0])
            and len(groups) == 2
            and {r.keyword for r in groups} == {"OBJECT_1", "OBJECT_2"}
            and all(
                not r.keyword.startswith("OBJECT") or r.keyword in {"OBJECT_1", "OBJECT_2"}
                for r in record.children
            )
            and all(len([v for v in r.children if v.keyword == "OBJECT"]) == 1 for r in groups)
            and len(parsed["objects"]) == 2
            and {obj["group"] for obj in parsed["objects"]} == {"OBJECT_1", "OBJECT_2"}
        )
    return False


def compare_hazards(records, observed, tolerance_mm=0.001):
    """One-to-one comparison; a matching count never establishes matching content."""
    kinds = {"UNROUTED_NET": "PartialNets"}
    unused = list(range(len(observed)))
    matches, missing = [], []
    not_verified = {
        "PARTIAL_NET_mapping": (
            "PARTIAL_NET report parsing does not establish its formal native hazard mapping; "
            "these records remain unsupported_type and do not represent an opens count"
        ),
        "pin_number": "Violation file does not expose pin number",
        "pad_name_and_layer": "Hazard object getters do not always expose these fields",
        "proximity_CLEARANCE_semantics": (
            "Raw report CLEARANCE is retained; it is not assumed to mean either "
            "native ActualClearance or RequiredClearance"
        ),
    }

    def position(a, b):
        try:
            return (
                a is not None
                and b is not None
                and len(a) == len(b) == 2
                and all(abs(float(x) - float(y)) <= tolerance_mm for x, y in zip(a, b, strict=True))
            )
        except (TypeError, ValueError):
            return False

    def finite_nonnegative(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    def placement_corner(detail):
        shape = detail.get("placement_geometry_observation") or {}
        raw = shape.get("points_array_mm")
        if (
            shape.get("source") != "IMGCPCBPlacementOutline151.Geometry.PointsArray"
            or shape.get("unit") != "mm"
            or shape.get("complete") is not True
            or shape.get("errors") != []
            or shape.get("position_getter_supported") is not False
            or type(shape.get("cutout_count")) is not int
            or shape["cutout_count"] != 0
            or not isinstance(raw, (list, tuple))
            or len(raw) != 3
            or any(not isinstance(axis, (list, tuple)) or len(axis) != 5 for axis in raw)
            or any(
                type(v) not in (int, float) or not math.isfinite(v) for axis in raw for v in axis
            )
        ):
            return None
        vertices = list(zip(raw[0], raw[1], strict=True))
        xs, ys = sorted(set(raw[0])), sorted(set(raw[1]))
        if (
            any(raw[2])
            or len(xs) != 2
            or len(ys) != 2
            or vertices[0] != vertices[-1]
            or set(vertices) != {(x, y) for x in xs for y in ys}
            or any(
                (left[0] == right[0]) == (left[1] == right[1])
                for left, right in zip(vertices, vertices[1:], strict=False)
            )
        ):
            return None
        bounds = [xs[0], ys[0], xs[1], ys[1]]
        if (
            shape.get("rectangle_bounds_mm") != bounds
            or shape.get("rectangle_min_corner_mm") != bounds[:2]
        ):
            return None
        extrema = shape.get("native_extrema_mm")
        if (
            not isinstance(extrema, (list, tuple))
            or len(extrema) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in extrema)
            or not (
                extrema[0] <= bounds[0] < bounds[2] <= extrema[2]
                and extrema[1] <= bounds[1] < bounds[3] <= extrema[3]
            )
        ):
            return None
        return bounds[:2]

    def pin_layer_material(detail, pad):
        material = detail.get("layer_material_observation") or {}
        if (
            material.get("source") != "native_pin_owned_pad_material_mask151"
            or material.get("unit") != "mm"
            or material.get("complete") is not True
            or material.get("unread") != []
            or type(material.get("layer")) is not int
            or material["layer"] != pad["layer"]
            or not isinstance(material.get("pads"), list)
            or not 1 <= len(material["pads"]) <= 32
        ):
            return False
        return any(
            row.get("name") == pad.get("name")
            and row.get("layer") == pad["layer"]
            and row.get("geometry")
            and all(g.get("filled") is True for g in row["geometry"])
            for row in material["pads"]
        )

    def proximity_pair(row, hazard):
        """Finite151.0 pin-to-own top placement-outline correspondence only."""
        if hazard.get("kind") != "Proximity" or not position(
            row.get("position_mm"), [hazard.get("x"), hazard.get("y")]
        ):
            return None
        objects = row.get("objects", [])
        pads = [o for o in objects if o.get("kind") == "PAD"]
        outlines = [o for o in objects if o.get("kind") == "OUTLINE_OBJ"]
        if len(objects) != 2 or len(pads) != 1 or len(outlines) != 1:
            return None
        pad, outline = pads[0], outlines[0]
        if (
            pad.get("qualifier") not in (["PART"], ["SMD"])
            or outline.get("qualifier") != ["PLACE"]
            or outline.get("side") != "TOP"
            or pad.get("layer") != 1
            or not pad.get("net")
            or not pad.get("refdes")
            or pad["refdes"] != outline.get("refdes")
        ):
            return None
        native = hazard.get("proximity_observation") or {}
        if (
            native.get("source") != "IMGCPCBHazard151.propertyget"
            or native.get("unit") != "mm"
            or native.get("complete") is not True
            or native.get("errors") != []
            or not finite_nonnegative(native.get("actual_clearance_mm"))
            or not finite_nonnegative(native.get("required_clearance_mm"))
        ):
            return None
        first, second = (
            native.get("first_object_position_mm"),
            native.get("second_object_position_mm"),
        )
        if any(
            not isinstance(point, (list, tuple))
            or len(point) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)
            for point in (first, second)
        ):
            return None
        orders = [
            order
            for order in ((0, 1), (1, 0))
            if position(objects[order[0]].get("position_mm"), first)
            and position(objects[order[1]].get("position_mm"), second)
        ]
        if len(orders) != 1:
            return None
        details = hazard.get("object_details", [])
        if hazard.get("object_collection_complete") is not True or len(details) != 2:
            return None
        pairs = []
        for p in details:
            if (
                p.get("type_name") != "IMGCPCBPin"
                or p.get("refdes") != pad["refdes"]
                or p.get("net") != pad["net"]
                or not position(p.get("position_mm"), pad["position_mm"])
                or p.get("position_source") != "IMGCPCBPin151.PositionX/PositionY(EPcbUnitMM4)"
                or type(p.get("is_smd")) is not bool
                or (p["is_smd"] and p.get("layer") != pad["layer"])
                or (not p["is_smd"] and p.get("layer") is not None)
                or not pin_layer_material(p, pad)
            ):
                continue
            for o in details:
                if (
                    o is p
                    or o.get("type_name") != "IMGCPCBPlacementOutline"
                    or o.get("refdes") != outline["refdes"]
                    or o.get("net") is not None
                    or type(o.get("layer")) not in (int, float)
                    or o.get("layer") != 1
                    or o.get("position_mm") is not None
                    or not position(placement_corner(o), outline["position_mm"])
                ):
                    continue
                identities = [p.get("uid"), o.get("uid"), p.get("component_uid")]
                if (
                    any(not isinstance(v, str) or v in {"", "0"} for v in identities)
                    or p["uid"] == o["uid"]
                    or p["component_uid"] != o.get("component_uid")
                ):
                    continue
                pairs.append((p, o))
        if len(pairs) != 1:
            return None
        p, o = pairs[0]
        return {
            "scope": "151.0 Layer1 pin to its own Top placement outline",
            "formal_position_order": list(orders[0]),
            "pin_uid": p["uid"],
            "placement_outline_uid": o["uid"],
            "component_uid": p["component_uid"],
            "actual_clearance_mm": native["actual_clearance_mm"],
            "required_clearance_mm": native["required_clearance_mm"],
            "native_CLEARANCE_values": row.get("native_clearance"),
            "CLEARANCE_semantics_verified": False,
            "placement_location_basis": (
                "native rectangle minimum corner; no Outline Position getter"
            ),
            "pad_layer_basis": "actual Pin.Pads(layer1,includeNegative=True) material observation",
        }

    for index, row in enumerate(records):
        supported = row["kind"] in kinds or row["kind"] == "PROXIMITY"
        candidates = []
        proximity_witnesses = {}
        for i in unused:
            hazard = observed[i]
            if row["kind"] == "PROXIMITY":
                witness = proximity_pair(row, hazard)
                if witness is not None:
                    candidates.append(i)
                    proximity_witnesses[i] = witness
                continue
            if not supported or hazard.get("kind") != kinds[row["kind"]]:
                continue
            if not position(row["position_mm"], [hazard.get("x"), hazard.get("y")]):
                continue
            expected = Counter((o["refdes"], o["net"]) for o in row["objects"])
            actual = Counter(
                (o.get("refdes"), o.get("net")) for o in hazard.get("object_details", [])
            )
            if not expected or any(None in pair for pair in expected) or expected != actual:
                continue
            # Object positions distinguish equal-count hazards on different pads.
            remaining = list(hazard["object_details"])
            for obj in row["objects"]:
                found = next(
                    (
                        n
                        for n, val in enumerate(remaining)
                        if val.get("refdes") == obj["refdes"]
                        and val.get("net") == obj["net"]
                        and position(obj["position_mm"], val.get("position_mm"))
                    ),
                    None,
                )
                if found is None:
                    break
                remaining.pop(found)
            else:
                candidates.append(i)
        if candidates:
            chosen = candidates[0]
            unused.remove(chosen)
            matches.append({"record": index, "hazard": chosen})
            if chosen in proximity_witnesses:
                matches[-1]["proximity_correspondence"] = proximity_witnesses[chosen]
        else:
            missing.append(
                {
                    "record": index,
                    "reason": "unsupported_type" if not supported else "content_mismatch",
                }
            )
    return {
        "valid": not missing and not unused,
        "scope": (
            "Report/native hazard identity and position correspondence; finite151.0 "
            "Layer1 pin-to-own-Top-placement pairs only for PROXIMITY. "
            "Report CLEARANCE semantics and rule applicability remain unverified."
        ),
        "matches": matches,
        "unmatched_records": missing,
        "unmatched_hazards": unused,
        "tolerance_mm": tolerance_mm,
        "compared": [
            "mapped_type",
            "hazard_position",
            "object_refdes",
            "object_net",
            "object_position",
        ]
        + (
            ["formal_proximity_pair_positions", "native_object_type_layer_uid_owner"]
            if any("proximity_correspondence" in match for match in matches)
            else []
        ),
        "not_verified": not_verified,
        "coordinate_basis": "observed DRCFILE 2002 nanometres / native mm getters",
    }


def configuration_binding(selected, paths, layer_count):
    """Verify selected controls/matrix entries, retaining native defaults and exclusions."""
    contents = {
        kind: _read_contents(paths[kind]) for kind in ("effective_rules", "effective_elements")
    }
    selected_snapshots = {key: _selected_contents(selected[key]) for key in ("rules", "elements")}
    return _configuration_binding_contents(selected, contents, layer_count, selected_snapshots)


def _configuration_binding_contents(selected, contents, layer_count, selected_snapshots):

    def annular_scalar_contract(nodes, label):
        # The installed 151.0 descriptions expose MinAnnularRing(eUnit) PUT,
        # but do not define this file field's engine serialization. Preserve raw
        # equality; do not infer nm/mil normalization from124460 versus4.9.
        fields = {}
        for node in nodes:
            fields.setdefault(node.keyword, []).append(node.values)
        if not any(value == ["YES"] for value in fields.get("MINIMUMANNULARRING", [])):
            return
        for name in ("MINIMUMANNULARRING", "MINIMUMANNULARRINGVALUE", "BASE_UNITS", "VERSION"):
            values = fields.get(name, [])
            if len(values) != 1 or len(values[0]) != 1:
                raise ValueError(f"Ambiguous or missing {label} active annular field: {name}")
        try:
            value = float(fields["MINIMUMANNULARRINGVALUE"][0][0])
            unit = float(fields["BASE_UNITS"][0][0])
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"Invalid {label} active annular value/unit") from error
        if (
            not math.isfinite(value)
            or value < 0
            or not math.isfinite(unit)
            or not unit.is_integer()
        ):
            raise ValueError(f"Invalid {label} active annular value/unit")

    def categories(nodes):
        result = {}
        for node in nodes:
            if node.keyword != "PROGRAMDEFINEDMATRIXENTRY":
                continue
            if len(node.values) != 1 or node.values[0] in result:
                raise ValueError("Ambiguous effective/selected element category identity")
            result[node.values[0]] = [
                list(row.values) for row in node.children if row.keyword == "PDME"
            ]
        return result

    def flatten(nodes, prefix=()):
        result = {}
        for node in nodes:
            if node.keyword == "CREATOR":
                continue
            values = tuple(str(v) for v in node.values)
            key = (*prefix, node.keyword)
            result.setdefault(key, []).append(values)
            result.update(flatten(node.children, (*prefix, node.keyword + repr(values))))
        return result

    comparisons = {}
    for kind in ("rules", "elements"):
        consumed_selected_sha = selected_snapshots[kind].record["sha256"]
        if consumed_selected_sha != selected[kind]["sha256"]:
            raise ValueError("Captured DRC input does not match selected bytes")
        original_nodes = R.parse_records(R.decode_text(selected_snapshots[kind].data))
        effective_nodes = R.parse_records(R.decode_text(contents["effective_" + kind].data))
        if kind == "rules":
            annular_scalar_contract(original_nodes, "selected")
            annular_scalar_contract(effective_nodes, "effective")
        original_categories, effective_categories = (
            categories(original_nodes),
            categories(effective_nodes),
        )
        original, effective = flatten(original_nodes), flatten(effective_nodes)
        omitted, unverified, differences = [], [], []
        for path, values in original.items():
            if path == ("PROGRAMDEFINEDMATRIXENTRY",):
                # Categories are named containers, not an ordered scalar setting.
                # New native categories are disclosed below. Every selected child
                # still has to match under its original category and pair identity.
                if Counter(values) - Counter(effective.get(path, [])):
                    differences.append(
                        {"field": list(path), "selected": values, "effective": effective.get(path)}
                    )
                continue
            layer = re.search(r"(?:NNCLAYER|MPCLAYER)\('([\d.]+)'", ".".join(path))
            if layer_count and layer and float(layer[1]) > layer_count:
                omitted.append(list(path))
                continue
            if path == ("MINIMUMANNULARRINGVALUE",) and original.get(("MINIMUMANNULARRING",)) == [
                ("NO",)
            ]:
                if effective.get(path) != values:
                    unverified.append(
                        {
                            "field": list(path),
                            "reason": "inactive numeric serialization differs",
                            "selected": [list(value) for value in values],
                            "effective": [list(value) for value in effective[path]]
                            if path in effective
                            else None,
                        }
                    )
                continue
            # Parent layer lists are restricted to the actual board's layer count.
            if layer_count and path[-1] in {"NNCLAYER", "MPCLAYER"}:
                values = [v for v in values if float(v[0]) <= layer_count]
            if Counter(values) != Counter(effective.get(path, [])):
                differences.append(
                    {"field": list(path), "selected": values, "effective": effective.get(path)}
                )
        if differences:
            raise ValueError(
                "Effective " + kind + " do not match selected settings: " + repr(differences[:3])
            )
        comparisons[kind] = {
            "selected_sha256": consumed_selected_sha,
            "effective_sha256": contents["effective_" + kind].record["sha256"],
            "verified_selected_fields": len(original) - len(omitted) - len(unverified),
            "outside_board_layers": omitted,
            "not_verified": unverified,
            "engine_added_fields": [list(p) for p in effective if p not in original],
            "engine_added_categories": [
                {"name": name, "entries": entries}
                for name, entries in sorted(effective_categories.items())
                if name not in original_categories
            ],
        }
    return {
        "verified": True,
        "scope": "selected controls and matrix entries for actual board layers",
        "numeric_unit_qualification": "not_established_by_raw_setting_equality",
        "comparisons": comparisons,
    }


def registered_engine():
    import winreg

    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"MGCPCBEngines.DRC\CurVer") as key:
        progid = winreg.QueryValueEx(key, "")[0]
    if not re.fullmatch(r"MGCPCBEngines\.DRC\.\d+", progid):
        raise ValueError("The registered versioned DRC engine is unavailable")
    return progid


def run(params, client, a):
    """Save/close the identified board, run once, leave reopen to a fresh worker."""
    from .cell_refresh import capture
    from .native_identity import application_identity

    clock_started = time.monotonic()
    project = Path(params["project"]).resolve()
    board = a._layout_board_path(params)
    app = a._application(client, attach_only=True)
    doc = a._licensed_document(app)
    if Path(str(doc.FullName)).resolve() != board.resolve():
        raise a.AdapterError(
            "E_CONFLICT", "The requested board must already be open", {"write_attempted": False}
        )
    try:
        config = configurations(params, board)
        progid = registered_engine()
        validate_output_paths(outputs(board), board)
    except (OSError, ValueError) as error:
        raise a.AdapterError("E_VALIDATION", str(error), {"write_attempted": False}) from error
    geometry = capture(doc, a)
    identity = application_identity(app, "pcb")
    paths = outputs(board)
    before = {k: file_record(p) for k, p in paths.items()}
    result = {
        "pcb": str(board),
        "ran": False,
        "completed": False,
        "passes": False,
        "clean": False,
        "change_status": "not_applied",
        "persistence_state": "unchanged",
        "geometry_before": geometry,
        "input_configurations": config,
        "source": {
            "kind": "native_checker",
            "complete": False,
            "method": progid + ".Go; fresh Drc.txt/DrcViolations.dat; reopened GetHazards",
            "application_version": identity.get("product_version"),
            "checker_version": progid,
            "freshness": "unknown",
            "input": {
                "requested_project": str(project),
                "observed_pcb": str(board),
                "snapshot_sha256": digest(geometry),
            },
        },
        "completion": {"status": "incomplete", "timed_out": False},
        "native_stages": dict.fromkeys(
            ("save_close", "configuration", "go", "report_read"), "not_started"
        ),
        "setup_export": "not_called_hidden_unsupported",
        "board_lifecycle": {"save_count": 0, "close_count": 0},
    }
    stage = "save_close"
    try:
        result["native_stages"][stage] = "started"
        a._save_close_board(app, board, lifecycle=result["board_lifecycle"])
        result.update(
            persistence_state="partial",
            board_save_acknowledged=True,
            board_close_observed=True,
            persistence_verified=False,
        )
        result["native_stages"][stage] = "acknowledged"
        stage = "configuration"
        result["native_stages"][stage] = "started"
        work = board.parent / "Work" / "xpedition-cli" / "native-drc" / uuid.uuid4().hex
        work.mkdir(parents=True, exist_ok=False)
        paths["effective_rules"].parent.mkdir(parents=True, exist_ok=True)
        staged = {}
        for kind, row in config.items():
            data = Path(row["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("Selected DRC input changed before staging")
            path = work / ("input.drc" if kind == "rules" else "input.e2e")
            path.write_bytes(data)
            staged[kind] = file_record(path)
            if staged[kind]["sha256"] != row["sha256"] or staged[kind]["bytes"] != row["bytes"]:
                raise ValueError("Staged DRC configuration bytes differ from selected input")
        result["staged_input_configurations"] = staged
        engine = a._native_call("drc.engine.create", lambda: client.Dispatch(progid))
        a._native_call(
            "drc.engine.DesignFileName", lambda: setattr(engine, "DesignFileName", str(board))
        )
        a._native_call(
            "drc.engine.SetupFileName",
            lambda: setattr(engine, "SetupFileName", staged["rules"]["path"]),
        )
        a._native_call(
            "drc.engine.SetupElementsFileName",
            lambda: setattr(engine, "SetupElementsFileName", staged["elements"]["path"]),
        )
        # Do not set rule switches or clearances here: consume the declared profile.
        for row in staged.values():
            if file_record(Path(row["path"])) != row:
                raise ValueError("Bound DRC configuration changed before Go")
        # Preserve any old automatic outputs without moving their real paths.
        # The supported Go operation must produce fresh Config files itself.
        before = {key: file_record(path) for key, path in paths.items()}
        retained = []
        for key in ("effective_rules", "effective_elements"):
            path = paths[key]
            if before[key]["exists"]:
                destination = work / ("before-go-" + path.name)
                path.resolve().relative_to(board.parent.resolve())
                destination.resolve().relative_to(board.parent.resolve())
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != before[key]["sha256"]:
                    raise ValueError("Native configuration output changed before Go")
                destination.write_bytes(data)
                copied = file_record(destination)
                if (
                    copied["sha256"] != before[key]["sha256"]
                    or copied["bytes"] != before[key]["bytes"]
                ):
                    raise ValueError("Preserved pre-Go effective configuration copy is incomplete")
                retained.append({**before[key], "retained_copy": str(destination)})
        result["effective_configuration_before_go"] = retained
        result["report_before"] = before
        result["native_stages"][stage] = "bound"
        stage = "go"
        result["native_stages"][stage] = "started"
        started = time.time_ns()
        result["report_started_ns"] = started
        result.update(change_status="unknown", persistence_state="partial")
        result["native_go_dispatched"] = True
        go_error = None
        try:
            a._native_call("drc.engine.Go", engine.Go)
        except TimeoutError as error:
            result["completion"]["timed_out"] = True
            result["native_stages"][stage] = "timed_out"
            result["native_go_error"] = str(error)
            raise a.AdapterError("E_TIMEOUT", "Native DRC completion is unknown") from error
        except Exception as error:
            go_error = str(error)
        result.update(native_go_returned=True, native_go_error=go_error)
        result["native_stages"][stage] = "returned"
        result["outputs_after_go"] = {key: file_record(path) for key, path in paths.items()}
        messages = [
            {"severity": int(e.Severity), "message": str(e.ErrorString)} for e in engine.Errors
        ]
        result.update(engine_messages=messages, native_go_error=go_error)
        result["native_go_returned"] = True
        # The official engine uses an exception for detected hazards as well as
        # execution errors. Only the observed hazard-count form is accepted here.
        hazard_exception = re.search(r"The DRC engine found (\d+) hazards", go_error or "")
        if go_error and not hazard_exception:
            raise ValueError("Native DRC execution failed; old reports were not consumed")
        if any(m["severity"] >= 2 for m in messages):
            raise ValueError("Native DRC reported a fatal error")
        stage = "report_read"
        result["native_stages"][stage] = "started"
        fresh = read_reports(
            paths,
            before,
            started,
            selected=staged,
            layer_count=int(geometry["model"]["layers"]),
            engine_version=progid,
            expected_board=board,
        )
        if hazard_exception and int(hazard_exception[1]) != fresh["count"]:
            raise ValueError("Native exception and fresh violation count disagree")
        result.update(
            ran=True,
            completed=True,
            count=fresh["count"],
            native_reports=fresh,
            report_started_ns=started,
            report_before=before,
            change_status="applied",
            persistence_state="partial",
        )
        result["completion"]["status"] = "completed"
        result["native_stages"][stage] = "complete"
        result["source"].update(
            complete=True,
            freshness="current_run",
            coverage={
                k: fresh[k]
                for k in (
                    "rule_config_sha256",
                    "rules_checked",
                    "excluded_rules",
                    "effective_settings",
                    "effective_elements",
                    "configuration_binding",
                    "switch_interpretation",
                    "rule_execution_verification",
                    "annular_unit_binding",
                )
            },
        )
        result["source"]["limitations"] = [
            "Rule coverage is the consumed profile and emitted effective setup, "
            "not every application rule.",
            "No rule property getters exist on IDRCEngine; all switches/matrices "
            "and ignored values remain in the bound native files.",
        ]
        result["passes"] = result["clean"] = fresh["count"] == 0
        result["seconds"] = round(time.monotonic() - clock_started, 3)
        result["_untrusted"] = [
            "pcb",
            "engine_messages",
            "native_go_error",
            "native_reports",
            "input_configurations",
            "source.input",
            "source.coverage",
        ]
        return result
    except Exception as error:
        if isinstance(error, ReportBindingError):
            result["unqualified_report_observation"] = error.observation
        native = (
            error
            if isinstance(error, a.AdapterError)
            else a.AdapterError("E_NATIVE_FAILURE", str(error))
        )
        if result["native_stages"][stage] not in {"returned", "timed_out", "complete"}:
            result["native_stages"][stage] = "failed"
        result["failure"] = {
            "stage": stage,
            "code": native.code,
            "message": native.message,
            "cause": type(error).__name__,
        }
        lifecycle = result["board_lifecycle"]
        if lifecycle.get("save_acknowledged") is True:
            result.update(board_save_acknowledged=True, persistence_state="partial")
        elif lifecycle["save_count"]:
            result["persistence_state"] = "unknown"
        if "closed_observed" in lifecycle:
            result["board_close_observed"] = lifecycle["closed_observed"]
        native.details = {**(native.details or {}), "native_result": result}
        raise native from error
