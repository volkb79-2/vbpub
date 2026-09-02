"""ciu-P33 / CIU-60 — `ciu clean --vanilla` (S6.4b).

Oracle O6 has two halves and the DEFAULT half is the load-bearing one:

* plain `ciu clean` must keep leaving every `deploy.VANILLA_RESET_FILES` entry
  completely untouched — a regression here would silently start deleting an
  operator's rendered config and workspace identity on every ordinary teardown;
* `--vanilla` additionally removes exactly those files, tolerating an already
  absent one, after everything plain clean already does.

The file list is taken FROM `deploy.VANILLA_RESET_FILES` rather than restated
here: ciu-P47 added a fourth entry (`ciu.instance.generated.toml`), and a
hand-copied tuple would have kept passing while covering only three of them.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402

RESET_FILES = deploy.VANILLA_RESET_FILES


def _profile():
    profile = MagicMock()
    profile.config = {"deploy": {"project_name": "proj", "environment_tag": "env"}}
    return profile


def _workspace(tmp_path: Path, *, present=RESET_FILES) -> Path:
    (tmp_path / "applications/api").mkdir(parents=True)
    bodies = {
        "ciu.global.toml": "[ciu]\nenv = 'rendered'\n",
        "ciu.env": 'export DOCKER_NETWORK_INTERNAL="proj-abc123-network"\n',
        "ciu.global.instance.toml.j2": "[ciu.instance]\nservice_profiles = ['core']\n",
        "ciu.instance.generated.toml": (
            '[ciu.instance.generated]\nnetwork = "proj-abc123-network"\n'
        ),
    }
    # Every reset file must have a body here: a new entry added to
    # VANILLA_RESET_FILES with no fixture body would otherwise KeyError rather
    # than quietly go untested.
    assert set(bodies) == set(deploy.VANILLA_RESET_FILES)
    for name in present:
        (tmp_path / name).write_text(bodies[name], encoding="utf-8")
    # A committed override and a stack file, to prove clean's blast radius is
    # exactly the named files and nothing adjacent.
    (tmp_path / "ciu.global.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    return tmp_path


def _run_clean(monkeypatch, repo_root: Path, *, vanilla=False, fail=False):
    """action_clean with the Docker-touching passes stubbed out."""
    monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
    monkeypatch.setattr(
        deploy, "render_selected_stacks", lambda *a, **k: {"applications/api": {}}
    )
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda *a, **k: [])
    monkeypatch.setattr(deploy, "_workspace_identity_network", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "_list_stack_project_networks", lambda *a, **k: [])
    monkeypatch.setattr(deploy, "_remove_identity_networks", lambda nets: ([], []))
    monkeypatch.setattr(deploy, "_network_exists", lambda net: False)
    monkeypatch.setattr(deploy.worktree_pkg, "release_own_lease", lambda *a, **k: None)
    if fail:
        monkeypatch.setattr(
            deploy.engine,
            "reset_service",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("compose down failed")),
        )
    else:
        monkeypatch.setattr(deploy.engine, "reset_service", lambda *a, **k: None)
    return deploy.action_clean(
        repo_root,
        _profile(),
        [{"path": "applications/api"}],
        ignore_errors=True,
        **({"vanilla": True} if vanilla else {}),
    )


# ---------------------------------------------------------------------------
# The default half — this package changes NOTHING about plain `ciu clean`
# ---------------------------------------------------------------------------


def test_plain_clean_leaves_every_reset_file_untouched(monkeypatch, tmp_path):
    repo_root = _workspace(tmp_path)
    before = {name: (repo_root / name).read_bytes() for name in RESET_FILES}

    assert _run_clean(monkeypatch, repo_root) == 0

    for name in RESET_FILES:
        assert (repo_root / name).exists(), f"plain clean removed {name}"
        assert (repo_root / name).read_bytes() == before[name]


def test_plain_clean_is_the_default_when_vanilla_is_not_passed(monkeypatch, tmp_path):
    """`action_clean`'s `vanilla` parameter defaults to False, so every
    existing caller (there are several, none of which pass it) is unaffected."""
    import inspect

    repo_root = _workspace(tmp_path)
    assert (
        inspect.signature(deploy.action_clean).parameters["vanilla"].default is False
    )
    assert _run_clean(monkeypatch, repo_root) == 0
    assert all((repo_root / name).exists() for name in RESET_FILES)


def test_plain_clean_prints_nothing_about_vanilla(monkeypatch, tmp_path, capsys):
    repo_root = _workspace(tmp_path)
    _run_clean(monkeypatch, repo_root)
    assert "vanilla" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The new-flag half
# ---------------------------------------------------------------------------


def test_vanilla_removes_exactly_the_reset_files(monkeypatch, tmp_path, capsys):
    repo_root = _workspace(tmp_path)

    assert _run_clean(monkeypatch, repo_root, vanilla=True) == 0

    for name in RESET_FILES:
        assert not (repo_root / name).exists(), f"--vanilla kept {name}"
    # Committed inputs are never in scope: a --vanilla is a reset to
    # freshly-CLONED state, not a reset to empty-directory state.
    assert (repo_root / "ciu.global.toml.j2").exists()
    assert (repo_root / "ciu.global.defaults.toml.j2").exists()
    out = capsys.readouterr().out
    for name in RESET_FILES:
        assert name in out


def test_vanilla_on_an_already_clean_workspace_succeeds(monkeypatch, tmp_path, capsys):
    repo_root = _workspace(tmp_path, present=())

    assert _run_clean(monkeypatch, repo_root, vanilla=True) == 0
    assert "already at vanilla state" in capsys.readouterr().out


@pytest.mark.parametrize("missing", RESET_FILES)
def test_vanilla_tolerates_any_single_file_already_absent(
    monkeypatch, tmp_path, missing
):
    present = tuple(name for name in RESET_FILES if name != missing)
    repo_root = _workspace(tmp_path, present=present)

    assert _run_clean(monkeypatch, repo_root, vanilla=True) == 0
    assert not any((repo_root / name).exists() for name in RESET_FILES)


def test_vanilla_reports_a_removal_that_failed(monkeypatch, tmp_path, capsys):
    """A --vanilla that could not remove a file it found must not report
    success — that would claim a reset that did not happen."""
    repo_root = _workspace(tmp_path)
    real_unlink = Path.unlink

    def selective(self, *args, **kwargs):
        if self.name == "ciu.env":
            raise OSError("EACCES")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", selective)

    assert _run_clean(monkeypatch, repo_root, vanilla=True) == 1
    out = capsys.readouterr().out
    assert "could not remove" in out
    assert (repo_root / "ciu.env").exists()


def test_vanilla_is_skipped_when_the_teardown_failed(monkeypatch, tmp_path, capsys):
    """Safe direction: a failed clean keeps ciu.env, which is the workspace
    identity the retry (and any manual cleanup) has to resolve from."""
    repo_root = _workspace(tmp_path)

    assert _run_clean(monkeypatch, repo_root, vanilla=True, fail=True) == 1

    for name in RESET_FILES:
        assert (repo_root / name).exists()
    assert "--vanilla skipped" in capsys.readouterr().out


def test_vanilla_reset_files_is_exactly_the_documented_set():
    """The one place the literal set is pinned.

    Everything else in this file derives from `deploy.VANILLA_RESET_FILES`, so
    this is what makes adding or dropping an entry a deliberate act rather than
    a silent widening of what `--vanilla` deletes.
    """
    assert deploy.VANILLA_RESET_FILES == (
        "ciu.global.toml",
        "ciu.env",
        "ciu.global.instance.toml.j2",
        "ciu.instance.generated.toml",
    )


# ---------------------------------------------------------------------------
# CLI wiring: `ciu clean --vanilla` -> deploy `--clean --vanilla`
# ---------------------------------------------------------------------------


def test_deploy_argparse_accepts_vanilla_and_defaults_it_false():
    assert deploy.parse_args(["--clean"]).vanilla is False
    assert deploy.parse_args(["--clean", "--vanilla"]).vanilla is True


def test_main_forwards_vanilla_to_action_clean(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_clean(repo_root, profile, selection, *, ignore_errors, vanilla=False):
        seen["vanilla"] = vanilla
        return 0

    monkeypatch.setattr(deploy, "action_clean", fake_clean)
    _stub_main_preamble(monkeypatch, tmp_path)

    assert deploy.main(["--clean", "--vanilla", "-y"]) == 0
    assert seen["vanilla"] is True


def test_main_defaults_vanilla_false_for_a_plain_clean(monkeypatch, tmp_path):
    seen: dict = {}

    def fake_clean(repo_root, profile, selection, *, ignore_errors, vanilla=False):
        seen["vanilla"] = vanilla
        return 0

    monkeypatch.setattr(deploy, "action_clean", fake_clean)
    _stub_main_preamble(monkeypatch, tmp_path)

    assert deploy.main(["--clean", "-y"]) == 0
    assert seen["vanilla"] is False


def _stub_main_preamble(monkeypatch, tmp_path: Path) -> None:
    """Cut `deploy.main` down to its action dispatch for these two tests."""
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda *a, **k: {})
    monkeypatch.setattr(deploy, "resolve_profiles", lambda *a, **k: _profile())
    monkeypatch.setattr(deploy, "build_selection", lambda *a, **k: [{"path": "x"}])


def test_cli_clean_help_documents_vanilla():
    from ciu import cli

    text = cli._VERB_HELP["clean"]
    assert "--vanilla" in text
    for name in RESET_FILES:
        assert name in text
