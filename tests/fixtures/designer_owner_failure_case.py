"""Fixed non-CAD failure fixture. No real COM module is imported.

Only new synthetic resource locks are released by the teardown flag. The unknown
spawn case aborts this test wrapper with exit 23; that is not product cancellation
or a native cleanup success. There is no terminate/kill path.
"""

from __future__ import annotations

import argparse
import json
import msvcrt
import os
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xpedition_cli import designer_native_launch as N
from xpedition_cli import designer_native_owner as O


class FixtureAbort(BaseException):
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument(
        "--scenario",
        choices=("spawned_unresolved", "unknown_spawn", "writer_failure"),
        required=True,
    )
    args = parser.parse_args()
    root = Path(args.root)
    report = {
        "fixture": args.scenario,
        "launch_calls": 0,
        "real_com_imported": False,
        "native_cleanup_qualified": False,
        "fixture_io_failures": [],
    }

    def capture_io_failure(point, error):
        report["fixture_io_failures"].append(
            {
                "callpoint": point,
                "writer_phase": report.get("active_writer_phase"),
                "launch_calls_at_failure": report["launch_calls"],
                "errno": error.errno if type(error.errno) is int else None,
                "winerror": getattr(error, "winerror", None)
                if type(getattr(error, "winerror", None)) is int
                else None,
            }
        )

    class ObservedOs:
        """Only this fixture's L.os binding changes; never mutate global os."""

        def __getattr__(self, name):
            return getattr(os, name)

        def replace(self, source, destination):
            try:
                return os.replace(source, destination)
            except OSError as error:
                point = (
                    "journal.snapshot_replace"
                    if Path(destination).name == "snapshot.json"
                    else "journal.other_replace"
                )
                capture_io_failure(point, error)
                raise  # Product failure remains a failure; no automatic retry.

        def fsync(self, descriptor):
            try:
                return os.fsync(descriptor)
            except OSError as error:
                capture_io_failure("journal.fsync", error)
                raise

    O.L.os = ObservedOs()
    original_write_new = O.L._write_new

    def observed_write_new(path, data):
        try:
            return original_write_new(path, data)
        except OSError as error:
            role = (
                "snapshot_temp"
                if path.name.startswith("snapshot-") and path.suffix == ".tmp"
                else "immutable_record"
            )
            capture_io_failure("journal.write_flush_close." + role, error)
            raise

    O.L._write_new = observed_write_new
    original_publish = O.L._replace_snapshot

    def observed_publish(source, destination):
        try:
            return original_publish(source, destination)
        except OSError as error:
            callpoint = getattr(error, "snapshot_callpoint", None)
            allowed = {
                "CreateFileW",
                "GetFileInformationByHandle",
                "SetFileInformationByHandle",
                "NtSetInformationFile",
                "CloseHandle",
            }
            point = (
                "journal.snapshot_publish." + callpoint
                if callpoint in allowed
                else "journal.snapshot_publish"
            )
            capture_io_failure(point, error)
            raise

    O.L._replace_snapshot = observed_publish

    def release_requested():
        try:
            return (root / "fixture-only-release").exists()
        except OSError as error:
            capture_io_failure("fixture.release_flag_exists", error)
            raise

    resource = (root / "synthetic-resource.bin").open("x+b")
    resource.write(b"x")
    resource.flush()
    resource.seek(0)
    msvcrt.locking(resource.fileno(), msvcrt.LK_NBLCK, 1)

    class Lease:
        launch_attempted = True
        process = None if args.scenario == "unknown_spawn" else object()

        def snapshot(self):
            if release_requested() and args.scenario != "unknown_spawn":
                return {"alive": False, "launcher_alive": False, "worker_generation_observed": True}
            return {"alive": None, "launcher_alive": None, "worker_generation_observed": False}

        def close(self):
            if not release_requested():
                raise N.LaunchError("fixture_still_owned")
            report["synthetic_lease_closed"] = True

    lease = Lease()

    def launch(*args):
        report["launch_calls"] += 1
        error = N.LaunchError("fixture_after_spawn_failure")
        error.lease = lease
        error.dispatched = None if args_scenario == "unknown_spawn" else True
        raise error

    args_scenario = args.scenario
    N.prepare_and_launch = launch
    N.constrain_plan_to_owner = lambda plan, owner_root: plan
    original_writer = O.L.EventWriter

    class Writer(original_writer):
        def append(self, phase, **details):
            report["active_writer_phase"] = phase
            if args.scenario == "writer_failure" and phase == "owner_unresolved":
                report["writer_failure_observed"] = True
                raise OSError(5, "synthetic private journal failure")
            return super().append(phase, **details)

    O.L.EventWriter = Writer

    def pump():
        return None

    original_sleep = time.sleep

    def fixture_sleep(seconds):
        if args.scenario == "unknown_spawn" and release_requested():
            report["aborted_fixture_only"] = True
            raise FixtureAbort()
        original_sleep(seconds)

    O.time = types.SimpleNamespace(monotonic=time.monotonic, sleep=fixture_sleep)

    fake = types.ModuleType("pythoncom")
    fake.CoInitializeEx = lambda flags: None
    fake.CoUninitialize = lambda: None
    fake.PumpWaitingMessages = pump
    sys.modules["pythoncom"] = fake
    sys.modules["comtypes"] = types.ModuleType("comtypes")

    class Runtime:
        endpoint = "fixture-endpoint-not-COM"

        def close(self):
            return True

    O.create_native_runtime = lambda epoch: Runtime()
    code = 21
    try:
        code = O._run_owned_child(O.OwnerTicket(root, args.epoch))
    except FixtureAbort:
        code = 23
    finally:
        # Synthetic fixture resource only, never CAD/file-provenance resources.
        resource.seek(0)
        msvcrt.locking(resource.fileno(), msvcrt.LK_UNLCK, 1)
        resource.close()
        report["synthetic_resource_released_by_fixture_teardown"] = True
        report["exit_code"] = code
        (root / "fixture-report.json").write_text(json.dumps(report), encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
