"""Supported tie observations on owned synthetic IDispatch objects, never CAD proof."""

from types import SimpleNamespace

import pytest
from test_copper_native import Collection, NativeGeometry, PropertyDispatch

from xpedition_cli import copper_ties as T
from xpedition_cli import native_com_adapter as A


class TieGeometry(NativeGeometry):
    def __init__(self, *args, is_path=True, arced=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_path, self.arced = is_path, arced
        self._oleobj_.values[5] = 0.254

    def IsPath(self):
        return self.is_path

    def IsArced(self):
        return self.arced


class DocumentDispatch:
    def __init__(self, collection, fault=None):
        self.collection, self.fault, self.calls = collection, fault, []

    def Invoke(self, dispid, locale, kind, wants_result, *arguments):
        self.calls.append((dispid, locale, kind, wants_result, arguments))
        assert (dispid, locale, kind, wants_result, arguments) == (154, 0, 2, 1, (0, 0))
        if self.fault == "ties_collection_error":
            raise RuntimeError("synthetic collection unavailable")
        if self.fault == "ties_drift" and len(self.calls) == 2:
            self.collection[0].Geometry._oleobj_.values[5] = 0.3
        return self.collection


class FailingCollection(Collection):
    def Item(self, number):
        if number == 1:
            raise RuntimeError("synthetic first index unavailable")
        return super().Item(number)


def install_inventory(doc, fault=None):
    """Install one open arced path and one filled circle; no implied pin ownership."""
    path = TieGeometry([[1, 2, 0], [3, 4, 0.5]], closed=False, arced=True)
    path.Filled = False
    circle = TieGeometry(
        circle=(4, 5, 0.4),
        is_path=False,
        cutouts=[TieGeometry(circle=(4, 5, 0.1), is_path=False)],
    )
    items = []
    for uid, geometry in [("tie-1", path), ("tie-2", circle)]:
        extrema = SimpleNamespace(getter_calls=[])
        extrema._oleobj_ = PropertyDispatch(
            extrema, dict(zip((1, 2, 3, 4), (1, 2, 5, 6), strict=True))
        )
        items.append(
            SimpleNamespace(
                UniqueId=uid,
                Net=SimpleNamespace(Name="A", UniqueId="11"),
                Layer=7,  # Intentionally different from existing conductor index2.
                Type2=13,
                IsValid=lambda: True,
                Extrema=extrema,
                Geometry=geometry,
            )
        )
    if fault == "ties_width_error":
        path._oleobj_.values[5] = RuntimeError("synthetic width unavailable")
    if fault == "ties_cutout_error":
        circle.Cutouts[0]._oleobj_.values[12] = RuntimeError("synthetic cutout unavailable")
    collection = FailingCollection(items) if fault == "ties_item_error" else Collection(items)
    if fault == "ties_empty":
        collection.clear()
    doc._oleobj_ = DocumentDispatch(collection, fault)
    return doc


@pytest.fixture
def doc(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(DISPATCH_PROPERTYGET=2))
    return install_inventory(SimpleNamespace())


def test_supported_explicit_unit_reads_preserve_open_path_arcs_and_native_layer(doc):
    value = T.observe(doc, A)
    assert value["complete"] is True
    assert value["collection"]["count"] == 2
    first, second = value["collection"]["items"]
    assert (first["uid"], first["net"], first["net_uid"], first["native_layer"]) == (
        "tie-1",
        "A",
        "11",
        7,
    )
    path = first["geometry"]
    assert path["is_path"] is True and path["is_closed"] is False
    assert path["is_arced"] is True and path["filled"] is False
    assert path["points_xyr_mm"] == [[1.0, 2.0, 0.0], [3.0, 4.0, 0.5]]
    assert path["line_width_mm"] == 0.254
    assert first["extrema_mm"] == [1.0, 2.0, 5.0, 6.0]
    assert second["geometry"]["circle_mm"] == [4.0, 5.0, 0.4]
    assert second["geometry"]["cutouts"]["items"][0]["circle_mm"] == [4.0, 5.0, 0.1]
    for item in doc._oleobj_.collection:
        assert all(call[-1] == (4,) for call in item.Extrema.getter_calls)
        assert all(call[-1] == (4,) for call in item.Geometry.getter_calls)
    result = T.compare(value, T.observe(doc, A))
    assert result["consistency"] == "two_matching_complete_reads"
    assert result["evidence_boundary"]["complete_material_proven"] is False


@pytest.mark.parametrize("fault", ["ties_width_error", "ties_cutout_error", "ties_item_error"])
def test_getter_and_index_failures_remain_explicit_without_losing_other_indices(doc, fault):
    install_inventory(doc, fault)
    first = T.observe(doc, A)
    result = T.compare(first, T.observe(doc, A))
    assert first["complete"] is False
    assert result["comparison"]["equal"] is True
    assert result["consistency"] == "unverified"
    assert [v["index"] for v in result["collection"]["items"]] == [1, 2]
    assert result["collection"]["items"][1]["uid"] == "tie-2"
    assert result["before"] == first


def test_empty_is_current_collection_only_and_drift_does_not_compare_equal(doc):
    install_inventory(doc, "ties_empty")
    value = T.observe(doc, A)
    result = T.compare(value, T.observe(doc, A))
    assert value["complete"] is True and value["collection"]["count"] == 0
    assert "does not prove absence" in result["evidence_boundary"]["empty_meaning"]
    install_inventory(doc, "ties_drift")
    first, second = T.observe(doc, A), T.observe(doc, A)
    result = T.compare(first, second)
    assert first["complete"] and second["complete"]
    assert result["comparison"]["equal"] is False and result["consistency"] == "unverified"
    assert result["before"] == first


def test_collection_failure_and_bounded_truncation_are_not_empty_success(doc, monkeypatch):
    install_inventory(doc, "ties_collection_error")
    value = T.observe(doc, A)
    assert value["collection"] is None and value["complete"] is False
    assert value["errors"][0]["field"] == "collection"
    install_inventory(doc)
    monkeypatch.setattr(T, "MAX_ITEMS", 1)
    value = T.observe(doc, A)
    assert value["complete"] is False
    assert value["collection"]["count"] == 2
    assert value["collection"]["truncated"] is True
    assert len(value["collection"]["items"]) == 1


def test_null_geometry_duplicate_uid_and_nonfinite_points_cannot_be_complete(doc):
    items = doc._oleobj_.collection
    items[0].Geometry._oleobj_.values[1] = ((1, float("nan")), (2, 3), (0, 0))
    items[1].Geometry = None
    items[1].UniqueId = items[0].UniqueId
    result = T.observe(doc, A)
    assert result["complete"] is False
    assert result["errors"][0]["cause"] == "duplicate_uid"
    first, second = result["collection"]["items"]
    assert first["geometry"]["points_xyr_mm"] is None
    assert first["geometry"]["line_width_mm"] == 0.254
    assert second["geometry"] is None and second["errors"][0]["field"] == "geometry"


class RecordingCollection(Collection):
    """Count actual native-shaped index requests without allocating large inputs."""

    def __init__(self, items=(), *, count=None):
        super().__init__(items)
        self.reported_count = len(self) if count is None else count
        self.count_reads = 0
        self.item_reads = []

    @property
    def Count(self):
        self.count_reads += 1
        if isinstance(self.reported_count, Exception):
            raise self.reported_count
        return self.reported_count

    def Item(self, number):
        self.item_reads.append(number)
        return super().Item(number)


def single_geometry(doc, geometry):
    item = doc._oleobj_.collection[0]
    item.Geometry = geometry
    doc._oleobj_.collection = RecordingCollection([item])
    return doc._oleobj_.collection


@pytest.mark.parametrize("location", ["document", "cutouts"])
@pytest.mark.parametrize("count", [-1, True, 1.5, RuntimeError("private native text")])
def test_invalid_native_count_never_requests_an_index(doc, location, count):
    collection = RecordingCollection(count=count)
    if location == "document":
        doc._oleobj_.collection = collection
    else:
        geometry = TieGeometry()
        geometry.Cutouts = collection
        single_geometry(doc, geometry)
    result = T.observe(doc, A)
    observed = result["collection"]
    if location == "cutouts":
        observed = observed["items"][0]["geometry"]["cutouts"]
    assert result["complete"] is False
    assert collection.count_reads == 1 and collection.item_reads == []
    assert observed["items"] == [] and observed["truncated"] is False
    assert observed["errors"] == [
        {
            "field": "count",
            "cause": "negative_count"
            if count == -1
            else type(count).__name__
            if isinstance(count, Exception)
            else "ValueError",
        }
    ]
    assert "private native text" not in str(result)


def test_total_index_budget_is_shared_by_document_and_nested_collections(doc, monkeypatch):
    leaf = TieGeometry([[1, 2, 0]])
    leaf.Cutouts = RecordingCollection()
    child = TieGeometry([[1, 2, 0]], cutouts=[leaf])
    child.Cutouts = RecordingCollection(child.Cutouts)
    root = TieGeometry([[1, 2, 0]], cutouts=[child, TieGeometry()])
    root.Cutouts = RecordingCollection(root.Cutouts)
    top = single_geometry(doc, root)
    monkeypatch.setattr(T, "MAX_INDICES", 2)

    result = T.observe(doc, A)

    assert result["limits"]["total_collection_indices"] == 2
    assert result["complete"] is False
    assert top.item_reads == [1] and root.Cutouts.item_reads == [1]
    assert child.Cutouts.item_reads == [] and leaf.getter_calls == []
    assert sum(len(v.item_reads) for v in (top, root.Cutouts, child.Cutouts)) == 2
    cutouts = result["collection"]["items"][0]["geometry"]["cutouts"]
    assert cutouts["count"] == 2 and cutouts["truncated"] is True
    nested = cutouts["items"][0]["cutouts"]
    assert nested["count"] == 1 and nested["items"] == [] and nested["truncated"] is True


def test_collection_limit_bounds_native_index_calls_even_with_large_reported_count(
    doc, monkeypatch
):
    collection = RecordingCollection(doc._oleobj_.collection[:1], count=1000000)
    doc._oleobj_.collection = collection
    monkeypatch.setattr(T, "MAX_ITEMS", 1)

    result = T.observe(doc, A)

    assert result["complete"] is False
    assert collection.count_reads == 1 and collection.item_reads == [1]
    assert result["collection"]["count"] == 1000000
    assert result["collection"]["truncated"] is True
    assert len(result["collection"]["items"]) == 1


def test_geometry_budget_prevents_all_getters_on_later_geometries(doc, monkeypatch):
    child, second = TieGeometry(), TieGeometry()
    root = TieGeometry(cutouts=[child, second])
    root.Cutouts = RecordingCollection(root.Cutouts)
    single_geometry(doc, root)
    monkeypatch.setattr(T, "MAX_GEOMETRIES", 1)

    result = T.observe(doc, A)

    assert result["complete"] is False
    assert [v[0] for v in root.getter_calls] == [5, 1]
    assert root.Cutouts.item_reads == [1, 2]
    for geometry in (child, second):
        assert geometry.getter_calls == [] and geometry.method_calls == []
    observed = result["collection"]["items"][0]["geometry"]["cutouts"]["items"]
    assert [v["index"] for v in observed] == [1, 2]
    assert all(
        v["errors"] == [{"field": "geometry", "cause": "geometry_budget_exceeded"}]
        for v in observed
    )


def test_depth_limit_retains_denied_index_without_reading_its_geometry(doc, monkeypatch):
    denied = TieGeometry()
    child = TieGeometry(cutouts=[denied])
    child.Cutouts = RecordingCollection(child.Cutouts)
    root = TieGeometry(cutouts=[child])
    single_geometry(doc, root)
    monkeypatch.setattr(T, "MAX_DEPTH", 1)

    result = T.observe(doc, A)

    assert result["complete"] is False
    assert root.getter_calls and child.getter_calls
    assert child.Cutouts.item_reads == [1]
    assert denied.getter_calls == [] and denied.method_calls == []
    observed = result["collection"]["items"][0]["geometry"]["cutouts"]["items"][0]
    assert observed["cutouts"]["items"] == [
        {
            "index": 1,
            "errors": [{"field": "geometry", "cause": "geometry_budget_exceeded"}],
        }
    ]


@pytest.mark.parametrize("child_points", [1, 2])
def test_total_point_budget_is_shared_and_preserves_readable_nonpoint_fields(
    doc, monkeypatch, child_points
):
    child = TieGeometry([[3, 4, 0], [5, 6, 0]][:child_points])
    root = TieGeometry([[1, 2, 0], [2, 3, 0]], cutouts=[child])
    single_geometry(doc, root)
    monkeypatch.setattr(T, "MAX_POINTS", 3)

    result = T.observe(doc, A)

    assert result["complete"] is (child_points == 1)
    observed = result["collection"]["items"][0]["geometry"]
    assert observed["points_xyr_mm"] == [[1.0, 2.0, 0.0], [2.0, 3.0, 0.0]]
    nested = observed["cutouts"]["items"][0]
    assert nested["line_width_mm"] == 0.254
    if child_points == 1:
        assert nested["points_xyr_mm"] == [[3.0, 4.0, 0.0]] and nested["errors"] == []
    else:
        assert nested["points_xyr_mm"] is None
        assert nested["errors"] == [{"field": "points_xyr_mm", "cause": "ValueError"}]
    # A single native array getter is needed to establish its length. The budget
    # bounds accepted coordinate triples; it does not promise native array paging.
    assert sum(v[0] == 1 for v in root.getter_calls) == 1
    assert sum(v[0] == 1 for v in child.getter_calls) == 1


@pytest.mark.parametrize(
    "rows",
    [
        None,
        (),
        [[1], [2]],
        [[1], [2], [0], [0]],
        [[1, 2], [3], [0, 0]],
        ["x", [2], [0]],
        [[1, True], [2, 3], [0, 0]],
        [[1], [float("inf")], [0]],
        [[1], [2], ["0"]],
    ],
)
def test_malformed_point_arrays_are_typed_failures_not_empty_complete_reads(doc, rows):
    geometry = TieGeometry()
    geometry._oleobj_.values[1] = rows
    single_geometry(doc, geometry)

    first, second = T.observe(doc, A), T.observe(doc, A)
    result = T.compare(first, second)

    assert first["complete"] is False
    assert result["comparison"]["equal"] is True
    assert result["consistency"] == "unverified"
    observed = first["collection"]["items"][0]["geometry"]
    assert observed["points_xyr_mm"] is None and observed["line_width_mm"] == 0.254
    assert observed["errors"] == [{"field": "points_xyr_mm", "cause": "ValueError"}]


def test_duplicate_zero_uid_keeps_raw_indices_and_paths_without_inventing_ids(doc):
    for item in doc._oleobj_.collection:
        item.UniqueId = 0
    first, second = T.observe(doc, A), T.observe(doc, A)
    result = T.compare(first, second)
    assert result["comparison"]["equal"] is True and result["consistency"] == "unverified"
    assert [(v["index"], v["uid"]) for v in result["collection"]["items"]] == [(1, "0"), (2, "0")]
    assert result["collection"]["items"][0]["geometry"]["points_xyr_mm"] == [
        [1.0, 2.0, 0.0],
        [3.0, 4.0, 0.5],
    ]
    assert result["evidence_boundary"]["complete_material_proven"] is False
