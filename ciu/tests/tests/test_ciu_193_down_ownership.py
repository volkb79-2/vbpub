"""CIU-193: `ciu down` / `--stop` must stop only THIS checkout's containers.

With shared (non per-instance) deploy tags, the S7.8 name prefix
(``{project}-{env}-``) and the tagged compose project name select every
checkout's containers. CIU-191 made ``clean`` ownership-filtered by the
``ciu.instance`` / ``ciu.repo-root`` labels; ``down`` (bare, ``--profile`` /
``--phases`` restricted, and ``--dir``) still stopped them all.

Oracles (fake docker only; no real daemon is touched):
- in a worktree instance every ``down`` form stops only containers labelled
  for that worktree and names each container it leaves alone, with the reason;
- the main checkout's ``down`` still stops its own and its legacy unlabelled
  containers (and leaves a worktree's labelled ones alone);
- a checkout with no identity keeps the pre-CIU-191 unfiltered behaviour, and
  an untagged selection (identity-derived, already per-instance compose
  projects) is not filtered;
- a worktree that records no instance id refuses instead of guessing.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from tests.test_ciu_191_worktree_rm_foreign_identity import (  # noqa: E402
    MAIN_ID,
    MAIN_ROOT,
    WT_ID,
    WT_ROOT,
    _checkout,
)

TAGGED = {"deploy": {"project_name": "naf", "environment_tag": "dev"}}
PROJECT = "naf-dev-apps"


class StopDaemon:
    """Running containers of TWO checkouts that share the deploy tags."""

    def __init__(self):
        # name -> (instance label, repo-root label[, compose working_dir]);
        # all in one compose project. The main checkout's containers were
        # started from its own tree (MAIN_ROOT), never from a test checkout.
        self.containers = {
            "naf-dev-app": (MAIN_ID, MAIN_ROOT),
            "naf-dev-legacy": ("", "", f"{MAIN_ROOT}/apps/api"),
            "naf-dev-wt-own": (WT_ID, WT_ROOT),
            "naf-dev-moved": (WT_ID, MAIN_ROOT),
        }
        self.stopped: list[str] = []
        self.calls: list[list[str]] = []
        self.fail_ps = False

    def __call__(self, args, **_kw):
        args = list(args)
        self.calls.append(args)
        if args[0] == "ps":
            if self.fail_ps:
                return subprocess.CompletedProcess(args, 1, "", "Cannot connect to the Docker daemon")
            fmt = args[args.index("--format") + 1]
            labelled = "ciu.instance" in fmt
            with_wd = "com.docker.compose.project.working_dir" in fmt
            rows = []
            for name, labels in sorted(self.containers.items()):
                inst, root = labels[0], labels[1]
                wd = labels[2] if len(labels) > 2 else ""
                if not labelled:
                    rows.append(name)
                elif with_wd:
                    rows.append(f"{name}\t{inst}\t{root}\t{wd}")
                else:
                    rows.append(f"{name}\t{inst}\t{root}")
            return subprocess.CompletedProcess(args, 0, "\n".join(rows) + "\n", "")
        if args[0] == "stop":
            self.stopped.extend(args[1:])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unhandled docker argv: {args}")


@pytest.fixture
def daemon(monkeypatch):
    fake = StopDaemon()
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    monkeypatch.setattr(deploy, "_stack_compose_projects", lambda *_a, **_k: [PROJECT])
    return fake


def _worktree(tmp_path: Path, instance_id: str = WT_ID) -> Path:
    return _checkout(tmp_path, instance=True, instance_id=instance_id, root=WT_ROOT)


def _main(tmp_path: Path) -> Path:
    return _checkout(tmp_path, instance=False, instance_id=MAIN_ID, root=MAIN_ROOT)


SELECTION = [{"path": "apps/api", "name": "api", "phase_key": "phase_1"}]


# --- bare `ciu down` ------------------------------------------------------


def test_bare_down_in_a_worktree_stops_only_its_own_and_names_what_it_leaves(
    daemon, tmp_path, capsys
):
    """THE ACCEPTANCE TEST (bare form)."""
    repo = _worktree(tmp_path)
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-wt-own"]
    out = capsys.readouterr().out
    assert "NOT stopping naf-dev-app: owned by another instance (main01)" in out
    assert "NOT stopping naf-dev-legacy: unlabelled" in out
    assert "NOT stopping naf-dev-moved: owned by another checkout" in out


def test_bare_down_in_the_main_checkout_stops_its_own_and_legacy_unlabelled(daemon, tmp_path):
    repo = _main(tmp_path)
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert sorted(daemon.stopped) == ["naf-dev-app", "naf-dev-legacy"]


def test_bare_down_without_any_identity_is_unfiltered_as_before(daemon, tmp_path):
    (tmp_path / "apps").mkdir()
    assert deploy.action_stop(TAGGED, repo_root=tmp_path) == 0
    assert sorted(daemon.stopped) == sorted(daemon.containers)
    # The unfiltered listing keeps its historical names-only format.
    ps = next(c for c in daemon.calls if c[0] == "ps")
    assert ps[ps.index("--format") + 1] == "{{.Names}}"


def test_down_with_only_foreign_containers_stops_nothing_and_succeeds(
    daemon, tmp_path, capsys
):
    del daemon.containers["naf-dev-wt-own"]
    repo = _worktree(tmp_path)
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []
    assert not any(c[0] == "stop" for c in daemon.calls)
    out = capsys.readouterr().out
    assert "NOT stopping naf-dev-app" in out


def test_worktree_without_a_recorded_instance_id_refuses_and_stops_nothing(
    daemon, tmp_path, capsys
):
    repo = _worktree(tmp_path, instance_id="")
    assert deploy.action_stop(TAGGED, repo_root=repo) == 1
    assert daemon.stopped == []
    assert "[CIU-193]" in capsys.readouterr().out


def test_ownership_listing_failure_fails_the_down(daemon, tmp_path):
    daemon.fail_ps = True
    repo = _worktree(tmp_path)
    assert deploy.action_stop(TAGGED, repo_root=repo) == 1
    assert daemon.stopped == []


# --- restricted `ciu down --profile/--phases` ------------------------------


def test_restricted_down_in_a_worktree_stops_only_its_own(daemon, tmp_path, capsys):
    repo = _worktree(tmp_path)
    assert deploy.action_stop(TAGGED, repo_root=repo, selection=SELECTION) == 0
    assert daemon.stopped == ["naf-dev-wt-own"]
    ps = next(c for c in daemon.calls if c[0] == "ps")
    assert f"label=com.docker.compose.project={PROJECT}" in ps
    assert "NOT stopping naf-dev-app: owned by another instance (main01)" in capsys.readouterr().out


def test_restricted_down_in_the_main_checkout_is_unchanged_for_its_own(daemon, tmp_path):
    repo = _main(tmp_path)
    assert deploy.action_stop(TAGGED, repo_root=repo, selection=SELECTION) == 0
    assert sorted(daemon.stopped) == ["naf-dev-app", "naf-dev-legacy"]


def test_restricted_down_of_an_untagged_selection_is_not_filtered(daemon, tmp_path):
    """Untagged compose projects are identity-derived (CIU-46), i.e. already
    per-instance: an unlabelled container in one is this checkout's."""
    repo = _worktree(tmp_path)
    assert deploy.action_stop({"deploy": {}}, repo_root=repo, selection=SELECTION) == 0
    assert sorted(daemon.stopped) == sorted(daemon.containers)


def test_restricted_down_in_an_unattributable_worktree_refuses(daemon, tmp_path):
    repo = _worktree(tmp_path, instance_id="")
    assert deploy.action_stop(TAGGED, repo_root=repo, selection=SELECTION) == 1
    assert daemon.stopped == []


# --- the `ciu down` verb dispatch (deploy._run) ------------------------------


def _run_down(monkeypatch, root: Path, *, profile_arg=None):
    from ciu.deploy_pkg.profiles import Profile

    profile = Profile(name="apps", config=TAGGED)
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_k: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _c: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _d: root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _r: {})
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _c, _n: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _p, _ph: SELECTION)
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: ["stop"])
    args = argparse.Namespace(
        define_root=None, update_cert_permission=False, profile=profile_arg,
        phases=None, dry_run=False, no_preflight=False, ignore_errors=False,
    )
    return deploy._run(args, ["--stop"])


def test_ciu_down_verb_in_a_worktree_is_ownership_filtered(daemon, monkeypatch, tmp_path):
    repo = _worktree(tmp_path)
    assert _run_down(monkeypatch, repo) == 0
    assert daemon.stopped == ["naf-dev-wt-own"]


def test_ciu_down_profile_verb_in_a_worktree_is_ownership_filtered(daemon, monkeypatch, tmp_path):
    repo = _worktree(tmp_path)
    assert _run_down(monkeypatch, repo, profile_arg=["apps"]) == 0
    assert daemon.stopped == ["naf-dev-wt-own"]


# --- `ciu down --dir` (stop_stack) ------------------------------------------


@pytest.fixture
def one_stack(monkeypatch):
    """stop_stack's identity resolution, reduced to its result."""
    from ciu.deploy_pkg.profiles import Profile

    state = {"config": TAGGED}

    def resolve(_root, *, stack, profiles=None, **_kw):
        return {"resolved": {"identities": {stack: {
            "api": {"compose_project": PROJECT},
        }}}}

    monkeypatch.setattr(deploy, "resolve_identities", resolve)
    monkeypatch.setattr(
        deploy, "load_global_config", lambda _r, **_k: {},
    )
    monkeypatch.setattr(
        deploy, "resolve_profiles",
        lambda _c, _n=None, **_k: Profile(name="p", config=state["config"]),
    )
    return state


def _stack_dir(repo: Path) -> str:
    return "apps/api"


def test_down_dir_in_a_worktree_stops_only_its_own(daemon, one_stack, tmp_path, capsys):
    repo = _worktree(tmp_path)
    assert deploy.stop_stack(repo, _stack_dir(repo)) == 0
    assert daemon.stopped == ["naf-dev-wt-own"]
    out = capsys.readouterr().out
    assert "NOT stopping naf-dev-legacy: unlabelled" in out
    assert "NOT stopping naf-dev-app: owned by another instance (main01)" in out


def test_down_dir_in_the_main_checkout_is_unchanged_for_its_own(daemon, one_stack, tmp_path):
    repo = _main(tmp_path)
    assert deploy.stop_stack(repo, _stack_dir(repo)) == 0
    assert sorted(daemon.stopped) == ["naf-dev-app", "naf-dev-legacy"]


def test_down_dir_of_an_untagged_stack_is_not_filtered(daemon, one_stack, tmp_path):
    one_stack["config"] = {"deploy": {}}
    repo = _worktree(tmp_path)
    assert deploy.stop_stack(repo, _stack_dir(repo)) == 0
    assert sorted(daemon.stopped) == sorted(daemon.containers)


def test_down_dir_in_an_unattributable_worktree_refuses(daemon, one_stack, tmp_path):
    repo = _worktree(tmp_path, instance_id="")
    with pytest.raises(RuntimeError, match=r"\[CIU-193\]"):
        deploy.stop_stack(repo, _stack_dir(repo))
    assert daemon.stopped == []


def test_down_dir_ownership_listing_failure_refuses_before_stop(daemon, one_stack, tmp_path):
    daemon.fail_ps = True
    repo = _worktree(tmp_path)
    with pytest.raises(RuntimeError, match="Cannot connect"):
        deploy.stop_stack(repo, _stack_dir(repo))
    assert not any(c[0] == "stop" for c in daemon.calls)


# --- review rounds 1-2: Compose working_dir evidence, strict listing ---------


def _per_instance(tag: str) -> dict:
    return {"deploy": {"project_name": "naf", "environment_tag": tag}}


def test_unlabelled_own_containers_are_attributed_by_compose_working_dir(
    daemon, tmp_path, capsys
):
    """A worktree's own pre-label (or never-labelled) container carries no CIU
    labels; Compose's project working_dir inside this checkout is the proof."""
    repo = _worktree(tmp_path)
    daemon.containers["naf-dev-wt-legacy"] = ("", "", str(repo / "apps" / "api"))
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert sorted(daemon.stopped) == ["naf-dev-wt-legacy", "naf-dev-wt-own"]
    out = capsys.readouterr().out
    assert (
        f"NOT stopping naf-dev-legacy: unlabelled, and its compose working_dir "
        f"{MAIN_ROOT}/apps/api is not in this checkout"
    ) in out


@pytest.mark.parametrize("wd", ["", "<no value>", "relative/apps", "/elsewhere/apps"])
def test_unlabelled_without_in_checkout_working_dir_is_left_alone(daemon, tmp_path, capsys, wd):
    repo = _worktree(tmp_path)
    daemon.containers = {"naf-dev-x": ("", "", wd)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []
    assert "NOT stopping naf-dev-x: unlabelled, and its compose working_dir" in capsys.readouterr().out


def test_a_relative_working_dir_is_never_resolved_against_the_cwd(daemon, tmp_path, monkeypatch):
    repo = _worktree(tmp_path)
    monkeypatch.chdir(repo)
    daemon.containers = {"naf-dev-x": ("", "", "apps/api")}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_a_sibling_directory_with_a_common_prefix_is_not_inside(daemon, tmp_path):
    (tmp_path / "wt").mkdir()
    repo = _worktree(tmp_path / "wt")
    daemon.containers = {"naf-dev-x": ("", "", f"{repo}-other/apps")}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_unmanaged_linked_worktree_stops_its_own_and_leaves_mains(
    daemon, monkeypatch, tmp_path
):
    """A plain `git worktree add` checkout: no S16 record, so `ciu up` never
    labelled its containers; Compose evidence attributes them."""
    repo = _checkout(tmp_path, instance=False, instance_id=WT_ID, root=WT_ROOT)
    monkeypatch.setattr(deploy, "_is_linked_git_worktree", lambda _root: True)
    daemon.containers = {
        "naf-dev-mine": ("", "", str(repo / "apps" / "api")),
        "naf-dev-legacy": ("", "", f"{MAIN_ROOT}/apps/api"),
    }
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-mine"]
    daemon.stopped.clear()
    assert deploy.action_stop(TAGGED, repo_root=repo, selection=SELECTION) == 0
    assert daemon.stopped == ["naf-dev-mine"]


def test_down_dir_uses_the_working_dir_evidence(daemon, one_stack, tmp_path):
    repo = _worktree(tmp_path)
    daemon.containers["naf-dev-wt-legacy"] = ("", "", str(repo / "apps" / "api"))
    assert deploy.stop_stack(repo, _stack_dir(repo)) == 0
    assert sorted(daemon.stopped) == ["naf-dev-wt-legacy", "naf-dev-wt-own"]


def test_a_tag_equal_to_this_checkouts_id_is_still_filtered(daemon, tmp_path):
    """Round-2 regression: main (id main01) uses tag main01; a worktree using
    the literal tag main01 shares the names. Main's down must not stop the
    worktree's labelled container."""
    daemon.containers = {
        f"naf-{MAIN_ID}-app": ("", "", f"{MAIN_ROOT}/apps/api"),
        f"naf-{MAIN_ID}-worktree-app": (WT_ID, WT_ROOT),
    }
    repo = _main(tmp_path)
    assert deploy.action_stop(_per_instance(MAIN_ID), repo_root=repo) == 0
    assert daemon.stopped == [f"naf-{MAIN_ID}-app"]


def test_per_instance_tags_in_a_managed_worktree_stop_its_own(daemon, tmp_path):
    repo = _worktree(tmp_path)
    daemon.containers = {
        f"naf-{WT_ID}-db": (WT_ID, WT_ROOT),
        f"naf-{WT_ID}-old": ("", "", str(repo / "apps" / "db")),
    }
    assert deploy.action_stop(_per_instance(WT_ID), repo_root=repo) == 0
    assert sorted(daemon.stopped) == [f"naf-{WT_ID}-db", f"naf-{WT_ID}-old"]


def test_bare_down_without_an_owner_fails_on_a_listing_failure(daemon, tmp_path):
    """Review finding 2: the unfiltered bare listing must not read a daemon
    failure as "nothing running" either."""
    daemon.fail_ps = True
    (tmp_path / "apps").mkdir()
    assert deploy.action_stop(TAGGED, repo_root=tmp_path) == 1
    assert deploy.action_stop(TAGGED) == 1
    assert daemon.stopped == []


def test_clean_keeps_the_plain_label_verdict(daemon, tmp_path):
    """The working_dir evidence is a `down` rule; `clean` (CIU-191) still
    lists with the three-field label format."""
    repo = _worktree(tmp_path)
    owner = deploy._clean_owner(repo)
    names = deploy._matching_containers(TAGGED, all_states=True, owner=owner, skipped=[])
    assert names == ["naf-dev-wt-own"]
    ps = next(c for c in daemon.calls if c[0] == "ps")
    assert "working_dir" not in ps[ps.index("--format") + 1]
