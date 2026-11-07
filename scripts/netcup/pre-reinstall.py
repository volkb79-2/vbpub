#!/usr/bin/env python3
"""Validate, identify, log and submit the reviewed Debian-v2 reinstall."""

from __future__ import annotations

import argparse
import hashlib
import json
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
        from debian_install_v2.plan_preview import PlanPreviewError, resolve_plan

        return package_root, added, ConfigError, PlanPreviewError, load_config, resolve_plan
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
    package_root, added, ConfigError, PlanPreviewError, load_config, resolve_plan = _producer_imports()
    try:
        try:
            config = load_config(str(config_path))
        except (ConfigError, OSError, TypeError, ValueError) as exc:
            raise PreflightError(f"source config failed strict v2 config validation: {exc}") from exc
        try:
            bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
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


def validate_api_record(host: str, record: object) -> None:
    expected = HOSTS[host]
    if not isinstance(record, dict):
        raise PreflightError("API identity mismatch: record is not an object")
    ips = [
        item.get("ip") for item in record.get("ipv4Addresses", [])
        if isinstance(item, dict)
    ]
    live = record.get("serverLiveInfo")
    disks = live.get("disks", []) if isinstance(live, dict) else []
    names = [item.get("dev") for item in disks if isinstance(item, dict)]
    if (
        record.get("id") != expected.server_id
        or record.get("name") != expected.server_name
        or record.get("hostname") != expected.hostname
        or expected.ipv4 not in ips
        or not isinstance(live, dict)
        or live.get("state") != "RUNNING"
        or "vda" not in names
    ):
        raise PreflightError("API identity mismatch: server ID/name/hostname/address/state/disk differ")


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
    package_root, added, _ConfigError, _PlanPreviewError, _load_config, _resolve_plan = _producer_imports()
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
) -> None:
    expected = HOSTS[host]
    if not isinstance(payload, dict) or (
        payload.get("serverId") != expected.server_id
        or payload.get("hostname") != expected.hostname
        or payload.get("imageFlavourId") != expected.image_id
        or payload.get("diskName") != "vda"
        or payload.get("customScript") != expected_custom_script
    ):
        raise PreflightError(
            "provider payload mismatch: expected server, hostname, pinned image, vda and reviewed customScript"
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


def _wizard_argv(host: str, bundle_path: Path, run_dir: Path, *, dry_run: bool = False) -> list[str]:
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
    ]


def run_logged(log_path: str | Path, action: Callable[[], int | CommandResult]) -> int:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a", encoding="utf-8") as log:
        log.write(f"START={datetime.now(timezone.utc).isoformat()}\n")
        try:
            outcome = action()
            if isinstance(outcome, CommandResult):
                status = outcome.returncode
                if outcome.stdout:
                    log.write(outcome.stdout.rstrip() + "\n")
                if outcome.stderr:
                    log.write("STDERR: " + outcome.stderr.rstrip() + "\n")
            else:
                status = int(outcome)
        except BaseException as exc:
            status = int(getattr(exc, "exit_code", 1))
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
    runner: Callable = subprocess.run,
) -> int:
    if host not in HOSTS:
        raise PreflightError("host must be r1002 or v1001")
    output_dir = Path(run_dir) if run_dir is not None else RELEASE_ROOT / host
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os_chmod_private(output_dir)
    bundle_sha_path = output_dir / "bundle.sha256"

    def action():
        # Start the log before any local or remote preflight so stale-key and
        # identity refusals also leave a durable EXIT record.
        checked = validate_bundle(
            host, config_path, bundle_path,
            disk_size=disk_size,
            root_start_sector=root_start_sector,
            root_number=root_number,
            repo_branch=repo_branch,
        )
        expected = HOSTS[host]
        provider_payload = {
            "serverId": expected.server_id,
            "hostname": expected.hostname,
            "imageFlavourId": expected.image_id,
            "diskName": "vda",
            "customScript": checked.bundle["customScript"],
        }
        validate_provider_payload(
            host,
            provider_payload,
            expected_custom_script=checked.bundle["customScript"],
        )
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
        wizard = runner(
            _wizard_argv(host, Path(bundle_path), output_dir, dry_run=dry_run),
            cwd=NETCUP_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return CommandResult(wizard.returncode, wizard.stdout, wizard.stderr)

    status = run_logged(output_dir / "reinstall.log", action)
    if status:
        return status
    print(f"Validated config and offline plan; bundle SHA256 recorded in {bundle_sha_path}")
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
    consent = parser.add_mutually_exclusive_group(required=True)
    consent.add_argument("--dry-run", action="store_true", help="validate and log the provider plan without submitting")
    consent.add_argument("--yes", action="store_true", help="required consent to submit the provider install")
    args = parser.parse_args(argv)
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
        )
    except PreflightError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
