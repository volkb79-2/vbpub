"""Deprecated flag-based entrypoint; use ``cli-extended surface ...``.

``python -m cli_extended.surface_cli`` keeps its exact flags and behaviour but
prints a deprecation warning first. The factory imports a consumer's normal
registry builder; the consumer does not need to write a manifest or Markdown
generator of its own.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .cli import template_text
from .config import load_factory
from .review import (
    ReviewCatalogError,
    SurfaceSpecError,
    check_cli_surface,
    sync_cli_surface,
)
from .surface import SurfaceError

DEPRECATION = (
    "[WARN] python -m cli_extended.surface_cli is deprecated; "
    "use 'cli-extended surface ...'"
)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cli_extended.surface_cli",
        description="Regenerate or check a registered CLI's semantic specification.",
    )
    parser.add_argument(
        "--factory",
        required=True,
        help="consumer registry builder as 'python.module:callable' or 'path/to/file.py:callable'",
    )
    parser.add_argument("--review", required=True, type=Path, help="semantic TOML catalog")
    parser.add_argument("--manifest", type=Path, help="generated JSON manifest")
    parser.add_argument("--spec", type=Path, help="canonical product CLI spec")
    parser.add_argument(
        "--max-candidates",
        type=int,
        help="explicit candidate limit override (otherwise read from the TOML catalog)",
    )
    parser.add_argument("action", choices=("sync", "check", "template"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the shared sync/check workflow for one consumer registry."""

    print(DEPRECATION, file=sys.stderr)
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if args.action != "template" and (args.manifest is None or args.spec is None):
        parser.error("sync and check require both --manifest and --spec")
    try:
        app = load_factory(args.factory)
        if args.action == "template":
            sys.stdout.write(template_text(app, args.review, args.max_candidates))
            return 0
        operation = sync_cli_surface if args.action == "sync" else check_cli_surface
        report = operation(
            app,
            review_path=args.review,
            manifest_path=args.manifest,
            spec_path=args.spec,
            max_candidates=args.max_candidates,
        )
    except (
        AttributeError,
        ImportError,
        OSError,
        ReviewCatalogError,
        SurfaceError,
        SurfaceSpecError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"[ERROR] cli-extended surface: {exc}", file=sys.stderr)
        return 2

    for finding in report.findings:
        print(f"[REVIEW] {finding}", file=sys.stderr)
    if args.action == "sync":
        print("CLI surface files synchronized.")
        return 0
    if report.passed:
        print("CLI surface check passed.")
        return 0
    print("CLI surface check failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
