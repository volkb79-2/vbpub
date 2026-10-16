"""Doctor checks for the independently installable pwmcp client.

The shared ``doctor`` verb (cli-extended) runs these named checks; the CLI wiring
lives in :mod:`pwmcp_client.cli`.
"""

from __future__ import annotations

import argparse

from cli_extended import CheckResult, CliRuntime, DoctorCheck

from .contract import PwmcpContract, load_contract
from .session import VersionMismatch, verify_installed_playwright

# What loading the contract (URL or file) can raise for an unreachable server, a
# missing file, malformed JSON (a ValueError) or a payload without the expected keys.
_LOAD_ERRORS = (OSError, ValueError, KeyError, TypeError)


def _one_line(value: object) -> str:
    return " ".join(str(value).split())


def try_load_contract(source: str) -> tuple[PwmcpContract | None, str]:
    """Load the contract, returning ``(None, one-line problem)`` instead of raising."""

    try:
        return load_contract(source), ""
    except _LOAD_ERRORS as exc:
        return None, f"{type(exc).__name__}: {_one_line(exc)}"


def contract_check(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    contract, problem = try_load_contract(args.contract)
    if contract is None:
        return CheckResult(
            "fail",
            f"cannot load the pwmcp contract from {args.contract} ({problem})",
            remedy="start the pwmcp stack or pass --contract URL_OR_FILE",
        )
    return CheckResult(
        "ok",
        f"release={contract.release} ws={contract.ws_url}",
        details=dict(contract.__dict__),
    )


def playwright_check(runtime: CliRuntime, args: argparse.Namespace) -> CheckResult:
    contract, _problem = try_load_contract(args.contract)
    if contract is None:
        return CheckResult("skip", "the pwmcp contract is unavailable (see the contract check)")
    try:
        installed = verify_installed_playwright(contract)
    except VersionMismatch as exc:
        return CheckResult(
            "fail",
            _one_line(exc),
            remedy=f"pip install playwright=={contract.playwright_version}",
        )
    return CheckResult(
        "ok",
        f"playwright={installed}",
        details={"installed": installed, "required": contract.playwright_version},
    )


DOCTOR_CHECKS = (
    DoctorCheck("contract", "the pwmcp server contract is reachable and well-formed", contract_check),
    DoctorCheck(
        "playwright",
        "the installed Playwright package matches the contract's protocol",
        playwright_check,
    ),
)
