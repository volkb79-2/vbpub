"""Fake-only acceptance checks for the runner's host endpoint record handoff."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "runner"
START = RUNNER / "start-cockpit-runner.sh"

ENDPOINTS = {
    "MDT_HOST_DOCKER_GID": "221",
    "MDT_DOCKER_API_SOCKET": "/run/docker-api/docker.sock",
    "MDT_DOCKER_API_SOCKET_DIR": "/run/docker-api",
    "CGPROFILE_CONTROL_SOCKET": "/run/cgprofile/control/cgprofile.sock",
    "CGPROFILE_CONTROL_SOCKET_DIR": "/run/cgprofile/control",
    "CGPROFILE_CONTROL_GID": "332",
    "CGPROFILE_OBSERVER_SOCKET_DIR": "/run/cgprofile/observer",
    "TOPOS_OBSERVE_SOCKET": "/run/topos/observe/topos.sock",
    "TOPOS_OBSERVE_SOCKET_DIR": "/run/topos/observe",
    "TOPOS_OBSERVE_GID": "443",
    "MDT_BUILDKIT_SOCKET": "/run/mdt-buildkitd/buildkitd.sock",
    "MDT_BUILDKIT_SOCKET_DIR": "/run/mdt-buildkitd",
    "MDT_BUILDKIT_GID": "554",
}
LEVEL_GIDS = ("CGPROFILE_CONTROL_GID", "TOPOS_OBSERVE_GID", "MDT_BUILDKIT_GID")

# Documentation and explicit refusal text may explain these names. Executable
# runner files may not embed endpoint paths or numeric endpoint GIDs.
DOCUMENTATION_ALLOWLIST = {"README.md"}
NEVER_MATCH = re.compile(r"(?!)")
REFUSAL_CONTEXT_ALLOWLIST = {
    "lib.sh": re.compile(r"^\s*echo\s+\"host endpoints:", re.IGNORECASE),
}


def _runner_env() -> str:
    return "\n".join(
        (
            "RUNNER_FQDN=host.example.org",
            "RUNNER_SHORT=host",
            "RUNNER_SERVER_ID=7",
            "RUNNER_USER=runner",
            "RUNNER_UID=1003",
            "RUNNER_HOME=/home/runner",
            "COW_DEVICE=/dev/cow",
            "COW_PARENT_DISK=/dev/disk",
            "COW_MOUNT=/srv/cow",
            "COW_ROOT=/srv/cow/root",
            "GATES_SLICE=dev-gates.slice",
            "INTERACTIVE_SLICE=dev-interactive.slice",
            "BACKGROUND_SLICE=dev-background.slice",
            "COCKPIT_NAME=mdt-cockpit-runner",
            "COCKPIT_CONFIG_DIR=/tmp/cockpit-config",
            "COCKPIT_HOME_DIR=/tmp/cockpit-home",
            "COCKPIT_TMP_DIR=/tmp/cockpit-tmp",
            "COCKPIT_IMAGE=example.invalid/cockpit:test",
            "",
        )
    )


def _write_endpoint_file(path: Path, overrides: dict[str, str] | None = None, missing: str | None = None) -> None:
    facts = dict(ENDPOINTS)
    facts.update(overrides or {})
    if missing is not None:
        facts.pop(missing)
    path.write_text("".join(f"{name}={value}\n" for name, value in facts.items()))


def _run_start(tmp_path: Path, *, overrides: dict[str, str] | None = None, missing: str | None = None):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    endpoint_file = tmp_path / "host-endpoints.env"
    runner_env_file = tmp_path / "runner.env"
    argv_log = tmp_path / "docker-argv.bin"
    _write_endpoint_file(endpoint_file, overrides, missing)
    runner_env_file.write_text(_runner_env())

    fakes = {
        "docker": '#!/bin/bash\nprintf \'%s\\0\' "$@" >> "$DOCKER_ARGV_LOG"\nprintf \'\\0\' >> "$DOCKER_ARGV_LOG"\n',
        "hostname": "#!/bin/sh\nprintf 'host.example.org\\n'\n",
        "hostnamectl": "#!/bin/sh\nprintf 'host\\n'\n",
        "cat": "#!/bin/sh\nif [ \"$1\" = /etc/vbpub/server-id ]; then printf '7\\n'; else exec /bin/cat \"$@\"; fi\n",
        "id": "#!/bin/sh\ncase \"$1\" in -u|-g) printf '1003\\n' ;; *) exec /usr/bin/id \"$@\" ;; esac\n",
    }
    for name, body in fakes.items():
        path = fake_bin / name
        path.write_text(body)
        path.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "RUNNER_ENV": str(runner_env_file),
            # The only endpoint path override is this test-only variable.
            "NETCUP_TEST_HOST_ENDPOINTS_FILE": str(endpoint_file),
            "DOCKER_ARGV_LOG": str(argv_log),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    proc = subprocess.run(["/bin/bash", str(START)], cwd=RUNNER, env=env, text=True, capture_output=True)
    return proc, argv_log


def _docker_calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [chunk.decode().split("\0") for chunk in path.read_bytes().split(b"\0\0") if chunk]


def _option_values(argv: list[str], option: str) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == option]


def test_cockpit_run_argv_uses_recorded_groups_binds_and_environment(tmp_path: Path):
    proc, argv_log = _run_start(tmp_path)
    assert proc.returncode == 0, proc.stderr
    run_calls = [call[1:] for call in _docker_calls(argv_log) if call and call[0] == "run"]
    assert len(run_calls) == 1
    argv = run_calls[0]

    assert _option_values(argv, "--group-add") == [
        ENDPOINTS["MDT_HOST_DOCKER_GID"],
        ENDPOINTS["CGPROFILE_CONTROL_GID"],
        ENDPOINTS["TOPOS_OBSERVE_GID"],
        ENDPOINTS["MDT_BUILDKIT_GID"],
    ]

    mounts = _option_values(argv, "--mount")
    expected_dirs = (
        "MDT_DOCKER_API_SOCKET_DIR",
        "CGPROFILE_CONTROL_SOCKET_DIR",
        "CGPROFILE_OBSERVER_SOCKET_DIR",
        "TOPOS_OBSERVE_SOCKET_DIR",
        "MDT_BUILDKIT_SOCKET_DIR",
    )
    for variable in expected_dirs:
        endpoint_dir = ENDPOINTS[variable]
        assert f"type=bind,source={endpoint_dir},target={endpoint_dir},readonly" in mounts

    passed_env = dict(value.split("=", 1) for value in _option_values(argv, "-e"))
    for name, value in ENDPOINTS.items():
        assert passed_env[name] == value
    assert "CGPROFILE_OBSERVER_GID" not in passed_env
    assert passed_env["DOCKER_HOST"] == f"unix://{ENDPOINTS['MDT_DOCKER_API_SOCKET']}"
    assert passed_env["BUILDKIT_HOST"] == f"unix://{ENDPOINTS['MDT_BUILDKIT_SOCKET']}"


@pytest.mark.parametrize("missing", tuple(ENDPOINTS))
def test_missing_endpoint_variable_is_named_before_docker_runs(tmp_path: Path, missing: str):
    proc, argv_log = _run_start(tmp_path, missing=missing)
    assert proc.returncode == 2
    assert f"host endpoints: {missing} is not set" in proc.stderr
    assert _docker_calls(argv_log) == []


@pytest.mark.parametrize("gid_name", LEVEL_GIDS)
def test_zero_level_gid_is_refused(tmp_path: Path, gid_name: str):
    proc, argv_log = _run_start(tmp_path, overrides={gid_name: "000"})
    assert proc.returncode == 2
    assert f"host endpoints: {gid_name} must not be 0" in proc.stderr
    assert _docker_calls(argv_log) == []


@pytest.mark.parametrize("gid_name", LEVEL_GIDS)
def test_level_gid_equal_to_docker_gid_is_refused(tmp_path: Path, gid_name: str):
    proc, argv_log = _run_start(tmp_path, overrides={gid_name: "000221"})
    assert proc.returncode == 2
    assert f"host endpoints: {gid_name} must differ from MDT_HOST_DOCKER_GID" in proc.stderr
    assert _docker_calls(argv_log) == []


@pytest.mark.parametrize(
    ("left", "right"),
    (
        ("CGPROFILE_CONTROL_GID", "TOPOS_OBSERVE_GID"),
        ("CGPROFILE_CONTROL_GID", "MDT_BUILDKIT_GID"),
        ("TOPOS_OBSERVE_GID", "MDT_BUILDKIT_GID"),
    ),
)
def test_level_gids_must_be_pairwise_distinct(tmp_path: Path, left: str, right: str):
    proc, argv_log = _run_start(tmp_path, overrides={right: ENDPOINTS[left]})
    assert proc.returncode == 2
    assert f"host endpoints: {left} must differ from {right}" in proc.stderr
    assert _docker_calls(argv_log) == []


def test_runner_scripts_have_no_endpoint_path_or_numeric_gid_literals():
    forbidden_paths = re.compile(r"/run/(?:docker-api|cgprofile|topos|mdt-buildkitd)(?:/|$)")
    numeric_gid = re.compile(
        r"\b(?:MDT_HOST_DOCKER_GID|CGPROFILE_CONTROL_GID|TOPOS_OBSERVE_GID|MDT_BUILDKIT_GID)\s*=\s*[0-9]+"
        r"|--group-add(?:=|\s+)[\"']?[0-9]+\b"
    )
    script_files = [
        RUNNER / "lib.sh",
        RUNNER / "runner.env.example",
        RUNNER / "README.md",
        *sorted(RUNNER.glob("*.sh")),
    ]
    for path in script_files:
        if path.name in DOCUMENTATION_ALLOWLIST:
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if REFUSAL_CONTEXT_ALLOWLIST.get(path.name, NEVER_MATCH).search(line):
                continue
            assert forbidden_paths.search(line) is None, f"{path.name}:{line_number} hard-codes an endpoint path"
            assert numeric_gid.search(line) is None, f"{path.name}:{line_number} hard-codes an endpoint GID"
