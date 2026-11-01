"""Static caller/observer fixture; no arbitrary command or handler is accepted."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xpedition_cli.execution_lifecycle import launch_fixture, observe_execution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("launch", "observe"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--execution")
    parser.add_argument("--epoch")
    parser.add_argument("--birth")
    args = parser.parse_args()
    if args.mode == "launch":
        result = launch_fixture(args.root, observation_budget_seconds=0.05)
    else:
        result = observe_execution(
            args.root, args.execution, expected_epoch=args.epoch, expected_birth=args.birth
        )
    print(json.dumps({"caller_pid": os.getpid(), "observation": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
