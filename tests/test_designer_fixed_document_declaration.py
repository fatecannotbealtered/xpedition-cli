"""The three installed151 signatures are fixed, including the Open retval IID."""

import threading
from types import SimpleNamespace as NS

import pytest
from designer_identity_fakes import hard_no_native  # noqa: F401

from xpedition_cli import designer_dual_invoke as D


def descriptor(shape):
    kind = shape[0]
    if kind == 26:
        return NS(vt=kind, lptdesc=NS(contents=descriptor(shape[1])))
    if kind == 29:
        return NS(vt=kind, hreftype=shape[1])
    return NS(vt=kind)


@pytest.fixture
def metadata():
    rows, roles = {}, {}
    doc = [26, [29, D.DOCUMENT_IID]]
    for iid, name, member, flags, returned in D._DOCUMENT_MEMBERS.values():
        for kind, label in ((4, "dispatch"), (3, "dual")):
            params = []
            if returned:
                params.append((1, [8]))
                if kind == 3:
                    params.append((10, [26, doc]))
            values = [
                NS(tdesc=descriptor(shape), _=NS(paramdesc=NS(wParamFlags=pflags)))
                for pflags, shape in params
            ]
            fd = NS(
                memid=member,
                name=name,
                invkind=1,
                funckind=4 if kind == 4 else 1,
                cParams=len(params),
                cParamsOpt=0,
                wFuncFlags=flags,
                callconv=4,
                oVft={7: 104, 10: 128, 12: 152}[member],
                elemdescFunc=NS(
                    tdesc=descriptor(doc if returned and kind == 4 else [24 if kind == 4 else 25])
                ),
                lprgelemdescParam=values,
            )
            rows[(iid, label, member)] = fd

    class Info:
        def __init__(self, iid, kind):
            self.iid, self.kind = iid, kind

        def GetTypeAttr(self):
            return NS(guid=self.iid, typekind=self.kind, wTypeFlags=64, cFuncs=len(self.members()))

        def members(self):
            return [
                fd
                for (iid, label, _), fd in rows.items()
                if iid == self.iid and label == ("dispatch" if self.kind == 4 else "dual")
            ]

        def GetRefTypeOfImplType(self, index):
            assert index == -1
            return -1

        def GetRefTypeInfo(self, value):
            return roles[(self.iid, 3)] if value == -1 else NS(GetTypeAttr=lambda: NS(guid=value))

        def GetFuncDesc(self, index):
            return self.members()[index]

        def GetNames(self, member, count):
            fd = next(fd for fd in self.members() if fd.memid == member)
            return (
                [fd.name]
                + (["FileName"] if member == 7 else [])
                + (["retval"] if member == 7 and self.kind == 3 else [])
            )

    for iid in {D.DOCS_IID, D.DOCUMENT_IID}:
        for kind in (3, 4):
            roles[(iid, kind)] = Info(iid, kind)
    library = NS(GetTypeInfoOfGuid=lambda iid: roles[(iid, 4)])
    budget = NS(read=lambda point, operation: operation())
    call = D.FixedDocumentCall(
        None,
        NS(),
        budget,
        owner_thread=threading.current_thread(),
        owner_ident=threading.get_ident(),
    )
    call.bridge = NS(guid=lambda iid: iid, hold=lambda point, operation: operation())
    return NS(rows=rows, call=call, library=library)


@pytest.mark.parametrize("operation", ["open_design", "save_all", "save_document"])
def test_formal_dispatch_and_dual_are_both_checked(metadata, operation):
    twin, contract = metadata.call._document_declaration(metadata.library, operation)
    assert twin.kind == 3 and set(contract) == {"dispatch", "dual"}
    assert (
        contract["dispatch"]["member"]
        == contract["dual"]["member"]
        == D._DOCUMENT_MEMBERS[operation][2]
    )


@pytest.mark.parametrize(
    "fault",
    ["id110", "optional", "variant_input", "wrong_return_iid", "not_retval", "function_kind"],
)
def test_open_cannot_be_replaced_by_other_namespace_or_looser_signature(metadata, fault):
    dispatch = metadata.rows[(D.DOCS_IID, "dispatch", 7)]
    dual = metadata.rows[(D.DOCS_IID, "dual", 7)]
    if fault == "id110":
        dispatch.memid = 110
    elif fault == "optional":
        dispatch.cParamsOpt = 1
    elif fault == "variant_input":
        dispatch.lprgelemdescParam[0].tdesc = descriptor([12])
    elif fault == "wrong_return_iid":
        dispatch.elemdescFunc.tdesc = descriptor([26, [29, D.SHEETS_IID]])
    elif fault == "not_retval":
        dual.lprgelemdescParam[1]._.paramdesc.wParamFlags = 2
    else:
        dual.funckind = 4
    with pytest.raises(D.DualInvokeError):
        metadata.call._document_declaration(metadata.library, "open_design")


def test_doc_save_hidden_flag_and_void_contract_are_not_ignored(metadata):
    metadata.rows[(D.DOCUMENT_IID, "dispatch", 12)].wFuncFlags = 0
    with pytest.raises(D.DualInvokeError):
        metadata.call._document_declaration(metadata.library, "save_document")
