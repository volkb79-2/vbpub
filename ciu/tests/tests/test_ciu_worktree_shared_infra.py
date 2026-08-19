"""Tests for S16.1/CIU-22 — `ciu worktree add --shared-infra` and
`worktree.connect_shared_infra_after_up` (`src/ciu/worktree.py`).

Covers the handoff's three oracles:

- O1: add-time validation (`_preflight_shared_infra_for_add`,
  `worktree.add`'s all-or-nothing group) and the recorded intent grammar
  (`parse_shared_infra_config`).
- O2/O3: the post-up join (`connect_shared_infra_after_up`) — reference
  revalidation, target-service discovery, Docker-STATE (never Docker
  diagnostic TEXT) concurrent-connect detection, and reverse-order rollback
  scoped to only THIS invocation's own zero-return connects.

The gate (`tester-unified:local`) has no Docker socket, so every Docker
branch is driven through a scripted fake assigned to `worktree.procutil.docker`
(the seam named in the handoff, precedent:
`test_ciu_deploy_actions.py:1348-1379`) — a strict fake that raises on any
UNSCRIPTED call, so a regression that issues an extra/different Docker
command fails the test immediately instead of silently passing.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, check=False)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway git repo on branch 'main' with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ciu.env\n", encoding="utf-8")
    assert _git(["add", "README.md", ".gitignore"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _network_for(path: Path) -> str:
    return "net-" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]


@pytest.fixture
def fake_generate_env(monkeypatch):
    """Replace the real (subprocess) env generation with one that writes a
    synthetic ciu.env carrying a deterministic INSTANCE_ID and
    DOCKER_NETWORK_INTERNAL for the target's own physical path — no
    docker/subprocess dependency."""
    def fake(path: Path, **_kw) -> int:
        (path / "ciu.env").write_text(
            f'export INSTANCE_ID="{hashlib.sha256(str(path).encode()).hexdigest()[:6]}"\n'
            f'export DOCKER_NETWORK_INTERNAL="{_network_for(path)}"\n',
            encoding="utf-8",
        )
        return 0
    monkeypatch.setattr(worktree, "_generate_env_in", fake)
    monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
    return fake


@pytest.fixture
def ref_worktree(tmp_repo, fake_generate_env):
    """A registered reference worktree with a real ciu.env (created via an
    ordinary, non-shared-infra `add`)."""
    path = worktree.add(tmp_repo, "primary-ref", base="main")
    return path, _network_for(path)


@pytest.fixture
def track_git_add_calls(monkeypatch):
    """Record only real `git worktree add` side-effect calls (never the
    read-only `git worktree list` calls `find_worktree` legitimately makes
    even on a negative path) — proves O1's "no git-add call" requirement."""
    calls: list[list[str]] = []
    real_git = worktree._git

    def wrapper(args, cwd):
        if args[:2] == ["worktree", "add"]:
            calls.append(args)
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", wrapper)
    return calls


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class ScriptedDocker:
    """A strict, ordered-response fake for `worktree.procutil.docker`.

    Rules are matched in registration order by predicate(args). A response
    may be a fixed CompletedProcess, or a list of them consumed one at a time
    (the LAST element sticks once exhausted) — this is what lets a single
    predicate (e.g. "inspect this network's membership") answer differently
    across successive calls, as O2's concurrent-join / rollback fixtures need.
    An unmatched call raises AssertionError: a regression that issues an
    extra or different Docker command fails loudly instead of silently
    returning a default success.
    """

    def __init__(self):
        self.calls: list[list[str]] = []
        self._rules: list[list] = []

    def on(self, predicate, response) -> "ScriptedDocker":
        self._rules.append([predicate, response])
        return self

    def __call__(self, args, **kw):
        args = list(args)
        self.calls.append(args)
        for rule in self._rules:
            predicate, response = rule
            if predicate(args):
                if isinstance(response, list):
                    resp = response[0]
                    if len(response) > 1:
                        rule[1] = response[1:]
                    response = resp
                if isinstance(response, BaseException):
                    raise response
                return response
        raise AssertionError(f"unscripted docker call: {args}")


def _is_network_inspect_exists(args, network):
    return args == ["network", "inspect", network]


def _is_network_membership(args, network):
    return (
        len(args) >= 3 and args[0] == "network" and args[1] == "inspect"
        and args[2] == network and "--format" in args
    )


def _is_ref_project_ps(args, network, project):
    return (
        args and args[0] == "ps"
        and f"network={network}" in args
        and f"label=com.docker.compose.project={project}" in args
    )


def _is_service_ps(args, project, service):
    return (
        args and args[0] == "ps" and "--no-trunc" in args
        and f"label=com.docker.compose.project={project}" in args
        and f"label=com.docker.compose.service={service}" in args
    )


def _is_connect(args, network, cid):
    return args == ["network", "connect", network, cid]


def _is_disconnect(args, network, cid):
    return args == ["network", "disconnect", network, cid]


# ---------------------------------------------------------------------------
# O1 — `worktree add --shared-infra` validation and intent recording
# ---------------------------------------------------------------------------


class TestAddSharedInfra:
    def test_success_records_all_four_fields_in_order(self, tmp_repo, fake_generate_env, ref_worktree, monkeypatch):
        ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="cid1\n"))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "vault-dev-vault"), _proc(0, stdout="cid2\n"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        target = worktree.add(
            tmp_repo, "child", base="main", profile="core,db",
            shared_infra="primary-ref",
            shared_infra_services="api,worker",
            shared_infra_ref_projects="idp-dev-idp,vault-dev-vault",
        )
        overlay_text = (target / "ciu.global.worktree.toml.j2").read_text(encoding="utf-8")
        assert f'ref_path = "{ref_path}"' in overlay_text
        assert f'network = "{ref_network}"' in overlay_text
        assert 'services = ["api", "worker"]' in overlay_text
        assert 'ref_projects = ["idp-dev-idp", "vault-dev-vault"]' in overlay_text
        assert "CIU_SHARED_INFRA" not in (target / "ciu.env").read_text(encoding="utf-8")

    def test_success_round_trips_through_parse_shared_infra_config(
        self, tmp_repo, fake_generate_env, ref_worktree, monkeypatch
    ):
        ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="cid1\n"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        target = worktree.add(
            tmp_repo, "child", base="main", profile="core",
            shared_infra="primary-ref",
            shared_infra_services="api",
            shared_infra_ref_projects="idp-dev-idp",
        )
        values = tomllib.loads(
            (target / "ciu.global.worktree.toml.j2").read_text(encoding="utf-8")
        )
        intent = worktree.parse_shared_infra_config(values)
        assert intent == worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )

    def test_unresolved_ref_fails_before_git_add(self, tmp_repo, track_git_add_calls, monkeypatch):
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="does not resolve"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="nonexistent",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []
        assert fake.calls == []
        assert not (tmp_repo / ".worktrees" / "child").exists()

    def test_missing_partner_flag_fails_before_git_add_and_before_docker(
        self, tmp_repo, track_git_add_calls, monkeypatch
    ):
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="partial group"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                # shared_infra_ref_projects omitted
            )
        assert track_git_add_calls == []
        assert fake.calls == []

    def test_missing_profile_fails_as_partial_group(self, tmp_repo, track_git_add_calls, monkeypatch):
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="partial group"):
            worktree.add(
                tmp_repo, "child", base="main",  # no profile
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []
        assert fake.calls == []

    def test_ordinary_add_without_any_shared_infra_flag_is_unaffected(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        fake = ScriptedDocker()  # any call would raise -- proves none happens
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        target = worktree.add(tmp_repo, "child", base="main", profile="core")
        env_text = (target / "ciu.env").read_text(encoding="utf-8")
        assert "CIU_SHARED_INFRA" not in env_text
        assert fake.calls == []

    def test_ref_env_missing_docker_network_internal_fails(
        self, tmp_repo, fake_generate_env, track_git_add_calls, monkeypatch
    ):
        # A reference worktree whose ciu.env carries no network at all.
        ref = worktree.add(tmp_repo, "primary-ref", base="main")
        (ref / "ciu.env").write_text('export INSTANCE_ID="x"\n', encoding="utf-8")
        track_git_add_calls.clear()  # discard the ref's OWN legitimate add call

        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="DOCKER_NETWORK_INTERNAL"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []
        assert fake.calls == []

    def test_docker_network_inspect_nonzero_fails(
        self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch
    ):
        _ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(1, stderr="no such network"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="does not exist or is not inspectable"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []

    def test_masquerader_fixture_refuses_before_git_add(
        self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch
    ):
        """REQUIRED (O1): the ref network has ONE running container, but it
        carries an UNDECLARED project label -- the declared R query returns
        empty. A bare labelled-container count on the network must never be
        accepted as liveness."""
        _ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        # The declared project's OWN query returns nothing -- the one running
        # container on the network belongs to some other, undeclared project.
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout=""))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="does not look live"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []

    def test_all_r_and_combined_fixture_refuses_before_git_add(
        self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch
    ):
        """REQUIRED (O1): two declared reference projects, only ONE has a
        running labelled container -- liveness is AND-combined, never OR."""
        _ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="cid1\n"))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "vault-dev-vault"), _proc(0, stdout=""))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="vault-dev-vault"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp,vault-dev-vault",
            )
        assert track_git_add_calls == []

    def test_empty_list_item_in_services_fails(self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch):
        _ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="blank items"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api,,worker",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []

    def test_duplicate_list_item_in_ref_projects_fails(
        self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch
    ):
        _ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="duplicate item"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp,idp-dev-idp",
            )
        assert track_git_add_calls == []

    def test_ref_registered_but_no_ciu_env_at_all_fails(
        self, tmp_repo, track_git_add_calls, monkeypatch
    ):
        """The reference is a REAL registered worktree (e.g. `ciu env
        generate` was never run there) with no ciu.env file at all --
        distinct from an existing-but-unreadable or network-less one."""
        res = subprocess.run(
            ["git", "worktree", "add", "-b", "primary-ref",
             str(tmp_repo / ".worktrees" / "primary-ref"), "main"],
            cwd=str(tmp_repo), capture_output=True, text=True,
        )
        assert res.returncode == 0
        track_git_add_calls.clear()

        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="does not exist"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []
        assert fake.calls == []

    def test_ref_ciu_env_unreadable_fails(self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch):
        ref_path, _ref_network = ref_worktree
        env_file = ref_path / "ciu.env"
        env_file.chmod(0o000)
        try:
            fake = ScriptedDocker()
            monkeypatch.setattr(worktree.procutil, "docker", fake)
            with pytest.raises(worktree.WorktreeError, match="could not read"):
                worktree.add(
                    tmp_repo, "child", base="main", profile="core",
                    shared_infra="primary-ref",
                    shared_infra_services="api",
                    shared_infra_ref_projects="idp-dev-idp",
                )
            assert track_git_add_calls == []
            assert fake.calls == []
        finally:
            env_file.chmod(0o644)  # let tmp_path cleanup remove it

    def test_network_inspect_filenotfound_fails(self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch):
        _ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), FileNotFoundError("docker missing"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="could not inspect network"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []

    def test_ref_project_ps_filenotfound_fails(self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch):
        _ref_path, ref_network = ref_worktree
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(
            lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"),
            OSError("docker daemon unreachable"),
        )
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="could not query reference project"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []


# ---------------------------------------------------------------------------
# parse_shared_infra_config — the sole reader
# ---------------------------------------------------------------------------


class TestParseSharedInfraConfig:
    def test_all_absent_returns_none(self):
        assert worktree.parse_shared_infra_config({}) is None
        assert worktree.parse_shared_infra_config({"unrelated": {"value": 1}}) is None

    def test_complete_intent_parses_in_order(self):
        intent = worktree.parse_shared_infra_config({"ciu": {"instance": {
            "shared_infra": {
                "ref_path": "/repo/.worktrees/primary-ref",
                "network": "net-abc123",
                "services": ["api", "worker"],
                "ref_projects": ["idp-dev-idp", "vault-dev-vault"],
            }
        }}})
        assert intent == worktree.SharedInfraIntent(
            ref_path=Path("/repo/.worktrees/primary-ref"),
            network="net-abc123",
            services=("api", "worker"),
            ref_projects=("idp-dev-idp", "vault-dev-vault"),
        )

    def test_partial_intent_raises_naming_missing_fields(self):
        with pytest.raises(worktree.WorktreeError, match="missing=.*network"):
            worktree.parse_shared_infra_config({"ciu": {"instance": {
                "shared_infra": {
                    "ref_path": "/repo/.worktrees/primary-ref",
                    "services": ["api"],
                    "ref_projects": ["idp-dev-idp"],
                }
            }}})

    def test_duplicate_service_in_stored_config_raises(self):
        with pytest.raises(worktree.WorktreeError, match="duplicate"):
            worktree.parse_shared_infra_config({"ciu": {"instance": {
                "shared_infra": {
                    "ref_path": "/repo/.worktrees/primary-ref",
                    "network": "net-abc123", "services": ["api", "api"],
                    "ref_projects": ["idp-dev-idp"],
                }
            }}})

    def test_blank_item_in_stored_config_raises(self):
        with pytest.raises(worktree.WorktreeError, match="non-empty string array"):
            worktree.parse_shared_infra_config({"ciu": {"instance": {
                "shared_infra": {
                    "ref_path": "/repo/.worktrees/primary-ref",
                    "network": "net-abc123", "services": ["api"],
                    "ref_projects": ["idp-dev-idp", "", "vault-dev-vault"],
                }
            }}})


# ---------------------------------------------------------------------------
# O2/O3 — connect_shared_infra_after_up
# ---------------------------------------------------------------------------


COMPOSE_PROJECT = "child-dev-child"


def _base_fake(ref_network: str, ref_projects=("idp-dev-idp",)) -> ScriptedDocker:
    """A ScriptedDocker pre-loaded with the reference-liveness rules every
    connect_shared_infra_after_up call must satisfy before touching targets."""
    fake = ScriptedDocker()
    fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
    for project in ref_projects:
        fake.on(
            lambda a, project=project: _is_ref_project_ps(a, ref_network, project),
            _proc(0, stdout="refcid\n"),
        )
    return fake


class TestConnectSharedInfraAfterUp:
    def test_success_connects_only_absent_selected_targets(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """REQUIRED (O2): two requested services, one already attached, and a
        third UNREQUESTED service. Exactly the absent selected ID is
        connected; the unrequested service is never queried (the strict fake
        would raise if it were)."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api", "worker"), ref_projects=("idp-dev-idp",),
        )
        cid_api = "a" * 64
        cid_worker = "b" * 64

        fake = _base_fake(ref_network)
        fake.on(
            lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"),
            _proc(0, stdout=f"{cid_api}\tchild-api\n"),
        )
        fake.on(
            lambda a: _is_service_ps(a, COMPOSE_PROJECT, "worker"),
            _proc(0, stdout=f"{cid_worker}\tchild-worker\n"),
        )
        # worker already a member; api absent.
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(0, stdout=f"{cid_worker} "))
        fake.on(lambda a: _is_connect(a, ref_network, cid_api), _proc(0))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        connect_calls = [c for c in fake.calls if c[:2] == ["network", "connect"]]
        assert connect_calls == [["network", "connect", ref_network, cid_api]]

    def test_idempotent_rerun_all_already_present_issues_no_connect(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        cid = "c" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(0, stdout=f"{cid} "))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        assert not any(c[:2] == ["network", "connect"] for c in fake.calls)
        assert not any(c[:2] == ["network", "disconnect"] for c in fake.calls)

    def test_concurrent_join_fixture_non_zero_then_present_is_success_no_rollback(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """REQUIRED (O2): a target absent from the ONE membership snapshot has
        its connect return non-zero, but the NEXT membership inspection
        includes it -- success, zero disconnects, no rollback entry."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        cid = "d" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        # 1st membership inspect (pre-connect snapshot): absent.
        # 2nd membership inspect (re-inspect after non-zero connect): present.
        fake.on(lambda a: _is_network_membership(a, ref_network), [_proc(0, stdout=""), _proc(0, stdout=f"{cid} ")])
        # Deliberately NOT an "already exists"-shaped message: this must be
        # proven by STATE (the re-inspect below), not by a text-matcher a
        # regression could satisfy just as easily. A text-matching mutant
        # keyed on "already exists" would fail this fixture; only the real,
        # state-based implementation passes it.
        fake.on(lambda a: _is_connect(a, ref_network, cid), _proc(1, stderr="context deadline exceeded"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)  # must not raise

        assert not any(c[:2] == ["network", "disconnect"] for c in fake.calls)
        # Docker's diagnostic text is never inspected: the fake's stderr text
        # ("context deadline exceeded") looks nothing like an already-exists
        # message and is irrelevant to the state-based verdict either way.

    def test_genuine_failure_fixture_non_zero_then_absent_raises_and_no_rollback_needed(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """REQUIRED (O2): a target's connect returns non-zero and the
        re-inspection STILL excludes it -- a genuine failure."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        cid = "e" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(0, stdout=""))
        fake.on(lambda a: _is_connect(a, ref_network, cid), _proc(1, stderr="no such container"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.1\]"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        assert not any(c[:2] == ["network", "disconnect"] for c in fake.calls)
        assert not any(c[0] == "compose" for c in fake.calls)  # never `docker compose down`

    def test_three_target_rollback_discriminator(self, tmp_repo, ref_worktree, monkeypatch):
        """REQUIRED (O3): A is a pre-existing member (never touched); B is
        absent and connects successfully (zero); C is absent and its connect
        fails with re-inspection still absent. The disconnect set must be
        EXACTLY [B] -- never A (pre-existing) or C (never actually joined)."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("svc-a", "svc-b", "svc-c"), ref_projects=("idp-dev-idp",),
        )
        cid_a, cid_b, cid_c = "a" * 64, "b" * 64, "c" * 64

        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-a"), _proc(0, stdout=f"{cid_a}\tchild-a\n"))
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-b"), _proc(0, stdout=f"{cid_b}\tchild-b\n"))
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-c"), _proc(0, stdout=f"{cid_c}\tchild-c\n"))
        # 1st membership inspect (pre-connect snapshot): only A present.
        # 2nd membership inspect (re-inspect after C's failed connect): A + B
        # (B's own connect succeeded for real in between).
        fake.on(
            lambda a: _is_network_membership(a, ref_network),
            [_proc(0, stdout=f"{cid_a} "), _proc(0, stdout=f"{cid_a} {cid_b} ")],
        )
        fake.on(lambda a: _is_connect(a, ref_network, cid_b), _proc(0))
        fake.on(lambda a: _is_connect(a, ref_network, cid_c), _proc(1, stderr="no such container"))
        fake.on(lambda a: _is_disconnect(a, ref_network, cid_b), _proc(0))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="child-c"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        connect_calls = [c for c in fake.calls if c[:2] == ["network", "connect"]]
        disconnect_calls = [c for c in fake.calls if c[:2] == ["network", "disconnect"]]
        assert connect_calls == [
            ["network", "connect", ref_network, cid_b],
            ["network", "connect", ref_network, cid_c],
        ]
        assert disconnect_calls == [["network", "disconnect", ref_network, cid_b]]

    def test_reinspect_failure_after_failed_connect_still_rolls_back(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """REQUIRED (defect 4 fix): B connects successfully (real membership
        created); C's connect then fails AND the re-inspect call issued to
        classify that failure ALSO fails (non-zero). This must still roll
        back B -- the earlier bug let the re-inspect's own WorktreeError
        propagate straight past the rollback logic, leaving a real,
        CIU-created membership stranded on the reference network."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("svc-b", "svc-c"), ref_projects=("idp-dev-idp",),
        )
        cid_b, cid_c = "7" * 64, "8" * 64

        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-b"), _proc(0, stdout=f"{cid_b}\tchild-b\n"))
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-c"), _proc(0, stdout=f"{cid_c}\tchild-c\n"))
        # 1st membership inspect (pre-connect snapshot): both absent.
        # 2nd membership inspect (re-inspect after C's failed connect): the
        # inspect call ITSELF fails.
        fake.on(
            lambda a: _is_network_membership(a, ref_network),
            [_proc(0, stdout=""), _proc(1, stderr="no such network")],
        )
        fake.on(lambda a: _is_connect(a, ref_network, cid_b), _proc(0))
        fake.on(lambda a: _is_connect(a, ref_network, cid_c), _proc(1, stderr="no such container"))
        fake.on(lambda a: _is_disconnect(a, ref_network, cid_b), _proc(0))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="could not inspect shared-infra network"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        disconnect_calls = [c for c in fake.calls if c[:2] == ["network", "disconnect"]]
        assert disconnect_calls == [["network", "disconnect", ref_network, cid_b]]

    def test_reinspect_failure_and_rollback_disconnect_failure_both_surface(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """Same as above, but the rollback disconnect ALSO fails -- both
        failures must surface in the final message, never silently dropped."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("svc-b", "svc-c"), ref_projects=("idp-dev-idp",),
        )
        cid_b, cid_c = "9" * 64, "0" * 64

        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-b"), _proc(0, stdout=f"{cid_b}\tchild-b\n"))
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-c"), _proc(0, stdout=f"{cid_c}\tchild-c\n"))
        fake.on(
            lambda a: _is_network_membership(a, ref_network),
            [_proc(0, stdout=""), _proc(1, stderr="daemon unreachable")],
        )
        fake.on(lambda a: _is_connect(a, ref_network, cid_b), _proc(0))
        fake.on(lambda a: _is_connect(a, ref_network, cid_c), _proc(1, stderr="no such container"))
        fake.on(lambda a: _is_disconnect(a, ref_network, cid_b), _proc(1, stderr="endpoint not found"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError) as exc_info:
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        message = str(exc_info.value)
        assert "could not inspect shared-infra network" in message
        assert "rollback also failed for" in message
        assert cid_b in message and "endpoint not found" in message

    def test_ref_no_longer_registered_fails_before_any_docker_call(self, tmp_repo, tmp_path, monkeypatch):
        intent = worktree.SharedInfraIntent(
            ref_path=tmp_repo / ".worktrees" / "gone",
            network="net-whatever", services=("api",), ref_projects=("idp-dev-idp",),
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="no longer a registered worktree"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert fake.calls == []

    def test_ref_network_changed_fails_before_any_docker_call(self, tmp_repo, ref_worktree, monkeypatch):
        ref_path, _real_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network="stale-network-name",
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="network changed"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert fake.calls == []

    def test_reference_project_equal_to_current_project_fails_before_docker(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=(COMPOSE_PROJECT,),
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="OWN compose project"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert fake.calls == []

    def test_declared_target_service_absent_fails_before_any_connect(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api", "ghost"), ref_projects=("idp-dev-idp",),
        )
        cid = "f" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "ghost"), _proc(0, stdout=""))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="ghost"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        assert not any(c[:2] == ["network", "connect"] for c in fake.calls)

    def test_masquerader_fixture_at_post_up_revalidation_refuses(self, tmp_repo, ref_worktree, monkeypatch):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout=""))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="does not look live"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert not any(c[:2] == ["network", "connect"] for c in fake.calls)

    def test_ref_ciu_env_missing_at_post_up_fails(self, tmp_repo, monkeypatch):
        """The reference is registered (real `git worktree add`) but its
        ciu.env does not exist -- distinct from an unreadable or
        network-less one."""
        res = subprocess.run(
            ["git", "worktree", "add", "-b", "primary-ref",
             str(tmp_repo / ".worktrees" / "primary-ref"), "main"],
            cwd=str(tmp_repo), capture_output=True, text=True,
        )
        assert res.returncode == 0
        ref_path = tmp_repo / ".worktrees" / "primary-ref"

        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network="net-whatever",
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="does not exist"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert fake.calls == []

    def test_ref_ciu_env_unreadable_at_post_up_fails(self, tmp_repo, ref_worktree, monkeypatch):
        ref_path, ref_network = ref_worktree
        env_file = ref_path / "ciu.env"
        env_file.chmod(0o000)
        try:
            intent = worktree.SharedInfraIntent(
                ref_path=ref_path, network=ref_network,
                services=("api",), ref_projects=("idp-dev-idp",),
            )
            fake = ScriptedDocker()
            monkeypatch.setattr(worktree.procutil, "docker", fake)
            with pytest.raises(worktree.WorktreeError, match="could not read"):
                worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
            assert fake.calls == []
        finally:
            env_file.chmod(0o644)

    def test_membership_inspect_nonzero_raises(self, tmp_repo, ref_worktree, monkeypatch):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        cid = "1" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(1, stderr="no such network"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="could not inspect shared-infra network"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

    def test_membership_inspect_filenotfound_raises_worktree_error(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """REQUIRED (defect 2 fix): `_network_container_ids` must wrap
        FileNotFoundError/OSError into WorktreeError like every other Docker
        call site in this feature -- an unwrapped OSError here would escape
        connect_shared_infra_after_up raw, miss engine's `except
        worktree.WorktreeError` translation, and crash the whole `ciu
        deploy` run instead of failing just this one stack."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        cid = "6" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), FileNotFoundError("docker missing"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="could not inspect shared-infra network"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

    def test_service_ps_filenotfound_raises(self, tmp_repo, ref_worktree, monkeypatch):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), FileNotFoundError("docker missing"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="could not query shared-infra service"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

    def test_connect_call_filenotfound_raises_and_message_has_no_rollback(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        cid = "2" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(0, stdout=""))
        fake.on(lambda a: _is_connect(a, ref_network, cid), OSError("docker daemon unreachable"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="docker daemon unreachable"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

    def test_rollback_disconnect_raises_and_returns_nonzero_both_surface_in_message(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """Two earlier successful connects (B1, B2), then a genuine failure on
        C. Rollback runs in REVERSE order (B2 then B1): B2's disconnect call
        itself raises OSError, B1's disconnect returns non-zero -- both
        failure shapes must surface in the final error message alongside the
        original C failure, never swallowed."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("svc-b1", "svc-b2", "svc-c"), ref_projects=("idp-dev-idp",),
        )
        cid_b1, cid_b2, cid_c = "3" * 64, "4" * 64, "5" * 64

        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-b1"), _proc(0, stdout=f"{cid_b1}\tchild-b1\n"))
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-b2"), _proc(0, stdout=f"{cid_b2}\tchild-b2\n"))
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "svc-c"), _proc(0, stdout=f"{cid_c}\tchild-c\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(0, stdout=""))
        fake.on(lambda a: _is_connect(a, ref_network, cid_b1), _proc(0))
        fake.on(lambda a: _is_connect(a, ref_network, cid_b2), _proc(0))
        fake.on(lambda a: _is_connect(a, ref_network, cid_c), _proc(1, stderr="no such container"))
        fake.on(lambda a: _is_disconnect(a, ref_network, cid_b2), OSError("daemon gone"))
        fake.on(lambda a: _is_disconnect(a, ref_network, cid_b1), _proc(1, stderr="endpoint not found"))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError) as exc_info:
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        message = str(exc_info.value)
        assert "child-c" in message  # original failure retained
        assert "rollback also failed for" in message
        assert cid_b2 in message and "daemon gone" in message
        assert cid_b1 in message and "endpoint not found" in message
        disconnect_calls = [c for c in fake.calls if c[:2] == ["network", "disconnect"]]
        assert disconnect_calls == [
            ["network", "disconnect", ref_network, cid_b2],
            ["network", "disconnect", ref_network, cid_b1],
        ]

    def test_all_r_and_combined_at_post_up_revalidation(self, tmp_repo, ref_worktree, monkeypatch):
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp", "vault-dev-vault"),
        )
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="cid\n"))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "vault-dev-vault"), _proc(0, stdout=""))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="vault-dev-vault"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
