"""Bounded real writer/observer children; file IPC only, never COM or CAD."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xpedition_cli import execution_lifecycle as L


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--role", choices=("writer", "observer"), required=True)
    args = parser.parse_args()
    root = Path(args.root)
    key = (root / "key.bin").read_bytes()
    intent = json.loads((root / "intent.json").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 8
    result = {
        "role": args.role,
        "status": "incomplete",
        "cad_used": False,
        "original_operation_status": "unknown",
    }

    def wait_for(name):
        while not (root / name).exists():
            if (root / "stop").exists() or time.monotonic() >= deadline:
                raise TimeoutError("bounded fixture observation ended")
            time.sleep(0.002)

    def validate_raw(raw):
        wrapped = json.loads(raw)
        expected = hmac.new(key, L._canonical(wrapped["payload"]), hashlib.sha256).hexdigest()
        assert hmac.compare_digest(expected, wrapped["mac"])
        assert wrapped["payload"]["execution_epoch"] == intent["execution_epoch"]
        assert wrapped["payload"]["sequence"] >= 1

    try:
        if args.role == "writer":
            writer = L.EventWriter(root, key, intent)
            writer.append(
                "initial_unknown", original_operation_status="unknown", cad_verified=False
            )
            (root / "writer-ready").write_bytes(b"ready\n")
            wait_for("reader-held")
            for index in range(32):
                if (root / "stop").exists() or time.monotonic() >= deadline:
                    raise TimeoutError("bounded fixture writer ended")
                writer.append(
                    "read_only_observation",
                    observation_index=index,
                    original_operation_status="unknown",
                    cad_verified=False,
                )
                time.sleep(0.001)
            result.update(status="observed", append_count=writer.sequence)
        else:
            wait_for("writer-ready")
            observations, incomplete = 0, 0
            with L._snapshot_reader(root / "snapshot.json") as original:
                old = original.read(L.MAX_RECORD_BYTES + 1)
                validate_raw(old)
                (root / "reader-held").write_bytes(b"shared-delete reader remains open\n")
                while not (root / "writer-done").exists() or observations < 5:
                    if (root / "stop").exists() or time.monotonic() >= deadline:
                        raise TimeoutError("bounded fixture observer ended")
                    try:
                        value, _ = L._read(root / "snapshot.json", key)
                        assert value["execution_epoch"] == intent["execution_epoch"]
                        observations += 1
                    except (FileNotFoundError, PermissionError):
                        incomplete += 1  # A read-open gap remains unknown, never success.
                    time.sleep(0.001)
                original.seek(0)
                assert original.read(L.MAX_RECORD_BYTES + 1) == old
            result.update(
                status="observed",
                valid_snapshots=observations,
                read_open_incomplete=incomplete,
                original_reader_full_version=True,
            )
    except BaseException as error:
        result.update(
            error_type=type(error).__name__,
            errno=getattr(error, "errno", None),
            winerror=getattr(error, "winerror", None),
        )
    finally:
        if args.role == "writer":
            (root / "writer-done").write_bytes(b"writer terminal observation\n")
        with (root / (args.role + "-result.json")).open("x", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "observed" else 21


if __name__ == "__main__":
    raise SystemExit(main())
