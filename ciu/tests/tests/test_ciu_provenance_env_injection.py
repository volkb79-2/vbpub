"""CIU-21 (O2, S17.4) — in-container image revision, injected via the overlay.

Two halves, two seams:

- The overlay-injection half (composefile.generate_overlay's new
  ``image_revisions=`` keyword) needs no docker at all — the map is passed in
  as plain data, so these tests build it by hand.
- The map-BUILDING half (engine._build_image_revisions) is docker-aware and is
  driven through the SAME seam the rest of the suite uses for this:
  ``monkeypatch.setattr(deploy.procutil, "docker", fake)`` (precedent:
  tests/tests/test_ciu_deploy_actions.py:1368) — the gate mounts no docker
  socket, so this is the only way to exercise it in-gate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu import engine  # noqa: E402
from ciu import governance as governance_mod  # noqa: E402
from ciu.composefile import generate_overlay  # noqa: E402


# ---------------------------------------------------------------------------
# composefile.py stays docker-free (S8.1 invariant, verified at carve time —
# pinned here so a future edit cannot silently reintroduce the import).
# ---------------------------------------------------------------------------


def test_composefile_module_imports_no_docker_seam() -> None:
    """The single binding constraint on composefile.py: it must stay
    DOCKER-FREE. O2's map is built in engine.py and passed in as data —
    composefile.py must never gain a docker/procutil/subprocess import."""
    import ciu.composefile as composefile_mod

    source = Path(composefile_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("import docker", "import procutil", "import subprocess"):
        assert forbidden not in source, f"composefile.py must not {forbidden!r}"


# ---------------------------------------------------------------------------
# generate_overlay(image_revisions=...) — the injection half (no docker)
# ---------------------------------------------------------------------------


class TestGenerateOverlayImageRevisions:
    def _stack(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        monkeypatch.setenv("REPO_ROOT", str(repo))
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo))
        stack = repo / "infra" / "app"
        stack.mkdir(parents=True)
        return stack

    def test_unconditional_with_governance_disabled_two_distinct_labels(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REQUIRED discriminator (O2): governance.enabled=false AND two
        services with DIFFERENT baked labels — the only shape that catches
        the 'smuggle the map through the governance dict' false-PASS, which
        every governance-enabled-only test misses."""
        stack = self._stack(tmp_path, monkeypatch)
        compose_yaml = (
            "services:\n"
            "  api:\n    image: myapp/api:abc123\n"
            "  worker:\n    image: myapp/worker:def456\n"
        )
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance={"enabled": False},
            image_revisions={"api": "abc123", "worker": "def456"},
        )
        assert path is not None, (
            "a non-empty image_revisions map must write an overlay even when "
            "governance is disabled and there is no other reason to"
        )
        doc = yaml.safe_load(path.read_text())
        assert doc["services"]["api"]["environment"] == ["CIU_IMAGE_REVISION=abc123"]
        assert doc["services"]["worker"]["environment"] == ["CIU_IMAGE_REVISION=def456"]

    def test_no_governance_table_at_all_still_injects(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """governance=None (no [<root>.governance] table at all) must not
        suppress the injection either — it is unconditional."""
        stack = self._stack(tmp_path, monkeypatch)
        compose_yaml = "services:\n  api:\n    image: myapp/api:abc123\n"
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance=None,
            image_revisions={"api": "abc123"},
        )
        assert path is not None
        doc = yaml.safe_load(path.read_text())
        assert doc["services"]["api"]["environment"] == ["CIU_IMAGE_REVISION=abc123"]

    def test_exempt_services_does_not_exempt_the_injection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """governance.exempt_services governs RESOURCE governance only — the
        exempted service still gets CIU_IMAGE_REVISION."""
        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setenv(governance_mod.CGROUP_PARENT_ENV_VAR, "besteffort.slice")
        compose_yaml = (
            "services:\n"
            "  api:\n    image: myapp/api:abc123\n"
            "  quiet:\n    image: myapp/quiet:xyz789\n"
        )
        gov_cfg = {"enabled": True, "exempt_services": ["quiet"]}
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance=gov_cfg,
            image_revisions={"api": "abc123", "quiet": "xyz789"},
        )
        doc = yaml.safe_load(path.read_text())
        # exempted from RESOURCE governance: no cgroup_parent/mem_limit
        assert "cgroup_parent" not in doc["services"]["quiet"]
        assert "mem_limit" not in doc["services"]["quiet"]
        # but NOT exempted from the provenance injection
        assert doc["services"]["quiet"]["environment"] == ["CIU_IMAGE_REVISION=xyz789"]
        # and the non-exempt service is governed AND labelled, tied to its OWN
        # image's revision (not a shared/ambiguous value)
        assert "cgroup_parent" in doc["services"]["api"]
        assert doc["services"]["api"]["environment"] == ["CIU_IMAGE_REVISION=abc123"]

    def test_append_never_clobber_alongside_ksm_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S15.11 precedent: governance's own KSM LD_PRELOAD injection uses the
        SAME 'environment' merge key. An assignment (svc["environment"] = [...])
        instead of an append would silently drop one or the other — this is
        the CIU-14-class failure O2's observable calls out by name."""
        stack = self._stack(tmp_path, monkeypatch)
        monkeypatch.setenv(governance_mod.CGROUP_PARENT_ENV_VAR, "besteffort.slice")
        shim = tmp_path / "ksm.so"
        shim.write_bytes(b"\x00")
        compose_yaml = "services:\n  api:\n    image: myapp/api:abc123\n"
        gov_cfg = {"enabled": True, "ksm_optin": str(shim)}
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance=gov_cfg,
            image_revisions={"api": "abc123"},
            repo_root=tmp_path / "repo", physical_root=tmp_path / "repo",
        )
        doc = yaml.safe_load(path.read_text())
        env = doc["services"]["api"]["environment"]
        assert "CIU_IMAGE_REVISION=abc123" in env
        assert any(e.startswith("LD_PRELOAD=") for e in env), (
            "the KSM entry must survive — an assignment would have dropped it"
        )

    def test_service_with_no_revision_entry_gets_no_variable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Omitted from the map (no baked label, or build: with no image: yet)
        means OMITTED from the overlay too — never an empty placeholder."""
        stack = self._stack(tmp_path, monkeypatch)
        compose_yaml = (
            "services:\n"
            "  api:\n    image: myapp/api:abc123\n"
            "  builder:\n    build: .\n"
        )
        path = generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml,
            governance=None,
            image_revisions={"api": "abc123"},  # builder deliberately absent
        )
        doc = yaml.safe_load(path.read_text())
        assert "builder" not in doc.get("services", {})
        assert doc["services"]["api"]["environment"] == ["CIU_IMAGE_REVISION=abc123"]

    def test_empty_image_revisions_is_fully_backward_compatible(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No image_revisions (None, the default) and nothing else to wire ->
        still None, byte-identical to pre-O2 behaviour."""
        stack = self._stack(tmp_path, monkeypatch)
        compose_yaml = "services:\n  api:\n    image: myapp/api:abc123\n"
        assert generate_overlay(stack, {}, [], compose_yaml_text=compose_yaml) is None
        assert generate_overlay(
            stack, {}, [], compose_yaml_text=compose_yaml, image_revisions={}
        ) is None


# ---------------------------------------------------------------------------
# engine._build_image_revisions — the map-BUILDING half (docker seam)
# ---------------------------------------------------------------------------


def _docker_inspect_fake(labels: dict[str, str]):
    """Fake procutil.docker for 'image inspect --format ...'."""
    def fake(cmd, **_kw):
        image = cmd[2] if len(cmd) > 2 else ""
        if image not in labels or not labels[image]:
            return subprocess.CompletedProcess(cmd, 0, "<no value>\n", "")
        return subprocess.CompletedProcess(cmd, 0, labels[image] + "\n", "")
    return fake


class TestBuildImageRevisions:
    def test_maps_each_service_to_its_own_labelled_revision(self, monkeypatch):
        monkeypatch.setattr(
            deploy.procutil, "docker",
            _docker_inspect_fake({
                "myapp/api:abc123": "abc123",
                "myapp/worker:def456": "def456",
            }),
        )
        compose_yaml = (
            "services:\n"
            "  api:\n    image: myapp/api:abc123\n"
            "  worker:\n    image: myapp/worker:def456\n"
        )
        result = engine._build_image_revisions(compose_yaml)
        assert result == {"api": "abc123", "worker": "def456"}

    def test_omits_build_only_service_with_no_baked_image(self, monkeypatch):
        monkeypatch.setattr(
            deploy.procutil, "docker",
            _docker_inspect_fake({"myapp/api:abc123": "abc123"}),
        )
        compose_yaml = (
            "services:\n"
            "  api:\n    image: myapp/api:abc123\n"
            "  builder:\n    build: .\n"
        )
        result = engine._build_image_revisions(compose_yaml)
        assert result == {"api": "abc123"}
        assert "builder" not in result

    def test_omits_service_with_no_baked_label(self, monkeypatch):
        """docker --format renders a missing key as '<no value>'; that must
        read as absent, not as a literal revision string."""
        monkeypatch.setattr(
            deploy.procutil, "docker",
            _docker_inspect_fake({"postgres:16": ""}),
        )
        compose_yaml = "services:\n  db:\n    image: postgres:16\n"
        result = engine._build_image_revisions(compose_yaml)
        assert result == {}

    def test_never_uses_get_git_hash(self, monkeypatch):
        """The map must come from each image's OWN baked label — never the
        host tree's current commit. A stale image running against a newer
        checkout must still show its OWN (stale) revision, not the host's."""
        monkeypatch.setattr(
            deploy.procutil, "docker",
            _docker_inspect_fake({"myapp/api:old111": "old111"}),
        )
        monkeypatch.setattr(engine, "get_git_hash", lambda: "new222")
        compose_yaml = "services:\n  api:\n    image: myapp/api:old111\n"
        result = engine._build_image_revisions(compose_yaml)
        assert result == {"api": "old111"}

    def test_calls_deploy_image_revision_label_not_a_reimplementation(self, monkeypatch):
        """Named seam per the handoff: the label lookup must go through
        deploy._image_revision_label, never a second implementation."""
        calls = []
        monkeypatch.setattr(
            deploy, "_image_revision_label",
            lambda image: calls.append(image) or "stubbed",
        )
        compose_yaml = "services:\n  api:\n    image: myapp/api:whatever\n"
        result = engine._build_image_revisions(compose_yaml)
        assert calls == ["myapp/api:whatever"]
        assert result == {"api": "stubbed"}

    def test_no_services_key_returns_empty_map(self):
        assert engine._build_image_revisions("") == {}
        assert engine._build_image_revisions("version: '3'\n") == {}
        assert engine._build_image_revisions("just_a_string") == {}
