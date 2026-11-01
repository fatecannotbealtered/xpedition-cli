"""Locale-independent classification of Xpedition COM faults.

The adapter maps COM failures onto canonical codes. Anything that classifies by
searching the COM *description* breaks on a localised installation, so the
licensing fault is matched on its EXCEPINFO numbers instead. These tests pin
that, and pin that an ordinary fault is still not mistaken for a licensing one.
"""

from __future__ import annotations

from xpedition_cli.native_com_adapter import _com_error

# Observed on XPED2604, Chinese UI: the description carries neither "license"
# nor "token", so only the numbers identify the fault.
LICENSE_DESCRIPTION = "自动化代码不包含身份验证所需的许可调用。"


def com_error(hresult: int, text: str, excepinfo: tuple | None, extra: int | None = None):
    """Build an exception shaped like pywin32's com_error."""
    return Exception(hresult, text, excepinfo, extra)


def license_fault() -> Exception:
    return com_error(
        -2147352567,
        "发生意外。",
        (0, "Xpedition Layout", LICENSE_DESCRIPTION, None, 10279, -2147220947),
        None,
    )


def test_missing_licensing_call_is_non_retryable_auth() -> None:
    error = _com_error(license_fault(), "Components")
    assert error.code == "E_AUTH"
    assert error.details["action"] == "Components"
    # The localised description must not leak out as the classification reason.
    assert "license" in error.details["hint"]


def test_type_mismatch_is_not_read_as_a_licensing_fault() -> None:
    # DISP_E_TYPEMISMATCH arrives without EXCEPINFO, as seen from a live session.
    error = _com_error(com_error(-2147352571, "类型不匹配。", None, 5), "DesignComponents")
    assert error.code == "E_SERVER"


def test_other_excepinfo_faults_are_not_read_as_licensing_faults() -> None:
    other = com_error(
        -2147352567,
        "发生意外。",
        (0, "Xpedition Layout", "某个其他错误。", None, 10280, -2147220948),
        None,
    )
    assert _com_error(other, "Components").code == "E_SERVER"


def test_english_license_text_still_maps_to_auth() -> None:
    error = _com_error(Exception("automation license denied"), "Components")
    assert error.code == "E_AUTH"


def test_unregistered_and_launch_faults_keep_their_codes() -> None:
    assert _com_error(Exception("Class not registered"), "x").code == "E_BACKEND_UNAVAILABLE"
    assert _com_error(Exception("(-2146959355, ...)"), "x").code == "E_BACKEND_UNAVAILABLE"


def test_connected_but_unimplemented_interface_does_not_request_activation_retry():
    exc = com_error(
        -2147352567, "发生意外。", (0, "LibraryManager", "未实现。", None, 0, -2147467263)
    )
    error = _com_error(exc, "LibraryServices")
    assert error.code == "E_BACKEND_UNAVAILABLE"
    assert error.details["native"]["scode_hex"] == "0x80004001"
    assert error.details["native"]["source"] == "LibraryManager"


def test_permission_denial_is_not_a_retryable_server_fault():
    error = _com_error(com_error(-2147024891, "拒绝访问。", None), "OpenLibrary")
    assert error.code == "E_FORBIDDEN"
    assert error.details["native"]["hresult_hex"] == "0x80070005"


def test_parts_editor_license_error_retains_its_distinct_native_source():
    exc = com_error(
        -2147352567,
        "发生意外。",
        (0, "PDBEditor", "Cannot acquire an Automation Basic license.", None, 0, -2147220985),
    )
    error = _com_error(exc, "PartsEditor.OpenDatabase")
    assert error.code == "E_AUTH"
    assert error.details["native"]["scode"] == -2147220985
    assert error.details["native"]["source"] == "PDBEditor"
