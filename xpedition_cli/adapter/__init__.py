"""The adapter layer: the only code of xpedition-cli that talks to Xpedition (DCLI-260).

Each adapter capability is a method that takes the request dict of
``contract/schemas/adapter.schema.json`` and returns its reply. Implement only what the
chosen modes need: ``probe`` always; ``sessions`` and the instance-side capabilities for
session mode; ``devices`` for device mode. A failure raises ``AdapterError`` with an error
code, and a write capability says in ``dispatch_state`` whether the change began.

The conformance suites never call this code: they run the tool with ``XPEDITION_CLI_FAKE_HOST``
set, and the fake host answers every capability (DVER-023).

Layout of the package:

- ``base``: the handler interface, the call context and the preview helpers
- ``sessions``: sessions, documents, and the ``doc save`` preview and read-back
- ``jobs``: help for the commands the runtime runs as long jobs (DCLI-065)
- ``platform``: finding Xpedition, its environment, COM, processes and dialogs
- ``project``, ``library``: file-mode commands on the project and its central library
- ``designer``: Designer sessions and the schematic commands
- ``layout``: Layout sessions and the board commands
- ``plan``: pure-Python planners and parsers the commands use
"""
from __future__ import annotations

from typing import Any

from .._driver import Adapter as BaseAdapter
from .._driver import AdapterError
from . import base, jobs, sessions
from .base import Context, Handler


def _handlers() -> dict[str, Handler]:
    from . import designer, layout, library, project

    table: dict[str, Handler] = {}
    for module in (project, library, designer, layout):
        for handler in module.HANDLERS:
            table[handler.command] = handler
    return table


class _Lazy(dict):  # type: ignore[type-arg]
    """The handler table, built on first use so that a read of the session list stays light."""

    def _load(self) -> None:
        if not super().__len__():
            self.update(_handlers())

    def get(self, key: Any, default: Any = None) -> Any:
        self._load()
        return super().get(key, default)

    def __getitem__(self, key: Any) -> Any:
        self._load()
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        self._load()
        return super().__contains__(key)


HANDLERS: dict[str, Handler] = _Lazy()


class Adapter(BaseAdapter):
    # ------------------------------------------------------------------ the application

    def probe(self, request: dict[str, Any]) -> dict[str, Any]:
        from .platform import env

        return env.probe()

    # ------------------------------------------------------------------ sessions and documents

    def sessions(self, request: dict[str, Any]) -> dict[str, Any]:
        return sessions.list_sessions(self.store)

    def documents(self, request: dict[str, Any]) -> dict[str, Any]:
        return sessions.documents(self.store, request)

    def open_session(self, request: dict[str, Any]) -> dict[str, Any]:
        return sessions.open_session(self.store, request)

    def close_session(self, request: dict[str, Any]) -> dict[str, Any]:
        return sessions.close_session(self.store, request)

    def create_doc(self, request: dict[str, Any]) -> dict[str, Any]:
        from . import project

        return project.create_doc(self.store, request)

    def open_doc(self, request: dict[str, Any]) -> dict[str, Any]:
        return sessions.open_doc(self.store, request)

    def save_doc(self, request: dict[str, Any]) -> dict[str, Any]:
        return sessions.save_doc(self.store, request)

    def close_doc(self, request: dict[str, Any]) -> dict[str, Any]:
        return sessions.close_doc(self.store, request)

    # ------------------------------------------------------------------ domain commands

    def _handler(self, request: dict[str, Any]) -> Handler:
        handler = HANDLERS.get(request.get("command", ""))
        if handler is None:
            raise AdapterError("E_UNSUPPORTED", f"The adapter has no command {request.get('command')!r}.",
                               dispatch_state="not_started")
        return handler

    def read(self, request: dict[str, Any]) -> dict[str, Any]:
        return {"result": self._handler(request).read(Context(self.store, request))}

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("command") == "doc save":
            return sessions.save_preview(self.store, request)
        handler = self._handler(request)
        try:
            out = handler.preview(Context(self.store, request))
        except AdapterError as exc:
            if exc.data.get("conflict") != "application_state":
                raise
            # What the application's state stands in the way of is a blocked preview (DCLI-054).
            return base.preview(f"{handler.command} cannot change anything now.", [], [], blocked_by=[str(exc)],
                                knowledge=exc.data.get("knowledge"))
        if request.get("session"):
            self.store.set(sessions.preview_key(request), {"changes": out.get("changes", [])})
        return out

    def apply(self, request: dict[str, Any]) -> dict[str, Any]:
        handler = self._handler(request)
        ctx = Context(self.store, request)
        if handler.long(ctx):
            # The runtime calls this in its long job's worker (DCLI-065); meanwhile the
            # instance shows as busy to other calls.
            with jobs.running(self.store, request.get("session"), handler.command):
                out = handler.run(ctx, jobs.Progress())
            facts = {"dispatch_state": "dispatched", "delivery": "not_applicable", **out}
        else:
            facts = handler.apply(ctx)
        if request.get("session") and request.get("document") and sessions.changed(facts):
            kept = self.store.get(sessions.preview_key(request), {}) or {}
            sessions.note_change(self.store, request["session"], request["document"], kept.get("changes", []))
        if request.get("session"):
            self.store.delete(sessions.preview_key(request))
        return facts

    def read_back(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("command") == "doc save":
            return sessions.save_read_back(self.store, request)
        return self._handler(request).read_back(Context(self.store, request))
