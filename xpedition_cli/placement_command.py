"""CLI boundary for offline planning and guarded native placement tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import CLIError
from .placement import plan_placement, read_json, same_position, validate_request

# Global presentation flags plus the narrowly scoped task input flags. No wildcard
# targets, path positionals, replacement, routing or implicit backend switching.
COMMON_FLAGS = {"--format", "--fields", "--compact", "--json", "--quiet", "--help", "-h", "--file"}
BOOL_FLAGS = {"--compact", "--json", "--quiet", "--help", "-h", "--dry-run"}
COMMAND_FLAGS = {
    "placement-plan": COMMON_FLAGS | {"--input", "--backend"},
    "placement": COMMON_FLAGS | {"--project", "--backend", "--dry-run", "--confirm"},
}


def validate_cli(argv: list[str], positionals: list[str]) -> None:
    if len(positionals) < 2 or positionals[0] != "pcb" or positionals[1] not in COMMAND_FLAGS:
        return
    if len(positionals) != 2:
        raise CLIError("E_USAGE", "placement commands require named flags, not extra positionals")
    allowed = COMMAND_FLAGS[positionals[1]]
    seen = set()
    i = 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("-"):
            i += 1
            continue
        name, sep, inline = token.partition("=")
        if name not in allowed:
            raise CLIError(
                "E_USAGE", "option is not supported by this placement command", {"option": name}
            )
        if name in seen:
            raise CLIError("E_USAGE", "placement options must not repeat", {"option": name})
        seen.add(name)
        if name not in BOOL_FLAGS:
            if not sep:
                i += 1
                if i >= len(argv) or argv[i].startswith("-"):
                    raise CLIError("E_USAGE", "placement option requires a value", {"option": name})
            elif not inline:
                raise CLIError(
                    "E_USAGE", "placement option requires a nonempty value", {"option": name}
                )
        i += 1


def _preview(native: Any, params: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    result = native.invoke("placement_batch", {**params, "apply": False}, timeout_seconds=120.0)
    try:
        # Do not sign an arbitrary adapter-generated digest or a different task.
        rows = [row["before"] for row in result["results"]]
        reconstructed = plan_placement(request, rows)
        if result["request"] != reconstructed["request"]:
            raise ValueError("different request")
        for key in ("state_digest", "results", "summary"):
            if result[key] != reconstructed[key]:
                raise ValueError("inconsistent preview")
        if not isinstance(result["pcb"], str) or not result["pcb"]:
            raise ValueError("missing board")
    except (CLIError, KeyError, TypeError, ValueError) as error:
        raise CLIError(
            "E_SERVER", "native placement preview is incomplete or inconsistent"
        ) from error
    return result


def dispatch_placement(positionals: list[str], options: dict[str, Any]) -> dict[str, Any]:
    if len(positionals) != 2:
        raise CLIError("E_USAGE", "placement commands do not accept positional paths")
    file = options.get("file")
    if not file:
        raise CLIError("E_USAGE", "placement requires --file TASK.json")
    request = validate_request(read_json(str(file)))
    if positionals[1] == "placement-plan":
        if options.get("backend", "mock") != "mock":
            raise CLIError(
                "E_USAGE", "placement-plan is offline; use input observations, not a native backend"
            )
        if not options.get("input"):
            raise CLIError("E_USAGE", "placement-plan requires --input OBSERVATIONS.json")
        observation = read_json(str(options["input"]))
        if (
            not isinstance(observation, dict)
            or set(observation) != {"schema_version", "components"}
            or observation["schema_version"] != "1.0"
        ):
            raise CLIError(
                "E_VALIDATION", "observation document needs schema_version and components"
            )
        return plan_placement(request, observation["components"])
    if options.get("backend") != "native_xpedition":
        raise CLIError(
            "E_BACKEND_UNAVAILABLE", "pcb placement requires explicit --backend native_xpedition"
        )
    if options.get("dry_run") and options.get("confirm") is not None:
        raise CLIError("E_USAGE", "use either --dry-run or --confirm")
    if not options.get("dry_run") and options.get("confirm") is None:
        raise CLIError("E_CONFIRMATION_REQUIRED", "preview the placement task before confirming")
    path = options.get("project")
    if not path:
        raise CLIError("E_USAGE", "pcb placement requires --project PATH")
    path = Path(str(path)).expanduser().resolve()
    if path.suffix.lower() not in {".prj", ".pcb"} or not path.is_file():
        raise CLIError("E_NOT_FOUND", "a .prj or .pcb file is required")
    from .backends import NativeBackend
    from .confirm import consume, issue

    native = NativeBackend()
    native.require_implemented()
    params = {"project": str(path), "request": request}
    preview = _preview(native, params, request)
    scope = {
        "operation": "pcb_placement",
        "backend": "native_xpedition",
        "project": str(path),
        "pcb": preview["pcb"],
        "request": request,
        "state_digest": preview["state_digest"],
    }
    if options.get("dry_run"):
        token, expiry = issue(scope)
        preview["risk"] = {
            "tier": "T1",
            "blast_radius": "selected placements and a board save; traces are not rerouted",
            "partial_failure": (
                "previously moved parts may remain changed and the current part may be unplaced"
            ),
        }
        return {
            "preview": preview,
            "confirm_token": token,
            "expires_at": expiry,
            "_untrusted": ["preview"],
        }
    # Fresh preview binds current identities, sides, protection and positions.
    # Adapter rechecks the digest under its batch lock immediately before editing.
    consume(str(options["confirm"]), scope)
    try:
        result = native.invoke(
            "placement_batch",
            {**params, "apply": True, "state_digest": preview["state_digest"]},
            timeout_seconds=600.0,
        )
    except CLIError as error:
        if error.code == "E_CONFLICT" and (error.details or {}).get("write_attempted") is False:
            raise
        raise CLIError(
            "E_PROJECT_INVALID",
            "placement outcome is unknown; inspect the board before another write",
            {
                "stage": "submit",
                "write_attempted": True,
                "outcome": "unknown",
                "cause_code": error.code,
                "retry_safe": False,
            },
        ) from error
    if (
        not isinstance(result, dict)
        or result.get("outcome") != "complete"
        or not isinstance(result.get("verification"), dict)
        or result["verification"].get("valid") is not True
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "placement did not complete; inspect per-item outcomes",
            {"stage": "execution", "report": result, "retry_safe": False, "_untrusted": ["report"]},
        )
    # A response must account for every selected identity, not only a successful subset.
    results = result.get("results")
    if (
        not isinstance(results, list)
        or len(results) != len(request["selection"])
        or any(
            not isinstance(row, dict) or row.get("id") != name or row.get("ok") is not True
            for row, name in zip(results, request["selection"], strict=True)
        )
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "placement response did not account for every selected component",
            {"stage": "response", "write_attempted": True, "outcome": "unknown"},
        )
    for planned, returned in zip(preview["results"], results, strict=True):
        observed = returned.get("observed")
        if not isinstance(observed, dict) or not same_position(planned["target"], observed):
            raise CLIError(
                "E_PROJECT_INVALID",
                "placement response did not verify the requested target",
                {"stage": "response", "write_attempted": True, "outcome": "unknown"},
            )
    if preview["summary"]["changed_count"] and (
        result.get("write_attempted") is not True or result.get("drc_restored") is not True
    ):
        raise CLIError(
            "E_PROJECT_INVALID",
            "placement execution or DRC restoration is unconfirmed",
            {"stage": "response", "write_attempted": True},
        )
    if result.get("write_attempted") is True and result.get("saved") is not True:
        raise CLIError(
            "E_PROJECT_INVALID",
            "placement save is unconfirmed",
            {"stage": "save", "write_attempted": True},
        )
    return result
