"""COM: reaching a running Designer or Layout, reading and calling its members, and telling
what a COM error means.

Xpedition is reached out of process through pywin32. Only a program started through its
launcher registers in the running object table, so this module attaches (``GetActiveObject``)
and never activates. A COM object belongs to the thread that made it: code that runs in a
helper thread (``call_with_timeout``) attaches there itself.

Errors are told apart by their numbers, never by their text alone: Xpedition's descriptions are
localized, and the Chinese description of the missing licence call says neither "license" nor
"token".
"""
from __future__ import annotations

import shutil
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

from ..._driver import AdapterError

T = TypeVar("T")


def _signed(value: int) -> int:
    """An HRESULT as pywin32 reports it: a signed 32-bit number."""
    return value - (1 << 32) if value & 0x80000000 else value


MK_E_UNAVAILABLE = _signed(0x800401E3)  # nothing of that class in the running object table
CO_E_CLASSSTRING = _signed(0x800401F3)
REGDB_E_CLASSNOTREG = _signed(0x80040154)
CO_E_SERVER_EXEC_FAILURE = _signed(0x80080005)  # COM activation, without the launcher's environment
CO_E_OBJNOTCONNECTED = _signed(0x800401FD)
RPC_E_CALL_REJECTED = _signed(0x80010001)
RPC_E_CONNECTION_TERMINATED = _signed(0x80010006)
RPC_E_SERVER_DIED = _signed(0x80010007)
RPC_E_SERVER_DIED_DNE = _signed(0x80010012)
RPC_E_CHANGED_MODE = _signed(0x80010106)
RPC_E_DISCONNECTED = _signed(0x80010108)
RPC_E_SERVERCALL_RETRYLATER = _signed(0x8001010A)
RPC_E_SERVERCALL_REJECTED = _signed(0x8001010B)
RPC_E_CANTCALLOUT_ININPUTSYNCCALL = _signed(0x8001010D)
RPC_S_SERVER_UNAVAILABLE = _signed(0x800706BA)
RPC_S_CALL_FAILED = _signed(0x800706BE)

ABSENT = frozenset({MK_E_UNAVAILABLE, CO_E_CLASSSTRING, REGDB_E_CLASSNOTREG})  # only these let an attach try the next ProgID
BUSY = frozenset({RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER, RPC_E_SERVERCALL_REJECTED, RPC_E_CANTCALLOUT_ININPUTSYNCCALL})
DISCONNECTED = frozenset({RPC_E_DISCONNECTED, RPC_E_SERVER_DIED, RPC_E_SERVER_DIED_DNE, RPC_E_CONNECTION_TERMINATED,
                          RPC_S_SERVER_UNAVAILABLE, RPC_S_CALL_FAILED, CO_E_OBJNOTCONNECTED})
UNAVAILABLE = DISCONNECTED | ABSENT | {CO_E_SERVER_EXEC_FAILURE}

# Xpedition reports "the automation code does not contain the licensing call required for
# authentication" through EXCEPINFO, as code 10279 with scode 0x8004022D. Ordinary faults do
# not carry this EXCEPINFO: DISP_E_TYPEMISMATCH, for one, arrives as (hresult, text, None, n).
LICENCE_CALL_MISSING_CODE = 10279
LICENCE_CALL_MISSING_SCODE = _signed(0x8004022D)
LICENCE_MARKERS = ("license", "licence", "token", "许可证")
# Designer refuses an operation it considers already done (opening the project it has open,
# most often) with code 64185 and the text "Xpedition supports only scripts without GUI".
SCRIPT_WITHOUT_GUI_CODE = 64185

BUSY_RETRY_MS = 2000
DISPATCH_METHOD = 1
DISPATCH_PROPERTYGET = 2
VT_BOOL = 11
VT_VARIANT = 12
PARAMFLAG_FIN = 1


# ------------------------------------------------------------------ errors


def hresult(exc: BaseException) -> int | None:
    """The HRESULT of a pywin32 ``com_error`` (or of an exception shaped like one)."""
    value = getattr(exc, "hresult", None)
    if isinstance(value, int):
        return value
    args = getattr(exc, "args", ())
    if args and type(args[0]) is int:
        return args[0]
    return None


def excepinfo_codes(exc: BaseException) -> tuple[int | None, int | None]:
    """``(code, scode)`` from a ``com_error``'s EXCEPINFO; Xpedition puts its own error number in
    the code (help context) field."""
    args = getattr(exc, "args", ())
    if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) >= 6:
        info = args[2]
        return (info[4] if isinstance(info[4], int) else None, info[5] if isinstance(info[5], int) else None)
    return None, None


def com_text(exc: BaseException) -> str:
    """What the application said: its EXCEPINFO source and description, else the error text."""
    args = getattr(exc, "args", ())
    info = args[2] if len(args) > 2 and isinstance(args[2], tuple) else ()
    if len(info) > 2 and info[2]:
        source = str(info[1] or "Xpedition").strip()
        return f"{source}: {str(info[2]).strip()}"
    if len(args) > 1 and isinstance(args[1], str) and args[1].strip():
        return args[1].strip()
    return str(exc) or type(exc).__name__


def licence_error(exc: BaseException) -> bool:
    """Whether Xpedition refused a call for want of a licence: the missing-licence-call EXCEPINFO,
    or a description that names the licence."""
    code, scode = excepinfo_codes(exc)
    if code == LICENCE_CALL_MISSING_CODE and scode == LICENCE_CALL_MISSING_SCODE:
        return True
    text = f"{com_text(exc)} {exc}".lower()
    return any(marker in text for marker in LICENCE_MARKERS)


def error_code(exc: BaseException) -> str:
    """The runtime error code of a COM failure: E_BUSY when the program refused the call because it
    is busy, E_HUMAN_REQUIRED for a licence refusal, E_BACKEND_UNAVAILABLE when the program is gone
    or cannot be reached, E_NATIVE_FAILURE for anything else."""
    numbers = {hresult(exc), excepinfo_codes(exc)[1]} - {None}
    if numbers & BUSY:
        return "E_BUSY"
    if licence_error(exc):
        return "E_HUMAN_REQUIRED"
    if numbers & UNAVAILABLE or "class not registered" in str(exc).lower():
        return "E_BACKEND_UNAVAILABLE"
    return "E_NATIVE_FAILURE"


def call_state(exc: BaseException) -> str | None:
    """Whether a failed call reached the program: a busy refusal did not (``not_started``), a lost
    connection may have (None, unknown), any other fault came back from it (``dispatched``)."""
    code = error_code(exc)
    if code == "E_BUSY":
        return "not_started"
    if code == "E_BACKEND_UNAVAILABLE":
        return None
    return "dispatched"


def to_adapter_error(exc: BaseException, what: str, *, dispatch_state: str | None = None) -> AdapterError:
    """The ``AdapterError`` for a COM failure while doing ``what`` ("reading Components")."""
    code = error_code(exc)
    number = hresult(exc)
    native = com_text(exc) + (f" (0x{number & 0xFFFFFFFF:08X})" if number is not None else "")
    extra: dict[str, Any] = {"native_message": native}
    if code == "E_BUSY":
        message = f"Xpedition is busy and refused {what}; nothing was done. Try again when it is idle."
        extra["retry_after_ms"] = BUSY_RETRY_MS
        dispatch_state = dispatch_state or "not_started"
    elif code == "E_HUMAN_REQUIRED":
        message = f"Xpedition refused {what} for want of an automation licence; a person must check the licence."
        extra["action"] = "activate_license"
    elif code == "E_BACKEND_UNAVAILABLE":
        if CO_E_SERVER_EXEC_FAILURE in (number, excepinfo_codes(exc)[1]):
            message = f"COM could not start Xpedition for {what}; start the program through its launcher, then attach."
        else:
            message = f"Xpedition could not be reached for {what}: the program is not running, has quit, or is not registered."
    elif excepinfo_codes(exc)[0] == SCRIPT_WITHOUT_GUI_CODE:
        message = (f"Xpedition refused {what} against its current state: it treats the request as already done, "
                   "as when the project asked for is already open.")
    else:
        message = f"Xpedition failed {what}."
    return AdapterError(code, message, dispatch_state=dispatch_state, **extra)


# ------------------------------------------------------------------ pywin32


def _client() -> Any:
    try:
        import win32com.client
    except ImportError:
        raise AdapterError("E_BACKEND_UNAVAILABLE", "pywin32 is needed to reach Xpedition through COM, and it is not installed.",
                           dispatch_state="not_started") from None
    _wrap_without_query(win32com.client)
    return win32com.client


def _wrap_without_query(client: Any) -> None:
    """Wrap an object Xpedition hands back without asking it again for its interface.

    pywin32 wraps each object a property or a collection returns in its generated class, and the
    class first asks the object, across the process boundary, for the interface it already is: a
    few milliseconds each, half of the time it takes to read a routed board's hundreds of traces,
    pads and plane pieces. An object that comes as a dispatch interface is kept as it is; one made
    from nothing or from another wrapper goes the usual way."""
    base = client.DispatchBaseClass
    if getattr(base, "_xpedition_cli_quick", False):
        return
    usual = base.__init__

    def quick(self: Any, oobj: Any = None) -> None:
        if oobj is not None and type(oobj).__name__ == "PyIDispatch":
            self.__dict__["_oleobj_"] = oobj
            return
        usual(self, oobj)

    base.__init__ = quick
    base._xpedition_cli_quick = True


def _pythoncom() -> Any:
    try:
        import pythoncom
    except ImportError:
        raise AdapterError("E_BACKEND_UNAVAILABLE", "pywin32 is needed to reach Xpedition through COM, and it is not installed.",
                           dispatch_state="not_started") from None
    return pythoncom


@contextmanager
def initialized() -> Iterator[None]:
    """COM initialized for the current thread (single-threaded apartment) while the block runs.

    A thread that is already in the multithreaded apartment is left in it."""
    pythoncom = _pythoncom()
    try:
        pythoncom.CoInitialize()
        owned = True
    except Exception as exc:  # noqa: BLE001 - pythoncom.com_error
        if hresult(exc) != RPC_E_CHANGED_MODE:
            raise to_adapter_error(exc, "initializing COM", dispatch_state="not_started") from None
        owned = False
    try:
        yield
    finally:
        if owned:
            pythoncom.CoUninitialize()


def call_with_timeout(fn: Callable[[], T], timeout_s: float) -> T:
    """Run ``fn()`` in a new thread that initializes COM itself, and return its value.

    ``TimeoutError`` when it does not finish in ``timeout_s``: the thread is left running as a
    daemon, since a COM call cannot be cancelled. An exception ``fn`` raises is raised here.
    """
    box: dict[str, Any] = {}
    done = threading.Event()

    def run() -> None:
        try:
            with initialized():
                box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run, name="xpedition-com-call", daemon=True)
    thread.start()
    if not done.wait(timeout_s):
        raise TimeoutError(f"The COM call did not finish within {timeout_s:g} s.")
    if "error" in box:
        raise box["error"]
    return box["value"]


# ------------------------------------------------------------------ the type-library cache

_HEALED = False


def heal_gen_py_cache(root: Path | None = None) -> list[str]:
    """Remove win32com ``gen_py`` entries whose generated module is gone; return their names.

    win32com caches each type library's wrapper under ``gen_py`` in %TEMP%. A temp cleaner that
    deletes the .py files but leaves ``__pycache__`` turns an entry into an empty namespace
    package, and win32com then fails every ``GetActiveObject`` on that library with "has no
    attribute 'CLSIDToClassMap'", which looks like "Designer is not running". An entry is only a
    cache, rebuilt on demand. One being generated right now has its .py files and no
    ``__pycache__`` yet, so it is left alone.
    """
    if root is None:
        try:
            import win32com
        except ImportError:
            return []
        gen_path = str(getattr(win32com, "__gen_path__", "") or "")
        if not gen_path:
            return []
        root = Path(gen_path)
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    removed: list[str] = []
    for entry in entries:
        if (entry.is_dir() and entry.name != "__pycache__" and (entry / "__pycache__").is_dir()
                and not (entry / "__init__.py").exists()):
            if entry.is_symlink() or not entry.resolve().is_relative_to(root.resolve()):
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                removed.append(entry.name)
    if removed:
        print(f"repaired the win32com type-library cache: removed {', '.join(removed)}", file=sys.stderr, flush=True)
    return removed


# ------------------------------------------------------------------ attaching


def _hkcr_names() -> list[str]:
    """The names of the keys under HKEY_CLASSES_ROOT."""
    try:
        import winreg
    except ImportError:
        return []
    names: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "") as root:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(root, index))
                except OSError:
                    break
                index += 1
    except OSError:
        return []
    return names


def progids(base: str, *others: str) -> list[str]:
    """The registered ProgIDs of one class: each base name, then its versioned names
    (``<base>.<number>``) newest first. ``progids("MGCPCB.ExpeditionPCBApplication",
    "MGCPCB.Application")`` gives both bases, then ``.151`` and ``.60`` of each. The bases alone
    when the registry cannot be read or holds none of them."""
    bases = (base, *others)
    registered = set(_hkcr_names())
    bare = [name for name in bases if name in registered]
    versioned: list[tuple[int, int, str]] = []
    for name in registered:
        for order, stem in enumerate(bases):
            suffix = name[len(stem) + 1:] if name.startswith(stem + ".") else ""
            if suffix.isdigit():
                versioned.append((-int(suffix), order, name))
    if not bare and not versioned:
        return list(bases)
    return bare + [name for _, _, name in sorted(versioned)]


def active_object(progids: list[str]) -> Any | None:
    """The running object registered under the first of ``progids`` that has one; None when none
    is running. Only "not running" and "not registered" let it try the next ProgID: any other
    failure is raised, since it does not show that the program is absent."""
    global _HEALED
    client = _client()
    if not _HEALED:
        _HEALED = True
        heal_gen_py_cache()
    for progid in progids:
        try:
            return client.GetActiveObject(progid)
        except Exception as exc:  # noqa: BLE001 - classified below
            if hresult(exc) in ABSENT:
                continue
            error = to_adapter_error(exc, f"attaching to {progid}", dispatch_state="not_started")
            raise AdapterError(error.data["code"], f"Attaching to {progid} failed; this does not show that the program is not "
                               f"running. {error}", **{k: v for k, v in error.data.items() if k != "code"}) from None
    return None


def bind_typelib(obj: Any, client: Any = None) -> Any:
    """Wrap ``obj`` in the early-bound class generated from the running program's own type library.

    ``EnsureDispatch`` trusts any wrapper already generated for the class, including one from an
    older release that shares the interface ID, so the library is taken from the running object
    and every class of it is registered from that module.
    """
    client = client if client is not None else _client()
    gencache = getattr(client, "gencache", None)
    if gencache is None:
        return obj
    raw = getattr(obj, "_oleobj_", None)
    if raw is None:
        try:
            return gencache.EnsureDispatch(obj)
        except Exception:  # noqa: BLE001 - no type information: it stays late-bound
            return obj
    try:
        info = raw.GetTypeInfo()
        library, _ = info.GetContainingTypeLib()
        guid, lcid, _, major, minor, _ = library.GetLibAttr()
        module = gencache.EnsureModule(guid, lcid, major, minor)
        gencache.AddModuleToCache(guid, lcid, major, minor, bFlushNow=False)
        classes = client.CLSIDToClass
        for iid in {*module.CLSIDToClassMap, *module.CLSIDToPackageMap}:
            if classes.HasClass(iid):
                owner = classes.GetClass(iid).__module__
                if owner != module.__name__ and not owner.startswith(module.__name__ + "."):
                    classes.RegisterCLSIDsFromDict(gencache.GetModuleForCLSID(iid).CLSIDToClassMap)
        wrapper = gencache.GetClassForCLSID(str(info.GetTypeAttr()[0]))
        if wrapper is None:
            raise TypeError("the running program's type library has no class for this object")
        return wrapper(raw)
    except Exception as exc:  # noqa: BLE001 - classified
        raise to_adapter_error(exc, "binding the type library") from None


def late_bound(obj: Any, client: Any = None) -> Any:
    """``obj`` late-bound, with its type library loaded.

    win32com fills ``constants`` only once the library's generated classes are loaded, but the
    generated classes make parameterised properties (``Document.Components(...)``) uncallable the
    way the late-bound ones are. So the library is loaded for its constants and the object is
    returned late-bound."""
    client = client if client is not None else _client()
    if getattr(client, "gencache", None) is not None:
        obj = bind_typelib(obj, client)
    dynamic = getattr(client, "dynamic", None)
    if dynamic is not None:
        try:
            return dynamic.Dispatch(obj)
        except Exception:  # noqa: BLE001 - the object as it came
            pass
    return obj


def constants(names: list[str], client: Any = None) -> dict[str, Any]:
    """The values of type-library constants (``epcbSelectAll``...); the library must be loaded."""
    client = client if client is not None else _client()
    table = getattr(client, "constants", None)
    values: dict[str, Any] = {}
    for name in names:
        value = getattr(table, name, None) if table is not None else None
        if value is None:
            raise AdapterError("E_BACKEND_UNAVAILABLE", f"The Xpedition type-library constant {name} is not available.",
                               dispatch_state="not_started")
        values[name] = value
    return values


# ------------------------------------------------------------------ members


def _wrap(value: Any) -> Any:
    if type(value).__name__ == "PyIDispatch":
        return _client().Dispatch(value)
    return value


def propget(obj: Any, dispid: int, *args: Any) -> Any:
    """A property that takes arguments, read through its dispid.

    Read by name, pywin32 fetches the property without them and hands the arguments to the
    result's default member instead: ``doc.Nets(1)`` is ``doc.Nets.Item(1)``."""
    try:
        value = obj._oleobj_.Invoke(dispid, 0, DISPATCH_PROPERTYGET, 1, *args)
    except Exception as exc:  # noqa: BLE001 - classified
        raise to_adapter_error(exc, f"reading property {dispid}") from None
    return _wrap(value)


def invoke_method(obj: Any, name: str, *args: Any, result_type: tuple[int, int] = (VT_VARIANT, 0),
                  arg_types: tuple[tuple[int, int], ...] | None = None) -> Any:
    """Call a method by name with DISPATCH_METHOD (GetIDsOfNames, then InvokeTypes).

    An object without live type information makes a Python attribute read send PROPERTYGET,
    which must not be used to find a method that changes something; ``ForwardAnnotate`` is
    called this way (``result_type=(VT_BOOL, 0)``). Arguments go as VARIANTs unless
    ``arg_types`` says otherwise.
    """
    raw = getattr(obj, "_oleobj_", None)
    if raw is None and hasattr(obj, "GetIDsOfNames"):
        raw = obj
    if raw is None:
        try:
            return getattr(obj, name)(*args)
        except Exception as exc:  # noqa: BLE001 - classified
            raise to_adapter_error(exc, f"calling {name}", dispatch_state=call_state(exc)) from None
    try:
        dispid = raw.GetIDsOfNames(name)
    except Exception as exc:  # noqa: BLE001 - classified
        raise to_adapter_error(exc, f"finding the method {name}", dispatch_state="not_started") from None
    types = tuple(arg_types) if arg_types is not None else tuple((VT_VARIANT, PARAMFLAG_FIN) for _ in args)
    try:
        value = raw.InvokeTypes(dispid, 0, DISPATCH_METHOD, result_type, types, *args)
    except Exception as exc:  # noqa: BLE001 - classified
        raise to_adapter_error(exc, f"calling {name}", dispatch_state=call_state(exc)) from None
    return _wrap(value)


def member(obj: Any, name: str) -> Any:
    """A member that is a property on one release and a method on another.

    Several members that read like accessors (``GetLocation``, ``GetConnections``,
    ``DefaultFilePath``) are properties; calling one raises DISP_E_PARAMNOTOPTIONAL. The property
    is tried first and the call form after it. None when the member cannot be read."""
    try:
        value = getattr(obj, name)
    except Exception:  # noqa: BLE001 - the member is not readable
        return None
    if value is None:
        return None
    if hasattr(value, "Count") or not callable(value):
        return value
    try:
        return value()
    except Exception:  # noqa: BLE001 - it was the value itself
        return value
