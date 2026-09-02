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
    (repo / ".gitignore").write_text(
        "ciu.env\nciu.global.instance.toml.j2\nciu.instance.generated.toml\n",
        encoding="utf-8",
    )
    assert _git(["add", "README.md", ".gitignore"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _network_for(path: Path) -> str:
    return "net-" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:8]


@pytest.fixture
def fake_generate_env(monkeypatch, write_instance_facts):
    """Replace the real (subprocess) env generation with one that writes
    synthetic `[ciu.instance.generated]` facts carrying a deterministic
    instance_id and network for the target's own physical path — no
    docker/subprocess dependency. CIU-75: the generated facts file
    (`ciu.instance.generated.toml` since ciu-P47), not `ciu.env`, is what CIU
    reads back."""
    def fake(path: Path, **_kw) -> int:
        write_instance_facts(
            path,
            instance_id=hashlib.sha256(str(path).encode()).hexdigest()[:6],
            network=_network_for(path),
            repo_root=str(path),
            physical_repo_root=str(path),
            repo_name="repo",
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
        overlay_text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        assert f'ref_path = "{ref_path}"' in overlay_text
        assert f'network = "{ref_network}"' in overlay_text
        assert 'services = ["api", "worker"]' in overlay_text
        assert 'ref_projects = ["idp-dev-idp", "vault-dev-vault"]' in overlay_text
        # CIU-75: the intent lives in the overlay's own table, never smuggled
        # into the legacy env export.
        assert "CIU_SHARED_INFRA" not in overlay_text

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
            (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
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
        overlay_text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        assert "shared_infra" not in overlay_text
        assert fake.calls == []

    def test_ref_env_missing_docker_network_internal_fails(
        self, tmp_repo, fake_generate_env, track_git_add_calls, monkeypatch,
        write_instance_facts,
    ):
        # A reference worktree whose generated facts carry no network at all.
        ref = worktree.add(tmp_repo, "primary-ref", base="main")
        write_instance_facts(ref, instance_id="x", network="")
        track_git_add_calls.clear()  # discard the ref's OWN legitimate add call

        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(
            worktree.WorktreeError, match="declares no generated instance network"
        ):
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
        generate` was never run there) with no generated overlay table at all
        -- distinct from an existing-but-unreadable or network-less one."""
        res = subprocess.run(
            ["git", "worktree", "add", "-b", "primary-ref",
             str(tmp_repo / ".worktrees" / "primary-ref"), "main"],
            cwd=str(tmp_repo), capture_output=True, text=True,
        )
        assert res.returncode == 0
        track_git_add_calls.clear()

        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(
            worktree.WorktreeError, match="declares no generated instance network"
        ):
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
        env_file = ref_path / "ciu.instance.generated.toml"
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

    def test_ref_ciu_env_malformed_entry_fails(
        self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch
    ):
        """CIU-62 — the reference's generated table EXISTS and is readable,
        but one entry is malformed: `WorkspaceEnvError`, a `ValueError`
        subclass that the pre-fix `except OSError` did not catch. Refusing
        here is the whole point of this preflight (O1: no git-add, no docker
        call). CIU-75 moved the source; the refusal contract is unchanged."""
        ref_path, _ref_network = ref_worktree
        (ref_path / "ciu.instance.generated.toml").write_text(
            "[ciu.instance.generated]\nnetwork = not-a-toml-value\n",
            encoding="utf-8",
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.1\] could not read"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []
        assert fake.calls == []

    def test_ref_ciu_env_non_utf8_fails(
        self, tmp_repo, ref_worktree, track_git_add_calls, monkeypatch
    ):
        """CIU-62 — a non-UTF-8 byte raises `UnicodeDecodeError`, a SIBLING
        of `WorkspaceEnvError` under `ValueError`. Neither name alone covers
        it, and `OSError` covers neither."""
        ref_path, _ref_network = ref_worktree
        (ref_path / "ciu.instance.generated.toml").write_bytes(
            b'[ciu.instance.generated]\nnetwork = "\xff\xfe"\n'
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.1\] could not read"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra="primary-ref",
                shared_infra_services="api",
                shared_infra_ref_projects="idp-dev-idp",
            )
        assert track_git_add_calls == []
        assert fake.calls == []

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
        generated overlay table does not exist -- distinct from an unreadable
        or network-less one."""
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
        with pytest.raises(worktree.WorktreeError, match="reference network changed"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert fake.calls == []

    def test_ref_ciu_env_unreadable_at_post_up_fails(self, tmp_repo, ref_worktree, monkeypatch):
        ref_path, ref_network = ref_worktree
        env_file = ref_path / "ciu.instance.generated.toml"
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

    def test_ref_ciu_env_malformed_entry_at_post_up_fails(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """CIU-62 — post-up revalidation half. A malformed entry
        (`WorkspaceEnvError`) escaped the pre-fix `except OSError` here, so
        the join's own "has the reference's network changed?" guard could be
        skipped by a traceback rather than answered."""
        ref_path, ref_network = ref_worktree
        (ref_path / "ciu.instance.generated.toml").write_text(
            "[ciu.instance.generated]\nnetwork = not-a-toml-value\n",
            encoding="utf-8",
        )
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.1\] could not read"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert fake.calls == []

    def test_ref_ciu_env_non_utf8_at_post_up_fails(self, tmp_repo, ref_worktree, monkeypatch):
        """CIU-62 — and the non-UTF-8 byte (`UnicodeDecodeError`)."""
        ref_path, ref_network = ref_worktree
        (ref_path / "ciu.instance.generated.toml").write_bytes(
            b'[ciu.instance.generated]\nnetwork = "\xff\xfe"\n'
        )
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.1\] could not read"):
            worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)
        assert fake.calls == []

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


# ===========================================================================
# S16.1/CIU-52 — `shared_infra.ref_services` reference-service addressing
# ===========================================================================
#
# The load-bearing distinction this whole feature rests on, re-derived from
# the shipped code above rather than assumed:
#
#   * `services`     -> THIS (joining) instance's OWN diverging containers,
#                       discovered by `com.docker.compose.project=<THIS
#                       instance's compose project>` in
#                       connect_shared_infra_after_up's target loop.
#   * `ref_projects` -> the REFERENCE's compose projects, consulted ONLY by
#                       _check_reference_network_and_projects for liveness.
#
# They are two independent lists about two different instances — never
# positionally paired (the shipped fixtures above use two services against
# one ref project, and the connect loop never consults ref_projects per
# service). So a REFERENCE-side service is a third axis with no name in the
# old schema, and an alias must never be inferred from `services`: that would
# point this instance's own copy of a service at the reference's copy of it.
# `ref_services` is that third axis.


REF_PROJECT = "dstdns"
REF_TAG = "aaaaaa"
REF_VAULT = f"{REF_PROJECT}-{REF_TAG}-vault"
# An UNRELATED third instance C, running its own vault under the same
# project name but its own tag.
C_VAULT = f"{REF_PROJECT}-cccccc-vault"


def _is_ref_service_ps(args, network, service):
    """The CIU-52 authentication query: RUNNING containers on the REFERENCE's
    network carrying a service label, deliberately NOT scoped by compose
    project (the container NAME carries the reference's identity, and that is
    the authenticating fact). The final clause keeps this predicate from ever
    matching the pre-existing project-scoped target-discovery query."""
    return (
        args and args[0] == "ps" and "--no-trunc" in args
        and f"network={network}" in args
        and f"label=com.docker.compose.service={service}" in args
        and not any(
            a.startswith("label=com.docker.compose.project=") for a in args
        )
    )


def _write_ref_global(ref_path: Path, *, port: int | None = 8200) -> None:
    """The REFERENCE instance's own committed global defaults.

    `environment_tag` is deliberately expanded from `$INSTANCE_ID` rather than
    hardcoded: that is what makes the environ-isolation oracle (8) meaningful
    — a leak of the CALLING process's ambient INSTANCE_ID would visibly change
    the derived container name.
    """
    body = (
        "[deploy]\n"
        f'project_name = "{REF_PROJECT}"\n'
        'environment_tag = "${INSTANCE_ID}"\n'
    )
    if port is not None:
        body += f"\n[topology.services.vault]\ninternal_port = {port}\n"
    (ref_path / "ciu.global.defaults.toml.j2").write_text(body, encoding="utf-8")


@pytest.fixture
def ref_instance(tmp_repo, fake_generate_env, write_instance_facts):
    """Reference instance A: a registered worktree whose OWN generated overlay
    facts pin instance_id=aaaaaa, so its own rendered config derives
    `dstdns-aaaaaa-<service>` (CIU-75: those facts, not `ciu.env`, are what
    the reference's chain renders against)."""
    path = worktree.add(tmp_repo, "primary-ref", base="main")
    network = _network_for(path)
    write_instance_facts(
        path,
        instance_id=REF_TAG,
        network=network,
        repo_root=str(path),
        physical_repo_root=str(path),
        repo_name="repo",
    )
    _write_ref_global(path)
    return path, network


def _add_fake(ref_network, *, vault_live=(REF_VAULT,), services=("vault",)):
    """A ScriptedDocker carrying the reference-liveness rules plus the CIU-52
    authentication query's answer for each service."""
    fake = ScriptedDocker()
    fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
    fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="refcid\n"))
    for service in services:
        fake.on(
            lambda a, service=service: _is_ref_service_ps(a, ref_network, service),
            _proc(0, stdout="".join(f"{n}\n" for n in vault_live)),
        )
    return fake


def _add_child(tmp_repo, ref_services, **kw):
    return worktree.add(
        tmp_repo, kw.pop("name", "child"), base="main", profile="core",
        shared_infra="primary-ref",
        shared_infra_services="api",
        shared_infra_ref_projects="idp-dev-idp",
        shared_infra_ref_services=ref_services,
        **kw,
    )


class TestRefServicesAddTimeResolution:
    def test_headline_contract_resolves_qualified_host_and_port(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 1 — the whole point: a bare `--shared-infra-ref-services
        vault` produces this instance's own `topology.services.vault` block
        addressing the REFERENCE's qualified container, with NO hand-written
        override anywhere in the fixture."""
        _ref_path, ref_network = ref_instance
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        target = _add_child(tmp_repo, "vault")

        values = tomllib.loads(
            (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        )
        assert values["topology"]["services"]["vault"] == {
            "internal_host": REF_VAULT,
            "internal_port": 8200,
        }

    def test_controlled_wrong_derivation_is_refused_before_any_git_mutation(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """ORACLE 2 — the controlled wrong implementation: derivation yields
        the BARE service name (exactly the CIU-49 bug relocated to the shared-
        infra path) instead of the qualified form. Add-time authentication
        against live Docker state catches it, names both the computed name and
        what is actually live, and refuses BEFORE `git worktree add`.

        Without the authentication step this mutant writes `internal_host =
        "vault"` and ships — which is the entire reason the step exists."""
        _ref_path, ref_network = ref_instance
        track_git_add_calls.clear()  # discard the reference's own legitimate add
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        from ciu import deploy as deploy_mod
        monkeypatch.setattr(deploy_mod, "container_name", lambda _cfg, svc: svc)

        with pytest.raises(worktree.WorktreeError) as exc_info:
            _add_child(tmp_repo, "vault")

        message = str(exc_info.value)
        assert "'vault'" in message              # the computed (wrong) name
        assert REF_VAULT in message              # what is actually live
        assert "not live on network" in message
        assert track_git_add_calls == []
        assert not (tmp_repo / ".worktrees" / "child").exists()

    def test_three_instance_non_interference(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 3 — the filing's own named fixture, made adversarial:
        an unrelated instance C runs its OWN vault and is ALSO connected to
        A's network carrying the IDENTICAL
        `com.docker.compose.service=vault` label, so the live-name query
        returns both. B's resolution is A's container ALWAYS, because it is
        derived from A's own rendered config rather than chosen out of
        whatever the network happens to contain."""
        _ref_path, ref_network = ref_instance
        monkeypatch.setattr(
            worktree.procutil, "docker",
            _add_fake(ref_network, vault_live=(C_VAULT, REF_VAULT)),
        )

        target = _add_child(tmp_repo, "vault")

        values = tomllib.loads(
            (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        )
        assert values["topology"]["services"]["vault"]["internal_host"] == REF_VAULT
        assert C_VAULT not in (
            target / "ciu.global.instance.toml.j2"
        ).read_text(encoding="utf-8")

    def test_three_instance_impostor_alone_is_refused_not_adopted(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """ORACLE 3 (sharp edge) — A's vault is DOWN and only unrelated C's
        vault is on the network under the same service label. A resolution
        that picked "the vault-labelled container on this network" would
        silently address C. Authentication refuses instead."""
        _ref_path, ref_network = ref_instance
        track_git_add_calls.clear()
        monkeypatch.setattr(
            worktree.procutil, "docker",
            _add_fake(ref_network, vault_live=(C_VAULT,)),
        )

        with pytest.raises(worktree.WorktreeError) as exc_info:
            _add_child(tmp_repo, "vault")

        message = str(exc_info.value)
        assert REF_VAULT in message and C_VAULT in message
        assert track_git_add_calls == []

    def test_rename_escape_hatch_writes_only_the_alias_block(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 9 — `secrets=vault`: A's vault container is addressed under
        THIS instance's own local name `secrets`; no `topology.services.vault`
        block is written at all."""
        _ref_path, ref_network = ref_instance
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        target = _add_child(tmp_repo, "secrets=vault")

        text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        values = tomllib.loads(text)
        assert values["topology"]["services"]["secrets"]["internal_host"] == REF_VAULT
        assert "vault" not in values["topology"]["services"]
        assert "[topology.services.vault]" not in text
        assert values["ciu"]["instance"]["shared_infra"]["ref_services"] == {
            "secrets": {"service": "vault", "container": REF_VAULT, "port": 8200},
        }

    def test_port_omission_invents_nothing(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 13 — the reference declares no internal_port for the
        service: the overlay writes internal_host only, and no `port` key
        lands in the recorded sub-table either."""
        ref_path, ref_network = ref_instance
        _write_ref_global(ref_path, port=None)
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        target = _add_child(tmp_repo, "vault")

        text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        values = tomllib.loads(text)
        assert values["topology"]["services"]["vault"] == {"internal_host": REF_VAULT}
        assert "internal_port" not in text
        assert values["ciu"]["instance"]["shared_infra"]["ref_services"]["vault"] == {
            "service": "vault", "container": REF_VAULT,
        }

    def test_non_integer_reference_port_is_not_recorded(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """A reference declaring a non-integer internal_port is treated as
        declaring none — never coerced, never copied through."""
        ref_path, ref_network = ref_instance
        (ref_path / "ciu.global.defaults.toml.j2").write_text(
            "[deploy]\n"
            f'project_name = "{REF_PROJECT}"\n'
            'environment_tag = "${INSTANCE_ID}"\n'
            "\n[topology.services.vault]\n"
            'internal_port = "8200"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        target = _add_child(tmp_repo, "vault")
        values = tomllib.loads(
            (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        )
        assert values["topology"]["services"]["vault"] == {"internal_host": REF_VAULT}

    def test_two_aliases_may_share_one_reference_service(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        _ref_path, ref_network = ref_instance
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        target = _add_child(tmp_repo, "secrets=vault,vault")
        values = tomllib.loads(
            (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        )
        services = values["topology"]["services"]
        assert services["secrets"]["internal_host"] == REF_VAULT
        assert services["vault"]["internal_host"] == REF_VAULT

    def test_reference_without_deploy_identity_refuses_naming_the_service(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """The reference's own config cannot produce a qualified name (no
        deploy.project_name): a loud refusal naming the service and the
        reference root, never a bare or partially-qualified guess."""
        ref_path, ref_network = ref_instance
        (ref_path / "ciu.global.defaults.toml.j2").write_text(
            '[deploy]\nenvironment_tag = "${INSTANCE_ID}"\n', encoding="utf-8"
        )
        track_git_add_calls.clear()
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        with pytest.raises(
            worktree.WorktreeError, match="could not resolve reference service 'vault'"
        ):
            _add_child(tmp_repo, "vault")
        assert track_git_add_calls == []

    def test_reference_with_no_deploy_identity_refuses(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """A reference carrying no committed global config still renders (its
        gitignored overlay alone is a legal chain layer since CIU-60), but it
        then names no `deploy.project_name`, so no reference container can be
        derived — a loud refusal before any git mutation, never a guess."""
        ref_path, ref_network = ref_instance
        (ref_path / "ciu.global.defaults.toml.j2").unlink()
        track_git_add_calls.clear()
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        with pytest.raises(
            worktree.WorktreeError,
            match="could not resolve reference service 'vault'",
        ):
            _add_child(tmp_repo, "vault")
        assert track_git_add_calls == []

    def test_reference_whose_own_chain_cannot_render_refuses(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """The render arm itself: a reference whose committed template is not
        parseable TOML fails at `render_global_chain`, and that failure is
        reported as the reference's own, not silently swallowed."""
        ref_path, ref_network = ref_instance
        (ref_path / "ciu.global.defaults.toml.j2").write_text(
            "[deploy\nproject_name = ", encoding="utf-8"
        )
        track_git_add_calls.clear()
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        with pytest.raises(
            worktree.WorktreeError,
            match="could not render the shared-infra reference's own global",
        ):
            _add_child(tmp_repo, "vault")
        assert track_git_add_calls == []

    def test_derived_container_name_that_is_not_a_legal_name_refuses(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """A reference whose deploy identity would derive an illegal container
        name refuses rather than recording a value that a later `$VAR`
        expansion or secret scan would have to cope with."""
        ref_path, ref_network = ref_instance
        (ref_path / "ciu.global.defaults.toml.j2").write_text(
            '[deploy]\nproject_name = "not a name"\nenvironment_tag = "x"\n',
            encoding="utf-8",
        )
        track_git_add_calls.clear()
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        with pytest.raises(worktree.WorktreeError, match="not a legal container name"):
            _add_child(tmp_repo, "vault")
        assert track_git_add_calls == []


class TestRefServicesRenderIsolation:
    def test_reference_checkout_gains_no_rendered_config_and_ignores_ambient_env(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 8 — resolution reads the reference READ-ONLY and under the
        reference's OWN environment:

        * no `ciu.global.toml` is written into the reference's checkout
          (`write_rendered=False`), and
        * a POISONED ambient `INSTANCE_ID`/`REPO_ROOT` in the calling process
          does not reach the reference's templates (`environ=ref_env`) — the
          reference's `environment_tag` interpolates `$INSTANCE_ID`, so a leak
          would visibly rewrite the derived container name to
          `dstdns-poison-vault`.
        """
        ref_path, ref_network = ref_instance
        monkeypatch.setenv("INSTANCE_ID", "poison")
        monkeypatch.setenv("REPO_ROOT", "/nowhere")
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        before = {p.name for p in ref_path.iterdir()}
        target = _add_child(tmp_repo, "vault")
        after = {p.name for p in ref_path.iterdir()}

        assert after == before
        assert not (ref_path / "ciu.global.toml").exists()
        text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        assert REF_VAULT in text
        assert "poison" not in text


class TestRefServicesBackwardCompatibility:
    def _overlay_of(self, tmp_repo, name, ref_services, ref_network, monkeypatch):
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))
        target = _add_child(tmp_repo, ref_services, name=name)
        return (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")

    def test_omitting_the_flag_is_byte_identical_and_costs_zero_docker_calls(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 5 — the backward-compatibility invariant, proven as a real
        byte comparison against the overlay the PRE-CIU-52 code path produces
        (the shipped four-line shape, reconstructed here literally), plus a
        strict-fake call-list comparison showing not one extra Docker call at
        add time."""
        ref_path, ref_network = ref_instance
        without = ScriptedDocker()
        without.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        without.on(
            lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"),
            _proc(0, stdout="refcid\n"),
        )
        monkeypatch.setattr(worktree.procutil, "docker", without)

        target = worktree.add(
            tmp_repo, "child", base="main", profile="core",
            shared_infra="primary-ref",
            shared_infra_services="api",
            shared_infra_ref_projects="idp-dev-idp",
        )
        text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")

        # ciu-P47: the CIU-owned identity table is no longer appended into
        # this file — it has its own — so the byte comparison is once again
        # exactly the four-line pre-CIU-52 shape the worktree writer emits,
        # and nothing else has touched the file since.
        assert text == (
            "# Worktree-local sparse global override (S3.1b / S16).\n"
            "# Durable configuration: preserved by `ciu clean` and `ciu env generate`.\n"
            "[ciu.instance]\n"
            'service_profiles = ["core"]\n'
            "\n"
            "[ciu.instance.shared_infra]\n"
            f'ref_path = "{ref_path}"\n'
            f'network = "{ref_network}"\n'
            'services = ["api"]\n'
            'ref_projects = ["idp-dev-idp"]\n'
        )
        # The identity facts landed in their own file, in full, unchanged.
        assert (target / "ciu.instance.generated.toml").read_text(
            encoding="utf-8"
        ).endswith(
            "[ciu.instance.generated]\n"
            'repo_name = "repo"\n'
            f'instance_id = "{hashlib.sha256(str(target).encode()).hexdigest()[:6]}"\n'
            f'network = "{_network_for(target)}"\n'
            f'physical_repo_root = "{target}"\n'
            f'repo_root = "{target}"\n'
            'public_fqdn = ""\n'
        )
        assert without.calls == [
            ["network", "inspect", ref_network],
            [
                "ps",
                "--filter", f"network={ref_network}",
                "--filter", "label=com.docker.compose.project=idp-dev-idp",
                "--format", "{{.ID}}",
            ],
        ]

        intent = worktree.parse_shared_infra_config(tomllib.loads(text))
        assert intent.ref_services == ()

    def test_omitting_the_flag_costs_zero_extra_docker_calls_at_join_time(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """ORACLE 5 (join half) — an intent with no ref_services issues the
        exact pre-CIU-52 join call sequence; the strict fake would raise on
        any additional query."""
        ref_path, ref_network = ref_worktree
        intent = worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        cid = "a" * 64
        fake = _base_fake(ref_network)
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(0, stdout=""))
        fake.on(lambda a: _is_connect(a, ref_network, cid), _proc(0))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        worktree.connect_shared_infra_after_up(tmp_repo, COMPOSE_PROJECT, intent)

        assert not any(_is_ref_service_ps(c, ref_network, "vault") for c in fake.calls)
        assert fake.calls[-1] == ["network", "connect", ref_network, cid]


class TestRefServicesSchemaRoundTrip:
    def test_overlay_round_trips_to_the_exact_intent(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 4 — overlay text -> tomllib -> parse_shared_infra_config
        reproduces the exact SharedInfraIntent, ref_services included."""
        ref_path, ref_network = ref_instance
        monkeypatch.setattr(
            worktree.procutil, "docker",
            _add_fake(ref_network, services=("vault", "consul")),
        )
        (ref_path / "ciu.global.defaults.toml.j2").write_text(
            "[deploy]\n"
            f'project_name = "{REF_PROJECT}"\n'
            'environment_tag = "${INSTANCE_ID}"\n'
            "\n[topology.services.vault]\ninternal_port = 8200\n",
            encoding="utf-8",
        )
        target = _add_child(tmp_repo, "vault")

        text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        intent = worktree.parse_shared_infra_config(tomllib.loads(text))
        assert intent == worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
            ref_services=(
                worktree.SharedInfraRefService(
                    alias="vault", service="vault", container=REF_VAULT, port=8200
                ),
            ),
        )

    def test_declaration_order_is_canonicalised_so_the_round_trip_is_exact(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """Aliases are recorded sorted, at BOTH resolution and parse, so a
        flag written in any order round-trips to one canonical intent."""
        _ref_path, ref_network = ref_instance
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        target = _add_child(tmp_repo, "zulu=vault,alpha=vault")
        text = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        intent = worktree.parse_shared_infra_config(tomllib.loads(text))
        assert [e.alias for e in intent.ref_services] == ["alpha", "zulu"]
        assert text.index("[topology.services.alpha]") < text.index(
            "[topology.services.zulu]"
        )


class TestRefServicesClosedShape:
    """ORACLE 6 — the shape is WIDENED by exactly one optional key, not
    opened."""

    def _wrap(self, shared_infra):
        return {"ciu": {"instance": {"shared_infra": shared_infra}}}

    def _base(self, **extra):
        return self._wrap({
            "ref_path": "/repo/.worktrees/primary-ref",
            "network": "net-abc123",
            "services": ["api"],
            "ref_projects": ["idp-dev-idp"],
            **extra,
        })

    def test_unknown_top_level_key_is_still_named(self):
        with pytest.raises(worktree.WorktreeError, match=r"unknown=\['aliases'\]"):
            worktree.parse_shared_infra_config(self._base(aliases=["vault"]))

    def test_missing_required_key_is_still_named(self):
        with pytest.raises(worktree.WorktreeError, match="missing=.*network"):
            worktree.parse_shared_infra_config(self._wrap({
                "ref_path": "/repo/.worktrees/primary-ref",
                "services": ["api"],
                "ref_projects": ["idp-dev-idp"],
                "ref_services": {"vault": {"service": "vault", "container": REF_VAULT}},
            }))

    def test_ref_services_is_optional(self):
        intent = worktree.parse_shared_infra_config(self._base())
        assert intent.ref_services == ()

    @pytest.mark.parametrize("entry, match", [
        ({"service": "vault"}, r"missing=\['container'\]"),
        ({"container": REF_VAULT}, r"missing=\['service'\]"),
        (
            {"service": "vault", "container": REF_VAULT, "extra": 1},
            r"unknown=\['extra'\]",
        ),
        ({"service": "vault", "container": REF_VAULT, "port": "8200"}, "must be an integer"),
        ({"service": "vault", "container": REF_VAULT, "port": True}, "must be an integer"),
        ({"service": 7, "container": REF_VAULT}, "service must be a string"),
        ({"service": "VAULT", "container": REF_VAULT}, "service must be a string"),
        ({"service": "vault", "container": 7}, "container must be a string"),
        ({"service": "vault", "container": "$VAULT"}, "container must be a string"),
    ])
    def test_malformed_sub_table_refuses(self, entry, match):
        with pytest.raises(worktree.WorktreeError, match=match):
            worktree.parse_shared_infra_config(self._base(ref_services={"vault": entry}))

    def test_non_table_and_empty_table_refuse(self):
        for value in (["vault"], {}, "vault"):
            with pytest.raises(
                worktree.WorktreeError, match="must be a non-empty table of tables"
            ):
                worktree.parse_shared_infra_config(self._base(ref_services=value))

    def test_sub_table_that_is_not_a_table_refuses(self):
        with pytest.raises(worktree.WorktreeError, match=r"ref_services\.vault must be a table"):
            worktree.parse_shared_infra_config(self._base(ref_services={"vault": "x"}))

    def test_illegal_alias_key_in_stored_config_refuses(self):
        with pytest.raises(worktree.WorktreeError, match="alias '9bad' must match"):
            worktree.parse_shared_infra_config(self._base(ref_services={
                "9bad": {"service": "vault", "container": REF_VAULT},
            }))


class TestRefServicesGrammarRefusals:
    """ORACLE 11 — every grammar refusal happens before any side effect: no
    git worktree add, and (because the grammar is checked alongside its two
    sibling flags) no Docker call either."""

    @pytest.mark.parametrize("raw, match", [
        ("", "non-empty comma-separated list"),
        ("vault,,consul", "blank items"),
        ("vault,vault", "duplicate item"),
        ("vault,vault=vault", "duplicate alias"),
        ("9bad=vault", "alias '9bad' must match"),
        ("a b=vault", "alias 'a b' must match"),
        ("vault=VAULT", "reference service 'VAULT' .* must match"),
        ("vault=-nope", "reference service '-nope' .* must match"),
        ("vault=$SECRET", r"reference service '\$SECRET' .* must match"),
        ("va$ult=vault", r"alias 'va\$ult' must match"),
        ("a=b=c", "reference service 'b=c' .* must match"),
    ])
    def test_refusal_before_any_side_effect(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch, raw, match
    ):
        _ref_path, ref_network = ref_instance
        track_git_add_calls.clear()
        fake = _add_fake(ref_network)
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match=match):
            _add_child(tmp_repo, raw)
        assert track_git_add_calls == []
        assert fake.calls == []

    def test_ref_services_alone_is_a_partial_group_refusal(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """ORACLE 12 — the flag is optional but never standalone: without the
        rest of the group it is refused before any git or Docker call."""
        _ref_path, _ref_network = ref_instance
        track_git_add_calls.clear()
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="partial group"):
            worktree.add(
                tmp_repo, "child", base="main", profile="core",
                shared_infra_ref_services="vault",
            )
        assert track_git_add_calls == []
        assert fake.calls == []

    def test_adopt_ref_services_alone_is_a_partial_group_refusal(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        fake = ScriptedDocker()
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match="all-or-nothing"):
            worktree.adopt(
                tmp_repo, "adopted", "primary-ref",
                shared_infra_ref_services="vault",
            )
        assert fake.calls == []


class TestRefServicesAuthenticationFailureModes:
    """The reviewer-named question: an authentication that cannot be
    ANSWERED must be a clear determination failure, never an empty (and
    therefore permissive-looking, or falsely-stale) result."""

    def test_docker_binary_missing_is_a_query_failure_not_an_absent_container(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        _ref_path, ref_network = ref_instance
        track_git_add_calls.clear()
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="refcid\n"))
        fake.on(
            lambda a: _is_ref_service_ps(a, ref_network, "vault"),
            FileNotFoundError("docker missing"),
        )
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError) as exc_info:
            _add_child(tmp_repo, "vault")
        message = str(exc_info.value)
        assert "could not query reference service 'vault'" in message
        assert "not live on network" not in message  # not a staleness verdict
        assert track_git_add_calls == []

    def test_daemon_unreachable_is_a_query_failure(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        _ref_path, ref_network = ref_instance
        track_git_add_calls.clear()
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="refcid\n"))
        fake.on(
            lambda a: _is_ref_service_ps(a, ref_network, "vault"),
            OSError("daemon unreachable"),
        )
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="daemon unreachable"):
            _add_child(tmp_repo, "vault")
        assert track_git_add_calls == []

    def test_nonzero_ps_is_a_query_failure_not_an_empty_live_set(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        """A non-zero `docker ps` must NOT collapse into "found: []" — that
        would report a determination CIU never made."""
        _ref_path, ref_network = ref_instance
        track_git_add_calls.clear()
        fake = ScriptedDocker()
        fake.on(lambda a: _is_network_inspect_exists(a, ref_network), _proc(0))
        fake.on(lambda a: _is_ref_project_ps(a, ref_network, "idp-dev-idp"), _proc(0, stdout="refcid\n"))
        fake.on(
            lambda a: _is_ref_service_ps(a, ref_network, "vault"),
            _proc(1, stderr="invalid filter"),
        )
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError) as exc_info:
            _add_child(tmp_repo, "vault")
        message = str(exc_info.value)
        assert "could not query reference service 'vault'" in message
        assert "invalid filter" in message
        assert "found: []" not in message
        assert track_git_add_calls == []

    def test_no_container_at_all_is_a_staleness_verdict_naming_the_empty_set(
        self, tmp_repo, ref_instance, track_git_add_calls, monkeypatch
    ):
        _ref_path, ref_network = ref_instance
        track_git_add_calls.clear()
        monkeypatch.setattr(
            worktree.procutil, "docker", _add_fake(ref_network, vault_live=()),
        )
        with pytest.raises(worktree.WorktreeError) as exc_info:
            _add_child(tmp_repo, "vault")
        message = str(exc_info.value)
        assert "not live on network" in message
        assert "found: []" in message
        assert track_git_add_calls == []

    def test_comma_joined_names_are_split(self, tmp_repo, ref_instance, monkeypatch):
        """`docker ps --format '{{.Names}}'` emits a comma-joined list when a
        container carries several names; the recorded one must still match."""
        _ref_path, ref_network = ref_instance
        monkeypatch.setattr(
            worktree.procutil, "docker",
            _add_fake(ref_network, vault_live=(f"other-alias,{REF_VAULT}",)),
        )
        target = _add_child(tmp_repo, "vault")
        values = tomllib.loads(
            (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
        )
        assert values["topology"]["services"]["vault"]["internal_host"] == REF_VAULT


class TestRefServicesJoinTimeReverification:
    """ORACLE 7 — a write-once `add`-time record is re-proven at join time,
    inside the every-precondition-before-any-side-effect region."""

    def _intent(self, ref_path, ref_network, container=REF_VAULT):
        return worktree.SharedInfraIntent(
            ref_path=ref_path, network=ref_network,
            services=("api",), ref_projects=("idp-dev-idp",),
            ref_services=(
                worktree.SharedInfraRefService(
                    alias="vault", service="vault", container=container, port=8200
                ),
            ),
        )

    def test_drifted_container_refuses_before_any_connect(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """The reference was re-created under a new identity between `add`
        and `up`: the recorded container is gone and an unrelated one now
        answers the same service label. Refuse, and attempt ZERO connects."""
        ref_path, ref_network = ref_worktree
        fake = _base_fake(ref_network)
        fake.on(
            lambda a: _is_ref_service_ps(a, ref_network, "vault"),
            _proc(0, stdout=f"{C_VAULT}\n"),
        )
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError) as exc_info:
            worktree.connect_shared_infra_after_up(
                tmp_repo, COMPOSE_PROJECT, self._intent(ref_path, ref_network)
            )

        message = str(exc_info.value)
        assert "recorded reference container" in message
        assert REF_VAULT in message and C_VAULT in message
        assert "re-run `ciu worktree add --shared-infra`" in message
        assert [c for c in fake.calls if c[:2] == ["network", "connect"]] == []
        # and never even reached this instance's own target discovery
        assert not any(_is_service_ps(c, COMPOSE_PROJECT, "api") for c in fake.calls)

    def test_reverification_precedes_target_discovery_and_then_connects(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        """Present at join time => the pre-existing connect behaviour is
        unchanged, and the re-verification query is issued BEFORE the first
        target-discovery query."""
        ref_path, ref_network = ref_worktree
        cid = "b" * 64
        fake = _base_fake(ref_network)
        fake.on(
            lambda a: _is_ref_service_ps(a, ref_network, "vault"),
            _proc(0, stdout=f"{REF_VAULT}\n"),
        )
        fake.on(lambda a: _is_service_ps(a, COMPOSE_PROJECT, "api"), _proc(0, stdout=f"{cid}\tchild-api\n"))
        fake.on(lambda a: _is_network_membership(a, ref_network), _proc(0, stdout=""))
        fake.on(lambda a: _is_connect(a, ref_network, cid), _proc(0))
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        worktree.connect_shared_infra_after_up(
            tmp_repo, COMPOSE_PROJECT, self._intent(ref_path, ref_network)
        )

        auth_at = next(
            i for i, c in enumerate(fake.calls)
            if _is_ref_service_ps(c, ref_network, "vault")
        )
        target_at = next(
            i for i, c in enumerate(fake.calls)
            if _is_service_ps(c, COMPOSE_PROJECT, "api")
        )
        connect_at = next(
            i for i, c in enumerate(fake.calls) if c[:2] == ["network", "connect"]
        )
        assert auth_at < target_at < connect_at

    def test_reverification_query_failure_refuses_before_any_connect(
        self, tmp_repo, ref_worktree, monkeypatch
    ):
        ref_path, ref_network = ref_worktree
        fake = _base_fake(ref_network)
        fake.on(
            lambda a: _is_ref_service_ps(a, ref_network, "vault"),
            _proc(1, stderr="daemon gone"),
        )
        monkeypatch.setattr(worktree.procutil, "docker", fake)

        with pytest.raises(worktree.WorktreeError, match="could not query reference service"):
            worktree.connect_shared_infra_after_up(
                tmp_repo, COMPOSE_PROJECT, self._intent(ref_path, ref_network)
            )
        assert [c for c in fake.calls if c[:2] == ["network", "connect"]] == []


class TestRefServicesMergeOrder:
    def test_overlay_wins_internal_host_while_committed_port_survives(
        self, tmp_repo, ref_instance, monkeypatch
    ):
        """ORACLE 10 — the emitted `[topology.services.vault]` block is merged
        LAST (the worktree overlay layer), so `internal_host` genuinely
        overrides a committed bare default, while an `internal_port` the
        overlay does NOT write survives from that same committed default.

        This is the end-to-end proof that the block CIU writes is the value
        `secrets/providers.py` will actually read."""
        from ciu import config_model
        from ciu.workspace_env import read_instance_identity_env

        ref_path, ref_network = ref_instance
        _write_ref_global(ref_path, port=None)  # the reference declares no port
        monkeypatch.setattr(worktree.procutil, "docker", _add_fake(ref_network))

        target = _add_child(tmp_repo, "vault")
        (target / "ciu.global.defaults.toml.j2").write_text(
            "[deploy]\n"
            'project_name = "child"\n'
            'environment_tag = "${INSTANCE_ID}"\n'
            "\n[topology.services.vault]\n"
            'internal_host = "vault"\n'
            "internal_port = 8200\n",
            encoding="utf-8",
        )

        merged = config_model.render_global_chain(
            target, target, write_rendered=False,
            environ=read_instance_identity_env(target),
        )
        assert merged["topology"]["services"]["vault"] == {
            "internal_host": REF_VAULT,   # overlay wins
            "internal_port": 8200,        # committed default survives
        }
        assert not (target / "ciu.global.toml").exists()
