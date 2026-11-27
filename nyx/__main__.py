"""Allow ``python -m nyx`` to launch the daemon."""

from nyx.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
