from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

import cli_extended.config as config
from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    VerbSpec,
    export_cli_surface,
)
from cli_extended.config import (
    CliConfig,
    ConfigError,
    load_cli,
    load_factory,
    load_project_config,
)

STANDALONE = """\
schema_version = 1

[[clis]]
id = "monitor-task"
factory = "monitor-task.py:build_cli"
review = "cli-review.toml"
manifest = "cli-surface.json"
spec = "CLI-SPEC.md"
findings = "findings.toml"
"""


def _standalone(directory: Path, text: str = STANDALONE) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "cli-extended.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _pyproject(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pyproject.toml"
    path.write_text(body, encoding="utf-8")
    return path


PYPROJECT_TABLE = """\
[tool.cli-extended]
schema_version = 1
[[tool.cli-extended.clis]]
id = "one"
factory = "pkg.cli:build"
"""


def test_o1_standalone_file_loads_with_absolute_paths(tmp_path):
    path = _standalone(tmp_path)
    loaded = load_project_config(path)
    assert loaded.path == path.resolve()
    assert loaded.root == tmp_path.resolve()
    (cli,) = loaded.clis
    assert cli == CliConfig(
        id="monitor-task",
        factory=f"{tmp_path.resolve()}/monitor-task.py:build_cli",
        root=tmp_path.resolve(),
        review=tmp_path.resolve() / "cli-review.toml",
        manifest=tmp_path.resolve() / "cli-surface.json",
        spec=tmp_path.resolve() / "CLI-SPEC.md",
        findings=tmp_path.resolve() / "findings.toml",
    )


def test_o1_module_factory_and_optional_keys_stay_as_given(tmp_path):
    path = _pyproject(tmp_path, PYPROJECT_TABLE)
    (cli,) = load_project_config(path).clis
    assert cli.factory == "pkg.cli:build"
    assert (cli.review, cli.manifest, cli.spec, cli.findings) == (None,) * 4


def test_o1_absolute_paths_are_kept(tmp_path):
    absolute = tmp_path / "elsewhere" / "review.toml"
    path = _standalone(
        tmp_path,
        f'schema_version = 1\n[[clis]]\nid = "a"\nfactory = "{absolute.parent}/f.py:b"\n'
        f'review = "{absolute}"\n',
    )
    (cli,) = load_project_config(path).clis
    assert cli.review == absolute
    assert cli.factory == f"{absolute.parent}/f.py:b"


def test_o1_discovery_walks_up_from_nested_directories(tmp_path):
    path = _standalone(tmp_path / "project")
    nested = tmp_path / "project" / "a" / "b"
    nested.mkdir(parents=True)
    assert load_project_config(start=nested).path == path.resolve()


def test_o1_discovery_uses_cwd_by_default(tmp_path, monkeypatch):
    path = _standalone(tmp_path)
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path / "sub")
    assert load_project_config().path == path.resolve()


def test_o1_discovery_reads_the_pyproject_table_and_skips_pyprojects_without_it(tmp_path):
    inner = tmp_path / "inner"
    _pyproject(inner, '[tool.other]\nx = 1\n')
    _pyproject(tmp_path / "plain", "[project]\nname = 'x'\n")
    outer = _pyproject(tmp_path, PYPROJECT_TABLE)
    assert load_project_config(start=inner).path == outer.resolve()
    assert load_project_config(start=tmp_path / "plain").path == outer.resolve()


def test_o1_directory_with_both_files_is_ambiguous(tmp_path):
    _standalone(tmp_path)
    _pyproject(tmp_path, PYPROJECT_TABLE)
    with pytest.raises(ConfigError, match="ambiguous configuration in .*keep only one"):
        load_project_config(start=tmp_path)


def test_o1_pyproject_without_table_does_not_make_a_directory_ambiguous(tmp_path):
    _standalone(tmp_path)
    _pyproject(tmp_path, "[project]\nname = 'x'\n")
    assert load_project_config(start=tmp_path).path.name == "cli-extended.toml"


def test_o1_nothing_found_lists_the_searched_directories(tmp_path):
    start = tmp_path / "a" / "b"
    start.mkdir(parents=True)
    with pytest.raises(ConfigError) as caught:
        load_project_config(start=start)
    message = str(caught.value)
    assert message.startswith("no cli-extended.toml or pyproject.toml with [tool.cli-extended] found; searched: ")
    assert str(start.resolve()) in message
    assert str(start.resolve().parent) in message
    assert str(tmp_path.resolve()) in message


def test_o1_explicit_pyproject_without_table_is_an_error(tmp_path):
    path = _pyproject(tmp_path, "[project]\nname = 'x'\n")
    with pytest.raises(ConfigError, match="has no .tool.cli-extended. table"):
        load_project_config(path)


def test_o1_explicit_pyproject_with_non_table_tool_is_an_error(tmp_path):
    path = _pyproject(tmp_path, 'tool = "x"\n')
    with pytest.raises(ConfigError, match="has no .tool.cli-extended. table"):
        load_project_config(path)


def test_o1_explicit_missing_and_invalid_files_are_config_errors(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_project_config(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("schema_version = [", encoding="utf-8")
    with pytest.raises(ConfigError, match="is not valid TOML"):
        load_project_config(bad)


@pytest.mark.parametrize(
    "text, message",
    (
        ('schema_version = 1\nextra = 1\n[[clis]]\nid="a"\nfactory="m:f"\n', "unknown key 'extra' in the configuration table"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory="m:f"\nbogus = 1\n', "unknown key 'bogus' in clis\\[0\\]"),
        ('schema_version = 2\n[[clis]]\nid="a"\nfactory="m:f"\n', "schema_version .* must be the integer 1, got 2"),
        ('schema_version = true\n[[clis]]\nid="a"\nfactory="m:f"\n', "got True"),
        ('[[clis]]\nid="a"\nfactory="m:f"\n', "got None"),
        ("schema_version = 1\n", "at least one"),
        ("schema_version = 1\nclis = []\n", "at least one"),
        ("schema_version = 1\nclis = 3\n", "at least one"),
        ("schema_version = 1\nclis = [1]\n", "clis\\[0\\] .* must be a table"),
        ('schema_version = 1\n[[clis]]\nfactory="m:f"\n', "missing required key 'id'"),
        ('schema_version = 1\n[[clis]]\nid="a"\n', "missing required key 'factory'"),
        ('schema_version = 1\n[[clis]]\nid=""\nfactory="m:f"\n', "clis\\[0\\].id .* non-empty string"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory="m:f"\nreview=3\n', "clis\\[0\\].review .* non-empty string"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory="m:f"\nmanifest="m.json"\n', "manifest and spec together"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory="m:f"\nspec="s.md"\n', "manifest and spec together"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory="m:f"\n[[clis]]\nid="a"\nfactory="m:f"\n', "duplicate CLI id 'a'"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory="nocolon"\n', "factory in .* must use the form"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory=":f"\n', "factory in .* must use the form"),
        ('schema_version = 1\n[[clis]]\nid="a"\nfactory="m:"\n', "factory in .* must use the form"),
    ),
)
def test_o1_invalid_configurations_are_refused(tmp_path, text, message):
    path = _standalone(tmp_path, text)
    with pytest.raises(ConfigError, match=message) as caught:
        load_project_config(path)
    assert str(path.resolve()) in str(caught.value)


def test_o1_unknown_key_in_the_pyproject_table_is_refused(tmp_path):
    path = _pyproject(tmp_path, PYPROJECT_TABLE + 'surprise = 1\n')
    with pytest.raises(ConfigError, match="unknown key 'surprise'"):
        load_project_config(path)


def test_o1_select_cases(tmp_path):
    single = load_project_config(_standalone(tmp_path / "one"))
    assert single.select(None) is single.clis[0]
    assert single.select("monitor-task") is single.clis[0]
    with pytest.raises(ConfigError, match=r"unknown CLI 'nope'.*configured: monitor-task"):
        single.select("nope")
    many = load_project_config(
        _standalone(
            tmp_path / "many",
            'schema_version = 1\n[[clis]]\nid="a"\nfactory="m:f"\n[[clis]]\nid="b"\nfactory="m:g"\n',
        )
    )
    assert many.select("b").id == "b"
    with pytest.raises(ConfigError, match=r"several CLIs \(a, b\); pass --cli ID"):
        many.select(None)


def _build(command="audit-tool"):
    registry = CliRegistry(
        CliIdentity("AUDIT", "1.0", "Audit Tool", command=command),
        prog=command,
        description="Audit resources.",
    )
    registry.register(VerbSpec("inspect", description="inspect", handler=lambda *_: 0))
    return registry.build()


def test_o2_load_cli_checks_the_executable_name_against_the_id(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load_factory", lambda spec, *, root=None: _build())
    good = CliConfig(id="audit-tool", factory="m:f", root=tmp_path)
    assert load_cli(good).identity.command_name == "audit-tool"
    with pytest.raises(ConfigError, match="'other-tool' does not match the registered executable 'audit-tool'"):
        load_cli(CliConfig(id="other-tool", factory="m:f", root=tmp_path))


def test_o2_load_cli_wraps_factory_shape_errors(tmp_path, monkeypatch):
    def broken(spec, *, root=None):
        raise TypeError("did not return a RegisteredCli")

    monkeypatch.setattr(config, "load_factory", broken)
    with pytest.raises(ConfigError, match="cannot load factory 'm:f' for 'x': did not return"):
        load_cli(CliConfig(id="x", factory="m:f", root=tmp_path))


def test_o2_file_factory_resolves_against_root_not_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "_cli_extended_surface_rooted_factory", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / "rooted-factory.py").write_text(
        "from cli_extended import CliIdentity, CliRegistry, VerbSpec\n"
        "def build():\n"
        "    registry = CliRegistry(CliIdentity('A', '1.0', 'A', command='audit-tool'),\n"
        "        prog='audit-tool', description='d')\n"
        "    registry.register(VerbSpec('inspect', description='i', handler=lambda *_: 0))\n"
        "    return registry.build()\n",
        encoding="utf-8",
    )
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    path = _standalone(
        project,
        'schema_version = 1\n[[clis]]\nid = "audit-tool"\nfactory = "rooted-factory.py:build"\n',
    )
    (cli,) = load_project_config(path).clis
    assert load_cli(cli).identity.command_name == "audit-tool"
    # A relative file target given directly is joined to the root as well.
    assert load_factory("rooted-factory.py:build", root=project).identity.command_name == "audit-tool"
    with pytest.raises(FileNotFoundError):
        load_factory("rooted-factory.py:build")


# The factory loader moved here from surface_cli; its behaviour is unchanged.


def test_factory_loader_validates_and_calls_imported_target(monkeypatch):
    app = _build()
    module = types.SimpleNamespace(build_cli=lambda: app, value=1)
    monkeypatch.setattr(config.importlib, "import_module", lambda _name: module)

    assert load_factory("consumer.cli:build_cli") is app
    with pytest.raises(ValueError, match="module:callable"):
        load_factory("not-a-factory")
    with pytest.raises(TypeError, match="not callable"):
        load_factory("consumer.cli:value")
    with pytest.raises(TypeError, match="did not return"):
        module.build_cli = lambda: object()
        load_factory("consumer.cli:build_cli")
    module.build_cli = lambda: app
    monkeypatch.setattr(
        config.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("missing module")),
    )
    with pytest.raises(ImportError, match="missing module"):
        load_factory("consumer.cli:build_cli")


def test_factory_loader_supports_hyphenated_script_and_sibling_imports(tmp_path, monkeypatch):
    module_name = "_cli_extended_surface_monitor_task"
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    script_dir = tmp_path / "consumer"
    script_dir.mkdir()
    (script_dir / "consumer_support.py").write_text(
        "VALUE = 'loaded from sibling'\n", encoding="utf-8"
    )
    script = script_dir / "monitor-task.py"
    script.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from consumer_support import VALUE\n"
        "from cli_extended import ArgumentSpec, CliIdentity, CliRegistry, VerbSpec\n"
        "@dataclass\n"
        "class FactoryState:\n"
        "    value: str = VALUE\n"
        "def parse_target(value):\n"
        "    return value\n"
        "def build_cli():\n"
        "    assert FactoryState().value == 'loaded from sibling'\n"
        "    registry = CliRegistry(\n"
        "        CliIdentity('AUDIT', '1.0', 'Audit Tool', command='audit-tool'),\n"
        "        prog='audit-tool', description='Audit resources.')\n"
        "    registry.register(VerbSpec(\n"
        "        'inspect', description='inspect resources', group='READ',\n"
        "        handler=lambda _args: None,\n"
        "        arguments=(ArgumentSpec('target', 'target', parser_kwargs={'type': parse_target}),)))\n"
        "    return registry.build()\n",
        encoding="utf-8",
    )

    app = load_factory(f"{script}:build_cli")
    surface = export_cli_surface(app)
    target = next(
        action
        for action in surface["routes"][0]["actions"]
        if action["kind"] == "argument"
    )

    assert app.identity.command_name == "audit-tool"
    assert "inspect" in app.command_parsers
    assert target["type"] == {
        "callable": "_cli_extended_surface_monitor_task.parse_target"
    }

    # A second load finds the script directory already on sys.path; it still
    # rebuilds the same registry under the same manifest-visible module name.
    again = load_factory(f"{script}:build_cli")
    assert again.identity.command_name == app.identity.command_name

    loaded_module = sys.modules[module_name]
    script.write_text("raise RuntimeError('factory import failed')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="factory import failed"):
        load_factory(f"{script}:build_cli")
    assert sys.modules[module_name] is loaded_module

    broken_script = script_dir / "broken-cli.py"
    broken_script.write_text("raise RuntimeError('broken factory')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="broken factory"):
        load_factory(f"{broken_script}:build_cli")
    assert "_cli_extended_surface_broken_cli" not in sys.modules


def test_factory_loader_rejects_a_directory_path(tmp_path):
    with pytest.raises(ValueError, match="is not a file"):
        load_factory(f"{tmp_path}:build_cli")


def test_factory_loader_preserves_missing_path_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_factory(f"{tmp_path / 'missing-factory.py'}:build_cli")


@pytest.mark.parametrize(
    "invalid_spec",
    (None, pytest.param(types.SimpleNamespace(loader=None), id="no-loader")),
)
def test_factory_loader_rejects_unloadable_script_specs(tmp_path, monkeypatch, invalid_spec):
    script = tmp_path / "factory.py"
    script.write_text("def build_cli(): pass\n", encoding="utf-8")
    monkeypatch.setattr(
        config.importlib.util, "spec_from_file_location", lambda *_args: invalid_spec
    )

    with pytest.raises(ImportError, match="cannot load factory module"):
        load_factory(f"{script}:build_cli")


@pytest.mark.parametrize("specification", ("module", ":build_cli", "module:"))
def test_factory_loader_rejects_each_incomplete_factory_component(specification):
    with pytest.raises(ValueError, match="module:callable"):
        load_factory(specification)
