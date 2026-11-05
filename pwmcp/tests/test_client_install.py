"""pwmcp-client-install.py: the client-only installer (no docker/ciu/stack), tested docker-free and offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "pwmcp-client-install.py"
SPEC = importlib.util.spec_from_file_location("pwmcp_client_install", SCRIPT)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)

TAG = "pwmcp-v1.64.1-r2"
WHEELS = {
    "pwmcp-client": "pwmcp_client-1.64.1.post2-py3-none-any.whl",
    "playwright": "playwright-1.64.1-py3-none-manylinux1_x86_64.whl",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bundle(tmp_path: Path, *, tamper: bool = False, drop_manifest: bool = False, no_client: bool = False,
            extra_member: bool = True) -> Path:
    manifest = {"project": "pwmcp", "files": {}}
    members: dict[str, bytes] = {}
    for dist, name in WHEELS.items():
        if no_client and dist == "pwmcp-client":
            continue
        data = f"wheel {dist}".encode()
        members[f"wheels/{name}"] = data
        manifest[dist] = {"wheel": name, "sha256": _sha(b"other" if tamper and dist == "playwright" else data)}
    if not drop_manifest:
        members["manifest.json"] = json.dumps(manifest).encode()
    if extra_member:
        members["docs/README.md"] = b"stack docs are never extracted"
        members["../escape.whl"] = b"traversal"
    path = tmp_path / f"{TAG}.tar.xz"
    with tarfile.open(path, "w:xz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    (tmp_path / f"{path.name}.sha256").write_text(f"{_sha(path.read_bytes())}  {path.name}\n", encoding="utf-8")
    return path


def _args(**overrides: object) -> argparse.Namespace:
    values = dict(version=None, bundle=None, sha256=None, repo="o/r", token=None, dir="x", bin_dir="y")
    values.update(overrides)
    return argparse.Namespace(**values)


class Runner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], check: bool) -> None:
        assert check is True
        self.calls.append(argv)


def test_release_tag_accepts_release_or_tag_and_refuses_anything_else(capsys: pytest.CaptureFixture[str]) -> None:
    assert installer.release_tag("1.64.1-r2") == TAG == installer.release_tag(TAG)
    with pytest.raises(SystemExit):
        installer.release_tag("1.64.1.post2")
    assert "--version must look like 1.64.1-r2" in capsys.readouterr().err


def test_install_from_a_local_bundle_builds_a_hash_locked_offline_venv(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    root, bin_dir = tmp_path / "root", tmp_path / "bin"
    runner = Runner()
    release = installer.install(_args(bundle=str(bundle), dir=str(root), bin_dir=str(bin_dir)), run=runner)
    assert release == root / "releases" / TAG
    # only wheels are extracted: no stack docs, no path traversal outside the wheelhouse
    assert sorted(p.name for p in (release / "wheelhouse").iterdir()) == sorted(WHEELS.values())
    assert not (tmp_path / "escape.whl").exists() and not (root / "escape.whl").exists()
    lock = (release / "requirements.lock").read_text(encoding="utf-8").splitlines()
    assert f"pwmcp-client==1.64.1.post2 --hash=sha256:{_sha(b'wheel pwmcp-client')}" in lock
    assert f"playwright==1.64.1 --hash=sha256:{_sha(b'wheel playwright')}" in lock
    create, install, check = runner.calls
    assert create[1:3] == ["-m", "venv"]
    assert "--no-index" in install and "--require-hashes" in install and str(release / "requirements.lock") in install
    assert check[-4:-1] == ["-m", "pip", "check"] or "check" in check
    assert (root / "current").resolve() == release.resolve()
    assert (bin_dir / "pwmcp").is_symlink()
    assert (bin_dir / "pwmcp").readlink() == root / "current" / "venv" / "bin" / "pwmcp"
    # upgrade: another release lands side by side and `current` switches; a reinstall replaces in place
    installer.install(_args(bundle=str(bundle), dir=str(root), bin_dir=str(bin_dir)), run=Runner())
    assert (root / "current").resolve() == release.resolve()


def test_failed_install_removes_the_partial_release(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)

    def boom(argv: list[str], check: bool) -> None:
        raise RuntimeError("pip failed")

    root = tmp_path / "root"
    with pytest.raises(RuntimeError):
        installer.install(_args(bundle=str(bundle), dir=str(root), bin_dir=str(tmp_path / "bin")), run=boom)
    assert not (root / "releases" / TAG).exists() and not (root / "current").exists()


def test_bundle_checks_fail_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle = _bundle(tmp_path)
    with pytest.raises(SystemExit):
        installer.obtain_bundle(_args(bundle=str(bundle), sha256="0" * 64), tmp_path)
    assert "bundle SHA256 mismatch" in capsys.readouterr().err
    (tmp_path / f"{bundle.name}.sha256").unlink()
    with pytest.raises(SystemExit):
        installer.obtain_bundle(_args(bundle=str(bundle)), tmp_path)
    assert "no SHA256 to verify" in capsys.readouterr().err
    assert installer.obtain_bundle(_args(bundle=str(bundle), sha256=_sha(bundle.read_bytes()).upper()), tmp_path)[1] == TAG
    with pytest.raises(SystemExit):
        installer.obtain_bundle(_args(bundle=str(tmp_path / "missing.tar.xz")), tmp_path)
    assert "is not a file" in capsys.readouterr().err
    odd = tmp_path / "odd.tar.xz"
    odd.write_bytes(b"x")
    with pytest.raises(SystemExit):
        installer.obtain_bundle(_args(bundle=str(odd), sha256="0"), tmp_path)
    assert "cannot derive the release" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        installer.obtain_bundle(_args(), tmp_path)
    assert "give --version RELEASE or --bundle FILE" in capsys.readouterr().err


@pytest.mark.parametrize("kwargs, message", [
    ({"tamper": True}, "SHA256 mismatch"),
    ({"drop_manifest": True}, "has no manifest.json"),
    ({"no_client": True}, "lists no pwmcp-client wheel"),
])
def test_wheel_and_manifest_tampering_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kwargs: dict, message: str
) -> None:
    bundle = _bundle(tmp_path, **kwargs)
    with pytest.raises(SystemExit):
        installer.install(_args(bundle=str(bundle), dir=str(tmp_path / "r"), bin_dir=str(tmp_path / "b")), run=Runner())
    assert message in capsys.readouterr().err


def test_wheel_set_must_match_the_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    house = tmp_path / "house"
    house.mkdir()
    (house / "stray-1.0-py3-none-any.whl").write_bytes(b"x")
    manifest = {"pwmcp-client": {"wheel": WHEELS["pwmcp-client"], "sha256": "0" * 64}}
    with pytest.raises(SystemExit):
        installer.lock_lines(manifest, house)
    assert "do not match the manifest" in capsys.readouterr().err


def test_download_uses_the_public_url_without_a_token_and_the_api_with_one(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    body, sidecar = bundle.read_bytes(), (tmp_path / f"{bundle.name}.sha256").read_bytes()
    seen: list[tuple[str, str | None, str | None]] = []

    def fetcher(url: str, token: str | None, accept: str | None = None) -> bytes:
        seen.append((url, token, accept))
        if url.endswith(".sha256") and "download" in url:
            return sidecar
        if "download" in url:
            return body
        if url.endswith(f"/releases/tags/{TAG}"):
            return json.dumps({"assets": [
                {"name": bundle.name, "url": "https://api/asset/1"},
                {"name": bundle.name + ".sha256", "url": "https://api/asset/2"},
            ]}).encode()
        return body if url.endswith("/1") else sidecar

    work = tmp_path / "work"
    work.mkdir()
    path, tag = installer.obtain_bundle(_args(version="1.64.1-r2"), work, fetcher)
    assert (path.name, tag) == (bundle.name, TAG)
    assert seen[0] == (f"https://github.com/o/r/releases/download/{TAG}/{bundle.name}", None, None)
    seen.clear()
    work2 = tmp_path / "work2"
    work2.mkdir()
    installer.obtain_bundle(_args(version=TAG, token="tok"), work2, fetcher)
    assert ("https://api/asset/1", "tok", "application/octet-stream") in seen
    with pytest.raises(SystemExit):
        installer.download_release_asset("o/r", TAG, "missing.bin", "tok", fetcher)


def test_fetch_sends_the_token_and_accept_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        captured.update(headers=dict(request.header_items()), timeout=timeout)
        return Response(b"payload")

    monkeypatch.setattr(installer.urllib.request, "urlopen", fake_urlopen)
    assert installer.fetch("https://x", "tok", "application/octet-stream") == b"payload"
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["headers"]["Accept"] == "application/octet-stream"
    assert installer.fetch("https://x", None) == b"payload"
    assert "Authorization" not in captured["headers"]


def test_replace_symlink_never_clobbers_a_real_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "sub" / "link"
    installer.replace_symlink(link, target)
    other = tmp_path / "o"
    other.mkdir()
    (link.parent / ".link.new").symlink_to(target)  # a stale temp link is replaced
    installer.replace_symlink(link, other)
    assert link.resolve() == other.resolve()
    real = tmp_path / "real"
    real.write_text("x")
    with pytest.raises(SystemExit):
        installer.replace_symlink(real, target)
    assert "refusing to replace" in capsys.readouterr().err


def test_main_reads_the_token_file_then_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []
    monkeypatch.setattr(installer, "install", lambda args: seen.append(args.token))
    token_file = tmp_path / "tok"
    token_file.write_text("  filetoken\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "envtoken")
    assert installer.main(["--version", "1.64.1-r2", "--github-token-file", str(token_file)]) == 0
    assert installer.main(["--version", "1.64.1-r2"]) == 0
    monkeypatch.delenv("GITHUB_TOKEN")
    assert installer.main(["--version", "1.64.1-r2"]) == 0
    assert seen == ["filetoken", "envtoken", None]
