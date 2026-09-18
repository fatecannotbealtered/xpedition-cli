"""Read-only pin assignment planning against supplied observations, never COM.

Identifiers are strings. Missing observations are unknown, not unconnected.
A plan is NOT an executable ChangeSet: changing an existing net can affect peers.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .errors import CLIError

COMMANDS = {("schematic", "pin-plan"), ("schematic", "pin-check")}
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_ASSIGNMENT_BYTES = 2 * 1024 * 1024
MAX_ASSIGNMENTS = 10000
MAX_PAGE = 1000
DEFAULT_PAGE = 100
_MISSING = object()


def _invalid(message: str, **details: Any) -> CLIError:
    return CLIError("E_VALIDATION", message, details)


def _identifier(value: Any, field: str, *, pin: bool = False) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or (pin and "." in value)):
        raise _invalid("identifier must be a non-empty, exact string without control characters",
                       field=field)
    return value


def _bytes(path: str, maximum: int) -> bytes:
    try:
        with Path(path).expanduser().open("rb") as handle:
            data = handle.read(maximum + 1)
    except FileNotFoundError as error:
        raise CLIError("E_NOT_FOUND", "input file was not found") from error
    except OSError as error:
        raise CLIError("E_IO", "cannot read input file") from error
    if len(data) > maximum:
        raise _invalid("input exceeds the documented size limit", maximum_bytes=maximum)
    return data


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("duplicate JSON object key")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise _invalid("nonfinite JSON numbers are not accepted")


def _number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise _invalid("nonfinite JSON numbers are not accepted")
    return number


def read_snapshot(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_object,
                           parse_constant=_constant, parse_float=_number)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _invalid("snapshot must be UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise _invalid("snapshot must be an object")
    if "ok" in value:
        if value.get("ok") is not True or value.get("schema_version") != "1.0":
            raise _invalid("snapshot envelope must be a successful schema_version 1.0 response")
        value = value.get("data")
        if not isinstance(value, dict):
            raise _invalid("snapshot envelope data must be an object")
    _identifier(value.get("project"), "project")
    _identifier(value.get("revision"), "revision")
    if not isinstance(value.get("components"), list):
        raise _invalid("snapshot.components must be an explicit array; no mock defaults are added")
    for key in ("has_more", "truncated", "incomplete"):
        if value.get(key):
            raise _invalid("a known partial snapshot cannot establish pin assignment state")
    return value


def read_assignments(data: bytes) -> list[dict[str, str]]:
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader, [])
        if (len(set(header)) != len(header) or not {"refdes", "pin", "net"} <= set(header)
                or set(header) - {"refdes", "pin", "net", "expected_net"}):
            raise _invalid("CSV needs unique refdes,pin,net columns; expected_net is optional")
        records: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in reader:
            if len(row) != len(header):
                raise _invalid("CSV row has the wrong number of columns", row=reader.line_num)
            record = dict(zip(header, row, strict=True))
            for key in ("refdes", "pin", "net"):
                _identifier(record[key], key, pin=key == "pin")
            if record.get("expected_net"):
                _identifier(record["expected_net"], "expected_net")
            target = record["refdes"], record["pin"]
            if target in seen:
                raise _invalid("CSV repeats a pin assignment", row=reader.line_num)
            seen.add(target)
            records.append(record)
            if len(records) > MAX_ASSIGNMENTS:
                raise _invalid("too many assignments", maximum=MAX_ASSIGNMENTS)
    except (UnicodeError, csv.Error) as error:
        raise _invalid("assignments must be well-formed UTF-8 CSV") from error
    if not records:
        raise _invalid("CSV must contain at least one assignment")
    return records


def _index(snapshot: dict[str, Any]) -> tuple[dict, dict, dict]:
    components: dict[str, list[dict]] = defaultdict(list)
    memberships: dict[str, set[str]] = defaultdict(set)
    peers: dict[str, set[str]] = defaultdict(set)
    for component in snapshot["components"]:
        if not isinstance(component, dict):
            raise _invalid("snapshot component must be an object")
        refdes = _identifier(component.get("refdes"), "components.refdes")
        components[refdes].append(component)
        if "pins" in component and not isinstance(component["pins"], list):
            raise _invalid("component.pins must be an array when present")
        for pin in component.get("pins", []):
            if not isinstance(pin, dict):
                raise _invalid("pin must be an object")
            number = _identifier(pin.get("number"), "pins.number", pin=True)
            if "net" in pin and pin["net"] is not None:
                net = _identifier(pin["net"], "pins.net")
                peers[net].add(f"{refdes}.{number}")
    connections = snapshot.get("connections", [])
    if not isinstance(connections, list):
        raise _invalid("snapshot.connections must be an array when present")
    for connection in connections:
        if not isinstance(connection, dict) or not isinstance(connection.get("pins"), list):
            raise _invalid("connection must have an explicit pins array")
        net = _identifier(connection.get("net"), "connections.net")
        for value in connection["pins"]:
            key = _identifier(value, "connections.pins")
            refdes, separator, pin = key.rpartition(".")
            if not separator or not refdes or not pin:
                raise _invalid("connection pin must be refdes.pin")
            memberships[key].add(net)
            peers[net].add(key)
    return components, memberships, peers


def assess(snapshot: dict[str, Any], assignments: list[dict[str, str]], *, check: bool = False,
           limit: int = DEFAULT_PAGE, offset: int = 0) -> dict[str, Any]:
    """Validate all requested pins before pagination; checks are snapshot-scoped."""
    components, memberships, peers = _index(snapshot)
    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    indexes: dict[str, dict[str, list[dict]]] = {}
    for refdes, candidates in components.items():
        if len(candidates) == 1:
            pins: dict[str, list[dict]] = defaultdict(list)
            for pin in candidates[0].get("pins", []):
                pins[pin["number"]].append(pin)
            indexes[refdes] = pins
    peer_samples = {net: sorted(pins)[:9] for net, pins in peers.items()}
    for assignment in assignments:
        refdes, number, target = (assignment[key] for key in ("refdes", "pin", "net"))
        key = f"{refdes}.{number}"
        observed: Any = _MISSING
        problem = None
        candidates = components.get(refdes, [])
        if len(candidates) != 1:
            problem = "component_missing" if not candidates else "component_ambiguous"
        else:
            pins = indexes[refdes].get(number, [])
            if len(pins) != 1:
                problem = "pin_missing_or_unobserved" if not pins else "pin_ambiguous"
            else:
                evidence = set(memberships.get(key, set()))
                direct = pins[0].get("net", _MISSING)
                if direct is not _MISSING:
                    evidence.add(direct)
                if len(evidence) > 1:
                    problem = "conflicting_connectivity"
                elif evidence:
                    observed = next(iter(evidence))
                else:
                    problem = "connectivity_unobserved"
        if problem:
            issues.append({"code": problem, "pin": key})
        elif not check and "expected_net" in assignment:
            expected = assignment["expected_net"] or None
            if expected != observed:
                problem = "precondition_mismatch"
                issues.append({"code": problem, "pin": key, "expected": expected,
                               "observed": observed})
        known = observed is not _MISSING
        action = ("blocked" if problem else "noop" if observed == target else
                  "connect" if observed is None else "reassign")
        if check:
            if problem:
                match = None
            else:
                match = observed == target
                if not match:
                    issues.append({"code": "net_mismatch", "pin": key,
                                   "expected": target, "observed": observed})
            action = "unknown" if match is None else "matched" if match else "mismatched"
        old_peers = [p for p in peer_samples.get(observed, []) if p != key] if known else []
        peer_count = (len(peers.get(observed, set())) - int(key in peers.get(observed, set()))) if known else 0
        row = {"refdes": refdes, "pin": number, "pin_id": key,
               "observed_net": observed if known else None, "observed_known": known,
               "desired_net": target, "action": action,
               "other_observed_pins_on_net": peer_count,
               "peer_sample": old_peers[:8], "peer_sample_truncated": peer_count > 8,
               "requires_isolation_review": action == "reassign" and peer_count > 0}
        rows.append(row)
    counts = Counter(row["action"] for row in rows)
    start = min(offset, len(rows))
    end = min(start + limit, len(rows))
    unknown = counts["unknown"] if check else counts["blocked"]
    matches = (False if counts["mismatched"] else None if unknown else True) if check else None
    return {"mode": "check" if check else "plan", "scope": "requested_pins_in_supplied_snapshot",
            "project": snapshot["project"], "revision": snapshot["revision"],
            "valid": not issues, "matches": matches, "items": rows[start:end],
            "count": end - start, "offset": start, "next_offset": end if end < len(rows) else None,
            "has_more": end < len(rows), "summary": {"requested": len(rows), "actions": dict(counts),
            "issue_count": len(issues)}, "issues": issues[:DEFAULT_PAGE],
            "issues_truncated": len(issues) > DEFAULT_PAGE,
            "execution": {"supported": False, "performed": False,
                          "reason": "observation_only; not an executable ChangeSet or native verification"},
            "_untrusted": ["project", "revision", "items", "issues", "source"]}


def validate_argv(argv: list[str]) -> None:
    values = {"--input", "--file", "--limit", "--offset", "--fields", "--format"}
    booleans = {"--compact", "--quiet", "--help", "-h", "--json"}
    seen: set[str] = set()
    i = 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("-"):
            i += 1
            continue
        name, equal, value = token.partition("=")
        key = "--format" if name == "--json" else "--help" if name == "-h" else name
        if name not in values | booleans or key in seen:
            raise CLIError("E_USAGE", "unsupported or repeated option for offline pin workflow")
        seen.add(key)
        if name in values:
            if not equal:
                i += 1
                value = argv[i] if i < len(argv) else ""
            if not value or (not equal and value.startswith("-")):
                raise CLIError("E_USAGE", "option requires a value; use = for a dash-prefixed path")
        elif equal:
            raise CLIError("E_USAGE", "boolean options do not take a value")
        i += 1


def run(command: tuple[str, ...], options: dict[str, Any]) -> dict[str, Any]:
    if (command not in COMMANDS or options.get("backend") not in {None, "mock"}
            or options.get("project") or options.get("confirm") is not None or options.get("dry_run")):
        raise CLIError("E_USAGE", "pin workflows only read explicit saved inputs; no backend or write gate")
    if not options.get("input") or not options.get("file"):
        raise CLIError("E_USAGE", "pin workflow requires --input SNAPSHOT --file ASSIGNMENTS.csv")
    limit = DEFAULT_PAGE if options.get("limit") is None else options["limit"]
    offset = options.get("offset") or 0
    if (type(limit) is not int or not 1 <= limit <= MAX_PAGE or
            type(offset) is not int or offset < 0):
        raise _invalid("limit must be 1..1000 and offset a nonnegative integer")
    snapshot_bytes = _bytes(str(options["input"]), MAX_SNAPSHOT_BYTES)
    assignment_bytes = _bytes(str(options["file"]), MAX_ASSIGNMENT_BYTES)
    result = assess(read_snapshot(snapshot_bytes), read_assignments(assignment_bytes),
                    check=command[1] == "pin-check", limit=limit, offset=offset)
    result["source"] = {"snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
                        "assignments_sha256": hashlib.sha256(assignment_bytes).hexdigest(),
                        "freshness": "not_checked", "origin": "caller_supplied"}
    return result


def protected_fields(fields: str | None) -> str | None:
    if not fields:
        return fields
    # Preserve interpretation/security controls even on the pre-PR #4 output layer.
    return fields + ",valid,matches,summary,execution,source,count,offset,next_offset,has_more,issues_truncated,_untrusted"
