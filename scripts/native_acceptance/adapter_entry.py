"""One-request SDK adapter process; no listener, activation, or external state."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PACK = Path(__file__).resolve().parent
ROOT = PACK.parent.parent
manifest_bytes = (PACK / "manifest.json").read_bytes()
if hashlib.sha256(manifest_bytes).hexdigest() != os.environ.get(
    "XPEDITION_ACCEPTANCE_MANIFEST_SHA256"
):
    raise SystemExit("The finite SDK adapter was not invoked with the sealed acceptance manifest")
manifest = json.loads(manifest_bytes)
source = Path(manifest["checkout"]) / "xpedition_cli/native_com_adapter.py"
if hashlib.sha256(source.read_bytes()).hexdigest() != manifest["adapter_sha256"]:
    raise SystemExit("Candidate adapter hash changed")
scratch = Path(manifest["scratch_root"])
os.environ["XPEDITION_CLI_CONFIG_DIR"] = str(scratch / "state")
os.environ["XPEDITION_MGC_WD"] = str(scratch / "wdir")
os.environ["XPEDITION_REQUIRE_EXISTING"] = "1"
os.environ["XPEDITION_EXPECTED_ADAPTER_BUILD"] = manifest["adapter_sha256"]
sys.path.insert(0, manifest["checkout"])
cache = scratch / "com-cache"
cache.mkdir(parents=True, exist_ok=True)
import win32com  # noqa: E402 -- configure the sealed cache before COM imports

win32com.__gen_path__ = str(cache)
import win32com.gen_py  # noqa: E402

win32com.gen_py.__path__ = [str(cache)]
from xpedition_cli.native_com_adapter import main  # noqa: E402

raise SystemExit(main())
