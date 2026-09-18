from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import api_inventory as inventory
from xpedition_cli.errors import CLIError


class VarDesc:
    memid = 9
    varkind = 2
    elemdescVar = (3, 0, None)

    @property
    def value(self):
        raise AssertionError("constant values must not be inspected")

    def __getitem__(self, index):
        assert index == 3
        return 0


class TypeInfo:
    def __init__(self, name="IFixture", broken=False):
        self.name, self.broken, self.func_reads, self.var_reads = name, broken, [], []

    def GetTypeAttr(self):
        if self.broken:
            raise RuntimeError("private exception detail")
        return NS(
            iid="{FAKE}",
            typekind=4,
            cFuncs=2,
            cVars=1,
            cImplTypes=0,
            wTypeFlags=0,
            wMajorVerNum=1,
            wMinorVerNum=0,
        )

    def GetDocumentation(self, memid):
        assert memid == -1
        return (self.name, "not returned", 0, "private-help-path")

    def GetFuncDesc(self, index):
        self.func_reads.append(index)
        return NS(
            memid=7,
            args=((3, 1, None), (8, 48, "do-not-print-default")),
            invkind=2 if index == 0 else 4,
            wFuncFlags=0,
            callconv=4,
            cParamsOpt=1,
            rettype=(24, 0, None),
        )

    def GetVarDesc(self, index):
        self.var_reads.append(index)
        return VarDesc()

    def GetNames(self, memid, count):
        return ("Value", "first", "second")[:count] if memid == 7 else ("Constant",)


class Library:
    def __init__(self, infos=None):
        self.infos = [TypeInfo()] if infos is None else infos

    def GetLibAttr(self):
        return ("{LIBRARY}", 0, 3, 1, 2, 0)

    def GetTypeInfoCount(self):
        return len(self.infos)

    def GetTypeInfo(self, index):
        return self.infos[index]


class Com:
    COINIT_APARTMENTTHREADED = 2
    REGKIND_NONE = 2

    def __init__(self, library=None):
        self.calls = []
        self.library = library or Library()

    def CoInitializeEx(self, flags):
        assert flags == 2
        self.calls.append("initialize")

    def LoadTypeLibEx(self, path, flags):
        assert flags == self.REGKIND_NONE
        self.calls.append(("load_no_registration", path))
        return self.library

    def CoUninitialize(self):
        self.calls.append("uninitialize")

    def __getattr__(self, name):
        raise AssertionError(f"prohibited automation API: {name}")


def test_type_listing_does_not_read_members_or_values():
    lib = Library([TypeInfo("A"), TypeInfo("B")])
    result = inventory.inspect_library(lib, limit=1)
    assert result["count"] == 1 and result["next_offset"] == 1
    assert result["total"] == 2 and result["complete_in_scope"]
    assert result["library"]["version"] == "1.2"
    assert all(not i.func_reads and not i.var_reads for i in lib.infos)
    assert all(value is False for value in result["execution"].values())


def test_exact_selection_member_kinds_and_defaults_omitted():
    result = inventory.inspect_library(Library(), name="IFixture")
    assert [r["kind"] for r in result["items"]] == ["property_get", "property_put", "variable"]
    assert result["items"][0]["member_id"] == "7"
    assert result["items"][0]["parameters"][1]["default_declared"] is True
    text = json.dumps(result)
    assert "do-not-print-default" not in text and "private-help-path" not in text
    assert result["semantic_validation"] == "not_performed"


def test_member_paging_only_fetches_requested_metadata():
    info = TypeInfo()
    result = inventory.inspect_library(Library([info]), name="IFixture", offset=1, limit=1)
    assert info.func_reads == [1] and not info.var_reads
    assert result["has_more"] and result["next_offset"] == 2
    result = inventory.inspect_library(Library([info]), name="IFixture", offset=99)
    assert result["count"] == 0 and result["offset"] == 3


def test_unreadable_headers_mark_partial_and_prevent_unique_selection():
    lib = Library([TypeInfo("A"), TypeInfo("B", broken=True)])
    result = inventory.inspect_library(lib, limit=1)
    assert not result["complete_in_scope"] and result["issue_count"] == 1
    assert "private exception" not in json.dumps(result)
    with pytest.raises(CLIError):
        inventory.inspect_library(lib, name="A")


def test_failed_member_is_not_silently_omitted():
    info = TypeInfo()

    def fail(index):
        raise RuntimeError("not-for-output")

    info.GetFuncDesc = fail
    result = inventory.inspect_library(Library([info]), name="IFixture")
    assert result["count"] == 3 and result["issue_count"] == 2
    assert result["items"][2]["kind"] == "variable"
    assert not result["complete_in_scope"]
    assert "not-for-output" not in json.dumps(result)


@pytest.mark.parametrize("name,expected", [("ifixture", "E_NOT_FOUND"), ("Missing", "E_NOT_FOUND")])
def test_type_name_exact(name, expected):
    with pytest.raises(CLIError) as error:
        inventory.inspect_library(Library(), name=name)
    assert error.value.code == expected


def test_duplicate_type_names_do_not_choose_first():
    with pytest.raises(CLIError) as error:
        inventory.inspect_library(Library([TypeInfo(), TypeInfo()]), name="IFixture")
    assert error.value.code == "E_CONFLICT"


def test_counts_are_bounded():
    lib = Library()
    lib.GetTypeInfoCount = lambda: inventory.MAX_TYPES + 1
    with pytest.raises(CLIError):
        inventory.inspect_library(lib)


@pytest.mark.parametrize("value", [object(), {"a": 1}, "guess-a-type", [0] * 33])
def test_descriptors_never_serialize_arbitrary_objects(value):
    with pytest.raises(ValueError):
        inventory._descriptor(value)


def test_nested_descriptor_structure_is_not_a_guessed_schema():
    assert inventory._descriptor((26, (29, 42))) == [26, [29, 42]]
    value = 1
    for _ in range(10):
        value = [value]
    with pytest.raises(ValueError):
        inventory._descriptor(value)


def fixture_file(tmp_path):
    path = tmp_path / "synthetic.tlb"
    path.write_bytes(b"MSFTsynthetic-not-a-real-library")
    return path


def test_loader_uses_no_registration_and_balances_com_lifecycle(tmp_path, monkeypatch):
    path = fixture_file(tmp_path)
    before = path.read_bytes()
    com = Com()
    monkeypatch.setattr(inventory, "_pythoncom", lambda: com)
    result = inventory.run({"input": str(path)})
    assert com.calls == ["initialize", ("load_no_registration", str(path)), "uninitialize"]
    assert path.read_bytes() == before
    assert len(result["source"]["sha256"]) == 64
    assert result["source"]["freshness"] == "hash_checked_before_and_after"


def test_load_failure_is_sanitized_and_lifecycle_balanced(tmp_path, monkeypatch):
    path = fixture_file(tmp_path)
    com = Com()

    def fail(*args):
        raise RuntimeError("secret path error")

    com.LoadTypeLibEx = fail
    monkeypatch.setattr(inventory, "_pythoncom", lambda: com)
    with pytest.raises(CLIError) as error:
        inventory.run({"input": str(path)})
    assert error.value.code == "E_BACKEND_UNAVAILABLE"
    assert "secret path" not in str(error.value)
    assert com.calls == ["initialize", "uninitialize"]


def test_initialize_failure_does_not_uninitialize_someone_elses_apartment(tmp_path, monkeypatch):
    path = fixture_file(tmp_path)
    com = Com()

    def fail(*args):
        raise RuntimeError("different apartment")

    com.CoInitializeEx = fail
    monkeypatch.setattr(inventory, "_pythoncom", lambda: com)
    with pytest.raises(CLIError):
        inventory.run({"input": str(path)})
    assert com.calls == []


def test_input_change_during_load_is_conflict(tmp_path, monkeypatch):
    path = fixture_file(tmp_path)
    com = Com()
    original = com.LoadTypeLibEx

    def change(*args):
        path.write_bytes(b"MSFTchanged")
        return original(*args)

    com.LoadTypeLibEx = change
    monkeypatch.setattr(inventory, "_pythoncom", lambda: com)
    with pytest.raises(CLIError) as error:
        inventory.run({"input": str(path)})
    assert error.value.code == "E_CONFLICT"
    assert com.calls[-1] == "uninitialize"


@pytest.mark.parametrize("path", ["//host/share/file.tlb", "\\\\host\\file.tlb", "https://x/a.tlb"])
def test_remote_inputs_rejected_without_open(path):
    with pytest.raises(CLIError):
        inventory._local_file(path)


@pytest.mark.parametrize(
    "suffix,data", [(".exe", b"MZ\0\0"), (".dll", b"MSFT"), (".tlb", b"MZ\0\0"), (".olb", b"bad")]
)
def test_executable_or_nonstandalone_inputs_never_reach_loader(tmp_path, monkeypatch, suffix, data):
    path = tmp_path / ("bad" + suffix)
    path.write_bytes(data)
    monkeypatch.setattr(inventory, "_pythoncom", lambda: pytest.fail("loader reached"))
    with pytest.raises(CLIError):
        inventory.run({"input": str(path)})


@pytest.mark.parametrize("opts", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"name": ""}])
def test_bad_controls_fail_before_file_access(opts):
    with pytest.raises(CLIError):
        inventory.run({"input": "missing.tlb", **opts})


@pytest.mark.parametrize(
    "args",
    [
        ["--backend", "native_xpedition"],
        ["--confirm", "ct_secret"],
        ["--dry-run"],
        ["--input", "--name", "A"],
        ["--input=x", "--input=y"],
        ["--output", "x"],
        ["--json", "--format=json"],
    ],
)
def test_strict_flag_boundary(args):
    with pytest.raises(CLIError):
        inventory.validate_argv(["system", "api-inventory", *args])


def test_fields_cannot_hide_scope_execution_or_incompleteness():
    fields = inventory.protected_fields("items")
    assert {"complete_in_scope", "execution", "source", "_untrusted", "has_more"} <= set(
        fields.split(",")
    )


def test_platform_fail_is_explicit(monkeypatch):
    monkeypatch.setattr(inventory.sys, "platform", "linux")
    with pytest.raises(CLIError) as error:
        inventory._pythoncom()
    assert error.value.code == "E_BACKEND_UNAVAILABLE"


def test_overlarge_file_rejected_before_loader(tmp_path, monkeypatch):
    path = fixture_file(tmp_path)
    monkeypatch.setattr(inventory, "MAX_BYTES", 8)
    monkeypatch.setattr(inventory, "_pythoncom", lambda: pytest.fail("loader accessed"))
    with pytest.raises(CLIError):
        inventory.run({"input": str(path)})
