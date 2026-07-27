#!/usr/bin/env python3
"""Resolve the newest jointly publishable Playwright version and update pwmcp config files.

Steps:
  1. Fetch all stable playwright versions from npm, PyPI, and Microsoft's official
     Playwright image registry.
  2. Select the highest version common to all three.  A package release is not
     publishable until its matching ``mcr.microsoft.com/playwright`` base image
     exists, and pwmcp's Python and npm consumers must agree on one version.
  3. Compute the next r<N> counter by scanning git tags (pwmcp-v<pw_ver>-r*).
  4. Update ciu.defaults.toml.j2 and ciu.toml.j2 (playwright_version + image.tag).
  5. Update docker-bake.hcl defaults and write cmru.vars for downstream scripts.

Outputs:
  pwmcp/cmru.vars  — KEY=VALUE env file consumed by build-bundle.py / publish-bundle.py
                     and build-push.py
  ciu.defaults.toml.j2 — playwright_version and image.tag updated in-place (PyPI version)
  ciu.toml.j2          — same (kept in sync with defaults)
  docker-bake.hcl      — common-version defaults updated in-place

Consumer contract:
  - :latest and :latest-npm track the same newest version available from all
    required upstreams.
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

NPM_PLAYWRIGHT_URL = "https://registry.npmjs.org/playwright"
PYPI_PLAYWRIGHT_URL = "https://pypi.org/pypi/playwright/json"
MCR_PLAYWRIGHT_TAGS_URL = "https://mcr.microsoft.com/v2/playwright/tags/list?n=10000"
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


def _stable_version(value: str) -> tuple[int, int, int] | None:
    """Return a sortable stable semver tuple, excluding prereleases."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value.strip())
    return tuple(map(int, match.groups())) if match else None


def _latest_version(versions: set[str], label: str) -> str:
    stable = [version for version in versions if _stable_version(version)]
    if not stable:
        fail(f"{label} did not provide any stable Playwright versions")
    return max(stable, key=lambda version: _stable_version(version))


def fetch_npm_versions() -> set[str]:
    payload = _fetch_json(NPM_PLAYWRIGHT_URL, "npm")
    versions = payload.get("versions")
    if not isinstance(versions, dict):
        fail("npm response missing 'versions' object")
    return {str(version) for version in versions if _stable_version(str(version))}


def fetch_pypi_versions() -> set[str]:
    payload = _fetch_json(PYPI_PLAYWRIGHT_URL, "PyPI")
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        fail("PyPI response missing 'releases' object")
    # A release key with no files has been withdrawn and cannot be installed.
    return {
        str(version)
        for version, files in releases.items()
        if _stable_version(str(version)) and isinstance(files, list) and files
    }


def fetch_mcr_versions(distro: str) -> set[str]:
    payload = _fetch_json(MCR_PLAYWRIGHT_TAGS_URL, "Microsoft Container Registry")
    tags = payload.get("tags")
    if not isinstance(tags, list):
        fail("Microsoft Container Registry response missing 'tags' array")
    pattern = re.compile(rf"^v(\d+\.\d+\.\d+)-{re.escape(distro)}$")
    return {
        match.group(1)
        for tag in tags
        if isinstance(tag, str) and (match := pattern.fullmatch(tag))
    }


def resolve_latest_common_version(
    npm_versions: set[str], pypi_versions: set[str], mcr_versions: set[str],
) -> str:
    """Choose the newest stable version that every required upstream provides."""
    common = npm_versions & pypi_versions & mcr_versions
    if not common:
        fail(
            "No common stable Playwright version across npm, PyPI, and the "
            "Microsoft Container Registry"
        )
    return _latest_version(common, "the upstream intersection")


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
    distro = read_current_distro()
    log("Fetching Playwright versions from npm, PyPI, and the Microsoft Container Registry...")
    npm_versions = fetch_npm_versions()
    pypi_versions = fetch_pypi_versions()
    mcr_versions = fetch_mcr_versions(distro)
    npm_latest = _latest_version(npm_versions, "npm")
    pypi_latest = _latest_version(pypi_versions, "PyPI")
    mcr_latest = _latest_version(mcr_versions, "the Microsoft Container Registry")
    agreed_version = resolve_latest_common_version(npm_versions, pypi_versions, mcr_versions)
    log(
        f"upstream latest: npm={npm_latest} PyPI={pypi_latest} "
        f"MCR/{distro}={mcr_latest}; agreed stable version: {agreed_version}"
    )

    release_n = compute_release_number(agreed_version)
    pwmcp_version = f"{agreed_version}-r{release_n}"
    log(f"pwmcp release: pwmcp-v{pwmcp_version}")

    log(f"Updating {DEFAULTS_FILE.name} (agreed version: {agreed_version})...")
    update_toml_j2(DEFAULTS_FILE, agreed_version, pwmcp_version)

    if TOML_OVERRIDE_FILE.exists():
        log(f"Updating {TOML_OVERRIDE_FILE.name}...")
        update_toml_j2(TOML_OVERRIDE_FILE, agreed_version, pwmcp_version)

    log(f"Updating {BAKE_FILE.name}...")
    update_bake_hcl(BAKE_FILE, agreed_version, agreed_version, pwmcp_version, pwmcp_version)

    log(f"Updating {CONTRACT_FILE.name}...")
    update_contract(CONTRACT_FILE, release=pwmcp_version, playwright_version=agreed_version)

    log(f"Writing {RELEASE_VARS_FILE.name}...")
    pw_mcp_ver = _read_bake_var("PLAYWRIGHT_MCP_VERSION")
    cdt_mcp_ver = _read_bake_var("CHROME_DEVTOOLS_MCP_VERSION")
    mcp_proxy_ver = _read_bake_var("MCP_PROXY_VERSION")
    lh_ver = _read_bake_var("LIGHTHOUSE_VERSION")
    write_release_vars(agreed_version, agreed_version, distro, pwmcp_version, pwmcp_version,
                       pw_mcp_ver, cdt_mcp_ver, mcp_proxy_ver, lh_ver)

    log(
        f"Done. PLAYWRIGHT_VERSION_NPM={agreed_version}  PLAYWRIGHT_VERSION_PYPI={agreed_version}  "
        f"PWMCP_VERSION_NPM={pwmcp_version}  PWMCP_VERSION_PYPI={pwmcp_version}"
    )
    log(f"Git tag to create after push: pwmcp-v{pwmcp_version}")


if __name__ == "__main__":
    main()
