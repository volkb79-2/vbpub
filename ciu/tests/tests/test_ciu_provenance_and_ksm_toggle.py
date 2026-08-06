"""S15.18 (CIU-17) ad-hoc KSM toggle + S17 (CIU-18) image-revision preflight.

Both are "refuse the wrong thing" features, so the tests that matter are the
refusals and — just as much — the cases that must NOT refuse. A provenance check
that fires on external images or on a dirty dev tree gets disabled permanently
within a day, and then enforces nothing at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, governance  # noqa: E402

REV_LABEL = "org.opencontainers.image.revision"


class TestKsmToggle:
    def test_unset_env_leaves_the_configured_value_alone(self, monkeypatch):
        """The override is opt-in: absent, it must never invent a policy."""
        monkeypatch.delenv(governance.KSM_ENV_VAR, raising=False)
        assert governance.resolve_ksm_optin("builtin") == "builtin"
        assert governance.resolve_ksm_optin("") == ""
        assert governance.resolve_ksm_optin("tools/x.so") == "tools/x.so"

    @pytest.mark.parametrize("value", ["1", "on", "true", "yes", "builtin", "BUILTIN"])
    def test_truthy_values_force_the_builtin_shim(self, monkeypatch, value):
        monkeypatch.setenv(governance.KSM_ENV_VAR, value)
        assert governance.resolve_ksm_optin("") == governance.BUILTIN_KSM

    @pytest.mark.parametrize("value", ["0", "off", "false", "no", ""])
    def test_falsy_values_force_it_off(self, monkeypatch, value):
        monkeypatch.setenv(governance.KSM_ENV_VAR, value)
        assert governance.resolve_ksm_optin("builtin") == ""

    def test_any_other_value_is_taken_as_an_explicit_shim_path(self, monkeypatch):
        monkeypatch.setenv(governance.KSM_ENV_VAR, "/opt/custom.so")
        assert governance.resolve_ksm_optin("builtin") == "/opt/custom.so"

    def test_resolution_is_read_fresh_not_cached(self, monkeypatch):
        """A long-lived process (or the next test) must see a changed env."""
        monkeypatch.setenv(governance.KSM_ENV_VAR, "off")
        assert governance.resolve_ksm_optin("builtin") == ""
        monkeypatch.setenv(governance.KSM_ENV_VAR, "on")
        assert governance.resolve_ksm_optin("builtin") == governance.BUILTIN_KSM


def _docker_ps(rows):
    """Fake procutil.docker for both `ps` (rows) and `image inspect` (labels)."""
    names, labels = rows

    def fake(cmd, **_kw):
        if cmd and cmd[0] == "ps":
            out = "".join(f"{n}\t{i}\t{p}\n" for n, i, p in names)
            return subprocess.CompletedProcess(cmd, 0, out, "")
        image = cmd[2] if len(cmd) > 2 else ""
        if image not in labels:
            return subprocess.CompletedProcess(cmd, 1, "", "No such image")
        return subprocess.CompletedProcess(cmd, 0, labels[image] + "\n", "")
    return fake


PREFIX = "proj-abc"
ONE_RUNNING = ([("proj-abc-api", "example/api:latest", "proj-abc-api")],
               {"example/api:latest": "abc12345"})


def _rows(revision, project="proj-abc-api"):
    return ([("proj-abc-api", "example/api:latest", project)],
            {"example/api:latest": revision})


class TestRunningProvenance:
    """S17.2 is a TEST-time gate over RUNNING containers, not a deploy-time one:
    at deploy the question is 'did I bake?', which surfaces at once. The question
    that yields bad EVIDENCE is asked against an already-running stack."""

    def test_matching_revision_passes(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(_rows("abc12345")))
        deploy.verify_running_provenance(PREFIX)

    def test_mismatched_revision_REFUSES(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(_rows("deadbeef")))
        with pytest.raises(ValueError, match=r"\[S17\].*different commit"):
            deploy.verify_running_provenance(PREFIX)

    def test_ignore_mismatch_downgrades_to_a_warning(self, monkeypatch, capsys):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(_rows("deadbeef")))
        deploy.verify_running_provenance(PREFIX, ignore_mismatch=True)
        out = capsys.readouterr().out
        assert "S17" in out and "deadbeef" in out

    def test_a_SIBLING_instance_is_not_reported_as_stale(self, monkeypatch):
        """A worktree instance (S16) legitimately runs a different commit. Its
        containers carry a different compose project, and scoping by that prefix
        is what stops this check from failing every multi-instance host."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(
            deploy.procutil, "docker",
            _docker_ps(_rows("deadbeef", project="OTHER-instance-api")),
        )
        deploy.verify_running_provenance(PREFIX)  # must not raise

    def test_unlabelled_image_is_SKIPPED_not_refused(self, monkeypatch):
        """External images (postgres:16) carry no revision label; refusing on
        absence would break every install on upgrade, and absence is not
        evidence of mismatch."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(_rows("")))
        deploy.verify_running_provenance(PREFIX)

    def test_no_value_label_is_treated_as_absent(self, monkeypatch):
        """docker --format renders a missing key as the literal '<no value>';
        comparing that against a revision would refuse every unlabelled image."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(_rows("<no value>")))
        deploy.verify_running_provenance(PREFIX)

    def test_dirty_tree_warns_and_does_not_refuse(self, monkeypatch, capsys):
        """Uncommitted changes are in NO image, so nothing can match. Refusing
        would fire on every dev-loop run and get switched off for good."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345-dirty")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(_rows("deadbeef")))
        deploy.verify_running_provenance(PREFIX)
        assert "dirty" in capsys.readouterr().out

    def test_non_git_checkout_is_silent(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "dev")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(_rows("deadbeef")))
        deploy.verify_running_provenance(PREFIX)

    def test_nothing_running_is_not_a_mismatch(self, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_ps(([], {})))
        deploy.verify_running_provenance(PREFIX)

    def test_docker_unavailable_does_not_manufacture_a_verdict(self, monkeypatch):
        def boom(*_a, **_kw):
            raise FileNotFoundError("docker")
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", boom)
        deploy.verify_running_provenance(PREFIX)
