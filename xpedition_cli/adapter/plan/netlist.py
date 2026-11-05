"""A drawn schematic's netlist, as Designer reads it back, against the one the plan asked for.

`schematic_layout.plan_to_params` gives a `verify` record: the nets by name with their
pins (`R1.1`), the links (pin pairs an unnamed wire must join), the parts the plan places
and the pins it marks no-connect. `compare_netlist` holds a read-back snapshot (components
with pins that name their net) against it.
"""

from __future__ import annotations

from typing import Any


def compare_netlist(snapshot: dict[str, Any], verify: dict[str, Any], identities: dict[str, str] | None = None) -> dict[str, Any]:
    """Whether the read-back holds exactly the planned netlist.

    `identities` maps a pin (`R1.1`) to the identity of the net it is on, for the pins
    of unnamed nets that a snapshot does not name; the snapshot's own net names fill the
    rest.
    """
    actual: dict[str, set[str]] = {}
    pin_net: dict[str, str] = dict(identities or {})
    for component in snapshot.get("components", []):
        refdes = str(component.get("refdes") or "")
        for pin in component.get("pins", []):
            net = pin.get("net")
            number = pin.get("number")
            if refdes and net and number is not None:
                ref = f"{refdes}.{number}"
                actual.setdefault(str(net), set()).add(ref)
                pin_net.setdefault(ref, str(net))
    differences: list[dict[str, Any]] = []
    expected = verify.get("nets") or {}
    for net, pins in expected.items():
        want = {str(p) for p in pins}
        got = actual.get(str(net), set())
        if want != got:
            differences.append({"net": str(net), "missing": sorted(want - got), "unexpected": sorted(got - want)})
    broken: list[list[str]] = []
    for link in verify.get("links") or []:
        if len(link) != 2:
            continue
        a, b = str(link[0]), str(link[1])
        if not pin_net.get(a) or pin_net.get(a) != pin_net.get(b):
            broken.append([a, b])
    extra, unplanned, joined = beyond_the_plan(snapshot, verify, pin_net)
    return {
        "matches": not differences and not broken and not extra and not unplanned and not joined,
        "nets_checked": len(expected),
        "differences": differences,
        "links_checked": len(verify.get("links") or []),
        "links_broken": broken,
        "extra_nets": extra,
        "unplanned_components": unplanned,
        "no_connects_joined": joined,
    }


def beyond_the_plan(
    snapshot: dict[str, Any], verify: dict[str, Any], pin_net: dict[str, str]
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """What the read-back holds that the plan does not ask for.

    Walking the expected nets finds a planned pin on the wrong net, but not a net the
    plan never named: a template's leftover wiring, a planned pin wired into an
    unnamed junction, a no-connect pin joined to something. Those come back here as
    nets beyond the plan (with at least one planned pin), parts the plan did not
    place, and no-connect pins that share a net with another pin.
    """
    expected = verify.get("nets") or {}
    parts = verify.get("parts")
    planned = (
        {str(item) for item in parts}
        if parts is not None
        # a request without `parts`: the parts its nets name
        else {str(pin).rsplit(".", 1)[0] for pins in expected.values() for pin in pins}
    )
    groups: dict[str, set[str]] = {}
    for ref, identity in pin_net.items():
        groups.setdefault(str(identity), set()).add(str(ref))
    linked = {frozenset((str(link[0]), str(link[1]))) for link in verify.get("links") or [] if len(link) == 2}
    extra: list[dict[str, Any]] = []
    for identity, pins in sorted(groups.items()):
        if identity in expected or len(pins) < 2:
            continue
        if not any(pin.rsplit(".", 1)[0] in planned for pin in pins):
            continue
        # an unnamed junction the plan asked for joins exactly the pins of its links
        if all(any(frozenset((pin, other)) in linked for other in pins if other != pin) for pin in pins):
            continue
        extra.append({"net": identity, "pins": sorted(pins)})
    unplanned: list[str] = []
    if parts is not None:
        present = {str(component.get("refdes")) for component in snapshot.get("components", []) if component.get("refdes")}
        unplanned = sorted(present - planned)
    joined: list[dict[str, Any]] = []
    for ref in verify.get("no_connect") or []:
        identity = pin_net.get(str(ref))
        if identity is not None and len(groups.get(str(identity), ())) > 1:
            joined.append({"pin": str(ref), "net": str(identity), "pins": sorted(groups[str(identity)])})
    return extra, unplanned, joined
