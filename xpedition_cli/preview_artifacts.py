"""Independently reopen the exact PNG set promised by an offline preview."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from pathlib import Path

from .errors import CLIError

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000


def _key(path):
    return os.path.normcase(str(path))


def expectation(command, options, preview):
    """Freeze names from the requested input, rather than the producer's reply."""
    output = Path(str(options["output"])).expanduser().resolve()
    if command == "library preview":
        if options.get("part"):
            images = [{"path": str(output)}]
        else:
            from .cli.library import _file_safe

            spec = json.loads(
                Path(str(options["file"])).expanduser().resolve().read_text(encoding="utf-8")
            )
            parts = spec.get("parts") if isinstance(spec, dict) else None
            if (
                not isinstance(parts, list)
                or not parts
                or any(
                    not isinstance(part, dict) or not str(part.get("number", "")).strip()
                    for part in parts
                )
            ):
                raise CLIError("E_VALIDATION", "Preview input needs a nonempty part list.")
            numbers = [" ".join(str(part["number"]).split()) for part in parts]
            images = [
                {
                    "part": number,
                    "path": str(output)
                    if len(numbers) == 1
                    else str(
                        output.with_name(f"{output.stem}-{_file_safe(number)}{output.suffix}")
                    ),
                }
                for number in numbers
            ]
    else:
        sheets = preview.get("design_preview", {}).get("sheets")
        if not isinstance(sheets, list) or not sheets or any(type(n) is not int for n in sheets):
            raise CLIError("E_VALIDATION", "Preview needs a frozen nonempty sheet selection.")
        images = [
            {
                "sheet": number,
                "path": str(output)
                if len(sheets) == 1
                else str(output.with_name(f"{output.stem}-sheet{number}{output.suffix}")),
            }
            for number in sheets
        ]
    if len({_key(item["path"]) for item in images}) != len(images):
        raise CLIError("E_VALIDATION", "Preview image names collide after filename conversion.")
    return {"output": str(output), "images": images}


def verify(options, result, expected, before, after):
    """Check byte delivery only; pixels cannot establish native engineering truth."""
    from PIL import Image

    checks, images = [], []

    def check(name, passed, **details):
        checks.append({"id": name, "passed": bool(passed), **details})

    output = Path(str(options["output"])).expanduser().resolve()
    wanted = expected.get("images") if isinstance(expected, dict) else None
    returned = result.get("pictures") if isinstance(result, dict) else None
    check("frozen-output", isinstance(expected, dict) and expected.get("output") == str(output))
    check("nonempty-frozen-images", isinstance(wanted, list) and bool(wanted))
    check("nonempty-returned-images", isinstance(returned, list) and bool(returned))
    if not isinstance(wanted, list) or not wanted or not isinstance(returned, list) or not returned:
        return _report(checks, images)
    if "count" in result:
        check(
            "reported-image-count", type(result["count"]) is int and result["count"] == len(wanted)
        )
    if isinstance(result.get("summary"), dict) and "sheets" in result["summary"]:
        count = result["summary"]["sheets"]
        check("reported-sheet-count", type(count) is int and count == len(wanted))
    expected_by_path = {_key(item["path"]): item for item in wanted}
    returned_paths = []
    for item in returned:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path:
            check("returned-image-path", False)
            continue
        candidate = Path(path)
        # No arbitrary sibling, alternate directory, alias or traversal is authorized
        # by the fact that the producer happened to return it.
        canonical = candidate.is_absolute() and _key(candidate) == _key(candidate.resolve())
        allowed = canonical and _key(candidate) in expected_by_path
        check("declared-image-path", allowed, path=path)
        returned_paths.append(_key(candidate))
    check("unique-returned-images", len(returned_paths) == len(set(returned_paths)))
    check("exact-returned-image-set", set(returned_paths) == set(expected_by_path))
    if not all(item["passed"] for item in checks):
        return _report(checks, images)
    previous = {_key(item["path"]): item for item in before}
    observed = {_key(item["path"]): item for item in after}
    changed = {
        key for key, item in observed.items() if item.get("exists") and item != previous.get(key)
    }
    check("exact-new-image-set", changed == set(expected_by_path))
    for item in returned:
        path = Path(item["path"])
        pinned = expected_by_path[_key(path)]
        check(
            "image-identity",
            all(item.get(key) == value for key, value in pinned.items() if key != "path"),
            path=str(path),
        )
        try:
            info = path.lstat()
            regular = stat.S_ISREG(info.st_mode) and not path.is_symlink()
            regular = regular and not getattr(info, "st_file_attributes", 0) & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            )
            check("regular-image-file", regular, path=str(path))
            if not regular:
                continue
            check("bounded-image-bytes", 0 < info.st_size <= MAX_BYTES, path=str(path))
            if not 0 < info.st_size <= MAX_BYTES:
                continue
            data = path.read_bytes()
            sha256 = hashlib.sha256(data).hexdigest()
            observation = observed.get(_key(path), {})
            check(
                "observed-image-sha256",
                observation.get("exists") is True
                and observation.get("sha256") == sha256
                and observation.get("size") == len(data),
                path=str(path),
            )
            with Image.open(io.BytesIO(data)) as picture:
                check("png-format", picture.format == "PNG", path=str(path))
                width, height = picture.size
                bounded = 0 < width * height <= MAX_PIXELS
                check("bounded-image-pixels", bounded, path=str(path))
                dimensions = all(type(item.get(k)) is int for k in ("width", "height"))
                check(
                    "returned-image-dimensions",
                    dimensions and (width, height) == (item.get("width"), item.get("height")),
                    path=str(path),
                )
                if not bounded:
                    continue
                picture.verify()
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
            check("decoded-all-pixels", True, path=str(path))
            images.append({"path": str(path), "sha256": sha256, "width": width, "height": height})
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            SyntaxError,
            Image.DecompressionBombError,
        ) as error:
            check("image-readback", False, path=str(path), error=type(error).__name__)
    return _report(checks, images)


def _report(checks, images):
    return {
        "valid": bool(checks) and all(item["passed"] for item in checks),
        "checks": checks,
        "images": images,
        "source": "independently reopened PNG bytes and frozen expected output set",
        "scope": "preview image file set, integrity, decoding and reported dimensions",
        "not_checked": [
            "native library or CAD engineering acceptance",
            "font correctness and visual readability",
            "design intent, connectivity, rules and manufacturing",
        ],
    }
