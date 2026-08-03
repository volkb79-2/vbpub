"""Per-use execution containment for dispatched agent CLIs (CR-13a, D-R7).

WHAT THIS CLOSES
----------------
Until this module existed, an agent CLI was a direct child of the daemon
process, in the daemon's namespace. That namespace has, as deployed:

* ``/var/run/docker.sock`` -- host-root-equivalent;
* the operator's home directory, carrying every CLI's credentials;
* every registered project repository, not only the one being worked on;
* a route to ``nyxloomd-net``, where the control plane binds ``0.0.0.0``.

The child also received ``os.environ.copy()`` minus a small DENYLIST. A
denylist is a stopgap by construction: it fails open on every name nobody
thought to list, and the set of names grows with the deployment rather than
with the code. This module inverts it -- a child receives exactly what its
route DECLARES and nothing else -- and puts the process itself in a
per-use container that has none of the four capabilities above.

THE REQUIREMENT IS FAIL-CLOSED, AND FREE ROUTES CANNOT WAIVE IT
---------------------------------------------------------------
:func:`requires_containment` answers "must this route run contained?" and
answers YES unless a route explicitly declares ``trust = "operator"``. An
undeclared route is contained. That direction is deliberate and is the whole
lesson of the denylist: a default that exempts what nobody classified is a
default that exempts whatever is newest.

A FREE endpoint cannot be declared operator-trusted at all. Free providers
may log or train on prompts -- that is the price of free -- so the free-ness
of a route (``status = "free"``, or the ``free-endpoint`` prompt hint that
``free_models.py`` generates onto every discovered free route) overrides the
declaration rather than being overridden by it. An operator who types
``trust = "operator"`` onto a free route gets containment anyway.

WHAT CONTAINMENT IS, MECHANICALLY
---------------------------------
A ``docker run`` of the route's image with:

* NO docker socket and NO operator home -- by CONSTRUCTION, because the mount
  list is built from the declared repository and contains nothing else;
* the declared repository bind-mounted at its OWN path (so a worktree path in
  a prompt, in ``spec.cwd``, and in the agent's git output all mean the same
  thing on both sides of the boundary);
* a network that is NOT the daemon's. Docker's inter-bridge isolation is what
  makes the control plane unreachable: the default bridge cannot route to a
  user-defined network, and ``nyxloomd``'s 8942 is published on no host port.
  ``--network none`` would be stronger still but would also cut the model
  provider off, which is not a posture -- it is a non-functioning route;
* ``--cap-drop ALL``, ``--security-opt no-new-privileges``, and the daemon's
  own uid/gid (so work landing in the mounted repository is not root-owned);
* environment by ALLOWLIST: the route's declared secret NAMES are forwarded
  by name (``-e NAME``, never ``-e NAME=VALUE``), so no secret VALUE ever
  appears in an argv, a ``ps`` listing, or a serialized ``spec.json``.

HOST PATHS ARE NOT THE DAEMON'S PATHS
-------------------------------------
The daemon runs containerized and talks to the host's docker daemon through
the mounted socket (docker-out-of-docker). A ``-v`` source is resolved by the
HOST daemon, so passing the daemon's own view of a path silently mounts an
EMPTY directory -- the agent would then run against an empty repository and
fail in a way that looks like a broken model. ``nyxloomd/docker-compose.yml``
warns about exactly this. :func:`host_path` translates through a declared
mapping and REFUSES when a path is unmapped inside a container, because a
silently-empty mount is a containment failure that presents as a work
failure.

WHAT IS NOT HERE
----------------
CPU, memory, pid and wall-time caps; per-task/role permission and mount
policy as selector constraints; a recorded containment IDENTITY on the
attempt. All CR-13b, which depends on CR-08/CR-10. This module builds
containment and the fail-closed launch gate over it, and nothing else.

WHAT IS NOT COVERED
-------------------
The four operator-initiated interactive surfaces that shell out to a CLI
directly rather than through the wrapper -- ``intake_chat``,
``decision_chat``, ``onboarding_scan`` and ``onboarding_questionnaire``, each
in their own ``_run_subprocess_turn``. They still inherit the daemon's whole
environment and run uncontained. ``effects_dispatch.admissible`` already
recorded that it does not reach them; this module does not either. They are
driven by an explicit human command rather than by the autonomous loop, and
they use the operator-trusted review route -- which is why this is a stated
gap rather than a silent one. Closing it means giving those call sites a
route and a repository root, which is a change to four call chains and
belongs to whichever package next touches them.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .log import get_logger

log = get_logger("containment")

#: A route declaring this trust level runs UNCONTAINED: it is the operator's
#: own subscription/credential, driven by a CLI the operator installed. The
#: only value that waives containment, and it is powerless on a free route.
TRUST_OPERATOR = "operator"

#: The explicit form of the default. Recorded in the vocabulary so a route
#: file can SAY what it means rather than relying on the absence of a key.
TRUST_UNTRUSTED = "untrusted"

#: The closed trust vocabulary. A value outside it is not "some third
#: posture" -- it is a typo, and a typo must not read as `operator`.
TRUST_VALUES: frozenset[str] = frozenset({TRUST_OPERATOR, TRUST_UNTRUSTED})

#: Non-secret operational names a child process needs to run AT ALL, and
#: which carry no authority: where to find binaries, where its own home is,
#: how to render text, how to reach a proxy. This is the ALLOWLIST that
#: replaces ``wrapper.DAEMON_ONLY_ENV``. It is short on purpose -- a name
#: joins it because a CLI cannot start without it, never because a CLI is
#: more convenient with it.
BASE_ENV_ALLOW: tuple[str, ...] = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
)

#: Env var naming the image every contained launch uses unless its route
#: names its own. Deliberately NOT defaulted to a literal: an image name
#: guessed by the code is an image nobody built, and "containment is
#: configured" must be a thing an operator did.
IMAGE_ENV = "NYXLOOM_CONTAINMENT_IMAGE"

#: Env var naming the docker network contained agents join. Any network that
#: is not the daemon's own; see the module docstring on why not ``none``.
NETWORK_ENV = "NYXLOOM_CONTAINMENT_NETWORK"
DEFAULT_NETWORK = "bridge"

#: Env var carrying ``<daemon-path>=<host-path>`` pairs, comma-separated.
#: Example: ``/workspaces/vbpub=/home/vb/volkb79-2/vbpub``.
HOST_MAP_ENV = "NYXLOOM_CONTAINMENT_HOST_MAP"

#: Bounds every docker control-plane call this module makes. A hung docker
#: daemon must refuse a launch, not hold the reconcile pass open.
DOCKER_TIMEOUT = 30.0


class ContainmentUnavailable(Exception):
    """Containment is required here and could not be established.

    Carries a short machine-readable ``reason`` because this string reaches an
    operator through a receipt's ``blocked_reason`` -- "containment failed" is
    a page nobody can act on, while ``image-missing:<name>`` is a one-line fix.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# the requirement


def is_free_endpoint(route: Any) -> bool:
    """Whether this route is served by a free provider.

    Two sources because there are two ways one arrives: hand-written routes
    carry ``status = "free"``, and every route ``free_models.py`` generates
    carries the ``free-endpoint`` prompt hint. Reading both means a route
    cannot become exempt by being written the other way.
    """
    if getattr(route, "status", None) == "free":
        return True
    return "free-endpoint" in (getattr(route, "prompt_hints", None) or [])


def requires_containment(route: Any) -> bool:
    """Must this route's agent run inside a per-use container?

    YES unless the route explicitly declares ``trust = "operator"`` AND is not
    a free endpoint. See the module docstring for why the default is this way
    round and why free-ness is not waivable.
    """
    if is_free_endpoint(route):
        return True
    return getattr(route, "trust", None) != TRUST_OPERATOR


def declared_secrets(route: Any) -> tuple[str, ...]:
    """The secret env NAMES this route declares, de-duplicated, in order.

    Names only, never values: a route file is tracked in git, and the whole
    point of the inversion is that the daemon decides what to forward from an
    environment the route never sees.
    """
    return tuple(dict.fromkeys(getattr(route, "secrets", None) or ()))


def child_env(route: Any, overrides: Mapping[str, str] | None = None,
              environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a dispatched CLI receives. ALLOWLIST, not denylist.

    :data:`BASE_ENV_ALLOW` (present names only) plus the route's declared
    secrets plus this attempt's explicit overrides, which win. Everything else
    in the daemon's environment -- including every secret no route asked for
    -- is simply absent, so a name nobody classified is excluded by default
    rather than included by default.
    """
    src = os.environ if environ is None else environ
    env = {name: src[name] for name in BASE_ENV_ALLOW if name in src}
    for name in declared_secrets(route):
        if name in src:
            env[name] = src[name]
    env.update(overrides or {})
    return env


# ---------------------------------------------------------------------------
# host path translation (docker-out-of-docker)


def parse_host_map(raw: str | None) -> tuple[tuple[str, str], ...]:
    """``"/a=/b,/c=/d"`` -> ``(("/a", "/b"), ("/c", "/d"))``, longest first.

    Longest-prefix-first so a nested mapping wins over the mount it sits
    inside. Malformed entries are DROPPED rather than raising: an unusable
    mapping surfaces as "host-path-unmapped" at the point of use, which names
    the path that actually failed, instead of as a parse error naming none.
    """
    pairs: list[tuple[str, str]] = []
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        inner, outer = chunk.split("=", 1)
        inner, outer = inner.strip().rstrip("/"), outer.strip().rstrip("/")
        if inner and outer:
            pairs.append((inner, outer))
    return tuple(sorted(pairs, key=lambda p: len(p[0]), reverse=True))


def in_container(root: Path | None = None) -> bool:
    """Whether this process is itself inside a container.

    ``/.dockerenv`` is what docker itself writes. The question matters only
    for one decision -- whether an unmapped path may be passed to ``-v`` --
    and outside a container the daemon's paths ARE the host's, so an identity
    mapping is correct rather than assumed.
    """
    marker = (root or Path("/")) / ".dockerenv"
    return marker.exists()


def host_path(path: str, mapping: Sequence[tuple[str, str]], *,
              inside: bool) -> str:
    """``path`` as the HOST docker daemon will resolve it.

    Raises :class:`ContainmentUnavailable` when this process is containerized
    and no mapping covers the path. That refusal is the point: docker creates
    a missing bind source as an empty directory, so the alternative is an
    agent running against an empty repository -- containment technically
    established, work silently impossible, and nothing in the logs saying so.
    """
    for inner, outer in mapping:
        if path == inner:
            return outer
        if path.startswith(inner + "/"):
            return outer + path[len(inner):]
    if inside:
        raise ContainmentUnavailable(f"host-path-unmapped:{path}")
    return path


# ---------------------------------------------------------------------------
# the plan


@dataclass(frozen=True)
class ContainmentPlan:
    """Everything needed to run one contained leg, and nothing else.

    Frozen and fully explicit: the mount list is the complete set of host
    paths the agent can reach, so "does this leg see the docker socket?" is
    answered by reading one tuple rather than by reasoning about defaults.
    """

    image: str
    network: str
    workdir: str
    #: ``(host_source, container_destination)``. The destination equals the
    #: daemon's own path for the repository, so every path the agent is told
    #: about resolves identically inside and outside.
    mounts: tuple[tuple[str, str], ...]
    #: Env var NAMES forwarded into the container. Values are taken from the
    #: docker client's own environment by ``-e NAME``, so they stay out of
    #: argv entirely.
    env_names: tuple[str, ...]
    user: str

    def docker_argv(self, inner_argv: Sequence[str],
                    name: str = "") -> list[str]:
        """The full ``docker run`` argv wrapping ``inner_argv``.

        ``--name`` is optional and is a LIFECYCLE handle, not an identity
        record (that is CR-13b): the wrapper uses it to force-remove a
        container whose docker client it had to SIGKILL, which would otherwise
        leave an unsupervised agent running with the client gone.
        """
        argv = ["docker", "run", "--rm", "--network", self.network,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", self.user, "--workdir", self.workdir]
        if name:
            argv += ["--name", name]
        for src, dst in self.mounts:
            argv += ["--volume", f"{src}:{dst}"]
        for env_name in self.env_names:
            argv += ["--env", env_name]
        argv.append(self.image)
        argv.extend(inner_argv)
        return argv


def container_name(attempt_id: str) -> str:
    """A docker-legal name for one attempt's container."""
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in attempt_id)
    return f"nyxloom-{safe}"


def image_for(route: Any, environ: Mapping[str, str] | None = None) -> str:
    """The image this route runs in: its own, else the deployment default."""
    src = os.environ if environ is None else environ
    return (getattr(route, "containment_image", None)
            or src.get(IMAGE_ENV, "") or "")


def plan_for(route: Any, *, worktree: str, repo_root: str,
             overrides: Mapping[str, str] | None = None,
             environ: Mapping[str, str] | None = None,
             inside: bool | None = None) -> ContainmentPlan:
    """The plan for one contained leg, or a refusal naming what is missing.

    Every refusal here is a CONFIGURATION fault the operator can fix, and each
    is named separately because they have different fixes: no image built, no
    repository declared by the launch site, a worktree that is not in the
    repository it claims, or a path the host daemon cannot resolve.
    """
    src = os.environ if environ is None else environ
    image = image_for(route, src)
    if not image:
        raise ContainmentUnavailable("no-image-configured")
    if not repo_root:
        raise ContainmentUnavailable("no-repository-declared")
    root = os.path.normpath(repo_root)
    work = os.path.normpath(worktree or repo_root)
    if work != root and not work.startswith(root + os.sep):
        raise ContainmentUnavailable(f"worktree-outside-repository:{work}")
    mapping = parse_host_map(src.get(HOST_MAP_ENV))
    contained = in_container() if inside is None else inside
    source = host_path(root, mapping, inside=contained)
    return ContainmentPlan(
        image=image,
        network=src.get(NETWORK_ENV) or DEFAULT_NETWORK,
        workdir=work,
        mounts=((source, root),),
        env_names=declared_secrets(route) + tuple(overrides or ()),
        user=f"{os.getuid()}:{os.getgid()}",
    )


# ---------------------------------------------------------------------------
# probing the runtime


def local_run(argv: Sequence[str], *, cwd: str | None = None,
              timeout: float | None = None) -> subprocess.CompletedProcess:
    """The default runner: a plain captured subprocess.

    Shaped like ``effects.SystemProcesses.run`` so the effect boundary can
    pass its own port and get a recorded transcript, while the wrapper -- a
    detached process with no ports -- passes this.
    """
    return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def probe(image: str, run: Callable[..., Any] | None = None) -> str:
    """``""`` when a container for ``image`` could be started right now, else
    a short reason.

    Both questions are asked of docker itself rather than inferred: a socket
    that exists but answers nothing, and an image name that was never built,
    are the two ways this fails in practice and they look identical from the
    filesystem.
    """
    runner = local_run if run is None else run
    try:
        res = runner(["docker", "version", "--format", "{{.Server.Version}}"],
                     timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return "docker-unavailable"
    if res.returncode != 0:
        return "docker-unavailable"
    try:
        res = runner(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                     timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return "docker-unavailable"
    if res.returncode != 0:
        return f"image-missing:{image}"
    return ""


def unavailable_reason(route: Any, *, repo_root: str,
                       run: Callable[..., Any] | None = None,
                       environ: Mapping[str, str] | None = None) -> str:
    """``""`` when this route could be launched contained right now, else why.

    The pre-launch form of :func:`plan_for` + :func:`probe`, asked at the
    effect boundary where the worktree is not yet known. It answers about the
    repository root, which is what the mount is built from, so the only thing
    it cannot see is a worktree outside its own repository -- which the
    wrapper re-checks anyway.
    """
    try:
        plan = plan_for(route, worktree=repo_root, repo_root=repo_root,
                        environ=environ)
    except ContainmentUnavailable as exc:
        return exc.reason
    return probe(plan.image, run)


def force_remove(name: str, run: Callable[..., Any] | None = None) -> None:
    """Best-effort ``docker rm -f``.

    Called only after the wrapper has SIGKILLed a docker client it could not
    stop politely. The client dying does NOT stop the container, so without
    this an agent keeps running with nothing supervising it -- the one way a
    contained launch is worse than an uncontained one. Best effort because the
    caller is already on its kill path and has nothing better to do with a
    failure than log it.
    """
    runner = local_run if run is None else run
    try:
        runner(["docker", "rm", "--force", name], timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("contained-container-not-removed", container=name,
                    error=repr(exc)[:200])
