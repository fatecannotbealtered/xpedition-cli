"""Developer measurement entry; no public engineering or release qualification.

Stages a new immutable source copy (including generated contract resources),
launches only the fixed dedicated-owner profiles, and observes that same owner.
No historical operation is adopted. No CAD is terminated or replayed here.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

PRIVATE = {"epoch", "execution_epoch", "connection_epoch", "endpoint", "objref", "ticket"}


def safe(value):
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items() if key not in PRIVATE}
    if isinstance(value, list):
        return [safe(item) for item in value]
    return value


def write_new(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def validate_staged_source(case):
    """Require a complete regular-file snapshot before any package import."""
    source, manifest = case / "source", case / "source-binding.json"
    for path in (case, source, manifest):
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("staged_source_reparse_refused")
    with manifest.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("staged_source_manifest_byte_limit")
    binding = json.loads(raw)
    expected = binding.get("working_files")
    if (
        not re.fullmatch(r"[a-f0-9]{40}", str(binding.get("base_commit")))
        or binding.get("exe_used") is not False
        or binding.get("public_engineering_qualification") is not False
        or not isinstance(expected, dict)
        or not 1 <= len(expected) <= 4096
    ):
        raise ValueError("staged_source_binding_invalid")
    for name, digest in expected.items():
        path = PurePosixPath(name) if isinstance(name, str) else None
        if (
            path is None
            or path.is_absolute()
            or path.as_posix() != name
            or "\\" in name
            or ":" in name
            or ".." in path.parts
            or not path.parts
            or path.parts[0] not in {"xpedition_cli", "contract"}
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise ValueError("staged_source_member_invalid")
    required = {
        "xpedition_cli/__init__.py",
        "xpedition_cli/designer_native_owner.py",
        "xpedition_cli/designer_native_launch.py",
        "xpedition_cli/startup_input_seal.py",
        "contract/driver-v2/product_contract.json",
    }
    if not required <= expected.keys():
        raise ValueError("staged_source_required_member_missing")
    found, pending, total = {}, [source], 0
    while pending:
        for path in pending.pop().iterdir():
            info = path.lstat()
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("staged_source_reparse_refused")
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
                raise ValueError("staged_source_nonregular_or_oversize_member")
            name = path.relative_to(source).as_posix()
            if name not in expected:
                raise ValueError("staged_source_extra_member")
            data = path.read_bytes()
            total += len(data)
            if total > 64 * 1024 * 1024 or len(found) >= 4096:
                raise ValueError("staged_source_resource_limit")
            found[name] = hashlib.sha256(data).hexdigest()
    if found != expected:
        raise ValueError("staged_source_missing_or_changed_member")
    return source.resolve()


def require_staged_imports(source):
    package = source / "xpedition_cli"
    for name, module in tuple(sys.modules.items()):
        if name == "xpedition_cli" or name.startswith("xpedition_cli."):
            origin = getattr(module, "__file__", None)
            if not origin or not Path(origin).resolve().is_relative_to(package):
                raise ValueError("nonstaged_package_import_refused")


def observe_command_safely(owner, ticket, command_id, seconds):
    """Read an existing ID, including older immutable snapshots, without replay."""
    if not re.fullmatch(r"owner-command-[a-f0-9]{32}", str(command_id)) or not 0 <= seconds <= 2:
        raise ValueError("bounded_existing_owner_command_required")
    try:
        answer = owner.observe_owner_command(ticket, command_id, timeout_seconds=seconds)
        if answer.get("command_id") != command_id:
            raise ValueError("owner_command_observer_identity_mismatch")
        return {"status": "unknown", "request_replayed": False, **answer}
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "command_id": command_id,
            "status": "unknown",
            "observation": "unobserved",
            "reason": "owner_command_observation_incomplete",
            "initial_observation": None,
            "observations": [],
            "native_writer_admitted": False,
            "request_replayed": False,
        }


def stage(repo, workspace, name):
    case = workspace / name
    case.mkdir(parents=True, exist_ok=False)
    source = case / "source"
    source.mkdir()
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    raw = subprocess.check_output(["git", "archive", base, "xpedition_cli", "contract"], cwd=repo)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for entry in archive.getmembers():
            target = source / entry.name
            if not target.resolve().is_relative_to(source.resolve()) or not (
                entry.isfile() or entry.isdir()
            ):
                raise ValueError("source_archive_nonregular_entry_refused")
        archive.extractall(source, filter="data")
    # Record working implementation too; git HEAD alone may precede a measured
    # fix. No config, journal, credentials or runtime directory is copied.
    package = repo / "xpedition_cli"
    for path in package.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("source_reparse_entry_refused")
        target = source / "xpedition_cli" / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    # Use matching working generated resources rather than mismatched base bytes.
    for path in (repo / "contract").rglob("*"):
        if path.is_file():
            target = source / "contract" / path.relative_to(repo / "contract")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    files = {
        str(path.relative_to(source)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(source.rglob("*"))
        if path.is_file()
    }
    write_new(
        case / "source-binding.json",
        {
            "base_commit": base,
            "working_files": files,
            "exe_used": False,
            "public_engineering_qualification": False,
        },
    )
    return {"source_staged": True, "cad_called": False, "file_count": len(files)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "stage",
            "launch-empty",
            "launch-new-project",
            "observe",
            "observe-command",
            "identity",
            "read_project",
            "save_close_project",
            "quit_empty",
        ),
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--installation-root", type=Path)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--command-id")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", args.case):
        raise ValueError("plain_new_case_name_required")
    workspace, case = args.workspace.resolve(), args.workspace.resolve() / args.case
    if args.action == "stage":
        print(json.dumps(stage(args.repo.resolve(), workspace, args.case)))
        return 0
    source = validate_staged_source(case)
    require_staged_imports(source)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    from xpedition_cli import designer_native_owner as O
    from xpedition_cli.designer_native_launch import PROJECT_SCHEME, SCHEME

    require_staged_imports(source)

    root = case / "owner"
    if args.action.startswith("launch-"):
        if args.installation_root is None:
            raise ValueError("explicit_audited_installation_root_required")
        plan = {
            "scheme": PROJECT_SCHEME if args.action == "launch-new-project" else SCHEME,
            "installation_root": str(args.installation_root.resolve()),
            "runtime_directory": str(root / "startup"),
        }
        started = time.monotonic()
        answer = O.launch_measurement_owner(root, plan, observation_seconds=0.5)
        report = {
            "action": args.action,
            "wall_seconds": time.monotonic() - started,
            "observation": safe(answer["observation"]),
            "native_writer_admitted": False,
        }
        write_new(case / "launch-first-observation.json", report)
        print(json.dumps(report))
        return 0
    intent = json.loads((root / "intent.json").read_text(encoding="utf-8"))["payload"]
    ticket = O.OwnerTicket(root, intent["execution_epoch"])
    if args.action == "observe-command":
        print(json.dumps(safe(observe_command_safely(O, ticket, args.command_id, args.seconds))))
        return 0
    if args.action == "observe":
        if not 0 <= args.seconds <= 60:
            raise ValueError("bounded_observation_required")
        deadline, previous = time.monotonic() + args.seconds, None
        while True:
            observation = O.observe_owner(ticket)
            signature = (observation.get("event_count"), observation.get("owner_state"))
            if signature != previous:
                print(json.dumps(safe(observation)), flush=True)
                previous = signature
            if time.monotonic() >= deadline:
                return 0
            time.sleep(0.25)
    if O.observe_owner(ticket).get("connection_observation") != "session_specific_native":
        raise O.OwnerError("native_connection_unobserved_no_command_dispatched")
    answer = O.submit_owner_command(ticket, args.action, observation_seconds=0.5)
    # Raw evidence, including the private correlation epoch, stays inside the
    # same private owner directory. The console never exports that capability.
    write_new(root / "commands" / answer["command_id"] / "client-first-observation.json", answer)
    print(json.dumps(safe(answer)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
