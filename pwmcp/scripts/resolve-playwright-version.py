#!/usr/bin/env python3
"""Resolve the newest jointly publishable Playwright version and update pwmcp config files.

Steps:
  1. Fetch stable Playwright versions and publication times from npm and PyPI,
     plus available tags from Microsoft's official Playwright image registry.
  2. Select the highest version common to all three whose package publication
     timestamps clear the temporary 14-day age window.  A package release is
     not publishable until its matching base-image tag exists, and PWMCP's
     Python and npm consumers must agree on one version.
  3. Compute the next r<N> counter by scanning git tags (pwmcp-v<pw_ver>-r*).
  4. Update ciu.defaults.toml.j2 and ciu.toml.j2 (playwright_version + image.tag).
  5. Update the Dockerfile and docker-bake.hcl with the matching base-image digest.
  6. Write cmru.vars for downstream scripts.

Outputs:
  pwmcp/cmru.vars  — KEY=VALUE env file consumed by build-bundle.py / publish-bundle.py
                     and build-push.py
  ciu.defaults.toml.j2 — playwright_version and image.tag updated in-place (PyPI version)
  ciu.toml.j2          — same (kept in sync with defaults)
  docker-bake.hcl      — common-version defaults updated in-place

Consumer contract:
  - :latest and :latest-npm track the same reviewed version available from all
    required upstreams.
  - Use `pip install playwright==<X>` + `image: pwmcp:<X>` for a guaranteed matching pair.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.request
from pathlib import Path

NPM_PLAYWRIGHT_URL = "https://registry.npmjs.org/playwright"
PYPI_PLAYWRIGHT_URL = "https://pypi.org/pypi/playwright/json"
MCR_PLAYWRIGHT_TAGS_URL = "https://mcr.microsoft.com/v2/playwright/tags/list?n=10000"
MCR_PLAYWRIGHT_MANIFEST_URL = "https://mcr.microsoft.com/v2/playwright/manifests"
TIMEOUT_SECONDS = 20
RETRIES = 3
TEMPORARY_AGE_WINDOW_DAYS = 14

PWMCP_DIR = Path(__file__).resolve().parent.parent
DEFAULTS_FILE = PWMCP_DIR / "ciu.defaults.toml.j2"
TOML_OVERRIDE_FILE = PWMCP_DIR / "ciu.toml.j2"
BAKE_FILE = PWMCP_DIR / "docker-bake.hcl"
RELEASE_VARS_FILE = PWMCP_DIR / "cmru.vars"
CONTRACT_FILE = PWMCP_DIR / "pwmcp.contract.json"
DOCKERFILE = PWMCP_DIR / "containers" / "pwmcp" / "Dockerfile"
LIGHTHOUSE_PACKAGE_FILE = PWMCP_DIR / "containers" / "pwmcp" / "lighthouse-mcp" / "package.json"
LIGHTHOUSE_LOCK_FILE = PWMCP_DIR / "containers" / "pwmcp" / "lighthouse-mcp" / "package-lock.json"


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def _fetch_http(
    url: str,
    label: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
) -> tuple[bytes, object]:
    """Fetch an HTTP response body and headers with retry logic."""
    last_exc: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "pwmcp/resolve-playwright-version",
                    **(headers or {}),
                },
                method=method,
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return resp.read(), getattr(resp, "headers", {})
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
    return b"", {}  # unreachable


def _fetch_json(url: str, label: str) -> dict:
    """Fetch JSON from a URL with retry logic."""
    body, _headers = _fetch_http(url, label)
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


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


def _parse_release_time(value: object, label: str) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(f"{label} contains an invalid publication timestamp: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_npm_release_times() -> dict[str, datetime]:
    payload = _fetch_json(NPM_PLAYWRIGHT_URL, "npm")
    times = payload.get("time")
    if not isinstance(times, dict):
        fail("npm response missing 'time' publication metadata")
    result: dict[str, datetime] = {}
    for version, value in times.items():
        parsed = _parse_release_time(value, "npm publication metadata")
        if parsed is not None and _stable_version(str(version)):
            result[str(version)] = parsed
    return result


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


def fetch_pypi_release_times() -> dict[str, datetime]:
    payload = _fetch_json(PYPI_PLAYWRIGHT_URL, "PyPI")
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        fail("PyPI response missing 'releases' object")
    result: dict[str, datetime] = {}
    for version, files in releases.items():
        if not _stable_version(str(version)):
            continue
        if not isinstance(files, list):
            continue
        if not files:
            continue
        timestamps = [
            _parse_release_time(
                item.get("upload_time_iso_8601") or item.get("upload_time"),
                f"PyPI {version} file metadata",
            )
            for item in files
            if isinstance(item, dict)
        ]
        valid = [timestamp for timestamp in timestamps if timestamp is not None]
        if valid:
            result[str(version)] = max(valid)
    return result


def _filter_by_age(
    versions: set[str], release_times: dict[str, datetime], cutoff: datetime,
    label: str, age_window_days: int,
) -> set[str]:
    eligible = {version for version in versions if release_times.get(version, datetime.max.replace(tzinfo=timezone.utc)) <= cutoff}
    if not eligible:
        fail(f"{label} has no stable version at least {age_window_days} days old")
    return eligible


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


def fetch_mcr_manifest_digest(version: str, distro: str) -> str:
    """Return the immutable multi-arch manifest digest for a Playwright tag."""
    url = f"{MCR_PLAYWRIGHT_MANIFEST_URL}/v{version}-{distro}"
    _body, headers = _fetch_http(
        url,
        f"Microsoft Container Registry manifest for v{version}-{distro}",
        headers={
            "Accept": (
                "application/vnd.docker.distribution.manifest.list.v2+json, "
                "application/vnd.oci.image.index.v1+json"
            )
        },
        method="HEAD",
    )
    digest = headers.get("Docker-Content-Digest") if hasattr(headers, "get") else None
    if not isinstance(digest, str):
        fail(f"Microsoft Container Registry manifest for v{version}-{distro} did not return Docker-Content-Digest")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        fail(
            f"Microsoft Container Registry returned an invalid manifest digest for "
            f"v{version}-{distro}: {digest!r}"
        )
    return digest


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

    content, version_matches = re.subn(
        r'(playwright_version\s*=\s*)"[^"]+"',
        f'\\1"{pw_version}"',
        content,
    )
    if version_matches != 1:
        fail(f"{path.name} must contain exactly one playwright_version field (found {version_matches})")
    # Update image.tag under [pwmcp.unified.image].
    content, image_matches = re.subn(
        r'(\[pwmcp\.unified\.image\][^\[]*tag\s*=\s*)"[^"]+"',
        lambda m: m.group(0).rsplit('"', 2)[0] + f'"{image_tag}"',
        content,
        flags=re.DOTALL,
    )
    if image_matches != 1:
        fail(f"{path.name} must contain exactly one [pwmcp.unified.image] tag field (found {image_matches})")
    path.write_text(content, encoding="utf-8")


def update_bake_hcl(
    path: Path,
    playwright_version: str,
    pwmcp_version: str,
    image_digest: str,
) -> None:
    content = path.read_text(encoding="utf-8")

    content, playwright_matches = re.subn(
        r'(variable\s+"PLAYWRIGHT_VERSION"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{playwright_version}"',
        content,
        flags=re.DOTALL,
    )
    if playwright_matches != 1:
        fail(f"{path.name} must contain exactly one PLAYWRIGHT_VERSION variable (found {playwright_matches})")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
        fail(f"{path.name} base-image digest is not a sha256 digest: {image_digest!r}")
    content, digest_matches = re.subn(
        r'(variable\s+"PLAYWRIGHT_IMAGE_DIGEST"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        rf'\g<1>"{image_digest}"',
        content,
        flags=re.DOTALL,
    )
    if digest_matches != 1:
        fail(
            f"{path.name} must contain exactly one PLAYWRIGHT_IMAGE_DIGEST variable "
            f"(found {digest_matches})"
        )
    content, pwmcp_matches = re.subn(
        r'(variable\s+"PWMCP_VERSION"\s*\{[^}]*default\s*=\s*)"[^"]+"',
        f'\\1"{pwmcp_version}"',
        content,
        flags=re.DOTALL,
    )
    if pwmcp_matches != 1:
        fail(f"{path.name} must contain exactly one PWMCP_VERSION variable (found {pwmcp_matches})")
    path.write_text(content, encoding="utf-8")


def update_dockerfile(path: Path, image_digest: str) -> None:
    """Update the authoritative base-image digest in Dockerfile ARG and FROM."""
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
        fail(f"Dockerfile base-image digest is not a sha256 digest: {image_digest!r}")
    content = path.read_text(encoding="utf-8")
    content, arg_matches = re.subn(
        r"(^ARG PLAYWRIGHT_IMAGE_DIGEST=)[^\s]+$",
        rf"\g<1>{image_digest}",
        content,
        flags=re.MULTILINE,
    )
    if arg_matches != 1:
        fail(
            f"{path.name} must contain exactly one PLAYWRIGHT_IMAGE_DIGEST ARG "
            f"(found {arg_matches})"
        )
    content, from_matches = re.subn(
        r"(^FROM mcr\.microsoft\.com/playwright:v\$\{PLAYWRIGHT_VERSION\}-\$\{PLAYWRIGHT_DISTRO\})"
        r"@\$\{PLAYWRIGHT_IMAGE_DIGEST\}$",
        r"\g<1>@${PLAYWRIGHT_IMAGE_DIGEST}",
        content,
        flags=re.MULTILINE,
    )
    if from_matches != 1:
        fail(
            f"{path.name} must contain exactly one Playwright base FROM with a replaceable digest "
            f"(found {from_matches})"
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


def _read_single_value(path: Path, pattern: str, label: str) -> str:
    content = path.read_text(encoding="utf-8")
    matches = re.findall(pattern, content, flags=re.DOTALL | re.MULTILINE)
    if len(matches) != 1 or not matches[0].strip():
        fail(f"{path.name} must contain exactly one non-empty {label} (found {len(matches)})")
    return matches[0]


def _read_toml_playwright_version(path: Path) -> str:
    return _read_single_value(
        path,
        r'playwright_version\s*=\s*"([^"]+)"',
        "playwright_version field",
    )


def _read_toml_release_tag(path: Path) -> str:
    return _read_single_value(
        path,
        r'\[pwmcp\.unified\.image\][^\[]*?tag\s*=\s*"([^"]+)"',
        "[pwmcp.unified.image] tag field",
    )


def _read_docker_arg(name: str) -> str:
    return _read_single_value(
        DOCKERFILE,
        rf"^ARG\s+{re.escape(name)}=([^\s]+)\s*$",
        f"Dockerfile {name} ARG",
    )


def _read_docker_base_digest() -> str:
    _read_single_value(
        DOCKERFILE,
        r"^FROM mcr\.microsoft\.com/playwright:v\$\{PLAYWRIGHT_VERSION\}-\$\{PLAYWRIGHT_DISTRO\}@"
        r"\$\{PLAYWRIGHT_IMAGE_DIGEST\}\s*$",
        "Playwright base-image FROM digest argument",
    )
    return _read_docker_arg("PLAYWRIGHT_IMAGE_DIGEST")


def _assert_same(label: str, values: dict[str, str]) -> str:
    distinct = set(values.values())
    if len(distinct) != 1:
        rendered = ", ".join(f"{source}={value}" for source, value in values.items())
        fail(f"{label} projections disagree: {rendered}")
    return next(iter(distinct))


def check_committed_inputs() -> None:
    """Validate the committed release projection without contacting upstreams.

    CMRU FEAT-03 will own upstream selection. Until that resolver is available,
    PWMCP release preparation must consume the reviewed, committed projection
    instead of silently selecting whatever is newest at release time.
    """
    contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    if (
        not isinstance(contract, dict)
        or not isinstance(contract.get("release"), str)
        or not contract["release"].strip()
    ):
        fail(f"{CONTRACT_FILE.name} must contain a non-empty release string")
    playwright_version = _assert_same(
        "Playwright version",
        {
            DEFAULTS_FILE.name: _read_toml_playwright_version(DEFAULTS_FILE),
            BAKE_FILE.name: _read_bake_var("PLAYWRIGHT_VERSION"),
            DOCKERFILE.name: _read_docker_arg("PLAYWRIGHT_VERSION"),
        },
    )
    image_digest = _assert_same(
        "Playwright base-image digest",
        {
            BAKE_FILE.name: _read_bake_var("PLAYWRIGHT_IMAGE_DIGEST"),
            DOCKERFILE.name: _read_docker_arg("PLAYWRIGHT_IMAGE_DIGEST"),
            f"{DOCKERFILE.name} FROM": _read_docker_base_digest(),
        },
    )
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None:
        fail(f"Playwright base-image digest is not a sha256 digest: {image_digest!r}")
    release_tag = _assert_same(
        "PWMCP release version",
        {
            DEFAULTS_FILE.name: _read_toml_release_tag(DEFAULTS_FILE),
            BAKE_FILE.name: _read_bake_var("PWMCP_VERSION"),
            CONTRACT_FILE.name: contract["release"],
        },
    )
    contract_playwright = contract.get("playwright")
    if not isinstance(contract_playwright, dict):
        fail(f"{CONTRACT_FILE.name} must contain a playwright object")
    if contract_playwright.get("python") != playwright_version:
        fail(
            f"{CONTRACT_FILE.name} playwright.python={contract_playwright.get('python')!r} "
            f"does not match committed Playwright version {playwright_version!r}"
        )
    expected_protocol = ".".join(playwright_version.split(".")[:2])
    if contract_playwright.get("protocol") != expected_protocol:
        fail(
            f"{CONTRACT_FILE.name} playwright.protocol={contract_playwright.get('protocol')!r} "
            f"does not match Playwright protocol {expected_protocol!r}"
        )

    for name in (
        "PLAYWRIGHT_MCP_VERSION",
        "CHROME_DEVTOOLS_MCP_VERSION",
        "MCP_PROXY_VERSION",
        "LIGHTHOUSE_VERSION",
    ):
        _assert_same(name, {"docker-bake.hcl": _read_bake_var(name), "Dockerfile": _read_docker_arg(name)})

    package = json.loads(LIGHTHOUSE_PACKAGE_FILE.read_text(encoding="utf-8"))
    lock = json.loads(LIGHTHOUSE_LOCK_FILE.read_text(encoding="utf-8"))
    package_dependencies = package.get("dependencies")
    lock_root = lock.get("packages", {}).get("")
    lock_dependencies = lock_root.get("dependencies") if isinstance(lock_root, dict) else None
    if not isinstance(package_dependencies, dict) or not isinstance(lock_dependencies, dict):
        fail("Lighthouse package.json and package-lock.json must contain dependency tables")
    for name, expected in package_dependencies.items():
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(expected)):
            fail(f"{LIGHTHOUSE_PACKAGE_FILE.name}: {name} must use an exact version, got {expected!r}")
        if lock_dependencies.get(name) != expected:
            fail(
                f"{LIGHTHOUSE_LOCK_FILE.name}: root dependency {name}={lock_dependencies.get(name)!r} "
                f"does not match package.json {expected!r}"
            )
        installed = lock.get("packages", {}).get(f"node_modules/{name}", {}).get("version")
        if installed != expected:
            fail(
                f"{LIGHTHOUSE_LOCK_FILE.name}: resolved {name}={installed!r} "
                f"does not match package.json {expected!r}"
            )

    if TOML_OVERRIDE_FILE.exists():
        _assert_same(
            "PWMCP override Playwright version",
            {DEFAULTS_FILE.name: playwright_version, TOML_OVERRIDE_FILE.name: _read_toml_playwright_version(TOML_OVERRIDE_FILE)},
        )
        _assert_same(
            "PWMCP override release version",
            {DEFAULTS_FILE.name: release_tag, TOML_OVERRIDE_FILE.name: _read_toml_release_tag(TOML_OVERRIDE_FILE)},
        )

    distro = read_current_distro()
    write_release_vars(
        playwright_version,
        distro,
        release_tag,
        image_digest,
        _read_bake_var("PLAYWRIGHT_MCP_VERSION"),
        _read_bake_var("CHROME_DEVTOOLS_MCP_VERSION"),
        _read_bake_var("MCP_PROXY_VERSION"),
        _read_bake_var("LIGHTHOUSE_VERSION"),
    )
    log(
        f"Validated committed release projection: PLAYWRIGHT_VERSION={playwright_version} "
        f"PWMCP_VERSION={release_tag}"
    )


def write_release_vars(
    playwright_version: str,
    distro: str,
    pwmcp_version: str,
    image_digest: str,
    playwrght_mcp_version: str,
    chrome_devtools_mcp_version: str,
    mcp_proxy_version: str,
    lighthouse_version: str,
) -> None:
    RELEASE_VARS_FILE.write_text(
        f"PLAYWRIGHT_VERSION={playwright_version}\n"
        f"PLAYWRIGHT_DISTRO={distro}\n"
        f"PLAYWRIGHT_IMAGE_DIGEST={image_digest}\n"
        f"PLAYWRIGHT_MCP_VERSION={playwrght_mcp_version}\n"
        f"CHROME_DEVTOOLS_MCP_VERSION={chrome_devtools_mcp_version}\n"
        f"MCP_PROXY_VERSION={mcp_proxy_version}\n"
        f"LIGHTHOUSE_VERSION={lighthouse_version}\n"
        f"PWMCP_VERSION={pwmcp_version}\n"
        "GHCR_PACKAGE_NAMES=pwmcp\n",
        encoding="utf-8",
    )


def read_current_distro() -> str:
    content = DEFAULTS_FILE.read_text(encoding="utf-8")
    matches = re.findall(r'image_distro\s*=\s*"([^"]+)"', content)
    if len(matches) != 1 or not matches[0].strip():
        fail(f"{DEFAULTS_FILE.name} must contain exactly one non-empty image_distro field (found {len(matches)})")
    return matches[0]


def refresh_upstream_projection(age_window_days: int = TEMPORARY_AGE_WINDOW_DAYS) -> None:
    if age_window_days < 0:
        fail("--age-window-days must be non-negative")
    distro = read_current_distro()
    cutoff = datetime.now(timezone.utc) - timedelta(days=age_window_days)
    log(
        "Fetching Playwright versions and publication times from npm, PyPI, "
        "and the Microsoft Container Registry..."
    )
    npm_versions = fetch_npm_versions()
    pypi_versions = fetch_pypi_versions()
    mcr_versions = fetch_mcr_versions(distro)
    npm_versions = _filter_by_age(
        npm_versions, fetch_npm_release_times(), cutoff, "npm", age_window_days
    )
    pypi_versions = _filter_by_age(
        pypi_versions, fetch_pypi_release_times(), cutoff, "PyPI", age_window_days
    )
    npm_latest = _latest_version(npm_versions, "npm")
    pypi_latest = _latest_version(pypi_versions, "PyPI")
    mcr_latest = _latest_version(mcr_versions, "the Microsoft Container Registry")
    agreed_version = resolve_latest_common_version(npm_versions, pypi_versions, mcr_versions)
    image_digest = fetch_mcr_manifest_digest(agreed_version, distro)
    log(
        f"eligible latest (cutoff {cutoff.isoformat()}): npm={npm_latest} "
        f"PyPI={pypi_latest} MCR/{distro}={mcr_latest}; agreed stable version: {agreed_version}"
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
    update_bake_hcl(BAKE_FILE, agreed_version, pwmcp_version, image_digest)

    log(f"Updating {DOCKERFILE.name} with base-image digest {image_digest}...")
    update_dockerfile(DOCKERFILE, image_digest)

    log(f"Updating {CONTRACT_FILE.name}...")
    update_contract(CONTRACT_FILE, release=pwmcp_version, playwright_version=agreed_version)

    log(f"Writing {RELEASE_VARS_FILE.name}...")
    pw_mcp_ver = _read_bake_var("PLAYWRIGHT_MCP_VERSION")
    cdt_mcp_ver = _read_bake_var("CHROME_DEVTOOLS_MCP_VERSION")
    mcp_proxy_ver = _read_bake_var("MCP_PROXY_VERSION")
    lh_ver = _read_bake_var("LIGHTHOUSE_VERSION")
    write_release_vars(agreed_version, distro, pwmcp_version, image_digest,
                       pw_mcp_ver, cdt_mcp_ver, mcp_proxy_ver, lh_ver)

    log(
        f"Done. PLAYWRIGHT_VERSION={agreed_version} PWMCP_VERSION={pwmcp_version}"
    )
    log(f"Git tag to create after push: pwmcp-v{pwmcp_version}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Validate the committed PWMCP version projection or explicitly refresh it"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="validate committed versions and write cmru.vars without contacting upstreams (default)",
    )
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="select the newest common upstream version at least 14 days old and rewrite the projection",
    )
    parser.add_argument(
        "--age-window-days",
        type=int,
        default=TEMPORARY_AGE_WINDOW_DAYS,
        help="temporary refresh age window until CMRU FEAT-03 owns version selection (default: 14)",
    )
    args = parser.parse_args(argv)
    if args.refresh:
        refresh_upstream_projection(args.age_window_days)
    else:
        check_committed_inputs()


if __name__ == "__main__":
    main()
