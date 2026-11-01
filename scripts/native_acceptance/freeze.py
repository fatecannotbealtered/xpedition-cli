"""Seal a new attempt of the existing acceptance entry; never invoke CAD here."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from checks import require, within

PACK = Path(__file__).resolve().parent
CHECKOUT = PACK.parents[1]


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pin(path):
    path = path.resolve()
    return {"source": str(path), "sha256": sha(path), "size": path.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--inputs-root", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--sdd-home", type=Path, required=True)
    parser.add_argument(
        "--resume-source",
        type=Path,
        help="Reuse a stopped source-library stage after independent readback "
        "and journal reconciliation",
    )
    parser.add_argument(
        "--resume-built",
        type=Path,
        help="Continue an existing independently checked two-part schematic",
    )
    parser.add_argument(
        "--source-cli",
        action="store_true",
        help="Use pinned source during development; final acceptance uses the frozen binary",
    )
    parser.add_argument(
        "--reconcile-package",
        type=Path,
        help="Reconcile a stopped package using its unchanged native error report",
    )
    parser.add_argument(
        "--orphan-only",
        action="store_true",
        help="Run the independent partial-placement case from a built checkpoint",
    )
    args = parser.parse_args()
    require(
        re.fullmatch(r"[A-Za-z0-9_-]+", args.attempt), "E_USAGE", "Use an ASCII attempt identifier"
    )
    run_root, inputs, sdd = [p.resolve() for p in (args.run_root, args.inputs_root, args.sdd_home)]
    require(str(run_root).isascii(), "E_SCOPE", "Native scratch paths must be ASCII")
    entry = within(run_root / "entries" / args.attempt, run_root)
    scratch = within(run_root / "xpedition-native-acceptance" / args.attempt, run_root)
    results = within(run_root / "xpedition-results" / args.attempt, run_root)
    resume = None
    require(
        not args.orphan_only or args.resume_built,
        "E_USAGE",
        "Independent orphan acceptance requires a checked built template",
    )
    require(not (args.resume_source and args.resume_built), "E_USAGE", "Select one checkpoint")
    if args.resume_source or args.resume_built:
        previous = within(
            (args.resume_source or args.resume_built).resolve(), run_root / "xpedition-results"
        )
        context = json.loads((previous / "run-context.json").read_text(encoding="utf-8"))
        scratch = within(
            Path(context["owned_projects"][0]).parent.parent,
            run_root / "xpedition-native-acceptance",
        )
        require(
            (args.resume_built or not (scratch / "target").exists())
            and not (scratch / "orphan").exists(),
            "E_REPLAY",
            "Source continuation requires untouched target and orphan stages",
        )
        label = "build-two-part-schematic" if args.resume_built else "author-source-library"
        replies = sorted(previous.glob(f"*-{label}-apply.stdout.json"))
        require(len(replies) == 1, "E_RESUME", "Expected one source-library apply record")
        reply = json.loads(replies[0].read_text(encoding="utf-8"))
        receipt = reply.get("error", {}).get("details", {}).get("receipt") or {}
        require(
            receipt.get("operation_id")
            and (
                receipt.get("extensions", {}).get("result", {}).get("netlist", {}).get("matches")
                is True
                if args.resume_built
                else receipt.get("extensions", {}).get("result", {}).get("ok") is True
            ),
            "E_RESUME",
            "Continuation requires an observed completed library backend",
        )
        resume = {
            "previous_results": str(previous),
            "operation_id": receipt["operation_id"],
            "apply_record": pin(replies[0]),
            "stage": "built" if args.resume_built else "source",
        }
    failed_package = None
    if args.reconcile_package:
        require(
            args.resume_built, "E_USAGE", "Package reconciliation requires the built checkpoint"
        )
        previous = within(args.reconcile_package.resolve(), run_root / "xpedition-results")
        replies = sorted(previous.glob("*-materialize-package-apply.stdout.json"))
        require(len(replies) == 1, "E_RESUME", "Expected one stopped package apply record")
        reply = json.loads(replies[0].read_text(encoding="utf-8"))
        receipt = reply.get("error", {}).get("details", {}).get("receipt") or {}
        result = receipt.get("extensions", {}).get("result") or {}
        registration_failure = (
            receipt.get("extensions", {}).get("handler_error", {}).get("code") == "E_SERVER"
        )
        require(
            receipt.get("operation_id")
            and (
                (
                    result.get("packaged") is False
                    and result.get("exit_code") == 5
                    and result.get("log_sha256")
                )
                or registration_failure
            ),
            "E_RESUME",
            "Expected an observed failed packager report",
        )
        failed_package = {
            "operation_id": receipt["operation_id"],
            "apply_record": pin(replies[0]),
            "stage": "registration" if registration_failure else "package",
        }
    require(
        not entry.exists() and not results.exists() and (resume or not scratch.exists()),
        "E_REPLAY",
        "A started or sealed attempt cannot be replaced",
    )
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    source_names = (
        subprocess.run(
            [
                "git",
                "-C",
                str(CHECKOUT),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            check=True,
            creationflags=flags,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    pinned = [
        pin(within(CHECKOUT / name, CHECKOUT))
        for name in sorted(set(source_names))
        if name and name != "HANDOFF.md"
    ]
    binary = CHECKOUT / "dist/xpedition-cli.exe"
    pinned.append(pin(Path(sys.executable)))
    if not args.source_cli:
        pinned.append(pin(binary))
    frozen = json.loads((inputs / "input-freeze.json").read_text(encoding="utf-8"))
    for name, expected in frozen["files"].items():
        path = within(inputs / name, inputs)
        require(
            sha(path) == expected, "E_HASH", "Frozen QA requirements changed", {"path": str(path)}
        )
        pinned.append(pin(path))
    pinned.append(pin(inputs / "input-freeze.json"))
    if resume:
        pinned.append(resume["apply_record"])
    if failed_package:
        pinned.append(failed_package["apply_record"])
    applications = {}
    import win32api

    for domain, subdir, filename in [
        ("schematic", "wv", "viewdraw.exe"),
        ("pcb", "wg", "ExpeditionPCB.exe"),
    ]:
        path = sdd / subdir / "win64/bin" / filename
        info = win32api.GetFileVersionInfo(str(path), "\\")

        def version(ms, ls):
            return ".".join(str(i) for i in (ms >> 16, ms & 65535, ls >> 16, ls & 65535))

        applications[domain] = {
            "path": str(path),
            "process_name": filename,
            "file_version": version(info["FileVersionMS"], info["FileVersionLS"]),
            "product_version_on_disk": version(info["ProductVersionMS"], info["ProductVersionLS"]),
            "sha256": sha(path),
            "expected_live_typelib": [155, 0],
        }
        pinned.append(pin(path))
    seeds = []
    template = sdd / "standard/templates/dxdesigner/XPED"
    for path in sorted(template.rglob("*")):
        if path.is_file():
            relative = path.relative_to(template)
            destination = (
                relative.relative_to("Default") if relative.parts[0] == "Default" else relative
            )
            seeds.append({**pin(path), "destination": str(destination)})
    lib = sdd / "standard/templates/dxdesigner/TemplateLibrary"
    ignored = {"Templates", "Work", "LogFiles", "ProjectBackup", "Thumbnail"}
    for path in sorted(lib.rglob("*")):
        if path.is_file() and not any(part in ignored for part in path.relative_to(lib).parts):
            seeds.append({**pin(path), "destination": str(Path("Library") / path.relative_to(lib))})
    require(
        len({item["destination"] for item in seeds}) == len(seeds),
        "E_HASH",
        "Seed destinations collide",
    )
    entry.mkdir(parents=True)
    for name in (
        "run.py",
        "checks.py",
        "processes.py",
        "engine.py",
        "native_worker.py",
        "adapter_entry.py",
        "identity_probe.py",
    ):
        shutil.copyfile(PACK / name, entry / name)
    (entry / "adapter.cmd").write_bytes(
        (
            '@echo off\r\n"' + sys.executable + '" -B "' + str(entry / "adapter_entry.py") + '"\r\n'
        ).encode("ascii")
    )
    helpers = [
        {"name": path.name, "sha256": sha(path)}
        for path in sorted(entry.iterdir())
        if path.name != "run.py"
    ]
    manifest = {
        "kind": "xpedition-explicit-user-bounded-entry/v1",
        "version": "2.0.0-dev.1",
        "checkout": str(CHECKOUT),
        "task_root": str(CHECKOUT),
        "run_root": str(run_root),
        "scratch_root": str(scratch),
        "results_root": str(results),
        "inputs_root": str(inputs),
        "account": "TCLKING\\example-user",
        "python_executable": sys.executable,
        "require_non_elevated": True,
        "require_unrestricted_token": True,
        "pywin32_distribution": importlib.metadata.version("pywin32"),
        "binary": str(binary),
        "binary_sha256": None if args.source_cli else sha(binary),
        "cli_mode": "source" if args.source_cli else "frozen_binary",
        "cli_prefix": [sys.executable, "-B", "-m", "xpedition_cli.main"]
        if args.source_cli
        else [str(binary)],
        "adapter_sha256": sha(CHECKOUT / "xpedition_cli/native_com_adapter.py"),
        "source_commit": subprocess.run(
            ["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            creationflags=flags,
        ).stdout.strip(),
        "maximum_seconds": 1800,
        "maximum_process_calls": 96,
        "applications": applications,
        "entry_files": helpers,
        "pinned_files": pinned,
        "seed_files": seeds,
        "orphan_operation": {
            "type": "place_component",
            "library": "QA20261006",
            "device_name": "QA_J1X02_P254",
            "symbol_name": "QA_CONN_1dbfb997",
            "refdes": "J_ORPHAN",
            "x": 500,
            "y": 400,
        },
        "operation_sequence": [
            "identity/hash/once-only/deadline gates",
            "owned source and target QA libraries",
            "two-part schematic and independent connections",
            "package/board/update",
            "units/placement/pads",
            "owned save/close/reopen",
            "single real placement with injected Refdes failure",
        ],
        "impact": {
            "only_write_roots": [str(scratch), str(results)],
            "existing_documents": "never close or replace unowned documents",
            "application_windows": "retained",
            "finite_children": "new test children only; never terminate existing CAD PIDs",
        },
        "known_unproven_stages": [
            "Native API reachability and required pad/unit/angle getters are checked at run time",
            "No native DRC, routing, bottom-side or CAM/DFM acceptance in these two cases",
        ],
        "derived_from": "the existing 2026-10-07 native-user-acceptance-v1 entry, "
        "preserved in the takeover evidence",
        "native_task_acceptance": "not_run",
        "resume_source": resume,
        "failed_package": failed_package,
        "orphan_only": args.orphan_only,
    }
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (entry / "manifest.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    launcher = entry / "run.py"
    launcher.write_text(
        re.sub(
            r'MANIFEST_SHA256 = "[^"]+"',
            'MANIFEST_SHA256 = "' + digest + '"',
            launcher.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "entry": str(launcher),
                "manifest_sha256": digest,
                "entry_sha256": sha(launcher),
                "pinned_files": len(pinned),
                "native_task_acceptance": "not_run",
            }
        )
    )


if __name__ == "__main__":
    main()
