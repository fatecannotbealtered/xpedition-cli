"""Read-only, retained-reference preflight for a whole-project release.

No activation, authentication, Load, Open, GUI setting or release is performed
here. OS process identity, COM application identity and document ownership are
separate observations. An unobservable writer is never classified as unrelated.
"""

from __future__ import annotations

import copy
import os
import threading
import uuid
from pathlib import Path
from typing import Any

DOMAINS = ("library", "schematic", "pcb")
TYPE_LIBRARIES = {
    "schematic": ("4ADEF4E1-690A-11CE-9261-0020C5E26659", "ViewDraw"),
    "pcb": ("EDEDED00-D5F6-4B04-8FE7-EDEDEDEDED00", "MGCPCB"),
    "library": ("BD83B6B2-BE4F-42BF-B83E-F11B676B82B6", "LibraryManager"),
}
_PUBLIC_IDENTITY_FIELDS = (
    "InstanceGuid",
    "HWnd",
    "Hwnd",
    "HWND",
    "WindowHandle",
    "ActiveViewHandle",
    "ProcessID",
    "Name",
    "FullName",
    "Version",
)


def _guid(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def _canonical_iunknown(app):
    """Retain/compare canonical COM identity only within this client/context."""
    import pythoncom

    return app._oleobj_.QueryInterface(pythoncom.IID_IUnknown)


def _error(message: str, **details: Any):
    from .native_com_adapter import AdapterError

    return AdapterError(
        "E_HUMAN_REQUIRED",
        message,
        {"backup_complete": False, "write_attempted": False, **details},
    )


def process_generation(pid: int) -> dict:
    """Read birth, liveness and image through one query-only process handle.

    This uses the same GetProcessTimes identity basis as the retained-owner
    acceptance method. A PID or an unreadable image alone is not an identity.
    """
    if os.name != "nt":
        raise OSError("Windows process generation observation is unavailable")
    import ctypes
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = (w.HANDLE,) + (ctypes.POINTER(w.FILETIME),) * 4
    kernel.GetProcessTimes.restype = w.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = (
        w.HANDLE,
        w.DWORD,
        w.LPWSTR,
        ctypes.POINTER(w.DWORD),
    )
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    kernel.CloseHandle.restype = w.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        length = w.DWORD(32768)
        image = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        birth, exit_time = [(item.dwHighDateTime << 32) | item.dwLowDateTime for item in times[:2]]
        return {
            "pid": pid,
            "birth_filetime_100ns": str(birth),
            "alive": exit_time == 0,
            "executable": image.value,
        }
    finally:
        kernel.CloseHandle(handle)


def _path(value: str) -> str:
    return os.path.normcase(str(Path(value).resolve()))


def _inventory(domain, process_reader, generation_reader):
    rows = []
    for row in process_reader(domain):
        pid = row.get("pid")
        if type(pid) is not int or pid <= 0:
            raise _error("Native process PID is unavailable", domain=domain, process=row)
        actual = generation_reader(pid)
        birth, image = actual.get("birth_filetime_100ns"), actual.get("executable")
        if (
            actual.get("pid") != pid
            or actual.get("alive") is not True
            or not isinstance(birth, str)
            or not birth.isdecimal()
            or int(birth) <= 0
            or not isinstance(image, str)
            or not Path(image).is_absolute()
            or not isinstance(row.get("executable"), str)
            or _path(row["executable"]) != _path(image)
        ):
            raise _error(
                "Native process generation/image is incomplete",
                domain=domain,
                process=row,
            )
        rows.append(
            {
                "pid": pid,
                "parent_pid": row.get("parent_pid"),
                "birth_filetime_100ns": birth,
                "executable": _path(image),
            }
        )
    if len({row["pid"] for row in rows}) != len(rows):
        raise _error("Native process inventory contains duplicate identities", domain=domain)
    return sorted(rows, key=lambda row: row["pid"])


def _map_processes(rows, pid, domain):
    """Allow one observed worker and its proven installed parent launcher only."""
    matches = [row for row in rows if row["pid"] == pid]
    if len(matches) != 1:
        raise _error("COM application is not mapped to the process inventory", domain=domain)
    worker = matches[0]
    remaining = [row for row in rows if row["pid"] != pid]
    if not remaining:
        return {"worker": pid, "launcher": None}
    image = Path(worker["executable"])
    product = {"pcb": "wg", "schematic": "wv", "library": "lm"}.get(domain)
    if len(remaining) == 1 and product is not None and len(image.parents) >= 4:
        parent = remaining[0]
        expected_worker_tail = [product, "win64", "bin", image.name.lower()]
        actual_tail = [part.lower() for part in image.parts[-4:]]
        launcher = image.parents[3] / "common" / "win64" / "bin" / image.name
        if (
            actual_tail == expected_worker_tail
            and worker["parent_pid"] == parent["pid"]
            and parent["executable"] == _path(str(launcher))
            and int(parent["birth_filetime_100ns"]) <= int(worker["birth_filetime_100ns"])
        ):
            return {"worker": pid, "launcher": parent["pid"]}
    raise _error(
        "Additional native writers have no proved COM/document ownership",
        domain=domain,
        processes=rows,
        observed_application_pid=pid,
    )


def _application_pin(identity, domain, rows, *, closed_designer=None):
    """Validate the actual 151.0 identity shape; preserve located gaps."""
    gaps = []

    def require(condition, field, reason):
        if not condition:
            gaps.append({"field": field, "reason": reason})

    require(isinstance(identity, dict), "application", "identity_not_a_mapping")
    if not isinstance(identity, dict):
        raise _error("COM application identity is incomplete", domain=domain, identity_gaps=gaps)
    require(identity.get("domain") == domain, "domain", "wrong_or_missing_domain")
    pid = identity.get("pid")
    require(type(pid) is int and pid > 0, "pid", "missing_or_invalid_pid")
    library = identity.get("type_library")
    require(isinstance(library, dict), "type_library", "missing_or_invalid_type_library")
    library = library if isinstance(library, dict) else {}
    expected_guid, expected_name = TYPE_LIBRARIES[domain]
    require(
        _guid(library.get("guid")) == _guid(expected_guid),
        "type_library.guid",
        "wrong_or_missing_guid",
    )
    require(
        library.get("name") == expected_name,
        "type_library.name",
        "wrong_or_missing_name",
    )
    require(
        type(library.get("major")) is int and library["major"] == 151,
        "type_library.major",
        "requires_observed_151",
    )
    require(
        type(library.get("minor")) is int and library["minor"] == 0,
        "type_library.minor",
        "requires_observed_0",
    )
    for field in ("lcid", "syskind", "flags"):
        require(
            type(library.get(field)) is int,
            "type_library." + field,
            "missing_or_invalid_scalar",
        )
    for key in identity:
        if key == "type_library_error":
            require(False, key, "type_information_getter_failed")
    for key in library:
        if "error" in key.lower():
            require(False, "type_library." + key, "type_information_getter_failed")
    public = identity.get("public_fields")
    require(isinstance(public, dict), "public_fields", "missing_or_invalid_mapping")
    public = public if isinstance(public, dict) else {}
    for field in ("Name", "FullName", "Version"):
        require(
            isinstance(public.get(field), str) and bool(public[field]),
            "public_fields." + field,
            "missing_or_invalid_declared_identity",
        )
    matches = [row for row in rows if row["pid"] == pid]
    image = public.get("FullName")
    require(
        isinstance(image, str)
        and Path(image).is_absolute()
        and len(matches) == 1
        and _path(image) == matches[0]["executable"],
        "public_fields.FullName",
        "executable_does_not_match_observed_process",
    )
    if "ProcessID" in public:
        require(
            type(public["ProcessID"]) is int and public["ProcessID"] == pid,
            "public_fields.ProcessID",
            "process_id_contradiction",
        )
    source = identity.get("pid_source")
    require(isinstance(source, dict), "pid_source", "mapping_basis_missing")
    source = source if isinstance(source, dict) else {}
    if domain == "schematic" and closed_designer is not None:
        # CloseProject destroys the sheet view HWND in actual 151.0. This is
        # deliberately unavailable to initial/pre-dispatch identity checks.
        previous = closed_designer["application"]
        match = source.get("matching_process")
        match = match if isinstance(match, dict) else {}
        require(
            source.get("property") == "FullName"
            and len(matches) == 1
            and match.get("pid") == pid
            and match.get("parent_pid") == matches[0]["parent_pid"]
            and isinstance(match.get("executable"), str)
            and _path(match["executable"]) == matches[0]["executable"],
            "pid_source.matching_process",
            "post_close_unique_executable_mapping_incomplete",
        )
        require(
            closed_designer.get("same_iunknown") is True,
            "retained_reference.IUnknown",
            "post_close_canonical_reference_not_proved",
        )
        require(
            library == previous["type_library"]
            and {key: public[key] for key in _PUBLIC_IDENTITY_FIELDS if key in public}
            == previous["public_fields"]
            and pid == previous["pid"],
            "retained_reference.application",
            "post_close_public_identity_changed",
        )
        boundary = (
            "confirmed CloseProject; empty document; retained canonical IUnknown; "
            "unchanged 151.0 public identity, unique executable and OS generation; "
            "closed sheet HWND is not a live-window observation"
        )
    elif domain != "library":
        hwnd, prop = source.get("hwnd"), source.get("property")
        require(
            type(hwnd) is int and hwnd > 0,
            "pid_source.hwnd",
            "actual_window_handle_missing",
        )
        require(
            source.get("api") == "GetWindowThreadProcessId",
            "pid_source.api",
            "actual_window_pid_mapping_missing",
        )
        handles = {"HWnd", "Hwnd", "HWND", "WindowHandle", "ActiveViewHandle"}
        from .designer_window_identity import (
            SOURCES,
        )
        from .designer_window_identity import (
            validate as validate_designer_window,
        )

        formal = domain == "schematic" and prop in SOURCES
        require(
            prop in handles | {"Gui.HWND"} or formal,
            "pid_source.property",
            "unsupported_window_basis",
        )
        if formal:
            require(
                validate_designer_window(identity, rows),
                "designer_window_binding",
                "formal_window_document_generation_binding_incomplete",
            )
        if prop in handles:
            require(
                type(public.get(prop)) is int and public[prop] == hwnd,
                "public_fields." + prop,
                "window_property_contradiction_or_missing",
            )
        if domain == "pcb":
            guid = _guid(public.get("InstanceGuid"))
            require(
                guid is not None and guid != str(uuid.UUID(int=0)),
                "public_fields.InstanceGuid",
                "instance_guid_missing_or_invalid",
            )
        boundary = "formal 151.0 identity, HWND-to-PID mapping, OS generation and document"
    else:
        # Actual LibraryManager 151.0 exposes no observed HWND/InstanceGuid.
        # Its existing formal FullName mapping is usable only with a unique
        # matching live executable, separately pinned by OS birth generation.
        match = source.get("matching_process")
        match = match if isinstance(match, dict) else {}
        require(
            source.get("property") == "FullName",
            "pid_source.property",
            "library_requires_declared_fullname_mapping",
        )
        require(
            len(matches) == 1
            and match.get("pid") == pid
            and match.get("parent_pid") == matches[0]["parent_pid"]
            and isinstance(match.get("executable"), str)
            and _path(match["executable"]) == matches[0]["executable"],
            "pid_source.matching_process",
            "library_unique_executable_mapping_incomplete",
        )
        boundary = (
            "LibraryManager has no observed formal HWND/InstanceGuid; formal 151.0 "
            "FullName unique executable mapping, OS generation and library document only"
        )
    if gaps:
        raise _error(
            "COM application identity is incomplete",
            domain=domain,
            identity_gaps=gaps,
            identity=identity,
        )
    pin = {
        "pid": pid,
        "pid_source": source,
        "type_library": library,
        "public_fields": {key: public[key] for key in _PUBLIC_IDENTITY_FIELDS if key in public},
        "process_roles": _map_processes(rows, pid, domain),
        "identity_boundary": boundary,
    }
    from .designer_window_identity import SOURCES

    if domain == "schematic" and source.get("property") in SOURCES:
        pin["designer_native_identity"] = copy.deepcopy(identity)
    return pin


def _document(app, domain, project, board):
    if domain == "schematic":
        opened = app.IsProjectOpened()
        if type(opened) is not bool:
            raise ValueError("Designer IsProjectOpened is not an observed boolean")
        name = app.GetProjectData().GetProjectFilePath() if opened else None
    else:
        document = app.ActiveLibrary if domain == "library" else app.ActiveDocument
        opened = document is not None
        name = document.FullName if opened else None
    if not opened:
        return {"open": False, "path": None, "relation": "empty"}
    if not isinstance(name, str) or not name or not Path(name).is_absolute():
        raise ValueError("Native document full path is unavailable")
    name = Path(name).resolve()
    local = name == project.parent or project.parent in name.parents
    if domain == "library" and local:
        raise _error("The project library is still open; release stopped", library=str(name))
    target = project if domain == "schematic" else board
    if name == target:
        relation = "target"
    elif local:
        raise _error(
            "Another document in this project remains open; release stopped",
            domain=domain,
            document=str(name),
        )
    else:
        relation = "outside_project"
    return {"open": True, "path": _path(str(name)), "relation": relation}


class ReleasePreflight:
    """Hold attached apps and freeze every writer before the first save/close."""

    def __init__(
        self,
        project,
        board,
        *,
        process_reader,
        generation_reader,
        application_reader,
        identity_reader,
    ):
        self.project, self.board = project.resolve(), board.resolve() if board else None
        self.process_reader, self.generation_reader = process_reader, generation_reader
        self.application_reader, self.identity_reader = (
            application_reader,
            identity_reader,
        )
        self.apps: dict[str, Any] = {}
        self.thread = threading.get_ident()
        self.closed = {}
        self.closed_observations = {}
        self.baseline = self._observe(attach=True)
        self.designer_unknown = None
        self.designer_unknown_error = None
        if self.targets("schematic"):
            try:
                self.designer_unknown = _canonical_iunknown(self.apps["schematic"])
            except Exception as exc:
                # The normal HWND path does not need this supplemental proof.
                # Missing canonical identity may never authorize its fallback.
                self.designer_unknown_error = type(exc).__name__

    def _observe(self, *, attach=False, closed=()):
        result = {}
        self.current_closed_fallbacks = set()
        step = "release_preflight.inventory_before"
        try:
            for domain in DOMAINS:
                step = "release_preflight.inventory_before"
                before = _inventory(domain, self.process_reader, self.generation_reader)
                if not before:
                    result[domain] = {
                        "processes": [],
                        "application": None,
                        "document": None,
                    }
                    continue
                if attach:
                    step = "release_preflight.application_attach"
                    self.apps[domain] = self.application_reader(domain)
                if domain not in self.apps:
                    raise _error("A new native writer appeared after preflight", domain=domain)
                app = self.apps[domain]
                step = "release_preflight.application_identity"
                identity = self.identity_reader(app, domain)
                document = None
                retained = None
                if (
                    domain == "schematic"
                    and domain in closed
                    and (identity.get("pid_source") or {}).get("property") == "FullName"
                ):
                    step = "release_preflight.closed_document"
                    document = _document(app, domain, self.project, self.board)
                    identity_document = identity.get("document")
                    identity_empty = (
                        isinstance(identity_document, dict)
                        and identity_document.get("status") == "observed"
                        and identity_document.get("open") is False
                        and identity_document.get("path") is None
                        and not any("error" in key.lower() for key in identity_document)
                    )
                    if (
                        domain not in self.closed
                        or document != {"open": False, "path": None, "relation": "empty"}
                        or not identity_empty
                        or threading.get_ident() != self.thread
                        or self.designer_unknown is None
                    ):
                        raise _error(
                            "Closed Designer requires canonical identity and empty document",
                            domain=domain,
                            canonical_reference_error=self.designer_unknown_error,
                            document=document,
                            identity_document=identity_document,
                        )
                    same = _canonical_iunknown(app) == self.designer_unknown
                    retained = {
                        "application": self.baseline[domain]["application"],
                        "same_iunknown": type(same) is bool and same is True,
                    }
                step = "release_preflight.application_pin"
                pin = _application_pin(identity, domain, before, closed_designer=retained)
                if document is None:
                    step = "release_preflight.native_document"
                    document = _document(app, domain, self.project, self.board)
                step = "release_preflight.inventory_after"
                after = _inventory(domain, self.process_reader, self.generation_reader)
                if after != before:
                    raise _error("Native writer identity changed during preflight", domain=domain)
                result[domain] = {
                    "processes": before,
                    "application": pin,
                    "document": document,
                }
                if retained is not None:
                    self.current_closed_fallbacks.add(domain)
                    self.closed_observations[domain] = {
                        "identity": copy.deepcopy(identity),
                        "document": document,
                        "same_iunknown": retained["same_iunknown"],
                        "close_progress": copy.deepcopy(self.closed[domain]),
                    }
            # A library/Designer writer may appear while a later domain is
            # inspected. Recheck the whole set, not merely each domain locally.
            for domain in DOMAINS:
                step = "release_preflight.writer_set_recheck"
                if (
                    _inventory(domain, self.process_reader, self.generation_reader)
                    != result[domain]["processes"]
                ):
                    raise _error(
                        "Native writer set changed during whole-project preflight",
                        domain=domain,
                    )
        except Exception as exc:
            from .native_com_adapter import AdapterError

            if isinstance(exc, AdapterError):
                exc.details.setdefault("write_attempted", False)
                exc.details.setdefault("backup_complete", False)
                exc.details.setdefault("domain", domain)
                exc.details.setdefault("release_preflight", copy.deepcopy(result))
                raise
            details = {
                "domain": domain,
                "stage": step,
                "call_point": step,
                "cause_type": type(exc).__name__,
                "cause": "native_observation_failed",
                "cause_reason": "native_observation_failed",
                "release_preflight": result,
            }
            hr = getattr(exc, "hresult", None)
            if type(hr) is int:
                details["cause_hresult_hex"] = f"0x{hr & 0xFFFFFFFF:08x}"
            raise _error(
                "Native release conditions could not be completely observed", **details
            ) from exc
        return result

    def revalidate(self, *, closed=()):
        """Re-read retained references and all OS writers; no side effects."""
        expected = copy.deepcopy(self.baseline)
        for domain in closed:
            if expected[domain]["document"] is not None:
                expected[domain]["document"] = {
                    "open": False,
                    "path": None,
                    "relation": "empty",
                }
        actual = self._observe(closed=closed)
        for domain in closed:
            if domain in self.current_closed_fallbacks:
                # The alternate identity above was independently verified. Keep
                # its real FullName basis, never invent a live HWND in evidence.
                expected[domain]["application"] = actual[domain]["application"]
        if "schematic" in closed:
            previous = expected["schematic"].get("application") or {}
            current = actual["schematic"].get("application") or {}
            if "designer_native_identity" in previous or "designer_native_identity" in current:
                from .designer_close_transition import expected_after, verify

                try:
                    same = _canonical_iunknown(self.apps["schematic"]) == self.designer_unknown
                except Exception:
                    same = False
                transition = verify(
                    previous.get("designer_native_identity"),
                    current.get("designer_native_identity"),
                    expected["schematic"]["processes"],
                    actual["schematic"]["processes"],
                    expected_project=str(self.project),
                    close_progress=self.closed.get("schematic", {}),
                    same_retained_iunknown=type(same) is bool
                    and same is True
                    and self.designer_unknown is not None,
                    same_client_thread=threading.get_ident() == self.thread,
                )
                if transition["accepted"] is not True:
                    raise _error(
                        "Confirmed Designer close identity transition is incomplete",
                        domain="schematic",
                        close_transition=transition,
                    )
                previous["designer_native_identity"] = expected_after(
                    previous["designer_native_identity"]
                )
                self.closed_observations["schematic"] = {
                    "identity": copy.deepcopy(current["designer_native_identity"]),
                    "document": actual["schematic"]["document"],
                    "same_iunknown": True,
                    "close_progress": copy.deepcopy(self.closed["schematic"]),
                    "formal_identity_transition": transition,
                }
        if actual != expected:
            raise _error(
                "Release target or writer set changed before further dispatch",
                expected=expected,
                observed=actual,
            )
        return actual

    def confirm_designer_closed(self, progress):
        """Register this dispatch's acknowledged close, never a caller assertion."""
        if (
            not self.targets("schematic")
            or type(progress.get("save_count")) is not int
            or progress.get("save_count") != 1
            or type(progress.get("close_count")) is not int
            or progress.get("close_count") != 1
            or progress.get("save_acknowledged") is not True
            or progress.get("close_returned") is not True
            or progress.get("closed_observed") is not True
        ):
            raise _error("Designer close facts are incomplete; further dispatch stopped")
        self.closed["schematic"] = copy.deepcopy(progress)

    def targets(self, domain):
        return (self.baseline[domain].get("document") or {}).get("relation") == "target"
