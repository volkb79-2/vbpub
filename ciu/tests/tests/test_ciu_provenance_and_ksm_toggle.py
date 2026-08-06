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


def _selection(tmp_path: Path, compose_text: str) -> list[dict]:
    from ciu.config_constants import CIU_COMPOSE_OUTPUT

    stack = tmp_path / "applications" / "api"
    stack.mkdir(parents=True)
    (stack / CIU_COMPOSE_OUTPUT).write_text(compose_text, encoding="utf-8")
    return [{"path": "applications/api"}]


def _docker_returning(labels: dict[str, str]):
    """Fake procutil.docker: `image inspect` answers from *labels* by image name."""
    def fake(cmd, **_kw):
        image = cmd[2] if len(cmd) > 2 else ""
        if image not in labels:
            return subprocess.CompletedProcess(cmd, 1, "", "No such image")
        return subprocess.CompletedProcess(cmd, 0, labels[image] + "\n", "")
    return fake


class TestImageRevisionPreflight:
    COMPOSE = "services:\n  api:\n    image: example/api:latest\n"

    def test_matching_revision_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker",
                            _docker_returning({"example/api:latest": "abc12345"}))
        deploy.image_revision_preflight(tmp_path, _selection(tmp_path, self.COMPOSE))

    def test_mismatched_revision_REFUSES(self, tmp_path, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker",
                            _docker_returning({"example/api:latest": "deadbeef"}))
        with pytest.raises(ValueError, match=r"\[S17\].*built from a different commit"):
            deploy.image_revision_preflight(tmp_path, _selection(tmp_path, self.COMPOSE))

    def test_ignore_mismatch_downgrades_to_a_warning(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker",
                            _docker_returning({"example/api:latest": "deadbeef"}))
        deploy.image_revision_preflight(
            tmp_path, _selection(tmp_path, self.COMPOSE), ignore_mismatch=True
        )
        out = capsys.readouterr().out
        assert "S17" in out and "deadbeef" in out

    def test_unlabelled_image_is_SKIPPED_not_refused(self, tmp_path, monkeypatch):
        """External images (postgres:16) carry no revision label. Refusing on
        absence would break every deploy on upgrade, and absence is not evidence
        of mismatch."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker",
                            _docker_returning({"example/api:latest": ""}))
        deploy.image_revision_preflight(tmp_path, _selection(tmp_path, self.COMPOSE))

    def test_no_value_label_is_treated_as_absent(self, tmp_path, monkeypatch):
        """docker's --format renders a missing key as the literal '<no value>';
        comparing that string against a revision would refuse every unlabelled
        image."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker",
                            _docker_returning({"example/api:latest": "<no value>"}))
        deploy.image_revision_preflight(tmp_path, _selection(tmp_path, self.COMPOSE))

    def test_absent_image_is_not_a_mismatch(self, tmp_path, monkeypatch):
        """A missing image is compose's problem to report; inventing a mismatch
        verdict for it would be a wrong answer dressed as a safety check."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_returning({}))
        deploy.image_revision_preflight(tmp_path, _selection(tmp_path, self.COMPOSE))

    def test_dirty_tree_warns_and_does_not_refuse(self, tmp_path, monkeypatch, capsys):
        """Uncommitted changes are in NO image, so nothing can match. Refusing
        here would fire on every dev-loop deploy and get switched off for good —
        a rule nobody can keep enforces nothing."""
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345-dirty")
        monkeypatch.setattr(deploy.procutil, "docker",
                            _docker_returning({"example/api:latest": "deadbeef"}))
        deploy.image_revision_preflight(tmp_path, _selection(tmp_path, self.COMPOSE))
        assert "dirty" in capsys.readouterr().out

    def test_non_git_checkout_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "dev")
        monkeypatch.setattr(deploy.procutil, "docker",
                            _docker_returning({"example/api:latest": "deadbeef"}))
        deploy.image_revision_preflight(tmp_path, _selection(tmp_path, self.COMPOSE))

    def test_no_preflight_skips_entirely(self, tmp_path, monkeypatch):
        def explode(*_a, **_kw):  # pragma: no cover - must never run
            raise AssertionError("docker inspected despite --no-preflight")

        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", explode)
        deploy.image_revision_preflight(
            tmp_path, _selection(tmp_path, self.COMPOSE), no_preflight=True
        )

    def test_missing_rendered_compose_is_not_a_mismatch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(deploy.engine, "get_git_hash", lambda: "abc12345")
        monkeypatch.setattr(deploy.procutil, "docker", _docker_returning({}))
        deploy.image_revision_preflight(tmp_path, [{"path": "applications/absent"}])
