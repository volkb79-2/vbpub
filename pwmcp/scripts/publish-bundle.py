#!/usr/bin/env python3
"""Publish the pwmcp stack bundle to GitHub Releases.

Mirrors ciu/tools/publish-wheel-release.py adapted for a tar.gz bundle artifact.

Required environment (from release.toml or shell):
  GITHUB_PUSH_PAT
  GITHUB_USERNAME
  GITHUB_REPO  (default: vbpub)

Reads PWMCP_VERSION from .release-vars (written by resolve-playwright-version.py).

Publish strategy:
  - Creates immutable release  pwmcp-v<version>  with the versioned bundle.
  - Recreates moving release   pwmcp-latest       pointing to the same asset.
"""
from __future__ import annotations

import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PWMCP_DIR = Path(__file__).resolve().parent.parent
RELEASE_VARS_FILE = PWMCP_DIR / ".release-vars"
DIST_DIR = PWMCP_DIR / "dist"


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def fail(msg: str, status: int | None = None, body: str | None = None) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    if status is not None:
        print(f"[ERROR] HTTP status: {status}", file=sys.stderr)
    if body:
        print(f"[ERROR] Response body: {body}", file=sys.stderr)
    raise SystemExit(1)


def load_release_vars(path: Path) -> None:
    if not path.exists():
        fail(f"{path} not found — run resolve-playwright-version.py first")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def api_request(method: str, url: str, token: str, data: bytes | None = None, content_type: str | None = None) -> tuple[int, str]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = Request(url, method=method, headers=headers, data=data)
    try:
        with urlopen(req) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        return exc.code, body


def parse_json(body: str, ctx: str) -> dict:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON for {ctx}: {exc}")
    return {}


def get_release_by_tag(api_base: str, owner: str, repo: str, tag: str, token: str) -> dict | None:
    status, body = api_request("GET", f"{api_base}/repos/{owner}/{repo}/releases/tags/{tag}", token)
    if status == 404:
        return None
    if status >= 400:
        fail(f"Failed to fetch release tag {tag}", status, body)
    return parse_json(body, f"release/{tag}")


def create_release(api_base: str, owner: str, repo: str, tag: str, title: str, notes: str, token: str) -> dict:
    payload = json.dumps({"tag_name": tag, "name": title, "body": notes}).encode()
    status, body = api_request("POST", f"{api_base}/repos/{owner}/{repo}/releases", token, data=payload, content_type="application/json")
    if status >= 400:
        fail(f"Failed to create release {tag}", status, body)
    return parse_json(body, f"create/{tag}")


def update_release(api_base: str, owner: str, repo: str, release_id: int, title: str, notes: str, token: str) -> dict:
    payload = json.dumps({"name": title, "body": notes}).encode()
    status, body = api_request("PATCH", f"{api_base}/repos/{owner}/{repo}/releases/{release_id}", token, data=payload, content_type="application/json")
    if status >= 400:
        fail(f"Failed to update release {release_id}", status, body)
    return parse_json(body, f"update/{release_id}")


def delete_release(api_base: str, owner: str, repo: str, release_id: int, token: str) -> None:
    status, body = api_request("DELETE", f"{api_base}/repos/{owner}/{repo}/releases/{release_id}", token)
    if status >= 400:
        fail(f"Failed to delete release {release_id}", status, body)


def list_assets(api_base: str, owner: str, repo: str, release_id: int, token: str) -> list[dict]:
    status, body = api_request("GET", f"{api_base}/repos/{owner}/{repo}/releases/{release_id}/assets", token)
    if status >= 400:
        fail(f"Failed to list assets for release {release_id}", status, body)
    return parse_json(body, f"assets/{release_id}")  # type: ignore[return-value]


def delete_asset(api_base: str, owner: str, repo: str, asset_id: int, token: str) -> None:
    status, body = api_request("DELETE", f"{api_base}/repos/{owner}/{repo}/releases/assets/{asset_id}", token)
    if status >= 400:
        fail(f"Failed to delete asset {asset_id}", status, body)


def upload_asset(upload_url: str, asset_path: Path, asset_name: str, token: str) -> None:
    upload_url = upload_url.split("{", 1)[0]
    data = asset_path.read_bytes()
    status, body = api_request("POST", f"{upload_url}?name={asset_name}", token, data=data, content_type="application/octet-stream")
    if status >= 400:
        fail(f"Failed to upload {asset_name}", status, body)


def publish_release_asset(
    api_base: str, owner: str, repo: str,
    tag: str, title: str, notes: str,
    asset_path: Path, asset_name: str, token: str,
    *, recreate: bool = False,
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

    for asset in list_assets(api_base, owner, repo, int(release_id), token):
        if asset.get("name") == asset_name and asset.get("id"):
            delete_asset(api_base, owner, repo, int(asset["id"]), token)
            break

    upload_asset(str(upload_url), asset_path, asset_name, token)


def find_bundle(dist_dir: Path, pwmcp_version: str) -> Path:
    expected = dist_dir / f"pwmcp-{pwmcp_version}.tar.xz"
    if not expected.exists():
        candidates = sorted(dist_dir.glob("pwmcp-*.tar.xz"))
        if not candidates:
            fail(f"No bundle in {dist_dir}. Run build-bundle.py first.")
        if len(candidates) > 1:
            fail(f"Multiple bundles in {dist_dir}: {[c.name for c in candidates]}; clean + rebuild.")
        return candidates[0]
    return expected


def load_release_toml_credentials(repo_root: Path) -> None:
    """Populate GITHUB_USERNAME / GITHUB_PUSH_PAT / GITHUB_REPO from release.toml."""
    release_toml = repo_root / "release.toml"
    if not release_toml.exists():
        return
    try:
        import tomllib
        with release_toml.open("rb") as fh:
            config = tomllib.load(fh)
        github = config.get("github", {})
        if not os.environ.get("GITHUB_USERNAME") and github.get("username"):
            os.environ["GITHUB_USERNAME"] = str(github["username"])
        if not os.environ.get("GITHUB_PUSH_PAT") and github.get("token"):
            os.environ["GITHUB_PUSH_PAT"] = str(github["token"])
        if not os.environ.get("GITHUB_REPO") and github.get("repo"):
            os.environ["GITHUB_REPO"] = str(github["repo"])
    except Exception:
        pass


def main() -> None:
    load_release_vars(RELEASE_VARS_FILE)

    repo_root = PWMCP_DIR.parent
    for env_path in [repo_root / ".env", PWMCP_DIR / ".env"]:
        load_env_file(env_path)
    load_release_toml_credentials(repo_root)

    pwmcp_version = os.environ.get("PWMCP_VERSION", "")
    if not pwmcp_version:
        fail("PWMCP_VERSION not set — run resolve-playwright-version.py first")

    token = os.environ.get("GITHUB_PUSH_PAT", "")
    if not token:
        fail("GITHUB_PUSH_PAT is required")
    owner = os.environ.get("GITHUB_USERNAME", "")
    repo = os.environ.get("GITHUB_REPO", "vbpub")
    if not owner:
        fail("GITHUB_USERNAME is required")

    bundle_path = find_bundle(DIST_DIR, pwmcp_version)
    bundle_hash = sha256(bundle_path.read_bytes()).hexdigest()
    asset_name = bundle_path.name

    api_base = "https://api.github.com"
    release_tag = f"pwmcp-v{pwmcp_version}"
    latest_tag = "pwmcp-latest"
    release_notes = f"pwmcp {pwmcp_version}"
    latest_notes = f"pwmcp (latest → {pwmcp_version})"

    publish_release_asset(
        api_base, owner, repo, release_tag,
        release_tag, release_notes,
        bundle_path, asset_name, token,
    )
    log(f"Published release {release_tag}")

    publish_release_asset(
        api_base, owner, repo, latest_tag,
        latest_tag, latest_notes,
        bundle_path, asset_name, token,
        recreate=True,
    )
    log(f"Moved {latest_tag} → {release_tag}")

    latest_url = f"https://github.com/{owner}/{repo}/releases/download/{latest_tag}/{asset_name}"
    log(f"Published pwmcp {pwmcp_version}")
    log(f"PWMCP_BUNDLE_LATEST_URL={latest_url}")
    log(f"PWMCP_BUNDLE_SHA256={bundle_hash}")
    log(f"Next step: git tag -a {release_tag} -m 'pwmcp {pwmcp_version}' && git push origin {release_tag}")


if __name__ == "__main__":
    main()
