"""Shared machinery for the image-runtime lane (stdlib only).

The lane tests the EXACT image artifacts it is given - `MDT_RUNTIME_IMAGE_NATIVE` and
`MDT_RUNTIME_IMAGE_VSC` - by starting real containers from them. This module is the
only place that talks to Docker:

* every container gets `--cgroup-parent $CGROUP_PARENT_DEV_GATES` (the gate tier),
  `--init`, `--network none` (the lane is offline) and a unique name
  `mdt-cockpit-rt-<pid>-<n>`, and is removed BY EXACT NAME in a `finally`;
* volumes and throw-away images are created under the same unique-name scheme and
  removed by exact name; nothing is ever removed by filter or ancestor;
* nothing here sleeps or polls: every wait is a synchronous `docker run` that returns
  when its process exits, or - for an interactive program - a read on its terminal that
  returns when the program shows the next thing the test waits for (`timeout=` and the
  drive() failsafe are generous limits that turn a stuck session into an error, never a
  verdict).

There are no defaults for the images: an unset variable is a configuration error.
"""
from __future__ import annotations

import atexit
import contextlib
import fcntl
import functools
import itertools
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent

ENV_NATIVE = "MDT_RUNTIME_IMAGE_NATIVE"
ENV_VSC = "MDT_RUNTIME_IMAGE_VSC"
ENV_GATES = "CGROUP_PARENT_DEV_GATES"
ENV_LOCAL_MANIFEST = "MDT_RUNTIME_LOCAL_DEV_MANIFEST"
BUILD_MODE_LABEL = "net.volkb79.mdt.build-mode"
LOCAL_ARTIFACTS_LABEL = "net.volkb79.mdt.local-artifacts"

# D5, written out independently of the Dockerfile on purpose: the lane must be able
# to disagree with the image.
PATH_PREFIX = ("/opt/mdt/bin", "/opt/mdt/venv/bin", "/opt/mdt/npm/bin", "/srv/pwmcp-instance/bin")
PATH_SUFFIX = ("/home/vscode/.local/bin",)

DOCKER = "docker"
GENEROUS_TIMEOUT = 600  # seconds; a failsafe against a hung container, never a verdict
_PID = os.getpid()
_COUNTER = itertools.count(1)
_LIVE_CONTAINERS: set[str] = set()
_LIVE_VOLUMES: set[str] = set()
_LIVE_IMAGES: set[str] = set()


class ConfigurationError(RuntimeError):
    """The lane was started without what it needs; refuse rather than guess."""


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


@dataclass(frozen=True)
class ImageInfo:
    ref: str
    image_id: str
    labels: Mapping[str, str]
    env: Mapping[str, str]
    user: str

    @property
    def build_mode(self) -> str:
        return self.labels.get(BUILD_MODE_LABEL, "<absent>")


def _unique(kind: str) -> str:
    return f"mdt-cockpit-rt-{_PID}-{next(_COUNTER)}{('-' + kind) if kind else ''}"


def require_environment() -> dict[str, str]:
    values = {}
    for name in (ENV_GATES, ENV_NATIVE, ENV_VSC):
        value = os.environ.get(name, "").strip()
        if not value:
            raise ConfigurationError(
                f"{name} is not set. The image-runtime lane tests the exact images it is "
                "given and has no default (it never falls back to a :latest tag); run it as "
                f"`{ENV_NATIVE}=<ref> {ENV_VSC}=<ref> ./run-gate.py image-runtime`."
            )
        values[name] = value
    return values


def _docker(args: Sequence[str], *, input_bytes: bytes | None = None, timeout: int = GENEROUS_TIMEOUT
            ) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], input=input_bytes, capture_output=True, check=False, timeout=timeout
    )


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


@functools.lru_cache(maxsize=None)
def image_info(ref: str) -> ImageInfo:
    result = _docker(["image", "inspect", ref])
    if result.returncode != 0:
        raise ConfigurationError(f"image {ref!r} is not available locally: {_decode(result.stderr).strip()}")
    document = json.loads(result.stdout)[0]
    config = document.get("Config") or {}
    env = dict(item.split("=", 1) for item in (config.get("Env") or []) if "=" in item)
    return ImageInfo(
        ref=ref,
        image_id=document["Id"],
        labels=config.get("Labels") or {},
        env=env,
        user=config.get("User") or "",
    )


_ANNOUNCED = False


def announce() -> None:
    """Print each image's ID and build-mode label once, at lane start."""
    global _ANNOUNCED
    if _ANNOUNCED:
        return
    _ANNOUNCED = True
    values = require_environment()
    for family, name in (("native", ENV_NATIVE), ("vsc", ENV_VSC)):
        info = image_info(values[name])
        print(
            f"[image-runtime] {family}: {info.ref} id={info.image_id} {BUILD_MODE_LABEL}={info.build_mode}",
            file=sys.stderr, flush=True,
        )


def _remove_container(name: str) -> None:
    _docker(["rm", "-f", name], timeout=120)
    _LIVE_CONTAINERS.discard(name)


def _container_command(
    name: str,
    image: str,
    argv: Sequence[str],
    *,
    user: str | None,
    env: Mapping[str, str] | None,
    tmpfs: Sequence[str],
    volumes: Sequence[str],
    workdir: str | None,
    tty: bool,
    interactive: bool,
    memory: str,
) -> list[str]:
    """The one place a lane container's `docker run` argv is built."""
    gates = require_environment()[ENV_GATES]
    command = [
        "run", "--name", name, "--init", "--cgroup-parent", gates, "--network", "none",
        "--memory", memory, "--label", "mdt-cockpit-rt=1", "--entrypoint", argv[0],
    ]
    if user is not None:
        command += ["--user", user]
    if tty:
        command.append("-t")
    if interactive:
        command.append("-i")
    if workdir is not None:
        command += ["-w", workdir]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    for spec in tmpfs:
        command += ["--tmpfs", spec]
    for spec in volumes:
        command += ["-v", spec]
    return command + [image, *argv[1:]]


def run(
    image: str,
    argv: Sequence[str],
    *,
    user: str | None = None,
    env: Mapping[str, str] | None = None,
    tmpfs: Sequence[str] = (),
    volumes: Sequence[str] = (),
    workdir: str | None = None,
    tty: bool = False,
    stdin: bytes | None = None,
    timeout: int = GENEROUS_TIMEOUT,
    memory: str = "2g",
) -> Completed:
    """Run one command in a fresh container and return its outcome.

    `argv[0]` becomes the entrypoint, so the result never depends on an image's own
    ENTRYPOINT. With `tty=True` the container has a pseudo-terminal and stdout/stderr
    arrive merged (as on a real terminal).
    """
    name = _unique("")
    command = _container_command(
        name, image, argv, user=user, env=env, tmpfs=tmpfs, volumes=volumes, workdir=workdir,
        tty=tty, interactive=stdin is not None, memory=memory,
    )
    _LIVE_CONTAINERS.add(name)
    try:
        result = _docker(command, input_bytes=stdin, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return Completed(124, _decode(exc.stdout or b""), "TIMEOUT (failsafe) " + _decode(exc.stderr or b""))
    finally:
        _remove_container(name)
    return Completed(result.returncode, _decode(result.stdout), _decode(result.stderr))


_ANSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\x1b[=>78]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
DRIVE_FAILSAFE = 180  # seconds; ends a stuck session with an ERROR, never decides a verdict


@dataclass(frozen=True)
class Session:
    returncode: int
    screen: str  # everything the terminal showed, escape sequences removed
    completed_steps: int


def drive(
    image: str,
    argv: Sequence[str],
    steps: Sequence[tuple[str, bytes]],
    *,
    user: str | None = None,
    env: Mapping[str, str] | None = None,
    tmpfs: Sequence[str] = (),
    volumes: Sequence[str] = (),
    workdir: str | None = None,
    failsafe: int = DRIVE_FAILSAFE,
    memory: str = "2g",
) -> Session:
    """Run an interactive program on a real pseudo-terminal and drive it by what it SHOWS.

    `steps` is a list of `(regex, keys)`: when the terminal output (escape sequences removed)
    next matches `regex`, `keys` are sent. There is no sleep and no deadline-as-verdict: the
    session ends when the program exits, and `failsafe` only turns a stuck session into an
    error. Steps that never match because the program already exited are simply not
    completed (`completed_steps` says how many were) - a dialog that may not appear is
    therefore expressed as a trailing optional step.
    """
    name = _unique("")
    command = [DOCKER] + _container_command(
        name, image, argv, user=user, env=env, tmpfs=tmpfs, volumes=volumes, workdir=workdir,
        tty=True, interactive=True, memory=memory,
    )
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    _LIVE_CONTAINERS.add(name)
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    raw = b""
    consumed = 0  # clean-text offset already used by earlier steps
    done = 0
    deadline = time.monotonic() + failsafe
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"interactive session exceeded the {failsafe}s failsafe; screen so far:\n"
                                   + _ANSI.sub(b"", raw).decode("utf-8", "replace")[-2000:])
            ready, _, _ = select.select([master], [], [], remaining)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:  # EIO: the child side closed
                break
            if not chunk:
                break
            raw += chunk
            clean = _ANSI.sub(b"", raw).decode("utf-8", "replace")
            while done < len(steps):
                pattern, keys = steps[done]
                match = re.compile(pattern).search(clean, consumed)
                if not match:
                    break
                os.write(master, keys)
                consumed = match.end()
                done += 1
        returncode = process.wait()
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait()
        _remove_container(name)
    return Session(returncode, _ANSI.sub(b"", raw).decode("utf-8", "replace").replace("\r", ""), done)


def sh(image: str, script: str, **kwargs) -> Completed:
    """`bash -c <script>` in a fresh container (the image's default user)."""
    return run(image, ["bash", "-c", script], **kwargs)


@contextlib.contextmanager
def volume() -> Iterator[str]:
    """A uniquely named, initially empty docker volume, removed by exact name."""
    name = _unique("vol")
    created = _docker(["volume", "create", "--label", "mdt-cockpit-rt=1", name])
    if created.returncode != 0:
        raise RuntimeError(f"cannot create volume {name}: {_decode(created.stderr)}")
    _LIVE_VOLUMES.add(name)
    try:
        yield name
    finally:
        _docker(["volume", "rm", "-f", name], timeout=120)
        _LIVE_VOLUMES.discard(name)


def own_volume(
    image: str, volume_name: str, uid_gid: str = "1000:1000", *, path: str = "/home/vscode", mode: str = "0700"
) -> None:
    """Root pre-step: hand an empty volume to the run uid (mode 0700 for a home, like the host source)."""
    result = run(
        image,
        ["bash", "-c", f"chown {uid_gid} {path} && chmod {mode} {path}"],
        user="0", volumes=[f"{volume_name}:{path}"],
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot prepare volume {volume_name}: {result.output}")


@contextlib.contextmanager
def built_image(dockerfile: str, *, build_args: Mapping[str, str] | None = None, label: str = "img"
                ) -> Iterator[str]:
    """Build a throw-away image from `dockerfile` (text) and remove its tag afterwards.

    Uses the Docker daemon's own builder, not the shared remote one: the base is a
    local-only image that a remote BuildKit cannot see. The build context is empty.
    """
    tag = f"mdt-cockpit-rt-{_PID}-{next(_COUNTER)}-{label}:throwaway"
    gates = require_environment()[ENV_GATES]
    command = [
        "buildx", "build", "--builder", "default", "--load", "--cgroup-parent", gates,
        "--tag", tag, "--file", "-", "--progress", "plain",
    ]
    for key, value in (build_args or {}).items():
        command += ["--build-arg", f"{key}={value}"]
    empty_context = _empty_context()
    command.append(str(empty_context))
    _LIVE_IMAGES.add(tag)
    try:
        result = _docker(command, input_bytes=dockerfile.encode("utf-8"))
        if result.returncode != 0:
            raise RuntimeError(f"throw-away image build failed:\n{_decode(result.stdout)}\n{_decode(result.stderr)}")
        yield tag
    finally:
        _docker(["image", "rm", "-f", tag], timeout=120)
        _LIVE_IMAGES.discard(tag)


@functools.lru_cache(maxsize=None)
def _empty_context() -> Path:
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix=f"mdt-cockpit-rt-{_PID}-ctx-"))
    atexit.register(lambda: directory.rmdir() if directory.exists() else None)
    return directory


def _cleanup_leftovers() -> None:
    for name in sorted(_LIVE_CONTAINERS):
        _docker(["rm", "-f", name], timeout=120)
    for name in sorted(_LIVE_VOLUMES):
        _docker(["volume", "rm", "-f", name], timeout=120)
    for tag in sorted(_LIVE_IMAGES):
        _docker(["image", "rm", "-f", tag], timeout=120)


atexit.register(_cleanup_leftovers)


def template_post_create_command() -> str:
    """The `postCreateCommand` of the shipped devcontainer template, parsed, not retyped."""
    sys.path.insert(0, str(ROOT / "templates"))
    import initialize_container_environment as bootstrap

    document = bootstrap._parse_jsonc_object((ROOT / "templates" / "devcontainer.json").read_text(encoding="utf-8"))
    command = document["postCreateCommand"]
    if not isinstance(command, str) or not command.strip():
        raise ConfigurationError("templates/devcontainer.json has no string postCreateCommand")
    return command


def template_container_env() -> dict[str, str]:
    """The `containerEnv` of the shipped devcontainer template, parsed, not retyped."""
    sys.path.insert(0, str(ROOT / "templates"))
    import initialize_container_environment as bootstrap

    document = bootstrap._parse_jsonc_object((ROOT / "templates" / "devcontainer.json").read_text(encoding="utf-8"))
    env = document["containerEnv"]
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ConfigurationError("templates/devcontainer.json has no string-valued containerEnv")
    return dict(env)


class ImageCase(unittest.TestCase):
    """Base for one image family. Subclasses set `family` to `native` or `vsc`."""

    family = ""

    @classmethod
    def setUpClass(cls) -> None:
        values = require_environment()
        announce()
        cls.image = image_info(values[ENV_NATIVE if cls.family == "native" else ENV_VSC])

    @property
    def is_vsc(self) -> bool:
        return self.family == "vsc"

    def sh(self, script: str, **kwargs) -> Completed:
        return sh(self.image.ref, script, **kwargs)

    def assertOk(self, result: Completed, what: str = "command") -> None:
        if result.returncode != 0:
            self.fail(f"{what} failed with exit {result.returncode}:\n{result.output}")


class NativeCase(ImageCase):
    family = "native"


class VscCase(ImageCase):
    family = "vsc"
