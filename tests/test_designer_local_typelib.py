"""Fixed installed local typelib source, no COM activation or registration."""

import os
import sys
import threading
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_dual_invoke as D
from xpedition_cli import designer_window_identity as W


@pytest.fixture
def local_source(tmp_path, monkeypatch):
    home = tmp_path / "installed"
    binary = home / "wv" / "win64" / "bin"
    binary.mkdir(parents=True)
    executable = binary / "viewdraw.exe"
    executable.write_bytes(b"nonexecutable fixture")
    path = binary / "viewdraw.tlb"
    path.write_bytes(b"bounded formal metadata fixture")
    state = NS(path=path, loader_calls=[], authorizations=[], queries=[], points=[], mutate=None)
    attributes = ("{" + W.VIEWDRAW_GUID + "}", 0, 3, 151, 0, 8)
    state.attrs = NS(guid=attributes[0], lcid=0, syskind=3, wMajorVerNum=151, wMinorVerNum=0)
    library = NS(GetLibAttr=lambda: state.attrs)

    def loader(filename, flag):
        state.loader_calls.append((filename, flag))
        assert filename == str(path) and flag == 2
        if state.mutate:
            state.mutate()
        return library

    def authorizer(value):
        state.authorizations.append(value)
        if getattr(state, "denied", False):
            raise RuntimeError("scope denied private path")

    def query(iid, major, minor, lcid):
        state.queries.append((iid, major, minor, lcid))
        return str(state.path)

    class Budget:
        def read(self, point, operation):
            state.points.append(point)
            assert len(state.points) <= 128
            return operation()

    probe = D.ReadonlyCountProbe(
        None,
        NS(MakeIID=lambda value: value, QueryPathOfRegTypeLib=query),
        Budget(),
        owner_thread=threading.current_thread(),
        owner_ident=threading.get_ident(),
        installation_root=home,
        cad_executable=executable,
        source_authorizer=authorizer,
    )
    probe.bridge = NS(hold=lambda point, operation: probe.read(point, operation))
    probe.result["library"] = {"lcid": 0}
    fake_module = NS(REGKIND_NONE=2, LoadTypeLibEx=loader)
    monkeypatch.setitem(sys.modules, "comtypes.typeinfo", fake_module)
    state.probe, state.remote_attributes, state.library = probe, attributes, library
    state.loader_module = fake_module
    return state


def test_local_loader_explicitly_uses_no_registration_and_binds_file_identity(local_source):
    scene = local_source
    loaded = scene.probe._load_local_library(scene.remote_attributes)
    assert loaded is scene.library
    assert scene.loader_calls == [(str(scene.path), 2)]
    assert scene.authorizations == [scene.path, scene.path]
    source = scene.probe.result["type_library_source"]
    assert source["status"] == "observed" and source["regkind"] == 2
    assert source["pin"]["bytes"] == scene.path.stat().st_size
    assert source["pin"]["file_id"] == str(scene.path.stat().st_ino)
    assert len(source["pin"]["sha256"]) == 64


@pytest.mark.parametrize("fault", ["scope", "missing_scope", "registry", "wrong_installation"])
def test_source_rejection_never_loads_a_developer_or_other_installation_typelib(
    local_source, fault
):
    scene = local_source
    if fault == "scope":
        scene.denied = True
    elif fault == "missing_scope":
        scene.probe.source_authorizer = None
    elif fault == "registry":
        scene.path = scene.path.parent.parent / "other.tlb"
    else:
        scene.probe.cad_executable = scene.path.parent.parent / "viewdraw.exe"
    with pytest.raises((D.DualInvokeError, RuntimeError)):
        scene.probe._load_local_library(scene.remote_attributes)
    assert scene.loader_calls == []


def test_default_registration_enum_is_never_silently_accepted(local_source):
    scene = local_source
    scene.loader_module.REGKIND_NONE = 0
    with pytest.raises(D.DualInvokeError, match="no_registration_loader"):
        scene.probe._load_local_library(scene.remote_attributes)
    assert scene.loader_calls == [] and scene.authorizations == []


@pytest.mark.parametrize("field,value", [("syskind", 1), ("lcid", 1), ("wMajorVerNum", 150)])
def test_local_library_metadata_must_match_the_held_current_library(local_source, field, value):
    scene = local_source
    setattr(scene.attrs, field, value)
    with pytest.raises(D.DualInvokeError, match="local_and_remote_library_mismatch"):
        scene.probe._load_local_library(scene.remote_attributes)


@pytest.mark.parametrize("change", ["bytes", "identity"])
def test_loader_time_file_change_rejects_even_equal_bytes_at_a_new_identity(local_source, change):
    scene = local_source

    def mutate():
        if change == "bytes":
            scene.path.write_bytes(b"changed metadata")
        else:
            replacement = scene.path.with_suffix(".replacement")
            replacement.write_bytes(scene.path.read_bytes())
            replacement.replace(scene.path)

    scene.mutate = mutate
    with pytest.raises(D.DualInvokeError, match="typelib_changed"):
        scene.probe._load_local_library(scene.remote_attributes)
    assert len(scene.loader_calls) == 1


def test_hardlinked_typelib_is_refused_before_loading(local_source):
    scene = local_source
    os.link(scene.path, scene.path.with_suffix(".hardlink"))
    with pytest.raises(D.DualInvokeError, match="single_link"):
        scene.probe._load_local_library(scene.remote_attributes)
    assert scene.loader_calls == []
