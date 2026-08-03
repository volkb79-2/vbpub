"""Global warning-severity policy — CIU v2 (S10.6).

Some conditions CIU detects are configuration SMELLS rather than definite,
unconditional breakage — e.g. S15.16's mem_min ancestor-chain gap: real,
worth surfacing loudly, but an operator MAY already know about it and accept
the risk for one run. The default posture is **fail first, fail early**:
such a condition is reported as a ``[WARN]`` line AND, by default, raises
immediately — nothing gets hidden behind a warning line nobody reads.

This is deliberately NOT applied to every existing `[WARN]`/error site in
CIU — see docs/DESIGN-NOTES.md D6 for the survey of candidates and why a few
(S15.G9-1's missing-slice abort; S15.13's forward-compat unknown-key warning)
are deliberately NOT wired through this, at least not yet.

Public API
----------
WARNINGS_AS_ERRORS_ENV_VAR : str
warnings_as_errors_enabled() -> bool
warn_or_raise(message) -> None
"""
from __future__ import annotations

import os

WARNINGS_AS_ERRORS_ENV_VAR = "CIU_WARNINGS_AS_ERRORS"


def warnings_as_errors_enabled() -> bool:
    """Resolve the global warnings-as-errors posture. Default: enabled (``True``).

    Read fresh from the environment on every call — no cached global state,
    so tests (and a long-lived process reconfigured mid-run) see a changed
    env var immediately. Env-var-only, no dedicated CLI flag: this mirrors
    every other ambient ``CIU_*`` toggle in this codebase
    (``CIU_SKIP_DOOD_PREFLIGHT``, ``CIU_ADOPT_LEGACY_PROJECT``,
    ``CIU_SSH_INSECURE_TOFU``, ...), none of which have one either — CIU's
    per-verb argparse surface (S10.4) is built as individual small parsers
    per verb, not a shared parent parser, so a single flag meant to apply
    everywhere is cheaper and more consistent as an env var than as N
    separate ``--warnings-as-errors`` registrations.

    Any value other than the literal string ``"0"`` counts as enabled —
    unset, ``"1"``, or anything else all mean "yes" (fail-safe default: an
    operator must explicitly opt OUT, not merely fail to opt in).
    """
    return os.environ.get(WARNINGS_AS_ERRORS_ENV_VAR, "1") != "0"


def warn_or_raise(message: str) -> None:
    """Print ``[WARN] {message}``; then, unless opted out, raise ``ValueError(message)``.

    Fail first, fail early: nothing gets hidden behind a warning line nobody
    reads. Set ``CIU_WARNINGS_AS_ERRORS=0`` to opt into the softer behavior
    (log the ``[WARN]``, keep going) for a specific run.

    Callers should pass *message* already fully formatted (including any
    ``[S-xx]`` spec-ID tag) — the raised exception's text is exactly the
    printed line's payload, no re-wrapping, so the same string is what an
    operator sees whether it ends up on stdout or in a traceback.
    """
    print(f"[WARN] {message}", flush=True)
    if warnings_as_errors_enabled():
        raise ValueError(message)
