"""PyInstaller entry point that preserves the package import context."""

import re
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--internal-designer-owner"]:
        if (
            len(arguments) != 5
            or arguments[1] != "--root"
            or not arguments[2]
            or arguments[3] != "--epoch"
            or re.fullmatch(r"[a-f0-9]{32}", arguments[4]) is None
        ):
            return 21
        from xpedition_cli.designer_frozen_binding import FrozenBindingError, frozen_owner_binding

        sys.dont_write_bytecode = True
        try:
            frozen_owner_binding()
        except (FrozenBindingError, OSError, ValueError):
            return 21
        from xpedition_cli.designer_native_owner import main as owner_main

        return owner_main(["--owned-child", *arguments[1:]])
    from xpedition_cli.main import main as public_main

    return public_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
