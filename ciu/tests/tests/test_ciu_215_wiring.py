"""CIU-215 -- engine/deploy/oneshot wiring of the git common-dir fragment."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ciu import config_model, deploy, engine, oneshot, worktree  # noqa: E402
from ciu.workspace_env import GENERATED_FACTS_KEYS, write_generated_facts  # noqa: E402
from test_ciu_engine_execution_boundaries import (  # noqa: E402
    GLOBAL_DEFAULTS, STACK_DEFAULTS, COMPOSE,
)

TABLE = '\n[demo.linked_worktree]\ngit_common_dir = "ro"\n'


def _repo(tmp_path, monkeypatch, table=TABLE):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "engine-boundary-net")
    for key in ("CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID"):
        monkeypatch.setenv(key, str(os.getuid() if key == "CONTAINER_UID" else os.getgid()))
    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "1")
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(GLOBAL_DEFAULTS)
    keys = ("REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID")
    (tmp_path / "ciu.env").write_text(
        "\n".join(f'export {k}="{os.environ[k]}"' for k in keys) + "\n")
    (tmp_path / ".gitignore").write_text("**/.ciu/\n")
    stack = tmp_path / "applications" / "demo"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text(
        STACK_DEFAULTS.replace('[demo.hooks]\npost_compose = ["./post.py"]\n', "") + table)
    (stack / "ciu.compose.yml.j2").write_text(COMPOSE)
    for argv in (["git", "init", "-b", "main"], ["git", "add", "."],
                 ["git", "-c", "user.name=T", "-c", "user.email=t@e.x", "commit", "-m", "x"]):
        subprocess.run(argv, cwd=tmp_path, capture_output=True, check=True)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
    return stack


def _prepare(stack, tmp_path):
    return engine.prepare_stack(working_dir=stack, define_root=tmp_path, skip_hostdir_check=True)


def test_primary_checkout_and_absent_table_render_identically(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(
        worktree, "git_common_dir_mount",
        lambda _r: pytest.fail("a primary checkout must not derive a mount"))
    with_table = _prepare(stack, tmp_path)
    assert with_table.file_args == ["-f", engine.CIU_COMPOSE_OUTPUT]
    assert not (stack / ".ciu" / engine.GITDIR_OVERLAY_NAME).exists()
    rendered = (stack / engine.CIU_COMPOSE_OUTPUT).read_bytes()
    (stack / "ciu.defaults.toml.j2").write_text(
        (stack / "ciu.defaults.toml.j2").read_text().replace(TABLE, ""))
    without = _prepare(stack, tmp_path)
    assert (stack / engine.CIU_COMPOSE_OUTPUT).read_bytes() == rendered
    assert without.file_args == with_table.file_args


def test_linked_without_the_table_writes_no_fragment(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch, table="")
    monkeypatch.setattr(worktree, "is_linked_git_worktree", lambda _r: True)
    assert _prepare(stack, tmp_path).file_args == ["-f", engine.CIU_COMPOSE_OUTPUT]
    assert not (stack / ".ciu" / engine.GITDIR_OVERLAY_NAME).exists()


@pytest.mark.parametrize("managed", [False, True])
def test_linked_worktree_appends_the_fragment_last(tmp_path, monkeypatch, managed, capsys):
    stack = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(worktree, "is_linked_git_worktree", lambda _r: True)
    seen = {}

    def fake_write(stack_dir, text, table, mount):
        seen["table"], seen["mount"] = table, mount
        path = Path(stack_dir) / ".ciu" / engine.GITDIR_OVERLAY_NAME
        path.parent.mkdir(exist_ok=True)
        path.write_text("services: {}\n")
        return path

    monkeypatch.setattr(engine, "write_gitdir_overlay", fake_write)
    monkeypatch.setattr(worktree, "git_common_dir_mount", lambda root: ("mount", root))
    if managed:
        monkeypatch.setattr(
            engine, "workspace_ownership_labels",
            lambda _r: {engine.OWNERSHIP_LABEL_INSTANCE: "i1",
                        engine.OWNERSHIP_LABEL_REPO_ROOT: "/p"})
    prepared = _prepare(stack, tmp_path)
    expected = ["-f", engine.CIU_COMPOSE_OUTPUT]
    if managed:
        expected += ["-f", f".ciu/{engine.OWNERSHIP_OVERLAY_NAME}"]
    expected += ["-f", f".ciu/{engine.GITDIR_OVERLAY_NAME}"]
    assert prepared.file_args == expected
    assert seen["table"] == config_model.LinkedWorktreeConfig("ro", None)
    assert seen["mount"][0] == "mount"
    assert "[CIU-215] git common dir mounted (ro)" in capsys.readouterr().out


def test_a_derivation_failure_refuses_with_a_value_error(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(worktree, "is_linked_git_worktree", lambda _r: True)

    def boom(_root):
        raise worktree.WorktreeError("[CIU-215] disagree")

    monkeypatch.setattr(worktree, "git_common_dir_mount", boom)
    with pytest.raises(ValueError, match=r"\[CIU-215\] disagree"):
        _prepare(stack, tmp_path)


def test_an_invalid_table_refuses_at_render_even_in_a_primary_checkout(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch, table='\n[demo.linked_worktree]\ngit_common_dir = "x"\n')
    with pytest.raises(ValueError, match=r"\[CIU-215\].*\"ro\" or \"rw\""):
        _prepare(stack, tmp_path)


def test_a_shipped_stack_refuses_the_table_but_not_its_absence(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch)
    (stack / "docker-compose.yml").write_text("services: {a: {image: x}}\n")
    with pytest.raises(ValueError, match=r"\[CIU-215\].*--shipped"):
        engine.run_shipped(stack, define_root=tmp_path, dry_run=True, generate_env=True)
    # a table-free stack config, and a stack with no config at all, still run
    (stack / "ciu.defaults.toml.j2").write_text("[demo]\nname = 'x'\n")
    assert engine.run_shipped(stack, define_root=tmp_path, dry_run=True, generate_env=True)["status"] == "success"
    (stack / "ciu.defaults.toml.j2").unlink()
    assert engine.run_shipped(stack, define_root=tmp_path, dry_run=True, generate_env=True)["status"] == "success"


def test_a_shipped_stack_config_that_only_mentions_the_name_passes(tmp_path, monkeypatch):
    stack = _repo(tmp_path, monkeypatch, table="")
    (stack / "docker-compose.yml").write_text("services: {a: {image: x}}\n")
    (stack / "ciu.defaults.toml.j2").write_text(
        "[demo]\nnote = 'linked_worktree is only text here'\n")
    assert engine.run_shipped(stack, define_root=tmp_path, dry_run=True, generate_env=True)["status"] == "success"


def test_reset_passes_the_fragment_to_down_and_removes_it(tmp_path, monkeypatch):
    facts = {key: "" for key in GENERATED_FACTS_KEYS}
    facts["repo_name"], facts["instance_id"] = "r", "abc123"
    write_generated_facts(tmp_path, facts)
    machine = tmp_path / ".ciu"
    (machine / "gitdir").mkdir(parents=True)
    (machine / "gitdir" / "0.config").write_text("x")
    (machine / engine.GITDIR_OVERLAY_NAME).write_text("services: {}\n")
    seen = []

    def run(cmd, **_kw):
        seen.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(engine.procutil, "run_cmd", run)
    config = {"deploy": {"project_name": "project", "labels": {"prefix": "ciu"}}}
    engine.reset_service(config, tmp_path, assume_yes=True, repo_root=tmp_path)
    down = next(c for c in seen if "down" in c)
    assert str(tmp_path.resolve() / ".ciu" / engine.GITDIR_OVERLAY_NAME) in down
    assert not (machine / engine.GITDIR_OVERLAY_NAME).exists()
    assert not (machine / "gitdir").exists()


def test_a_oneshot_passes_the_fragment_through_unchanged(tmp_path):
    prepared = SimpleNamespace(
        file_args=["-f", "ciu.compose.yml", "-f", f".ciu/{engine.GITDIR_OVERLAY_NAME}"],
        ownership_path=None, working_dir=tmp_path)
    got = oneshot._compose_file_args(
        prepared, temp_dir=tmp_path, extend=None, service_name="runner")
    assert got == prepared.file_args


def test_ciu_check_reports_an_invalid_table_under_the_shape_stage():
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    assert re.search(r"validate_linked_worktree_table\(merged\.get\(root_key", source)
    with pytest.raises(ValueError, match="CIU-215"):
        config_model.validate_linked_worktree_table({"linked_worktree": {"git_common_dir": "no"}})


def test_check_reports_an_invalid_table_in_the_shape_stage(monkeypatch, tmp_path):
    """`ciu check` reports the engine's own refusal without rendering."""
    from types import SimpleNamespace as NS
    profile = NS(config={"deploy": {}}, name="test")
    merged = {"app": {"linked_worktree": {"git_common_dir": "flat"}}}
    monkeypatch.setattr(deploy.config_model, "deep_merge", lambda *_a, **_k: merged)
    monkeypatch.setattr(deploy.secret_directives, "discover", lambda *_a, **_k: [])
    monkeypatch.setattr(deploy.secret_directives, "find_misplaced", lambda *_a, **_k: [])
    monkeypatch.setattr(deploy.config_model, "find_secret_shaped_keys", lambda *_a, **_k: [])
    monkeypatch.setattr(deploy.governance_mod, "resolve_stack_governance", lambda *_a, **_k: None)
    report = deploy._CheckReport()
    deploy._check_stack_config(
        rel="tools/app", stack_cfg={"app": {}}, profile=profile, repo_root=tmp_path,
        root_key="app", ciu_context={}, identity={}, identity_unreadable=False,
        report=report, cache={},
    )
    finding = report._stages["shape"]["findings"][0]["message"]
    assert report._stages["shape"]["status"] == "fail"
    assert finding.startswith("[CIU-215]") and "flat" in finding


def test_the_consumers_example_parses_with_the_shipped_loader():
    import tomllib
    docs = Path(__file__).resolve().parents[2] / "docs"
    consumers = (docs / "CONSUMERS.md").read_text(encoding="utf-8")
    section = consumers.split("## 5f.", 1)[1].split("## 6.", 1)[0]
    block = re.search(r"```toml\n(.*?)```", section, re.S).group(1)
    parsed = tomllib.loads(block)
    assert config_model.validate_linked_worktree_table(parsed["test_runner"]) == (
        config_model.LinkedWorktreeConfig("ro", None))
    for name in ("SPEC.md", "CONFIG.md"):
        text = (docs / name).read_text(encoding="utf-8")
        assert "linked_worktree" in text and '"ro"' in text and '"rw"' in text
