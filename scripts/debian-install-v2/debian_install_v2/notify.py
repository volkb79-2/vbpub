"""Notification backend selection and the Mattermost incoming-webhook sender.

Stdlib only (the target host has nothing else). The webhook URL is a SECRET:
it is never logged, never put in an exception message, and anything that may
quote it goes through ``redact_text`` first. Only the host part may appear.

The Mattermost wire contract is ``mattermost/CONSUMER.md``: a plain
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
# Everything after the host is the credential (a subpath deployment puts
# /hooks/<id> below a prefix), so mask the whole run of URL characters.
# Keep this pattern identical in netcup_scp_client.py and install-host.py.
_HOOK_URL_RE = re.compile(r"https?://[^\s'\"`]*?/hooks/[^\s'\"`]+")
_CONTROL_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]+")
_MD_METACHARS = "\\*_[]()#|`~<>"
LABEL_MAX = 64
SERVER_MAX = 64
RUN_ID_MAX = 16
STAGE_MAX = 32
EVENT_MAX = 300
_LABEL_OK_RE = re.compile(r"^[A-Za-z0-9 ._:/-]{1,64}$")
#: Consecutive failed messages (per process) after which posting stops.
BREAKER_LIMIT = 3
_breaker = {"failures": 0, "open": False}


def reset_breaker() -> None:
    _breaker["failures"] = 0
    _breaker["open"] = False
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")
_STATUS_MARK = {"ok": "✅", "fail": "❌", "run": "⏳", "warn": "⚠️"}


class NotifyConfigError(ValueError):
    pass


def validate_host_label(label: str) -> None:
    """Operator config: rejected (never silently sanitized) when unsafe."""
    if label and not _LABEL_OK_RE.fullmatch(label):
        raise NotifyConfigError(
            "notify_host_label must be 1-64 characters from [A-Za-z0-9 ._:/-]"
        )


def sanitize_field(value: object, cap: int) -> str:
    """Make one interpolated message field inert in Mattermost markdown.

    Control characters (CR/LF/tab...) collapse to one space, the field is
    capped at ``cap``, markdown metacharacters are backslash-escaped, and a
    zero-width space after every ``@`` defuses @channel/@all/@here/@user.
    """
    text = _CONTROL_RE.sub(" ", str(value)).strip()
    text = _truncate(text, cap)
    for char in _MD_METACHARS:
        text = text.replace(char, "\\" + char)
    return text.replace("@", "@\u200b")


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
    if parts.scheme != "https" or not host:
        raise NotifyConfigError("mattermost_webhook_url must be an https:// URL with a host")


def effective_backend(
    notify_backend: str, *, has_telegram: bool, has_mattermost: bool
) -> str:
    """Explicit choice must match the credentials; unset infers; both -> refuse."""
    if notify_backend:
        if notify_backend not in BACKENDS:
            raise NotifyConfigError(
                f"notify_backend must be one of {', '.join(BACKENDS)} (or unset)"
            )
        if notify_backend == "telegram" and has_mattermost:
            raise NotifyConfigError("mattermost_webhook_url requires notify_backend=mattermost")
        if notify_backend == "mattermost" and has_telegram:
            raise NotifyConfigError("Telegram credentials require notify_backend=telegram")
        if notify_backend == "none" and (has_telegram or has_mattermost):
            raise NotifyConfigError("notify_backend=none cannot have notification credentials")
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
    server = sanitize_field(server_name, SERVER_MAX) or "unknown-host"
    label = sanitize_field(host_label, LABEL_MAX)
    who = f"**{label}** (`{server}`)" if label else f"**{server}**"
    run = sanitize_field((run_id or "--------")[:8], RUN_ID_MAX)
    head = (
        f"{mark} {who} | run `{run}` | {sanitize_field(stage, STAGE_MAX)} | "
        f"{sanitize_field(event, EVENT_MAX)}"
    )
    text = redact_text(head, secrets)
    if excerpt:
        # Each part is bounded BEFORE assembly so the closing fence is always
        # present; head <= ~600 and excerpt <= 800 keep the total far below
        # MESSAGE_LIMIT.
        body = redact_text(excerpt, secrets).replace("```", "'''")
        text += "\n```\n" + _truncate(body.strip(), ERROR_EXCERPT_LIMIT) + "\n```"
    return text


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
    if _breaker["open"]:
        return False
    opener = opener or urllib.request.urlopen
    data = json.dumps({"text": text}).encode("utf-8")
    host = webhook_host(url)
    ok = False
    for attempt in range(1, attempts + 1):
        retryable = True
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
                ok = True
                break
            reason = f"HTTP {status}"
            retryable = int(status) >= 500 or int(status) == 429
        except urllib.error.HTTPError as exc:
            reason = f"HTTP {exc.code}"
            retryable = exc.code >= 500 or exc.code == 429
        except Exception as exc:  # status channel: nothing here may fail an install
            reason = type(exc).__name__
        if _breaker["open"]:
            break
        _LOG.warning(
            "Mattermost notification to %s failed (attempt %d/%d): %s",
            host, attempt, attempts, reason,
        )
        if not retryable:
            break
        if attempt < attempts:
            sleep(backoff)
    if ok:
        _breaker["failures"] = 0
        return True
    _breaker["failures"] += 1
    if _breaker["failures"] >= BREAKER_LIMIT:
        _breaker["open"] = True
        _LOG.warning(
            "mattermost notifications disabled for this run after %d failures", BREAKER_LIMIT
        )
    return False
