"""CMRU's one domain-error family (W2-PKG5 review fix round 1, B1).

Deliberate refusals and failures are *expected* outcomes: they carry the exit
code of CMRU's taxonomy (``cmru.exit_codes``) and an optional remediation hint,
and the CLI boundary renders them as ``[ERROR] <message>`` plus the hint, never
as an "unexpected" internal error.

Two inheritance choices are deliberate:

* :class:`CmruError` subclasses ``RuntimeError``. The release transaction's
  cleanup and rollback sites catch ``RuntimeError`` (``_DOMAIN_ERRORS``); a
  domain error must still run every one of them.
* :class:`CmruError` also subclasses the library's ``CliFailure``, which the
  CLI runner already renders with the right exit code, message and hint. The
  library's ``expected_exceptions`` registry option exists but always exits 1
  and has no hint, so it cannot carry these codes. Not registering bare
  ``RuntimeError``/``Exception`` keeps genuine programming errors unexpected.
"""
from __future__ import annotations

import subprocess

from cli_extended import CliFailure

from cmru import exit_codes


class CmruError(CliFailure, RuntimeError):
    """A deliberate CMRU refusal or failure with a taxonomy exit code."""

    default_exit_code = exit_codes.FAILURE

    def __init__(
        self, message: str, *, exit_code: int | None = None, hint: str | None = None,
    ) -> None:
        super().__init__(
            message,
            exit_code=self.default_exit_code if exit_code is None else exit_code,
            hint=hint,
        )

    def __str__(self) -> str:
        return self.message


class UsageRefusal(CmruError):
    """The invocation names something that does not exist or is malformed (2)."""

    default_exit_code = exit_codes.CONFIG_ERROR


class CredentialMissing(CmruError):
    """A required credential is absent (3: a prerequisite is missing)."""

    default_exit_code = exit_codes.PREREQ_MISSING


class StepUnavailable(CmruError):
    """A declared step or derived configuration prerequisite is absent (3)."""

    default_exit_code = exit_codes.PREREQ_MISSING


class UnsafeRecord(CmruError):
    """A retained build record is unsafe or incomplete to act on (4)."""

    default_exit_code = exit_codes.REFUSED


class RefusedBeforeChange(CmruError):
    """A refusal made before anything changed; exit 4 (REFUSED)."""

    default_exit_code = exit_codes.REFUSED


class ReleaseLockHeld(RefusedBeforeChange):
    """Another release/build/abandon already holds the repository's release lock."""


class StepFailed(CmruError, subprocess.CalledProcessError):
    """A project step's command exited non-zero (1).

    Also a ``CalledProcessError`` so every caller that inspects that type, and
    ``.returncode``, keeps working.
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int,
        cmd: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        # ``CliFailure.__init__`` chains ``super().__init__(message)``, which in this
        # MRO reaches ``CalledProcessError`` (positional returncode/cmd), so
        # initialise the two bases explicitly instead of through ``CmruError``.
        subprocess.CalledProcessError.__init__(
            self, returncode, cmd if cmd is not None else [],
        )
        self.message = message
        self.exit_code = self.default_exit_code
        self.hint = hint
        self.show_help = False

    def __str__(self) -> str:
        return self.message
