"""Installed command entrypoint for AI-harness session tools."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    from .cli_registry import harness_cli

    return harness_cli().run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
