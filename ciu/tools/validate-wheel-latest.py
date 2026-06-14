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
    # The moving pointer carries whatever version was last published; validate by
    # presence of a wheel asset, not by reconstructing a filename from a version.
    latest_tag = os.getenv("CIU_LATEST_TAG", "ciu-latest")

    api_base = "https://api.github.com"
    release = api_request(f"{api_base}/repos/{owner}/{repo}/releases/tags/{latest_tag}", token)
    wheels = [a for a in release.get("assets", []) if str(a.get("name", "")).endswith(".whl")]
    if not wheels:
        print(f"[ERROR] Release '{latest_tag}' has no .whl asset", file=sys.stderr)
        raise SystemExit(1)

    asset = wheels[0]
    download_url = asset.get("browser_download_url")
    if not download_url:
        print("[ERROR] Latest asset missing download URL", file=sys.stderr)
        raise SystemExit(1)

    print(f"[INFO] CIU latest asset: {asset.get('name')}")
    print(f"[INFO] CIU_WHEEL_LATEST_URL={download_url}")


if __name__ == "__main__":
    main()
