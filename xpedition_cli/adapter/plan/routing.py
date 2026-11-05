"""The auto-router's passes and layers as a request names them, turned into what Layout's
`RoutePass` takes.

A pass list is `route:1-5,viamin,smooth:2`: a pass name, then its effort as N or N-M
(1 to 5; 1-3 when left out). Layers are `"1,4"`, a list, or nothing (every enabled layer).
"""

from __future__ import annotations

from typing import Any

from . import PlanError

# RoutePass.PassType values (EPcbAR…Pass) by the name the CLI takes
ROUTE_PASS_TYPES = {
    "fanout": 2,
    "route": 8,
    "novia": 6,
    "viamin": 14,
    "smooth": 10,
    "spread": 11,
    "expand": 1,
    "removehangers": 7,
}
ROUTE_ITEMS_ALL_NETS = 0  # epcbARAllNetsItem
ROUTE_ITEMS_SELECTED_NETS = 8  # epcbARSelectedNetsItem
ROUTE_ORDER_AUTO = 0  # epcbARAutoOrder
# RoutePass.LayerSelect(EPcbARLayerSelectType eType, long nLayer): 0 BottomUp, 1 TopDown,
# 2 AddLayer, 3 RemoveLayer. Called with (layer number, wanted) it reads layer 2 as
# AddLayer and 3 as RemoveLayer and refuses the outer layers.
ROUTE_LAYER_REMOVE = 3
DEFAULT_ROUTE_PASSES = "route:1-5,viamin:1-3,smooth:1-3"


def parse_route_passes(text: str) -> list[dict[str, Any]]:
    """`route:1-5,viamin,smooth:2` → pass records with their effort range (1..5); an empty
    text is `DEFAULT_ROUTE_PASSES`."""
    passes: list[dict[str, Any]] = []
    for token in str(text or DEFAULT_ROUTE_PASSES).split(","):
        token = token.strip().lower()
        if not token:
            continue
        name, _sep, effort = token.partition(":")
        if name not in ROUTE_PASS_TYPES:
            raise PlanError("E_USAGE", "unknown route pass", {"pass": name, "known": sorted(ROUTE_PASS_TYPES)})
        start, end = 1, 3
        if effort:
            first, _sep, last = effort.partition("-")
            try:
                start = int(first)
                end = int(last) if last else start
            except ValueError as exc:
                raise PlanError("E_USAGE", "effort is N or N-M", {"pass": token}) from exc
        if not 1 <= start <= end <= 5:
            raise PlanError("E_USAGE", "effort runs from 1 to 5", {"pass": token})
        passes.append({"pass": name, "type": ROUTE_PASS_TYPES[name], "effort": [start, end]})
    if not passes:
        raise PlanError("E_USAGE", "no route passes given")
    return passes


def parse_route_layers(value: Any) -> list[int]:
    """Routing layer numbers from `"1,4"`, a list, or nothing (every enabled layer)."""
    if value is None or value == "":
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    layers: list[int] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit() or int(text) < 1:
            raise PlanError("E_USAGE", f"routing layers must be positive numbers: {text!r}", {"layer": text})
        if int(text) not in layers:
            layers.append(int(text))
    return sorted(layers)
