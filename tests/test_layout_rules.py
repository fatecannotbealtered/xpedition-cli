"""constraint get and constraint import (adapter/layout_rules.py) on a fake Constraint Manager model."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from test_layout_fakes import BOARD, Progress, context  # noqa: E402

from xpedition_cli._driver import AdapterError  # noqa: E402
from xpedition_cli.adapter import layout as L  # noqa: E402
from xpedition_cli.adapter import layout_rules as LR  # noqa: E402
from xpedition_cli.adapter.plan import rules as R  # noqa: E402

LAYERS = 2


class Items(list):
    @property
    def Count(self) -> int:  # noqa: N802 - COM name
        return len(self)

    def Item(self, key: Any) -> Any:  # noqa: N802
        if isinstance(key, int):
            return self[key - 1]
        return next(item for item in self if item.Name == key)


class Constraint:
    def __init__(self, kind: int, value: float) -> None:
        self.ConstraintType, self.Value = kind, value


class Constraints(Items):
    def AddByType(self, kind: int, value: float) -> Constraint:  # noqa: N802
        item = Constraint(kind, value)
        self.append(item)
        return item


class RuleLayer:
    def __init__(self, name: str, values: dict[int, float]) -> None:
        self.Name = name
        self.constraints = Constraints(Constraint(k, v) for k, v in values.items())

    def GetConstraints(self, mask: int) -> Constraints:  # noqa: N802
        return self.constraints


def layers(values: dict[int, float]) -> Items:
    return Items(RuleLayer(f"SIGNAL_{n}", dict(values)) for n in range(1, LAYERS + 1))


WIDTHS = {number: 10.0 for number in R.WIDTH_FIELDS.values()}
CLEARS = {number: 10.0 for number in R.CLEARANCE_FIELDS.values()}


class FakeClass:
    def __init__(self, design: FakeDesign, name: str) -> None:
        self.design, self.Name = design, name

    def AssignNet(self, net: SimpleNamespace) -> None:  # noqa: N802
        net.NetClass = self


class NamedLayers:
    def __init__(self, name: str, values: dict[int, float]) -> None:
        self.Name, self.Layers = name, layers(values)


class ClassList(Items):
    def __init__(self, design: FakeDesign) -> None:
        super().__init__()
        self.design = design

    def Add(self, name: str) -> FakeClass:  # noqa: N802
        item = FakeClass(self.design, name)
        self.append(item)
        self.design.scheme.NetClasses.append(NamedLayers(name, WIDTHS))
        return item


class RuleList(Items):
    def __init__(self, design: FakeDesign) -> None:
        super().__init__()
        self.design = design

    def Add(self, name: str) -> SimpleNamespace:  # noqa: N802
        rule = NamedLayers(name, CLEARS)
        self.append(rule)
        self.design.scheme.ClearanceRules.append(rule)
        return rule


class PairList(Items):
    def Add(self, first: SimpleNamespace, second: SimpleNamespace) -> SimpleNamespace:  # noqa: N802
        pair = SimpleNamespace(FirstNet=first, SecondNet=second, Name=R.pair_name([first.Name, second.Name]))
        self.append(pair)
        return pair


class ClassClearanceList(Items):
    def Add(self, kind: int, name: str, scheme: Any, source: Any, target: Any, rule: Any) -> SimpleNamespace:  # noqa: N802
        item = SimpleNamespace(Name=name, SourceNetClass=source, TargetNetClass=target, ClearanceRule=rule)
        self.append(item)
        return item


class FakeDesign:
    def __init__(self) -> None:
        self.scheme = SimpleNamespace(Name="(Master)", NetClasses=Items(), ClearanceRules=Items(), ClassClearances=ClassClearanceList())
        self.NetClasses = ClassList(self)
        self.ClearanceRules = RuleList(self)
        self.DifferentialPairs = PairList()
        self.PhysicalRules = SimpleNamespace(Schemes=Items([self.scheme]), ClassClearances=self.scheme.ClassClearances)
        default = self.NetClasses.Add(R.DEFAULT_CLASS)
        rule = self.ClearanceRules.Add(R.DEFAULT_RULE)
        self.scheme.ClassClearances.append(SimpleNamespace(Name="(Default)", SourceNetClass=None, TargetNetClass=None,
                                                           ClearanceRule=rule))
        self.nets = [SimpleNamespace(Name=name, NetClass=default) for name in ("A_P", "A_N", "VBUS", "GND")]


class Session:
    """The patches that put a fake design and board behind the handlers."""

    def __init__(self, state: str = "in_sync") -> None:
        self.design = FakeDesign()
        self.state = state
        self.loads = 0
        self.unloads = 0
        self.doc = SimpleNamespace(LayerCount=LAYERS)

    @contextmanager
    def ces(self, prj: str, name: str):
        yield self.design
        self.unloads += 1

    def load(self, doc: Any) -> bool:
        self.loads += 1
        self.state = "in_sync"
        return True

    def __enter__(self) -> Session:
        self.patches = [
            mock.patch.object(LR, "ces_design", self.ces),
            mock.patch.object(LR, "_nets", lambda design: design.nets),
            mock.patch.object(LR, "board_project", lambda path: ("D:/work/Demo.prj", "Board1")),
            mock.patch.object(LR, "load_state", lambda doc: self.state),
            mock.patch.object(LR, "load_into_layout", self.load),
            mock.patch.object(L, "in_layout", lambda ctx, work, **kw: work(None, self.doc)),
        ]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        for patch in reversed(self.patches):
            patch.stop()


def rules_file(folder: str, data: dict[str, Any]) -> str:
    path = Path(folder) / "rules.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


USB = {"net_classes": [{"name": "USB", "nets": ["A_P", "A_N"],
                        "layers": {"all": {"width_mm": 0.1, "min_width_mm": 0.08, "differential_spacing_mm": 0.12}}}],
       "differential_pairs": [{"nets": ["A_P", "A_N"]}],
       "clearances": [{"rule": "FINE", "layers": {"all": {"trace_to_pad_mm": 0.1}}, "classes": [["USB", "(All)"]]}]}


class ConstraintGetTest(unittest.TestCase):
    def test_the_summary_and_the_whole_file(self) -> None:
        with Session() as session, tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "rules.json")
            out = LR.ConstraintGet().read(context("constraint get", {"output": target}))
            self.assertEqual(out["nets"], 4)
            self.assertEqual(out["net_classes"], [{"name": "(Default)", "nets": 4, "layers": {
                "1": {"differential_spacing_mm": 0.254, "max_width_mm": 0.254, "min_width_mm": 0.254, "width_mm": 0.254},
                "2": {"differential_spacing_mm": 0.254, "max_width_mm": 0.254, "min_width_mm": 0.254, "width_mm": 0.254}}}])
            self.assertEqual(out["clearance_rules"], ["(Default Rule)"])
            self.assertEqual(out["class_clearances"], [{"from": "(All)", "to": "(All)", "rule": "(Default Rule)"}])
            self.assertTrue(out["loaded"])
            written = json.loads(Path(out["output"]).read_text(encoding="utf-8"))
            self.assertEqual(written["net_classes"][0]["nets"], ["A_N", "A_P", "GND", "VBUS"])
            self.assertEqual(session.unloads, 1)
            # the file it writes imports with no change
            self.assertTrue(R.plan(R.parse(written), LR.read_current(session.design, LAYERS)).empty())


class ConstraintImportTest(unittest.TestCase):
    def test_preview_lists_every_change_and_loading(self) -> None:
        with Session(), tempfile.TemporaryDirectory() as folder:
            preview = LR.ConstraintImport().preview(context("constraint import", {"file": rules_file(folder, USB)}))
        self.assertTrue(preview["can_apply"])
        keyed = {(c["target"].split("#", 1)[1], c["field"]): (c["before"], c["after"]) for c in preview["changes"]}
        self.assertEqual(keyed[("/constraints/net-classes/USB", "exists")], (False, True))
        self.assertEqual(keyed[("/constraints/nets/A_P", "net_class")], ("(Default)", "USB"))
        self.assertEqual(keyed[("/constraints/net-classes/USB/layers/1", "width_mm")], (None, 0.1))
        self.assertEqual(keyed[("/constraints/differential-pairs/A_N,A_P", "exists")], (False, True))
        self.assertEqual(keyed[("/constraints/class-clearances/USB/(All)", "rule")], (None, "FINE"))  # no pair of its own yet
        self.assertEqual(keyed[("/constraints", "loaded")], (True, True))

    def test_changes_layout_has_not_loaded_block_the_import(self) -> None:
        with Session(state="required"), tempfile.TemporaryDirectory() as folder:
            preview = LR.ConstraintImport().preview(context("constraint import", {"file": rules_file(folder, USB)}))
        self.assertFalse(preview["can_apply"])
        self.assertIn("holds changes Layout has not loaded yet", preview["blocked_by"][-1])

    def test_run_writes_loads_and_reads_back(self) -> None:
        with Session() as session, tempfile.TemporaryDirectory() as folder:
            ctx = context("constraint import", {"file": rules_file(folder, USB)})
            preview = LR.ConstraintImport().preview(ctx)
            facts = LR.ConstraintImport().run(ctx, Progress())
            self.assertEqual((facts["change"], facts["persistence"]), ("applied", "unchanged"))  # Layout keeps them with the board
            self.assertEqual(facts["result"]["net_classes_created"], ["USB"])
            self.assertEqual(facts["result"]["differential_pairs_created"], ["A_N,A_P"])
            self.assertTrue(facts["result"]["loaded"])
            self.assertEqual(session.loads, 1)
            usb = session.design.scheme.NetClasses.Item("USB").Layers.Item(1).GetConstraints(7)
            self.assertAlmostEqual(next(c.Value for c in usb if c.ConstraintType == 439), R.th(0.1))
            targets = sorted({c["target"] for c in preview["changes"]})
            fields = sorted({c["field"] for c in preview["changes"]})
            back = LR.ConstraintImport().read_back(context("constraint import", {"file": "x"}, targets=targets, fields=fields))
        self.assertEqual(back["unreadable"], [])
        for change in preview["changes"]:
            self.assertEqual(back["observed"][change["target"]][change["field"]], change["after"], change)

    def test_the_save_reads_the_constraints_back_too(self) -> None:
        # the import leaves them unsaved with the board: the next doc save lists them and reads them from the saved board
        with Session() as session, tempfile.TemporaryDirectory() as folder:
            ctx = context("constraint import", {"file": rules_file(folder, USB)})
            preview = LR.ConstraintImport().preview(ctx)
            LR.ConstraintImport().run(ctx, Progress())
            targets = sorted({c["target"] for c in preview["changes"]})
            fields = sorted({c["field"] for c in preview["changes"]})
            back = L.read_keys(session.doc, BOARD, targets, fields)
        self.assertEqual(back["unreadable"], [])
        for change in preview["changes"]:
            self.assertEqual(back["observed"][change["target"]][change["field"]], change["after"], change)

    def test_a_file_that_matches_changes_nothing(self) -> None:
        with Session() as session, tempfile.TemporaryDirectory() as folder:
            ctx = context("constraint import", {"file": rules_file(folder, USB)})
            LR.ConstraintImport().run(ctx, Progress())
            preview = LR.ConstraintImport().preview(ctx)
            facts = LR.ConstraintImport().run(ctx, Progress())
        self.assertEqual(preview["changes"], [])
        self.assertEqual(facts["change"], "not_applied")
        self.assertEqual(session.loads, 1)

    def test_a_missing_or_malformed_file(self) -> None:
        with Session(), tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(AdapterError) as missing:
                LR.ConstraintImport().preview(context("constraint import", {"file": os.path.join(folder, "none.json")}))
            bad = Path(folder) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            with self.assertRaises(AdapterError) as broken:
                LR.ConstraintImport().preview(context("constraint import", {"file": str(bad)}))
        self.assertEqual(missing.exception.data["code"], "E_NOT_FOUND")
        self.assertEqual(broken.exception.data["code"], "E_VALIDATION")

    def test_a_blocked_import_does_not_write(self) -> None:
        with Session() as session, tempfile.TemporaryDirectory() as folder:
            data = {"net_classes": [{"name": "USB", "nets": ["NOPE"]}]}
            with self.assertRaises(AdapterError) as caught:
                LR.ConstraintImport().run(context("constraint import", {"file": rules_file(folder, data)}), Progress())
        self.assertEqual(caught.exception.data.get("conflict"), "application_state")
        self.assertEqual(session.loads, 0)
        self.assertEqual([c.Name for c in session.design.NetClasses], ["(Default)"])


class SavedChangesTest(unittest.TestCase):
    def test_a_change_the_application_saved_is_not_noted_as_unsaved(self) -> None:
        from xpedition_cli.adapter import Adapter, sessions

        handler = SimpleNamespace(command="constraint import", answers=(), long=lambda ctx: False,
                                  apply=lambda ctx: {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
                                                     "persistence": "saved"})
        adapter = Adapter.__new__(Adapter)
        adapter.store = mock.Mock()
        request = {"command": "constraint import", "inputs": {}, "session": "layout-1-1", "document": BOARD}
        with mock.patch.object(Adapter, "_handler", lambda self, req: handler), \
                mock.patch.object(sessions, "note_change") as noted:
            adapter.apply(request)
        noted.assert_not_called()


if __name__ == "__main__":
    unittest.main()
