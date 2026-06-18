#!/usr/bin/env python3
"""cmru — repo-root entry point for the Configurable Multi Release Utility.

Run from the repo root:  ``./cmru.py <verb> [args]``  ≡  ``cmru <verb> [args]``.
``./cmru.py --help`` lists the verbs and the typical workflow. Config is ``cmru.toml``
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


if __name__ == "__main__":
    main()
