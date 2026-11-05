"""COM helpers (adapter/platform/com.py): attaching, members, and what a COM error means.

COM errors are faked with exceptions shaped like pywin32's ``com_error``:
``(hresult, text, excepinfo, argerror)`` with ``hresult`` also as an attribute. Nothing
attaches to a real program.
"""
from __future__ import annotations

import io
import shutil
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xpedition_cli._driver import AdapterError
from xpedition_cli.adapter.platform import com

# Observed on a Chinese user interface: the description carries neither "license" nor "token",
# so only the numbers identify the fault.
LICENCE_DESCRIPTION = "自动化代码不包含身份验证所需的许可调用。"


class FakeComError(Exception):
    def __init__(self, hresult: int, text: str = "", excepinfo: tuple | None = None, argerror: int | None = None) -> None:
        super().__init__(hresult, text, excepinfo, argerror)
        self.hresult = hresult


def licence_fault() -> FakeComError:
    return FakeComError(-2147352567, "发生意外。", (0, "Xpedition Layout", LICENCE_DESCRIPTION, None, 10279, -2147220947))


class ErrorCodes(unittest.TestCase):
    def code(self, exc: BaseException) -> str:
        return com.error_code(exc)

    def test_a_rejected_call_is_busy(self) -> None:
        for number in (com.RPC_E_CALL_REJECTED, com.RPC_E_SERVERCALL_RETRYLATER, com.RPC_E_SERVERCALL_REJECTED):
            with self.subTest(number=hex(number & 0xFFFFFFFF)):
                self.assertEqual(self.code(FakeComError(number, "被呼叫方拒绝接收呼叫。")), "E_BUSY")

    def test_the_missing_licence_call_needs_a_person_whatever_the_language(self) -> None:
        self.assertTrue(com.licence_error(licence_fault()))
        self.assertEqual(self.code(licence_fault()), "E_HUMAN_REQUIRED")

    def test_a_type_mismatch_is_not_a_licence_fault(self) -> None:
        # DISP_E_TYPEMISMATCH arrives without EXCEPINFO, as seen from a live session.
        exc = FakeComError(-2147352571, "类型不匹配。", None, 5)
        self.assertFalse(com.licence_error(exc))
        self.assertEqual(self.code(exc), "E_NATIVE_FAILURE")

    def test_other_excepinfo_faults_are_not_licence_faults(self) -> None:
        exc = FakeComError(-2147352567, "发生意外。", (0, "Xpedition Layout", "某个其他错误。", None, 10280, -2147220948))
        self.assertEqual(self.code(exc), "E_NATIVE_FAILURE")

    def test_an_english_or_parts_editor_licence_text_needs_a_person(self) -> None:
        self.assertEqual(self.code(Exception("automation license denied")), "E_HUMAN_REQUIRED")
        exc = FakeComError(-2147352567, "发生意外。", (0, "PDBEditor", "Cannot acquire an Automation Basic license.", None, 0,
                                                     -2147220985))
        self.assertEqual(self.code(exc), "E_HUMAN_REQUIRED")

    def test_a_gone_or_unregistered_server_is_unavailable(self) -> None:
        for exc in (FakeComError(com.RPC_E_DISCONNECTED), FakeComError(com.RPC_S_SERVER_UNAVAILABLE),
                    FakeComError(com.RPC_E_SERVER_DIED), FakeComError(com.REGDB_E_CLASSNOTREG), Exception("Class not registered"),
                    FakeComError(com.CO_E_SERVER_EXEC_FAILURE)):
            with self.subTest(exc=exc):
                self.assertEqual(self.code(exc), "E_BACKEND_UNAVAILABLE")

    def test_anything_else_is_a_native_failure(self) -> None:
        for exc in (FakeComError(-2147024891, "拒绝访问。"), AttributeError("module has no attribute 'CLSIDToClassMap'"),
                    FakeComError(-2147352567, "发生意外。", (0, "LibraryManager", "未实现。", None, 0, -2147467263))):
            with self.subTest(exc=exc):
                self.assertEqual(self.code(exc), "E_NATIVE_FAILURE")


class AdapterErrors(unittest.TestCase):
    def test_busy_says_retry_and_nothing_was_done(self) -> None:
        error = com.to_adapter_error(FakeComError(com.RPC_E_CALL_REJECTED, "busy"), "reading Components")
        self.assertEqual(error.data["code"], "E_BUSY")
        self.assertEqual(error.data["dispatch_state"], "not_started")
        self.assertGreater(error.data["retry_after_ms"], 0)

    def test_a_licence_refusal_asks_for_the_licence(self) -> None:
        error = com.to_adapter_error(licence_fault(), "reading Components")
        self.assertEqual((error.data["code"], error.data["action"]), ("E_HUMAN_REQUIRED", "activate_license"))
        self.assertIn("Xpedition Layout: " + LICENCE_DESCRIPTION, error.data["native_message"])
        self.assertIn("0x80020009", error.data["native_message"])

    def test_com_activation_failure_points_at_the_launcher(self) -> None:
        error = com.to_adapter_error(FakeComError(com.CO_E_SERVER_EXEC_FAILURE), "starting Layout")
        self.assertEqual(error.data["code"], "E_BACKEND_UNAVAILABLE")
        self.assertIn("launcher", str(error))

    def test_a_refusal_as_already_done_says_so(self) -> None:
        exc = FakeComError(-2147352567, "", (0, "Xpedition Designer", "Xpedition supports only scripts without GUI", None, 64185, 0))
        error = com.to_adapter_error(exc, "opening the project")
        self.assertEqual(error.data["code"], "E_NATIVE_FAILURE")
        self.assertIn("already", str(error))

    def test_whether_a_failed_call_reached_the_program(self) -> None:
        self.assertEqual(com.call_state(FakeComError(com.RPC_E_CALL_REJECTED)), "not_started")
        self.assertIsNone(com.call_state(FakeComError(com.RPC_E_DISCONNECTED)))
        self.assertEqual(com.call_state(licence_fault()), "dispatched")

    def test_the_text_is_the_applications_own(self) -> None:
        self.assertEqual(com.com_text(licence_fault()), "Xpedition Layout: " + LICENCE_DESCRIPTION)
        self.assertEqual(com.com_text(FakeComError(-2147352571, "类型不匹配。")), "类型不匹配。")


class ProgIds(unittest.TestCase):
    REGISTERED = ["MGCPCB.Application", "MGCPCB.Application.151", "MGCPCB.Application.60", "MGCPCB.ExpeditionPCBApplication",
                  "MGCPCB.ExpeditionPCBApplication.151", "MGCPCB.ExpeditionPCBApplication.60", "Viewdraw.Application",
                  "Viewdraw.Application.151", "Viewdraw.ApplicationAtTest", "Viewdraw.ApplicationAtTest.151", "Unrelated.App"]

    def test_bases_first_then_versions_newest_first(self) -> None:
        with mock.patch.object(com, "_hkcr_names", lambda: list(self.REGISTERED)):
            self.assertEqual(com.progids("Viewdraw.Application"), ["Viewdraw.Application", "Viewdraw.Application.151"])
            self.assertEqual(com.progids("MGCPCB.ExpeditionPCBApplication", "MGCPCB.Application"), [
                "MGCPCB.ExpeditionPCBApplication", "MGCPCB.Application", "MGCPCB.ExpeditionPCBApplication.151",
                "MGCPCB.Application.151", "MGCPCB.ExpeditionPCBApplication.60", "MGCPCB.Application.60"])

    def test_the_bases_alone_without_a_registry(self) -> None:
        with mock.patch.object(com, "_hkcr_names", lambda: []):
            self.assertEqual(com.progids("Viewdraw.Application"), ["Viewdraw.Application"])

    def test_the_classes_root_is_enumerated_to_its_end(self) -> None:
        names = ["A", "Viewdraw.Application"]

        class Root:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return None

        def enum_key(_key, index):
            if index >= len(names):
                raise OSError("no more")
            return names[index]

        fake = SimpleNamespace(HKEY_CLASSES_ROOT="HKCR", OpenKey=lambda hive, path: Root(), EnumKey=enum_key)
        with mock.patch.dict("sys.modules", {"winreg": fake}):
            self.assertEqual(com._hkcr_names(), names)


class Attaching(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        self.outcomes: dict[str, object] = {}
        client = SimpleNamespace(GetActiveObject=self.get_active)
        for name, value in (("_client", lambda: client), ("_HEALED", True)):
            patcher = mock.patch.object(com, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def get_active(self, progid: str):
        self.calls.append(progid)
        outcome = self.outcomes.get(progid, FakeComError(com.MK_E_UNAVAILABLE, "操作无法使用"))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def test_absent_classes_move_on_to_the_next_progid(self) -> None:
        app = object()
        self.outcomes = {"first": FakeComError(com.CO_E_CLASSSTRING), "second": FakeComError(com.REGDB_E_CLASSNOTREG), "third": app}
        self.assertIs(com.active_object(["first", "second", "third"]), app)

    def test_none_running_is_none(self) -> None:
        self.assertIsNone(com.active_object(["first", "second"]))
        self.assertEqual(self.calls, ["first", "second"])

    def test_another_failure_is_not_absence_and_is_not_retried(self) -> None:
        for exc in (FakeComError(-2147024891, "拒绝访问。"), FakeComError(-2147467259, "未指定的错误"),
                    AttributeError("module 'win32com.gen_py.X' has no attribute 'CLSIDToClassMap'")):
            with self.subTest(exc=exc):
                self.calls.clear()
                self.outcomes = {"first": exc}
                with self.assertRaises(AdapterError) as caught:
                    com.active_object(["first", "second"])
                self.assertEqual(caught.exception.data["code"], "E_NATIVE_FAILURE")
                self.assertEqual(caught.exception.data["dispatch_state"], "not_started")
                self.assertIn("does not show that the program is not running", str(caught.exception))
                self.assertEqual(self.calls, ["first"])

    def test_a_busy_program_is_busy(self) -> None:
        self.outcomes = {"first": FakeComError(com.RPC_E_CALL_REJECTED)}
        with self.assertRaises(AdapterError) as caught:
            com.active_object(["first"])
        self.assertEqual(caught.exception.data["code"], "E_BUSY")

    def test_the_type_library_cache_is_healed_once_before_attaching(self) -> None:
        healed: list[int] = []
        with mock.patch.object(com, "_HEALED", False), mock.patch.object(com, "heal_gen_py_cache", lambda: healed.append(1)):
            com.active_object(["first"])
            com.active_object(["first"])
        self.assertEqual(healed, [1])


class GenPyCache(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def entry(self, name: str, *, init: bool, pycache: bool) -> Path:
        entry = self.root / name
        entry.mkdir()
        if init:
            (entry / "__init__.py").write_text("# generated\n", encoding="utf-8")
        if pycache:
            (entry / "__pycache__").mkdir()
        return entry

    def test_an_entry_that_lost_its_module_is_removed(self) -> None:
        broken = self.entry("4ADEF4E1x0x155x0", init=False, pycache=True)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(com.heal_gen_py_cache(self.root), ["4ADEF4E1x0x155x0"])
        self.assertFalse(broken.exists())
        self.assertIn("4ADEF4E1x0x155x0", err.getvalue())

    def test_healthy_and_half_written_entries_are_left_alone(self) -> None:
        healthy = self.entry("54FE0C76x0x155x0", init=True, pycache=True)
        fresh = self.entry("91EF0189x0x155x0", init=False, pycache=False)  # being generated right now
        (self.root / "__pycache__").mkdir()
        (self.root / "dicts.dat").write_bytes(b"cache index")
        self.assertEqual(com.heal_gen_py_cache(self.root), [])
        for path in (healthy, fresh, self.root / "__pycache__", self.root / "dicts.dat"):
            self.assertTrue(path.exists())

    def test_a_missing_cache_root_is_not_an_error(self) -> None:
        self.assertEqual(com.heal_gen_py_cache(self.root / "gone"), [])


class TypeLibrary(unittest.TestCase):
    def fixture(self):
        calls: list = []
        live = "win32com.gen_py.fixture151"
        old = type("Old", (), {"__module__": "win32com.gen_py.fixture155"})
        new = type("New", (), {"__module__": live, "__init__": lambda self, raw: setattr(self, "_oleobj_", raw)})
        classes = {"app-iid": old, "text-iid": old}
        lib = SimpleNamespace(GetLibAttr=lambda: ("lib-guid", 0, 3, 151, 0, 8))
        info = SimpleNamespace(GetContainingTypeLib=lambda: (lib, 0), GetTypeAttr=lambda: ("app-iid",))
        raw = SimpleNamespace(GetTypeInfo=lambda: info)
        app = old()
        app._oleobj_ = raw
        module = SimpleNamespace(__name__=live, CLSIDToClassMap={}, CLSIDToPackageMap={"app-iid": "App", "text-iid": "Text"})
        cache = SimpleNamespace(
            EnsureDispatch=lambda value: value,
            EnsureModule=lambda *args, **kwargs: calls.append((args, kwargs)) or module,
            AddModuleToCache=lambda *args, **kwargs: calls.append((args, kwargs)),
            GetModuleForCLSID=lambda iid: SimpleNamespace(CLSIDToClassMap={iid: new}),
            GetClassForCLSID=lambda iid: classes[iid],
        )
        registry = SimpleNamespace(HasClass=lambda iid: iid in classes, GetClass=lambda iid: classes[iid],
                                   RegisterCLSIDsFromDict=lambda mapping: classes.update(mapping))
        return SimpleNamespace(gencache=cache, CLSIDToClass=registry), app, raw, new, classes, calls

    def test_the_running_programs_library_wins_over_an_older_wrapper_with_the_same_ids(self) -> None:
        client, app, raw, new, classes, calls = self.fixture()
        bound = com.bind_typelib(app, client)
        self.assertIs(type(bound), new)
        self.assertIs(bound._oleobj_, raw)
        self.assertIs(classes["text-iid"], new)
        self.assertEqual(calls[0][0], ("lib-guid", 0, 151, 0))
        self.assertEqual(calls[1][1], {"bFlushNow": False})

    def test_late_bound_loads_the_library_then_dispatches_dynamically(self) -> None:
        client, app, *_ = self.fixture()
        dynamic: list = []
        client.dynamic = SimpleNamespace(Dispatch=lambda obj: dynamic.append(obj) or "late")
        self.assertEqual(com.late_bound(app, client), "late")
        self.assertEqual(type(dynamic[0]).__name__, "New")

    def test_without_type_information_the_object_stays_as_it_came(self) -> None:
        app = object()

        def refuse(_app):
            raise TypeError("This COM object can not automate the makepy process")

        self.assertIs(com.bind_typelib(app, SimpleNamespace()), app)
        self.assertIs(com.bind_typelib(app, SimpleNamespace(gencache=SimpleNamespace(EnsureDispatch=refuse))), app)
        self.assertIs(com.late_bound(app, SimpleNamespace()), app)

    def test_a_missing_constant_is_reported(self) -> None:
        client = SimpleNamespace(constants=SimpleNamespace(epcbSelectAll=1))
        self.assertEqual(com.constants(["epcbSelectAll"], client), {"epcbSelectAll": 1})
        with self.assertRaises(AdapterError) as caught:
            com.constants(["epcbMissing"], client)
        self.assertEqual(caught.exception.data["code"], "E_BACKEND_UNAVAILABLE")


class PyIDispatch:
    """Stands in for pywin32's raw dispatch type, recognized by its name."""


class Members(unittest.TestCase):
    def test_propget_passes_the_arguments_to_the_property(self) -> None:
        calls: list = []
        obj = SimpleNamespace(_oleobj_=SimpleNamespace(Invoke=lambda *args: calls.append(args) or "net"))
        self.assertEqual(com.propget(obj, 24, "GND"), "net")
        self.assertEqual(calls, [(24, 0, com.DISPATCH_PROPERTYGET, 1, "GND")])

    def test_propget_wraps_a_returned_object(self) -> None:
        raw = PyIDispatch()
        obj = SimpleNamespace(_oleobj_=SimpleNamespace(Invoke=lambda *args: raw))
        client = SimpleNamespace(Dispatch=lambda value: ("wrapped", value))
        with mock.patch.object(com, "_client", lambda: client):
            self.assertEqual(com.propget(obj, 1), ("wrapped", raw))

    def test_propget_failure_is_an_adapter_error(self) -> None:
        def fail(*args):
            raise licence_fault()

        with self.assertRaises(AdapterError) as caught:
            com.propget(SimpleNamespace(_oleobj_=SimpleNamespace(Invoke=fail)), 7)
        self.assertEqual(caught.exception.data["code"], "E_HUMAN_REQUIRED")

    def raw(self, outcome=True):
        calls: list = []

        def invoke(*args):
            calls.append(args)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return SimpleNamespace(GetIDsOfNames=lambda name: {"ForwardAnnotate": 77, "Open": 5}[name], InvokeTypes=invoke), calls

    def test_invoke_method_dispatches_a_method_never_a_property_get(self) -> None:
        raw, calls = self.raw(True)
        self.assertIs(com.invoke_method(SimpleNamespace(_oleobj_=raw), "ForwardAnnotate", result_type=(com.VT_BOOL, 0)), True)
        self.assertEqual(calls, [(77, 0, com.DISPATCH_METHOD, (com.VT_BOOL, 0), ())])

    def test_invoke_method_sends_arguments_as_variants(self) -> None:
        raw, calls = self.raw(None)
        com.invoke_method(raw, "Open", "D:/work/demo/Demo.prj", 1)
        self.assertEqual(calls[0][4], ((com.VT_VARIANT, com.PARAMFLAG_FIN), (com.VT_VARIANT, com.PARAMFLAG_FIN)))
        self.assertEqual(calls[0][5:], ("D:/work/demo/Demo.prj", 1))

    def test_invoke_method_says_whether_the_call_reached_the_program(self) -> None:
        with self.assertRaises(AdapterError) as caught:
            com.invoke_method(self.raw()[0], "Missing")
        self.assertEqual(caught.exception.data["dispatch_state"], "not_started")
        for exc, code, state in ((FakeComError(com.RPC_E_CALL_REJECTED), "E_BUSY", "not_started"),
                                 (licence_fault(), "E_HUMAN_REQUIRED", "dispatched")):
            with self.subTest(code=code), self.assertRaises(AdapterError) as caught:
                com.invoke_method(self.raw(exc)[0], "ForwardAnnotate")
            self.assertEqual((caught.exception.data["code"], caught.exception.data["dispatch_state"]), (code, state))
        with self.assertRaises(AdapterError) as caught:
            com.invoke_method(self.raw(FakeComError(com.RPC_E_DISCONNECTED))[0], "ForwardAnnotate")
        self.assertNotIn("dispatch_state", caught.exception.data)

    def test_invoke_method_calls_a_plain_object_by_name(self) -> None:
        self.assertEqual(com.invoke_method(SimpleNamespace(Run=lambda x: x * 2), "Run", 4), 8)

    def test_member_reads_a_property_or_calls_a_method(self) -> None:
        class Collection:  # callable, as a COM collection's default member is
            Count = 2

            def __call__(self):
                raise AssertionError("a collection is not called")

        collection = Collection()

        class Obj:
            DefaultFilePath = "D:/work/demo"
            Pins = collection

            def GetLocation(self):  # noqa: N802 - COM spelling
                return (1, 2)

            def Needs(self, value):  # noqa: N802
                return value

            @property
            def Broken(self):  # noqa: N802
                raise FakeComError(-2147352573, "member not found")

        obj = Obj()
        self.assertEqual(com.member(obj, "DefaultFilePath"), "D:/work/demo")
        self.assertEqual(com.member(obj, "GetLocation"), (1, 2))
        self.assertIs(com.member(obj, "Pins"), collection)
        self.assertIsNone(com.member(obj, "Broken"))
        self.assertIsNone(com.member(obj, "Absent"))
        self.assertEqual(com.member(obj, "Needs"), obj.Needs)  # DISP_E_PARAMNOTOPTIONAL: the value itself


class FakePythoncom:
    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.events: list[tuple[str, int]] = []

    def CoInitialize(self) -> None:  # noqa: N802 - pythoncom's spelling
        self.events.append(("init", threading.get_ident()))
        if self.fail is not None:
            raise self.fail

    def CoUninitialize(self) -> None:  # noqa: N802
        self.events.append(("uninit", threading.get_ident()))


class Threads(unittest.TestCase):
    def use(self, fake: FakePythoncom) -> None:
        patcher = mock.patch.object(com, "_pythoncom", lambda: fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_initialized_balances_its_calls(self) -> None:
        fake = FakePythoncom()
        self.use(fake)
        with com.initialized():
            self.assertEqual([e[0] for e in fake.events], ["init"])
        self.assertEqual([e[0] for e in fake.events], ["init", "uninit"])

    def test_a_thread_in_another_apartment_is_left_as_it_is(self) -> None:
        fake = FakePythoncom(FakeComError(com.RPC_E_CHANGED_MODE))
        self.use(fake)
        with com.initialized():
            pass
        self.assertEqual([e[0] for e in fake.events], ["init"])

    def test_a_failed_initialization_is_an_adapter_error(self) -> None:
        self.use(FakePythoncom(FakeComError(-2147467259)))
        with self.assertRaises(AdapterError), com.initialized():
            pass

    def test_call_with_timeout_returns_the_value_from_its_own_initialized_thread(self) -> None:
        fake = FakePythoncom()
        self.use(fake)
        seen: list[int] = []
        self.assertEqual(com.call_with_timeout(lambda: seen.append(threading.get_ident()) or 42, 5.0), 42)
        self.assertNotEqual(seen[0], threading.get_ident())
        self.assertEqual(fake.events, [("init", seen[0]), ("uninit", seen[0])])

    def test_call_with_timeout_raises_what_the_call_raised(self) -> None:
        self.use(FakePythoncom())

        def fail():
            raise AdapterError("E_NOT_FOUND", "no such net")

        with self.assertRaises(AdapterError):
            com.call_with_timeout(fail, 5.0)

    def test_a_call_that_does_not_finish_times_out_and_is_left_running(self) -> None:
        self.use(FakePythoncom())
        release = threading.Event()
        self.addCleanup(release.set)
        with self.assertRaises(TimeoutError):
            com.call_with_timeout(lambda: release.wait(10), 0.05)


if __name__ == "__main__":
    unittest.main()
