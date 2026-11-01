"""The frozen owner reads the one qualified151 enum, never a cache default."""

import copy
import threading
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401
from test_designer_dual_invoke import scene as scene  # noqa: F401

from xpedition_cli import designer_dual_invoke as D
from xpedition_cli import designer_owner_dispatch as Dispatcher
from xpedition_cli import native_com_adapter as A


class Enum:
    def __init__(self):
        self.name, self.rows = (
            "VdBusOrWire",
            [
                NS(
                    name="VD_WIRE",
                    memid=1073741824,
                    varkind=2,
                    elemdescVar=NS(tdesc=NS(vt=22)),
                    _=NS(lpvarValue=NS(contents=NS(vt=3, _=NS(VT_I4=0)))),
                )
            ],
        )

    def GetTypeAttr(self):
        return NS(typekind=0, guid="{00000000-0000-0000-0000-000000000000}", cVars=len(self.rows))

    def GetVarDesc(self, index):
        return self.rows[index]

    def GetNames(self, member, count):
        return [next(r for r in self.rows if r.memid == member).name]


@pytest.fixture
def wire(scene):
    local, remote = Enum(), Enum()
    state = NS(
        local=local, remote=remote, local_entries=[local], remote_entries=[remote], source=scene
    )
    for lib, entries in (
        (scene.local_library, state.local_entries),
        (scene.typed_library, state.remote_entries),
    ):
        lib.GetTypeInfoCount = lambda entries=entries: len(entries)
        lib.GetTypeInfoType = lambda index: 0
        lib.GetDocumentation = lambda index, entries=entries: (entries[index].name,)
        lib.GetTypeInfo = lambda index, entries=entries: entries[index]
    state.probe = D.FixedWireConstantProbe(
        scene.app,
        scene.probe.com,
        scene.probe.budget,
        owner_thread=threading.current_thread(),
        owner_ident=threading.get_ident(),
    )
    return state


def test_current_qualified_zero_is_observed_and_no_business_member_is_invoked(wire):
    result = wire.probe.resolve()
    assert result["status"] == "observed" and result["value"] == 0
    assert result["declaration"]["enum"] == "VdBusOrWire"
    assert result["declaration"]["declared_vartype"] == 22
    assert result["declaration"]["value_vartype"] == 3
    assert result["native_writer_admitted"] is False
    assert wire.probe.resources_released is True
    assert not [r for r in wire.source.calls if r[0] in {"count", "getter"}]


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate_member",
        "duplicate_enum",
        "noninteger",
        "changed_value",
        "variant",
        "declared_type",
        "wrong_library",
    ],
)
def test_missing_ambiguous_or_changed_enum_never_falls_back_to_zero(wire, fault):
    row = wire.local.rows[0]
    if fault == "missing":
        row.name = "VD_BUS"
    elif fault == "duplicate_member":
        extra = copy.deepcopy(row)
        extra.memid += 1
        wire.local.rows.append(extra)
    elif fault == "duplicate_enum":
        wire.local_entries.append(copy.deepcopy(wire.local))
    elif fault == "noninteger":
        row._.lpvarValue.contents._.VT_I4 = False
    elif fault == "changed_value":
        row._.lpvarValue.contents._.VT_I4 = 1
    elif fault == "variant":
        row._.lpvarValue.contents.vt = 11
    elif fault == "declared_type":
        row.elemdescVar.tdesc.vt = 3
    else:
        wire.source.major = 150
    with pytest.raises(D.DualInvokeError) as error:
        wire.probe.resolve()
    assert error.value.dispatch_started is True  # Same macro may have earlier writes.
    assert wire.probe.result["value"] is None
    assert wire.probe.resources_released is True


def test_local_and_held_enum_member_identity_must_match(wire):
    wire.remote.rows[0].memid += 1
    with pytest.raises(D.DualInvokeError):
        wire.probe.resolve()
    assert wire.probe.result["value"] is None


def test_failed_enum_cleanup_retains_owner_graph(wire):
    wire.source.cleanup_failure = True
    with pytest.raises(D.DualInvokeError):
        wire.probe.resolve()
    assert wire.probe.resources_released is False and wire.probe.bridge.refs


def test_frozen_owner_never_touches_gencache_or_client_constants(monkeypatch):
    owner = object()
    seen = []

    class NoCache:
        def __getattr__(self, name):
            pytest.fail("No cached constants or generated wrapper: " + name)

    monkeypatch.setattr(A, "_owner_application", lambda: owner)
    monkeypatch.setattr(Dispatcher, "fixed_wire_constant", lambda app: seen.append(app) or 0)
    assert A._constants(NoCache(), ["VD_WIRE"]) == {"VD_WIRE": 0}
    assert seen == [owner]


def test_legacy_constant_lookup_is_unchanged(monkeypatch):
    monkeypatch.setattr(A, "_owner_application", lambda: None)
    assert A._constants(NS(constants=NS(VD_WIRE=23)), ["VD_WIRE"]) == {"VD_WIRE": 23}
