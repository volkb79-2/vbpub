"""Exit-on-severity policy for CIU (`ciu.exit_on`).

Replaces the previous boolean ``fail_fast`` / ``CIU_WARNINGS_AS_ERRORS``
mechanism with a single closed-vocabulary config key that controls the
minimum severity at which CIU aborts the run.

Vocabulary
----------
``WARN``    Exit on any warning OR error (strictest).
``ERROR``   Exit on errors only; warnings are logged and the run continues.
            This is the DEFAULT — it allows WARN to pass without aborting.
``NEVER``   Never abort due to this policy. All findings are logged
            regardless of severity; the operator accepts all risks.

Resolution order (first non-null wins):

1. ``ciu.exit_on`` in rendered global config (closed vocabulary string).
2. ``CIU_EXIT_ON`` environment variable (same vocabulary).
3. Default: ``ERROR``.

An explicit config declaration ALWAYS wins over the env variable — ambient
shell state cannot silently weaken a project's stated posture.

This is deliberately NOT applied to every existing ``[WARN]``/error site in
CIU — see docs/DESIGN-NOTES.md D6 for the survey of candidates and why a few
(S15.G9-1's missing-slice abort; S15.13's forward-compat unknown-key warning)
are deliberately NOT wired through this, at least not yet.
"""
from __future__ import annotations

import os

EXIT_ON_ENV_VAR = "CIU_EXIT_ON"

EXIT_ON_VALUES = ("WARN", "ERROR", "NEVER")
DEFAULT_EXIT_ON = "ERROR"

_VALID_EXIT_ON = frozenset(EXIT_ON_VALUES)


def _resolve_exit_on(config: dict | None = None) -> str:
    """Resolve the effective exit-on threshold from config, env, then default.

    Raises ValueError on any invalid value from either source, naming the
    key and the accepted vocabulary.
    """
    if config is not None:
        ciu_cfg = config.get("ciu")
        if isinstance(ciu_cfg, dict) and "exit_on" in ciu_cfg:
            return _validate_exit_on(ciu_cfg["exit_on"], source="ciu.exit_on")

    raw_env = os.environ.get(EXIT_ON_ENV_VAR)
    if raw_env is not None:
        return _validate_exit_on(raw_env.strip().upper(), source=f"${EXIT_ON_ENV_VAR}")

    return DEFAULT_EXIT_ON


def _validate_exit_on(value: object, *, source: str) -> str:
    normalized = str(value).strip().upper()
    if normalized not in _VALID_EXIT_ON:
        raise ValueError(
            f"[S10.7] {source} must be one of {', '.join(EXIT_ON_VALUES)} "
            f"(got {value!r})"
        )
    return normalized


def should_exit_on(severity: str, *, config: dict | None = None) -> bool:
    """Return True when *severity* meets or exceeds the configured threshold.

    Severity ordering: NEVER < WARN < ERROR (in terms of "how much does it take
    to make us exit"). A higher threshold means MORE things cause an exit.

    - exit_on=WARN:  exits on severity="WARN" and severity="ERROR"
    - exit_on=ERROR: exits on severity="ERROR" only
    - exit_on=NEVER: never exits via this policy

    An optional *config* dict enables config-driven policy resolution.
    """
    threshold = _resolve_exit_on(config)
    if threshold == "NEVER":
        return False
    if threshold == "WARN":
        return severity.upper() in ("WARN", "ERROR")
    # threshold == "ERROR"
    return severity.upper() == "ERROR"


# Backward-compat aliases used by existing call sites until they are migrated.
def warnings_as_errors_enabled() -> bool:
    """Legacy shim: True when exit_on resolves to WARN (the old 'enabled' posture).

    Existing call sites use warn_or_raise() which now delegates to should_exit_on().
    This function remains so external code importing it doesn't break during
    the transition; new code should use _resolve_exit_on() directly.
    """
    return _resolve_exit_on() == "WARN"


def warn_or_raise(message: str, *, severity: str = "WARN", config: dict | None = None) -> None:
    """Print a message tagged by *severity*; then exit per the exit_on policy.

    *severity* is ``"WARN"`` or ``"ERROR"`` (lowercase accepted). When
    ``should_exit_on(severity, config=config)`` returns True, raises
    ``ValueError(message)`` immediately after printing. Otherwise the run
    continues.

    Callers should pass *message* already fully formatted (including any
    ``[S-xx]`` spec-ID tag). The raised exception's text is exactly the
    printed line's payload.
    """
    tag = severity.upper()
    print(f"[{tag}] {message}", flush=True)
    if should_exit_on(tag, config=config):
        raise ValueError(message)
