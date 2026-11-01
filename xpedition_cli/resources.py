"""Files the CLI reads at run time that live beside the package, not in it.

`reference` reads `contract/contract.json`, which the spec sync keeps at the
repository root, and `changelog` reads `CHANGELOG.md`. A checkout or an editable
install finds them there; a wheel carries copies in `xpedition_cli/_bundled/`
(`setup.py` puts them there); the PyInstaller binary carries them in its bundle.
Never the working directory: a file there would be another project's.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def locate(relative: str) -> Path | None:
    """The file at `relative` (from the repository root) for this installation."""
    candidates = [
        PACKAGE.parent / relative,  # a checkout or an editable install
        PACKAGE / "_bundled" / Path(relative).name,  # a wheel
    ]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:  # the PyInstaller binary; unset, it would mean the working directory
        candidates.append(Path(bundle) / relative)
    return next((path for path in candidates if path.is_file()), None)
