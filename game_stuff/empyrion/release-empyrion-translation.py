#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sys
import zipfile
from datetime import datetime, timezone
import subprocess
from pathlib import Path

# ── Keystone import ──────────────────────────────────────────────────────────
# parents[2] from game_stuff/empyrion/release-empyrion-translation.py == the vbpub repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cmru" / "src"))
from cmru.release import GitHubReleases, write_sha256_sidecar

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
WORKFLOW_LOG_DEFAULT = "workflow.latest.log"


def load_cmru_credentials(repo_root: Path) -> None:
    """Populate GITHUB_USERNAME / GITHUB_REPO from cmru.toml and token from
    cmru.secret.toml.  Mirrors the pattern in tls-edge/scripts/publish-release.py.

    Credential resolution order: env > cmru.secret.toml > cmru.toml
    - Identity (owner→GITHUB_USERNAME, repo→GITHUB_REPO) comes from cmru.toml [github].
    - Token comes from env (GITHUB_PUSH_PAT / GITHUB_TOKEN) first, then
      cmru.secret.toml [github].token.  cmru.toml never contains a token.
    """
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return  # Best-effort; fallback to env vars

    def _load_toml(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with path.open("rb") as fh:
                return tomllib.load(fh)
        except Exception as exc:
            print(f"[WARN] Could not parse {path.name}: {exc}", file=sys.stderr)
            return {}

    # cmru.toml — identity only (no token)
    cmru_github = _load_toml(repo_root / "cmru.toml").get("github", {})
    if not os.environ.get("GITHUB_USERNAME") and cmru_github.get("owner"):
        os.environ["GITHUB_USERNAME"] = str(cmru_github["owner"])
    if not os.environ.get("GITHUB_REPO") and cmru_github.get("repo"):
        os.environ["GITHUB_REPO"] = str(cmru_github["repo"])

    # cmru.secret.toml — token only; never stored in cmru.toml
    secret_github = _load_toml(repo_root / "cmru.secret.toml").get("github", {})
    if not os.environ.get("GITHUB_PUSH_PAT") and not os.environ.get("GITHUB_TOKEN"):
        if secret_github.get("token"):
            os.environ["GITHUB_PUSH_PAT"] = str(secret_github["token"])


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


def publish_github_release_assets(
    metadata: dict,
    tag: str,
    release_name: str,
    assets_to_upload: list[Path],
    draft: bool,
    prerelease: bool,
) -> None:
    """Publish assets to a GitHub Release using the shared cmru keystone.

    Routes through GitHubReleases.publish() (cmru/src/cmru/release.py) so the release
    scheme stays uniform across all vbpub projects.  A .sha256 sidecar is written and
    uploaded alongside each asset for integrity verification.

    The date tag (empyrion-de-translation-YYYYMMDD) is preserved exactly as-is —
    this project does not use semver and does NOT go through publish_versioned().
    """
    token = (os.getenv("GITHUB_PUSH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    if not token:
        raise ValueError(
            "A GitHub token is required (GITHUB_PUSH_PAT or GITHUB_TOKEN).\n"
            "  Set it in the environment or in cmru.secret.toml [github].token."
        )

    owner, repo = parse_repo(metadata)
    notes = (
        "Empyrion German translation artifact release.\n\n"
        "- Includes final translated CSV files and translation report\n"
        "- Includes detailed translation trace reports\n"
        "- Installation uses local Steam library root placeholder\n"
    )

    # Build the full asset list: each zip + its .sha256 sidecar
    all_assets: list[Path] = []
    seen_names: set[str] = set()
    for asset_path in assets_to_upload:
        if asset_path.name not in seen_names:
            sidecar = write_sha256_sidecar(asset_path)
            all_assets.append(asset_path)
            all_assets.append(sidecar)
            seen_names.add(asset_path.name)
            seen_names.add(sidecar.name)

    gh = GitHubReleases(owner, repo, token)
    gh.publish(tag, release_name, notes, all_assets)
    print(f"[INFO] GitHub release published via keystone: tag={tag}")


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

    if (os.getenv("EMPYRION_PROMOTE_STEP5_FAILURES_TO_OK") or "").strip().lower() in {"1", "true", "yes", "on"}:
        cmd.insert(2, "--promote-failures-as-ok")

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
    workflow_log_path: Path | None,
) -> None:
    base_folder = "empyrion-de-translation-traces"
    with zipfile.ZipFile(artifact_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(failures_report_path, arcname=f"{base_folder}/{failures_report_path.name}")
        archive.write(success_report_path, arcname=f"{base_folder}/{success_report_path.name}")
        if workflow_log_path and workflow_log_path.exists():
            archive.write(workflow_log_path, arcname=f"{base_folder}/{WORKFLOW_LOG_DEFAULT}")


def _resolve_workflow_log_path(script_dir: Path) -> Path | None:
    env_candidates = [
        (os.getenv("EMPYRION_WORKFLOW_LOG") or "").strip(),
        (os.getenv("EMPYRION_WORKFLOW_LOG_LATEST") or "").strip(),
    ]
    for raw_path in env_candidates:
        if not raw_path:
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (script_dir / candidate).resolve()
        if candidate.exists():
            return candidate

    default_candidate = (script_dir / "reports" / WORKFLOW_LOG_DEFAULT).resolve()
    if default_candidate.exists():
        return default_candidate
    return None


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
        [
            path
            for path in output_dir.glob("empyrion-de-translation-*.zip")
            if not path.name.startswith("empyrion-de-translation-traces-")
        ],
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

    # ── Credential resolution: env > cmru.secret.toml > cmru.toml ────────────
    # parents[2] from game_stuff/empyrion/ == the vbpub repo root.
    repo_root = script_dir.parents[1]
    load_cmru_credentials(repo_root)

    metadata = load_release_metadata_from_env()
    workflow_log_path = _resolve_workflow_log_path(script_dir)
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
            workflow_log_path,
        )

        print(f"[INFO] Input dir: {input_dir}")
        print(f"[INFO] Summary report: {summary_report_path}")
        print(f"[INFO] Translation artifact: {translation_zip_path}")
        print(f"[INFO] Traces artifact: {traces_zip_path}")
        print(f"[INFO] Total changed rows: {total_changes}")
        print(f"[INFO] Failures counted from trace: {failure_count}")
        print(f"[INFO] Warnings counted from applied changes: {warnings_count}")
        if workflow_log_path and workflow_log_path.exists():
            print(f"[INFO] Workflow log included in traces zip: {workflow_log_path}")
        else:
            print("[WARN] No workflow log found; traces zip contains reports only")

    if args.publish_github:
        tag = args.tag or f"empyrion-de-translation-{date_stamp}"
        release_name = args.release_name or f"Empyrion DE Translation {date_stamp}"

        assets_to_upload: list[Path] = []
        seen_names: set[str] = set()
        for asset in [translation_zip_path, traces_zip_path]:
            if asset.name in seen_names:
                continue
            seen_names.add(asset.name)
            assets_to_upload.append(asset)

        publish_github_release_assets(
            metadata=metadata,
            tag=tag,
            release_name=release_name,
            assets_to_upload=assets_to_upload,
            draft=args.draft,
            prerelease=args.prerelease,
        )


if __name__ == "__main__":
    main()
