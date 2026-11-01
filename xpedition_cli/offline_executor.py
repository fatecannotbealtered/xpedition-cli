"""Internal source-only child entry. No dynamic factory, shell or CAD import."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xpedition_cli.execution_lifecycle import LifecycleError, run_child


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--execution", required=True)
    arguments = parser.parse_args(argv)
    try:
        return run_child(arguments.root, arguments.execution)
    except (LifecycleError, OSError, ValueError, KeyError):
        # The private owner journal is authoritative; stderr carries no payload,
        # capability, local path, arbitrary exception text or credential.
        print("offline_executor_incomplete", file=sys.stderr)
        return 21


if __name__ == "__main__":
    raise SystemExit(main())
