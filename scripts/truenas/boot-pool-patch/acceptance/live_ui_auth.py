#!/usr/bin/env python3
"""Authenticate to a TrueNAS UI through PWMCP without changing host state."""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import re
import stat
import sys
import tomllib

from playwright.sync_api import Error as PlaywrightError
from pwmcp_client import BrowserLease

from remote import RemoteError
from live_ui import connect_pwmcp


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RemoteError(f"{name} is required")
    return value


def run() -> None:
    base_url = required_env("TRUENAS_UI_URL").rstrip("/")
    ws_url = required_env("PWMCP_WS_URL")
    expected_playwright = required_env("PWMCP_PLAYWRIGHT_VERSION")
    secret_path = Path(required_env("TRUENAS_UI_SECRET_FILE"))
    if not secret_path.is_file():
        raise RemoteError("TrueNAS UI credential file does not exist")
    if stat.S_IMODE(secret_path.stat().st_mode) & 0o077:
        raise RemoteError("TrueNAS UI credential file must not be accessible by group or others")
    try:
        secrets = tomllib.loads(secret_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise RemoteError("cannot read TrueNAS UI credential TOML") from None
    admin = secrets.get("truenas_admin", {})
    username = admin.get("username")
    password = admin.get("password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        raise RemoteError("TrueNAS UI credential TOML is missing truenas_admin username/password")

    actual_playwright = importlib.metadata.version("playwright")
    if actual_playwright != expected_playwright:
        raise RemoteError(
            f"PWMCP requires Playwright {expected_playwright}; found {actual_playwright}"
        )
    if ws_url != "ws://pwmcp:3000/":
        raise RemoteError("PWMCP_WS_URL must use ws://pwmcp:3000/")

    print("[browser] acquiring a managed PWMCP browser lease", flush=True)
    try:
        with connect_pwmcp(ws_url) as browser_lease:
            context = browser_lease.browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            page.set_default_timeout(30_000)
            print("[navigation] opening TrueNAS sign-in page", flush=True)
            page.goto(base_url + "/", wait_until="domcontentloaded", timeout=120_000)
            username_field = page.get_by_role("textbox", name="Username")
            username_field.wait_for(state="visible", timeout=120_000)
            username_field.fill(username)
            page.get_by_role("textbox", name="Password").fill(password)
            page.get_by_role("button", name="Log In", exact=True).click()
            page.wait_for_url(re.compile(r"/ui/(?!signin)"), timeout=120_000)
            page.get_by_text("Datasets", exact=True).first.wait_for(
                state="visible", timeout=120_000,
            )
            title = page.title()
            snapshot = page.locator("body").aria_snapshot()
            useful = [
                line.strip() for line in snapshot.splitlines()
                if re.search(r"dashboard|datasets|system|services|credentials|account|settings|ssh", line, re.I)
            ]
            print(f"[login] authenticated dashboard loaded; title={title!r}", flush=True)
            print(f"[navigation] visible controls={useful[:80]!r}", flush=True)
            system_menu = page.get_by_role("button", name="System submenu", exact=True)
            if system_menu.count() == 1 and system_menu.is_visible():
                system_menu.click()
                page.wait_for_timeout(500)
                menu_snapshot = page.locator("body").aria_snapshot()
                menu_lines = [
                    line.strip() for line in menu_snapshot.splitlines()
                    if re.search(r"system|services|ssh|advanced|general|settings", line, re.I)
                ]
                print(f"[system-menu] visible controls={menu_lines[:80]!r}", flush=True)
                services_link = page.get_by_role("link", name="Services", exact=True)
                if services_link.count() == 1 and services_link.is_visible():
                    services_link.click()
                    page.wait_for_url(re.compile(r"/ui/system/services(?:$|\?)"), timeout=60_000)
                    page.wait_for_timeout(2_000)
                    services_snapshot = page.locator("body").aria_snapshot()
                    service_lines = services_snapshot.splitlines()
                    ssh_indexes = [
                        index for index, line in enumerate(service_lines)
                        if re.search(r"\bSSH\b", line, re.I)
                    ]
                    nearby = []
                    for index in ssh_indexes[:5]:
                        nearby.extend(service_lines[max(0, index - 4):index + 12])
                    print(
                        f"[services-page] url={page.url}; ssh-controls={nearby[:80]!r}",
                        flush=True,
                    )
                    ssh_rows = page.get_by_role("row", name=re.compile(r"^SSH\b"))
                    if ssh_rows.count() == 1:
                        edit = ssh_rows.get_by_role("button", name="Edit Service", exact=True)
                        if edit.count() == 1 and edit.is_visible():
                            edit.click()
                            page.wait_for_timeout(500)
                            dialog_snapshot = page.locator("body").aria_snapshot()
                            dialog_lines = [
                                line.strip() for line in dialog_snapshot.splitlines()
                                if re.search(
                                    r"password|root login|rootlogin|authentication|save|cancel|ssh service",
                                    line, re.I,
                                )
                            ]
                            print(f"[ssh-settings-dialog] controls={dialog_lines[:80]!r}", flush=True)

            page.goto(base_url + "/ui/dashboard", wait_until="domcontentloaded", timeout=120_000)
            page.get_by_text("Datasets", exact=True).first.wait_for(state="visible", timeout=60_000)
            credentials_menu = page.get_by_role("button", name="Credentials submenu", exact=True)
            credentials_menu.first.wait_for(state="visible", timeout=60_000)
            visible_credentials_menus = [
                credentials_menu.nth(index)
                for index in range(credentials_menu.count())
                if credentials_menu.nth(index).is_visible()
            ]
            if len(visible_credentials_menus) == 1:
                visible_credentials_menus[0].click()
                page.wait_for_timeout(500)
                credentials_snapshot = page.locator("body").aria_snapshot()
                credentials_lines = [
                    line.strip() for line in credentials_snapshot.splitlines()
                    if re.search(r"credentials|users|groups|root", line, re.I)
                ]
                print(f"[credentials-menu] visible controls={credentials_lines[:60]!r}", flush=True)
                local_users = page.get_by_role(
                    "link", name=re.compile(r"^(?:Local )?Users$", re.I)
                )
                if local_users.count() == 1 and local_users.is_visible():
                    local_users.click()
                    page.wait_for_timeout(2_000)
                    root_rows = page.get_by_role("row", name=re.compile(r"(^|\s)root(\s|$)", re.I))
                    print(
                        f"[local-users-page] url={page.url}; root-row-count={root_rows.count()}",
                        flush=True,
                    )
                    if root_rows.count() == 1:
                        edit_root = root_rows.get_by_role("button", name="Edit", exact=True)
                        if edit_root.count() == 1 and edit_root.is_visible():
                            edit_root.click()
                            page.wait_for_timeout(1_500)
                            root_snapshot = page.locator("body").aria_snapshot()
                            root_lines = [
                                line.strip() for line in root_snapshot.splitlines()
                                if re.search(r"root|ssh|public key|password|save|cancel", line, re.I)
                            ]
                            print(f"[root-user-form] controls={root_lines[:80]!r}", flush=True)
            page.goto(
                base_url + "/ui/credentials/users?username=root",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            page.get_by_text("Users", exact=True).first.wait_for(state="visible", timeout=60_000)
            page.wait_for_timeout(1_500)
            root_detail_snapshot = page.locator("body").aria_snapshot()
            root_detail_lines = [
                line.strip() for line in root_detail_snapshot.splitlines()
                if re.search(r"root|ssh|public key|built-in|system user|password|save", line, re.I)
            ]
            username_fields = page.get_by_role("textbox", name=re.compile("username", re.I))
            selected_root = any(
                username_fields.nth(index).input_value().strip() == "root"
                for index in range(username_fields.count())
            )
            ssh_key_fields = page.get_by_role(
                "textbox", name=re.compile(r"SSH.*(?:Public )?Key", re.I)
            )
            root_key_field_populated = any(
                bool(ssh_key_fields.nth(index).input_value().strip())
                for index in range(ssh_key_fields.count())
            )
            print(
                f"[root-user-direct-view] url={page.url}; selected_root={selected_root}; "
                f"ssh_key_field_count={ssh_key_fields.count()}; "
                f"ssh_key_field_populated={root_key_field_populated}; "
                f"controls={root_detail_lines[:80]!r}",
                flush=True,
            )
            root_heading = page.get_by_role(
                "heading", name=re.compile(r"^Details for root\b", re.I)
            )
            edit_root = root_heading.get_by_role("button", name="Edit", exact=True)
            if root_heading.count() == 1 and edit_root.count() == 1 and edit_root.is_visible():
                edit_root.click()
                page.wait_for_timeout(1_500)
                ssh_access = page.get_by_role("checkbox", name=re.compile(r"SSH Access", re.I))
                if ssh_access.count() == 1 and not ssh_access.is_checked():
                    ssh_access.check()
                    page.wait_for_timeout(500)
                edit_snapshot = page.locator("body").aria_snapshot()
                edit_lines = [
                    line.strip() for line in edit_snapshot.splitlines()
                    if re.search(r"root|ssh|public key|built-in|system user|password|save|cancel", line, re.I)
                ]
                edit_key_fields = page.get_by_role(
                    "textbox", name=re.compile(r"ssh.*key|key.*ssh", re.I)
                )
                edit_key_populated = any(
                    bool(edit_key_fields.nth(index).input_value().strip())
                    for index in range(edit_key_fields.count())
                )
                print(
                    f"[root-user-edit-form] ssh_key_field_count={edit_key_fields.count()}; "
                    f"ssh_access_checked={ssh_access.count() == 1 and ssh_access.is_checked()}; "
                    f"ssh_key_field_populated={edit_key_populated}; controls={edit_lines[:80]!r}",
                    flush=True,
                )
            context.close()
    except PlaywrightError as exc:
        summary = str(exc).splitlines()[0]
        raise RemoteError(f"PWMCP UI authentication check failed: {type(exc).__name__}: {summary}") from None


if __name__ == "__main__":
    try:
        run()
    except RemoteError as exc:
        print(f"live_ui_auth.py: {exc}", file=sys.stderr)
        raise SystemExit(1)
