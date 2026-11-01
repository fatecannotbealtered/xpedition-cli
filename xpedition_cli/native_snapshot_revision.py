"""Canonicalize native census order; retain every value and ordered geometry.

This applies only to the two explicit native snapshot projections, not arbitrary
lists, paths, contours, transcripts or material observations. It is not a native
completeness witness and cannot replace the strict full-board observers.
"""

from __future__ import annotations

import copy
import json


def recognized(value):
    return (
        isinstance(value, dict)
        and value.get("revision") == "native"
        and isinstance(value.get("metadata"), dict)
        and value["metadata"].get("backend") == "native_xpedition"
        and value.get("metadata", {}).get("source")
        in {"Viewdraw.Application", "MGCPCB.ExpeditionPCBApplication"}
    )


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _unique(rows, identity, label):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Native snapshot census is incomplete: " + label)
    keyed = {}
    for row in rows:
        key = identity(row)
        if key in keyed:
            raise ValueError("Native snapshot has duplicate stable identity: " + label)
        keyed[key] = row
    return [keyed[key] for key in sorted(keyed)]


def _text(value, label):
    if type(value) not in (str, int) or not str(value).strip():
        raise ValueError("Native snapshot lacks stable identity: " + label)
    return str(value)


def _blank_singleton_pin(row):
    """A complete unnumbered Designer terminal is identified by its sole owner."""
    pins = row.get("pins")
    if not (
        isinstance(pins, list)
        and len(pins) == 1
        and isinstance(pins[0], dict)
        and pins[0].get("number") == ""
    ):
        return False
    for name in ("pin_observation_coverage", "identity_observation_coverage"):
        coverage = row.get(name)
        if not (
            isinstance(coverage, dict)
            and coverage.get("complete") is True
            and coverage.get("errors") == []
        ):
            return False
    return True


def _components(rows, *, unnamed=False, designer_symbols=False):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Native component census is incomplete")
    identities, refs = set(), set()
    for row in rows:
        uid = _text(row.get("uid"), "component UID")
        if uid == "0":
            raise ValueError("Native component UID is unavailable")
        ref = row.get("refdes")
        if not isinstance(ref, str) or (not unnamed and not ref.strip()):
            raise ValueError("Native component reference identity is missing")
        if unnamed and ref:
            raise ValueError("Unnamed native symbols cannot carry a reference identity")
        if uid in identities or (ref and ref in refs):
            raise ValueError("Native component UID/reference identity is duplicated")
        identities.add(uid)
        if ref:
            refs.add(ref)
        # Normalize only native numeric/string UID representation.
        row["uid"] = uid
        # A fully observed single terminal on an unnamed Designer symbol needs
        # no made-up number or enumeration-order fallback. Keep its explicit
        # blank number and every observed field unchanged.
        if "pins" in row and not (unnamed and designer_symbols and _blank_singleton_pin(row)):
            row["pins"] = _unique(
                row["pins"], lambda pin: _text(pin.get("number"), "pin number"), "pin"
            )
        if designer_symbols and "attribute_display" in row:
            displays = row["attribute_display"]
            ordered = _unique(
                displays,
                lambda item: _text(item.get("name"), "attribute display name"),
                "attribute display",
            )
            names = [item["name"] for item in displays]
            if any(type(name) is not str for name in names) or len(
                {name.casefold() for name in names}
            ) != len(names):
                raise ValueError("Native attribute display names are missing or ambiguous")
            coverage = row.get("attribute_display_coverage")
            complete = (
                isinstance(coverage, dict)
                and coverage.get("enumeration_complete") is True
                and coverage.get("errors") == []
                and coverage.get("truncated") is False
                and type(coverage.get("attribute_count")) is int
                and coverage["attribute_count"] == len(displays)
            )
            # Attribute enumeration is unordered, but every full row remains.
            # Incomplete observations retain their original order and fields;
            # canonicalization does not manufacture a completeness witness.
            if complete:
                row["attribute_display"] = ordered
    return sorted(rows, key=lambda row: (row["uid"], row["refdes"]))


def canonical(value):
    """Reject ambiguous census identities; make permutation alone revision-neutral."""
    result = copy.deepcopy(value)
    if not recognized(result):
        return result
    for name in ("components", "unnamed_components"):
        if name in result:
            result[name] = _components(
                result[name],
                unnamed=name == "unnamed_components",
                designer_symbols=result["metadata"]["source"] == "Viewdraw.Application",
            )
    if "components" in result and "unnamed_components" in result:
        ids = [row["uid"] for name in ("components", "unnamed_components") for row in result[name]]
        if len(ids) != len(set(ids)):
            raise ValueError("Named and unnamed native components share a UID")
    if "nets" in result:
        result["nets"] = _unique(
            result["nets"], lambda row: _text(row.get("name"), "net name"), "net"
        )
        for row in result["nets"]:
            if "sheets" in row:
                sheets = row["sheets"]
                if not isinstance(sheets, list) or len(sheets) != len(set(sheets)):
                    raise ValueError("Native net sheet membership is incomplete or duplicated")
                row["sheets"] = sorted(sheets)
    if "sheets" in result:
        result["sheets"] = _unique(
            result["sheets"],
            lambda row: _text(row.get("number"), "sheet number"),
            "sheet",
        )
    if "connections" in result:
        result["connections"] = _unique(
            result["connections"], lambda row: _text(row.get("net"), "connection net"), "connection"
        )
        for row in result["connections"]:
            pins = row.get("pins")
            if not isinstance(pins, list) or len(pins) != len(set(pins)):
                raise ValueError("Native connection pin membership is incomplete or duplicated")
            row["pins"] = sorted(pins)
    pcb = result.get("pcb")
    if isinstance(pcb, dict):
        if "components" in pcb:
            pcb["components"] = _components(pcb["components"])
        if "nets" in pcb:
            pcb["nets"] = _unique(
                pcb["nets"], lambda row: _text(row.get("name"), "net name"), "pcb net"
            )
        # These basic projections have no native UID/shape. Preserve the entire
        # multiset and duplicates; the separate strict observer protects raw Cu.
        for name in ("tracks", "vias", "footprints"):
            if name in pcb:
                pcb[name] = sorted(pcb[name], key=_key)
    return result
