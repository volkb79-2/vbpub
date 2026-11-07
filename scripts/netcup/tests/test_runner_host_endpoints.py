"""Fake-only acceptance checks for the runner's host endpoint record handoff."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "runner"
START = RUNNER / "start-cockpit-runner.sh"
LIB = RUNNER / "lib.sh"

# Deliberately differ from the historical defaults so a literal substituted into
# the launcher cannot accidentally produce the same argv as the record.
ENDPOINTS = {
    "MDT_HOST_DOCKER_GID": "1221",
    "MDT_DOCKER_API_SOCKET": "/run/x-docker/api/docker.sock",
    "MDT_DOCKER_API_SOCKET_DIR": "/run/x-docker/api",
    "CGPROFILE_CONTROL_SOCKET": "/srv/ep/cg-control/control.sock",
    "CGPROFILE_CONTROL_SOCKET_DIR": "/srv/ep/cg-control",
    "CGPROFILE_CONTROL_GID": "1332",
    "CGPROFILE_OBSERVER_SOCKET": "/srv/ep/cg-observe/observer.sock",
    "CGPROFILE_OBSERVER_SOCKET_DIR": "/srv/ep/cg-observe",
    "TOPOS_OBSERVE_SOCKET": "/opt/ep/topos/topos.sock",
    "TOPOS_OBSERVE_SOCKET_DIR": "/opt/ep/topos",
    "TOPOS_OBSERVE_GID": "1443",
    "MDT_BUILDKIT_SOCKET": "/var/ep/buildkit/buildkitd.sock",
    "MDT_BUILDKIT_SOCKET_DIR": "/var/ep/buildkit",
    "MDT_BUILDKIT_GID": "1554",
}
LEVEL_GIDS = ("CGPROFILE_CONTROL_GID", "TOPOS_OBSERVE_GID", "MDT_BUILDKIT_GID")
SOCKET_DIR_PAIRS = (
    ("MDT_DOCKER_API_SOCKET", "MDT_DOCKER_API_SOCKET_DIR"),
    ("CGPROFILE_CONTROL_SOCKET", "CGPROFILE_CONTROL_SOCKET_DIR"),
    ("CGPROFILE_OBSERVER_SOCKET", "CGPROFILE_OBSERVER_SOCKET_DIR"),
    ("TOPOS_OBSERVE_SOCKET", "TOPOS_OBSERVE_SOCKET_DIR"),
    ("MDT_BUILDKIT_SOCKET", "MDT_BUILDKIT_SOCKET_DIR"),
)

FORBIDDEN_PATH = re.compile(r"/run/(?:docker-api|cgprofile|topos|mdt-buildkitd)\b")
FORBIDDEN_GID = re.compile(
    r"\b(?:MDT_HOST_DOCKER_GID|CGPROFILE_CONTROL_GID|TOPOS_OBSERVE_GID|MDT_BUILDKIT_GID)\s*=\s*[\"']?[0-9]+"
    r"|--group-add(?:=|\s+)[\"']?[0-9]+\b"
)
DOCUMENTATION_ALLOWLIST = {"README.md"}


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


def _endpoint_text(overrides: dict[str, str] | None = None, missing: str | None = None) -> str:
    facts = dict(ENDPOINTS)
    facts.update(overrides or {})
    if missing is not None:
        facts.pop(missing)
    return "# generated test record\n" + "".join(f"{name}={value}\n" for name, value in facts.items())


def _fake_stat(fake_bin: Path) -> None:
    # Tests fake only the uid returned by stat. File type, symlink status, and
    # permission bits are checked against the real temporary file.
    stat = fake_bin / "stat"
    stat.write_text(
        "#!/bin/bash\n"
        "if [[ \"${1:-}\" == -c && \"${2:-}\" == '%u:%a' ]]; then\n"
        "  file=${@: -1}\n"
        "  mode=$(/usr/bin/stat -c '%a' -- \"$file\")\n"
        "  owner=${TEST_ENDPOINTS_UID:-0}\n"
        "  if [[ -f \"$file.fake-uid\" ]]; then IFS= read -r owner < \"$file.fake-uid\"; fi\n"
        "  printf '%s:%s\\n' \"$owner\" \"$mode\"\n"
        "else\n"
        "  exec /usr/bin/stat \"$@\"\n"
        "fi\n"
    )
    stat.chmod(0o755)


def _fake_tools(tmp_path: Path, docker_gid: str = "1221") -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    _fake_stat(fake_bin)
    for name, body in {
        "docker": '#!/bin/bash\nprintf \'%s\\0\' "$@" >> "$DOCKER_ARGV_LOG"\nprintf \'\\0\' >> "$DOCKER_ARGV_LOG"\n',
        "hostname": "#!/bin/sh\nprintf 'host.example.org\\n'\n",
        "hostnamectl": "#!/bin/sh\nprintf 'host\\n'\n",
        "cat": "#!/bin/sh\nif [ \"$1\" = /etc/vbpub/server-id ]; then printf '7\\n'; else exec /bin/cat \"$@\"; fi\n",
        "id": "#!/bin/sh\ncase \"$1\" in -u|-g) printf '1003\\n' ;; *) exec /usr/bin/id \"$@\" ;; esac\n",
        "getent": "#!/bin/sh\nif [ \"$1\" = group ] && [ \"$2\" = docker ]; then printf 'docker:x:%s:runner\\n' \"${TEST_DOCKER_GROUP_GID:-1221}\"; exit 0; fi\nexec /usr/bin/getent \"$@\"\n",
    }.items():
        path = fake_bin / name
        path.write_text(body)
        path.chmod(0o755)
    return fake_bin


def _run_start(
    tmp_path: Path,
    *,
    overrides: dict[str, str] | None = None,
    missing: str | None = None,
    endpoint_text: str | None = None,
    endpoint_mode: int = 0o644,
    owner_uid: str = "0",
    docker_group_gid: str | None = None,
):
    facts = dict(ENDPOINTS)
    facts.update(overrides or {})
    fake_bin = _fake_tools(tmp_path, docker_group_gid or facts["MDT_HOST_DOCKER_GID"])
    endpoint_file = tmp_path / "host-endpoints.env"
    runner_env_file = tmp_path / "runner.env"
    argv_log = tmp_path / "docker-argv.bin"
    endpoint_file.write_text(endpoint_text if endpoint_text is not None else _endpoint_text(overrides, missing))
    endpoint_file.chmod(endpoint_mode)
    runner_env_file.write_text(_runner_env())
    runner_env_file.chmod(0o600)

    # Run a temporary copy of the production launcher whose sole test seam is
    # an explicit loader argument. The checked-in caller remains pinned to /etc.
    test_runner = tmp_path / "runner"
    test_runner.mkdir()
    shutil.copy2(LIB, test_runner / "lib.sh")
    source = START.read_text()
    prod_call = re.compile(r"(?m)^load_host_endpoints /etc/host-endpoints\.env \|\| exit \$\?$")
    if not prod_call.search(source):
        source, count = re.subn(r"(?m)^load_host_endpoints(?:\s+.*)?$", "load_host_endpoints /etc/host-endpoints.env || exit $?", source, count=1)
        assert count == 1, "production launcher must call the loader exactly once"
    source, count = prod_call.subn(f"load_host_endpoints {shlex.quote(str(endpoint_file))} || exit $?", source, count=1)
    assert count == 1, "temporary launcher must pass its endpoint path explicitly"
    start_copy = test_runner / "start-cockpit-runner.sh"
    start_copy.write_text(source)
    start_copy.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "RUNNER_ENV": str(runner_env_file),
            "DOCKER_ARGV_LOG": str(argv_log),
            "TEST_ENDPOINTS_UID": owner_uid,
            "TEST_DOCKER_GROUP_GID": docker_group_gid or facts["MDT_HOST_DOCKER_GID"],
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    proc = subprocess.run(["/bin/bash", str(start_copy)], cwd=RUNNER, env=env, text=True, capture_output=True)
    return proc, argv_log, endpoint_file


def _run_loader_batch(tmp_path: Path, records: dict[str, Path]):
    fake_bin = _fake_tools(tmp_path)
    runner_env_file = tmp_path / "runner.env"
    runner_env_file.write_text(_runner_env())
    runner_env_file.chmod(0o600)
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    harness = tmp_path / "load-records.sh"
    harness.write_text(
        "#!/bin/bash\n"
        "set -Eeuo pipefail\n"
        f"source {shlex.quote(str(LIB))}\n"
        "while (($#)); do\n"
        "  case_name=$1; record_file=$2; result_base=$3; shift 3\n"
        '  if load_host_endpoints "$record_file" >"$result_base.stdout" 2>"$result_base.stderr"; then\n'
        '    printf "LOADED=%s\\n" "$MDT_DOCKER_API_SOCKET" >>"$result_base.stdout"\n'
        '    if [[ ${UNUSED_FACTORY_KEY+x} ]]; then printf "UNUSED_FACTORY_KEY leaked\\n" >>"$result_base.stdout"; fi\n'
        "    status=0\n"
        "  else\n"
        "    status=$?\n"
        "  fi\n"
        '  printf "%s\\n" "$status" >"$result_base.status"\n'
        "done\n"
    )
    harness.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "RUNNER_ENV": str(runner_env_file),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        }
    )
    args = []
    result_bases = {}
    for name, record_file in records.items():
        result_base = result_dir / name
        result_bases[name] = result_base
        args.extend((name, str(record_file), str(result_base)))
    proc = subprocess.run(["/bin/bash", str(harness), *args], cwd=RUNNER, env=env, text=True, capture_output=True)
    results = {}
    for name, result_base in result_bases.items():
        if not result_base.with_suffix(".status").exists():
            continue
        results[name] = {
            "status": int(result_base.with_suffix(".status").read_text().strip()),
            "stdout": result_base.with_suffix(".stdout").read_text(),
            "stderr": result_base.with_suffix(".stderr").read_text(),
            "record": records[name],
        }
    return proc, results


def _docker_calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [chunk.decode().split("\0") for chunk in path.read_bytes().split(b"\0\0") if chunk]


def _option_values(argv: list[str], option: str) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == option]


def _scan_script_files() -> list[Path]:
    scripts = []
    for path in RUNNER.rglob("*"):
        if not path.is_file() or path.name in DOCUMENTATION_ALLOWLIST:
            continue
        with path.open("rb") as stream:
            has_shebang = stream.readline().startswith(b"#!")
        is_executable = bool(path.stat().st_mode & 0o111)
        if is_executable or has_shebang or path.name == "runner.env.example":
            scripts.append(path)
    return sorted(scripts)


def _literal_findings(paths: list[Path]) -> list[str]:
    findings = []
    for path in paths:
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if FORBIDDEN_PATH.search(line):
                findings.append(f"{path.name}:{line_number} hard-codes an endpoint path")
            if FORBIDDEN_GID.search(line):
                findings.append(f"{path.name}:{line_number} hard-codes an endpoint GID")
    return findings


@pytest.fixture(scope="module")
def loader_results(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("runner-endpoint-records")
    records: dict[str, Path] = {}
    markers: dict[str, Path] = {}

    def add(
        name: str,
        content: str = "",
        *,
        mode: int = 0o644,
        owner_uid: str | None = None,
        symlink: bool = False,
        directory: bool = False,
    ) -> Path:
        path = root / f"{name}.env"
        if directory:
            path.mkdir()
        else:
            path.write_text(content)
            path.chmod(mode)
            if owner_uid is not None:
                path.with_name(path.name + ".fake-uid").write_text(owner_uid + "\n")
        if symlink:
            link = root / f"{name}-link.env"
            link.symlink_to(path)
            path = link
        records[name] = path
        return path

    safe_entries = _endpoint_text().split("\n", 1)[1]
    for name in ENDPOINTS:
        add(f"missing_{name}", _endpoint_text(missing=name))

    for index, record_line in enumerate(
        (
            "MDT_HOST_DOCKER_GID=$(touch {marker})",
            "MDT_HOST_DOCKER_GID=`touch {marker}`",
            "MDT_HOST_DOCKER_GID=1221;touch {marker}",
            "MDT_HOST_DOCKER_GID=1221 value",
            'MDT_HOST_DOCKER_GID="1221"',
            "MDT_BUILDKIT_SOCKET_DIR=/var/ep/buildkit,readonly=false",
        )
    ):
        name = f"malicious_{index}"
        marker = root / f"{name}.executed"
        markers[name] = marker
        line = record_line.format(marker=shlex.quote(str(marker)))
        add(name, "# safe comment\n" + safe_entries + line + "\n")

    comment_marker = root / "comment.executed"
    markers["comment"] = comment_marker
    add(
        "comment",
        f"# $(touch {comment_marker}); `touch {comment_marker}`\n"
        + safe_entries
        + "UNUSED_FACTORY_KEY=accepted_but_not_exported\n",
    )
    add("wrong_owner", _endpoint_text(), owner_uid="1000")
    add("group_writable", _endpoint_text(), mode=0o664)
    add("world_writable", _endpoint_text(), mode=0o666)
    add("unreadable", _endpoint_text(), mode=0o000)
    add("symlink", _endpoint_text(), symlink=True)
    add("not_regular", directory=True)
    add("duplicate_known", _endpoint_text() + "MDT_HOST_DOCKER_GID=1777\n")
    add("duplicate_unknown", _endpoint_text() + "UNUSED_FACTORY_KEY=one\nUNUSED_FACTORY_KEY=two\n")
    add("bad_key", _endpoint_text() + "lowercase=valid\n")
    add("long_value", _endpoint_text() + "UNUSED_FACTORY_KEY=" + ("x" * 4097) + "\n")
    add("docker_gid_zero", _endpoint_text({"MDT_HOST_DOCKER_GID": "0"}))
    add("docker_gid_leading_zero", _endpoint_text({"MDT_HOST_DOCKER_GID": "0221"}))
    for gid_name in LEVEL_GIDS:
        add(f"{gid_name}_zero", _endpoint_text({gid_name: "0"}))
        add(f"{gid_name}_leading_zero", _endpoint_text({gid_name: "01332"}))
        add(f"{gid_name}_docker_equal", _endpoint_text({gid_name: ENDPOINTS["MDT_HOST_DOCKER_GID"]}))
    for index, (left, right) in enumerate(
        (
            ("CGPROFILE_CONTROL_GID", "TOPOS_OBSERVE_GID"),
            ("CGPROFILE_CONTROL_GID", "MDT_BUILDKIT_GID"),
            ("TOPOS_OBSERVE_GID", "MDT_BUILDKIT_GID"),
        )
    ):
        add(f"pairwise_{index}", _endpoint_text({right: ENDPOINTS[left]}))
    add("relative_socket", _endpoint_text({"MDT_DOCKER_API_SOCKET": "relative/docker.sock"}))
    add("mismatched_socket_dir", _endpoint_text({"MDT_BUILDKIT_SOCKET_DIR": "/var/ep/other"}))

    proc, results = _run_loader_batch(root, records)
    assert proc.returncode == 0, proc.stderr
    assert set(results) == set(records), proc.stderr
    return results, markers


def test_cockpit_run_argv_uses_recorded_groups_binds_and_environment(tmp_path: Path):
    assert "load_host_endpoints /etc/host-endpoints.env || exit $?" in START.read_text().splitlines()
    proc, argv_log, _ = _run_start(tmp_path)
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
    for _, dir_name in SOCKET_DIR_PAIRS:
        endpoint_dir = ENDPOINTS[dir_name]
        assert f"type=bind,source={endpoint_dir},target={endpoint_dir},readonly" in mounts

    passed_env = dict(value.split("=", 1) for value in _option_values(argv, "-e"))
    for name, value in ENDPOINTS.items():
        assert passed_env[name] == value
    assert "CGPROFILE_OBSERVER_GID" not in passed_env
    assert passed_env["DOCKER_HOST"] == f"unix://{ENDPOINTS['MDT_DOCKER_API_SOCKET']}"
    assert passed_env["BUILDKIT_HOST"] == f"unix://{ENDPOINTS['MDT_BUILDKIT_SOCKET']}"


@pytest.mark.parametrize("missing", tuple(ENDPOINTS))
def test_missing_endpoint_variable_is_named(loader_results, missing: str):
    results, _ = loader_results
    result = results[f"missing_{missing}"]
    assert result["status"] == 2
    assert f"host endpoints: {missing} is not set" in result["stderr"]


def test_missing_endpoint_refuses_before_docker_runs(tmp_path: Path):
    proc, argv_log, _ = _run_start(tmp_path, missing="CGPROFILE_OBSERVER_SOCKET")
    assert proc.returncode == 2
    assert "host endpoints: CGPROFILE_OBSERVER_SOCKET is not set" in proc.stderr
    assert _docker_calls(argv_log) == []


@pytest.mark.parametrize("index", range(6))
def test_record_parser_refuses_shell_syntax_and_mount_option_injection(loader_results, index: int):
    results, markers = loader_results
    result = results[f"malicious_{index}"]
    assert result["status"] == 2
    assert f"{result['record']}:16" in result["stderr"]
    assert "invalid record" in result["stderr"]
    assert not markers[f"malicious_{index}"].exists(), "record content was executed"


def test_record_parser_ignores_comments_and_exports_only_runner_keys(loader_results):
    results, markers = loader_results
    result = results["comment"]
    assert result["status"] == 0
    assert f"LOADED={ENDPOINTS['MDT_DOCKER_API_SOCKET']}" in result["stdout"]
    assert "UNUSED_FACTORY_KEY leaked" not in result["stdout"]
    assert not markers["comment"].exists(), "comment content was executed"


@pytest.mark.parametrize(
    ("name", "reason"),
    (
        ("wrong_owner", "not owned by root"),
        ("group_writable", "group/other writable"),
        ("world_writable", "group/other writable"),
        ("unreadable", "is not readable"),
        ("symlink", "must not be a symlink"),
    ),
)
def test_record_metadata_must_be_trusted(loader_results, name: str, reason: str):
    results, _ = loader_results
    result = results[name]
    assert result["status"] == 2
    assert str(result["record"]) in result["stderr"]
    assert reason in result["stderr"]


def test_record_must_be_a_regular_file(loader_results):
    results, _ = loader_results
    result = results["not_regular"]
    assert result["status"] == 2
    assert f"{result['record']} is not a regular file" in result["stderr"]


@pytest.mark.parametrize(
    ("name", "line_number", "message"),
    (
        ("duplicate_known", 16, "duplicate key MDT_HOST_DOCKER_GID"),
        ("duplicate_unknown", 17, "duplicate key UNUSED_FACTORY_KEY"),
        ("bad_key", 16, "invalid record"),
        ("long_value", 16, "invalid record"),
    ),
)
def test_record_parser_rejects_duplicates_and_out_of_grammar_lines(loader_results, name: str, line_number: int, message: str):
    results, _ = loader_results
    result = results[name]
    assert result["status"] == 2
    assert f"{result['record']}:{line_number}" in result["stderr"]
    assert message in result["stderr"]


@pytest.mark.parametrize(
    ("name", "key"),
    (
        ("docker_gid_zero", "MDT_HOST_DOCKER_GID"),
        ("docker_gid_leading_zero", "MDT_HOST_DOCKER_GID"),
        *((f"{gid}_zero", gid) for gid in LEVEL_GIDS),
        *((f"{gid}_leading_zero", gid) for gid in LEVEL_GIDS),
    ),
)
def test_all_runner_gids_are_positive_canonical_decimal(loader_results, name: str, key: str):
    results, _ = loader_results
    result = results[name]
    assert result["status"] == 2
    assert f"host endpoints: {key} must be a positive decimal GID without leading zeros" in result["stderr"]


@pytest.mark.parametrize("gid_name", LEVEL_GIDS)
def test_level_gid_equal_to_docker_gid_is_refused(loader_results, gid_name: str):
    results, _ = loader_results
    result = results[f"{gid_name}_docker_equal"]
    assert result["status"] == 2
    assert f"host endpoints: {gid_name} must differ from MDT_HOST_DOCKER_GID" in result["stderr"]


def test_cockpit_start_refuses_docker_group_gid_drift(tmp_path: Path):
    proc, argv_log, _ = _run_start(tmp_path, docker_group_gid="1999")
    assert proc.returncode == 2
    assert "MDT_HOST_DOCKER_GID does not match the local docker group GID" in proc.stderr
    assert _docker_calls(argv_log) == []


@pytest.mark.parametrize(
    ("name", "message"),
    (
        ("pairwise_0", "CGPROFILE_CONTROL_GID must differ from TOPOS_OBSERVE_GID"),
        ("pairwise_1", "CGPROFILE_CONTROL_GID must differ from MDT_BUILDKIT_GID"),
        ("pairwise_2", "TOPOS_OBSERVE_GID must differ from MDT_BUILDKIT_GID"),
    ),
)
def test_level_gids_must_be_pairwise_distinct(loader_results, name: str, message: str):
    results, _ = loader_results
    result = results[name]
    assert result["status"] == 2
    assert f"host endpoints: {message}" in result["stderr"]


@pytest.mark.parametrize(
    ("name", "key", "reason"),
    (
        ("relative_socket", "MDT_DOCKER_API_SOCKET", "must be absolute"),
        ("mismatched_socket_dir", "MDT_BUILDKIT_SOCKET_DIR", "must equal dirname(MDT_BUILDKIT_SOCKET)"),
    ),
)
def test_socket_paths_must_be_absolute_and_match_their_directories(loader_results, name: str, key: str, reason: str):
    results, _ = loader_results
    result = results[name]
    assert result["status"] == 2
    assert f"host endpoints: {key} {reason}" in result["stderr"]


def test_literal_path_scan_covers_all_runner_scripts():
    paths = _scan_script_files()
    names = {path.name for path in paths}
    assert {"remote-gate.py", "run-job", "sync-from-devcontainer", "provision-runner.sh"} <= names
    assert _literal_findings(paths) == []


@pytest.mark.parametrize(
    ("name", "line", "finding"),
    (
        ("start-cockpit-runner.sh", "target=/run/docker-api,readonly", "endpoint path"),
        ("provision-runner.sh", "ls /run/topos >/dev/null", "endpoint path"),
        ("remote-gate.py", "# /run/mdt-buildkitd/x", "endpoint path"),
        ("run-job", "docker run --group-add 221 image", "endpoint GID"),
    ),
)
def test_literal_path_scan_kills_review_survivor_fixtures(tmp_path: Path, name: str, line: str, finding: str):
    fixture = tmp_path / name
    fixture.write_text(line + "\n")
    assert finding in _literal_findings([fixture])[0]
