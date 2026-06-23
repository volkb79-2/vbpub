"""CIU SSH transport — subprocess ssh/rsync (default) + paramiko (optional).

Default: subprocess ssh/rsync (zero added deps, needs openssh-client).
Paramiko: set CIU_SSH_TRANSPORT=paramiko AND have paramiko installed.

Security rules (SPEC J §5):
  - known_host pinning: refuse connections without a pinned host key
    unless CIU_SSH_INSECURE_TOFU=1 (documented escape hatch only).
  - Key material is NEVER logged (only paths are logged/reported).
  - ASK_VAULT keys are resolved and written to temp files (mode 0600).
  - Temp files are always cleaned up in finally blocks.
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


def _parse_ask_vault(directive: str) -> tuple[str, Optional[str]]:
    """Parse ASK_VAULT:<path>[#<field>] → (path, field)."""
    body = directive[len("ASK_VAULT:"):]
    if "#" in body:
        vault_path, field = body.split("#", 1)
        return vault_path, field
    return body, None


def resolve_key(host_cfg: dict, config: dict, repo_root: Path) -> str:
    """Resolve the SSH private key. Returns a filesystem path (mode 0600).

    If ssh_key starts with 'ASK_VAULT:', reads via VaultKV2 + resolve_vault_token,
    writes to a temp file (mode 0600), returns the path (caller cleans up).
    Otherwise expands the path and returns it.
    NEVER logs key material — only paths.
    """
    ssh_key = host_cfg.get("ssh_key", "")
    if not ssh_key:
        raise ValueError("[SPEC J] No ssh_key configured for this host.")

    if ssh_key.startswith("ASK_VAULT:"):
        vault_path, field = _parse_ask_vault(ssh_key)
        # Resolve vault credentials
        from .secrets.providers import VaultKV2, resolve_vault_token, vault_addr_from_config
        token = resolve_vault_token(config, repo_root)
        if not token:
            raise ValueError(
                f"[SPEC J] ASK_VAULT: key requires a Vault token. "
                f"Set VAULT_TOKEN or configure vault.token_file."
            )
        addr = vault_addr_from_config(config)
        client = VaultKV2(addr, token)
        key_material = client.read(vault_path, field=field)
        if not key_material:
            raise ValueError(f"[SPEC J] Vault returned no key material for path '{vault_path}'.")
        # Write to temp file, mode 0600, never log material
        fd, tmp_path = tempfile.mkstemp(prefix="ciu_ssh_key_", suffix=".pem")
        try:
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w") as fh:
                fh.write(key_material)
                if not key_material.endswith("\n"):
                    fh.write("\n")
        except Exception:
            os.unlink(tmp_path)
            raise
        return tmp_path

    # Filesystem path
    return str(Path(ssh_key).expanduser().resolve())


def _known_hosts_file(host_cfg: dict) -> Optional[str]:
    """Write known_host to a temp file (0600). Returns path or None."""
    known_host = host_cfg.get("known_host")
    if not known_host:
        return None
    ssh_host = host_cfg.get("ssh_host", "")
    ssh_port = int(host_cfg.get("ssh_port", 22))
    # OpenSSH/paramiko known_hosts: a non-default port is keyed as "[host]:port",
    # not the bare hostname — otherwise the pinned entry never matches and the
    # connection is (fail-closed) rejected. Format: "<host-token> <keytype> <key>".
    host_token = ssh_host if ssh_port == 22 else f"[{ssh_host}]:{ssh_port}"
    line = f"{host_token} {known_host}\n"
    fd, tmp_path = tempfile.mkstemp(prefix="ciu_known_hosts_")
    try:
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w") as fh:
            fh.write(line)
    except Exception:
        os.unlink(tmp_path)
        raise
    return tmp_path


def ssh_exec(
    host_cfg: dict,
    argv: list[str],
    *,
    config: dict,
    repo_root: Path,
    interactive: bool = False,
    admin: bool = False,
) -> int:
    """Run a command on the remote host (or open an interactive shell).

    Security: FAIL CLOSED when no known_host is configured, unless
    CIU_SSH_INSECURE_TOFU=1 is explicitly set (escape hatch).
    """
    ssh_host = host_cfg.get("ssh_host")
    ssh_user = host_cfg.get("ssh_user", "root")
    ssh_port = str(host_cfg.get("ssh_port", 22))

    if not ssh_host:
        raise ValueError("[SPEC J] ssh_host not configured for this host.")

    # Check for known_host pinning requirement (SPEC J §5 security)
    known_host_val = host_cfg.get("known_host")
    if not known_host_val:
        tofu_ok = os.environ.get("CIU_SSH_INSECURE_TOFU", "") == "1"
        if not tofu_ok:
            raise ValueError(
                f"[SPEC J] Host '{ssh_host}' has no 'known_host' pinned. "
                "Refusing connection (no blind TOFU). "
                "Set CIU_SSH_INSECURE_TOFU=1 to override (insecure, for bootstrap only)."
            )

    use_paramiko = (
        os.environ.get("CIU_SSH_TRANSPORT", "").lower() == "paramiko"
    )
    if use_paramiko:
        try:
            import paramiko  # noqa: F401 - checked here
        except ImportError:
            use_paramiko = False

    key_path: Optional[str] = None
    known_hosts_path: Optional[str] = None
    vault_key_tmp: bool = False

    try:
        key_path = resolve_key(host_cfg, config, repo_root)
        vault_key_tmp = host_cfg.get("ssh_key", "").startswith("ASK_VAULT:")
        known_hosts_path = _known_hosts_file(host_cfg)

        if use_paramiko:
            return _ssh_exec_paramiko(
                ssh_host, ssh_user, int(ssh_port), key_path,
                known_hosts_path, argv, interactive=interactive,
            )
        else:
            return _ssh_exec_subprocess(
                ssh_host, ssh_user, ssh_port, key_path,
                known_hosts_path, argv, interactive=interactive,
            )
    finally:
        if vault_key_tmp and key_path and Path(key_path).exists():
            try:
                os.unlink(key_path)
            except OSError:
                pass
        if known_hosts_path and Path(known_hosts_path).exists():
            try:
                os.unlink(known_hosts_path)
            except OSError:
                pass


def _ssh_exec_subprocess(
    ssh_host: str,
    ssh_user: str,
    ssh_port: str,
    key_path: str,
    known_hosts_path: Optional[str],
    argv: list[str],
    *,
    interactive: bool,
) -> int:
    cmd = [
        "ssh",
        "-i", key_path,
        "-p", ssh_port,
    ]
    if known_hosts_path:
        cmd += ["-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={known_hosts_path}"]
    else:
        # TOFU escape hatch (CIU_SSH_INSECURE_TOFU=1 already gated upstream):
        # accept any host key. Insecure — bootstrap only.
        cmd += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    if not interactive:
        cmd += ["-o", "BatchMode=yes"]
    else:
        cmd += ["-t"]
    cmd.append(f"{ssh_user}@{ssh_host}")
    if argv:
        cmd += ["--"] + list(argv)
    # Stream stdout/stderr (no capture)
    result = subprocess.run(cmd)
    return result.returncode


def _ssh_exec_paramiko(
    ssh_host: str,
    ssh_user: str,
    ssh_port: int,
    key_path: str,
    known_hosts_path: Optional[str],
    argv: list[str],
    *,
    interactive: bool,
) -> int:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    if known_hosts_path:
        client.load_host_keys(known_hosts_path)
    client.connect(
        hostname=ssh_host,
        port=ssh_port,
        username=ssh_user,
        key_filename=key_path,
    )
    try:
        if not argv:
            # Interactive shell: open a channel
            chan = client.invoke_shell()
            import select
            import sys as _sys
            while True:
                r, _, _ = select.select([chan, _sys.stdin], [], [], 0.1)
                if chan in r:
                    data = chan.recv(1024)
                    if not data:
                        break
                    _sys.stdout.buffer.write(data)
                    _sys.stdout.buffer.flush()
                if _sys.stdin in r:
                    data = _sys.stdin.buffer.read(1)
                    if not data:
                        break
                    chan.send(data)
            return chan.recv_exit_status()
        else:
            cmd_str = " ".join(argv)
            stdin, stdout, stderr = client.exec_command(cmd_str)
            import sys as _sys
            for line in stdout:
                _sys.stdout.write(line)
            for line in stderr:
                _sys.stderr.write(line)
            return stdout.channel.recv_exit_status()
    finally:
        client.close()


def ssh_sync(
    host_cfg: dict,
    local_dir: str,
    remote_dir: str,
    *,
    config: dict,
    repo_root: Path,
    admin: bool = False,
) -> int:
    """Rsync the bundle to the remote host.

    Uses: rsync -az -e "ssh -i <key> -p <port> -o StrictHostKeyChecking=yes
          -o UserKnownHostsFile=<pinned>" <local_dir>/ <user>@<host>:<remote_dir>/
    """
    ssh_host = host_cfg.get("ssh_host")
    ssh_user = host_cfg.get("ssh_user", "root")
    ssh_port = str(host_cfg.get("ssh_port", 22))

    if not ssh_host:
        raise ValueError("[SPEC J] ssh_host not configured for this host.")

    known_host_val = host_cfg.get("known_host")
    if not known_host_val:
        tofu_ok = os.environ.get("CIU_SSH_INSECURE_TOFU", "") == "1"
        if not tofu_ok:
            raise ValueError(
                f"[SPEC J] Host '{ssh_host}' has no 'known_host' pinned. "
                "Refusing rsync (no blind TOFU). "
                "Set CIU_SSH_INSECURE_TOFU=1 to override (insecure)."
            )

    key_path: Optional[str] = None
    known_hosts_path: Optional[str] = None
    vault_key_tmp: bool = False

    try:
        key_path = resolve_key(host_cfg, config, repo_root)
        vault_key_tmp = host_cfg.get("ssh_key", "").startswith("ASK_VAULT:")
        known_hosts_path = _known_hosts_file(host_cfg)

        ssh_opts = [f"ssh -i {key_path} -p {ssh_port}"]
        if known_hosts_path:
            ssh_opts += ["-o StrictHostKeyChecking=yes",
                         f"-o UserKnownHostsFile={known_hosts_path}"]
        else:
            ssh_opts += ["-o StrictHostKeyChecking=no", "-o UserKnownHostsFile=/dev/null"]

        ssh_cmd = " ".join(ssh_opts)
        src = local_dir.rstrip("/") + "/"
        dst = f"{ssh_user}@{ssh_host}:{remote_dir}/"

        cmd = ["rsync", "-az", "-e", ssh_cmd, src, dst]
        result = subprocess.run(cmd)
        return result.returncode
    finally:
        if vault_key_tmp and key_path and Path(key_path).exists():
            try:
                os.unlink(key_path)
            except OSError:
                pass
        if known_hosts_path and Path(known_hosts_path).exists():
            try:
                os.unlink(known_hosts_path)
            except OSError:
                pass
