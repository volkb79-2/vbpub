"""User-agent presets and resolution: the ONE source of truth (PWMCP-09).

This stdlib-only module is imported by the client (``pwmcp inspect --user-agent``)
and is also copied verbatim into the server image (Dockerfile ->
``/opt/pwmcp/pwmcp_user_agent.py``), where ``entrypoint.sh`` runs it as a script to
resolve and validate ``PWMCP_USER_AGENT`` once at container start. There is no second
table anywhere: the image and the client carry the same file.

Scope (deliberately narrow): a user-agent string and its matching client-hint brand
metadata (``Sec-CH-UA``, ``navigator.userAgentData``). Nothing here touches
``navigator.webdriver``, plugins, canvas, or any other fingerprint surface.

Off by default: an unset, empty, or ``default`` value means "no override".
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

PRESETS: dict[str, str] = {
    "edge-windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
    ),
    "chrome-windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
    ),
    "firefox-windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0"
    ),
}

DEFAULT_NAME = "default"
SOURCE_DEFAULT = "default"
SOURCE_CUSTOM = "custom"
MAX_CUSTOM_LENGTH = 512

# Printable ASCII only (space through tilde): rejects CR/LF, tabs, other controls, non-ASCII.
_PRINTABLE_ASCII = re.compile(r"[\x20-\x7e]+")


class UserAgentError(ValueError):
    """The configured user-agent value is invalid."""


@dataclass(frozen=True)
class ResolvedUserAgent:
    """A resolved setting. ``user_agent`` is None when no override is active."""

    user_agent: str | None
    source: str  # "default" | "preset:<name>" | "custom"

    @property
    def active(self) -> bool:
        return self.user_agent is not None

    def as_fields(self) -> dict[str, str | None]:
        """The transparency fields every page-content result carries."""

        return {"user_agent": self.user_agent, "user_agent_source": self.source}


def resolve(value: str | None) -> ResolvedUserAgent:
    """Resolve a preset name, ``default``/unset, or a custom raw string."""

    if value is None or value == "" or value == DEFAULT_NAME:
        return ResolvedUserAgent(None, SOURCE_DEFAULT)
    if value in PRESETS:
        return ResolvedUserAgent(PRESETS[value], f"preset:{value}")
    if len(value) > MAX_CUSTOM_LENGTH:
        raise UserAgentError(
            f"custom user agent is {len(value)} characters; the maximum is {MAX_CUSTOM_LENGTH}"
        )
    if not _PRINTABLE_ASCII.fullmatch(value):
        raise UserAgentError(
            "custom user agent must be printable ASCII (no CR/LF, tabs, or non-ASCII characters)"
        )
    if value != value.strip():
        raise UserAgentError("custom user agent must not start or end with whitespace")
    # A mistyped preset ("edge-window", "Edge-Windows", "chrome") must not silently become
    # a custom UA. A real UA string always contains a space, "/" or "(".
    if value.casefold() in {name.casefold() for name in (*PRESETS, DEFAULT_NAME)} or not (
        {" ", "/", "("} & set(value)
    ):
        raise UserAgentError(
            f"{value!r} is neither a preset nor a plausible user-agent string; valid presets: "
            f"{', '.join(PRESETS)}, or {DEFAULT_NAME!r}; a custom string must contain a space, "
            "'/' or '('"
        )
    return ResolvedUserAgent(value, SOURCE_CUSTOM)


def _platform(ua: str) -> tuple[str, str]:
    if "Windows NT" in ua:
        return "Windows", "10.0.0"
    if "Macintosh" in ua:
        return "macOS", "14.0.0"
    if "Android" in ua:
        return "Android", "14.0.0"
    if "Linux" in ua or "X11" in ua:
        return "Linux", ""
    return "", ""


def client_hints(ua: str) -> dict[str, object] | None:
    """Client-hint metadata matching ``ua``, in CDP ``Emulation.UserAgentMetadata`` shape.

    * Chromium-family UAs (``Chrome/x.y.z.w``, optionally ``Edg/``): GREASE brand, Chromium,
      and Google Chrome or Microsoft Edge, with the UA's own versions.
    * Firefox UAs: Firefox does not send ``Sec-CH-UA``, so the brand list is empty. Whether
      Chromium then omits the header entirely is unverified (see README).
    * Anything else (custom strings without a recognisable browser token): ``None`` -- the
      UA string alone is overridden and the caller must not claim client-hint consistency.
    """

    platform, platform_version = _platform(ua)
    base: dict[str, object] = {
        "platform": platform,
        "platformVersion": platform_version,
        "architecture": "x86" if platform in {"Windows", "Linux", "macOS"} else "",
        "model": "",
        "mobile": "Mobile" in ua,
        "bitness": "64" if ("x64" in ua or "x86_64" in ua or "Win64" in ua) else "",
        "wow64": False,
    }
    chrome = re.search(r"Chrome/(\d+)\.(\d+\.\d+\.\d+)", ua)
    if chrome:
        major = chrome.group(1)
        full = f"{major}.{chrome.group(2)}"
        edge = re.search(r"Edg/(\d+)\.(\d+\.\d+\.\d+)", ua)
        if edge:
            vendor, vendor_major = "Microsoft Edge", edge.group(1)
            vendor_full = f"{vendor_major}.{edge.group(2)}"
        else:
            vendor, vendor_major, vendor_full = "Google Chrome", major, full
        base["brands"] = [
            {"brand": "Not=A?Brand", "version": "24"},
            {"brand": "Chromium", "version": major},
            {"brand": vendor, "version": vendor_major},
        ]
        base["fullVersionList"] = [
            {"brand": "Not=A?Brand", "version": "24.0.0.0"},
            {"brand": "Chromium", "version": full},
            {"brand": vendor, "version": vendor_full},
        ]
        return base
    if re.search(r"Firefox/\d+", ua) and "Chrome/" not in ua:
        base["brands"] = []
        base["fullVersionList"] = []
        return base
    return None


def cdp_override_params(ua: str) -> dict[str, object]:
    """Params for CDP ``Emulation.setUserAgentOverride`` (UA plus matching metadata)."""

    params: dict[str, object] = {"userAgent": ua}
    metadata = client_hints(ua)
    if metadata is not None:
        params["userAgentMetadata"] = metadata
    return params


def _main(argv: list[str]) -> int:
    """Entrypoint helper: ``python3 pwmcp_user_agent.py <ua|metadata|source> [VALUE]``.

    Prints one field of the resolved setting (empty output for an inactive override's
    ``ua``/``metadata``). Exit 1 with a message on stderr when the value is invalid.
    """

    if len(argv) not in {1, 2} or argv[0] not in {"ua", "metadata", "source"}:
        print("usage: pwmcp_user_agent.py ua|metadata|source [VALUE]", file=sys.stderr)
        return 2
    try:
        resolved = resolve(argv[1] if len(argv) == 2 else None)
    except UserAgentError as exc:
        print(f"FATAL: invalid PWMCP_USER_AGENT: {exc}", file=sys.stderr)
        return 1
    field = argv[0]
    if field == "source":
        print(resolved.source)
    elif resolved.user_agent is not None:
        if field == "ua":
            print(resolved.user_agent)
        else:
            metadata = client_hints(resolved.user_agent)
            if metadata is not None:
                print(json.dumps(metadata, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
