"""Installed local host-control and operator command entrypoint."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from .cli_registry import operator_cli

    return operator_cli().run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
