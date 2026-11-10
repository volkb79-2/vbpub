#!/usr/bin/env python3
"""Development-tree launcher; installed hosts use the `mounttrace` command."""

import sys

from mounttrace_cli.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
