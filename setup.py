"""Carry the two repository files the CLI reads at run time into the built package.

`reference` reads contract/contract.json (kept at the repository root by the spec
sync) and `changelog` reads CHANGELOG.md. A wheel holds only the package, so without
this a pip install from the repository could describe neither itself nor its
changes; `xpedition_cli.resources` looks for these copies in `_bundled/`. Everything
else about the package is in pyproject.toml.
"""

from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
BUNDLED = (ROOT / "CHANGELOG.md", ROOT / "contract" / "contract.json")


class BuildPy(build_py):
    def run(self) -> None:
        super().run()
        target = Path(self.build_lib) / "xpedition_cli" / "_bundled"
        self.mkpath(str(target))
        for source in BUNDLED:
            self.copy_file(str(source), str(target / source.name))


setup(cmdclass={"build_py": BuildPy})
