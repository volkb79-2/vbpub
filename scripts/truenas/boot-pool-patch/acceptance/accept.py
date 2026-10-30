#!/usr/bin/env python3
"""Run a registered TrueNAS update acceptance scenario on the disposable host."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid
from typing import Any, Iterator

from patch_drift import DriftError, official_latest_update, load_supported_builds
from remote import RemoteError, Target


ACCEPTANCE_ROOT = Path(__file__).resolve().parent
PATCH_ROOT = ACCEPTANCE_ROOT.parent
TRUENAS_ROOT = PATCH_ROOT.parent
WORKTREE_ROOT = TRUENAS_ROOT.parent.parent
NETCUP_ROOT = WORKTREE_ROOT / "scripts" / "netcup"
ARTIFACTS = PATCH_ROOT / "artifacts"
RESULTS_DIR = TRUENAS_ROOT / "docs" / "acceptance-results"
RESULTS_INDEX = TRUENAS_ROOT / "docs" / "ACCEPTANCE-RESULTS.md"
LOCK_PATH = Path("/home/vscode/vbpub-release-program-20261027/v1001.lock")
SERVER_ID = 804027
HOST = "v1001.vxxu.de"
PORT = 22
VERSION_RE = re.compile(r"\d+\.\d+\.\d+-(?:RC\.\d+|MASTER\+\d{8}-\d{6})\Z")
ISO_VERSION_RE = re.compile(
    r"(?P<version>\d+\.\d+\.\d+-(?:RC\.\d+|MASTER\+\d{8}-\d{6}))(?:-[A-Za-z0-9._-]+)?\.iso\Z"
)
START_STATES = ("vanilla", "path-a", "path-b", "path-a+b")
PATCH_TARGETS = ("none", "reapply")
STEPS = ("plant", "update", "verify", "ui", "api")
STORAGE_PROFILE = {
    "vanilla": "path-a",
    "path-a": "path-a",
    "path-b": "path-b",
    "path-a+b": "both",
}
SNAPSHOT_CATALOG = {
    "tn-v27rc1-stock-ssh-ready-20261027": {
        "version": "27.0.0-RC.1", "start_state": "vanilla",
        "description": "stock v27 RC.1, SSH ready",
    },
    "tn-v27rc1-path-a-patched-clean-20261030": {
        "version": "27.0.0-RC.1", "start_state": "path-a",
        "description": "v27 RC.1 patched with an empty Path A root; verified clean of survival fixtures",
    },
}


class AcceptError(RuntimeError):
    """The requested acceptance scenario could not be run safely."""


class LaneFailure(AcceptError):
    """A registered lane failed and its exit evidence must be retained."""

    def __init__(self, message: str, stage_result: dict[str, Any]) -> None:
        super().__init__(message)
        self.stage_result = stage_result


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptError(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AcceptError(f"expected a JSON object in {path}")
    return value


def _registry() -> tuple[dict[str, dict[str, Any]], list[str]]:
    builds = load_supported_builds()
    raw = _json_file(PATCH_ROOT / "supported-builds.json")
    acceptance_targets = raw.get("acceptance_targets")
    if (not isinstance(acceptance_targets, list) or not acceptance_targets
            or not all(isinstance(item, str) and item in builds for item in acceptance_targets)
            or len(set(acceptance_targets)) != len(acceptance_targets)):
        raise AcceptError("supported-builds.json acceptance_targets must list unique registered builds")
    return builds, acceptance_targets


def _parse_steps(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values or len(set(values)) != len(values):
        raise AcceptError("--steps must be a nonempty comma-separated list with no duplicates")
    invalid = sorted(set(values) - set(STEPS))
    if invalid:
        raise AcceptError(f"unsupported acceptance steps: {', '.join(invalid)}")
    return values


def _parse_source(raw: str) -> tuple[str, str | None]:
    if raw in SNAPSHOT_CATALOG:
        row = SNAPSHOT_CATALOG[raw]
        return str(row["version"]), raw
    if VERSION_RE.fullmatch(raw):
        return raw, None
    path = Path(raw)
    if path.suffix.lower() == ".iso":
        match = ISO_VERSION_RE.fullmatch(path.name)
        if match is None:
            raise AcceptError("--from ISO filename must contain its exact TrueNAS build version")
        return match.group("version"), None
    raise AcceptError("--from must be an exact TrueNAS build, a versioned ISO path, or a registered snapshot name")


def _credential_environment(*, with_ui: bool) -> dict[str, str]:
    key = ARTIFACTS / ".live-test-root-key"
    known_hosts = ARTIFACTS / "v1001.known_hosts"
    public_key = ARTIFACTS / ".live-test-root-key.pub"
    required_paths = {"TRUENAS_TEST_KEY": key, "TRUENAS_TEST_KNOWN_HOSTS": known_hosts}
    if not key.is_file() or not known_hosts.is_file():
        raise AcceptError("the registered external-host SSH key and known-hosts files are required in artifacts/")
    if not public_key.is_file():
        raise AcceptError("the registered TrueNAS survival public key is missing from artifacts/")
    env = {
        "TRUENAS_TEST_HOST": HOST,
        "TRUENAS_TEST_PORT": str(PORT),
        "TRUENAS_TEST_KEY": str(key),
        "TRUENAS_TEST_KNOWN_HOSTS": str(known_hosts),
        "TRUENAS_TEST_ALLOW_MUTATIONS": "1",
    }
    for name, value in required_paths.items():
        override = os.environ.get(name)
        if override and Path(override).resolve() != value.resolve():
            raise AcceptError(f"{name} must identify the registered disposable-host credential file")
    if with_ui:
        secret_file = ARTIFACTS / "v1001-ui-secrets.toml"
        if not secret_file.is_file():
            raise AcceptError("the local TrueNAS UI secret file is missing from artifacts/")
        env.update({
            "TRUENAS_UI_URL": f"https://{HOST}",
            "TRUENAS_UI_SECRET_FILE": str(secret_file),
            "PWMCP_WS_URL": os.environ.get("PWMCP_WS_URL", "ws://pwmcp:3000/"),
            "PWMCP_PLAYWRIGHT_VERSION": _playwright_version(),
        })
    return env


def _playwright_version() -> str:
    path = WORKTREE_ROOT / "pwmcp" / "pwmcp.contract.json"
    contract = _json_file(path)
    playwright = contract.get("playwright")
    if isinstance(playwright, dict):
        value = playwright.get("python")
        if isinstance(value, str) and value:
            requirements = ACCEPTANCE_ROOT / "requirements-live-ui.txt"
            pins = re.findall(
                r"(?m)^playwright==([0-9]+\.[0-9]+\.[0-9]+)\s*$",
                requirements.read_text(encoding="utf-8"),
            )
            if pins != [value]:
                raise AcceptError(
                    f"live UI Playwright pin {pins!r} does not match PWMCP contract {value!r}"
                )
            return value
    browser = contract.get("browser")
    if isinstance(browser, dict):
        value = browser.get("playwright_version") or browser.get("playwrightVersion")
        if isinstance(value, str) and value:
            return value
    value = contract.get("playwright_version") or contract.get("playwrightVersion")
    if isinstance(value, str) and value:
        return value
    raise AcceptError(f"PWMCP contract has no Playwright version: {path}")


def _pwmcp_network() -> str:
    configured = os.environ.get("TRUENAS_TEST_DOCKER_NETWORK", "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["docker", "ps", "-q"], text=True, capture_output=True, check=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AcceptError(f"cannot inspect running containers to locate PWMCP: {exc}") from exc
    matches: set[str] = set()
    for container_id in result.stdout.splitlines():
        if not container_id:
            continue
        inspected = subprocess.run(
            ["docker", "inspect", container_id], text=True, capture_output=True,
            check=False, timeout=20,
        )
        if inspected.returncode:
            continue
        try:
            rows = json.loads(inspected.stdout)
        except json.JSONDecodeError:
            continue
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            continue
        item = rows[0]
        name = str(item.get("Name", "")).lstrip("/")
        networks = item.get("NetworkSettings", {}).get("Networks", {})
        if not isinstance(networks, dict):
            continue
        for network, details in networks.items():
            aliases = details.get("Aliases") if isinstance(details, dict) else None
            if not isinstance(aliases, list):
                aliases = []
            if name == "pwmcp" or "pwmcp" in aliases:
                if network not in {"bridge", "host", "none"}:
                    matches.add(network)
    if len(matches) != 1:
        raise AcceptError(
            "could not derive one custom Docker bridge containing the PWMCP service; "
            "set TRUENAS_TEST_DOCKER_NETWORK to that verified bridge"
        )
    return next(iter(matches))


@contextmanager
def _host_lock() -> Iterator[None]:
    try:
        handle = LOCK_PATH.open("a+")
    except OSError as exc:
        raise AcceptError(f"cannot open the disposable-host lock {LOCK_PATH}: {exc}") from exc
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _stream_command(
    command: list[str], *, cwd: Path, env: dict[str, str], log_path: Path,
    timeout: int = 7200,
) -> dict[str, Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[command] {' '.join(shlex.quote(part) for part in command)}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
        except OSError as exc:
            raise AcceptError(f"cannot start {command[0]}: {exc}") from exc
        deadline = time.monotonic() + timeout
        assert process.stdout is not None
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
                if time.monotonic() >= deadline:
                    process.kill()
                    raise AcceptError(f"command exceeded its {timeout}s timeout: {command[0]}")
            status = process.wait(timeout=max(1, int(deadline - time.monotonic())))
        except (OSError, subprocess.TimeoutExpired) as exc:
            process.kill()
            process.wait()
            raise AcceptError(f"command failed to finish: {command[0]}: {exc}") from exc
    completed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"argv": command, "started_at": started, "completed_at": completed,
            "exit_code": status, "log": str(log_path.relative_to(WORKTREE_ROOT))}


def _provider_command(args: list[str], *, env: dict[str, str], log_path: Path) -> dict[str, Any]:
    provider_env = _provider_environment(env)
    return _stream_command(
        [sys.executable, "scp-api.py", *args], cwd=NETCUP_ROOT,
        env=provider_env, log_path=log_path,
    )


def _provider_environment(env: dict[str, str]) -> dict[str, str]:
    provider_env = dict(env)
    provider_env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(WORKTREE_ROOT / "libraries" / "cli-extended" / "src"),
        provider_env.get("PYTHONPATH", ""),
    )))
    return provider_env


def _json_log(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptError(f"provider CLI did not return valid JSON in {path}: {exc}") from exc
    first_json_line = next((index for index, line in enumerate(lines) if line.lstrip().startswith("{")), None)
    if first_json_line is None:
        raise AcceptError(f"provider CLI returned no JSON object in {path}")
    try:
        value = json.loads("\n".join(lines[first_json_line:]))
    except json.JSONDecodeError as exc:
        raise AcceptError(f"provider CLI did not return valid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AcceptError(f"provider CLI returned a non-object JSON value in {path}")
    return value


def _restore_snapshot(name: str, *, env: dict[str, str], run_dir: Path) -> list[dict[str, Any]]:
    if name not in SNAPSHOT_CATALOG:
        raise AcceptError(
            f"snapshot {name!r} is not in the acceptance snapshot catalog; "
            "use --from <version> --restore-snapshot NAME only after verifying that snapshot's state"
        )
    dry_log = run_dir / "provider-revert-dry-run.json"
    provider_env = _provider_environment(env)
    with _host_lock():
        dry_result = _provider_command(
            ["snapshots", str(SERVER_ID), "revert", name, "--dry-run", "--json"],
            env=env, log_path=dry_log,
        )
        if dry_result["exit_code"]:
            raise AcceptError(f"provider refused the snapshot restore dry-run; see {dry_log}")
        plan = _json_log(dry_log)
        selected = plan.get("wouldRevert")
        if not isinstance(selected, dict) or selected.get("name") != name:
            raise AcceptError(f"provider dry-run did not select the requested snapshot {name!r}: {plan!r}")

        revert_log = run_dir / "provider-revert.json"
        reverted = _provider_command(
            ["snapshots", str(SERVER_ID), "revert", name, "--yes", "--json"],
            env=env, log_path=revert_log,
        )
        if reverted["exit_code"]:
            raise AcceptError(f"provider snapshot restore failed; see {revert_log}")
        task = _json_log(revert_log).get("uuid")
        if not isinstance(task, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}", task
        ):
            raise AcceptError("provider snapshot restore response has no task UUID")
        monitor_log = run_dir / "provider-revert-task.log"
        monitor = _stream_command(
            [sys.executable, "monitor-task.py", "watch", task], cwd=NETCUP_ROOT,
            env=provider_env,
            log_path=monitor_log, timeout=7200,
        )
        if monitor["exit_code"]:
            raise AcceptError(f"provider snapshot restore task did not finish successfully; see {monitor_log}")

        details_log = run_dir / "provider-server-details-before-power.json"
        details = _provider_command(
            ["server-details", str(SERVER_ID), "--json"], env=env, log_path=details_log,
        )
        if details["exit_code"]:
            raise AcceptError(f"cannot inspect server state after snapshot restore; see {details_log}")
        state = _json_log(details_log).get("serverLiveInfo", {}).get("state")
        if state == "SHUTOFF":
            power_log = run_dir / "provider-power-on.json"
            powered = _provider_command(
                ["power", "on", str(SERVER_ID), "--yes", "--json"], env=env,
                log_path=power_log,
            )
            if powered["exit_code"]:
                raise AcceptError(f"provider power-on failed after snapshot restore; see {power_log}")
        elif state != "RUNNING":
            raise AcceptError(f"unexpected provider server state after snapshot restore: {state!r}")
        _wait_for_ssh(env)
    return [dry_result, reverted, monitor]


def _wait_for_ssh(env: dict[str, str], timeout: int = 900) -> None:
    deadline = time.monotonic() + timeout
    key = env["TRUENAS_TEST_KEY"]
    known_hosts = env["TRUENAS_TEST_KNOWN_HOSTS"]
    last_error = "no connection attempted"
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["ssh", "-p", str(PORT), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
             "-o", "ConnectTimeout=8", "-i", key, f"root@{HOST}", "true"],
            text=True, capture_output=True, check=False, timeout=15,
        )
        if result.returncode == 0:
            return
        last_error = (result.stderr or result.stdout).strip()[-240:] or f"ssh exit {result.returncode}"
        print(f"[restore] SSH not ready yet: {last_error}", flush=True)
        time.sleep(10)
    raise AcceptError(f"restored TrueNAS host did not become SSH-ready within {timeout}s: {last_error}")


def _inspect_state(env: dict[str, str], expected_version: str, start_state: str) -> dict[str, Any]:
    target = Target(
        host=env["TRUENAS_TEST_HOST"], port=int(env["TRUENAS_TEST_PORT"]),
        key=Path(env["TRUENAS_TEST_KEY"]),
        known_hosts=Path(env["TRUENAS_TEST_KNOWN_HOSTS"]),
        user=env.get("TRUENAS_TEST_USER", "root"),
    )
    info = target.call("system.info", timeout=120)
    actual_version = str(info.get("version", ""))
    if actual_version != expected_version:
        raise AcceptError(f"start source expected TrueNAS {expected_version}, found {actual_version!r}")
    a_state = target.shell(
        "if zfs list -H -o name boot-pool/user-data >/dev/null 2>&1; then printf present; "
        "else printf absent; fi", timeout=45,
    )
    b_state = target.shell(
        "if zpool list -H -o name 2>/dev/null | grep -Fxq boot-disk-data; "
        "then printf present; else printf absent; fi", timeout=45,
    )
    record_text = target.shell(
        "if test -f /root/v27-boot-pool-patch-install.json; then "
        "cat /root/v27-boot-pool-patch-install.json; else printf ABSENT; fi", timeout=45,
    )
    record = None
    if record_text != "ABSENT":
        try:
            record = json.loads(record_text)
        except json.JSONDecodeError as exc:
            raise AcceptError("TrueNAS has a malformed v27 patch install record") from exc
        if not isinstance(record, dict):
            raise AcceptError("TrueNAS v27 patch install record is not an object")
    expected = {
        "vanilla": ("absent", "absent"),
        "path-a": ("present", "absent"),
        "path-b": ("absent", "present"),
        "path-a+b": ("present", "present"),
    }[start_state]
    if (a_state, b_state) != expected:
        raise AcceptError(
            f"selected start state {start_state} does not match live pools "
            f"(Path A={a_state}, Path B={b_state})"
        )
    if start_state == "vanilla":
        if record is not None:
            raise AcceptError("vanilla start state has a v27 patch install record")
    else:
        if (record is None or record.get("patch") != "v27" or record.get("version") != expected_version
                or record.get("supported") is not True):
            raise AcceptError(f"patched start state has no matching v27 install record: {record!r}")
    return {
        "version": actual_version, "start_state": start_state,
        "path_a": a_state, "path_b": b_state,
        "v27_patch_record": record,
    }


def _inspect_post_state(
    env: dict[str, str], expected_version: str, start_state: str,
) -> dict[str, Any]:
    target = Target(
        host=env["TRUENAS_TEST_HOST"], port=int(env["TRUENAS_TEST_PORT"]),
        key=Path(env["TRUENAS_TEST_KEY"]),
        known_hosts=Path(env["TRUENAS_TEST_KNOWN_HOSTS"]),
        user=env.get("TRUENAS_TEST_USER", "root"),
    )
    info = target.call("system.info", timeout=120)
    actual_version = str(info.get("version", ""))
    if actual_version != expected_version:
        raise AcceptError(f"post-update verification expected TrueNAS {expected_version}, found {actual_version!r}")
    a_state = target.shell(
        "if zfs list -H -o name boot-pool/user-data >/dev/null 2>&1; then printf present; "
        "else printf absent; fi", timeout=45,
    )
    b_state = target.shell(
        "if zpool list -H -o name 2>/dev/null | grep -Fxq boot-disk-data; "
        "then printf present; else printf absent; fi", timeout=45,
    )
    expected = {"vanilla": ("present", "absent"), "path-a": ("present", "absent"),
                "path-b": ("absent", "present"), "path-a+b": ("present", "present")}[start_state]
    if (a_state, b_state) != expected:
        raise AcceptError(
            f"post-update state for {start_state} has unexpected pools "
            f"(Path A={a_state}, Path B={b_state})"
        )
    return {"version": actual_version, "post_update_state": start_state,
            "path_a": a_state, "path_b": b_state}


def _target_update_metadata(version: str, builds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    build = builds.get(version)
    if build is None:
        match = re.fullmatch(r"(\d+)\.0\.0-(RC\.\d+|MASTER\+\d{8}-\d{6})", version)
        if match is None:
            raise AcceptError(f"cannot infer an updater train for unregistered candidate {version!r}")
        kind = "RC" if match.group(2).startswith("RC.") else "Nightlies"
        train = f"TrueNAS-{match.group(1)}-{kind}"
    else:
        train = str(build["train"])
    match = re.fullmatch(r"TrueNAS-(\d+)-(Nightlies|RC)", train)
    if match is None:
        raise AcceptError(f"registered build has an unsupported updater train: {train!r}")
    current = official_latest_update(f"{match.group(1)}-{match.group(2)}")
    if current["version"] != version:
        raise AcceptError(
            f"registered target {version} is no longer the latest {train} build; "
            f"feed advertises {current['version']}. Retarget, re-register, and re-run acceptance."
        )
    return current


def _lane_command(
    lane: str, *, env: dict[str, str], run_dir: Path, network: str,
    allow_high_psi: bool, log_suffix: str | None = None,
) -> dict[str, Any]:
    command = [sys.executable, str(TRUENAS_ROOT / "boot-pool-patch" / "run-live-gate.py"),
               lane, "--docker-network", network, "--allow-dirty"]
    if allow_high_psi:
        command.append("--allow-high-psi")
    suffix = f"-{log_suffix}" if log_suffix else ""
    log_path = run_dir / f"{lane}{suffix}.log"
    with _host_lock():
        result = _stream_command(command, cwd=WORKTREE_ROOT, env=env, log_path=log_path)
    if result["exit_code"]:
        raise LaneFailure(f"registered live lane {lane} failed; see {log_path}", result)
    return result


def _source_state_for_snapshot(name: str, start_state: str, source_version: str) -> None:
    row = SNAPSHOT_CATALOG.get(name)
    if row is None:
        return
    if row["version"] != source_version or row["start_state"] != start_state:
        raise AcceptError(
            f"snapshot {name!r} is {row['start_state']} on {row['version']}, "
            f"not {start_state} on {source_version}"
        )


def _scenario_profiles(start_state: str) -> tuple[str, ...]:
    if start_state == "path-a+b":
        return ("path-a", "path-b")
    if start_state == "vanilla":
        return ("path-a",)
    return (start_state,)


def _patch_after_update(
    target_version: str, *, start_state: str, patched_target: str,
    source_record: dict[str, Any] | None, manifest_path: Path,
    base_env: dict[str, str], run_dir: Path, network: str,
    allow_high_psi: bool,
) -> dict[str, Any] | None:
    if patched_target == "none":
        print("[patch] skipped by --patched-target none", flush=True)
        return None
    manifest = _json_file(manifest_path)
    survival_profile = STORAGE_PROFILE[start_state]
    if target_version.startswith("27."):
        source_digest = source_record.get("bundle_sha256") if source_record else None
        action = "reapply-after-update" if isinstance(source_digest, str) else "install-after-update"
        lane_env = dict(base_env)
        lane_env.update({
            "TRUENAS_STORAGE_PROFILE": survival_profile,
            "TRUENAS_EXPECTED_VERSION": target_version,
            "TRUENAS_V27_PATCH_ACTION": action,
            "TRUENAS_TEST_ALLOW_PATCH_REAPPLY": "1",
        })
        if source_digest:
            lane_env["TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256"] = source_digest
        else:
            lane_env.pop("TRUENAS_V27_EXPECTED_PRIOR_BUNDLE_SHA256", None)
        return _lane_command(
            "live-v27-patch", env=lane_env, run_dir=run_dir,
            network=network, allow_high_psi=allow_high_psi,
        )
    if target_version.startswith("28."):
        # v28 has a separate install record. Its installer validates either
        # persistent root directly, including an already-created Path B pool.
        patch_profile = "path-a" if start_state == "path-a" else "path-b"
        lane_env = dict(base_env)
        lane_env.update({
            "TRUENAS_STORAGE_PROFILE": patch_profile,
            "TRUENAS_EXPECTED_VERSION": target_version,
            "TRUENAS_V28_PATCH_ACTION": "install",
        })
        return _lane_command(
            "live-v28-patch", env=lane_env, run_dir=run_dir,
            network=network, allow_high_psi=allow_high_psi,
        )
    raise AcceptError(f"no post-update patch route exists for TrueNAS {target_version}")


def _result_index(results_dir: Path, target_versions: list[str]) -> str:
    records: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("schema_version") == 1:
            records.append(record)
    rows: list[str] = []
    survival_lanes = {
        "live-update-survival-plant": "plant",
        "live-update-survival-update": "update",
        "live-update-survival-verify": "verify",
    }
    patch_lanes = {"live-v27-patch", "live-v28-patch"}
    optional_lanes = {"live-storage", "live-ui"}

    def stage_lane(stage: dict[str, Any]) -> str | None:
        argv = stage.get("argv")
        if not isinstance(argv, list):
            return None
        known = set(survival_lanes) | patch_lanes | optional_lanes
        return next((item for item in argv if isinstance(item, str) and item in known), None)

    def optional_status(
        latest_lane: dict[str, dict[str, Any]], lane: str, expected_profiles: tuple[str, ...],
        requested: bool,
    ) -> str:
        if not requested:
            return "—"
        stages = [stage for key, stage in latest_lane.items()
                  if key.startswith(f"{lane}:")]
        if any(stage.get("exit_code") != 0 for stage in stages):
            return "FAIL"
        suffix_profiles = {
            Path(str(stage.get("log", ""))).stem.rsplit("-", 1)[-1]
            for stage in stages
        }
        if len(expected_profiles) == 1:
            passed = bool(stages)
        else:
            passed = set(expected_profiles) <= suffix_profiles
        return "PASS" if passed else "incomplete"

    for start_state in START_STATES:
        for target in target_versions:
            matching = [
                row for row in records
                if row.get("start_state") == start_state and row.get("to_version") == target
            ]
            source_builds = sorted({str(row.get("from_version", "unknown")) for row in matching}) or ["—"]
            for source_build in source_builds:
                source_matching = [row for row in matching
                                   if str(row.get("from_version", "unknown")) == source_build]
                groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for row in source_matching:
                    key = (
                        str(row.get("survival_state_directory", row.get("run_id", ""))),
                        str(row.get("patched_target", "unknown")),
                    )
                    groups.setdefault(key, []).append(row)
                outcomes: list[tuple[str, str, dict[str, str], list[str]]] = []
                for (_state_directory, patch_target), group in groups.items():
                    latest_lane: dict[str, dict[str, Any]] = {}
                    for record in group:
                        for stage in record.get("stage_results", []):
                            if not isinstance(stage, dict):
                                continue
                            lane = stage_lane(stage)
                            if lane is None:
                                continue
                            key = lane
                            if lane in optional_lanes:
                                log_name = Path(str(stage.get("log", ""))).stem
                                prefix = f"{lane}-"
                                profile = log_name[len(prefix):] if log_name.startswith(prefix) else "unknown"
                                key = f"{lane}:{profile}"
                            prior = latest_lane.get(key)
                            if prior is None or str(stage.get("completed_at", "")) > str(prior.get("completed_at", "")):
                                latest_lane[key] = stage
                    latest_record = max(group, key=lambda item: item.get("started_at_utc", ""))
                    complete = all(lane in latest_lane for lane in survival_lanes)
                    failed = any(lane in latest_lane and latest_lane[lane].get("exit_code") != 0
                                 for lane in survival_lanes)
                    survival = "FAIL" if failed else "PASS" if complete and all(
                        latest_lane[lane].get("exit_code") == 0 for lane in survival_lanes
                    ) else "incomplete"
                    if patch_target == "none":
                        patch = "—"
                    elif any(lane in latest_lane and latest_lane[lane].get("exit_code") == 0
                             for lane in patch_lanes):
                        patch = "PASS"
                    elif any(lane in latest_lane and latest_lane[lane].get("exit_code") != 0
                             for lane in patch_lanes):
                        patch = "FAIL"
                    else:
                        patch = "incomplete"
                    requested = {step for record in group for step in record.get("steps", [])}
                    profiles = _scenario_profiles(start_state)
                    statuses = {
                        "survival": survival,
                        "patch": patch,
                        "api": optional_status(latest_lane, "live-storage", profiles, "api" in requested),
                        "ui": optional_status(latest_lane, "live-ui", profiles, "ui" in requested),
                    }
                    evidence = sorted({str(item.get("result_file", "result file")) for item in group})
                    outcomes.append((latest_record.get("started_at_utc", ""), patch_target,
                                     statuses, evidence))
                selected = max(outcomes, key=lambda item: item[0], default=None)
                if selected is None:
                    patch, statuses, evidence = "—", {
                        "survival": "not run", "patch": "not run", "api": "not run", "ui": "not run",
                    }, "—"
                else:
                    _started, patch, statuses, result_files = selected
                    evidence = "; ".join(f"`{path}`" for path in result_files)
                rows.append(
                    f"| {start_state} | {source_build} | {target} | {patch} | "
                    f"{statuses['survival']} | {statuses['patch']} | {statuses['api']} | "
                    f"{statuses['ui']} | {evidence} |"
                )
    return "\n".join([
        "# TrueNAS update acceptance results",
        "",
        "Generated by `python3 boot-pool-patch/acceptance/accept.py matrix`. One row is shown per source state, source build, and target; the most recently started attempt for that combination is selected. Survival is PASS only when plant, update, and verify all pass; patch is PASS only when the requested adapter lane passes; API/UI are separate and PASS only when every expected profile lane passes. Continuations share a private state directory and are combined. A later failed attempt is not hidden by an older pass or another source build.",
        "",
        "| Source state | From build | Target build | Target patch | Survival | Patch | API | UI | Evidence |",
        "|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Snapshot inventory",
        "",
        "| Snapshot | Meaning |",
        "|---|---|",
        "| `tn-v27rc1-stock-ssh-ready-20261027` | Stock v27 RC.1 with SSH configured; original provider snapshot. |",
        "| `tn-v27rc1-path-a-patched-20261030` | Older Path A snapshot; found to contain stale survival fixtures, so it is excluded from acceptance starts. |",
        "| `tn-v27rc1-path-a-patched-clean-20261030` | v27 RC.1 patched with an empty Path A root; clean acceptance start state. |",
        "",
    ])


def _matrix() -> int:
    _, target_versions = _registry()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    content = _result_index(RESULTS_DIR, target_versions)
    RESULTS_INDEX.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0


def _run(args: argparse.Namespace) -> int:
    source_version, source_snapshot = _parse_source(args.from_source)
    _source_state_for_snapshot(source_snapshot or "", args.start_state, source_version)
    restore_snapshot = args.restore_snapshot or source_snapshot
    if source_snapshot and args.restore_snapshot and source_snapshot != args.restore_snapshot:
        raise AcceptError("--from snapshot and --restore-snapshot must name the same snapshot")
    steps = _parse_steps(args.steps)
    if ("ui" in steps or "api" in steps) and args.patched_target != "reapply":
        raise AcceptError("post-update UI/API steps require --patched-target reapply")
    if "update" in steps and "plant" not in steps and not args.manifest:
        raise AcceptError("--steps update without plant requires --manifest from an earlier plant run")
    builds, target_versions = _registry()
    if args.to_version not in target_versions and args.patched_target != "none":
        raise AcceptError(
            f"unregistered candidate {args.to_version!r} may only be used for stock-update diagnostics; "
            "register its exact adapter before requesting reapply/UI/API acceptance"
        )
    if args.to_version not in target_versions and ("ui" in steps or "api" in steps):
        raise AcceptError(
            f"target {args.to_version!r} is not an acceptance target; registered targets: "
            + ", ".join(target_versions)
        )
    update_metadata = _target_update_metadata(args.to_version, builds)
    if not source_version.startswith("27."):
        raise AcceptError("update acceptance currently requires a v27 source build")
    if source_version == args.to_version:
        raise AcceptError("source and target builds must differ")

    with_ui = "ui" in steps
    base_env = dict(os.environ)
    base_env.update(_credential_environment(with_ui=with_ui))
    if not base_env.get("CGROUP_PARENT_DEV_GATES"):
        raise AcceptError("CGROUP_PARENT_DEV_GATES is unset; refusing an unplaced live gate")
    network = _pwmcp_network()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = ARTIFACTS / "acceptance-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        run_dir.chmod(0o700)
    except OSError:
        pass
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.manifest:
        manifest_path = Path(args.manifest).resolve(strict=False)
        state_dir = manifest_path.parent
        try:
            state_dir.relative_to(WORKTREE_ROOT)
        except ValueError as exc:
            raise AcceptError("--manifest must be inside this worktree") from exc
        if not state_dir.name.startswith(".update-survival"):
            raise AcceptError("--manifest must be inside a dedicated .update-survival directory")
        if manifest_path.name != "manifest.json":
            raise AcceptError("--manifest must name the dedicated directory's manifest.json")
        if state_dir.is_symlink() or (state_dir.exists() and not state_dir.is_dir()):
            raise AcceptError("--manifest parent must be a real .update-survival directory")
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_dir.chmod(0o700)
        continuation_from = None
        if not ({"plant", "update"} & set(steps)):
            if not manifest_path.is_file():
                raise AcceptError("a manifest continuation requires an existing planted manifest")
            planted = _json_file(manifest_path)
            expected_mode = "vanilla" if args.start_state == "vanilla" else "patched"
            if (planted.get("before_version") != source_version
                    or planted.get("mode") != expected_mode
                    or planted.get("profile") != STORAGE_PROFILE[args.start_state]
                    or planted.get("patched_target", "none") != args.patched_target):
                raise AcceptError("--manifest does not match the requested source state and patch target")
            state_relative = str(state_dir.relative_to(WORKTREE_ROOT))
            previous_runs = []
            for previous_path in RESULTS_DIR.glob("*.json"):
                try:
                    previous = _json_file(previous_path)
                except AcceptError:
                    continue
                if (previous.get("survival_state_directory") == state_relative
                        and previous.get("from_version") == source_version
                        and previous.get("to_version") == args.to_version
                        and previous.get("start_state") == args.start_state
                        and previous.get("patched_target") == args.patched_target
                        and {"plant", "update"} <= set(previous.get("steps", []))):
                    previous_runs.append(previous)
            if not previous_runs:
                raise AcceptError("manifest continuation has no recorded plant/update run for this scenario")
            previous = max(previous_runs, key=lambda item: item.get("started_at_utc", ""))
            required_successes = {
                "live-update-survival-plant", "live-update-survival-update",
            }
            successful_lanes = {
                lane for stage in previous.get("stage_results", [])
                if isinstance(stage, dict) and stage.get("exit_code") == 0
                and isinstance(stage.get("argv"), list)
                for lane in stage["argv"] if lane in required_successes
            }
            if successful_lanes != required_successes:
                raise AcceptError("manifest continuation requires successful plant and update lanes")
            if "verify" not in steps:
                verification_passed = any(
                    isinstance(stage, dict) and stage.get("exit_code") == 0
                    and "live-update-survival-verify" in stage.get("argv", [])
                    for item in previous_runs for stage in item.get("stage_results", [])
                )
                if not verification_passed:
                    raise AcceptError("UI/API-only continuation requires a previously passing survival verify lane")
            continuation_from = previous.get("result_file")
        else:
            continuation_from = None
    else:
        continuation_from = None
        state_dir = ARTIFACTS / f".update-survival-{run_id}"
        state_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        state_dir.chmod(0o700)
        manifest_path = state_dir / "manifest.json"
    report_path = state_dir / "report.json"
    update_record_path = state_dir / "update-job.json"
    stage_results: list[dict[str, Any]] = []
    source_record: dict[str, Any] | None = None
    result_path = RESULTS_DIR / f"{run_id}-{args.start_state}-to-{args.to_version}.json"
    result: dict[str, Any] = {
        "schema_version": 1, "run_id": run_id,
        "from_source": Path(args.from_source).name if Path(args.from_source).suffix.lower() == ".iso" else args.from_source,
        "from_version": source_version, "start_state": args.start_state,
        "to_version": args.to_version, "patched_target": args.patched_target,
        "steps": steps, "restore_snapshot": restore_snapshot,
        "official_update": {key: update_metadata.get(key) for key in
                            ("filename", "version", "checksum", "filesize", "profile", "train")},
        "started_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "survival_state_directory": str(state_dir.relative_to(WORKTREE_ROOT)),
        "result": "FAIL", "stage_results": [],
    }
    if continuation_from:
        result["continued_from"] = continuation_from
    try:
        if restore_snapshot:
            result["stage_results"].extend(_restore_snapshot(
                restore_snapshot, env=base_env, run_dir=run_dir,
            ))
        profile = STORAGE_PROFILE[args.start_state]
        start_env = dict(base_env)
        start_env.update({"TRUENAS_STORAGE_PROFILE": profile,
                          "TRUENAS_EXPECTED_VERSION": source_version})
        with _host_lock():
            if "plant" in steps or "update" in steps:
                state = _inspect_state(start_env, source_version, args.start_state)
            else:
                post_env = dict(base_env)
                post_env.update({"TRUENAS_EXPECTED_VERSION": args.to_version,
                                 "TRUENAS_STORAGE_PROFILE": profile})
                state = _inspect_post_state(post_env, args.to_version, args.start_state)
        result["start_inventory"] = state
        source_record = state.get("v27_patch_record")
        inspection_env = start_env
        if not ({"plant", "update"} & set(steps)):
            inspection_env = dict(base_env)
            inspection_env.update({"TRUENAS_STORAGE_PROFILE": profile,
                                   "TRUENAS_EXPECTED_VERSION": args.to_version})
        lane_result = _lane_command(
            "live-inspect", env=inspection_env, run_dir=run_dir,
            network=network, allow_high_psi=args.allow_high_psi,
        )
        stage_results.append(lane_result)

        if "plant" in steps:
            plant_env = dict(start_env)
            plant_env.update({
                "TRUENAS_SURVIVAL_MODE": "vanilla" if args.start_state == "vanilla" else "patched",
                "TRUENAS_SURVIVAL_PROFILE": profile,
                "TRUENAS_SURVIVAL_PATCHED_TARGET": args.patched_target,
                "TRUENAS_SURVIVAL_EXPECTED_AFTER": args.to_version,
                "TRUENAS_SURVIVAL_ARTIFACT_DIR": str(state_dir),
                "TRUENAS_SURVIVAL_MANIFEST": str(manifest_path),
                "TRUENAS_SURVIVAL_REPORT": str(report_path),
                "TRUENAS_SURVIVAL_UPDATE_RECORD": str(update_record_path),
                "TRUENAS_SURVIVAL_SSH_PUBLIC_KEY_FILE": str(ARTIFACTS / ".live-test-root-key.pub"),
            })
            lane_result = _lane_command(
                "live-update-survival-plant", env=plant_env, run_dir=run_dir,
                network=network, allow_high_psi=args.allow_high_psi,
            )
            stage_results.append(lane_result)

        if "update" in steps:
            if not manifest_path.is_file():
                raise AcceptError(f"update step requires a planted survival manifest: {manifest_path}")
            update_env = dict(start_env)
            update_env.update({
                "TRUENAS_SURVIVAL_MODE": "vanilla" if args.start_state == "vanilla" else "patched",
                "TRUENAS_SURVIVAL_PROFILE": profile,
                "TRUENAS_SURVIVAL_PATCHED_TARGET": args.patched_target,
                "TRUENAS_SURVIVAL_EXPECTED_AFTER": args.to_version,
                "TRUENAS_SURVIVAL_ARTIFACT_DIR": str(state_dir),
                "TRUENAS_SURVIVAL_MANIFEST": str(manifest_path),
                "TRUENAS_SURVIVAL_REPORT": str(report_path),
                "TRUENAS_SURVIVAL_UPDATE_RECORD": str(update_record_path),
                "TRUENAS_TEST_ALLOW_UPDATE": "1",
                "TRUENAS_TEST_ALLOW_REBOOT": "1",
                "TRUENAS_UPDATE_EXPECTED_VERSION": args.to_version,
                "TRUENAS_UPDATE_TRAIN": str(update_metadata["train"]),
            })
            lane_result = _lane_command(
                "live-update-survival-update", env=update_env, run_dir=run_dir,
                network=network, allow_high_psi=args.allow_high_psi,
            )
            stage_results.append(lane_result)
            if args.patched_target == "reapply":
                lane_result = _patch_after_update(
                    args.to_version, start_state=args.start_state,
                    patched_target=args.patched_target, source_record=source_record,
                    manifest_path=manifest_path, base_env=base_env, run_dir=run_dir,
                    network=network, allow_high_psi=args.allow_high_psi,
                )
                if lane_result is not None:
                    stage_results.append(lane_result)

        if "verify" in steps:
            if not manifest_path.is_file():
                raise AcceptError(f"verify step requires a survival manifest: {manifest_path}")
            verify_env = dict(base_env)
            verify_env.update({
                "TRUENAS_STORAGE_PROFILE": profile,
                "TRUENAS_EXPECTED_VERSION": args.to_version,
                "TRUENAS_SURVIVAL_MODE": "vanilla" if args.start_state == "vanilla" else "patched",
                "TRUENAS_SURVIVAL_PROFILE": profile,
                "TRUENAS_SURVIVAL_PATCHED_TARGET": args.patched_target,
                "TRUENAS_SURVIVAL_EXPECTED_AFTER": args.to_version,
                "TRUENAS_SURVIVAL_ARTIFACT_DIR": str(state_dir),
                "TRUENAS_SURVIVAL_MANIFEST": str(manifest_path),
                "TRUENAS_SURVIVAL_REPORT": str(report_path),
                "TRUENAS_SURVIVAL_UPDATE_RECORD": str(update_record_path),
            })
            lane_result = _lane_command(
                "live-update-survival-verify", env=verify_env, run_dir=run_dir,
                network=network, allow_high_psi=args.allow_high_psi,
            )
            stage_results.append(lane_result)

        if "ui" in steps:
            for ui_profile in _scenario_profiles(args.start_state):
                ui_env = dict(base_env)
                ui_env.update({"TRUENAS_STORAGE_PROFILE": ui_profile,
                               "TRUENAS_EXPECTED_VERSION": args.to_version})
                lane_result = _lane_command(
                    "live-ui", env=ui_env, run_dir=run_dir,
                    network=network, allow_high_psi=args.allow_high_psi,
                    log_suffix=ui_profile,
                )
                stage_results.append(lane_result)

        if "api" in steps:
            for api_profile in _scenario_profiles(args.start_state):
                api_env = dict(base_env)
                api_env.update({"TRUENAS_STORAGE_PROFILE": api_profile,
                                "TRUENAS_EXPECTED_VERSION": args.to_version})
                lane_result = _lane_command(
                    "live-storage", env=api_env, run_dir=run_dir,
                    network=network, allow_high_psi=args.allow_high_psi,
                    log_suffix=api_profile,
                )
                stage_results.append(lane_result)

        result["result"] = "PASS"
    except LaneFailure as exc:
        stage_results.append(exc.stage_result)
        result["error"] = str(exc)
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
    except (AcceptError, RemoteError, DriftError, OSError, ValueError, KeyError) as exc:
        result["error"] = str(exc)
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
    finally:
        result["stage_results"] = stage_results
        result["completed_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result["run_directory"] = str(run_dir.relative_to(WORKTREE_ROOT))
        result["result_file"] = str(result_path.relative_to(WORKTREE_ROOT))
        result_path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _, target_versions = _registry()
        RESULTS_INDEX.write_text(_result_index(RESULTS_DIR, target_versions), encoding="utf-8")
        print(f"[result] {result['result']} {result_path.relative_to(WORKTREE_ROOT)}", flush=True)
        print(f"[matrix] {RESULTS_INDEX.relative_to(WORKTREE_ROOT)}", flush=True)
    return 0 if result["result"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("matrix",))
    parser.add_argument("--from", dest="from_source")
    parser.add_argument("--start-state", choices=START_STATES)
    parser.add_argument("--to", dest="to_version")
    parser.add_argument("--patched-target", choices=PATCH_TARGETS, default="none")
    parser.add_argument("--steps", default="plant,update,verify")
    parser.add_argument("--restore-snapshot")
    parser.add_argument("--manifest", type=Path,
                        help="reuse a prior survival manifest when update or verify is run without plant")
    parser.add_argument("--allow-high-psi", action="store_true",
                        help="pass Run-Gate's one-run PSI override to each live lane")
    args = parser.parse_args(argv)
    if args.command == "matrix":
        return _matrix()
    if not args.from_source or not args.start_state or not args.to_version:
        parser.error("a scenario needs --from, --start-state, and --to")
    return _run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptError, RemoteError, DriftError, OSError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
