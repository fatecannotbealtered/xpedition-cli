"""Explicit fake native objects and hard stops; never constructs a COM instance."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from xpedition_cli import designer_window_identity as W


@pytest.fixture(autouse=True)
def hard_no_native(monkeypatch, tmp_path):
    from xpedition_cli import native_com_adapter as A
    from xpedition_cli import native_process as P
    from xpedition_cli.backends import NativeBackend
    from xpedition_cli.cli import common, sessions

    hits = []

    def refuse(*args, **kwargs):
        hits.append(True)
        pytest.fail("No native backend, process discovery or COM app acquisition is allowed")

    monkeypatch.setenv("XPEDITION_CLI_CONFIG_DIR", str(tmp_path / "private-fixture-only"))
    monkeypatch.setenv("XPEDITION_NATIVE_COMMAND", str(tmp_path / "does-not-exist.exe"))
    for owner, name in (
        (NativeBackend, "invoke"),
        (NativeBackend, "status"),
        (common, "native"),
        (sessions, "native"),
        (A, "_application"),
        (A, "_viewdraw_application"),
        (A, "processes"),
        (P, "processes"),
        (P, "unique_executable_pid"),
        (W, "_window"),
        (W, "_generation"),
        (W, "_load_com"),
    ):
        monkeypatch.setattr(owner, name, refuse)
    yield
    assert hits == [], "A swallowed default native call still fails this test"


class FakeComError(Exception):
    def __init__(self, hresult=-2147467262):
        super().__init__("secret/native-path/credential must not escape")
        self.hresult = hresult


class TypeInfo:
    def __init__(self, framework=False):
        self.framework = framework
        self.dispid, self.invkind, self.arguments = 242, 1, ()
        self.major, self.minor, self.name = 151, 0, "ViewDraw"
        self.return_guid = W.FRAMEWORK_IID
        self.members = [
            (4, "FullName", 2),
            (5, "Name", 2),
            (209, "Version", 2),
            (40, "ActiveViewHandle", 2),
            (901, "IsProjectOpened", 1),
            (902, "GetProjectData", 1),
            (242, "GetFramework", 1),
        ]

    def GetContainingTypeLib(self):
        library = SimpleNamespace(
            GetLibAttr=lambda: (W.VIEWDRAW_GUID, 0, 3, self.major, self.minor, 8),
            GetDocumentation=lambda index: (self.name,),
        )
        return library, 0

    def GetTypeAttr(self):
        return SimpleNamespace(
            cFuncs=0 if self.framework else len(self.members),
            iid=self.return_guid if self.framework else "ea4abd71-84b0-11ce-8237-00001b4d36b5",
        )

    def GetFuncDesc(self, index):
        dispid, name, kind = self.members[index]
        if name == "GetFramework":
            return (self.dispid, None, self.arguments, None, self.invkind)
        return (dispid, None, (), None, kind)

    def GetNames(self, dispid):
        if dispid == self.dispid:
            return ("GetFramework",)
        return (next(name for value, name, kind in self.members if value == dispid),)


class Scene:
    def __init__(self, tmp_path, *, framework=False):
        self.exe = str(tmp_path / "XP/wv/win64/bin/viewdraw.exe")
        self.path = str(tmp_path / "own.prj")
        self.opened = True
        self.app_unknown, self.framework_unknown = object(), object()
        self.info, self.framework_info = TypeInfo(), TypeInfo(True)
        self.com = SimpleNamespace(IID_IUnknown="unknown", IID_IOleWindow="window")
        self.direct_unsupported = framework
        self.framework_unsupported = False
        self.direct_zero = False
        self.wrong_canonical = False
        self.framework_drift = False
        self.framework_reads = 0
        self.hwnd = 123
        self.calls = []
        self.window = {"hwnd": 123, "is_window": True, "pid": 40, "thread_id": 55}
        self.generation = {
            "pid": 40,
            "alive": True,
            "executable": self.exe,
            "birth_filetime_100ns": "123456789",
        }
        self.window_reads = self.generation_reads = 0
        self.app = SimpleNamespace(
            FullName=self.exe,
            Name="Xpedition Designer",
            Version="2604.fixture",
            ActiveViewHandle=0,
            IsProjectOpened=lambda: self.opened,
            GetProjectData=self.project_data,
            GetFramework=self.get_framework,
            _oleobj_=SimpleNamespace(QueryInterface=self.app_query, GetTypeInfo=lambda: self.info),
        )
        self.framework = SimpleNamespace(
            _oleobj_=SimpleNamespace(
                QueryInterface=self.framework_query,
                GetTypeInfo=lambda: self.framework_info,
            )
        )

    def project_data(self):
        designs = [] if not self.opened else ["PhoneMain"]
        return SimpleNamespace(
            GetProjectFilePath=lambda: self.path if self.opened else None,
            GetiCDBDesigns=lambda: SimpleNamespace(
                GetCount=lambda: len(designs), GetItem=lambda index: designs[index - 1]
            ),
        )

    def app_query(self, iid):
        self.calls.append("app.QI." + iid)
        if iid == "unknown":
            return self.app_unknown
        if self.direct_unsupported:
            raise FakeComError()
        return self.window_interface(self.app_unknown, self.direct_zero)

    def framework_query(self, iid):
        self.calls.append("framework.QI." + iid)
        if iid == "unknown":
            return self.framework_unknown
        if self.framework_unsupported:
            raise FakeComError()
        return self.window_interface(self.framework_unknown)

    def window_interface(self, unknown, zero=False):
        def query(iid):
            self.calls.append("window.QI." + iid)
            return object() if self.wrong_canonical else unknown

        def get_window():
            self.calls.append("IOleWindow.GetWindow")
            return 0 if zero else self.hwnd

        return SimpleNamespace(QueryInterface=query, GetWindow=get_window)

    def get_framework(self):
        self.calls.append("app.GetFramework")
        self.framework_reads += 1
        if self.framework_drift and self.framework_reads > 1:
            self.framework_unknown = object()
        return self.framework

    def document(self):
        return {
            "status": "observed",
            "open": self.opened,
            "path": self.path if self.opened else None,
            "designs": ["PhoneMain"] if self.opened else [],
            "source": "IsProjectOpened/GetProjectData public getters",
        }

    def window_reader(self, hwnd):
        self.window_reads += 1
        return deepcopy(self.window)

    def generation_reader(self, pid):
        self.generation_reads += 1
        return deepcopy(self.generation)

    def rows(self, domain="schematic"):
        return (
            []
            if domain != "schematic"
            else [
                {key: self.generation[key] for key in ("pid", "executable", "birth_filetime_100ns")}
                | {
                    "parent_pid": 3,
                    "executable": W._path(self.generation["executable"]),
                }
            ]
        )

    def run(self):
        return W.observe(
            self.app,
            self.document(),
            self.exe,
            com_module=self.com,
            window_reader=self.window_reader,
            generation_reader=self.generation_reader,
        )

    def identity(self, proof=None):
        proof = self.run() if proof is None else proof
        return {
            "domain": "schematic",
            "pid": proof["pid"],
            "type_library": {
                "guid": "{" + W.VIEWDRAW_GUID + "}",
                "name": "ViewDraw",
                "major": 151,
                "minor": 0,
                "lcid": 0,
                "syskind": 3,
                "flags": 8,
            },
            "public_fields": {
                "FullName": self.exe,
                "Name": "Xpedition Designer",
                "Version": "2604.fixture",
                "ActiveViewHandle": 0,
            },
            "document": self.document(),
            "designer_window_binding": proof,
            "pid_source": {
                "property": proof["source"],
                "hwnd": proof["hwnd"],
                "api": "GetWindowThreadProcessId",
            },
        }

    def install_dependencies(self, monkeypatch):
        monkeypatch.setattr(W, "_load_com", lambda: self.com)
        monkeypatch.setattr(W, "_window", self.window_reader)
        monkeypatch.setattr(W, "_generation", self.generation_reader)
