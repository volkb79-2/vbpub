#!/usr/bin/env python3
"""Run one-at-a-time, reversible R2 sample mutations in tester-unified."""

from __future__ import annotations

import csv
import io
import os
import signal
import subprocess
import sys
import tokenize
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools" / "r2-sample.csv"
RESULTS = ROOT / ".assay" / "r2-sample-results.csv"
FIXED_SURVIVOR_IDS = {
    "b6ceaf603e63f6ceac0186a81a26e0ca00c2bbce124635e33fe0b657f4924b68",
    "1f110e05ebffa6257df51e698c9d1f42b663063ba7e2b94f5aaac7a29ff2536d",
    "044c0da1f684a57150436e4f2636cc4cf582deec578c21af413ffc316036bc47",
    "ca036de21bb7f78c8e2d55a52a6b840c656e8e90962814b3025dbc81b69b5cf9",
    "e947df662e5f736d0426d8b057b7b72439a4f476031cce1e48931b36bab99c5b",
}
REPLACEMENTS = {
    "True->False": ("True", "False"),
    "False->True": ("False", "True"),
    "Or->And": ("or", "and"),
    "And->Or": ("and", "or"),
    "Gt->GtE": (">", ">="),
    "GtE->Gt": (">=", ">"),
    "Lt->LtE": ("<", "<="),
    "LtE->Lt": ("<=", "<"),
    "Eq->NotEq": ("==", "!="),
    "NotEq->Eq": ("!=", "=="),
}


def replace_token(source: str, line: int, old: str, new: str, occurrence: int) -> str:
    matches = [
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.start[0] == line and token.string == old
    ]
    if len(matches) < occurrence:
        raise ValueError(f"line {line} has {len(matches)} {old!r} token(s), need occurrence {occurrence}")
    token = matches[occurrence - 1]
    source_lines = source.splitlines(keepends=True)
    start = sum(len(item) for item in source_lines[: line - 1]) + token.start[1]
    end = sum(len(item) for item in source_lines[: line - 1]) + token.end[1]
    return source[:start] + new + source[end:]


def run_file(test_file: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", test_file],
        cwd=ROOT,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def main() -> int:
    with MANIFEST.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    fresh_rows = [row for row in rows if row["candidate_id"] not in FIXED_SURVIVOR_IDS]
    if len(FIXED_SURVIVOR_IDS.intersection(row["candidate_id"] for row in rows)) != 5 or len(fresh_rows) < 60:
        raise SystemExit(f"sample requires the 5 corrected mutants plus at least 60 fresh rows; got {len(rows)}")
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise SystemExit("sample candidate IDs must be unique")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []
    active_path: Path | None = None
    active_source: bytes | None = None

    def restore_active(_signum: int, _frame: object) -> None:
        if active_path is not None and active_source is not None:
            active_path.write_bytes(active_source)
        raise KeyboardInterrupt("mutation sample interrupted; active source restored")

    signal.signal(signal.SIGTERM, restore_active)
    signal.signal(signal.SIGINT, restore_active)

    try:
        test_targets = sorted({row["test_file"] for row in rows})
        for test_target in test_targets:
            result = run_file(test_target)
            if result.returncode:
                print(f"BASELINE {test_target} FAIL\n{result.stdout}", flush=True)
                return 2
            print(f"BASELINE {test_target} PASS", flush=True)

        for index, row in enumerate(rows, start=1):
            source_path = ROOT / row["path"]
            active_path = source_path
            active_source = source_path.read_bytes()
            source = active_source.decode("utf-8")
            old, new = REPLACEMENTS[row["mutation"]]
            mutated = replace_token(source, int(row["line"]), old, new, int(row["occurrence"]))
            source_path.write_text(mutated, encoding="utf-8")
            try:
                result = run_file(row["test_file"])
                failed = [line.strip() for line in result.stdout.splitlines() if line.startswith("FAILED ")]
                if result.returncode == 0:
                    status = "SURVIVED"
                elif failed:
                    status = "KILLED"
                else:
                    status = "ERROR"
                    print(f"MUTANT {row['candidate_id']} ERROR\n{result.stdout}", flush=True)
                result_row = {
                    "candidate_id": row["candidate_id"],
                    "path": row["path"],
                    "line": row["line"],
                    "mutation": row["mutation"],
                    "test_file": row["test_file"],
                    "status": status,
                    "pytest_returncode": str(result.returncode),
                    "failed_nodeids": " ; ".join(failed),
                }
                results.append(result_row)
                with RESULTS.open("w", newline="", encoding="utf-8") as output:
                    writer = csv.DictWriter(output, fieldnames=list(result_row))
                    writer.writeheader()
                    writer.writerows(results)
                failed_text = "; ".join(failed) if failed else "-"
                print(
                    f"MUTANT {index}/{len(rows)} {row['candidate_id']} {status} failed={failed_text}",
                    flush=True,
                )
            finally:
                source_path.write_bytes(active_source)
                if source_path.read_bytes() != active_source:
                    raise RuntimeError(f"failed to restore {source_path}")
                active_path = None
                active_source = None
    except KeyboardInterrupt as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 130

    killed = sum(row["status"] == "KILLED" for row in results)
    survived = sum(row["status"] == "SURVIVED" for row in results)
    errors = sum(row["status"] == "ERROR" for row in results)
    fixed_survived = [
        row["candidate_id"]
        for row in results
        if row["candidate_id"] in FIXED_SURVIVOR_IDS and row["status"] != "KILLED"
    ]
    fresh_killed = sum(row["status"] == "KILLED" and row["candidate_id"] not in FIXED_SURVIVOR_IDS for row in results)
    print(
        f"SAMPLE SUMMARY total={len(results)} corrected_killed={killed - fresh_killed} "
        f"fresh={len(fresh_rows)} fresh_killed={fresh_killed} survived={survived} errors={errors}",
        flush=True,
    )
    return 1 if errors or fixed_survived or fresh_killed < 60 else 0


if __name__ == "__main__":
    raise SystemExit(main())
