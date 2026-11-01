"""Native profile copies must be previewed and cannot shadow a bound definition."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from xpedition_cli import board_view as V
from xpedition_cli import native_com_adapter as A


def prepare(tmp_path):
    config = tmp_path / "Config"
    config.mkdir()
    profile = config / "profile"
    profile.mkdir()
    (config / "All On.dcs").write_text("synthetic source")
    return config, profile


def native_control(config, profile, *, change_other=False):
    class Control:
        Name = "Loc: Top View"

        def LoadScheme(self, name):
            return True

        def SaveScheme(self, name):
            (config / "Top View.dcs").write_text("native registered scheme")
            (profile / "Top View.dcs").write_text("native registered scheme")
            if change_other:
                (config / "All On.dcs").write_text("unexpected source mutation")
            return True

    return NS(ActiveView=NS(DisplayControl=Control()))


def test_existing_profile_scheme_is_not_overwritten_by_new_base_scheme(tmp_path):
    config, profile = prepare(tmp_path)
    shadow = profile / "Top View.dcs"
    shadow.write_text("existing user scheme")
    with pytest.raises(A.AdapterError) as error:
        V.scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    assert error.value.code == "E_CONFLICT"
    assert shadow.read_text() == "existing user scheme"
    assert not (config / "Top View.dcs").exists()


def test_native_profile_copy_is_bound_and_drift_refused(tmp_path):
    config, profile = prepare(tmp_path)
    plan = V.scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    assert str(profile / "Top View.dcs") in plan["potential_native_files"]
    V.ensure_scheme(plan, A)
    assert V.register_new_scheme(plan, native_control(config, profile), A)
    record = json.loads(Path(plan["provenance"]["path"]).read_text())
    assert {f["relative_path"] for f in record["native_files"]} == {
        "Top View.dcs",
        "profile/Top View.dcs",
    }
    existing = V.scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    assert existing["definition_verified"] is True
    (profile / "Top View.dcs").write_text("wrong selected user copy")
    with pytest.raises(A.AdapterError):
        V.ensure_scheme(existing, A)


def test_native_registration_cannot_change_other_schemes(tmp_path):
    config, profile = prepare(tmp_path)
    plan = V.scheme_plan(tmp_path / "a.pcb", 4, {"top_view": True}, A)
    V.ensure_scheme(plan, A)
    with pytest.raises(A.AdapterError) as error:
        V.register_new_scheme(plan, native_control(config, profile, change_other=True), A)
    assert error.value.code == "E_VERIFY_INCOMPLETE"
    assert not Path(plan["provenance"]["path"]).exists()


def test_named_scheme_with_different_profile_definition_is_ambiguous(tmp_path):
    config, profile = prepare(tmp_path)
    (profile / "All On.dcs").write_text("different profile content")
    with pytest.raises(A.AdapterError) as error:
        V.scheme_plan(tmp_path / "a.pcb", 4, {"scheme": "Loc: All On"}, A)
    assert error.value.code == "E_CONFLICT"
