#!/usr/bin/env python3
"""Publish Playwright MCP client wheel to GitHub Releases."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass
class ApiResponse:
    status: int
    body: str


def log_debug(message: str) -> None:
    if os.getenv("PWMCP_DEBUG_API") == "1":
        print(f"[DEBUG] {message}")


def fail(message: str, status: Optional[int] = None, body: str | None = None) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    if status is not None:
        print(f"[ERROR] HTTP status: {status}", file=sys.stderr)
    if body:
        print(f"[ERROR] Response body: {body}", file=sys.stderr)
    raise SystemExit(1)


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def api_request(method: str, url: str, token: str, data: bytes | None = None, content_type: str | None = None) -> ApiResponse:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    if content_type:
        headers["Content-Type"] = content_type

    req = Request(url, method=method, headers=headers, data=data)
    try:
        with urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            log_debug(f"{method} {url} -> {resp.status}")
            return ApiResponse(resp.status, body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        log_debug(f"{method} {url} -> {exc.code}")
        return ApiResponse(exc.code, body)


def parse_json(body: str, context: str) -> Dict[str, Any]:
    if not body.strip():
        fail(f"Empty JSON input for {context}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON input for {context}: {exc}")
    return {}


def build_wheel(project_root: Path, wheel_glob: str) -> Path:
    dist_dir = project_root / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "-w", str(dist_dir)],
        check=True,
        cwd=str(project_root),
    )
    wheels = sorted(dist_dir.glob(wheel_glob))
    if not wheels:
        fail(f"Wheel not found in dist/ (glob: {wheel_glob})")
    return wheels[0]


def get_release_by_tag(api_base: str, owner: str, repo: str, tag: str, token: str) -> Optional[Dict[str, Any]]:
    resp = api_request("GET", f"{api_base}/repos/{owner}/{repo}/releases/tags/{tag}", token)
    if resp.status == 404:
        return None
    if resp.status >= 400:
        fail(f"Failed to fetch release tag {tag}", resp.status, resp.body)
    return parse_json(resp.body, f"release tag {tag}")


def create_release(api_base: str, owner: str, repo: str, tag: str, title: str, notes: str, token: str) -> Dict[str, Any]:
    payload = json.dumps({"tag_name": tag, "name": title, "body": notes}).encode("utf-8")
    resp = api_request("POST", f"{api_base}/repos/{owner}/{repo}/releases", token, data=payload, content_type="application/json")
    if resp.status >= 400:
        fail(f"Failed to create release {tag}", resp.status, resp.body)
    return parse_json(resp.body, f"create release {tag}")


def update_release(api_base: str, owner: str, repo: str, release_id: int, title: str, notes: str, token: str) -> Dict[str, Any]:
    payload = json.dumps({"name": title, "body": notes}).encode("utf-8")
    resp = api_request("PATCH", f"{api_base}/repos/{owner}/{repo}/releases/{release_id}", token, data=payload, content_type="application/json")
    if resp.status >= 400:
        fail(f"Failed to update release {release_id}", resp.status, resp.body)
    return parse_json(resp.body, f"update release {release_id}")


def delete_release(api_base: str, owner: str, repo: str, release_id: int, token: str) -> None:
    resp = api_request("DELETE", f"{api_base}/repos/{owner}/{repo}/releases/{release_id}", token)
    if resp.status >= 400:
        fail(f"Failed to delete release {release_id}", resp.status, resp.body)


def list_assets(api_base: str, owner: str, repo: str, release_id: int, token: str) -> list[Dict[str, Any]]:
    resp = api_request("GET", f"{api_base}/repos/{owner}/{repo}/releases/{release_id}/assets", token)
    if resp.status >= 400:
        fail(f"Failed to list assets for release {release_id}", resp.status, resp.body)
    return parse_json(resp.body, f"list assets {release_id}")


def delete_asset(api_base: str, owner: str, repo: str, asset_id: int, token: str) -> None:
    resp = api_request("DELETE", f"{api_base}/repos/{owner}/{repo}/releases/assets/{asset_id}", token)
    if resp.status >= 400:
        fail(f"Failed to delete existing asset {asset_id}", resp.status, resp.body)


def upload_asset(upload_url: str, asset_path: Path, asset_name: str, token: str) -> None:
    upload_url = upload_url.split("{", 1)[0]
    data = asset_path.read_bytes()
    resp = api_request(
        "POST",
        f"{upload_url}?name={asset_name}",
        token,
        data=data,
        content_type="application/octet-stream",
    )
    if resp.status >= 400:
        fail(f"Failed to upload asset {asset_name}", resp.status, resp.body)


def publish_release_asset(
    api_base: str,
    owner: str,
    repo: str,
    tag: str,
    title: str,
    notes: str,
    asset_path: Path,
    asset_name: str,
    token: str,
    *,
    recreate: bool = False,
) -> None:
    release = get_release_by_tag(api_base, owner, repo, tag, token)
    if release is None:
        release = create_release(api_base, owner, repo, tag, title, notes, token)
    else:
        release_id = release.get("id")
        if release_id and recreate:
            delete_release(api_base, owner, repo, int(release_id), token)
            release = create_release(api_base, owner, repo, tag, title, notes, token)
        elif release_id:
            update_release(api_base, owner, repo, int(release_id), title, notes, token)

    release_id = release.get("id")
    upload_url = release.get("upload_url")
    if not release_id or not upload_url:
        fail(f"Release response missing id/upload_url for tag {tag}")

    assets = list_assets(api_base, owner, repo, int(release_id), token)
    for asset in assets:
        if asset.get("name") == asset_name and asset.get("id"):
            delete_asset(api_base, owner, repo, int(asset["id"]), token)
            break

    upload_asset(str(upload_url), asset_path, asset_name, token)


def main() -> None:
    project_root = Path(__file__).resolve().parent
    repo_root = project_root.parent

    env_file = Path(os.getenv("PWMCP_ENV_FILE", repo_root / ".env"))
    if env_file.exists():
        load_env_file(env_file)

    import tomllib

    with open(project_root / "pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)
    project_meta = data.get("project", {})

    version = os.getenv("PWMCP_BUILD_VERSION") or os.getenv("PWMCP_VERSION")

    if not version:
        version = datetime.now(timezone.utc).strftime("%Y%m%d")

    package_name = os.getenv("PWMCP_PACKAGE_NAME", project_meta.get("name", "playwright-mcp-client"))
    dist_name = package_name.replace("-", "_")
    wheel_glob = os.getenv("PWMCP_WHEEL_GLOB", f"{dist_name}-*.whl")

    wheel_path = build_wheel(project_root, wheel_glob)

    token = os.getenv("GITHUB_PUSH_PAT") or os.getenv("GH_TOKEN")
    if not token:
        fail("GITHUB_PUSH_PAT or GH_TOKEN is required")

    owner = os.getenv("GITHUB_USERNAME")
    repo = os.getenv("GITHUB_REPO")
    if not owner or not repo:
        fail("GITHUB_USERNAME and GITHUB_REPO are required")

    latest_tag = os.getenv("PWMCP_LATEST_TAG", f"{package_name}-wheel-latest")
    release_tag = os.getenv("PWMCP_RELEASE_TAG", f"{package_name}-wheel-{version}")

    release_title = os.getenv("PWMCP_RELEASE_TITLE", release_tag)
    latest_title = os.getenv("PWMCP_LATEST_TITLE", latest_tag)

    release_notes = os.getenv(
        "PWMCP_RELEASE_NOTES",
        (
            f"{package_name} wheel {version}\n\n"
            "Includes:\n"
            "- WebSocket client SDK for Playwright MCP\n"
            "- Retry policy helpers and UI harness\n"
            "- Lightweight dependency set (no server stack)\n\n"
            "Install:\n"
            "```bash\n"
            "python -m pip install ./<wheel-file>\n"
            "```"
        ),
    )
    latest_notes = os.getenv(
        "PWMCP_LATEST_NOTES",
        (
            f"{package_name} wheel latest\n\n"
            "Includes:\n"
            "- Latest client SDK wheel\n"
            "- Ready for integration into Python apps\n\n"
            "Install:\n"
            "```bash\n"
            "python -m pip install ./<wheel-file>\n"
            "```"
        ),
    )

    release_asset_name = os.getenv("PWMCP_RELEASE_ASSET_NAME", wheel_path.name)
    latest_asset_name = os.getenv("PWMCP_LATEST_ASSET_NAME", wheel_path.name)

    api_base = "https://api.github.com"

    publish_release_asset(api_base, owner, repo, release_tag, release_title, release_notes, wheel_path, release_asset_name, token)
    publish_release_asset(
        api_base,
        owner,
        repo,
        latest_tag,
        latest_title,
        latest_notes,
        wheel_path,
        latest_asset_name,
        token,
        recreate=True,
    )

    print(f"[INFO] Published {wheel_path.name}")
    print(f"[INFO] Release tag: {release_tag}")
    print(f"[INFO] Latest tag: {latest_tag}")


if __name__ == "__main__":
    main()