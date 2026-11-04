"""CIU-168..CIU-174: the remote push-deploy and worktree defects found in the dstdns P8 path.

The remote-verb dispatch contracts (CIU-168/169/170) live beside their siblings in
``test_ciu_cli_remote_dispatch.py``; the clean-network contracts (CIU-171) in
``test_ciu_clean_identity_networks.py``. This module holds the rest: the S1.7 guard on a
bundle without a usable repository (CIU-169), the declared project-image scoping
(CIU-173), the worktree allocation remedies (CIU-174) and the unborn-HEAD refusal
(CIU-168).
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine, image_isolation as iso, worktree, workspace_env  # noqa: E402


def _replies(items):
    it = iter(items)
    return lambda *a, **k: next(it)


def _result(returncode: int, stdout: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# CIU-169: S1.7 on a pushed bundle whose git metadata is not usable
# ---------------------------------------------------------------------------


@pytest.fixture
def git_says_unignored(monkeypatch, tmp_path):
    """`git` reports a work tree in which the probe path is NOT ignored; the bundle root is tmp_path."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    replies = iter((_result(0, "true\n"), _result(1), _result(1)))
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *a, **k: next(replies))


@pytest.mark.parametrize("pattern", ["**/.ciu/", ".ciu/", "/.ciu", ".ciu"])
def test_gitignore_text_in_the_stack_dir_satisfies_the_guard(git_says_unignored, tmp_path, pattern):
    stack = tmp_path / "whoami"
    stack.mkdir()
    (stack / ".gitignore").write_text(f"node_modules/\n  {pattern}  \n", encoding="utf-8")
    engine._check_gitignore(stack)


def test_gitignore_text_above_the_stack_dir_satisfies_the_guard(git_says_unignored, tmp_path):
    stack = tmp_path / "apps" / "whoami"
    stack.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text("**/.ciu/\n", encoding="utf-8")
    engine._check_gitignore(stack)


def test_gitignore_above_the_bundle_root_never_satisfies_the_guard(git_says_unignored, tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    stack = bundle / "apps" / "whoami"
    stack.mkdir(parents=True)
    monkeypatch.setenv("REPO_ROOT", str(bundle))
    (tmp_path / ".gitignore").write_text("**/.ciu/\n", encoding="utf-8")  # outside the bundle
    with pytest.raises(ValueError, match="not gitignored"):
        engine._check_gitignore(stack)
    # ...but the same file INSIDE the bundle root does
    (bundle / ".gitignore").write_text("**/.ciu/\n", encoding="utf-8")
    assert engine._gitignore_text_ignores(stack, bundle) is True


def test_gitignore_without_a_bundle_root_trusts_only_the_stacks_own_file(monkeypatch, tmp_path):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setattr(engine.procutil, "run_cmd",
                        _replies((_result(0, "true\n"), _result(1), _result(1))))
    stack = tmp_path / "apps" / "whoami"
    stack.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text(".ciu\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not gitignored"):
        engine._check_gitignore(stack)


def test_a_later_negation_unignores_and_a_rooted_ancestor_pattern_does_not_apply(tmp_path):
    stack = tmp_path / "apps" / "whoami"
    stack.mkdir(parents=True)
    ignores = engine._gitignore_text_ignores
    (tmp_path / ".gitignore").write_text("/.ciu\n", encoding="utf-8")  # rooted at tmp_path only
    assert ignores(stack, tmp_path) is False
    (tmp_path / ".gitignore").write_text("**/.ciu/\n", encoding="utf-8")
    assert ignores(stack, tmp_path) is True
    (stack / ".gitignore").write_text("!.ciu\n", encoding="utf-8")  # deeper negation wins
    assert ignores(stack, tmp_path) is False
    (stack / ".gitignore").write_text(".ciu/\n/.ciu\n!.ciu\n.ciu\n", encoding="utf-8")  # order inside a file
    assert ignores(stack, tmp_path) is True
    (stack / ".gitignore").write_text("/.ciu\n", encoding="utf-8")  # rooted pattern in the stack's own file
    assert ignores(stack, tmp_path) is True


def test_a_bundle_root_that_is_not_an_ancestor_only_trusts_the_stack_file(tmp_path):
    stack = tmp_path / "a"
    stack.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    (tmp_path / ".gitignore").write_text(".ciu\n", encoding="utf-8")
    assert engine._gitignore_text_ignores(stack, other) is False
    assert engine._gitignore_text_ignores(stack, stack) is False


def test_unrelated_gitignore_lines_do_not_satisfy_the_guard(git_says_unignored, tmp_path):
    (tmp_path / ".git").mkdir()
    stack = tmp_path / "whoami"
    stack.mkdir()
    (stack / ".gitignore").write_text("*.log\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not gitignored"):
        engine._check_gitignore(stack)


def test_unreadable_gitignore_text_is_treated_as_absent_and_the_walk_stops_at_the_repo(
    git_says_unignored, tmp_path,
):
    (tmp_path / ".git").mkdir()
    stack = tmp_path / "whoami"
    stack.mkdir()
    (stack / ".gitignore").write_bytes(b"\xff\xfe\x00 not utf-8")
    with pytest.raises(ValueError, match="not gitignored"):
        engine._check_gitignore(stack)


# ---------------------------------------------------------------------------
# CIU-173: declared project images are scoped without a build: stanza
# ---------------------------------------------------------------------------

PULLED_ONLY = (
    "services:\n"
    "  api:\n    image: dstdns/api:local\n"
    "  db:\n    image: postgres:16\n"
)
DECLARED = frozenset({"dstdns/api:local"})


def test_compose_uses_images_matches_normalized_references():
    assert iso.compose_uses_images(PULLED_ONLY, DECLARED)
    assert iso.compose_uses_images("services:\n  a:\n    image: dstdns/api\n", {"dstdns/api:latest"})
    assert not iso.compose_uses_images(PULLED_ONLY, {"dstdns/other:local"})
    assert not iso.compose_uses_images("services:\n  a: {}\n", DECLARED)
    assert not iso.compose_uses_images("services: []\n", DECLARED)
    assert not iso.compose_uses_images("[]\n", DECLARED)
    assert not iso.compose_uses_images("", DECLARED)
    assert not iso.compose_uses_images("services:\n  a: not-a-mapping\n", DECLARED)
    assert not iso.compose_uses_images("services:\n  a:\n    image: 3\n", DECLARED)


def test_scope_compose_images_scopes_declared_images_only():
    scoped = iso.scope_compose_images(PULLED_ONLY, "abc123", DECLARED)
    assert "image: dstdns/api:local-abc123" in scoped
    assert "image: postgres:16\n" in scoped
    # without the declaration nothing is a project build: byte-for-byte unchanged
    assert iso.scope_compose_images(PULLED_ONLY, "abc123") == PULLED_ONLY
    # a declared image no service uses changes nothing
    assert iso.scope_compose_images(PULLED_ONLY, "abc123", frozenset({"dstdns/x:local"})) == PULLED_ONLY


def test_scope_compose_images_declared_image_skips_services_without_a_plain_image():
    source = (
        "services:\n"
        "  api:\n    image: dstdns/api:local\n"
        "  tool:\n    command: ['true']\n"
        "  blank:\n    image: ''\n"
    )
    scoped = iso.scope_compose_images(source, "abc123", DECLARED)
    assert "image: dstdns/api:local-abc123" in scoped


def test_project_images_key_is_validated():
    worktree._validate_worktree_table({"project_images": ["dstdns/api:local"]})
    worktree._validate_worktree_table({"project_images": []})
    for bad in ("dstdns/api:local", [1], [""], ["  "]):
        with pytest.raises(worktree.WorktreeError, match=r"\[CIU-173\].*project_images"):
            worktree._validate_worktree_table({"project_images": bad})


def test_declared_project_images_reads_the_effective_config():
    assert worktree.declared_project_images({}) == frozenset()
    assert worktree.declared_project_images({"ciu": "not-a-table"}) == frozenset()
    assert worktree.declared_project_images({"ciu": {"worktree": None}}) == frozenset()
    assert worktree.declared_project_images({"ciu": {"worktree": {"up": ["a"]}}}) == frozenset()
    config = {"ciu": {"worktree": {"project_images": [" dstdns/api:local ", "dstdns/web:local"]}}}
    assert worktree.declared_project_images(config) == {"dstdns/api:local", "dstdns/web:local"}
    with pytest.raises(worktree.WorktreeError, match="CIU-173"):
        worktree.declared_project_images({"ciu": {"worktree": {"project_images": "x"}}})


def test_image_scope_kwargs_passes_only_a_non_empty_declaration():
    assert worktree.image_scope_kwargs({}) == {}
    config = {"ciu": {"worktree": {"project_images": ["dstdns/api:local"]}}}
    assert worktree.image_scope_kwargs(config) == {"declared_images": DECLARED}


def test_linked_worktree_scopes_declared_images_of_a_stack_without_build(monkeypatch, tmp_path, capsys):
    """CIU-173: dstdns-shape compose (image: only) no longer runs main's `:local`."""
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda _root: "abc123")
    scoped = engine.scope_worktree_compose_images(tmp_path, PULLED_ONLY, declared_images=DECLARED)
    assert "image: dstdns/api:local-abc123" in scoped
    assert "image: postgres:16\n" in scoped
    assert "suffix 'abc123'" in capsys.readouterr().err


def test_primary_checkout_keeps_declared_images_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda _root: None)
    assert engine.scope_worktree_compose_images(tmp_path, PULLED_ONLY, declared_images=DECLARED) == PULLED_ONLY


def test_unused_or_absent_declaration_never_resolves_the_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix",
        lambda _root: pytest.fail("a stack using no declared image needs no worktree identity"),
    )
    assert engine.scope_worktree_compose_images(tmp_path, PULLED_ONLY) == PULLED_ONLY
    assert engine.scope_worktree_compose_images(
        tmp_path, PULLED_ONLY, declared_images=frozenset({"dstdns/unused:local"}),
    ) == PULLED_ONLY


def test_declaration_applies_next_to_build_declared_images(monkeypatch, tmp_path):
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda _root: "abc123")
    source = (
        "services:\n"
        "  built:\n    image: team/app:dev\n    build: .\n"
        "  web:\n    image: dstdns/web:local\n"
    )
    scoped = engine.scope_worktree_compose_images(
        tmp_path, source, declared_images=frozenset({"dstdns/web:local"}),
    )
    assert "team/app:dev-abc123" in scoped and "dstdns/web:local-abc123" in scoped


# ---------------------------------------------------------------------------
# CIU-174: a failed allocation names its way out
# ---------------------------------------------------------------------------


def _create_env(monkeypatch, shared):
    monkeypatch.setattr(worktree, "_allocation_lock", lambda _root: nullcontext())
    monkeypatch.setattr(worktree, "_ciu_root_offset", lambda _root: Path("."))
    monkeypatch.setattr(worktree, "primary_worktree_root", lambda root: root)
    monkeypatch.setattr(worktree, "find_instance_record", lambda *_a, **_k: None)
    monkeypatch.setattr(worktree, "_ensure_record_is_excluded", lambda *_a: None)
    monkeypatch.setattr(worktree, "_branch_exists", lambda *_a: False)
    monkeypatch.setattr(worktree, "list_worktrees", lambda *_a: [])
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(worktree, "_physical_workspace_target", lambda _root, target: target)
    monkeypatch.setattr(worktree, "_write_instance_record", lambda _record: None)
    monkeypatch.setattr(
        worktree, "_git",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="a" * 40, stderr=""),
    )


def test_stale_shared_record_refusal_names_ensure_and_rm(monkeypatch, tmp_path):
    def refuse(*_a, **_k):
        raise RuntimeError(f"physical workspace path is already recorded: {tmp_path}/x")
    _create_env(monkeypatch, SimpleNamespace(create_workspace=refuse))
    with pytest.raises(worktree.WorktreeError) as raised:
        worktree.create(tmp_path, "demo", path=tmp_path / ".worktrees" / "demo")
    text = str(raised.value)
    assert "already recorded" in text
    assert "`ciu worktree ensure demo`" in text and "`ciu worktree rm demo`" in text
    assert "git worktree remove" in text


def test_other_allocation_failures_carry_no_stale_record_remedy():
    assert worktree._allocation_remedy(RuntimeError("allocator"), "demo") == ""


def test_failed_preparation_keeps_the_partial_allocation_and_says_how_to_resume_or_drop(
    monkeypatch, tmp_path, capsys,
):
    _create_env(monkeypatch, SimpleNamespace(create_workspace=lambda *_a, **_k: "context"))

    def fail(*_a, **_k):
        raise worktree.WorktreeError("[S16] DOCKER_GID not found")
    monkeypatch.setattr(worktree, "_finish_allocation", fail)
    with pytest.raises(worktree.WorktreeError, match="DOCKER_GID not found"):
        worktree.create(tmp_path, "demo", path=tmp_path / ".worktrees" / "demo")
    err = capsys.readouterr().err
    assert "partial allocation" in err
    assert "`ciu worktree ensure demo`" in err and "`ciu worktree rm demo`" in err
    assert str(tmp_path / ".worktrees" / "demo") in err


# ---------------------------------------------------------------------------
# CIU-168: an unborn HEAD is a tagged error, not a traceback
# ---------------------------------------------------------------------------


def test_generate_ciu_env_reports_an_unborn_head_as_a_tagged_error(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workspace_env, "_detect_docker_gid", lambda: "990")
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda root: root)
    monkeypatch.setattr(workspace_env, "_detect_host_mdt_tmp", lambda: "")

    class Shared:
        @staticmethod
        def discover_git_context(_root):
            raise RuntimeError("git rev-parse HEAD failed (128)")

    from ciu import workspace as workspace_adapter
    monkeypatch.setattr(workspace_adapter, "_shared", lambda: Shared)
    with pytest.raises(workspace_env.WorkspaceEnvError) as raised:
        workspace_env.generate_ciu_env(tmp_path)
    text = str(raised.value)
    assert text.startswith("[S2.8] cannot derive the workspace identity")
    assert "git rev-parse HEAD failed (128)" in text
    assert "git commit --allow-empty" in text
