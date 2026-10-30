"""The ``pwmcp`` console script, registered on the shared cli-extended grammar."""

from __future__ import annotations

import argparse
import json
import os
import re
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

from . import ocr as ocr_module
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


_REGION = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*$")


def _parse_region(value: str) -> tuple[int, int, int, int]:
    found = _REGION.match(value)
    if not found or int(found.group(3)) == 0 or int(found.group(4)) == 0:
        raise CliFailure("--region must be x,y,w,h in whole pixels with a positive width and height", exit_code=2)
    x, y, w, h = (int(group) for group in found.groups())
    return x, y, w, h


def _capture_element(args: argparse.Namespace, runtime: CliRuntime) -> bytes:
    """Open ``args.url`` in a fresh context (as ``inspect`` does) and return a PNG of the element.

    The element is captured with Playwright's compositor screenshot, not ``canvas.toDataURL``:
    it works for 2D and WebGL canvases and any other element, and never trips over a tainted
    canvas. The price is that the PNG is the element as DISPLAYED (CSS size), not the canvas's
    intrinsic bitmap; ``--scale`` compensates for small text.
    """

    try:
        parsed = urlsplit(args.url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise CliFailure(f"URL is malformed: {exc}", exit_code=2) from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise CliFailure("URL must be an absolute http:// or https:// URL", exit_code=2)
    region = _parse_region(args.region) if args.region else None
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:
        raise CliFailure(
            "the Playwright Python package is not installed",
            hint="install the client bundle for this PWMCP release",
        ) from exc

    try:
        with BrowserLease.connect(
            contract_url=args.contract, label="pwmcp-ocr", timeout_ms=args.timeout_ms
        ) as lease:
            active = _server_identity(getattr(lease, "contract", None))
            options: dict[str, object] = {"ignore_https_errors": args.ignore_https_errors}
            if active.user_agent is not None:
                options["user_agent"] = active.user_agent
            context = lease.browser.new_context(**options)
            try:
                page = context.new_page()
                if active.user_agent is not None:
                    _apply_client_hints(context, page, active.user_agent, runtime)
                page.goto(args.url, wait_until="load", timeout=args.timeout_ms)
                target = page.locator(args.selector).filter(visible=True).first
                target.wait_for(state="visible", timeout=args.timeout_ms)
                if args.wait_ms:
                    page.wait_for_timeout(args.wait_ms)
                if region is None:
                    return target.screenshot(type="png", timeout=args.timeout_ms)
                target.scroll_into_view_if_needed(timeout=args.timeout_ms)
                box = target.bounding_box(timeout=args.timeout_ms)
                if box is None or region[0] + region[2] > box["width"] or region[1] + region[3] > box["height"]:
                    size = "not rendered" if box is None else f"{box['width']:g}x{box['height']:g}"
                    raise CliFailure(
                        f"--region {args.region} does not fit inside the element ({size})", exit_code=2
                    )
                clip = {"x": box["x"] + region[0], "y": box["y"] + region[1], "width": region[2], "height": region[3]}
                return page.screenshot(type="png", clip=clip, timeout=args.timeout_ms)
            finally:
                context.close()
    except (PwmcpError, PlaywrightError, OSError, ValueError) as exc:
        raise CliFailure(
            f"pwmcp ocr could not capture the element: {exc}",
            hint="check that PWMCP is running, the selector matches a visible element and the page loads",
        ) from exc


def _ocr_image_bytes(args: argparse.Namespace) -> bytes:
    if args.image == "-":
        data = sys.stdin.buffer.read()
        source = "standard input"
    else:
        path = Path(args.image).expanduser()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CliFailure(f"cannot read image {path}: {exc}") from exc
        source = str(path)
    if not data:
        raise CliFailure(f"the image from {source} is empty")
    return data


def _ocr(args: argparse.Namespace, runtime: CliRuntime) -> None:
    """Recognise text in an image (a file/stdin, or an element captured from a page)."""

    if bool(args.url) == bool(args.image):
        raise CliFailure("give exactly one of an image (--image PATH|-) or a page URL (with --selector)",
                         exit_code=2, show_help=True)
    if args.url and not args.selector:
        raise CliFailure("--selector is required when a URL is given", exit_code=2)
    if args.image and (args.selector or args.region or args.wait_ms):
        raise CliFailure("--selector, --region and --wait-ms apply only to a page URL, not --image", exit_code=2)
    if args.timeout_ms <= 0 or args.wait_ms < 0:
        raise CliFailure("--timeout-ms must be positive and --wait-ms must not be negative", exit_code=2)
    fmt = args.format
    if runtime.json_mode:
        if fmt == "tsv":
            raise CliFailure("--json and --format tsv contradict each other", exit_code=2)
        fmt = "json"

    # Resolve the sidecar BEFORE any capture, so a missing configuration fails fast.
    contract = None
    problem = ""
    discovered = not (args.sidecar_url or os.environ.get(ocr_module.OCR_URL_ENV))
    if discovered:
        contract, problem = try_load_contract(args.contract)
        if contract is None:
            # Distinct from "the server does not advertise OCR": here we do not know either way.
            raise CliFailure(
                f"cannot reach the pwmcp server to learn the OCR sidecar URL: {problem}",
                hint=f"start the pwmcp stack, fix --contract, or skip discovery with --sidecar-url / {ocr_module.OCR_URL_ENV}",
            )
    try:
        sidecar_url = ocr_module.resolve_sidecar_url(args.sidecar_url, contract=contract)
    except ocr_module.OcrError as exc:
        hint = "set pwmcp.ocr.enabled = true in the stack config" if isinstance(exc, ocr_module.OcrNotConfigured) else None
        raise CliFailure(str(exc), exit_code=2, hint=hint) from exc

    image = _capture_element(args, runtime) if args.url else _ocr_image_bytes(args)
    try:
        result = ocr_module.recognize(
            image,
            sidecar_url=sidecar_url,
            engine=args.engine,
            lang=args.lang,
            psm=args.psm,
            scale=args.scale,
            whitelist=args.whitelist,
            min_conf=args.min_conf,
            timeout=args.timeout_ms / 1000,
        )
    except ocr_module.OcrError as exc:
        raise CliFailure(f"pwmcp ocr failed: {exc}", hint="GET <sidecar>/v1/engines lists what the sidecar offers") from exc

    if result.completeness_unknown and args.min_conf is not None:
        runtime.output.warn(
            "the OCR sidecar predates the low_conf contract: it may have silently dropped the words below "
            f"--min-conf {args.min_conf:g}, so this result cannot be called complete; upgrade the sidecar"
        )
    if result.low_conf_words:
        runtime.output.warn(
            # (OcrResult.from_dict refuses flags without a threshold, so min_conf_applied is a number here)
            f"{result.low_conf_words} word(s) are below --min-conf {result.min_conf_applied:g} and flagged "
            "low_conf; they are still included in the result"
        )
    payload: object
    if fmt == "json":
        payload = result.to_dict()
    elif fmt == "tsv":
        payload = result.to_tsv()
    else:
        payload = result.text
    if args.output:
        output_path = Path(args.output).expanduser()
        text = json.dumps(payload, indent=2) if fmt == "json" else str(payload)
        try:
            output_path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        except OSError as exc:
            raise CliFailure(f"cannot write OCR output to {output_path}: {exc}") from exc
        runtime.output.info(f"wrote {fmt} output to {output_path}")
        return
    if fmt == "json":
        runtime.output.primary_json(payload)
    else:
        runtime.output.primary(payload)


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
            "pwmcp ocr --image console.png",
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
    registry.register(VerbSpec(
        "ocr",
        description="recognise text in an image (a screenshot file, stdin, or a page element) through the OCR sidecar",
        group=VerbGroup.EXPLORATION.value,
        examples=(
            "pwmcp ocr --image console.png",
            "pwmcp ocr --image console.png --format tsv --min-conf 60",
            "cat console.png | pwmcp ocr --image - --json",
            "pwmcp ocr https://scp.example/console --selector canvas --scale 2 --output screen.txt",
            "pwmcp ocr https://scp.example/console --selector canvas --region 0,0,640,200 --wait-ms 1000",
        ),
        include_progress=False,
        arguments=(ArgumentSpec(
            "url",
            "page URL to open in the remote browser and capture an element from (needs --selector); "
            "omit when using --image",
            metavar="URL",
            parser_kwargs={"nargs": "?", "default": None},
        ),),
        options=(
            OptionSpec(
                ("--image",),
                "recognise this local PNG/JPEG file, or '-' for standard input (the usual path: save a "
                "screenshot with the MCP browser_take_screenshot tool, then OCR it)",
                group="INPUT",
                metavar="PATH",
            ),
            OptionSpec(
                ("--selector",),
                "CSS selector of the element (e.g. a console canvas) to capture from the page",
                group="INPUT",
                metavar="CSS",
            ),
            OptionSpec(
                ("--region",),
                "capture only this rectangle of the element: x,y,w,h in pixels, relative to the element",
                group="INPUT",
                metavar="x,y,w,h",
            ),
            OptionSpec(
                ("--wait-ms",),
                "wait this long after the element is visible, before capturing it",
                group="WAIT",
                metavar="MILLISECONDS",
                parser_kwargs={"type": int, "default": 0},
            ),
            OptionSpec(
                ("--timeout-ms",),
                "navigation, capture and sidecar timeout in milliseconds",
                group="WAIT",
                metavar="MILLISECONDS",
                parser_kwargs={"type": int, "default": 30_000},
            ),
            OptionSpec(
                ("--engine",),
                "recognition engine (the sidecar lists them at /v1/engines)",
                group="RECOGNITION",
                metavar="NAME",
                parser_kwargs={"default": ocr_module.DEFAULT_ENGINE},
            ),
            OptionSpec(
                ("--lang",),
                "Tesseract language code(s), e.g. eng or eng+deu",
                group="RECOGNITION",
                metavar="LANG",
                parser_kwargs={"default": "eng"},
            ),
            OptionSpec(
                ("--psm",),
                "page segmentation mode: 3 auto, 4 columns, 6 uniform block (default; consoles), "
                "7 single line, 11 sparse text, 13 raw line",
                group="RECOGNITION",
                metavar="MODE",
                parser_kwargs={"type": int, "choices": (3, 4, 6, 7, 11, 13), "default": 6},
            ),
            OptionSpec(
                ("--scale",),
                "upscale the image by this integer factor before recognition (1-8); Tesseract wants "
                "glyphs of about 30 px or more; reported boxes stay in original pixels",
                group="RECOGNITION",
                metavar="FACTOR",
                parser_kwargs={"type": int, "default": 1},
            ),
            OptionSpec(
                ("--whitelist",),
                "only recognise these characters (printable ASCII, at most 128)",
                group="RECOGNITION",
                metavar="CHARS",
            ),
            OptionSpec(
                ("--min-conf",),
                "flag words whose confidence (0-100) is below N as low_conf; they are still returned, never removed. "
                "Without it nothing is assessed (low_conf is null, not false). Tesseract confidence is NOT calibrated: "
                "a 91 percent mean confidence read a garbled dialog and 10.0.2.15 as 16.0.2.15; a flag is a hint, never a safety "
                "check, and acting on text needs an EXACT match of a known screen signature",
                group="RECOGNITION",
                metavar="CONF",
                parser_kwargs={"type": float, "default": None},
            ),
            OptionSpec(
                ("--sidecar-url",),
                "OCR sidecar base URL; default: $PWMCP_OCR_URL, else the ocr_url the pwmcp contract "
                "advertises (set ocr.enabled on the server)",
                group="SIDECAR",
                metavar="URL",
            ),
            OptionSpec(
                ("--format",),
                "text (the complete recognised text only), json (full result) or tsv (one row per word, with a low_conf column); --json implies json",
                group="OUTPUT",
                metavar="FORMAT",
                parser_kwargs={"choices": ("text", "json", "tsv"), "default": "text"},
            ),
            OptionSpec(
                ("--output",),
                "write the result to a file instead of stdout",
                group="OUTPUT",
                metavar="PATH",
            ),
            OptionSpec(
                ("--ignore-https-errors",),
                "accept invalid HTTPS certificates when opening the page (URL mode only)",
                group="TLS",
                parser_kwargs={"action": "store_true"},
            ),
        ),
        handler=_ocr,
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
