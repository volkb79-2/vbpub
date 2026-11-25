"""agctl's command line and configuration boundary, on temp trees only.

No test here touches the real estate: `$CONTROLLER_HOME`, the runs directories and the state
directories are all built inside `tmp_path`, and no test starts the CLI against a live checkout.
"""

from __future__ import annotations

import pytest
from cli_extended import CliIdentity, VersionLookupError

from agctl import config as ag_config

LONG_NAME = "agent control: controller state and the estate view"


def ident(version: str = "9.9.9") -> CliIdentity:
    return CliIdentity("AGCTL", version, LONG_NAME, "agctl")


# --- config: defaults, environment, agctl.toml ---------------------------------------------


def test_an_unset_environment_resolves_the_documented_defaults(tmp_path):
    resolved = ag_config.load({}, cwd=tmp_path)

    home = ag_config.Path("~").expanduser() / "controller"
    assert resolved.home == home
    assert resolved.runs_dirs[0] == home / "runs"
    assert ag_config.Path("~").expanduser() / "c5-runs" in resolved.runs_dirs
    # The peers home is derived from CONTROLLER_HOME, never hard-coded to $HOME.
    assert resolved.peers_dir == home.parent / ag_config.PEERS_DIRNAME
    assert resolved.repo_state_dir is None
    assert resolved.state_dirs == (resolved.peers_dir,)
    assert resolved.toml_state == "absent"


def test_a_relative_home_resolves_against_the_caller_cwd(tmp_path):
    resolved = ag_config.load({"CONTROLLER_HOME": "ctl-home"}, cwd=tmp_path)

    assert resolved.home == tmp_path / "ctl-home"
    assert resolved.toml_path == tmp_path / "ctl-home" / ag_config.TOML_NAME


def test_a_configured_runs_dir_leads_and_the_default_is_kept(tmp_path):
    resolved = ag_config.load(
        {"CONTROLLER_HOME": str(tmp_path / "home"), "CONTROLLER_RUNS_DIR": str(tmp_path / "runs")},
        cwd=tmp_path,
    )

    assert resolved.runs_dirs[0] == tmp_path / "runs"
    assert tmp_path / "home" / "runs" in resolved.runs_dirs


def test_the_repository_state_dir_is_read_only_reported_when_the_repo_is_named(tmp_path):
    resolved = ag_config.load(
        {"CONTROLLER_HOME": str(tmp_path / "home"), "CONTROLLER_REPO": str(tmp_path)}, cwd=tmp_path
    )

    assert resolved.repo_state_dir == tmp_path / ".controller-state"
    assert resolved.state_dirs == (resolved.repo_state_dir, resolved.peers_dir)


def test_agctl_toml_extends_the_legacy_runs_dirs(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ag_config.TOML_NAME).write_text(
        '[runs]\nlegacy_dirs = ["../other-runs", "/tmp/absolute-runs"]\n', encoding="utf-8"
    )

    resolved = ag_config.load({"CONTROLLER_HOME": str(home)}, cwd=tmp_path)

    assert resolved.toml_state == "loaded"
    assert resolved.toml_legacy_dirs == (tmp_path / "other-runs", ag_config.Path("/tmp/absolute-runs"))
    assert resolved.runs_dirs[-2:] == resolved.toml_legacy_dirs


def test_an_explicitly_named_but_absent_agctl_toml_is_a_configuration_error(tmp_path):
    with pytest.raises(ag_config.ConfigError, match="does not exist"):
        ag_config.load({"AGCTL_TOML": str(tmp_path / "nope.toml")}, cwd=tmp_path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[runs\n", "cannot be read as TOML"),
        ('[runs]\nlegacy_dir = ["x"]\n', "unknown key"),
        ('[runs]\nlegacy_dirs = "x"\n', "list of path strings"),
        ("runs = 3\n", "[runs] must be a table"),
    ],
)
def test_a_malformed_agctl_toml_refuses_and_names_the_problem(tmp_path, text, expected):
    home = tmp_path / "home"
    home.mkdir()
    (home / ag_config.TOML_NAME).write_text(text, encoding="utf-8")

    with pytest.raises(ag_config.ConfigError, match=expected):
        ag_config.load({"CONTROLLER_HOME": str(home)}, cwd=tmp_path)


# --- the CLI boundary -----------------------------------------------------------------------


def test_help_names_the_estate_verb(capsys):
    from agctl import cli as agcli

    assert agcli.build_cli(ident()).run(argv=["--help"]) == 0
    assert "estate" in capsys.readouterr().out


def test_version_reports_the_identity_and_never_a_literal(capsys):
    from agctl import cli as agcli

    assert agcli.build_cli(ident("1.2.3")).run(argv=["--version"]) == 0
    assert "agctl 1.2.3" in capsys.readouterr().out


def test_an_unknown_verb_is_a_usage_error(capsys):
    from agctl import cli as agcli

    assert agcli.build_cli(ident()).run(argv=["estate-of-the-world"]) == 2
    assert "agctl" in capsys.readouterr().err


def test_the_estate_verb_is_registered_with_no_network_or_write_group(capsys):
    from agctl import cli as agcli

    app = agcli.build_cli(ident())
    assert app.run(argv=["estate", "--help"]) == 0
    assert "--json" in capsys.readouterr().out


def test_a_source_tree_without_the_installed_distribution_refuses_loudly():
    # A distribution name that is never installed exercises the refusal path of `main()`, not
    # the happy path, and there is deliberately no literal fallback version.
    with pytest.raises(VersionLookupError, match="not-installed-under-this-name"):
        CliIdentity.resolve(
            name="AGCTL",
            long_name=LONG_NAME,
            command="agctl",
            distribution="agctl-not-installed-under-this-name",
        )
