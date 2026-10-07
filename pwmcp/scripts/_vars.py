"""Load the prepared pwmcp release coordinate from ``cmru.vars``.

CMRU's explicit ``prepare`` phase owns the resolver and the generated file.
Build/publish phases consume that exact coordinate; they never silently invoke
network discovery or replace it with an environment value.

It also builds the shared cli-extended registration for the release-step scripts
(``script_cli``): every script imports the INSTALLED ``cli_extended`` (CX-D3) and
takes its version from the committed ``pwmcp.contract.json`` release.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from cli_extended import (
    CliIdentity,
    CliRegistry,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
)

# Keys that must be present in cmru.vars for downstream scripts to work.
_REQUIRED_KEYS = (
    "PLAYWRIGHT_VERSION",
    "PLAYWRIGHT_DISTRO",
    "PLAYWRIGHT_IMAGE_DIGEST",
    "PLAYWRIGHT_MCP_VERSION",
    "CHROME_DEVTOOLS_MCP_VERSION",
    "MCP_PROXY_VERSION",
    "LIGHTHOUSE_VERSION",
    "PWMCP_VERSION",
)

# Canonical locations relative to this file's position.
# This file lives at pwmcp/scripts/_vars.py  →  parent is pwmcp/scripts/,
# parent.parent is pwmcp/.
_SCRIPTS_DIR = Path(__file__).resolve().parent
_PWMCP_DIR = _SCRIPTS_DIR.parent
_VARS_FILE = _PWMCP_DIR / "cmru.vars"


def _parse_vars_file(path: Path) -> dict[str, str]:
    """Return KEY→value pairs from a KEY=VALUE env file (comments + blank lines skipped)."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _is_complete(vars_map: dict[str, str]) -> bool:
    return all(vars_map.get(k) for k in _REQUIRED_KEYS)


def load_vars() -> dict[str, str]:
    """Load the exact prepared coordinate or fail with an actionable remedy.

    The file is intentionally authoritative over shell values.  If it is
    absent or malformed, start a new CMRU release transaction (or run the
    project's explicit prepare command) rather than discovering a newer
    upstream halfway through a build or push.
    """
    if not _VARS_FILE.exists():
        print(
            f"[ERROR] {_VARS_FILE} is absent; run CMRU's pwmcp prepare phase first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    vars_map = _parse_vars_file(_VARS_FILE)
    if not _is_complete(vars_map):
        missing = [k for k in _REQUIRED_KEYS if not vars_map.get(k)]
        print(
            f"[ERROR] cmru.vars is missing required prepared keys: {missing}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _apply_to_env(vars_map)
    return dict(vars_map)


def _apply_to_env(vars_map: dict[str, str]) -> None:
    """Apply the prepared coordinate as the authoritative process environment."""
    for key, value in vars_map.items():
        os.environ[key] = value


_CONTRACT_FILE = _PWMCP_DIR / "pwmcp.contract.json"


def _contract_release() -> str | None:
    """The committed release coordinate, or ``None`` when it cannot be read."""
    try:
        payload = json.loads(_CONTRACT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    release = payload.get("release") if isinstance(payload, dict) else None
    return release if isinstance(release, str) else None


def script_cli(
    command: str,
    *,
    long_name: str,
    description: str,
    handler: Callable[[argparse.Namespace], None],
    preview: Callable[[argparse.Namespace], str],
    options: Sequence[OptionSpec] = (),
    examples: Sequence[str] = (),
) -> RegisteredCli:
    """Register one release-step script as a single-command cli-extended CLI.

    Every pwmcp script mutates something (an image, a bundle, a projection file),
    so each declares ``dry_run=True``: ``--dry-run`` prints ``preview(args)`` and
    changes nothing. None takes ``--yes``: CMRU runs them as non-interactive steps,
    and choosing the step (``--push``, ``publish-bundle.py``) is the confirmation.
    ``identity_banner="never"`` keeps ``[ERROR] ...`` lines exactly as the scripts'
    own ``fail()`` helpers print them.
    """
    identity = CliIdentity.resolve(
        name="PWMCP",
        long_name=long_name,
        command=command,
        version_probe=_contract_release,
    )
    registry = CliRegistry(
        identity,
        prog=command,
        description=description,
        single_command=True,
        # Bare invocation IS the operation (CMRU runs `python3 build-bundle.py`): it must
        # reach the parser/handler, never print help and exit 0.
        no_args_action=True,
        unexpected_exceptions="report",
        identity_banner="never",
    )

    def run(args: argparse.Namespace, runtime) -> None:
        if runtime.dry_run:
            runtime.output.info(f"dry run: {preview(args)}")
            return
        handler(args)

    registry.register(VerbSpec(
        command,
        description=description,
        group=VerbGroup.MAINTENANCE.value,
        examples=tuple(examples),
        mutating=True,
        dry_run=True,
        confirmation_required=False,
        include_json=False,
        include_progress=False,
        options=tuple(options),
        handler=run,
    ))
    return registry.build()


def run_script(cli: RegisteredCli, argv: Sequence[str] | None) -> None:
    """Run a script's registered CLI keeping each script's historical ``main`` contract.

    ``main`` returns ``None`` on success and raises ``SystemExit(status)`` otherwise,
    exactly as the scripts did with argparse and ``fail()``; the shared runner itself
    returns an int, which this converts at the process boundary.
    """
    status = cli.run(argv=argv)
    if status:
        raise SystemExit(status)
