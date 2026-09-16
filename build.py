"""Build the self-contained Python binary used by the release workflow."""

import os
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


if __name__ == "__main__":
    main()
