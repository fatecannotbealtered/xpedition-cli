"""Native-shaped source inputs shared by adapter and public-process checks; no CAD."""

from types import SimpleNamespace as NS

from xpedition_cli import native_com_adapter as A


class Collection(list):
    @property
    def Count(self):
        return len(self)

    def Item(self, number):
        return self[number - 1]


class FailedGetter(NS):
    def __getattribute__(self, name):
        if name == object.__getattribute__(self, "failed_member"):
            raise RuntimeError("owned synthetic getter failure")
        return super().__getattribute__(name)


def geometry(points, *, width=10.0, cutouts=()):
    return NS(
        IsCircle=lambda: False,
        PointsArray=tuple(tuple(p[index] / A.TH_TO_MM for p in points) for index in range(3)),
        LineWidth=width,
        Cutouts=Collection(cutouts),
    )


def native_document(mode="valid"):
    net = NS(Name="QA_SIGNAL", UniqueId=11, NumberOfOpens=0, IsRouted=lambda: True)
    valid = NS(
        UniqueId=318,
        Net=net,
        Layer=1,
        Geometry=geometry([[20, 15, 0], [24, 15, 0]]),
    )
    text = NS(
        PositionX=800,
        PositionY=600,
        TextString="R1",
        Type=2,
        TextType=1,
        UniqueId=501,
        Component=None,
        SideName="Top",
        Format=NS(Height=40, Orientation=270, Mirrored=False, PenWidth=4),
        Extrema=NS(
            GetMinX=lambda unit: 20.0,
            GetMinY=lambda unit: 14.8,
            GetMaxX=lambda unit: 22.0,
            GetMaxY=lambda unit: 15.8,
        ),
        StrokeText=lambda unit: [0.1, 20.1, 14.9, 0, 21.9, 15.7, 2],
    )
    outline = geometry([[0, 0, 0], [30, 0, 0], [30, 30, 0], [0, 30, 0], [0, 0, 0]])
    doc = NS(
        CurrentUnit=2,
        LayerCount=2,
        BoardOutline=NS(Geometry=outline),
        Components=Collection(),
        Traces=Collection([valid]),
        Vias=Collection(),
        MountingHoles=Collection(),
        PlaneShapes=Collection(),
        FabricationLayerGfxs=Collection(),
        FabricationLayerTexts=Collection([text]),
        Nets=Collection([net]),
    )
    invalid = NS(
        UniqueId=317,
        Net=net,
        Layer=1,
        Geometry=NS(IsCircle=lambda: False, PointsArray=(), LineWidth=31, Cutouts=Collection()),
    )
    if mode in {"empty_trace", "first_trace_unreadable"}:
        doc.Traces = Collection([invalid] + ([valid] if mode == "first_trace_unreadable" else []))
    elif mode == "trace_geometry_getter_failed":
        doc.Traces = Collection([FailedGetter(failed_member="Geometry", **vars(invalid))])
    elif mode == "empty_graphic":
        doc.Traces.clear()
        doc.FabricationLayerGfxs = Collection(
            [NS(UniqueId=601, Type=2, Component=None, SideName="Top", Geometry=invalid.Geometry)]
        )
    elif mode == "graphic_geometry_getter_failed":
        doc.Traces.clear()
        doc.FabricationLayerGfxs = Collection(
            [
                FailedGetter(
                    failed_member="Geometry", UniqueId=601, Type=2, Component=None, SideName="Top"
                )
            ]
        )
    elif mode.startswith("trace_layer_"):
        if mode == "trace_layer_missing":
            del valid.Layer
        elif mode == "trace_layer_failed":
            doc.Traces = Collection([FailedGetter(failed_member="Layer", **vars(valid))])
        else:
            valid.Layer = {
                "trace_layer_zero": 0,
                "trace_layer_invalid": "bad",
                "trace_layer_large": 3,
            }[mode]
    elif mode.startswith("layer_count_"):
        text.SideName = "Bottom"
        valid.Layer = 1
        if mode == "layer_count_missing":
            del doc.LayerCount
        elif mode == "layer_count_failed":
            doc = FailedGetter(failed_member="LayerCount", **vars(doc))
        else:
            doc.LayerCount = {"layer_count_zero": 0, "layer_count_invalid": "bad"}[mode]
    elif mode == "board_cutout":
        outline.Cutouts.append(
            geometry([[19, 14, 0], [23, 14, 0], [23, 17, 0], [19, 17, 0], [19, 14, 0]])
        )
    elif mode == "board_cutouts_failed":
        doc.BoardOutline.Geometry = FailedGetter(failed_member="Cutouts", **vars(outline))
    return doc


def bind_source(monkeypatch, board, mode="valid"):
    doc = native_document(mode)
    doc.FullName = str(board.resolve())
    app = NS(ActiveDocument=doc)
    monkeypatch.setattr(A, "_layout_board_path", lambda params: board)
    monkeypatch.setattr(A, "_application", lambda *args, **kwargs: app)
    monkeypatch.setattr(A, "_open_layout_document", lambda *args: (doc, []))
    monkeypatch.setattr(A, "_licensed_document", lambda *args: doc)

    def identity(observed, domain):
        assert observed is app and domain == "pcb"
        return {
            "pid": 4242,
            "document": {"open": True, "path": doc.FullName, "status": "observed"},
        }

    monkeypatch.setattr(A, "application_identity", identity)

    def property_collection(owner, key, *args):
        assert owner is doc
        assert key in {A.LAYOUT_LAYER_STACK, A.LAYOUT_OBSTRUCTS, A.LAYOUT_PLACEMENT_OBSTRUCTS}
        return Collection()

    monkeypatch.setattr(A, "_propget", property_collection)
    return doc
