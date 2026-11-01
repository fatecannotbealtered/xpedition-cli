"""Explicit, once-only user entry for the frozen Xpedition acceptance cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

MANIFEST_SHA256 = "555a5f37f893b4456bcb9340356a6df79930540257acf926ae32f205d0ded8be"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--run",
        action="store_true",
        help="Run the fixed owned native case once in the ordinary user terminal",
    )
    modes.add_argument(
        "--verify-only",
        action="store_true",
        help="Check pinned files and scope only; no COM or application call",
    )
    modes.add_argument(
        "--describe", action="store_true", help="Show the fixed account, scope, sequence and limits"
    )
    args = parser.parse_args()
    pack = Path(__file__).resolve().parent
    payload = (pack / "manifest.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != MANIFEST_SHA256:
        raise RuntimeError("E_HASH: fixed manifest bytes changed; execution refused")
    manifest = json.loads(payload)
    manifest["_sealed_manifest_sha256"] = MANIFEST_SHA256
    # Verify executable Python helpers BEFORE importing any engine/helper source.
    for item in manifest["entry_files"]:
        path = pack / item["name"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError("E_HASH: entry helper changed: " + item["name"])
    from engine import execute, verify

    if args.describe:
        print(
            json.dumps(
                {
                    key: manifest[key]
                    for key in (
                        "account",
                        "version",
                        "binary_sha256",
                        "adapter_sha256",
                        "scratch_root",
                        "results_root",
                        "maximum_seconds",
                        "operation_sequence",
                        "impact",
                        "known_unproven_stages",
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.verify_only:
        print(json.dumps(verify(manifest), ensure_ascii=False))
        return 0
    return execute(manifest)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": getattr(exc, "code", "E_ENTRY"),
                        "message": str(exc),
                        "details": getattr(exc, "details", {}),
                    },
                    "native_task_acceptance": "not_run",
                    "automatic_retry": False,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from None
