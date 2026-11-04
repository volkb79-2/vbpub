"""Signature-table tests for bot-protection detection, driven by saved pages and headers."""

from __future__ import annotations

import pytest
from blocked_support import LONG_FILLER, load_page

from pwmcp_client import blocked

CF_HEADERS = {"Server": "cloudflare", "CF-RAY": "a553626f5ca61da0-FRA"}

# (fixture, status, headers, provider, kind, an expected signal)
POSITIVE = [
    ("cloudflare-managed-challenge.html", 403, {**CF_HEADERS, "cf-mitigated": "challenge"},
     "Cloudflare", "managed challenge", "header:cf-mitigated=challenge"),
    ("cloudflare-managed-challenge.html", 200, {}, "Cloudflare", "managed challenge", "title:'Just a moment...'"),
    ("cloudflare-managed-challenge.html", None, None, "Cloudflare", "managed challenge",
     "text:'Performing security verification'"),
    ("cloudflare-block-page.html", 403, CF_HEADERS, "Cloudflare", "block page", "title:'Attention Required! | Cloudflare'"),
    ("cloudflare-legacy-checking.html", 503, {"server": "cloudflare"}, "Cloudflare", "managed challenge",
     "text:'Checking your browser before accessing'"),
    ("akamai-access-denied.html", 403, {"Server": "AkamaiGHost"}, "Akamai", "block page", "text:'errors.edgesuite.net'"),
    ("perimeterx-press-hold.html", 403, {}, "PerimeterX", "block page", "title:'Access to this page has been denied'"),
    ("datadome-captcha.html", 403, {"x-datadome": "protected"}, "DataDome", "captcha", "frame:captcha-delivery.com"),
    ("imperva-pardon.html", 200, {}, "Imperva", "interstitial", "title:'Pardon Our Interruption'"),
    ("generic-captcha-sorry.html", 429, {}, "Generic CAPTCHA", "bot check", "text:'unusual traffic from your computer network'"),
]


@pytest.mark.parametrize(("name", "status", "headers", "provider", "kind", "signal"), POSITIVE)
def test_saved_challenge_pages_are_recognised(name, status, headers, provider, kind, signal) -> None:
    page = load_page(name)
    verdict = blocked.detect(status=status, headers=headers, title=page.title, text=page.text, frames=page.frames)
    assert verdict is not None
    assert (verdict.provider, verdict.kind) == (provider, kind)
    assert signal in verdict.signals
    assert verdict.description == f"{provider} {kind}"


def test_status_is_listed_first_on_a_blocking_status() -> None:
    page = load_page("cloudflare-managed-challenge.html")
    verdict = blocked.detect(status=403, headers=CF_HEADERS, title=page.title, text=page.text)
    assert verdict.signals[0] == "status:403"
    assert "header:server=cloudflare" in verdict.signals and "header:cf-ray" in verdict.signals


def test_a_named_provider_outranks_the_generic_captcha_label() -> None:
    # "verify you are human" is generic; the Cloudflare title is definitive and named.
    verdict = blocked.detect(
        status=200, headers={}, title="Just a moment...", text="Please verify you are human", frames=[]
    )
    assert verdict.provider == "Cloudflare"
    only_generic = blocked.detect(status=200, headers={}, title="x", text="Please verify you are human")
    assert only_generic.provider == "Generic CAPTCHA"


@pytest.mark.parametrize(
    ("name", "status", "headers"),
    [
        ("login-with-recaptcha.html", 200, {"server": "cloudflare"}),  # a captcha frame on a real page
        ("article-about-challenges.html", 200, {"server": "cloudflare", "cf-ray": "x"}),  # quotes the wording, long
    ],
)
def test_real_pages_that_mention_or_embed_captchas_are_not_blocked(name, status, headers) -> None:
    page = load_page(name)
    assert blocked.detect(status=status, headers=headers, title=page.title, text=page.text, frames=page.frames) is None


def test_the_long_article_would_match_if_it_were_short() -> None:
    page = load_page("article-about-challenges.html")
    assert len(page.text) > blocked.MAX_CHALLENGE_TEXT
    short = page.text.replace(LONG_FILLER.strip(), "")
    assert len(short) <= blocked.MAX_CHALLENGE_TEXT
    assert blocked.detect(status=200, headers={}, title="t", text=short) is not None


@pytest.mark.parametrize("status", [None, 200, 404, 500])
def test_provider_headers_alone_need_a_blocking_status(status) -> None:
    assert blocked.detect(status=status, headers=CF_HEADERS, title="Home", text="Welcome") is None


@pytest.mark.parametrize("status", sorted(blocked.BLOCK_STATUSES))
def test_provider_headers_on_a_blocking_status_are_a_block(status) -> None:
    verdict = blocked.detect(status=status, headers=CF_HEADERS, title="Error", text="Forbidden")
    assert (verdict.provider, verdict.kind) == ("Cloudflare", "block")


def test_a_plain_403_without_any_provider_marker_is_not_called_bot_protection() -> None:
    assert blocked.detect(status=403, headers={"server": "nginx"}, title="403 Forbidden", text="Forbidden") is None
    assert blocked.detect(status=403, headers=None, title="", text="") is None


def test_header_value_patterns_do_not_match_other_servers() -> None:
    assert blocked.detect(status=403, headers={"server": "nginx", "x-cdn": "other"}, title="", text="x") is None
    verdict = blocked.detect(status=403, headers={"x-cdn": "Imperva", "x-iinfo": "1"}, title="", text="x")
    assert verdict.provider == "Imperva"


def test_a_cf_mitigated_header_with_another_value_is_not_definitive() -> None:
    assert blocked.detect(status=200, headers={"cf-mitigated": "other"}, title="", text="") is None


def test_a_captcha_frame_on_a_near_empty_page_is_a_block_even_on_200() -> None:
    verdict = blocked.detect(status=200, headers={}, title="", text=" ", frames=["https://hcaptcha.com/x"])
    assert verdict.provider == "Generic CAPTCHA" and verdict.signals == ("frame:hcaptcha.com",)


def test_signal_text_is_clipped_and_whitespace_normalised() -> None:
    title = "Just a moment " + "x" * 200
    verdict = blocked.detect(status=200, headers={}, title=title, text="")
    (signal,) = verdict.signals
    assert signal.endswith("...'") and len(signal) < 100
    assert blocked.detect(status=200, headers={}, title="Just a moment   ...", text="").signals == ("title:'Just a moment ...'",)


def test_every_signature_row_is_well_formed() -> None:
    import re

    for table in (blocked.TITLE_SIGNATURES, blocked.TEXT_SIGNATURES):
        for provider, kind, pattern in table:
            assert provider and kind
            re.compile(pattern)
    for provider, kind, needle in blocked.FRAME_SIGNATURES:
        assert provider and kind and needle == needle.lower()
    for provider, kind, name, pattern in blocked.HEADER_SIGNATURES:
        assert provider and kind and name == name.lower()
        assert pattern is None or re.compile(pattern)


def test_each_text_and_title_signature_matches_its_own_sample() -> None:
    samples = {
        "performing security verification": "Performing security verification",
        "checking if the site connection is secure": "Checking if the site connection is secure",
        "enable javascript and cookies to continue": "Enable JavaScript and cookies to continue",
        "px-captcha": '<div id="px-captcha">',
        "datadome": "protected by DataDome",
        "i'?m not a robot": "I'm not a robot",
        "are you (?:a )?robot": "Are you a robot?",
        "robot or human": "Robot or Human?",
    }
    import re

    for pattern in (row[2] for row in blocked.TEXT_SIGNATURES):
        probe = next((text for key, text in samples.items() if key in pattern), None)
        if probe is not None:
            assert re.search(pattern, probe, re.IGNORECASE), pattern
    assert blocked.detect(status=200, headers={}, title="Robot or human?", text="").provider == "Akamai"
