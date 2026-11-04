"""Honest detection of bot-protection pages (interstitials, challenges, block pages).

``pwmcp inspect`` used to return a Cloudflare "Just a moment..." page like any other
content, so a caller had to guess that it was blocked. :func:`detect` recognises such a
page from the main document's status and headers, its title, its (short) body text and
the frames it embeds, using the small signature tables below.

This is DETECTION only. Nothing here solves, bypasses or hides from a bot protection, and
the tables are heuristics: a page that is not detected is "not recognised as blocked",
never "known to be the target". Extend the tables by adding a row and a fixture test.

Rules (see README "Bot protection"):

* A DEFINITIVE signal alone makes a verdict: ``cf-mitigated: challenge``, a challenge
  title, or challenge text on a short page (challenge pages are tiny; an article that
  merely quotes "Just a moment..." is long).
* A HINT needs corroboration: a provider header (``server: cloudflare``, ``cf-ray``) or a
  captcha frame is a block only on a blocking status (403/429/503), and a captcha frame
  is also a block on a near-empty page. A login form with a reCAPTCHA is not a block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BLOCK_STATUSES = frozenset({403, 429, 503})
# Challenge text only counts on a page this short (characters of body text).
MAX_CHALLENGE_TEXT = 6000
# A captcha frame alone is a block only when the page is essentially just that frame.
NEAR_EMPTY_TEXT = 600
MAX_SIGNAL_TEXT = 80

_FLAGS = re.IGNORECASE

# (provider, kind, regex over the page title) - definitive.
TITLE_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("Cloudflare", "managed challenge", r"^\s*just a moment"),
    ("Cloudflare", "block page", r"^\s*attention required!?\s*\|\s*cloudflare"),
    ("Imperva", "interstitial", r"^\s*pardon our interruption"),
    ("PerimeterX", "block page", r"^\s*access to this page has been denied"),
    ("Akamai", "bot check", r"^\s*robot or human\??\s*$"),
)

# (provider, kind, regex over the body text) - definitive on a short page.
TEXT_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("Cloudflare", "managed challenge", r"performing security verification"),
    ("Cloudflare", "managed challenge", r"checking if the site connection is secure"),
    ("Cloudflare", "managed challenge", r"checking your browser before accessing"),
    ("Cloudflare", "managed challenge", r"enable javascript and cookies to continue"),
    ("Cloudflare", "block page", r"ddos protection by cloudflare"),
    ("Cloudflare", "block page", r"sorry, you have been blocked"),
    ("Akamai", "block page", r"errors\.edgesuite\.net"),
    ("PerimeterX", "bot check", r"press\s*(?:&|and)\s*hold to confirm you are (?:a )?human"),
    ("Imperva", "interstitial", r"incapsula incident id"),
    ("Generic CAPTCHA", "bot check", r"unusual traffic from your computer network"),
)

# (provider, kind, regex over the body text) - WEAK: a vendor page, a login checkbox label or an
# article can say these words, so they count only with corroboration (a blocking status or a
# near-empty page), never on text alone.
WEAK_TEXT_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("PerimeterX", "bot check", r"px-captcha"),
    ("DataDome", "bot check", r"datadome"),
    ("Generic CAPTCHA", "bot check", r"verify you are (?:a )?human"),
    ("Generic CAPTCHA", "bot check", r"are you (?:a )?robot\b"),
    ("Generic CAPTCHA", "bot check", r"i'?m not a robot"),
)

# (provider, kind, substring of a frame URL) - hint.
FRAME_SIGNATURES: tuple[tuple[str, str, str], ...] = (
    ("Cloudflare", "challenge widget", "challenges.cloudflare.com"),
    ("DataDome", "captcha", "captcha-delivery.com"),
    ("PerimeterX", "captcha", "captcha.px-cdn.net"),
    ("Arkose Labs", "captcha", "arkoselabs.com"),
    ("Generic CAPTCHA", "captcha", "google.com/recaptcha"),
    ("Generic CAPTCHA", "captcha", "recaptcha.net"),
    ("Generic CAPTCHA", "captcha", "hcaptcha.com"),
)

# (provider, kind, header name, regex over the value or None for "present") - hint.
HEADER_SIGNATURES: tuple[tuple[str, str, str, str | None], ...] = (
    ("Cloudflare", "block", "server", r"^cloudflare"),
    ("Cloudflare", "block", "cf-ray", None),
    ("Akamai", "block", "server", r"^akamai"),
    ("DataDome", "block", "x-datadome", None),
    ("DataDome", "block", "server", r"^datadome"),
    ("Imperva", "block", "x-iinfo", None),
    ("Imperva", "block", "x-cdn", r"^(?:imperva|incapsula)"),
)

_GENERIC = "Generic CAPTCHA"


@dataclass(frozen=True)
class BlockVerdict:
    """A page recognised as bot protection."""

    provider: str
    kind: str
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def description(self) -> str:
        return f"{self.provider} {self.kind}"


@dataclass
class _Hit:
    provider: str
    kind: str
    signal: str
    definitive: bool
    frame: bool = False
    weak_text: bool = False


def _clip(value: str) -> str:
    value = " ".join(value.split())
    return value if len(value) <= MAX_SIGNAL_TEXT else value[: MAX_SIGNAL_TEXT - 3] + "..."


def _hits(headers: dict[str, str], title: str, text: str, frames: list[str]) -> list[_Hit]:
    hits: list[_Hit] = []
    if headers.get("cf-mitigated", "").strip().lower() == "challenge":
        hits.append(_Hit("Cloudflare", "managed challenge", "header:cf-mitigated=challenge", True))
    for provider, kind, pattern in TITLE_SIGNATURES:
        if re.search(pattern, title, _FLAGS):
            hits.append(_Hit(provider, kind, f"title:{_clip(title)!r}", True))
    if len(text) <= MAX_CHALLENGE_TEXT:
        for provider, kind, pattern in TEXT_SIGNATURES:
            found = re.search(pattern, text, _FLAGS)
            if found:
                hits.append(_Hit(provider, kind, f"text:{_clip(found.group(0))!r}", True))
        for provider, kind, pattern in WEAK_TEXT_SIGNATURES:
            found = re.search(pattern, text, _FLAGS)
            if found:
                hits.append(_Hit(provider, kind, f"text:{_clip(found.group(0))!r}", False, weak_text=True))
    for provider, kind, needle in FRAME_SIGNATURES:
        if any(needle in url.lower() for url in frames):
            hits.append(_Hit(provider, kind, f"frame:{needle}", False, frame=True))
    for provider, kind, name, pattern in HEADER_SIGNATURES:
        value = headers.get(name)
        if value is not None and (pattern is None or re.search(pattern, value.strip(), _FLAGS)):
            shown = name if pattern is None else f"{name}={_clip(value)}"
            hits.append(_Hit(provider, kind, f"header:{shown}", False))
    return hits


def detect(
    *,
    status: int | None,
    headers: dict[str, str] | None,
    title: str,
    text: str,
    frames: list[str] | None = None,
) -> BlockVerdict | None:
    """Return a verdict when the page is recognised as bot protection, else None.

    ``headers`` are the main document's response headers (names lower-cased by the
    caller or not; they are normalised here). ``frames`` are the URLs of the page's frames.
    """

    normalized = {name.lower(): value for name, value in (headers or {}).items()}
    blocking = status in BLOCK_STATUSES
    near_empty = len(text.strip()) <= NEAR_EMPTY_TEXT
    counted = [
        hit
        for hit in _hits(normalized, title, text, frames or [])
        if hit.definitive or blocking or ((hit.frame or hit.weak_text) and near_empty)
    ]
    if not counted:
        return None
    # Prefer a definitive hit, then a named provider over the generic CAPTCHA label.
    counted.sort(key=lambda hit: (not hit.definitive, hit.provider == _GENERIC))
    lead = counted[0]
    signals = [hit.signal for hit in counted]
    if blocking:
        signals.insert(0, f"status:{status}")
    return BlockVerdict(lead.provider, lead.kind, tuple(dict.fromkeys(signals)))
