"""CIU-201 -- test-strength closeout of the ciu 7.23.0 R2 mutation survivors.

Every test asserts an observable behaviour. Docker is replaced by a fake
``docker`` executable on PATH (a real child process, so the production
``capture``/``check``/``text`` handling is exercised, not bypassed); nothing here
touches a real daemon, network or the host.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ciu import config_model, deploy, engine, workspace_env  # noqa: E402


_FAKE_DOCKER = """#!{python}
import json, sys
rules = json.load(open({cfg!r}))
key = " ".join(sys.argv[1:])
open({log!r}, "a").write(key + "\\n")
for prefix, rc, out, err in rules:
    if key.startswith(prefix):
        sys.stdout.write(out)
        sys.stderr.write(err)
        sys.exit(rc)
sys.exit(0)
"""


def _fake_docker(tmp_path, monkeypatch, rules):
    """Install a ``docker`` that answers the first rule whose prefix matches argv."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    cfg, log = tmp_path / "fake-docker.json", tmp_path / "fake-docker.log"
    cfg.write_text(json.dumps(rules), encoding="utf-8")
    exe = bindir / "docker"
    exe.write_text(
        _FAKE_DOCKER.format(python=sys.executable, cfg=str(cfg), log=str(log)),
        encoding="utf-8",
    )
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return log


def _owner(instance="I", root="/r", is_instance=False):
    return deploy._CleanOwner(instance_id=instance, repo_root=root, is_instance=is_instance)


# ---------------------------------------------------------------------------
# deploy.py: the clean owner record
# ---------------------------------------------------------------------------


def test_clean_owner_is_an_immutable_record_that_is_attributable_by_default():
    owner = _owner()
    assert owner.unattributable is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        owner.instance_id = "other"  # type: ignore[misc]


def test_clean_owner_reads_outdated_facts_without_repairing_them(tmp_path, monkeypatch):
    path = workspace_env.generated_facts_path(tmp_path)
    lines = workspace_env.render_generated_facts_block({
        "repo_name": "vbpub", "instance_id": "ab12cd", "network": "vbpub-ab12cd-network",
        "physical_repo_root": "/host/vbpub", "repo_root": "/workspaces/vbpub",
        "public_fqdn": "example.test",
    })
    path.write_text(
        "\n".join(x for x in lines if not x.startswith("schema_version = ")) + "\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    monkeypatch.setattr(deploy, "_is_worktree_instance", lambda _root: True)
    monkeypatch.setattr(workspace_env, "_old_identity_resources_exist", lambda _f: False)
    monkeypatch.setattr(
        workspace_env, "generate_ciu_env",
        lambda *_a, **_k: pytest.fail("a read-only owner lookup must never repair facts"),
    )
    owner = deploy._clean_owner(tmp_path)
    assert owner is not None and owner.is_instance and owner.instance_id is None
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# deploy.py: ownership-filtered listings run real subprocesses
# ---------------------------------------------------------------------------


def test_owned_names_keeps_owned_and_reports_foreign_with_reason(tmp_path, monkeypatch):
    _fake_docker(tmp_path, monkeypatch, [
        ["ps", 0, "mine\tI\t/r\nforeign\tOTHER\t/r\n", ""],
    ])
    skipped: list[tuple[str, str]] = []
    kept = deploy._owned_names(["ps"], _owner(), skipped, what="docker ps")
    assert kept == ["mine"]
    assert skipped == [("foreign", "owned by another instance (OTHER)")]


def test_owned_names_refuses_a_failed_listing_with_its_own_error(tmp_path, monkeypatch):
    _fake_docker(tmp_path, monkeypatch, [["ps", 1, "", "daemon unreachable\n"]])
    with pytest.raises(ValueError, match=r"\[S6.4a\] docker ps failed: daemon unreachable"):
        deploy._owned_names(["ps"], _owner(), [], what="docker ps")


def test_owned_names_tolerates_lines_with_fewer_than_three_fields(tmp_path, monkeypatch):
    # name only; name + instance label only (no repo-root label)
    _fake_docker(tmp_path, monkeypatch, [["ps", 0, "bare\nhalf\tI\n", ""]])
    skipped: list = []
    assert deploy._owned_names(["ps"], _owner(), skipped, what="docker ps") == ["bare", "half"]
    assert skipped == []


def test_endpoint_ownership_is_read_from_the_inspect_labels(tmp_path, monkeypatch):
    _fake_docker(tmp_path, monkeypatch, [["inspect", 0, "I\t/r\n", ""]])
    assert deploy._endpoint_foreign_reason("c1", _owner(is_instance=True)) is None
    _fake_docker(tmp_path, monkeypatch, [["inspect", 0, "OTHER\t/r\n", ""]])
    assert "another instance (OTHER)" in deploy._endpoint_foreign_reason("c1", _owner())


def test_endpoint_with_only_an_instance_label_is_still_judged(tmp_path, monkeypatch):
    _fake_docker(tmp_path, monkeypatch, [["inspect", 0, "I\n", ""]])
    assert deploy._endpoint_foreign_reason("c1", _owner()) is None


def test_endpoint_that_cannot_be_inspected_is_foreign_not_an_exception(tmp_path, monkeypatch):
    _fake_docker(tmp_path, monkeypatch, [["inspect", 1, "", "no such container\n"]])
    assert deploy._endpoint_foreign_reason("c1", _owner()) == "could not be inspected"


def test_network_ownership_is_read_from_its_labels(tmp_path, monkeypatch):
    own = json.dumps([{"Labels": {"ciu.instance": "I", "ciu.repo-root": "/r"}}])
    _fake_docker(tmp_path, monkeypatch, [["network inspect", 0, own, ""]])
    assert deploy._network_foreign_reason("n", _owner()) is None
    other = json.dumps([{"Labels": {"ciu.instance": "OTHER"}}])
    _fake_docker(tmp_path, monkeypatch, [["network inspect", 0, other, ""]])
    assert "another instance (OTHER)" in deploy._network_foreign_reason("n", _owner())


def test_network_that_cannot_be_inspected_is_foreign_not_an_exception(tmp_path, monkeypatch):
    _fake_docker(tmp_path, monkeypatch, [["network inspect", 1, "", "no such network\n"]])
    assert deploy._network_foreign_reason("n", _owner()) == "could not be inspected"


# ---------------------------------------------------------------------------
# deploy.py: the clean plan
# ---------------------------------------------------------------------------

_CONFIG = {"deploy": {"project_name": "p", "environment_tag": "e"}}


def _plan(tmp_path, owner):
    deploy._print_clean_plan(
        tmp_path, _CONFIG, [], tagged=True, owner=owner, name_owner=None,
    )


def test_clean_plan_lists_stopped_containers_too(tmp_path, monkeypatch, capsys):
    _fake_docker(tmp_path, monkeypatch, [
        ["ps -a", 0, "p-e-running\np-e-exited\n", ""],
        ["ps ", 0, "p-e-running\n", ""],
    ])
    _plan(tmp_path, None)
    out = capsys.readouterr().out
    assert "containers to remove (2): p-e-running, p-e-exited" in out


def test_clean_plan_names_the_known_root_and_marks_an_unknown_one(tmp_path, monkeypatch, capsys):
    _fake_docker(tmp_path, monkeypatch, [])
    _plan(tmp_path, _owner(root="/the/root"))
    assert "identity: instance I, root /the/root" in capsys.readouterr().out
    _plan(tmp_path, _owner(root=None))
    assert "identity: instance I, root (unknown)" in capsys.readouterr().out


def test_clean_dry_run_reaches_the_cleanup_in_plan_only_mode(monkeypatch, tmp_path):
    from test_ciu105_protected_teardown import _wire_main

    _wire_main(monkeypatch, tmp_path, protected=True)
    seen: list[dict] = []
    monkeypatch.setattr(
        deploy, "action_clean", lambda *_a, **kw: seen.append(kw) or 0,
    )
    # a dry run removes nothing, so the protected-instance gate does not apply
    assert deploy.main(["--clean", "--dry-run"]) == 0
    assert seen and seen[0].get("plan_only") is True


# ---------------------------------------------------------------------------
# deploy.py: linked-worktree detection runs real git
# ---------------------------------------------------------------------------


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True,
    )


def test_linked_git_worktree_is_detected_with_real_git(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(["init", "-b", "main"], main)
    _git(["-c", "user.email=t@e.x", "-c", "user.name=T", "commit", "--allow-empty",
          "-m", "init"], main)
    linked = tmp_path / "linked"
    _git(["worktree", "add", "-b", "wt", str(linked)], main)
    assert deploy._is_linked_git_worktree(linked) is True
    assert deploy._is_linked_git_worktree(main) is False
    assert deploy._is_linked_git_worktree(tmp_path / "not-a-repo-dir-yet") is False


# ---------------------------------------------------------------------------
# engine.py
# ---------------------------------------------------------------------------


def test_unverified_slice_warning_is_flushed(monkeypatch):
    monkeypatch.setenv("CGROUP_PARENT_DEV_INTERACTIVE", "dev-interactive.slice")
    monkeypatch.setattr(
        engine.governance, "check_slice_unit", lambda _n: (None, "systemd is not PID 1"),
    )
    monkeypatch.setattr(engine.governance, "slice_probe_unavailable", lambda: True)
    calls = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: calls.append((a, k)))
    assert engine._root_helper_cgroup_parent() == "dev-interactive.slice"
    assert len(calls) == 1 and calls[0][1].get("flush") is True


def test_ownership_scan_does_not_follow_a_symlink_out_of_the_tree(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f").write_text("x", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link").symlink_to(outside, target_is_directory=True)
    real_lstat = os.lstat

    def lstat(path, *a, **k):
        info = real_lstat(path, *a, **k)
        if str(path).endswith(os.sep + "link" + os.sep + "f"):  # only reachable by following
            return SimpleNamespace(st_uid=os.getuid() + 1, st_mode=info.st_mode)
        return info

    monkeypatch.setattr(engine.os, "lstat", lstat)
    assert engine._tree_owned_by_caller(tree) is True


def test_make_writable_does_not_follow_a_symlink_out_of_the_tree(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    locked = outside / "f"
    locked.write_text("x", encoding="utf-8")
    locked.chmod(0o444)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link").symlink_to(outside, target_is_directory=True)
    engine._make_tree_writable(tree)
    assert locked.stat().st_mode & 0o777 == 0o444


# ---------------------------------------------------------------------------
# workspace_env.py: the own-container inspect runs a real child process
# ---------------------------------------------------------------------------


def _own_id(monkeypatch):
    monkeypatch.setattr(workspace_env, "_own_container_id", lambda: "c" * 64)


def test_own_container_name_comes_from_the_inspect_output(tmp_path, monkeypatch, capsys):
    _own_id(monkeypatch)
    _fake_docker(tmp_path, monkeypatch, [["inspect", 0, "/real-name\n", ""]])
    assert workspace_env._resolve_own_container_name("stale") == "real-name"


def test_failed_own_inspect_warns_with_the_docker_error_and_keeps_the_name(
    tmp_path, monkeypatch, capsys,
):
    _own_id(monkeypatch)
    _fake_docker(tmp_path, monkeypatch, [["inspect", 1, "", "Error: permission denied\n"]])
    assert workspace_env._resolve_own_container_name("x") == "x"
    assert "(Error: permission denied)" in capsys.readouterr().out


def test_failed_own_inspect_without_stderr_says_no_name_was_returned(
    tmp_path, monkeypatch, capsys,
):
    _own_id(monkeypatch)
    _fake_docker(tmp_path, monkeypatch, [["inspect", 1, "", ""]])
    assert workspace_env._resolve_own_container_name("x") == "x"
    assert "(no container name returned)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# config_model.py: the linked-worktree hint needs BOTH path components
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pointer", [".git/other/wt", "notgit/worktrees/wt"])
def test_hint_needs_a_dot_git_worktrees_pointer(tmp_path, pointer):
    primary = tmp_path / "primary"
    (primary / pointer).mkdir(parents=True)
    (primary / ".env.guard").write_text("X=1\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {primary / pointer}\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        config_model.expand_env_vars_or_fail(
            'key = "$NAF_GUARD_USER"\n', str(linked / "t.j2"), environ={},
        )
    assert "NAF_GUARD_USER" in str(excinfo.value)
    assert "linked worktree" not in str(excinfo.value)
