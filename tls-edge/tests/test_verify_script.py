"""End-to-end checks for the installed verify.sh operator script.

The harness copies the real script under a temporary instance root, then runs
it with a recording Docker stub. The stub executes every sh -c probe with its
supplied environment, using local wget/grep stand-ins and no containers or
network access.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT


VERIFY_SCRIPT = ROOT / "scripts" / "verify.sh"
REPO_ROOT = ROOT.parent
DOMAIN = "example.test"
TRAEFIK = "traefik-test"
PROXY = "proxy-test"
CANARY = "tlsedge-verify-canary"


_DOCKER_STUB = r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["VERIFY_DOCKER_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")


def finish(code=0, stdout="", stderr=""):
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    raise SystemExit(code)


def setting(name, default=""):
    return os.environ.get(name, default)


if not args:
    finish(2, stderr="docker stub: missing command\n")

if args[0] == "ps":
    finish()

if args[0] == "compose":
    code = int(setting("VERIFY_COMPOSE_EXIT", "0"))
    finish(code, stderr="compose config rejected\n" if code else "")

if args[0] == "inspect":
    fmt = args[2] if len(args) > 2 and args[1] == "-f" else ""
    if ".State.Status" in fmt:
        value = "stopped" if setting("VERIFY_CONTAINERS_RUNNING", "yes") == "no" else "running"
        finish(0, value + "\n")
    if ".State.Health.Status" in fmt:
        bad = setting("VERIFY_HEALTH_FAIL") == args[-1]
        finish(0, "unhealthy\n" if bad else "healthy\n")
    finish(0)

if args[0] == "network" and len(args) >= 3 and args[1] == "inspect":
    target = args[-1]
    if target == "ingress_public":
        finish(int(setting("VERIFY_PUBLIC_NETWORK_EXIT", "0")))
    if target == "traefik_internal":
        code = int(setting("VERIFY_INTERNAL_NETWORK_EXIT", "0"))
        finish(code, setting("VERIFY_INTERNAL_NETWORK", "true") + "\n" if code == 0 else "")
    finish(1)

if args[0] == "logs":
    if "--tail" in args:
        if setting("VERIFY_ACCESS_LOG_READ_FAIL") == "yes":
            finish(1, stderr="logs unavailable\n")
        if setting("VERIFY_ACCESS_LOG", "json") == "json":
            finish(0, '{"RouterName":"app@docker"}\n')
        finish(0)
    if setting("VERIFY_ACME_LOGS_FAIL") == "yes":
        finish(1, stderr="logs unavailable\n")
    finish(0, setting("VERIFY_ACME_LOG_CONTENT"))

if args[0] == "rm":
    finish(0)

if args[0] != "run":
    finish(2, stderr="docker stub: unsupported invocation\n")

run_env = {}
volumes = []
i = 1
image = None
command = []
value_options = {"--network", "--name", "-l", "-e", "-v", "--entrypoint", "--volumes-from"}
while i < len(args):
    item = args[i]
    if item in {"--rm", "-d"}:
        i += 1
        continue
    if item in value_options:
        value = args[i + 1]
        if item == "-e":
            key, separator, val = value.partition("=")
            run_env[key] = val if separator else ""
        elif item == "-v":
            volumes.append(value)
        i += 2
        continue
    image = item
    command = args[i + 1:]
    break

if image is None:
    finish(2, stderr="docker stub: run image missing\n")

if image == "traefik/whoami:v1.11":
    code = int(setting("VERIFY_CANARY_START_EXIT", "0"))
    finish(code, stderr="canary start failed\n" if code else "")

if image == "curlimages/curl:8.10.1":
    urls = [item for item in command if item.startswith(("http://", "https://"))]
    url = urls[-1] if urls else ""
    if url.startswith("http://"):
        finish(0, setting("VERIFY_REDIRECT_STATUS", "301"))
    if url.startswith("https://"):
        count_file = Path(setting("VERIFY_CANARY_COUNT_FILE"))
        count = int(count_file.read_text(encoding="utf-8")) + 1 if count_file.exists() else 1
        count_file.write_text(str(count), encoding="utf-8")
        after = int(setting("VERIFY_CANARY_AFTER", "1"))
        finish(0, "Hostname: whoami-test\n" if count >= after else "")
    finish(1)

if image == "alpine/openssl":
    finish(0, setting("VERIFY_TLS_OUTPUT", "subject=CN=example.test\nissuer=CN=Test CA\n"))

if image == "alpine:3.20" and command[:2] == ["sh", "-c"]:
    if "PROXY_HOST" in run_env and setting("VERIFY_PROXY_PROBE_RUN_FAIL") == "yes":
        finish(1, stderr="proxy probe did not run\n")
    if "OPS_URL" in run_env and setting("VERIFY_OPS_PROBE_RUN_FAIL") == "yes":
        finish(1, stderr="ops probe did not run\n")

    probe = command[2]
    for volume in volumes:
        if volume.endswith(":/a:ro"):
            probe = probe.replace("/a/acme.json", setting("VERIFY_ACME_PATH"))
    child_env = {
        key: value for key, value in os.environ.items()
        if key not in {"EDGE_TRAEFIK", "EDGE_DOCKERPROXY", "EDGE_ACME_VOLUME", "DOMAIN"}
    }
    child_env.update(run_env)
    completed = subprocess.run(
        ["sh", "-c", probe],
        capture_output=True,
        check=False,
        env=child_env,
        text=True,
    )
    finish(completed.returncode, completed.stdout, completed.stderr)

finish(2, stderr="docker stub: unsupported image " + image + "\n")
'''


_WGET_STUB = r'''#!/bin/sh
printf '%s\n' "$*" >> "$VERIFY_WGET_LOG"
url=""
for arg in "$@"; do
    case "$arg" in
        http://*|https://*) url="$arg" ;;
    esac
done
case "$url" in
    */version)
        if [ "$VERIFY_PROXY_GET" = "fail" ]; then
            echo "wget: connection refused" >&2
            exit 1
        fi
        echo '{"ApiVersion":"1.45"}'
        exit 0
        ;;
    */containers/create)
        case "$VERIFY_POST_STATUS" in
            403) echo "server returned error: HTTP/1.1 403 Forbidden" >&2; exit 1 ;;
            400) echo "server returned error: HTTP/1.1 400 Bad Request" >&2; exit 1 ;;
            200) echo '{}' ; exit 0 ;;
            *) echo "wget: connection refused" >&2; exit 1 ;;
        esac
        ;;
    */metrics)
        if [ "$VERIFY_OPS_MODE" = "reachable" ]; then
            echo "metrics"
            exit 0
        fi
        echo "wget: connection refused" >&2
        exit 1
        ;;
esac
echo "wget: no scripted response for $url" >&2
exit 1
'''


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _make_instance(
    run_root: Path, env_overrides: dict[str, str] | None = None
) -> tuple[Path, dict[str, str]]:
    instance = run_root / "instance"
    scripts = instance / "scripts"
    stack = instance / "ciu-stack"
    bin_dir = run_root / "bin"
    acme_dir = run_root / "acme-volume"
    for directory in (scripts, stack, bin_dir, acme_dir):
        directory.mkdir(parents=True, exist_ok=True)

    shutil.copy2(VERIFY_SCRIPT, scripts / "verify.sh")
    (stack / "ciu.compose.yml").write_text("services: {}\n", encoding="utf-8")
    (stack / "ciu.defaults.toml.j2").write_text(
        'mode = "acme-tls"\nexpose_http = true\n', encoding="utf-8"
    )
    _write_executable(
        instance / "ciu",
        "#!/bin/sh\n"
        'if [ "$VERIFY_CIU_EXIT" = "0" ]; then echo "ciu check ok"; exit 0; fi\n'
        'echo "ciu check failed" >&2\nexit 1\n',
    )

    overrides = env_overrides or {}
    acme_file = acme_dir / "acme.json"
    if overrides.get("VERIFY_ACME_PATH_MISSING") != "yes":
        acme_file.write_text(
            overrides.get(
                "VERIFY_ACME_CONTENT",
                '{"Certificates":[{"domain":"example.test"}]}\n',
            ),
            encoding="utf-8",
        )
        acme_file.chmod(0o644 if overrides.get("VERIFY_ACME_PERMS_BAD") == "yes" else 0o600)

    real_grep = shutil.which("grep")
    assert real_grep is not None
    _write_executable(bin_dir / "docker", _DOCKER_STUB)
    _write_executable(bin_dir / "wget", _WGET_STUB)
    _write_executable(
        bin_dir / "grep",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$VERIFY_GREP_LOG"\n'
        f"exec {shlex.quote(real_grep)} \"$@\"\n",
    )
    _write_executable(
        bin_dir / "sleep",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$VERIFY_SLEEP_LOG"\n'
        "exit 0\n",
    )

    env = dict(os.environ)
    env.update(
        {
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
            "EDGE_TRAEFIK": TRAEFIK,
            "EDGE_DOCKERPROXY": PROXY,
            "EDGE_ACME_VOLUME": "acme-volume-test",
            "VERIFY_DOCKER_LOG": str(run_root / "docker.jsonl"),
            "VERIFY_WGET_LOG": str(run_root / "wget.log"),
            "VERIFY_GREP_LOG": str(run_root / "grep.log"),
            "VERIFY_SLEEP_LOG": str(run_root / "sleep.log"),
            "VERIFY_CANARY_COUNT_FILE": str(run_root / "canary-count"),
            "VERIFY_ACME_PATH": str(acme_file),
            "VERIFY_CIU_EXIT": "0",
            "VERIFY_PROXY_GET": "ok",
            "VERIFY_POST_STATUS": "403",
            "VERIFY_OPS_MODE": "unreachable",
            "VERIFY_ACCESS_LOG": "json",
        }
    )
    env.update(overrides)
    return instance, env


def _run_verify(
    run_root: Path,
    *,
    env_overrides: dict[str, str] | None = None,
    args: tuple[str, ...] = ("--domain", DOMAIN),
    script_text: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], str, dict[str, str]]:
    instance, env = _make_instance(run_root, env_overrides)
    script = instance / "scripts" / "verify.sh"
    if script_text is not None:
        script.write_text(script_text, encoding="utf-8")
    completed = subprocess.run(
        ["bash", str(script), *args],
        cwd=instance,
        capture_output=True,
        check=False,
        env=env,
        text=True,
    )
    return completed, completed.stdout + completed.stderr, env


def _clean(output: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


def _section(output: str, number: int) -> str:
    clean = _clean(output)
    headings = list(re.finditer(r"(?m)^(\d+)\. ", clean))
    for index, heading in enumerate(headings):
        if int(heading.group(1)) == number:
            end = headings[index + 1].start() if index + 1 < len(headings) else len(clean)
            return clean[heading.start():end]
    raise AssertionError(f"check {number} section missing from output:\n{clean}")


def _assert_check(output: str, number: int, status: str) -> str:
    section = _section(output, number)
    assert f"[{status}]" in section, section
    return section


def _docker_calls(env: dict[str, str]) -> list[list[str]]:
    path = Path(env["VERIFY_DOCKER_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _labels(call: list[str]) -> list[str]:
    return [call[index + 1] for index, item in enumerate(call[:-1]) if item == "-l"]


def _canary_probe_calls(calls: list[list[str]]) -> list[list[str]]:
    return [
        call for call in calls
        if call[:1] == ["run"]
        and "curlimages/curl:8.10.1" in call
        and any(item.startswith("https://") for item in call)
    ]


def test_full_success_exits_zero_and_canary_is_acme_free_and_removed(tmp_path: Path):
    completed, output, env = _run_verify(tmp_path)
    assert completed.returncode == 0, output
    for number in range(1, 11):
        _assert_check(output, number, "PASS")
    _assert_check(output, 11, "SKIP")

    clean = _clean(output)
    observed_order = [int(value) for value in re.findall(r"(?m)^(\d+)\. ", clean)]
    assert observed_order == list(range(1, 12)), clean
    assert "cleanup_tmp: command not found" not in output

    calls = _docker_calls(env)
    canary_start = next(
        call for call in calls if call[:1] == ["run"] and "traefik/whoami:v1.11" in call
    )
    labels = _labels(canary_start)
    tick = chr(96)
    assert f"traefik.http.routers.verify-canary.rule=Host({tick}tls-edge-verify-canary.invalid{tick})" in labels
    assert "traefik.http.routers.verify-canary.entrypoints=websecure" in labels
    assert "traefik.http.routers.verify-canary.tls=true" in labels
    assert not any("certresolver" in label.lower() for label in labels)

    probes = _canary_probe_calls(calls)
    assert len(probes) == 1
    probe = probes[0]
    assert "--max-time" in probe and probe[probe.index("--max-time") + 1] == "5"
    assert probe[probe.index("--connect-to") + 1] == f"{DOMAIN}:443:{TRAEFIK}:443"
    assert probe[probe.index("-H") + 1] == "Host: tls-edge-verify-canary.invalid"
    assert f"https://{DOMAIN}/" in probe
    assert any(call == ["rm", "-f", CANARY] for call in calls)

    wget_log = Path(env["VERIFY_WGET_LOG"]).read_text(encoding="utf-8")
    assert f"http://{PROXY}:2375/version" in wget_log
    assert f"http://{PROXY}:2375/containers/create" in wget_log
    proxy_probe = next(
        call for call in calls if call[:1] == ["run"] and "PROXY_HOST=" + PROXY in call
    )
    assert proxy_probe[proxy_probe.index("-e") + 1] == "PROXY_HOST=" + PROXY
    assert "sh" in proxy_probe and "-c" in proxy_probe


@pytest.mark.parametrize(
    ("number", "env_overrides", "args"),
    [
        (1, {"VERIFY_CIU_EXIT": "1"}, ("--domain", DOMAIN)),
        (2, {"VERIFY_COMPOSE_EXIT": "1"}, ("--domain", DOMAIN)),
        (3, {"VERIFY_HEALTH_FAIL": TRAEFIK}, ("--domain", DOMAIN)),
        (4, {"VERIFY_INTERNAL_NETWORK": "false"}, ("--domain", DOMAIN)),
        (5, {"VERIFY_POST_STATUS": "400"}, ("--domain", DOMAIN)),
        (5, {"VERIFY_POST_STATUS": "200"}, ("--domain", DOMAIN)),
        (6, {"VERIFY_REDIRECT_STATUS": "200"}, ("--domain", DOMAIN)),
        (7, {"VERIFY_TLS_OUTPUT": ""}, ("--domain", DOMAIN)),
        (8, {"VERIFY_ACME_PERMS_BAD": "yes"}, ("--domain", DOMAIN)),
        (9, {"VERIFY_CANARY_AFTER": "999"}, ("--domain", DOMAIN)),
        (
            11,
            {"VERIFY_OPS_MODE": "reachable"},
            ("--domain", DOMAIN, "--ops-check", "ingress_public", "ops-app", "8080"),
        ),
    ],
)
def test_each_check_reports_observed_failure(
    tmp_path: Path,
    number: int,
    env_overrides: dict[str, str],
    args: tuple[str, ...],
):
    completed, output, _ = _run_verify(tmp_path, env_overrides=env_overrides, args=args)
    assert completed.returncode == 1, output
    _assert_check(output, number, "FAIL")


def test_proxy_probe_without_output_fails_check_5(tmp_path: Path):
    completed, output, _ = _run_verify(
        tmp_path, env_overrides={"VERIFY_PROXY_PROBE_RUN_FAIL": "yes"}
    )
    assert completed.returncode == 1, output
    assert "probe did not report a result" in _assert_check(output, 5, "FAIL")


def test_acme_zero_domain_matches_warns_and_domain_is_passed_as_data(tmp_path: Path):
    completed, output, env = _run_verify(
        tmp_path, env_overrides={"VERIFY_ACME_CONTENT": '{"Certificates":[]}\\n'}
    )
    assert completed.returncode == 0, output
    section = _assert_check(output, 8, "WARN")
    assert "does not mention example.test" in section
    assert "references domain example.test" not in section
    calls = _docker_calls(env)
    domain_probe = next(
        call for call in calls if call[:1] == ["run"] and "VERIFY_DOMAIN=" + DOMAIN in call
    )
    assert domain_probe[domain_probe.index("-e") + 1] == "VERIFY_DOMAIN=" + DOMAIN


def test_acme_domain_probe_read_failure_fails_check_8(tmp_path: Path):
    completed, output, _ = _run_verify(
        tmp_path, env_overrides={"VERIFY_ACME_PATH_MISSING": "yes"}
    )
    assert completed.returncode == 1, output
    assert "domain probe could not read the file" in _assert_check(output, 8, "FAIL")


def test_acme_log_read_failure_fails_check_8(tmp_path: Path):
    completed, output, _ = _run_verify(
        tmp_path, env_overrides={"VERIFY_ACME_LOGS_FAIL": "yes"}
    )
    assert completed.returncode == 1, output
    assert "could not read traefik logs" in _assert_check(output, 8, "FAIL")


@pytest.mark.parametrize(
    ("env_overrides", "probe_count", "sleep_count"),
    [
        ({"VERIFY_CANARY_AFTER": "3"}, 3, 2),
        ({"VERIFY_CANARY_AFTER": "999"}, 30, 29),
    ],
)
def test_canary_polls_until_hostname_or_thirty_probes_then_cleans_up(
    tmp_path: Path,
    env_overrides: dict[str, str],
    probe_count: int,
    sleep_count: int,
):
    completed, output, env = _run_verify(tmp_path, env_overrides=env_overrides)
    calls = _docker_calls(env)
    assert len(_canary_probe_calls(calls)) == probe_count
    sleeps = Path(env["VERIFY_SLEEP_LOG"]).read_text(encoding="utf-8").splitlines()
    assert len(sleeps) == sleep_count
    assert any(call == ["rm", "-f", CANARY] for call in calls)
    if probe_count == 3:
        assert completed.returncode == 0, output
        _assert_check(output, 9, "PASS")
    else:
        assert completed.returncode == 1, output
        _assert_check(output, 9, "FAIL")


def test_canary_start_failure_is_check_9_failure_and_later_checks_run(tmp_path: Path):
    completed, output, env = _run_verify(
        tmp_path, env_overrides={"VERIFY_CANARY_START_EXIT": "1"}
    )
    assert completed.returncode == 1, output
    assert "could not be started" in _assert_check(output, 9, "FAIL")
    _assert_check(output, 10, "PASS")
    calls = _docker_calls(env)
    assert any(call == ["rm", "-f", CANARY] for call in calls)


def test_ops_probe_requires_marker_when_docker_run_does_not_run(tmp_path: Path):
    completed, output, env = _run_verify(
        tmp_path,
        env_overrides={"VERIFY_OPS_PROBE_RUN_FAIL": "yes"},
        args=("--domain", DOMAIN, "--ops-check", "ingress_public", "ops-app", "8080"),
    )
    assert completed.returncode == 1, output
    assert "did not report reachable or unreachable" in _assert_check(output, 11, "FAIL")
    calls = _docker_calls(env)
    probe = next(call for call in calls if call[:1] == ["run"] and "OPS_URL=" in " ".join(call))
    assert probe[probe.index("-e") + 1] == "OPS_URL=http://ops-app:8080/metrics"


def test_ops_probe_reports_unreachable_as_pass(tmp_path: Path):
    completed, output, env = _run_verify(
        tmp_path,
        args=("--domain", DOMAIN, "--ops-check", "ingress_public", "ops-app", "8080"),
    )
    assert completed.returncode == 0, output
    _assert_check(output, 11, "PASS")
    wget_log = Path(env["VERIFY_WGET_LOG"]).read_text(encoding="utf-8")
    assert "http://ops-app:8080/metrics" in wget_log


def test_access_log_warns_when_there_is_no_json_evidence(tmp_path: Path):
    completed, output, _ = _run_verify(tmp_path, env_overrides={"VERIFY_ACCESS_LOG": "empty"})
    assert completed.returncode == 0, output
    assert "no JSON lines with RouterName" in _assert_check(output, 10, "WARN")


def test_access_log_read_failure_is_an_explicit_warning(tmp_path: Path):
    completed, output, _ = _run_verify(
        tmp_path, env_overrides={"VERIFY_ACCESS_LOG_READ_FAIL": "yes"}
    )
    assert completed.returncode == 0, output
    assert "could not read Traefik logs" in _assert_check(output, 10, "WARN")


def test_4_0_0_script_reproduces_the_regressions_in_the_same_harness(tmp_path: Path):
    legacy = subprocess.run(
        ["git", "show", "tls-edge-v4.0.0:tls-edge/scripts/verify.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    completed, output, env = _run_verify(
        tmp_path,
        env_overrides={"VERIFY_POST_STATUS": "400"},
        script_text=legacy,
    )
    assert completed.returncode == 127, output
    assert "cleanup_tmp: command not found" in output
    assert "[PASS] socket proxy: GET /version allowed, POST /containers/create rejected" in _section(output, 5)
    wget_log = Path(env["VERIFY_WGET_LOG"]).read_text(encoding="utf-8")
    assert "http://:2375/version" in wget_log

    calls = _docker_calls(env)
    canary_start = next(
        call for call in calls if call[:1] == ["run"] and "traefik/whoami:v1.11" in call
    )
    labels = _labels(canary_start)
    tick = chr(96)
    assert f"traefik.http.routers.verify-canary.rule=Host({tick}verify.example.test{tick})" in labels
    assert not any(label.endswith(".tls=true") for label in labels)
    assert not any("certresolver" in label.lower() for label in labels)
    old_probes = _canary_probe_calls(calls)
    assert len(old_probes) == 1
    assert "https://verify.example.test/" in old_probes[0]
    assert not any(item.startswith("Host: ") for item in old_probes[0])
