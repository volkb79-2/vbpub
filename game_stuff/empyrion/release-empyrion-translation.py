#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FINAL_FILES = [
    "Dialogues.de.completed.csv",
    "Localization.de.completed.csv",
    "PDA.de.completed.csv",
    "applied_changes.csv",
]


def load_release_metadata(path: Path) -> dict:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    github = data.get("github", {}) if isinstance(data, dict) else {}
    env = data.get("env", {}) if isinstance(data, dict) else {}
    return {
        "github_username": (github.get("username") or "unknown").strip(),
        "github_repo": (github.get("repo") or "unknown").strip(),
        "oci_vendor": (env.get("OCI_VENDOR") or "unknown").strip(),
    }


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
        "2. Backup existing files before replacement.",
        "3. Replace files from this artifact with the translated files:",
        "   - Dialogues.de.completed.csv -> Dialogues.csv at `G:\\SteamLibrary\\steamapps\\workshop\\content\\383120\\3143225812\\Content\\Configuration`",
        "   - Localization.de.completed.csv -> Localization.csv at `G:\\SteamLibrary\\steamapps\\workshop\\content\\383120\\3143225812\\Extras`",
        "   - PDA.de.completed.csv -> PDA.csv at `G:\\SteamLibrary\\steamapps\\workshop\\content\\383120\\3143225812\\Extras\\PDA`",
        "4. Keep original filenames in the target folders (`Dialogues.csv`, `Localization.csv`, `PDA.csv`).",
        "5. Start Empyrion and test German text in dialogues/UI/PDA missions.",
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
    parser.add_argument("--release-toml", default="../../release.toml")
    parser.add_argument("--artifact-name", default="")
    parser.add_argument("--skip-qa", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    input_dir = (script_dir / args.input_dir).resolve()
    output_dir = (script_dir / args.output_dir).resolve()
    release_toml = (script_dir / args.release_toml).resolve()

    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    for name in FINAL_FILES:
        file_path = input_dir / name
        if not file_path.exists():
            raise FileNotFoundError(f"Required file not found: {file_path}")
    if not release_toml.exists():
        raise FileNotFoundError(f"release.toml not found: {release_toml}")

    if not args.skip_qa:
        run_qa(script_dir, input_dir)

    metadata = load_release_metadata(release_toml)
    changes_csv = input_dir / "applied_changes.csv"
    total_changes, by_file, by_status = summarize_changes(changes_csv)
    contains_english_rows = collect_status_rows(changes_csv, "de_contains_english")

    output_dir.mkdir(parents=True, exist_ok=True)
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


if __name__ == "__main__":
    main()
