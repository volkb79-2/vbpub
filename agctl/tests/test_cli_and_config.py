"""The configuration boundary: every path resolved from the environment or `agctl.toml`.

No test here touches the real estate: `$CONTROLLER_HOME`, the runs directories and the state
directories are all built inside `tmp_path`, and nothing is read outside it.
"""

from __future__ import annotations

import pytest

from agctl import config as ag_config

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


def test_the_gate_paths_default_to_the_controller_home(tmp_path):
    resolved = ag_config.load({"CONTROLLER_HOME": str(tmp_path / "home")}, cwd=tmp_path)

    # `remote-gate.sh` writes its locks under the home and its results under the home too
    # (184/225); the override is the one variable the script itself honours.
    assert resolved.gate_lock_dir == tmp_path / "home" / ag_config.GATE_LOCK_DIR
    assert resolved.remote_gate_out == tmp_path / "home" / ag_config.GATE_OUT_DIR


def test_a_configured_gate_result_directory_replaces_the_default(tmp_path):
    resolved = ag_config.load(
        {"CONTROLLER_HOME": str(tmp_path / "home"), "CONTROLLER_REMOTE_OUT": "gates"}, cwd=tmp_path
    )

    assert resolved.remote_gate_out == tmp_path / "gates"


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
        ('legacy_dirs = ["x"]\n', "top level names unknown key"),
        ("runs = 3\n", r"\[runs\] must be a table"),
    ],
)
def test_a_malformed_agctl_toml_refuses_and_names_the_problem(tmp_path, text, expected):
    home = tmp_path / "home"
    home.mkdir()
    (home / ag_config.TOML_NAME).write_text(text, encoding="utf-8")

    with pytest.raises(ag_config.ConfigError, match=expected):
        ag_config.load({"CONTROLLER_HOME": str(home)}, cwd=tmp_path)


def test_an_empty_path_value_refuses_instead_of_resolving_to_the_cwd():
    with pytest.raises(ag_config.ConfigError, match="names no directory"):
        ag_config.expand("   ", ag_config.Path("/tmp"))


def test_a_toml_that_is_not_utf8_is_refused_with_the_decode_reason(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ag_config.TOML_NAME).write_bytes(b"\xff\xfe[runs]\n")

    with pytest.raises(ag_config.ConfigError, match="cannot be read as TOML"):
        ag_config.load({"CONTROLLER_HOME": str(home)}, cwd=tmp_path)


def test_an_explicitly_named_existing_agctl_toml_is_loaded(tmp_path):
    named = tmp_path / "explicit.toml"
    named.write_text('[runs]\nlegacy_dirs = ["runs-extra"]\n', encoding="utf-8")

    resolved = ag_config.load({"AGCTL_TOML": str(named)}, cwd=tmp_path)

    assert resolved.toml_path == named
    assert resolved.toml_state == "loaded"
    assert resolved.toml_legacy_dirs == (tmp_path / "runs-extra",)


def test_a_relative_path_with_a_dotdot_is_resolved_to_one_spelling(tmp_path):
    resolved = ag_config.load({"CONTROLLER_HOME": str(tmp_path / "home")}, cwd=tmp_path)

    (home,) = [path for path in resolved.runs_dirs if path.name == "runs"]
    assert ".." not in str(home)
