from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_SRC = ROOT / "libraries" / "patchseries" / "src"
PROVENANCE = ROOT / "wings-patchstack" / "scripts" / "provenance.py"
sys.path.insert(0, str(LIBRARY_SRC))


def _write_series(path: Path, subject: str) -> Path:
    path.mkdir(parents=True)
    (path / "0001-change.patch").write_text(
        "From 0123456789abcdef0123456789abcdef01234567 Mon Sep 17 00:00:00 2001\n"
        "From: Author <author@example.test>\n"
        "Subject: [PATCH] "
        + subject
        + "\n\n"
        + subject
        + " body.\n\n"
        "Change-Id: change\n"
        "Capability: placement\n"
        "Role: enable\n"
        "Risk: low\n"
        "Requires: none\n\n"
        "---\n"
        "diff --git a/pkg/file.go b/pkg/file.go\n"
        "--- a/pkg/file.go\n"
        "+++ b/pkg/file.go\n"
        "@@ -1 +1 @@\n"
        "-before\n"
        "+"
        + subject
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    path = tmp_path / "image-ledger.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "legacy_unrecorded": [
                    {
                        "upstream": "1.13.3",
                        "suffix": "cgroup.4",
                        "reason": "legacy floor",
                    }
                ],
                "current_series": {
                    "upstream_version": "1.13.3",
                    "upstream_commit": "a" * 40,
                    "series_digest": "b" * 64,
                },
                "builds": [],
                "reservations": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _command(
    ledger: Path,
    command: str,
    *arguments: str,
) -> list[str]:
    return [
        sys.executable,
        str(PROVENANCE),
        command,
        "--ledger",
        str(ledger),
        *arguments,
    ]


def _reserve(ledger: Path, series: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(
            ledger,
            "reserve",
            "--series",
            str(series),
            "--upstream-version",
            "1.13.3",
            "--upstream-commit",
            "a" * 40,
        ),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(LIBRARY_SRC)},
    )


def test_concurrent_distinct_builds_reserve_different_suffixes(
    tmp_path: Path, ledger: Path
) -> None:
    series_a = _write_series(tmp_path / "series-a", "first input")
    series_b = _write_series(tmp_path / "series-b", "second input")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_reserve, ledger, series_a),
            executor.submit(_reserve, ledger, series_b),
        ]
        results = [future.result() for future in futures]

    assert [result.returncode for result in results] == [0, 0]
    reservations = [result.stdout.rstrip("\n").split("\t") for result in results]
    assert {item[1] for item in reservations} == {"cgroup.5", "cgroup.6"}
    assert len({item[5] for item in reservations}) == 2
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert {item["suffix"] for item in persisted["reservations"]} == {
        "cgroup.5",
        "cgroup.6",
    }


def test_same_digest_cannot_have_two_active_tag_reservations(
    tmp_path: Path, ledger: Path
) -> None:
    series = _write_series(tmp_path / "series", "same input")

    first = _reserve(ledger, series)
    second = _reserve(ledger, series)

    assert first.returncode == 0
    assert second.returncode == 2
    assert "active reservation" in second.stderr


def test_record_consumes_reservation_and_release_frees_failed_allocation(
    tmp_path: Path, ledger: Path
) -> None:
    series = _write_series(tmp_path / "series", "first input")
    reserved = _reserve(ledger, series)
    assert reserved.returncode == 0
    fields = reserved.stdout.rstrip("\n").split("\t")
    _, suffix, digest, _, _, reservation_id = fields

    recorded = subprocess.run(
        _command(
            ledger,
            "record",
            "--upstream-version",
            "1.13.3",
            "--suffix",
            suffix,
            "--series-digest",
            digest,
            "--reservation-id",
            reservation_id,
            "--image-id",
            "sha256:" + "b" * 64,
            "--built-at",
            "2030-01-01T00:00:00Z",
        ),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(LIBRARY_SRC)},
    )
    assert recorded.returncode == 0, recorded.stderr
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["reservations"] == []
    assert persisted["builds"][0]["suffix"] == suffix
    assert persisted["builds"][0]["image_label_series_digest"] == digest

    failed = _reserve(ledger, _write_series(tmp_path / "failed", "failed build"))
    assert failed.returncode == 0
    failed_fields = failed.stdout.rstrip("\n").split("\t")
    released = subprocess.run(
        _command(
            ledger,
            "release",
            "--reservation-id",
            failed_fields[5],
        ),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(LIBRARY_SRC)},
    )
    assert released.returncode == 0, released.stderr
    retry = _reserve(
        ledger, _write_series(tmp_path / "retry", "different failed build")
    )
    assert retry.returncode == 0
    assert retry.stdout.split("\t")[1] == failed_fields[1]
