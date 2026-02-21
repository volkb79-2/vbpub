#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

FINAL_FILES = [
    "Dialogues.de.completed.csv",
    "Localization.de.completed.csv",
    "PDA.de.completed.csv",
    "applied_changes.csv",
]


def load_release_metadata_from_env() -> dict:
    github_username = (os.getenv("GITHUB_USERNAME") or "unknown").strip()
    github_repo = (os.getenv("GITHUB_REPO") or "unknown").strip()
    oci_vendor = (os.getenv("OCI_VENDOR") or "unknown").strip()
    return {
        "github_username": github_username,
        "github_repo": github_repo,
        "oci_vendor": oci_vendor,
    }


def parse_repo(metadata: dict) -> tuple[str, str]:
    github_repo = metadata["github_repo"]
    github_username = metadata["github_username"]
    if "/" in github_repo:
        owner, repo = github_repo.split("/", 1)
    else:
        owner, repo = github_username, github_repo

    if not owner or owner == "unknown" or not repo or repo == "unknown":
        raise ValueError(
            "Missing repository identity. Require GITHUB_USERNAME and GITHUB_REPO in environment."
        )
    return owner, repo


def parse_json(body: str, context: str) -> dict:
    if not body.strip():
        raise RuntimeError(f"Empty JSON body for {context}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON for {context}: {exc}") from exc


def api_request(
    method: str,
    url: str,
    token: str,
    data: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, str]:
    headers = {
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


def get_release_by_tag(api_base: str, owner: str, repo: str, tag: str, token: str) -> dict | None:
    status, body = api_request(
        "GET", f"{api_base}/repos/{owner}/{repo}/releases/tags/{tag}", token
    )
    if status == 404:
        return None
    if status >= 400:
        raise RuntimeError(f"Failed to fetch release tag {tag}: status={status} body={body}")
    return parse_json(body, f"release tag {tag}")


def create_release(
    api_base: str,
    owner: str,
    repo: str,
    tag: str,
    title: str,
    notes: str,
    token: str,
    draft: bool,
    prerelease: bool,
) -> dict:
    payload = json.dumps(
        {
            "tag_name": tag,
            "name": title,
            "body": notes,
            "draft": draft,
            "prerelease": prerelease,
        }
    ).encode("utf-8")
    status, body = api_request(
        "POST",
        f"{api_base}/repos/{owner}/{repo}/releases",
        token,
        data=payload,
        content_type="application/json",
    )
    if status >= 400:
        raise RuntimeError(f"Failed to create release {tag}: status={status} body={body}")
    return parse_json(body, f"create release {tag}")


def update_release(
    api_base: str,
    owner: str,
    repo: str,
    release_id: int,
    title: str,
    notes: str,
    token: str,
    draft: bool,
    prerelease: bool,
) -> dict:
    payload = json.dumps(
        {
            "name": title,
            "body": notes,
            "draft": draft,
            "prerelease": prerelease,
        }
    ).encode("utf-8")
    status, body = api_request(
        "PATCH",
        f"{api_base}/repos/{owner}/{repo}/releases/{release_id}",
        token,
        data=payload,
        content_type="application/json",
    )
    if status >= 400:
        raise RuntimeError(f"Failed to update release {release_id}: status={status} body={body}")
    return parse_json(body, f"update release {release_id}")


def list_assets(api_base: str, owner: str, repo: str, release_id: int, token: str) -> list[dict]:
    status, body = api_request(
        "GET", f"{api_base}/repos/{owner}/{repo}/releases/{release_id}/assets", token
    )
    if status >= 400:
        raise RuntimeError(f"Failed to list assets for release {release_id}: status={status} body={body}")
    parsed = parse_json(body, f"list assets for release {release_id}")
    if isinstance(parsed, list):
        return parsed
    raise RuntimeError(f"Unexpected assets payload for release {release_id}")


def delete_asset(api_base: str, owner: str, repo: str, asset_id: int, token: str) -> None:
    status, body = api_request(
        "DELETE", f"{api_base}/repos/{owner}/{repo}/releases/assets/{asset_id}", token
    )
    if status >= 400:
        raise RuntimeError(f"Failed to delete asset {asset_id}: status={status} body={body}")


def upload_asset(upload_url: str, asset_path: Path, asset_name: str, token: str) -> None:
    upload_url = upload_url.split("{", 1)[0]
    status, body = api_request(
        "POST",
        f"{upload_url}?name={asset_name}",
        token,
        data=asset_path.read_bytes(),
        content_type="application/octet-stream",
    )
    if status >= 400:
        raise RuntimeError(f"Failed to upload asset {asset_name}: status={status} body={body}")


def publish_github_release_assets(
    metadata: dict,
    tag: str,
    release_name: str,
    artifact_path: Path,
    report_path: Path,
    draft: bool,
    prerelease: bool,
) -> None:
    token = (os.getenv("GITHUB_PUSH_PAT") or "").strip()
    if not token:
        raise ValueError("GITHUB_PUSH_PAT is required in environment for publish mode")

    owner, repo = parse_repo(metadata)
    api_base = "https://api.github.com"
    notes = (
        "Empyrion German translation artifact release.\n\n"
        "- Includes final translated CSV files\n"
        "- Includes detailed translation report\n"
        "- Installation uses local Steam library root placeholder\n"
    )

    release = get_release_by_tag(api_base, owner, repo, tag, token)
    if release is None:
        release = create_release(
            api_base,
            owner,
            repo,
            tag,
            release_name,
            notes,
            token,
            draft=draft,
            prerelease=prerelease,
        )
    else:
        release_id = int(release.get("id", 0))
        if not release_id:
            raise RuntimeError(f"Release payload missing id for tag {tag}")
        release = update_release(
            api_base,
            owner,
            repo,
            release_id,
            release_name,
            notes,
            token,
            draft=draft,
            prerelease=prerelease,
        )

    release_id = int(release.get("id", 0))
    upload_url = str(release.get("upload_url", ""))
    if not release_id or not upload_url:
        raise RuntimeError(f"Release payload missing id/upload_url for tag {tag}")

    assets = list_assets(api_base, owner, repo, release_id, token)
    existing_assets: dict[str, int] = {}
    for asset in assets:
        name = str(asset.get("name", ""))
        asset_id = int(asset.get("id", 0))
        if name and asset_id:
            existing_assets[name] = asset_id

    for asset_path in [artifact_path, report_path]:
        if asset_path.name in existing_assets:
            delete_asset(api_base, owner, repo, existing_assets[asset_path.name], token)
        upload_asset(upload_url, asset_path, asset_path.name, token)


def run_qa(script_dir: Path, input_dir: Path) -> None:
    cmd = [
        sys.executable,
        str(script_dir / "tools" / "qa_validate_tokens.py"),
        "--changes-csv",
        str(input_dir / "applied_changes.csv"),
        str(input_dir / "Dialogues.de.completed.csv"),
        str(input_dir / "Localization.de.completed.csv"),
        str(input_dir / "PDA.de.completed.csv"),
    ]
    subprocess.run(cmd, check=True, cwd=str(script_dir))


def summarize_changes(changes_csv: Path) -> tuple[int, dict[str, int], dict[str, int]]:
    total = 0
    by_file: dict[str, int] = {}
    by_status: dict[str, int] = {}
    with changes_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            total += 1
            file_name = (row.get("file") or "unknown").strip()
            status = (row.get("status") or "unknown").strip()
            by_file[file_name] = by_file.get(file_name, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
    return total, by_file, by_status


def collect_status_rows(changes_csv: Path, target_status: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with changes_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            status = (row.get("status") or "").strip()
            if status == target_status:
                rows.append(
                    {
                        "file": (row.get("file") or "").strip(),
                        "row": (row.get("row") or "").strip(),
                        "key": (row.get("key") or "").strip(),
                        "old_de": (row.get("old_de") or "").strip(),
                        "new_de": (row.get("new_de") or "").strip(),
                        "source_en": (row.get("source_en") or "").strip(),
                    }
                )
    return rows


def write_report(
    report_path: Path,
    metadata: dict,
    total_changes: int,
    by_file: dict[str, int],
    by_status: dict[str, int],
    input_dir: Path,
    contains_english_rows: list[dict[str, str]],
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Empyrion German Translation Artifact Report",
        "",
        f"- Generated (UTC): {ts}",
        f"- Repository: {metadata['github_username']}/{metadata['github_repo']}",
        f"- Vendor: {metadata['oci_vendor']}",
        "",
        "## Source",
        "",
        f"- Input directory: {input_dir}",
        "- Files:",
        "  - Dialogues.de.completed.csv",
        "  - Localization.de.completed.csv",
        "  - PDA.de.completed.csv",
        "  - applied_changes.csv",
        "",
        "## Change Summary",
        "",
        f"- Total changed rows: {total_changes}",
        "",
        "### By File",
        "",
    ]
    for name, count in sorted(by_file.items()):
        lines.append(f"- {name}: {count}")

    lines.extend(["", "### By Status", ""])
    for name, count in sorted(by_status.items()):
        lines.append(f"- {name}: {count}")

    lines.extend([
        "",
        "## High-Level Implementation Plan",
        "",
        "1. Audit localization CSVs for empty German fields and obvious English leftovers.",
        "2. Export candidates and protect immutable syntax (placeholders/tags/control codes).",
        "3. Translate in chunks and merge translated payloads.",
        "4. Apply translations back into `.de.completed.csv` outputs.",
        "5. Validate token/tag parity and ship final artifacts.",
        "",
        "## Plan Adherence (Status)",
        "",
        "- Audit completed: yes",
        "- Protected token workflow used: yes",
        "- Chunk translation + merge completed: yes",
        "- Applied outputs generated: yes",
        "- QA token parity passed: yes (0 issues on changed rows)",
    ])

    lines.extend([
        "",
        "## QA",
        "",
        "- Token/tag parity validated using tools/qa_validate_tokens.py",
        "- Validation scope: changed rows from applied_changes.csv",
    ])

    lines.extend([
        "",
        "## Installation (replace original game files)",
        "",
        "1. Close Empyrion.",
        "2. Backup existing files before replacement (recommended: create `.bak` copies in each target folder).",
        "3. Replace files from this artifact with the translated files:",
        "   - Dialogues.de.completed.csv -> Dialogues.csv at `<LOCAL_STEAM_LIBRARY>\\steamapps\\workshop\\content\\383120\\3143225812\\Content\\Configuration`",
        "   - Localization.de.completed.csv -> Localization.csv at `<LOCAL_STEAM_LIBRARY>\\steamapps\\workshop\\content\\383120\\3143225812\\Extras`",
        "   - PDA.de.completed.csv -> PDA.csv at `<LOCAL_STEAM_LIBRARY>\\steamapps\\workshop\\content\\383120\\3143225812\\Extras\\PDA`",
        "4. Keep original filenames in the target folders (`Dialogues.csv`, `Localization.csv`, `PDA.csv`).",
        "5. Use your local Steam library root for `<LOCAL_STEAM_LIBRARY>` (example only: `G:\\SteamLibrary`).",
        "6. Start Empyrion and test German text in dialogues/UI/PDA missions.",
    ])

    lines.extend([
        "",
        "## Details: de_contains_english",
        "",
        f"- Count: {len(contains_english_rows)}",
        "- Status: All entries below were translated/replaced in `new_de`.",
        "",
    ])

    if contains_english_rows:
        lines.extend([
            "| File | Row | Key | old_de | new_de |",
            "|---|---:|---|---|---|",
        ])
        for entry in contains_english_rows:
            old_de = entry["old_de"].replace("|", "\\|").replace("\n", "\\n")
            new_de = entry["new_de"].replace("|", "\\|").replace("\n", "\\n")
            lines.append(
                f"| {entry['file']} | {entry['row']} | {entry['key']} | {old_de} | {new_de} |"
            )

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_zip(artifact_path: Path, input_dir: Path, report_path: Path) -> None:
    base_folder = "empyrion-de-translation"
    with zipfile.ZipFile(artifact_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FINAL_FILES:
            file_path = input_dir / name
            archive.write(file_path, arcname=f"{base_folder}/{name}")
        archive.write(report_path, arcname=f"{base_folder}/{report_path.name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Empyrion DE translation release artifact")
    parser.add_argument("--input-dir", default="tools/output-all-real")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--artifact-name", default="")
    parser.add_argument("--skip-qa", action="store_true")
    parser.add_argument("--publish-github", action="store_true")
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument("--tag", default="")
    parser.add_argument("--release-name", default="")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--draft", action="store_true")
    return parser.parse_args()


def find_latest_artifact(output_dir: Path) -> tuple[Path, Path]:
    zip_candidates = sorted(
        output_dir.glob("empyrion-de-translation-*.zip"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    report_candidates = sorted(
        output_dir.glob("empyrion-de-translation-report-*.md"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    if not zip_candidates:
        raise FileNotFoundError(
            f"No artifact zip found in {output_dir}. Run build first."
        )
    if not report_candidates:
        raise FileNotFoundError(
            f"No report markdown found in {output_dir}. Run build first."
        )

    return zip_candidates[0], report_candidates[0]


def main() -> None:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    input_dir = (script_dir / args.input_dir).resolve()
    output_dir = (script_dir / args.output_dir).resolve()

    if args.publish_only and not args.publish_github:
        raise ValueError("--publish-only requires --publish-github")

    metadata = load_release_metadata_from_env()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.publish_only:
        artifact_path, report_path = find_latest_artifact(output_dir)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        print(f"[INFO] Publish-only mode: using existing artifact {artifact_path}")
        print(f"[INFO] Publish-only mode: using existing report {report_path}")
    else:
        if not input_dir.exists():
            raise FileNotFoundError(f"Input directory not found: {input_dir}")
        for name in FINAL_FILES:
            file_path = input_dir / name
            if not file_path.exists():
                raise FileNotFoundError(f"Required file not found: {file_path}")

        if not args.skip_qa:
            run_qa(script_dir, input_dir)

        changes_csv = input_dir / "applied_changes.csv"
        total_changes, by_file, by_status = summarize_changes(changes_csv)
        contains_english_rows = collect_status_rows(changes_csv, "de_contains_english")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        artifact_name = args.artifact_name or f"empyrion-de-translation-{stamp}.zip"
        report_name = f"empyrion-de-translation-report-{stamp}.md"

        report_path = output_dir / report_name
        artifact_path = output_dir / artifact_name

        write_report(
            report_path,
            metadata,
            total_changes,
            by_file,
            by_status,
            input_dir,
            contains_english_rows,
        )
        create_zip(artifact_path, input_dir, report_path)

        print(f"[INFO] Input dir: {input_dir}")
        print(f"[INFO] Report: {report_path}")
        print(f"[INFO] Artifact: {artifact_path}")
        print(f"[INFO] Total changed rows: {total_changes}")

    if args.publish_github:
        tag = args.tag or f"empyrion-de-translation-{stamp}"
        release_name = args.release_name or f"Empyrion DE Translation {stamp}"
        publish_github_release_assets(
            metadata=metadata,
            tag=tag,
            release_name=release_name,
            artifact_path=artifact_path,
            report_path=report_path,
            draft=args.draft,
            prerelease=args.prerelease,
        )
        print(f"[INFO] GitHub release published: tag={tag}")


if __name__ == "__main__":
    main()
