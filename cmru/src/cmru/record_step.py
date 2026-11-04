"""Optional rigor record step (rigor-evidence design C-1).

A project that declares ``[project.release].record_step`` gets a command run after the
artifact build and before ``push``. cmru stays agnostic: it hands the command protected
inputs (release identity, an artifact inventory, the gate evidence directory and an empty
transaction-private output directory), accepts exactly two output files, checks their
size, and passes the validated paths to the push step. It never parses a record and never
commits one. A project that declares nothing never reaches this module.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from cmru.errors import RefusedBeforeChange

#: Environment names cmru owns for the record step (inputs) and the push step (outputs).
RECORD_ENV_KEYS = (
    "CMRU_RECORD_OUT",
    "CMRU_BUILD_INVENTORY",
    "CMRU_GATE_EVIDENCE_DIR",
    "CMRU_RECORD_JSON",
    "CMRU_RECORD_BODY",
)
RECORD_JSON_NAME = "record.json"
RECORD_BODY_NAME = "release-body.md"
RECORD_JSON_MAX_BYTES = 64 * 1024
RECORD_BODY_MAX_BYTES = 8 * 1024
INVENTORY_NAME = "build-inventory.json"


@contextmanager
def private_record_dir() -> Iterator[Path]:
    """A mode-0700 directory outside the release worktree, removed on exit."""
    with tempfile.TemporaryDirectory(prefix="cmru-record-") as raw:
        root = Path(raw)
        root.chmod(0o700)
        (root / "out").mkdir(mode=0o700)
        yield root


def write_inventory(
    private: Path, *, project: str, tag: str, commit: str,
    artifacts: list[dict[str, Any]],
) -> Path:
    """Write the artifact inventory (``release.json`` shape) outside the worktree."""
    path = private / INVENTORY_NAME
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "cmru-release-inventory",
                "project": project,
                "tag": tag,
                "source_commit": commit,
                "artifacts": artifacts,
            },
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return path


def first_evidence_directory(project_root: Path, evidence_paths: tuple[str, ...]) -> str | None:
    """The first declared evidence path that is a real directory, else ``None``."""
    for raw in evidence_paths:
        candidate = project_root / raw
        if candidate.is_dir() and not candidate.is_symlink():
            return str(candidate)
    return None


def protected_inputs(
    private: Path, inventory: Path, evidence_dir: str | None,
) -> dict[str, str | None]:
    """The record step's protected inputs, beyond the release identity."""
    return {
        "CMRU_RECORD_OUT": str(private / "out"),
        "CMRU_BUILD_INVENTORY": str(inventory),
        "CMRU_GATE_EVIDENCE_DIR": evidence_dir,
    }


def validate_outputs(project: str, out: Path) -> Mapping[str, str]:
    """Refuse anything but exactly ``record.json`` and ``release-body.md`` within caps.

    Returns the protected push-step environment naming the two validated files."""
    expected = {RECORD_JSON_NAME: RECORD_JSON_MAX_BYTES, RECORD_BODY_NAME: RECORD_BODY_MAX_BYTES}
    present = sorted(entry.name for entry in out.iterdir())
    unexpected = [name for name in present if name not in expected]
    if unexpected:
        raise RefusedBeforeChange(
            f"{project}: record step wrote undeclared output(s) {', '.join(unexpected)}; "
            f"only {RECORD_JSON_NAME} and {RECORD_BODY_NAME} are accepted"
        )
    for name, limit in expected.items():
        path = out / name
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise RefusedBeforeChange(
                f"{project}: record step did not write {name} in CMRU_RECORD_OUT"
            ) from None
        if not stat.S_ISREG(info.st_mode):
            raise RefusedBeforeChange(f"{project}: record output {name} is not a regular file")
        if info.st_size > limit:
            raise RefusedBeforeChange(
                f"{project}: record output {name} is {info.st_size} bytes, over the "
                f"{limit}-byte limit; fix the input, cmru never truncates"
            )
        if info.st_size == 0:
            raise RefusedBeforeChange(f"{project}: record output {name} is empty")
    try:
        (out / RECORD_BODY_NAME).read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise RefusedBeforeChange(
            f"{project}: record output {RECORD_BODY_NAME} is not valid UTF-8"
        ) from None
    return {
        "CMRU_RECORD_JSON": str(out / RECORD_JSON_NAME),
        "CMRU_RECORD_BODY": str(out / RECORD_BODY_NAME),
    }
