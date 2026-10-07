"""B142 end to end: a planted passthrough secret never reaches ANY artifact.

The unit tests prove each serialisation seam redacts. This module proves the
composed claim through the real CLI, a real git repository and a real child
process: the child prints the secrets on both streams, and the whole output
directory, the whole repository checkout (including any ``.assay`` state), and
the captured CLI stdout and stderr are searched for the raw values.

The secrets are fake. Both a ``*PASSWORD*`` name and a name that would escape a
name-pattern redactor (``SCHEMA_GATE_DSN``) are planted, so a redactor keyed on
secret-looking NAMES fails here.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from conftest import R0_LANE, GitRepo, set_key

from assay.cli import main
from assay.verify import verify_document

SECRETS = {
    "X_PASSWORD": "s3cr3t-planted-password",
    "SCHEMA_GATE_DSN": "postgresql://u:planted-dsn-pw@h/db",
}

ECHO = (
    "echo X_PASSWORD=$X_PASSWORD; echo SCHEMA_GATE_DSN=$SCHEMA_GATE_DSN; "
    "echo err:$X_PASSWORD 1>&2; echo err:$SCHEMA_GATE_DSN 1>&2; exit {code}"
)


def _everything_under(*roots: Path) -> dict[Path, bytes]:
    found: dict[Path, bytes] = {}
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file() and path.is_symlink() is False:
                found[path] = path.read_bytes()
    return found


@pytest.mark.parametrize("code", [0, 7])
def test_a_planted_passthrough_secret_appears_in_no_artifact(
    git_repo: GitRepo, tmp_path: Path, monkeypatch, code: int
):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    lane = set_key(
        R0_LANE,
        "argv",
        json.dumps(["/bin/sh", "-c", ECHO.format(code=code)]),
    )
    lane = set_key(lane, "env_passthrough", json.dumps(sorted(SECRETS)))
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / "verdict.json"
    stdout, stderr = io.StringIO(), io.StringIO()

    exit_code = main(
        ["run", "package", "--file", str(path), "--verdict-json", str(target)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == (0 if code == 0 else 1)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["env_effective"]["X_PASSWORD"] == "<passthrough>"
    assert document["env_effective"]["SCHEMA_GATE_DSN"] == "<passthrough>"
    assert document["env_effective_passthrough_sha256"] == {
        name: hashlib.sha256(value.encode()).hexdigest()
        for name, value in SECRETS.items()
    }
    assert verify_document(document) == []

    artifacts = _everything_under(out_dir, git_repo.path)
    artifacts[Path("<stdout>")] = stdout.getvalue().encode()
    artifacts[Path("<stderr>")] = stderr.getvalue().encode()
    assert target in artifacts
    for location, content in artifacts.items():
        for name, value in SECRETS.items():
            assert value.encode() not in content, (
                f"{name}'s raw value reached {location}"
            )
    if code != 0:
        # The echo is still diagnosable: masked at the same byte width.
        assert "X_PASSWORD=" + "*" * len(SECRETS["X_PASSWORD"]) in (
            document["result_stdout_tail"]
        )
