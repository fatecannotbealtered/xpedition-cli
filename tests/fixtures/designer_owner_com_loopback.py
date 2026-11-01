"""Explicit one-shot non-CAD COM transport measurement, never collected by pytest.

The only activated object is Windows Scripting.Dictionary in our own cscript
child. There is no CAD lookup, registration, system configuration or termination.
An unresolved native call keeps this helper and its ownership evidence alive.
The initial unknown is immutable; the result is a separate later observation.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xpedition_cli import designer_owner_transport as T
from xpedition_cli import execution_lifecycle as L


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = L._plain_path(args.root)
    L._private_mkdir(root)
    started = time.monotonic()

    def record(phase, **facts):
        value = {"phase": phase, "elapsed_seconds": round(time.monotonic() - started, 6), **facts}
        with (root / "safe-observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")

    L._write_new(root / "initial.json", b'{"original_outcome":"unknown","immutable":true}\n')
    own = L._identity(L._kernel().GetCurrentProcess())
    L._write_new(root / "owner.json", json.dumps(own, sort_keys=True).encode())
    sys.coinit_flags = 2
    import comtypes  # noqa: F401
    import pythoncom

    pythoncom.CoInitializeEx(2)
    epoch, shared = secrets.token_hex(16), {}
    ready, stop = threading.Event(), threading.Event()

    def broker():
        runtime = None
        pythoncom.CoInitializeEx(0)
        try:
            runtime = T.create_native_runtime(epoch)
            shared["runtime"] = runtime
            ready.set()
            stop.wait()
            while not runtime.close():
                runtime.handoff.changed.wait(0.05)
                runtime.handoff.changed.clear()
            shared["broker_cleanup"] = True
        except BaseException as error:
            shared["broker_error"] = type(error).__name__
            ready.set()
        finally:
            runtime = None
            pythoncom.CoUninitialize()

    worker = threading.Thread(target=broker, name="NonCADBrokerMTA")
    worker.start()
    child = raw = canonical = stream = None
    result = {
        "status": "incomplete",
        "cad_used": False,
        "native_writer_admitted": False,
        "original_outcome": "unknown",
        "cleanup_verified": None,
    }
    try:
        assert ready.wait(5) and "runtime" in shared, "broker_start_incomplete"
        runtime = shared["runtime"]
        record("broker_ready", apartment="MTA")
        permit, release = root / "offer-permit", root / "client-release"
        body = "\n".join(
            [
                "Option Explicit",
                "Dim app, broker, fs, i, accepted",
                'Set app = CreateObject("Scripting.Dictionary")',
                'app.Add "probe", "fixture-value"',
                'Set fs = CreateObject("Scripting.FileSystemObject")',
                "For i = 1 To 800",
                f'If fs.FileExists("{permit}") Then Exit For',
                "WScript.Sleep 25",
                "Next",
                f'If Not fs.FileExists("{permit}") Then WScript.Quit 31',
                f'Set broker = GetObject("{runtime.endpoint}")',
                f'accepted = broker.OfferStartupApplication(app, "{epoch}")',
                "Set broker = Nothing",
                "If Not accepted Then WScript.Quit 32",
                "For i = 1 To 800",
                f'If fs.FileExists("{release}") Then Exit For',
                "WScript.Sleep 25",
                "Next",
                "Set app = Nothing",
                "WScript.Quit 0",
                "",
            ]
        )
        script = root / "private-client.vbs"
        L._write_new(script, body.encode("ascii"))
        cscript = Path(os.environ["SystemRoot"]) / "System32" / "cscript.exe"
        with (root / "client-stderr.log").open("xb") as errors:
            child = L._DetachedChild(
                [str(cscript), "//Nologo", str(script)],
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        identity = L._identity(int(child._handle))
        if os.path.normcase(str(cscript)) != identity["executable"]:
            raise RuntimeError("own_cscript_image_differs")
        record(
            "client_original_handle_pinned",
            pid=identity["pid"],
            birth_filetime_100ns=identity["birth_filetime_100ns"],
            image_matches_fixed_cscript=True,
        )

        class Pin:
            def snapshot(self):
                return L._identity(int(child._handle))

        expected = T.ExpectedGeneration(
            identity["pid"], identity["birth_filetime_100ns"], identity["executable"]
        )
        runtime.handoff.arm(expected, Pin())
        L._write_new(permit, b"original own child HANDLE pinned before offer\n")
        deadline = time.monotonic() + 10
        while True:
            pythoncom.PumpWaitingMessages()
            stats = runtime.handoff.snapshot()
            if stats["accepted"] == 1 and stats["inflight"] == 0:
                break
            if stats["rejected"] or time.monotonic() >= deadline:
                raise RuntimeError("host_offer_incomplete")
            time.sleep(0.01)
        record(
            "authenticated_callback_body_retired",
            handoff=stats,
            cscript_generation_matched=True,
            rpc_origin="ncalrpc",
        )
        stream = runtime.handoff.take_stream()
        raw = pythoncom.CoGetInterfaceAndReleaseStream(stream, pythoncom.IID_IDispatch)
        stream = None
        canonical = raw.QueryInterface(pythoncom.IID_IUnknown)
        member = raw.GetIDsOfNames(0, "Count")
        counts = [raw.InvokeTypes(member, 0, 2, (pythoncom.VT_I4, 0), ()) for _ in range(2)]
        stable = raw.QueryInterface(pythoncom.IID_IUnknown) == canonical
        assert counts == [1, 1] and stable is True, "retained_surrogate_readback_failed"
        record(
            "sta_readback_returned",
            counts=counts,
            canonical_stable=stable,
            unmarshals=1,
            application_kind="Scripting.Dictionary_surrogate",
        )
        result.update(
            status="observed",
            read_counts=counts,
            canonical_stable=True,
            marshaled_MTA_to_STA=True,
            callback=stats,
        )
    except BaseException as error:
        result["failure_type"] = type(error).__name__
        result["failure_hresult"] = getattr(error, "hresult", None)
        record("measurement_incomplete", **result)
    finally:
        raw = canonical = stream = None
        L._write_new(
            root / "client-release", b"fixture normal-exit release, not native cancellation\n"
        )
        stop.set()
        while worker.is_alive():
            pythoncom.PumpWaitingMessages()
            worker.join(0.02)
        if child is not None:
            kernel = L._kernel()
            while True:
                signalled = int(kernel.WaitForSingleObject(int(child._handle), 0))
                if signalled == 0:
                    break
                if signalled != 258:
                    record("client_exit_wait_unobservable", cleanup_verified=None)
                    threading.Event().wait()  # Remain explicit owner; never terminate.
                pythoncom.PumpWaitingMessages()
                time.sleep(0.02)
            exited = L._identity(int(child._handle))
            result["client_original_generation_exit_verified"] = (
                exited["pid"] == identity["pid"]
                and exited["birth_filetime_100ns"] == identity["birth_filetime_100ns"]
                and exited["alive"] is False
            )
            result["client_exit_code"] = child.poll()
            child.close_parent_handle()
        result["cleanup_verified"] = (
            shared.get("broker_cleanup") is True
            and result.get("client_original_generation_exit_verified") is True
        )
        pythoncom.CoUninitialize()
    L._write_new(root / "result.json", json.dumps(result, sort_keys=True).encode())
    print(json.dumps(result, sort_keys=True))
    return (
        0
        if result["status"] == "observed"
        and result["cleanup_verified"]
        and result.get("client_exit_code") == 0
        else 21
    )


if __name__ == "__main__":
    raise SystemExit(main())
