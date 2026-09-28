"""The fabrication package: what a board house needs, assembled from Layout's outputs.

Layout writes ODB++, Gerber and NC drill files through three dialogs whose settings
live in `Config/ODBSetup.ocf` and `Config/PlotSetup.gpf` (HKP-style text). This module
holds the pure parts: patching those setups so the ODB++ job carries the drill layer
and the outline and the Gerber set carries the cells' silkscreen and the board outline,
reading the outputs back to check they are not empty, and writing the package folder
with a manifest, a README for the board house, a centroid file and a BOM.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CELL_TYPES = (
    "Buried",
    "Connector",
    "DiscreteAxial",
    "DiscreteChip",
    "DiscreteOther",
    "DiscreteRadial",
    "EdgeConnector",
    "EmbeddedPassive",
    "General",
    "Graphic",
    "ICBareDie",
    "ICBGA",
    "ICDIP",
    "ICFlipChip",
    "ICLCC",
    "ICOther",
    "ICPGA",
    "ICPLCC",
    "ICSIP",
    "ICSOIC",
    "Jumper",
    "Mechanical",
    "TestPoint",
)
GERBER_DIR = "Output\\\\Gerber\\\\"  # as the setup file writes it (escaped backslashes)
SILKSCREEN_FILES = (("SilkscreenTop", "Top"), ("SilkscreenBottom", "Bottom"))
OUTLINE_FILE = "BoardOutline"
GERBER_HEADER_BYTES = 400  # a file with only the header is empty
STALE_TOLERANCE = 2.0  # seconds: file times are coarser than the run's start
# fab-friendly description of each Gerber file Layout's default setup writes
GERBER_LAYERS = {
    "EtchLayer1Top": "L1 顶层铜 (top copper)",
    "EtchLayerTop": "L1 顶层铜 (top copper)",
    "EtchLayer2": "L2 内层铜，正片 (inner copper, positive)",
    "EtchLayer2Neg": "L2 内层平面，负片 (inner plane, negative)",
    "EtchLayer3": "L3 内层铜，正片 (inner copper, positive)",
    "EtchLayer3Neg": "L3 内层平面，负片 (inner plane, negative)",
    "EtchLayer4Bottom": "L4 底层铜 (bottom copper)",
    "EtchLayerBottom": "L4 底层铜 (bottom copper)",
    "SoldermaskTop": "顶层阻焊 (top solder mask)",
    "SoldermaskBottom": "底层阻焊 (bottom solder mask)",
    "SolderPasteTop": "顶层锡膏 (top paste)",
    "SolderPasteBottom": "底层锡膏 (bottom paste)",
    "SilkscreenTop": "顶层丝印 (top silkscreen)",
    "SilkscreenBottom": "底层丝印 (bottom silkscreen)",
    "GeneratedSilkscreenTop": "顶层丝印，丝印生成器版 (generated top silkscreen)",
    "GeneratedSilkscreenBottom": "底层丝印，丝印生成器版 (generated bottom silkscreen)",
    "BoardOutline": "板框 (board outline)",
    "DrillDrawingThrough": "钻孔图 (drill drawing)",
}


# -- setup files ---------------------------------------------------------------------


def patch_odb_setup(text: str) -> tuple[str, list[str]]:
    """The ODB++ setup with every drill span included and the board outline on; what changed."""
    changes: list[str] = []
    pattern = re.compile(r'( \.\.NAME "d_[^"]+"\r?\n\s+\.\.\.INCLUDE )NO')
    if pattern.search(text):
        text = pattern.sub(r"\1YES", text)
        changes.append("drill spans included")
    if re.search(r"^\.BOARD_OUTLINE NO", text, re.M):
        text = re.sub(r"^\.BOARD_OUTLINE NO", ".BOARD_OUTLINE YES", text, flags=re.M)
        changes.append("board outline included")
    return text, changes


def _gerber_file_block(name: str, lines: list[str], note: str) -> str:
    body = "\n".join(lines)
    return (
        f'\n.GerberOutputFile "{name}.gdo"\n ..ProcessFile Yes\n ..FlashPads Yes\n'
        f' ..GerberOutputPath "{GERBER_DIR}{name}.gdo"\n ..HeaderText\n'
        f'   ...CommentLine "xpedition-cli: {note}"\n{body}\n'
    )


def silkscreen_block(name: str, side: str) -> str:
    """A Gerber file of every cell type's silkscreen outline and reference designator on
    one side: the grammar of the installation's own multi-layer plot setups."""
    lines = [f" ..CellType {kind}" for kind in CELL_TYPES]
    lines += [
        f" ..CellItemsSide {side}",
        # the installation's own setups: layer 1 for the top side, 0 for the bottom
        f"   ...CellItemsLayer {1 if side == 'Top' else 0}",
        "   ...CellItem SilkscreenOutline",
        "   ...CellItem SilkscreenReferenceDesignator",
    ]
    return _gerber_file_block(name, lines, f"silkscreen, {side.lower()}")


def _file_block(text: str, name: str) -> str | None:
    """The `.GerberOutputFile "name.gdo"` block of a plot setup, up to the next file."""
    pattern = (
        r'^\.GerberOutputFile "'
        + re.escape(name)
        + r'\.gdo"\r?\n(?:(?!^\.GerberOutputFile ).*\r?\n?)*'
    )
    match = re.search(pattern, text, re.M)
    return match.group(0) if match else None


def _silkscreen_ok(block: str, side: str) -> bool:
    """Whether a silkscreen file block draws the cells of `side` (the dialog rewrites the
    file in its own order, so the markers are checked, not the text)."""
    layers = re.findall(r"\.\.\.CellItemsLayer (\d+)", block)
    # the dialog rewrites the bottom's layer 0 as the board's last layer number
    layer_ok = layers == ["1"] if side == "Top" else bool(layers) and layers != ["1"]
    return layer_ok and all(
        marker in block
        for marker in (
            f"..CellItemsSide {side}",
            "...CellItem SilkscreenOutline",
            "...CellItem SilkscreenReferenceDesignator",
        )
    )


def patch_gerber_setup(text: str) -> tuple[str, list[str]]:
    """The Gerber plot setup with silkscreen and outline files added; what changed.

    `..BoardItem SilkscreenTop` is accepted and writes an empty file: the silkscreen of
    a design comes from its cells (`..CellType` / `..CellItem`), or from the Silkscreen
    Generator's altered copy. A silkscreen file defined that way is replaced; any other
    file already defined is left alone.
    """
    changes: list[str] = []
    for name, side in SILKSCREEN_FILES:
        block = _file_block(text, name)
        if block is not None and not _silkscreen_ok(block, side):
            # a board item that draws nothing, or the wrong side/layer: replace it
            text = text.replace(block, "")
            block = None
        if block is None:
            text = text.rstrip("\n") + "\n" + silkscreen_block(name, side)
            changes.append(f"{name}.gdo")
    if f'.GerberOutputFile "{OUTLINE_FILE}.gdo"' not in text:
        text = (
            text.rstrip("\n")
            + "\n"
            + _gerber_file_block(OUTLINE_FILE, [" ..BoardItem BoardOutline"], "board outline")
        )
        changes.append(f"{OUTLINE_FILE}.gdo")
    return text, changes


# -- reading the outputs back ------------------------------------------------------------


@dataclass
class OutputFile:
    name: str
    path: str
    bytes: int
    draws: int  # Gerber draws/flashes, drill hits, or ODB++ features
    empty: bool
    note: str = ""
    mtime: float = 0.0  # seconds since the epoch; tells this run's files from older ones

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "bytes": self.bytes,
            "draws": self.draws,
            "empty": self.empty,
            "note": self.note,
        }


def gerber_files(folder: Path) -> list[OutputFile]:
    """Every `.gdo` in the Gerber output folder with its draw count (`D01`/`D03`)."""
    files: list[OutputFile] = []
    for path in sorted(folder.glob("*.gdo")):
        try:
            text = path.read_text(encoding="latin-1")
        except OSError:
            continue
        # coordinates are modal, so a line may carry only X, Y, I or J
        draws = len(re.findall(r"^(?:G0[123])?[XYIJ]-?\d", text, re.M))
        stat = path.stat()
        files.append(
            OutputFile(
                path.stem,
                str(path),
                stat.st_size,
                draws,
                # a negative plane without draws is solid copper, not an empty layer
                draws == 0 and not path.stem.endswith("Neg"),
                GERBER_LAYERS.get(path.stem, ""),
                stat.st_mtime,
            )
        )
    return files


def drill_files(folder: Path) -> list[OutputFile]:
    """Every `.ncd` (Excellon) drill file with its hit count."""
    files: list[OutputFile] = []
    for path in sorted(folder.glob("*.ncd")):
        try:
            text = path.read_text(encoding="latin-1")
        except OSError:
            continue
        # Excellon coordinates are modal: a hit may repeat only X or only Y
        hits = len(re.findall(r"^[XY]-?\d+", text, re.M))
        tools = len(re.findall(r"^T\d+C", text, re.M))
        note = "非金属化孔 (non-plated)" if "NonPlated" in path.stem else "金属化孔 (plated)"
        stat = path.stat()
        files.append(
            OutputFile(
                path.stem,
                str(path),
                stat.st_size,
                hits,
                hits == 0,
                f"{note}, {tools} tools",
                stat.st_mtime,
            )
        )
    return files


def odb_job(folder: Path) -> dict[str, Any]:
    """The layers of an ODB++ job (from its matrix) with their feature counts."""
    matrix = folder / "matrix" / "matrix"
    if not matrix.is_file():
        return {"present": False, "job": str(folder)}
    modified = matrix.stat().st_mtime
    text = matrix.read_text(encoding="latin-1", errors="replace")
    step = (
        next((p for p in (folder / "steps").iterdir() if p.is_dir()), None)
        if (folder / "steps").is_dir()
        else None
    )
    layers: list[dict[str, Any]] = []
    for block in re.findall(r"LAYER \{(.*?)\}", text, re.S):
        fields = dict(re.findall(r"(\w+)=(\S*)", block))
        name = fields.get("NAME", "")
        features = None
        if step is not None:
            feature_file = step / "layers" / name / "features"
            if feature_file.is_file():
                try:
                    features = sum(
                        1
                        for line in feature_file.read_text(
                            encoding="latin-1", errors="replace"
                        ).splitlines()
                        if line[:1] in ("L", "P", "A", "T", "S", "B")
                    )
                except OSError:
                    features = None
        layers.append({"name": name, "type": fields.get("TYPE", ""), "features": features})
    return {
        "present": True,
        "job": str(folder),
        "step": step.name if step else None,
        "layers": layers,
        "drill": any(item["type"] == "DRILL" for item in layers),
        "profile": bool(step and (step / "profile").is_file()),
        "modified": modified,
    }


def copper_names(layer_count: int) -> tuple[set[str], set[str]]:
    """The file names that carry the top and the bottom copper of a board of
    `layer_count` layers (any bottom layer when the count is not known)."""
    top = {"EtchLayer1Top", "EtchLayerTop"}
    if layer_count >= 2:
        return top, {f"EtchLayer{layer_count}Bottom", "EtchLayerBottom"}
    return top, {"EtchLayerBottom"}


def _is_bottom(name: str, layer_count: int) -> bool:
    if layer_count >= 2:
        return name in copper_names(layer_count)[1]
    return bool(re.fullmatch(r"EtchLayer\d*Bottom", name))


def checks(
    gerbers: list[OutputFile],
    drills: list[OutputFile],
    odb: dict[str, Any],
    *,
    requested: list[str] | None = None,
    layer_count: int = 0,
    runs: list[dict[str, Any]] | None = None,
    unplaced: list[str] | None = None,
) -> dict[str, Any]:
    """What is still missing for a board house.

    `requested` names the formats this run was asked for: one that produced nothing is
    a problem, not a format left out. With `layer_count`, the copper is checked layer
    by layer; `runs` are the output commands this run started, each with `format` and
    `finished`; `unplaced` are parts the BOM lists but the board has not placed.
    """
    problems: list[str] = []
    names = {item.name for item in gerbers if not item.empty}
    present = {item.name for item in gerbers}
    wanted = set(requested) if requested is not None else None
    if wanted is not None:
        if "gerber" in wanted and not names:
            problems.append("Gerber was requested but no Gerber file of this run has any draws")
        if "ncdrill" in wanted and not drills:
            problems.append("NC drill was requested but this run wrote no drill file")
        if "odb" in wanted and not odb.get("present"):
            problems.append("ODB++ was requested but this run wrote no ODB++ job")
    for run in runs or []:
        if not run.get("finished", True):
            problems.append(f"the {run.get('format', 'output')} output did not finish in time")
    if names:
        top, _bottom = copper_names(layer_count)
        if not names & top:
            problems.append("Gerber EtchLayer1Top is missing or empty")
        if not any(_is_bottom(name, layer_count) for name in names):
            bottom = f"EtchLayer{layer_count}Bottom" if layer_count >= 2 else "bottom copper"
            problems.append(f"Gerber {bottom} is missing or empty")
        for inner in range(2, layer_count):
            positive, negative = f"EtchLayer{inner}", f"EtchLayer{inner}Neg"
            if negative in present or positive in names:
                continue
            if positive in present:
                problems.append(
                    f"inner layer L{inner} has no copper in {positive}: the package leaves an "
                    f"empty file out, so the board house would get {layer_count - 1} copper "
                    "layers"
                )
            else:
                problems.append(f"Gerber has no file for inner layer L{inner}")
        for needed in ("SoldermaskTop", "SoldermaskBottom"):
            if needed not in names:
                problems.append(f"Gerber {needed} is missing or empty")
        if "SilkscreenTop" not in names and "GeneratedSilkscreenTop" not in names:
            problems.append("Gerber has no top silkscreen")
        if OUTLINE_FILE not in names:
            problems.append("Gerber has no board outline")
    if drills and not any(not item.empty for item in drills):
        problems.append("the drill files have no holes")
    if odb.get("present"):
        if not odb.get("drill"):
            problems.append("the ODB++ job has no drill layer")
        if not odb.get("profile"):
            problems.append("the ODB++ job has no board profile")
    if unplaced:
        shown = ", ".join(unplaced[:10]) + (" …" if len(unplaced) > 10 else "")
        count = "1 part is" if len(unplaced) == 1 else f"{len(unplaced)} parts are"
        problems.append(
            f"{count} not placed on the board ({shown}): the BOM lists them and the "
            "centroid file does not"
        )
    return {"ok": not problems, "problems": problems}


# -- the package -------------------------------------------------------------------------


def centroid_rows(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pick-and-place rows: reference designator, centre in mm, rotation, side, footprint."""
    rows = []
    for item in components:
        if not item.get("placed", True):
            continue
        rows.append(
            {
                "refdes": item.get("refdes", ""),
                "x_mm": item.get("x"),
                "y_mm": item.get("y"),
                "rotation": item.get("rotation", 0),
                "side": item.get("side", "top"),
                "footprint": item.get("footprint", ""),
                "part_number": item.get("part_number", ""),
            }
        )
    rows.sort(key=lambda row: str(row["refdes"]))
    return rows


def bom_rows(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """BOM lines grouped by part number and footprint, with the reference designators."""
    groups: dict[tuple[str, str], list[str]] = {}
    for item in components:
        key = (str(item.get("part_number", "")), str(item.get("footprint", "")))
        groups.setdefault(key, []).append(str(item.get("refdes", "")))
    rows = []
    for (number, footprint), refs in sorted(groups.items()):
        rows.append(
            {
                "part_number": number,
                "footprint": footprint,
                "quantity": len(refs),
                "refdes": " ".join(sorted(refs)),
            }
        )
    return rows


def _csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def readme(
    board: str,
    size: dict[str, Any],
    layer_count: int,
    gerbers: list[OutputFile],
    drills: list[OutputFile],
    odb: dict[str, Any],
    problems: list[str],
) -> str:
    """The note that goes to the board house, in Chinese with the English terms."""
    lines = [
        f"# {board} 打板资料",
        "",
        f"- 板子尺寸：{size.get('width')} × {size.get('height')} mm"
        "（板框在 Gerber 的 BoardOutline 和 ODB++ 的 profile 里）",
        f"- 层数：{layer_count} 层；叠层按 Gerber 文件名的 L1…L{layer_count} 顺序，L1 为顶层",
        "- 板厚、表面处理、阻焊颜色：未指定，按板厂默认（常见 1.6 mm、有铅喷锡、绿油白字）",
        "- 最小线宽/线距：以 Gerber/ODB++ 中的实际图形为准",
        "- 孔：金属化孔见 ThruHolePlated，非金属化孔（安装孔）见 ThruHoleNonPlated；单位见文件头",
        "",
        "## ODB++",
        "",
    ]
    if odb.get("present"):
        names = ", ".join(item["name"] for item in odb.get("layers", []))
        lines.append(f"- 作业目录 `odbpp/`（或同名 zip），层：{names}")
        drill = "是" if odb.get("drill") else "否"
        profile = "是" if odb.get("profile") else "否"
        lines.append(f"- 含钻孔层：{drill}；含板框：{profile}")
    else:
        lines.append("- 未生成")
    lines += ["", "## Gerber（RS-274X）", "", "| 文件 | 内容 | 绘制数 |", "|---|---|---|"]
    for item in gerbers:
        if item.empty:
            lines.append(f"| {item.name} | {item.note or '-'} | 0（空，未打包） |")
        else:
            lines.append(f"| {item.name}.gbr | {item.note or '-'} | {item.draws} |")
    lines += ["", "## 钻孔（Excellon）", "", "| 文件 | 内容 | 孔数 |", "|---|---|---|"]
    for item in drills:
        lines.append(f"| {item.name}.drl | {item.note} | {item.draws} |")
    lines += [
        "",
        "## 贴片",
        "",
        "- `centroid.csv`：位号、元件原点坐标（mm，坐标原点在板框左下角；是封装的原点，"
        "不一定是元件中心）、旋转、面、封装",
        "- `bom.csv`：按料号和封装汇总的清单；料号为设计文件中的值",
    ]
    if problems:
        lines += ["", "## 未完成", ""] + [f"- {problem}" for problem in problems]
    return "\n".join(lines) + "\n"


def write_package(
    target: Path,
    board: str,
    size: dict[str, Any],
    layer_count: int,
    gerber_dir: Path | None,
    drill_dir: Path | None,
    odb_dir: Path | None,
    components: list[dict[str, Any]],
    *,
    requested: list[str] | None = None,
    runs: list[dict[str, Any]] | None = None,
    since: float | None = None,
) -> dict[str, Any]:
    """Copy the outputs into `target` with board-house names, and write the manifest,
    the README, the centroid file and the BOM. Returns the manifest.

    With `since` (the epoch time the run started), files older than it are an earlier
    export's leftovers: they stay out of the package and are listed under `stale`.
    """
    import shutil
    import zipfile

    target.mkdir(parents=True, exist_ok=True)
    gerbers = gerber_files(gerber_dir) if gerber_dir and gerber_dir.is_dir() else []
    drills = drill_files(drill_dir) if drill_dir and drill_dir.is_dir() else []
    odb = odb_job(odb_dir) if odb_dir and odb_dir.is_dir() else {"present": False}
    stale: list[str] = []
    if since is not None:
        cutoff = since - STALE_TOLERANCE
        stale = [item.path for item in gerbers + drills if item.mtime < cutoff]
        gerbers = [item for item in gerbers if item.mtime >= cutoff]
        drills = [item for item in drills if item.mtime >= cutoff]
        if odb.get("present") and float(odb.get("modified") or 0.0) < cutoff:
            stale.append(str(odb.get("job")))
            odb = {"present": False, "job": odb.get("job"), "stale": True}
    copied: list[str] = []
    skipped: list[str] = []
    names = {item.name for item in gerbers}
    # Layout's stock setups write the outer copper twice; the numbered file is kept
    aliases = {"EtchLayerTop": "EtchLayer1Top"}
    if layer_count >= 2:
        aliases["EtchLayerBottom"] = f"EtchLayer{layer_count}Bottom"
    if gerbers:
        (target / "gerber").mkdir(exist_ok=True)
        for item in gerbers:
            # an empty file, or a duplicate of a numbered copper file, stays behind
            alias = aliases.get(item.name)
            if item.empty or (alias and alias in names):
                skipped.append(item.name)
                continue
            destination = target / "gerber" / f"{item.name}.gbr"
            shutil.copyfile(item.path, destination)
            copied.append(str(destination))
    if drills:
        (target / "drill").mkdir(exist_ok=True)
        for item in drills:
            destination = target / "drill" / f"{item.name}.drl"
            shutil.copyfile(item.path, destination)
            copied.append(str(destination))
    if odb.get("present") and odb_dir is not None:
        archive = target / f"{odb_dir.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(odb_dir.rglob("*")):
                if path.is_file():
                    bundle.write(path, str(Path(odb_dir.name) / path.relative_to(odb_dir)))
        copied.append(str(archive))
    centroid = centroid_rows(components)
    bom = bom_rows(components)
    (target / "centroid.csv").write_text(_csv(centroid), encoding="utf-8")
    (target / "bom.csv").write_text(_csv(bom), encoding="utf-8")
    unplaced = sorted(
        str(item.get("refdes", "")) for item in components if not item.get("placed", True)
    )
    verdict = checks(
        gerbers,
        drills,
        odb,
        requested=requested,
        layer_count=layer_count,
        runs=runs,
        unplaced=unplaced,
    )
    (target / "README.md").write_text(
        readme(board, size, layer_count, gerbers, drills, odb, verdict["problems"]),
        encoding="utf-8",
    )
    manifest = {
        "board": board,
        "size": size,
        "layer_count": layer_count,
        "package": str(target),
        "gerber": [item.as_dict() for item in gerbers],
        "drill": [item.as_dict() for item in drills],
        "odb": odb,
        "skipped": skipped,
        "stale": stale,
        "unplaced": unplaced,
        "centroid_rows": len(centroid),
        "bom_rows": len(bom),
        "files": copied + [str(target / name) for name in ("centroid.csv", "bom.csv", "README.md")],
        "checks": verdict,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return manifest
