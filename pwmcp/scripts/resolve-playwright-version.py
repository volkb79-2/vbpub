#!/usr/bin/env python3
"""Resolve the latest Playwright version from npm AND PyPI and update pwmcp config files.

Steps:
  1. Fetch latest playwright version from npm registry (PLAYWRIGHT_VERSION_NPM).
  2. Fetch latest playwright version from PyPI (PLAYWRIGHT_VERSION_PYPI).
  3. Compute the next r<N> counter by scanning git tags (pwmcp-v<pw_ver>-r*),
     computed independently for npm and pypi versions.
  4. Update ciu.defaults.toml.j2 and ciu.toml.j2 (playwright_version + image.tag)
     to track the PyPI version (the canonical consumer version).
  5. Update docker-bake.hcl defaults (PLAYWRIGHT_VERSION_NPM, PLAYWRIGHT_VERSION_PYPI,
     PWMCP_VERSION_NPM, PWMCP_VERSION_PYPI).
  6. Write cmru.vars for downstream scripts (build-bundle.py, publish-bundle.py,
     and GHCR visibility sync) — emits BOTH PLAYWRIGHT_VERSION_NPM and
     PLAYWRIGHT_VERSION_PYPI; PLAYWRIGHT_VERSION is an alias for PLAYWRIGHT_VERSION_PYPI
     for backwards compatibility.

Outputs:
  pwmcp/cmru.vars  — KEY=VALUE env file consumed by build-bundle.py / publish-bundle.py
                     and build-push.py
  ciu.defaults.toml.j2 — playwright_version and image.tag updated in-place (PyPI version)
  ciu.toml.j2          — same (kept in sync with defaults)
  docker-bake.hcl      — PLAYWRIGHT_VERSION_NPM, PLAYWRIGHT_VERSION_PYPI,
                         PWMCP_VERSION_NPM, PWMCP_VERSION_PYPI defaults updated in-place

Consumer contract:
  - :latest always tracks PLAYWRIGHT_VERSION_PYPI (what `pip install playwright` yields).
  - :latest-npm tracks PLAYWRIGHT_VERSION_NPM.
  - Use `pip install playwright==<X>` + `image: pwmcp:<X>` for a guaranteed matching pair.
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
PYPI_PLAYWRIGHT_URL = "https://pypi.org/pypi/playwright/json"
TIMEOUT_SECONDS = 20
RETRIES = 3

PWMCP_DIR = Path(__file__).resolve().parent.parent
DEFAULTS_FILE = PWMCP_DIR / "ciu.defaults.toml.j2"
TOML_OVERRIDE_FILE = PWMCP_DIR / "ciu.toml.j2"
BAKE_FILE = PWMCP_DIR / "docker-bake.hcl"
RELEASE_VARS_FILE = PWMCP_DIR / "cmru.vars"
CONTRACT_FILE = PWMCP_DIR / "pwmcp.contract.json"


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def _fetch_json(url: str, label: str) -> dict:
    """Fetch JSON from a URL with retry logic."""
    last_exc: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "pwmcp/resolve-playwright-version"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in {429, 500, 502, 503, 504} and attempt < RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                log(f"HTTP {exc.code} from {label}; retrying in {delay}s ({attempt}/{RETRIES})")
                time.sleep(delay)
                continue
            fail(f"{label} fetch failed: HTTP {exc.code}")
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                log(f"Network error from {label}: {exc.reason}; retrying in {delay}s ({attempt}/{RETRIES})")
                time.sleep(delay)
                continue
            fail(f"{label} fetch failed: {exc.reason}")
    fail(f"{label} fetch failed after {RETRIES} attempts: {last_exc}")
    return {}  # unreachable


def fetch_npm_latest_version() -> str:
    payload = _fetch_json(NPM_PLAYWRIGHT_URL, "npm")
    version = str(payload.get("version") or "").strip()
    if not version:
        fail("npm response missing 'version' field")
    return version


def fetch_pypi_latest_version() -> str:
    payload = _fetch_json(PYPI_PLAYWRIGHT_URL, "PyPI")
    version = str((payload.get("info") or {}).get("version") or "").strip()
    if not version:
        fail("PyPI response missing 'info.version' field")
    return version


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
    # Update image.tag under [pwmcp.unified.image].
    content = re.sub(
        r'(\[pwmcp\.unified\.image\][^\[]*tag\s*=\s*)"[^"]+"',
        lambda m: m.group(0).rsplit('"', 2)[0] + f'"{image_tag}"',
        content,
        flags=re.DOTALL,
    )
    path.write_text(content, encoding="utf-8")


def update_bake_hcl(
    path: Path,
    npm_version: str,
    pypi_version: str,
    pwmcp_version_npm: str,
    pwmcp_version_pypi: str,
) -> None:
    content = path.read_text(encoding="utf-8")

    content = re.sub(
        r'(variable\s+"PLAYWRIGHT_VERSION_NPM"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{npm_version}"',
        content,
        flags=re.DOTALL,
    )
    content = re.sub(
        r'(variable\s+"PLAYWRIGHT_VERSION_PYPI"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{pypi_version}"',
        content,
        flags=re.DOTALL,
    )
    content = re.sub(
        r'(variable\s+"PWMCP_VERSION_NPM"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{pwmcp_version_npm}"',
        content,
        flags=re.DOTALL,
    )
    content = re.sub(
        r'(variable\s+"PWMCP_VERSION_PYPI"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{pwmcp_version_pypi}"',
        content,
        flags=re.DOTALL,
    )
    path.write_text(content, encoding="utf-8")


def update_contract(path: Path, *, release: str, playwright_version: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["release"] = release
    payload["playwright"]["python"] = playwright_version
    payload["playwright"]["protocol"] = ".".join(playwright_version.split(".")[:2])
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# Non-auto-updated package pins: read from bake file to preserve current values.
def _read_bake_var(name: str) -> str:
    """Read a single variable default from docker-bake.hcl."""
    content = BAKE_FILE.read_text(encoding="utf-8")
    m = re.search(r'variable\s+"' + re.escape(name) + r'"\s*\{[^}]*default\s*=\s*"([^"]+)"', content, re.DOTALL)
    if not m:
        fail(f"{name} not found in {BAKE_FILE.name}")
    return m.group(1)


def write_release_vars(
    npm_version: str,
    pypi_version: str,
    distro: str,
    pwmcp_version_npm: str,
    pwmcp_version_pypi: str,
    playwrght_mcp_version: str,
    chrome_devtools_mcp_version: str,
    mcp_proxy_version: str,
    lighthouse_version: str,
) -> None:
    # PLAYWRIGHT_VERSION is kept as alias for PLAYWRIGHT_VERSION_PYPI for backwards compat
    # (consumed by _vars.py which checks for PLAYWRIGHT_VERSION key).
    RELEASE_VARS_FILE.write_text(
        f"PLAYWRIGHT_VERSION_NPM={npm_version}\n"
        f"PLAYWRIGHT_VERSION_PYPI={pypi_version}\n"
        f"PLAYWRIGHT_VERSION={pypi_version}\n"
        f"PLAYWRIGHT_DISTRO={distro}\n"
        f"PLAYWRIGHT_MCP_VERSION={playwrght_mcp_version}\n"
        f"CHROME_DEVTOOLS_MCP_VERSION={chrome_devtools_mcp_version}\n"
        f"MCP_PROXY_VERSION={mcp_proxy_version}\n"
        f"LIGHTHOUSE_VERSION={lighthouse_version}\n"
        f"PWMCP_VERSION_NPM={pwmcp_version_npm}\n"
        f"PWMCP_VERSION_PYPI={pwmcp_version_pypi}\n"
        f"PWMCP_VERSION={pwmcp_version_pypi}\n"
        "GHCR_PACKAGE_NAMES=pwmcp\n",
        encoding="utf-8",
    )


def read_current_distro() -> str:
    content = DEFAULTS_FILE.read_text(encoding="utf-8")
    m = re.search(r'image_distro\s*=\s*"([^"]+)"', content)
    return m.group(1) if m else "noble"


def main() -> None:
    log("Fetching latest playwright version from npm...")
    npm_version = fetch_npm_latest_version()
    log(f"npm latest playwright: {npm_version}")

    log("Fetching latest playwright version from PyPI...")
    pypi_version = fetch_pypi_latest_version()
    log(f"PyPI latest playwright: {pypi_version}")

    release_n_npm = compute_release_number(npm_version)
    pwmcp_version_npm = f"{npm_version}-r{release_n_npm}"
    log(f"pwmcp npm release: pwmcp-v{pwmcp_version_npm}")

    release_n_pypi = compute_release_number(pypi_version)
    pwmcp_version_pypi = f"{pypi_version}-r{release_n_pypi}"
    log(f"pwmcp pypi release: pwmcp-v{pwmcp_version_pypi}")

    distro = read_current_distro()

    # ciu.toml.j2 / ciu.defaults.toml.j2 track the PyPI version (canonical consumer version).
    log(f"Updating {DEFAULTS_FILE.name} (PyPI version: {pypi_version})...")
    update_toml_j2(DEFAULTS_FILE, pypi_version, pwmcp_version_pypi)

    if TOML_OVERRIDE_FILE.exists():
        log(f"Updating {TOML_OVERRIDE_FILE.name}...")
        update_toml_j2(TOML_OVERRIDE_FILE, pypi_version, pwmcp_version_pypi)

    log(f"Updating {BAKE_FILE.name}...")
    update_bake_hcl(BAKE_FILE, npm_version, pypi_version, pwmcp_version_npm, pwmcp_version_pypi)

    log(f"Updating {CONTRACT_FILE.name}...")
    update_contract(CONTRACT_FILE, release=pwmcp_version_pypi, playwright_version=pypi_version)

    log(f"Writing {RELEASE_VARS_FILE.name}...")
    pw_mcp_ver = _read_bake_var("PLAYWRIGHT_MCP_VERSION")
    cdt_mcp_ver = _read_bake_var("CHROME_DEVTOOLS_MCP_VERSION")
    mcp_proxy_ver = _read_bake_var("MCP_PROXY_VERSION")
    lh_ver = _read_bake_var("LIGHTHOUSE_VERSION")
    write_release_vars(npm_version, pypi_version, distro, pwmcp_version_npm, pwmcp_version_pypi,
                       pw_mcp_ver, cdt_mcp_ver, mcp_proxy_ver, lh_ver)

    log(
        f"Done. PLAYWRIGHT_VERSION_NPM={npm_version}  PLAYWRIGHT_VERSION_PYPI={pypi_version}  "
        f"PWMCP_VERSION_NPM={pwmcp_version_npm}  PWMCP_VERSION_PYPI={pwmcp_version_pypi}"
    )
    log(f"Git tags to create after push: pwmcp-v{pwmcp_version_npm}  pwmcp-v{pwmcp_version_pypi}")


if __name__ == "__main__":
    main()
