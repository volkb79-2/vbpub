"""CLI dispatch tests for the S16 worktree lifecycle."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli  # noqa: E402
from ciu import dev  # noqa: E402
from ciu import worktree as wt_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _patch_repo_root(monkeypatch, tmp_path):
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: tmp_path)


class TestWorktreeAddDispatch:
    def test_forwards_all_options(self, monkeypatch, capsys):
        seen = {}

        def fake_add(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
            shared_infra_ref_services,
        ):
            seen.update(
                repo_root=repo_root,
                name=name,
                base=base,
                profile=profile,
                worktree_dir=worktree_dir,
                shared_infra=shared_infra,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
                shared_infra_ref_services=shared_infra_ref_services,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        code = cli._worktree(
            [
                "add",
                "mypkg",
                "--base",
                "develop",
                "--profile",
                "core,db",
                "--worktree-dir",
                ".wt",
            ]
        )
        assert code == 0
        assert seen == {
            "repo_root": seen["repo_root"],
            "name": "mypkg",
            "base": "develop",
            "profile": "core,db",
            "worktree_dir": ".wt",
            "shared_infra": None,
            "shared_infra_services": None,
            "shared_infra_ref_projects": None,
            "shared_infra_ref_services": None,
        }
        assert "worktree ready:" in capsys.readouterr().out

    def test_worktree_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fake_add(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] boom")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        assert cli._worktree(["add", "mypkg"]) == 2
        assert "[S16] boom" in capsys.readouterr().err

    def test_add_json_refuses_missing_postcondition_record(self, monkeypatch, capsys):
        monkeypatch.setattr(wt_mod, "add", lambda *_a, **_kw: Path("/tmp/checkout"))
        monkeypatch.setattr(wt_mod, "find_instance_record", lambda *_a: None)
        assert cli._worktree(["add", "mypkg", "--json"]) == 2
        assert "no managed record" in capsys.readouterr().err

    def test_add_json_emits_managed_record(self, monkeypatch, capsys):
        record = _ready_record()
        monkeypatch.setattr(wt_mod, "add", lambda *_a, **_kw: record.git_worktree_path)
        monkeypatch.setattr(wt_mod, "find_instance_record", lambda *_a: record)
        assert cli._worktree(["add", "mypkg", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["operation"] == "add"


class TestWorktreeAddSharedInfraDispatch:
    def test_forwards_shared_infra_flags(self, monkeypatch, capsys):
        seen = {}

        def fake_add(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
            shared_infra_ref_services,
        ):
            seen.update(
                shared_infra=shared_infra,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
                shared_infra_ref_services=shared_infra_ref_services,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        code = cli._worktree(
            [
                "add",
                "mypkg",
                "--profile",
                "core,db",
                "--shared-infra",
                "primary",
                "--shared-infra-services",
                "api,worker",
                "--shared-infra-ref-projects",
                "idp-dev-idp,vault-dev-vault",
                "--shared-infra-ref-services",
                "vault,secrets=vault",
            ]
        )
        assert code == 0
        assert seen == {
            "shared_infra": "primary",
            "shared_infra_services": "api,worker",
            "shared_infra_ref_projects": "idp-dev-idp,vault-dev-vault",
            "shared_infra_ref_services": "vault,secrets=vault",
        }
        assert "worktree ready:" in capsys.readouterr().out

    def test_shared_infra_flags_default_to_none(self, monkeypatch):
        seen = {}

        def fake_add(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
            shared_infra_ref_services,
        ):
            seen.update(
                shared_infra=shared_infra,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
                shared_infra_ref_services=shared_infra_ref_services,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        cli._worktree(["add", "mypkg"])
        assert seen == {
            "shared_infra": None,
            "shared_infra_services": None,
            "shared_infra_ref_projects": None,
            "shared_infra_ref_services": None,
        }

    def test_partial_shared_infra_error_surfaces_as_exit_2(
        self, monkeypatch, capsys
    ):
        def fake_add(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16.1] partial group")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        assert cli._worktree(["add", "mypkg", "--shared-infra", "primary"]) == 2
        assert "partial group" in capsys.readouterr().err

    def test_ref_services_alone_reaches_the_all_or_nothing_refusal(
        self, monkeypatch, capsys
    ):
        """O12 (CLI half): the new flag is OPTIONAL but not standalone — it
        joins the existing all-or-nothing group, so supplying only it is
        forwarded and refused, never quietly accepted as a no-op."""
        seen = {}

        def fake_add(*_a, **kw):
            seen.update(kw)
            raise wt_mod.WorktreeError("[S16.1] partial group")

        monkeypatch.setattr(wt_mod, "add", fake_add)
        assert cli._worktree(
            ["add", "mypkg", "--shared-infra-ref-services", "vault"]
        ) == 2
        assert seen["shared_infra_ref_services"] == "vault"
        assert seen["shared_infra"] is None
        assert "partial group" in capsys.readouterr().err


class TestWorktreeRmDispatch:
    def test_forwards_yes_and_force(self, monkeypatch, capsys):
        seen = {}

        def fake_remove(repo_root, name, *, yes, force):
            seen.update(repo_root=repo_root, name=name, yes=yes, force=force)
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        assert cli._worktree(["rm", "mypkg", "-y", "--force"]) == 0
        assert seen["name"] == "mypkg"
        assert seen["yes"] is True and seen["force"] is True
        assert "removed:" in capsys.readouterr().out

    def test_defaults_yes_and_force_false(self, monkeypatch):
        seen = {}

        def fake_remove(repo_root, name, *, yes, force):
            seen.update(yes=yes, force=force)
            return Path("/tmp/x")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        cli._worktree(["rm", "mypkg"])
        assert seen == {"yes": False, "force": False}

    def test_worktree_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fake_remove(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] clean failed")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        assert cli._worktree(["rm", "mypkg"]) == 2
        assert "clean failed" in capsys.readouterr().err


class TestWorktreeListDispatch:
    def test_lists_primary_and_others(self, monkeypatch, capsys, tmp_path):
        primary = tmp_path / "repo"
        primary.mkdir()
        (primary / ".git").mkdir()
        linked = primary / ".worktrees" / "pkg"
        linked.mkdir(parents=True)
        (linked / ".git").write_text("gitdir: ../..\n", encoding="utf-8")

        monkeypatch.setattr(
            wt_mod,
            "list_worktrees",
            lambda repo_root: [
                wt_mod.WorktreeInfo(primary, "main", "abc12345"),
                wt_mod.WorktreeInfo(linked, "pkg", "def45678"),
            ],
        )
        assert cli._worktree(["list"]) == 0
        out = capsys.readouterr().out
        assert "(primary)" in out
        assert "pkg" in out

    def test_list_json_emits_managed_document(self, monkeypatch, capsys):
        doc = {
            "schema_version": 1,
            "operation": "list",
            "status": "ready",
            "instances": [],
        }
        monkeypatch.setattr(wt_mod, "list_instances", lambda *_a: doc)
        assert cli._worktree(["list", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == doc


class TestWorktreeInspectDispatch:
    def test_inspect_json_emits_document(self, monkeypatch, capsys):
        doc = {
            "schema_version": 1,
            "operation": "inspect",
            "status": "ready",
            "instance": {"logical_name": "logical-one"},
            "git": {"registered": True, "path": "/tmp/wt",
                    "branch": "main", "head": "abc12345", "dirty": False},
        }
        monkeypatch.setattr(wt_mod, "inspect_instance", lambda *_a, **_kw: doc)
        assert cli._worktree(["inspect", "logical-one", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == doc

    def test_inspect_prose_uses_document_facts(self, monkeypatch, capsys):
        doc = {
            "schema_version": 1,
            "operation": "inspect",
            "status": "ready",
            "instance": {"logical_name": "logical-one"},
            "git": {"registered": True, "path": "/tmp/wt", "branch": "main",
                    "head": "abc12345", "dirty": False, "detached": False,
                    "primary": False},
        }
        monkeypatch.setattr(wt_mod, "inspect_instance", lambda *_a, **_kw: doc)
        assert cli._worktree(["inspect", "logical-one"]) == 0
        out = capsys.readouterr().out
        assert "logical-one" in out
        assert "abc12345" in out

    def test_inspect_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fail(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] boom")

        monkeypatch.setattr(wt_mod, "inspect_instance", fail)
        assert cli._worktree(["inspect", "logical-one", "--json"]) == 2
        assert "[S16] boom" in capsys.readouterr().err


class TestWorktreeRmJsonDispatch:
    def test_rm_json_emits_removal_document(self, monkeypatch, capsys):
        doc = {
            "schema_version": 1,
            "operation": "remove",
            "status": "removed",
            "removed_path": "/tmp/wt",
        }
        monkeypatch.setattr(
            wt_mod, "remove_document", lambda *_a, **_kw: doc
        )
        assert cli._worktree(["rm", "mypkg", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == doc

    def test_rm_json_failure_emits_no_success_document(self, monkeypatch, capsys):
        def fail(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] clean failed")

        monkeypatch.setattr(wt_mod, "remove_document", fail)
        assert cli._worktree(["rm", "mypkg", "--json"]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "[S16] clean failed" in captured.err


class TestCapabilitiesDispatch:
    def test_capabilities_json_dispatch(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ciu", "capabilities", "--json"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["schema_version"] == 1
        assert doc["capabilities"] == [
            "worktree.branches.v1",
            "worktree.exec-local.v1",
            "worktree.exec-target.v1",
            "worktree.identity.v1",
            "worktree.inspect.v1",
            # ciu-P27: shipped together — reaping is only ever as safe as the
            # ownership signal it consults, so a consumer that allowlists one
            # needs the other.
            "worktree.lease.v1",
            "worktree.lifecycle-json.v1",
            "worktree.reap.v1",
            "worktree.up.v1",
        ]

    def test_capabilities_prose_dispatch(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ciu", "capabilities"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "worktree.identity.v1" in out
        assert "worktree.inspect.v1" in out


def _ready_record() -> wt_mod.WorktreeInstanceRecord:
    return wt_mod.WorktreeInstanceRecord(
        logical_name="task-one", display_name="ciu-20260817_123456-task",
        branch="ciu-20260817_123456-task",
        git_worktree_path=Path("/tmp/repo/.worktrees/ciu-20260817_123456-task"),
        ciu_root_offset=Path("."), created_at_utc="2026-08-17T12:34:56Z",
        base_ref="main", state="ready", instance_id="abc123",
        network="repo-abc123-network",
    )


class TestManagedLifecycleDispatch:
    def test_create_forwards_generated_and_advanced_identity(self, monkeypatch):
        seen = {}

        def fake_create(repo_root, logical_name, **kwargs):
            seen.update(logical_name=logical_name, **kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, "create", fake_create)
        assert cli._worktree([
            "create", "task-one", "--prefix", "ciu", "--feature", "exact-exec",
            "--branch", "advanced", "--path", "/tmp/advanced",
        ]) == 0
        assert seen["logical_name"] == "task-one"
        assert seen["prefix"] == "ciu"
        assert seen["feature"] == "exact-exec"
        assert seen["branch"] == "advanced"
        assert seen["path"] == Path("/tmp/advanced")

    def test_ensure_json_is_versioned(self, monkeypatch, capsys):
        monkeypatch.setattr(wt_mod, "ensure", lambda *_a, **_kw: _ready_record())
        assert cli._worktree(["ensure", "task-one", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert payload["operation"] == "ensure"
        assert payload["status"] == "ready"
        assert payload["instance"]["logical_name"] == "task-one"

    def test_adopt_forwards_only_explicit_target(self, monkeypatch):
        seen = {}

        def fake_adopt(repo_root, logical_name, path, **kwargs):
            seen.update(logical_name=logical_name, path=path, **kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, "adopt", fake_adopt)
        assert cli._worktree(["adopt", "task-one", "/tmp/existing"]) == 0
        assert seen["logical_name"] == "task-one"
        assert seen["path"] == "/tmp/existing"

    @pytest.mark.parametrize("verb", ["create", "ensure"])
    def test_ref_services_flag_forwards_on_create_and_ensure(
        self, monkeypatch, verb
    ):
        """O4/O9 (CLI half): registered on the shared create/ensure options
        function, so both lifecycle spellings accept it identically."""
        seen = {}

        def fake_lifecycle(repo_root, logical_name, **kwargs):
            seen.update(kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, verb, fake_lifecycle)
        assert cli._worktree([
            verb, "task-one", "--profile", "core",
            "--shared-infra", "primary",
            "--shared-infra-services", "api",
            "--shared-infra-ref-projects", "idp-dev-idp",
            "--shared-infra-ref-services", "secrets=vault",
        ]) == 0
        assert seen["shared_infra_ref_services"] == "secrets=vault"

    def test_ref_services_flag_forwards_on_adopt_and_defaults_to_none(
        self, monkeypatch
    ):
        seen = {}

        def fake_adopt(repo_root, logical_name, path, **kwargs):
            seen.update(kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, "adopt", fake_adopt)
        assert cli._worktree([
            "adopt", "task-one", "/tmp/existing", "--profile", "core",
            "--shared-infra", "primary",
            "--shared-infra-services", "api",
            "--shared-infra-ref-projects", "idp-dev-idp",
            "--shared-infra-ref-services", "vault",
        ]) == 0
        assert seen["shared_infra_ref_services"] == "vault"

        seen.clear()
        assert cli._worktree(["adopt", "task-two", "/tmp/existing"]) == 0
        assert seen["shared_infra_ref_services"] is None


class TestWorktreeUpExecDispatch:
    def test_up_forwards_logical_and_returns_exact_child_code(self, monkeypatch):
        seen = {}

        def fake_up(repo_root, logical):
            seen.update(repo_root=repo_root, logical=logical)
            return 17

        monkeypatch.setattr(wt_mod, "up_instance", fake_up)
        assert cli._worktree(["up", "logical-one"]) == 17
        assert seen["logical"] == "logical-one"
        assert seen["repo_root"] == seen["repo_root"]

    def test_up_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fail(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] not ready")

        monkeypatch.setattr(wt_mod, "up_instance", fail)
        assert cli._worktree(["up", "logical-one"]) == 2
        assert "not ready" in capsys.readouterr().err

    def test_exec_forwards_logical_and_argv_and_returns_exact_code(self, monkeypatch):
        seen = {}

        def fake_exec(repo_root, logical, argv):
            seen.update(logical=logical, argv=argv)
            return 9

        monkeypatch.setattr(wt_mod, "exec_instance", fake_exec)
        assert cli._worktree(["exec", "logical-one", "--", "pwd", "a b"]) == 9
        assert seen["logical"] == "logical-one"
        assert seen["argv"] == ["--", "pwd", "a b"]

    def test_exec_target_forwards_alias_and_argv(self, monkeypatch):
        seen = {}

        def fake_exec_target(repo_root, logical, alias, argv):
            seen.update(logical=logical, alias=alias, argv=argv)
            return 5

        monkeypatch.setattr(wt_mod, "exec_target_instance", fake_exec_target)
        assert cli._worktree(["exec", "logical-one", "--target", "tester", "--", "pwd"]) == 5
        assert seen == {"logical": "logical-one", "alias": "tester", "argv": ["--", "pwd"]}

    def test_exec_forwards_define_root(self, monkeypatch):
        seen = {}

        def fake_resolve(define_root, cwd):
            seen["define_root"] = define_root
            return Path("/resolved")

        monkeypatch.setattr(dev, "resolve_repo_root", fake_resolve)

        def fake_exec(repo_root, logical, argv):
            seen.update(repo_root=repo_root, logical=logical, argv=argv)
            return 0

        monkeypatch.setattr(wt_mod, "exec_instance", fake_exec)
        assert cli._worktree(
            ["exec", "logical-one", "--define-root", "/r", "--", "pwd"]
        ) == 0
        assert seen["define_root"] == "/r"
        assert seen["repo_root"] == Path("/resolved")
        assert seen["argv"] == ["--", "pwd"]

    def test_exec_without_separator_refuses_exit_2(self, monkeypatch, capsys):
        monkeypatch.setattr(
            wt_mod, "exec_instance",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        assert cli._worktree(["exec", "logical-one", "pwd"]) == 2
        assert "unexpected argument" in capsys.readouterr().err

    def test_exec_target_missing_value_refuses_exit_2(self, monkeypatch, capsys):
        assert cli._worktree(["exec", "logical-one", "--target"]) == 2
        assert "--target requires an alias" in capsys.readouterr().err

    def test_exec_define_root_missing_value_refuses_exit_2(self, monkeypatch, capsys):
        assert cli._worktree(["exec", "logical-one", "--define-root"]) == 2
        assert "--define-root requires a PATH" in capsys.readouterr().err

    def test_exec_without_logical_name_refuses_exit_2(self, monkeypatch, capsys):
        assert cli._worktree(["exec"]) == 2
        assert "requires a logical name and a `--` separator" in capsys.readouterr().err

    def test_exec_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fail(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] missing --")

        monkeypatch.setattr(wt_mod, "exec_instance", fail)
        assert cli._worktree(["exec", "logical-one"]) == 2
        assert "missing --" in capsys.readouterr().err


def test_identity_only_env_generation_writes_facts_without_bootstrap(
    tmp_path, monkeypatch
):
    from ciu import workspace_env

    seen = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        workspace_env, "resolve_env_root",
        lambda start, define, defaults: seen.setdefault("root", tmp_path),
    )
    monkeypatch.setattr(
        workspace_env, "generate_ciu_env",
        lambda root: seen.setdefault("generated", root / "ciu.env"),
    )
    assert cli._env_generate(["--identity-only"]) == 0
    assert seen == {"root": tmp_path, "generated": tmp_path / "ciu.env"}
