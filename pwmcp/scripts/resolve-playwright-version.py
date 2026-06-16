#!/usr/bin/env python3
"""Resolve the latest Playwright version from npm and update pwmcp config files.

Steps:
  1. Fetch latest playwright version from npm registry.
  2. Compute the next r<N> counter by scanning git tags (pwmcp-v<pw_ver>-r*).
  3. Update ciu.defaults.toml.j2 and ciu.toml.j2 (playwright_version + image.tag).
  4. Update docker-bake.hcl defaults (PLAYWRIGHT_VERSION + PWMCP_VERSION).
  5. Write .release-vars for downstream scripts (build-bundle.py, publish-bundle.py).

Outputs:
  pwmcp/.release-vars  — KEY=VALUE env file consumed by build-bundle.py / publish-bundle.py
  ciu.defaults.toml.j2 — playwright_version and image.tag updated in-place
  ciu.toml.j2          — same (kept in sync with defaults)
  docker-bake.hcl      — PLAYWRIGHT_VERSION and PWMCP_VERSION defaults updated in-place
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NPM_PLAYWRIGHT_URL = "https://registry.npmjs.org/playwright/latest"
TIMEOUT_SECONDS = 20
RETRIES = 3

PWMCP_DIR = Path(__file__).resolve().parent.parent
DEFAULTS_FILE = PWMCP_DIR / "ciu.defaults.toml.j2"
TOML_OVERRIDE_FILE = PWMCP_DIR / "ciu.toml.j2"
BAKE_FILE = PWMCP_DIR / "docker-bake.hcl"
RELEASE_VARS_FILE = PWMCP_DIR / ".release-vars"


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def fetch_npm_latest_version() -> str:
    last_exc: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                NPM_PLAYWRIGHT_URL,
                headers={"User-Agent": "pwmcp/resolve-playwright-version"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            version = str(payload.get("version") or "").strip()
            if not version:
                fail("npm response missing 'version' field")
            return version
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in {429, 500, 502, 503, 504} and attempt < RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                log(f"HTTP {exc.code}; retrying in {delay}s ({attempt}/{RETRIES})")
                time.sleep(delay)
                continue
            fail(f"npm fetch failed: HTTP {exc.code}")
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                log(f"Network error: {exc.reason}; retrying in {delay}s ({attempt}/{RETRIES})")
                time.sleep(delay)
                continue
            fail(f"npm fetch failed: {exc.reason}")
    fail(f"npm fetch failed after {RETRIES} attempts: {last_exc}")
    return ""  # unreachable


def list_git_tags(pattern: str) -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--list", pattern],
        capture_output=True, text=True, check=False,
        cwd=str(PWMCP_DIR),
    )
    if result.returncode != 0:
        return []
    return [t.strip() for t in result.stdout.splitlines() if t.strip()]


def compute_release_number(pw_version: str) -> int:
    """Scan git tags for pwmcp-v<pw_version>-r* and return next N."""
    tags = list_git_tags(f"pwmcp-v{pw_version}-r*")
    if not tags:
        return 1
    max_n = 0
    pattern = re.compile(rf"^pwmcp-v{re.escape(pw_version)}-r(\d+)$")
    for tag in tags:
        m = pattern.match(tag)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def update_toml_j2(path: Path, pw_version: str, image_tag: str) -> None:
    content = path.read_text(encoding="utf-8")

    content = re.sub(
        r'(playwright_version\s*=\s*)"[^"]+"',
        f'\\1"{pw_version}"',
        content,
    )
    # Update image.tag — under [pwmcp.playwright_server.image]
    # The section header immediately precedes the tag field, so we match the block.
    content = re.sub(
        r'(\[pwmcp\.playwright_server\.image\][^\[]*tag\s*=\s*)"[^"]+"',
        lambda m: m.group(0).rsplit('"', 2)[0] + f'"{image_tag}"',
        content,
        flags=re.DOTALL,
    )
    # Also update image.tag under [pwmcp.unified.image] (same tag value keeps
    # the unified image in lockstep with the playwright-server image).
    content = re.sub(
        r'(\[pwmcp\.unified\.image\][^\[]*tag\s*=\s*)"[^"]+"',
        lambda m: m.group(0).rsplit('"', 2)[0] + f'"{image_tag}"',
        content,
        flags=re.DOTALL,
    )
    path.write_text(content, encoding="utf-8")


def update_bake_hcl(path: Path, pw_version: str, pwmcp_version: str) -> None:
    content = path.read_text(encoding="utf-8")

    content = re.sub(
        r'(variable\s+"PLAYWRIGHT_VERSION"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{pw_version}"',
        content,
        flags=re.DOTALL,
    )
    content = re.sub(
        r'(variable\s+"PWMCP_VERSION"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{pwmcp_version}"',
        content,
        flags=re.DOTALL,
    )
    path.write_text(content, encoding="utf-8")


def write_release_vars(pw_version: str, distro: str, pwmcp_version: str) -> None:
    RELEASE_VARS_FILE.write_text(
        f"PLAYWRIGHT_VERSION={pw_version}\n"
        f"PLAYWRIGHT_DISTRO={distro}\n"
        f"PWMCP_VERSION={pwmcp_version}\n",
        encoding="utf-8",
    )


def read_current_distro() -> str:
    content = DEFAULTS_FILE.read_text(encoding="utf-8")
    m = re.search(r'image_distro\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else "noble"


def main() -> None:
    log("Fetching latest playwright version from npm...")
    new_pw_version = fetch_npm_latest_version()
    log(f"npm latest playwright: {new_pw_version}")

    release_n = compute_release_number(new_pw_version)
    pwmcp_version = f"{new_pw_version}-r{release_n}"
    log(f"pwmcp release: pwmcp-v{pwmcp_version}")

    distro = read_current_distro()

    log(f"Updating {DEFAULTS_FILE.name}...")
    update_toml_j2(DEFAULTS_FILE, new_pw_version, pwmcp_version)

    if TOML_OVERRIDE_FILE.exists():
        log(f"Updating {TOML_OVERRIDE_FILE.name}...")
        update_toml_j2(TOML_OVERRIDE_FILE, new_pw_version, pwmcp_version)

    log(f"Updating {BAKE_FILE.name}...")
    update_bake_hcl(BAKE_FILE, new_pw_version, pwmcp_version)

    log(f"Writing {RELEASE_VARS_FILE.name}...")
    write_release_vars(new_pw_version, distro, pwmcp_version)

    log(f"Done. PLAYWRIGHT_VERSION={new_pw_version}  PWMCP_VERSION={pwmcp_version}")
    log(f"Git tag to create after push: pwmcp-v{pwmcp_version}")


if __name__ == "__main__":
    main()
