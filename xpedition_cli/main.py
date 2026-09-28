"""The command line: parse against the registry, run the command, emit one envelope."""

from __future__ import annotations

import re
import sys
import time
from typing import Any

from . import __version__
from .audit import record
from .backends import suppress_progress
from .cli.registry import GLOBAL_OPTIONS, STAGES, Command, Param, by_path, commands, domains
from .errors import CLIError
from .output import emit, failure, success


def _switches() -> set[str]:
    names = {name for name, kind in GLOBAL_OPTIONS.items() if kind == "switch"}
    for command in commands():
        names.update(param.name for param in command.all_params() if param.type == "boolean")
    return names


def _tokens(argv: list[str]) -> tuple[list[str], list[tuple[str, str | None]]]:
    """Positionals, and each option with its value (None for a switch)."""
    switches = _switches()
    positionals: list[str] = []
    flags: list[tuple[str, str | None]] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "-h":
            token = "--help"
        if token.startswith("--"):
            name, separator, inline = token[2:].partition("=")
            if not name:
                raise CLIError("E_USAGE", "an option needs a name", {"option": token})
            if name in switches:
                if separator:
                    raise CLIError("E_USAGE", f"--{name} takes no value", {"option": f"--{name}"})
                flags.append((name, None))
            elif separator:
                flags.append((name, inline))
            else:
                index += 1
                if index >= len(argv) or argv[index].startswith("--"):
                    raise CLIError(
                        "E_USAGE", f"option --{name} requires a value", {"option": f"--{name}"}
                    )
                flags.append((name, argv[index]))
            index += 1
            continue
        positionals.append(token)
        index += 1
    return positionals, flags


def _command(positionals: list[str], wants_help: bool) -> Command | None:
    table = by_path()
    for length in (2, 1):
        command = table.get(" ".join(positionals[:length]))
        if command is not None:
            extra = positionals[length:]
            if extra:
                raise CLIError(
                    "E_USAGE",
                    f"{command.path} takes options, not positional arguments",
                    {"arguments": extra, "hint": f"xpedition-cli {command.path} --help"},
                )
            return command
    if wants_help and (not positionals or (len(positionals) == 1 and positionals[0] in domains())):
        return None
    if not positionals:
        raise CLIError(
            "E_USAGE",
            "a command is required",
            {"domains": domains(), "hint": "xpedition-cli --help, or reference for agents"},
        )
    if positionals[0] in domains():
        verbs = [item.path for item in commands() if item.path.split()[0] == positionals[0]]
        raise CLIError(
            "E_USAGE",
            f"unknown {positionals[0]} command: {' '.join(positionals)}"
            if len(positionals) > 1
            else f"{positionals[0]} needs a verb",
            {"commands": verbs},
        )
    raise CLIError(
        "E_USAGE",
        f"unknown command: {' '.join(positionals)}",
        {"domains": domains(), "hint": "xpedition-cli --help"},
    )


_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")


def _check_value(command: Command, param: Param, value: str) -> None:
    if param.multiple:
        for item in value.split(","):
            if item.strip():
                _check_one(command, param.name, param.type, param.choices, item.strip())
        return
    _check_one(command, param.name, param.type, param.choices, value)


def _check_one(
    command: Command, name: str, kind: str, choices: tuple[str, ...], value: str
) -> None:
    where = f"{command.path}: --{name}"
    if kind == "integer":
        try:
            int(value)
        except ValueError as exc:
            raise CLIError("E_VALIDATION", f"{where} must be an integer", {name: value}) from exc
    elif kind == "number":
        try:
            float(value)
        except ValueError as exc:
            raise CLIError("E_VALIDATION", f"{where} must be a number", {name: value}) from exc
    elif kind == "semver":
        if not _SEMVER.match(value):
            raise CLIError("E_VALIDATION", f"{where} must be a semantic version", {name: value})
    if choices and value.lower() not in choices:
        raise CLIError(
            "E_VALIDATION",
            f"{where} is one of {', '.join(choices)}",
            {name: value, "choices": list(choices)},
        )


def parse_argv(argv: list[str]) -> tuple[Command | None, dict[str, Any]]:
    positionals, flags = _tokens(argv)
    wants_help = any(name == "help" for name, _ in flags)
    command = _command(positionals, wants_help)
    options: dict[str, Any] = {"format": "json", "compact": False, "fields": None, "quiet": False}
    options["help"] = wants_help
    options["_positionals"] = positionals
    if command is None:
        return None, options
    declared = {param.name: param for param in command.all_params()}
    seen: dict[str, str | None] = {}
    for name, value in flags:
        param = declared.get(name)
        if param is None and name not in GLOBAL_OPTIONS:
            raise CLIError(
                "E_USAGE",
                f"{command.path} takes no --{name}",
                {
                    "option": f"--{name}",
                    "accepted": sorted(f"--{item}" for item in declared),
                    "hint": f'xpedition-cli reference --command "{command.path}"',
                },
            )
        key = name.replace("-", "_")
        if param is not None and value is not None:
            _check_value(command, param, value)
        if name in seen:
            if param is not None and param.multiple:
                options[key] = f"{options[key]},{value}"
                continue
            if name == "fields":
                options[key] = f"{options[key]},{value}"
                continue
            if seen[name] != value:
                raise CLIError(
                    "E_USAGE",
                    f"--{name} was given twice with different values",
                    {"option": f"--{name}", "values": [seen[name], value]},
                )
            continue
        seen[name] = value
        if value is None:
            options[key] = True
        elif name == "json":
            options["format"] = "json"
        else:
            options[key] = value
    if options.get("json"):
        options["format"] = "json"
    if options.get("format") not in {"json", "text", "raw"}:
        raise CLIError("E_VALIDATION", "--format is json, text or raw")
    for key in ("limit", "offset"):
        if options.get(key) is not None:
            options[key] = int(options[key])
            if options[key] < 0:
                raise CLIError("E_VALIDATION", f"--{key} must not be negative")
    if not wants_help:
        missing = [
            f"--{param.name}"
            for param in command.all_params()
            if param.required and options.get(param.name.replace("-", "_")) is None
        ]
        if missing:
            raise CLIError(
                "E_USAGE",
                f"{command.path} requires {' and '.join(missing)}",
                {"missing": missing, "hint": f"xpedition-cli {command.path} --help"},
            )
        exclusive = command.extra.get("mutually_exclusive") or []
        for group in exclusive:
            given = [name for name in group if options.get(name.replace("-", "_")) is not None]
            if len(given) > 1:
                raise CLIError(
                    "E_USAGE",
                    f"{command.path} takes only one of {', '.join('--' + name for name in group)}",
                    {"given": ["--" + name for name in given]},
                )
    return command, options


# -- help for people ------------------------------------------------------------------

_STAGE_TITLES = {
    "environment": "Environment",
    "session": "Session",
    "project": "Project",
    "library": "Library",
    "schematic": "Schematic",
    "board": "Board",
    "placement": "Placement",
    "routing": "Routing",
    "inspection": "Inspection",
    "fabrication": "Fabrication",
}


def help_text(command: Command | None, positionals: list[str]) -> str:
    if command is not None:
        lines = [f"xpedition-cli {command.path}", "", command.description, ""]
        if command.writes:
            lines.append(
                "A write: run it with --dry-run first, then with --confirm <confirm_token>"
                + (" --dangerous" if command.tier == "dangerous" else "")
                + "."
            )
            if command.tier == "dangerous":
                lines.append(f"--dangerous is needed {command.dangerous_when or 'always'}.")
            lines.append("")
        lines.append("Options:")
        for param in command.all_params():
            value = "" if param.type == "boolean" else f" <{param.type}>"
            flags = " (required)" if param.required else ""
            lines.append(f"  --{param.name}{value}{flags}  {param.description}".rstrip())
        lines += ["", "Examples:", *[f"  {example}" for example in command.examples], ""]
        return "\n".join(lines)
    domain = positionals[0] if positionals else None
    lines = [
        f"xpedition-cli {__version__} - drive Xpedition Designer and Layout, built for agents",
        "",
        "Usage: xpedition-cli <command> [options]",
        "Agents: xpedition-cli reference is the full contract; doctor checks the machine.",
        "",
    ]
    for stage in STAGES:
        items = [
            item
            for item in commands()
            if item.stage == stage and (domain is None or item.path.split()[0] == domain)
        ]
        if not items:
            continue
        lines.append(f"{_STAGE_TITLES[stage]}:")
        for item in items:
            summary = item.description.split(";")[0].split(". ")[0]
            lines.append(f"  {item.path:<22} {summary}")
        lines.append("")
    lines += [
        "Every command takes --format, --json, --compact, --fields and --quiet.",
        "Writes run with --dry-run, then --confirm <token>; dangerous ones also --dangerous.",
        "Native commands need a licensed Xpedition on Windows and the [native] extra.",
        "",
    ]
    return "\n".join(lines)


def _output_preferences(options: dict[str, Any], argv: list[str]) -> tuple[str, bool]:
    """How to write an error: from the parsed options, or from argv when parsing failed."""
    if options:
        return str(options.get("format", "json")), bool(options.get("compact"))
    output_format = "json"
    for index, token in enumerate(argv):
        if token.startswith("--format="):
            output_format = token.partition("=")[2]
        elif token == "--format" and index + 1 < len(argv):
            output_format = argv[index + 1]
    if output_format not in {"json", "text", "raw"}:
        output_format = "json"
    return output_format, "--compact" in argv


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, OSError):
            pass
    started = time.perf_counter()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv == ["--version"]:
        sys.stdout.write(f"xpedition-cli {__version__}\n")
        return 0
    options: dict[str, Any] = {}
    command_text = "xpedition-cli"
    try:
        command, options = parse_argv(raw_argv)
        command_text = command.path if command else " ".join(options.get("_positionals") or [])
        suppress_progress(bool(options.get("quiet")))
        if options.get("help"):
            sys.stdout.write(help_text(command, options.get("_positionals") or []))
            return 0
        assert command is not None
        data = command.run(options)
        emit(
            success(data, started, options.get("fields")),
            options.get("format", "json"),
            bool(options.get("compact")),
        )
        exit_code = 0
    except CLIError as error:
        emit(failure(error, started), *_output_preferences(options, raw_argv))
        exit_code = error.exit_code
    except Exception as error:  # pragma: no cover - last-resort process boundary
        cli_error = CLIError(
            "E_UNKNOWN", "unexpected internal error", {"type": type(error).__name__}
        )
        emit(failure(cli_error, started), *_output_preferences(options, raw_argv))
        exit_code = cli_error.exit_code
    record(
        command_text or "xpedition-cli",
        exit_code,
        max(0, int((time.perf_counter() - started) * 1000)),
        raw_argv,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
