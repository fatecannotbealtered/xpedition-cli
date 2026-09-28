"""What the command modules share: paths, the write gate, the native backend, paging."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..backends import NativeBackend
from ..confirm import consume, issue
from ..errors import CLIError
from ..query_page import query_page


def text(options: dict[str, Any], name: str) -> str:
    value = options.get(name)
    return "" if value is None else str(value).strip()


def comma_list(options: dict[str, Any], name: str) -> list[str]:
    """A plural flag's values in the order given, each once."""
    raw = options.get(name)
    if raw is None:
        return []
    items = list(dict.fromkeys(item.strip() for item in str(raw).split(",") if item.strip()))
    if not items:
        raise CLIError("E_VALIDATION", f"--{name} names nothing", {name: str(raw)})
    return items


def number(options: dict[str, Any], name: str, default: float | None = None) -> float | None:
    raw = options.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise CLIError("E_VALIDATION", f"--{name} must be a number", {name: str(raw)}) from exc


def integer(options: dict[str, Any], name: str, default: int | None = None) -> int | None:
    raw = options.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw))
    except ValueError as exc:
        raise CLIError("E_VALIDATION", f"--{name} must be an integer", {name: str(raw)}) from exc


def project_file(options: dict[str, Any], command: str) -> Path:
    """The existing .prj a command works on."""
    raw = text(options, "project")
    if not raw:
        raise CLIError("E_USAGE", f"{command} requires --project X.prj")
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() != ".prj":
        raise CLIError("E_VALIDATION", f"{command} expects a .prj path", {"path": str(path)})
    if not path.is_file():
        raise CLIError("E_NOT_FOUND", "project file was not found", {"path": str(path)})
    return path


def board_file(options: dict[str, Any], command: str) -> str:
    """The existing .prj or .pcb a board command works on."""
    raw = text(options, "project")
    if not raw:
        raise CLIError("E_USAGE", f"{command} requires --project X.prj (or the board's .pcb)")
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() not in {".prj", ".pcb"}:
        raise CLIError(
            "E_VALIDATION", f"{command} expects a .prj or .pcb path", {"path": str(path)}
        )
    if not path.is_file():
        raise CLIError("E_NOT_FOUND", "project file was not found", {"path": str(path)})
    return str(path)


def input_file(options: dict[str, Any], name: str, command: str, what: str) -> Path:
    raw = text(options, name)
    if not raw:
        raise CLIError("E_USAGE", f"{command} requires --{name} {what}")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise CLIError("E_NOT_FOUND", f"{what} was not found", {"path": str(path)})
    return path


def read_json_file(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CLIError("E_NOT_FOUND", f"{what} cannot be read: {exc}", {"path": str(path)}) from exc
    except json.JSONDecodeError as exc:
        raise CLIError(
            "E_VALIDATION", f"{what} is not valid JSON: {exc}", {"path": str(path)}
        ) from exc


def read_design(options: dict[str, Any], command: str) -> tuple[Path, dict[str, Any]]:
    path = input_file(options, "design", command, "the design file")
    design = read_json_file(path, "the design file")
    if not isinstance(design, dict):
        raise CLIError(
            "E_VALIDATION", "the design file must hold a JSON object", {"design": str(path)}
        )
    return path, design


def output_file(
    options: dict[str, Any], command: str, suffix: str, *, required: bool = False
) -> Path | None:
    raw = text(options, "output")
    if not raw:
        if required:
            raise CLIError("E_USAGE", f"{command} requires --output FILE{suffix}")
        return None
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() != suffix:
        raise CLIError(
            "E_VALIDATION", f"{command}: --output must end in {suffix}", {"output": str(path)}
        )
    return path


def write_json_output(options: dict[str, Any], command: str, payload: Any) -> str | None:
    """Write `payload` to --output (a new .json unless --replace); its path, or None."""
    path = output_file(options, command, ".json")
    if path is None:
        return None
    if path.exists() and not options.get("replace"):
        raise CLIError(
            "E_CONFLICT", "output file already exists", {"path": str(path), "hint": "--replace"}
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


def pace(options: dict[str, Any], command: str) -> float:
    value = number(options, "pace", 0.0) or 0.0
    if value < 0 or value > 10:
        raise CLIError("E_VALIDATION", f"{command}: --pace is 0 to 10 seconds", {"pace": value})
    return value


def continue_on_error(options: dict[str, Any]) -> bool:
    """CLI-SPEC §15.5: a batch goes on past a failed item unless told `false`."""
    value = options.get("continue_on_error")
    if value is None:
        return True
    word = str(value).strip().lower()
    if word not in {"true", "false"}:
        raise CLIError("E_VALIDATION", "--continue-on-error is true or false", {"value": value})
    return word == "true"


def native() -> NativeBackend:
    backend = NativeBackend()
    backend.require_implemented()
    return backend


def reader(options: dict[str, Any]) -> NativeBackend:
    """The native backend for a read, with --timeout applied to its snapshot."""
    backend = native()
    seconds = number(options, "timeout")
    if seconds is not None:
        if not 1 <= seconds <= 3600:
            raise CLIError("E_VALIDATION", "--timeout is 1 to 3600 seconds", {"timeout": seconds})
        backend.read_timeout_seconds = seconds
    return backend


# -- the write gate -----------------------------------------------------------------


def check_gate(options: dict[str, Any], command: str) -> None:
    """A write runs as --dry-run or --confirm TOKEN, never both, never neither."""
    if options.get("dry_run") and options.get("confirm") is not None:
        raise CLIError("E_USAGE", "use either --dry-run or --confirm, not both")
    if not options.get("dry_run") and options.get("confirm") is None:
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            f"{command} requires --dry-run, then --confirm <confirm_token>",
        )


def previewed(preview: dict[str, Any], scope: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The dry run's answer: the preview and a token bound to `scope`."""
    token, expires_at = issue(scope)
    return {
        "preview": preview,
        **extra,
        "confirm_token": token,
        "expires_at": expires_at,
        "_untrusted": ["preview", *[key for key in extra if key != "_untrusted"]],
    }


def confirmed(options: dict[str, Any], scope: dict[str, Any]) -> None:
    consume(str(options["confirm"]), scope)


def require_dangerous(options: dict[str, Any], why: str) -> None:
    """CLI-SPEC §15.4: the second gate of a dangerous write, checked before the token
    is spent, so the same token still works once --dangerous is added."""
    if not options.get("dangerous"):
        raise CLIError(
            "E_CONFIRMATION_REQUIRED",
            f"{why}; confirm with --dangerous as well as the token",
            {"hint": "run the same command with --dangerous --confirm <confirm_token>"},
        )


# -- lists ------------------------------------------------------------------------


def page(
    project: dict[str, Any],
    items: Iterable[Any],
    options: dict[str, Any],
    untrusted: list[str] | None = None,
) -> dict[str, Any]:
    result = query_page(
        items,
        query=options.get("query"),
        limit=options.get("limit"),
        offset=int(options.get("offset") or 0),
    )
    return {
        "project": project["project"],
        **result,
        "_untrusted": untrusted or ["project", "items"],
    }


def sheets_option(options: dict[str, Any], planned: list[int]) -> list[int] | None:
    """`--sheets 3,4`: only these sheets of the design; None means all of them."""
    raw = options.get("sheets")
    if raw is None:
        return None
    try:
        chosen = sorted({int(part) for part in str(raw).split(",") if part.strip()})
    except ValueError as exc:
        raise CLIError(
            "E_VALIDATION",
            "--sheets is a comma-separated list of sheet numbers",
            {"sheets": str(raw)},
        ) from exc
    if not chosen:
        raise CLIError("E_VALIDATION", "--sheets names no sheet", {"sheets": str(raw)})
    unknown = [number for number in chosen if number not in planned]
    if unknown:
        raise CLIError(
            "E_VALIDATION",
            "--sheets names sheets the design does not list",
            {"sheets": unknown, "design_sheets": planned},
        )
    return chosen
