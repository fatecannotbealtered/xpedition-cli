"""Diagnostic names cannot replace a returned native result or object identity."""

import hashlib
import json

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_native_owner as O
from xpedition_cli import native_com_adapter as A


@pytest.mark.parametrize(
    "name", ["POINTER(GIT)", "POINTER(ITypeLib)", "opaque secret/address text"]
)
def test_dynamic_type_names_roundtrip_real_native_call_and_strict_boundary(tmp_path, name):
    trace = O._BoundaryTrace(tmp_path)
    value = type(name, (), {"__repr__": lambda self: pytest.fail("No repr/address is allowed")})()
    calls = []
    try:
        with A._owner_boundary_trace(trace):
            returned = A._native_call(
                "fixed_document.dual_document.git.create_comtypes_table",
                lambda: calls.append(True) or value,
            )
        assert returned is value and calls == [True] and trace.failed is False
        rows = [
            json.loads(line)
            for line in (tmp_path / "native-boundary.ndjson").read_text().splitlines()
        ]
        assert [r["event"] for r in rows] == ["begin", "end"]
        assert rows[-1]["result"] == {
            "type": "OpaqueNativeObject",
            "length": len(name),
            "sha256": hashlib.sha256(name.encode("utf-8")).hexdigest(),
        }
        assert name not in (tmp_path / "native-boundary.ndjson").read_text()
        with pytest.raises(O.OwnerError):
            O._BoundaryTrace._safe_fields({"result": {"type": name}})
    finally:
        trace.close()


def test_safe_identifier_type_labels_remain_unchanged(tmp_path):
    trace = O._BoundaryTrace(tmp_path)
    try:
        with A._owner_boundary_trace(trace):
            A._native_call("native.safe_type", lambda: object())
        last = json.loads((tmp_path / "native-boundary.ndjson").read_text().splitlines()[-1])
        assert last["result"] == {"type": "object"}
    finally:
        trace.close()


def test_sink_failure_after_normal_return_keeps_fact_and_never_prevents_cleanup(tmp_path):
    trace = O._BoundaryTrace(tmp_path)
    stream, calls = trace.stream, []

    class FailingEnd:
        writes = 0

        def write(self, value):
            self.writes += 1
            if self.writes == 2:
                raise OSError("fixed diagnostic write failure")
            return stream.write(value)

        def __getattr__(self, name):
            return getattr(stream, name)

    trace.stream = FailingEnd()
    try:
        with A._owner_boundary_trace(trace), pytest.raises(OSError) as failed:
            A._native_call("native.returned", lambda: calls.append("returned") or True)
        assert failed.value.native_call_returned is True
        assert failed.value.native_result_summary is True
        assert failed.value.native_boundary_trace_incomplete is True
        assert calls == ["returned"]
        with A._owner_boundary_trace(trace):
            A._native_call("worker.CoUninitialize", lambda: calls.append("cleanup"))
        assert calls == ["returned", "cleanup"]
    finally:
        trace.close()
