"""Check pinned SDK bytes, runtime generation and optional complete design evidence."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(spec_root: Path | None = None, *, release: bool = False) -> dict:
    from xpedition_cli import __version__
    from xpedition_cli.driver import runtime

    errors = []
    saved = os.environ.get("XPEDITION_CLI_CONFIG_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="xpedition-contract-check-") as temporary:
            os.environ["XPEDITION_CLI_CONFIG_DIR"] = temporary
            # Version/reference are SDK builtins; no native adapter methods execute.
            with runtime(state_dir=Path(temporary) / "state") as cli:
                reference = cli.run(["reference", "--index"]).envelope
                if not reference["ok"]:
                    raise ValueError("Public reference rejected its generated contract")
                data = reference["data"]
                readiness = cli.reference["release_readiness"]
    finally:
        if saved is None:
            os.environ.pop("XPEDITION_CLI_CONFIG_DIR", None)
        else:
            os.environ["XPEDITION_CLI_CONFIG_DIR"] = saved
    if data["version"] != __version__:
        errors.append("Public contract version differs from loaded Python source")
    if release and readiness["level"] not in {"stable", "beta"}:
        errors.append("Release is blocked: " + readiness["reason"])
    design = None
    generation = "not_checked_without_explicit_pinned_spec"
    if spec_root:
        from check_development_design import inspect_design

        design = inspect_design(spec_root)
        errors.extend(design["errors"])
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(spec_root / "scripts/generate_product.py"),
                "--registry",
                str(ROOT / "registry.json"),
                "--output",
                str(ROOT / "contract/driver-v2"),
                "--check",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        generated = json.loads(result.stdout)
        generation = "passed" if result.returncode == 0 and generated["ok"] else "failed"
        if generation == "failed":
            errors.append(generated.get("error", {}).get("message", "Runtime generation failed"))
    return {
        "ok": not errors,
        "version": __version__,
        "public_commands": len(data["commands"]),
        "sdk_bytes": "verified_against_37_file_lock",
        "runtime_generation": generation,
        "development_design": design["counts"] if design else "not_checked",
        "release_readiness": readiness,
        "native_task_acceptance": "not_run",
        "errors": errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec-root", type=Path, default=os.environ.get("XPEDITION_DRIVER_SPEC_ROOT")
    )
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = check(Path(args.spec_root) if args.spec_root else None, release=args.release)
    except (OSError, ValueError, KeyError) as error:
        result = {"ok": False, "errors": [str(error)], "native_task_acceptance": "not_run"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
