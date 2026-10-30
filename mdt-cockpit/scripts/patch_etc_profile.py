#!/usr/bin/env python3
"""Make Debian's /etc/profile keep an inherited PATH instead of replacing it.

Stock Debian /etc/profile assigns PATH unconditionally:

    if [ "$(id -u)" -eq 0 ]; then
      PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    else
      PATH="/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games"
    fi
    export PATH

so every login shell throws away entries added at runtime (a consumer containerEnv.PATH, Dev
Containers features, the userEnvProbe login shell). Each assignment becomes
`PATH="${PATH:-<same default>}"`, the approach the Dev Containers common-utils feature uses; a
cleared environment still gets the Debian default.

Only those two lines are changed, and only if the file matches the stock block EXACTLY - any
other content (another distribution, a changed base image, an already patched file) is an
error, never patched blindly.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DEFAULT = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
USER_DEFAULT = "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games"

STOCK = (
    'if [ "$(id -u)" -eq 0 ]; then\n'
    f'  PATH="{ROOT_DEFAULT}"\n'
    "else\n"
    f'  PATH="{USER_DEFAULT}"\n'
    "fi\n"
    "export PATH\n"
)
PATCHED = (
    'if [ "$(id -u)" -eq 0 ]; then\n'
    f'  PATH="${{PATH:-{ROOT_DEFAULT}}}"\n'
    "else\n"
    f'  PATH="${{PATH:-{USER_DEFAULT}}}"\n'
    "fi\n"
    "export PATH\n"
)


class UnexpectedProfile(RuntimeError):
    pass


def patch(text: str) -> str:
    if text.count(STOCK) != 1:
        raise UnexpectedProfile(
            "/etc/profile does not contain the stock Debian PATH block exactly once; refusing to "
            "patch a file I do not recognise (expected:\n" + STOCK + ")"
        )
    patched = text.replace(STOCK, PATCHED, 1)
    assert patched.count(PATCHED) == 1 and ROOT_DEFAULT in patched and USER_DEFAULT in patched
    return patched


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: patch_etc_profile.py /etc/profile", file=sys.stderr)
        return 2
    path = Path(argv[1])
    try:
        patched = patch(path.read_text(encoding="utf-8"))
    except (UnexpectedProfile, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    path.write_text(patched, encoding="utf-8")
    print(f"[INFO] {path}: PATH assignments now keep an inherited PATH", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
