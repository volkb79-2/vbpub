"""The ``pwmcp`` console script, registered on the shared cli-extended grammar."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    LogLevel,
    OptionSpec,
    RegisteredCli,
    VerbGroup,
    VerbSpec,
    VersionLookupError,
    register_doctor,
    register_skills_verbs,
)

from .diagnostics import DOCTOR_CHECKS, try_load_contract
from .session import BrowserLease, PwmcpError
from .user_agent import ResolvedUserAgent, UserAgentError, cdp_override_params, resolve

DEFAULT_CONTRACT = "http://pwmcp:3000/contract"
_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
EXIT_NOT_INSTALLED = 3


def identity(version_file: Path = _VERSION_FILE) -> CliIdentity:
    """Resolve the CLI identity: installed metadata and the packaged VERSION must agree."""

    return CliIdentity.resolve(
        name="PWMCP",
        long_name="client utility",
        command="pwmcp",
        distribution="pwmcp-client",
        version_file=version_file,
    )


def _contract(args: argparse.Namespace, runtime: CliRuntime) -> None:
    contract, problem = try_load_contract(args.contract)
    if contract is None:
        raise CliFailure(
            f"cannot load the pwmcp contract from {args.contract} ({problem})",
            hint="start the pwmcp stack or pass --contract URL_OR_FILE",
        )
    if runtime.json_mode:
        runtime.output.primary(dict(contract.__dict__))
    else:
        runtime.output.primary(json.dumps(contract.__dict__, indent=2, sort_keys=True))


def _server_identity(contract: object) -> ResolvedUserAgent:
    """The server-level user-agent setting as published in the contract (None = default)."""

    value = getattr(contract, "user_agent", None)
    if not value:
        return ResolvedUserAgent(None, "default")
    return ResolvedUserAgent(str(value), str(getattr(contract, "user_agent_source", "custom")))


def _apply_client_hints(context: object, page: object, ua: str, runtime: CliRuntime) -> None:
    """Set the UA and its matching client-hint metadata over CDP on this page.

    ``new_context(user_agent=...)`` already sets the UA string; this additionally sets
    ``userAgentMetadata`` (Sec-CH-UA brands, navigator.userAgentData) so they do not
    still say HeadlessChrome. Only done when the UA names a recognisable browser; a
    failure is reported, never silent, and leaves the UA-string override in place.
    """

    params = cdp_override_params(ua)
    if "userAgentMetadata" not in params:
        runtime.output.warn("client-hint metadata not set: the user agent names no recognised browser")
        return
    try:
        session = context.new_cdp_session(page)  # type: ignore[attr-defined]
        session.send("Emulation.setUserAgentOverride", params)
    except Exception as exc:  # noqa: BLE001 - any CDP failure degrades to UA-string-only, loudly
        runtime.output.warn(f"client-hint metadata not applied (user agent string only): {exc}")


def _observed_user_agent(page: object) -> str | None:
    """What the page itself reports, so the result states the measured identity."""

    try:
        value = page.evaluate("navigator.userAgent")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - measurement is best-effort; fall back to the configured value
        return None
    return value if isinstance(value, str) and value else None


def _inspect(args: argparse.Namespace, runtime: CliRuntime) -> None:
    """Read rendered page text, an ARIA snapshot, or a remote screenshot."""

    try:
        parsed = urlsplit(args.url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise CliFailure(f"URL is malformed: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise CliFailure("URL must be an absolute http:// or https:// URL")
    if args.timeout_ms <= 0:
        raise CliFailure("--timeout-ms must be a positive integer")
    if args.format == "screenshot" and not args.output:
        raise CliFailure("--output is required when --format=screenshot")

    # PWMCP-09: an explicit per-call --user-agent is validated before connecting.
    call_override: ResolvedUserAgent | None = None
    if args.user_agent is not None:
        try:
            call_override = resolve(args.user_agent)
        except UserAgentError as exc:
            raise CliFailure(f"--user-agent is invalid: {exc}") from exc

    output_path = Path(args.output).expanduser() if args.output else None
    image_type = "png"
    if args.format == "screenshot" and output_path is not None:
        suffix = output_path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            image_type = "jpeg"
        elif suffix != ".png":
            raise CliFailure("screenshot output must end in .png, .jpg, or .jpeg")

    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:
        raise CliFailure(
            "the Playwright Python package is not installed",
            hint="install the client bundle for this PWMCP release",
        ) from exc

    try:
        with BrowserLease.connect(
            contract_url=args.contract,
            label="pwmcp-inspect",
            timeout_ms=args.timeout_ms,
        ) as lease:
            # Effective identity: the per-call --user-agent wins, else the server-level
            # setting the service publishes in its contract, else the browser default.
            active = call_override or _server_identity(getattr(lease, "contract", None))
            context_options: dict[str, object] = {
                "ignore_https_errors": args.ignore_https_errors
            }
            if active.user_agent is not None:
                context_options["user_agent"] = active.user_agent
            # A fresh context per call: the override dies with it, which is the restore.
            context = lease.browser.new_context(**context_options)
            try:
                page = context.new_page()
                if active.user_agent is not None:
                    _apply_client_hints(context, page, active.user_agent, runtime)
                page.goto(args.url, wait_until=args.wait_until, timeout=args.timeout_ms)
                if args.wait_for:
                    page.locator(args.wait_for).filter(visible=True).first.wait_for(
                        state="visible", timeout=args.timeout_ms
                    )

                if args.format == "text":
                    payload = page.locator("body").inner_text(timeout=args.timeout_ms)
                elif args.format == "snapshot":
                    payload = page.locator("body").aria_snapshot(timeout=args.timeout_ms)
                else:
                    assert output_path is not None
                    payload = page.screenshot(
                        full_page=args.full_page,
                        type=image_type,
                        animations="disabled",
                        timeout=args.timeout_ms,
                    )
                observed_ua = _observed_user_agent(page)
            finally:
                context.close()
    except (PwmcpError, PlaywrightError, OSError, ValueError) as exc:
        raise CliFailure(
            f"pwmcp inspect failed: {exc}",
            hint="check that PWMCP is running and the page's wait condition is reachable",
        ) from exc

    # Transparency (PWMCP-09). With no override (source "default") the output is exactly
    # what it always was: same stdout, same JSON shape, empty stderr. Only when an override
    # is ACTIVE is the identity reported: one stderr line that --quiet cannot silence, and
    # in --json mode the identity fields (the bare JSON string becomes an object).
    overridden = active.source != "default"
    fields = {
        "user_agent": observed_ua or active.user_agent,
        "user_agent_source": active.source,
    }
    if overridden:
        runtime.output.emit(
            LogLevel.INFO,
            f"user agent: {fields['user_agent']} (source: {fields['user_agent_source']})",
            force=True,
        )

    if output_path is None:
        if runtime.json_mode and overridden:
            runtime.output.primary({"content": payload, **fields})
        else:
            runtime.output.primary(payload)
        return

    try:
        if args.format == "screenshot":
            output_path.write_bytes(payload)
        else:
            output_path.write_text(payload.rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        raise CliFailure(f"cannot write {args.format} output to {output_path}: {exc}") from exc
    runtime.output.info(f"wrote {args.format} output to {output_path}")
    if runtime.json_mode and overridden:
        runtime.output.primary({"output": str(output_path), "format": args.format, **fields})


def build_cli(cli_identity: CliIdentity | None = None) -> RegisteredCli:
    """Build the registered CLI; import-safe (nothing runs until ``.run()``)."""

    registry = CliRegistry(
        cli_identity or identity(),
        prog="pwmcp",
        description="Diagnose and inspect the pwmcp Playwright service from a consumer container.",
        getting_started=(
            "pwmcp doctor",
            "pwmcp contract",
            "pwmcp inspect https://example.com --format snapshot",
        ),
        global_options=(
            OptionSpec(
                ("--contract",),
                "pwmcp contract URL or JSON file",
                group="CONTRACT",
                metavar="URL_OR_FILE",
                parser_kwargs={"default": DEFAULT_CONTRACT},
            ),
        ),
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "contract",
        description="print the pwmcp server contract (release, Playwright version, endpoints, limits) as JSON",
        group=VerbGroup.EXPLORATION.value,
        examples=("pwmcp contract", "pwmcp contract --contract ./pwmcp.contract.json"),
        include_progress=False,
        handler=_contract,
    ))
    registry.register(VerbSpec(
        "inspect",
        description="inspect a rendered webpage through the remote PWMCP browser",
        group=VerbGroup.EXPLORATION.value,
        examples=(
            "pwmcp inspect https://example.com",
            "pwmcp inspect https://example.com --format snapshot --output page.yml",
            "pwmcp inspect https://example.com --format screenshot --output page.png --full-page",
            "pwmcp inspect https://example.com --wait-for '#content' --wait-until domcontentloaded",
            "pwmcp inspect https://self-signed.example --ignore-https-errors",
            "pwmcp inspect https://example.com --user-agent edge-windows",
        ),
        include_progress=False,
        arguments=(ArgumentSpec(
            "url",
            "absolute HTTP or HTTPS page URL to open in the remote browser",
            metavar="URL",
        ),),
        options=(
            OptionSpec(
                ("--format",),
                "output rendered body text, an ARIA snapshot, or a PNG/JPEG screenshot",
                group="OUTPUT",
                metavar="FORMAT",
                parser_kwargs={"choices": ("text", "snapshot", "screenshot"), "default": "text"},
            ),
            OptionSpec(
                ("--output",),
                "write the result to a file instead of stdout (required for screenshots)",
                group="OUTPUT",
                metavar="PATH",
            ),
            OptionSpec(
                ("--wait-until",),
                "navigation condition before inspection",
                group="WAIT",
                metavar="STATE",
                parser_kwargs={
                    "choices": ("commit", "domcontentloaded", "load", "networkidle"),
                    "default": "load",
                },
            ),
            OptionSpec(
                ("--wait-for",),
                "CSS selector that must become visible before inspection",
                group="WAIT",
                metavar="SELECTOR",
            ),
            OptionSpec(
                ("--timeout-ms",),
                "navigation and selector timeout in milliseconds",
                group="WAIT",
                metavar="MILLISECONDS",
                parser_kwargs={"type": int, "default": 30_000},
            ),
            OptionSpec(
                ("--full-page",),
                "capture the full scrollable page when taking a screenshot",
                group="OUTPUT",
                parser_kwargs={"action": "store_true"},
            ),
            OptionSpec(
                ("--user-agent",),
                "present this user agent for this inspection only: a preset "
                "(edge-windows, chrome-windows, firefox-windows), 'default' for the browser's "
                "own, or a custom printable-ASCII string; overrides the server-level setting",
                group="IDENTITY",
                metavar="PRESET_OR_STRING",
            ),
            OptionSpec(
                ("--ignore-https-errors",),
                "accept invalid HTTPS certificates for any host in this inspection context",
                group="TLS",
                parser_kwargs={"action": "store_true"},
            ),
        ),
        handler=_inspect,
    ))
    register_doctor(
        registry,
        DOCTOR_CHECKS,
        description="check that the pwmcp contract is reachable and the installed Playwright matches it",
    )
    register_skills_verbs(registry, package="pwmcp_client")
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    try:
        cli = build_cli()
    except VersionLookupError as exc:
        print(f"[ERROR] cannot determine the pwmcp-client version: {exc}", file=sys.stderr)
        return EXIT_NOT_INSTALLED
    return cli.run(argv=argv)


if __name__ == "__main__":
    raise SystemExit(main())
