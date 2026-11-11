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
import os
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
                cfg = labels[3] if len(labels) > 3 else ""
                if not labelled:
                    rows.append(name)
                elif with_wd:
                    rows.append(f"{name}\t{inst}\t{root}\t{wd}\t{cfg}")
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
    # The git worktree registry below the checkout (resolved paths); None
    # models git being unable to answer. The real query has its own tests.
    fake.registered = set()
    monkeypatch.setattr(deploy, "_registered_worktrees", lambda _base: fake.registered)
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


def test_down_dir_without_identity_keeps_the_id_listing_and_fails_on_its_failure(
    daemon, one_stack, tmp_path
):
    (tmp_path / "apps" / "api").mkdir(parents=True)
    assert deploy.stop_stack(tmp_path, _stack_dir(tmp_path)) == 0
    ps = next(c for c in daemon.calls if c[0] == "ps")
    assert ps[ps.index("--format") + 1] == "{{.ID}}"
    daemon.fail_ps = True
    with pytest.raises(RuntimeError, match="docker ps failed while resolving Compose project"):
        deploy.stop_stack(tmp_path, _stack_dir(tmp_path))


# --- review rounds 1-3: Compose evidence for unlabelled candidates, strict listing


def _per_instance(tag: str) -> dict:
    return {"deploy": {"project_name": "naf", "environment_tag": tag}}


def _compose_project(stack_dir: Path) -> tuple[str, str]:
    """(working_dir, config_files) labels of a project Compose ran from *stack_dir*."""
    stack_dir.mkdir(parents=True, exist_ok=True)
    compose = stack_dir / "ciu.compose.yml"
    overlay = stack_dir / ".ciu" / "ciu.compose.overlay.yml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    overlay.write_text("services: {}\n", encoding="utf-8")
    return str(stack_dir), f"{compose},{overlay}"


def test_unlabelled_own_containers_are_attributed_by_compose_evidence(
    daemon, tmp_path, capsys
):
    """A worktree's own pre-label (or never-labelled) container carries no CIU
    labels; Compose's project dir and config files inside this checkout prove it."""
    repo = _worktree(tmp_path)
    daemon.containers["naf-dev-wt-legacy"] = ("", "", *_compose_project(repo / "apps" / "api"))
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert sorted(daemon.stopped) == ["naf-dev-wt-legacy", "naf-dev-wt-own"]
    out = capsys.readouterr().out
    assert (
        f"NOT stopping naf-dev-legacy: unlabelled, and its compose project "
        f"({MAIN_ROOT}/apps/api) is not provably this checkout's"
    ) in out


@pytest.mark.parametrize("wd", ["", "<no value>", "relative/apps", "/elsewhere/apps"])
def test_unlabelled_without_in_checkout_working_dir_is_left_alone(daemon, tmp_path, capsys, wd):
    repo = _worktree(tmp_path)
    _, cfg = _compose_project(repo / "apps" / "api")
    daemon.containers = {"naf-dev-x": ("", "", wd, cfg)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []
    assert "NOT stopping naf-dev-x: unlabelled, and its compose project" in capsys.readouterr().out


def test_a_relative_working_dir_is_never_resolved_against_the_cwd(daemon, tmp_path, monkeypatch):
    repo = _worktree(tmp_path)
    _, cfg = _compose_project(repo / "apps" / "api")
    monkeypatch.chdir(repo)
    daemon.containers = {"naf-dev-x": ("", "", "apps/api", cfg)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_a_sibling_directory_with_a_common_prefix_is_not_inside(daemon, tmp_path):
    (tmp_path / "wt").mkdir()
    repo = _worktree(tmp_path / "wt")
    daemon.containers = {"naf-dev-x": ("", "", *_compose_project(Path(f"{repo}-other") / "apps"))}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


@pytest.mark.parametrize("stack", ["apps/api", "."])
def test_a_nested_checkout_below_this_one_is_not_this_checkouts(daemon, tmp_path, stack):
    """Review round 3: a linked worktree nested at <root>/.worktrees/child is
    lexically inside the parent but is another checkout (its stack may sit
    in a subdirectory or at the child's own root)."""
    repo = _worktree(tmp_path)
    child = repo / ".worktrees" / "child"
    child.mkdir(parents=True)
    (child / ".git").write_text("gitdir: /elsewhere/.git/worktrees/child\n", encoding="utf-8")
    daemon.containers = {"naf-dev-child": ("", "", *_compose_project((child / stack).resolve()))}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_a_symlink_inside_the_checkout_pointing_out_is_not_this_checkouts(daemon, tmp_path):
    (tmp_path / "wt").mkdir()
    repo = _worktree(tmp_path / "wt")
    outside = tmp_path / "outside"
    wd, cfg = _compose_project(outside / "apps")
    (repo / "link").symlink_to(outside)
    linked_cfg = cfg.replace(str(outside), str(repo / "link"))
    daemon.containers = {"naf-dev-x": ("", "", str(repo / "link" / "apps"), linked_cfg)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_an_in_root_project_directory_with_external_compose_files_is_not_enough(daemon, tmp_path):
    """`docker compose --project-directory <in-root> -f /elsewhere/compose.yml`."""
    (tmp_path / "wt").mkdir()
    repo = _worktree(tmp_path / "wt")
    wd, _ = _compose_project(repo / "apps" / "api")
    _, external = _compose_project(tmp_path / "elsewhere")
    daemon.containers = {"naf-dev-x": ("", "", wd, external)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


@pytest.mark.parametrize("cfg", ["", "<no value>"])
def test_missing_config_files_evidence_is_not_enough(daemon, tmp_path, cfg):
    repo = _worktree(tmp_path)
    wd, _ = _compose_project(repo / "apps" / "api")
    daemon.containers = {"naf-dev-x": ("", "", wd, cfg)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_a_vanished_project_directory_is_not_enough(daemon, tmp_path):
    repo = _worktree(tmp_path)
    gone = repo / "gone"
    daemon.containers = {"naf-dev-x": ("", "", str(gone), f"{gone}/ciu.compose.yml")}
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
        "naf-dev-mine": ("", "", *_compose_project(repo / "apps" / "api")),
        "naf-dev-legacy": ("", "", f"{MAIN_ROOT}/apps/api"),
    }
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-mine"]
    daemon.stopped.clear()
    assert deploy.action_stop(TAGGED, repo_root=repo, selection=SELECTION) == 0
    assert daemon.stopped == ["naf-dev-mine"]


def test_down_dir_uses_the_compose_evidence(daemon, one_stack, tmp_path):
    repo = _worktree(tmp_path)
    daemon.containers["naf-dev-wt-legacy"] = ("", "", *_compose_project(repo / "apps" / "api"))
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
        f"naf-{WT_ID}-old": ("", "", *_compose_project(repo / "apps" / "db")),
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


def test_a_nested_checkout_root_itself_is_not_this_checkouts(daemon, tmp_path):
    """The project dir and its only compose file sit directly in the nested
    checkout's root: the boundary check must include that deepest directory."""
    repo = _worktree(tmp_path)
    child = repo / "child"
    child.mkdir()
    (child / ".git").mkdir()
    (child / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    daemon.containers = {"naf-dev-child": ("", "", str(child), str(child / "docker-compose.yml"))}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


# --- review round 4: nested CIU roots, the worktree registry, shipped overrides


def test_a_nested_ciu_root_below_this_one_is_not_this_checkouts(daemon, tmp_path):
    """A nested CIU root is its own owner (S6.4a), with or without a .git."""
    repo = _worktree(tmp_path)
    nested = repo / "standalone"
    nested.mkdir()
    (nested / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    daemon.containers = {"naf-dev-x": ("", "", *_compose_project(nested / "app"))}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_a_registered_worktree_with_a_hidden_git_marker_is_not_this_checkouts(daemon, tmp_path):
    repo = _worktree(tmp_path)
    child = repo / "mnt" / "child"
    daemon.containers = {"naf-dev-x": ("", "", *_compose_project(child / "apps"))}
    daemon.registered = {os.path.realpath(child)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_an_unanswerable_worktree_registry_proves_nothing(daemon, tmp_path, capsys):
    repo = _worktree(tmp_path)
    daemon.containers = {"naf-dev-x": ("", "", *_compose_project(repo / "apps"))}
    daemon.registered = None
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []
    assert "NOT stopping naf-dev-x: unlabelled, and its compose project" in capsys.readouterr().out


def test_a_shipped_stacks_deleted_image_override_still_counts(daemon, tmp_path):
    """run_shipped passes a temporary `-f .ciu/ciu-compose-worktree-images-*.yml`
    and deletes it after Compose started; Compose keeps listing it."""
    repo = _worktree(tmp_path)
    stack = repo / "vendor" / "vault"
    (stack / ".ciu").mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    gone = stack / ".ciu" / "ciu-compose-worktree-images-abc.yml"
    daemon.containers = {
        "naf-dev-vault": ("", "", str(stack), f"{stack / 'docker-compose.yml'},{gone}"),
    }
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-vault"]


def test_a_deleted_config_file_in_a_vanished_directory_does_not_count(daemon, tmp_path):
    repo = _worktree(tmp_path)
    wd, cfg = _compose_project(repo / "apps" / "api")
    daemon.containers = {"naf-dev-x": ("", "", wd, f"{cfg},{repo / 'gone' / 'x.yml'}")}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_a_deleted_config_file_outside_the_checkout_does_not_count(daemon, tmp_path):
    (tmp_path / "wt").mkdir()
    repo = _worktree(tmp_path / "wt")
    wd, cfg = _compose_project(repo / "apps" / "api")
    daemon.containers = {"naf-dev-x": ("", "", wd, f"{cfg},{tmp_path / 'x.yml'}")}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def _git(*args, cwd):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def test_the_registry_lists_other_worktrees_even_with_their_git_file_gone(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "x", cwd=main)
    _git("worktree", "add", "-q", ".worktrees/child", cwd=main)
    _git("worktree", "add", "-q", str(tmp_path / "sibling"), cwd=main)
    (main / ".worktrees" / "child" / ".git").unlink()
    base = os.path.realpath(main)
    assert deploy._registered_worktrees(base) == {
        os.path.realpath(main / ".worktrees" / "child"),
        os.path.realpath(tmp_path / "sibling"),
    }
    # From a linked worktree, the primary is one of the others.
    assert deploy._registered_worktrees(os.path.realpath(tmp_path / "sibling")) == {
        base, os.path.realpath(main / ".worktrees" / "child"),
    }


def test_the_registry_is_none_when_git_cannot_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert deploy._registered_worktrees(str(tmp_path)) is None  # not a repo

    def no_git(*_a, **_k):
        raise OSError("git not found")

    monkeypatch.setattr(deploy.procutil, "run_cmd", no_git)
    assert deploy._registered_worktrees(str(tmp_path)) is None


def test_a_vanished_working_dir_does_not_count_even_with_live_config_files(daemon, tmp_path):
    """Only a config FILE may be gone (the shipped override); the project
    directory itself must exist."""
    repo = _worktree(tmp_path)
    _, cfg = _compose_project(repo / "apps" / "api")
    daemon.containers = {"naf-dev-x": ("", "", str(repo / "apps" / "gone"), cfg)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


# --- review round 5: the primary checkout and its nested worktrees ----------
# The primary keeps CIU-191's rule (unlabelled = its own legacy containers)
# unless Compose's paths PROVE a checkout or CIU root nested below it. Absence
# of evidence (no labels, external paths, git unable to answer, a shipped
# bundle without .git) keeps the old behaviour.


def _nested_child(repo: Path, how: str) -> Path:
    child = repo / ".worktrees" / "child"
    child.mkdir(parents=True)
    if how == "git":
        (child / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    elif how == "ciu-root":
        (child / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    return child


@pytest.mark.parametrize("how", ["registered", "git", "ciu-root"])
def test_primary_down_leaves_a_nested_checkouts_unlabelled_container(
    daemon, tmp_path, capsys, how
):
    repo = _main(tmp_path)
    child = _nested_child(repo, how)
    if how == "registered":
        daemon.registered = {os.path.realpath(child)}
    daemon.containers["naf-dev-child"] = ("", "", *_compose_project(child / "apps" / "api"))
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert sorted(daemon.stopped) == ["naf-dev-app", "naf-dev-legacy"]
    assert (
        f"NOT stopping naf-dev-child: unlabelled, and its compose project "
        f"({child / 'apps' / 'api'}) belongs to another checkout"
    ) in capsys.readouterr().out


def test_primary_down_checks_every_config_file_for_nested_evidence(daemon, tmp_path):
    repo = _main(tmp_path)
    child = _nested_child(repo, "git")
    wd, _ = _compose_project(repo / "apps" / "api")
    _, child_cfg = _compose_project(child / "apps" / "api")
    daemon.containers = {"naf-dev-x": ("", "", wd, child_cfg)}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == []


def test_primary_down_still_stops_its_own_in_root_unlabelled_stack(daemon, tmp_path):
    repo = _main(tmp_path)
    _nested_child(repo, "git")
    daemon.containers = {"naf-dev-x": ("", "", *_compose_project(repo / "apps" / "api"))}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-x"]


@pytest.mark.parametrize("wd", ["", "<no value>", "relative/apps"])
def test_primary_down_without_usable_evidence_keeps_the_legacy_rule(daemon, tmp_path, wd):
    repo = _main(tmp_path)
    daemon.containers = {"naf-dev-x": ("", "", wd, "")}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-x"]


def test_primary_down_with_an_unanswerable_registry_still_uses_on_disk_markers(daemon, tmp_path):
    """A `ciu down --host` bundle has no .git: nothing is registered, its own
    stacks stop as before, and on-disk markers still count."""
    repo = _main(tmp_path)
    child = _nested_child(repo, "git")
    daemon.registered = None
    daemon.containers = {
        "naf-dev-own": ("", "", *_compose_project(repo / "apps" / "api")),
        "naf-dev-child": ("", "", *_compose_project(child / "apps")),
    }
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-own"]


def test_primary_labels_win_over_paths(daemon, tmp_path):
    """A container labelled with the primary's own identity is its own,
    wherever Compose ran it from."""
    repo = _main(tmp_path)
    child = _nested_child(repo, "git")
    daemon.containers = {"naf-dev-x": (MAIN_ID, MAIN_ROOT, *_compose_project(child / "apps"))}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-x"]


def test_primary_restricted_and_dir_down_share_the_nested_evidence(daemon, one_stack, tmp_path):
    repo = _main(tmp_path)
    child = _nested_child(repo, "registered")
    daemon.registered = {os.path.realpath(child)}
    daemon.containers = {
        "naf-dev-app": (MAIN_ID, MAIN_ROOT),
        "naf-dev-child": ("", "", *_compose_project(child / "apps" / "api")),
    }
    assert deploy.action_stop(TAGGED, repo_root=repo, selection=SELECTION) == 0
    assert daemon.stopped == ["naf-dev-app"]
    daemon.stopped.clear()
    assert deploy.stop_stack(repo, _stack_dir(repo)) == 0
    assert daemon.stopped == ["naf-dev-app"]


def test_primary_down_leaves_an_outside_registered_worktrees_container(daemon, tmp_path, capsys):
    """`git worktree add ../x` lives outside the primary's root; the registry
    still proves the container is that worktree's."""
    (tmp_path / "main").mkdir()
    repo = _main(tmp_path / "main")
    sibling = tmp_path / "sibling"
    daemon.registered = {os.path.realpath(sibling)}
    daemon.containers = {
        "naf-dev-app": (MAIN_ID, MAIN_ROOT),
        "naf-dev-sib": ("", "", *_compose_project(sibling / "apps" / "api")),
    }
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-app"]
    assert "NOT stopping naf-dev-sib: unlabelled, and its compose project" in capsys.readouterr().out


def test_primary_down_treats_an_outside_unregistered_path_by_the_legacy_rule(daemon, tmp_path):
    """No registry entry and outside the root: no positive evidence (a moved
    checkout's old path looks the same), so CIU-191's rule applies."""
    (tmp_path / "main").mkdir()
    repo = _main(tmp_path / "main")
    other = tmp_path / "other"
    other.mkdir()
    (other / ".git").mkdir()
    daemon.containers = {"naf-dev-x": ("", "", *_compose_project(other / "apps"))}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-x"]


def test_primary_down_never_resolves_a_relative_path_against_the_cwd(daemon, tmp_path, monkeypatch):
    repo = _main(tmp_path)
    _nested_child(repo, "git")
    monkeypatch.chdir(repo)
    daemon.containers = {"naf-dev-x": ("", "", ".worktrees/child/apps", "")}
    assert deploy.action_stop(TAGGED, repo_root=repo) == 0
    assert daemon.stopped == ["naf-dev-x"]
