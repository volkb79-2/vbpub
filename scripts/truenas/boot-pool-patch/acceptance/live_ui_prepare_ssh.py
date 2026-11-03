#!/usr/bin/env python3
"""Enable key-only SSH from the TrueNAS UI for a disposable external target."""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import tomllib

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect
from pwmcp_client import BrowserLease

from live_ui import connect_pwmcp
from remote import RemoteError


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RemoteError(f"{name} is required")
    return value


def read_admin_credentials() -> tuple[str, str]:
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
    return username, password


def read_root_public_key() -> str:
    key_path = Path(required_env("TRUENAS_ROOT_SSH_PUBLIC_KEY_FILE"))
    try:
        lines = [line.strip() for line in key_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        raise RemoteError("cannot read the configured root SSH public-key file") from None
    if len(lines) != 1 or not lines[0].startswith("ssh-ed25519 "):
        raise RemoteError("root SSH public-key file must contain exactly one Ed25519 public key")
    try:
        validated = subprocess.run(
            ["ssh-keygen", "-lf", str(key_path)], text=True, capture_output=True,
            check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RemoteError("could not validate the configured root SSH public key") from None
    if validated.returncode != 0 or "(ED25519)" not in validated.stdout:
        raise RemoteError("root SSH public-key file is not a valid Ed25519 key")
    return lines[0]


def authenticate(page, base_url: str, username: str, password: str) -> None:
    page.set_default_timeout(30_000)
    page.goto(base_url + "/", wait_until="domcontentloaded", timeout=120_000)
    page.get_by_role("textbox", name="Username").fill(username)
    page.get_by_role("textbox", name="Password").fill(password)
    page.get_by_role("button", name="Log In", exact=True).click()
    page.wait_for_url(re.compile(r"/ui/(?!signin)"), timeout=120_000)
    page.get_by_text("Datasets", exact=True).first.wait_for(state="visible", timeout=120_000)


def root_user_heading(page):
    heading = page.get_by_role("heading", name=re.compile(r"^Details for root\b", re.I))
    heading.wait_for(state="visible", timeout=60_000)
    edit = heading.get_by_role("button", name="Edit", exact=True)
    if heading.count() != 1 or edit.count() != 1:
        raise RemoteError("the built-in root user does not expose one editable details panel")
    return edit


def open_root_user_edit_form(page, base_url: str):
    page.goto(base_url + "/ui/credentials/users?username=root",
              wait_until="domcontentloaded", timeout=120_000)
    page.get_by_text("Users", exact=True).first.wait_for(state="visible", timeout=60_000)
    edit = root_user_heading(page)
    edit.click()
    ssh_access = page.get_by_role(
        "checkbox", name=re.compile(r"^ssh SSH Access$", re.I)
    )
    ssh_access.wait_for(state="visible", timeout=30_000)
    # User data arrives after the built-in user form renders.
    page.wait_for_timeout(2_000)
    return ssh_access


def configure_root_ssh_access(page, base_url: str, public_key: str) -> None:
    ssh_access = open_root_user_edit_form(page, base_url)
    changed = False
    if not ssh_access.is_checked():
        ssh_access.check()
        changed = True
        print("[root-ssh] enabled SSH access for the built-in root account", flush=True)

    key_field = page.get_by_role(
        "textbox", name="SSH Public Keys (Authorized Keys)", exact=True
    )
    key_field.wait_for(state="visible", timeout=30_000)
    existing_keys = [line.strip() for line in key_field.input_value().splitlines() if line.strip()]
    if public_key not in existing_keys:
        key_field.fill("\n".join([*existing_keys, public_key]))
        changed = True
        print("[root-ssh] added the configured Ed25519 key; retained any existing authorized keys", flush=True)

    disable_password = page.get_by_role("checkbox", name="Disable Password", exact=True)
    if disable_password.count() != 1:
        raise RemoteError("the root user form does not expose one Disable Password setting")
    if not disable_password.is_checked():
        disable_password.check()
        changed = True

    if changed:
        save = page.get_by_role("button", name="Save", exact=True)
        if save.count() != 1 or not save.is_enabled():
            raise RemoteError("root SSH settings changed but the form did not enable one Save button")
        save.click()
        page.wait_for_timeout(2_000)
        print("[root-ssh] saved root SSH access with password login disabled", flush=True)
    else:
        print("[root-ssh] configured key and password-disabled state were already present", flush=True)

    verify_access = open_root_user_edit_form(page, base_url)
    verify_key = page.get_by_role(
        "textbox", name="SSH Public Keys (Authorized Keys)", exact=True
    )
    verify_disable_password = page.get_by_role("checkbox", name="Disable Password", exact=True)
    if (
        not verify_access.is_checked()
        or public_key not in [line.strip() for line in verify_key.input_value().splitlines()]
        or not verify_disable_password.is_checked()
    ):
        raise RemoteError("root SSH key/access settings did not persist after saving")
    print("[root-ssh] verified the configured key is authorized and root password is disabled", flush=True)


def ssh_row(page):
    rows = page.get_by_role("row", name=re.compile(r"^SSH\b"))
    rows.first.wait_for(state="visible", timeout=60_000)
    if rows.count() != 1:
        raise RemoteError(f"expected one SSH service row, found {rows.count()}")
    return rows


def disable_password_authentication(page) -> None:
    row = ssh_row(page)
    edit = row.get_by_role("button", name="Edit Service", exact=True)
    if edit.count() != 1:
        raise RemoteError("the SSH service row does not have one Edit Service button")
    edit.click()
    password_auth = page.get_by_role("checkbox", name="Allow Password Authentication", exact=True)
    password_auth.wait_for(state="visible", timeout=30_000)
    # The form renders before its middleware-backed values arrive. Read the
    # checkbox only after the initial UI data fetch has settled.
    page.wait_for_timeout(2_000)
    if password_auth.is_checked():
        password_auth.uncheck()
        save = page.get_by_role("button", name="Save", exact=True)
        if save.count() != 1:
            raise RemoteError("SSH settings did not expose one Save button")
        save.click()
        password_auth.wait_for(state="detached", timeout=30_000)
        print("[ssh-settings] disabled password authentication in the SSH service", flush=True)
    else:
        print("[ssh-settings] password authentication was already disabled", flush=True)

    page.goto(required_env("TRUENAS_UI_URL").rstrip("/") + "/ui/system/services",
              wait_until="domcontentloaded", timeout=120_000)
    row = ssh_row(page)
    edit = row.get_by_role("button", name="Edit Service", exact=True)
    edit.click()
    password_auth = page.get_by_role("checkbox", name="Allow Password Authentication", exact=True)
    password_auth.wait_for(state="visible", timeout=30_000)
    page.wait_for_timeout(2_000)
    if password_auth.is_checked():
        raise RemoteError("SSH password authentication is still enabled after saving the setting")
    print("[ssh-settings] verified password authentication is off", flush=True)


def enable_and_start_ssh(page) -> None:
    # Navigate away from the read-only settings dialog after its checked state.
    page.goto(required_env("TRUENAS_UI_URL").rstrip("/") + "/ui/system/services",
              wait_until="domcontentloaded", timeout=120_000)
    row = ssh_row(page)
    autostart = row.get_by_role("switch")
    if autostart.count() != 1:
        raise RemoteError("the SSH service row does not have one automatic-start switch")
    if not autostart.is_checked():
        autostart.click()
        expect(autostart).to_be_checked(timeout=30_000)
        print("[ssh-service] enabled automatic start for SSH", flush=True)
    else:
        print("[ssh-service] automatic start for SSH was already enabled", flush=True)

    row = ssh_row(page)
    status = row.inner_text()
    if re.search(r"\bRunning\b", status, re.I) is None:
        start = row.get_by_role("button", name="Start Service", exact=True)
        if start.count() != 1:
            raise RemoteError("the stopped SSH service row does not have one Start Service button")
        start.click()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            row = ssh_row(page)
            status = row.inner_text()
            if re.search(r"\bRunning\b", status, re.I):
                break
            page.wait_for_timeout(1_000)
        else:
            raise RemoteError(f"SSH service did not reach Running status; last row: {status!r}")
        print("[ssh-service] started SSH and confirmed Running status", flush=True)
    else:
        print("[ssh-service] SSH service is Running", flush=True)


def run() -> None:
    if os.environ.get("TRUENAS_TEST_ALLOW_MUTATIONS") != "1":
        raise RemoteError("set TRUENAS_TEST_ALLOW_MUTATIONS=1 only for the disposable external target")
    base_url = required_env("TRUENAS_UI_URL").rstrip("/")
    ws_url = required_env("PWMCP_WS_URL")
    expected_playwright = required_env("PWMCP_PLAYWRIGHT_VERSION")
    username, password = read_admin_credentials()
    public_key = read_root_public_key()
    actual_playwright = importlib.metadata.version("playwright")
    if actual_playwright != expected_playwright:
        raise RemoteError(
            f"PWMCP requires Playwright {expected_playwright}; found {actual_playwright}"
        )
    if ws_url != "ws://pwmcp:3000/":
        raise RemoteError("PWMCP_WS_URL must use ws://pwmcp:3000/")

    print("[browser] acquiring PWMCP browser lease for guarded SSH setup", flush=True)
    try:
        with connect_pwmcp(ws_url) as browser_lease:
            context = browser_lease.browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            authenticate(page, base_url, username, password)
            print("[login] authenticated to the TrueNAS UI", flush=True)
            configure_root_ssh_access(page, base_url, public_key)
            page.goto(base_url + "/ui/system/services", wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_url(re.compile(r"/ui/system/services(?:$|\?)"), timeout=60_000)
            ssh_row(page).wait_for(state="visible", timeout=60_000)
            disable_password_authentication(page)
            enable_and_start_ssh(page)
            context.close()
    except PlaywrightError as exc:
        summary = str(exc).splitlines()[0]
        raise RemoteError(
            f"PWMCP SSH setup failed: {type(exc).__name__}: {summary}"
        ) from None


if __name__ == "__main__":
    try:
        run()
    except RemoteError as exc:
        print(f"live_ui_prepare_ssh.py: {exc}", file=sys.stderr)
        raise SystemExit(1)
