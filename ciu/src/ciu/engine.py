#!/usr/bin/env python3
"""CIU v2 engine — S8.3 integration pipeline.

This module is the cutover (packet P9): it wires the already-landed v2
building blocks into the 17-step pipeline mandated by SPEC §S8.3.

Step → module map (S8.3):
   1 load env ............ workspace_env.bootstrap_workspace_env (S2)
   2 render global chain . config_model.render_global_chain (S3.3)
   3 render stack ........ config_model.render_stack (S3.1/S3.4)
   4 merge ............... config_model.deep_merge (S3.3)
   5 validate ............ config_model.validate_stack_shape + secrets.discover
                           + secrets.find_misplaced + gitignore + fqdn/certs (S11)
   6 reset ............... reset_service (S6.4) — optional
   7 auto-generate ....... auto_generate_values (S3.9)
   8 hostdirs ............ create_hostdirs (S6) + DooD preflight (S1.5)
   9 pre_secrets hooks ... hooks_runner.run_hooks (S9 / S8.3)
  10 secrets ............. materialize.materialize + providers.* (S4)
  11 pre_compose hooks ... hooks_runner.run_hooks
  12 configfiles ......... composefile.render_configfiles (S5)
  13 render compose ...... composefile.guard_config + render_compose (S4.21)
  14 leak scan ........... composefile.leak_scan + validate_consumption (S4.20/S4.22)
  15 overlay ............. composefile.generate_overlay (S4.17/S8.1)
  16 compose up .......... composefile.compose_* + procutil.run_cmd (S8.1)
  17 post_compose hooks .. hooks_runner.run_hooks

Most v1 logic (template rendering, secret resolution, hook loading, vault I/O,
flatten/env-build, registry auth, cert validation) has been DELETED here and now
lives in the dedicated modules listed above (see SPEC Appendix A / C).

P10 removed the last two transitional shims (``render_global_config_chain`` /
``render_stack_config``): the v2 ``deploy.py`` and tests call
``config_model.render_global_chain`` / ``config_model.render_stack`` directly,
and ``render_utils.py`` is deleted.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from . import config_model
from . import composefile
from . import governance
from . import hooks_runner
from . import hosts
from . import procutil
from . import worktree
from .deploy_pkg import health as _health
from .config_constants import (
    CIU_COMPOSE_OUTPUT,
    CIU_COMPOSE_TEMPLATE,
    GLOBAL_CONFIG_DEFAULTS,
    GLOBAL_CONFIG_RENDERED,
    MACHINE_DIR,
    OVERLAY_NAME,
    RENDERED_SUBDIR,
    SECRETS_SUBDIR,
    SHIPPED_COMPOSE,
    STACK_CONFIG_RENDERED,
)
from .cli_utils import get_cli_version
from .paths import to_physical_path
from .secrets import directives as secret_directives
from .secrets import materialize as secret_materialize
from .secrets.providers import VaultError, VaultKV2, resolve_vault_token, vault_addr_from_config
from .workspace_env import (  # P8 contract — relied on exactly, never edited here.
    REQUIRED_KEYS_CORE,
    WorkspaceEnvError,
    bootstrap_env_init,
    bootstrap_workspace_env,
    enforce_standalone_root,
    ensure_workspace_network,
    generated_facts_path,
    read_generated_facts,
    resolve_env_root,
    validate_required_certs,
)

# Global logger instance (configured after parsing config).
logger = logging.getLogger(__name__)


# ===========================================================================
# A. KEPT helpers (ported, mostly unchanged)
# ===========================================================================


def check_runtime_dependencies() -> None:
    """Validate that required runtime dependencies are installed."""
    if os.getenv("SKIP_DEPENDENCY_CHECK") == "1":
        return

    print("[INFO] Validating runtime dependencies...", flush=True)
    missing_deps: list[tuple[str, str, str]] = []
    warnings: list[tuple[str, str, str, str]] = []

    try:
        import tomllib  # noqa: F401
        import pathlib  # noqa: F401
        import subprocess as _sp  # noqa: F401
        import json as _json  # noqa: F401
        import hashlib as _hl  # noqa: F401
    except ImportError as e:
        missing_deps.append(
            ("python-stdlib", f"Python standard library ({e.name})", "Upgrade Python to 3.11+")
        )

    try:
        # timeout 30s (not 5s): on slow/contended storage the docker CLI can be
        # slow to cold-load; a tight timeout produces false "missing dependency"
        # failures. Set SKIP_DEPENDENCY_CHECK=1 to bypass entirely.
        result = procutil.run_cmd(["docker", "--version"], timeout=30, check=False)
        if result.returncode != 0:
            missing_deps.append(("docker", "Docker Engine", "https://docs.docker.com/engine/install/"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing_deps.append(("docker", "Docker Engine", "https://docs.docker.com/engine/install/"))

    try:
        # timeout 30s (not 5s): the compose v2 plugin is a ~60MB binary; on slow
        # storage a cold load under I/O contention can exceed a tight timeout and
        # be misreported as missing. SKIP_DEPENDENCY_CHECK=1 bypasses this.
        result = procutil.run_cmd(["docker", "compose", "version"], timeout=30, check=False)
        if result.returncode != 0:
            missing_deps.append(("docker compose", "Docker Compose v2", "https://docs.docker.com/compose/install/"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing_deps.append(("docker compose", "Docker Compose v2", "https://docs.docker.com/compose/install/"))

    # hvac (Vault client) — OPTIONAL note only. v2 uses urllib, not hvac, so
    # this is informational; the directive names that referenced it are gone.
    try:
        import hvac  # noqa: F401
    except ImportError:
        warnings.append(
            ("hvac", "Vault client library", "pip install hvac",
             "Optional; CIU v2 talks to Vault over urllib and does not require it"),
        )

    try:
        import jinja2  # noqa: F401
    except ImportError:
        missing_deps.append(("jinja2", "Jinja2 template engine", "pip install jinja2"))

    try:
        import tomli_w  # noqa: F401
    except ImportError:
        missing_deps.append(("tomli_w", "TOML writer library", "pip install tomli_w"))

    try:
        import yaml  # noqa: F401
    except ImportError:
        missing_deps.append(("pyyaml", "PyYAML (overlay generation)", "pip install pyyaml"))

    if missing_deps:
        print("[ERROR] Missing required dependencies:", flush=True)
        for cmd, name, install_info in missing_deps:
            print(f"  [X] {name} ({cmd})", flush=True)
            print(f"     Install: {install_info}", flush=True)
        print("\n[ERROR] Cannot continue without required dependencies", flush=True)
        raise DependencyError("missing required runtime dependencies")

    if warnings:
        print("[WARN] Optional dependencies missing:", flush=True)
        for cmd, name, install_cmd, note in warnings:
            print(f"  [!] {name} ({cmd})", flush=True)
            print(f"     Install: {install_cmd}", flush=True)
            print(f"     Note: {note}", flush=True)
        print("", flush=True)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure the logging module with the specified level."""
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    level = level_map.get(log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s", force=True)
    logger.setLevel(level)
    logger.info(f"Logging configured: {log_level.upper()}")


def get_git_hash() -> str:
    """Return the current git commit hash (short, 8 chars), with -dirty suffix."""
    try:
        result = procutil.run_cmd(["git", "rev-parse", "--short=8", "HEAD"], check=True)
        git_hash = result.stdout.strip()
        result = procutil.run_cmd(["git", "status", "--porcelain"], check=True)
        is_dirty = len(result.stdout.strip()) > 0
        return f"{git_hash}{'-dirty' if is_dirty else ''}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "dev"


IMAGE_REVISION_LABEL = "org.opencontainers.image.revision"


def bake_revision_args() -> list[str]:
    """``docker buildx bake --set`` args stamping the source revision onto every
    baked image.

    WHY: without it, a running container cannot be traced back to the commit it
    was built from, so any live/e2e/integration result is evidence about an
    UNKNOWN artifact — the result reads as a fact about the code when it is
    really a fact about whatever image happened to be deployed. Stamping is the
    cheap half; a deploy- or test-time check comparing this label against the
    commit under test is the half that makes it enforceable (see
    docs/DESIGN-NOTES.md D7, "a provenance precondition").

    ``get_git_hash()`` already appends ``-dirty`` for an unclean tree, and that
    suffix is the load-bearing part: a dirty build must never claim a clean
    commit, or the label becomes a more convincing version of the same lie.

    Returns ``[]`` when the revision is unknown (``"dev"`` — no git, or git
    unavailable), so a non-git checkout bakes exactly as before. Stamping
    ``revision=dev`` would be worse than stamping nothing: it looks like an
    answer.
    """
    rev = get_git_hash()
    if rev == "dev":
        return []
    return ["--set", f"*.labels.{IMAGE_REVISION_LABEL}={rev}"]


def get_timestamp() -> str:
    """Return the current timestamp in ISO 8601 format (UTC)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def auto_generate_values(config: dict) -> dict:
    """S3.9 — compute build metadata and expose UID/GID to templates.

    B7 fix: ``container_gid if container_gid is not None and container_gid != ''
    else docker_gid`` — GID 0 is valid (falsy-safe, never truthiness).
    """
    config.setdefault("auto_generated", {})
    config["auto_generated"]["build_version"] = get_git_hash()
    config["auto_generated"]["build_time"] = get_timestamp()

    deploy_shared = config.get("deploy", {}).get("env", {}).get("shared", {})
    container_uid = deploy_shared.get("CONTAINER_UID")
    container_gid = deploy_shared.get("CONTAINER_GID")
    docker_gid = deploy_shared.get("DOCKER_GID")

    # B7 / S2.5: 0 is valid; only None / "" mean unset.
    def _unset(v: Any) -> bool:
        return v is None or v == ""

    if _unset(container_uid) or _unset(docker_gid):
        raise ValueError(
            "[ERROR] Missing required deploy.env.shared values for hostdir ownership. "
            "Ensure CONTAINER_UID and DOCKER_GID are set via ciu.env."
        )

    config["auto_generated"]["uid"] = container_uid
    config["auto_generated"]["gid"] = (
        container_gid if (container_gid is not None and container_gid != "") else docker_gid
    )
    config["auto_generated"]["docker_gid"] = docker_gid
    return config


# ===========================================================================
# Exit-code exception taxonomy (S10.3)
#   WorkspaceEnvError / DependencyError / DooDPreflightError → 3
#   ValueError (validation, [S...]) / argparse                → 2
#   SecretLeakError / VaultError / compose / hook failure     → 1
# ===========================================================================


class DependencyError(RuntimeError):
    """Missing runtime dependency (exit 3)."""


class DooDPreflightError(RuntimeError):
    """PHYSICAL_REPO_ROOT not reachable by the docker daemon (S1.5, exit 3)."""


class ComposeError(RuntimeError):
    """docker compose up failed (exit 1)."""


# ===========================================================================
# CLI parsing (S10.1)
# ===========================================================================


def parse_arguments(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse command-line arguments for ``ciu`` (the non-subcommand surface)."""
    parser = argparse.ArgumentParser(
        description=f"CIU {get_cli_version()}: TOML-based Docker Compose orchestration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start service in current directory
  %(prog)s

  # Start service in a specific directory
  %(prog)s -d /srv/postgres

  # Dry run with context printing (debugging)
  %(prog)s --dry-run --print-context

  # Reset service to fresh state, also deleting secret files
  %(prog)s --reset --secrets -y

  # Secret lifecycle
  %(prog)s secrets list -d /srv/postgres
  %(prog)s secrets reset --name redis_password -y
""",
    )

    parser.add_argument("-d", "--dir", type=Path, default=Path.cwd(), metavar="PATH",
                        help="Working directory containing service files (default: current directory)")
    parser.add_argument("-f", "--file", type=str, default=CIU_COMPOSE_TEMPLATE, metavar="NAME",
                        help=f"Compose template name (default: {CIU_COMPOSE_TEMPLATE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run everything except docker compose up (S8.3)")
    parser.add_argument("--print-context", action="store_true",
                        help="Print merged configuration as JSON with secrets redacted (S4.23)")
    parser.add_argument("--render-toml", action="store_true",
                        help="Render ciu.toml from templates and stop (S8.3 step 3)")
    parser.add_argument("--define-root", type=Path, default=None, metavar="PATH",
                        help="Override repository root directory (no parent walking)")
    parser.add_argument("--root-folder", dest="define_root", type=Path, default=None, metavar="PATH",
                        help="Alias for --define-root")
    parser.add_argument("--skip-hostdir-check", action="store_true",
                        help="Skip hostdir creation/validation (cleanup mode)")
    parser.add_argument("--skip-hooks", action="store_true",
                        help="Skip pre_secrets/pre_compose/post_compose hooks (cleanup mode)")
    parser.add_argument("--skip-secrets", action="store_true",
                        help="Skip secret materialization and overlay generation (cleanup mode)")

    network_group = parser.add_mutually_exclusive_group()
    network_group.add_argument("--auto-connect-network", dest="auto_connect_network",
                               action="store_true", default=None,
                               help="Auto-connect devcontainer to DOCKER_NETWORK_INTERNAL (override config)")
    network_group.add_argument("--no-auto-connect-network", dest="auto_connect_network",
                               action="store_false", default=None,
                               help="Disable devcontainer network auto-connect (override config)")

    parser.add_argument("--generate-env", action="store_true",
                        help="Generate ciu.env with autodetected values (S2.8 bootstrap)")
    parser.add_argument("--version", action="version", version=f"ciu {get_cli_version()}")
    parser.add_argument("--update-cert-permission", action="store_true",
                        help="Update Let's Encrypt cert permissions (requires root)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Non-interactive mode (auto-confirm all prompts)")
    parser.add_argument("--reset", action="store_true",
                        help="Clean service to fresh state (containers, volumes, rendered configs)")
    parser.add_argument("--secrets", action="store_true",
                        help="With --reset: also delete the stack's secret store files (S4.25)")
    parser.add_argument("--shipped", action="store_true",
                        help=f"Run a maintainer's pre-shipped {SHIPPED_COMPOSE} through CIU "
                             f"(env+network+DooD preflight, no secrets/overlay; S8.5). "
                             f"Override the file with -f.")

    return parser.parse_args(argv)


def _build_secrets_subparser() -> argparse.ArgumentParser:
    """Argparse for the ``ciu secrets list|reset`` subcommand (S4.25)."""
    parser = argparse.ArgumentParser(prog="ciu secrets",
                                     description="CIU secret lifecycle commands (S4.25)")
    parser.add_argument("action", choices=["list", "reset"], help="list or reset secret store files")
    parser.add_argument("-d", "--dir", type=Path, default=Path.cwd(), metavar="PATH",
                        help="Stack directory (default: current directory)")
    parser.add_argument("--define-root", type=Path, default=None, metavar="PATH",
                        help="Override repository root directory")
    parser.add_argument("--root-folder", dest="define_root", type=Path, default=None, metavar="PATH",
                        help="Alias for --define-root")
    parser.add_argument("--name", type=str, default=None, metavar="NAME",
                        help="Limit the action to a single secret name (reset only)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip the reset confirmation prompt")
    return parser


# ===========================================================================
# C. HOSTDIRS v2 (S6)
# ===========================================================================


def privileged_fs_op(physical_path: Path, op: str, *args: str) -> None:
    """Run a chown/chmod inside a one-shot ``alpine`` helper container (S6.5).

    The daemon has root even when the operator does not, so a PermissionError
    on a direct os.chown/os.chmod is recovered by mounting the physical path
    into alpine and running the same operation there. Module-level so tests can
    monkeypatch it.

    *op* is ``"chown"`` or ``"chmod"``; *args* are the tool arguments preceding
    the mounted ``/t`` target (e.g. ``"0:994"`` or ``"775"``).
    """
    procutil.docker(
        ["run", "--rm", "-v", f"{physical_path}:/t", "alpine", op, *args, "/t"],
        check=True,
    )


def privileged_rmtree(physical_path: Path) -> None:
    """Remove a directory tree (including itself) via a root helper container (S6.5).

    The deletion mirror of :func:`privileged_fs_op`. ``--reset`` (S6.4) must wipe
    hostdirs that hold image-UID-owned data — postgres uid 999, pgAdmin 5050 and
    friends provisioned per S6.7 Pattern (a) — but the unprivileged operator
    cannot recurse the ``0700`` subtrees those entrypoints create, so a host-side
    ``shutil.rmtree`` dies on ``Permission denied``. The daemon is root, so we
    mount the target's PARENT and ``rm -rf`` the named child: the directory and
    all of its contents go regardless of owner. Module-level so tests can
    monkeypatch it.
    """
    procutil.docker(
        ["run", "--rm", "-v", f"{physical_path.parent}:/t", "alpine",
         "rm", "-rf", f"/t/{physical_path.name}"],
        check=True,
    )


def _rmtree_with_fallback(vol_dir: Path, *, repo_root: Optional[Path]) -> None:
    """Remove *vol_dir*, routing through the S6.5 root helper whenever DooD applies (CIU-9).

    Resolve the physical path (S1.4) FIRST. In a DooD context
    (``to_physical_path(vol_dir) != vol_dir`` — S1.4/S1.9) the operator's local
    filesystem view of ``vol_dir`` is not necessarily the same directory the
    Docker daemon actually bind-mounted; a local ``shutil.rmtree`` success on
    the logical path proves nothing about the physical path's state (it can
    silently no-op the removal the daemon-visible volume needed). So in DooD,
    removal ALWAYS goes through :func:`privileged_rmtree` against the physical
    path, unconditionally — the local attempt is skipped entirely, not just
    used as an optimistic first try.

    On a native host (S1.9, logical == physical) a local ``shutil.rmtree``
    accurately reflects daemon-visible state, so it is used directly; a
    ``PermissionError`` there (image-UID-owned data — postgres 999, pgAdmin
    5050, S6.7 Pattern (a)) still falls back to the same root helper. A
    partial rmtree (some entries removed before hitting an image-owned
    ``0700`` subdir) is fine — the helper's ``rm -rf`` cleans whatever
    remains.
    """
    try:
        physical = to_physical_path(vol_dir, repo_root=repo_root)
    except ValueError:
        # No DooD context resolvable (REPO_ROOT/PHYSICAL_REPO_ROOT unset) —
        # treat as native host: logical is all we have.
        physical = vol_dir

    if physical != vol_dir:
        # DooD: the daemon's view differs from ours. Route through the root
        # helper unconditionally (S6.5) — never attempt the local rmtree.
        print(
            f"[INFO]     DooD context ({vol_dir} -> {physical}); "
            "removing via root helper container (S6.5)",
            flush=True,
        )
        privileged_rmtree(physical)
        return

    # Native host (S1.9): logical == physical, so a local rmtree is trustworthy.
    try:
        shutil.rmtree(vol_dir)
        return
    except PermissionError:
        pass
    print(
        f"[INFO]     Operator lacks privilege over {vol_dir}; "
        "removing via root helper container (S6.5)",
        flush=True,
    )
    privileged_rmtree(physical)


def _chown_with_fallback(
    path: Path, uid: int, gid: int, *, physical_path: Path, chown_fn: Optional[Callable]
) -> None:
    """os.chown; on PermissionError fall back to the helper container (S6.5)."""
    if chown_fn is not None:
        chown_fn(path, uid, gid)
        return
    try:
        os.chown(path, uid, gid)
    except PermissionError:
        privileged_fs_op(physical_path, "chown", f"{uid}:{gid}")


def _chmod_with_fallback(path: Path, mode: int, *, physical_path: Path) -> None:
    """os.chmod; on PermissionError fall back to the helper container (S6.5).

    Unlike :func:`_chown_with_fallback`, chmod has no test-seam override: mode is
    always applied via ``os.chmod`` (which succeeds on real test temp dirs) and
    only escalates to the privileged helper on a genuine PermissionError.
    """
    try:
        os.chmod(path, mode)
    except PermissionError:
        privileged_fs_op(physical_path, "chmod", format(mode, "o"))


def create_hostdirs(
    config: dict,
    stack_dir: Path,
    *,
    repo_root: Path,
    physical_root: Optional[Path] = None,
    chown_fn: Optional[Callable] = None,
) -> dict:
    """Create & own hostdirs per S6; rewrite each value to its absolute physical path.

    Walks every ``[<root>...hostdir]`` table (S6.1). Each value is either a
    string ("" → auto ``<stack>/vol-<service.name>-<purpose>``; non-empty path
    used as given, absolute allowed) or an inline table
    ``{path?, uid?, gid?, mode?, seed?}`` overriding the S6.3 defaults per dir.

    After creation the value is rewritten to the **absolute physical path**
    (S6.2) so templates emit it directly. Directories are created mode 0775,
    owner ``CONTAINER_UID:DOCKER_GID`` (S6.3; falsy-safe ints — 0 valid).
    Pre-existing dirs with compatible ownership pass; incompatible aborts
    listing the observed owner/group/mode. Ownership ops degrade to a one-shot
    helper container on PermissionError (S6.5). ``seed`` copies a tree on FIRST
    creation only (S6.6).

    *chown_fn* (test seam) replaces the direct+fallback ownership applier; when
    given it is called as ``chown_fn(path, uid, gid)`` and may raise to simulate
    failures.
    """
    stack_dir = Path(stack_dir).resolve()
    repo_root = Path(repo_root)

    deploy_shared = config.get("deploy", {}).get("env", {}).get("shared", {})
    auto = config.get("auto_generated", {})

    def _int_or_none(*candidates: Any) -> Optional[int]:
        for c in candidates:
            if c is None or c == "":
                continue
            try:
                return int(c)
            except (TypeError, ValueError):
                continue
        return None

    default_uid = _int_or_none(deploy_shared.get("CONTAINER_UID"), auto.get("uid"))
    default_gid = _int_or_none(deploy_shared.get("DOCKER_GID"), auto.get("docker_gid"))

    if default_uid is None or default_gid is None:
        raise ValueError(
            "CONTAINER_UID/DOCKER_GID not found in config - "
            "ensure ciu.env is loaded before running CIU"
        )

    print(f"[INFO] Scanning for volume directories (UID:{default_uid}, GID:{default_gid})...", flush=True)
    created_count = 0

    def _to_physical(p: Path) -> Path:
        return to_physical_path(p, repo_root=repo_root, physical_root=physical_root)

    def _apply_ownership(path: Path, uid: int, gid: int, mode: int) -> None:
        physical = _to_physical(path)
        if chown_fn is not None:
            chown_fn(path, uid, gid)
        else:
            _chown_with_fallback(path, uid, gid, physical_path=physical, chown_fn=None)
        _chmod_with_fallback(path, mode, physical_path=physical)

    def _seed(path: Path, seed_rel: str, uid: int, gid: int, mode: int) -> None:
        src = (stack_dir / seed_rel).resolve()
        if not src.exists():
            raise ValueError(
                f"[S6.6] hostdir seed directory not found: {src} "
                f"(relative to stack dir {stack_dir})"
            )
        shutil.copytree(src, path, dirs_exist_ok=True)
        _apply_ownership(path, uid, gid, mode)

    def _create_dir(path: Path, uid: int, gid: int, mode: int, seed_rel: Optional[str]) -> None:
        nonlocal created_count
        existed = path.exists()
        if existed and not path.is_dir():
            raise ValueError(f"[S6.3] Path exists and is not a directory: {path}")

        if existed:
            st = path.stat()
            cur_mode = stat.S_IMODE(st.st_mode)
            compatible = (st.st_uid == uid and st.st_gid == gid) or (
                st.st_gid == gid and (cur_mode & 0o020)
            )
            if compatible:
                print(
                    f"[INFO]   Exists with compatible ownership: {path} "
                    f"(owner={st.st_uid}, group={st.st_gid}, mode={oct(cur_mode)})",
                    flush=True,
                )
                return
            raise ValueError(
                f"[S6.3] Existing hostdir has incompatible ownership/permissions: {path} "
                f"(observed owner={st.st_uid}, group={st.st_gid}, mode={oct(cur_mode)}; "
                f"expected owner {uid}, group {gid}). Fix ownership or remove the directory."
            )

        path.mkdir(mode=mode, parents=True, exist_ok=True)
        if seed_rel:
            _seed(path, seed_rel, uid, gid, mode)
        _apply_ownership(path, uid, gid, mode)
        print(f"[INFO]   Created: {path} ({uid}:{gid}, {oct(mode)})", flush=True)
        created_count += 1

    def _resolve_entry(purpose: str, value: Any, service_name: Optional[str]) -> tuple[Path, int, int, int, Optional[str]]:
        uid, gid, mode, seed_rel = default_uid, default_gid, 0o775, None
        if isinstance(value, dict):
            raw_path = value.get("path", "")
            o_uid = _int_or_none(value.get("uid"))
            o_gid = _int_or_none(value.get("gid"))
            if o_uid is not None:
                uid = o_uid
            if o_gid is not None:
                gid = o_gid
            if value.get("mode"):
                mode = int(str(value["mode"]), 8)
            seed_rel = value.get("seed") or None
        else:
            raw_path = value

        if not raw_path:
            # `_scan_section` rejects this combination before reaching the
            # resolver; the service name is therefore present for auto paths.
            path = stack_dir / f"vol-{service_name}-{purpose}"
        else:
            path = Path(raw_path)
            if not path.is_absolute():
                path = stack_dir / path
        return path.resolve(), uid, gid, mode, seed_rel

    def _scan_section(section: dict) -> None:
        hostdir = section.get("hostdir")
        if isinstance(hostdir, dict):
            service_name = section.get("name")
            if not service_name and any(
                (isinstance(v, str) and not v) or (isinstance(v, dict) and not v.get("path"))
                for v in hostdir.values()
            ):
                raise ValueError(
                    "[ERROR] hostdir section found without service name. "
                    "Add 'name' to the parent section so CIU can generate hostdir paths."
                )
            for purpose, value in hostdir.items():
                if not isinstance(value, (str, dict)):
                    raise ValueError(
                        f"[S6.1] hostdir '{purpose}' must be a path string or inline table, "
                        f"got {type(value).__name__}"
                    )
                path, uid, gid, mode, seed_rel = _resolve_entry(purpose, value, service_name)
                _create_dir(path, uid, gid, mode, seed_rel)
                # S6.2: rewrite to absolute physical path string for templates.
                hostdir[purpose] = str(_to_physical(path))

        for value in section.values():
            if isinstance(value, dict):
                _scan_section(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _scan_section(item)

    _scan_section(config)

    if created_count:
        print(f"[INFO] Created {created_count} volume directories", flush=True)
    else:
        print("[INFO] No volume directories to create", flush=True)
    return config


# ===========================================================================
# D. RESET v2 (S6.4 / S4.25)
# ===========================================================================


def reset_service(
    config: dict,
    stack_dir: Path,
    *,
    compose_file: str = CIU_COMPOSE_TEMPLATE,
    remove_secrets: bool = False,
    assume_yes: bool = False,
    specs: Optional[list] = None,
    repo_root: Optional[Path] = None,
) -> None:
    """Reset one stack to a fresh state (S6.4); raises RuntimeError on hard failure.

    Steps:
      1. ``docker compose down -v`` (with the overlay ``-f`` when it exists),
         scoped by ``-p`` AND ``--project-directory`` (CIU-71) so a relative
         path in an overlay resolves the same way ``up`` resolved it.
      2. Remove ``<stack>/vol-*`` directories — resolved against the STACK DIR,
         never the process cwd (B14 / S6.4).
      3. Remove rendered ``ciu.compose.yml`` + ``ciu.toml`` + ``.ciu/rendered/``
         + the overlay.
      4. Orphan cleanup via the anchored label filter
         ``<prefix>.component=<service>`` (docker ps label equality, S6.4).

    *repo_root* is optional at the type level (kept for the ValueError below
    to name it explicitly) but effectively REQUIRED at runtime: every real
    caller (``main_execution``'s reset step, ``deploy.py``'s reset path)
    already resolves and passes it, and step 1's ``--project-directory``
    raises loudly rather than silently omitting the flag when it is absent.

    Secret store files are KEPT unless *remove_secrets* (S4.25), in which case
    materialize.reset_secrets semantics apply (rm the stack/project store files).
    """
    stack_dir = Path(stack_dir).resolve()
    deployment = config.get("deploy", {})
    project_name = deployment.get("project_name")
    label_prefix = deployment.get("labels", {}).get("prefix")

    if not project_name:
        raise ValueError("deploy.project_name is required for reset")
    if not label_prefix:
        raise ValueError("deploy.labels.prefix is required for reset")

    service_name = stack_dir.name
    print(f"[INFO] Resetting service: {service_name} (project: {project_name})", flush=True)

    overlay_path = stack_dir / MACHINE_DIR / OVERLAY_NAME
    ownership_path = stack_dir / MACHINE_DIR / OWNERSHIP_OVERLAY_NAME

    # Step 1: docker compose down -v --remove-orphans (with overlay args when
    # present). --remove-orphans tears down one-shot init/sidecar containers
    # declared in the project but absent from the current compose selection;
    # without it an exited *-init sidecar lingers and pins the project's named
    # volumes through teardown (CIU-3, S6.4).
    print("[INFO]   Step 1/4: Stopping containers and removing volumes...", flush=True)
    # S8.7: down under the scoped project (matches what `up` created). CIU-46
    # cutover: when the config lacks the naming pair, derive the
    # workspace-identity project from THIS checkout's ciu.env — the same name
    # a config-less `up` passed. There is no -p-less compose invocation left
    # in CIU; the withdrawn basename fallback could never be enumerated by
    # clean and collided across checkouts.
    # S8.7 / CIU-46 cutover: ONE compose project name drives the down `-p`
    # AND step 4's orphan-sweep scope — they can never disagree.
    try:
        compose_project = compose_project_name(config, stack_dir)
    except ValueError:
        if repo_root is None:
            raise ValueError(
                "[S8.7] deploy.project_name/environment_tag not set and no "
                "repo_root was provided to derive the workspace-identity "
                "compose project; set both tags or pass repo_root."
            )
        compose_project = identity_compose_project_name(Path(repo_root), stack_dir)
    # CIU-71: --project-directory is required unconditionally, independent of
    # which branch above resolved compose_project — down's relative paths
    # (e.g. a build.context restated by an overlay) must resolve against the
    # same repo root `up` used, not this compose file's own directory.
    if repo_root is None:
        raise ValueError(
            "[CIU-71] repo_root is required to invoke docker compose with "
            "--project-directory (a stack's relative paths, build.context "
            "included, must resolve against the repo root, not this compose "
            "file's own directory); pass repo_root."
        )
    down_cmd = [
        "docker", "compose", "-p", compose_project,
        "--project-directory", str(Path(repo_root).resolve()),
        "-f", CIU_COMPOSE_OUTPUT,
    ]
    if overlay_path.exists():
        down_cmd += ["-f", f"{MACHINE_DIR}/{OVERLAY_NAME}"]
    # S16.9: the same file set `up` composed with, so `down` addresses exactly
    # the project `up` created (a fragment silently dropped here would leave
    # compose reasoning about a different merged model than the one running).
    if ownership_path.exists():
        down_cmd += ["-f", f"{MACHINE_DIR}/{OWNERSHIP_OVERLAY_NAME}"]
    down_cmd += ["down", "-v", "--remove-orphans"]
    try:
        result = procutil.run_cmd(down_cmd, check=False)
        if result.returncode != 0:
            print(f"[WARN]   docker compose down failed (may be OK if no containers): {result.stderr}", flush=True)
        else:
            print("[INFO]   Containers stopped, volumes removed", flush=True)
    except FileNotFoundError as e:
        raise RuntimeError(f"docker not available for reset: {e}") from e

    # Step 2: remove vol-* dirs of THIS stack dir (B14). Fixed-UID images write
    # data owned by the image UID (postgres 999, pgAdmin 5050 — S6.7 Pattern (a)),
    # which the unprivileged operator cannot rmtree; on PermissionError we degrade
    # to the S6.5 root helper container so the wipe completes instead of aborting.
    print("[INFO]   Step 2/4: Removing host-mounted volume directories...", flush=True)
    removed = 0
    for vol_dir in stack_dir.glob("vol-*"):
        if vol_dir.is_dir():
            _rmtree_with_fallback(vol_dir, repo_root=repo_root)
            print(f"[INFO]     Removed: {vol_dir}", flush=True)
            removed += 1
    print(f"[INFO]   Removed {removed} volume directories" if removed else "[INFO]   No volume directories to remove", flush=True)

    # Step 3: remove rendered outputs + overlay + rendered configfiles.
    print("[INFO]   Step 3/4: Removing generated configuration files...", flush=True)
    targets = [
        stack_dir / CIU_COMPOSE_OUTPUT,
        stack_dir / STACK_CONFIG_RENDERED,
        overlay_path,
        ownership_path,
    ]
    removed = 0
    for f in targets:
        if f.exists():
            f.unlink()
            print(f"[INFO]     Removed: {f}", flush=True)
            removed += 1
    rendered_dir = stack_dir / MACHINE_DIR / RENDERED_SUBDIR
    if rendered_dir.exists():
        shutil.rmtree(rendered_dir)
        print(f"[INFO]     Removed: {rendered_dir}", flush=True)
        removed += 1
    print(f"[INFO]   Removed {removed} configuration artifacts" if removed else "[INFO]   No configuration files to remove", flush=True)

    # Optional: remove secret store files (S4.25).
    if remove_secrets:
        print("[INFO]   Removing secret store files (--secrets)...", flush=True)
        if specs is not None and repo_root is not None:
            deleted = secret_materialize.reset_secrets(stack_dir, Path(repo_root), specs)
            for d in deleted:
                print(f"[INFO]     Removed secret store: {d}", flush=True)
        # Fallback for callers without specs: drop the per-stack secrets dir.
        stack_secrets = stack_dir / MACHINE_DIR / SECRETS_SUBDIR
        if stack_secrets.exists():
            shutil.rmtree(stack_secrets)
            print(f"[INFO]     Removed secret store dir: {stack_secrets}", flush=True)

    # Step 4: orphan cleanup with anchored label equality filter (S6.4).
    #
    # CIU-19: the component filter alone is NOT instance-scoped. A second
    # checkout of the same repo (a worktree instance, S16) labels its containers
    # with the SAME `<prefix>.component=<service>` — only the compose project
    # name carries the instance id. Filtering on the component alone therefore
    # matched every instance on the host, so `ciu clean` in one instance deleted
    # the same-named service out of ALL of them. Observed live: cleaning a
    # worktree instance removed the PRIMARY instance's db-init container.
    #
    # Both filters are ANDed by docker, and `compose_project_name` is the same
    # value used for `down -p` a few lines above — so this scopes the sweep to
    # exactly the containers this reset already owns.
    print("[INFO]   Step 4/4: Cleaning orphaned containers...", flush=True)
    label_filter = f"label={label_prefix}.component={service_name}"
    # Always scoped to the SAME compose project the down above used (CIU-19's
    # lesson: a component-only sweep reaches into every instance on the host).
    extra_filters = [
        "--filter", f"label=com.docker.compose.project={compose_project}",
    ]
    try:
        result = procutil.run_cmd(
            ["docker", "ps", "-a", "--filter", label_filter, *extra_filters,
             "--format", "{{.Names}}"],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            names = [n.strip() for n in result.stdout.strip().splitlines() if n.strip()]
            # `stdout.strip()` is truthy above, so its non-blank splitlines
            # necessarily yield at least one name.
            print(f"[INFO]     Found {len(names)} orphaned containers", flush=True)
            for name in names:
                rm = procutil.run_cmd(["docker", "rm", "-f", name], check=False)
                if rm.returncode == 0:
                    print(f"[INFO]       Removed: {name}", flush=True)
                else:
                    print(f"[WARN]       Failed to remove {name}: {rm.stderr}", flush=True)
        else:
            print("[INFO]   No orphaned containers found", flush=True)
    except FileNotFoundError as e:
        print(f"[WARN] Failed to clean orphaned containers (docker unavailable): {e}", flush=True)

    print(f"[INFO] Reset complete for service: {service_name}", flush=True)


# ===========================================================================
# DooD preflight (S1.5)
# ===========================================================================


def _dood_preflight(physical_stack_dir: Path) -> None:
    """Probe that the daemon can reach the physical stack dir (S1.5).

    Only runs when PHYSICAL_REPO_ROOT != REPO_ROOT. Skippable via
    ``CIU_SKIP_DOOD_PREFLIGHT=1`` for tests.
    """
    if os.environ.get("CIU_SKIP_DOOD_PREFLIGHT") == "1":
        return
    repo_root = os.environ.get("REPO_ROOT")
    physical_root = os.environ.get("PHYSICAL_REPO_ROOT")
    if not repo_root or not physical_root:
        return
    if Path(repo_root).resolve() == Path(physical_root).resolve():
        return
    print("[INFO] DooD preflight: probing daemon reachability of physical repo root...", flush=True)
    try:
        result = procutil.docker(
            ["run", "--rm", "-v", f"{physical_stack_dir}:/probe", "alpine", "test", "-e", "/probe"],
            check=False,
        )
    except FileNotFoundError as e:
        raise DooDPreflightError(f"docker not available for DooD preflight: {e}") from e
    if result.returncode != 0:
        raise DooDPreflightError(
            "[S1.5] the docker daemon cannot reach PHYSICAL_REPO_ROOT "
            f"({physical_root}); the probe of {physical_stack_dir} failed. "
            "Named-volume workspaces (where the project lives in a docker volume, "
            "not a host bind) are unsupported: bind-mount the workspace from the "
            "host, or run CIU where PHYSICAL_REPO_ROOT == REPO_ROOT."
        )


# ===========================================================================
# Compose execution (S8.1) — live streaming, ported onto procutil-style Popen
# ===========================================================================


def compose_project_name(config: dict, stack_dir: Path) -> str:
    """Instance-scoped compose project: ``{project}-{env_tag}-{stack}`` (S8.7).

    Without an explicit ``-p``, docker compose derives the project from the
    stack directory BASENAME — identical for every checkout/worktree of the
    repo, so a second instance's ``compose up`` ADOPTS the first instance's
    containers (same project + service, changed container_name) and removes
    them. Scoping the project with the same ``deploy.project_name`` /
    ``deploy.environment_tag`` pair that already scopes container names
    (S7.7/S7.8) makes compose reconciliation per-instance.
    """
    deploy_cfg = config.get("deploy", {})
    project = deploy_cfg.get("project_name")
    env_tag = deploy_cfg.get("environment_tag")
    if not project or not env_tag:
        raise ValueError(
            "[S8.7] deploy.project_name and deploy.environment_tag are required "
            "to derive the compose project name"
        )
    return f"{project}-{env_tag}-{Path(stack_dir).name}"


def identity_compose_project_name(repo_root: Path, stack_dir: Path) -> str:
    """Workspace-identity compose project for config-less deployments (CIU-46).

    ``{repo_name}-{instance_id}-{stack_basename}``, derived from THIS
    workspace's own ``[ciu.instance.generated]`` facts file read by EXACT
    path (CIU-75: the sole instance-fact source — never the legacy ``ciu.env``
    export, and never the ambient process environment, which a shell that
    sourced another checkout's record would contaminate). Unique per workspace
    AND per stack, so config-less shipped stacks can neither collide across
    checkouts (the withdrawn basename "legacy" fallback's hazard) nor escape
    clean's S6.4a enumeration: up and clean call this same function, so they
    name a project identically by construction.

    Raises ValueError naming the generated facts file when the facts are
    missing or lack the identity keys — a deployment that cannot be NAMED must
    not start, and a teardown that cannot be named must refuse rather than skip
    (defaults-are-hazards: no invented basename stands in for identity).
    The composed name is normalized exactly like docker compose's own
    project-name rule (lowercase, ``[a-z0-9_-]`` only) and must still start
    with an alphanumeric, else ValueError.
    """
    facts_path = generated_facts_path(repo_root)
    values = read_generated_facts(Path(repo_root))
    repo_name = values.get("repo_name", "")
    instance_id = values.get("instance_id", "")
    if not repo_name or not instance_id:
        raise ValueError(
            "[S8.7] {root} declares no repo_name/instance_id — run `ciu env "
            "generate` first; the workspace-identity compose project is "
            "derived from them".format(root=facts_path)
        )
    name = re.sub(
        r"[^a-z0-9_-]",
        "",
        f"{repo_name}-{instance_id}-{Path(stack_dir).name}".lower(),
    )
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise ValueError(
            f"[S8.7] workspace-identity compose project normalizes to "
            f"{name!r}, which docker compose would refuse; rename the stack "
            "directory or set deploy.project_name/environment_tag."
        )
    basename = Path(stack_dir).name
    if basename != re.sub(r"[^a-z0-9_-]", "", basename.lower()):
        # Round-trip guard (adversarial review): sibling stacks 'Vault' and
        # 'vault' (or 'my.repo' and 'myrepo') would normalize onto the SAME
        # project within one workspace — the second up silently adopts the
        # first one's containers. Refuse instead of colliding.
        raise ValueError(
            f"[S8.7] config-less compose naming requires the stack directory "
            f"name to be already normalized ({basename!r} normalizes to "
            f"{re.sub(r'[^a-z0-9_-]', '', basename.lower())!r}); rename the "
            "directory or set deploy.project_name/environment_tag."
        )
    return name


def legacy_project_containers(stack_dir: Path, expected_project: str) -> list[str]:
    """Containers of THIS instance still under the legacy compose project (S8.7).

    Legacy = the pre-S8.7 default project (the stack dir basename). Returns
    the names of containers that carry the legacy project label AND belong to
    this instance (name starts with ``{project}-{env_tag}-``) — exactly the
    set that would collide on ``container_name`` under the scoped project.
    Containers of OTHER instances under the legacy label are left alone.
    """
    legacy_project = Path(stack_dir).name
    if legacy_project == expected_project:
        return []
    # expected_project = f"{project}-{env_tag}-{stack}"; the instance's
    # container-name prefix is f"{project}-{env_tag}-" (S7.7/S7.8).
    name_prefix = expected_project[: -len(Path(stack_dir).name)]
    result = procutil.run_cmd(
        [
            "docker", "ps", "-a",
            "--filter", f"label=com.docker.compose.project={legacy_project}",
            "--format", "{{.Names}}",
        ],
        check=False,
    )
    if result.returncode != 0:
        return []
    return [
        n.strip()
        for n in result.stdout.splitlines()
        if n.strip() and n.strip().startswith(name_prefix)
    ]


def guard_legacy_compose_project(stack_dir: Path, expected_project: str) -> None:
    """Fail (or migrate, with CIU_ADOPT_LEGACY_PROJECT=1) on legacy containers (S8.7).

    The first scoped-project ``up`` on a stack deployed by a pre-S8.7 CIU
    would hit ``container_name`` conflicts mid-deploy. Detect the legacy
    containers up front: with ``CIU_ADOPT_LEGACY_PROJECT=1`` remove them
    (bind-mounted state survives container removal; compose recreates them
    under the scoped project), otherwise abort with the one-time migration
    instructions.
    """
    legacy = legacy_project_containers(stack_dir, expected_project)
    if not legacy:
        return
    if os.environ.get("CIU_ADOPT_LEGACY_PROJECT") == "1":
        print(
            f"[S8.7] CIU_ADOPT_LEGACY_PROJECT=1 — removing {len(legacy)} legacy-project "
            f"container(s) before scoped-project up: {', '.join(legacy)}",
            flush=True,
        )
        procutil.run_cmd(["docker", "rm", "-f", *legacy], check=False)
        return
    raise ComposeError(
        f"[S8.7] {len(legacy)} container(s) of this instance still belong to the "
        f"legacy compose project '{Path(stack_dir).name}': {', '.join(legacy)}. "
        f"One-time migration: re-run with CIU_ADOPT_LEGACY_PROJECT=1 (removes and "
        f"recreates them under project '{expected_project}'; bind-mounted state "
        f"survives), or run 'ciu clean' for this stack first."
    )


def execute_docker_compose_with_logs(
    file_args: list[str], *, cwd: Path, env: Optional[dict] = None,
    project: str, repo_root: Path,
) -> dict:
    """Run ``docker compose -p <project> --project-directory <repo_root>
    <file_args> up -d`` with live streaming.

    *project* (S8.7) is REQUIRED and passed as ``-p`` so compose
    reconciliation is always instance-scoped and always enumerable by clean
    (CIU-46 cutover): CIU never runs compose without an explicit project —
    the withdrawn optional-None form let docker derive the cwd basename,
    which collided across checkouts and escaped every teardown pass.

    *repo_root* (CIU-71) is REQUIRED and passed as ``--project-directory`` so
    a stack's ``build.context``/``dockerfile`` resolve against the REPO
    ROOT — a deliberate EXCEPTION to how CIU resolves its other relative
    paths (hostdirs, ``ASK_FILE`` secret sources, configfile schema/template
    paths all resolve stack-dir-relative, never repo-root-relative), made
    because a Dockerfile ``COPY`` of a repo-shared asset needs the repo
    root, not the compose file's own directory (docker compose's default
    absent this flag). Without it, a stack whose Dockerfile ``COPY``s a
    repo-root-relative path fails at build time with a path-not-found error
    the operator has no reason to associate with CIU's invocation.

    Returns ``{'status': 'success'|'error'|'interrupted', 'message', 'stdout'}``.
    """
    result = {"status": "success", "message": "", "stdout": ""}
    print("[INFO] Executing docker compose up...", flush=True)
    cmd = [
        "docker", "compose", "-p", project,
        "--project-directory", str(Path(repo_root).resolve()),
        *file_args, "up", "-d",
    ]

    proc = None
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env, cwd=str(cwd),
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"  [COMPOSE] {line.rstrip()}", flush=True)
            lines.append(line)
        proc.wait()
        result["stdout"] = "".join(lines)
        if proc.returncode != 0:
            result["status"] = "error"
            result["message"] = f"Docker compose failed with exit code {proc.returncode}"
            print(f"[ERROR] Docker compose execution failed (exit {proc.returncode})", flush=True)
            return result
        print("[SUCCESS] Docker compose up completed", flush=True)
    except KeyboardInterrupt:
        print("\n[WARN] User interrupted docker compose execution", flush=True)
        result["status"] = "interrupted"
        result["message"] = "User interrupted execution"
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    except FileNotFoundError as e:
        result["status"] = "error"
        result["message"] = f"docker not available: {e}"
        print(f"[ERROR] {result['message']}", flush=True)
    return result


def _build_image_revisions(compose_yaml_text: str) -> dict[str, str]:
    """S17.4/CIU-21 — per-service baked ``image.revision`` label, by service.

    Read once per render/up pass from the rendered compose's own ``image:``
    keys (never from :func:`get_git_hash` — that is the HOST tree's CURRENT
    view, not the RUNNING image's baked truth, and comparing one against the
    other is a check that can never fail) and passed to
    :func:`composefile.generate_overlay` as plain data, so composefile.py
    never needs a docker import (S8.1's docker-free invariant for that
    module). A service with no baked label, or a ``build:`` service with no
    ``image:`` baked yet, is OMITTED rather than given a placeholder — reads
    :func:`deploy._image_revision_label` (never reimplements its label
    lookup), which already returns "" for both an absent label and an image
    that does not exist yet (this overlay is generated at Step 15, before
    Step 16's ``docker compose up`` — on a plain ``ciu up`` with no prior
    ``bake`` the image may legitimately not exist yet).
    """
    import yaml as _yaml

    from . import deploy as deploy_mod

    doc = _yaml.safe_load(compose_yaml_text) or {}
    services = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(services, dict):
        return {}

    revisions: dict[str, str] = {}
    for name, block in services.items():
        image = (block or {}).get("image") if isinstance(block, dict) else None
        if not image:
            continue
        revision = deploy_mod._image_revision_label(image)
        if revision:
            revisions[str(name)] = revision
    return revisions


# ---------------------------------------------------------------------------
# S16.9 (CIU-25 substrate) — ownership labels + instance lease on `ciu up`
# ---------------------------------------------------------------------------

# A SECOND, separately named compose fragment rather than more keys inside
# ciu.compose.overlay.yml. The overlay is composefile.py's artifact and is
# omitted entirely when a stack has no secrets/configfiles/governance/image
# revisions to wire (composefile.generate_overlay returns None) — ownership
# labels must be stamped on EVERY managed instance's resources, including the
# plainest possible stack, so they cannot ride on a file that may not exist.
# Keeping them separate also keeps the attribution honest in `docker inspect`:
# one file, one purpose.
OWNERSHIP_OVERLAY_NAME = "ciu.compose.ownership.yml"

# The label pair a future `ciu worktree reap` (CIU-25 / ciu-P27) reads to
# attribute a container, volume or network to a checkout. Closed vocabulary.
OWNERSHIP_LABEL_INSTANCE = "ciu.instance"
OWNERSHIP_LABEL_REPO_ROOT = "ciu.repo-root"


def workspace_ownership_labels(repo_root: Path) -> dict[str, str] | None:
    """The S16.9 ownership label pair for THIS checkout, or ``None``.

    ``None`` — meaning "stamp nothing, behave exactly as before" — for any
    checkout that is not a MANAGED worktree instance, i.e. that carries no
    ``ciu.worktree-instance.json``. A PRIMARY checkout's resources are
    deliberately left unlabeled by this package: labels are the input to a
    future destructive verb, and widening what that verb can see to include
    the primary workspace is a decision that needs its own package, not a
    side effect of this one.

    Both values are read from THIS workspace's OWN
    ``ciu.instance.generated.toml`` by EXACT PATH (CIU-75) — never
    from the legacy ``ciu.env`` export, and never from the ambient process
    environment, which a shell that sourced a sibling checkout's ciu.env
    carries (the CIU-41 species of contamination). Mislabeling here would be
    worse than not labeling: it would attribute one instance's containers to
    another checkout's root.
    """
    if not (repo_root / worktree.WORKTREE_INSTANCE_RECORD).is_file():
        return None
    facts_path = generated_facts_path(repo_root)
    values = read_generated_facts(repo_root)
    instance_id = values.get("instance_id", "")
    physical_root = values.get("physical_repo_root", "")
    if not instance_id or not physical_root:
        raise ValueError(
            f"[S16.9] {facts_path} lacks instance_id/physical_repo_root — "
            "this managed worktree instance cannot label the resources it "
            "creates. Run `ciu env generate` in this checkout."
        )
    return {
        OWNERSHIP_LABEL_INSTANCE: instance_id,
        OWNERSHIP_LABEL_REPO_ROOT: physical_root,
    }


def _labelable_top_level(doc: dict, key: str) -> list[str]:
    """Top-level ``volumes:``/``networks:`` entries compose actually CREATES.

    An ``external: true`` entry is declared, not created — it exists before
    this project and outlives it, so `ciu up` has nothing to claim there and
    compose rejects extra keys on it anyway. The workspace network is exactly
    such an entry (S2.6): it is created by ``ciu env generate``, not by up.
    """
    block = doc.get(key)
    if not isinstance(block, dict):
        return []
    names = []
    for name, body in block.items():
        if isinstance(body, dict) and body.get("external"):
            continue
        names.append(str(name))
    return names


def write_ownership_overlay(
    stack_dir: Path, compose_yaml_text: str, labels: dict[str, str]
) -> Path | None:
    """Write the S16.9 ownership fragment for one stack; ``None`` if nothing
    to label.

    Emitted on the ENGINE side and merged by compose as an extra ``-f``, so
    composefile.py's overlay generation is untouched. Labels use the LIST
    form (``- k=v``) the shipped compose templates already use, so the base
    file and this fragment are the same shape for compose's merge.
    """
    import yaml as _yaml

    doc = _yaml.safe_load(compose_yaml_text) or {}
    if not isinstance(doc, dict):
        return None
    entries = [f"{key}={value}" for key, value in sorted(labels.items())]
    services = doc.get("services")
    fragment: dict[str, Any] = {}
    if isinstance(services, dict) and services:
        fragment["services"] = {
            str(name): {"labels": list(entries)} for name in services
        }
    for key in ("volumes", "networks"):
        names = _labelable_top_level(doc, key)
        if names:
            fragment[key] = {name: {"labels": list(entries)} for name in names}
    if not fragment:
        return None

    path = Path(stack_dir) / MACHINE_DIR / OWNERSHIP_OVERLAY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _yaml.safe_dump(fragment, sort_keys=False, default_flow_style=False)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def acquire_instance_lease(repo_root: Path) -> Any:
    """S16.9 — acquire/renew this instance's ``held`` lease before `up` runs.

    Two independent gates, both of which must pass, and either of which alone
    means "do nothing at all":

    1. ``[ciu.worktree].lease_ttl_hours`` is configured (no default — see
       :func:`worktree.resolve_lease_ttl_hours`);
    2. this checkout is a MANAGED worktree instance (it has a lifecycle
       record). A PRIMARY/unmanaged checkout never acquires a lease.

    Deliberately BEFORE the compose invocation, not after it: a run that
    crashes halfway has still created containers, and the ownership claim
    over them must already be on disk. Claiming slightly too early is
    recoverable (`ciu clean` clears it); claiming too late is the orphaned-
    resource hole CIU-25 exists to close.
    """
    ttl_hours = worktree.resolve_worktree_lease_ttl(repo_root)
    if ttl_hours is None:
        return None
    return worktree.acquire_own_lease(repo_root, ttl_hours=ttl_hours)


# ===========================================================================
# E. PIPELINE — main_execution (S8.3)
# ===========================================================================


def main_execution(
    working_dir: Path,
    compose_file: str = CIU_COMPOSE_TEMPLATE,
    dry_run: bool = False,
    reset: bool = False,
    yes: bool = False,
    print_context: bool = False,
    render_toml: bool = False,
    define_root: Optional[Path] = None,
    skip_hostdir_check: bool = False,
    skip_hooks: bool = False,
    skip_secrets: bool = False,
    remove_secrets: bool = False,
    generate_env: bool = False,
    update_cert_permission: bool = False,
    auto_connect_network: Optional[bool] = None,
    compose_profiles: Optional[list[str]] = None,
    ciu_context: Optional[dict] = None,
) -> dict:
    """Run the S8.3 pipeline for one stack. Returns a result dict with 'status'.

    *compose_profiles* (P10 / S7.4): compose profile names joined into the
    compose process env's ``COMPOSE_PROFILES`` (step 16, via
    composefile.compose_process_env). The deploy orchestrator passes
    ``service.profiles + profile.compose_profiles`` here so the right compose
    services are activated; ``None`` means no profiles (single-stack ``ciu``).

    *ciu_context* (S3.12 / CIU-44): the deployment-selection facts
    (``selected_profiles`` / ``deployed_stacks``, see
    ``profiles.render_ciu_context``) exposed to templates as ``ciu.*`` and to
    hooks on the HookContext. ``None`` omits them — template references fail
    loudly rather than seeing an empty selection.
    """
    working_dir = Path(working_dir).resolve()
    result: dict = {"status": "success", "dry_run": dry_run}

    print("[INFO] Checking runtime dependencies...", flush=True)
    check_runtime_dependencies()

    original_cwd = Path.cwd()
    try:
        os.chdir(working_dir)

        # ---- Step 1: load env (S2) ----
        print("[STEP 1/17] Loading workspace environment...", flush=True)
        try:
            bootstrap_workspace_env(
                start_dir=working_dir,
                define_root=define_root,
                defaults_filename=GLOBAL_CONFIG_DEFAULTS,
                generate_env=generate_env,
                update_cert_permission=update_cert_permission,
                required_keys=REQUIRED_KEYS_CORE,
            )
        except WorkspaceEnvError:
            raise  # exit 3 via main()

        # S1.2 — enforce from working_dir (the invocation / --dir target), the same
        # source deploy.py's render path now uses via the shared helper.
        enforce_standalone_root(working_dir)

        # repo_root from --define-root or env REPO_ROOT (keep v1 mismatch rule).
        if define_root:
            repo_root = Path(define_root).resolve()
            env_repo_root = os.environ.get("REPO_ROOT")
            if env_repo_root and Path(env_repo_root).resolve() != repo_root:
                raise ValueError(
                    f"[ERROR] --define-root ({repo_root}) does not match REPO_ROOT ({env_repo_root}). "
                    "Update ciu.env or use a matching --define-root."
                )
        else:
            env_repo_root = os.environ.get("REPO_ROOT")
            if not env_repo_root:
                raise WorkspaceEnvError(
                    "[ERROR] REPO_ROOT not set. Ensure ciu.env is loaded before running CIU."
                )
            repo_root = Path(env_repo_root).resolve()

        # ---- Step 2: render global chain (S3.3) ----
        print("[STEP 2/17] Rendering global configuration...", flush=True)
        global_config = (
            config_model.render_global_chain(
                working_dir, repo_root, ciu_context=ciu_context
            )
            if ciu_context is not None
            else config_model.render_global_chain(working_dir, repo_root)
        )

        log_level = global_config.get("deploy", {}).get("log_level", "INFO")
        configure_logging(log_level)

        auto_connect_setting = global_config.get("ciu", {}).get("auto_connect_network", True)
        if auto_connect_network is not None:
            auto_connect_setting = auto_connect_network

        # ---- Step 3: render stack (S3.1/S3.4) ----
        print("[STEP 3/17] Rendering stack configuration...", flush=True)
        stack_config = (
            config_model.render_stack(
                working_dir,
                global_config,
                preserve_state=True,
                ciu_context=ciu_context,
            )
            if ciu_context is not None
            else config_model.render_stack(working_dir, global_config, preserve_state=True)
        )

        if render_toml:
            print("[SUCCESS] Rendered CIU TOML files (ciu.global.toml, ciu.toml)", flush=True)
            return result

        ensure_workspace_network(auto_connect=auto_connect_setting)

        # ---- Step 4: merge (S3.3) ----
        print("[STEP 4/17] Merging configurations...", flush=True)
        merged = config_model.deep_merge(global_config, stack_config)

        # ---- Step 5: validate (S11) ----
        print("[STEP 5/17] Validating merged configuration (S11)...", flush=True)
        root_key = config_model.validate_stack_shape(stack_config)
        config_model.validate_stack_provisioning(stack_config, source=str(working_dir))
        # QOL-11: declared layouts/exec-targets/vendor_images are GLOBAL-scope
        # keys under [deploy]/[ciu] — validate against global_config (never
        # stack_config/merged), so a malformed declaration is caught on every
        # single-stack run, not only when this run's own --layout/exec/
        # provenance feature is invoked.
        try:
            config_model.validate_declared_features(global_config, hosts.load_hosts(repo_root))
        except worktree.WorktreeError as exc:
            # exec-target shape (S16.7) raises WorktreeError, not ValueError;
            # it is an S11 validation-shape failure like its Step-5 siblings
            # (validate_stack_shape/validate_stack_provisioning) — S10.3 exit 2.
            raise ValueError(str(exc)) from exc
        specs = secret_directives.discover(root_key, merged)

        misplaced = secret_directives.find_misplaced(merged, stack_root_key=root_key)
        if misplaced:
            paths = ", ".join(p for p, _ in misplaced)
            raise ValueError(
                f"[S4.5/S4.1] secret directive(s) or secrets table(s) found outside the "
                f"'{root_key}.secrets' scope at: {paths}. Move them under the stack root key's "
                "secrets table, or remove them."
            )

        _check_gitignore(working_dir)

        ciu_cfg = merged.get("ciu", {})
        # S2.3: require_fqdn defaults FALSE (v1 defaulted True — fixed).
        if ciu_cfg.get("require_fqdn", False) and not os.environ.get("PUBLIC_FQDN"):
            raise ValueError("[S2.3] ciu.require_fqdn is true but PUBLIC_FQDN is empty.")
        if ciu_cfg.get("require_certs", False):
            validate_required_certs()  # S2.4 — validates PUBLIC_TLS_* as given

        stack_toml_path = working_dir / STACK_CONFIG_RENDERED

        # ---- Step 6: optional reset ----
        if reset:
            print("[STEP 6/17] Resetting service...", flush=True)
            reset_service(
                merged, working_dir,
                compose_file=compose_file,
                remove_secrets=remove_secrets,
                assume_yes=yes,
                specs=specs,
                repo_root=repo_root,
            )
            print("[SUCCESS] Service reset complete", flush=True)

        # ---- Step 7: auto-generate (S3.9) ----
        print("[STEP 7/17] Auto-generating values (UID, GID, BUILD_VERSION)...", flush=True)
        merged = auto_generate_values(merged)

        # ---- Step 8: hostdirs (S6) + DooD preflight (S1.5) ----
        if skip_hostdir_check:
            print("[STEP 8/17] --skip-hostdir-check: skipping hostdir creation/validation", flush=True)
        else:
            print("[STEP 8/17] Creating volume directories...", flush=True)
            create_hostdirs(merged, working_dir, repo_root=repo_root)

        # DooD preflight once before the first daemon bind use (S1.5).
        _dood_preflight(to_physical_path(working_dir, repo_root=repo_root))

        # Secret store-file map for hook contexts (S9.3).
        def _secret_file(name: str) -> Path:
            for s in specs:
                if s.name == name:
                    if s.kind == "ASK_FILE":
                        p = Path(s.locator)
                        return p if p.is_absolute() else working_dir / p
                    if s.kind == "GEN_LOCAL":
                        return secret_materialize.project_store(repo_root) / s.locator
                    return secret_materialize.stack_store(working_dir) / s.name
            raise KeyError(name)

        # S3.12 / CIU-44: identity + selection facts on the hook context, so
        # ciu-conforming hooks read them from ctx instead of re-parsing the
        # identity record or trusting ambient environment state.
        #
        # CIU-62: this is the REAL-RUN twin of deploy.py's `_workspace_identity`
        # (the `ciu check` preflight builds the same two HookContext fields),
        # and it carried the identical gap. The read fails three unrelated ways
        # — `OSError` (the read), `UnicodeDecodeError` (a non-UTF-8 byte) and a
        # malformed table — and before CIU-62 a non-UTF-8 record escaped here
        # and crashed `ciu up` at STEP 12 with a raw traceback. CIU-75 moved
        # the SOURCE to the `[ciu.instance.generated]` table (its own file
        # since ciu-P47) and the
        # reader now normalizes all three to `WorkspaceEnvError`, so one name
        # covers what three used to. The degradation it falls back to is
        # deliberately unchanged: `{}`, so `ctx.instance_id` / `ctx.network`
        # are None exactly as during a run outside a provisioned workspace.
        # Both preflight and real run must answer this question the same way,
        # or a hook's `validate_config` would see an identity its own `run()`
        # will not.
        _facts_path = generated_facts_path(repo_root)
        _hook_identity: dict = {}
        _hook_identity_unreadable = False
        try:
            _hook_identity = read_generated_facts(repo_root)
        except WorkspaceEnvError as _identity_exc:
            # CIU-62 review ruling — the twin of deploy._workspace_identity's
            # own warning, and it must stay a PAIR with it. The `{}`
            # degradation is deliberate (preflight and real run must answer
            # the identity question identically), but silence made it
            # indistinguishable from a genuinely unmanaged workspace: a hook
            # reading `ctx.instance_id is None` had no way to tell "genuinely
            # unmanaged" from "corrupt, swallowed". CIU-80: both sites now
            # additionally set `ctx.identity_unreadable = True` here, so a hook
            # can branch on the distinction itself, not just read a log line.
            # CIU-75 note: CIU-80's own version of this guard was
            # `if _env_path.is_file()`, which called a DIRECTORY where the
            # record belongs "absent" and so left the flag False on a path that
            # plainly exists and cannot be read. The reader draws the line now
            # — it returns `{}` only for a genuinely absent overlay or one with
            # no generated table, and raises for every present-but-unreadable
            # form — so the flag is true exactly when CIU-80 says it should be.
            print(
                f"[WARN] [S3.12] could not read workspace identity from "
                f"{_facts_path}: {_identity_exc}. Hook context identity "
                "(instance_id, network) will be None for this run — repair "
                "with `ciu env generate`",
                flush=True,
            )
            _hook_identity = {}
            _hook_identity_unreadable = True

        ctx = hooks_runner.HookContext(
            point="", stack_dir=working_dir, repo_root=repo_root, secret_file=_secret_file,
            selected_profiles=(
                tuple(ciu_context.get("selected_profiles") or [])
                if ciu_context else None
            ),
            deployed_stacks=(
                tuple(ciu_context.get("deployed_stacks") or [])
                if ciu_context else None
            ),
            # CIU-75's renamed fact keys + CIU-80's flag. Its twin is
            # deploy._check_hooks_for_stack's HookContext; the two must stay
            # identical in BOTH halves (S3.12/CIU-44 pairing).
            instance_id=_hook_identity.get("instance_id"),
            network=_hook_identity.get("network"),
            identity_unreadable=_hook_identity_unreadable,
        )

        # S9.3 / CIU-4: readiness helpers on the hook context so service-touching
        # hooks don't race `docker compose up -d`. wait_healthy resolves a service
        # name to its project-scoped container and polls Docker health; wait_tcp is
        # a dependency-free port probe for images that expose no healthcheck.
        _deploy_cfg = merged.get("deploy", {})
        _project = _deploy_cfg.get("project_name")
        _env_tag = _deploy_cfg.get("environment_tag")

        def _container_status(service: str) -> str:
            cname = (
                f"{_project}-{_env_tag}-{service}"
                if _project and _env_tag
                else service
            )
            try:
                res = procutil.docker(
                    ["inspect", "--format", "{{json .State}}", cname], check=False
                )
            except FileNotFoundError:
                return "not-found"
            if res.returncode != 0 or not (res.stdout or "").strip():
                return "not-found"
            try:
                state = json.loads(res.stdout)
            except (ValueError, TypeError):
                return "not-found"
            return _health.classify(state)

        def _wait_healthy(
            service: str, *, timeout_s: float = 120.0, interval_s: float = 2.0
        ) -> bool:
            return _health.wait_healthy(
                lambda: _container_status(service),
                timeout_s=timeout_s,
                interval_s=interval_s,
            )

        def _wait_tcp(
            host: str, port: int, *, timeout_s: float = 30.0, interval_s: float = 0.5
        ) -> bool:
            return _health.wait_tcp(
                host, port, timeout_s=timeout_s, interval_s=interval_s
            )

        ctx.wait_healthy = _wait_healthy
        ctx.wait_tcp = _wait_tcp

        def _hooks_for(point: str) -> list:
            return list(merged.get(root_key, {}).get("hooks", {}).get(point, []))

        # S9.4a — every name this stack declares via an S4.1 directive. A
        # `persist:'secret'` return colliding with one of these is refused
        # (S4.6's uniqueness rule, now spanning both channels that can produce
        # a store file). Computed from the SAME `specs` Step 5 discovered, so
        # the two channels can never disagree about what is declared.
        declared_secret_names = frozenset(spec.name for spec in specs)

        # ---- Step 9: pre_secrets hooks (S9) ----
        if skip_hooks:
            print("[STEP 9/17] --skip-hooks: skipping pre_secrets hooks", flush=True)
        else:
            pre_secrets = _hooks_for("pre_secrets")
            if pre_secrets:
                print(f"[STEP 9/17] Running {len(pre_secrets)} pre_secrets hook(s)...", flush=True)
                ctx.point = "pre_secrets"
                hooks_runner.run_hooks(
                    pre_secrets, "pre_secrets", merged, ctx, stack_toml_path,
                    declared_secret_names=declared_secret_names,
                )

        # ---- Step 10: secrets (S4) ----
        materialized: dict = {}
        if skip_secrets:
            print("[STEP 10/17] --skip-secrets: skipping materialization and overlay", flush=True)
        else:
            print("[STEP 10/17] Resolving and materializing secrets...", flush=True)
            vault = None
            if any(s.kind in ("ASK_VAULT", "GEN_TO_VAULT") for s in specs):
                addr = vault_addr_from_config(merged)
                token = resolve_vault_token(merged, repo_root)
                if token is None:
                    raise VaultError(
                        "[S4.16] vault-backed secrets are declared but no Vault token "
                        "resolved (VAULT_TOKEN env, vault.token_file, or the vault "
                        "stack's hook-persisted 'root_token' secret store file, "
                        "S9.4a). Aborting before any container starts."
                    )
                vault = VaultKV2(addr, token)
            materialized = secret_materialize.materialize(
                specs, stack_dir=working_dir, repo_root=repo_root, vault=vault, assume_yes=yes,
            )
            for spec in specs:
                if spec.expose_env:
                    print(
                        f"[NOTICE] secret '{spec.name}' is exposed into the compose env as "
                        f"${{{spec.expose_env}}} (S4.19 — discouraged escape hatch)",
                        flush=True,
                    )

        # ---- Step 11: pre_compose hooks (S9) ----
        if skip_hooks:
            print("[STEP 11/17] --skip-hooks: skipping pre_compose hooks", flush=True)
        else:
            pre_compose = _hooks_for("pre_compose")
            if pre_compose:
                print(f"[STEP 11/17] Running {len(pre_compose)} pre_compose hook(s)...", flush=True)
                ctx.point = "pre_compose"
                hooks_runner.run_hooks(
                    pre_compose, "pre_compose", merged, ctx, stack_toml_path,
                    declared_secret_names=declared_secret_names,
                )

        # ---- Step 12: configfiles (S5) ----
        print("[STEP 12/17] Rendering configfiles...", flush=True)

        def _secret_value(name: str) -> str:
            if name in materialized:
                value = materialized[name].value
                if value is None:
                    raise ValueError(
                        f"[S5.4] secret '{name}' is ASK_FILE; its content is referenced in "
                        "place and can never be embedded in a configfile template."
                    )
                return value
            raise ValueError(
                f"[S5.4] configfile requested secret('{name}') but it is not materialized "
                "(declare it in a secrets table, and do not run with --skip-secrets)."
            )

        configfile_mounts = (
            composefile.render_configfiles(
                working_dir, root_key, merged, _secret_value, ciu_context=ciu_context
            )
            if ciu_context is not None
            else composefile.render_configfiles(
                working_dir, root_key, merged, _secret_value
            )
        )

        # ---- Step 13: render compose template (S4.21) ----
        print("[STEP 13/17] Rendering compose template...", flush=True)
        guarded = composefile.guard_config(merged, specs)

        if print_context:
            print("\n[CONTEXT] Merged configuration (secrets redacted, S4.23):", flush=True)
            print(json.dumps(composefile.redact_config(merged, specs), indent=2, default=str), flush=True)

        compose_template = working_dir / compose_file
        if compose_template.suffix == ".j2":
            if ciu_context is not None:
                rendered_compose = composefile.render_compose(
                    compose_template, guarded, ciu_context=ciu_context
                )
            else:
                rendered_compose = composefile.render_compose(compose_template, guarded)
            output_path = working_dir / CIU_COMPOSE_OUTPUT
            # S8.4: atomic write via tmp sibling + os.replace (no partial writes).
            tmp_path = output_path.with_suffix(".yml.tmp")
            tmp_path.write_text(rendered_compose, encoding="utf-8")
            os.replace(tmp_path, output_path)
        else:
            rendered_compose = compose_template.read_text(encoding="utf-8")

        # ---- Step 14: leak scan (S4.22) + consumption (S4.20) ----
        print("[STEP 14/17] Scanning rendered compose for leaks...", flush=True)
        composefile.leak_scan(rendered_compose, materialized)
        declared_names = {s.name for s in specs}
        hook_consumed = {
            spec.name for spec in specs
            if getattr(spec, "consumed_by", None) == "hook"
        }
        unconsumed = composefile.validate_consumption(
            rendered_compose,
            declared_names,
            configfile_mounts=configfile_mounts,
            hook_consumed=hook_consumed,
        )
        for name in unconsumed:
            print(f"[WARN] declared secret '{name}' is consumed by no channel (S4.20)", flush=True)

        # ---- Step 15: overlay (S4.17/S8.1/S15/S17.4) ----
        print("[STEP 15/17] Generating overlay...", flush=True)
        governance_config = governance.resolve_stack_governance(
            merged.get(root_key, {}).get("governance"), global_config
        )
        image_revisions = _build_image_revisions(rendered_compose)
        overlay_path = composefile.generate_overlay(
            working_dir, materialized, configfile_mounts,
            repo_root=repo_root, physical_root=None,
            compose_yaml_text=rendered_compose,
            governance=governance_config,
            image_revisions=image_revisions,
        )
        # S4.22 completeness: scan the overlay for secret leaks too.
        if overlay_path is not None and overlay_path.exists():
            composefile.leak_scan(overlay_path.read_text(encoding="utf-8"), materialized)

        # S16.9 (CIU-25 substrate): ownership labels for a MANAGED worktree
        # instance only. Written next to the overlay, merged as a separate
        # `-f` below, and removed by reset_service alongside every other
        # generated artifact.
        ownership_labels = workspace_ownership_labels(repo_root)
        ownership_path = (
            write_ownership_overlay(working_dir, rendered_compose, ownership_labels)
            if ownership_labels is not None
            else None
        )
        if ownership_path is not None:
            print(
                f"[INFO] [S16.9] ownership labels: "
                f"{OWNERSHIP_LABEL_INSTANCE}={ownership_labels[OWNERSHIP_LABEL_INSTANCE]}",
                flush=True,
            )
            composefile.leak_scan(
                ownership_path.read_text(encoding="utf-8"), materialized
            )

        # ---- Step 16: compose up (S8.1) ----
        if dry_run:
            print("[STEP 16/17] --dry-run: skipping docker compose up", flush=True)
        else:
            print("[STEP 16/17] Starting stack (docker compose up -d)...", flush=True)
            compose_env = composefile.compose_process_env(
                specs, materialized, compose_profiles=compose_profiles
            )
            file_args = composefile.compose_file_args(working_dir, overlay_path)
            if ownership_path is not None:
                file_args += ["-f", f"{MACHINE_DIR}/{OWNERSHIP_OVERLAY_NAME}"]
            # S16.9: claim ownership BEFORE anything is created (see
            # acquire_instance_lease). A no-op unless lease_ttl_hours is
            # configured AND this checkout is a managed instance.
            acquire_instance_lease(repo_root)
            project = compose_project_name(global_config, working_dir)
            guard_legacy_compose_project(working_dir, project)
            # S16.3/CIU-24 — resolve the primary-worktree policy only for an
            # actual Compose start.  Dry-run and render-only paths must remain
            # Docker/worktree-family-free: a tester container can deliberately
            # mount only the current linked worktree, not every sibling named
            # by git's host-visible worktree registry.
            worktree_cap = worktree.resolve_worktree_cap(repo_root)
            # S16.3/CIU-24 — the family-wide budget slot wraps ONLY the real
            # Compose start: candidate identity is resolved and the lock is
            # held/released entirely inside this context manager. P02's
            # post-up shared-infra join (below) deliberately stays OUTSIDE
            # it — it starts no new instance, so serializing it under the
            # capacity lock would only add unrelated contention.
            try:
                with worktree.worktree_budget_slot(
                    repo_root, worktree_cap,
                    os.environ["DOCKER_NETWORK_INTERNAL"],
                    working_dir.relative_to(repo_root),
                ):
                    docker_result = execute_docker_compose_with_logs(
                        file_args, cwd=working_dir, env=compose_env, project=project,
                        repo_root=repo_root,
                    )
            except worktree.WorktreeError as exc:
                raise ComposeError(str(exc)) from exc
            if docker_result["status"] == "error":
                raise ComposeError(docker_result["message"])
            if docker_result["status"] == "interrupted":
                result["status"] = "interrupted"
                result["message"] = "User aborted deployment"
            result["stdout"] = docker_result.get("stdout", "")

            # ---- S16.1/CIU-22: join declared shared-infra services ----
            # Only after Compose reports SUCCESS (never on "interrupted"); the
            # intent lives in THIS worktree's already-loaded environment.
            # Parsing AND joining both go through the SAME WorktreeError ->
            # ComposeError translation: a partial/malformed stored intent is
            # exactly as much "this stack's up failed" as a bad join, and
            # must fail only this stack (deploy._run_stack's `except
            # engine.ComposeError`), never escape and crash the whole run.
            if docker_result["status"] == "success":
                try:
                    shared_infra_intent = worktree.parse_shared_infra_config(global_config)
                    if shared_infra_intent is not None:
                        print("[STEP 16/17] Joining declared shared-infra services...", flush=True)
                        worktree.connect_shared_infra_after_up(
                            repo_root, project, shared_infra_intent
                        )
                except worktree.WorktreeError as exc:
                    raise ComposeError(str(exc)) from exc

        # ---- Step 17: post_compose hooks (S9) ----
        if skip_hooks:
            print("[STEP 17/17] --skip-hooks: skipping post_compose hooks", flush=True)
        elif result["status"] == "success":
            post_compose = _hooks_for("post_compose")
            if post_compose:
                print(f"[STEP 17/17] Running {len(post_compose)} post_compose hook(s)...", flush=True)
                ctx.point = "post_compose"
                hooks_runner.run_hooks(
                    post_compose, "post_compose", merged, ctx, stack_toml_path,
                    declared_secret_names=declared_secret_names,
                )

        result["config"] = composefile.redact_config(merged, specs)
        return result
    finally:
        # S8.4 / B10: always restore cwd on every path.
        os.chdir(original_cwd)


def run_shipped(
    working_dir: Path,
    *,
    compose_file: str = SHIPPED_COMPOSE,
    dry_run: bool = False,
    define_root: Optional[Path] = None,
    generate_env: bool = False,
    update_cert_permission: bool = False,
    auto_connect_network: Optional[bool] = None,
    compose_profiles: Optional[list[str]] = None,
) -> dict:
    """Run a maintainer's pre-shipped compose file *through* CIU (S8.5).

    The ``--shipped`` passthrough: it does NOT require a CIU stack config
    (``ciu.defaults.toml.j2``) and performs none of the secret / overlay /
    configfile steps. It still adds the value a plain ``docker compose up``
    cannot: loads ``ciu.env`` (machine identity, S2), renders the global chain
    for the ``auto_connect_network`` setting, ensures/attaches the workspace
    network (S2.8), runs the DooD reachability preflight (S1.5), then runs
    ``docker compose -f <compose_file> up -d`` (default ``docker-compose.yml``)
    with the same cwd/project convention as the native path.

    CIU never writes *compose_file* — it is the maintainer's committed file.
    """
    working_dir = Path(working_dir).resolve()
    result: dict = {"status": "success", "dry_run": dry_run, "shipped": True}

    print("[INFO] Checking runtime dependencies...", flush=True)
    check_runtime_dependencies()

    original_cwd = Path.cwd()
    try:
        os.chdir(working_dir)

        # ---- Load env (S2) — same bootstrap as the native path's step 1 ----
        print("[SHIPPED 1/4] Loading workspace environment...", flush=True)
        bootstrap_workspace_env(
            start_dir=working_dir,
            define_root=define_root,
            defaults_filename=GLOBAL_CONFIG_DEFAULTS,
            generate_env=generate_env,
            update_cert_permission=update_cert_permission,
            required_keys=REQUIRED_KEYS_CORE,
        )

        if define_root:
            repo_root = Path(define_root).resolve()
        else:
            # CIU-46 (review): prefer THIS checkout's own env root — found by
            # walking UP from the stack dir to its global-defaults marker —
            # over an ambient `REPO_ROOT`, which a shell that sourced ANOTHER
            # checkout's ciu.env carries. A linked worktree nested under its
            # main checkout would otherwise name its compose project with the
            # MAIN instance's identity record and silently adopt main's
            # containers. The walk-up lands on exactly the env root
            # bootstrap_workspace_env loaded, so naming and environment agree.
            resolved_env_root = resolve_env_root(
                working_dir, None, GLOBAL_CONFIG_DEFAULTS
            )
            # resolve_env_root falls back to start_dir when NOTHING is found;
            # only an actually-present marker makes this the checkout's root.
            marker_found = (
                resolved_env_root / GLOBAL_CONFIG_DEFAULTS
            ).is_file()
            env_repo_root = os.environ.get("REPO_ROOT")
            if marker_found:
                if (
                    env_repo_root
                    and Path(env_repo_root).resolve() != resolved_env_root.resolve()
                ):
                    print(
                        "[INFO] [S8.7] ambient REPO_ROOT points at "
                        f"{Path(env_repo_root).resolve()} but this stack's "
                        f"checkout resolves to {resolved_env_root} — using the "
                        "checkout's own record for compose project identity.",
                        flush=True,
                    )
                repo_root = resolved_env_root.resolve()
                os.environ["REPO_ROOT"] = str(repo_root)
            elif env_repo_root:
                # No local marker to trust; keep the bootstrapped environment.
                repo_root = Path(env_repo_root).resolve()
            else:
                raise WorkspaceEnvError(
                    "[ERROR] REPO_ROOT not set. Ensure ciu.env is loaded before running CIU."
                )

        # ---- Global chain only (no stack config in shipped mode) ----
        print("[SHIPPED 2/4] Rendering global configuration...", flush=True)
        global_config = config_model.render_global_chain(working_dir, repo_root)
        configure_logging(global_config.get("deploy", {}).get("log_level", "INFO"))
        auto_connect_setting = global_config.get("ciu", {}).get("auto_connect_network", True)
        if auto_connect_network is not None:
            auto_connect_setting = auto_connect_network
        ensure_workspace_network(auto_connect=auto_connect_setting)

        # ---- DooD preflight (S1.5) before the first daemon bind use ----
        _dood_preflight(to_physical_path(working_dir, repo_root=repo_root))

        compose_path = working_dir / compose_file
        if not compose_path.is_file():
            raise FileNotFoundError(
                f"[--shipped] no pre-shipped compose file at {compose_path}. "
                f"Ship a '{compose_file}' next to the stack, or pass -f <name>."
            )

        # ---- Compose up (S8.5) — no overlay, no expose_env secrets ----
        compose_env = composefile.compose_process_env(
            [], {}, compose_profiles=compose_profiles
        )
        if dry_run:
            print("[SHIPPED 3/4] --dry-run: skipping docker compose up", flush=True)
            print(f"[SHIPPED 4/4] would run: docker compose --project-directory {repo_root} -f {compose_file} up -d", flush=True)
            return result

        print(f"[SHIPPED 3/4] Starting shipped stack (docker compose -f {compose_file} up -d)...", flush=True)
        try:
            shipped_project: Optional[str] = compose_project_name(global_config, working_dir)
        except ValueError:
            # CIU-46 cutover: there is NO basename fallback. A config-less
            # shipped stack names its compose project from THIS workspace's
            # own ciu.env identity — unique per workspace AND per stack, and
            # the exact name clean's S6.4a enumeration derives from the same
            # record. (The withdrawn legacy fallback let docker derive the
            # cwd basename: identical for every checkout of the repo, so it
            # both collided across instances and escaped every teardown pass.)
            shipped_project = identity_compose_project_name(repo_root, working_dir)
            print(
                "[INFO] [S8.7] deploy.project_name/environment_tag not set — "
                f"shipped stack uses the workspace-identity compose project "
                f"'{shipped_project}'",
                flush=True,
            )
        # CIU-46 cutover: shipped_project is ALWAYS a known name now (the
        # S8.7 scoped project or the computed identity project) — the guard
        # runs unconditionally.
        guard_legacy_compose_project(working_dir, shipped_project)
        # Same S16.3 boundary as the native path: only a genuine Compose start
        # reads the primary-worktree policy.  ``--dry-run`` returned above.
        worktree_cap = worktree.resolve_worktree_cap(repo_root)
        # S16.9 — same claim-before-creation rule as the native path. A shipped
        # stack's containers are exactly as orphanable as a rendered one's, and
        # the lease is a pure record write with no generated artifact, so there
        # is no reason for the two paths to differ here. (The OWNERSHIP LABELS
        # deliberately do NOT follow: they are a generated compose fragment,
        # and `clean` skips `reset_service` for a shipped stack — see S16.9's
        # "Still open".)
        acquire_instance_lease(repo_root)
        # S16.3/CIU-24 — same budget-slot wiring as main_execution's native
        # path (see the comment there): wraps ONLY the real Compose start.
        try:
            with worktree.worktree_budget_slot(
                repo_root, worktree_cap,
                os.environ["DOCKER_NETWORK_INTERNAL"],
                working_dir.relative_to(repo_root),
            ):
                docker_result = execute_docker_compose_with_logs(
                    ["-f", compose_file], cwd=working_dir, env=compose_env,
                    project=shipped_project, repo_root=repo_root,
                )
        except worktree.WorktreeError as exc:
            raise ComposeError(str(exc)) from exc
        if docker_result["status"] == "error":
            raise ComposeError(docker_result["message"])
        if docker_result["status"] == "interrupted":
            result["status"] = "interrupted"
            result["message"] = "User aborted shipped deployment"
        result["stdout"] = docker_result.get("stdout", "")

        # ---- S16.1/CIU-22: join declared shared-infra services ----
        # Parsing AND joining both go through the SAME WorktreeError ->
        # ComposeError translation -- see the identical comment in
        # main_execution for why an untranslated WorktreeError here would
        # crash the whole `ciu deploy` run instead of failing one stack.
        if docker_result["status"] == "success":
            try:
                shared_infra_intent = worktree.parse_shared_infra_config(global_config)
                if shared_infra_intent is not None:
                    # CIU-46: shipped_project is ALWAYS a known name now (the
                    # S8.7 scoped project, or the workspace-identity project) —
                    # the former "cannot derive the project to scope the join"
                    # refusal is unreachable and withdrawn. The join's label
                    # filters scope to exactly what up named, legacy case
                    # included; an empty normalized basename already refused
                    # earlier, so there is no unscoped case left.
                    print("[SHIPPED 3/4] Joining declared shared-infra services...", flush=True)
                    worktree.connect_shared_infra_after_up(
                        repo_root, shipped_project, shared_infra_intent
                    )
            except worktree.WorktreeError as exc:
                raise ComposeError(str(exc)) from exc

        print("[SHIPPED 4/4] Done.", flush=True)
        return result
    finally:
        os.chdir(original_cwd)


def _check_gitignore(stack_dir: Path) -> None:
    """S1.7 — abort when ``<stack>/.ciu`` is not gitignored inside a git work tree.

    Skips silently when not inside a git repo. We probe a representative path
    UNDER ``.ciu`` (``.ciu/secrets``) because ``git check-ignore`` on a
    not-yet-existing ``.ciu`` with the canonical directory pattern ``**/.ciu/``
    reports "not ignored" until the directory exists; the under-path query is
    stable for both ``**/.ciu/`` and ``.ciu/`` patterns (creates no files).

    git check-ignore returncode semantics (preserved exactly):
      0  — path IS ignored
      1  — path is NOT ignored
      128 — not inside a git repository
    """
    try:
        inside = procutil.run_cmd(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=False,
        )
    except FileNotFoundError:
        return  # git not installed — skip
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return  # not a git work tree — skip silently

    ciu_dir = stack_dir / MACHINE_DIR
    probe = ciu_dir / SECRETS_SUBDIR
    # rc=0 → ignored, rc=1 → not ignored, rc=128 → no repo (treat as not ignored)
    ignored = procutil.run_cmd(["git", "check-ignore", "-q", str(probe)], check=False)
    if ignored.returncode == 0:
        return
    # Fall back to the literal `.ciu` path (covers the case where it already
    # exists as a directory and the pattern matches it directly).
    ignored_dir = procutil.run_cmd(["git", "check-ignore", "-q", str(ciu_dir)], check=False)
    if ignored_dir.returncode == 0:
        return
    raise ValueError(
        f"[S1.7] {ciu_dir} is not gitignored. CIU machine-owned artifacts must be "
        "ignored. Add `**/.ciu/` to your .gitignore and retry."
    )


# ===========================================================================
# F. secrets subcommand (S4.25)
# ===========================================================================


def secrets_command(args: argparse.Namespace) -> int:
    """Implement ``ciu secrets list|reset`` (S4.25).

    Loads env + renders/merges configs enough to discover specs (pipeline steps
    1–5), then calls materialize.list_secrets / reset_secrets. Never prints
    secret values.
    """
    working_dir = Path(args.dir).resolve()
    check_runtime_dependencies()

    original_cwd = Path.cwd()
    try:
        os.chdir(working_dir)
        bootstrap_workspace_env(
            start_dir=working_dir,
            define_root=args.define_root,
            defaults_filename=GLOBAL_CONFIG_DEFAULTS,
            generate_env=False,
            update_cert_permission=False,
            required_keys=REQUIRED_KEYS_CORE,
        )
        repo_root = Path(
            str(args.define_root.resolve()) if args.define_root else os.environ.get("REPO_ROOT", str(working_dir))
        ).resolve()

        global_config = config_model.render_global_chain(working_dir, repo_root)
        stack_config = config_model.render_stack(working_dir, global_config, preserve_state=True)
        merged = config_model.deep_merge(global_config, stack_config)
        root_key = config_model.validate_stack_shape(stack_config)
        specs = secret_directives.discover(root_key, merged)
    finally:
        os.chdir(original_cwd)

    # S9.4a — a hook-persisted secret has no directive to discover, so it is
    # enumerated from the stack's own provenance sidecar and appended to the
    # directive rows. Without this it would be INVISIBLE to `ciu secrets list`
    # while sitting in the very same store dir, and `ciu secrets reset` would
    # skip exactly the values that have no other way of being reached.
    hook_rows = secret_materialize.hook_secret_rows(
        working_dir, (s.name for s in specs)
    )

    if args.action == "list":
        rows = secret_materialize.list_secrets(specs, working_dir, repo_root)
        _print_secret_table(rows + hook_rows)
        return 0

    # reset
    selected = [args.name] if args.name else None
    if selected:
        known = {s.name for s in specs} | {row["name"] for row in hook_rows}
        if args.name not in known:
            print(f"[ERROR] no such secret '{args.name}' in this stack", flush=True)
            return 2
    if not args.yes:
        scope = f"secret '{args.name}'" if args.name else "ALL secret store files"
        reply = input(f"Delete {scope} for {working_dir.name}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("[INFO] Aborted.", flush=True)
            return 0
    deleted = secret_materialize.reset_secrets(working_dir, repo_root, specs, names=selected)
    deleted += secret_materialize.reset_hook_secrets(working_dir, names=selected)
    if deleted:
        for d in deleted:
            print(f"[INFO] Removed: {d}", flush=True)
        print(f"[SUCCESS] Removed {len(deleted)} secret store file(s)", flush=True)
    else:
        print("[INFO] No secret store files to remove", flush=True)
    return 0


def _print_secret_table(rows: list[dict]) -> None:
    """Print a secrets table WITHOUT values (S4.25)."""
    if not rows:
        print("[INFO] No secrets declared in this stack.", flush=True)
        return
    headers = ("NAME", "KIND", "LOCATOR", "STORE", "EXISTS")
    widths = [len(h) for h in headers]
    table = []
    for r in rows:
        row = (
            r["name"], r["kind"], str(r.get("locator") or "-"),
            r["store"], "yes" if r["exists"] else "no",
        )
        table.append(row)
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers), flush=True)
    for row in table:
        print(fmt.format(*row), flush=True)


# ===========================================================================
# main() — argv dispatch + exit-code mapper (S10.3)
# ===========================================================================


def _exit_code_for(exc: BaseException) -> int:
    """Map an exception to the S10.3 exit code."""
    if isinstance(exc, (WorkspaceEnvError, DependencyError, DooDPreflightError)):
        return 3
    if isinstance(exc, ValueError):
        return 2  # validation / [S...] config errors
    if isinstance(exc, hooks_runner.HookExecutionError):
        return 1  # runtime: hook body raised
    # SecretLeakError, VaultError, ComposeError, anything else.
    return 1


def main(argv: Optional[list] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # Subcommand dispatch: `ciu secrets ...` — detect before the main parser.
    if raw and raw[0] == "secrets":
        sub = _build_secrets_subparser().parse_args(raw[1:])
        try:
            return secrets_command(sub)
        except SystemExit:
            raise
        except BaseException as exc:  # noqa: BLE001
            print(f"[ERROR] {exc}", flush=True)
            return _exit_code_for(exc)

    args = parse_arguments(raw)

    # --generate-env fast path (S2.8 single bootstrap entry point).
    generate_env_only = (
        args.generate_env
        and not args.dry_run
        and not args.reset
        and not args.print_context
        and not args.render_toml
        and not args.update_cert_permission
        and not args.skip_hostdir_check
        and not args.skip_hooks
        and not args.skip_secrets
        and not args.shipped
    )
    if generate_env_only:
        try:
            print("[INFO] Checking runtime dependencies...", flush=True)
            check_runtime_dependencies()
            env_root = resolve_env_root(
                start_dir=args.dir, define_root=args.define_root, defaults_filename=GLOBAL_CONFIG_DEFAULTS,
            )
            env_path = bootstrap_env_init(env_root)  # S2.8 single bootstrap
            print(f"[SUCCESS] Generated {env_path}", flush=True)
            return 0
        except SystemExit:
            raise
        except BaseException as exc:  # noqa: BLE001
            print(f"[ERROR] {exc}", flush=True)
            return _exit_code_for(exc)

    if args.shipped:
        # Default to the pre-shipped compose name unless the user overrode -f.
        shipped_file = SHIPPED_COMPOSE if args.file == CIU_COMPOSE_TEMPLATE else args.file
        try:
            result = run_shipped(
                working_dir=args.dir,
                compose_file=shipped_file,
                dry_run=args.dry_run,
                define_root=args.define_root,
                generate_env=args.generate_env,
                update_cert_permission=args.update_cert_permission,
                auto_connect_network=args.auto_connect_network,
            )
        except SystemExit:
            raise
        except BaseException as exc:  # noqa: BLE001
            print(f"[ERROR] {exc}", flush=True)
            return _exit_code_for(exc)
        status = result.get("status")
        return 0 if status == "success" else 1

    try:
        result = main_execution(
            working_dir=args.dir,
            compose_file=args.file,
            dry_run=args.dry_run,
            reset=args.reset,
            yes=args.yes,
            print_context=args.print_context,
            render_toml=args.render_toml,
            define_root=args.define_root,
            skip_hostdir_check=args.skip_hostdir_check,
            skip_hooks=args.skip_hooks,
            skip_secrets=args.skip_secrets,
            remove_secrets=args.secrets,
            generate_env=args.generate_env,
            update_cert_permission=args.update_cert_permission,
            auto_connect_network=args.auto_connect_network,
        )
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", flush=True)
        return _exit_code_for(exc)

    status = result.get("status")
    if status == "success":
        return 0
    if status == "interrupted":
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
