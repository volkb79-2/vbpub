#!/usr/bin/env python3
"""CIU v2 reset_service() tests (S6.4 / S4.25).

v2 signature:
    reset_service(config, stack_dir, *, compose_file=..., remove_secrets=False,
                  assume_yes=..., specs=None, repo_root=None)

- Uses procutil.run_cmd (no subprocess.run, no sys.exit; raises RuntimeError).
- vol-* are resolved against the STACK DIR, never the process cwd (B14): the
  tests chdir somewhere else and still expect the stack's vol-* removed.
- Secret store files are KEPT unless remove_secrets (S4.25).
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import engine  # noqa: E402
from ciu.engine import reset_service  # noqa: E402


def _base_config() -> dict:
    return {"deploy": {"project_name": "test-project", "labels": {"prefix": "dstdns"}}}


@pytest.fixture(autouse=True)
def _identity_record(tmp_path):
    """CIU-46 cutover: reset's config-less down path derives its compose
    project from THIS checkout's ciu.env (REPO_NAME/INSTANCE_ID). The shared
    fixture omits environment_tag, so most tests here exercise that path;
    the record lives at tmp_path (the repo_root every call below passes)."""
    (tmp_path / "ciu.env").write_text(
        'export REPO_NAME="dstdns"\nexport INSTANCE_ID="abc123"\n',
        encoding="utf-8",
    )


def _reset(config, stack_dir, *, repo_root=None, **kw):
    """reset_service with the explicit repo_root the cutover requires."""
    return reset_service(
        config, stack_dir, repo_root=repo_root if repo_root is not None else stack_dir, **kw
    )


def _ok(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


class TestResetServiceDockerComposeDown:
    def test_runs_docker_compose_down(self, tmp_path):
        config = _base_config()
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()) as mock_run:
            _reset(config, tmp_path, assume_yes=True)
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("compose" in c and "down" in c for c in calls)

    def test_down_includes_remove_orphans(self, tmp_path):
        """CIU-3 / S6.4: down tears down orphans (exited init/sidecars) too."""
        config = _base_config()
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()) as mock_run:
            _reset(config, tmp_path, assume_yes=True)
            first_cmd = mock_run.call_args_list[0].args[0]
            assert "down" in first_cmd and "-v" in first_cmd
            assert "--remove-orphans" in first_cmd

    def test_down_includes_overlay_when_present(self, tmp_path):
        config = _base_config()
        overlay = tmp_path / ".ciu" / "ciu.compose.overlay.yml"
        overlay.parent.mkdir(parents=True)
        overlay.write_text("secrets: {}\n")
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()) as mock_run:
            _reset(config, tmp_path, assume_yes=True)
            first_cmd = mock_run.call_args_list[0].args[0]
            assert ".ciu/ciu.compose.overlay.yml" in first_cmd


class TestResetServiceVolumeDirectories:
    def test_removes_vol_directories_in_stack_dir_not_cwd(self, tmp_path, monkeypatch):
        """B14: vol-* removed from the STACK dir, even when cwd is elsewhere."""
        stack = tmp_path / "stack"
        stack.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        (stack / "vol-postgres-data").mkdir()
        (stack / "vol-redis-data").mkdir()
        (stack / "not-a-volume").mkdir()
        # A decoy vol-* in cwd must NOT be touched.
        (elsewhere / "vol-decoy").mkdir()

        config = _base_config()
        # CIU-46 requires an explicit repo_root for the config-less down; this
        # test's subject is the NATIVE rmtree path, so pin the physical-path
        # translation off (the DooD routing has its own test class).
        monkeypatch.setattr(engine, "to_physical_path", lambda p, **k: p)
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            _reset(config, stack, repo_root=tmp_path, assume_yes=True)

        assert not (stack / "vol-postgres-data").exists()
        assert not (stack / "vol-redis-data").exists()
        assert (stack / "not-a-volume").exists()
        assert (elsewhere / "vol-decoy").exists()  # cwd decoy untouched (B14)


class TestResetServicePrivilegeFallback:
    def test_permission_denied_falls_back_to_helper_container(self, tmp_path, monkeypatch):
        """S6.4/S6.5: an image-UID-owned vol-* the operator cannot rmtree is wiped
        via the root helper container instead of aborting the reset."""
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "vol-postgres-data").mkdir()
        (stack / "vol-redis-data").mkdir()
        rendered = stack / ".ciu" / "rendered"
        rendered.mkdir(parents=True)

        real_rmtree = engine.shutil.rmtree

        def fake_rmtree(path, *a, **k):
            # Image-owned data dirs deny the unprivileged operator; everything
            # else (e.g. the rendered dir in Step 3) still deletes normally.
            if Path(path).name.startswith("vol-"):
                raise PermissionError(13, "Permission denied")
            return real_rmtree(path, *a, **k)

        config = _base_config()
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()), \
             patch.object(engine.shutil, "rmtree", side_effect=fake_rmtree), \
             patch.object(engine, "privileged_rmtree") as mock_helper:
            # MUST NOT raise: the wipe completes via the helper (S6.4).
            # Pin the physical-path translation off: this test exercises the
            # NATIVE PermissionError fallback, not DooD routing (its own class).
            monkeypatch.setattr(engine, "to_physical_path", lambda p, **k: p)
            _reset(config, stack, repo_root=tmp_path, assume_yes=True)

        assert mock_helper.call_count == 2  # both vol dirs routed to root helper
        targets = {Path(c.args[0]).name for c in mock_helper.call_args_list}
        assert targets == {"vol-postgres-data", "vol-redis-data"}
        assert not rendered.exists()  # Step 3 (no permission issue) still ran

    def test_writable_vol_dirs_skip_the_helper(self, tmp_path, monkeypatch):
        """No PermissionError → direct rmtree, helper container never invoked."""
        stack = tmp_path / "stack"
        stack.mkdir()
        (stack / "vol-data").mkdir()

        config = _base_config()
        with patch.object(engine.procutil, "run_cmd", return_value=_ok()), \
             patch.object(engine, "privileged_rmtree") as mock_helper:
            # Pin the physical-path translation off: this test exercises the
            # NATIVE direct-rmtree path, not DooD routing (its own class).
            monkeypatch.setattr(engine, "to_physical_path", lambda p, **k: p)
            _reset(config, stack, repo_root=tmp_path, assume_yes=True)

        assert mock_helper.call_count == 0
        assert not (stack / "vol-data").exists()


class TestRmtreeWithFallbackDooD:
    """CIU-9: in a DooD context, removal must go through the physical path
    unconditionally — a local rmtree success on the logical path proves
    nothing about the physical path the daemon actually bind-mounted.
    """

    def test_dood_routes_through_physical_path_even_when_local_would_succeed(self, tmp_path):
        """to_physical_path(vol_dir) != vol_dir → privileged_rmtree(physical),
        and the local shutil.rmtree is never attempted at all (not even as an
        optimistic first try) — this is the CIU-9 fix itself."""
        vol_dir = tmp_path / "vol-consul-data"
        vol_dir.mkdir()
        physical = tmp_path / "physical-vol-consul-data"

        with patch.object(engine, "to_physical_path", return_value=physical) as mock_to_phys, \
             patch.object(engine.shutil, "rmtree") as mock_rmtree, \
             patch.object(engine, "privileged_rmtree") as mock_helper:
            engine._rmtree_with_fallback(vol_dir, repo_root=Path("/workspaces/dstdns"))

        mock_to_phys.assert_called_once_with(vol_dir, repo_root=Path("/workspaces/dstdns"))
        mock_rmtree.assert_not_called()  # local attempt skipped entirely in DooD
        mock_helper.assert_called_once_with(physical)

    def test_dood_routes_through_physical_path_even_when_dir_no_longer_exists_locally(self, tmp_path):
        """The bug's exact symptom: the operator's own UID:GID owns the data
        (no PermissionError possible), so a local rmtree always "succeeds" —
        but that success is on the wrong (logical) directory. Even with the
        vol_dir already gone locally, DooD routing still fires."""
        vol_dir = tmp_path / "vol-consul-data"  # never created — simulates the
        physical = tmp_path / "physical-vol-consul-data"  # daemon's real target

        with patch.object(engine, "to_physical_path", return_value=physical), \
             patch.object(engine.shutil, "rmtree") as mock_rmtree, \
             patch.object(engine, "privileged_rmtree") as mock_helper:
            engine._rmtree_with_fallback(vol_dir, repo_root=Path("/workspaces/dstdns"))

        mock_rmtree.assert_not_called()
        mock_helper.assert_called_once_with(physical)

    def test_native_host_still_uses_local_rmtree_directly(self, tmp_path):
        """to_physical_path(vol_dir) == vol_dir (S1.9, native host) → local
        shutil.rmtree is used, helper not invoked when it succeeds."""
        vol_dir = tmp_path / "vol-data"
        vol_dir.mkdir()

        with patch.object(engine, "to_physical_path", return_value=vol_dir), \
             patch.object(engine, "privileged_rmtree") as mock_helper:
            engine._rmtree_with_fallback(vol_dir, repo_root=tmp_path)

        assert mock_helper.call_count == 0
        assert not vol_dir.exists()

    def test_native_host_permission_error_falls_back_to_helper(self, tmp_path):
        """Native host (physical == logical) but PermissionError (fixed-UID
        image, S6.7 Pattern (a)) still degrades to the root helper."""
        vol_dir = tmp_path / "vol-postgres-data"
        vol_dir.mkdir()

        with patch.object(engine, "to_physical_path", return_value=vol_dir), \
             patch.object(engine.shutil, "rmtree", side_effect=PermissionError(13, "Permission denied")), \
             patch.object(engine, "privileged_rmtree") as mock_helper:
            engine._rmtree_with_fallback(vol_dir, repo_root=tmp_path)

        mock_helper.assert_called_once_with(vol_dir)

    def test_no_repo_root_context_falls_back_to_native_behavior(self, tmp_path):
        """to_physical_path raising ValueError (no REPO_ROOT/PHYSICAL_REPO_ROOT
        resolvable) is treated as native host, not DooD — preserves the
        pre-existing behavior for callers/tests with no DooD env configured."""
        vol_dir = tmp_path / "vol-data"
        vol_dir.mkdir()

        with patch.object(engine, "to_physical_path", side_effect=ValueError("REPO_ROOT not set")), \
             patch.object(engine, "privileged_rmtree") as mock_helper:
            engine._rmtree_with_fallback(vol_dir, repo_root=None)

        assert mock_helper.call_count == 0
        assert not vol_dir.exists()


class TestPrivilegedRmtree:
    def test_mounts_parent_and_rm_rf_named_child(self, tmp_path):
        """S6.5 deletion helper: mount the PARENT, rm -rf the named child so the
        directory itself goes (mounting the dir would leave the mountpoint)."""
        target = tmp_path / "vol-postgres-data"
        with patch.object(engine.procutil, "docker", return_value=_ok()) as mock_docker:
            engine.privileged_rmtree(target)

        cmd = mock_docker.call_args.args[0]
        assert cmd[:3] == ["run", "--rm", "-v"]
        assert cmd[3] == f"{tmp_path}:/t"  # PARENT mounted, not the target itself
        assert "alpine" in cmd
        assert cmd[-3:] == ["rm", "-rf", "/t/vol-postgres-data"]  # named child
        assert mock_docker.call_args.kwargs.get("check") is True


class TestResetServiceConfigFiles:
    def test_removes_rendered_files_and_overlay(self, tmp_path):
        config = _base_config()
        (tmp_path / "ciu.compose.yml").touch()
        (tmp_path / "ciu.toml").touch()
        overlay = tmp_path / ".ciu" / "ciu.compose.overlay.yml"
        overlay.parent.mkdir(parents=True)
        overlay.touch()
        rendered = tmp_path / ".ciu" / "rendered"
        rendered.mkdir(parents=True)
        (rendered / "svc").mkdir()

        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            _reset(config, tmp_path, assume_yes=True)

        assert not (tmp_path / "ciu.compose.yml").exists()
        assert not (tmp_path / "ciu.toml").exists()
        assert not overlay.exists()
        assert not rendered.exists()


class TestResetServiceSecrets:
    def test_secret_store_kept_by_default(self, tmp_path):
        config = _base_config()
        secrets_dir = tmp_path / ".ciu" / "secrets"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "redis_password").write_text("x")

        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            _reset(config, tmp_path, assume_yes=True)

        assert (secrets_dir / "redis_password").exists()  # kept (S4.25)

    def test_secret_store_removed_with_remove_secrets(self, tmp_path):
        config = _base_config()
        secrets_dir = tmp_path / ".ciu" / "secrets"
        secrets_dir.mkdir(parents=True)
        (secrets_dir / "redis_password").write_text("x")

        with patch.object(engine.procutil, "run_cmd", return_value=_ok()):
            _reset(config, tmp_path, assume_yes=True, remove_secrets=True)

        assert not secrets_dir.exists()  # removed (S4.25 --secrets)


class TestResetServiceOrphanedContainers:
    def test_uses_anchored_component_label(self, tmp_path):
        config = _base_config()

        def run(cmd, **kw):
            if "ps" in cmd:
                return _ok(stdout="orphan-1\norphan-2\n")
            return _ok()

        with patch.object(engine.procutil, "run_cmd", side_effect=run) as mock_run:
            _reset(config, tmp_path, assume_yes=True)

        ps_calls = [c.args[0] for c in mock_run.call_args_list if "ps" in c.args[0]]
        assert ps_calls, "expected a docker ps call"
        # Anchored label equality: <prefix>.component=<service> (S6.4).
        assert any(f"label=dstdns.component={tmp_path.name}" in c for c in ps_calls)

    def test_orphan_sweep_is_scoped_to_THIS_instance(self, tmp_path):
        """CIU-19 — the component label is NOT instance-scoped.

        A worktree instance (S16) is a second checkout of the same repo, so its
        containers carry the SAME `<prefix>.component=<service>` label; only the
        compose project name carries the instance id. Filtering on the component
        alone therefore matched every instance on the host, and `ciu clean` in
        one instance deleted the same-named service out of ALL of them —
        observed live: cleaning a worktree instance removed the PRIMARY
        instance's db-init container. With a full stack up that is someone's
        database.
        """
        config = _base_config()
        # The shared fixture omits environment_tag, so EVERY other test in this
        # file exercises the unscoped path -- which is why the scoped one was
        # never covered. Supply the S8.7 pair explicitly here.
        config["deploy"]["environment_tag"] = "abc123"

        def run(cmd, **kw):
            if "ps" in cmd:
                return _ok(stdout="orphan-1\n")
            return _ok()

        with patch.object(engine.procutil, "run_cmd", side_effect=run) as mock_run:
            _reset(config, tmp_path, assume_yes=True)

        ps = next(c.args[0] for c in mock_run.call_args_list if "ps" in c.args[0])
        expected = engine.compose_project_name(config, tmp_path)
        assert f"label=com.docker.compose.project={expected}" in ps, (
            "orphan sweep must be scoped to this instance's compose project; "
            f"got {ps}"
        )

    def test_orphan_sweep_warns_when_it_cannot_scope(self, tmp_path, capsys):
        """Without the S8.7 naming pair there is nothing to scope BY, and the
        host-wide component sweep is deliberately kept (it is what catches
        legacy pre-scoping containers). It must say so out loud rather than
        quietly reaching across instances."""
        config = _base_config()  # no environment_tag -> nothing to scope by

        def run(cmd, **kw):
            if "ps" in cmd:
                return _ok(stdout="")
            return _ok()

        with patch.object(engine.procutil, "run_cmd", side_effect=run) as mock_run:
            _reset(config, tmp_path, assume_yes=True)

        out = capsys.readouterr().out
        assert "across EVERY instance" in out
        ps = next(c.args[0] for c in mock_run.call_args_list if "ps" in c.args[0])
        assert not any("com.docker.compose.project=" in a for a in ps)


class TestResetServiceValidation:
    def test_requires_project_name(self, tmp_path):
        with pytest.raises(ValueError, match="deploy.project_name"):
            reset_service({}, tmp_path, assume_yes=True)

    def test_requires_label_prefix(self, tmp_path):
        config = {"deploy": {"project_name": "test"}}
        with pytest.raises(ValueError, match="deploy.labels.prefix"):
            _reset(config, tmp_path, assume_yes=True)
