#!/usr/bin/env python3
"""Send a plaintext email using SMTP (fail-fast on missing config)."""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.message import EmailMessage


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a plaintext email")
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--body-file", required=True, help="Path to plaintext body file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    smtp_host = require_env("SMTP_HOST")
    smtp_port = int(require_env("SMTP_PORT"))
    smtp_username = require_env("SMTP_USERNAME")
    smtp_password = require_env("SMTP_PASSWORD")
    smtp_from = require_env("SMTP_FROM")
    smtp_to = require_env("SMTP_TO")
    smtp_starttls = os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}

    body = open(args.body_file, "r", encoding="utf-8").read()

    message = EmailMessage()
    message["From"] = smtp_from
    message["To"] = smtp_to
    message["Subject"] = args.subject
    message.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if smtp_starttls:
            server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(message)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)
