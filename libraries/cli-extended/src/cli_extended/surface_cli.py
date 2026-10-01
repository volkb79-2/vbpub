"""Command-line entrypoint for regenerating and checking consumer CLI specs.

Run with ``python -m cli_extended.surface_cli``. The factory imports a
consumer's normal registry builder; the consumer does not need to write a
manifest or Markdown generator of its own.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Sequence

from .parser import RegisteredCli
from .review import (
    ReviewCatalogError,
    SurfaceSpecError,
    check_cli_surface,
    load_cli_review_catalog,
    render_cli_review_template,
    sync_cli_surface,
)
from .surface import SurfaceError, export_cli_surface


def _load_factory(specification: str):
    target, separator, attribute_name = specification.partition(":")
    if not separator or not target or not attribute_name:
        raise ValueError(
            "factory must use the form 'python.module:callable' or 'path/to/file.py:callable'"
        )
    if target.endswith(".py") or "/" in target or "\\" in target:
        path = Path(target).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"factory path {str(path)!r} is not a file")
        stable_stem = "".join(
            character if character.isalnum() or character == "_" else "_"
            for character in path.stem
        )
        module_name = f"_cli_extended_surface_{stable_stem}"
        module_spec = importlib.util.spec_from_file_location(module_name, path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"cannot load factory module from {str(path)!r}")
        module = importlib.util.module_from_spec(module_spec)
        # Match `python path/to/script.py` for sibling imports. Keep the script
        # directory on sys.path because a registered handler may import a
        # sibling lazily when the CLI is eventually run.
        script_directory = str(path.parent)
        if script_directory in sys.path:
            sys.path.remove(script_directory)
        sys.path.insert(0, script_directory)
        missing = object()
        previous_module = sys.modules.get(module_name, missing)
        sys.modules[module_name] = module
        try:
            module_spec.loader.exec_module(module)
        except BaseException:
            if previous_module is missing:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module
            raise
    else:
        module = importlib.import_module(target)
    factory = getattr(module, attribute_name)
    if not callable(factory):
        raise TypeError(f"factory target {specification!r} is not callable")
    app = factory()
    if not isinstance(app, RegisteredCli):
        raise TypeError(
            f"factory {specification!r} did not return a RegisteredCli"
        )
    return app


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

    parser = _argument_parser()
    args = parser.parse_args(argv)
    if args.action != "template" and (args.manifest is None or args.spec is None):
        parser.error("sync and check require both --manifest and --spec")
    try:
        app = _load_factory(args.factory)
        if args.action == "template":
            catalog = load_cli_review_catalog(args.review)
            if catalog.cli_id != app.identity.command_name:
                raise ReviewCatalogError(
                    f"review catalog cli_id {catalog.cli_id!r} does not match "
                    f"registered executable {app.identity.command_name!r}"
                )
            limit = (
                catalog.max_candidates
                if args.max_candidates is None
                else args.max_candidates
            )
            surface = export_cli_surface(
                app,
                interaction_groups=catalog.interaction_groups,
                max_candidates=limit,
            )
            template = render_cli_review_template(surface, catalog)
            if template:
                sys.stdout.write(template)
            else:
                print("No semantic review rows need adding or updating.")
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
