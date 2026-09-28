"""Direct foreground entrypoint for the supervised Nyxloom daemon."""

from __future__ import annotations


def main() -> int:
    from . import config
    from .daemon import Daemon

    Daemon(config.load_registry()).run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
