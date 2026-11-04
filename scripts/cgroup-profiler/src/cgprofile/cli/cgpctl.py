"""``cgpctl``: the client console script (package 1 stub).

Package 2 replaces this with the cli-extended client of CGPCTL-SPEC.md. Until
then the stub keeps the wheel's script real: ``--version`` is answered here,
``mark`` and the socket verbs go to the 1.x code path unchanged (``ctl <verb>``
is the 1.x spelling of what ``cgpctl <verb>`` will be).
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from ..version import installed_version

_USAGE = (
    "usage: cgpctl [--version] [-h] <verb> [args]\n"
    "interim client (cgprofile package 1): verbs are the 1.x `ctl` verbs "
    "(version, host, status, start, stop, watch, report, gc) and `mark`.\n"
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        print(f"cgpctl {installed_version()}")
        return 0
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        return 0
    from . import legacy

    if args[0] == "mark":
        return legacy.main(args)
    return legacy.main(["ctl", *args])
