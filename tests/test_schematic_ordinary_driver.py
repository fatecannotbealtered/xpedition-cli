"""Independent Driver readback; synthetic complete getter data, never CAD."""

from __future__ import annotations

import copy

import pytest
from test_schematic_edit_durability_cli import edit_scene, no_native  # noqa: F401

from xpedition_cli import driver


def _observations(case):
    host = "dedicated_startup_host_Application_and_authenticated_ncalrpc"
    document = case.written["lifecycle"]["before_identity"]
    app = {
        "pid": 4242,
        "document": copy.deepcopy(document),
        "dedicated_startup_binding": {
            "source": host,
            "canonical_stable": True,
            "original_handle_rechecked": True,
            "connection_epoch_sha256": "a" * 64,
        },
        "pid_source": {
            "matching_process": {
                "alive": True,
                "pid": 4242,
                "birth_filetime_100ns": "134000000000000001",
                "executable": "C:/fixture/viewdraw.exe",
            }
        },
    }
    return tuple(
        {
            "id": label,
            "extensions": {
                "facts": {"native": copy.deepcopy(native), "application": copy.deepcopy(app)}
            },
        }
        for label, native in (("before", case.before), ("after", case.fresh))
    )


def _verify(case, before, after, written=None):
    return driver._schematic_ordinary_persistence_readback(
        case.written if written is None else written,
        {"project": str(case.project)},
        before,
        after,
        [case.operation],
        save_metadata=None,
    )


def test_driver_uses_its_own_getters_and_records_not_handler_flags(edit_scene):  # noqa: F811
    before, after = _observations(edit_scene)
    result = _verify(edit_scene, before, after)
    assert edit_scene.written["persistence_verified"] is False
    assert result["valid"] is True and result["persistence_verified"] is True
    assert result["host_object_binding_verified"] is True
    assert result["snapshot_before_id"] == "before"
    assert result["snapshot_after_id"] == "after"


@pytest.mark.parametrize(
    "bad", ["birth", "epoch", "host_missing", "pid", "document", "non_target", "save_only"]
)
def test_driver_rejects_changed_instance_or_incomplete_persistence(edit_scene, bad):  # noqa: F811
    before, after = _observations(edit_scene)
    app = after["extensions"]["facts"]["application"]
    written = copy.deepcopy(edit_scene.written)
    if bad == "birth":
        app["pid_source"]["matching_process"]["birth_filetime_100ns"] = "134000000000000002"
    elif bad == "epoch":
        app["dedicated_startup_binding"]["connection_epoch_sha256"] = "b" * 64
    elif bad == "host_missing":
        app.pop("dedicated_startup_binding")
    elif bad == "pid":
        app["pid"] = 5000
    elif bad == "document":
        app["document"]["path"] = str(edit_scene.project.parent / "Other.prj")
    elif bad == "non_target":
        native = after["extensions"]["facts"]["native"]
        next(part for part in native["components"] if part["refdes"] == "C1")["x"] += 10
    elif bad == "save_only":
        written = {"saved": True, "persistence_verified": True, "verification": {"valid": True}}
    result = _verify(edit_scene, before, after, written)
    assert result["valid"] is False and result["persistence_verified"] is False
