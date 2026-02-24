#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

FINAL_FILES = [
    "Dialogues.de.completed.csv",
    "Localization.de.completed.csv",
    "PDA.de.completed.csv",
]
CANONICAL_INPUT_DIR = "output-all-real"
CANONICAL_REPORTS_DIR = "reports"
SUMMARY_REPORT_NAME = "translation-report.md"
FAILURES_REPORT_NAME = "translation-failures.md"
SUCCESS_REPORT_NAME = "translation-success.md"


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


def parse_json(body: str, context: str):
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


def upload_asset(upload_url: str, asset_path: Path, token: str) -> None:
    upload_url = upload_url.split("{", 1)[0]
    status, body = api_request(
        "POST",
        f"{upload_url}?name={asset_path.name}",
        token,
        data=asset_path.read_bytes(),
        content_type="application/octet-stream",
    )
    if status >= 400:
        raise RuntimeError(f"Failed to upload asset {asset_path.name}: status={status} body={body}")


def publish_github_release_assets(
    metadata: dict,
    tag: str,
    release_name: str,
    assets_to_upload: list[Path],
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
        "- Includes final translated CSV files and translation report\n"
        "- Includes detailed translation trace reports\n"
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

    for asset_path in assets_to_upload:
        if asset_path.name in existing_assets:
            delete_asset(api_base, owner, repo, existing_assets[asset_path.name], token)
        upload_asset(upload_url, asset_path, token)


def run_qa(script_dir: Path, input_dir: Path) -> None:
    cmd = [
        sys.executable,
        str(script_dir / "qa_validate_tokens.py"),
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


def _count_failure_entries(failures_report_path: Path) -> int:
    if not failures_report_path.exists():
        return 0
    content = failures_report_path.read_text(encoding="utf-8")
    if "No errors remaining." in content:
        return 0
    return sum(1 for line in content.splitlines() if line.startswith("### "))


def _resolve_reports(script_dir: Path) -> tuple[Path, Path]:
    reports_dir = script_dir / CANONICAL_REPORTS_DIR
    failures_report_path = reports_dir / FAILURES_REPORT_NAME
    success_path = reports_dir / SUCCESS_REPORT_NAME

    if not failures_report_path.exists():
        failures_report_path.parent.mkdir(parents=True, exist_ok=True)
        failures_report_path.write_text(
            "# Translation Failures Trace\n\nNo errors remaining.\n",
            encoding="utf-8",
        )

    if not success_path.exists():
        success_path.parent.mkdir(parents=True, exist_ok=True)
        success_path.write_text(
            "# Translation Success Trace\n\nNo success trace available. Run translate-mt first.\n",
            encoding="utf-8",
        )

    return failures_report_path, success_path


def write_summary_report(
    report_path: Path,
    metadata: dict,
    total_changes: int,
    by_file: dict[str, int],
    by_status: dict[str, int],
    input_dir: Path,
    failure_count: int,
    warnings_count: int,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Empyrion Translation Summary Report",
        "",
        f"- Generated (UTC): {ts}",
        f"- Repository: {metadata['github_username']}/{metadata['github_repo']}",
        f"- Vendor: {metadata['oci_vendor']}",
        f"- Input directory: {input_dir}",
        "",
        "## Packaging Overview",
        "",
        f"- failures: **{failure_count}**",
        f"- warnings: **{warnings_count}**",
        f"- total changed rows: **{total_changes}**",
        "",
        "## Changed Rows by File",
        "",
    ]
    for name, count in sorted(by_file.items()):
        lines.append(f"- {name}: {count}")

    lines.extend(["", "## Changed Rows by Status", ""])
    for name, count in sorted(by_status.items()):
        lines.append(f"- {name}: {count}")

    lines.extend([
        "",
        "## Included Artifacts",
        "",
        "- Dialogues.de.completed.csv",
        "- Localization.de.completed.csv",
        "- PDA.de.completed.csv",
        "- translation-success.md",
        "- translation-failures.md",
        "",
        "## Notes",
        "",
        "- `translation-failures.md` contains full failure trace diagnostics.",
        "- `translation-success.md` contains success trace diagnostics in the traces bundle.",
    ])

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_translation_zip(
    artifact_path: Path,
    input_dir: Path,
    summary_report_path: Path,
    failures_report_path: Path,
) -> None:
    base_folder = "empyrion-de-translation"
    with zipfile.ZipFile(artifact_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FINAL_FILES:
            archive.write(input_dir / name, arcname=f"{base_folder}/{name}")
        archive.write(summary_report_path, arcname=f"{base_folder}/{summary_report_path.name}")
        archive.write(failures_report_path, arcname=f"{base_folder}/{failures_report_path.name}")


def create_traces_zip(
    artifact_path: Path,
    failures_report_path: Path,
    success_report_path: Path,
) -> None:
    base_folder = "empyrion-de-translation-traces"
    with zipfile.ZipFile(artifact_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(failures_report_path, arcname=f"{base_folder}/{failures_report_path.name}")
        archive.write(success_report_path, arcname=f"{base_folder}/{success_report_path.name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Empyrion DE translation release artifacts")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--skip-qa", action="store_true")
    parser.add_argument("--publish-github", action="store_true")
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument("--tag", default="")
    parser.add_argument("--release-name", default="")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--draft", action="store_true")
    return parser.parse_args()


def find_latest_artifacts(output_dir: Path) -> tuple[Path, Path]:
    translation_zips = sorted(
        output_dir.glob("empyrion-de-translation-*.zip"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    traces_zips = sorted(
        output_dir.glob("empyrion-de-translation-traces-*.zip"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    if not translation_zips:
        raise FileNotFoundError(f"No translation artifact zip found in {output_dir}. Run build first.")
    if not traces_zips:
        raise FileNotFoundError(f"No traces artifact zip found in {output_dir}. Run build first.")

    return translation_zips[0], traces_zips[0]


def main() -> None:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    input_dir = (script_dir / CANONICAL_INPUT_DIR).resolve()
    output_dir = (script_dir / args.output_dir).resolve()

    if args.publish_only and not args.publish_github:
        raise ValueError("--publish-only requires --publish-github")

    metadata = load_release_metadata_from_env()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.publish_only:
        translation_zip_path, traces_zip_path = find_latest_artifacts(output_dir)
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        print(f"[INFO] Publish-only mode: using translation artifact {translation_zip_path}")
        print(f"[INFO] Publish-only mode: using traces artifact {traces_zip_path}")
    else:
        if not input_dir.exists():
            raise FileNotFoundError(f"Input directory not found: {input_dir}")

        required_for_build = [*FINAL_FILES, "applied_changes.csv"]
        for name in required_for_build:
            file_path = input_dir / name
            if not file_path.exists():
                raise FileNotFoundError(f"Required file not found: {file_path}")

        if not args.skip_qa:
            run_qa(script_dir, input_dir)

        changes_csv = input_dir / "applied_changes.csv"
        total_changes, by_file, by_status = summarize_changes(changes_csv)

        failures_report_path, success_report_path = _resolve_reports(script_dir)
        failure_count = _count_failure_entries(failures_report_path)
        warnings_count = int(by_status.get("de_contains_english", 0))

        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        summary_report_path = output_dir / SUMMARY_REPORT_NAME
        translation_zip_path = output_dir / f"empyrion-de-translation-{date_stamp}.zip"
        traces_zip_path = output_dir / f"empyrion-de-translation-traces-{date_stamp}.zip"

        write_summary_report(
            summary_report_path,
            metadata,
            total_changes,
            by_file,
            by_status,
            input_dir,
            failure_count,
            warnings_count,
        )
        create_translation_zip(
            translation_zip_path,
            input_dir,
            summary_report_path,
            failures_report_path,
        )
        create_traces_zip(
            traces_zip_path,
            failures_report_path,
            success_report_path,
        )

        print(f"[INFO] Input dir: {input_dir}")
        print(f"[INFO] Summary report: {summary_report_path}")
        print(f"[INFO] Translation artifact: {translation_zip_path}")
        print(f"[INFO] Traces artifact: {traces_zip_path}")
        print(f"[INFO] Total changed rows: {total_changes}")
        print(f"[INFO] Failures counted from trace: {failure_count}")
        print(f"[INFO] Warnings counted from applied changes: {warnings_count}")

    if args.publish_github:
        tag = args.tag or f"empyrion-de-translation-{date_stamp}"
        release_name = args.release_name or f"Empyrion DE Translation {date_stamp}"
        publish_github_release_assets(
            metadata=metadata,
            tag=tag,
            release_name=release_name,
            assets_to_upload=[translation_zip_path, traces_zip_path],
            draft=args.draft,
            prerelease=args.prerelease,
        )
        print(f"[INFO] GitHub release published: tag={tag}")


if __name__ == "__main__":
    main()
