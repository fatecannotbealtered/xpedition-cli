"""Secondary Layout context must not inherit the Designer's dialog PID."""

from types import SimpleNamespace

from xpedition_cli import native_com_adapter as bridge
from xpedition_cli import native_identity, win_dialogs


def test_layout_prompt_answerer_uses_bound_secondary_pid(monkeypatch):
    monkeypatch.setenv("XPEDITION_EXPECTED_DOMAIN", "schematic")
    monkeypatch.setenv("XPEDITION_EXPECTED_PID", "111")
    monkeypatch.setenv("XPEDITION_EXPECTED_PCB_PID", "222")
    answerer = bridge._PromptAnswerer(interval=0.001)
    observed = []

    def answer(rules, process, **kwargs):
        observed.append(kwargs.get("pid"))
        answerer._stop.set()
        return [], []

    monkeypatch.setattr(win_dialogs, "answer_prompts", answer)
    answerer._run()
    assert observed == [222]


def test_layout_document_property_is_not_called_as_a_default_com_member():
    class Document:
        FullName = "D:/owned/QA.pcb"

        def __call__(self):
            raise AssertionError("ActiveDocument is already a property result")

    class TypeInfo:
        def GetContainingTypeLib(self):
            raise RuntimeError("No fixture typelib metadata")

        def GetTypeAttr(self):
            return SimpleNamespace(cFuncs=1)

        def GetFuncDesc(self, index):
            return [5, None, None, None, 2]

        def GetNames(self, member):
            return ["ActiveDocument"]

    # Metadata absence must not prevent declared getters being discovered.
    info = TypeInfo()
    info.GetContainingTypeLib = lambda: (
        SimpleNamespace(
            GetLibAttr=lambda: ("fixture", 0, 3, 155, 0, 0), GetDocumentation=lambda n: ("fixture",)
        ),
        0,
    )
    app = SimpleNamespace(
        _oleobj_=SimpleNamespace(GetTypeInfo=lambda: info), ActiveDocument=Document()
    )
    result = native_identity.application_identity(app, "pcb")
    assert result["document"]["status"] == "observed"
    assert result["document"]["path"] == "D:/owned/QA.pcb"
