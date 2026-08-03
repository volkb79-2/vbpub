"""CR-13a (D-R7): per-use execution containment, and its fail-closed gate.

Two kinds of test, and the split is the point.

- ``TestReal*`` classes start REAL containers against the REAL docker daemon
  and assert the four capability criteria from the outside. A containment
  test that mocks the container proves that the argv was assembled the way
  the test expected -- not that the boundary holds. Each has a POSITIVE
  CONTROL that runs the same image with the one flag removed and SUCCEEDS,
  because "the read failed" is also what a broken command line looks like.
  They skip where docker is unreachable (the gate container has no socket).
- everything else drives the pure functions, including every refusal, with a
  substituted runner. Those cover the same lines, so the boundary is
  measured in both places and the skippable half is never the only proof.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from nyxloom import containment
from nyxloom.config import RouteDef

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE_IMAGE = "alpine:latest"


# ---------------------------------------------------------------------------
# helpers


def _route(**kw) -> RouteDef:
    base = dict(route_id="r1", cli="fake", model="m")
    base.update(kw)
    return RouteDef(**base)


class _Runner:
    """A recording stand-in for ``processes.run`` / ``local_run``.

    ``results`` maps a matched argv fragment to (returncode) or an exception
    to raise, so a test can make exactly one of the two docker calls fail --
    which is the difference between "no docker" and "no image", and the whole
    reason :func:`containment.probe` makes two calls rather than one.
    """

    def __init__(self, version_rc=0, inspect_rc=0, raises=None) -> None:
        self.version_rc, self.inspect_rc, self.raises = version_rc, inspect_rc, raises
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, cwd=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        if self.raises is not None:
            raise self.raises
        rc = self.version_rc if "version" in argv else self.inspect_rc
        return subprocess.CompletedProcess(argv, rc, "", "")


def _docker_available() -> bool:
    try:
        res = subprocess.run(["docker", "image", "inspect", PROBE_IMAGE],
                             capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason=f"needs a reachable docker daemon with {PROBE_IMAGE} present "
           "(the gate container has no docker socket)")


def _run(argv, env=None, timeout=120):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=env if env is not None else os.environ.copy())


def _host_repo_root() -> str | None:
    """The HOST path for this checkout, verified by actually mounting it.

    Not assumed and not read from a config file: the mapping is probed with a
    container that must be able to see a file this process can see. A wrong
    mapping mounts an empty directory rather than failing, so the only honest
    check is to look inside one.
    """
    marker = "pyproject.toml"
    candidates = []
    declared = os.environ.get(containment.HOST_MAP_ENV)
    if declared:
        candidates.append(containment.parse_host_map(declared))
    # The deployed devcontainer/daemon mapping (nyxloomd/docker-compose.yml
    # and nyxloom.toml's own gate argv both hard-code this pair).
    candidates.append((("/workspaces/vbpub", "/home/vb/volkb79-2/vbpub"),))
    candidates.append(())          # identity: not containerized
    for mapping in candidates:
        try:
            host = containment.host_path(str(REPO_ROOT), mapping, inside=True)
        except containment.ContainmentUnavailable:
            continue
        res = _run(["docker", "run", "--rm", "--volume", f"{host}:/probe",
                    PROBE_IMAGE, "test", "-f", f"/probe/{marker}"])
        if res.returncode == 0:
            return host
    return None


# ---------------------------------------------------------------------------
# the requirement


class TestTheRequirementFailsClosed:

    def test_a_route_that_declares_nothing_is_contained(self):
        """The default is the whole lesson of the denylist it replaces: a
        posture nobody declared is not a posture that was granted."""
        assert containment.requires_containment(_route()) is True

    def test_only_an_explicit_operator_trust_declaration_runs_uncontained(self):
        assert containment.requires_containment(
            _route(trust=containment.TRUST_OPERATOR)) is False

    def test_an_explicit_untrusted_declaration_is_contained(self):
        assert containment.requires_containment(
            _route(trust=containment.TRUST_UNTRUSTED)) is True

    def test_a_typo_in_the_trust_value_does_not_read_as_operator(self):
        """`trust = "Operator"` is not a third posture; it is a mistake, and
        a mistake must fall to the contained side."""
        for value in ("Operator", "operator ", "trusted", "yes", ""):
            assert containment.requires_containment(_route(trust=value)) is True
        assert set(containment.TRUST_VALUES) == {"operator", "untrusted"}

    @pytest.mark.parametrize("free_route", [
        _route(status="free"),
        _route(prompt_hints=["free-endpoint"]),
        _route(prompt_hints=["incremental-write", "free-endpoint"]),
    ], ids=["status", "hint", "hint-among-others"])
    def test_a_free_endpoint_is_contained_however_it_was_written(self, free_route):
        assert containment.is_free_endpoint(free_route) is True
        assert containment.requires_containment(free_route) is True

    def test_a_free_route_cannot_be_declared_operator_trusted(self):
        """The one non-waivable case. A free provider may log or train on the
        prompt; that is a property of the endpoint, not of the operator's
        opinion of it, so the declaration loses."""
        route = _route(status="free", trust=containment.TRUST_OPERATOR)
        assert containment.requires_containment(route) is True

    def test_a_paid_route_is_not_mistaken_for_a_free_one(self):
        route = _route(status="fallback-only", prompt_hints=["incremental-write"],
                       trust=containment.TRUST_OPERATOR)
        assert containment.is_free_endpoint(route) is False
        assert containment.requires_containment(route) is False


# ---------------------------------------------------------------------------
# the environment allowlist that replaced the denylist


class TestPerRouteSecretInjection:

    ENV = {
        "PATH": "/usr/bin", "HOME": "/home/op", "LANG": "C",
        "AA_API_KEY": "aa", "NTFY_TOKEN": "pub", "NTFY_CMD_TOKEN": "cmd",
        "OPENROUTER_API_KEY": "or", "DEEPSEEK_API_KEY": "ds",
        "A_SECRET_NOBODY_LISTED": "surprise",
    }

    def test_a_route_receives_only_the_secrets_it_declares(self):
        env = containment.child_env(_route(secrets=["OPENROUTER_API_KEY"]),
                                    environ=self.ENV)
        assert env["OPENROUTER_API_KEY"] == "or"
        for absent in ("AA_API_KEY", "NTFY_TOKEN", "NTFY_CMD_TOKEN",
                       "DEEPSEEK_API_KEY"):
            assert absent not in env

    def test_a_name_nobody_classified_is_absent_rather_than_inherited(self):
        """The defect the denylist could not fix. ``DAEMON_ONLY_ENV`` named
        three secrets; a fourth added to the deployment tomorrow was
        forwarded to every agent until someone remembered to list it. Under
        an allowlist the same name is simply not there."""
        env = containment.child_env(_route(secrets=["OPENROUTER_API_KEY"]),
                                    environ=self.ENV)
        assert "A_SECRET_NOBODY_LISTED" not in env
        assert not (set(env) - set(containment.BASE_ENV_ALLOW)
                    - {"OPENROUTER_API_KEY"})

    def test_declaring_no_secrets_yields_no_secrets(self):
        env = containment.child_env(_route(), environ=self.ENV)
        assert set(env) == {"PATH", "HOME", "LANG"}

    def test_the_operational_allowlist_carries_no_authority(self):
        """Every name a CLI needs merely to START. If one of these could
        authorize anything the allowlist would be a denylist again."""
        assert "AA_API_KEY" not in containment.BASE_ENV_ALLOW
        assert not any("TOKEN" in n or "KEY" in n or "SECRET" in n
                       for n in containment.BASE_ENV_ALLOW)

    def test_a_declared_secret_absent_from_the_environment_is_simply_absent(self):
        env = containment.child_env(_route(secrets=["NOT_SET_ANYWHERE"]),
                                    environ=self.ENV)
        assert "NOT_SET_ANYWHERE" not in env

    def test_overrides_win_and_are_added_even_when_unlisted(self):
        """The attempt identity (CR-03) travels this way, and so does any
        per-dispatch value the daemon chooses deliberately."""
        env = containment.child_env(
            _route(), {"NYXLOOM_ATTEMPT_ID": "att-1", "HOME": "/tmp/x"},
            environ=self.ENV)
        assert env["NYXLOOM_ATTEMPT_ID"] == "att-1"
        assert env["HOME"] == "/tmp/x"

    def test_duplicate_declarations_are_forwarded_once(self):
        route = _route(secrets=["A", "B", "A"])
        assert containment.declared_secrets(route) == ("A", "B")

    def test_the_real_process_environment_is_the_default_source(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "live")
        monkeypatch.setenv("AA_API_KEY", "daemon-only")
        env = containment.child_env(_route(secrets=["OPENROUTER_API_KEY"]))
        assert env["OPENROUTER_API_KEY"] == "live"
        assert "AA_API_KEY" not in env

    def test_the_denylist_stopgap_is_gone_rather_than_kept_alongside(self):
        """CR-13a's obligation, stated mechanically: the RISK-006 denylist is
        REPLACED. Keeping both would mean two answers to "what does a child
        receive", and the weaker one would be the one nobody edits."""
        from nyxloom import wrapper
        assert not hasattr(wrapper, "DAEMON_ONLY_ENV")
        assert not hasattr(wrapper, "child_env")


# ---------------------------------------------------------------------------
# host path translation


class TestHostPathTranslation:

    def test_a_mapped_path_is_translated(self):
        mapping = containment.parse_host_map("/workspaces/vbpub=/home/vb/vbpub")
        assert containment.host_path("/workspaces/vbpub/nyxloom", mapping,
                                     inside=True) == "/home/vb/vbpub/nyxloom"
        assert containment.host_path("/workspaces/vbpub", mapping,
                                     inside=True) == "/home/vb/vbpub"

    def test_the_longest_matching_prefix_wins(self):
        mapping = containment.parse_host_map("/w=/a,/w/inner=/b")
        assert containment.host_path("/w/inner/x", mapping, inside=True) == "/b/x"
        assert containment.host_path("/w/other/x", mapping, inside=True) == "/a/other/x"

    def test_a_prefix_that_is_only_a_string_prefix_does_not_match(self):
        """``/workspaces/vbpub2`` is not inside ``/workspaces/vbpub``."""
        mapping = containment.parse_host_map("/workspaces/vbpub=/host")
        with pytest.raises(containment.ContainmentUnavailable):
            containment.host_path("/workspaces/vbpub2/x", mapping, inside=True)

    def test_an_unmapped_path_refuses_when_this_process_is_containerized(self):
        """The trap ``nyxloomd/docker-compose.yml`` warns about: the host
        daemon CREATES a missing bind source as an empty directory, so the
        alternative to refusing is an agent working in an empty repository
        while every log says containment succeeded."""
        with pytest.raises(containment.ContainmentUnavailable) as exc:
            containment.host_path("/workspaces/other", (), inside=True)
        assert exc.value.reason == "host-path-unmapped:/workspaces/other"

    def test_an_unmapped_path_is_itself_when_this_process_is_not_containerized(self):
        assert containment.host_path("/srv/repo", (), inside=False) == "/srv/repo"

    def test_malformed_mapping_entries_are_dropped_not_raised(self):
        assert containment.parse_host_map("garbage,/a=/b, ,=/c,/d=") == (("/a", "/b"),)
        assert containment.parse_host_map(None) == ()
        assert containment.parse_host_map("") == ()

    def test_trailing_slashes_do_not_create_a_second_mapping(self):
        assert containment.parse_host_map("/a/=/b/") == (("/a", "/b"),)

    def test_container_detection_reads_dockers_own_marker(self, tmp_path):
        assert containment.in_container(tmp_path) is False
        (tmp_path / ".dockerenv").write_text("")
        assert containment.in_container(tmp_path) is True

    def test_the_real_marker_is_consulted_by_default(self):
        assert containment.in_container() is Path("/.dockerenv").exists()


# ---------------------------------------------------------------------------
# the plan


class TestThePlan:

    def _plan(self, route=None, **kw):
        args = dict(worktree="/repo/.worktrees/feat/x", repo_root="/repo",
                    environ={containment.IMAGE_ENV: "agent:local"}, inside=False)
        args.update(kw)
        return containment.plan_for(route or _route(), **args)

    def test_the_declared_repository_is_the_only_mount(self):
        plan = self._plan()
        assert plan.mounts == (("/repo", "/repo"),)
        argv = plan.docker_argv(["claude", "-p", "go"])
        assert argv.count("--volume") == 1
        assert "/var/run/docker.sock" not in " ".join(argv)

    def test_the_repository_is_mounted_at_its_own_path(self):
        """So a worktree path in a prompt, in ``spec.cwd`` and in the agent's
        own git output all denote the same directory on both sides."""
        plan = self._plan()
        src, dst = plan.mounts[0]
        assert dst == "/repo"
        assert plan.workdir == "/repo/.worktrees/feat/x"

    def test_a_containerized_daemon_translates_the_mount_source(self):
        plan = self._plan(
            environ={containment.IMAGE_ENV: "agent:local",
                     containment.HOST_MAP_ENV: "/repo=/host/repo"},
            inside=True)
        assert plan.mounts == (("/host/repo", "/repo"),)

    def test_only_declared_names_cross_the_boundary_and_no_values_do(self):
        plan = self._plan(_route(secrets=["OPENROUTER_API_KEY"]))
        argv = plan.docker_argv(["opencode"])
        assert argv.count("--env") == 1
        assert "OPENROUTER_API_KEY" in argv
        assert not any("=" in a and "API_KEY" in a for a in argv), (
            "a secret VALUE reached the argv; -e NAME must be used so docker "
            "reads the value from its own environment instead")

    def test_overrides_are_forwarded_by_name_too(self):
        plan = self._plan(_route(secrets=["S"]),
                          overrides={"NYXLOOM_ATTEMPT_ID": "att-1"})
        assert plan.env_names == ("S", "NYXLOOM_ATTEMPT_ID")

    def test_the_network_is_not_the_daemons_and_is_configurable(self):
        assert self._plan().network == containment.DEFAULT_NETWORK
        plan = self._plan(environ={containment.IMAGE_ENV: "i",
                                   containment.NETWORK_ENV: "agents-net"})
        assert plan.network == "agents-net"
        assert "agents-net" in plan.docker_argv(["x"])

    def test_capabilities_and_privilege_escalation_are_dropped(self):
        argv = self._plan().docker_argv(["x"])
        assert argv[argv.index("--cap-drop") + 1] == "ALL"
        assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"

    def test_the_container_runs_as_the_daemons_own_uid(self):
        """Work landing in the mounted repository must not become
        root-owned -- an operator who then cannot edit their own checkout has
        been contained rather badly."""
        assert self._plan().user == f"{os.getuid()}:{os.getgid()}"

    def test_the_inner_argv_is_the_tail_and_the_image_immediately_precedes_it(self):
        argv = self._plan().docker_argv(["claude", "--model", "haiku"])
        assert argv[:2] == ["docker", "run"]
        assert argv[-4:] == ["agent:local", "claude", "--model", "haiku"]

    def test_a_container_name_is_added_only_when_asked(self):
        assert "--name" not in self._plan().docker_argv(["x"])
        argv = self._plan().docker_argv(["x"], "nyxloom-att-1")
        assert argv[argv.index("--name") + 1] == "nyxloom-att-1"

    def test_a_container_name_is_derived_from_the_attempt_id(self):
        assert containment.container_name("att-9f2c") == "nyxloom-att-9f2c"
        assert containment.container_name("a/b c") == "nyxloom-a-b-c"

    def test_a_route_may_name_its_own_image(self):
        plan = self._plan(_route(containment_image="reviewer:local"))
        assert plan.image == "reviewer:local"

    def test_without_a_configured_image_there_is_no_plan(self):
        with pytest.raises(containment.ContainmentUnavailable) as exc:
            self._plan(environ={})
        assert exc.value.reason == "no-image-configured"

    def test_a_launch_site_that_declares_no_repository_is_refused(self):
        """`repo_root=""` is "nobody said", not "nothing needed". Defaulting
        it would silently mount nothing and look like success."""
        with pytest.raises(containment.ContainmentUnavailable) as exc:
            self._plan(repo_root="")
        assert exc.value.reason == "no-repository-declared"

    def test_a_worktree_outside_its_declared_repository_is_refused(self):
        with pytest.raises(containment.ContainmentUnavailable) as exc:
            self._plan(worktree="/elsewhere/tree")
        assert exc.value.reason == "worktree-outside-repository:/elsewhere/tree"

    def test_a_worktree_equal_to_the_repository_is_fine(self):
        assert self._plan(worktree="/repo").workdir == "/repo"

    def test_an_empty_worktree_falls_back_to_the_repository(self):
        assert self._plan(worktree="").workdir == "/repo"

    def test_a_traversal_worktree_cannot_escape_its_repository(self):
        with pytest.raises(containment.ContainmentUnavailable):
            self._plan(worktree="/repo/../etc")

    def test_the_process_environment_is_the_default_source(self, monkeypatch):
        monkeypatch.setenv(containment.IMAGE_ENV, "from-env:tag")
        monkeypatch.delenv(containment.HOST_MAP_ENV, raising=False)
        assert containment.image_for(_route()) == "from-env:tag"
        plan = containment.plan_for(_route(), worktree="/repo", repo_root="/repo",
                                    inside=False)
        assert plan.image == "from-env:tag"


# ---------------------------------------------------------------------------
# probing the runtime


class TestProbe:

    def test_a_reachable_daemon_with_the_image_present_is_available(self):
        runner = _Runner()
        assert containment.probe("agent:local", runner) == ""
        assert runner.calls[0][:2] == ["docker", "version"]
        assert runner.calls[1][:3] == ["docker", "image", "inspect"]

    def test_a_daemon_that_refuses_is_named_separately_from_a_missing_image(self):
        assert containment.probe("agent:local", _Runner(version_rc=1)) == "docker-unavailable"
        assert containment.probe("agent:local", _Runner(inspect_rc=1)) == "image-missing:agent:local"

    def test_no_docker_binary_at_all_is_unavailable_not_a_crash(self):
        assert containment.probe(
            "i", _Runner(raises=FileNotFoundError("docker"))) == "docker-unavailable"

    def test_a_hung_docker_daemon_refuses_rather_than_hanging_the_pass(self):
        runner = _Runner(raises=subprocess.TimeoutExpired(["docker"], 30))
        assert containment.probe("i", runner) == "docker-unavailable"

    def test_the_image_inspect_call_is_bounded_too(self):
        class _SecondCallHangs(_Runner):
            def __call__(self, argv, *, cwd=None, timeout=None):
                self.calls.append(list(argv))
                if "inspect" in argv:
                    raise subprocess.TimeoutExpired(list(argv), 30)
                return subprocess.CompletedProcess(list(argv), 0, "", "")

        assert containment.probe("i", _SecondCallHangs()) == "docker-unavailable"

    def test_every_docker_call_carries_a_timeout(self):
        runner = _Runner()
        containment.probe("i", runner)
        assert containment.DOCKER_TIMEOUT > 0

    def test_the_default_runner_is_a_real_subprocess(self):
        res = containment.local_run(["true"], timeout=30)
        assert res.returncode == 0


class TestUnavailableReason:
    """The pre-launch form the effect boundary asks."""

    ENV = {containment.IMAGE_ENV: "agent:local",
           containment.HOST_MAP_ENV: "/repo=/host/repo"}

    def test_an_establishable_route_reports_no_reason(self):
        assert containment.unavailable_reason(
            _route(), repo_root="/repo", run=_Runner(), environ=self.ENV) == ""

    def test_a_configuration_fault_is_reported_without_touching_docker(self):
        runner = _Runner()
        assert containment.unavailable_reason(
            _route(), repo_root="/repo", run=runner, environ={}) == "no-image-configured"
        assert runner.calls == []

    def test_an_unmapped_repository_is_reported_before_docker_is_asked(self):
        """A containerized daemon with no declared host mapping cannot mount
        the repository at all, and finding that out from the docker daemon is
        not possible -- the mount would succeed and be empty."""
        runner = _Runner()
        reason = containment.unavailable_reason(
            _route(), repo_root="/repo", run=runner,
            environ={containment.IMAGE_ENV: "agent:local"})
        if containment.in_container():
            assert reason == "host-path-unmapped:/repo"
            assert runner.calls == []
        else:
            assert reason == ""

    def test_a_runtime_fault_is_reported_after_the_configuration_is_fine(self):
        assert containment.unavailable_reason(
            _route(), repo_root="/repo", run=_Runner(inspect_rc=1),
            environ=self.ENV) == "image-missing:agent:local"


class TestForceRemove:

    def test_it_asks_docker_to_remove_the_container(self):
        runner = _Runner()
        containment.force_remove("nyxloom-att-1", runner)
        assert runner.calls == [["docker", "rm", "--force", "nyxloom-att-1"]]

    def test_a_failure_to_remove_is_logged_and_swallowed(self):
        """The caller has already SIGKILLed the docker client and has nothing
        better to do with an error. Raising here would replace a recorded
        interrupt with an unhandled exception in the wrapper's exit path."""
        containment.force_remove("x", _Runner(raises=OSError("no docker")))

    def test_the_default_runner_is_used_when_none_is_given(self, monkeypatch):
        seen = {}

        def fake(argv, *, cwd=None, timeout=None):
            seen["argv"] = list(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(containment, "local_run", fake)
        containment.force_remove("nyxloom-att-2")
        assert seen["argv"] == ["docker", "rm", "--force", "nyxloom-att-2"]


# ---------------------------------------------------------------------------
# REAL containers: the four capability criteria, proved from outside
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def host_repo() -> str:
    host = _host_repo_root()
    if host is None:
        pytest.skip("no verified host-path mapping for this checkout")
    return host


@pytest.fixture()
def real_plan(host_repo):
    """A plan whose mount source is the VERIFIED host path for this repo."""
    def build(route=None, **over):
        env = {containment.IMAGE_ENV: PROBE_IMAGE,
               containment.HOST_MAP_ENV: f"{REPO_ROOT}={host_repo}"}
        env.update(over.pop("environ", {}))
        return containment.plan_for(route or _route(secrets=[]),
                                    worktree=str(REPO_ROOT),
                                    repo_root=str(REPO_ROOT),
                                    environ=env, inside=True, **over)
    return build


@requires_docker
class TestRealContainerCapabilities:

    def test_the_declared_repository_is_reachable_and_is_the_whole_mount(self, real_plan):
        """The positive half of every test below: containment that also
        prevented the agent from seeing its own work would pass the four
        refusals and be useless."""
        plan = real_plan()
        res = _run(plan.docker_argv(["ls", "pyproject.toml"]))
        assert res.returncode == 0, res.stderr
        assert "pyproject.toml" in res.stdout

    def test_a_contained_leg_cannot_read_the_operator_home(self, real_plan):
        """ACCEPTANCE 1. A decoy 'operator home' is created as a real docker
        volume with a real secret in it; the contained argv cannot read it,
        and the SAME image with one added mount can -- so the refusal is the
        boundary and not a broken command line."""
        volume = f"nyxloom-test-home-{uuid.uuid4().hex[:8]}"
        _run(["docker", "volume", "create", volume])
        try:
            seed = _run(["docker", "run", "--rm", "--volume", f"{volume}:/ophome",
                         PROBE_IMAGE, "sh", "-c",
                         "echo sk-operator-credential > /ophome/.claude.json"])
            assert seed.returncode == 0, seed.stderr

            plan = real_plan()
            contained = _run(plan.docker_argv(["cat", "/ophome/.claude.json"]))
            assert contained.returncode != 0
            assert "sk-operator-credential" not in contained.stdout

            control = _run(["docker", "run", "--rm", "--volume", f"{volume}:/ophome",
                            PROBE_IMAGE, "cat", "/ophome/.claude.json"])
            assert control.returncode == 0
            assert "sk-operator-credential" in control.stdout
        finally:
            _run(["docker", "volume", "rm", "-f", volume])

    def test_a_contained_leg_cannot_reach_the_docker_socket(self, real_plan):
        """ACCEPTANCE 2. ``/var/run/docker.sock`` is host-root-equivalent and
        IS mounted into the daemon this agent used to be a child of."""
        plan = real_plan()
        contained = _run(plan.docker_argv(
            ["sh", "-c", "test -S /var/run/docker.sock && echo REACHABLE"]))
        assert contained.returncode != 0
        assert "REACHABLE" not in contained.stdout

        control = _run(["docker", "run", "--rm",
                        "--volume", "/var/run/docker.sock:/var/run/docker.sock",
                        PROBE_IMAGE, "sh", "-c",
                        "test -S /var/run/docker.sock && echo REACHABLE"])
        assert control.returncode == 0 and "REACHABLE" in control.stdout

    def test_a_contained_leg_receives_only_the_secrets_its_route_declares(
            self, real_plan, monkeypatch):
        """ACCEPTANCE 3, end to end: the route declares one name, the process
        environment holds five, and the container sees one."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-declared")
        monkeypatch.setenv("AA_API_KEY", "aa-daemon-only")
        monkeypatch.setenv("NTFY_TOKEN", "ntfy-publisher")
        monkeypatch.setenv("NTFY_CMD_TOKEN", "ntfy-commands")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-other-route")

        route = _route(secrets=["OPENROUTER_API_KEY"])
        plan = real_plan(route, overrides={"NYXLOOM_ATTEMPT_ID": "att-1"})
        env = containment.child_env(route, {"NYXLOOM_ATTEMPT_ID": "att-1"})
        res = _run(plan.docker_argv(["env"]), env=env)
        assert res.returncode == 0, res.stderr

        seen = dict(line.split("=", 1) for line in res.stdout.splitlines()
                    if "=" in line)
        assert seen.get("OPENROUTER_API_KEY") == "or-declared"
        assert seen.get("NYXLOOM_ATTEMPT_ID") == "att-1"
        for undeclared in ("AA_API_KEY", "NTFY_TOKEN", "NTFY_CMD_TOKEN",
                           "DEEPSEEK_API_KEY"):
            assert undeclared not in seen, (
                f"{undeclared} crossed the container boundary undeclared")
        assert "or-declared" not in " ".join(plan.docker_argv(["env"]))

    def test_a_contained_leg_cannot_open_the_control_plane_port(self, real_plan):
        """ACCEPTANCE 4. A stand-in control plane is put on its OWN docker
        network -- the shape ``nyxloomd`` is deployed in, binding 0.0.0.0 on
        a private bridge with no published host port. The contained leg is
        on a different network and cannot open the connection; a container ON
        that network can, which is what makes this a boundary rather than a
        dead listener."""
        suffix = uuid.uuid4().hex[:8]
        net, listener = f"nyxloom-test-net-{suffix}", f"nyxloom-test-cp-{suffix}"
        serve = ("while true; do printf 'HTTP/1.0 200 OK\\r\\n\\r\\nnyxloomd\\r\\n'"
                 " | nc -l -p 8942; done")
        try:
            made = _run(["docker", "network", "create", net])
            assert made.returncode == 0, made.stderr
            up = _run(["docker", "run", "-d", "--name", listener, "--network", net,
                       PROBE_IMAGE, "sh", "-c", serve])
            assert up.returncode == 0, up.stderr
            ip = _run(["docker", "inspect", "-f",
                       "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                       listener]).stdout.strip()
            assert ip, "no address for the stand-in control plane"

            reachable = _run(["docker", "run", "--rm", "--network", net, PROBE_IMAGE,
                              "wget", "-q", "-T", "5", "-O", "-", f"http://{ip}:8942"])
            assert reachable.returncode == 0 and "nyxloomd" in reachable.stdout, (
                "the stand-in control plane is not actually serving; the "
                "refusal below would prove nothing")

            plan = real_plan()
            assert plan.network != net
            contained = _run(plan.docker_argv(
                ["wget", "-q", "-T", "5", "-O", "-", f"http://{ip}:8942"]))
            assert contained.returncode != 0
            assert "nyxloomd" not in contained.stdout
        finally:
            _run(["docker", "rm", "-f", listener])
            _run(["docker", "network", "rm", net])

    def test_a_route_whose_image_was_never_built_cannot_launch(self, host_repo):
        """ACCEPTANCE 5, at this layer: the runtime is asked, not assumed."""
        missing = f"nyxloom-nonexistent-{uuid.uuid4().hex[:8]}:local"
        assert containment.probe(missing) == f"image-missing:{missing}"
        assert containment.unavailable_reason(
            _route(), repo_root=str(REPO_ROOT),
            environ={containment.IMAGE_ENV: missing,
                     containment.HOST_MAP_ENV: f"{REPO_ROOT}={host_repo}"},
        ) == f"image-missing:{missing}"

    def test_the_probe_agrees_with_a_real_image_that_does_exist(self):
        assert containment.probe(PROBE_IMAGE) == ""


@requires_docker
class TestRealContainedLaunchThroughTheWrapper:
    """The whole path, end to end: a WrapperSpec for an untrusted route goes
    in, a real container comes out, and the receipt/event contract is the
    same one an uncontained leg produces.

    The unit half above proves the plan is right. This proves the wrapper
    USES it -- the gap a mocked container leaves wide open.
    """

    def _run(self, tmp_path, host_repo, monkeypatch, route, argv):
        from nyxloom import storage, wrapper
        from nyxloom.types import (
            Actor, ActorKind, Attempt, AttemptState, EventType, Role, Route,
            TaskState, TaskStateFile, utc_now)

        monkeypatch.setenv(containment.HOST_MAP_ENV, f"{REPO_ROOT}={host_repo}")
        project, task_id, attempt_id = "demo", "demo-P01-sample", "att-c1"
        states: dict = {}
        tsf = TaskStateFile(schema_version=1, task_id=task_id, project=project,
                            state=TaskState.ACTIVE, since=utc_now())
        storage.append_and_apply(
            project, states, actor=Actor(ActorKind.TICK, "test"),
            type=EventType.TASK_CREATED, payload={"statefile": tsf.to_dict()},
            task_id=task_id)
        storage.append_and_apply(
            project, states, actor=Actor(ActorKind.TICK, "test"),
            type=EventType.ATTEMPT_CREATED,
            payload={"attempt": Attempt(
                attempt_id=attempt_id, role=Role.IMPLEMENTER,
                state=AttemptState.CREATED,
                route=Route(route_id=route["route_id"], cli=route["cli"],
                            model=route["model"]),
                started=utc_now()).to_dict()},
            task_id=task_id, attempt_id=attempt_id)

        attempt_dir = tmp_path / "attempt"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        spec = wrapper.WrapperSpec(
            project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
            cwd=str(REPO_ROOT), log_path=str(attempt_dir / "attempt.log"),
            receipt_path=str(attempt_dir / "receipt.json"),
            attempt_dir=str(attempt_dir), route_def=route,
            repo_root=str(REPO_ROOT))
        spec_path = attempt_dir / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict()), encoding="utf-8")
        rc = wrapper.wrapper_main(str(spec_path))
        return rc, attempt_dir

    def test_an_untrusted_route_actually_runs_inside_a_container(
            self, tmp_state, tmp_path, host_repo, monkeypatch):
        route = {"route_id": "free-1", "cli": "opencode", "model": "m:free",
                 "status": "free", "containment_image": PROBE_IMAGE}
        rc, attempt_dir = self._run(
            tmp_path, host_repo, monkeypatch, route,
            ["sh", "-c",
             "cat /proc/self/cgroup > /dev/null; "
             "test ! -S /var/run/docker.sock && "
             "test -f pyproject.toml && echo CONTAINED-OK"])

        log_text = (attempt_dir / "attempt.log").read_text(encoding="utf-8")
        assert rc == 0, log_text
        assert "CONTAINED-OK" in log_text
        receipt = json.loads((attempt_dir / "receipt.json").read_text())
        assert receipt["result"] == "done"

    def test_the_same_route_refuses_when_its_image_does_not_exist(
            self, tmp_state, tmp_path, host_repo, monkeypatch):
        """ACCEPTANCE 5, end to end: a route configured to require
        containment that is unavailable does not launch -- and the reason is
        in the receipt, not only in a log line."""
        missing = f"nyxloom-nonexistent-{uuid.uuid4().hex[:8]}:local"
        route = {"route_id": "free-1", "cli": "opencode", "model": "m:free",
                 "status": "free", "containment_image": missing}
        rc, attempt_dir = self._run(tmp_path, host_repo, monkeypatch, route,
                                    ["sh", "-c", "echo SHOULD-NOT-RUN"])

        from nyxloom import wrapper
        assert rc == wrapper.CONTAINMENT_EXIT
        receipt = json.loads((attempt_dir / "receipt.json").read_text())
        assert receipt["blocked_reason"] == f"containment-unavailable:image-missing:{missing}"
        assert not (attempt_dir / "attempt.log").exists() or \
            "SHOULD-NOT-RUN" not in (attempt_dir / "attempt.log").read_text()
