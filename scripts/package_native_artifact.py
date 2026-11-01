"""Package and re-read every onedir byte; this is not a release/trial package.

The source executable is never run or changed. A fresh output directory is
required. The existing native archive/source auditor is called directly, so a
stale JSON report cannot qualify a changed binary or omit a native DLL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAILURE_REASONS = {
    "native_artifact_file_exceeds_observed_size",
    "source_commit_not_full_sha",
    "native_artifact_manifest_not_bounded_plain_file",
    "native_artifact_output_must_be_outside_bundle",
    "native_artifact_output_must_be_new",
    "native_artifact_archive_audit_failed",
    "native_artifact_changed_after_archive_audit",
    "native_artifact_source_changed_while_packaging",
    "native_artifact_zip_member_set_mismatch",
    "native_artifact_zip_crc_mismatch",
    "native_artifact_zip_payload_mismatch",
    "native_artifact_source_changed_after_packaging",
}

from xpedition_cli.designer_frozen_binding import (  # noqa: E402
    MANIFEST_NAME,
    MAX_MANIFEST_BYTES,
    collect_bundle_files,
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("native_artifact_file_exceeds_observed_size")
    return raw


def _git_source():
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, timeout=30, check=True
    )
    head = done.stdout.decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise ValueError("source_commit_not_full_sha")
    return head


def _manifest_row(root):
    path = root / MANIFEST_NAME
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("native_artifact_manifest_not_bounded_plain_file")
    raw = _read(path, MAX_MANIFEST_BYTES)
    return {"path": MANIFEST_NAME, "bytes": len(raw), "sha256": _sha(raw)}


def package(binary, output):
    import build

    binary, output = Path(binary).resolve(), Path(output).absolute()
    if output == binary.parent or binary.parent in output.parents:
        raise ValueError("native_artifact_output_must_be_outside_bundle")
    if output.exists():
        raise ValueError("native_artifact_output_must_be_new")
    inventory = build.archive_inventory(binary, windows=True, profile="onedir-owner")
    if not inventory["passed"]:
        raise ValueError("native_artifact_archive_audit_failed")
    root = binary.parent
    before = collect_bundle_files(root)
    if before != inventory["collected_payloads"]:
        raise ValueError("native_artifact_changed_after_archive_audit")
    manifest = _manifest_row(root)
    rows = sorted([*before, manifest], key=lambda row: row["path"])
    head = _git_source()
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "xpedition-cli-native-artifact.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for row in rows:
            raw = _read(root / row["path"], row["bytes"])
            if len(raw) != row["bytes"] or _sha(raw) != row["sha256"]:
                raise ValueError("native_artifact_source_changed_while_packaging")
            zipped.writestr("xpedition-cli/" + row["path"], raw)
    # Exact member set, CRC, sizes, and content digests are checked from the ZIP.
    # Presence of xpedition-cli.exe alone is deliberately insufficient.
    with zipfile.ZipFile(archive) as zipped:
        expected = {"xpedition-cli/" + row["path"]: row for row in rows}
        if len(zipped.infolist()) != len(expected) or set(zipped.namelist()) != set(expected):
            raise ValueError("native_artifact_zip_member_set_mismatch")
        if zipped.testzip() is not None:
            raise ValueError("native_artifact_zip_crc_mismatch")
        for name, row in expected.items():
            with zipped.open(name) as stream:
                raw = stream.read(row["bytes"] + 1)
            if len(raw) != row["bytes"] or _sha(raw) != row["sha256"]:
                raise ValueError("native_artifact_zip_payload_mismatch")
    if collect_bundle_files(root) != before or _manifest_row(root) != manifest:
        raise ValueError("native_artifact_source_changed_after_packaging")
    raw_archive = archive.read_bytes()
    report = {
        "schema_version": "1.0",
        "ok": True,
        "source_commit": head,
        "source_compile_matches": True,
        "profile": "onedir-owner",
        "binary": inventory["binary"],
        "archive": {"name": archive.name, "bytes": len(raw_archive), "sha256": _sha(raw_archive)},
        "payload_files": rows,
        "archive_checks": len(inventory["checks"]),
        "whole_bundle_zip_verified": True,
        "input_bundle_unchanged": True,
        "native_task_acceptance": "not_run",
        "release_qualification": "unqualified",
        "offline_smoke": "not_checked_by_packager",
        "trial_launcher_included": False,
        "scope": (
            "Offline native build artifact only; no CAD, launch, release, or trial qualification"
        ),
    }
    for name, value in (("archive-inventory.json", inventory), ("NATIVE-ARTIFACT.json", report)):
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = package(args.binary, args.output)
    except (
        OSError,
        ValueError,
        ImportError,
        RuntimeError,
        EOFError,
        subprocess.SubprocessError,
        SystemExit,
        zipfile.BadZipFile,
    ) as error:
        report = {
            "schema_version": "1.0",
            "ok": False,
            "error_type": type(error).__name__,
            "reason": str(error) if str(error) in FAILURE_REASONS else None,
            "native_task_acceptance": "not_run",
            "release_qualification": "unqualified",
        }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
