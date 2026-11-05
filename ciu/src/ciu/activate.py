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
import re
import shlex
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

# Never pushed, whatever the host row says (CIU-175): ``.git`` is local state;
# ``.ciu`` holds the control host's materialised secrets (e.g. the registry
# read token); ``ciu.env`` is the control host's machine facts; ``vol-*`` are the
# control host's persistent stack data directories (``vol-<service>-<purpose>``,
# may hold credentials and keys; the target creates its own). Slash-less rsync
# patterns match files, directories and symlinks at any depth, so nested stacks
# are covered; :func:`make_tarball` applies the same matcher to the tar+scp
# fallback. NOT excluded: a rendered ``ciu.toml`` / ``ciu.compose.yml`` at a stack
# root (the target re-renders them) -- exclude those per host via ``bundle_excludes``
# when they carry values that must not leave the control host.
ALWAYS_BUNDLE_EXCLUDES = [".git", ".ciu", "ciu.env", "vol-*"]


def bundle_excludes_for(host_cfg: dict) -> list[str]:
    """The exclude list for EVERY bundle push: the mandatory set plus the host's
    ``bundle_excludes`` (a list of strings; the default adds nothing further)."""
    extra = host_cfg.get("bundle_excludes")
    if extra is None:
        extra = []
    if not isinstance(extra, list) or not all(isinstance(e, str) for e in extra):
        raise ValueError("[S14.6] 'bundle_excludes' must be a list of strings.")
    merged = list(ALWAYS_BUNDLE_EXCLUDES)
    merged += [e for e in extra if e not in merged]
    return merged

def remote_path_word(path: str, what: str = "path") -> str:
    """A shell word for a REMOTE *path*: quoted, but a leading ``~`` / ``~/`` is expanded
    deliberately (``"$HOME"``) instead of being suppressed by the quoting (CIU-169).

    ``~user`` forms are refused: the remote user's home is the only one this tool knows.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"[S14.6] {what} must be a non-empty string.")
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        return '"$HOME"' + shlex.quote(path[1:])
    if path.startswith("~"):
        raise ValueError(
            f"[S14.6] {what} {path!r}: '~user' forms are not supported; "
            "use '~/...' (the ssh user's home) or an absolute path."
        )
    return shlex.quote(path)


def bundle_dir_for(host_cfg: dict) -> str:
    """The validated ``bundle_dir`` of a host row (default ``/opt/ciu/current``).

    Whitespace is refused: rsync's destination argument is not shell-quoted, so a
    space would work for ``mkdir`` and break the transfer.
    """
    value = host_cfg.get("bundle_dir", "/opt/ciu/current")
    remote_path_word(value, "'bundle_dir'")
    if any(ch.isspace() for ch in value):
        raise ValueError(f"[S14.6] 'bundle_dir' {value!r} must not contain whitespace.")
    return value


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


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    """rsync-style glob to regex: ``*``/``?``/``[..]`` stop at ``/``, ``**`` crosses it."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if c == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[" and "]" in pattern[i + 2:]:
            end = pattern.index("]", i + 2)
            body = pattern[i + 1:end].replace("\\", "\\\\")
            out.append("[" + re.sub(r"^!", "^", body) + "]")
            i = end
        elif c == "\\" and i + 1 < len(pattern):
            i += 1
            out.append(re.escape(pattern[i]))
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


def _compile_excludes(excludes: list[str]) -> list[tuple["re.Pattern[str]", bool, bool]]:
    """``(regex, dir_only, match_full_path)`` per rsync ``--exclude`` pattern."""
    compiled = []
    for raw in excludes:
        dir_only = raw.endswith("/")
        pat = raw.rstrip("/")
        anchored = pat.startswith("/")
        pat = pat.lstrip("/")
        compiled.append((_glob_regex(pat), dir_only, anchored or "/" in pat))
    return compiled


def _is_excluded(segments: list[str], is_dir: bool, compiled) -> bool:
    """rsync semantics: a pattern hitting the path or any ancestor directory excludes it."""
    for depth in range(1, len(segments) + 1):
        node_is_dir = is_dir or depth < len(segments)
        for regex, dir_only, full in compiled:
            if dir_only and not node_is_dir:
                continue
            target = "/".join(segments[:depth]) if full else segments[depth - 1]
            if regex.match(target):
                return True
    return False


def make_tarball(local_dir: str, *, excludes: Optional[list[str]] = None) -> str:
    """tar.gz *local_dir*'s contents into a temp file; return its path.

    Members are stored **relative to** ``local_dir`` (arcname ".") so that a
    remote ``tar xzf … -C <bundle_dir>`` reproduces the tree directly under
    ``bundle_dir`` — matching rsync's ``<src>/ -> <dst>/`` content semantics.
    ``excludes`` use rsync ``--exclude`` pattern semantics (slash-less patterns at
    any depth, a trailing ``/`` for directories only, a leading or inner ``/`` for
    a path from the bundle root, ``*``/``?``/``[..]``/``**`` globs), so both push
    strategies ship the same file set (the equivalence is tested against real rsync).
    """
    compiled = _compile_excludes(excludes or [])
    root = Path(local_dir).resolve()

    def _filter(ti: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
        # ti.name is relative to arcname "." e.g. "./.git/config" or ".git/config".
        # Strip exactly the leading "./" prefix — NOT with str.lstrip("./"), which
        # deletes *every* leading '.'/'/' char and would mangle dotpaths
        # ("./.git" -> "git"), silently defeating the default ".git" exclude and
        # shipping a different tree than rsync (SPEC S14.6a: "identical tree").
        name = ti.name
        if name in (".", "./"):
            return ti
        rel = name[2:] if name.startswith("./") else name
        if not rel:
            return ti
        if _is_excluded(rel.split("/"), ti.isdir(), compiled):
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
    dir_word = remote_path_word(remote_dir_clean, "bundle_dir")
    tar_word = remote_path_word(remote_tar, "bundle_dir")
    try:
        rc = ssh_exec(host_cfg, [f"mkdir -p {dir_word}"],
                      config=config, repo_root=repo_root)
        if rc != 0:
            _err(f"[S14.6] Could not create remote bundle_dir '{remote_dir_clean}' (rc={rc}).")
            return rc
        rc = scp_file(host_cfg, tarball, remote_tar, config=config, repo_root=repo_root)
        if rc != 0:
            _err(f"[S14.6] scp of the bundle failed (rc={rc}).")
            return rc
        extract = (
            f"tar xzf {tar_word} -C {dir_word} && rm -f {tar_word}"
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
    excludes = bundle_excludes_for(host_cfg)
    bundle_dir_for({"bundle_dir": remote_dir})  # validate before anything is sent

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
    remote = f"cd {remote_path_word(bundle_dir, 'bundle_dir')} && {cmd}"
    if remaining:
        remote += " " + shlex.join(remaining)
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
