"""Synthetic bounded PDFs and fake sch2pdf boundary; never launches CAD/tools."""

from __future__ import annotations

import copy
import hashlib
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import DecodedStreamObject, EncodedStreamObject, NameObject

from xpedition_cli import native_com_adapter as N
from xpedition_cli import schematic_pdf_artifacts as P


def write_pdf(
    path, *, pages=2, content=b"0 0 m 10 10 l S\n", compressed=False, empty=False, null_dest=False
):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=1190, height=842)
        if not empty:
            stream = DecodedStreamObject()
            stream.set_data(content)
            if compressed:
                stream = stream.flate_encode()
            page[NameObject("/Contents")] = writer._add_object(stream)
    if null_dest:
        from pypdf.generic import IndirectObject

        writer.root_object[NameObject("/Dests")] = IndirectObject(0, 0, writer)
    with Path(path).open("wb") as output:
        writer.write(output)


@pytest.fixture
def producer(tmp_path, monkeypatch):
    project = tmp_path / "NonFixedCase.prj"
    project.write_text("owned source project", encoding="utf-8")
    home = tmp_path / "sdd"
    executable = home / "common" / "win64" / "bin" / "sch2pdf.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"offline converter fixture")
    output = tmp_path / "fresh.pdf"
    source = {
        "application": {
            "pid": 4242,
            "document": {"open": True, "path": str(project), "status": "observed"},
        },
        "native": {
            "project": "CaseA",
            "sheets": [{"number": 1, "name": "CaseA.1"}, {"number": 2, "name": "CaseA.2"}],
            "components": [
                {"refdes": "U1", "uid": "one", "pins": [{"number": "1", "net": "A"}]},
                {"refdes": "R1", "uid": "two", "pins": []},
            ],
            "nets": [{"name": "A"}],
            "connections": [],
        },
    }
    options = {
        "project": str(project),
        "output": str(output),
        "_artifact_generation": "op-fresh-generation",
    }
    expected = P.expectation(options, source, producer_executable=executable)
    calls, behavior = [], {"exit": 0, "pages": 2}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if behavior.get("timeout"):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        write_pdf(argv[argv.index("-a") + 1], pages=behavior["pages"])
        return SimpleNamespace(
            returncode=behavior["exit"],
            stdout=b"Printed CaseA Sheet 1\nPrinted CaseA Sheet 2\n",
            stderr=b"",
        )

    monkeypatch.setattr(N, "_configure_environment", lambda: home)
    monkeypatch.setattr(N.subprocess, "run", run)
    params = {
        **options,
        "artifact_generation": options["_artifact_generation"],
        "_pdf_expectation": expected,
        "_native_process_timeout_seconds": 42,
        "_native_budget_profile_id": "offline-budget",
    }
    return options, source, expected, params, calls, behavior


def test_fresh_generation_producer_and_independent_native_protection(producer):
    options, source, expected, params, calls, _behavior = producer
    result = N._export_pdf(params)
    checked = P.verify(source, copy.deepcopy(source), result, expected)
    assert checked["valid"] is True, checked
    assert checked["artifact_structure"] == "passed"
    assert checked["native_protection"] == "passed"
    assert checked["decoded"]["page_count"] == 2
    assert calls[0][1]["timeout"] == 42 < 600
    assert result["producer_timing"]["profile_id"] == "offline-budget"
    assert result["artifact"]["generation_id"] == "op-fresh-generation"
    assert "pixel rendering" in checked["not_checked"]


def test_native_collection_reordering_is_not_schematic_change(producer):
    options, source, expected, params, _calls, _behavior = producer
    result = N._export_pdf(params)
    after = copy.deepcopy(source)
    after["native"]["components"].reverse()
    after["native"]["sheets"].reverse()
    assert P.verify(source, after, result, expected)["valid"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows native path spelling contract")
def test_before_and_after_protection_accept_same_absolute_windows_path_spelling(producer):
    _options, source, expected, params, _calls, _behavior = producer
    result = N._export_pdf(params)
    before, after = copy.deepcopy(source), copy.deepcopy(source)
    before["application"]["document"]["path"] = expected["project"]["path"].lower()
    after["application"]["document"]["path"] = (
        expected["project"]["path"].swapcase().replace("\\", "/")
    )
    checked = P.verify(before, after, result, expected)
    assert checked["valid"] is True
    assert [
        row for row in checked["checks"] if row["id"].startswith("native-source-protected-")
    ] == [
        {"id": "native-source-protected-before", "passed": True},
        {"id": "native-source-protected-after", "passed": True},
    ]


@pytest.mark.parametrize("label", ["before", "after"])
@pytest.mark.parametrize(
    "fault",
    ["foreign", "prefix", "parent", "relative", "missing", "pid", "closed", "status", "native_net"],
)
def test_each_protected_readback_keeps_path_identity_and_native_material_guards(
    producer, label, fault
):
    _options, source, expected, params, _calls, _behavior = producer
    result = N._export_pdf(params)
    before, after = copy.deepcopy(source), copy.deepcopy(source)
    changed = before if label == "before" else after
    document = changed["application"]["document"]
    project = Path(expected["project"]["path"])
    if fault == "foreign":
        document["path"] = str(project.with_name("different.prj"))
    elif fault == "prefix":
        document["path"] = str(project) + ".unrelated"
    elif fault == "parent":
        document["path"] = str(project.parent / "child" / ".." / project.name)
    elif fault == "relative":
        document["path"] = project.name
    elif fault == "missing":
        document.pop("path")
    elif fault == "pid":
        changed["application"]["pid"] = 9001
    elif fault == "closed":
        document["open"] = False
    elif fault == "status":
        document.pop("status")
    else:
        changed["native"]["components"][0]["pins"][0]["net"] = "WRONG"
    checked = P.verify(before, after, result, expected)
    assert checked["valid"] is False
    protected = {
        row["id"]: row["passed"]
        for row in checked["checks"]
        if row["id"].startswith("native-source-protected-")
    }
    assert protected["native-source-protected-" + label] is False
    other = "after" if label == "before" else "before"
    assert protected["native-source-protected-" + other] is True


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_project",
        "wrong_generation",
        "wrong_output",
        "wrong_hash",
        "wrong_size",
        "wrong_invocation",
        "wrong_native_net",
        "wrong_native_pid",
        "wrong_native_project",
        "missing_native_after",
    ],
)
def test_binding_and_native_mismatch_cannot_pass(producer, fault):
    options, source, expected, params, _calls, _behavior = producer
    result = N._export_pdf(params)
    after = copy.deepcopy(source)
    if fault == "wrong_project":
        result["artifact"]["project"] = "C:/foreign/other.prj"
    elif fault == "wrong_generation":
        result["artifact"]["generation_id"] = "op-old"
    elif fault == "wrong_output":
        result["artifact"]["output"] = "C:/foreign/other.pdf"
    elif fault == "wrong_hash":
        result["artifact"]["sha256"] = "0" * 64
    elif fault == "wrong_size":
        result["pages"][0]["width_pt"] = 1
    elif fault == "wrong_invocation":
        result["artifact"]["invocation"]["argv"][-1] = "0"
    elif fault == "wrong_native_net":
        after["native"]["components"][0]["pins"][0]["net"] = "WRONG"
    elif fault == "wrong_native_pid":
        after["application"]["pid"] = 9000
    elif fault == "wrong_native_project":
        after["application"]["document"]["path"] = "C:/foreign/other.prj"
    else:
        after.pop("native")
    assert P.verify(source, after, result, expected)["valid"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "bad_pdf",
        "missing_page",
        "producer_nonzero",
        "producer_replaced",
        "project_replaced",
        "old_bytes",
    ],
)
def test_corruption_and_old_or_replaced_inputs_cannot_pass(producer, fault):
    options, source, expected, params, _calls, behavior = producer
    if fault == "missing_page":
        behavior["pages"] = 1
    elif fault == "producer_nonzero":
        behavior["exit"] = 1
    elif fault == "old_bytes":
        write_pdf(options["output"])
        expected["previous_output"] = P.file_pin(options["output"])
        params["replace"] = True
    result = N._export_pdf(params)
    if fault == "bad_pdf":
        Path(options["output"]).write_bytes(b"%PDF-1.4\ncorrupt")
        result["artifact"]["sha256"] = hashlib.sha256(
            Path(options["output"]).read_bytes()
        ).hexdigest()
    elif fault == "producer_replaced":
        Path(expected["producer"]["path"]).write_bytes(b"replaced executable")
    elif fault == "project_replaced":
        Path(options["project"]).write_text("changed source project", encoding="utf-8")
    assert P.verify(source, source, result, expected)["valid"] is False


def test_replaced_plan_source_stops_before_converter_dispatch(producer):
    options, source, expected, params, calls, _behavior = producer
    Path(options["project"]).write_text("changed after preview", encoding="utf-8")
    with pytest.raises(N.AdapterError) as caught:
        N._export_pdf(params)
    assert caught.value.code == "E_CONFLICT"
    assert caught.value.details["write_attempted"] is False
    assert calls == []


def test_no_designer_witness_is_reported_separately(producer):
    options, source, expected, params, _calls, _behavior = producer
    expected = P.expectation(options, producer_executable=expected["producer"]["path"])
    params["_pdf_expectation"] = expected
    result = N._export_pdf(params)
    checked = P.verify({}, {}, result, expected)
    assert checked["valid"] is True
    assert checked["artifact_structure"] == "passed"
    assert checked["native_protection"] == "not_observed"
    assert "native schematic protection was not observed" in checked["not_checked"]


def test_converter_timeout_retains_dispatched_stage_without_replay(producer):
    _options, _source, _expected, params, calls, behavior = producer
    behavior["timeout"] = True
    with pytest.raises(N.AdapterError) as caught:
        N._export_pdf(params)
    assert caught.value.code == "E_TIMEOUT"
    assert caught.value.details["failed_stage"] == "sch2pdf_producer"
    assert caught.value.details["producer_dispatched"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("value", [0, -1, 586, float("inf"), float("nan"), "bad"])
def test_bad_child_budget_refuses_before_converter_dispatch(producer, value):
    _options, _source, _expected, params, calls, _behavior = producer
    params["_native_process_timeout_seconds"] = value
    with pytest.raises(N.AdapterError) as caught:
        N._export_pdf(params)
    assert caught.value.details["write_attempted"] is False
    assert calls == []


def test_compressed_stream_decodes_with_explicit_bounds(tmp_path):
    path = tmp_path / "bounded.pdf"
    write_pdf(path, compressed=True)
    assert P.decode_pdf(path)["page_count"] == 2


def test_optional_zero_destination_only_is_explicitly_observed(tmp_path):
    path = tmp_path / "dests.pdf"
    write_pdf(path, null_dest=True)
    checked = P.decode_pdf(path)
    assert checked["compatibility_observations"] == ["optional /Dests 0 0 R treated as null"]


@pytest.mark.parametrize("member", ["/Contents", "/BrokenResource"])
def test_zero_reference_compatibility_does_not_cover_other_objects(tmp_path, member):
    from pypdf.generic import IndirectObject

    path = tmp_path / "missing.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=1190, height=842)
    page[NameObject(member)] = IndirectObject(0, 0, writer)
    with path.open("wb") as output:
        writer.write(output)
    with pytest.raises(PdfReadError):
        P.decode_pdf(path)


@pytest.mark.parametrize("fault", ["bad_xref", "incremental", "encrypted", "newer_version"])
def test_unsupported_pdf_features_cannot_invoke_parser_recovery(tmp_path, fault):
    path = tmp_path / "unsupported.pdf"
    write_pdf(path)
    data = path.read_bytes()
    if fault == "bad_xref":
        data = data.replace(b"startxref\n", b"startxref\n999")
    elif fault == "incremental":
        data += b"\nstartxref\n0\n%%EOF\n"
    elif fault == "encrypted":
        data = data.replace(b"trailer\n<<", b"trailer\n<< /Encrypt 0 0 R")
    else:
        data = data.replace(b"%PDF-1.3", b"%PDF-1.7")
    path.write_bytes(data)
    with pytest.raises(ValueError):
        P.decode_pdf(path)


@pytest.mark.parametrize(
    "fault",
    [
        "stream_limit",
        "page_limit",
        "file_limit",
        "object_limit",
        "empty_page",
        "unsupported_filter",
    ],
)
def test_resource_and_unsupported_format_limits_fail_closed(tmp_path, monkeypatch, fault):
    path = tmp_path / "limited.pdf"
    if fault == "stream_limit":
        write_pdf(path, content=b"0 0 m\n" * 100, compressed=True)
        monkeypatch.setitem(P.LIMITS, "stream_bytes", 100)
    elif fault == "empty_page":
        write_pdf(path, empty=True)
    elif fault == "unsupported_filter":
        writer = PdfWriter()
        page = writer.add_blank_page(width=1190, height=842)
        stream = EncodedStreamObject()
        stream._data = b"302030206d3e"
        stream[NameObject("/Filter")] = NameObject("/ASCIIHexDecode")
        page[NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as target:
            writer.write(target)
    else:
        write_pdf(path)
        monkeypatch.setitem(
            P.LIMITS,
            {"page_limit": "pages", "file_limit": "file_bytes", "object_limit": "objects"}[fault],
            1,
        )
    with pytest.raises((ValueError, PdfReadError)):
        P.decode_pdf(path)
