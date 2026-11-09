"""Fake Layout COM objects for the Layout tests (test_layout*.py); this module holds no tests.

The fakes take and give millimetres where Layout's getters take a unit (``GetPositionX(4)``,
``Extrema``) and mils where Layout gives raw geometry (``PointsArray``), as Layout does on a
board in mils. ``world(app)`` replaces COM, the process list, the dialogs and the licence server,
so nothing reaches a real Xpedition.
"""
from __future__ import annotations

import math
import os
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any
from unittest import mock

from xpedition_cli.adapter import base
from xpedition_cli.adapter import layout as L
from xpedition_cli.adapter.platform import com, dialogs, processes

PID = 4242
SESSION = f"layout-{PID}-6553f1a0"
MIL = 0.0254
BOARD = os.path.abspath("D:/work/demo/PCB/Demo.pcb")


class FakeComError(Exception):
    """Shaped like pywin32's ``com_error``: ``(hresult, text, excepinfo, argerror)``."""

    def __init__(self, hresult: int = -2147352567, text: str = "Exception occurred.", excepinfo: tuple | None = None) -> None:
        super().__init__(hresult, text, excepinfo, None)
        self.hresult = hresult


def licence_fault() -> FakeComError:
    return FakeComError(-2147352567, "Exception occurred.", (0, "Xpedition Layout", "no licensing call", None, 10279, -2147220947))


class Collection(list):
    @property
    def Count(self) -> int:  # noqa: N802 - COM property
        return len(self)

    def Item(self, index: int) -> Any:  # noqa: N802 - COM method, one-based
        return self[index - 1]


class Ole:
    """The ``_oleobj_`` of a fake: dispid properties for ``com.propget`` and type information."""

    def __init__(self, owner: Any) -> None:
        self.owner = owner

    def Invoke(self, dispid: int, lcid: int, flags: int, result: int, *args: Any) -> Any:  # noqa: N802
        value = self.owner.props[dispid]
        if isinstance(value, Exception):
            raise value
        return value(*args) if callable(value) else value

    def GetTypeInfo(self) -> Any:  # noqa: N802
        return SimpleNamespace(GetDocumentation=lambda index: (getattr(self.owner, "type_name", ""), None, 0, None))


class Fake:
    props: dict[int, Any] = {}
    type_name = ""

    @property
    def _oleobj_(self) -> Ole:
        return Ole(self)


class Extrema:
    def __init__(self, box: tuple[float, float, float, float]) -> None:
        self.box = box

    def GetMinX(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self.box[0]

    def GetMinY(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self.box[1]

    def GetMaxX(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self.box[2]

    def GetMaxY(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self.box[3]


class Geometry:
    """Raw geometry in mils, built from mm."""

    def __init__(self, rows_mm: list[list[float]] | None = None, circle_mm: tuple[float, float, float] | None = None,
                 width_mm: float = 0.0, filled: bool = False) -> None:
        self.rows = [[value / MIL for value in row] for row in rows_mm] if rows_mm else None
        self.circle = circle_mm
        self.LineWidth = width_mm / MIL
        self.Filled = filled
        self.Cutouts = Collection()

    def IsCircle(self) -> bool:  # noqa: N802
        return self.circle is not None

    @property
    def PointsArray(self) -> Any:  # noqa: N802
        return self.rows

    @property
    def CircleX(self) -> float:  # noqa: N802
        return self.circle[0] / MIL

    @property
    def CircleY(self) -> float:  # noqa: N802
        return self.circle[1] / MIL

    @property
    def CircleR(self) -> float:  # noqa: N802
        return self.circle[2] / MIL


def rect_rows(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], [0.0] * 5]


class Outline:
    def __init__(self, rows_mm: list[list[float]]) -> None:
        self.Geometry = Geometry(rows_mm)
        xs, ys = rows_mm[0], rows_mm[1]
        self.Extrema = Extrema((min(xs), min(ys), max(xs), max(ys)))


def turn(dx: float, dy: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return round(dx * math.cos(angle) - dy * math.sin(angle), 9), round(dx * math.sin(angle) + dy * math.cos(angle), 9)


class FakePin(Fake):
    type_name = "IMGCPCBPin"

    def __init__(self, component: FakeComponent, name: str, dx: float, dy: float, net: str) -> None:
        self.Component = component
        self.Name = name
        self.offset = (dx, dy)
        self.net_name = net
        self.Pads = Collection([SimpleNamespace(Layer=1, Geometries=Collection([Geometry(circle_mm=(0.0, 0.0, 0.3))]))])
        self.Holes = Collection()

    @property
    def Net(self) -> Any:  # noqa: N802
        return SimpleNamespace(Name=self.net_name) if self.net_name else None

    def _at(self) -> tuple[float, float]:
        dx, dy = turn(*self.offset, self.Component.Orientation)
        return self.Component.x + dx, self.Component.y + dy

    def GetPositionX(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self._at()[0]

    def GetPositionY(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self._at()[1]


class FakeComponent(Fake):
    """A part whose footprint is ``width`` x ``height``, with its cell origin ``offset`` from the
    footprint's centre and pins ``{name: (dx, dy, net)}`` measured from the origin, unturned."""

    def __init__(self, refdes: str, width: float = 1.6, height: float = 0.8, *, offset: tuple[float, float] = (0.0, 0.0),
                 pins: dict[str, tuple[float, float, str]] | None = None, placed: bool = False, x: float = 0.0, y: float = 0.0,
                 rotation: float = 0.0, cell: str = "CELL", part_number: str = "PN-1") -> None:
        self.RefDes = refdes
        self.CellName = cell
        self.PartNumber = part_number
        self.size = (width, height)
        self.offset = offset
        self.Placed = placed
        self.x, self.y, self.Orientation = x, y, rotation
        self.Side = 1
        self.calls: list[tuple[Any, ...]] = []
        self.refuse_place = False
        self.pin_list = Collection(FakePin(self, name, dx, dy, net) for name, (dx, dy, net) in (pins or {}).items())

    def Place(self, x: float, y: float, rotation: float, top: bool, a: int, unit: int, b: int) -> None:  # noqa: N802
        assert unit == L.UNIT_MM and top is True
        self.calls.append(("Place", x, y, rotation))
        if self.refuse_place:
            raise FakeComError(text="DRC violation")
        self.Placed, self.x, self.y, self.Orientation = True, x, y, rotation

    def UnPlace(self) -> None:  # noqa: N802
        self.calls.append(("UnPlace",))
        self.Placed = False

    def GetPositionX(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self.x

    def GetPositionY(self, unit: int) -> float:  # noqa: N802
        assert unit == L.UNIT_MM
        return self.y

    @property
    def Extrema(self) -> Extrema:  # noqa: N802
        if not self.Placed:
            raise FakeComError(text="no extents for an unplaced component")
        width, height = self.size
        if round(self.Orientation) % 180 == 90:
            width, height = height, width
        ox, oy = turn(*self.offset, self.Orientation)
        cx, cy = self.x - ox, self.y - oy
        return Extrema((cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2))

    @property
    def Pins(self) -> Collection:  # noqa: N802
        return self.pin_list


class FakeNet:
    def __init__(self, doc: FakeDoc, name: str, routed: bool = False, opens: int = 1, power: bool = False) -> None:
        self.doc, self.Name, self.routed, self.NumberOfOpens, self.IsPower = doc, name, routed, opens, power
        self.UniqueId = f"net-{name}"

    def IsRouted(self) -> bool:  # noqa: N802 - a method in 151.0
        return self.routed

    @property
    def Pins(self) -> Collection:  # noqa: N802 - only placed parts' pins are listed
        return Collection(pin for part in self.doc.components if part.Placed for pin in part.pin_list if pin.net_name == self.Name)


class FakeAssignment:
    def __init__(self, net: str, layer: int, state: int) -> None:
        self.net_name, self.layer, self.states = net, layer, [state]
        self.LayerObject = SimpleNamespace(ConductorLayer=layer)

    @property
    def Net(self) -> Any:  # noqa: N802
        return SimpleNamespace(Name=self.net_name)

    @property
    def PlaneDataState(self) -> int:  # noqa: N802
        return self.states[-1]

    @PlaneDataState.setter
    def PlaneDataState(self, value: int) -> None:  # noqa: N802
        self.states.append(value)


class FakeRoutePass:
    def __init__(self, doc: FakeDoc) -> None:
        self.doc = doc
        self.calls: list[tuple[Any, ...]] = []

    def __getattr__(self, name: str) -> Any:
        if name == "Go":
            return self.go
        return lambda *args: self.calls.append((name, *args))

    def go(self) -> None:
        self.calls.append(("Go",))
        self.doc.passes.append(self)
        if self.doc.on_go is not None:
            self.doc.on_go(self)


class FakeIntegration:
    def __init__(self, status: int = 1, allowed: bool = True) -> None:
        self.ForwardAnnotationStatus = status
        self.IsForwardAnnotationAllowed = allowed


class FakeDoc(Fake):
    def __init__(self, path: str = BOARD, components: list[FakeComponent] | None = None, nets: list[str] | None = None, *,
                 width: float = 50.0, height: float = 40.0, layers: int = 4, unit: int = L.UNIT_MILS) -> None:
        self.FullName = path
        self.LayerCount = layers
        self.CurrentUnit = unit
        self.components = list(components or [])
        self.nets = [FakeNet(self, name) for name in (nets or [])]
        self.BoardOutline: Any = Outline(rect_rows(0.0, 0.0, width, height))
        self.ProjectIntegration: Any = FakeIntegration()
        self.traces: list[Any] = []
        self.vias: list[Any] = []
        self.shapes: list[Any] = []
        self.assignments: list[FakeAssignment] = []
        self.generated: list[Any] = []
        self.hazards: list[Any] = []
        self.texts: list[Any] = []
        self.gfxs: list[Any] = []
        self.holes: list[Any] = []
        self.passes: list[FakeRoutePass] = []
        self.on_go: Any = None
        self.calls: list[tuple[Any, ...]] = []
        self.validated: list[Any] = []
        self.RespectComponentPlacementDRC = True
        self.drc_history: list[bool] = []
        self.save_answer: Any = True
        self.props: dict[int, Any] = {}

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "RespectComponentPlacementDRC" and "drc_history" in self.__dict__:
            self.drc_history.append(value)
        object.__setattr__(self, name, value)

    # collections
    @property
    def Components(self) -> Collection:  # noqa: N802
        return Collection(self.components)

    @property
    def Nets(self) -> Collection:  # noqa: N802
        return Collection(self.nets)

    @property
    def Traces(self) -> Collection:  # noqa: N802
        return Collection(self.traces)

    @property
    def Vias(self) -> Collection:  # noqa: N802
        return Collection(self.vias)

    @property
    def PlaneShapes(self) -> Collection:  # noqa: N802
        return Collection(self.shapes)

    @property
    def PlaneAssignments(self) -> Collection:  # noqa: N802
        return Collection(self.assignments)

    @property
    def GeneratedPlanes(self) -> Collection:  # noqa: N802
        return Collection(self.generated)

    @property
    def FabricationLayerTexts(self) -> Collection:  # noqa: N802
        return Collection(self.texts)

    @property
    def FabricationLayerGfxs(self) -> Collection:  # noqa: N802
        return Collection(self.gfxs)

    @property
    def MountingHoles(self) -> Collection:  # noqa: N802
        return Collection(self.holes)

    def net(self, name: str) -> FakeNet:
        return next(net for net in self.nets if net.Name == name)

    # members
    def Validate(self, key: Any) -> Any:  # noqa: N802
        self.validated.append(key)
        return 7 if key == 0 else True

    def FindComponent(self, refdes: str) -> FakeComponent | None:  # noqa: N802
        return next((part for part in self.components if part.RefDes == refdes), None)

    def _outline(self, name: str, n: int, rows: Any, *rest: Any) -> None:
        self.calls.append((name, n, rows, *rest))
        if name == "PutBoardOutline":
            self.BoardOutline = Outline([list(row) for row in rows])

    def PutBoardOutline(self, n: int, rows: Any, pen: float, unit: int) -> None:  # noqa: N802
        self._outline("PutBoardOutline", n, rows, pen, unit)

    def PutRouteBorder(self, n: int, rows: Any, pen: float, unit: int) -> None:  # noqa: N802
        self._outline("PutRouteBorder", n, rows, pen, unit)

    def PutManufacturingOutline(self, n: int, rows: Any, unit: int) -> None:  # noqa: N802
        self._outline("PutManufacturingOutline", n, rows, unit)

    def PutPlaneShape(self, layer: int, n: int, points: Any, net: Any, obstruct: bool, *rest: Any) -> Any:  # noqa: N802
        self.calls.append(("PutPlaneShape", layer, n, points, net.Name, obstruct, *rest))
        # the default plane class draws solid copper as hatch lines as wide as their distance (6 mil)
        shape = SimpleNamespace(Layer=layer, Net=SimpleNamespace(Name=net.Name),
                                PlaneClass=SimpleNamespace(Name="(Default)", HatchPattern=2, HatchWidth=6.0, HatchDistance=6.0),
                                Extrema=Extrema((min(points[0]), min(points[1]), max(points[0]), max(points[1]))))
        self.shapes.append(shape)
        self.assignments.append(FakeAssignment(net.Name, layer, L.PLANE_DRAFT))  # a new shape starts in Draft
        return shape

    def FindPlaneAssignment(self, layer: int, net: Any) -> FakeAssignment | None:  # noqa: N802
        return next((a for a in self.assignments if a.layer == layer and a.net_name == net.Name), None)

    def GetHazards(self, kind: int) -> Collection:  # noqa: N802
        self.calls.append(("GetHazards", kind))
        return Collection(self.hazards)

    def NewRoutePass(self) -> FakeRoutePass:  # noqa: N802
        return FakeRoutePass(self)

    def PutFabricationLayerText(self, text: str, x: float, y: float, *rest: Any) -> None:  # noqa: N802
        self.calls.append(("PutFabricationLayerText", text, x, y, *rest))

    def Save(self) -> Any:  # noqa: N802
        self.calls.append(("Save",))
        return self.save_answer

    def Close(self, save: bool) -> bool:  # noqa: N802
        self.calls.append(("Close", save))
        return True


class FakeGui:
    """Layout's GUI object: write-only suppression switches, the window handle, menu commands."""

    def __init__(self, rejects: set[str] | None = None) -> None:
        object.__setattr__(self, "rejects", rejects or set())
        object.__setattr__(self, "written", {})
        object.__setattr__(self, "commands", [])
        object.__setattr__(self, "accept", True)
        object.__setattr__(self, "on_command", None)
        object.__setattr__(self, "HWND", 0x5150)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.rejects:
            raise FakeComError(text=f"{name} is not supported")
        self.written[name] = value

    def set(self, name: str, value: Any) -> None:
        """Set a fake's own attribute (``accept``, ``on_command``), not a recorded switch."""
        object.__setattr__(self, name, value)

    def ProcessCommand(self, command: int) -> bool:  # noqa: N802
        self.commands.append(command)
        if self.on_command is not None:
            self.on_command(command)
        return self.accept


class FakeApp:
    def __init__(self, doc: FakeDoc | None = None) -> None:
        self.ActiveDocument = doc
        self.Gui = FakeGui()
        self.FullName = "C:/Xpedition/wg/win64/bin/ExpeditionPCB.exe"
        self.boards: dict[str, FakeDoc] = {}
        self.calls: list[tuple[Any, ...]] = []

    def OpenDocument(self, path: str) -> FakeDoc:  # noqa: N802
        self.calls.append(("OpenDocument", path))
        self.ActiveDocument = self.boards.get(os.path.normcase(path)) or FakeDoc(path)
        return self.ActiveDocument

    def Quit(self) -> None:  # noqa: N802
        self.calls.append(("Quit",))


class FakeAnswerer:
    """``dialogs.PromptAnswerer``: records the rules it was given, presses nothing."""

    made: list[FakeAnswerer] = []

    def __init__(self, rules: Any, pid: Any, interval_s: float = 1.0) -> None:
        self.rules, self.pid = tuple(rules), pid
        self.dialogs: list[dict[str, Any]] = []
        FakeAnswerer.made.append(self)

    def __enter__(self) -> FakeAnswerer:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def blocking_dialogs(self) -> list[dict[str, Any]]:
        return list(self.dialogs)


class Licensing:
    def __init__(self) -> None:
        self.keys: list[Any] = []

    def GetToken(self, key: Any) -> Any:  # noqa: N802
        self.keys.append(key)
        return f"token-{key}"


def process(pid: int = PID, path: str = "C:/Xpedition/wg/win64/bin/ExpeditionPCB.exe") -> processes.ProcessInfo:
    return processes.ProcessInfo(pid=pid, path=path, start_time=1)


@contextmanager
def world(app: FakeApp | None, *, running: list[processes.ProcessInfo] | None = None, blocking: list[dict[str, Any]] | None = None,
          processing: list[str] | None = None, windows: list[dict[str, Any]] | None = None):
    """Patch COM, the process list, the dialogs and the licence server around fake Layout objects."""
    FakeAnswerer.made = []
    licensing = Licensing()
    patches = [
        mock.patch.object(com, "active_object", lambda progids: app),
        mock.patch.object(com, "call_with_timeout", lambda fn, timeout: fn()),
        mock.patch.object(com, "initialized", nullcontext),
        mock.patch.object(com, "progids", lambda *bases: list(bases)),
        mock.patch.object(processes, "processes", lambda name: list(running if running is not None else [process()])),
        mock.patch.object(processes, "window_pid", lambda hwnd: PID),
        mock.patch.object(processes, "descendants", lambda pid: [PID]),
        mock.patch.object(processes, "alive", lambda pid: False),
        mock.patch.object(dialogs, "PromptAnswerer", FakeAnswerer),
        mock.patch.object(dialogs, "blocking_dialogs", lambda pid: list(blocking or [])),
        mock.patch.object(dialogs, "processing_windows", lambda pid: list(processing or [])),
        mock.patch.object(dialogs, "licence_windows", lambda pid: []),
        mock.patch.object(dialogs, "top_windows", lambda pid=None, titled_only=True: list(windows or [])),
        mock.patch.object(dialogs, "find_dialogs", lambda pid=None: []),
        mock.patch.object(L, "_dispatch", lambda progid: licensing),
        mock.patch.object(L, "CLOSE_SETTLE_S", 0.0),
    ]
    for patch in patches:
        patch.start()
    try:
        yield SimpleNamespace(licensing=licensing)
    finally:
        for patch in reversed(patches):
            patch.stop()


def context(command: str, inputs: dict[str, Any] | None = None, *, document: str = BOARD, **request: Any) -> base.Context:
    return base.Context(mock.Mock(), {"command": command, "inputs": dict(inputs or {}), "session": SESSION, "document": document,
                                      **request})


class Progress:
    """A long job's progress callback, recording what the handler reports."""

    def __init__(self) -> None:
        self.steps: list[tuple[Any, ...]] = []
        self.events: list[tuple[Any, ...]] = []

    def __call__(self, done: float, total: float | None, unit: str) -> None:
        self.steps.append((done, total, unit))

    def blocked(self, action: str, message: str) -> None:
        self.events.append(("blocked", action, message))

    def unblocked(self) -> None:
        self.events.append(("unblocked",))
