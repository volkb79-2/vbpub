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
    if token.endswith("-"):
        return False  # file-name prefix in front of a shell/placeholder ID (`golden-$ID.raw`)
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


# --- capture ID: naming, pairing, mismatch refusal ---------------------------

def test_capture_ids_are_8_lowercase_hex_and_random() -> None:
    ids = {credentials.new_capture_id() for _ in range(200)}
    assert len(ids) > 190
    assert all(credentials.CAPTURE_ID_RE.match(i) for i in ids)
    for bad in ("", "abc", "0A1B2C3D", "0a1b2c3", "0a1b2c3d0", "0a1b2c3g", None):
        with pytest.raises(credentials.CredentialError):
            credentials.validate_capture_id(bad)


def test_capture_naming_scheme_for_image_and_sidecar() -> None:
    template = Path("/x/truenas-27-20GiB-golden.raw")
    assert credentials.image_name_for(template, "0a1b2c3d") == "truenas-27-20GiB-golden-0a1b2c3d.raw"
    assert credentials.sidecar_name_for(template, "0a1b2c3d") == "truenas-27-20GiB-golden-0a1b2c3d.secrets.toml"
    # an ID-bearing name used as template is re-stemmed, never doubled
    assert credentials.image_name_for(Path("/x/g-20GiB-0a1b2c3d.raw"), "ffffffff") == "g-20GiB-ffffffff.raw"
    assert credentials.split_capture_stem(Path("a-golden-0a1b2c3d.secrets.toml")) == ("a-golden", "0a1b2c3d")
    assert credentials.split_capture_stem(Path("a-golden.secrets.toml")) == ("a-golden", None)


def test_step1_plan_capture_new_id_for_finished_reuse_for_unfinished(tmp_path: Path) -> None:
    template = tmp_path / "g-golden.raw"
    # seed without ID -> fresh ID, names carry it
    cid, image, sidecar = STEP1.plan_capture({}, template)
    assert credentials.CAPTURE_ID_RE.match(cid)
    assert image.name == f"g-golden-{cid}.raw" and sidecar.name == f"g-golden-{cid}.secrets.toml"
    # unfinished capture (no image, no finished marker) keeps its ID on retry
    assert STEP1.plan_capture({"capture_id": "0a1b2c3d"}, template)[0] == "0a1b2c3d"
    # finished by image on disk -> new ID
    (tmp_path / "g-golden-0a1b2c3d.raw").write_bytes(b"x")
    assert STEP1.plan_capture({"capture_id": "0a1b2c3d"}, template)[0] != "0a1b2c3d"
    # finished by marker -> new ID
    done = {"capture_id": "11111111", "truenas_api": {"captured_image_sha256": "ab" * 32}}
    assert STEP1.plan_capture(done, template)[0] != "11111111"
    with pytest.raises(STEP1.StepError):
        STEP1.plan_capture({"capture_id": "nothex"}, template)


def test_step1_sidecar_round_trips_capture_id(tmp_path: Path) -> None:
    doc = _document("p" * 32)
    doc["capture_id"] = "0a1b2c3d"
    doc["image_file"] = "g-0a1b2c3d.raw"
    path = tmp_path / "g-0a1b2c3d.secrets.toml"
    path.write_text(STEP1.serialize_secret_document(doc))
    path.chmod(0o600)
    assert tomllib.loads(path.read_text())["capture_id"] == "0a1b2c3d"
    loaded = STEP1.load_secret_document(path, tmp_path / "g-0a1b2c3d.raw")
    assert loaded["capture_id"] == "0a1b2c3d"
    # the template name is accepted when the sidecar records the ID'd file name
    assert STEP1.load_secret_document(path, tmp_path / "g.raw")["image_file"] == "g-0a1b2c3d.raw"


def test_require_matching_pair_refuses_every_mismatch(tmp_path: Path) -> None:
    img = tmp_path / "g-0a1b2c3d.raw"
    side = tmp_path / "g-0a1b2c3d.secrets.toml"
    assert credentials.require_matching_pair(img, side, "0a1b2c3d") == "0a1b2c3d"
    for image, sidecar, recorded in (
        (img, tmp_path / "g-ffffffff.secrets.toml", "0a1b2c3d"),   # file-name mismatch
        (img, side, "ffffffff"),                                   # content mismatch
        (img, side, None),                                         # sidecar without ID
        (tmp_path / "g.raw", side, "0a1b2c3d"),                    # image without ID
        (img, tmp_path / "g.secrets.toml", "0a1b2c3d"),            # sidecar name without ID
    ):
        with pytest.raises(credentials.CredentialError):
            credentials.require_matching_pair(image, sidecar, recorded)


STEP2 = _load("golden_step2_creds", GOLDEN_ROOT / "step2-build-unattended-restore-iso.py")


def _write_step2_sidecar(path: Path, image_name: str, capture_id: str | None) -> None:
    doc = _document("p" * 32)
    doc["image_file"] = image_name
    if capture_id:
        doc["capture_id"] = capture_id
    path.write_text(STEP1.serialize_secret_document(doc))
    path.chmod(0o600)


def test_step2_build_refuses_sidecar_with_different_capture_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(STEP2, "validate_webhook", lambda _v: None)
    monkeypatch.setattr(STEP2, "public_key_from_private", lambda _p: "ssh-ed25519 AAAA t")
    image = tmp_path / "g-0a1b2c3d.raw"
    # sidecar content records another ID while both file names agree
    side = tmp_path / "g-0a1b2c3d.secrets.toml"
    _write_step2_sidecar(side, image.name, "ffffffff")
    with pytest.raises(STEP2.BuildError, match="mismatch"):
        STEP2.load_secrets(side, image)
    # sidecar file name carries another ID
    other = tmp_path / "g-ffffffff.secrets.toml"
    _write_step2_sidecar(other, image.name, "0a1b2c3d")
    with pytest.raises(STEP2.BuildError, match="mismatch"):
        STEP2.load_secrets(other, image)
    # a matching pair is accepted and reports its ID
    _write_step2_sidecar(side, image.name, "0a1b2c3d")
    assert STEP2.load_secrets(side, image)["capture_id"] == "0a1b2c3d"
