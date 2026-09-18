"""Inspect trusted standalone type libraries without activating an EDA server.

Only ITypeLib/ITypeInfo metadata is read. This does not certify operational
semantics, availability, safety, or the callable surface of the CLI adapter.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from .errors import CLIError

COMMAND = ("system", "api-inventory")
MAX_BYTES = 32 * 1024 * 1024
MAX_TYPES = 10000
MAX_MEMBERS = 20000
MAX_PARAMETERS = 256
DEFAULT_LIMIT = 100
MAX_LIMIT = 200
_INVOCATION = {1: "function", 2: "property_get", 4: "property_put", 8: "property_putref"}


def _bounded(value: Any, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("metadata count is outside inventory limits")
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("invalid metadata name")
    return value


def _detail(error: Exception, stage: str, **indexes: Any) -> dict[str, Any]:
    # No arbitrary exception text, local paths, registry contents or help strings.
    result = {"stage": stage, **indexes}
    hresult = getattr(error, "hresult", None)
    if type(hresult) is int:
        result["hresult"] = str(hresult)
    return result


def _descriptor(value: Any, depth: int = 0) -> Any:
    """Keep raw numeric type descriptors; do not infer Python/JSON schemas."""
    if depth > 8:
        raise ValueError("type descriptor exceeds maximum nesting")
    if value is None or type(value) is int:
        return value
    if isinstance(value, (tuple, list)) and len(value) <= 32:
        return [_descriptor(item, depth + 1) for item in value]
    raise ValueError("unsupported descriptor representation")


def _element(value: Any) -> dict[str, Any]:
    if not isinstance(value, (tuple, list)) or len(value) not in {2, 3}:
        raise ValueError("unsupported element descriptor")
    flags = int(value[1])
    return {
        "type_descriptor": _descriptor(value[0]),
        "flags": flags,
        "default_declared": bool(flags & 32),
    }


def _type_summary(info: Any, index: int) -> dict[str, Any]:
    attr = info.GetTypeAttr()
    return {
        "index": index,
        "name": _name(info.GetDocumentation(-1)[0]),
        "guid": str(attr.iid),
        "kind": int(attr.typekind),
        "functions": _bounded(attr.cFuncs, MAX_MEMBERS),
        "variables": _bounded(attr.cVars, MAX_MEMBERS),
        "implemented_types": _bounded(attr.cImplTypes, MAX_TYPES),
        "flags": int(attr.wTypeFlags),
        "version": f"{attr.wMajorVerNum}.{attr.wMinorVerNum}",
    }


def _member(info: Any, index: int, function: bool) -> dict[str, Any]:
    if function:
        desc = info.GetFuncDesc(index)
        count = _bounded(len(desc.args), MAX_PARAMETERS)
        names = info.GetNames(desc.memid)
        if not names or len(names) > count + 1:
            raise ValueError("member names missing or oversized")
        params = []
        for pos, arg in enumerate(desc.args):
            params.append(
                {
                    "position": pos,
                    "name": _name(names[pos + 1]) if pos + 1 < len(names) else None,
                    **_element(arg),
                }
            )
        return {
            "index": index,
            "kind": _INVOCATION.get(desc.invkind, "unknown"),
            "member_id": str(desc.memid),
            "name": _name(names[0]),
            "invocation_kind": int(desc.invkind),
            "flags": int(desc.wFuncFlags),
            "calling_convention": int(desc.callconv),
            "optional_parameters": int(desc.cParamsOpt),
            "parameters": params,
            "return": _element(desc.rettype),
        }
    desc = info.GetVarDesc(index)
    # No desc.value: constant/default values are not part of this inventory.
    names = info.GetNames(desc.memid)
    return {
        "index": index,
        "kind": "variable",
        "member_id": str(desc.memid),
        "name": _name(names[0]),
        "variable_kind": int(desc.varkind),
        "flags": int(desc[3]),
        "element": _element(desc.elemdescVar),
    }


def inspect_library(
    library: Any, *, name: str | None = None, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        attr = library.GetLibAttr()
        identity = {"guid": str(attr[0]), "lcid": int(attr[1]), "version": f"{attr[3]}.{attr[4]}"}
        count = _bounded(library.GetTypeInfoCount(), MAX_TYPES)
    except Exception as error:
        raise CLIError(
            "E_VALIDATION", "cannot read type library identity", _detail(error, "library_header")
        ) from error
    headers: list[dict[str, Any]] = []
    for index in range(count):
        try:
            row = _type_summary(library.GetTypeInfo(index), index)
        except Exception as error:
            problem = _detail(error, "type_header", type_index=index)
            issues.append(problem)
            row = {"index": index, "error": problem}
        headers.append(row)
    selected = None
    if name is not None:
        if issues:
            raise CLIError(
                "E_VALIDATION",
                "cannot resolve type uniquely with unreadable headers",
                {"stage": "type_selection", "unreadable_headers": len(issues)},
            )
        matches = [row for row in headers if row["name"] == name]
        if len(matches) != 1:
            code = "E_NOT_FOUND" if not matches else "E_CONFLICT"
            raise CLIError(
                code,
                "type name must select exactly one type",
                {"stage": "type_selection", "match_count": len(matches)},
            )
        selected = matches[0]
        count = selected["functions"] + selected["variables"]
        start = min(offset, count)
        end = min(start + limit, count)
        info = library.GetTypeInfo(selected["index"])
        rows = []
        for position in range(start, end):
            is_function = position < selected["functions"]
            index = position if is_function else position - selected["functions"]
            try:
                row = _member(info, index, is_function)
            except Exception as error:
                problem = _detail(
                    error, "member", type_index=selected["index"], member_position=position
                )
                issues.append(problem)
                row = {"position": position, "error": problem}
            rows.append(row)
    else:
        start = min(offset, count)
        end = min(start + limit, count)
        rows = headers[start:end]
    return {
        "library": identity,
        "selected_type": selected,
        "scope": "selected_member_page" if name is not None else "type_headers",
        "items": rows,
        "count": len(rows),
        "offset": start,
        "next_offset": end if end < count else None,
        "has_more": end < count,
        "total": count,
        "complete_in_scope": not issues,
        "issue_count": len(issues),
        "issues": issues[:100],
        "issues_truncated": len(issues) > 100,
        "execution": {
            "application_activated": False,
            "members_invoked": False,
            "registration_requested": False,
            "capabilities_granted": False,
        },
        "semantic_validation": "not_performed",
        "source": {},
        "_untrusted": ["library", "selected_type", "items", "issues", "source"],
    }


def _local_file(raw: str) -> Path:
    if raw.startswith(("\\\\", "//")) or "://" in raw:
        raise CLIError("E_USAGE", "input must be a trusted local standalone .tlb or .olb file")
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise CLIError("E_NOT_FOUND", "type library file was not found") from error
    except (OSError, ValueError) as error:
        raise CLIError("E_IO", "cannot resolve type library input") from error
    if str(path).startswith(("\\\\", "//")) or path.suffix.lower() not in {".tlb", ".olb"}:
        raise CLIError("E_USAGE", "only local standalone .tlb/.olb files are supported")
    if not path.is_file():
        raise CLIError("E_USAGE", "type library input must be a regular file")
    return path


def _fingerprint(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
    except OSError as error:
        raise CLIError("E_IO", "cannot read type library file") from error
    if len(data) > MAX_BYTES or data[:4] not in {b"MSFT", b"SLTG"}:
        raise CLIError("E_VALIDATION", "input is not a supported bounded standalone type library")
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _load_metadata(com: Any, path: Path) -> Any:
    # pywin32 exposes LoadTypeLib, not LoadTypeLibEx. Microsoft documents that
    # LoadTypeLib does NOT register a type library when a path is supplied.
    # Keep this absolute-path boundary even if a caller bypasses run().
    if not path.is_absolute():
        raise CLIError("E_USAGE", "metadata loading requires an absolute file path")
    return com.LoadTypeLib(str(path))


def _pythoncom() -> Any:
    if sys.platform != "win32":
        raise CLIError("E_BACKEND_UNAVAILABLE", "type library metadata loading requires Windows")
    try:
        import pythoncom
    except ImportError as error:
        raise CLIError(
            "E_BACKEND_UNAVAILABLE", "type library metadata loading requires pywin32"
        ) from error
    return pythoncom


def run(options: dict[str, Any]) -> dict[str, Any]:
    if not options.get("input"):
        raise CLIError("E_USAGE", "system api-inventory requires --input PATH.tlb")
    limit = DEFAULT_LIMIT if options.get("limit") is None else options["limit"]
    offset = options.get("offset") or 0
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_LIMIT
        or type(offset) is not int
        or offset < 0
    ):
        raise CLIError("E_VALIDATION", "limit must be 1..200 and offset nonnegative")
    name = options.get("name")
    if name is not None and (not isinstance(name, str) or not name or len(name) > 512):
        raise CLIError("E_VALIDATION", "name must be a nonempty exact type name")
    path = _local_file(str(options["input"]))
    before = _fingerprint(path)
    com = _pythoncom()
    library = None
    initialized = False
    try:
        com.CoInitializeEx(com.COINIT_APARTMENTTHREADED)
        initialized = True
        library = _load_metadata(com, path)
        result = inspect_library(library, name=name, limit=limit, offset=offset)
        if _fingerprint(path) != before:
            raise CLIError("E_CONFLICT", "type library file changed during inventory")
        result["source"] = {
            "path": str(path),
            **before,
            "origin": "caller_supplied_local_file",
            "freshness": "hash_checked_before_and_after",
        }
        return result
    except CLIError:
        raise
    except Exception as error:
        raise CLIError(
            "E_BACKEND_UNAVAILABLE",
            "cannot inspect the supplied type library",
            _detail(error, "type_library_load"),
        ) from error
    finally:
        library = None
        if initialized:
            com.CoUninitialize()


def validate_argv(argv: list[str]) -> None:
    values = {"--input", "--name", "--limit", "--offset", "--fields", "--format"}
    flags = {"--compact", "--quiet", "--help", "-h", "--json"}
    seen: set[str] = set()
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("-"):
            name, equal, value = token.partition("=")
            key = "--format" if name == "--json" else "--help" if name == "-h" else name
            if name not in values | flags or key in seen:
                raise CLIError("E_USAGE", "unsupported or repeated option for API inventory")
            seen.add(key)
            if name in values:
                if not equal:
                    index += 1
                    value = argv[index] if index < len(argv) else ""
                if not value or (not equal and value.startswith("-")):
                    raise CLIError(
                        "E_USAGE", "option requires a value; use = for dash-prefixed values"
                    )
            elif equal:
                raise CLIError("E_USAGE", "boolean options do not take values")
        index += 1


def protected_fields(fields: str | None) -> str | None:
    if not fields:
        return fields
    return fields + (
        ",scope,execution,semantic_validation,source,complete_in_scope,issue_count"
        ",count,offset,next_offset,has_more,total,issues_truncated,_untrusted"
    )
