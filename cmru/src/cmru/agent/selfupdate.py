"""Self-update handoff for the cmru-agent (spec §6.4).

When desired state pins a new cmru version:
  1. Stage the new cmru wheel into a fresh venv under <root>/venv-<version>.
  2. Write a pending-selfupdate marker.
  3. Hand off via systemd restart (never overwrite the running interpreter in place).

Ships a minimal systemd unit template (rendered at enroll).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Systemd unit template (rendered at enroll — no dstdns topology inside)
# ---------------------------------------------------------------------------

CMRU_AGENT_SERVICE_TEMPLATE = """\
[Unit]
Description=CMRU reconciler agent
After=network-online.target consul.service

[Service]
Type=simple
ExecStart={venv_python} -m cmru.agent.cli run --scope {scope}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def render_service_unit(venv_python: str, scope: str = "system") -> str:
    """Render the systemd unit template with the given venv python path."""
    return CMRU_AGENT_SERVICE_TEMPLATE.format(
        venv_python=venv_python,
        scope=scope,
    )


# ---------------------------------------------------------------------------
# Venv staging
# ---------------------------------------------------------------------------

def stage_new_venv(
    root: Path,
    version: str,
    wheel_path: Path,
) -> Path:
    """Create a fresh venv under <root>/venv-<version> and install the wheel.

    Returns the path to the staged venv.  Does NOT activate or restart.
    The running interpreter is NEVER overwritten.
    """
    venv_dir = root / f"venv-{version}"
    if venv_dir.exists():
        log.info("Venv %s already staged", venv_dir)
        return venv_dir

    log.info("Staging new venv %s for cmru %s", venv_dir, version)
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"venv creation failed (exit {result.returncode}): {result.stderr[:500]}"
        )

    pip = venv_dir / "bin" / "pip"
    result = subprocess.run(
        [str(pip), "install", "--quiet", str(wheel_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"wheel install failed (exit {result.returncode}): {result.stderr[:500]}"
        )

    log.info("Staged venv %s successfully", venv_dir)
    return venv_dir


def write_pending_marker(root: Path, version: str, venv_dir: Path) -> None:
    """Write a pending-selfupdate marker file."""
    marker = root / "pending-selfupdate"
    marker.write_text(f"version={version}\nvenv={venv_dir}\n")
    log.info("Wrote pending-selfupdate marker: %s", marker)


def read_pending_marker(root: Path) -> Optional[dict]:
    """Read the pending-selfupdate marker; returns None if absent."""
    marker = root / "pending-selfupdate"
    if not marker.exists():
        return None
    data: dict = {}
    for line in marker.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    return data or None


def clear_pending_marker(root: Path) -> None:
    marker = root / "pending-selfupdate"
    if marker.exists():
        marker.unlink()


def handoff_via_systemd(
    version: str,
    venv_dir: Path,
    scope: str = "system",
    unit_name: str = "cmru-agent",
    dry_run: bool = False,
) -> None:
    """Update the symlink/current reference and hand off via systemctl restart.

    NEVER overwrites the running interpreter in place.  Instead:
    1. Atomically update the 'venv' symlink to point to venv_dir.
    2. Call `systemctl restart <unit>` (or `systemctl --user restart` for user scope).
    3. The running process exits cleanly; `Restart=always` brings the new version up.
    """
    root = venv_dir.parent
    current_link = root / "venv-current"

    # Atomic symlink update: new → rename
    tmp_link = root / "venv-current.new"
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    os.symlink(venv_dir, tmp_link)
    tmp_link.rename(current_link)
    log.info("Updated venv-current → %s", venv_dir)

    if dry_run:
        log.info("[DRY RUN] Would restart systemd unit %s", unit_name)
        return

    cmd = ["systemctl"]
    if scope == "user":
        cmd.append("--user")
    cmd += ["restart", unit_name]

    log.info("Restarting %s via systemd: %s", unit_name, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.warning(
            "systemctl restart %s failed (exit %s): %s — relying on Restart=always",
            unit_name, result.returncode, result.stderr[:200]
        )
        # Exit cleanly — systemd Restart=always will bring the new version up
        sys.exit(0)
