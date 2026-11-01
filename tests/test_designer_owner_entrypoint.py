"""Actual source-entry child tests with no native runtime or COM imports."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_dispatch as D


def _child(body):
    preamble = r"""
import ctypes,json,runpy,sys
sys.path.insert(0,sys.argv[1])
def forbidden(*a,**k):raise AssertionError("No native or COM call in entrypoint test")
ctypes.WinDLL=ctypes.OleDLL=forbidden
from xpedition_cli import designer_native_owner as O
from xpedition_cli import designer_owner_dispatch as D
from xpedition_cli.designer_owner_transport import Handoff
O.create_native_runtime=O.L._kernel=forbidden
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-c",
            preamble + body,
            str(Path(__file__).resolve().parents[1]),
            str(Path(O.__file__).resolve()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    out, err = process.communicate(timeout=10)
    assert process.returncode == 0, err
    return json.loads(out)


def test_duplicate_entry_module_exact_type_rejection_is_real_without_native():
    value = _child(r"""
namespace=runpy.run_path(sys.argv[2],run_name="offline_duplicate_entry")
owner=namespace["NativeApplication"].__new__(namespace["NativeApplication"])
handoff=Handoff.__new__(Handoff)
try:D.issue_from_owner(owner,handoff,"a"*32)
except D.DispatchError as error:
 print(json.dumps({"reason":str(error),"callpoint":error.grant_callpoint,
  "exact_package_type":type(owner) is O.NativeApplication}))
else:raise AssertionError("Duplicate source type was silently admitted")
""")
    assert value == {
        "reason": "native_owner_and_handoff_objects_required",
        "callpoint": "bridge.native_types",
        "exact_package_type": False,
    }


def test_actual_main_entry_delegates_to_canonical_package_before_owner_work():
    value = _child(r"""
def fixed_main():
 owner=O.NativeApplication.__new__(O.NativeApplication)
 owner._owner=lambda:None
 owner.probe=lambda:{"canonical_stable":True}
 handoff=Handoff.__new__(Handoff)
 D._handoff_ready=lambda *a:None
 D._wrap_existing=lambda *a:object()
 D._canonical=lambda *a:None
 D._check=lambda *a:None
 grant=D.issue_from_owner(owner,handoff,"a"*32)
 print(json.dumps({"canonical_main":True,"same_object":grant.owner is owner,
  "owner_module":type(owner).__module__}))
 return 0
O.main=fixed_main
runpy.run_path(sys.argv[2],run_name="__main__")
""")
    assert value == {
        "canonical_main": True,
        "same_object": True,
        "owner_module": "xpedition_cli.designer_native_owner",
    }


@pytest.mark.parametrize("point", sorted(D.GRANT_CALLPOINTS))
def test_safe_grant_failure_keeps_exact_fixed_reason_and_callpoint(point):
    error = D.DispatchError("native_owner_and_handoff_objects_required")
    error.grant_callpoint = point
    value = O._failure_facts(error, "owner.bridge_grant_admission", True)
    assert value["reason"] == "native_owner_and_handoff_objects_required"
    assert value["grant_callpoint"] == point
    assert value["dispatched"] is True


def test_untrusted_callpoint_or_error_text_is_not_exported():
    error = D.DispatchError("secret endpoint:objref:must-not-leak")
    error.grant_callpoint = "raw-pointer=private"
    value = O._failure_facts(error, "owner.bridge_grant_admission", True)
    assert value["reason"] == "owned_component_failure"
    assert value["grant_callpoint"] is None
    assert "secret" not in json.dumps(value)
