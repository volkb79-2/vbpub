#!/usr/bin/env python3
"""Validate, identify, log and submit the reviewed Debian-v2 reinstall."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


NETCUP_DIR = Path(__file__).resolve().parent
REPO_ROOT = NETCUP_DIR.parents[1]
RELEASE_ROOT = Path("/home/vscode/vbpub-release-program-20261027")
ENV_HELPER = RELEASE_ROOT / "dstdns-r2" / "env.sh"


@dataclass(frozen=True)
class Host:
    server_id: int
    server_name: str
    hostname: str
    short_name: str
    ipv4: str
    image_id: int
    ssh_key: Path


HOSTS = {
    "r1002": Host(
        server_id=799611,
        server_name="v2202511209318402047",
        hostname="r1002.vxxu.de",
        short_name="r1002",
        ipv4="152.53.187.201",
        image_id=128,
        ssh_key=Path("/home/vscode/.ssh/vbpub-scp_installer-r1002.vxxu.de-20260909-ed25519"),
    ),
    "v1001": Host(
        server_id=804027,
        server_name="v2202511209318406253",
        hostname="v1001.vxxu.de",
        short_name="v1001",
        ipv4="152.53.166.181",
        image_id=128,
        ssh_key=Path("/home/vscode/.ssh/vbpub-scp_installer-v1001.vxxu.de-20260909-ed25519"),
    ),
}


class PreflightError(ValueError):
    """A local, identity, or bundle guard refused before provider submission."""

    exit_code = 2


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    already_logged: bool = False


@dataclass(frozen=True)
class BundleCheck:
    digest: str
    plan: dict
    config: object
    bundle: dict


def _producer_imports():
    package_root = REPO_ROOT / "scripts" / "debian-install-v2"
    if not (package_root / "debian_install_v2" / "config.py").is_file():
        raise PreflightError("Debian-v2 source is missing; strict config validation is unavailable")
    added = str(package_root) not in sys.path
    if added:
        sys.path.insert(0, str(package_root))
    try:
        from debian_install_v2.config import ConfigError, load_config
        from debian_install_v2.customscript import build_customscript_bundle
        from debian_install_v2.plan_preview import PlanPreviewError, resolve_plan

        return (
            package_root, added, ConfigError, PlanPreviewError,
            load_config, resolve_plan, build_customscript_bundle,
        )
    except Exception:
        if added:
            sys.path.remove(str(package_root))
        raise


def _embedded_config(custom_script: str, *, host: str, expected_branch: str, load_config, ConfigError):
    try:
        tokens = shlex.split(custom_script)
    except ValueError as exc:
        raise PreflightError("customScript has malformed shell quoting") from exc
    values = {}
    for token in tokens:
        key, separator, value = token.partition("=")
        if separator and key in {"REPO_BRANCH", "VBPUB_CONFIG_EXTRA_JSON"}:
            if key in values:
                raise PreflightError(f"customScript has duplicate {key} assignments")
            values[key] = value
    if values.get("REPO_BRANCH") != expected_branch:
        raise PreflightError(
            f"customScript branch mismatch for {host}: expected {expected_branch!r}"
        )
    if "VBPUB_CONFIG_EXTRA_JSON" not in values:
        raise PreflightError("customScript has no embedded VBPUB_CONFIG_EXTRA_JSON")
    try:
        embedded = json.loads(values["VBPUB_CONFIG_EXTRA_JSON"])
        config = load_config(raw_json=json.dumps(embedded))
    except (json.JSONDecodeError, ConfigError, TypeError, ValueError) as exc:
        raise PreflightError(f"customScript failed strict v2 config validation: {exc}") from exc
    return config


def validate_bundle(
    host: str,
    config_path: str | Path,
    bundle_path: str | Path,
    *,
    disk_size: str,
    root_start_sector: int,
    root_number: int,
    repo_branch: str = "main",
) -> BundleCheck:
    if host not in HOSTS:
        raise PreflightError("host must be r1002 or v1001")
    (
        package_root, added, ConfigError, PlanPreviewError,
        load_config, resolve_plan, build_customscript_bundle,
    ) = _producer_imports()
    try:
        try:
            config = load_config(str(config_path))
        except (ConfigError, OSError, TypeError, ValueError) as exc:
            raise PreflightError(f"source config failed strict v2 config validation: {exc}") from exc
        try:
            bundle_bytes = Path(bundle_path).read_bytes()
            bundle = json.loads(bundle_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreflightError(f"cannot read generated customScript bundle: {exc}") from exc
        if not isinstance(bundle, dict) or set(bundle) != {"config", "customScript", "completionMarker"}:
            raise PreflightError("customScript bundle must contain exactly config, customScript and completionMarker")
        if not isinstance(bundle["customScript"], str) or not bundle["customScript"].strip():
            raise PreflightError("customScript bundle has no non-empty customScript string")
        try:
            bundle_config = load_config(raw_json=json.dumps(bundle["config"]))
        except (ConfigError, TypeError, ValueError) as exc:
            raise PreflightError(f"generated bundle failed strict v2 config validation: {exc}") from exc
        expected_bundle_config = asdict(config)
        if expected_bundle_config.get("controller_ssh_pubkey") == "":
            expected_bundle_config["controller_ssh_pubkey"] = "{{CONTROLLER_SSH_PUBKEY}}"
        if asdict(bundle_config) != expected_bundle_config:
            raise PreflightError("generated bundle config does not match the selected source config")
        embedded_config = _embedded_config(
            bundle["customScript"], host=host, expected_branch=repo_branch,
            load_config=load_config, ConfigError=ConfigError,
        )
        if asdict(embedded_config) != asdict(bundle_config):
            raise PreflightError("customScript's embedded config does not match the bundle config")
        expected_marker = f"{config.state_dir}/stage2_done"
        if bundle["completionMarker"] != expected_marker:
            raise PreflightError("customScript completionMarker does not match the validated config")
        try:
            regenerated = build_customscript_bundle(
                config,
                repo_branch=repo_branch,
                controller_ssh_placeholder=True,
            )
        except (ConfigError, TypeError, ValueError) as exc:
            raise PreflightError(f"cannot regenerate customScript bundle from source config: {exc}") from exc
        # Match runtime.output.primary_json(), which is the producer's actual
        # stdout representation used by build-customscript redirection.
        regenerated_bytes = (json.dumps(regenerated) + "\n").encode("utf-8")
        if bundle_bytes != regenerated_bytes:
            raise PreflightError(
                "bundle bytes do not exactly match the customScript regenerated from the reviewed source config"
            )
        try:
            plan = resolve_plan(
                config,
                disk_size=disk_size,
                root_start_sector=root_start_sector,
                root_number=root_number,
            )
        except (PlanPreviewError, ValueError) as exc:
            raise PreflightError(f"offline disk plan did not resolve: {exc}") from exc
        return BundleCheck(
            digest=sha256_file(bundle_path),
            plan=plan,
            config=config,
            bundle=bundle,
        )
    finally:
        if added:
            try:
                sys.path.remove(str(package_root))
            except ValueError:
                pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_ssh_identity(host: str, output: str) -> None:
    expected = HOSTS[host]
    lines = output.splitlines()
    wanted = [expected.hostname, expected.short_name, str(expected.server_id), "vda"]
    if lines[:4] != wanted:
        raise PreflightError("SSH identity mismatch: hostname/short-name/server-ID/disk differ")


def validate_live_disk(output: str, *, disk_size: str, root_start_sector: int) -> None:
    lines = output.splitlines()
    if len(lines) < 6:
        raise PreflightError("SSH disk preflight did not return disk bytes and root start sector")
    (
        package_root, added, _ConfigError, _PlanPreviewError,
        _load_config, _resolve_plan, _build_customscript_bundle,
    ) = _producer_imports()
    try:
        from debian_install_v2.plan_preview import disk_size_bytes

        expected_size = disk_size_bytes(disk_size)
    finally:
        if added:
            try:
                sys.path.remove(str(package_root))
            except ValueError:
                pass
    try:
        actual_size = int(lines[4])
        actual_start = int(lines[5])
    except ValueError as exc:
        raise PreflightError("SSH disk preflight returned malformed numeric facts") from exc
    if actual_size != expected_size or actual_start != root_start_sector:
        raise PreflightError(
            f"live disk mismatch: expected {expected_size} bytes/root start {root_start_sector}, "
            f"got {actual_size} bytes/root start {actual_start}"
        )


def validate_provider_payload(
    host: str,
    payload: object,
    *,
    expected_custom_script: str,
    expected_ssh_key_ids: list[int],
) -> None:
    expected = HOSTS[host]
    if not isinstance(payload, dict) or (
        payload.get("serverId") != expected.server_id
        or payload.get("hostname") != expected.hostname
        or payload.get("imageFlavourId") != expected.image_id
        or payload.get("diskName") != "vda"
        or payload.get("customScript") != expected_custom_script
        or payload.get("sshKeyIds") != expected_ssh_key_ids
    ):
        raise PreflightError(
            "provider payload mismatch: expected server, hostname, pinned image, vda, explicit SSH keys and reviewed customScript"
        )


def sha256_manifest(path: str | Path, bundle_path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = sha256_file(bundle_path)
    destination.write_text(f"{digest}  {Path(bundle_path)}\n", encoding="utf-8")
    return destination


def _remote_identity_command(host: str, root_number: int) -> str:
    expected = HOSTS[host]
    root_device = f"/dev/vda{root_number}"
    remote_script = "; ".join((
        "set -e",
        f"test \"$(hostname -f)\" = {shlex.quote(expected.hostname)}",
        f"test \"$(hostnamectl --static)\" = {shlex.quote(expected.short_name)}",
        f"test \"$(cat /etc/vbpub/server-id)\" = {expected.server_id}",
        "test \"$(lsblk -dn -o NAME /dev/vda)\" = vda",
        "printf '%s\\n' \"$(hostname -f)\" \"$(hostnamectl --static)\" "
        f"\"$(cat /etc/vbpub/server-id)\" \"$(lsblk -dn -o NAME /dev/vda)\" "
        f"\"$(blockdev --getsize64 /dev/vda)\" \"$(lsblk -n -o START {root_device})\"",
    ))
    env_function = "r1002_ssh" if host == "r1002" else "v1001_ssh"
    if host == "r1002":
        remote = f"root@{expected.ipv4}"
        api_check = "r1002_api_check"
    else:
        remote = 'root@"$V1001_IP"'
        api_check = "v1001_api_check"
    return (
        f"source {str(ENV_HELPER)!r}; {api_check} || exit $?; "
        f"{env_function} {remote} {shlex.quote(remote_script)}"
    )


def _wizard_argv(
    host: str,
    bundle_path: Path,
    run_dir: Path,
    *,
    ssh_key_ids: list[int],
    dry_run: bool = False,
) -> list[str]:
    expected = HOSTS[host]
    return [
        sys.executable,
        str(NETCUP_DIR / "install-host.py"),
        "wizard",
        "--dry-run" if dry_run else "--yes",
        "--monitor",
        "--attach-custom-script",
        "--server-id", str(expected.server_id),
        "--image", str(expected.image_id),
        "--custom-script-file", str(bundle_path),
        "--completion-marker", "/var/lib/vbpub/bootstrap/stage2_done",
        "--local-controller-key", "retain",
        "--ssh-identity-file", str(expected.ssh_key),
        "--ssh-host", expected.ipv4,
        "--ssh-user", "root",
        "--out-dir", str(run_dir),
        *[argument for key_id in ssh_key_ids for argument in ("--ssh-key-id", str(key_id))],
    ]


def _current_remote_commit(repo_branch: str) -> str:
    package_root, added, _ConfigError, _PlanPreviewError, _load_config, _resolve_plan, _build = _producer_imports()
    try:
        from debian_install_v2.customscript import REPO_URL_DEFAULT
    finally:
        if added:
            try:
                sys.path.remove(str(package_root))
            except ValueError:
                pass
    result = subprocess.run(
        ["git", "ls-remote", REPO_URL_DEFAULT, f"refs/heads/{repo_branch}"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PreflightError(f"could not resolve remote branch commit: {result.stderr.strip()[-1000:]}")
    rows = result.stdout.split()
    if len(rows) < 2 or rows[1] != f"refs/heads/{repo_branch}":
        raise PreflightError(f"remote branch commit for {repo_branch!r} could not be resolved")
    return rows[0]


def _write_task_state(path: Path, task_id: str) -> str:
    command = f"{sys.executable} {NETCUP_DIR / 'monitor-task.py'} watch {task_id}"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as state:
        state.write(f"task_id={task_id}\nmonitor_command={command}\n")
        state.flush()
        os.fsync(state.fileno())
    os.chmod(path, 0o600)
    return command


TASK_ID_RE = re.compile(r"^\s*Task UUID:\s*(\S+)\s*$", re.IGNORECASE)


def _stream_wizard(
    argv: list[str],
    *,
    log,
    task_state_path: Path,
    popen_factory: Callable | None = None,
) -> CommandResult:
    factory = popen_factory or subprocess.Popen
    process = factory(
        argv,
        cwd=NETCUP_DIR,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    task_id = None
    capture_output = "--dry-run" in argv
    captured = []
    try:
        for line in process.stdout:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
            if capture_output:
                captured.append(line)
            match = TASK_ID_RE.match(line)
            if match and task_id is None:
                task_id = match.group(1)
                monitor_command = _write_task_state(task_state_path, task_id)
                log.write(f"TASK_ID={task_id}\nMONITOR_COMMAND={monitor_command}\n")
                log.flush()
                print(f"TASK_ID={task_id}", flush=True)
        returncode = process.wait()
    except KeyboardInterrupt:
        if task_id is not None:
            monitor_command = f"{sys.executable} {NETCUP_DIR / 'monitor-task.py'} watch {task_id}"
            print(
                f"Installation task {task_id} was submitted. Continue monitoring with:\n"
                f"  {monitor_command}",
                file=sys.stderr,
                flush=True,
            )
        terminate = getattr(process, "terminate", None)
        if terminate is not None:
            terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill = getattr(process, "kill", None)
            if kill is not None:
                kill()
            process.wait()
        except BaseException:
            pass
        raise
    finally:
        close = getattr(process.stdout, "close", None)
        if close is not None:
            close()
    return CommandResult(returncode, "".join(captured), "", already_logged=True)


def _provider_payload_from_wizard_output(output: str) -> dict:
    marker = "INSTALLATION PARAMETERS SUMMARY"
    marker_at = output.find(marker)
    if marker_at < 0:
        raise PreflightError("wizard dry-run did not print its generated provider payload")
    json_at = output.find("{", marker_at + len(marker))
    if json_at < 0:
        raise PreflightError("wizard dry-run provider payload was not JSON")
    try:
        payload, _end = json.JSONDecoder().raw_decode(output[json_at:])
    except json.JSONDecodeError as exc:
        raise PreflightError("wizard dry-run provider payload was malformed JSON") from exc
    if not isinstance(payload, dict):
        raise PreflightError("wizard dry-run provider payload was not an object")
    return payload


def run_logged(log_path: str | Path, action: Callable) -> int:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as log:
        log.write(f"START={datetime.now(timezone.utc).isoformat()}\n")
        try:
            outcome = action(log)
            if isinstance(outcome, CommandResult):
                status = outcome.returncode
                if outcome.stdout and not outcome.already_logged:
                    log.write(outcome.stdout.rstrip() + "\n")
                if outcome.stderr and not outcome.already_logged:
                    log.write("STDERR: " + outcome.stderr.rstrip() + "\n")
            else:
                status = int(outcome)
        except BaseException as exc:
            status = 130 if isinstance(exc, KeyboardInterrupt) else int(getattr(exc, "exit_code", 1))
            log.write(f"ERROR={type(exc).__name__}: {exc}\n")
            log.write(f"EXIT={status}\n")
            log.flush()
            path.with_suffix(path.suffix + ".EXIT").write_text(f"{status}\n", encoding="utf-8")
            raise
        log.write(f"EXIT={status}\n")
        log.flush()
    path.with_suffix(path.suffix + ".EXIT").write_text(f"{status}\n", encoding="utf-8")
    return status


def run_reinstall(
    host: str,
    config_path: str | Path,
    bundle_path: str | Path,
    *,
    disk_size: str,
    root_start_sector: int,
    root_number: int,
    repo_branch: str = "main",
    run_dir: str | Path | None = None,
    dry_run: bool = False,
    ssh_key_ids: list[int] | None = None,
    expected_bundle_sha256: str | None = None,
    expected_commit: str | None = None,
    runner: Callable = subprocess.run,
    commit_lookup: Callable[[str], str] | None = None,
    popen_factory: Callable | None = None,
) -> int:
    if host not in HOSTS:
        raise PreflightError("host must be r1002 or v1001")
    if not ssh_key_ids or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ssh_key_ids):
        raise PreflightError("choose account SSH keys explicitly with one or more --ssh-key-id values")
    if not expected_commit or not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_commit):
        raise PreflightError("a full --expect-commit SHA is required")
    if not dry_run and not expected_bundle_sha256:
        raise PreflightError("--yes requires --expect-bundle-sha256 from the reviewed dry-run")
    if expected_bundle_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_bundle_sha256):
        raise PreflightError("--expect-bundle-sha256 must be a 64-character SHA-256 hex digest")
    output_dir = Path(run_dir) if run_dir is not None else RELEASE_ROOT / host
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os_chmod_private(output_dir)
    bundle_sha_path = output_dir / "bundle.sha256"

    def action(log):
        # Start the log before any local or remote preflight so stale-key and
        # identity refusals also leave a durable EXIT record.
        checked = validate_bundle(
            host, config_path, bundle_path,
            disk_size=disk_size,
            root_start_sector=root_start_sector,
            root_number=root_number,
            repo_branch=repo_branch,
        )
        current_commit = (commit_lookup or _current_remote_commit)(repo_branch)
        if current_commit.lower() != expected_commit.lower():
            raise PreflightError(
                f"remote branch commit {current_commit} differs from --expect-commit {expected_commit}"
            )
        if expected_bundle_sha256 and checked.digest.lower() != expected_bundle_sha256.lower():
            raise PreflightError(
                f"bundle SHA256 {checked.digest} differs from --expect-bundle-sha256 {expected_bundle_sha256}"
            )
        log.write(f"REMOTE_COMMIT={current_commit}\nBUNDLE_SHA256={checked.digest}\n")
        log.flush()
        print(f"REMOTE_COMMIT={current_commit}\nBUNDLE_SHA256={checked.digest}", flush=True)
        sha256_manifest(bundle_sha_path, bundle_path)
        identity = runner(
            ["bash", "-lc", _remote_identity_command(host, root_number)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if identity.returncode != 0:
            raise PreflightError(f"API/SSH host identity guard failed ({identity.returncode})")
        validate_ssh_identity(host, identity.stdout)
        validate_live_disk(
            identity.stdout,
            disk_size=disk_size,
            root_start_sector=root_start_sector,
        )
        preview = _stream_wizard(
            _wizard_argv(
                host, Path(bundle_path), output_dir,
                ssh_key_ids=ssh_key_ids, dry_run=True,
            ),
            log=log,
            task_state_path=output_dir / "task-id",
            popen_factory=popen_factory,
        )
        if preview.returncode != 0:
            return preview
        provider_payload = _provider_payload_from_wizard_output(preview.stdout)
        validate_provider_payload(
            host,
            provider_payload,
            expected_custom_script=checked.bundle["customScript"],
            expected_ssh_key_ids=ssh_key_ids,
        )
        if dry_run:
            return preview
        submitted = _stream_wizard(
            _wizard_argv(
                host, Path(bundle_path), output_dir,
                ssh_key_ids=ssh_key_ids, dry_run=False,
            ),
            log=log,
            task_state_path=output_dir / "task-id",
            popen_factory=popen_factory,
        )
        return submitted

    status = run_logged(output_dir / "reinstall.log", action)
    if status:
        return status
    print(
        f"Validated config, offline plan, provider payload and remote commit; "
        f"bundle SHA256 recorded in {bundle_sha_path}",
        flush=True,
    )
    return 0


def os_chmod_private(path: Path) -> None:
    import os

    os.chmod(path, 0o700)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=tuple(HOSTS))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--disk-size", required=True)
    parser.add_argument("--root-start-sector", required=True, type=int)
    parser.add_argument("--root-number", required=True, type=int)
    parser.add_argument("--repo-branch", default="main")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument(
        "--ssh-key-id", required=True, action="append", type=int,
        help="explicit Netcup account SSH key ID to inject; repeat for multiple keys",
    )
    parser.add_argument(
        "--expect-commit", required=True,
        help="full remote branch commit printed by build-customscript and verified as merged",
    )
    parser.add_argument(
        "--expect-bundle-sha256",
        help="bundle SHA-256 printed by the reviewed dry-run; required with --yes",
    )
    consent = parser.add_mutually_exclusive_group(required=True)
    consent.add_argument("--dry-run", action="store_true", help="validate and log the provider plan without submitting")
    consent.add_argument("--yes", action="store_true", help="required consent to submit the provider install")
    args = parser.parse_args(argv)
    if args.yes and not args.expect_bundle_sha256:
        parser.error("--yes requires --expect-bundle-sha256 from the reviewed dry-run")
    try:
        return run_reinstall(
            args.host,
            args.config,
            args.bundle,
            disk_size=args.disk_size,
            root_start_sector=args.root_start_sector,
            root_number=args.root_number,
            repo_branch=args.repo_branch,
            run_dir=args.run_dir,
            dry_run=args.dry_run,
            ssh_key_ids=args.ssh_key_id,
            expected_bundle_sha256=args.expect_bundle_sha256,
            expected_commit=args.expect_commit,
        )
    except PreflightError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
