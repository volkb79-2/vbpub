#!/usr/bin/env python3
"""cmru — repo-root entry point for the Configurable Multi Release Utility.

Run from the repo root:  ``./cmru.py <verb> [args]``  ≡  ``cmru <verb> [args]``.
``./cmru.py --help`` lists the verbs and the typical workflow. A standalone project
uses its own ``cmru.toml``; this multi-project checkout uses ``cmru.orchestration.toml``.
(secrets via cmru.secret.toml / $GITHUB_PUSH_PAT — see cmru/docs/SPEC.md S2.4).

Named ``cmru.py`` (not ``cmru``) because the ``cmru/`` package dir occupies that name.
Prefer ``pip install -e cmru`` to get a bare ``cmru`` on PATH; this shim just puts
``cmru/src`` on sys.path and calls ``cmru.cli:main`` so it works without installing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "cmru" / "src"))

from cmru.cli import main  # noqa: E402


_ESTATE_CONFIG_VERBS = frozenset({
    "build", "changelog", "cleanup", "publish", "release", "resolve",
    "run", "standards", "status",
})


def _root_argv(argv: list[str]) -> list[str]:
    """Make this repository-specific shim select its explicit orchestration file.

    The installed ``cmru`` executable remains portable and therefore defaults to
    ``$PWD/cmru.toml``. This file is different: it is the vbpub estate launcher,
    and its root config is an explicit, known fact. Injecting the absolute path
    here keeps the convenience at the wrapper boundary without making the core
    CLI discover a parent checkout or add a compatibility fallback.
    """
    if not argv or argv[0] not in _ESTATE_CONFIG_VERBS:
        return argv
    if any(arg == "--config" or arg.startswith("--config=") for arg in argv[1:]):
        return argv
    return [*argv, "--config", str(ROOT / "cmru.orchestration.toml")]


if __name__ == "__main__":
    # Keep progress visible through `2>&1 | tee ...`; otherwise Python switches
    # stdout to block buffering because the pipeline is not a TTY.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    main(_root_argv(sys.argv[1:]))
