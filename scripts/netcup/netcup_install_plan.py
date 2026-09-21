"""Shared, dependency-light installation customScript rendering.

Both the API installer and the manual customScript wizard use this module so
the bootstrap URL, repository source, notification validation, controller key,
and retention contract cannot drift into two separately maintained templates.
"""
from __future__ import annotations

import shlex
import urllib.parse
from typing import Mapping


def validate_notification(
    notify_backend: str,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    mattermost_webhook_url: str = "",
) -> str:
    backend = notify_backend.strip().lower()
    if backend not in {"telegram", "mattermost", "none"}:
        raise ValueError("notify_backend must be telegram, mattermost, or none")
    telegram_configured = bool(telegram_bot_token) or bool(telegram_chat_id)
    if bool(telegram_bot_token) != bool(telegram_chat_id):
        raise ValueError("telegram_bot_token and telegram_chat_id must be supplied together")
    if backend == "telegram" and mattermost_webhook_url:
        raise ValueError("mattermost_webhook_url requires notify_backend=mattermost")
    if backend == "mattermost":
        if telegram_configured:
            raise ValueError("Telegram credentials require notify_backend=telegram")
        parsed = urllib.parse.urlparse(mattermost_webhook_url)
        if (
            not mattermost_webhook_url
            or parsed.scheme != "https"
            or not parsed.netloc
            or any(char.isspace() for char in mattermost_webhook_url)
        ):
            raise ValueError("mattermost_webhook_url must be an https:// URL without whitespace")
    if backend == "none" and (telegram_configured or mattermost_webhook_url):
        raise ValueError("notify_backend=none cannot have notification credentials")
    return backend


def build_customscript(
    *,
    bootstrap: Mapping[str, str],
    auto_reboot_after_stage1: bool,
    never_reboot: bool,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    controller_pubkey: str = "",
    notify_backend: str = "telegram",
    mattermost_webhook_url: str = "",
    retain_controller_ssh_key: bool = False,
) -> str:
    """Build a fully expanded, safely shell-quoted v2 bootstrap command."""
    backend = validate_notification(
        notify_backend,
        telegram_bot_token,
        telegram_chat_id,
        mattermost_webhook_url,
    )
    required = ("remote_url", "repo_url", "repo_branch")
    missing = [key for key in required if not str(bootstrap.get(key, "")).strip()]
    if missing:
        raise ValueError(f"bootstrap source missing: {', '.join(missing)}")
    if any(char.isspace() for key in required for char in str(bootstrap[key])):
        raise ValueError("bootstrap source values must not contain whitespace")
    if not all(str(bootstrap[key]).startswith("https://") for key in ("remote_url", "repo_url")):
        raise ValueError("bootstrap remote_url and repo_url must be https:// URLs")
    env_parts = [
        f"REPO_URL={shlex.quote(str(bootstrap['repo_url']).rstrip('/'))}",
        f"REPO_BRANCH={shlex.quote(str(bootstrap['repo_branch']))}",
        f"AUTO_REBOOT_AFTER_STAGE1={'yes' if auto_reboot_after_stage1 else 'no'}",
        f"NEVER_REBOOT={'yes' if never_reboot else 'no'}",
        f"NOTIFY_BACKEND={backend}",
        f"RETAIN_CONTROLLER_SSH_KEY={'yes' if retain_controller_ssh_key else 'no'}",
    ]
    if backend == "telegram" and telegram_bot_token:
        env_parts += [
            f"TELEGRAM_BOT_TOKEN={shlex.quote(telegram_bot_token)}",
            f"TELEGRAM_CHAT_ID={shlex.quote(telegram_chat_id)}",
        ]
    if backend == "mattermost":
        env_parts.append(f"MATTERMOST_WEBHOOK_URL={shlex.quote(mattermost_webhook_url)}")
    if controller_pubkey:
        env_parts.append(f"CONTROLLER_SSH_PUBKEY={shlex.quote(controller_pubkey)}")
    return f"curl -fsSL {shlex.quote(str(bootstrap['remote_url']))} | " + " ".join(env_parts) + " python3 -"


def placeholder_customscript() -> str:
    """Return the portable recipe form consumed by the API installer."""
    return (
        "curl -fsSL {{BOOTSTRAP_URL}} | "
        "REPO_URL={{BOOTSTRAP_REPO_URL}} REPO_BRANCH={{BOOTSTRAP_REPO_BRANCH}} "
        "AUTO_REBOOT_AFTER_STAGE1=yes NEVER_REBOOT=no "
        "NOTIFY_BACKEND='{{NOTIFY_BACKEND}}' "
        "TELEGRAM_BOT_TOKEN='{{TELEGRAM_BOT_TOKEN}}' TELEGRAM_CHAT_ID='{{TELEGRAM_CHAT_ID}}' "
        "MATTERMOST_WEBHOOK_URL='{{MATTERMOST_WEBHOOK_URL}}' "
        "CONTROLLER_SSH_PUBKEY='{{CONTROLLER_SSH_PUBKEY}}' "
        "RETAIN_CONTROLLER_SSH_KEY='{{RETAIN_CONTROLLER_SSH_KEY}}' "
        "python3 -"
    )
