"""``cgpd``: the daemon console script (package 1 stub).

Package 3 replaces this with the cli-extended daemon entry (config file,
SIGHUP reload, unit renderer). Until then the stub keeps the wheel's script
real: ``--version`` is answered here and ``run`` is the 1.x ``serve`` path,
unchanged, so the daemon's behaviour does not move in this package.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence

from ..version import installed_version

_USAGE = (
    "usage: cgpd [--version] [-h] run [serve options]\n"
    "interim daemon entry (cgprofile package 1): `run` is the 1.x `serve` "
    "verb with its flags and environment, unchanged.\n"
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        print(f"cgpd {installed_version()}")
        return 0
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(_USAGE)
        return 0
    if args[0] != "run":
        sys.stderr.write(f"cgpd: unknown verb {args[0]!r}; {_USAGE}")
        return 2
    from . import legacy

    return legacy.main(["serve", *args[1:]])
