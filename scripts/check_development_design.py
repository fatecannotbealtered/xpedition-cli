"""Check surveyed design and migration preview without contacting Xpedition.

Requires a read-only tool-driver-cli-spec checkout matching development/spec-pin.json.
Generated artifacts are a preview only; this script never imports native adapters,
loads handlers, creates product config, or invokes an application.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("contract_io", "check_spec", "check_product_design", "generate_product")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_spec_pin(spec_root: Path, repo: Path = ROOT) -> dict:
    """Reject another version or changed tool/schema bytes, without editing the spec."""
    pin = load(repo / "development/spec-pin.json")
    spec_root = spec_root.resolve(strict=True)
    for name, expected in pin["sha256"].items():
        path = spec_root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"pinned specification artifact missing or changed: {name}")
    if (spec_root / ".git").exists():
        head = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(spec_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if head.returncode or head.stdout.strip() != pin["commit"]:
            raise ValueError("specification checkout does not match the recorded commit")
    elif pin.get("source_kind") != "exact_git_archive" or len(pin["sha256"]) < 100:
        raise ValueError(
            "specification must be a matching checkout or the complete byte-pinned Git archive"
        )
    return {
        "commit": pin["commit"],
        "local_version": pin["local_version"],
        "checked_files": len(pin["sha256"]),
        "publication_status": pin["publication_status"],
        "source_kind": pin.get("source_kind", "git_checkout"),
    }


@contextmanager
def spec_tools(spec_root: Path, repo: Path = ROOT):
    pin = check_spec_pin(spec_root, repo)
    saved = {name: sys.modules.pop(name, None) for name in MODULES}
    old_path = list(sys.path)
    sys.path.insert(0, str(spec_root / "scripts"))
    try:
        checker = importlib.import_module("check_product_design")
        generator = importlib.import_module("generate_product")
        yield checker, generator, pin
    finally:
        sys.path[:] = old_path
        for name in MODULES:
            sys.modules.pop(name, None)
            if saved[name] is not None:
                sys.modules[name] = saved[name]


def compatibility_errors(carrier: dict, baseline: dict, runtime: list[dict]) -> list[str]:
    expected = {c["path"]: c for c in baseline["commands"]}
    actual = {c["path"]: c for c in runtime}
    errors = []
    if expected != actual:
        errors.append(
            "current command paths, parser flags/codecs, examples or declarations "
            "differ from the compatibility baseline"
        )
    described = {
        c["path"]: {k: v for k, v in c.items() if k != "driver"} for c in carrier["commands"]
    }
    if described != actual:
        errors.append("current design carrier does not describe the actual legacy registry")
    return errors


def source_mapping_errors(carrier: dict, repo: Path) -> list[str]:
    errors = []
    record = carrier["design"]
    artifacts = {e["id"]: e for e in record["evidence"]}
    inventory = load(repo / "development/evidence/capability-task-map.json")
    recorded_ids = {"c" + c["id"][1:].lower() for c in inventory["capabilities"]}
    if {c["id"].split("-", 1)[0] for c in record["capabilities"]} != recorded_ids:
        errors.append("design denominator no longer contains every surveyed capability group")
    symbols = {}
    router_artifact = next(
        (
            e
            for e in artifacts.values()
            if e["kind"] == "implementation" and e["path"] == "xpedition_cli/native_com_adapter.py"
        ),
        None,
    )
    methods = set()
    if router_artifact:
        router = ast.parse((repo / router_artifact["path"]).read_text(encoding="utf-8"))
        dispatch = next(
            n for n in router.body if isinstance(n, ast.FunctionDef) and n.name == "dispatch"
        )
        methods = {
            n.value
            for n in ast.walk(dispatch)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
    for task in record["tasks"]:
        for entry in task["adapter_entries"]:
            artifact = artifacts.get(entry["artifact_ref"])
            if not artifact or artifact["kind"] != "implementation":
                continue  # The official checker reports this with the cross-reference.
            path = repo / artifact["path"]
            if path not in symbols:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                symbols[path] = {
                    n.name
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            symbol = entry["entrypoint"].split(" [", 1)[0].split(".")[-1]
            if symbol not in symbols[path]:
                errors.append(
                    f"{entry['command_path']}: adapter entrypoint {symbol} "
                    "absent from the hashed source"
                )
            if " [request.method=" in entry["entrypoint"]:
                method = entry["entrypoint"].split(" [request.method=", 1)[1].removesuffix("]")
                # Protocol names live in dispatch, not in the implementation helper.
                if method not in methods:
                    errors.append(
                        f"{entry['command_path']}: request.method {method} absent from "
                        "the hashed dispatch protocol source"
                    )
    return errors


def inspect_design(spec_root: Path, repo: Path = ROOT) -> dict:
    """Schema/semantic/source checks plus generator build, all read-only."""
    repo = repo.resolve(strict=True)
    current = load(repo / "development-current-registry.json")
    canonical = load(repo / "development-registry.json")
    boundary = load(repo / "development/migration-boundary.json")
    baseline = load(repo / "development/current-command-baseline.json")
    sys.path.insert(0, str(repo))
    try:
        from xpedition_cli.cli.registry import commands

        runtime = [c.contract() for c in commands()]
    finally:
        sys.path.pop(0)
    errors = compatibility_errors(current, baseline, runtime)
    if load(repo / "development/current-design.json") != current["design"]:
        errors.append("standalone current-design.json differs from the current carrier design")
    with spec_tools(spec_root, repo) as (checker, generator, pin):
        reports = {}
        for name, carrier in (("current", current), ("preview", canonical)):
            report = checker.check_design(carrier, registry_dir=repo, require=True)
            reports[name] = report
            errors.extend(f"{name}: {e}" for e in report["errors"])
            if not report["errors"]:
                errors.extend(f"{name}: {e}" for e in source_mapping_errors(carrier, repo))
        build = generator.build_contract(canonical, registry_dir=repo)
        generated = {c["path"] for c in build["reference"]["commands"]}
    declared = {c["path"] for c in canonical["commands"]}
    current_paths = {c["path"] for c in runtime}
    inherited = set(boundary["existing_builtin_paths"])
    planned = set(boundary["planned_unimplemented_paths"])
    implemented = set(boundary["implemented_sdk_paths"])
    if declared != current_paths - inherited or generated != current_paths | implemented or planned:
        errors.append(
            "preview/custom/generated command boundary differs from the current tree "
            "plus implemented SDK builtins"
        )
    if planned & current_paths:
        errors.append("an unimplemented preview operation path was exposed by the current registry")
    for c in canonical["commands"]:
        raw = next(r for r in runtime if r["path"] == c["path"])
        before = {
            (p["name"], p["required"], p["multiple"])
            for p in raw["params"]
            if p["name"] not in {"dry-run", "confirm", "dangerous"}
        }
        after = {(p["name"], p["required"], p["multiple"]) for p in c["params"]}
        if before != after:
            errors.append(
                f"preview changes the named/required/repeatable parameters of {c['path']}"
            )
    return {
        "ok": not errors,
        "scope": "implemented_local_tree_and_surveyed_development_design",
        "spec_pin": pin,
        "counts": {
            "current_commands": len(generated),
            "current_handler_declarations": len(current_paths),
            "current_domain_commands": sum(
                m["role"] == "domain" for m in current["design"]["commands"]
            ),
            "preview_custom_commands": len(declared),
            "generated_preview_commands": len(generated),
            "native_runs": sum(e["kind"] == "real_run" for e in current["design"]["evidence"]),
            "native_task_tests_not_run": sum(
                t["status"] == "not_run" for t in current["design"]["tests"]
            ),
        },
        "coverage": {name: report["coverage"] for name, report in reports.items()},
        "compatibility": boundary["compatibility"]
        if not compatibility_errors(current, baseline, runtime)
        else "drift",
        "reports": reports,
        "errors": errors,
        "limitations": [
            "Design validation does not authenticate source documents "
            "or survey/license an application.",
            (
                "The candidate uses the published SDK; its tree is a documented "
                "breaking migration. Native task acceptance is pending."
            ),
            "Offline fixture regression and declared-command FCC "
            "cannot prove whole-application coverage.",
            "No application, handler, COM object, license check or native adapter is invoked.",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-root", default=os.environ.get("XPEDITION_DRIVER_SPEC_ROOT"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not args.spec_root:
        parser.error(
            "--spec-root or XPEDITION_DRIVER_SPEC_ROOT must name "
            "the pinned read-only local checkout"
        )
    try:
        report = inspect_design(Path(args.spec_root))
        if args.report:
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        value = {k: v for k, v in report.items() if k != "reports"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        value = {
            "ok": False,
            "errors": [str(exc), *getattr(exc, "errors", [])],
            "scope": "development_design_and_current_compatibility_only",
        }
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False))
    return 0 if value["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
