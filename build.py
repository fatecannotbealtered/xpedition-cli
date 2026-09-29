"""Build the self-contained Python binary used by the release workflow, then run it."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent


def main() -> None:
    PyInstaller.__main__.run(
        [
            str(ROOT / "build_entry.py"),
            "--name",
            "xpedition-cli",
            "--onefile",
            "--clean",
            "--noconfirm",
            "--paths",
            str(ROOT),
            "--add-data",
            f"{ROOT / 'contract' / 'contract.json'}{os.pathsep}contract",
            "--add-data",
            f"{ROOT / 'CHANGELOG.md'}{os.pathsep}.",
        ]
    )
    smoke(Path("dist") / ("xpedition-cli.exe" if os.name == "nt" else "xpedition-cli"))


def smoke(binary: Path) -> None:
    """Run the frozen binary before anything ships it.

    A module the freezer left out fails here, on the build runner, instead of on a
    user's machine: 1.0.1's binaries could run no command, and the release published
    them. Three commands that need no Xpedition reach the handler modules, the
    bundled contract and changelog, the planner and Pillow.
    """
    sys.path.insert(0, str(ROOT))
    from xpedition_cli import __version__
    from xpedition_cli.cli.registry import commands

    def run(*args: str) -> dict:
        done = subprocess.run(
            [str(binary), *args, "--compact"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
        try:
            payload = json.loads(done.stdout)
        except ValueError:
            raise SystemExit(
                f"smoke: `{' '.join(args)}` printed no JSON: {done.stdout[:400]!r} "
                f"{done.stderr[:400]!r}"
            ) from None
        if not payload.get("ok"):
            raise SystemExit(f"smoke: `{' '.join(args)}` failed: {json.dumps(payload)[:600]}")
        return payload["data"]

    version = run("version")["version"]
    if version != __version__:
        raise SystemExit(f"smoke: the binary says {version}, the package {__version__}")
    listed, declared = len(run("reference")["commands"]), len(commands())
    if listed != declared:
        raise SystemExit(f"smoke: reference lists {listed} commands, the registry {declared}")
    with tempfile.TemporaryDirectory() as folder:
        data = run(
            "schematic",
            "render",
            "--design",
            str(ROOT / "examples" / "demo-sensor-board.json"),
            "--output",
            str(Path(folder) / "preview.png"),
        )
        drawn = [Path(item["path"]) for item in data["pictures"]]
        if not drawn or not all(path.is_file() for path in drawn):
            raise SystemExit(f"smoke: schematic render drew nothing: {data['pictures']}")
    print(f"smoke: {binary} runs version, reference ({listed} commands) and schematic render")


if __name__ == "__main__":
    main()
