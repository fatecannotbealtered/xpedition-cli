"""Bounded sch2pdf artifact checks, separate from native and visual acceptance.

The supported producer format is classic-xref PDF 1.0--1.4, without encryption,
incremental revisions or object streams. Only unfiltered and single Flate streams
with predictor 1 are decoded. Other PDF features fail closed rather than falling
back to an unbounded decoder. This validates structure/content syntax, not pixels.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import zlib
from pathlib import Path

from .pdf_source_identity import same_project_path

LIMITS = {
    "file_bytes": 128 * 1024 * 1024,
    "pages": 2048,
    "objects": 50000,
    "object_depth": 64,
    "object_visits": 500000,
    "stream_bytes": 16 * 1024 * 1024,
    "decoded_total_bytes": 128 * 1024 * 1024,
    "content_operations_per_page": 100000,
    "content_syntax_bytes_per_page": 512 * 1024,
}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def file_pin(path):
    path = Path(path).expanduser().resolve()
    before = path.stat()
    if not path.is_file() or before.st_size > LIMITS["file_bytes"]:
        raise ValueError("PDF artifact input exceeds its file bound")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            if size > LIMITS["file_bytes"]:
                raise ValueError("PDF artifact input grew beyond its file bound")
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("PDF artifact input changed while hashing")
    return {"path": str(path), "bytes": after.st_size, "sha256": digest.hexdigest()}


def _same_pin(actual, expected):
    return isinstance(expected, dict) and all(
        actual.get(k) == expected.get(k) for k in ("path", "bytes", "sha256")
    )


def _facts(value):
    value = value if isinstance(value, dict) else {}
    return value.get("extensions", {}).get("facts", value)


def _native_digest(native):
    from .native_snapshot_revision import canonical, recognized

    # The SDK drift guard and PDF source witness use the same native census
    # semantics, including unordered net-to-sheet membership. This preserves
    # every field and ordered path while rejecting ambiguous stable identities.
    value = canonical(native) if recognized(native) else copy.deepcopy(native)
    # Collection enumeration order is not native design state. Ordered geometry
    # inside a record remains ordered; all material fields remain protected.
    for key in ("components", "unnamed_components", "sheets", "nets", "connections"):
        if isinstance(value.get(key), list):
            if key in {"components", "unnamed_components"}:
                for record in value[key]:
                    if isinstance(record.get("pins"), list):
                        record["pins"].sort(key=_digest)
            value[key].sort(key=_digest)
    return _digest(value)


def _sheet_census(source, schematic=None):
    native = _facts(source).get("native") or {}
    sheets = native.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        return None
    result = []
    for item in sheets:
        number = item.get("number")
        name = item.get("name") or item.get("full_name")
        if not isinstance(name, str) or not name or type(number) is not int or number <= 0:
            raise ValueError("Native sheet census is incomplete")
        stem, dot, tail = name.rpartition(".")
        if not dot or tail != str(number) or not stem:
            raise ValueError("Native sheet census has an unsupported identity")
        if schematic is None or schematic == stem:
            result.append(
                {"schematic": stem, "number": number, "printed": f"{stem} Sheet {number}"}
            )
    if not result or len(result) != len({(item["schematic"], item["number"]) for item in result}):
        raise ValueError("Native sheet census is empty or ambiguous")
    return sorted(result, key=lambda item: (item["schematic"], item["number"]))


def expectation(options, source=None, *, producer_executable=None):
    """Freeze file inputs and optional fresh native witnesses into a signed plan."""
    project = Path(options["project"]).expanduser().resolve()
    output = Path(options["output"]).expanduser().resolve()
    producer = producer_executable or options.get("_sch2pdf_executable")
    facts = _facts(source)
    native = facts.get("native")
    return {
        "generation_id": options.get("_artifact_generation") or options.get("artifact_generation"),
        "project": file_pin(project),
        "output": str(output),
        "previous_output": file_pin(output) if output.is_file() else None,
        "color": int(options.get("color") if options.get("color") is not None else 1),
        "schematic": options.get("schematic") or None,
        "producer": file_pin(producer) if producer else None,
        "native_application": copy.deepcopy(facts.get("application")),
        "native_sha256": _native_digest(native) if isinstance(native, dict) else None,
        "sheet_census": _sheet_census(source, options.get("schematic") or None),
        "limits": dict(LIMITS),
    }


def _check_classic_xref(data):
    if not re.match(rb"%PDF-1\.[0-4](?:\r?\n|\r)", data):
        raise ValueError("Only classic sch2pdf PDF 1.0--1.4 is supported")
    if re.search(rb"/(?:ObjStm|XRef|Encrypt)\b", data):
        raise ValueError("Object streams, xref streams and encrypted PDFs are unsupported")
    endings = list(re.finditer(rb"startxref\s+(\d+)\s+%%EOF", data))
    if len(endings) != 1 or data[endings[0].end() :].strip():
        raise ValueError("Incremental or incomplete PDF revisions are unsupported")
    offset = int(endings[0].group(1))
    if data[offset : offset + 4] != b"xref":
        raise ValueError("Classic xref offset is invalid")
    lines = io.BytesIO(data[offset:])
    lines.readline()
    entries = 0
    while True:
        line = lines.readline().strip()
        if line == b"trailer":
            break
        header = re.fullmatch(rb"(\d+)\s+(\d+)", line)
        if not header:
            raise ValueError("Classic xref subsection is invalid")
        first, count = map(int, header.groups())
        entries += count
        if first + count > LIMITS["objects"] or entries > LIMITS["objects"]:
            raise ValueError("PDF object count exceeds its bound")
        for _ in range(count):
            if not re.fullmatch(rb"\d{10}\s\d{5}\s[fn]\s*", lines.readline().strip()):
                raise ValueError("Classic xref entry is invalid")
    sizes = [int(match) for match in re.findall(rb"/Size\s+(\d+)\b", data)]
    if not sizes or any(size > LIMITS["objects"] for size in sizes):
        raise ValueError("PDF declared object count is missing or exceeds its bound")


def decode_pdf(path):
    """Strictly parse supported PDF objects and bounded stream/content syntax."""
    from pypdf import Configuration, PdfReader, apply_configuration
    from pypdf import __version__ as parser_version
    from pypdf.generic import ContentStream, DecodedStreamObject, IndirectObject, StreamObject

    pin = file_pin(path)
    with Path(path).open("rb") as stream:
        data = stream.read(LIMITS["file_bytes"] + 1)
    if len(data) > LIMITS["file_bytes"]:
        raise ValueError("PDF bytes grew beyond their file bound")
    if hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise ValueError("PDF bytes changed before decoding")
    _check_classic_xref(data)
    configuration = Configuration(
        maximum_declared_stream_length=LIMITS["stream_bytes"],
        array_based_stream_maximum_output_length=LIMITS["stream_bytes"],
        zlib_maximum_output_length=LIMITS["stream_bytes"],
        zlib_maximum_recovery_input_length=65536,
        page_tree_maximum_entries=LIMITS["pages"] * 2,
        page_tree_maximum_depth=32,
    )
    decoded = {}
    total = 0

    def stream_data(stream):
        nonlocal total
        identity = id(stream)
        if identity in decoded:
            return decoded[identity]
        raw = stream._data
        if not isinstance(raw, bytes) or len(raw) > LIMITS["stream_bytes"]:
            raise ValueError("PDF encoded stream exceeds its bound")
        filters = stream.get("/Filter")
        if isinstance(filters, list) and len(filters) == 1:
            filters = filters[0]
        if filters is None:
            content = raw
        else:
            if filters != "/FlateDecode":
                raise ValueError("PDF filter is outside the bounded sch2pdf subset")
            parameters = stream.get("/DecodeParms")
            if parameters is not None and (
                not isinstance(parameters, dict)
                or set(parameters) - {"/Predictor"}
                or parameters.get("/Predictor", 1) != 1
            ):
                raise ValueError("PDF Flate predictor is outside the bounded subset")
            inflater = zlib.decompressobj()
            content = inflater.decompress(raw, LIMITS["stream_bytes"] + 1)
            if (
                len(content) > LIMITS["stream_bytes"]
                or inflater.unconsumed_tail
                or not inflater.eof
                or inflater.unused_data
            ):
                raise ValueError("PDF decoded stream is incomplete or exceeds its bound")
        total += len(content)
        if total > LIMITS["decoded_total_bytes"]:
            raise ValueError("PDF aggregate decoded bytes exceed their bound")
        decoded[identity] = content
        return content

    with apply_configuration(configuration):
        reader = PdfReader(io.BytesIO(data), strict=True, root_object_recovery_limit=0)
        if reader.is_encrypted or reader.xref_objStm:
            raise ValueError("Encrypted or compressed PDF objects are unsupported")
        seen, visits = set(), 0
        compatibility = []
        pending = [(reader.trailer, 0, None)]
        while pending:
            value, depth, member = pending.pop()
            visits += 1
            if depth > LIMITS["object_depth"] or visits > LIMITS["object_visits"]:
                raise ValueError("PDF object traversal exceeds its bound")
            if isinstance(value, IndirectObject):
                if value.idnum == 0 and value.generation == 0 and member == "/Dests":
                    # Stock sch2pdf emits this optional empty destination link.
                    # Do not relax missing page/content/font/other references.
                    compatibility.append("optional /Dests 0 0 R treated as null")
                    continue
                key = (value.idnum, value.generation)
                if key in seen:
                    continue
                seen.add(key)
                if len(seen) > LIMITS["objects"]:
                    raise ValueError("PDF indirect object count exceeds its bound")
                value = value.get_object()
            if isinstance(value, StreamObject):
                stream_data(value)
            if isinstance(value, dict):
                pending.extend((item, depth + 1, str(key)) for key, item in value.items())
            elif isinstance(value, list):
                pending.extend((item, depth + 1, None) for item in value)
        if not 0 < len(reader.pages) <= LIMITS["pages"]:
            raise ValueError("PDF page count exceeds its bound")
        pages = []
        for number, page in enumerate(reader.pages, 1):
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if not all(math.isfinite(value) and 0 < value <= 14400 for value in (width, height)):
                raise ValueError("PDF page dimensions are invalid")
            contents = page.get("/Contents")
            if contents is None:
                raise ValueError("A schematic PDF page has no content stream")
            contents = contents.get_object()
            streams = contents if isinstance(contents, list) else [contents]
            raw = b"\n".join(stream_data(item.get_object()) for item in streams)
            if not raw.strip() or len(raw) > LIMITS["content_syntax_bytes_per_page"]:
                raise ValueError("PDF page content is empty or exceeds its bound")
            content = DecodedStreamObject()
            content.set_data(raw)
            operations = ContentStream(content, reader).operations
            if not operations or len(operations) > LIMITS["content_operations_per_page"]:
                raise ValueError("PDF page content operation count exceeds its bound")
            pages.append(
                {
                    "page": number,
                    "width_pt": round(width, 1),
                    "height_pt": round(height, 1),
                    "width_mm": round(width / 72 * 25.4, 1),
                    "height_mm": round(height / 72 * 25.4, 1),
                    "orientation": "landscape"
                    if width > height
                    else "portrait"
                    if height > width
                    else "square",
                    "content_sha256": hashlib.sha256(raw).hexdigest(),
                    "content_operations": len(operations),
                }
            )
    if not _same_pin(file_pin(path), pin):
        raise ValueError("PDF changed during independent decoding")
    return {
        "file": pin,
        "pages": pages,
        "page_count": len(pages),
        "decoded_bytes": total,
        "objects": len(seen),
        "limits": dict(LIMITS),
        "compatibility_observations": compatibility,
        "parser": {
            "name": "pypdf",
            "version": parser_version,
            "strict": True,
            "xref_recovery": False,
        },
        "validation": "bounded PDF object and content syntax; no pixels or fonts rendered",
    }


def verify(before, after, result, expected):
    """Independently reopen files and compare signed invocation/native witnesses."""
    checks = []

    def check(name, passed):
        checks.append({"id": name, "passed": bool(passed)})

    artifact = result.get("artifact") or {}
    decoded = None
    try:
        output = expected["output"]
        decoded = decode_pdf(output)
        pin = decoded["file"]
        check(
            "producer-returned-success",
            result.get("exported") is True and result.get("exit_code") == 0,
        )
        check(
            "current-generation",
            bool(expected.get("generation_id"))
            and artifact.get("generation_id") == expected["generation_id"],
        )
        check("requested-project", artifact.get("project") == expected["project"]["path"])
        check("requested-output", artifact.get("output") == output == result.get("path"))
        check("producer-byte-hash", artifact.get("sha256") == pin["sha256"])
        previous = expected.get("previous_output")
        check("fresh-output-bytes", previous is None or previous.get("sha256") != pin["sha256"])
        check(
            "source-project-unchanged",
            _same_pin(file_pin(expected["project"]["path"]), expected["project"])
            and _same_pin(artifact.get("source_project_before") or {}, expected["project"])
            and _same_pin(artifact.get("source_project_after") or {}, expected["project"]),
        )
        producer = expected.get("producer")
        check(
            "bound-producer-executable",
            bool(producer)
            and _same_pin(file_pin(producer["path"]), producer)
            and _same_pin(artifact.get("producer_before") or {}, producer)
            and _same_pin(artifact.get("producer_after") or {}, producer),
        )
        arguments = (
            [
                producer["path"],
                "-project",
                expected["project"]["path"],
                "-a",
                output,
                "-c",
                str(expected["color"]),
            ]
            if producer
            else None
        )
        if arguments and expected.get("schematic"):
            arguments += ["-schematic", expected["schematic"]]
        check(
            "exact-producer-invocation",
            artifact.get("invocation", {}).get("argv") == arguments
            and artifact.get("invocation", {}).get("shell") is False,
        )
        printed = result.get("sheets")
        check(
            "producer-page-table",
            isinstance(printed, list)
            and len(printed) == len(set(printed)) == decoded["page_count"]
            and artifact.get("printed_sheets") == printed,
        )
        check(
            "producer-page-sizes",
            all(
                {
                    key: page[key]
                    for key in (
                        "page",
                        "width_pt",
                        "height_pt",
                        "width_mm",
                        "height_mm",
                        "orientation",
                    )
                }
                == reported
                for page, reported in zip(decoded["pages"], result.get("pages", []), strict=True)
            ),
        )
        census = expected.get("sheet_census")
        if census is not None:
            check(
                "native-sheet-census-matches-page-table",
                [item["printed"] for item in census] == printed,
            )
    except Exception as error:
        checks.append(
            {"id": "pdf-independent-decode", "passed": False, "cause_type": type(error).__name__}
        )
    native_checks = []
    if expected.get("native_sha256") is not None:
        before, after = _facts(before), _facts(after)
        for label, facts in (("before", before), ("after", after)):
            application = facts.get("application") or {}
            bound = expected.get("native_application") or {}
            document = application.get("document") or {}
            passed = (
                isinstance(facts.get("native"), dict)
                and _native_digest(facts["native"]) == expected["native_sha256"]
                and bool(bound.get("pid"))
                and application.get("pid") == bound["pid"]
                and document.get("open") is True
                and document.get("status") == "observed"
                and same_project_path(document.get("path"), expected["project"]["path"])
            )
            native_checks.append({"id": f"native-source-protected-{label}", "passed": passed})
    native_valid = bool(native_checks) and all(item["passed"] for item in native_checks)
    return {
        "valid": bool(checks)
        and all(item["passed"] for item in checks)
        and (not native_checks or native_valid),
        "checks": checks + native_checks,
        "artifact_structure": "passed"
        if checks and all(item["passed"] for item in checks)
        else "failed",
        "native_protection": "passed"
        if native_valid
        else "failed"
        if native_checks
        else "not_observed",
        "decoded": decoded,
        "source": "independently reopened PDF bytes, pinned producer "
        "and optional fresh native snapshots",
        "scope": "PDF freshness, invocation provenance, bounded object/content syntax "
        "and page census",
        "not_checked": [
            "pixel rendering",
            "font glyph correctness",
            "visual readability",
            "PDF destination navigation",
            "schematic design intent",
            "rendered connectivity equivalence to the native database",
            "native border-to-page clipping",
            "native ERC",
            "manufacturing acceptance",
        ]
        + ([] if native_checks else ["native schematic protection was not observed"]),
    }
