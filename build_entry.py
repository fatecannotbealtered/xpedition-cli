"""PyInstaller entry point that preserves the package import context."""

from xpedition_cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
