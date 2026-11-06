"""Real Compose acceptance for CIU-141.

This single test runs only when the registered `ciu-run-os-integration` lane
sets CIU_RUN_OS_INTEGRATION=1 and supplies CMRU's isolated DinD endpoint. The
fixture lives under this checkout. CMRU's sidecar has no checkout mount, so
this probe uses daemon-owned named volumes for service mounts and seeds one
with CIU's generated secret through `docker cp`. Every container, network, and
volume it creates has a unique ciurun-test- name; cleanup addresses only those
exact names and IDs.
"""

from __future__ import annotations

import errno
import hmac
import json
import os
import pty
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ciu.secrets import materialize
from ciu.workspace_env import MACHINE_FACTS_KEYS, write_generated_facts

_MIRROR_BASE = (
    "mirror.gcr.io/library/debian:bookworm-slim@"
    "sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171"
)
_DOCKER_HOST = "tcp://localhost:2375"
_WAIT_BOUND_SECONDS = 300


def _docker(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=timeout,
    )


def _inspect(container: str) -> dict | None:
    result = _docker(["inspect", container])
    if result.returncode != 0:
        return None
    document = json.loads(result.stdout)
    assert isinstance(document, list) and len(document) == 1
    return document[0]


def _ciurun_env() -> dict[str, str]:
    env = os.environ.copy()
    # tests/conftest.py disables real Docker side effects for ordinary tests.
    # This registered integration lane explicitly opts into the real isolated
    # daemon, so the product subprocess must not inherit that unit-test switch.
    env.pop("CIU_TEST_SUITE", None)
    project_source = Path(__file__).resolve().parents[2]
    source_paths = (
        project_source / "src",
        project_source.parent / "cmru" / "src",
        project_source.parent / "libraries" / "worktree" / "src",
    )
    inherited_paths = []
    for entry in env.get("PYTHONPATH", "").split(os.pathsep):
        if entry:
            path = Path(entry)
            if not path.is_absolute():
                path = project_source / path
            inherited_paths.append(str(path.resolve()))
    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys([*map(str, source_paths), *inherited_paths])
    )
    return env


def _cli(root: Path, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, "-m", "ciu", *args],
        cwd=root,
        env=env or _ciurun_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=_WAIT_BOUND_SECONDS,
    )


def _read_event_line(fd: int, process: subprocess.Popen) -> dict:
    payload = bytearray()
    deadline = time.monotonic() + _WAIT_BOUND_SECONDS
    while not payload.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("timed out waiting for CIU's `launched` event")
        readable, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if not readable:
            if process.poll() is not None:
                raise RuntimeError(
                    f"CIU exited {process.returncode} before the `launched` event"
                )
            continue
        chunk = os.read(fd, 4096)
        if not chunk:
            raise RuntimeError("CIU closed the lifecycle event channel before `launched`")
        payload.extend(chunk)
    return json.loads(payload.decode("utf-8"))


def _read_events_to_eof(fd: int) -> list[dict]:
    payload = bytearray()
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        payload.extend(chunk)
    return [json.loads(line) for line in payload.decode("utf-8").splitlines()]


def _wait_for_path(path: Path, process: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + _WAIT_BOUND_SECONDS
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process is not None and process.poll() is not None:
            pytest.fail(f"CIU exited {process.returncode} before {path.name} appeared")
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for acceptance fixture marker {path}")


def _wait_for_container_path(
    container_id: str, path: str, process: subprocess.Popen,
) -> None:
    deadline = time.monotonic() + _WAIT_BOUND_SECONDS
    while time.monotonic() < deadline:
        result = _docker(["exec", container_id, "test", "-e", path])
        if result.returncode == 0:
            return
        if process.poll() is not None:
            pytest.fail(f"CIU exited {process.returncode} before container path {path} appeared")
        inspected = _docker(["inspect", container_id])
        if inspected.returncode == 0:
            container_state = json.loads(inspected.stdout)[0]["State"]
            if container_state["Status"] != "running":
                logs = _docker(["logs", container_id])
                pytest.fail(
                    f"one-shot exited {container_state.get('ExitCode')} before "
                    f"container path {path} appeared; output:\n{logs.stdout}{logs.stderr}"
                )
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for container path {path}")


def _read_pty(fd: int) -> str:
    output = bytearray()
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)
    return output.decode("utf-8", errors="replace")


def _events_file_args() -> tuple[int, int, list[str]]:
    read_fd, write_fd = os.pipe()
    return read_fd, write_fd, ["--events-fd", str(write_fd)]


def _fixture_files(root: Path, *, project_name: str, image: str, uid: int, gid: int,
                   network: str, readonly_volume: str, writable_volume: str,
                   secrets_volume: str) -> tuple[Path, Path]:
    initialized = subprocess.run(
        ["git", "init", "--quiet", "-b", "main"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert initialized.returncode == 0, initialized.stderr
    subprocess.run(["git", "config", "user.name", "CIU acceptance fixture"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "ciu-run-fixture@example.invalid"],
        cwd=root, check=True,
    )
    stack = root / "tools" / "runner"
    stack.mkdir(parents=True)
    (root / ".gitignore").write_text("**/.ciu/\n", encoding="utf-8")
    (root / ".fixture-root").write_text("CIU-141 acceptance fixture\n", encoding="utf-8")
    (root / "ciu.env").write_text("# acceptance fixture; CIU reads generated facts\n", encoding="utf-8")
    (root / "ciu.global.defaults.toml.j2").write_text(
        f"""\
[ciu]
require_fqdn = false
require_certs = false
auto_connect_network = false

[deploy]
project_name = {json.dumps(project_name)}
environment_tag = "acceptance"
network_name = "$DOCKER_NETWORK_INTERNAL"
log_level = "ERROR"

[deploy.env.shared]
CONTAINER_UID = "$CONTAINER_UID"
DOCKER_GID = "$DOCKER_GID"
REPO_ROOT = "$REPO_ROOT"
PHYSICAL_REPO_ROOT = "$PHYSICAL_REPO_ROOT"

[deploy.labels]
prefix = "ciurun-test"

[deploy.phases.phase_1]
name = "run acceptance"
services = [{{ path = "tools/runner", name = "runner", enabled = true, health = false }}]
""",
        encoding="utf-8",
    )
    (stack / "ciu.defaults.toml.j2").write_text(
        f"""\
[runner]
stack_name = "runner"
dependency_revision = "before"

[runner.worker]
image = {json.dumps(image)}
user = "{uid}:{gid}"
container_name = "ciurun-test-persistent-{project_name}"
hostname = "ciurun-test-host-{project_name}"
working_dir = "/workspace"
cgroup_parent = "ciurun-test.slice"
readonly_volume = {json.dumps(readonly_volume)}
writable_volume = {json.dumps(writable_volume)}
secrets_volume = {json.dumps(secrets_volume)}

[runner.secrets]
acceptance_token = "GEN_LOCAL:ciurun-test/acceptance-token"
""",
        encoding="utf-8",
    )
    (stack / "ciu.compose.yml.j2").write_text(
        """\
services:
  worker:
    image: {{ runner.worker.image }}
    container_name: {{ runner.worker.container_name }}
    hostname: {{ runner.worker.hostname }}
    user: {{ runner.worker.user }}
    working_dir: {{ runner.worker.working_dir }}
    cgroup_parent: {{ runner.worker.cgroup_parent }}
    init: true
    tty: true
    stdin_open: true
    depends_on:
      - dependency
    environment:
      - DEPENDENCY_REVISION={{ runner.dependency_revision }}
    volumes:
      - type: volume
        source: readonly
        target: /probe-ro
        read_only: true
      - type: volume
        source: writable
        target: /probe-rw
      - type: volume
        source: secret_store
        target: /run/secrets
        read_only: true
    networks:
      - workspace
    labels:
      ciurun-test.acceptance: "true"
  dependency:
    image: {{ runner.worker.image }}
    command: ["sh", "-c", "sleep 600"]
    environment:
      - DEPENDENCY_REVISION={{ runner.dependency_revision }}
    networks:
      - workspace

networks:
  workspace:
    external: true
    name: {{ deploy.network_name }}

volumes:
  readonly:
    external: true
    name: {{ runner.worker.readonly_volume }}
  writable:
    external: true
    name: {{ runner.worker.writable_volume }}
  secret_store:
    external: true
    name: {{ runner.worker.secrets_volume }}
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitignore", ".fixture-root"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture root"], cwd=root, check=True)
    facts = {
        "repo_name": project_name,
        "instance_id": project_name[-6:],
        "network": network,
        "physical_repo_root": str(root),
        "repo_root": str(root),
        "public_fqdn": "",
    }
    machine = {key: "" for key in MACHINE_FACTS_KEYS}
    machine.update({
        "env_type": "test",
        "container_uid": str(uid),
        "container_gid": str(gid),
        "docker_gid": str(gid),
        "user_uid": str(os.getuid()),
        "user_gid": str(os.getgid()),
        "python_executable": sys.executable,
    })
    write_generated_facts(root, facts, machine_facts=machine)
    # The disposable checkout represents an isolated CIU worktree instance.
    # Its lifecycle record makes `ciu clean` use the instance teardown rule,
    # so the shared workspace network is removable after this fixture's
    # containers have been cleaned.
    (root / "ciu.worktree-instance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "logical_name": project_name,
                "display_name": project_name,
                "branch": "main",
                "git_worktree_path": str(root.resolve()),
                "ciu_root_offset": ".",
                "created_at_utc": "2026-11-06T00:00:00Z",
                "base_ref": "main",
                "state": "ready",
                "runtime": {"instance_id": project_name[-6:], "network": network},
                "recovery_status": None,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return root, stack


@pytest.mark.os_integration
def test_ciu_run_real_docker_acceptance() -> None:
    """Check the Compose behaviors and lifecycle that fake Docker cannot prove."""
    if os.environ.get("CIU_RUN_OS_INTEGRATION") != "1":
        pytest.skip("run only through the registered ciu-run-os-integration lane")
    if os.environ.get("DOCKER_HOST", "").strip() != _DOCKER_HOST:
        pytest.fail("registered lane requires CMRU tester-gate --enable-docker")
    if not os.environ.get("CGROUP_PARENT_DEV_GATES", "").strip():
        pytest.fail("CMRU did not forward CGROUP_PARENT_DEV_GATES")
    if _docker(["info"]).returncode != 0:
        pytest.fail("CMRU's isolated Docker daemon is unreachable")
    compose_version = _docker(["compose", "version", "--short"])
    assert compose_version.returncode == 0, compose_version.stderr

    project_source = Path(__file__).resolve().parents[2]
    token = uuid.uuid4().hex[:12]
    project_name = f"ciurun-test-{os.getpid()}-{token}"
    network = f"{project_name}-network"
    image = f"{project_name}-fixture:local"
    readonly_volume = f"{project_name}-readonly"
    writable_volume = f"{project_name}-writable"
    secrets_volume = f"{project_name}-secrets"
    volume_names = [readonly_volume, writable_volume, secrets_volume]
    uid = os.getuid() if os.getuid() else 65534
    gid = os.getgid() if os.getuid() else 65534
    root = Path(tempfile.mkdtemp(prefix=f"{project_name}-", dir=project_source))
    owned_names: set[str] = set()
    network_created = False
    image_built = False
    volumes_created: list[str] = []
    controller: subprocess.Popen | None = None
    controller_pid: int | None = None
    foreground_process: subprocess.Popen | None = None

    try:
        dockerfile = (
            f"FROM {_MIRROR_BASE}\n"
            "RUN mkdir -p /probe-ro /probe-rw /run/secrets "
            "&& printf 'read-only-value\\n' > /probe-ro/input.txt "
            f"&& chown -R {uid}:{gid} /probe-rw\n"
            "CMD [\"sleep\", \"600\"]\n"
        )
        (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
        built = subprocess.run(
            ["docker", "build", "--tag", image, "-"],
            input=dockerfile,
            capture_output=True, text=True, check=False, timeout=900,
        )
        assert built.returncode == 0, built.stderr[-4000:]
        image_built = True
        created = _docker([
            "network", "create", "--label", f"io.ciu.instance-id={project_name[-6:]}", network,
        ])
        assert created.returncode == 0, created.stderr
        network_created = True
        for volume_name in volume_names:
            volume = _docker(["volume", "create", volume_name])
            assert volume.returncode == 0, volume.stderr
            volumes_created.append(volume_name)
        root, stack = _fixture_files(
            root, project_name=project_name, image=image, uid=uid, gid=gid,
            network=network, readonly_volume=readonly_volume,
            writable_volume=writable_volume, secrets_volume=secrets_volume,
        )
        env = _ciurun_env()

        # Render revision one, then start only the dependency as the real
        # Compose sibling whose ID and StartedAt must survive a changed render.
        plan_result = _cli(
            root, "run", "--root-folder", str(root), "--dry-run", "--json",
            "tools/runner:worker", "--", "true", env=env,
        )
        assert plan_result.returncode == 0, plan_result.stderr
        secret_path = materialize.project_store(root) / "ciurun-test" / "acceptance-token"
        secret_value = secret_path.read_text(encoding="utf-8").strip()
        assert secret_value and secret_value not in plan_result.stdout
        stack_defaults = stack / "ciu.defaults.toml.j2"
        defaults_text = stack_defaults.read_text(encoding="utf-8")
        secret_declaration = (
            '\n[runner.secrets]\nacceptance_token = '
            '"GEN_LOCAL:ciurun-test/acceptance-token"\n'
        )
        assert secret_declaration in defaults_text
        stack_defaults.write_text(
            defaults_text.replace(secret_declaration, "\n"), encoding="utf-8",
        )
        refreshed_plan = _cli(
            root, "run", "--root-folder", str(root), "--dry-run", "--json",
            "tools/runner:worker", "--", "true", env=env,
        )
        assert refreshed_plan.returncode == 0, refreshed_plan.stderr
        assert secret_value not in refreshed_plan.stdout
        plan = json.loads(refreshed_plan.stdout)

        seed_name = f"{project_name}-secret-seed"
        owned_names.add(seed_name)
        seed_created = _docker([
            "create", "--pull=never", "--name", seed_name, "--mount",
            f"type=volume,source={secrets_volume},target=/seed", image, "sleep", "600",
        ])
        assert seed_created.returncode == 0, seed_created.stderr
        seed_started = _docker(["start", seed_name])
        assert seed_started.returncode == 0, seed_started.stderr
        seeded = _docker(["cp", str(secret_path), f"{seed_name}:/seed/acceptance_token"])
        assert seeded.returncode == 0, seeded.stderr
        secret_owner = _docker([
            "exec", seed_name, "chown", f"{uid}:{gid}", "/seed/acceptance_token",
        ])
        assert secret_owner.returncode == 0, secret_owner.stderr
        secret_mode = _docker(["exec", seed_name, "chmod", "0440", "/seed/acceptance_token"])
        assert secret_mode.returncode == 0, secret_mode.stderr
        seed_removed = _docker(["rm", "-f", seed_name])
        assert seed_removed.returncode == 0, seed_removed.stderr

        compose_files = ["-f", "ciu.compose.yml"]
        overlay = stack / ".ciu" / "ciu.compose.overlay.yml"
        if overlay.is_file():
            compose_files.extend(["-f", ".ciu/ciu.compose.overlay.yml"])
        ownership = stack / ".ciu" / "ciu.compose.ownership.yml"
        if ownership.is_file():
            compose_files.extend(["-f", ".ciu/ciu.compose.ownership.yml"])
        compose_prefix = [
            "docker", "compose", "--progress", "quiet", "-p", plan["project"],
            "--project-directory", str(root), *compose_files,
        ]
        dependency_up = subprocess.run(
            [*compose_prefix, "up", "-d", "dependency"], cwd=stack, env=env,
            capture_output=True, text=True, check=False, timeout=180,
        )
        assert dependency_up.returncode == 0, dependency_up.stderr
        dependency_name = f"{plan['project']}-dependency-1"
        owned_names.add(dependency_name)
        dependency_before = _inspect(dependency_name)
        assert dependency_before is not None
        assert dependency_before["State"]["Status"] == "running"

        stack_defaults.write_text(
            stack_defaults.read_text(encoding="utf-8").replace(
                'dependency_revision = "before"', 'dependency_revision = "after"',
            ),
            encoding="utf-8",
        )

        # PTY foreground: service tty/stdin_open are honored. The command
        # publishes a readiness file and waits for a release file in its volume.
        foreground_name = f"{project_name}-foreground"
        owned_names.add(foreground_name)
        body = (
            "set -eu; "
            f"test \"$(id -u)\" = \"{uid}\"; "
            "test \"$(cat /probe-ro/input.txt)\" = read-only-value; "
            "if touch /probe-ro/forbidden 2>/dev/null; then exit 41; fi; "
            "test -r /run/secrets/acceptance_token; "
            "printf '%s\\n' \"$CIU_RUN_CONTAINER_NAME\" > /probe-rw/container-name; "
            "printf '%s\\n' \"$CIU_RUN_CGROUP_PARENT\" > /probe-rw/cgroup-parent; "
            "printf 'rw-ok\\n' > /probe-rw/proof; "
            ": > /probe-rw/ready; "
            "printf 'ready\\n'; "
            "while [ ! -e /probe-rw/release ]; do sleep 1; done; "
            "printf 'secret-present uid=%s\\n' \"$(id -u)\""
        )
        read_fd, write_fd, event_args = _events_file_args()
        master, slave = pty.openpty()
        foreground = subprocess.Popen(
            [sys.executable, "-m", "ciu", "run", "--root-folder", str(root),
             *event_args, "--name", foreground_name, "tools/runner:worker", "--",
             "sh", "-c", body],
            cwd=root, env=env, stdin=slave, stdout=slave, stderr=slave,
            pass_fds=(write_fd,), close_fds=True,
        )
        foreground_process = foreground
        os.close(slave)
        os.close(write_fd)
        try:
            launched = _read_event_line(read_fd, foreground)
        except RuntimeError as exc:
            if foreground.poll() is None:
                foreground.terminate()
            foreground.wait(timeout=30)
            diagnostic = _read_pty(master)
            os.close(read_fd)
            os.close(master)
            pytest.fail(
                f"{exc}; foreground output:\n{diagnostic}",
            )
        assert launched["schema"] == "ciu-run-event/1"
        assert launched["event"] == "launched"
        assert launched["container"]["name"] == foreground_name
        container_id = launched["container"]["id"]
        owned_names.add(foreground_name)
        _wait_for_container_path(container_id, "/probe-rw/ready", foreground)
        running = _inspect(container_id)
        assert running is not None
        assert running["Name"] == f"/{foreground_name}"
        assert running["Config"]["User"] == f"{uid}:{gid}"
        assert running["Config"]["Hostname"] == f"ciurun-test-host-{project_name}"
        assert running["Config"]["Tty"] is True
        assert running["Config"]["OpenStdin"] is True
        assert running["HostConfig"]["CgroupParent"] == "ciurun-test.slice"
        assert running["HostConfig"]["Init"] is True
        assert network in running["NetworkSettings"]["Networks"]
        mounts_by_target = {mount["Destination"]: mount for mount in running["Mounts"]}
        assert mounts_by_target["/probe-ro"]["Type"] == "volume"
        assert mounts_by_target["/probe-ro"]["Name"] == readonly_volume
        assert mounts_by_target["/probe-ro"]["RW"] is False
        assert mounts_by_target["/probe-rw"]["Type"] == "volume"
        assert mounts_by_target["/probe-rw"]["Name"] == writable_volume
        assert mounts_by_target["/probe-rw"]["RW"] is True
        secret_mount = mounts_by_target["/run/secrets"]
        assert secret_mount["Type"] == "volume"
        assert secret_mount["Name"] == secrets_volume
        assert secret_mount["RW"] is False
        assert all(
            secret_value not in entry for entry in running["Config"].get("Env", [])
        )
        assert (dependency := _inspect(dependency_name)) is not None
        assert dependency["Id"] == dependency_before["Id"]
        assert dependency["State"]["StartedAt"] == dependency_before["State"]["StartedAt"]
        released = _docker(["exec", container_id, "touch", "/probe-rw/release"])
        assert released.returncode == 0, released.stderr
        assert foreground.wait(timeout=_WAIT_BOUND_SECONDS) == 0
        remaining_events = _read_events_to_eof(read_fd)
        os.close(read_fd)
        terminal_output = _read_pty(master)
        os.close(master)
        events = [launched, *remaining_events]
        assert [event["event"] for event in events] == ["launched", "exited", "removed"]
        assert events[1]["exit_code"] == 0
        assert events[2]["removed"] is True
        assert "secret-present" in terminal_output
        assert secret_value not in terminal_output
        assert _inspect(foreground_name) is None
        foreground_process = None

        volume_reader = f"{project_name}-volume-reader"
        owned_names.add(volume_reader)
        readback = _docker([
            "run", "--pull=never", "--rm", "--name", volume_reader,
            "--mount", f"type=volume,source={readonly_volume},target=/probe-ro,readonly",
            "--mount", f"type=volume,source={writable_volume},target=/probe-rw,readonly",
            "--mount", f"type=volume,source={secrets_volume},target=/run/secrets,readonly",
            image, "sh", "-c",
            (
                "cat /probe-ro/input.txt; cat /probe-rw/container-name; "
                "cat /probe-rw/cgroup-parent; cat /probe-rw/proof; "
                "cat /run/secrets/acceptance_token"
            ),
        ])
        assert readback.returncode == 0, "named-volume readback failed"
        readback_lines = readback.stdout.splitlines()
        assert readback_lines[:4] == [
            "read-only-value", foreground_name, "ciurun-test.slice", "rw-ok",
        ]
        if len(readback_lines) != 5 or not hmac.compare_digest(
            readback_lines[4], secret_value,
        ):
            pytest.fail("CIU-generated secret did not reach the one-shot named volume")

        # Non-zero foreground status is preserved and the exact named
        # container is removed after CIU reads its inspected exit code.
        failure_name = f"{project_name}-failure"
        owned_names.add(failure_name)
        failure_events = root / "failure-events.jsonl"
        failure = _cli(
            root, "run", "--root-folder", str(root), "--events-file", str(failure_events),
            "--name", failure_name, "tools/runner:worker", "--", "sh", "-c", "exit 42",
            env=env,
        )
        assert failure.returncode == 42, failure.stderr
        failure_records = [
            json.loads(line) for line in failure_events.read_text(encoding="utf-8").splitlines()
        ]
        assert [event["event"] for event in failure_records] == [
            "launched", "exited", "removed",
        ]
        assert failure_records[1]["exit_code"] == 42
        assert failure_records[2]["removed"] is True
        assert _inspect(failure_name) is None

        # A controller SIGTERM reaches the exact container; CIU collects 143
        # and removes the one-shot before returning.
        interrupted_name = f"{project_name}-interrupted"
        owned_names.add(interrupted_name)
        interrupt_read, interrupt_write, interrupt_args = _events_file_args()
        interrupted = subprocess.Popen(
            [sys.executable, "-m", "ciu", "run", "--root-folder", str(root),
             *interrupt_args, "--name", interrupted_name, "tools/runner:worker", "--",
             "sh", "-c", "sleep 600"],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, pass_fds=(interrupt_write,), close_fds=True,
        )
        foreground_process = interrupted
        os.close(interrupt_write)
        interrupted_event = _read_event_line(interrupt_read, interrupted)
        assert interrupted_event["event"] == "launched"
        interrupted.send_signal(signal.SIGTERM)
        interrupted_stdout, interrupted_stderr = interrupted.communicate(
            timeout=_WAIT_BOUND_SECONDS,
        )
        foreground_process = None
        interrupt_records = [interrupted_event, *_read_events_to_eof(interrupt_read)]
        os.close(interrupt_read)
        assert interrupted.returncode == 143, interrupted_stderr
        assert [event["event"] for event in interrupt_records] == [
            "launched", "exited", "removed",
        ]
        assert interrupt_records[1]["exit_code"] == 143
        assert interrupt_records[2]["removed"] is True
        assert secret_value not in interrupted_stdout + interrupted_stderr
        assert _inspect(interrupted_name) is None

        # Compose's `run -d` stdout is the exact full ID even when the command
        # exits immediately; --name overrides service container_name.
        immediate_name = f"{project_name}-immediate"
        owned_names.add(immediate_name)
        immediate = _cli(
            root, "run", "--root-folder", str(root), "--detach", "--json",
            "--name", immediate_name, "tools/runner:worker", "--", "sh", "-c", "exit 7",
            env=env,
        )
        assert immediate.returncode == 0, immediate.stderr
        immediate_event = json.loads(immediate.stdout)
        immediate_id = immediate_event["container"]["id"]
        assert len(immediate_id) == 64 and immediate_event["container"]["name"] == immediate_name
        immediate_container = _inspect(immediate_id)
        assert immediate_container is not None
        assert immediate_container["State"]["Status"] == "exited"
        assert immediate_container["State"]["ExitCode"] == 7
        removed_immediate = _docker(["rm", "-f", immediate_name])
        assert removed_immediate.returncode == 0, removed_immediate.stderr

        # Without an explicit --name, Compose's one-shot name is generated;
        # the service's persistent container_name must not capture this run.
        implicit = _cli(
            root, "run", "--root-folder", str(root), "--detach", "--json",
            "tools/runner:worker", "--", "true", env=env,
        )
        assert implicit.returncode == 0, implicit.stderr
        implicit_event = json.loads(implicit.stdout)
        implicit_name = implicit_event["container"]["name"]
        assert implicit_name.startswith(plan["project"] + "-")
        assert implicit_name != f"ciurun-test-persistent-{project_name}"
        owned_names.add(implicit_name)
        implicit_id = implicit_event["container"]["id"]
        assert len(implicit_id) == 64
        assert _inspect(implicit_id) is not None
        removed_implicit = _docker(["rm", "-f", implicit_name])
        assert removed_implicit.returncode == 0, removed_implicit.stderr

        # Two live detached runs get distinct names/IDs and remain running.
        concurrent = []
        for suffix in ("a", "b"):
            name = f"{project_name}-concurrent-{suffix}"
            owned_names.add(name)
            launch = _cli(
                root, "run", "--root-folder", str(root), "--detach", "--json",
                "--name", name, "tools/runner:worker", "--", "sh", "-c",
                "sleep 600", env=env,
            )
            assert launch.returncode == 0, launch.stderr
            event = json.loads(launch.stdout)
            concurrent.append((name, event["container"]["id"]))
        assert concurrent[0][1] != concurrent[1][1]
        for name, _container_id in concurrent:
            sibling = _inspect(name)
            assert sibling is not None and sibling["State"]["Status"] == "running"

        # A shell is the recorded detached owner. Kill only that controller,
        # then let `ciu run reap` remove its abandoned running one-shot while
        # the current pytest-owned siblings remain live.
        orphan_name = f"{project_name}-orphan-controller"
        owned_names.add(orphan_name)
        orphan_json = root / "orphan-launch.json"
        orphan_error = root / "orphan-launch.stderr"
        orphan_ready = root / "orphan-controller-ready"
        controller_script = (
            "set -e; "
            f"{shlex_quote(sys.executable)} -m ciu run "
            f"--root-folder {shlex_quote(str(root))} --detach --json "
            f"--owner-pid \"$$\" --name {shlex_quote(orphan_name)} "
            "tools/runner:worker -- sh -c 'sleep 600' "
            f"> {shlex_quote(str(orphan_json))} 2> {shlex_quote(str(orphan_error))}; "
            f": > {shlex_quote(str(orphan_ready))}; "
            "read -r _"
        )
        controller = subprocess.Popen(
            ["bash", "-c", controller_script], cwd=root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        controller_pid = controller.pid
        _wait_for_path(orphan_ready, controller)
        orphan_event = json.loads(orphan_json.read_text(encoding="utf-8"))
        orphan_id = orphan_event["container"]["id"]
        assert _inspect(orphan_id)["State"]["Status"] == "running"
        os.kill(controller_pid, signal.SIGKILL)
        controller.wait(timeout=30)
        controller = None
        orphan_started = datetime.fromisoformat(
            orphan_event["labels"]["ciu.run.started"]
        ).astimezone(UTC)
        age_threshold = orphan_started + timedelta(seconds=1)
        while datetime.now(UTC) < age_threshold:
            time.sleep(0.05)
        reaped = _cli(
            root, "run", "--root-folder", str(root), "reap", "--older-than", "1s", "--yes",
            env=env,
        )
        assert reaped.returncode == 0, reaped.stderr
        assert orphan_id in reaped.stdout and "removed" in reaped.stdout
        assert _inspect(orphan_id) is None
        for _name, sibling_id in concurrent:
            sibling = _inspect(sibling_id)
            assert sibling is not None and sibling["State"]["Status"] == "running"

        # Compose plain down leaves one-offs. CIU clean's down --remove-orphans
        # removes this checkout's own one-offs (the ownership guard rejects a
        # foreign root before reaching that destructive command).
        down = _cli(root, "down", "--root-folder", str(root), env=env)
        assert down.returncode == 0, down.stderr
        for _name, sibling_id in concurrent:
            sibling = _inspect(sibling_id)
            assert sibling is not None and sibling["State"]["Status"] == "running"
        cleaned = _cli(root, "clean", "--root-folder", str(root), "--yes", env=env)
        assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
        for _name, sibling_id in concurrent:
            assert _inspect(sibling_id) is None
        assert _inspect(dependency_name) is None
    finally:
        if foreground_process is not None and foreground_process.poll() is None:
            foreground_process.kill()
            foreground_process.wait(timeout=30)
        if controller is not None and controller.poll() is None:
            controller.kill()
            controller.wait(timeout=30)
        for name in sorted(owned_names):
            _docker(["rm", "-f", name])
        for volume_name in volumes_created:
            _docker(["volume", "rm", volume_name])
        if network_created:
            _docker(["network", "rm", network])
        if image_built:
            _docker(["image", "rm", "-f", image])
        shutil.rmtree(root, ignore_errors=True)


def shlex_quote(value: str) -> str:
    """Quote a fixture path/name for the one intentional bash controller."""
    return shlex.quote(value)
