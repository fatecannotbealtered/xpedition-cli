"""Explicit synthetic ACL measurement, never a CAD/native target invocation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from startup_input_seal_native import _foundation_probe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--new-case", choices=("foundation-02", "foundation-03"))
    args = parser.parse_args()
    result = _foundation_probe(
        args.root, case_name=args.new_case or "foundation", create_root=args.new_case is None
    )
    root = Path(args.root)
    (root / ((args.new_case or "foundation") + "-result.json")).write_text(
        json.dumps(result, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("protection_pass") is True else 21


if __name__ == "__main__":
    raise SystemExit(main())
