"""CIU docker-optional push→activate deploy path (SPEC S14.6, CIU-12).

The default ``ciu up --host`` path (S14.2) rsyncs the repo to the target and
runs ``ciu env generate && ciu render && ciu up`` there — which requires Docker
**and** a full Python/CIU install on the target. That does not fit a shared
**Passenger webhoster**: an SSH shell with only POSIX ``sh`` + ``tar``/``unzip``
+ ``touch``, no Docker and no general-purpose Python.

``--thin`` engages this module instead. It splits the deploy into two concerns:

  1. **push** — ship an artifact (the repo tree) to the host's ``bundle_dir``.
     Prefer :func:`transport_ssh.ssh_sync` (rsync); fall back to a ``tar`` +
     :func:`transport_ssh.scp_file` + remote-``tar``-extract when the host or the
     control box has no ``rsync``. Only ``sh``/``tar`` are needed on the target.

  2. **activate** — run a **pluggable activation contract** over ``ssh_exec``:
     the ``bootstrap | apply | health | rollback`` verbs (the same four-verb
     shape as the cmru ProjectAdapter). CIU does **not** hardcode
     ``ciu render && ciu up``; the *project* supplies the shell command(s) via
     the ``activate`` host key. CIU supplies transport + inventory + host-key
     pinning + Vault-key resolution.

CIU supplies transport; the project supplies activation. The existing docker
render-on-target path (S14.2) is untouched — ``--thin`` is a parallel branch.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

from .transport_ssh import scp_file, ssh_exec, ssh_sync

# The four-verb activation contract (mirrors the cmru ProjectAdapter shape:
# bootstrap|apply|health|rollback). CIU maps CLI intent onto these verbs; the
# project's `activate` entrypoint/table implements them in shell.
ACTIVATION_VERBS = ("bootstrap", "apply", "health", "rollback")

# Shipped-by-default with neither the docker render path nor the target needing
# it; keeps the scp/tar bundle small. Overridable per host via bundle_excludes.
DEFAULT_BUNDLE_EXCLUDES = [".git"]

# Name of the transient tarball dropped in bundle_dir by the scp/tar fallback.
_REMOTE_TARBALL = ".ciu_bundle.tar.gz"


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def resolve_activation_command(host_cfg: dict, verb: str) -> str:
    """Resolve the remote shell command for a contract *verb*.

    The ``activate`` host key takes one of two shapes:

      * a **string** entrypoint — CIU appends the verb::

            activate = "sh deploy/activate.sh"     # -> "sh deploy/activate.sh apply"

      * a **table** of explicit per-verb commands::

            [deploy.hosts.web.activate]
            bootstrap = "sh deploy/activate.sh bootstrap"
            apply     = "touch tmp/restart.txt"
            health    = "sh deploy/health.sh"
            rollback  = "sh deploy/rollback.sh"

    Raises ValueError with an actionable message when ``activate`` is missing,
    malformed, or has no entry for *verb*.
    """
    if verb not in ACTIVATION_VERBS:
        raise ValueError(
            f"[S14.6] Unknown activation verb '{verb}'. "
            f"Expected one of: {', '.join(ACTIVATION_VERBS)}."
        )
    activate = host_cfg.get("activate")
    if activate is None:
        raise ValueError(
            "[S14.6] This host is being deployed with --thin (docker-optional "
            "push→activate) but declares no 'activate' entrypoint. Add either\n"
            "    activate = \"sh deploy/activate.sh\"\n"
            "or a [deploy.hosts.<name>.activate] table with "
            f"{'/'.join(ACTIVATION_VERBS)} commands to the host inventory."
        )
    if isinstance(activate, str):
        entry = activate.strip()
        if not entry:
            raise ValueError("[S14.6] 'activate' entrypoint is empty.")
        return f"{entry} {verb}"
    if isinstance(activate, dict):
        cmd = activate.get(verb)
        if not cmd or not str(cmd).strip():
            raise ValueError(
                f"[S14.6] The activate table for this host has no '{verb}' command. "
                f"Add {verb} = \"...\" (or use a string entrypoint that takes the verb)."
            )
        return str(cmd).strip()
    raise ValueError(
        "[S14.6] 'activate' must be a string entrypoint or a per-verb table, "
        f"got {type(activate).__name__}."
    )


def make_tarball(local_dir: str, *, excludes: Optional[list[str]] = None) -> str:
    """tar.gz *local_dir*'s contents into a temp file; return its path.

    Members are stored **relative to** ``local_dir`` (arcname ".") so that a
    remote ``tar xzf … -C <bundle_dir>`` reproduces the tree directly under
    ``bundle_dir`` — matching rsync's ``<src>/ -> <dst>/`` content semantics.
    ``excludes`` are matched against each member's path relative to
    ``local_dir`` (leading segment or exact path), mirroring rsync ``--exclude``.
    """
    ex = set(excludes or [])
    root = Path(local_dir).resolve()

    def _filter(ti: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        # ti.name is relative to arcname "." e.g. "./.git/config" or ".git/config"
        rel = ti.name.lstrip("./")
        if not rel:
            return ti
        first = rel.split("/", 1)[0]
        if first in ex or rel in ex:
            return None
        return ti

    fd, tmp_path = tempfile.mkstemp(prefix="ciu_bundle_", suffix=".tar.gz")
    os.close(fd)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            tar.add(str(root), arcname=".", filter=_filter)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return tmp_path


def _push_scp(
    host_cfg: dict,
    local_dir: str,
    remote_dir: str,
    *,
    config: dict,
    repo_root: Path,
    excludes: Optional[list[str]] = None,
) -> int:
    """tar + scp + remote-extract fallback for hosts/control-boxes without rsync.

    Only ``sh`` and ``tar`` are required on the target. The transient tarball is
    removed remotely after extraction and locally in ``finally``.
    """
    tarball = make_tarball(local_dir, excludes=excludes)
    remote_dir_clean = remote_dir.rstrip("/")
    remote_tar = f"{remote_dir_clean}/{_REMOTE_TARBALL}"
    try:
        rc = ssh_exec(host_cfg, [f"mkdir -p {remote_dir_clean}"],
                      config=config, repo_root=repo_root)
        if rc != 0:
            _err(f"[S14.6] Could not create remote bundle_dir '{remote_dir_clean}' (rc={rc}).")
            return rc
        rc = scp_file(host_cfg, tarball, remote_tar, config=config, repo_root=repo_root)
        if rc != 0:
            _err(f"[S14.6] scp of the bundle failed (rc={rc}).")
            return rc
        extract = (
            f"tar xzf {remote_tar} -C {remote_dir_clean} && rm -f {remote_tar}"
        )
        rc = ssh_exec(host_cfg, [extract], config=config, repo_root=repo_root)
        if rc != 0:
            _err(f"[S14.6] Remote tar extraction failed (rc={rc}).")
        return rc
    finally:
        try:
            os.unlink(tarball)
        except OSError:
            pass


def push_bundle(
    host_cfg: dict,
    local_dir: str,
    remote_dir: str,
    *,
    config: dict,
    repo_root: Path,
) -> int:
    """Push the bundle to ``remote_dir``; return an rc (0 == success).

    ``push_mode`` host key selects the strategy:

      * ``"auto"`` (default) — try rsync; on control-side ``rsync`` absent
        (``FileNotFoundError``) **or** remote ``rsync`` absent (ssh rc 127),
        fall back to the tar+scp path.
      * ``"rsync"`` — rsync only (no fallback).
      * ``"scp"``   — tar+scp only (never invoke rsync).

    Both strategies honour ``bundle_excludes`` so they ship an identical tree.
    """
    mode = str(host_cfg.get("push_mode", "auto")).lower()
    excludes = host_cfg.get("bundle_excludes")
    if excludes is None:
        excludes = DEFAULT_BUNDLE_EXCLUDES
    if not isinstance(excludes, list):
        raise ValueError("[S14.6] 'bundle_excludes' must be a list of strings.")

    if mode == "scp":
        return _push_scp(host_cfg, local_dir, remote_dir,
                         config=config, repo_root=repo_root, excludes=excludes)

    if mode not in ("auto", "rsync"):
        raise ValueError(
            f"[S14.6] Unknown push_mode '{mode}'. Expected auto | rsync | scp."
        )

    try:
        rc = ssh_sync(host_cfg, local_dir, remote_dir,
                      config=config, repo_root=repo_root, excludes=excludes)
    except FileNotFoundError:
        # rsync binary missing on the CONTROL host.
        if mode == "rsync":
            _err("[S14.6] push_mode=rsync but 'rsync' is not installed on the control host.")
            raise
        _err("[S14.6] 'rsync' not found on the control host; falling back to tar+scp.")
        return _push_scp(host_cfg, local_dir, remote_dir,
                         config=config, repo_root=repo_root, excludes=excludes)

    if rc == 127 and mode == "auto":
        # 127 from ssh -> the remote could not find 'rsync'. Fall back.
        _err("[S14.6] remote 'rsync' not found (rc=127); falling back to tar+scp.")
        return _push_scp(host_cfg, local_dir, remote_dir,
                         config=config, repo_root=repo_root, excludes=excludes)
    return rc


def run_activation(
    host_cfg: dict,
    verb: str,
    *,
    config: dict,
    repo_root: Path,
    bundle_dir: str,
    remaining: Optional[list[str]] = None,
) -> int:
    """Run one contract *verb* on the target over ``ssh_exec``.

    The remote command is ``cd <bundle_dir> && <activation-cmd> [remaining...]``,
    passed as ONE argv element so the remote login shell parses the ``cd``/``&&``
    chain intact (same rule as the docker render-on-target path).
    """
    cmd = resolve_activation_command(host_cfg, verb)
    remote = f"cd {bundle_dir} && {cmd}"
    if remaining:
        remote += " " + " ".join(remaining)
    return ssh_exec(host_cfg, [remote], config=config, repo_root=repo_root)


def run_thin_up(
    host_cfg: dict,
    *,
    config: dict,
    repo_root: Path,
    bundle_dir: str,
    bootstrap: bool = False,
    rollback: bool = False,
    remaining: Optional[list[str]] = None,
) -> int:
    """Orchestrate ``ciu up --host <name> --thin`` (docker-optional).

    * ``--rollback``: run the ``rollback`` verb only (revert to previous
      release); no fresh push.
    * otherwise: **push** the bundle, then run ``bootstrap`` (only when
      ``--bootstrap`` is given — first-time host setup), then ``apply``.

    ``remaining`` (e.g. ``--profile apps``) is appended to the ``apply`` verb so
    the project's activation script can act on the selection.
    """
    if rollback:
        return run_activation(host_cfg, "rollback",
                              config=config, repo_root=repo_root, bundle_dir=bundle_dir)

    rc = push_bundle(host_cfg, str(repo_root), bundle_dir,
                     config=config, repo_root=repo_root)
    if rc != 0:
        return rc

    if bootstrap:
        rc = run_activation(host_cfg, "bootstrap",
                            config=config, repo_root=repo_root, bundle_dir=bundle_dir)
        if rc != 0:
            return rc

    return run_activation(host_cfg, "apply",
                          config=config, repo_root=repo_root,
                          bundle_dir=bundle_dir, remaining=remaining)
