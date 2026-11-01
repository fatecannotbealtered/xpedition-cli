"""Create a new private delivery directory; never copy authorization or runtime state.

This is a builder utility, not an additional product command or a release path.
The public launcher remains scripts/run-trial.ps1 and the readiness gate remains.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pin(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def stage(binary: Path, inventory: Path, destination: Path) -> dict:
    binary, inventory, destination = (path.resolve() for path in (binary, inventory, destination))
    actual = pin(binary)
    report = json.loads(inventory.read_text(encoding="utf-8"))
    if report.get("passed") is not True or any(
        report.get("binary", {}).get(key) != value for key, value in actual.items()
    ):
        raise ValueError("A passing actual archive inventory bound to this binary is required")
    launcher = ROOT / "scripts/run-trial.ps1"
    text = launcher.read_text(encoding="utf-8")
    match_hash = re.search(r"\$trialExpectedExeSha = '([0-9a-f]{64})'", text)
    match_bytes = re.search(r"\$trialExpectedExeBytes = (\d+)", text)
    if (
        not match_hash
        or match_hash[1] != actual["sha256"]
        or not match_bytes
        or int(match_bytes[1]) != actual["bytes"]
    ):
        raise ValueError("Public launcher does not bind the selected executable")
    if destination.exists():
        raise FileExistsError("A staged candidate is immutable; use a new destination")
    destination.mkdir(parents=True)
    records = []

    def copy(source: Path, relative: str):
        info = source.lstat()
        if source.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError("No linked delivery input: " + str(source))
        if Path(relative).name.lower() in {
            "scope.json",
            "session.json",
            "operations.db",
            "operations.sqlite",
            ".env",
        }:
            raise ValueError("Runtime authority/state must stay outside the delivery")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(source.read_bytes())
        records.append({"path": Path(relative).as_posix(), **pin(target)})

    copy(binary, "bin/xpedition-cli.exe")
    copy(inventory, "inventory/archive-inventory.json")
    copy(launcher, "scripts/run-trial.ps1")
    for name in ("LICENSE", "NOTICE.md"):
        copy(ROOT / name, name)
    copy(ROOT / "contract/driver-v2/reference.json", "reference.json")
    copy(ROOT / "contract/driver-v2/reference.json", "examples/reference.json")
    for name in ("scope.schema.json", "common.schema.json"):
        copy(ROOT / "xpedition_cli/_vendor/spec/schemas" / name, "schemas/" + name)
    for folder in ("skills", "docs", "examples", "schemas"):
        for source in sorted((ROOT / folder).rglob("*")):
            if source.is_file() and source.suffix in {".md", ".json", ".ps1", ".png", ".svg"}:
                relative = source.relative_to(ROOT).as_posix()
                if (destination / relative).exists():
                    continue
                copy(source, relative)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    readme = (
        "# Private representative-engineering candidate\n\n"
        "唯一产品入口：scripts/run-trial.ps1。使用当前用户授权、新任务配置和公开副本登记。\n\n"
        f"EXE: {actual['bytes']} bytes; SHA-256 `{actual['sha256']}`.\n\n"
        "Start at docs/PHONE-READINESS-20261024.md and examples/native-readiness-workflow.md. "
        "The root and examples/reference.json describe this candidate. schemas/ contains the "
        "public task-scope format. Historical R18r10 documentation does not qualify "
        "this binary.\n\n"
        "No development Python is required by the product entry. Legal CAD/COM/licensing and "
        "the stated Windows environment remain external. Runtime scope, credentials, journal "
        "and session state are excluded. Native acceptance and remaining blockers are recorded "
        "outside this immutable package. Keep2.0.0-dev.1,65leaves,private and unpublishable. "
        "No hardware/manufacturing sign-off, full-board autorouting or BatchDRC qualification.\n"
    )
    (destination / "README.md").write_text(readme, encoding="utf-8")
    records.append({"path": "README.md", **pin(destination / "README.md")})
    manifest = {
        "schema_version": "1.0",
        "kind": "private-native-repair-candidate",
        "source_commit": commit,
        "binary": actual,
        "files": records,
        "qualification": "No native qualification is inferred from packaging or build inclusion",
        "release_readiness": "unpublishable",
        "scope_or_journal_copied": False,
    }
    (destination / "package-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"package": str(destination), "files": len(records), "binary": actual}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.binary, args.inventory, args.destination)))


if __name__ == "__main__":
    main()
