"""Narrow supported native pad records; no general polygon interpretation."""

from . import library_read as R


def supported_pad(record):
    allowed = {"RECTANGLE", "SQUARE", "ROUND", "PAD_OPTIONS", "OFFSET", "TIMESTAMP", "HISTORY"}
    if any(c.keyword not in allowed for c in record.children):
        return False
    shapes = [c for c in record.children if c.keyword in {"RECTANGLE", "SQUARE", "ROUND"}]
    if len(shapes) != 1 or any(
        c.keyword not in {"WIDTH", "HEIGHT", "DIAMETER"} for c in shapes[0].children
    ):
        return False
    offsets = record.all("OFFSET")
    if len(offsets) != 1 or offsets[0].values != [(0.0, 0.0)]:
        return False
    return all(not c.children for c in shapes[0].children)


def supported_stack(record):
    if any(
        c.keyword not in {"PADSTACK_TYPE", "TIMESTAMP", "HISTORY", "TECHNOLOGY"}
        for c in record.children
    ):
        return False
    tech = record.all("TECHNOLOGY")
    if (
        record.value("PADSTACK_TYPE") != "PIN_SMD"
        or len(tech) != 1
        or tech[0].values != ["(Default)"]
    ):
        return False
    allowed = {
        "TOP_PAD",
        "BOTTOM_PAD",
        "TOP_SOLDERMASK_PAD",
        "BOTTOM_SOLDERMASK_PAD",
        "TOP_SOLDERPASTE_PAD",
        "BOTTOM_SOLDERPASTE_PAD",
        "TECHNOLOGY_OPTIONS",
    }
    return (
        all(c.keyword in allowed and not c.children for c in tech[0].children)
        and tech[0].value("TECHNOLOGY_OPTIONS") == "NONE"
    )


def records(text):
    return {
        (r.keyword, str(r.values[0])): r
        for r in R.parse_records(text)
        if r.values and r.keyword in {"PAD", "PADSTACK"}
    }
