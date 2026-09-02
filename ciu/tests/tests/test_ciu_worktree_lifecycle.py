"""S16.9 (CIU-25 substrate) — the LIFECYCLE wiring: `ciu up` stamps ownership
labels and claims a lease, `ciu clean` drops the claim on success.

`test_ciu_worktree_lease.py` owns the record schema, the config key and the
`ciu worktree lease` verb. This file's job is narrower and complementary:
prove the pipeline calls that substrate at the right moment, under the right
gate, with values read from the right place —

- BOTH behaviors are gated on "this checkout carries a
  `ciu.worktree-instance.json`". A PRIMARY / unmanaged checkout is provably
  untouched: no lease, no labels, no new files;
- ownership label values come from THIS workspace's OWN generated `ciu.env`,
  read by EXACT PATH — never the ambient process environment (the CIU-41
  contamination species). The fixtures below deliberately set a DIFFERENT
  ambient identity than the workspace's own, so a regression that reads
  ambient state fails loudly instead of coincidentally agreeing;
- the label fragment is a SEPARATE compose `-f`, produced entirely on the
  engine side, because `composefile.generate_overlay` legitimately returns
  `None` for a stack with nothing else to wire — and the plainest stack's
  containers still need to be attributable;
- a lease is claimed BEFORE the compose invocation (a crashed run has still
  created containers), and dropped only by a teardown that SUCCEEDED.

No Docker: `execute_docker_compose_with_logs` is replaced with a canned
result, mirroring `test_ciu_engine_worktree_budget.py`'s established seam.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, engine, worktree  # noqa: E402
from ciu.config_constants import MACHINE_DIR, SHIPPED_COMPOSE  # noqa: E402


GLOBAL_DEFAULTS = """\
[ciu]
require_fqdn = false
require_certs = false
auto_connect_network = false

[deploy]
project_name = "lease"
environment_tag = "test"
log_level = "INFO"

[deploy.labels]
prefix = "lease"

[deploy.env.shared]
CONTAINER_UID = "$CONTAINER_UID"
CONTAINER_GID = "$CONTAINER_GID"
DOCKER_GID = "$DOCKER_GID"
"""

STACK_DEFAULTS = """\
[demo]
name = "demo"
image = "alpine:3"
"""

COMPOSE = """\
services:
  {{ demo.name }}:
    image: {{ demo.image }}
volumes:
  demo-data: {}
  borrowed:
    external: true
networks:
  demo-net: {}
  workspace:
    external: true
    name: lease-net
"""

WORKSPACE_INSTANCE_ID = "wksp01"


def _record_body(root: Path) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "logical_name": "managed",
            "display_name": "managed",
            "branch": "managed",
            "git_worktree_path": str(root.resolve()),
            "ciu_root_offset": ".",
            "created_at_utc": "2026-08-17T12:34:56Z",
            "base_ref": "main",
            "state": "ready",
            "runtime": {
                "instance_id": WORKSPACE_INSTANCE_ID,
                "network": "lease-net",
            },
            "recovery_status": None,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def _base_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "lease-net")
    monkeypatch.setenv("CONTAINER_UID", str(os.getuid()))
    monkeypatch.setenv("CONTAINER_GID", str(os.getgid()))
    monkeypatch.setenv("DOCKER_GID", str(os.getgid()))
    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "1")
    # The CIU-41 trap, armed on purpose: the AMBIENT identity disagrees with
    # what this workspace's own generated overlay facts say. Any label that
    # ends up carrying these values was read from the wrong place.
    monkeypatch.setenv("INSTANCE_ID", "ambient-wrong")


def _write_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, managed: bool
) -> Path:
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(GLOBAL_DEFAULTS)
    # Both outputs of a real `ciu env generate`: the legacy `ciu.env` export
    # (still what `bootstrap_workspace_env` treats as "already generated") and
    # the overlay's generated table, which since CIU-75 is the identity CIU
    # actually reads back.
    (tmp_path / "ciu.env").write_text(
        "\n".join(
            f'export {key}="{os.environ[key]}"'
            for key in (
                "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
                "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID",
            )
        )
        + f'\nexport INSTANCE_ID="{WORKSPACE_INSTANCE_ID}"\n'
    )
    from ciu.workspace_env import write_generated_facts

    write_generated_facts(
        tmp_path,
        {
            "repo_name": "repo",
            "instance_id": WORKSPACE_INSTANCE_ID,
            "network": os.environ["DOCKER_NETWORK_INTERNAL"],
            "physical_repo_root": os.environ["PHYSICAL_REPO_ROOT"],
            "repo_root": os.environ["REPO_ROOT"],
            "public_fqdn": "",
        },
    )
    (tmp_path / ".gitignore").write_text("**/.ciu/\n")
    if managed:
        (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).write_text(
            _record_body(tmp_path), encoding="utf-8"
        )
    stack = tmp_path / "applications" / "demo"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text(STACK_DEFAULTS)
    (stack / "ciu.compose.yml.j2").write_text(COMPOSE)
    return stack


class SpyCompose:
    """Records the exact `-f` argument list `up` composed with."""

    def __init__(self, order: list | None = None):
        self.calls: list[list[str]] = []
        self._order = order

    def __call__(self, file_args, **kwargs):
        self.calls.append(list(file_args))
        if self._order is not None:
            self._order.append("compose")
        return {"status": "success", "message": "", "stdout": "up\n"}


@pytest.fixture
def hermetic_engine(monkeypatch):
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
    monkeypatch.setattr(engine, "create_hostdirs", lambda *a, **k: None)


# ===========================================================================
# O4 — the ownership label pair, and where its values come from
# ===========================================================================


class TestOwnershipLabelResolution:
    def test_unmanaged_checkout_gets_no_labels_at_all(self, tmp_path, monkeypatch):
        """O4's negative: a PRIMARY checkout's resources are NOT labeled by
        this package — widening a future destructive verb's field of view to
        the primary workspace needs its own decision, not a side effect."""
        _write_repo(tmp_path, monkeypatch, managed=False)
        assert engine.workspace_ownership_labels(tmp_path) is None

    def test_managed_instance_labels_come_from_its_own_ciu_env(
        self, tmp_path, monkeypatch
    ):
        """review_focus: the ambient INSTANCE_ID is deliberately WRONG here."""
        _write_repo(tmp_path, monkeypatch, managed=True)
        assert os.environ["INSTANCE_ID"] == "ambient-wrong"
        assert engine.workspace_ownership_labels(tmp_path) == {
            "ciu.instance": WORKSPACE_INSTANCE_ID,
            "ciu.repo-root": str(tmp_path),
        }

    def test_label_names_are_the_closed_pair(self):
        assert engine.OWNERSHIP_LABEL_INSTANCE == "ciu.instance"
        assert engine.OWNERSHIP_LABEL_REPO_ROOT == "ciu.repo-root"

    def test_a_managed_instance_with_an_identity_less_overlay_refuses(
        self, tmp_path, monkeypatch, write_instance_facts
    ):
        """Guessing an owner is worse than not labeling: a mislabeled
        container attributes one instance's resources to another's root."""
        _write_repo(tmp_path, monkeypatch, managed=True)
        write_instance_facts(tmp_path, repo_root="/x")
        with pytest.raises(ValueError, match=r"\[S16.9\].*instance_id"):
            engine.workspace_ownership_labels(tmp_path)


class TestOwnershipFragment:
    LABELS = {"ciu.instance": "abc123", "ciu.repo-root": "/host/repo"}
    ENTRIES = ["ciu.instance=abc123", "ciu.repo-root=/host/repo"]

    def test_every_service_volume_and_network_up_creates_is_labeled(self, tmp_path):
        import yaml

        path = engine.write_ownership_overlay(tmp_path, COMPOSE.replace(
            "{{ demo.name }}", "demo").replace("{{ demo.image }}", "alpine:3"),
            self.LABELS)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc["services"]["demo"]["labels"] == self.ENTRIES
        assert doc["volumes"]["demo-data"]["labels"] == self.ENTRIES
        assert doc["networks"]["demo-net"]["labels"] == self.ENTRIES

    def test_external_volumes_and_networks_are_never_labeled(self, tmp_path):
        """`up` did not create them — the workspace network is created by
        `ciu env generate` (S2.6), and compose rejects extra keys there."""
        import yaml

        path = engine.write_ownership_overlay(tmp_path, COMPOSE.replace(
            "{{ demo.name }}", "demo").replace("{{ demo.image }}", "alpine:3"),
            self.LABELS)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "borrowed" not in doc["volumes"]
        assert "workspace" not in doc["networks"]

    def test_written_under_the_machine_dir_next_to_the_overlay(self, tmp_path):
        path = engine.write_ownership_overlay(
            tmp_path, "services:\n  a:\n    image: x\n", self.LABELS
        )
        assert path == tmp_path / MACHINE_DIR / engine.OWNERSHIP_OVERLAY_NAME

    def test_a_stack_with_nothing_labelable_writes_no_file(self, tmp_path):
        assert engine.write_ownership_overlay(tmp_path, "{}\n", self.LABELS) is None
        assert not (tmp_path / MACHINE_DIR).exists()

    def test_a_non_mapping_compose_document_writes_no_file(self, tmp_path):
        assert engine.write_ownership_overlay(tmp_path, "- a\n- b\n", self.LABELS) is None

    def test_services_only_stack_still_gets_labels(self, tmp_path):
        import yaml

        path = engine.write_ownership_overlay(
            tmp_path, "services:\n  a:\n    image: x\n", self.LABELS
        )
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc == {"services": {"a": {"labels": self.ENTRIES}}}

    @pytest.mark.parametrize(
        "text", ["services:\n  a: {}\n", "services: []\nvolumes: []\n"]
    )
    def test_non_mapping_or_absent_top_level_blocks_are_skipped(self, tmp_path, text):
        import yaml

        result = engine.write_ownership_overlay(tmp_path, text, self.LABELS)
        if result is None:
            return
        doc = yaml.safe_load(result.read_text(encoding="utf-8"))
        assert "volumes" not in doc and "networks" not in doc


# ===========================================================================
# O4 — `ciu up` wiring
# ===========================================================================


class TestUpStampsOwnershipLabels:
    def test_managed_instance_adds_the_fragment_to_the_compose_invocation(
        self, tmp_path, monkeypatch, hermetic_engine
    ):
        import yaml

        stack = _write_repo(tmp_path, monkeypatch, managed=True)
        spy = SpyCompose()
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True
        )

        assert result["status"] == "success"
        fragment = f"{MACHINE_DIR}/{engine.OWNERSHIP_OVERLAY_NAME}"
        assert spy.calls[0][-2:] == ["-f", fragment]
        doc = yaml.safe_load((stack / fragment).read_text(encoding="utf-8"))
        assert doc["services"]["demo"]["labels"] == [
            f"ciu.instance={WORKSPACE_INSTANCE_ID}",
            f"ciu.repo-root={tmp_path}",
        ]

    def test_unmanaged_checkout_composes_exactly_as_before(
        self, tmp_path, monkeypatch, hermetic_engine
    ):
        stack = _write_repo(tmp_path, monkeypatch, managed=False)
        spy = SpyCompose()
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", spy)

        engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True
        )

        fragment = f"{MACHINE_DIR}/{engine.OWNERSHIP_OVERLAY_NAME}"
        assert fragment not in spy.calls[0]
        assert not (stack / fragment).exists()

    def test_reset_removes_the_fragment_and_downs_with_it(
        self, tmp_path, monkeypatch
    ):
        stack = tmp_path / "demo"
        (stack / MACHINE_DIR).mkdir(parents=True)
        fragment = stack / MACHINE_DIR / engine.OWNERSHIP_OVERLAY_NAME
        fragment.write_text("services: {}\n", encoding="utf-8")
        (stack / "ciu.compose.yml").write_text("services: {}\n", encoding="utf-8")
        seen: list[list[str]] = []
        monkeypatch.setattr(
            engine.procutil, "run_cmd",
            lambda cmd, **kw: (seen.append(cmd), MagicMock(returncode=0))[1],
        )
        monkeypatch.setattr(engine.procutil, "docker", lambda *a, **kw: MagicMock(
            returncode=0, stdout="", stderr=""
        ))

        engine.reset_service(
            {"deploy": {
                "project_name": "p", "environment_tag": "e",
                "labels": {"prefix": "p"},
            }},
            stack, assume_yes=True, repo_root=tmp_path,
        )

        assert f"{MACHINE_DIR}/{engine.OWNERSHIP_OVERLAY_NAME}" in seen[0]
        assert not fragment.exists()


# ===========================================================================
# O3 — `ciu up` claims the lease
# ===========================================================================


class TestUpAcquiresTheLease:
    def test_no_ttl_configured_means_no_lease_at_all(self, tmp_path, monkeypatch):
        """O2's additive default, at the call site: a consumer who configured
        nothing takes on zero new expiry risk."""
        monkeypatch.setattr(
            engine.worktree, "resolve_worktree_lease_ttl", lambda _r: None
        )
        called: list = []
        monkeypatch.setattr(
            engine.worktree, "acquire_own_lease",
            lambda *a, **k: called.append(a),
        )
        assert engine.acquire_instance_lease(tmp_path) is None
        assert called == []

    def test_configured_ttl_is_passed_through_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            engine.worktree, "resolve_worktree_lease_ttl", lambda _r: 6.0
        )
        seen: list = []
        monkeypatch.setattr(
            engine.worktree, "acquire_own_lease",
            lambda root, *, ttl_hours: seen.append((root, ttl_hours)) or "rec",
        )
        assert engine.acquire_instance_lease(tmp_path) == "rec"
        assert seen == [(tmp_path, 6.0)]

    def test_up_claims_the_lease_before_compose_runs(
        self, tmp_path, monkeypatch, hermetic_engine
    ):
        """Ordering is the point: a run that crashes halfway has still created
        containers, so the claim must already be on disk."""
        stack = _write_repo(tmp_path, monkeypatch, managed=True)
        monkeypatch.setattr(
            engine.worktree, "resolve_worktree_lease_ttl", lambda _r: 24.0
        )
        order: list[str] = []
        spy = SpyCompose(order=order)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", spy)
        real_acquire = engine.worktree.acquire_own_lease
        monkeypatch.setattr(
            engine.worktree, "acquire_own_lease",
            lambda *a, **k: (order.append("lease"), real_acquire(*a, **k))[1],
        )

        engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True
        )

        assert order == ["lease", "compose"]
        stored = json.loads(
            (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).read_text("utf-8")
        )
        assert stored["schema_version"] == 2
        assert stored["lease"]["mode"] == "held"
        assert stored["lease"]["holder"].endswith(f":{WORKSPACE_INSTANCE_ID}")

    def test_up_renews_an_existing_lease_rather_than_restarting_it(
        self, tmp_path, monkeypatch, hermetic_engine
    ):
        stack = _write_repo(tmp_path, monkeypatch, managed=True)
        monkeypatch.setattr(
            engine.worktree, "resolve_worktree_lease_ttl", lambda _r: 24.0
        )
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", SpyCompose())
        engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True
        )
        first = json.loads(
            (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).read_text("utf-8")
        )["lease"]
        engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True
        )
        second = json.loads(
            (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).read_text("utf-8")
        )["lease"]
        assert second["acquired_at_utc"] == first["acquired_at_utc"]

    def test_a_primary_checkout_never_acquires_a_lease(
        self, tmp_path, monkeypatch, hermetic_engine
    ):
        """review_focus: even with a TTL configured, an unmanaged checkout
        gets no lease and no record is invented for it."""
        stack = _write_repo(tmp_path, monkeypatch, managed=False)
        monkeypatch.setattr(
            engine.worktree, "resolve_worktree_lease_ttl", lambda _r: 24.0
        )
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", SpyCompose())

        engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True
        )

        assert not (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).exists()

    def test_a_dry_run_claims_nothing(self, tmp_path, monkeypatch, hermetic_engine):
        stack = _write_repo(tmp_path, monkeypatch, managed=True)
        monkeypatch.setattr(
            engine.worktree, "resolve_worktree_lease_ttl", lambda _r: 24.0
        )
        before = (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).read_text("utf-8")

        engine.main_execution(
            working_dir=stack, define_root=tmp_path, dry_run=True,
            skip_hostdir_check=True,
        )

        assert (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).read_text(
            "utf-8"
        ) == before


class TestShippedModeParity:
    """`ciu up --shipped` (S8.5) consumes a maintainer-owned compose file and
    generates no CIU artifacts. The lease follows it anyway (a pure record
    write); the label FRAGMENT deliberately does not, because `ciu clean`
    skips `reset_service` for a shipped stack and would never remove it."""

    def _shipped(self, tmp_path: Path, monkeypatch, *, managed: bool) -> Path:
        _write_repo(tmp_path, monkeypatch, managed=managed)
        stack = tmp_path / "vendor" / "legacy"
        stack.mkdir(parents=True)
        (stack / SHIPPED_COMPOSE).write_text("services:\n  legacy:\n    image: alpine:3\n")
        return stack

    def test_shipped_up_still_claims_the_lease(self, tmp_path, monkeypatch):
        stack = self._shipped(tmp_path, monkeypatch, managed=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", SpyCompose())
        monkeypatch.setattr(
            engine.worktree, "resolve_worktree_lease_ttl", lambda _r: 24.0
        )

        assert engine.run_shipped(stack, define_root=tmp_path)["status"] == "success"

        stored = json.loads(
            (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).read_text("utf-8")
        )
        assert stored["lease"]["mode"] == "held"

    def test_shipped_up_writes_no_label_fragment_into_a_vendored_stack(
        self, tmp_path, monkeypatch
    ):
        """Pinned as DELIBERATE (SPEC S16.9 'Still open'), not accidental: an
        artifact `clean` never removes has no business under vendor content."""
        stack = self._shipped(tmp_path, monkeypatch, managed=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", SpyCompose())

        engine.run_shipped(stack, define_root=tmp_path)

        assert not (stack / MACHINE_DIR / engine.OWNERSHIP_OVERLAY_NAME).exists()


# ===========================================================================
# O3 — `ciu clean` drops the claim, ON SUCCESS ONLY
# ===========================================================================


class TestCleanClearsTheLease:
    def _profile(self):
        profile = MagicMock()
        profile.config = {
            "deploy": {"project_name": "proj", "environment_tag": "env"}
        }
        return profile

    def _hermetic(self, monkeypatch, *, survivors: list[str]):
        monkeypatch.setattr(deploy, "render_selected_stacks", lambda *a, **k: {})
        monkeypatch.setattr(deploy, "_matching_containers", lambda *a, **k: [])
        monkeypatch.setattr(
            deploy, "_remove_project_volumes", lambda cfg=None, **_kw: survivors
        )

    def _leased(self, root: Path) -> Path:
        path = root / worktree.WORKTREE_INSTANCE_RECORD
        path.write_text(_record_body(root), encoding="utf-8")
        worktree.acquire_own_lease(root, ttl_hours=24)
        return path

    def test_successful_clean_drops_the_claim(self, tmp_path, monkeypatch):
        path = self._leased(tmp_path)
        self._hermetic(monkeypatch, survivors=[])
        assert deploy.action_clean(
            tmp_path, self._profile(), [], ignore_errors=True
        ) == 0
        assert json.loads(path.read_text(encoding="utf-8"))["lease"] is None

    def test_a_failed_clean_leaves_the_claim_standing(self, tmp_path, monkeypatch):
        """review_focus / O3's negative: erasing the lease over resources that
        may still be running would manufacture 'unowned' out of 'unknown'."""
        path = self._leased(tmp_path)
        before = path.read_text(encoding="utf-8")
        self._hermetic(monkeypatch, survivors=["proj-env-data"])
        assert deploy.action_clean(
            tmp_path, self._profile(), [], ignore_errors=True
        ) == 1
        assert path.read_text(encoding="utf-8") == before

    def test_an_unmanaged_checkout_is_untouched(self, tmp_path, monkeypatch):
        self._hermetic(monkeypatch, survivors=[])
        assert deploy.action_clean(
            tmp_path, self._profile(), [], ignore_errors=True
        ) == 0
        assert not (tmp_path / worktree.WORKTREE_INSTANCE_RECORD).exists()

    def test_an_unreadable_record_warns_without_failing_the_clean(
        self, tmp_path, monkeypatch, capsys
    ):
        """What clean CERTIFIES is that the resources are gone, and they are.
        A record too malformed to parse is a pre-existing S16 defect this
        teardown neither caused nor can repair — and leaving the claim in
        place is the SAFE direction: a future reap reads it as still owned."""
        path = tmp_path / worktree.WORKTREE_INSTANCE_RECORD
        path.write_text('{"schema_version": 1}\n', encoding="utf-8")
        self._hermetic(monkeypatch, survivors=[])
        assert deploy.action_clean(
            tmp_path, self._profile(), [], ignore_errors=True
        ) == 0
        out = capsys.readouterr().out
        assert "S16.9 lease not cleared" in out
        assert path.read_text(encoding="utf-8") == '{"schema_version": 1}\n'
