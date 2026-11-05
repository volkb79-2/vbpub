"""CIU's public CLI grammar, registered once with ``cli_extended.CliRegistry``.

This module is the single declaration of every verb, argument and option
(AC-03/AC-04): parsing, grouped help, ``--json``/``--yes``/``--dry-run``/
``--traceback``, exact-spelling option matching, the surface manifest and shell
completion all derive from it. The handlers are thin adapters: each one turns the
parsed namespace back into the token list of the implementation behind it
(``cli._run_legacy``) and calls that. The implementation layers (``deploy``,
``engine``, ``worktree``, ...) keep their own argparse; they only ever receive
tokens that the registry has already validated, so a spelling the registry refuses
never reaches them.

Surface-change decisions are recorded in ``docs/CLI-SURFACE-DECISIONS-20261104.md``.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from typing import Any

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliRegistry,
    OptionSpec,
    Requires,
    RequiresChoice,
    Conflicts,
    VerbGroup,
    VerbSpec,
)

from .cli_utils import ciu_identity

#: ONE exception policy for the root and every delegated registry: the library refuses a
#: delegate whose policy differs from its parent's. "report" turns an unexpected exception
#: into one ``[ERROR]`` line (exit 1) with ``--traceback`` for the stack (CIU-140 class).
UNEXPECTED_EXCEPTIONS_POLICY = "report"
CIU_LOGGER = "ciu"

#: Process exit codes (SPEC S10.3). 0/1/2 are the library's; 3 is CIU's own
#: environment/bootstrap class. 4 (contention) and 5 (remote failure) are RESERVED for v8
#: and are never emitted by v7 (decision record, D-EXIT).
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3

Handler = Callable[[argparse.Namespace, Any], "int | None"]


# --------------------------------------------------------------------------
# Option helpers
# --------------------------------------------------------------------------


def _dest(spec: OptionSpec) -> str:
    kwargs = spec.parser_kwargs
    if "dest" in kwargs:
        return str(kwargs["dest"])
    long_flags = [flag for flag in spec.flags if flag.startswith("--")]
    return (long_flags[0] if long_flags else spec.flags[0]).lstrip("-").replace("-", "_")


def flag(name: str, help: str, *, group: str = "OPTIONS", dest: str | None = None,
         xgroup: str | None = None, required: bool = False, hidden: bool = False) -> OptionSpec:
    """A boolean ``--name`` switch."""
    kwargs: dict[str, Any] = {"action": "store_true", "default": False}
    if dest:
        kwargs["dest"] = dest
    return OptionSpec((name,), help, group=group, parser_kwargs=kwargs,
                      mutually_exclusive_group=xgroup, mutually_exclusive_required=required,
                      hidden=hidden)


def value(name: str, help: str, metavar: str, *, group: str = "OPTIONS", dest: str | None = None,
          kind: Callable[[str], Any] | None = None, choices: Sequence[str] | None = None,
          default: Any = None, xgroup: str | None = None, required: bool = False) -> OptionSpec:
    """A single-valued ``--name VALUE`` option."""
    kwargs: dict[str, Any] = {"default": default}
    if dest:
        kwargs["dest"] = dest
    if kind is not None:
        kwargs["type"] = kind
    if choices is not None:
        kwargs["choices"] = tuple(choices)
    return OptionSpec((name,), help, group=group, metavar=metavar, parser_kwargs=kwargs,
                      mutually_exclusive_group=xgroup, mutually_exclusive_required=required)


def repeat(name: str, help: str, metavar: str, *, group: str = "OPTIONS") -> OptionSpec:
    """A repeatable ``--name VALUE`` option."""
    return OptionSpec((name,), help, group=group, metavar=metavar,
                      parser_kwargs={"action": "append", "default": None})


def _tokens_for_options(args: argparse.Namespace, options: Sequence[OptionSpec]) -> list[str]:
    """The implementation-layer spelling of every option the operator supplied."""
    tokens: list[str] = []
    for spec in options:
        raw = getattr(args, _dest(spec), None)
        action = spec.parser_kwargs.get("action", "store")
        name = spec.flags[0]
        if action == "store_true":
            if raw:
                tokens.append(name)
        elif action == "append":
            for item in raw or ():
                tokens.extend((name, str(item)))
        elif raw is not None:
            tokens.extend((name, str(raw)))
    return tokens


def _tokens_for_arguments(args: argparse.Namespace, arguments: Sequence[ArgumentSpec],
                          remainder: str | None) -> list[str]:
    tokens: list[str] = []
    for spec in arguments:
        raw = getattr(args, spec.name)
        if isinstance(raw, (list, tuple)):
            if spec.name == remainder:
                if raw:
                    tokens.append("--")
                    tokens.extend(str(item) for item in raw)
            else:
                tokens.extend(str(item) for item in raw)
        else:
            tokens.append(str(raw))
    return tokens


def adapter(prefix: Sequence[str], options: Sequence[OptionSpec] = (),
            arguments: Sequence[ArgumentSpec] = (), *, json_tokens: Sequence[str] | None = None,
            yes: bool = False, dry_run: bool = False, remainder: str | None = None,
            positional_first: bool = False) -> Handler:
    """Build the handler that re-spells a parsed command for its implementation layer.

    ``json_tokens`` is what the library-owned ``--json`` becomes for the implementation
    (``None``: the verb has no JSON mode). ``yes``/``dry_run`` forward the library-owned
    ``--yes``/``--dry-run``. ``remainder`` names the ``-- ARGV...`` argument.
    """

    def handle(args: argparse.Namespace, runtime: Any) -> int:
        from .cli import _run_legacy

        positional = _tokens_for_arguments(args, arguments, remainder)
        tokens = [*prefix]
        if positional_first:
            head = [t for t in positional if t != "--"]
            tail: list[str] = []
            if "--" in positional:
                split = positional.index("--")
                head, tail = positional[:split], positional[split:]
            tokens += head
            positional = tail
        tokens += _tokens_for_options(args, options)
        if json_tokens and runtime.json_mode:
            tokens += list(json_tokens)
        if yes and runtime.yes:
            tokens.append("--yes")
        if dry_run and runtime.dry_run:
            tokens.append("--dry-run")
        tokens += positional
        return _run_legacy(tokens)

    return handle


# --------------------------------------------------------------------------
# Shared declarations
# --------------------------------------------------------------------------

ROOT = value("--root-folder", "override the CIU repo root (no parent walking)", "PATH",
             group="SELECTION")
PROFILE = repeat("--profile", "restrict to the named host profile (repeatable; default: active profile)",
                 "NAME", group="SELECTION")
PHASES = value("--phases", "restrict to the given phase numbers", "N,M", group="SELECTION")
IGNORE_ERRORS = flag("--ignore-errors", "continue past a failing stack (final exit is still 1)",
                     group="BEHAVIOUR")
PROTECTED = flag("--i-understand-this-is-protected",
                 "required in addition to --yes against an instance whose [deploy] table "
                 "declares protected = true (CIU-105)", group="BEHAVIOUR")


def _remainder_argument(name: str, description: str, metavar: str) -> ArgumentSpec:
    return ArgumentSpec(name, description, metavar=metavar,
                        parser_kwargs={"nargs": "*", "default": []})


def ciu_registry(prog: str, description: str, **kwargs: Any) -> CliRegistry:
    """The ``CliRegistry`` every CIU parser (root and delegates) shares.

    One place decides the identity (installed ``ciu`` metadata and/or the build-time
    version, never a literal fallback), the unexpected-exception policy, the logger and
    the run-scoped global overrides.
    """
    for owned in ("identity", "unexpected_exceptions"):
        if owned in kwargs:
            raise TypeError(f"ciu_registry owns {owned!r}; do not pass it")
    extra = tuple(kwargs.pop("global_options", ()))
    kwargs.setdefault("logging_logger", CIU_LOGGER)
    return CliRegistry(
        ciu_identity(),
        prog=prog,
        description=description,
        global_options=(*_global_options(), *extra),
        unexpected_exceptions=UNEXPECTED_EXCEPTIONS_POLICY,
        **kwargs,
    )


class _TimePrefixAction(argparse.Action):
    """``--log-prefix-time-short``: a process-wide presentation choice."""

    def __init__(self, option_strings, dest, nargs=0, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from . import output

        setattr(namespace, self.dest, True)
        os.environ[output._TIME_ENV] = "1"
        output.configure(True)


class _KsmAction(argparse.Action):
    """``--ksm`` / ``--no-ksm``: the ambient CIU_KSM for THIS run only (S15.18).

    The flag and ``CIU_KSM`` are ONE resolution point (governance.resolve_ksm_optin
    reads the variable), so the two can never disagree.
    """

    def __init__(self, option_strings, dest, nargs=0, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from .governance import BUILTIN_KSM, KSM_ENV_VAR

        setattr(namespace, self.dest, True)
        os.environ[KSM_ENV_VAR] = BUILTIN_KSM if option_string == "--ksm" else "off"


def _global_options() -> tuple[OptionSpec, ...]:
    return (
        OptionSpec(
            ("--log-prefix-time-short",),
            "prefix severity lines with local HH:MM:SS timestamps; interactive terminals "
            "colour INFO/WARN/ERROR, pipes and logs stay plain",
            group="OUTPUT CONTROL",
            parser_kwargs={"action": _TimePrefixAction, "nargs": 0, "default": False},
        ),
        OptionSpec(
            ("--ksm",),
            "inject CIU's KSM shim, THIS run only (never written back to the TOML layer)",
            group="RUN OVERRIDES",
            parser_kwargs={"action": _KsmAction, "nargs": 0, "default": False, "dest": "ksm_on"},
            mutually_exclusive_group="ksm-override",
        ),
        OptionSpec(
            ("--no-ksm",),
            "do not inject the KSM shim, THIS run only; PASSTHROUGH: it does NOT disable a "
            "KSM an image enables itself (S15.18)",
            group="RUN OVERRIDES",
            parser_kwargs={"action": _KsmAction, "nargs": 0, "default": False, "dest": "ksm_off"},
            mutually_exclusive_group="ksm-override",
        ),
    )


# --------------------------------------------------------------------------
# Delegated groups
# --------------------------------------------------------------------------


def _env_cli() -> Any:
    registry = ciu_registry("ciu env", "Generate and print this checkout's instance environment.")
    generate_options = (
        ROOT,
        # Hidden (AC-08): internal plumbing for the worktree allocator, which seeds identity
        # before the full generation; not an operator control.
        flag("--identity-only", "write only the identity facts (S3.1b), not the machine facts",
             group="BEHAVIOUR", hidden=True),
    )
    registry.register(VerbSpec(
        "generate",
        description=(
            "(Re)generate ciu.env from system state and rewrite this checkout's CIU-owned "
            "ciu.instance.generated.toml so templates read identity facts from the merged "
            "config chain instead of ambient environment (S3.1b)."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_json=False, include_progress=False, options=generate_options,
        examples=("ciu env generate", "ciu env generate --root-folder ."),
        handler=adapter(["env", "generate"], generate_options),
    ))
    print_options = (ROOT,)
    registry.register(VerbSpec(
        "print",
        description=(
            "Print the existing ciu.env as shell `export KEY='value'` lines, for: "
            "eval \"$(ciu env print)\". It only prints (no subprocess can change your shell) "
            "and generates nothing: run `ciu env generate` first if ciu.env is missing."
        ),
        group=VerbGroup.EXPLORATION.value, include_json=False, include_progress=False,
        options=print_options, examples=("eval \"$(ciu env print)\"",),
        handler=adapter(["env", "print"], print_options),
    ))
    return registry.build()


def _show_cli() -> Any:
    """``ciu show profiles|layouts``: read-only listings of declared configuration.

    Absorbs the former top-level ``profiles`` and ``layouts`` verbs (v8 S18, decision C6);
    both gain ``--json``. They never write generated config (CIU-109 stays fixed).
    """
    registry = ciu_registry("ciu show", "List what the configuration declares (read-only).")
    registry.register(VerbSpec(
        "profiles",
        description="List the declared host profiles with their phases, stacks and compose profiles.",
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=(ROOT,),
        handler=adapter(["profiles"], (ROOT,), json_tokens=["--json"]),
    ))
    registry.register(VerbSpec(
        "layouts",
        description=(
            "List the declared deploy layouts ([deploy.layouts.<name>]) with their environment and "
            "ordered host list (S7.5c). Shows what is DECLARED; `ciu up --layout` validates."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=(ROOT,),
        handler=adapter(["layouts"], (ROOT,), json_tokens=["--json"]),
    ))
    return registry.build()


def _governance_cli() -> Any:
    """``ciu governance ksm|iops-baseline`` (v8 S18: governance absorbs ksm and iops-baseline)."""
    registry = ciu_registry("ciu governance",
                            "Resource-governance helpers (S15): KSM shim, IOPS baseline.")
    ksm_options = (ROOT, flag("--force", "rebuild even when the shim is current", group="BEHAVIOUR"))
    registry.register(VerbSpec(
        "ksm",
        description="Build the KSM shim into .ciu/ksm/ (S15.17, CIU-17).",
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_json=False, include_progress=False, options=ksm_options,
        handler=adapter(["ksm", "build"], ksm_options),
    ))
    iops = (
        value("--path", "output file (default: /var/lib/ciu/io-baseline.env)", "PATH"),
        value("--runtime", "fio measurement seconds (default: 10)", "N", kind=int),
        flag("--force", "re-measure even when the existing result is under 30 days old"),
    )
    registry.register(VerbSpec(
        "iops-baseline",
        description=(
            "Measure this host's disk randread IOPS ceiling with fio and write RIOPS_MAX/"
            "RIOPS_ENGINE for governance read_iops derivation (S15.4/S15.9). Explicit opt-in; "
            "generates ~10s of saturating read I/O. Without fio it prints a notice and exits 0."
        ),
        group=VerbGroup.MAINTENANCE.value, mutating=True, expensive=True, include_json=False,
        include_progress=False, include_confirmation=False, options=iops,
        handler=adapter(["iops-baseline"], iops),
    ))
    return registry.build()


def _schema_cli() -> Any:
    """``ciu schema capabilities`` (v8 S18: schema absorbs capabilities)."""
    registry = ciu_registry("ciu schema", "Machine-readable contracts CIU publishes.")
    registry.register(VerbSpec(
        "capabilities",
        description=(
            "Print CIU's versioned, closed capability allowlist (D-009). Consumers allowlist "
            "shipped machine-contract identifiers instead of inferring features from SemVer; "
            "--json emits the separately versioned document."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False,
        handler=adapter(["capabilities"], json_tokens=["--json"]),
    ))
    return registry.build()


def _host_cli() -> Any:
    registry = ciu_registry("ciu host", "Remote host inventory (.ciu.hosts.toml).")
    options = (
        ROOT,
        value("--user", "deploy user on the target (default ciu)", "U", group="TARGET"),
        value("--port", "ssh port (default 22; pinned in the [ADDR]:N known-hosts form only "
                        "when it is not 22)", "N", group="TARGET", kind=int),
        value("--controller", "name for the key comment; defaults to "
                              "topology.external.public_fqdn and is REQUIRED when that is not "
                              "declared (CIU never guesses a controller hostname)", "FQDN",
              group="STEP 1"),
        value("--from", "add an authorized_keys from=\"PATTERN\" restriction to the printed key "
                        "line (opt-in; OpenSSH matches it against the client's source address)",
              "PATTERN", group="STEP 1", dest="from_pattern"),
        flag("--docker", "ask the installer to add the deploy user to the docker group",
             group="STEP 1"),
        value("--installer-url", "override the pinned release-asset URL (a mirror or a "
                                 "commit-pinned raw URL); nothing ever falls back to `latest`; "
                                 "REQUIRED on an unreleased (.dev) ciu", "URL", group="STEP 1"),
        value("--ssh-host", "ssh-keyscan ADDR, verify the fingerprint, prove the login and "
                            "write the inventory row (step 2)", "ADDR", group="STEP 2"),
        value("--fingerprint", "the SHA256 fingerprint read on the target's console (TOFU "
                               "with a second channel)", "SHA256:...", group="STEP 2"),
        flag("--replace", "rotate: regenerate the key and overwrite an existing row's "
                          "ssh_key/known_host at step 2", group="BEHAVIOUR"),
        flag("--abort", "remove a pending (step-1-only) key pair", group="BEHAVIOUR"),
    )
    name_arg = ArgumentSpec("name", "host name for the inventory row", metavar="NAME")
    registry.register(VerbSpec(
        "enroll",
        description=(
            "Enroll a bare remote host into .ciu.hosts.toml (S14.7 / CIU-93). Two steps, one "
            "verb: without --ssh-host it generates an ed25519 key pair and PRINTS the public key "
            "plus the one command the target's admin runs (nothing is written to the inventory); "
            "with --ssh-host/--fingerprint it verifies the host key, proves the login with "
            "`ciu version` on the target, and only then writes [deploy.hosts.<name>]. "
            "CIU_SSH_INSECURE_TOFU is never set by this verb."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_json=False, include_progress=False, arguments=(name_arg,), options=options,
        handler=adapter(["host", "enroll"], options, (name_arg,), positional_first=True),
        constraints=(
            *(Conflicts(("--abort", other),
                        "--abort removes the pending step-1 key pair and takes no other "
                        "enrollment flag")
              for other in ("--ssh-host", "--fingerprint", "--replace")),
            Requires("--fingerprint", ("--ssh-host",),
                     "a fingerprint only verifies step 2: there is nothing to keyscan "
                     "without an address"),
        ),
    ))
    return registry.build()


def _secrets_cli() -> Any:
    registry = ciu_registry("ciu secrets", "Materialised secret store files (S4.25).")
    common = (
        value("--dir", "stack directory (default: cwd)", "PATH", group="SELECTION"),
        ROOT,
    )
    registry.register(VerbSpec(
        "list",
        description="List the materialised secret store files (names only, never values).",
        group=VerbGroup.EXPLORATION.value, include_json=False, include_progress=False,
        options=common, handler=adapter(["secrets", "list"], common),
    ))
    host_name = ArgumentSpec("host", "host name in the inventory", metavar="HOST")
    hs_options = (
        ROOT,
        flag("--materialize", "resolve all declared entries (prompt rules as ASK_EXTERNAL); "
                              "prints store file paths", group="MODE", xgroup="hs-mode",
             required=True),
        flag("--list", "print entry names and store-file existence (never values)", group="MODE",
             xgroup="hs-mode", required=True),
        value("--path", "print the store file path for one declared entry", "NAME", group="MODE",
              dest="path_name", xgroup="hs-mode", required=True),
    )
    registry.register(VerbSpec(
        "host",
        description=(
            "Host-scoped local secrets (S14.3a / CIU-35): ASK_EXTERNAL/GEN_LOCAL entries declared "
            "under [deploy.hosts.<host>.secrets], materialised under the project store's "
            "hosts/<host>/ namespace, resolvable BEFORE any Vault exists on the target. "
            "Explicit-only: values are never printed and nothing materialises inside ssh/up."
        ),
        group=VerbGroup.MIXED.value, mutating=True, include_json=False, include_progress=False,
        arguments=(host_name,), options=hs_options,
        handler=adapter(["host-secrets"], hs_options, (host_name,), yes=True,
                        positional_first=True),
    ))
    reset_options = (*common, value("--name", "restrict the reset to one secret name", "N",
                                    group="SELECTION"))
    registry.register(VerbSpec(
        "reset",
        description="Delete secret store files (all of the stack's, or one with --name).",
        group=VerbGroup.MAINTENANCE.value, mutating=True, dry_run=False,
        include_json=False, include_progress=False, options=reset_options,
        handler=adapter(["secrets", "reset"], reset_options, yes=True),
    ))
    return registry.build()


def _worktree_cli() -> Any:
    registry = ciu_registry(
        "ciu worktree",
        "Managed, family-scoped worktree instances (S16): a checkout plus its CIU identity.",
    )

    def create_options(*, base_default: str | None, dir_default: str | None,
                       network: bool) -> tuple[OptionSpec, ...]:
        from .worktree import DEFAULT_WORKTREE_DIR

        options = [
            value("--base", "branch, tag or exact commit SHA under test"
                            + (" (default: main)" if base_default else ""), "REF",
                  group="BASE", default=None),
            value("--profile", "initial service-profile selection for the instance overlay",
                  "P1,P2", group="BASE"),
            value("--worktree-dir", "directory that holds managed checkouts"
                                    + (f" (default: {DEFAULT_WORKTREE_DIR})" if dir_default else ""),
                  "DIR", group="BASE", dest="worktree_dir"),
            value("--name", "display name", "DISPLAY", group="NAMING", dest="display_name",
                  xgroup=None),
            value("--prefix", "branch/directory prefix (with --feature)", "PROJECT_OR_COMPONENT",
                  group="NAMING"),
            value("--feature", "feature slug (with --prefix)", "FEATURE", group="NAMING"),
            value("--branch", "explicit branch name", "BRANCH", group="NAMING"),
            value("--path", "explicit checkout path", "PATH", group="NAMING"),
            value("--shared-infra", "shared-infra reference", "REF", group="SHARED INFRA",
                  dest="shared_infra"),
            value("--shared-infra-services", "services taken from the shared infra", "S1,S2",
                  group="SHARED INFRA", dest="shared_infra_services"),
            value("--shared-infra-ref-projects", "reference projects of the shared infra", "R1,R2",
                  group="SHARED INFRA", dest="shared_infra_ref_projects"),
            value("--shared-infra-ref-services", "reference services of the shared infra",
                  "A1,A2=S2", group="SHARED INFRA", dest="shared_infra_ref_services"),
        ]
        if network:
            options.append(flag("--adopt-existing-network",
                                "explicitly claim an unlabelled existing network and record its "
                                "Docker id", group="BEHAVIOUR"))
        return tuple(options)

    logical = ArgumentSpec("logical_name", "logical instance name", metavar="LOGICAL")
    naming_constraints = (
        Conflicts(("--name", "--prefix"), "--name is the alternative to --prefix/--feature"),
        Conflicts(("--name", "--feature"), "--name is the alternative to --prefix/--feature"),
    )

    create = (ROOT, *create_options(base_default="main", dir_default="x", network=False),
              flag("--up", "start the worktree's declared environment after creation",
                   group="BEHAVIOUR"))
    registry.register(VerbSpec(
        "create",
        description=(
            "Create a managed linked checkout and its CIU identity. Creation does NOT deploy "
            "or start containers unless --up is supplied. --base may be a branch, tag or exact "
            "commit SHA (default: main)."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_progress=False, arguments=(logical,), options=create,
        examples=('ciu worktree create pkg-under-test --base "$(git rev-parse HEAD)" --json',),
        handler=adapter(["worktree", "create"], create, (logical,), json_tokens=["--json"],
                        positional_first=True),
        constraints=naming_constraints,
    ))
    ensure = (ROOT, *create_options(base_default=None, dir_default=None, network=True))
    registry.register(VerbSpec(
        "ensure",
        description=(
            "Idempotent spelling for automation: reuse an exact ready instance, create one when "
            "absent, or resume a checkpointed CIU-owned partial allocation. It does not rewrite a "
            "conflicting instance; on an existing record omit initial-overlay options."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_progress=False, arguments=(logical,), options=ensure,
        examples=("ciu worktree ensure pkg-under-test --json",),
        handler=adapter(["worktree", "ensure"], ensure, (logical,), json_tokens=["--json"],
                        positional_first=True),
        constraints=naming_constraints,
    ))
    adopt_path = ArgumentSpec("path", "existing unmanaged linked checkout", metavar="PATH")
    adopt = (
        ROOT,
        value("--profile", "initial service-profile selection for the instance overlay",
              "P1,P2", group="BASE"),
        flag("--adopt-existing-network", "explicitly claim an unlabelled existing network and "
                                         "record its Docker id", group="BEHAVIOUR"),
        value("--shared-infra", "shared-infra reference", "REF", group="SHARED INFRA",
              dest="shared_infra"),
        value("--shared-infra-services", "services taken from the shared infra", "S1,S2",
              group="SHARED INFRA", dest="shared_infra_services"),
        value("--shared-infra-ref-projects", "reference projects of the shared infra", "R1,R2",
              group="SHARED INFRA", dest="shared_infra_ref_projects"),
        value("--shared-infra-ref-services", "reference services of the shared infra",
              "A1,A2=S2", group="SHARED INFRA", dest="shared_infra_ref_services"),
    )
    registry.register(VerbSpec(
        "adopt",
        description=(
            "Take ownership of an existing unmanaged linked checkout and prepare every "
            "committed CIU root in it."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_progress=False, arguments=(logical, adopt_path), options=adopt,
        handler=adapter(["worktree", "adopt"], adopt, (logical, adopt_path),
                        json_tokens=["--json"], positional_first=True),
    ))
    rm = (
        ROOT,
        flag("--force", "remove even a checkout that is not otherwise removable", group="BEHAVIOUR"),
        flag("--keep-branch", "keep the branch even when it is merged", group="BRANCH",
             xgroup="branch-mode"),
        flag("--delete-branch", "explicitly delete the branch, including an unmerged branch",
             group="BRANCH", xgroup="branch-mode"),
        repeat("--discard-protected-state", "discard protected ignored state (.assay/, vol-*) "
                                            "for this exact branch", "BRANCH", group="CONSENT"),
        repeat("--discard-changes", "discard tracked or untracked changes for this exact branch",
               "BRANCH", group="CONSENT"),
        repeat("--root-escalation", "after a confirmed permission failure, retry the blocked "
                                    "descendants for this exact branch", "BRANCH", group="CONSENT"),
    )
    registry.register(VerbSpec(
        "rm",
        description=(
            "Clean THEN remove a managed worktree; keeps an unmerged branch tip by default and "
            "requires exact branch consent for changes or protected ignored state."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, include_progress=False,
        arguments=(logical,), options=rm, examples=("ciu worktree rm pkg-under-test --yes",),
        handler=adapter(["worktree", "rm"], rm, (logical,), json_tokens=["--json"], yes=True,
                        positional_first=True),
    ))
    registry.register(VerbSpec(
        "list", description="List linked checkouts.", group=VerbGroup.EXPLORATION.value,
        include_progress=False, options=(ROOT,),
        handler=adapter(["worktree", "list"], (ROOT,), json_tokens=["--json"]),
    ))
    registry.register(VerbSpec(
        "inspect", description="The exact record plus freshly read Git facts for one instance.",
        group=VerbGroup.EXPLORATION.value, include_progress=False, arguments=(logical,),
        options=(ROOT,),
        handler=adapter(["worktree", "inspect"], (ROOT,), (logical,), json_tokens=["--json"],
                        positional_first=True),
    ))
    lease = (
        ROOT,
        value("--extend", "renew a bounded `held` lease for DURATION (e.g. 24h)", "DURATION",
              group="LEASE", xgroup="lease-mode", required=True),
        flag("--perpetual", "declare an unbounded lease", group="LEASE", xgroup="lease-mode",
             required=True),
        flag("--release", "drop the claim", group="LEASE", xgroup="lease-mode", required=True),
    )
    registry.register(VerbSpec(
        "lease",
        description=(
            "Set this instance's explicit ownership claim (S16.9). It reads no Docker state, so "
            "it works on a stopped instance exactly as on a running one."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_progress=False, arguments=(logical,), options=lease,
        handler=adapter(["worktree", "lease"], lease, (logical,), json_tokens=["--json"],
                        positional_first=True),
    ))
    refork = (ROOT, value("--onto", "trunk ref to re-record the fork point against", "TRUNK_REF"))
    registry.register(VerbSpec(
        "refork",
        description="Re-record the fork point after a rebase onto trunk (CIU-142).",
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_progress=False, arguments=(logical,), options=refork,
        handler=adapter(["worktree", "refork"], refork, (logical,), json_tokens=["--json"],
                        positional_first=True),
    ))
    branches = (
        ROOT,
        value("--base", "base ref the branches are judged against (default: main)", "REF",
              default=None),
        repeat("--discard-protected-state", "with --yes, discard ignored resumable state for "
                                            "this exact branch", "BRANCH", group="CONSENT"),
    )
    registry.register(VerbSpec(
        "branches",
        description=(
            "Survey local branches against a base; --yes prunes exactly the fully-merged, clean "
            "ones with no protected ignored state (never age-based, never the mainline)."
        ),
        group=VerbGroup.MIXED.value, mutating=True, include_progress=False, options=branches,
        handler=adapter(["worktree", "branches"], branches, json_tokens=["--json"], yes=True),
        constraints=(Requires("--discard-protected-state", ("--yes",),
                              "protected state is only discarded when pruning"),),
    ))
    reap = (
        ROOT,
        value("--category", "narrow what --yes acts on (never the unattributable/ambiguous "
                            "categories)", "C1,C2"),
    )
    registry.register(VerbSpec(
        "reap",
        description=(
            "Survey Docker resource groups (S16.10); --yes destroys exactly the ones a record, a "
            "lease or a ciu.instance label proves disposable. Run without --yes first."
        ),
        group=VerbGroup.MIXED.value, mutating=True, dry_run=True, include_progress=False,
        dry_run_help="with --yes: print the exact commands instead of running them",
        options=reap,
        handler=adapter(["worktree", "reap"], reap, json_tokens=["--json"], yes=True, dry_run=True),
    ))
    up = (ROOT, flag("--all", "start the full default deploy set, ignoring [ciu.worktree].up",
                     group="BEHAVIOUR"))
    registry.register(VerbSpec(
        "up", description="Explicitly start the selected ready instance (never implicit).",
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_json=False, include_progress=False, arguments=(logical,), options=up,
        handler=adapter(["worktree", "up"], up, (logical,), positional_first=True),
    ))
    target = value("--target", "run inside the ONE already-running declared container (S16.7)",
                   "ALIAS")
    exec_argv = _remainder_argument("argv", "exact argv to run (no shell), after `--`", "ARGV")
    exec_options = (ROOT, target)
    registry.register(VerbSpec(
        "exec",
        description=(
            "Run exact argv (no shell) in the instance's root or its declared container target; "
            "never starts anything implicitly (S16.6)."
        ),
        group=VerbGroup.MIXED.value, mutating=True, include_confirmation=False,
        include_json=False, include_progress=False, arguments=(logical, exec_argv),
        options=exec_options, examples=("ciu worktree exec pkg-under-test -- pytest -q",),
        handler=adapter(["worktree", "exec"], exec_options, (logical, exec_argv),
                        remainder="argv", positional_first=True),
    ))
    return registry.build()


# --------------------------------------------------------------------------
# Root registry
# --------------------------------------------------------------------------


def build_cli() -> Any:
    """Build the ciu ``RegisteredCli`` (import-safe: never runs a handler)."""
    registry = ciu_registry(
        "ciu",
        "Container orchestration, development worktrees and host operations "
        "(compose · init · up). Uses ciu.global.toml + ciu.env; run from a CIU-enabled "
        "repository.",
        getting_started=("ciu check", "ciu up --dry-run", "ciu worktree list"),
    )
    register = registry.register

    # ---- SCAFFOLDING / ENVIRONMENT -------------------------------------------------
    init_options = (
        value("--project-name", "project slug (default: derived from the directory name)",
              "NAME", group="SCAFFOLD"),
        value("--environment-tag", "environment tag (default: $INSTANCE_ID)", "TAG",
              group="SCAFFOLD"),
        value("--stacks", "comma-separated stack skeletons to scaffold", "A,B", group="SCAFFOLD"),
        value("--hooks", "comma-separated shipped hook templates copied into every scaffolded "
                         "stack (S19.1)", "NAME1,NAME2", group="SCAFFOLD"),
    )
    register(VerbSpec(
        "init",
        description=(
            "Guided repo scaffolding: writes a validated ciu.global.defaults.toml.j2, gitignore "
            "entries and optional stack skeletons (never overwrites). With no option it asks "
            "interactively."
        ),
        group=VerbGroup.AUTHENTICATION.value, mutating=True, interactive=True,
        include_confirmation=False, include_json=False, include_progress=False,
        options=init_options, handler=adapter(["init"], init_options),
    ))
    register(VerbSpec("env", description="Generate and print this checkout's instance environment.",
                      group=VerbGroup.AUTHENTICATION.value, mutating=True, delegate=_env_cli(),
                      include_json=False, include_progress=False))
    register(VerbSpec("governance", description="Resource-governance helpers (S15): KSM shim, IOPS baseline.",
                      group=VerbGroup.MAINTENANCE.value, mutating=True, delegate=_governance_cli(),
                      include_json=False, include_progress=False))

    # ---- AUTHORING ------------------------------------------------------------------
    render_options = (
        PROFILE, PHASES, ROOT,
        value("--host", "render on a configured remote host", "NAME", group="REMOTE"),
    )
    register(VerbSpec(
        "render",
        description="Render ciu.global.toml and each stack's ciu.toml from their Jinja2 templates.",
        group=VerbGroup.MODIFICATION.value, mutating=True, include_confirmation=False,
        include_json=False, include_progress=False, options=render_options,
        handler=adapter(["render"], render_options),
    ))
    register(VerbSpec("show", description="List what the configuration declares (read-only).",
                      group=VerbGroup.EXPLORATION.value, delegate=_show_cli(),
                      include_progress=False))

    # ---- WORKTREE / MACHINE INTERFACES ---------------------------------------------
    register(VerbSpec("worktree", description="Managed worktree instances (S16).",
                      group=VerbGroup.MIXED.value, mutating=True, delegate=_worktree_cli(),
                      include_progress=False))
    register(VerbSpec("schema", description="Machine-readable contracts CIU publishes.",
                      group=VerbGroup.EXPLORATION.value, delegate=_schema_cli(),
                      include_progress=False))
    resolve_options = (
        ROOT, PROFILE,
        value("--stack", "selected repo-relative stack path; a unique basename works", "S"),
        value("--service", "exact Compose service key (not a substring)", "X"),
        flag("--live", "add container state and health from exact Compose labels"),
    )
    register(VerbSpec(
        "resolve",
        description=(
            "Read service identities from the rendered config without writing generated files or "
            "contacting Docker (S18 / CIU-118). --json is required: it emits one versioned "
            "document mirroring v8's resolved.identities.<stack>.<service>."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=resolve_options,
        handler=adapter(["resolve"], resolve_options, json_tokens=["--json"]),
    ))
    selector = ArgumentSpec("selector", "<stack>[:<service>]", metavar="STACK[:SERVICE]")
    exec_argv2 = _remainder_argument("argv", "exact argv to run, after `--`", "ARGV")
    exec_opts = (ROOT, PROFILE)
    register(VerbSpec(
        "exec",
        description=(
            "Run exact argv in exactly one already-running service of this instance. CIU does not "
            "start the stack. A matching declared [ciu.worktree.exec_targets] entry applies its "
            "workdir and mount proof (S16.7 / CIU-118)."
        ),
        group=VerbGroup.MIXED.value, mutating=True, include_confirmation=False, include_json=False,
        include_progress=False, arguments=(selector, exec_argv2), options=exec_opts,
        examples=("ciu exec api:web -- pytest -q",),
        handler=adapter(["exec"], exec_opts, (selector, exec_argv2), remainder="argv"),
    ))

    # ---- EVIDENCE -------------------------------------------------------------------
    prov = (
        ROOT,
        flag("--ignore-mismatch", "run even when a container was built from a different commit; "
                                  "the result then describes the OLD artifact"),
        flag("--no-preflight", "skip the preflight check"),
    )
    register(VerbSpec(
        "provenance",
        description=(
            "Verify the RUNNING containers were built from the commit under test, before a live "
            "lane produces evidence (S17.2)."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=prov,
        handler=adapter(["provenance"], prov, json_tokens=["--json"]),
    ))

    # ---- STACK ORCHESTRATION -------------------------------------------------------
    up_options = (
        PROFILE, PHASES, ROOT, IGNORE_ERRORS,
        value("--dir", "deploy ONE stack directory (engine mode; with --host, the stack inside "
                       "the pushed bundle)", "PATH", group="MODE"),
        value("--layout", "push-deploy a named [deploy.layouts.<name>] host-to-bundles plan",
              "NAME", group="MODE"),
        value("--host", "push-deploy to a host from the inventory (.ciu.hosts.toml)", "NAME",
              group="MODE"),
        flag("--thin", "docker-optional: push an artifact to bundle_dir, then run the host's shell "
                       "activation contract (S14.6)", group="REMOTE"),
        flag("--bootstrap", "(with --thin) run the 'bootstrap' verb before 'apply'", group="REMOTE",
             xgroup="thin-mode"),
        flag("--rollback", "(with --thin) run the 'rollback' verb only (no fresh push)",
             group="REMOTE", xgroup="thin-mode"),
        flag("--deploy", "run each phase in order (the default action)", group="ACTION"),
        flag("--healthcheck", "run the S7.7 health gate (turns itself on when a selected stack "
                              "declares a healthy/completed requirement)", group="ACTION"),
        flag("--no-preflight", "skip host/provisioning preflight checks (break-glass)",
             group="BEHAVIOUR"),
        flag("--skip-check", "skip the `ciu check` static preflight that `ciu up` runs by default "
                             "(break-glass)", group="BEHAVIOUR", dest="skip_check"),
        flag("--render-toml", "(--dir) stop after rendering TOML", group="SINGLE STACK"),
        flag("--print-context", "(--dir) print the template context", group="SINGLE STACK"),
        flag("--reset", "(--dir) remove this stack's containers/volumes/artifacts first",
             group="SINGLE STACK"),
        flag("--skip-hostdir-check", "(--dir) skip the host-directory check", group="SINGLE STACK"),
        flag("--skip-hooks", "(--dir) skip hooks", group="SINGLE STACK"),
        flag("--skip-secrets", "(--dir) skip secret materialisation", group="SINGLE STACK"),
        flag("--shipped", "(--dir) run the committed docker-compose.yml path", group="SINGLE STACK"),
        value("--file", "(--dir) select the stack template (advanced)", "NAME", group="SINGLE STACK",
              dest="file"),
    )
    register(VerbSpec(
        "up",
        description=(
            "Render, materialise secrets and start the Docker Compose stack(s): by host profile "
            "(default), one stack directory (--dir), a layout (--layout) or a remote host (--host). "
            "--dry-run renders and validates but does not call Docker."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, expensive=True, dry_run=True,
        include_json=False, include_progress=False, options=up_options,
        dry_run_help="render and validate, but do not call Docker",
        examples=("ciu up --profile core --healthcheck", "ciu up --dir applications/web"),
        handler=adapter(["up"], up_options, yes=True, dry_run=True),
        constraints=(
            Requires("--thin", ("--host",), "--thin applies to the --host push path"),
            Requires("--bootstrap", ("--thin",), "--bootstrap needs the --thin activation path"),
            Requires("--rollback", ("--thin",), "--rollback needs the --thin activation path"),
            Conflicts(("--layout", "--profile"),
                      "a layout owns the host order and the bundles (S7.5c)"),
            Conflicts(("--layout", "--host"),
                      "a layout owns the host order and the bundles (S7.5c)"),
            Conflicts(("--layout", "--dir"),
                      "a layout owns the host order and the bundles (S7.5c)"),
        ),
    ))
    down_options = (
        PROFILE, PHASES, ROOT, PROTECTED,
        value("--dir", "stop only the selected stack's running Compose project (with --host, "
                       "the stack inside the pushed bundle)", "PATH", group="MODE"),
        value("--host", "run the stop action on a configured remote host", "NAME", group="MODE"),
    )
    register(VerbSpec(
        "down", description="Stop project containers; volumes are preserved (`ciu clean` removes them).",
        group=VerbGroup.MODIFICATION.value, mutating=True, include_json=False, include_progress=False,
        include_confirmation=True, options=down_options,
        handler=adapter(["down"], down_options, yes=True),
    ))
    clean_options = (
        PROFILE, PHASES, ROOT, IGNORE_ERRORS, PROTECTED,
        flag("--vanilla", "ALSO remove this workspace's rendered ciu.global.toml, ciu.env, "
                          "ciu.global.instance.toml.j2 and ciu.instance.generated.toml (a full "
                          "reset to freshly-cloned state); only after a successful teardown",
             group="BEHAVIOUR"),
        value("--identity", "remove resources carrying a retired instance id by exact Compose "
                            "labels, without loading the old config", "OLD_ID", group="IDENTITY"),
        value("--expected-checkout", "require the resource owner label to match this absolute CIU "
                                     "root; refuses the selected root's current identity", "PATH",
              group="IDENTITY"),
    )
    register(VerbSpec(
        "clean",
        description=(
            "Tear down completely: remove ALL project containers, `docker compose down -v "
            "--remove-orphans`, project volumes, vol-* hostdirs and rendered artifacts. The post-clean "
            "invariant (S6.4) is enforced: zero project containers AND zero project volumes remain."
        ),
        group=VerbGroup.MAINTENANCE.value, mutating=True, expensive=True, include_json=False,
        include_progress=False, include_confirmation=True, options=clean_options,
        handler=adapter(["clean"], clean_options, yes=True),
        constraints=(
            Requires("--expected-checkout", ("--identity",),
                     "--expected-checkout qualifies an --identity clean"),
            *(Conflicts(("--identity", other),
                        "an --identity clean removes a retired id's resources by exact labels "
                        "and cannot be combined with ordinary clean options")
              for other in ("--profile", "--phases", "--ignore-errors", "--vanilla",
                            "--i-understand-this-is-protected")),
        ),
    ))
    diagnose = (
        value("--project", "restrict to one Compose/CIU project label", "NAME"),
        value("--logs", "recent log lines per container to scan (0-10000; default 100)", "N",
              kind=int),
    )
    register(VerbSpec(
        "diagnose",
        description=(
            "Read-only scan of CIU-labelled containers: Docker state, OOM and exit evidence, "
            "restart counts, health history, memory/swap configuration and bounded recent logs "
            "correlated with known failure signatures."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=diagnose,
        handler=adapter(["diagnose"], diagnose, json_tokens=["--json"]),
    ))
    # `status` absorbs the former `health` verb (v8 S18, decision C5): the report is the
    # default, --gate turns the verdict into the exit status (S7.7), --preflight probes the
    # rendered images for missing healthcheck tools.
    strict_opt = flag("--strict", "(with --preflight) treat missing tools as an error",
                      group="MODE")
    host_opt = value("--host", "(with --gate) run the health gate on a remote host (SPEC S14)",
                     "NAME", group="REMOTE")
    thin_opt = flag("--thin", "(with --gate --host) run the docker-optional 'health' activation "
                              "verb (S14.6)", group="REMOTE")
    dir_opt = value("--dir", "(with --gate) gate ONE stack directory (with --host, the stack inside "
                             "the pushed bundle)", "PATH", group="SELECTION")
    status_options = (
        PROFILE, ROOT, dir_opt,
        flag("--gate", "run the S7.7 health gate over the selection: the exit status is the "
                       "verdict (0 healthy, 1 not)", group="MODE", xgroup="status-mode"),
        flag("--preflight", "probe the rendered compose images for missing healthcheck tools",
             group="MODE", xgroup="status-mode"),
        strict_opt, PHASES, host_opt, thin_opt,
    )
    report_handler = adapter(["status"], (PROFILE, ROOT), json_tokens=["--json"])
    gate_handler = adapter(["health"], (PROFILE, ROOT, dir_opt, PHASES, host_opt, thin_opt))
    preflight_handler = adapter(["health", "--preflight"], (ROOT, strict_opt))

    def status_handler(args: argparse.Namespace, runtime: Any) -> int | None:
        if args.preflight:
            return preflight_handler(args, runtime)
        if args.gate:
            return gate_handler(args, runtime)
        return report_handler(args, runtime)

    register(VerbSpec(
        "status",
        description=(
            "Read-only per-stack report: the resolved Compose project, running containers, each "
            "container's health and image reference. A stack not yet deployed has an empty "
            "container list; an unreachable Docker daemon is a clean error, never a healthy-looking "
            "result. --gate runs the S7.7 health gate instead and exits non-zero unless healthy; "
            "--preflight probes images for missing healthcheck tools."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=status_options,
        handler=status_handler,
        examples=("ciu status --json", "ciu status --gate --profile core",
                  "ciu status --preflight --strict"),
        constraints=(
            Requires("--strict", ("--preflight",), "--strict qualifies --preflight"),
            Requires("--thin", ("--host",), "--thin applies to the --host path"),
            Requires("--host", ("--gate",), "a remote verdict is the --gate form"),
            Requires("--phases", ("--gate",), "phases restrict the health gate"),
            Requires("--dir", ("--gate",), "--dir selects the stack the health gate checks"),
            Conflicts(("--json", "--gate"), "the gate's verdict is its exit status"),
            Conflicts(("--json", "--preflight"), "the preflight report is text"),
        ),
    ))

    # ---- PROVISIONING / MIGRATION ---------------------------------------------------
    check_options = (
        PROFILE, PHASES, ROOT,
        flag("--live", "probe live Vault/Postgres/MinIO/Consul/Docker state too"),
    )
    register(VerbSpec(
        "check",
        description=(
            "Validate the whole config pipeline across the selection (no deploy, S13.4a): stack "
            "shape, secret grammar, the requires/provides graph, governance, hooks, the guarded "
            "compose render, the leak scan, vault-presence, state-secrets and migration stages. "
            "Entirely in memory: no hostdir, secret, rendered file or hook run()."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=check_options,
        handler=adapter(["check"], check_options, json_tokens=["--json"]),
    ))
    graph_options = (
        PROFILE, PHASES, ROOT,
        value("--format", "graph format; use --json for the JSON document", "FMT",
              choices=("mermaid", "dot")),
    )
    register(VerbSpec(
        "graph",
        description="Render the requires/provides dependency graph to stdout (never deploys).",
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=graph_options,
        handler=adapter(["graph"], graph_options, json_tokens=["--format", "json"]),
        constraints=(Conflicts(("--format", "--json"), "--json selects the JSON format"),),
    ))
    # `migrate` absorbs `migration-check` (v8 S18: `ciu migrate [--check] ...`). v7 has only the
    # report mode, so --check is mandatory here; v8 adds the mutating modes beside it.
    mig = (ROOT, flag("--check", "report artifacts in THIS checkout left behind by an older "
                                 "CIU (S13.7); exit non-zero on ANY finding", group="MODE"))
    migrate_adapter = adapter(["migration-check"], (ROOT,), json_tokens=["--json"])

    def migrate_handler(args: argparse.Namespace, runtime: Any) -> int | None:
        if not args.check:
            raise CliFailure("ciu migrate needs --check (the only mode in v7)", exit_code=EXIT_USAGE)
        return migrate_adapter(args, runtime)

    register(VerbSpec(
        "migrate",
        description=(
            "Report artifacts in THIS checkout left behind by an older CIU (S13.7). CIU performs "
            "hard cutovers; this is the one place that knows its own version history, so a stale "
            "artifact produces a named finding with a remediation. A diagnostic: exit non-zero on "
            "ANY finding, regardless of WARN/ERROR. `ciu check`'s `migration` stage walks the same "
            "rules."
        ),
        group=VerbGroup.EXPLORATION.value, include_progress=False, options=mig,
        examples=("ciu migrate --check --json",), handler=migrate_handler,
    ))

    # ---- DEV LOOP -------------------------------------------------------------------
    targets = _remainder_argument("targets", "bake targets (default: all)", "TARGET")
    bake_options = (
        PROFILE, ROOT,
        flag("--no-cache", "pass --no-cache to buildx"),
        flag("--allow-shared-tag", "allow a linked-worktree bake to write shared image tags"),
    )
    register(VerbSpec(
        "bake",
        description=(
            "Thin wrapper over `docker buildx bake --load`. No targets and no --profile bakes `all`; "
            "--profile resolves the targets exactly as `ciu up --profile` would deploy them. "
            "--profile and explicit targets are mutually exclusive."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, expensive=True, include_json=False,
        include_progress=False, include_confirmation=False, arguments=(targets,),
        options=bake_options, handler=adapter(["bake"], bake_options, (targets,)),
    ))
    stack = ArgumentSpec("stack", "stack directory (relative to repo root) carrying [<root>.dev]",
                         metavar="STACK")
    dev_options = (
        value("--profile", "host profile to render for (default: active profile)", "NAME"),
        flag("--no-prebuild", "skip the prebuild steps (re-run the dev server only)"),
        ROOT,
    )
    register(VerbSpec(
        "dev",
        description=(
            "Run the stack's [<root>.dev] dev-loop profile (S5a): ordered prebuild steps, then the "
            "long-running command with the source bind-mounted. For HMR dev servers and "
            "contract-coupled pre-build chains."
        ),
        group=VerbGroup.MODIFICATION.value, mutating=True, expensive=True, include_json=False,
        include_progress=False, include_confirmation=False, arguments=(stack,), options=dev_options,
        handler=adapter(["dev"], dev_options, (stack,)),
    ))

    # ---- SECRETS / REMOTE -----------------------------------------------------------
    register(VerbSpec("secrets", description="Materialised secret store files (S4.25).",
                      group=VerbGroup.MIXED.value, mutating=True, delegate=_secrets_cli(),
                      include_json=False, include_progress=False))
    register(VerbSpec("host", description="Remote host inventory (.ciu.hosts.toml).",
                      group=VerbGroup.AUTHENTICATION.value, mutating=True, delegate=_host_cli(),
                      include_json=False, include_progress=False))
    ssh_host = ArgumentSpec("host", "host name in the inventory", metavar="HOST")
    ssh_cmd = _remainder_argument("command", "command to run, after `--` (default: interactive shell)",
                                  "CMD")
    ssh_options = (ROOT, flag("--admin", "use the admin key/user (higher-privilege access)"))
    register(VerbSpec(
        "ssh",
        description=(
            "Open an interactive shell or run a command on a remote host; host config is read from "
            ".ciu.hosts.toml or ~/.ciu/hosts.toml. Anything after `--` is the remote command and is "
            "never parsed as a CIU flag."
        ),
        group=VerbGroup.MIXED.value, mutating=True, interactive=True, include_confirmation=False,
        include_json=False, include_progress=False, arguments=(ssh_host, ssh_cmd),
        options=ssh_options, examples=("ciu ssh web1 -- uptime",),
        handler=adapter(["ssh"], ssh_options, (ssh_host, ssh_cmd), remainder="command",
                        positional_first=True),
    ))

    # ---- shared library verbs -------------------------------------------------------
    from cli_extended import register_doctor, register_skills_verbs

    from .doctor import doctor_checks

    register_skills_verbs(registry, package="ciu")
    register_doctor(registry, doctor_checks())
    return registry.build()
