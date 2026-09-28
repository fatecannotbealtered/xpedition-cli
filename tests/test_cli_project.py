"""project create | info."""

from __future__ import annotations


def _clone(params: dict) -> dict:
    return {
        "project": "New",
        "path": params["project"],
        "template": params["template"],
        "created": True,
        "files": 264,
        "bytes": 1000,
        "rewritten": ["CentralLibrary", "DBCFile"],
        "parts_databases": [],
        "template_closed": False,
        "opened": True,
    }


def test_create_previews_the_copy_then_clones_the_template(cli, adapter, project, tmp_path) -> None:
    target = tmp_path / "new" / "New.prj"
    adapter.on("clone_project", _clone)
    code, payload = cli(
        "project", "create", "--template", str(project), "--project", str(target), "--dry-run"
    )
    preview = payload["data"]["preview"]
    assert code == 0 and [change["action"] for change in preview["changes"]] == [
        "copy_project_folder",
        "rename_project_file",
        "rewrite_library_keys",
        "open_project",
    ]
    assert adapter.calls == []  # the preview is pure file inspection
    token = payload["data"]["confirm_token"]
    code, payload = cli(
        "project",
        "create",
        "--template",
        str(project),
        "--project",
        str(target),
        "--confirm",
        token,
    )
    assert code == 0 and payload["data"]["created"] is True
    assert adapter.last("clone_project") == {"template": str(project), "project": str(target)}


def test_create_refuses_what_designer_could_not_use(cli, adapter, project, tmp_path) -> None:
    cases = [
        (tmp_path / "missing.prj", tmp_path / "a" / "A.prj", 3),  # no template
        (project, tmp_path / "b" / "B.txt", 2),  # not a .prj
        (project, tmp_path / "项目" / "C.prj", 2),  # not ASCII
        (project, project, 6),  # exists
    ]
    for template, target, code_expected in cases:
        code, payload = cli(
            "project", "create", "--template", str(template), "--project", str(target), "--dry-run"
        )
        assert code == code_expected, (template, target, payload)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "other.txt").write_text("x", encoding="utf-8")
    code, payload = cli(
        "project",
        "create",
        "--template",
        str(project),
        "--project",
        str(busy / "D.prj"),
        "--dry-run",
    )
    assert code == 6 and "not empty" in payload["error"]["message"]
    assert adapter.calls == []


def test_create_token_binds_the_destination(cli, adapter, project, tmp_path) -> None:
    _, payload = cli(
        "project",
        "create",
        "--template",
        str(project),
        "--project",
        str(tmp_path / "x" / "X.prj"),
        "--dry-run",
    )
    token = payload["data"]["confirm_token"]
    code, payload = cli(
        "project",
        "create",
        "--template",
        str(project),
        "--project",
        str(tmp_path / "y" / "Y.prj"),
        "--confirm",
        token,
    )
    assert code == 6 and payload["error"]["code"] == "E_CONFLICT"
    assert "clone_project" not in adapter.methods()


def test_info_reads_the_prj_without_any_application(cli, adapter, project, tmp_path) -> None:
    (tmp_path / "Lib").mkdir()
    (tmp_path / "Lib" / "Lib.lmc").write_text("", encoding="utf-8")
    code, payload = cli("project", "info", "--project", str(project))
    data = payload["data"]
    assert code == 0 and adapter.calls == []
    assert data["project"] == "Board" and data["xpedition_version"] == "XPED2604"
    assert data["central_library"]["exists"] is True
    kinds = {design["name"]: design["kind"] for design in data["designs"]}
    assert kinds == {"Schematic1": "schematic", "Board1": "board"}
    board = next(design for design in data["designs"] if design["kind"] == "board")
    assert board["root_block"] == "Schematic1" and board["board"] is None
    assert board["parts_databases"] == ["PartsDBLibs\\PartQuest.pdb"]
    assert board["cell_libraries"] == ["CellDBLibs\\PartQuest.cel"]
    assert data["board"] is None and data["ascii_path"] is True


def test_info_reports_the_board_once_one_exists(cli, project) -> None:
    text = project.read_text(encoding="utf-8").replace(
        'KEY PCBDesignPath ""', 'KEY PCBDesignPath "PCB\\Board.pcb"'
    )
    project.write_text(text, encoding="utf-8")
    (project.parent / "PCB").mkdir()
    (project.parent / "PCB" / "Board.pcb").write_text("", encoding="utf-8")
    _, payload = cli("project", "info", "--project", str(project))
    board = next(design for design in payload["data"]["designs"] if design["kind"] == "board")
    assert board["board_exists"] is True and payload["data"]["board"].endswith("Board.pcb")


def test_info_of_a_missing_or_wrong_file(cli, tmp_path) -> None:
    code, payload = cli("project", "info", "--project", str(tmp_path / "No.prj"))
    assert code == 3 and payload["error"]["code"] == "E_NOT_FOUND"
    other = tmp_path / "design.json"
    other.write_text("{}", encoding="utf-8")
    code, payload = cli("project", "info", "--project", str(other))
    assert code == 2 and payload["error"]["code"] == "E_VALIDATION"
