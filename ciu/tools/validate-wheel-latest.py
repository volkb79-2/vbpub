#!/usr/bin/env python3
"""Validate CIU latest wheel asset from GitHub Releases."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen


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
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def api_request(url: str, token: Optional[str]) -> Dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        print(f"[ERROR] GitHub API error: {exc.code}", file=sys.stderr)
        if body:
            print(f"[ERROR] {body}", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent

    env_file = Path(os.getenv("CIU_ENV_FILE", repo_root / ".env"))
    if env_file.exists():
        load_env_file(env_file)
    else:
        fallback_env = script_dir.parent / ".env"
        if fallback_env.exists():
            load_env_file(fallback_env)

    owner = os.getenv("GITHUB_USERNAME")
    repo = os.getenv("GITHUB_REPO")
    if not owner or not repo:
        print("[ERROR] GITHUB_USERNAME and GITHUB_REPO are required", file=sys.stderr)
        raise SystemExit(1)

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_PUSH_PAT")
    latest_tag = os.getenv("CIU_LATEST_TAG", "ciu-wheel-latest")

    import tomllib

    project_root = script_dir.parent
    data = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    project_meta = data.get("project", {})
    version = project_meta.get("version")

    if not version:
        dynamic_meta = data.get("tool", {}).get("setuptools", {}).get("dynamic", {})
        version_attr = dynamic_meta.get("version", {}).get("attr")
        if not version_attr:
            print("[ERROR] Unable to resolve project version from pyproject.toml", file=sys.stderr)
            raise SystemExit(1)

        module_path, _, attr_name = version_attr.rpartition(".")
        if not module_path or not attr_name:
            print(f"[ERROR] Invalid dynamic version attr: {version_attr}", file=sys.stderr)
            raise SystemExit(1)

        sys.path.insert(0, str(project_root / "src"))
        try:
            module = __import__(module_path, fromlist=[attr_name])
        except ImportError as exc:
            print(f"[ERROR] Unable to import {module_path} for version lookup: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

        version = getattr(module, attr_name, None)
        if not version:
            print("[ERROR] Dynamic version attribute resolved to empty", file=sys.stderr)
            raise SystemExit(1)

    asset_name = os.getenv("CIU_LATEST_ASSET_NAME", f"ciu-{version}-py3-none-any.whl")

    api_base = "https://api.github.com"
    release = api_request(f"{api_base}/repos/{owner}/{repo}/releases/tags/{latest_tag}", token)
    assets = release.get("assets", [])

    asset = next((item for item in assets if item.get("name") == asset_name), None)
    if not asset:
        print(f"[ERROR] Latest release missing asset: {asset_name}", file=sys.stderr)
        raise SystemExit(1)

    download_url = asset.get("browser_download_url")
    if not download_url:
        print("[ERROR] Latest asset missing download URL", file=sys.stderr)
        raise SystemExit(1)

    print("[INFO] CIU wheel latest release asset found")
    print(f"[INFO] CIU_WHEEL_LATEST_URL={download_url}")


if __name__ == "__main__":
    main()
