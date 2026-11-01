"""Check the native artifact route without starting CAD or publishing anything.

This does not confer release or native task qualification. The tag workflow's
legacy onefile/single-executable transport is deliberately refused; a separate
manual artifact workflow collects the whole onedir-owner payload.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def route_checks(profile, archive_layout):
    return [
        {"name": "Windows native artifact runner", "passed": sys.platform == "win32"},
        {
            "name": "x64 CPython 3.12 artifact interpreter",
            "passed": platform.python_implementation() == "CPython"
            and sys.version_info[:2] == (3, 12)
            and sys.maxsize > 2**32,
        },
        {"name": "explicit onedir-owner profile", "passed": profile == "onedir-owner"},
        {"name": "whole onedir archive transport", "passed": archive_layout == "whole-onedir"},
    ]


def check(*, profile, archive_layout, phase="route", binary=None):
    checks = route_checks(profile, archive_layout)
    report = {
        "schema_version": "1.0",
        "ok": False,
        "phase": phase,
        "profile": profile,
        "archive_layout": archive_layout,
        "checks": checks,
        "dependencies": None,
        "archive": None,
        "release_qualification": "unqualified",
        "native_task_acceptance": "not_run",
        "scope": "Offline dependency/archive checks only; no CAD, launch, or publication",
    }
    if not all(row["passed"] for row in checks):
        return report
    if phase not in {"route", "dependencies", "archive"}:
        checks.append({"name": "known offline check phase", "passed": False})
        return report
    if phase == "route":
        report["ok"] = True
        return report
    # The freezer's own implementation checks exact installed distributions,
    # real pythoncom/win32com modules, and actual collected payloads. Do not
    # duplicate that list or infer pywin32 support from pywin32-ctypes.
    import build

    if phase == "dependencies":
        try:
            report["dependencies"] = build.dependency_preflight(windows=True)
        except SystemExit:
            checks.append({"name": "locked native dependency preflight", "passed": False})
        else:
            checks.append({"name": "locked native dependency preflight", "passed": True})
    elif binary is None:
        checks.append({"name": "explicit existing executable", "passed": False})
    else:
        inventory = build.archive_inventory(Path(binary), windows=True, profile=profile)
        report["archive"] = {
            "binary": inventory["binary"],
            "profile": inventory["profile"],
            "native_platform": inventory["native_platform"],
            "payload_file_count": len(inventory["collected_payloads"]),
            "source_module_count": len(inventory["source_bindings"]),
            "check_count": len(inventory["checks"]),
            "failed_checks": [row["name"] for row in inventory["checks"] if not row["passed"]],
            "owner_bundle_binding": inventory["owner_bundle_binding"],
        }
        checks.append(
            {"name": "actual archive and all onedir bytes", "passed": inventory["passed"]}
        )
    report["ok"] = all(row["passed"] for row in checks)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=("onefile", "onedir-owner"))
    parser.add_argument(
        "--archive-layout", required=True, choices=("single-executable", "whole-onedir")
    )
    parser.add_argument("--phase", choices=("route", "dependencies", "archive"), default="route")
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args(argv)
    try:
        report = check(
            profile=args.profile,
            archive_layout=args.archive_layout,
            phase=args.phase,
            binary=args.binary,
        )
    except (
        OSError,
        ValueError,
        TypeError,
        ImportError,
        RuntimeError,
        EOFError,
        SystemExit,
    ) as error:
        report = {
            "schema_version": "1.0",
            "ok": False,
            "phase": args.phase,
            "error_type": type(error).__name__,
            "release_qualification": "unqualified",
            "native_task_acceptance": "not_run",
        }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
