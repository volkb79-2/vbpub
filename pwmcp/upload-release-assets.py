#!/usr/bin/env python3
"""Upload Playwright MCP bundle to GitHub releases (config-driven)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import tomllib


def load_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def ensure_env(release_config: Path) -> tuple[str, str, str]:
    config = load_toml(release_config)
    github = config.get("github") or {}
    username = (github.get("username") or "").strip()
    repo = (github.get("repo") or "").strip()
    token = (github.get("token") or "").strip()

    if not username or not repo:
        raise ValueError("github.username and github.repo are required in release config")
    if not token:
        raise ValueError("github.token is required in release config")

    os.environ.setdefault("GITHUB_USERNAME", username)
    os.environ.setdefault("GITHUB_REPO", repo)
    os.environ.setdefault("GITHUB_PUSH_PAT", token)
    os.environ.setdefault("GH_TOKEN", token)

    return username, repo, token


def main() -> None:
    root = Path(__file__).resolve().parent
    repo_root = root.parent

    release_config = Path(os.getenv("RELEASE_MANAGER_CONFIG", repo_root / "release.toml")).resolve()
    bundle_config = root / "bundle.toml"

    owner, repo, _ = ensure_env(release_config)
    bundle = load_toml(bundle_config)

    archive = bundle.get("archive") or {}
    name_template = archive.get("name_template") or "bundle-{version}.tar.gz"
    version_env = archive.get("version_env") or "VERSION"
    fallback_env = archive.get("fallback_env") or "BUILD_DATE"

    version = os.getenv(version_env) or os.getenv(fallback_env)
    if not version:
        raise ValueError(f"{version_env} or {fallback_env} must be set")

    dist_dir = resolve_path(root, bundle.get("dist_dir") or "dist")
    bundle_name = (bundle.get("release_assets") or {}).get("bundle_name") or "bundle"
    notes = (bundle.get("release_assets") or {}).get("notes") or ""
    notes_latest = (bundle.get("release_assets") or {}).get("notes_latest") or ""
    legacy_assets = (bundle.get("release_assets") or {}).get("legacy_assets") or []

    tag = f"{bundle_name}-{version}"
    latest_tag = f"{bundle_name}-latest"

    archive_name = name_template.format(version=version)
    archive_path = dist_dir / archive_name
    if not archive_path.exists():
        raise FileNotFoundError(f"Missing bundle archive: {archive_path}")

    repo_slug = f"{owner}/{repo}"

    def gh(*args: str) -> None:
        subprocess.run(["gh", *args], check=True)

    def gh_capture(*args: str) -> str:
        result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def list_release_assets(release_tag: str) -> set[str]:
        raw = gh_capture("release", "view", release_tag, "--repo", repo_slug, "--json", "assets")
        if not raw:
            return set()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return set()
        assets = data.get("assets") or []
        return {asset.get("name", "") for asset in assets if asset.get("name")}

    def delete_legacy_assets(release_tag: str) -> None:
        if not legacy_assets:
            return
        existing = list_release_assets(release_tag)
        for asset_name in legacy_assets:
            if asset_name not in existing:
                continue
            try:
                gh("release", "delete-asset", release_tag, asset_name, "--repo", repo_slug, "--yes")
            except subprocess.CalledProcessError:
                print(f"[WARN] Legacy asset not removed: {release_tag}/{asset_name}")

    rendered_notes = notes.format(tag=tag, bundle_name=bundle_name)
    rendered_latest = notes_latest.format(tag=latest_tag, bundle_name=bundle_name)

    try:
        gh("release", "view", tag, "--repo", repo_slug)
    except subprocess.CalledProcessError:
        gh(
            "release",
            "create",
            tag,
            "--repo",
            repo_slug,
            "--title",
            tag,
            "--notes",
            rendered_notes,
            str(archive_path),
        )
    else:
        delete_legacy_assets(tag)
        gh("release", "upload", tag, "--repo", repo_slug, "--clobber", str(archive_path))

    try:
        gh("release", "view", latest_tag, "--repo", repo_slug)
    except subprocess.CalledProcessError:
        pass
    else:
        delete_legacy_assets(latest_tag)
        gh("release", "delete", latest_tag, "--repo", repo_slug, "--yes")

    gh(
        "release",
        "create",
        latest_tag,
        "--repo",
        repo_slug,
        "--title",
        latest_tag,
        "--notes",
        rendered_latest,
        str(archive_path),
    )


if __name__ == "__main__":
    main()
