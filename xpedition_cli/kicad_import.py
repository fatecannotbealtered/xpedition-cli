"""Convert KiCad footprint libraries into a project's central library, in bulk.

    python -m xpedition_cli.kicad_import --project X.prj [--libraries A,B] [--root DIR]
                                         [--limit N] [--timeout SECONDS]

Every `.pretty` folder becomes one cell partition (see `native_com_adapter._kicad_import`).
This is the bulk step for testing a converted library; whether the conversion becomes a
public CLI command is a separate decision, so the entry point stays a module.
"""

from __future__ import annotations

import argparse
import json
import sys

from .backends import NativeBackend
from .errors import CLIError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="import KiCad footprint libraries")
    parser.add_argument("--project", required=True, help="the Xpedition .prj")
    parser.add_argument("--libraries", default="", help="comma-separated .pretty names")
    parser.add_argument("--root", default="", help="the KiCad footprints folder")
    parser.add_argument("--limit", type=int, default=0, help="only the first N libraries")
    parser.add_argument("--timeout", type=float, default=6 * 3600.0)
    args = parser.parse_args(argv)
    request: dict[str, object] = {"project": args.project}
    if args.libraries:
        request["libraries"] = [s.strip() for s in args.libraries.split(",") if s.strip()]
    if args.root:
        request["root"] = args.root
    if args.limit:
        request["limit"] = args.limit
    native = NativeBackend()
    native.require_implemented()
    try:
        result = native.invoke("kicad_import", request, timeout_seconds=args.timeout)
    except CLIError as exc:
        result = {"ok": False, "error": exc.code, "message": str(exc), "details": exc.details}
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
