"""CIU ``ciu dev <stack>`` — the dev-loop runner (S5a / CIU-5).

A build-tool-agnostic dev loop, declared per stack as ``[<root>.dev]``: ordered
``prebuild`` steps (gated on ``depends_on`` health), then a long-running
``command`` with the source bind-mounted and ``port`` exposed. No npm/Vite/
uvicorn specifics live here — the profile describes *any* dev server, including a
contract-coupled pre-build chain that depends on a live service (e.g.
``fetch:openapi`` against a running backend → ``gen:api`` codegen → ``vite dev``)
which a production ``bake`` does not model.

Public API
----------
DevProfile                          parsed, validated ``[<root>.dev]`` shape
parse_dev_profile(stack_cfg, root)  -> DevProfile (raises ValueError [S5a])
build_run_command(profile, ...)     -> list[str]  (pure; the `docker run` argv)
run_dev(stack, *, repo_root, ...)   -> int        (render → wait → run)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config_model
from . import procutil
from .config_constants import GLOBAL_CONFIG_DEFAULTS
from .deploy_pkg import health as _health
from .cli_utils import cli_error


def resolve_repo_root(define_root: Path | str | None, start_dir: Path) -> Path:
    """Resolve the CIU root from explicit input or the nearest committed marker.

    ``define_root`` (``--root-folder``/``--root-folder``) is explicit operator
    intent, so it ALWAYS wins outright — returned immediately, with no
    consistency check against ambient ``$REPO_ROOT`` (an explicit flag is not
    something a stale shell variable gets to second-guess).

    Ambient ``REPO_ROOT`` is an output for child processes, never a selector.
    A missing marker is a typed refusal rather than an invented current root.
    """
    from .workspace import CiuWorkspaceError, resolve_ciu_root

    try:
        return resolve_ciu_root(start_dir, root_folder=define_root)
    except CiuWorkspaceError as exc:
        raise ValueError(str(exc)) from exc


# ---------------------------------------------------------------------------
# S5a — the [<root>.dev] profile
# ---------------------------------------------------------------------------


@dataclass
class DevProfile:
    """Parsed ``[<root>.dev]`` dev-loop profile (S5a)."""

    command: str
    """The long-running dev-server command (required)."""

    image: str | None = None
    """Base image to run the dev loop in. One of `image`/`build` is required."""

    build: dict | None = None
    """Optional ``{context, dockerfile, target}`` to build a dev image instead."""

    prebuild: tuple[str, ...] = ()
    """Ordered commands run before `command`; abort on first failure (S5a)."""

    ports: tuple[tuple[int, int], ...] = ()
    """(host, container) port pairs to publish (e.g. the HMR port)."""

    mounts: tuple[str, ...] = ()
    """``docker -v`` mount specs (source bind + anonymous volumes)."""

    depends_on: tuple[str, ...] = ()
    """Services to ``wait_healthy`` before prebuild (reuses CIU-4)."""

    workdir: str = "/app"
    """Container working directory."""

    env: dict = field(default_factory=dict)
    """Extra environment variables for the dev container."""

    network: str | None = None
    """Network to join; defaults to the stack's ``deploy.network_name``."""


def _parse_ports(value: object) -> tuple[tuple[int, int], ...]:
    """Normalise a `port` value to (host, container) pairs.

    Accepts an int, a ``"host:container"`` string, a bare numeric string, or a
    list thereof. ``int`` / bare-numeric map to the same host and container port.
    """
    if value is None:
        return ()
    items = value if isinstance(value, list) else [value]
    out: list[tuple[int, int]] = []
    for item in items:
        if isinstance(item, bool):  # bool is an int subclass — reject explicitly
            raise ValueError(f"[S5a] [<root>.dev].port entry {item!r} must be an int or 'host:container'")
        if isinstance(item, int):
            out.append((item, item))
        elif isinstance(item, str) and ":" in item:
            host_s, cont_s = item.split(":", 1)
            try:
                out.append((int(host_s), int(cont_s)))
            except ValueError as exc:
                raise ValueError(f"[S5a] [<root>.dev].port entry {item!r} must be 'host:container'") from exc
        elif isinstance(item, str) and item.isdigit():
            out.append((int(item), int(item)))
        else:
            raise ValueError(f"[S5a] [<root>.dev].port entry {item!r} must be an int or 'host:container'")
    return tuple(out)


def parse_dev_profile(stack_config: dict, root_key: str) -> DevProfile:
    """Parse and validate ``[<root>.dev]``; raise ``ValueError`` tagged ``[S5a]``.

    ``stack_config`` is the rendered stack config (root key + optional state);
    ``root_key`` its single non-reserved key (S3.5). Requires a ``command`` and
    one of ``image``/``build``; all other keys are optional with sane defaults.
    """
    root = stack_config.get(root_key)
    dev = root.get("dev") if isinstance(root, dict) else None
    if not isinstance(dev, dict):
        raise ValueError(
            f"[S5a] stack root '{root_key}' has no [dev] table; `ciu dev` requires "
            f"a [{root_key}.dev] profile (command + image/build, optional "
            "prebuild/port/mount/depends_on)"
        )

    command = dev.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(
            f"[S5a] [{root_key}.dev].command is required and must be a non-empty string"
        )

    image = dev.get("image")
    build = dev.get("build")
    if image is None and build is None:
        raise ValueError(
            f"[S5a] [{root_key}.dev] requires either `image` (a base image) or "
            "`build` (a {context, dockerfile, target} table)"
        )
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise ValueError(
            f"[S5a] [{root_key}.dev].image must be a non-empty string"
        )
    if build is not None and not isinstance(build, dict):
        raise ValueError(
            f"[S5a] [{root_key}.dev].build must be a table {{context, dockerfile, target}}"
        )

    prebuild_raw = dev.get("prebuild", [])
    if not isinstance(prebuild_raw, list) or not all(isinstance(s, str) for s in prebuild_raw):
        raise ValueError(f"[S5a] [{root_key}.dev].prebuild must be a list of command strings")

    mounts_raw = dev.get("mount", [])
    if not isinstance(mounts_raw, list) or not all(isinstance(s, str) for s in mounts_raw):
        raise ValueError(f"[S5a] [{root_key}.dev].mount must be a list of docker -v mount strings")

    deps_raw = dev.get("depends_on", [])
    if not isinstance(deps_raw, list) or not all(isinstance(s, str) for s in deps_raw):
        raise ValueError(f"[S5a] [{root_key}.dev].depends_on must be a list of service names")

    workdir = dev.get("workdir", "/app")
    if not isinstance(workdir, str) or not workdir:
        raise ValueError(f"[S5a] [{root_key}.dev].workdir must be a non-empty string")

    env = dev.get("env", {})
    if not isinstance(env, dict):
        raise ValueError(f"[S5a] [{root_key}.dev].env must be a table of NAME = value")

    network = dev.get("network")
    if network is not None and not isinstance(network, str):
        raise ValueError(f"[S5a] [{root_key}.dev].network must be a string")

    return DevProfile(
        command=command,
        image=image,
        build=build,
        prebuild=tuple(prebuild_raw),
        ports=_parse_ports(dev.get("port")),
        mounts=tuple(mounts_raw),
        depends_on=tuple(deps_raw),
        workdir=workdir,
        env={str(k): str(v) for k, v in env.items()},
        network=network,
    )


# ---------------------------------------------------------------------------
# `docker run` argv (pure — unit-testable without docker)
# ---------------------------------------------------------------------------


def build_run_command(
    profile: DevProfile,
    *,
    image: str,
    network: str | None = None,
    interactive: bool = True,
    no_prebuild: bool = False,
) -> list[str]:
    """Build the ``docker run`` argv for the dev loop (pure).

    Prebuild steps and the dev ``command`` run in a SINGLE ephemeral container
    (``--rm``) so the prebuild's generated files land in the same mounted source
    tree the dev server then serves. The command line is
    ``sh -c '<prebuild1> && <prebuild2> && exec <command>'`` so a failing
    prebuild step aborts before the server starts, and ``exec`` hands signals
    straight to the dev server. *network* defaults the join when the profile does
    not pin one (so prebuild steps can reach a live ``depends_on`` service).
    """
    cmd = ["docker", "run", "--rm"]
    if interactive:
        cmd.append("-it")
    net = profile.network or network
    if net:
        cmd += ["--network", net]
    for host, container in profile.ports:
        cmd += ["-p", f"{host}:{container}"]
    for mount in profile.mounts:
        cmd += ["-v", mount]
    for key, value in profile.env.items():
        cmd += ["-e", f"{key}={value}"]
    cmd += ["-w", profile.workdir, image]

    steps = [] if no_prebuild else list(profile.prebuild)
    steps.append(f"exec {profile.command}")
    cmd += ["sh", "-c", " && ".join(steps)]
    return cmd


# ---------------------------------------------------------------------------
# run_dev — render → wait_healthy(depends_on) → docker run
# ---------------------------------------------------------------------------


def _container_status_fn(project: str | None, env_tag: str | None) -> Callable[[str], str]:
    """Return a ``service -> classify-string`` resolver for the live project."""
    import json

    def status(service: str) -> str:
        cname = (
            f"{project}-{env_tag}-{service}" if project and env_tag else service
        )
        try:
            res = procutil.docker(
                ["inspect", "--format", "{{json .State}}", cname], check=False
            )
        except FileNotFoundError:
            return "not-found"
        if res.returncode != 0 or not (res.stdout or "").strip():
            return "not-found"
        try:
            return _health.classify(json.loads(res.stdout))
        except (ValueError, TypeError):
            return "not-found"

    return status


def _build_dev_image(profile: DevProfile, stack_dir: Path, *, run_fn, repo_root: Path) -> str:
    """Build the dev image from ``profile.build`` and return its tag.

    CIU-79: ``build.context`` (and ``dockerfile``, which moves WITH context —
    S8.1a's rule) resolves against ``repo_root``, not ``stack_dir`` -- the
    same repo-root-relative convention CIU-71 established for `ciu up`'s
    `docker compose --project-directory`. `ciu dev` runs a plain `docker
    build`, which has no `--project-directory` equivalent, so CIU resolves
    the context to an absolute path itself before building the argv.
    """
    build = profile.build or {}
    context = build.get("context", ".")
    dockerfile = build.get("dockerfile", "Dockerfile")
    target = build.get("target")
    tag = build.get("tag", f"ciu-dev-{stack_dir.name}")
    context_dir = (Path(repo_root) / context).resolve()
    argv = ["docker", "build", "-t", tag, "-f", str(context_dir / dockerfile)]
    if target:
        argv += ["--target", target]
    argv.append(str(context_dir))
    rc = run_fn(argv, cwd=str(stack_dir))
    if rc != 0:
        raise RuntimeError(f"[S5a] dev image build failed (exit {rc}) for {stack_dir}")
    return tag


def run_dev(
    stack: str,
    *,
    repo_root: Path,
    profile_name: str | None = None,
    no_prebuild: bool = False,
    interactive: bool | None = None,
    # injectables for tests:
    global_loader: Callable[[Path], dict] | None = None,
    wait_fn: Callable[..., bool] | None = None,
    run_fn: Callable[..., int] | None = None,
    build_run_fn: Callable[..., int] | None = None,
) -> int:
    """Render the stack, gate on ``depends_on`` health, then run the dev loop.

    Returns the dev container's exit code (or non-zero on a setup failure).
    ``global_loader``/``wait_fn``/``run_fn`` are injectable so the orchestration
    is testable without docker.
    """
    if interactive is None:
        # Use a TTY only when stdin is one — otherwise `docker run -it` fails
        # with "the input device is not a TTY" under CI / nohup / captured stdout.
        import sys as _sys

        interactive = _sys.stdin.isatty()
    repo_root = Path(repo_root).resolve()
    stack_dir = (repo_root / stack).resolve()
    if not stack_dir.is_dir():
        cli_error(f"[ERROR] dev: stack directory not found: {stack_dir}")
        return 2

    if global_loader is None:
        def global_loader(root: Path) -> dict:
            return config_model.render_global_chain(root, root)
    if run_fn is None:
        run_fn = lambda argv, **kw: subprocess.call(argv, **kw)  # noqa: E731
    if build_run_fn is None:
        build_run_fn = run_fn

    try:
        from .deploy_pkg import profiles as _profiles

        global_config = global_loader(repo_root)
        # Apply the service profile's overrides (topology/env) like the deploy path,
        # so `--profile` is honored and network/host facts match (S7.4 / Seam 4).
        # With no name and no CIU_SERVICES_PROFILE the default profile leaves the
        # config unchanged; a bad name raises ValueError (caught below → exit 2).
        resolved_profile = _profiles.resolve_profiles(
            global_config, [profile_name] if profile_name else None
        )
        global_config = resolved_profile.config
        # S3.12 / CIU-44: `ciu dev` selects exactly this one stack — declared
        # as such, so template guards see an honest selection.
        ciu_ctx = _profiles.render_ciu_context(resolved_profile, [{"path": stack}])
        stack_config = config_model.render_stack(
            stack_dir,
            global_config=global_config,
            preserve_state=True,
            ciu_context=ciu_ctx,
        )
        root_key = config_model.validate_stack_shape(stack_config)
        profile = parse_dev_profile(stack_config, root_key)
    except (FileNotFoundError, ValueError) as exc:
        cli_error(f"[ERROR] dev: {exc}")
        return 2

    merged = config_model.deep_merge(global_config, stack_config)
    deploy_cfg = merged.get("deploy", {})
    project = deploy_cfg.get("project_name")
    env_tag = deploy_cfg.get("environment_tag")
    network = profile.network or deploy_cfg.get("network_name")

    # Gate prebuild on dependency health (reuses CIU-4 / S9.3).
    if profile.depends_on:
        status_fn = _container_status_fn(project, env_tag)
        for service in profile.depends_on:
            print(f"[INFO] dev: waiting for dependency '{service}' to be healthy...", flush=True)
            ready = (
                wait_fn(service)
                if wait_fn is not None
                else _health.wait_healthy(lambda: status_fn(service))
            )
            if not ready:
                cli_error(
                    f"[ERROR] dev: dependency '{service}' did not become healthy; "
                    "start it first (e.g. `ciu up`) or fix its healthcheck",
                )
                return 1

    # Resolve the image (prebuilt or built from profile.build).
    try:
        image = profile.image or _build_dev_image(
            profile, stack_dir, run_fn=build_run_fn, repo_root=repo_root
        )
    except RuntimeError as exc:
        cli_error(f"[ERROR] {exc}")
        return 1

    argv = build_run_command(
        profile,
        image=image,
        network=network,
        interactive=interactive,
        no_prebuild=no_prebuild,
    )
    print(f"[INFO] dev: {' '.join(argv)}", flush=True)
    return run_fn(argv, cwd=str(stack_dir))
