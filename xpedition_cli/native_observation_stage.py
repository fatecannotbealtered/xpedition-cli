"""One lexical native read stage; never an authorization or cross-command cache.

Only the two factories perform the native getter. Returned JSON is always a fresh
copy. The opaque object cannot arrive through the native adapter JSON protocol.
The owning caller supplies its already-established application/PID identity read;
this module does not infer a new COM Application getter.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path

_ISSUER = object()
MAX_STAGE_SECONDS = 600
MAX_STAGE_BYTES = 64 * 1024 * 1024


def stage_limits():
    """Public resource declaration; it cannot issue or authorize a stage."""
    return {
        "maximum_lease_seconds": MAX_STAGE_SECONDS,
        "maximum_complete_material_bytes": MAX_STAGE_BYTES,
        "lifetime": "one native adapter call and one lexical read stage",
        "cross_phase_reuse": False,
        "cross_command_reuse": False,
        "public_request_injection": False,
        "lease_extends_parent_operation_budget": False,
        "source": "fresh strict complete native getter; same document/PID/board binding",
    }


def _encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


class NativeReadStage:
    """Opaque, immutable serialized material with a lexical, identity-bound lease."""

    __slots__ = (
        "_doc",
        "_adapter",
        "_identity_reader",
        "_identity",
        "_board",
        "_unit",
        "_payload",
        "_texts_payload",
        "_kind",
        "_active",
        "_started",
    )

    def __setattr__(self, name, value):
        if hasattr(self, name):
            raise AttributeError("Native read stage fields are immutable")
        object.__setattr__(self, name, value)

    def __init__(self, issuer, doc, adapter, identity_reader, kind, getter):
        if issuer is not _ISSUER or not callable(identity_reader):
            raise ValueError("Native read stages can only be issued inside a native read")
        self._doc, self._adapter = doc, adapter
        self._identity_reader = identity_reader
        self._identity = _encoded(identity_reader())
        self._board = str(Path(str(doc.FullName)).resolve())
        self._unit = doc.CurrentUnit
        self._kind, self._active, self._started = kind, True, time.monotonic()
        self._texts_payload = None
        try:
            self._payload = _encoded(getter())
            if len(self._payload.encode("utf-8")) > MAX_STAGE_BYTES:
                raise ValueError("Complete native read exceeds the bounded stage material size")
            self._check(doc, adapter)
        except BaseException:
            self.close()
            raise

    def _check(self, doc, adapter):
        if (
            not self._active
            or doc is not self._doc
            or adapter is not self._adapter
            or time.monotonic() - self._started > MAX_STAGE_SECONDS
            or str(Path(str(doc.FullName)).resolve()) != self._board
            or doc.CurrentUnit != self._unit
            or _encoded(self._identity_reader()) != self._identity
        ):
            raise ValueError(
                "Native read stage expired or its document/application identity changed"
            )

    def read(self, doc, adapter, *, kind):
        self._check(doc, adapter)
        if kind != self._kind:
            raise ValueError("A native read stage has the wrong material source")
        return json.loads(self._payload)

    def close(self):
        object.__setattr__(self, "_active", False)


def material(stage, doc, adapter, *, kind):
    # Exact type prevents dictionaries, subclasses or request-shaped lookalikes.
    if type(stage) is not NativeReadStage:
        raise ValueError("A native-issued read stage is required")
    return stage.read(doc, adapter, kind=kind)


def text_inventory(stage, doc, adapter):
    """The optional complete native text sidecar, never projected silk alone."""
    material(stage, doc, adapter, kind="complete_capture")
    return json.loads(stage._texts_payload) if stage._texts_payload is not None else None


@contextmanager
def full_capture(doc, adapter, *, identity_reader, include_texts=False):
    from .cell_refresh import capture

    if type(include_texts) is not bool:
        raise ValueError("Complete text observation selection must be an internal boolean")

    stage = NativeReadStage(
        _ISSUER,
        doc,
        adapter,
        identity_reader,
        "complete_capture",
        lambda: capture(doc, adapter, strict=True),
    )
    try:
        if include_texts:
            from . import board_labels

            texts = board_labels.observe(
                doc, Path(str(doc.FullName)), adapter, _capture_stage=stage
            )["texts"]
            payload = _encoded(texts)
            if len((stage._payload + payload).encode("utf-8")) > MAX_STAGE_BYTES:
                raise ValueError("Complete native read exceeds the bounded stage material size")
            object.__setattr__(stage, "_texts_payload", payload)
        yield stage
    finally:
        stage.close()


@contextmanager
def raw_model(doc, adapter, *, identity_reader):
    stage = NativeReadStage(
        _ISSUER,
        doc,
        adapter,
        identity_reader,
        "raw_board_model",
        lambda: adapter._board_model(doc, strict=True),
    )
    try:
        yield stage
    finally:
        stage.close()
