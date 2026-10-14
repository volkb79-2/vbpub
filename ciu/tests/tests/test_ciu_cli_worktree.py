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


class TestWorktreeCreateDispatch:
    def test_selected_ciu_root_resolves_from_cwd_or_explicit_target(
        self, tmp_path, capsys
    ):
        git_root = tmp_path / "mono"
        nested = git_root / "component"
        nested.mkdir(parents=True)
        unrelated = git_root / "other"
        unrelated.mkdir()
        mistyped_child = nested / "source"
        mistyped_child.mkdir()
        (nested / "ciu.global.defaults.toml.j2").write_text(
            "[ciu]\n", encoding="utf-8"
        )

        assert cli._resolve_worktree_ciu_root_cli(
            git_root, None, nested / "src"
        ) == nested
        assert cli._resolve_worktree_ciu_root_cli(
            git_root, git_root, nested
        ) == nested
        (git_root / "ciu.global.defaults.toml.j2").write_text(
            "[ciu]\n", encoding="utf-8"
        )
        assert cli._resolve_worktree_ciu_root_cli(
            git_root, git_root, nested
        ) == nested
        assert cli._resolve_worktree_ciu_root_cli(
            git_root, git_root, git_root
        ) == git_root
        with pytest.raises(SystemExit) as exc:
            cli._resolve_worktree_ciu_root_cli(git_root, unrelated, nested)
        assert exc.value.code == 2
        assert "neither a CIU root nor the Git-family root" in capsys.readouterr().err
        with pytest.raises(SystemExit) as exc:
            cli._resolve_worktree_ciu_root_cli(git_root, mistyped_child, nested)
        assert exc.value.code == 2

    def test_forwards_all_options(self, monkeypatch, capsys):
        seen = {}

        def fake_create(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            display_name,
            prefix,
            feature,
            branch,
            path,
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
                display_name=display_name,
                prefix=prefix,
                feature=feature,
                branch=branch,
                path=path,
                shared_infra=shared_infra,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
                shared_infra_ref_services=shared_infra_ref_services,
            )
            return _ready_record()

        monkeypatch.setattr(wt_mod, "create", fake_create)
        code = cli._worktree(
            [
                "create",
                "mypkg",
                "--base",
                "develop",
                "--profile",
                "core,db",
                "--worktree-dir",
                ".wt",
                "--name",
                "display",
            ]
        )
        assert code == 0
        assert seen == {
            "repo_root": seen["repo_root"],
            "name": "mypkg",
            "base": "develop",
            "profile": "core,db",
            "worktree_dir": ".wt",
            "display_name": "display",
            "prefix": None,
            "feature": None,
            "branch": None,
            "path": None,
            "shared_infra": None,
            "shared_infra_services": None,
            "shared_infra_ref_projects": None,
            "shared_infra_ref_services": None,
        }
        assert "worktree ready:" in capsys.readouterr().out

    def test_worktree_error_maps_to_exit_2(self, monkeypatch, capsys):
        def fake_create(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16] boom")

        monkeypatch.setattr(wt_mod, "create", fake_create)
        assert cli._worktree(["create", "mypkg"]) == 2
        assert "[S16] boom" in capsys.readouterr().err

    def test_create_json_emits_managed_record(self, monkeypatch, capsys):
        record = _ready_record()
        monkeypatch.setattr(wt_mod, "create", lambda *_a, **_kw: record)
        assert cli._worktree(["create", "mypkg", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["operation"] == "create"


class TestWorktreeCreateSharedInfraDispatch:
    def test_forwards_shared_infra_flags(self, monkeypatch, capsys):
        seen = {}

        def fake_create(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            display_name,
            prefix,
            feature,
            branch,
            path,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
            shared_infra_ref_services,
        ):
            seen.update(
                shared_infra=shared_infra,
                profile=profile,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
                shared_infra_ref_services=shared_infra_ref_services,
            )
            return _ready_record()

        monkeypatch.setattr(wt_mod, "create", fake_create)
        code = cli._worktree(
            [
                "create",
                "mypkg",
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
            "profile": None,
            "shared_infra_services": "api,worker",
            "shared_infra_ref_projects": "idp-dev-idp,vault-dev-vault",
            "shared_infra_ref_services": "vault,secrets=vault",
        }
        assert "worktree ready:" in capsys.readouterr().out

    def test_shared_infra_flags_default_to_none(self, monkeypatch):
        seen = {}

        def fake_create(
            repo_root,
            name,
            *,
            base,
            profile,
            worktree_dir,
            display_name,
            prefix,
            feature,
            branch,
            path,
            shared_infra,
            shared_infra_services,
            shared_infra_ref_projects,
            shared_infra_ref_services,
        ):
            seen.update(
                shared_infra=shared_infra,
                profile=profile,
                shared_infra_services=shared_infra_services,
                shared_infra_ref_projects=shared_infra_ref_projects,
                shared_infra_ref_services=shared_infra_ref_services,
            )
            return _ready_record()

        monkeypatch.setattr(wt_mod, "create", fake_create)
        cli._worktree(["create", "mypkg"])
        assert seen == {
            "shared_infra": None,
            "profile": None,
            "shared_infra_services": None,
            "shared_infra_ref_projects": None,
            "shared_infra_ref_services": None,
        }

    def test_partial_shared_infra_error_surfaces_as_exit_2(
        self, monkeypatch, capsys
    ):
        def fake_create(*_a, **_kw):
            raise wt_mod.WorktreeError("[S16.1] partial group")

        monkeypatch.setattr(wt_mod, "create", fake_create)
        assert cli._worktree(["create", "mypkg", "--shared-infra", "primary"]) == 2
        assert "partial group" in capsys.readouterr().err

    def test_ref_services_alone_reaches_the_all_or_nothing_refusal(
        self, monkeypatch, capsys
    ):
        """O12 (CLI half): the new flag is OPTIONAL but not standalone — it
        joins the existing all-or-nothing group, so supplying only it is
        forwarded and refused, never quietly accepted as a no-op."""
        seen = {}

        def fake_create(*_a, **kw):
            seen.update(kw)
            raise wt_mod.WorktreeError("[S16.1] partial group")

        monkeypatch.setattr(wt_mod, "create", fake_create)
        assert cli._worktree(
            ["create", "mypkg", "--shared-infra-ref-services", "vault"]
        ) == 2
        assert seen["shared_infra_ref_services"] == "vault"
        assert seen["shared_infra"] is None
        assert "partial group" in capsys.readouterr().err


class TestWorktreeRmDispatch:
    def test_forwards_yes_and_force(self, monkeypatch, capsys):
        seen = {}

        def fake_remove(repo_root, name, *, yes, force, root_escalation):
            seen.update(
                repo_root=repo_root, name=name, yes=yes, force=force,
                root_escalation=root_escalation,
            )
            return Path("/tmp/repo/.worktrees/mypkg")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        assert cli._worktree([
            "rm", "mypkg", "-y", "--force", "--root-escalation", "yes"
        ]) == 0
        assert seen["name"] == "mypkg"
        assert seen["yes"] is True and seen["force"] is True
        assert seen["root_escalation"] == "yes"
        assert "removed:" in capsys.readouterr().out

    def test_defaults_yes_and_force_false(self, monkeypatch):
        seen = {}

        def fake_remove(repo_root, name, *, yes, force, root_escalation):
            seen.update(yes=yes, force=force, root_escalation=root_escalation)
            return Path("/tmp/x")

        monkeypatch.setattr(wt_mod, "remove", fake_remove)
        cli._worktree(["rm", "mypkg"])
        assert seen == {"yes": False, "force": False, "root_escalation": "ask"}

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
                wt_mod.WorktreeInfo(primary, "main", "abc12345", is_primary=True),
                wt_mod.WorktreeInfo(linked, "pkg", "def45678", is_primary=False),
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

    def test_rm_json_emits_partial_removal_report(self, monkeypatch, capsys):
        report = {
            "schema_version": 1,
            "operation": "remove",
            "status": "partial",
            "failed_phase": "checkout_deletion",
            "completed_phases": ["resource_cleanup", "lease_release"],
        }

        def fail(*_a, **_kw):
            raise wt_mod.WorktreeRemovalFailure("partial removal", report)

        monkeypatch.setattr(wt_mod, "remove_document", fail)
        assert cli._worktree(["rm", "mypkg", "--json"]) == 2
        captured = capsys.readouterr()
        assert json.loads(captured.out) == report
        assert captured.err == ""


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

    def test_profile_flag_forwards_on_create_ensure_and_adopt(
        self, monkeypatch
    ):
        seen = {}

        def fake_lifecycle(repo_root, logical_name, **kwargs):
            seen.update(kwargs)
            return _ready_record()

        for verb in ("create", "ensure"):
            monkeypatch.setattr(wt_mod, verb, fake_lifecycle)
            assert cli._worktree([verb, "task-one", "--profile", "core,db"]) == 0
            assert seen["profile"] == "core,db"
            seen.clear()

        def fake_adopt(repo_root, logical_name, path, **kwargs):
            seen.update(kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, "adopt", fake_adopt)
        assert cli._worktree([
            "adopt", "task-one", "/tmp/existing", "--profile", "core,db"
        ]) == 0
        assert seen["profile"] == "core,db"

    def test_create_ensure_and_adopt_use_the_selected_nested_ciu_root(
        self, monkeypatch
    ):
        git_root = Path("/tmp/mono")
        nested_root = git_root / "component"
        monkeypatch.setattr(
            cli, "_resolve_worktree_git_root_cli", lambda *_a: git_root
        )
        monkeypatch.setattr(
            cli, "_resolve_worktree_ciu_root_cli", lambda *_a: nested_root
        )
        seen = {}

        def fake_lifecycle(root, logical_name, **kwargs):
            seen.update(root=root, logical_name=logical_name, kwargs=kwargs)
            return _ready_record()

        for verb in ("create", "ensure"):
            seen.clear()
            monkeypatch.setattr(wt_mod, verb, fake_lifecycle)
            assert cli._worktree([verb, "nested-task", "--profile", "core"]) == 0
            assert seen["root"] == nested_root

        seen.clear()

        def fake_adopt(root, logical_name, path, **kwargs):
            seen.update(root=root, logical_name=logical_name, path=path, kwargs=kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, "adopt", fake_adopt)
        assert cli._worktree([
            "adopt", "nested-task", "/tmp/existing", "--profile", "core"
        ]) == 0
        assert seen["root"] == nested_root

    def test_nested_root_lifecycle_verbs_keep_the_selected_root(
        self, monkeypatch
    ):
        git_root = Path("/tmp/mono")
        nested_root = git_root / "component"
        monkeypatch.setattr(
            cli, "_resolve_worktree_git_root_cli", lambda *_a: git_root
        )
        monkeypatch.setattr(
            cli, "_resolve_worktree_ciu_root_cli", lambda *_a: nested_root
        )
        calls = []

        def record_root(name):
            def capture(root, *_args, **_kwargs):
                calls.append((name, root))
                return _ready_record()
            return capture

        def document_root(name):
            def capture(root, *_args, **_kwargs):
                calls.append((name, root))
                return {"status": "ready"}
            return capture

        monkeypatch.setattr(wt_mod, "create", record_root("create"))
        monkeypatch.setattr(wt_mod, "ensure", record_root("ensure"))
        monkeypatch.setattr(wt_mod, "adopt", record_root("adopt"))
        monkeypatch.setattr(
            wt_mod, "up_instance",
            lambda root, *_a, **_kw: (calls.append(("up", root)) or 0),
        )
        assert cli._worktree([
            "create", "nested-task", "--profile", "core", "--up"
        ]) == 0
        assert cli._worktree(["ensure", "nested-task"]) == 0
        assert cli._worktree(["adopt", "nested-adopt", "/tmp/target"]) == 0

        monkeypatch.setattr(
            wt_mod, "inspect_instance", document_root("inspect")
        )
        assert cli._worktree(["inspect", "nested-task", "--json"]) == 0
        assert cli._worktree(["up", "nested-task"]) == 0

        monkeypatch.setattr(
            wt_mod, "apply_lease", record_root("lease")
        )
        assert cli._worktree(["lease", "nested-task", "--perpetual"]) == 0

        monkeypatch.setattr(
            wt_mod, "remove_document",
            lambda root, *_a, **_kw: (calls.append(("rm", root)) or {"removed": True}),
        )
        assert cli._worktree(["rm", "nested-task", "--json"]) == 0

        monkeypatch.setattr(
            wt_mod, "list_instances", document_root("list")
        )
        assert cli._worktree(["list", "--json"]) == 0

        monkeypatch.setattr(
            wt_mod, "branch_hygiene", document_root("branches")
        )
        assert cli._worktree(["branches", "--json"]) == 0
        monkeypatch.setattr(
            wt_mod, "reap_groups", document_root("reap")
        )
        assert cli._worktree(["reap", "--json"]) == 0

        monkeypatch.setattr(
            wt_mod, "exec_instance",
            lambda root, *_a, **_kw: (calls.append(("exec", root)) or 0),
        )
        assert cli._worktree(["exec", "nested-task", "--", "true"]) == 0

        assert calls == [
            ("create", nested_root), ("up", nested_root),
            ("ensure", nested_root), ("adopt", nested_root),
            ("inspect", nested_root), ("up", nested_root),
            ("lease", nested_root), ("rm", nested_root),
            ("list", nested_root), ("branches", nested_root),
            ("reap", nested_root), ("exec", nested_root),
        ]

    def test_overlay_options_refuse_without_a_selected_ciu_root(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            cli, "_resolve_worktree_git_root_cli", lambda *_a: Path("/tmp/mono")
        )
        monkeypatch.setattr(cli, "_resolve_worktree_ciu_root_cli", lambda *_a: None)
        monkeypatch.setattr(
            wt_mod, "create",
            lambda *_a, **_kw: pytest.fail("allocation must not run without a CIU root"),
        )

        assert cli._worktree(["create", "rootless", "--profile", "core"]) == 2
        assert "require a selected CIU root" in capsys.readouterr().err

    def test_existing_network_claim_flag_forwards_only_to_supported_verbs(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            cli, "_resolve_worktree_git_root_cli", lambda *_a: Path("/tmp/mono")
        )
        monkeypatch.setattr(
            cli, "_resolve_worktree_ciu_root_cli", lambda *_a: Path("/tmp/mono/ciu")
        )
        seen = {}

        def fake_ensure(_root, _name, **kwargs):
            seen.update(kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, "ensure", fake_ensure)
        assert cli._worktree([
            "ensure", "legacy", "--adopt-existing-network"
        ]) == 0
        assert seen["adopt_existing_network"] is True

        def fake_adopt(_root, _name, _path, **kwargs):
            seen.clear()
            seen.update(kwargs)
            return _ready_record()

        monkeypatch.setattr(wt_mod, "adopt", fake_adopt)
        assert cli._worktree([
            "adopt", "legacy", "/tmp/existing", "--adopt-existing-network"
        ]) == 0
        assert seen["adopt_existing_network"] is True

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
            verb, "task-one",
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
            "adopt", "task-one", "/tmp/existing",
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

    def test_exec_forwards_root_folder(self, monkeypatch):
        seen = {}

        def fake_resolve(start_dir, *, root_folder):
            seen["define_root"] = root_folder
            return Path("/resolved")

        monkeypatch.setattr("ciu.workspace.resolve_worktree_git_root", fake_resolve)

        def fake_exec(repo_root, logical, argv):
            seen.update(repo_root=repo_root, logical=logical, argv=argv)
            return 0

        monkeypatch.setattr(wt_mod, "exec_instance", fake_exec)
        assert cli._worktree(
            ["exec", "logical-one", "--root-folder", "/r", "--", "pwd"]
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

    def test_exec_root_folder_missing_value_refuses_exit_2(self, monkeypatch, capsys):
        assert cli._worktree(["exec", "logical-one", "--root-folder"]) == 2
        assert "--root-folder requires a PATH" in capsys.readouterr().err

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


def test_env_generate_does_not_offer_allocation_lock_bypass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli._env_generate(["--allocation-lock-held"])


def test_explicit_env_generate_returns_nonzero_on_managed_network_refusal(
    tmp_path, monkeypatch, capsys
):
    from ciu import deploy, workspace_env

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        deploy,
        "action_generate_env",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            workspace_env.ManagedRuntimeVerificationError("foreign network owner")
        ),
    )

    assert cli._env_generate([]) == 1
    assert "[ERROR] foreign network owner" in capsys.readouterr().err
