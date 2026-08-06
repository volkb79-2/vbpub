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

from ciu import deploy, governance, ksm  # noqa: E402

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


class TestMemoryProfile:
    """S15.19 — per-service KSM strategy.

    The point of this table is that the right choice is a property of the IMAGE,
    not of the estate: `ksm_optin` could only say yes/no for everything.
    """

    def test_absent_profile_defaults_to_preload(self):
        """A genuine POLICY default (§4.2a's legitimate case): with governance on
        and a shim configured, injecting is correct absent any statement, and it
        substitutes for no fact held elsewhere."""
        assert governance.resolve_service_ksm({}, "anything") == "preload"

    def test_service_entry_wins_over_default(self):
        cfg = {"memory_profile": {"default": {"ksm": "preload"},
                                  "services": {"otel": {"ksm": "off"}}}}
        assert governance.resolve_service_ksm(cfg, "otel") == "off"
        assert governance.resolve_service_ksm(cfg, "other") == "preload"

    def test_default_applies_when_service_is_unlisted(self):
        cfg = {"memory_profile": {"default": {"ksm": "off"}}}
        assert governance.resolve_service_ksm(cfg, "anything") == "off"

    def test_unknown_strategy_is_a_HARD_ERROR_not_a_silent_fallback(self):
        """A typo must not read as 'default applies'. `ksm = "wraper"` silently
        yielding preload — with no message — is the exact silent-wrong-answer
        this config layer exists to prevent."""
        cfg = {"memory_profile": {"services": {"x": {"ksm": "wraper"}}}}
        with pytest.raises(ValueError, match=r"unknown ksm strategy"):
            governance.resolve_service_ksm(cfg, "x")

    def test_wrapper_is_an_accepted_strategy(self):
        """S15.20. `wrapper` is a generic CIU capability, not a dstdns feature:
        a consumer whose images are static-only has no other way to opt in."""
        cfg = {"memory_profile": {"services": {"otel": {"ksm": "wrapper"}}}}
        assert governance.resolve_service_ksm(cfg, "otel") == "wrapper"

    def test_wrapper_is_never_the_implicit_default(self):
        """It REPLACES the image's entrypoint — destructive if wrong, where
        preload is additive and inert. It must always be asked for."""
        assert governance.resolve_service_ksm({}, "anything") == "preload"
        cfg = {"memory_profile": {"default": {}, "services": {"x": {}}}}
        assert governance.resolve_service_ksm(cfg, "x") == "preload"


class TestExecWrapperEntrypoint:
    """S15.20 — wrapping means RE-STATING the original entrypoint, because
    compose's `entrypoint:` replaces rather than prepends."""

    def _inspect(self, payload, rc=0):
        def fake(cmd, **_kw):
            return subprocess.CompletedProcess(cmd, rc, payload, "")
        return fake

    def test_null_entrypoint_reads_as_empty_not_unknown(self, monkeypatch):
        """An image with no ENTRYPOINT is the EASY wrapping case; it must not be
        confused with 'could not inspect', which demands the opposite action."""
        monkeypatch.setattr(ksm.subprocess, "run", self._inspect("null\n"))
        assert ksm.image_entrypoint("img") == []
        assert ksm.wrapper_entrypoint("img") == [ksm.WRAPPER_TARGET]

    def test_real_entrypoint_is_restated_after_the_wrapper(self, monkeypatch):
        """Measured: [wrapper] alone FAILS here (execvp on the first CMD token —
        CMD is arguments, not a program); [wrapper, *original] works."""
        monkeypatch.setattr(ksm.subprocess, "run",
                            self._inspect('["docker-entrypoint.sh"]\n'))
        assert ksm.wrapper_entrypoint("vault") == [
            ksm.WRAPPER_TARGET, "docker-entrypoint.sh",
        ]

    def test_uninspectable_image_REFUSES_rather_than_guessing(self, monkeypatch):
        """Guessing would either drop the original (container never starts) or
        invent one. A memory optimisation must never risk either."""
        monkeypatch.setattr(ksm.subprocess, "run", self._inspect("", rc=1))
        with pytest.raises(ksm.KsmBuildError, match=r"cannot read the ENTRYPOINT"):
            ksm.wrapper_entrypoint("missing:tag")


class TestEntrypointDrift:
    """Wrapping FREEZES the entrypoint into rendered compose; drift makes the
    deployed container run the OLD command, silently."""

    def _inspect(self, payload, rc=0):
        def fake(cmd, **_kw):
            return subprocess.CompletedProcess(cmd, rc, payload, "")
        return fake

    def test_unchanged_entrypoint_passes(self, monkeypatch):
        monkeypatch.setattr(ksm.subprocess, "run", self._inspect('["a","b"]\n'))
        recorded = ksm.entrypoint_fingerprint("img")
        ok, _ = ksm.check_entrypoint_drift("img", recorded)
        assert ok

    def test_changed_entrypoint_is_CAUGHT(self, monkeypatch):
        monkeypatch.setattr(ksm.subprocess, "run", self._inspect('["a","b"]\n'))
        recorded = ksm.entrypoint_fingerprint("img")
        monkeypatch.setattr(ksm.subprocess, "run",
                            self._inspect('["tini","--","a","b"]\n'))
        ok, msg = ksm.check_entrypoint_drift("img", recorded)
        assert not ok and "DRIFT" in msg

    def test_uninspectable_is_NOT_reported_as_unchanged(self, monkeypatch):
        """CIU-15's lesson: an unverifiable claim must never read as verified."""
        monkeypatch.setattr(ksm.subprocess, "run", self._inspect("", rc=1))
        ok, msg = ksm.check_entrypoint_drift("img", '["a"]')
        assert not ok and "cannot inspect" in msg

    def test_no_recorded_value_is_not_a_drift_verdict(self, monkeypatch):
        ok, _ = ksm.check_entrypoint_drift("img", "")
        assert ok

    def test_ksm_off_suppresses_injection_without_exempting_all_governance(self):
        """The finer-grained control `exempt_services` could never express:
        opt out of KSM while KEEPING cgroup_parent/mem_limit/blkio."""
        cfg = dict(governance.GOVERNANCE_DEFAULTS)
        cfg.update({
            "enabled": True, "cgroup_parent": "dev-background.slice",
            "_ksm_optin_source": "/host/ksm.so",
            "memory_profile": {"services": {"quiet": {"ksm": "off"}}},
        })
        inj, _notes = governance.build_injections({"quiet": {}, "loud": {}}, cfg)

        assert "LD_PRELOAD" not in str(inj["quiet"].get("environment", ""))
        assert "volumes" not in inj["quiet"]
        assert inj["quiet"]["cgroup_parent"] == "dev-background.slice"  # still governed
        assert "LD_PRELOAD=/opt/ksm/ksm-optin.so" in inj["loud"]["environment"]
