"""TN-03: dynamic credentials, private sidecars, no committed secrets."""

from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

import pytest

GOLDEN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GOLDEN_ROOT))
import credentials  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STEP1 = _load("golden_step1_creds", GOLDEN_ROOT / "step1-install-prepare-truenas.py")


def test_generated_passwords_are_random_long_and_safe() -> None:
    values = {credentials.generate_password() for _ in range(50)}
    assert len(values) == 50
    for value in values:
        assert len(value) >= 24
        assert set(value) <= set(credentials.PASSWORD_ALPHABET)
        credentials.validate_admin_password(value)
    entropy_bits = len(next(iter(values))) * math.log2(len(credentials.PASSWORD_ALPHABET))
    assert entropy_bits > 128


@pytest.mark.parametrize("bad", ["truenas", "TrueNAS", "short", "x" * 23, " " + "y" * 30, "a" * 30 + "\n"])
def test_bad_passwords_are_refused(bad: str) -> None:
    with pytest.raises(credentials.CredentialError):
        credentials.validate_admin_password(bad)


def test_generate_password_refuses_short_length() -> None:
    with pytest.raises(credentials.CredentialError):
        credentials.generate_password(8)


def test_atomic_write_private_modes_and_no_temp_left(tmp_path: Path) -> None:
    target = tmp_path / "secrets" / "a.toml"
    credentials.atomic_write_private(target, b"x = 1\n")
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert sorted(p.name for p in target.parent.iterdir()) == ["a.toml"]
    credentials.atomic_write_private(target, b"x = 2\n")  # replace is atomic and keeps the mode
    assert target.read_text() == "x = 2\n" and target.stat().st_mode & 0o777 == 0o600


def test_atomic_write_failure_leaves_previous_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "d" / "a.toml"
    credentials.atomic_write_private(target, b"old")

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(credentials.os, "replace", boom)
    with pytest.raises(OSError):
        credentials.atomic_write_private(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in target.parent.iterdir()] == ["a.toml"]


def _document(password: str) -> dict:
    return {
        "image_file": "img.raw",
        "truenas_admin": {"username": "truenas_admin", "password": password},
        "root_ssh": {"private_key": "PRIV", "public_key": "ssh-ed25519 AAAA t"},
        "mattermost": {"webhook_url": "https://example.invalid/hooks/x"},
    }


def test_step1_sidecar_round_trips_with_and_without_api_block(tmp_path: Path) -> None:
    doc = _document("p" * 32)
    path = tmp_path / "img.secrets.toml"
    STEP1.atomic_write(path, STEP1.serialize_secret_document(doc).encode())
    assert tomllib.loads(path.read_text())["truenas_admin"]["password"] == "p" * 32
    doc["truenas_api"] = {
        "api_key": "k" * 20, "key_name": STEP1.API_KEY_NAME,
        "expires_at": "2027-01-01T00:00:00+00:00", "id": 3, "captured_image_sha256": "ab" * 32,
    }
    parsed = tomllib.loads(STEP1.serialize_secret_document(doc))
    assert parsed["truenas_api"]["captured_image_sha256"] == "ab" * 32


def test_pending_marker_round_trips(tmp_path: Path) -> None:
    doc = _document("p" * 32)
    doc["truenas_api"] = {
        "api_key": "k" * 20, "key_name": STEP1.API_KEY_NAME,
        "expires_at": "2027-01-01T00:00:00+00:00", "id": 3, "capture_pending": True,
    }
    assert tomllib.loads(STEP1.serialize_secret_document(doc))["truenas_api"]["capture_pending"] is True


def test_step1_refuses_sidecar_with_bootstrap_password(tmp_path: Path) -> None:
    image = tmp_path / "img.raw"
    path = tmp_path / "img.secrets.toml"
    doc = _document("truenas")
    path.write_text(STEP1.serialize_secret_document(doc))
    path.chmod(0o600)
    with pytest.raises(STEP1.StepError, match="bootstrap"):
        STEP1.load_secret_document(path, image)


def test_step1_sidecar_location_is_gitignored() -> None:
    repo = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=GOLDEN_ROOT, text=True, capture_output=True, check=True
    ).stdout.strip())
    for candidate in (
        STEP1.DEFAULT_SECRETS,
        GOLDEN_ROOT / "artifacts" / "hosts" / "h1.example.secrets.toml",
        STEP1.VM_DIR / "x.secrets.toml",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "-v", str(candidate.relative_to(repo))],
            cwd=repo, text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, f"{candidate} is not gitignored"


def test_step1_password_never_in_stdout_or_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    # step 1 only logs fixed messages around credentials; assert on the source:
    source = (GOLDEN_ROOT / "step1-install-prepare-truenas.py").read_text()
    for line in source.splitlines():
        if "log(" in line or "print(" in line:
            assert "password'" not in line and "api_key'" not in line and "new_password" not in line, line
    STEP1.log("fixed message")
    assert "fixed message" in capsys.readouterr().out


# --- tracked-docs scan -------------------------------------------------------

_TEXT_SUFFIXES = {".md", ".toml", ".py", ".txt", ".example", ".json", ".yaml", ".yml", ".j2", ".sh"}
_CREDENTIAL_LINE = re.compile(r"(password|passwd|api[_ -]?key|secret|token)", re.I)
_VALUE = re.compile(r"[A-Za-z0-9+/=_\-.%@^]{20,}")


def _entropy(text: str) -> float:
    return -sum(text.count(c) / len(text) * math.log2(text.count(c) / len(text)) for c in set(text))


def _looks_like_secret(token: str) -> bool:
    if "/" in token and token.count("/") > 1 and "=" not in token:
        return False  # paths
    if token.startswith(("http", "ssh-", "sha256")) or re.fullmatch(r"[0-9a-f]{40,128}", token):
        return False  # urls, key type prefixes, git/sha digests
    if re.search(r"\.(toml|py|md|raw|txt|json|qcow2|iso)$", token):
        return False  # file names
    classes = sum(bool(re.search(p, token)) for p in ("[a-z]", "[A-Z]", "[0-9]"))
    return classes == 3 and _entropy(token) > 4.2 and not re.fullmatch(r"[A-Za-z]+(?:[_.-][A-Za-z]+)*", token)


def test_tracked_truenas_docs_hold_no_credential_like_values() -> None:
    repo = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=GOLDEN_ROOT, text=True, capture_output=True, check=True
    ).stdout.strip())
    files = subprocess.run(
        ["git", "ls-files", "scripts/truenas"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.splitlines()
    findings: list[str] = []
    for name in files:
        path = repo / name
        if path.suffix not in _TEXT_SUFFIXES or "/tests/" in name or not path.is_file():
            continue
        for number, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
            if not _CREDENTIAL_LINE.search(line):
                continue
            for token in _VALUE.findall(line):
                if _looks_like_secret(token):
                    findings.append(f"{name}:{number}")  # never the value
    assert not findings, f"credential-like values near password/api_key words: {sorted(set(findings))}"


def test_tracked_truenas_files_hold_no_webhook_tokens() -> None:
    repo = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=GOLDEN_ROOT, text=True, capture_output=True, check=True
    ).stdout.strip())
    hits = subprocess.run(
        ["git", "grep", "-n", "-E", "/hooks/[a-z0-9]{20,}", "--", "scripts/truenas", ":!*/tests/*"],
        cwd=repo, text=True, capture_output=True, check=False,
    ).stdout.splitlines()
    assert not [h.split(":", 2)[0] + ":" + h.split(":", 2)[1] for h in hits]


def test_scanner_detects_a_planted_credential() -> None:
    assert _looks_like_secret("Zx9kQ4mT7vB2nL8pR5wY3cH6")
    assert not _looks_like_secret("truenas_admin_password_file")
