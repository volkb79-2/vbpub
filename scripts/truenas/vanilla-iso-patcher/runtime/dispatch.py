#!/usr/bin/python3
"""Keep the vanilla installer service unless GRUB selected auto-install."""

from __future__ import annotations

import os
from pathlib import Path
import runpy


AUTOINSTALL_FLAG = "truenas.autoinstall=1"
RUNTIME_DIR = Path("/usr/local/lib/truenas-vanilla-iso")


def _autoinstall_selected() -> bool:
    try:
        command_line = Path("/proc/cmdline").read_text(encoding="ascii").split()
    except OSError:
        return False
    return AUTOINSTALL_FLAG in command_line


def main() -> None:
    if _autoinstall_selected():
        runpy.run_path(str(RUNTIME_DIR / "autoinstall.py"), run_name="__main__")
        return
    os.execv(
        "/usr/bin/python3",
        ["/usr/bin/python3", "-m", "truenas_installer", "--server"],
    )


if __name__ == "__main__":
    main()
