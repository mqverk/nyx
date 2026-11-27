#!/usr/bin/env python3
"""Run nyx.

    python run.py                 # use .env defaults
    python run.py --host 0.0.0.0  # override the bind address
    python run.py --port 9000     # override the port
"""

from nyx.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
