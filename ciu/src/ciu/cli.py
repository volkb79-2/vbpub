#!/usr/bin/env python3
"""CIU — Container Infrastructure Utility (compose · init · up)"""

from __future__ import annotations

import os
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .cli_utils import CiuArgumentParser, cli_error, get_cli_version
from .config_constants import GLOBAL_CONFIG_DEFAULTS, WORKSPACE_ENV

def _resolve_repo_root_cli(define_root: Path | str | None, start_dir: Path) -> Path:
    """Resolve the repo root for a CLI verb, or exit cleanly on refusal (S1.1).

    ``dev.resolve_repo_root`` raises a ``[S1.1]``-tagged ``ValueError`` when no
    explicit root or committed marker can resolve the invocation. This is the
    ONE place every `dev`/`worktree` call site funnels through, so refusal
    always surfaces as this codebase's standard ``[ERROR] ...`` message plus a
    non-zero exit, matching every other CLI-level configuration error, never a
    raw traceback (O3).
    Imported late (mirrors every existing call site) so tests that monkeypatch
    ``dev.resolve_repo_root`` keep working unchanged.
    """
    from .dev import resolve_repo_root

    try:
        return resolve_repo_root(define_root, start_dir)
    except ValueError as exc:
        cli_error(f"[ERROR] {exc}")
        raise SystemExit(2)


def _resolve_worktree_git_root_cli(
    define_root: Path | str | None, start_dir: Path,
) -> Path:
    """Resolve the Git family for ``ciu worktree`` operations (S16)."""
    from .workspace import CiuWorkspaceError, resolve_worktree_git_root

    try:
        return resolve_worktree_git_root(start_dir, root_folder=define_root)
    except CiuWorkspaceError as exc:
        cli_error(f"[ERROR] {exc}")
        raise SystemExit(2)


def _resolve_worktree_ciu_root_cli(
    git_root: Path,
    define_root: Path | str | None,
    start_dir: Path,
) -> Path | None:
    """Resolve the selected nested CIU root for worktree allocation metadata.

    Worktree operations remain family-wide, but allocation overlays belong to
    one CIU root. A Git-family selector may itself be rootless, so absence of a
    CIU marker returns None and callers decide whether the operation requires
    an explicit target.
    """
    from .workspace import CiuWorkspaceError, resolve_ciu_root

    selected = None
    if define_root is not None:
        explicit_root = Path(define_root).resolve()
        family_root = Path(git_root).resolve()
        if explicit_root == family_root:
            # The family-root spelling is a selector for a nested CIU root.
            # Prefer the invocation's nearest committed marker even when the
            # family root itself is also a CIU root; otherwise it would mask
            # a nested root and route records to the wrong offset.
            start = Path(start_dir).resolve()
            try:
                start.relative_to(family_root)
            except ValueError:
                try:
                    selected = resolve_ciu_root(explicit_root)
                except CiuWorkspaceError:
                    return None
            else:
                try:
                    selected = resolve_ciu_root(start)
                except CiuWorkspaceError:
                    return None
        elif (explicit_root / GLOBAL_CONFIG_DEFAULTS).is_file():
            # This CLI's selector accepts the CIU root itself, not an
            # arbitrary descendant that happens to walk up to one.
            selected = explicit_root
        else:
            cli_error(
                f"[ERROR] [S16] explicit --root-folder {explicit_root} is "
                "neither a CIU root nor the Git-family root; refusing to "
                "look up worktree records at a different offset"
            )
            raise SystemExit(2)
    else:
        try:
            selected = resolve_ciu_root(start_dir)
        except CiuWorkspaceError:
            return None
    try:
        Path(selected).resolve().relative_to(Path(git_root).resolve())
    except ValueError:
        return None
    return Path(selected).resolve()


def _extract_define_root(rest: list[str]) -> tuple[Path | None, list[str]]:
    """Pull ``--root-folder``/``--root-folder`` out of *rest* first, before any
    other local parsing on one of CIU-54's fixed sites (S1.1): the
    ``--host`` branches of ``render``/``up``/``down``/``status --gate``, ``up
    --layout``, ``show layouts``, ``secrets host``, ``ssh``, and (S14.7,
    CIU-93) ``host``.

    Consumed here, NOT left in the returned remainder. Every one of these
    sites either forwards its remainder verbatim into a REMOTE argv
    (``render``/``up``/``down``/``status --gate --host``'s ``ssh_exec``/
    ``_push_host`` calls, ``ssh``'s ``cmd_argv``) or hands it to a stricter
    downstream parser. A LOCAL ``--root-folder`` value names a path on THIS
    machine; forwarding it would be nonsensical on the remote side (a foreign
    local path re-parsed by a remote ``ciu``). Extracting it first, with its
    own single-purpose parser, means nothing survives to leak remotely.

    ``allow_abbrev=False`` keeps the exact-spelling contract of the registry
    (SPEC S10.4): only the full flag name or its ``--flag=value`` form is
    consumed.
    """
    import argparse as _ap

    p = CiuArgumentParser(add_help=False, allow_abbrev=False)
    p.add_argument("--root-folder", dest="define_root",
                   type=Path, default=None, metavar="PATH")
    opts, remaining = p.parse_known_args(rest)
    return opts.define_root, remaining


def _resolve_repo_root_deploy(define_root: Path | str | None) -> Path:
    """Resolve repo_root for a REMOTE/push-deploy or listing CLI branch (S1.1,
    CIU-54), or exit cleanly on refusal — the sibling of
    ``_resolve_repo_root_cli`` above, routed through ``deploy.py``'s resolver
    instead of ``dev.py``'s.

    These 8 sites use the same resolver as `deploy.py`'s local/profile-based
    branches of the SAME verbs. Explicit ``--root-folder`` wins; otherwise
    the resolver walks up from the invocation directory to the nearest
    committed CIU marker. Keeping every branch on that resolver prevents a
    remote/listing path from selecting a different root than its local sibling
    and ensures a local path is not accidentally forwarded as a remote
    selector.

    Mirrors ``_resolve_repo_root_cli``'s exit contract: a ``[S1.1]``-tagged
    ``ValueError`` (``deploy.WorkspaceEnvError`` is a ``ValueError`` subclass)
    becomes ``[ERROR] ...`` + ``SystemExit(2)``, never a raw traceback (O3).
    Imported late, matching every other call site in this module.
    """
    from .deploy import resolve_repo_root

    try:
        return resolve_repo_root(define_root)
    except ValueError as exc:
        cli_error(f"[ERROR] {exc}")
        raise SystemExit(2)


def _load_remote_config(repo_root: Path) -> dict:
    """Load configuration before a remote SSH/activation operation.

    Remote transports may need this configuration for Vault-backed host
    credentials. Treating a malformed config as an empty mapping changes that
    security contract, so failure stops before any transport is opened.
    """
    from .deploy import load_global_config

    try:
        return load_global_config(repo_root, **_remote_render_kwargs(repo_root))
    except Exception as exc:
        cli_error(
            f"[ERROR] could not load global configuration for remote operation: {exc}",
        )
        raise SystemExit(2) from exc


#: Names the remote verbs never consume from the control host (CIU-168): its
#: machine facts and workspace identity. A push renders them on the TARGET.
_REMOTE_PLACEHOLDER = "ciu-remote-placeholder"
_REMOTE_NUMERIC_KEYS = frozenset({
    "CONTAINER_UID", "CONTAINER_GID", "DOCKER_UID", "DOCKER_GID", "USER_UID", "USER_GID",
})


def _remote_render_kwargs(repo_root: Path) -> dict:
    """Keyword arguments for the global-config load behind a remote verb (CIU-168).

    The remote verbs (S14.1/S14.2) need the config only for the host inventory,
    Vault-backed credentials and the repo layout, never for the control host's
    machine facts. Every identity/machine key the templates may reference and the
    process environment lacks gets an inert placeholder, and nothing is written
    (``ciu.global.toml`` stays untouched), so a control host without Docker, a
    docker group or a generated ``ciu.env`` can still reach a remote host.
    """
    from .workspace_env import (
        GENERATED_FACT_ENV_KEYS, MACHINE_FACT_ENV_KEYS, REQUIRED_KEYS_CORE,
    )

    environ = dict(os.environ)
    wanted = {*REQUIRED_KEYS_CORE, *GENERATED_FACT_ENV_KEYS.values(),
              *MACHINE_FACT_ENV_KEYS.values()}
    for key in sorted(wanted):
        if environ.get(key):
            continue
        if key in ("REPO_ROOT", "PHYSICAL_REPO_ROOT"):
            environ[key] = str(repo_root)
        elif key in _REMOTE_NUMERIC_KEYS:
            environ[key] = "0"
        else:
            environ[key] = _REMOTE_PLACEHOLDER
    return {"write_rendered": False, "environ": environ}


def _remote_ciu(host_cfg: dict) -> str:
    """The `ciu` executable on the target: the host row's ``remote_ciu`` or ``ciu``."""
    value = host_cfg.get("remote_ciu")
    if value is None or value == "":
        return "ciu"
    if not isinstance(value, str):
        raise ValueError(
            f"[S14.6] 'remote_ciu' must be a string path or command name, got {type(value).__name__}."
        )
    return value


def _remote_words(host_cfg: dict) -> tuple[str, str, str, str]:
    """Validated ``(ciu, ciu_word, bundle, bundle_word)`` of a host row; raises ValueError.

    The ``*_word`` values are shell-ready (quoted, a leading ``~/`` expanded to ``$HOME``).
    """
    from .activate import bundle_dir_for, remote_path_word

    ciu = _remote_ciu(host_cfg)
    bundle = bundle_dir_for(host_cfg)
    return ciu, remote_path_word(ciu, "'remote_ciu'"), bundle, remote_path_word(bundle, "'bundle_dir'")


def _remote_bundle_command(host_cfg: dict, argv: list[str]) -> str:
    """ONE remote command running ``ciu *argv`` inside the pushed bundle (CIU-170).

    Every remote verb after the push (render, down, status --gate) must run in
    the bundle with its ``ciu.env`` exported; a login-directory run has no CIU
    root and no environment. ``ciu env print`` failing (no ``ciu.env``) aborts the
    command with its exit code: the verb never runs without the bundle environment.
    Raises ``ValueError`` for an invalid ``bundle_dir``/``remote_ciu``.
    """
    _ciu, ciu_word, _bundle, bundle_word = _remote_words(host_cfg)
    return (
        f'cd {bundle_word} && {{ ciu_env=$({ciu_word} env print) || exit $?; '
        f'eval "$ciu_env"; }} && {ciu_word} {shlex.join(argv)}'
    )


def _run_remote_bundle(host_cfg: dict, argv: list[str], *, config: dict, repo_root: Path) -> int:
    """Run ``ciu *argv`` in the pushed bundle over ssh; an invalid host row is exit 2."""
    from .transport_ssh import ssh_exec

    try:
        remote_cmd = _remote_bundle_command(host_cfg, argv)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    # Pass the whole command as ONE argv element: ssh space-joins remote
    # args into a single string for the remote login shell to re-parse, so
    # an "sh -c" wrapper here would be re-split and break "&&"/cd. The login
    # shell ssh spawns already interprets the operators natively.
    return ssh_exec(host_cfg, [remote_cmd], config=config, repo_root=repo_root)


def _remote_preflight(host_cfg: dict, host_label: str, *, config: dict, repo_root: Path) -> int:
    """Check the push preconditions on the target and create ``bundle_dir`` (CIU-169).

    The host row is validated FIRST (exit 2, nothing sent). Then one SSH round trip:
    ``ciu`` must resolve on the non-login PATH (exit 127), and ``bundle_dir`` is
    created with its parents because rsync makes only the last path element
    (exit 126). Prints the exact remedy and returns the exit code; 0 means the
    push may proceed.
    """
    from .transport_ssh import ssh_exec

    try:
        ciu, ciu_word, bundle, bundle_word = _remote_words(host_cfg)
    except ValueError as exc:
        print(f"[ERROR] host '{host_label}': {exc}", file=sys.stderr)
        return 2
    probe = (
        f"command -v {ciu_word} >/dev/null 2>&1 || exit 127; "
        f"mkdir -p {bundle_word} || exit 126"
    )
    rc = ssh_exec(host_cfg, [probe], config=config, repo_root=repo_root)
    if rc == 127:
        print(
            f"[ERROR] host '{host_label}': '{ciu}' is not on the remote non-login PATH. "
            "Symlink it into /usr/local/bin on the target, or set "
            f"remote_ciu = \"/absolute/path/to/ciu\" in [deploy.hosts.{host_label}] (CIU-169).",
            file=sys.stderr,
        )
    elif rc == 126:
        print(
            f"[ERROR] host '{host_label}': cannot create bundle_dir '{bundle}' on the target; "
            f"choose a path the ssh user can write in [deploy.hosts.{host_label}] (CIU-169).",
            file=sys.stderr,
        )
    return rc


def _push_host(
    host_cfg: dict,
    host_label: str,
    *,
    repo_root: Path,
    config: dict,
    remaining: list[str],
    extra_exports: str = "",
) -> tuple[str, int]:
    """Push-deploy ONE host: docker_optional advisory, bundle sync, remote up.

    The ONE push implementation (review finding B3) shared by `ciu up --host`
    and each host in `ciu up --layout` (S7.5c) — the two used to reimplement
    this loop independently and had already drifted (the layout path lacked
    the docker_optional advisory). *extra_exports* is a pre-quoted
    ``export VAR=val; ...`` prefix for the remote command (the layout's
    CIU_LAYOUT* vars); empty for the plain --host path.

    Returns ``(stage, rc)`` where *stage* is ``"sync"`` if the bundle
    transfer failed (never reaching the remote) or ``"exec"`` for the
    remote-command result (rc == 0 on success). Never raises SystemExit —
    callers own their own error text and exit semantics, which already
    differ between --host (silent propagation) and --layout (named
    remainder).
    """
    # Advisory (S14.6): a docker-optional host has no docker; the
    # render-on-target path here needs it. Nudge, but do not block.
    if host_cfg.get("docker_optional"):
        print(
            f"[WARN] Host '{host_label}' is marked docker_optional but you are using "
            "the docker render-on-target path. Did you mean 'ciu up --host "
            f"{host_label} --thin'? (S14.6)",
            file=sys.stderr,
        )
    from .activate import bundle_excludes_for
    from .transport_ssh import ssh_exec, ssh_sync
    # Validate the whole host row BEFORE the first byte or ssh command goes out (CIU-169).
    try:
        excludes = bundle_excludes_for(host_cfg)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return "sync", 2
    rc = _remote_preflight(host_cfg, host_label, config=config, repo_root=repo_root)
    if rc != 0:
        return "preflight", rc
    _ciu, ciu_word, bundle_dir, bundle_word = _remote_words(host_cfg)
    # CIU-175: never ship `.ciu` (materialised secrets), `ciu.env`, `.git` or `vol-*`;
    # the host's `bundle_excludes` apply on this path exactly as on `--thin`.
    rc = ssh_sync(host_cfg, str(repo_root), bundle_dir, config=config,
                  repo_root=repo_root, excludes=excludes)
    if rc != 0:
        return "sync", rc
    # Pass the whole command as ONE argv element: ssh space-joins remote
    # args into a single string for the remote login shell to re-parse, so
    # an "sh -c" wrapper here would be re-split and break "&&"/cd. The login
    # shell ssh spawns already interprets the operators natively.
    remote_cmd = (
        extra_exports
        + f"cd {bundle_word} && {ciu_word} env generate && "
        + f"{ciu_word} render && "
        + f"{ciu_word} {shlex.join(['up', *remaining])}"
    )
    rc = ssh_exec(host_cfg, [remote_cmd], config=config, repo_root=repo_root)
    return "exec", rc


def _flag_given(argv: list[str], flag: str) -> bool:
    """True when *flag* selects this verb's code path (S10.4).

    The registry (``cli_registry``) has already validated every spelling: option
    prefixes are refused there and the mode exclusions (``--layout`` vs ``--host``/
    ``--dir``/``--profile``, ``--thin`` needs ``--host``) are declared constraints, so
    this router only needs the exact ``--flag VALUE`` / ``--flag=VALUE`` forms.
    """
    return any(a == flag or a.startswith(flag + "=") for a in argv)


def _env_generate(rest: list[str]) -> int:
    """Handle `ciu env generate [--root-folder PATH]`."""
    import argparse as _ap
    p = CiuArgumentParser(prog="ciu env generate", add_help=True)
    p.add_argument("--root-folder", dest="define_root",
                   type=Path, default=None, metavar="PATH",
                   help="Override repository root directory (no parent walking)")
    p.add_argument("--identity-only", action="store_true", default=False,
                   help=_ap.SUPPRESS)
    opts = p.parse_args(rest)
    from .workspace_env import WorkspaceEnvError
    try:
        if opts.identity_only:
            from .workspace_env import generate_ciu_env, resolve_env_root
            root = resolve_env_root(Path.cwd(), opts.define_root, GLOBAL_CONFIG_DEFAULTS)
            generate_ciu_env(root)
            return 0
        from .deploy import action_generate_env
        return action_generate_env(opts.define_root, Path.cwd())
    except WorkspaceEnvError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


def _shell_export_value(value: str) -> str:
    """Always-single-quoted shell literal for *value*.

    Built on `shlex.quote` (this codebase's existing shell-quoting helper), not
    a second escaping implementation: `shlex.quote` either returns the value
    untouched — only ever for values whose characters are all shell-inert — or
    a fully single-quoted form with embedded quotes escaped as `'\\''`. Wrapping
    the untouched case is therefore lossless, and it keeps every emitted line
    in one shape (`export KEY='value'`) an operator can eyeball.
    """
    quoted = shlex.quote(value)
    if quoted.startswith("'") and quoted.endswith("'"):
        return quoted
    return f"'{quoted}'"


def _env_print(rest: list[str]) -> int:
    """Handle `ciu env print [--root-folder PATH]`.

    Read-only: prints the ALREADY-WRITTEN ciu.env as `export KEY='value'`
    lines and nothing else, for `eval "$(ciu env print)"`. It is deliberately
    NOT called `apply` or `source`: a subprocess structurally cannot mutate its
    parent shell's environment, and naming it that way would document a
    capability that cannot exist. Nothing is (re)generated here.
    """
    import argparse as _ap
    p = CiuArgumentParser(prog="ciu env print", add_help=True)
    p.add_argument("--root-folder", dest="define_root",
                   type=Path, default=None, metavar="PATH",
                   help="Override repository root directory (no parent walking)")
    opts = p.parse_args(rest)
    from .workspace_env import (
        WorkspaceEnvError,
        parse_workspace_env,
        resolve_env_root,
    )
    # EVERY way this can fail — an unresolvable root, an absent/unreadable/
    # non-regular ciu.env, a malformed entry, a non-UTF-8 byte — returns a
    # clean `[ERROR]` + exit 1. A hand-edited or mis-created ciu.env is an
    # ordinary operator mistake, not an internal fault, so none of them
    # deserves a traceback.
    #
    # `is_file()` rather than `exists()`: a DIRECTORY named ciu.env satisfies
    # exists(), and the read then raises IsADirectoryError.
    #
    # The except clause follows worktree.py's `(OSError, WorkspaceEnvError)`
    # reader but adds `UnicodeDecodeError` explicitly, because that pattern
    # alone does NOT cover a non-UTF-8 byte: UnicodeDecodeError derives from
    # ValueError, not OSError, so `read_text(encoding="utf-8")` escapes both
    # arms. Named rather than folded into a bare `ValueError` so the reason
    # it is listed separately stays visible.
    try:
        root = resolve_env_root(Path.cwd(), opts.define_root, GLOBAL_CONFIG_DEFAULTS)
    except WorkspaceEnvError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    env_file = root / WORKSPACE_ENV
    if not env_file.is_file():
        print(
            f"[ERROR] No readable {WORKSPACE_ENV} at {root} — nothing to "
            f"print. Run: ciu env generate",
            file=sys.stderr,
        )
        return 1
    try:
        entries = parse_workspace_env(env_file)
    except (OSError, UnicodeDecodeError, WorkspaceEnvError) as exc:
        print(f"[ERROR] could not read {env_file}: {exc}", file=sys.stderr)
        return 1
    # `parse_workspace_env` returns a fully-built dict, so nothing reaches
    # stdout unless the WHOLE file parsed. Keeping the print loop outside the
    # try preserves that (and keeps a BrokenPipeError from `ciu env print |
    # head` out of the error path, where it would be misreported as a
    # ciu.env problem).
    for key, value in entries.items():
        print(f"export {key}={_shell_export_value(value)}")
    return 0


def _iops_baseline(rest: list[str]) -> int:
    """Handle `ciu iops-baseline [--path P] [--runtime N] [--force]` (S15.9)."""
    import argparse as _ap
    from .governance import run_iops_baseline
    p = CiuArgumentParser(prog="ciu iops-baseline", add_help=False)
    p.add_argument("--path", dest="path", type=Path, default=None, metavar="PATH")
    p.add_argument("--runtime", dest="runtime", type=int, default=10, metavar="N")
    p.add_argument("--force", action="store_true", default=False)
    opts = p.parse_args(rest)
    if opts.runtime < 1:
        cli_error("ciu iops-baseline: --runtime must be a positive integer.")
        return 2
    return run_iops_baseline(opts.path, runtime_s=opts.runtime, force=opts.force)


def _ksm(rest: list[str]) -> int:
    """Handle `ciu ksm build [--force]` (S15.17, CIU-17).

    The explicit verb. `ciu up`/`ciu render` also build the shim implicitly when
    `governance.ksm_optin = "builtin"` and no cached artifact exists, so this is
    a convenience (pre-warm a fresh worktree, force a rebuild, see the errors
    directly) rather than a required step.
    """
    import argparse as _ap

    from . import ksm as ksm_mod
    # CIU-10's reconciliation lives here (a pre-set PHYSICAL_REPO_ROOT wins only
    # when it agrees with mountinfo) — reuse it rather than re-deriving a second,
    # weaker answer to the same question.
    from .workspace_env import _detect_physical_repo_root

    p = CiuArgumentParser(prog="ciu ksm", add_help=False)
    p.add_argument("action", choices=["build"])
    p.add_argument("--force", action="store_true", default=False)
    p.add_argument("--root-folder", dest="define_root", default=None, metavar="PATH")
    opts = p.parse_args(rest)

    repo_root = _resolve_repo_root_cli(opts.define_root, Path.cwd())
    try:
        physical_root = _detect_physical_repo_root(repo_root)
        path = ksm_mod.build(repo_root, physical_root, force=opts.force)
    except ksm_mod.KsmBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(path)
    return 0


def _provenance(rest: list[str]) -> int:
    """Handle `ciu provenance [--ignore-mismatch] [--no-preflight] [--json]`.

    Verify that the RUNNING containers were built from the commit under test,
    before a live/integration lane is allowed to produce evidence. Standalone
    today; this is the check `ciu test` will call once that surface exists
    (docs/DESIGN-NOTES.md D7).

    This is the ONLY place that turns `verify_running_provenance`'s verdict
    into prose, a raise, or a warning — the function itself never prints and
    never raises, so `--json` can print ONLY the JSON document, never prose
    mixed onto the same stream.
    """
    import argparse as _ap
    import json as _json

    p = CiuArgumentParser(prog="ciu provenance", add_help=False)
    p.add_argument("--ignore-mismatch", "--force", dest="ignore_mismatch",
                   action="store_true", default=False)
    p.add_argument("--no-preflight", action="store_true", default=False)
    p.add_argument("--json", dest="json_output", action="store_true", default=False)
    p.add_argument("--root-folder", dest="define_root", default=None, metavar="PATH")
    opts = p.parse_args(rest)

    # This is an explicit break-glass bypass, not a lesser provenance verdict.
    # It must happen before configuration, Git, and Docker access: each of those
    # could independently refuse or fail while the operator requested no check.
    # A JSON result would falsely look like evidence, so reject that combination
    # rather than inventing a synthetic "skipped" verdict outside S17.3's grammar.
    if opts.no_preflight:
        if opts.json_output:
            p.error("--no-preflight cannot be combined with --json: no provenance verdict is produced")
        print("[INFO] --no-preflight: skipping provenance check")
        return 0

    # The evidence path deliberately imports its deployment closure only after
    # the break-glass return.  A requested no-check bypass must remain useful
    # even when that closure (or its optional environment) is itself broken.
    from .deploy import (
        ProvenanceResult,
        load_global_config,
        verify_running_provenance,
        warn,
    )

    repo_root = _resolve_repo_root_cli(opts.define_root, Path.cwd())
    try:
        config = load_global_config(repo_root)
    except Exception as exc:
        print(f"ciu provenance: could not load the global config: {exc}",
              file=sys.stderr)
        return 2

    deploy_cfg = config.get("deploy", {})
    project, env_tag = deploy_cfg.get("project_name"), deploy_cfg.get("environment_tag")
    if not project or not env_tag:
        # No instance identity means no way to tell THIS instance's containers
        # from a sibling worktree's, and a host-wide verdict would be wrong in
        # both directions. Refuse to answer rather than answer wrongly. Emitted
        # here, BEFORE verify_running_provenance is ever called — there is no
        # project_prefix to scope a check with.
        result = ProvenanceResult(
            schema_version=2, instance=None, commit_under_test=None,
            tree_state=None, containers=None, overall="refused-no-identity",
        )
        if opts.json_output:
            print(_json.dumps(result.to_dict(), indent=2))
        else:
            print("ciu provenance: deploy.project_name and deploy.environment_tag "
                  "are required to scope the check to this instance (S8.7).",
                  file=sys.stderr)
        return 2

    # CIU-39: the declared vendor baseline ([deploy.provenance] vendor_images).
    # Malformed declarations refuse loudly — a silently ignored declaration
    # would certify exactly the deployment it was written to vouch for.
    provenance_cfg = deploy_cfg.get("provenance")
    if provenance_cfg is not None and not isinstance(provenance_cfg, dict):
        print(
            "ciu provenance: [deploy.provenance] must be a TOML table "
            "holding vendor_images (e.g. vendor_images = [\"hashicorp/vault:1.15\"]).",
            file=sys.stderr,
        )
        return 2
    raw_vendor = (provenance_cfg or {}).get("vendor_images", [])
    if not isinstance(raw_vendor, list) or any(
        not isinstance(v, str) or not v.strip() for v in raw_vendor
    ):
        print(
            "ciu provenance: [deploy.provenance] vendor_images must be a list "
            "of non-empty image references (e.g. \"hashicorp/vault:1.15\").",
            file=sys.stderr,
        )
        return 2
    vendor_images = [v.strip() for v in raw_vendor]

    result = verify_running_provenance(f"{project}-{env_tag}", vendor_images=vendor_images)

    if opts.json_output:
        print(_json.dumps(result.to_dict(), indent=2))
        if result.overall == "mismatch" and not opts.ignore_mismatch:
            return 2
        return 0

    if result.overall == "mismatch":
        from .deploy import _image_reference_name

        mismatches = [c for c in (result.containers or []) if c.status == "mismatch"]
        declared_names = {_image_reference_name(v) for v in vendor_images}
        detail_lines = []
        for c in mismatches:
            if _image_reference_name(c.image) in declared_names:
                # Vendor drift: the declaration's artifact was swapped — the
                # evidence is reference-vs-declaration, not commit-vs-label.
                detail_lines.append(
                    f"  {c.name} ({c.image}): not the declared vendor reference"
                )
            else:
                detail_lines.append(
                    f"  {c.name} ({c.image}): running {c.labelled_revision}, "
                    f"testing {result.commit_under_test}"
                )
        detail = "\n".join(detail_lines)
        message = (
            f"[S17] {len(mismatches)} running container(s) disagree with their "
            f"expectation:\n{detail}\n"
            "Rebuild and redeploy (`ciu bake` + `ciu up`) so the result describes "
            "the code under test, correct the declaration for drifted vendor "
            "images, or pass --ignore-mismatch to run anyway (the result then "
            "describes whatever is actually running)."
        )
        if not opts.ignore_mismatch:
            print(message, file=sys.stderr)
            return 2
        # --ignore-mismatch downgrades the refusal to a warning and FALLS
        # THROUGH to the "provenance OK" print at the bottom of this
        # function — byte-identical to the OLD CLI: verify_running_provenance
        # used to warn and return normally on this path, and the old
        # cli._provenance then always printed the OK line afterward for any
        # clean run that didn't raise. Yes, a warning immediately followed by
        # "OK" reads as self-contradictory — that contradiction is the
        # documented old behaviour this function promises to reproduce
        # byte-for-byte (S17.3/O1), not a new design choice.
        warn(message)

    if result.overall == "not-verified-dirty":
        warn(
            "[S17] working tree is dirty — provenance NOT verified. Uncommitted "
            "changes are in no image, so no running container can match this "
            "tree; commit before treating a live result as evidence about this "
            "code."
        )
        return 0

    if result.overall == "not-verified-unknown":
        # Not a git checkout — nothing to compare against. Silent, as before:
        # this is not an adverse finding, just nothing to check.
        return 0

    if result.overall == "not-verified-no-evidence":
        warn(
            "[S17] provenance could not be verified — no checkable evidence "
            "(docker unavailable, or no containers with a usable revision "
            "label were found). This is not a refusal; rerun where docker is "
            "reachable for a real verdict, or inspect --json for the full "
            "document."
        )
        return 0

    # Reached for "verified-match", and for "mismatch" + --ignore-mismatch
    # (the fall-through above). Every other case returned earlier, so this
    # never claims "OK" for a run that could not check anything. With declared
    # vendor pins (CIU-39) the OK names what was actually verified: the commit
    # under test and/or the declared vendor references.
    vendor_pinned = [
        c for c in (result.containers or []) if c.status == "vendor-pinned"
    ]
    if vendor_pinned:
        print(
            f"provenance OK — running containers match {result.commit_under_test} "
            f"or their declared vendor references ({len(vendor_pinned)} pinned)"
        )
    else:
        print(f"provenance OK — running containers match {result.commit_under_test}")
    return 0


def _status(rest: list[str]) -> int:
    """Handle `ciu status [--profile NAME] [--json]` (CIU-QOL-6).

    Read-only: resolves the selected stacks via the SAME chain `ciu up
    --profile` uses (`load_global_config` -> `resolve_profiles` ->
    `build_selection`), then reports each one's compose project, containers,
    and health via `deploy.action_status`. No compose up/down/build/exec is
    ever invoked from this path.

    A `RuntimeError` out of `diagnose._inspect` (Docker daemon unreachable)
    is deliberately NOT swallowed into an empty/successful report here — it
    is caught ONLY to turn it into a clean one-line `[ERROR]` message and
    exit 2, never a raw traceback. Any other config/profile-resolution
    failure (e.g. a project-less workspace with no ciu.env identity to name a
    stack's compose project with) is reported the same way: a determination
    failure must be visible, never presented as "nothing running".
    """
    import argparse as _ap

    from .deploy import action_status, build_selection, load_global_config, resolve_profiles

    p = CiuArgumentParser(prog="ciu status", add_help=False)
    p.add_argument("--profile", action="append", default=None, metavar="NAME")
    p.add_argument("--json", dest="json_output", action="store_true", default=False)
    p.add_argument("--root-folder", dest="define_root",
                   type=Path, default=None, metavar="PATH")
    opts = p.parse_args(rest)

    raw_profiles = opts.profile
    if raw_profiles:
        expanded: list[str] = []
        for entry in raw_profiles:
            for part in entry.split(","):
                part = part.strip()
                if part:
                    expanded.append(part)
        cli_profiles: list[str] | None = expanded if expanded else None
    else:
        cli_profiles = None

    repo_root = _resolve_repo_root_cli(opts.define_root, Path.cwd())
    try:
        global_cfg = load_global_config(repo_root)
        profile = resolve_profiles(global_cfg, cli_profiles)
        selection = build_selection(profile)
        return action_status(repo_root, profile, selection, json_output=opts.json_output)
    except (RuntimeError, ValueError) as exc:
        print(f"[ERROR] ciu status: {exc}", file=sys.stderr)
        return 2


def _bake(rest: list[str]) -> int:
    """Handle `ciu bake [targets ...] [--no-cache]` / `ciu bake --profile
    NAME [--no-cache]` (CIU-QOL-7).

    No `--profile`: raw positional targets go straight to `docker buildx bake
    --load` (defaulting to `all` when none are given), with linked-worktree
    image isolation applied when CIU can resolve its generated identity. A
    linked Git checkout without a CIU root is refused because it cannot be
    safely distinguished from the primary image owner.

    With `--profile`, the target list instead comes from the SAME chain
    `ciu up --profile` uses (`load_global_config` -> `resolve_profiles` ->
    `build_selection`, mirroring `_status` above), fed through
    `deploy.collect_bake_targets_from_selection` -- the same pure resolver
    the removed internal `action_build` path used to reuse. Either way the
    result feeds the SAME `docker buildx bake ... --load` invocation, with
    identical revision-stamping (`engine.bake_revision_args()`) and
    `--no-cache` handling -- only the target list's SOURCE changes.

    `--profile` and explicit positional targets are mutually exclusive
    (prefix-aware: `a == "--profile" or a.startswith("--profile=")`, so the
    equals form is caught too -- mirrors the `up --layout` precedent's B2
    fix). Silently picking a winner between the two would invite a
    divergent-build bug, so this is a clear stderr error + exit 2 instead.
    """
    from .engine import bake_revision_args

    no_cache = "--no-cache" in rest
    allow_shared_tag = "--allow-shared-tag" in rest
    positional = [
        a for a in rest if a not in {"--no-cache", "--allow-shared-tag"}
    ]

    has_profile_flag = any(
        a == "--profile" or a.startswith("--profile=") for a in positional
    )
    if has_profile_flag:
        import argparse as _ap

        p = CiuArgumentParser(prog="ciu bake", add_help=False)
        p.add_argument("--profile", action="append", default=None, metavar="NAME")
        opts, remaining = p.parse_known_args(positional)
        if remaining:
            print(
                "ciu bake: --profile is mutually exclusive with explicit "
                "build targets -- --profile resolves the target list from "
                "the selection model (the same chain `ciu up --profile` "
                "uses); pass one or the other, not both.",
                file=sys.stderr,
            )
            return 2

        from .deploy import (
            build_selection,
            collect_bake_targets_from_selection,
            load_global_config,
            resolve_profiles,
        )
        expanded: list[str] = []
        for entry in opts.profile:
            for part in entry.split(","):
                part = part.strip()
                if part:
                    expanded.append(part)
        cli_profiles: list[str] | None = expanded if expanded else None

        repo_root = _resolve_repo_root_cli(None, Path.cwd())
        try:
            global_cfg = load_global_config(repo_root)
            profile = resolve_profiles(global_cfg, cli_profiles)
            selection = build_selection(profile)
        except (RuntimeError, ValueError) as exc:
            print(f"[ERROR] ciu bake: {exc}", file=sys.stderr)
            return 2
        targets = collect_bake_targets_from_selection(selection)
    else:
        targets = positional

    # A primary checkout keeps its historical Buildx argv. A linked checkout
    # scopes every tagged Bake output using the exact resolved Bake plan, so
    # target aliases and consumer-defined Bake files remain intact.
    suffix: str | None = None
    candidate_root = repo_root if has_profile_flag else None
    try:
        from . import worktree
        from .dev import resolve_repo_root

        if candidate_root is None:
            try:
                candidate_root = resolve_repo_root(None, Path.cwd())
            except ValueError as root_error:
                if not allow_shared_tag:
                    try:
                        linked_without_root = _bake_is_linked_checkout_without_ciu_root(
                            Path.cwd()
                        )
                    except ValueError as identity_error:
                        print(f"[ERROR] ciu bake: {identity_error}", file=sys.stderr)
                        return 2
                    if linked_without_root:
                        print(
                            "[ERROR] ciu bake: [CIU-117] this is a linked Git "
                            "worktree without a CIU root, so its image identity "
                            "cannot be resolved; refusing an unscoped Bake. "
                            f"The CIU root lookup failed: {root_error}",
                            file=sys.stderr,
                        )
                        return 2
                candidate_root = None
        if candidate_root is not None:
            suffix = worktree.resolve_worktree_image_tag_suffix(
                candidate_root,
                allow_shared_tag=allow_shared_tag,
            )
    except (RuntimeError, ValueError) as exc:
        print(f"[ERROR] ciu bake: {exc}", file=sys.stderr)
        return 2

    scoped_overrides: list[str] = []
    if suffix is not None:
        from .image_isolation import (
            ImageIsolationError,
            bake_scoped_tags,
            bake_tag_overrides,
            check_primary_image_collisions,
        )

        targets_for_print = targets or ["all"]
        try:
            preview = subprocess.run(
                ["docker", "buildx", "bake", *targets_for_print, "--print"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            print(
                f"[ERROR] ciu bake: could not resolve Buildx image tags: {exc}",
                file=sys.stderr,
            )
            return 2
        if preview.returncode != 0:
            if preview.stderr:
                print(preview.stderr, file=sys.stderr, end="")
            return preview.returncode
        try:
            scoped_tags = bake_scoped_tags(preview.stdout, suffix)
            if scoped_tags:
                from .deploy import resolve_primary_image_references

                primary_tags = resolve_primary_image_references(candidate_root)
                check_primary_image_collisions(scoped_tags, primary_tags)
            scoped_overrides = bake_tag_overrides(preview.stdout, suffix)
        except (ImageIsolationError, RuntimeError, ValueError) as exc:
            detail = str(exc)
            if not detail.startswith("[CIU-117]"):
                detail = f"[CIU-117] {detail}"
            print(f"[ERROR] ciu bake: {detail}", file=sys.stderr)
            return 2
    elif allow_shared_tag:
        print(
            "[WARN] [CIU-117] --allow-shared-tag was supplied; this Bake uses "
            "the declared tags without worktree scoping.",
            file=sys.stderr,
        )

    cmd = ["docker", "buildx", "bake"] + (targets or ["all"]) + ["--load"]
    # Provenance: stamp the source revision so a running container can be
    # traced back to the commit it was built from (engine.bake_revision_args).
    cmd += bake_revision_args()
    cmd += scoped_overrides
    if no_cache:
        cmd.append("--no-cache")
    return subprocess.call(cmd)


def _bake_is_linked_checkout_without_ciu_root(start: Path) -> bool:
    """Classify a rootless Bake invocation without guessing Git ownership.

    A standalone source tree or the primary checkout can keep legacy Bake
    behavior without a CIU root. A linked checkout cannot: there is no
    generated identity with which to isolate image tags. Broken or ambiguous
    Git inventory is therefore a refusal rather than an unscoped build.
    """
    from . import worktree
    from .workspace import CiuWorkspaceError, resolve_worktree_git_root

    start = Path(start).resolve()
    try:
        git_root = resolve_worktree_git_root(start)
    except CiuWorkspaceError as exc:
        try:
            has_git_marker = any(
                (parent / ".git").exists() or (parent / ".git").is_symlink()
                for parent in (start, *start.parents)
            )
        except OSError as marker_error:
            raise ValueError(
                f"[CIU-117] cannot determine Git ownership for {start}: {marker_error}"
            ) from marker_error
        if has_git_marker:
            raise ValueError(
                f"[CIU-117] cannot resolve Git ownership for {start}; "
                "refusing an unscoped Bake"
            ) from exc
        return False

    try:
        entries = worktree.list_worktrees(git_root)
    except (RuntimeError, ValueError) as exc:
        raise ValueError(
            f"[CIU-117] cannot inspect Git worktree ownership for {git_root}; "
            "refusing an unscoped Bake"
        ) from exc
    current = [entry for entry in entries if entry.path.resolve() == git_root.resolve()]
    primaries = [entry for entry in entries if entry.is_primary]
    if len(current) != 1 or len(primaries) != 1:
        raise ValueError(
            f"[CIU-117] cannot identify exactly one current and primary Git "
            f"worktree for {git_root}; refusing an unscoped Bake"
        )
    return not current[0].is_primary


def _worktree_exec(rest: list[str], resolve_repo_root) -> int:
    """`ciu worktree exec LOGICAL [--target ALIAS] -- ARGV...`.

    Parsed manually because argparse REMAINDER cannot both consume a
    `--target` option and keep a `--` separator byte-identical for the child.
    The separator is mandatory; a leading-dash argument is never misparsed as
    a CIU flag. Returns the exact child exit code.
    """
    from . import worktree as wt_mod

    if len(rest) < 2:
        raise wt_mod.WorktreeError(
            "[S16] `ciu worktree exec LOGICAL [--target ALIAS] -- ARGV...` "
            "requires a logical name and a `--` separator"
        )
    logical_name = rest[1]
    define_root: str | None = None
    target_alias: str | None = None
    argv: list[str] = []
    i = 2
    while i < len(rest):
        token = rest[i]
        if token == "--root-folder":
            if i + 1 >= len(rest):
                raise wt_mod.WorktreeError("[S16] --root-folder requires a PATH")
            define_root = rest[i + 1]
            i += 2
            continue
        if token == "--target":
            if i + 1 >= len(rest):
                raise wt_mod.WorktreeError("[S16] --target requires an alias")
            target_alias = rest[i + 1]
            i += 2
            continue
        if token == "--":
            argv = ["--"] + rest[i + 1:]
            break
        raise wt_mod.WorktreeError(
            f"[S16] unexpected argument {token!r} for `ciu worktree exec`; "
            "usage: ciu worktree exec LOGICAL [--target ALIAS] -- ARGV..."
        )
    git_root = _resolve_worktree_git_root_cli(define_root, Path.cwd())
    selected_ciu_root = _resolve_worktree_ciu_root_cli(
        git_root, define_root, Path.cwd()
    )
    repo_root = selected_ciu_root or git_root
    if target_alias is not None:
        return wt_mod.exec_target_instance(
            repo_root, logical_name, target_alias, argv
        )
    return wt_mod.exec_instance(repo_root, logical_name, argv)


def _seed_selected_root_env(define_root: Path | None) -> None:
    """Seed this process from the selected root's instance record (CIU-139).

    `resolve` and `exec` render Compose, so they need the same identity and
    machine facts `ciu up`/`check` get from ``bootstrap_workspace_env``; an
    unprimed shell must not be a failure. The bootstrap is read-only: a root
    with no record refuses naming the file and `ciu env generate`.
    """
    from .config_constants import GLOBAL_CONFIG_DEFAULTS as _defaults
    from .workspace_env import REQUIRED_KEYS_CORE, bootstrap_workspace_env

    bootstrap_workspace_env(
        start_dir=Path.cwd(),
        define_root=define_root,
        defaults_filename=_defaults,
        generate_env=False,
        update_cert_permission=False,
        required_keys=REQUIRED_KEYS_CORE,
    )


def _resolve_identities_cli(rest: list[str]) -> int:
    """CLI adapter for the read-only ``ciu resolve`` data surface."""
    define_root, rest = _extract_define_root(rest)
    parser = CiuArgumentParser(prog="ciu resolve", add_help=False, allow_abbrev=False)
    parser.add_argument("--profile", action="append", default=None, metavar="NAME")
    parser.add_argument("--stack", default=None)
    parser.add_argument("--service", default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--json", dest="json_output", action="store_true")
    opts = parser.parse_args(rest)
    if not opts.json_output:
        print("[S18] `ciu resolve` requires --json", file=sys.stderr)
        return 2
    repo_root = _resolve_repo_root_deploy(define_root)
    from .deploy import resolve_identities
    try:
        _seed_selected_root_env(define_root)
        document = resolve_identities(
            repo_root,
            stack=opts.stack,
            service=opts.service,
            profiles=opts.profile,
            live=opts.live,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1 if isinstance(exc, RuntimeError) else 2
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


def _exec_service_cli(rest: list[str]) -> int:
    """Parse CIU-owned flags and preserve every post-``--`` argv byte."""
    if "--" not in rest:
        print(
            "[S18] `ciu exec <stack>[:<service>] -- ARGV...` requires `--`",
            file=sys.stderr,
        )
        return 2
    split = rest.index("--")
    command_args = rest[split + 1:]
    define_root, before_separator = _extract_define_root(rest[:split])
    # Keep the explicit no-help and no-abbreviation contract: `--help`
    # belongs to the top-level dispatcher and child argv stays untouched.
    parser = CiuArgumentParser(prog="ciu exec", add_help=False, allow_abbrev=False)
    parser.add_argument("--profile", action="append", default=None, metavar="NAME")
    parser.add_argument("selector", nargs="?")
    opts = parser.parse_args(before_separator)
    if not opts.selector or not command_args:
        print(
            "[S18] `ciu exec <stack>[:<service>] -- ARGV...` requires a selector "
            "and at least one command argument",
            file=sys.stderr,
        )
        return 2
    repo_root = _resolve_repo_root_deploy(define_root)
    from .deploy import exec_service
    try:
        _seed_selected_root_env(define_root)
        return exec_service(
            repo_root, opts.selector, command_args, profiles=opts.profile
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1 if isinstance(exc, RuntimeError) else 2


def _worktree(rest: list[str]) -> int:
    """Handle the S16 managed-worktree lifecycle."""
    import argparse as _ap

    from . import worktree as wt_mod

    if rest and rest[0] == "exec":
        try:
            return _worktree_exec(rest, _resolve_worktree_git_root_cli)
        except wt_mod.WorktreeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    p = CiuArgumentParser(prog="ciu worktree", add_help=False)
    sub = p.add_subparsers(dest="action", required=True)

    def add_create_options(
        parser,
        *,
        base_default="main",
        worktree_dir_default=wt_mod.DEFAULT_WORKTREE_DIR,
        allow_network_adoption=False,
    ) -> None:
        parser.add_argument("--base", default=base_default, metavar="REF")
        parser.add_argument(
            "--profile", default=None, metavar="P1,P2",
            help="initial service-profile selection for the instance overlay",
        )
        parser.add_argument("--worktree-dir", dest="worktree_dir",
                            default=worktree_dir_default, metavar="DIR")
        parser.add_argument("--name", dest="display_name", default=None, metavar="DISPLAY")
        parser.add_argument("--prefix", default=None, metavar="PROJECT_OR_COMPONENT")
        parser.add_argument("--feature", default=None, metavar="FEATURE")
        parser.add_argument("--branch", default=None, metavar="BRANCH")
        parser.add_argument("--path", type=Path, default=None, metavar="PATH")
        parser.add_argument("--shared-infra", dest="shared_infra", default=None, metavar="REF")
        parser.add_argument("--shared-infra-services", dest="shared_infra_services",
                            default=None, metavar="S1,S2")
        parser.add_argument("--shared-infra-ref-projects", dest="shared_infra_ref_projects",
                            default=None, metavar="R1,R2")
        parser.add_argument("--shared-infra-ref-services", dest="shared_infra_ref_services",
                            default=None, metavar="A1,A2=S2")
        if allow_network_adoption:
            parser.add_argument(
                "--adopt-existing-network", action="store_true", default=False,
                help="explicitly claim an unlabelled existing network and record its Docker id",
            )
        parser.add_argument("--json", action="store_true", default=False)

    p_create = sub.add_parser("create", add_help=False)
    p_create.add_argument("logical_name")
    p_create.add_argument(
        "--up", action="store_true", default=False,
        help="start the worktree's declared environment after creation",
    )
    add_create_options(p_create)

    p_ensure = sub.add_parser("ensure", add_help=False)
    p_ensure.add_argument("logical_name")
    add_create_options(
        p_ensure, base_default=None, worktree_dir_default=None,
        allow_network_adoption=True,
    )

    p_adopt = sub.add_parser("adopt", add_help=False)
    p_adopt.add_argument("logical_name")
    p_adopt.add_argument("path")
    p_adopt.add_argument(
        "--profile", default=None, metavar="P1,P2",
        help="initial service-profile selection for the instance overlay",
    )
    p_adopt.add_argument(
        "--adopt-existing-network", action="store_true", default=False,
        help="explicitly claim an unlabelled existing network and record its Docker id",
    )
    p_adopt.add_argument("--shared-infra", dest="shared_infra", default=None, metavar="REF")
    p_adopt.add_argument("--shared-infra-services", dest="shared_infra_services",
                         default=None, metavar="S1,S2")
    p_adopt.add_argument("--shared-infra-ref-projects", dest="shared_infra_ref_projects",
                         default=None, metavar="R1,R2")
    p_adopt.add_argument("--shared-infra-ref-services", dest="shared_infra_ref_services",
                         default=None, metavar="A1,A2=S2")
    p_adopt.add_argument("--json", action="store_true", default=False)

    p_rm = sub.add_parser("rm", add_help=False)
    p_rm.add_argument("name")
    p_rm.add_argument("-y", "--yes", action="store_true", default=False)
    p_rm.add_argument("--force", action="store_true", default=False)
    rm_branch_mode = p_rm.add_mutually_exclusive_group()
    rm_branch_mode.add_argument(
        "--keep-branch", action="store_true", default=False,
        help="keep the branch even when it is merged",
    )
    rm_branch_mode.add_argument(
        "--delete-branch", action="store_true", default=False,
        help="explicitly delete the branch, including an unmerged branch",
    )
    p_rm.add_argument(
        "--discard-protected-state", action="append", default=[], metavar="BRANCH",
        help="explicitly discard protected ignored state for this exact branch",
    )
    p_rm.add_argument(
        "--discard-changes", action="append", default=[], metavar="BRANCH",
        help="explicitly discard tracked or untracked changes for this exact branch",
    )
    p_rm.add_argument(
        "--root-escalation", action="append", default=[], metavar="BRANCH",
        help="after a confirmed permission failure, retry blocked descendants for this exact branch",
    )
    p_rm.add_argument("--json", action="store_true", default=False)

    p_list = sub.add_parser("list", add_help=False)
    p_list.add_argument("--json", action="store_true", default=False)

    p_inspect = sub.add_parser("inspect", add_help=False)
    p_inspect.add_argument("logical_name")
    p_inspect.add_argument("--json", action="store_true", default=False)

    # S16.9 — the explicit operator verb over the ownership lease. The three
    # modes are mutually exclusive AND required: there is no "default" lease
    # operation, because every one of them is a deliberate statement about
    # who owns this instance's resources.
    p_lease = sub.add_parser("lease", add_help=False)
    p_lease.add_argument("logical_name")
    p_lease_mode = p_lease.add_mutually_exclusive_group(required=True)
    p_lease_mode.add_argument("--extend", default=None, metavar="DURATION")
    p_lease_mode.add_argument("--perpetual", action="store_true", default=False)
    p_lease_mode.add_argument("--release", action="store_true", default=False)
    p_lease.add_argument("--json", action="store_true", default=False)

    # CIU-142 — re-record fork_point_sha after a legitimate rebase.
    p_refork = sub.add_parser("refork", add_help=False)
    p_refork.add_argument("logical_name")
    p_refork.add_argument("--onto", default=None, metavar="TRUNK_REF")
    p_refork.add_argument("--json", action="store_true", default=False)

    p_branches = sub.add_parser("branches", add_help=False)
    p_branches.add_argument("--base", default="main", metavar="REF")
    p_branches.add_argument(
        "-y", "--yes", action="store_true", default=False,
        help="remove merged, clean branches without protected ignored state (default: survey only)",
    )
    p_branches.add_argument(
        "--discard-protected-state", action="append", default=[], metavar="BRANCH",
        help="with -y, discard ignored resumable state for this exact branch",
    )
    p_branches.add_argument("--json", action="store_true", default=False)

    # S16.10 — the Docker half of CIU-25. Survey-only unless -y is given, and
    # --category can only ever NARROW what -y acts on: the two never-reaped
    # categories are not selectable at all (worktree.resolve_reap_categories
    # refuses them), so no flag combination reaches them.
    p_reap = sub.add_parser("reap", add_help=False)
    p_reap.add_argument(
        "-y", "--yes", action="store_true", default=False,
        help="reap the provably disposable groups (default: survey only)",
    )
    p_reap.add_argument("--category", default=None, metavar="C1,C2")
    p_reap.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="with -y: print the exact commands instead of running them",
    )
    p_reap.add_argument("--json", action="store_true", default=False)

    p_up = sub.add_parser("up", add_help=False)
    p_up.add_argument("logical_name")
    p_up.add_argument(
        "--all", action="store_true", default=False,
        help="start the full default deploy set, ignoring [ciu.worktree].up",
    )

    # `exec` is parsed manually in `_worktree_exec` (argparse REMAINDER can
    # neither consume a `--target` option nor keep a `--` separator intact),
    # so no exec subparser is registered here.

    for parser in (
        p, p_create, p_ensure, p_adopt, p_rm, p_list, p_inspect,
        p_lease, p_refork, p_up, p_branches, p_reap,
    ):
        parser.add_argument("--root-folder", dest="define_root", default=None,
                            metavar="PATH")
    opts = p.parse_args(rest)

    # The PRIMARY checkout. `git worktree` operations are repo-wide, so they run
    # from here even when the target is another checkout.
    repo_root = _resolve_worktree_git_root_cli(
        getattr(opts, "define_root", None), Path.cwd()
    )

    try:
        selected_ciu_root = _resolve_worktree_ciu_root_cli(
            repo_root, getattr(opts, "define_root", None), Path.cwd()
        )
        lifecycle_root = selected_ciu_root or repo_root
        if opts.action in ("create", "ensure", "adopt"):
            overlay_requested = any(
                getattr(opts, key, None) is not None
                for key in (
                    "profile", "shared_infra", "shared_infra_services",
                    "shared_infra_ref_projects", "shared_infra_ref_services",
                )
            )
            network_claim_requested = bool(
                getattr(opts, "adopt_existing_network", False)
            )
            if (overlay_requested or network_claim_requested) and selected_ciu_root is None:
                raise wt_mod.WorktreeError(
                    "[S16] initial overlay and existing-network options require a "
                    "selected CIU root; run from that root or pass its path with "
                    "--root-folder"
                )
        def emit_record(operation: str, record) -> None:
            if getattr(opts, "json", False):
                print(json.dumps(
                    wt_mod.build_instance_document(operation, record),
                    sort_keys=True,
                ))
            else:
                print(f"worktree ready: {record.git_worktree_path}")
                print(f"  CIU root: {record.ciu_root}")
                if not (opts.action == "create" and opts.up):
                    print(f"  next: cd {record.ciu_root} && ciu up")

        if opts.action in ("create", "ensure"):
            lifecycle = wt_mod.create if opts.action == "create" else wt_mod.ensure
            record = lifecycle(
                lifecycle_root, opts.logical_name, base=opts.base,
                profile=opts.profile,
                worktree_dir=opts.worktree_dir, display_name=opts.display_name,
                prefix=opts.prefix, feature=opts.feature, branch=opts.branch,
                path=opts.path, shared_infra=opts.shared_infra,
                shared_infra_services=opts.shared_infra_services,
                shared_infra_ref_projects=opts.shared_infra_ref_projects,
                shared_infra_ref_services=opts.shared_infra_ref_services,
                **(
                    {"adopt_existing_network": opts.adopt_existing_network}
                    if opts.action == "ensure" else {}
                ),
            )
            emit_record(opts.action, record)
            if opts.action == "create" and opts.up:
                rc = wt_mod.up_instance(lifecycle_root, opts.logical_name)
                if rc != 0:
                    print(
                        f"[ERROR] worktree creation succeeded but `ciu worktree "
                        f"up {opts.logical_name}` failed (exit {rc}); the "
                        f"checkout is preserved. Retry with `ciu worktree up "
                        f"{opts.logical_name}`.",
                        file=sys.stderr,
                    )
                    return rc
            return 0

        if opts.action == "adopt":
            record = wt_mod.adopt(
                lifecycle_root, opts.logical_name, opts.path,
                profile=opts.profile,
                adopt_existing_network=opts.adopt_existing_network,
                shared_infra=opts.shared_infra,
                shared_infra_services=opts.shared_infra_services,
                shared_infra_ref_projects=opts.shared_infra_ref_projects,
                shared_infra_ref_services=opts.shared_infra_ref_services,
            )
            emit_record("adopt", record)
            return 0

        if opts.action == "rm":
            document = wt_mod.remove_document(
                lifecycle_root, opts.name, yes=opts.yes, force=opts.force,
                keep_branch=opts.keep_branch, delete_branch=opts.delete_branch,
                root_escalation=opts.root_escalation,
                discard_protected_state=opts.discard_protected_state,
                discard_changes=opts.discard_changes,
                json_output=opts.json,
            )
            if getattr(opts, "json", False):
                print(json.dumps(document, sort_keys=True))
                return 0
            print(f"removed: {document['removed_path']}")
            branch = document["branch"]
            if branch["outcome"] == "deleted":
                print(f"deleted branch: {branch['name']} (tip {branch['tip']})")
            elif branch["outcome"] == "kept-unmerged":
                print(
                    f"kept branch: {branch['name']} at {branch['tip']} "
                    f"({branch['reason']})"
                )
            elif branch["outcome"] == "kept-by-option":
                print(f"kept branch: {branch['name']} at {branch['tip']} (--keep-branch)")
            else:
                print("branch removal: skipped-detached")
            for item in document["removed_by_clean"]:
                print(
                    "removed ignored state during clean: "
                    f"{wt_mod._format_ignored_state_item(item)}"
                )
            for item in document["discarded_state"]:
                print(
                    f"discarded ignored state: {item['path']} [{item['class']}] "
                    f"{item['file_count']} file(s), {item['bytes']} bytes"
                )
            for path in document["discarded_changes"]:
                print(f"discarded uncommitted change: {path}")
            for phase, status in document["phase_report"]["phases"].items():
                print(f"phase {phase}: {status}")
            return 0

        if opts.action == "inspect":
            doc = wt_mod.inspect_instance(lifecycle_root, opts.logical_name)
            if getattr(opts, "json", False):
                print(json.dumps(doc, sort_keys=True))
            else:
                git = doc["git"]
                print(f"worktree inspect: {doc['instance']['logical_name']}")
                print(f"  status: {doc['status']}")
                print(f"  git path: {git['path']}")
                print(f"  branch: {git['branch']}")
                print(f"  HEAD: {git['head']}")
                print(f"  dirty: {git['dirty']}")
                fork = doc.get("fork_point")
                if fork is not None:
                    print(f"  fork point: {fork['status']}")
                if fork is not None and fork.get("remedy"):
                    print(
                        f"  recorded {fork['recorded']} != merge-base "
                        f"{fork['merge_base']}; run `{fork['remedy']}`"
                    )
            return 0

        if opts.action == "refork":
            doc = wt_mod.refork_instance(
                lifecycle_root, opts.logical_name, onto=opts.onto
            )
            if getattr(opts, "json", False):
                print(json.dumps(doc, sort_keys=True))
            else:
                info = doc["refork"]
                print(f"worktree refork: {doc['instance']['logical_name']}")
                print(f"  trunk: {info['trunk']}")
                print(f"  previous fork point: {info['previous_fork_point_sha']}")
                print(f"  fork point: {info['fork_point_sha']}")
                print(f"  changed: {info['changed']}")
            return 0

        if opts.action == "lease":
            record = wt_mod.apply_lease(
                lifecycle_root, opts.logical_name, extend=opts.extend,
                perpetual=opts.perpetual, release=opts.release,
            )
            if getattr(opts, "json", False):
                print(json.dumps(
                    wt_mod.build_instance_document("lease", record),
                    sort_keys=True,
                ))
            else:
                lease = record.lease
                print(f"worktree lease: {record.logical_name}")
                if lease is None:
                    print("  lease: none (released)")
                else:
                    print(f"  mode: {lease.mode}")
                    print(f"  holder: {lease.holder}")
                    print(f"  renewed: {lease.renewed_at_utc}")
                    print(f"  expires: {lease.expires_at_utc or 'never (perpetual)'}")
            return 0

        if opts.action == "up":
            if opts.all:
                return wt_mod.up_instance(
                    lifecycle_root, opts.logical_name, all_profiles=True,
                )
            return wt_mod.up_instance(lifecycle_root, opts.logical_name)

        if opts.action == "branches":
            doc = wt_mod.prune_branches(
                lifecycle_root, base=opts.base, yes=opts.yes,
                discard_protected_state=opts.discard_protected_state,
            ) if opts.yes else wt_mod.branch_hygiene(lifecycle_root, base=opts.base)
            # ONE exit-code decision, decided ABOVE the output-format branch so
            # every mode shares it: a partial prune is never a silent success.
            # It used to live inside the human/else arm only, so `--json`
            # reported exit 0 on the same partial document (review finding);
            # duplicating the check into both arms would just invite the drift
            # back, so it is hoisted rather than copied.
            code = 1 if doc.get("status") == "partial" else 0
            if getattr(opts, "json", False):
                print(json.dumps(doc, sort_keys=True))
            else:
                counts = doc["counts"]
                print(
                    f"branch hygiene vs '{doc['base']}' — "
                    f"{counts['prunable']} prunable, "
                    f"{counts['merged-dirty']} merged-dirty, "
                    f"{counts['merged-protected']} merged-protected, "
                    f"{counts['merged-ignored-unknown']} merged-ignored-unknown, "
                    f"{counts['unmerged']} unmerged, "
                    f"{counts['managed-instance']} managed-instance, "
                    f"{counts['current']} current, {counts['base']} base"
                )
                for category in wt_mod.BRANCH_CATEGORIES:
                    rows = [b for b in doc["branches"] if b["category"] == category]
                    if not rows:
                        continue
                    print(f"\n{category}:")
                    for b in rows:
                        where = f" @ {b['checkout']}" if b["checkout"] else ""
                        dirt = " dirty" if b["dirty"] else ""
                        ciu = (
                            f"  ciu:{b['ciu_instance']['logical_name']}"
                            f"({b['ciu_instance']['state']})"
                            if b["ciu_instance"] else ""
                        )
                        print(
                            f"  {b['name']}{where}  ahead {b['ahead']} "
                            f"behind {b['behind']} changed "
                            f"{b['changed_files']} file(s)  last "
                            f"{b['last_commit_at'][:10]}{dirt}{ciu}"
                        )
                        for ignored in b.get("ignored_state") or []:
                            suffix = " (capped)" if ignored["capped"] else ""
                            print(
                                f"      ignored {ignored['path']} [{ignored['class']}] "
                                f"{ignored['file_count']} file(s), {ignored['bytes']} bytes{suffix}"
                            )
                        if b.get("ignored_state_error"):
                            print(f"      ignored state unavailable: {b['ignored_state_error']}")
                # survey/prune documents always carry the hint
                print(f"\n{doc['hint']}")
                if doc["operation"] == "branches-prune":
                    # The prune's outcome is the headline, not the re-survey:
                    # removed/failed are named explicitly (the non-zero exit on
                    # a partial prune is decided once, above).
                    for name in doc.get("removed", []):
                        print(f"removed: {name}")
                    for f in doc.get("failed", []):
                        print(f"FAILED: {f['branch']} — {f['reason']}")
                    for item in doc.get("skipped", []):
                        print(f"skipped: {item['branch']} — {item['reason']}")
                    for item in doc.get("discarded", []):
                        print(
                            f"discarded: {item['branch']} {item['path']} "
                            f"[{item['class']}] {item['file_count']} file(s), "
                            f"{item['bytes']} bytes"
                        )
            return code

        if opts.action == "reap":
            doc = wt_mod.reap_groups(
                lifecycle_root, yes=opts.yes, categories=opts.category,
                dry_run=opts.dry_run,
            )
            # ONE exit-code decision, hoisted above the output-format branch
            # for the same reason `branches` hoists its own (ciu-P28): a
            # partial destructive pass must never report success in ANY output
            # mode, and duplicating the check into both arms invites the drift
            # straight back.
            code = 1 if doc["status"] == "partial" else 0
            if getattr(opts, "json", False):
                print(json.dumps(doc, sort_keys=True))
                return code
            counts = doc["counts"]
            print(
                "docker resource reap — "
                + ", ".join(f"{c} {counts[c]}" for c in wt_mod.REAP_CATEGORIES)
            )
            for category in wt_mod.REAP_CATEGORIES:
                rows = [g for g in doc["groups"] if g["category"] == category]
                if not rows:
                    continue
                print(f"\n{category}:")
                for g in rows:
                    print(
                        f"  {g['key']}  {len(g['containers'])} container(s) "
                        f"{len(g['volumes'])} volume(s) "
                        f"{len(g['networks'])} network(s)"
                    )
                    print(f"      {g['reason']}")
            for finding in doc["findings"]:
                print(f"\nFINDING [{finding['kind']}] {finding['path']}")
                print(f"  {finding['detail']}")
            for entry in doc.get("plan", []):
                print(f"\nwould reap {entry['group']} ({entry['category']}):")
                for command in entry["commands"]:
                    print(f"  {command}")
            for entry in doc.get("reaped", []):
                print(f"reaped: {entry['group']}")
                for note in entry["notes"]:
                    print(f"    {note}")
            for entry in doc.get("failed", []):
                print(f"FAILED: {entry['group']} — {entry['reason']}")
            print(f"\n{doc['hint']}")
            return code

        # Every action above returned; the only remaining action is "list"
        # (argparse's required subparsers make it one of the registered set).
        if getattr(opts, "json", False):
            print(json.dumps(wt_mod.list_instances(lifecycle_root), sort_keys=True))
        else:
            for info in wt_mod.list_worktrees(lifecycle_root):
                tag = "  (primary)" if info.is_primary else ""
                print(f"{info.head}  {info.branch:<40} {info.path}{tag}")
        return 0
    except wt_mod.WorktreeError as exc:
        if (
            "opts" in locals()
            and getattr(opts, "action", None) == "rm"
            and getattr(opts, "json", False)
            and getattr(exc, "document", None) is not None
        ):
            print(json.dumps(exc.document, sort_keys=True))
            print(str(exc), file=sys.stderr)
            return 2
        print(str(exc), file=sys.stderr)
        return 2


def _host(rest: list[str]) -> int:
    """S14.7 — the `host` verb GROUP (`ciu host enroll`).

    A GROUPED verb, not a third hyphenated flat one. The proposal's §6
    comparison table settles the naming ("a `host` group beside the flat
    `host-secrets`"), and `worktree` (S16, `_worktree` above) is this file's
    own established precedent for a verb that takes its own subcommand — so
    this is the smallest possible addition: one `elif` in `main`, one
    argparse subparser here, and `host-secrets` left exactly where it is
    (renaming a shipped verb is not this package's business).

    Steps 1 and 2 are the SAME subcommand, disambiguated by whether
    `--ssh-host`/`--fingerprint` are present, because step 2 is only
    meaningful once the target already carries the step-1 key.
    """
    import argparse as _ap

    from .host_enroll import EnrollError, enroll_abort, enroll_step1, enroll_step2

    define_root, rest = _extract_define_root(rest)
    p = CiuArgumentParser(prog="ciu host", add_help=False)
    sub = p.add_subparsers(dest="action", required=True)
    p_enroll = sub.add_parser("enroll", add_help=False)
    p_enroll.add_argument("name")
    p_enroll.add_argument("--user", default="ciu")
    p_enroll.add_argument("--port", type=int, default=22)
    p_enroll.add_argument("--controller", default=None)
    p_enroll.add_argument("--from", dest="from_pattern", default=None, metavar="PATTERN")
    p_enroll.add_argument("--docker", action="store_true", default=False)
    p_enroll.add_argument("--installer-url", dest="installer_url", default=None)
    p_enroll.add_argument("--replace", action="store_true", default=False)
    p_enroll.add_argument("--abort", action="store_true", default=False)
    p_enroll.add_argument("--ssh-host", dest="ssh_host", default=None, metavar="ADDR")
    p_enroll.add_argument("--fingerprint", default=None, metavar="SHA256:...")
    opts = p.parse_args(rest)

    repo_root = _resolve_repo_root_deploy(define_root)
    config = _load_remote_config(repo_root)
    try:
        # The registry already refuses --abort with any other enrollment flag and
        # --fingerprint without --ssh-host (declared Conflicts/Requires).
        if opts.abort:
            return enroll_abort(repo_root, opts.name)
        if opts.ssh_host:
            return enroll_step2(
                repo_root, opts.name,
                config=config,
                ssh_host=opts.ssh_host,
                port=opts.port,
                user=opts.user,
                fingerprint=opts.fingerprint,
                replace=opts.replace,
            )
        return enroll_step1(
            repo_root, opts.name,
            config=config,
            version=get_cli_version(),
            user=opts.user,
            port=opts.port,
            controller=opts.controller,
            from_pattern=opts.from_pattern,
            docker=opts.docker,
            installer_url_override=opts.installer_url,
            replace=opts.replace,
        )
    except EnrollError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


def _run_legacy(tokens: list[str]) -> int:
    """Run the implementation behind one registered command; its exit status.

    ``tokens`` is the implementation-layer spelling built by ``cli_registry.adapter``
    (the registry has already validated every option). The implementation signals its
    result with ``SystemExit``; that becomes the return value the registry converts back
    into the process status.
    """
    try:
        _legacy_dispatch(tokens)
    except SystemExit as stop:
        code = stop.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def main() -> None:
    """Console entry point: build the registered CLI and run it."""
    from cli_extended import VersionLookupError

    from . import output

    output.configure(os.environ.get(output._TIME_ENV) == "1")
    try:
        from .cli_registry import build_cli

        cli = build_cli()
    except VersionLookupError as exc:
        # A source tree that was neither built nor installed has no version; the library
        # refuses to invent one. Exit 3 = environment/bootstrap error (SPEC S10.3).
        print(f"[ERROR] ciu is not installed as a distribution: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
    raise SystemExit(cli.run(argv=sys.argv[1:]))


def _legacy_dispatch(raw: list[str]) -> None:
    verb = raw[0]
    rest = raw[1:]

    if verb == "env":
        # The registry guarantees the action is `generate` or `print`.
        if rest[0] == "generate":
            raise SystemExit(_env_generate(rest[1:]))
        raise SystemExit(_env_print(rest[1:]))

    elif verb == "iops-baseline":
        raise SystemExit(_iops_baseline(rest))

    elif verb == "render":
        if _flag_given(rest, "--host"):
            import argparse as _ap
            define_root, rest = _extract_define_root(rest)
            p = CiuArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            opts, remaining = p.parse_known_args(rest)
            repo_root = _resolve_repo_root_deploy(define_root)
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            host_cfg = get_host(repo_root, opts.host)
            raise SystemExit(_run_remote_bundle(
                host_cfg, ["render", *remaining], config=config, repo_root=repo_root))
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--render-toml"] + rest))

    elif verb == "profiles":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--list-profiles"] + rest))

    elif verb == "layouts":
        # S7.5c — pure listing of DECLARED layouts (no validation, no
        # inventory requirement); `ciu up --layout` is the validating consumer.
        define_root, rest = _extract_define_root(rest)
        repo_root = _resolve_repo_root_deploy(define_root)
        config = _load_remote_config(repo_root)
        from .deploy_pkg.layouts import list_layouts
        rows = list_layouts(config)
        if "--json" in rest:
            print(json.dumps({
                "schema_version": 1,
                "layouts": {
                    name: {"environment": environment, "hosts": list(hosts)}
                    for name, environment, hosts in rows
                },
            }, sort_keys=True))
            raise SystemExit(0)
        if not rows:
            print("(no layouts declared)")
            raise SystemExit(0)
        for name, environment, hosts in rows:
            env_part = f" environment={environment}" if environment else ""
            hosts_part = ", ".join(hosts) if hosts else "(no hosts)"
            print(f"{name}:{env_part} hosts=[{hosts_part}]")
        raise SystemExit(0)

    elif verb == "up":
        if _flag_given(rest, "--layout"):
            # S7.5c — push-deploy a named host→bundles plan. The layout owns
            # host order and bundles, so --host/--dir/--profile/--thin/
            # --bootstrap/--rollback are refused by the registered verb's
            # declared constraints (cli_registry) before this code runs: a
            # passthrough --profile would silently override the exported
            # CIU_SERVICES_PROFILE (S7.5 CLI precedence).
            #
            # ciu-P45 (CIU-54): --root-folder is extracted first (see
            # _extract_define_root); the layout name is the only value left to read.
            define_root, rest = _extract_define_root(rest)
            layout_parser = CiuArgumentParser(add_help=False)
            layout_parser.add_argument("--layout", dest="layout", default=None)
            layout_opts, remaining = layout_parser.parse_known_args(rest)
            layout_name = layout_opts.layout
            repo_root = _resolve_repo_root_deploy(define_root)
            config = _load_remote_config(repo_root)
            from .deploy_pkg.layouts import resolve_layout
            from .hosts import get_host, load_hosts
            try:
                layout = resolve_layout(config, load_hosts(repo_root), layout_name)
            except ValueError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                raise SystemExit(2)
            # Pure orchestration: delegate each host to _push_host (shared
            # with `up --host` — review finding B3, was reimplemented and had
            # already drifted), with the layout's exports prepended to the
            # single remote argv string (one-argv discipline).
            for index, host in enumerate(layout.hosts):
                not_deployed = layout.hosts[index + 1:]
                remainder = ", ".join(not_deployed) or "(none)"
                try:
                    host_cfg = get_host(repo_root, host)
                except ValueError as exc:
                    print(
                        f"[ERROR] layout '{layout.name}': {exc}; "
                        f"not deployed: {remainder}.",
                        file=sys.stderr,
                    )
                    raise SystemExit(2)
                exports = (
                    f"export CIU_SERVICES_PROFILE={shlex.quote(','.join(layout.bundles[host]))}; "
                    f"export CIU_LAYOUT={shlex.quote(layout.name)}; "
                    f"export CIU_LAYOUT_HOST={shlex.quote(host)}; "
                    f"export CIU_DEPLOY_ENVIRONMENT={shlex.quote(layout.environment)}; "
                )
                stage, rc = _push_host(
                    host_cfg, host,
                    repo_root=repo_root, config=config, remaining=remaining,
                    extra_exports=exports,
                )
                if rc != 0:
                    verb_text = {"preflight": "remote preflight failed",
                                 "sync": "bundle sync failed"}.get(stage, "up failed")
                    print(
                        f"[ERROR] layout '{layout.name}': {verb_text} on host "
                        f"'{host}' ({rc}); not deployed: {remainder}.",
                        file=sys.stderr,
                    )
                    raise SystemExit(rc)
            raise SystemExit(0)
        elif _flag_given(rest, "--host"):
            # Remote push-deploy path
            import argparse as _ap
            define_root, rest = _extract_define_root(rest)
            p = CiuArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            p.add_argument("--thin", action="store_true", default=False)
            p.add_argument("--bootstrap", action="store_true", default=False)
            p.add_argument("--rollback", action="store_true", default=False)
            opts, remaining = p.parse_known_args(rest)
            repo_root = _resolve_repo_root_deploy(define_root)
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            host_cfg = get_host(repo_root, opts.host)
            bundle_dir = host_cfg.get("bundle_dir", "/opt/ciu/current")

            if opts.thin:
                # Docker-optional push→activate path (S14.6). Pushes an artifact
                # to bundle_dir, then runs the project's shell activation contract
                # (bootstrap|apply|health|rollback) — no Docker/Python on target.
                # (--bootstrap/--rollback exclusivity and their need for --thin are
                # declared constraints of the registered verb.)
                from .activate import run_thin_up
                try:
                    rc = run_thin_up(
                        host_cfg,
                        config=config,
                        repo_root=repo_root,
                        bundle_dir=bundle_dir,
                        bootstrap=opts.bootstrap,
                        rollback=opts.rollback,
                        remaining=remaining,
                    )
                except ValueError as exc:
                    print(f"[ERROR] {exc}", file=sys.stderr)
                    raise SystemExit(2)
                raise SystemExit(rc)

            # B3 (review): the ONE push implementation, shared with the
            # `--layout` loop above — behavior here is unchanged (same
            # docker_optional advisory, same sync-then-exec, same silent rc
            # propagation on failure the existing tests pin).
            _stage, rc = _push_host(
                host_cfg, opts.host,
                repo_root=repo_root, config=config, remaining=remaining,
            )
            raise SystemExit(rc)
        elif _flag_given(rest, "--dir"):
            import argparse as _ap
            p = CiuArgumentParser(add_help=False)
            p.add_argument("--dir", dest="dir", default=None)
            opts, remaining = p.parse_known_args(rest)
            dir_arg = opts.dir or "."
            from .engine import main as engine_main
            raise SystemExit(engine_main(["-d", dir_arg] + remaining))
        else:
            # Profile-based deploy (defaults to active profile when no --profile given)
            from .deploy import main as deploy_main
            raise SystemExit(deploy_main(rest))

    elif verb == "down":
        if _flag_given(rest, "--host"):
            import argparse as _ap
            define_root, rest = _extract_define_root(rest)
            p = CiuArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            opts, remaining = p.parse_known_args(rest)
            repo_root = _resolve_repo_root_deploy(define_root)
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            host_cfg = get_host(repo_root, opts.host)
            raise SystemExit(_run_remote_bundle(
                host_cfg, ["down", *remaining], config=config, repo_root=repo_root))
        if _flag_given(rest, "--dir"):
            define_root, rest = _extract_define_root(rest)
            parser = CiuArgumentParser(prog="ciu down --dir")
            parser.add_argument("--dir", metavar="PATH")
            parser.add_argument("--profile", action="append", default=None, metavar="NAME")
            opts = parser.parse_args(rest)
            repo_root = _resolve_repo_root_deploy(define_root)
            from .deploy import stop_stack
            try:
                rc = stop_stack(repo_root, opts.dir, profiles=opts.profile)
            except ValueError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                raise SystemExit(2)
            except (OSError, RuntimeError) as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                raise SystemExit(1)
            raise SystemExit(rc)
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--stop"] + rest))

    elif verb == "clean":
        if _flag_given(rest, "--identity"):
            import argparse as _ap
            define_root, rest = _extract_define_root(rest)
            parser = CiuArgumentParser(prog="ciu clean --identity")
            parser.add_argument("--identity", metavar="OLD_ID")
            parser.add_argument("-y", "--yes", action="store_true")
            parser.add_argument("--expected-checkout", metavar="PATH")
            # Strict parse: the registry declares `--identity` as conflicting with every
            # ordinary clean option, so anything left over is a usage error here too.
            opts = parser.parse_args(rest)
            repo_root = _resolve_repo_root_deploy(define_root)
            from .deploy import action_clean_identity
            raise SystemExit(
                action_clean_identity(
                    repo_root,
                    opts.identity,
                    yes=opts.yes,
                    expected_checkout=opts.expected_checkout,
                )
            )
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--clean"] + rest))

    elif verb == "health":
        # `health` is not named in ciu-P29's O2 (which lists up/down/render),
        # but it carries the IDENTICAL plain-membership dispatch on the same
        # `--host` modifier (S10.4) with the identical consequence — deploy.py
        # accepts and ignores --host — so leaving it would leave the bug class
        # half-closed. Same predicate, no other change to this branch.
        if _flag_given(rest, "--host"):
            import argparse as _ap
            define_root, rest = _extract_define_root(rest)
            p = CiuArgumentParser(add_help=False)
            p.add_argument("--host", dest="host", default=None)
            p.add_argument("--thin", action="store_true", default=False)
            opts, remaining = p.parse_known_args(rest)
            repo_root = _resolve_repo_root_deploy(define_root)
            config = _load_remote_config(repo_root)
            from .hosts import get_host
            host_cfg = get_host(repo_root, opts.host)
            if opts.thin:
                # Docker-optional path (S14.6): run the project's 'health' verb
                # of the activation contract instead of remote `ciu health`.
                bundle_dir = host_cfg.get("bundle_dir", "/opt/ciu/current")
                from .activate import run_activation
                try:
                    rc = run_activation(
                        host_cfg, "health",
                        config=config, repo_root=repo_root,
                        bundle_dir=bundle_dir, remaining=remaining,
                    )
                except ValueError as exc:
                    print(f"[ERROR] {exc}", file=sys.stderr)
                    raise SystemExit(2)
                raise SystemExit(rc)
            raise SystemExit(_run_remote_bundle(
                host_cfg, ["status", "--gate", *remaining], config=config, repo_root=repo_root))
        if _flag_given(rest, "--dir"):
            # `status --gate --dir STACK` (CIU-170): the S7.7 gate over ONE stack, the
            # same single-stack gate `ciu up --dir --healthcheck` applies.
            define_root, rest = _extract_define_root(rest)
            parser = CiuArgumentParser(prog="ciu status --gate --dir")
            parser.add_argument("--dir", metavar="PATH")
            parser.add_argument("--profile", action="append", default=None)
            parser.add_argument("--phases", default=None)
            opts = parser.parse_args(rest)
            repo_root = _resolve_repo_root_deploy(define_root)
            from .deploy import load_global_config
            from .engine import _run_single_stack_healthcheck
            try:
                config = load_global_config(repo_root)
            except ValueError as exc:
                print(f"[ERROR] {exc}", file=sys.stderr)
                raise SystemExit(2)
            raise SystemExit(
                _run_single_stack_healthcheck(Path(opts.dir), define_root, config)
            )
        from .deploy import main as deploy_main
        if "--preflight" in rest:
            extra = [r for r in rest if r != "--preflight"]
            raise SystemExit(deploy_main(["--preflight"] + extra))
        raise SystemExit(deploy_main(["--healthcheck"] + rest))

    elif verb == "diagnose":
        import argparse as _ap
        from .diagnose import run as diagnose_run
        p = CiuArgumentParser(prog="ciu diagnose", add_help=False)
        p.add_argument("--project", default=None)
        p.add_argument("--logs", type=int, default=100)
        p.add_argument("--json", dest="json_output", action="store_true")
        opts = p.parse_args(rest)
        if opts.logs < 0 or opts.logs > 10_000:
            cli_error("ciu diagnose: --logs must be between 0 and 10000.")
            raise SystemExit(2)
        raise SystemExit(diagnose_run(project=opts.project, log_lines=opts.logs, json_output=opts.json_output))

    elif verb == "ksm":
        raise SystemExit(_ksm(rest))

    elif verb == "worktree":
        raise SystemExit(_worktree(rest))

    elif verb == "resolve":
        raise SystemExit(_resolve_identities_cli(rest))

    elif verb == "exec":
        raise SystemExit(_exec_service_cli(rest))

    elif verb == "capabilities":
        import argparse as _ap

        from . import worktree as wt_mod
        p = CiuArgumentParser(prog="ciu capabilities", add_help=False)
        p.add_argument("--json", action="store_true", default=False)
        opts = p.parse_args(rest)
        if opts.json:
            print(json.dumps(wt_mod.capabilities_document(), sort_keys=True))
        else:
            for identifier in sorted(wt_mod.WORKTREE_CAPABILITIES):
                print(identifier)
        raise SystemExit(0)

    elif verb == "provenance":
        raise SystemExit(_provenance(rest))

    elif verb == "status":
        raise SystemExit(_status(rest))

    elif verb == "init":
        from .scaffold import init_main

        raise SystemExit(init_main(rest))

    elif verb == "bake":
        raise SystemExit(_bake(rest))

    elif verb == "dev":
        import argparse as _ap
        from .dev import run_dev
        p = CiuArgumentParser(prog="ciu dev", add_help=False)
        p.add_argument("stack", nargs="?", default=None)
        p.add_argument("--profile", default=None, metavar="NAME")
        p.add_argument("--no-prebuild", dest="no_prebuild", action="store_true")
        p.add_argument("--root-folder", dest="define_root",
                       type=Path, default=None, metavar="PATH")
        opts = p.parse_args(rest)  # the registry requires <stack>
        repo_root = _resolve_repo_root_cli(opts.define_root, Path.cwd())
        raise SystemExit(run_dev(
            opts.stack,
            repo_root=repo_root,
            profile_name=opts.profile,
            no_prebuild=opts.no_prebuild,
        ))

    elif verb == "secrets":
        from .engine import main as engine_main
        raise SystemExit(engine_main(["secrets"] + rest))

    elif verb == "host":
        raise SystemExit(_host(rest))

    elif verb == "host-secrets":
        # S14.3a / CIU-35 — host-scoped local secrets. Explicit-only: values
        # are NEVER printed and materialization never happens implicitly inside
        # transport verbs.
        import argparse as _ap
        define_root, rest = _extract_define_root(rest)
        p = CiuArgumentParser(add_help=False)
        p.add_argument("host", nargs="?", default=None)
        p.add_argument("--materialize", action="store_true", default=False)
        p.add_argument("--list", action="store_true", default=False)
        p.add_argument("--path", dest="path_name", default=None)
        p.add_argument("-y", "--yes", action="store_true", default=False)
        # The registry requires <host> and exactly one of --materialize/--list/--path.
        opts = p.parse_args(rest)
        repo_root = _resolve_repo_root_deploy(define_root)
        from .hosts import get_host_secrets
        try:
            specs = get_host_secrets(repo_root, opts.host)
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            raise SystemExit(2)
        from .secrets.materialize import host_secret_store
        if opts.list:
            if not specs:
                print("(no host secrets declared)")
            else:
                for name in specs:
                    store = host_secret_store(repo_root, opts.host, name)
                    print(f"{name}  {'present' if store.exists() else 'absent'}")
            raise SystemExit(0)
        if opts.path_name is not None:
            if opts.path_name not in specs:
                print(
                    f"[ERROR] host '{opts.host}' declares no secret '{opts.path_name}'. "
                    f"Declared: {', '.join(sorted(specs)) or '(none)'}.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            print(host_secret_store(repo_root, opts.host, opts.path_name))
            raise SystemExit(0)
        # --materialize
        from .secrets.materialize import materialize_host_secrets
        try:
            results = materialize_host_secrets(
                repo_root,
                opts.host,
                specs,
                assume_yes=opts.yes,
                env=os.environ,
            )
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            raise SystemExit(2)
        if not results:
            print(f"host '{opts.host}': no secrets declared")
        else:
            for name, res in results.items():
                print(f"{name} -> {res.file}")
        raise SystemExit(0)

    elif verb == "check":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--check"] + rest))

    elif verb == "migration-check":
        # S13.7 — the standalone entry point into the SAME rule registry
        # `ciu check`'s `migration` stage walks. Root resolution is CIU-54's
        # established convention for a verb with its own parser
        # (`deploy.resolve_repo_root` via `_resolve_repo_root_deploy`), so it
        # answers identically to `ciu check` run in the same checkout.
        from .migration_check import main as migration_check_main
        raise SystemExit(migration_check_main(rest))

    elif verb == "graph":
        from .deploy import main as deploy_main
        raise SystemExit(deploy_main(["--graph"] + rest))

    else:  # "ssh": the registry only hands over registered verbs, and ssh is the last
        # Parse: ciu ssh <host> [--admin] [-- cmd...]
        import argparse as _ap
        from .hosts import get_host
        from .transport_ssh import ssh_exec
        p = CiuArgumentParser(prog="ciu ssh", add_help=False)
        p.add_argument("host", nargs="?", default=None)
        p.add_argument("--admin", action="store_true", default=False)
        # Split on '--' to separate host/flags from remote command
        if "--" in rest:
            sep = rest.index("--")
            ssh_rest = rest[:sep]
            cmd_argv = rest[sep + 1:]
        else:
            ssh_rest = rest
            cmd_argv = []
        # --root-folder is extracted from ssh_rest only (S1.1, CIU-54) —
        # never from cmd_argv, which is the literal remote command and must
        # pass through untouched even if it happens to contain that spelling.
        define_root, ssh_rest = _extract_define_root(ssh_rest)
        opts = p.parse_args(ssh_rest)  # the registry requires <host>
        # Resolve repo root (S1.1, CIU-54)
        repo_root = _resolve_repo_root_deploy(define_root)
        config = _load_remote_config(repo_root)
        try:
            host_cfg = get_host(repo_root, opts.host, admin=opts.admin)
        except (OSError, ValueError) as exc:
            cli_error(f"[ERROR] {exc}")
            raise SystemExit(2) from exc
        interactive = len(cmd_argv) == 0
        raise SystemExit(ssh_exec(
            host_cfg, cmd_argv,
            config=config,
            repo_root=repo_root,
            interactive=interactive,
            admin=opts.admin,
        ))


if __name__ == "__main__":
    main()
