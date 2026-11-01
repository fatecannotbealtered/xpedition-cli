"""Accept a schematic window PNG without claiming schematic engineering truth."""

from __future__ import annotations

from pathlib import Path

from .preview_artifacts import verify as verify_png


def expectation(options, application):
    return {
        "project": str(Path(str(options["project"])).expanduser().resolve()),
        "sheet": int(options.get("sheet") or 1),
        "output": str(Path(str(options["output"])).expanduser().resolve()),
        "pid": application.get("pid"),
        "executable": application.get("public_fields", {}).get("FullName"),
    }


def verify(options, result, expected, before, after, *, same_root):
    """Use fresh application identity and OS ownership as a separate capture witness."""
    checks = []
    incomplete = False

    def check(name, passed, *, missing=False):
        nonlocal incomplete
        checks.append(
            {"id": name, "passed": bool(passed), **({"missing": True} if missing else {})}
        )
        incomplete = incomplete or missing

    expected = expected if isinstance(expected, dict) else {}
    result = result if isinstance(result, dict) else {}
    capture = result.get("screenshot") or {}
    context = result.get("capture_context") or {}
    current = after.get("application") or {}
    output = str(Path(str(options["output"])).expanduser().resolve())
    project = str(Path(str(options["project"])).expanduser().resolve())

    def equal_path(actual, wanted):
        try:
            return (
                isinstance(actual, str)
                and bool(actual)
                and Path(actual).resolve() == Path(wanted).resolve()
            )
        except (OSError, TypeError, ValueError):
            return False

    check("frozen-output", expected.get("output") == output, missing=not expected)
    check("frozen-project", equal_path(expected.get("project"), project), missing=not expected)
    check(
        "frozen-sheet",
        expected.get("sheet") == int(options.get("sheet") or 1),
        missing=not expected,
    )
    document = current.get("document") or {}
    check(
        "independent-open-project",
        document.get("open") is True
        and equal_path(document.get("path"), project)
        and equal_path(current.get("active_project"), project),
        missing=not (document.get("path") and current.get("active_project")),
    )
    pid = expected.get("pid")
    check(
        "independent-process",
        type(pid) is int and pid > 0 and current.get("pid") == pid,
        missing=not (pid and current.get("pid")),
    )
    executable = expected.get("executable")
    fresh_executable = current.get("public_fields", {}).get("FullName")
    check(
        "independent-executable",
        bool(executable) and equal_path(fresh_executable, executable),
        missing=not (executable and fresh_executable),
    )
    check(
        "capture-project",
        equal_path(context.get("project"), project),
        missing=not context.get("project"),
    )
    check(
        "capture-sheet",
        context.get("sheet") == result.get("sheet") == expected.get("sheet"),
        missing=context.get("sheet") is None,
    )
    check(
        "capture-process",
        context.get("pid") == capture.get("window_pid") == pid,
        missing=not (context.get("pid") and capture.get("window_pid")),
    )
    check(
        "capture-executable",
        bool(executable) and equal_path(context.get("executable"), executable),
        missing=not context.get("executable"),
    )
    window = capture.get("window_hwnd")
    active = current.get("pid_source", {}).get("hwnd")
    active_view = current.get("public_fields", {}).get("ActiveViewHandle")
    check(
        "capture-application-hwnd",
        context.get("application_hwnd") == active and type(active) is int and active > 0,
        missing=not (active and context.get("application_hwnd")),
    )
    check(
        "capture-active-view-hwnd",
        context.get("active_view_hwnd") == active_view
        and type(active_view) is int
        and active_view > 0,
        missing=not (active_view and context.get("active_view_hwnd")),
    )
    check(
        "capture-window",
        type(window) is int and window > 0 and context.get("window_hwnd") == window,
        missing=not (window and context.get("window_hwnd")),
    )
    check(
        "capture-actual-root",
        context.get("same_root_verified") is True,
        missing="same_root_verified" not in context,
    )
    check(
        "capture-foreground",
        context.get("foreground_verified") is True and result.get("foreground") is True,
        missing="foreground_verified" not in context,
    )
    try:
        related = same_root(active, window) if active and window else None
        check("independent-window-root", related is True, missing=related is None)
    except (OSError, ValueError, TypeError) as error:
        checks.append(
            {
                "id": "independent-window-root",
                "passed": False,
                "missing": True,
                "error": type(error).__name__,
            }
        )
        incomplete = True
    bounds = capture.get("bounds_px")
    valid_bounds = (
        isinstance(bounds, (list, tuple))
        and len(bounds) == 4
        and all(type(n) is int for n in bounds)
    )
    check(
        "physical-capture-bounds",
        valid_bounds
        and capture.get("bounds_verified") is True
        and capture.get("coordinate_space") == "physical_pixels"
        and bounds[2] - bounds[0] == capture.get("width")
        and bounds[3] - bounds[1] == capture.get("height"),
        missing=bounds is None,
    )
    observed = after.get("artifact_observations", [])
    before_observed = before.get("artifact_observations", [])
    png = verify_png(
        options,
        {"pictures": [capture]},
        {"output": output, "images": [{"path": output}]},
        before_observed,
        observed,
    )
    checks.extend(png["checks"])
    sizes = [item.get("size") for item in observed if equal_path(item.get("path"), output)]
    check("capture-byte-count", type(capture.get("bytes")) is int and sizes == [capture["bytes"]])
    valid = all(item["passed"] for item in checks)
    failed = any(not item["passed"] and not item.get("missing") for item in checks)
    return {
        "valid": valid,
        "status": "passed" if valid else "incomplete" if incomplete and not failed else "failed",
        "checks": checks,
        "images": png["images"],
        "source": (
            "independently reopened PNG bytes, fresh Designer identity and OS window ownership"
        ),
        "scope": "requested schematic sheet capture identity and PNG byte delivery",
        "not_checked": [
            "schematic intent and connectivity",
            "native ERC",
            "font correctness and visual readability",
        ],
    }
