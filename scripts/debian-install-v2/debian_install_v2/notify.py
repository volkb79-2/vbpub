"""Notification backend selection and the Mattermost incoming-webhook sender.

Stdlib only (the target host has nothing else). The webhook URL is a SECRET:
it is never logged, never put in an exception message, and anything that may
quote it goes through ``redact_text`` first. Only the host part may appear.

The Mattermost wire contract is ``nyxloom/mattermost/CONSUMER.md``: a plain
``POST {"text": markdown}`` with no ``channel`` override; a failed POST never
fails the install.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

_LOG = logging.getLogger("debian_install_v2.notify")

BACKENDS = ("mattermost", "telegram", "none")
POST_ATTEMPTS = 2
POST_TIMEOUT_S = 10
POST_BACKOFF_S = 1.5
#: Mattermost's own post limit is 16383 characters; stay far below it.
MESSAGE_LIMIT = 3500
ERROR_EXCERPT_LIMIT = 800

REDACTED = "***REDACTED***"
# A Mattermost incoming-webhook URL: any scheme/host, path /hooks/<id>.
_HOOK_URL_RE = re.compile(r"https?://[^\s/'\"`]+/hooks/[A-Za-z0-9_-]+")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_STATUS_MARK = {"ok": "✅", "fail": "❌", "run": "⏳", "warn": "⚠️"}


class NotifyConfigError(ValueError):
    pass


def webhook_host(url: str) -> str:
    """The only part of a webhook URL that may appear in output."""
    try:
        return urllib.parse.urlsplit(url).hostname or "<invalid-url>"
    except ValueError:
        return "<invalid-url>"


def validate_webhook_url(url: str) -> None:
    """Raise NotifyConfigError (never quoting the URL) when it is unusable."""
    if any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in url):
        raise NotifyConfigError(
            "mattermost_webhook_url must not contain whitespace or control characters"
        )
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise NotifyConfigError("mattermost_webhook_url is not a valid URL") from None
    if parts.scheme not in {"https", "http"} or not host:
        raise NotifyConfigError("mattermost_webhook_url must be an http(s) URL with a host")


def effective_backend(
    notify_backend: str, *, has_telegram: bool, has_mattermost: bool
) -> str:
    """Explicit choice wins; otherwise infer; both credentials -> refuse."""
    if notify_backend:
        if notify_backend not in BACKENDS:
            raise NotifyConfigError(
                f"notify_backend must be one of {', '.join(BACKENDS)} (or unset)"
            )
        return notify_backend
    if has_telegram and has_mattermost:
        raise NotifyConfigError(
            "both Telegram and Mattermost credentials are present: set notify_backend "
            "explicitly to mattermost, telegram, or none"
        )
    if has_mattermost:
        return "mattermost"
    if has_telegram:
        return "telegram"
    return "none"


def redact_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Mask the webhook URL (and any other known secret) inside free text."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    text = _HOOK_URL_RE.sub(REDACTED, text)
    return _TELEGRAM_TOKEN_RE.sub(REDACTED, text)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_mattermost_message(
    *,
    host_label: str,
    server_name: str,
    run_id: str,
    stage: str,
    event: str,
    status: str = "run",
    excerpt: str = "",
    secrets: tuple[str, ...] = (),
) -> str:
    """THE one place the Mattermost message layout lives (target-host side).

    ``<mark> **label** (`server`) | run `abcd1234` | stage | event`` followed by
    an optional short fenced excerpt (already-bounded, secret-redacted).
    ``status`` is one of ok / fail / run / warn.
    """
    mark = _STATUS_MARK.get(status, _STATUS_MARK["run"])
    who = f"**{host_label}** (`{server_name}`)" if host_label else f"**{server_name}**"
    head = f"{mark} {who} | run `{(run_id or '--------')[:8]}` | {stage} | {event}"
    text = redact_text(head, secrets)
    if excerpt:
        body = redact_text(excerpt, secrets).replace("```", "'''")
        text += "\n```\n" + _truncate(body.strip(), ERROR_EXCERPT_LIMIT) + "\n```"
    return _truncate(text, MESSAGE_LIMIT)


def post_webhook(
    url: str,
    text: str,
    *,
    attempts: int = POST_ATTEMPTS,
    timeout: float = POST_TIMEOUT_S,
    backoff: float = POST_BACKOFF_S,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """POST ``{"text": text}``; bounded retries; NEVER raises, never logs the URL."""
    opener = opener or urllib.request.urlopen
    data = json.dumps({"text": text}).encode("utf-8")
    host = webhook_host(url)
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}, method="POST"
            )
            response = opener(request, timeout=timeout)
            try:
                status = getattr(response, "status", 200) or 200
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
            if 200 <= int(status) < 300:
                return True
            reason = f"HTTP {status}"
        except urllib.error.HTTPError as exc:
            reason = f"HTTP {exc.code}"
        except Exception as exc:  # status channel: nothing here may fail an install
            reason = type(exc).__name__
        _LOG.warning(
            "Mattermost notification to %s failed (attempt %d/%d): %s",
            host, attempt, attempts, reason,
        )
        if attempt < attempts:
            sleep(backoff)
    return False
