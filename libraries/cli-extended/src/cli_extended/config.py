"""Project configuration for the ``cli-extended`` tooling.

A project declares which CLIs it ships in ``[tool.cli-extended]`` of its
``pyproject.toml`` or, without a pyproject, at the top level of a standalone
``cli-extended.toml``.  This module also owns loading a consumer's registry
factory, shared with the legacy ``surface_cli`` entrypoint.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .parser import RegisteredCli

CONFIG_SCHEMA_VERSION = 1
STANDALONE_NAME = "cli-extended.toml"
PYPROJECT_NAME = "pyproject.toml"
_TOP_KEYS = ("schema_version", "clis")
_CLI_KEYS = ("id", "factory", "review", "manifest", "spec", "findings")


class ConfigError(ValueError):
    """The project configuration is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class CliConfig:
    """One configured CLI; every path is absolute."""

    id: str
    factory: str
    root: Path
    review: Path | None = None
    manifest: Path | None = None
    spec: Path | None = None
    findings: Path | None = None


@dataclass(frozen=True)
class ProjectConfig:
    """The parsed configuration file."""

    path: Path
    clis: tuple[CliConfig, ...]

    @property
    def root(self) -> Path:
        return self.path.parent

    def select(self, cli_id: str | None) -> CliConfig:
        """Return the CLI named ``cli_id``, or the only one when it is None."""

        ids = ", ".join(cli.id for cli in self.clis)
        if cli_id is None:
            if len(self.clis) == 1:
                return self.clis[0]
            raise ConfigError(
                f"{self.path} configures several CLIs ({ids}); pass --cli ID"
            )
        for cli in self.clis:
            if cli.id == cli_id:
                return cli
        raise ConfigError(f"unknown CLI {cli_id!r} in {self.path}; configured: {ids}")


def _reject_unknown(table: Mapping[str, Any], allowed: tuple[str, ...], where: str, path: Path) -> None:
    for key in table:
        if key not in allowed:
            raise ConfigError(f"unknown key {key!r} in {where} of {path}")


def _string(table: Mapping[str, Any], key: str, where: str, path: Path) -> str:
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} in {path} must be a non-empty string")
    return value


def _is_file_target(target: str) -> bool:
    return target.endswith(".py") or "/" in target


def _absolute(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def _factory(value: str, root: Path, where: str, path: Path) -> str:
    target, separator, attribute = value.partition(":")
    if not separator or not target or not attribute:
        raise ConfigError(
            f"{where}.factory in {path} must use the form "
            "'python.module:callable' or 'path/to/file.py:callable'"
        )
    if _is_file_target(target):
        target = str(_absolute(target, root))
    return f"{target}:{attribute}"


def _parse_cli(raw: Any, index: int, root: Path, path: Path) -> CliConfig:
    where = f"clis[{index}]"
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} in {path} must be a table")
    _reject_unknown(raw, _CLI_KEYS, where, path)
    for key in ("id", "factory"):
        if key not in raw:
            raise ConfigError(f"{where} in {path} is missing required key {key!r}")
    paths: dict[str, Path | None] = {}
    for key in ("review", "manifest", "spec", "findings"):
        paths[key] = (
            _absolute(_string(raw, key, where, path), root) if key in raw else None
        )
    if (paths["manifest"] is None) != (paths["spec"] is None):
        raise ConfigError(f"{where} in {path} must set manifest and spec together")
    return CliConfig(
        id=_string(raw, "id", where, path),
        factory=_factory(_string(raw, "factory", where, path), root, where, path),
        root=root,
        **paths,
    )


def _parse(table: Mapping[str, Any], path: Path) -> ProjectConfig:
    _reject_unknown(table, _TOP_KEYS, "the configuration table", path)
    version = table.get("schema_version")
    if type(version) is not int or version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"schema_version in {path} must be the integer {CONFIG_SCHEMA_VERSION}, "
            f"got {version!r}"
        )
    raw_clis = table.get("clis")
    if not isinstance(raw_clis, list) or not raw_clis:
        raise ConfigError(f"{path} must define at least one [[clis]] entry")
    clis = tuple(
        _parse_cli(raw, index, path.parent, path) for index, raw in enumerate(raw_clis)
    )
    seen: set[str] = set()
    for cli in clis:
        if cli.id in seen:
            raise ConfigError(f"duplicate CLI id {cli.id!r} in {path}")
        seen.add(cli.id)
    return ProjectConfig(path=path, clis=clis)


def _read_toml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def _pyproject_table(path: Path) -> Mapping[str, Any] | None:
    tool = _read_toml(path).get("tool")
    table = tool.get("cli-extended") if isinstance(tool, Mapping) else None
    return table if isinstance(table, Mapping) else None


def _load_file(path: Path) -> ProjectConfig:
    path = path.resolve()
    if path.name == PYPROJECT_NAME:
        table = _pyproject_table(path)
        if table is None:
            raise ConfigError(f"{path} has no [tool.cli-extended] table")
        return _parse(table, path)
    return _parse(_read_toml(path), path)


def load_project_config(
    path: Path | None = None, *, start: Path | None = None
) -> ProjectConfig:
    """Load the explicit ``path``, or discover the config upward from ``start``."""

    if path is not None:
        return _load_file(Path(path))
    origin = Path.cwd() if start is None else Path(start)
    origin = origin.resolve()
    searched: list[Path] = []
    for directory in (origin, *origin.parents):
        searched.append(directory)
        standalone = directory / STANDALONE_NAME
        pyproject = directory / PYPROJECT_NAME
        has_standalone = standalone.is_file()
        has_pyproject = pyproject.is_file() and _pyproject_table(pyproject) is not None
        if has_standalone and has_pyproject:
            raise ConfigError(
                f"ambiguous configuration in {directory}: both {STANDALONE_NAME} and "
                f"[tool.cli-extended] in {PYPROJECT_NAME}; keep only one"
            )
        if has_standalone:
            return _load_file(standalone)
        if has_pyproject:
            return _load_file(pyproject)
    listing = ", ".join(str(directory) for directory in searched)
    raise ConfigError(
        f"no {STANDALONE_NAME} or {PYPROJECT_NAME} with [tool.cli-extended] found; "
        f"searched: {listing}"
    )


def load_factory(specification: str, *, root: Path | None = None) -> RegisteredCli:
    """Import ``module:callable`` or ``path/file.py:callable`` and build the CLI."""

    target, separator, attribute_name = specification.partition(":")
    if not separator or not target or not attribute_name:
        raise ValueError(
            "factory must use the form 'python.module:callable' or 'path/to/file.py:callable'"
        )
    if _is_file_target(target):
        candidate = Path(target).expanduser()
        if root is not None and not candidate.is_absolute():
            candidate = root / candidate
        path = candidate.resolve(strict=True)
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


def load_cli(cli: CliConfig) -> RegisteredCli:
    """Build the configured CLI and require its executable name to equal its id."""

    try:
        app = load_factory(cli.factory, root=cli.root)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ConfigError(f"cannot load factory {cli.factory!r} for {cli.id!r}: {exc}") from exc
    if app.identity.command_name != cli.id:
        raise ConfigError(
            f"configured CLI id {cli.id!r} does not match the registered "
            f"executable {app.identity.command_name!r}"
        )
    return app
