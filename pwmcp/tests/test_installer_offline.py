"""Offline installer test: the committed get.py installs a fixture bundle into a temp root.

No network (socket.connect is refused in-process, pip runs --no-index inside get.py), a stub
`docker` on PATH and fake wheels. REAL: the committed get.py, the real installer/adapter.py,
the real standalone CIU inputs and the manifest rule used by scripts/build-bundle.py
(`cmru.manifest.build_bundle_manifest` plus one entry per [[project.installer.wheels]]).

Covers the behaviour of the shared installer template as rendered for pwmcp: unattended
install (no wizard questions, so `--unattended` needs no answers file), the maintenance copy at
<root>/bin/pwmcp-installer, the `pwmcp` launcher, update, operator-edit preservation and rollback.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fnmatch
import hashlib
import importlib.util
import os
import socket
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from _testdeps import optional_module

ROOT = Path(__file__).resolve().parents[1]
TAG = "pwmcp-v9.9.9"
TAG2 = "pwmcp-v9.9.10"

optional_module("cmru.manifest", "cmru (manifest writer) builds the bundle manifest", module_level=True)
from cmru.manifest import build_bundle_manifest, write_manifest  # noqa: E402
from cmru.release import sha256_file  # noqa: E402

_RELEASE_INPUTS = (
    "ciu.compose.yml.j2",
    "ciu.defaults.toml.j2",
    "standalone/ciu.global.defaults.toml.j2",
    "standalone/ciu.install.override.toml.j2",
    "installer/adapter.py",
)


def make_wheel(directory: Path, dist: str, version: str, console: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    pkg = dist.replace("-", "_")
    dist_info = f"{pkg}-{version}.dist-info"
    files = {
        f"{pkg}/__init__.py": f"def main():\n    print({dist!r}, {version!r})\n".encode(),
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n".encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if console:
        files[f"{dist_info}/entry_points.txt"] = f"[console_scripts]\n{console} = {pkg}:main\n".encode()
    record = [
        f"{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b'=').decode()},{len(d)}"
        for n, d in files.items()
    ] + [f"{dist_info}/RECORD,,"]
    files[f"{dist_info}/RECORD"] = ("\n".join(record) + "\n").encode()
    path = directory / f"{pkg}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def _installer_manifest(tag: str, stage: Path) -> dict:
    specs = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["project"]["installer"]["wheels"]
    manifest = build_bundle_manifest(project="pwmcp", tag=tag, bundle_root=stage)
    for spec in specs:
        matches = [p for p in stage.glob(spec["path"]) if fnmatch.fnmatch(p.name, Path(spec["path"]).name)]
        assert len(matches) == 1, spec
        manifest[spec["distribution"]] = {
            "wheel": matches[0].name, "sha256": sha256_file(matches[0]), "size": matches[0].stat().st_size,
        }
    return manifest


def make_bundle(workdir: Path, tag: str, marker: str) -> None:
    stage = workdir / "stage" / tag
    for rel in _RELEASE_INPUTS:
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / rel).read_bytes())
    (stage / "marker.txt").write_text(marker, encoding="utf-8")
    wheels = stage / "wheels"
    make_wheel(wheels, "pwmcp-client", "1.0.0", console="pwmcp")
    for dist in ("cli-extended", "greenlet", "pyee", "typing-extensions", "playwright"):
        make_wheel(wheels, dist, "1.0.0")
    write_manifest(_installer_manifest(tag, stage), stage / "manifest.json")
    asset = workdir / f"{tag}.tar.xz"
    with tarfile.open(asset, "w:xz") as archive:
        archive.add(stage, arcname=tag)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (workdir / f"{asset.name}.sha256").write_text(f"{digest}  {asset.name}\n", encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")

    served = tmp_path / "served"
    served.mkdir()
    make_bundle(served, TAG, "one")
    make_bundle(served, TAG2, "two")

    get_py = ROOT / "get.py"
    ns: dict = {"__file__": str(get_py), "__name__": "pwmcp_get"}
    exec(compile(get_py.read_text(encoding="utf-8"), str(get_py), "exec"), ns)
    root = tmp_path / "instance"
    ns["INSTALL_DIR_SYSTEM"] = str(root)

    def fake_download(tag, name, dest, token):
        import shutil
        shutil.copy2(served / name, dest)

    ns["_download_asset"] = fake_download

    def refuse(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    return ns, root


@contextlib.contextmanager
def as_root(ns):
    real_uid = os.getuid()
    original = ns["_expected_owner"]
    ns["_expected_owner"] = lambda: real_uid
    try:
        with mock.patch.object(ns["os"], "geteuid", return_value=0):
            yield
    finally:
        ns["_expected_owner"] = original


def run(ns, verb, **kw):
    base = dict(version=None, scope="system", variant=None, config=None, unattended=True, answers=None)
    base.update(kw)
    with as_root(ns):
        ns[verb](argparse.Namespace(**base), None)


def test_template_constants_match_cmru_toml():
    ns: dict = {"__file__": str(ROOT / "get.py"), "__name__": "pwmcp_get_consts"}
    exec(compile((ROOT / "get.py").read_text(encoding="utf-8"), "get.py", "exec"), ns)
    cfg = tomllib.loads((ROOT / "cmru.toml").read_text(encoding="utf-8"))["project"]["installer"]
    assert ns["ENTRYPOINT"] == cfg["entrypoint"]
    assert ns["LAUNCHERS"] == cfg["launchers"]
    assert [list(w) for w in ns["WHEEL_SPECS"]] == [[w["path"], w["distribution"]] for w in cfg["wheels"]]
    assert ns["QUESTIONS"] == [] and ns["STABLE_DIR"] == "" and ns["VARIANTS"] == []


def test_unattended_install_update_rollback_offline(env):
    ns, root = env

    run(ns, "do_install", version=TAG)

    # unattended install needed no answers file; the maintenance copy and launcher exist
    assert (root / "bin" / "pwmcp-installer").is_file()
    assert os.readlink(root / "bin" / "pwmcp") == "../current/venv/bin/pwmcp"
    assert not (root / "shared" / "answers.toml").exists(), "no questions, so no answers are stored"
    assert (root / "current" / "tree" / "marker.txt").read_text() == "one"
    # the adapter linked the standalone CIU root inputs and seeded the site override once
    for name in ("ciu.global.defaults.toml.j2", "ciu.compose.yml.j2", "ciu.defaults.toml.j2"):
        assert (root / name).is_symlink()
    override = root / "ciu.toml.j2"
    assert override.is_file() and not override.is_symlink()
    # the launcher runs from the hash-locked venv
    import subprocess
    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}  # the gate sets PYTHONPATH=client/src, which would shadow the venv's wheel
    out = subprocess.run([str(root / "bin" / "pwmcp")], capture_output=True, text=True, env=clean_env).stdout.strip()
    assert out == "pwmcp-client 1.0.0"

    # an operator edit of the override survives update; rollback restores the previous release
    override.write_text(override.read_text() + "\n# operator edit\n")
    run(ns, "do_update", version=TAG2)
    assert (root / "current" / "tree" / "marker.txt").read_text() == "two"
    assert "# operator edit" in override.read_text()

    run(ns, "do_rollback")
    assert (root / "current" / "tree" / "marker.txt").read_text() == "one"
    assert "# operator edit" in override.read_text()


def test_tampered_bundle_is_refused_before_any_change(env, tmp_path):
    ns, root = env
    served = tmp_path / "served"
    asset = served / f"{TAG}.tar.xz"
    asset.write_bytes(asset.read_bytes() + b"x")  # no longer matches the published sha256
    with pytest.raises(SystemExit):
        run(ns, "do_install", version=TAG)
    assert not (root / "current").exists()


def test_bundle_and_images_do_not_ship_glyphgrid_fixtures():
    """NOTICE review (M3): the atlases (tests/fixtures, tools/glyphgrid) are never copied into a bundle or image."""
    bundle = tomllib.loads((ROOT / "bundle.toml").read_text(encoding="utf-8"))["copy"]
    shipped = list(bundle["files"]) + list(bundle["dirs"])
    assert not [p for p in shipped if p.split("/")[0] in ("tests", "tools")], shipped
    for dockerfile in (ROOT / "containers").glob("*/Dockerfile"):
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            if line.startswith(("COPY ", "ADD ")) and "--from=" not in line:
                sources = line.split()[1:-1]
                assert not [s for s in sources if s.startswith(("tests", "tools", "./tests", "./tools")) or s == "."], line
