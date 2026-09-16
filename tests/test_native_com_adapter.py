from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xpedition_cli import native_com_adapter as native_adapter
from xpedition_cli.native_com_adapter import (
    AdapterError,
    _apply_designer_operation,
    _com_member,
    _component_location,
    _designer_net_name,
    _designer_snapshot,
    _export_pdf,
    _package_design,
    _packager_messages,
    _quiet_gui,
    _sch2pdf_sheets,
)


class _Collection:
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self._items[index - 1]


class _Connection:
    def __init__(self, number: str, net: str) -> None:
        self.CompPin = SimpleNamespace(Number=number)
        self.Net = SimpleNamespace(Name=net)


class _Component:
    def __init__(self, refdes: str, x: int, y: int, pins: list[tuple[str, str]]) -> None:
        self.Refdes = refdes
        self.Name = refdes
        self._location = SimpleNamespace(X=x, Y=y)
        self._connections = _Collection([_Connection(number, net) for number, net in pins])

    def GetLocation(self) -> object:
        return self._location

    def GetConnections(self) -> _Collection:
        return self._connections


class _Net:
    def __init__(self, name: str, pins: list[tuple[str, str]]) -> None:
        self.Name = name
        self._connections = _Collection(
            [
                SimpleNamespace(
                    CompPin=SimpleNamespace(Number=number, Component=SimpleNamespace(Refdes=refdes))
                )
                for refdes, number in pins
            ]
        )

    def Connections(self) -> _Collection:
        return self._connections


def test_designer_snapshot_maps_components_nets_and_connections() -> None:
    components = [
        _Component("R1", 100, 200, [("1", "3V3")]),
        _Component("C1", 300, 200, [("1", "3V3")]),
    ]
    net = _Net("3V3", [("R1", "1"), ("C1", "1")])

    class App:
        def GetActiveDesign(self) -> str:
            return "demo"

        def DesignComponents(self, *_args: object) -> _Collection:
            return _Collection(components)

        def DesignNets(self, *_args: object) -> _Collection:
            return _Collection([net])

        def SchematicSheetDocuments(self) -> _Collection:
            return _Collection([SimpleNamespace(Name="sheet1", FullName="sheet1")])

    result = _designer_snapshot(App(), {})
    assert result["project"] == "demo"
    assert result["components"][0]["refdes"] == "R1"
    assert result["connections"] == [{"net": "3V3", "pins": ["C1.1", "R1.1"]}]


def test_designer_connect_uses_vendor_add_net() -> None:
    class Block:
        def AddNet(self, *args: object) -> object:
            self.arguments = args
            return SimpleNamespace(
                GetSegments=lambda: _Collection([SimpleNamespace()]),
                AddLabel=lambda *label_args: setattr(self, "label", label_args),
            )

    first = _Component("R1", 0, 0, [("1", "")])
    second = _Component("C1", 100, 0, [("1", "")])

    class App:
        ActiveView = SimpleNamespace(Block=Block())

        def DesignComponents(self, *_args: object) -> _Collection:
            return _Collection([first, second])

    client = SimpleNamespace(constants=SimpleNamespace(VD_WIRE=7))
    pending = {"3V3": {"type": "create_net", "name": "3V3"}}
    result = _apply_designer_operation(
        App(),
        {"type": "connect", "net": "3V3", "pins": ["R1.1", "C1.1"]},
        client,
        "demo",
        pending,
    )
    assert result == {
        "type": "connect",
        "net": "3V3",
        "pins": ["R1.1", "C1.1"],
        "applied": True,
    }
    assert not pending


def test_component_location_reads_the_property_form() -> None:
    """Current Xpedition Standard exposes GetLocation as a property, not a method."""
    component = SimpleNamespace(GetLocation=SimpleNamespace(X=2000, Y=1500))
    assert _component_location(component) == {"x": 2000, "y": 1500}


def test_component_location_falls_back_to_the_method_form() -> None:
    component = _Component("R1", 100, 200, [])
    assert _component_location(component) == {"x": 100, "y": 200}


def test_component_location_is_empty_when_unavailable() -> None:
    assert _component_location(SimpleNamespace()) == {"x": None, "y": None}


def test_place_component_reports_the_orphan_when_refdes_assignment_fails() -> None:
    class Placed:
        GetLocation = SimpleNamespace(X=40, Y=60)

        def __setattr__(self, name: str, value: object) -> None:
            raise RuntimeError("Unable to add reference designator")

    class Block:
        def AddPartInstance(self, *_args: object) -> Placed:
            return Placed()

    class App:
        ActiveView = SimpleNamespace(Block=Block())

    with pytest.raises(AdapterError) as caught:
        _apply_designer_operation(
            App(),
            {
                "type": "place_component",
                "refdes": "R1",
                "library": "Resistors",
                "device_name": "R",
                "symbol_name": "R",
                "x": 40,
                "y": 60,
            },
            SimpleNamespace(),
            "demo",
            {},
        )
    details = caught.value.details
    assert details["orphan_placed"] is True
    assert details["orphan_library"] == "Resistors"
    assert (details["orphan_x"], details["orphan_y"]) == (40, 60)
    assert details["requested_refdes"] == "R1"
    assert "orphan_library" in details["_untrusted"]


class _Gui:
    """Layout's suppression members are write-only properties."""

    def __init__(self, rejects: set[str] | None = None) -> None:
        object.__setattr__(self, "_rejects", rejects or set())
        object.__setattr__(self, "written", {})

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._rejects:
            raise RuntimeError(f"{name} is not supported on this release")
        self.written[name] = value


def test_quiet_gui_sets_every_layout_suppression_switch() -> None:
    gui = _Gui()
    applied = _quiet_gui(SimpleNamespace(Gui=gui), "pcb")
    assert gui.written == {
        "SuppressTrivialDialogs": True,
        "SuppressNotepadDialogs": True,
        "SuppressVariantDataOutOfDateDialog": True,
        "SingleThreaded": True,
    }
    assert all(applied.values())


def test_quiet_gui_reports_switches_an_older_release_rejects() -> None:
    gui = _Gui(rejects={"SuppressVariantDataOutOfDateDialog"})
    applied = _quiet_gui(SimpleNamespace(Gui=gui), "pcb")
    assert applied["SuppressVariantDataOutOfDateDialog"] is False
    assert applied["SuppressTrivialDialogs"] is True


def test_quiet_gui_leaves_designer_alone() -> None:
    """Only Layout exposes these; Designer must not be touched."""
    gui = _Gui()
    assert _quiet_gui(SimpleNamespace(Gui=gui), "schematic") == {}
    assert gui.written == {}


def test_quiet_gui_survives_an_application_without_a_gui() -> None:
    assert _quiet_gui(SimpleNamespace(), "pcb") == {}


def test_com_member_prefers_the_property_form() -> None:
    """Several Xpedition accessors are properties; calling them raises."""

    def _explode() -> None:
        raise RuntimeError("DISP_E_PARAMNOTOPTIONAL")

    collection = _Collection([1, 2])
    assert _com_member(SimpleNamespace(GetConnections=collection), "GetConnections") is collection
    assert _com_member(SimpleNamespace(GetConnections=_explode), "GetConnections") is _explode


def test_com_member_falls_back_to_the_method_form() -> None:
    collection = _Collection([1])
    holder = SimpleNamespace(GetConnections=lambda: collection)
    assert _com_member(holder, "GetConnections") is collection


def test_com_member_is_none_when_the_member_is_absent() -> None:
    assert _com_member(SimpleNamespace(), "GetConnections") is None


class _Label:
    def __init__(self, text: str) -> None:
        self.TextString = text


class _NamedNet:
    """A Designer net carries its name on a label attached to one of its segments."""

    def __init__(self, text: str | None, segments: int = 2) -> None:
        self._label = _Label(text) if text is not None else None
        self.GetSegments = _Collection([object() for _ in range(segments)])

    def GetLabel(self, _segment: object) -> object | None:
        return self._label


def test_designer_net_name_reads_the_segment_label() -> None:
    assert _designer_net_name(_NamedNet("3V3")) == "3V3"


def test_designer_net_name_prefers_a_direct_name_when_present() -> None:
    net = _NamedNet("3V3")
    net.Name = "VCC"
    assert _designer_net_name(net) == "VCC"


def test_designer_net_name_is_empty_for_an_unlabelled_net() -> None:
    assert _designer_net_name(_NamedNet(None)) == ""


def test_designer_net_name_survives_a_net_without_segments() -> None:
    assert _designer_net_name(SimpleNamespace()) == ""


def test_schematic_placement_requires_an_explicit_library() -> None:
    """No MISC fallback: a stock install has no such library, so the default only
    produced Designer error 2005 instead of naming the missing parameter."""

    class App:
        ActiveView = SimpleNamespace(Block=SimpleNamespace())

    with pytest.raises(AdapterError) as caught:
        _apply_designer_operation(
            App(),
            {"type": "place_component", "refdes": "R1", "device_name": "R", "symbol_name": "R"},
            SimpleNamespace(),
            "demo",
            {},
        )
    assert caught.value.code == "E_CHANGESET_INVALID"
    assert caught.value.details["missing"] == ["library"]


PACKAGER_LOG = """
                                    Packager
     Common Data Base has been read

     Target PDB Name: Integration\LocalPartsDB.pdb
     ERROR:  There is no Part Number: R in the Parts
      DataBase for symbols with Part Name: R and Part Label: (null).

     ERROR:  There is no Part Number: C in the Parts

     Testing of Packaging is being terminated with 2 errors and 0 warnings.
      Design has NOT been packaged.
"""


def test_packager_messages_extract_errors_and_verdict(tmp_path) -> None:
    log = tmp_path / "PartPkg.log"
    log.write_text(PACKAGER_LOG, encoding="utf-8")
    errors, verdict = _packager_messages(log)
    assert errors == [
        "ERROR:  There is no Part Number: R in the Parts",
        "ERROR:  There is no Part Number: C in the Parts",
    ]
    assert verdict == "Design has NOT been packaged."


def test_packager_messages_tolerate_a_missing_log(tmp_path) -> None:
    assert _packager_messages(tmp_path / "absent.log") == ([], "")


def test_package_requires_a_project_path() -> None:
    with pytest.raises(AdapterError) as caught:
        _package_design({})
    assert caught.value.code == "E_USAGE"


def test_package_reports_a_missing_project_file(tmp_path) -> None:
    with pytest.raises(AdapterError) as caught:
        _package_design({"project": str(tmp_path / "absent.prj")})
    assert caught.value.code == "E_NOT_FOUND"


def test_sch2pdf_sheets_parses_printed_lines() -> None:
    stdout = (
        "SCH2PDF - V6.0\nOrdering pages\nCreating the PDF\n"
        "Printed Schematic1 Sheet 1\nPrinted Schematic1 Sheet 2\nSaving document\n"
    )
    assert _sch2pdf_sheets(stdout) == ["Schematic1 Sheet 1", "Schematic1 Sheet 2"]


def test_export_pdf_requires_a_project_path() -> None:
    with pytest.raises(AdapterError) as caught:
        _export_pdf({})
    assert caught.value.code == "E_USAGE"


def test_export_pdf_reports_a_missing_project_file(tmp_path) -> None:
    with pytest.raises(AdapterError) as caught:
        _export_pdf({"project": str(tmp_path / "absent.prj")})
    assert caught.value.code == "E_NOT_FOUND"


def test_export_pdf_refuses_to_replace_an_existing_file(tmp_path) -> None:
    project = tmp_path / "case.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    existing = tmp_path / "case.pdf"
    existing.write_bytes(b"%PDF-1.4")
    with pytest.raises(AdapterError) as caught:
        _export_pdf({"project": str(project)})
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["path"] == str(existing.resolve())


def test_export_pdf_rejects_a_non_pdf_output_and_bad_colours(tmp_path) -> None:
    project = tmp_path / "case.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    with pytest.raises(AdapterError) as caught:
        _export_pdf({"project": str(project), "output": str(tmp_path / "case.svg")})
    assert caught.value.code == "E_VALIDATION"
    with pytest.raises(AdapterError) as caught:
        _export_pdf({"project": str(project), "output": str(tmp_path / "out.pdf"), "color": 9})
    assert caught.value.code == "E_VALIDATION"


def test_export_pdf_runs_sch2pdf_through_the_launcher(tmp_path, monkeypatch) -> None:
    sdd_home = tmp_path / "sdd"
    executable = sdd_home / "common" / "win64" / "bin" / "sch2pdf.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    project = tmp_path / "case.prj"
    project.write_text("KEY DesignName Board1\n", encoding="utf-8")
    output = tmp_path / "out" / "case.pdf"
    calls: dict[str, object] = {}

    def fake_run(arguments, **kwargs):
        calls["arguments"] = list(arguments)
        calls["cwd"] = kwargs.get("cwd")
        Path(arguments[arguments.index("-a") + 1]).write_bytes(b"%PDF-1.4 fake")
        return SimpleNamespace(
            returncode=0,
            stdout=b"SCH2PDF - V6.0\nPrinted Schematic1 Sheet 1\nSaving document\n",
            stderr=b"",
        )

    monkeypatch.setattr(native_adapter, "_configure_environment", lambda: sdd_home)
    monkeypatch.setattr(native_adapter.subprocess, "run", fake_run)
    result = _export_pdf(
        {"project": str(project), "output": str(output), "color": 1, "schematic": "Schematic1"}
    )
    assert result["exported"] is True
    assert result["sheets"] == ["Schematic1 Sheet 1"]
    assert result["size"] > 0 and result["path"] == str(output.resolve())
    arguments = calls["arguments"]
    assert arguments[0] == str(executable)
    assert arguments[arguments.index("-project") + 1] == str(project.resolve())
    assert arguments[arguments.index("-c") + 1] == "1"
    assert arguments[arguments.index("-schematic") + 1] == "Schematic1"
    assert calls["cwd"] == str(executable.parent)


def test_annotation_summary_reads_counts_errors_and_the_verdict(tmp_path) -> None:
    log = tmp_path / "ForwardAnnotation.txt"
    log.write_bytes(
        b"     Clustering 39 Symbols:\r\n"
        b"     WARNING:  \r\n"
        b"\tUnable to update the cell 'CLI_SOIC4' in the Library Manager.\r\n"
        b"     18 nets were found containing 95 pins\r\n"
        b"     39 components were found\r\n"
        b"     ERROR:  Cell CLI_SOIC4 has 4 unique Alphanumeric Pin Numbers while\r\n"
        b"      Part Number XC6206-3.3 has 3.\r\n"
        b"     DataBase Load is being terminated with 1 errors and 2 warnings.\r\n"
    )
    summary = native_adapter._annotation_summary(log)
    assert summary["components"] == 39 and summary["nets"] == 18 and summary["pins"] == 95
    assert summary["errors"] == [
        "ERROR:  Cell CLI_SOIC4 has 4 unique Alphanumeric Pin Numbers while"
    ]
    assert summary["warnings"] == ["WARNING:"]
    assert summary["completed"] is False
    log.write_bytes(b"Forward-Annotation on the Layout Design has been successfully completed.\r\n")
    assert native_adapter._annotation_summary(log)["completed"] is True
    assert native_adapter._annotation_summary(tmp_path / "none.txt")["completed"] is False


def test_jobwizard_messages_find_the_copy_count_and_failures(tmp_path) -> None:
    log = tmp_path / "jobwizard.log"
    log.write_bytes("Job Wizard\r\n\r\nSuccessfully copied 58 file(s).\r\n".encode("mbcs"))
    assert native_adapter._jobwizard_messages(log) == ([], 58)
    log.write_bytes("Job Wizard\r\n未能从通用数据库中获取设计信息。\r\n".encode("mbcs"))
    errors, copied = native_adapter._jobwizard_messages(log)
    assert errors == ["未能从通用数据库中获取设计信息。"] and copied is None
    assert native_adapter._jobwizard_messages(tmp_path / "none.log") == ([], None)


_PRJ_TEXT = (
    "SECTION DesignInfo\n"
    'KEY CentralLibrary "RcLib\\TemplateLibrary.lmc"\n'
    "ENDSECTION\n"
    "SECTION iCDB\n"
    "LIST Designs\n"
    'VALUE "Schematic1"\n'
    'VALUE "Board1"\n'
    "ENDLIST\n"
    "ENDSECTION\n"
    "SECTION Board1\n"
    "LIST PDBs\n"
    'VALUE "PartsDBLibs\\Resistors.pdb"\n'
    'VALUE "PartsDBLibs\\PartQuest.pdb"\n'
    "ENDLIST\n"
    'KEY ConfigType "PCB"\n'
    'KEY PCBDesignPath "{pcb}"\n'
    "ENDSECTION\n"
)


def test_layout_board_path_resolves_a_project_to_its_board(tmp_path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_PRJ_TEXT.format(pcb=""), encoding="utf-8")
    with pytest.raises(AdapterError) as missing:
        native_adapter._layout_board_path({"project": str(project)})
    assert missing.value.code == "E_NOT_FOUND"
    assert "pcb create" in str(missing.value.details.get("hint"))
    board = tmp_path / "PCB" / "demo.pcb"
    board.parent.mkdir()
    board.write_bytes(b"")
    project.write_text(_PRJ_TEXT.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    assert native_adapter._layout_board_path({"project": str(project)}) == board.resolve()
    assert native_adapter._layout_board_path({"project": str(board)}) == board.resolve()
    with pytest.raises(AdapterError) as usage:
        native_adapter._layout_board_path({"project": str(tmp_path / "x.txt")})
    assert usage.value.code == "E_USAGE"
    with pytest.raises(AdapterError) as none:
        native_adapter._layout_board_path({})
    assert none.value.code == "E_USAGE"


def test_sync_prj_cells_registers_the_cell_partitions_the_library_has(tmp_path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_PRJ_TEXT.format(pcb=""), encoding="utf-8")
    library = tmp_path / "RcLib"
    (library / "CellDBLibs").mkdir(parents=True)
    (library / "CellDBLibs" / "PartQuest.cel").write_bytes(b"")
    assert native_adapter._sync_prj_cells(project, library) == ["CellDBLibs\\PartQuest.cel"]
    text = project.read_text(encoding="utf-8")
    assert 'LIST 2dCellLibraries\nVALUE "CellDBLibs\\PartQuest.cel"\nENDLIST\n' in text
    assert native_adapter._sync_prj_cells(project, library) == []
    (library / "CellDBLibs" / "Resistors.cel").write_bytes(b"")
    assert native_adapter._sync_prj_cells(project, library) == ["CellDBLibs\\Resistors.cel"]


def test_pcb_create_refuses_a_design_that_already_has_a_board(tmp_path) -> None:
    project = tmp_path / "demo.prj"
    project.write_text(_PRJ_TEXT.format(pcb="PCB\\demo.pcb"), encoding="utf-8")
    with pytest.raises(AdapterError) as conflict:
        native_adapter._pcb_create({"project": str(project)}, None)
    assert conflict.value.code == "E_CONFLICT"
    project.write_text(_PRJ_TEXT.format(pcb=""), encoding="utf-8")
    (tmp_path / "PCB").mkdir()
    (tmp_path / "PCB" / "demo.pcb").write_bytes(b"")
    with pytest.raises(AdapterError) as existing:
        native_adapter._pcb_create({"project": str(project)}, None)
    assert existing.value.code == "E_CONFLICT"
    with pytest.raises(AdapterError) as bad:
        native_adapter._pcb_create({"project": str(project), "name": "no good"}, None)
    assert bad.value.code == "E_USAGE"
    with pytest.raises(AdapterError) as nowhere:
        native_adapter._pcb_create({"project": str(tmp_path / "gone.prj")}, None)
    assert nowhere.value.code == "E_NOT_FOUND"
    with pytest.raises(AdapterError) as none:
        native_adapter._pcb_create({}, None)
    assert none.value.code == "E_USAGE"


def test_route_passes_parse_names_and_effort_ranges() -> None:
    passes = native_adapter.parse_route_passes("route:1-5, viamin, smooth:2")
    assert [(p["pass"], p["type"], p["effort"]) for p in passes] == [
        ("route", 8, [1, 5]),
        ("viamin", 14, [1, 3]),
        ("smooth", 10, [2, 2]),
    ]
    assert native_adapter.parse_route_passes("")[0]["pass"] == "route"
    for bad in ("tune", "route:0-3", "route:2-9", "route:x"):
        with pytest.raises(AdapterError) as error:
            native_adapter.parse_route_passes(bad)
        assert error.value.code == "E_USAGE"


def test_route_layers_parse_to_sorted_numbers_or_nothing() -> None:
    from xpedition_cli.native_com_adapter import AdapterError, parse_route_layers

    assert parse_route_layers("4,1,4") == [1, 4]
    assert parse_route_layers([1, "2"]) == [1, 2]
    assert parse_route_layers("") == [] and parse_route_layers(None) == []
    with pytest.raises(AdapterError):
        parse_route_layers("1,top")


def test_rounded_outline_points_carry_the_arc_centre_with_a_negative_radius() -> None:
    from xpedition_cli.native_com_adapter import _rounded_points

    xs, ys, rs = _rounded_points(0.0, 0.0, 60.0, 45.0, 3.0)
    assert len(xs) == 13 and (xs[0], ys[0], rs[0]) == (57.0, 0.0, 0.0)
    assert [i for i, r in enumerate(rs) if r] == [1, 4, 7, 10] and set(r for r in rs if r) == {-3.0}
    assert (xs[1], ys[1]) == (57.0, 3.0)  # the centre of the first corner's arc
    assert (xs[-1], ys[-1]) == (xs[0], ys[0])  # closed
    plain = _rounded_points(0.0, 0.0, 60.0, 45.0, 0.0)
    assert len(plain[0]) == 5 and not any(plain[2])


def test_top_view_scheme_fills_planes_and_hides_inner_layers() -> None:
    from xpedition_cli.native_com_adapter import top_view_scheme_text

    all_on = (
        "    ..Pad_Layer_On ( \n\tT T T T T T T T T T \t! [1 - 10]\n\t)\n"
        "    ..Trace_Layer_On ( \n\tT T T T T T T T T T \t! [1 - 10]\n\t)\n"
        "    ..Assy_Ref_Des_On                    True \t! top\n"
        "    ..Assy_Ref_Des_On                    True \t! bottom\n"
        "    ..Silk_Ref_Des_On                    True \t! top\n"
        '\t"Option.Planes.Data.Fill" "0"\n'
        '\t"LayerControl.1" "d:1" "e:1"\n\t"LayerControl.2" "d:1" "e:1"\n'
        '\t"LayerControl.3" "d:1" "e:1"\n\t"LayerControl.4" "d:1" "e:1"\n'
        '\t"Fabrication.Assembly.Part.Text.RefDes.Top" "d:1" "e:1"\n'
        '\t"Fabrication.Silkscreen.Part.Text.RefDes.Top" "d:1" "e:1"\n'
        '\t"Fabrication.DrillDrawingThrough" "d:1" "e:1"\n'
        '\t"Option.Fabrication.AssemblyItems.Top" "1"\n'
        '\t"Place.Part.Text.RefDes.Top" "d:1" "e:1"\n\t"Part.Cell.Origin.Top" "d:1" "e:1"\n'
        '\t"Option.Pin.Number.Top" "1"\n'
    )
    out = top_view_scheme_text(all_on, 4)
    assert '"Option.Planes.Data.Fill" "1"' in out
    assert '"LayerControl.1" "d:1"' in out and '"LayerControl.4" "d:1"' in out
    assert '"LayerControl.2" "d:0"' in out and '"LayerControl.3" "d:0"' in out
    assert '"Fabrication.Assembly.Part.Text.RefDes.Top" "d:0" "e:0"' in out
    assert '"Fabrication.Silkscreen.Part.Text.RefDes.Top" "d:1"' in out
    assert '"Fabrication.DrillDrawingThrough" "d:0" "e:0"' in out
    assert '"Option.Fabrication.AssemblyItems.Top" "0"' in out
    assert '"Place.Part.Text.RefDes.Top" "d:0" "e:0"' in out
    assert '"Part.Cell.Origin.Top" "d:0" "e:0"' in out
    assert '"Option.Pin.Number.Top" "0"' in out
    assert "T F F T T T T T T T" in out.split("Pad_Layer_On")[1]
    assert "T F F T T T T T T T" in out.split("Trace_Layer_On")[1]
    assert out.count("..Assy_Ref_Des_On                    False") == 2
    assert "..Silk_Ref_Des_On                    True" in out
    # a two-layer board keeps both layers
    assert "T T T T T T T T T T" in top_view_scheme_text(all_on, 2).split("Trace_Layer_On")[1]


def test_backup_layout_folder_zips_the_board_and_skips_logs(tmp_path) -> None:
    """`pcb create --replace` deletes a whole layout folder, so it is archived first."""
    import zipfile

    from xpedition_cli.native_com_adapter import _backup_layout_folder

    folder = tmp_path / "PCB"
    (folder / "Layout").mkdir(parents=True)
    (folder / "LogFiles").mkdir()
    (folder / "Board.pcb").write_text("board")
    (folder / "Layout" / "placement.dat").write_text("parts")
    (folder / "Layout" / "placement.dat.bak").write_text("older")
    (folder / "LogFiles" / "drill.txt").write_text("noise")

    archive = _backup_layout_folder(folder)

    assert archive is not None
    names = set(zipfile.ZipFile(archive).namelist())
    assert names == {"PCB/Board.pcb", "PCB/Layout/placement.dat"}
    assert folder.is_dir()  # the archive is written before anything is removed


def test_backup_layout_folder_answers_none_without_a_board(tmp_path) -> None:
    from xpedition_cli.native_com_adapter import _backup_layout_folder

    assert _backup_layout_folder(tmp_path / "missing") is None
    empty = tmp_path / "PCB"
    empty.mkdir()
    assert _backup_layout_folder(empty) is None
