"""Opt-in pytest plugin that enforces reviewed CLI cases at collection.

Enable it with one line in the root ``conftest.py``::

    pytest_plugins = ["cli_extended.pytest_plugin"]

It is deliberately not a ``pytest11`` entry point: installing the library must
never change how an unrelated project's tests are collected. This module only
imports ``pytest`` inside the hook that needs it, so importing the library
never requires pytest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ConfigError, load_project_config
from .review import ReviewCatalogError, assert_cli_case_tests, load_cli_review_catalog

CASE_MARKER = "cli_case(case_id): links a behavior test to a reviewed CLI case"
CONFIG_INI = "cli_extended_config"
PARTIAL_OPTION = "--cli-case-partial"


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("cli-extended")
    group.addoption(
        PARTIAL_OPTION,
        action="store_true",
        default=False,
        dest="cli_case_partial",
        help="check cli_case markers only for the collected tests (focused runs)",
    )
    parser.addini(
        CONFIG_INI,
        "path of the cli-extended project config (default: discovered upward "
        "from the pytest rootdir)",
    )


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", CASE_MARKER)


def pytest_collection_finish(session: Any) -> None:
    import pytest

    config = session.config
    root = Path(str(config.rootpath))
    configured = config.getini(CONFIG_INI)
    try:
        project = (
            load_project_config(root / configured)
            if configured
            else load_project_config(start=root)
        )
        for cli in project.clis:
            if cli.review is None:
                continue
            assert_cli_case_tests(
                session.items,
                load_cli_review_catalog(cli.review),
                partial=config.getoption("cli_case_partial"),
            )
    except (ConfigError, ReviewCatalogError, AssertionError) as exc:
        raise pytest.UsageError(f"cli-extended: {exc}") from exc
