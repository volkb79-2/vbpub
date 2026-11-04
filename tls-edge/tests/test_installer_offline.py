"""Offline installer test: the committed get.py installs a fixture bundle into a temp root.

No network (sockets are blocked in-process, pip runs with --no-index inside get.py), a stub
`docker` on PATH, fake ciu/dependency wheels. What is REAL: the committed get.py, the real
installer/adapter.py, the real ciu-stack templates, the real manifest writer
(scripts/write-bundle-manifest.py) and a real `git init` of the instance root.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib.util
import io
import os
import socket
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
TAG = "tls-edge-v9.9.9"
TAG2 = "tls-edge-v9.9.10"

try:
    import cmru.manifest  # noqa: F401  (the real manifest writer builds the fixture bundles)
except ImportError as exc:
    # This is the only end-to-end test of the installer: a missing cmru must be a failure,
    # not a green skip. The single, explicit opt-out is TLS_EDGE_ALLOW_INSTALLER_SKIP=1.
    if os.environ.get("TLS_EDGE_ALLOW_INSTALLER_SKIP") == "1":
        pytest.skip(f"cmru is not importable (explicit opt-out): {exc}", allow_module_level=True)
    pytest.fail(
        f"cmru.manifest is not importable ({exc}); the installer test cannot run. Install cmru "
        "(tester-unified ships it) or set TLS_EDGE_ALLOW_INSTALLER_SKIP=1 to skip deliberately.",
        pytrace=False,
    )


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = _load("tls_edge_adapter", ROOT / "installer" / "adapter.py")
MANIFEST = _load("tls_edge_write_manifest", ROOT / "scripts" / "write-bundle-manifest.py")


def make_wheel(directory: Path, dist: str, version: str, console: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    dist_info = f"{dist}-{version}.dist-info"
    files = {
        f"{dist}/__init__.py": f"def main():\n    print({dist!r}, {version!r})\n".encode(),
        f"{dist_info}/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n".encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if console:
        files[f"{dist_info}/entry_points.txt"] = f"[console_scripts]\n{console} = {dist}:main\n".encode()
    record = [
        f"{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b'=').decode()},{len(d)}"
        for n, d in files.items()
    ] + [f"{dist_info}/RECORD,,"]
    files[f"{dist_info}/RECORD"] = ("\n".join(record) + "\n").encode()
    path = directory / f"{dist}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def make_bundle(workdir: Path, tag: str, variant: str, marker: str) -> None:
    """A bundle shaped like build-artifact.sh's: real templates + adapter, fake wheels."""
    stage = workdir / "stage" / tag
    for rel in (
        "ciu-stack/ciu.compose.yml.j2", "ciu-stack/traefik.yml.j2",
        "ciu-stack/ciu.defaults.toml.j2", "ciu.global.defaults.toml.j2",
        "installer/adapter.py", "VERSION",
    ):
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / rel).read_bytes())
    (stage / "marker.txt").write_text(marker, encoding="utf-8")
    (stage / ".gitignore").write_bytes((ROOT / "installer" / "instance.gitignore").read_bytes())
    wheels = stage / "wheels"
    make_wheel(wheels, "ciu", "7.0.0", console="ciu")
    for dist in ("jinja2", "markupsafe", "pyyaml", "tomli_w"):
        make_wheel(wheels, dist, "1.0.0")
    (stage / "ciu.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(stage / "ciu.sh", 0o755)
    manifest = MANIFEST.build(tag, stage, config=ROOT / "cmru.toml")
    MANIFEST.write_manifest(manifest, stage / "manifest.json")
    asset = workdir / f"{tag}-{variant}.tar.xz"
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
    make_bundle(served, TAG, "py313", "one")
    make_bundle(served, TAG2, "py313", "two")

    get_py = ROOT / "get.py"
    ns: dict = {"__file__": str(get_py), "__name__": "tls_edge_get"}
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

    answers = tmp_path / "answers.toml"
    answers.write_text(
        'domain = "example.test"\nacme_email = "ops@example.test"\n'
        'tls_mode = "acme-tls"\ncalls = true\n', encoding="utf-8")
    return ns, root, answers


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


def run(ns, verb, answers=None, **kw):
    base = dict(version=None, scope="system", variant="py313", config=None,
                unattended=True, answers=str(answers) if answers else None)
    base.update(kw)
    with as_root(ns):
        ns[verb](argparse.Namespace(**base), None)


def git_count(stack: Path) -> int:
    out = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=stack, check=True,
                         capture_output=True, text=True).stdout
    return int(out)


def test_install_update_rollback_offline(env):
    ns, root, answers = env
    stack = root / "stack"

    run(ns, "do_install", answers, version=TAG)

    # layout: stable stack root with the shipped files, the seeded override, the stub, git
    assert (stack / "ciu-stack" / "ciu.defaults.toml.j2").is_file()
    assert (stack / "ciu.global.defaults.toml.j2").is_file()
    assert (stack / "marker.txt").read_text() == "one"
    assert not list(stack.glob("wheels")), "wheels are not synced into the stack"
    override = (stack / "ciu-stack" / "ciu.toml.j2").read_text()
    assert 'domain = "example.test"' in override
    assert 'mode = "acme-tls"' in override
    assert 'email = "ops@example.test"' in override
    assert "enabled = true" in override
    assert (stack / "consumer.env").read_text() == "TLS_EDGE_DOMAIN=example.test\n"
    assert "**/.ciu/" in (stack / ".gitignore").read_text()
    assert git_count(stack) == 1

    # the bundled ciu runs through the stub and the launcher, from the pinned venv
    stub = stack / "ciu"
    assert os.access(stub, os.X_OK)
    assert "current/venv/bin/ciu" in stub.read_text()
    assert subprocess.run([str(stub)], capture_output=True, text=True).stdout.strip() == "ciu 7.0.0"
    assert os.readlink(root / "bin" / "ciu") == "../current/venv/bin/ciu"
    assert (root / "bin" / "tls-edge-installer").is_file()

    # operator state: an edited override and ciu's runtime state survive an update
    (stack / "ciu-stack" / "ciu.toml.j2").write_text(override + "\n# operator edit\n")
    (stack / ".ciu").mkdir()
    (stack / ".ciu" / "state").write_text("keep")
    run(ns, "do_update", None, version=TAG2)
    assert (stack / "marker.txt").read_text() == "two"
    assert "# operator edit" in (stack / "ciu-stack" / "ciu.toml.j2").read_text()
    assert (stack / ".ciu" / "state").read_text() == "keep"
    assert git_count(stack) == 1

    run(ns, "do_rollback", None)
    assert (stack / "marker.txt").read_text() == "one"
    assert "# operator edit" in (stack / "ciu-stack" / "ciu.toml.j2").read_text()
    assert (stack / ".ciu" / "state").read_text() == "keep"


def test_unattended_without_domain_is_refused_before_any_change(env, tmp_path):
    ns, root, _ = env
    empty = tmp_path / "empty.toml"
    empty.write_text('tls_mode = "dev"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        run(ns, "do_install", empty, version=TAG)
    assert caught.value.code == 2
    assert not (root / "stack").exists()


def test_wrong_variant_is_refused(env):
    ns, root, answers = env
    with pytest.raises(SystemExit):
        run(ns, "do_install", answers, version=TAG, variant="py399")
    assert not (root / "stack").exists()


@pytest.mark.parametrize("bad", ['x"; rm', "a b", "{{ 7*7 }}", "-lead.test", ""])
def test_adapter_rejects_unsafe_domain(bad):
    with pytest.raises(ValueError):
        ADAPTER.seed_text({"domain": bad})


def test_adapter_seed_modes():
    text = ADAPTER.seed_text({"domain": "example.test", "tls_mode": "acme-dns",
                              "dns_provider": "route53", "calls": False})
    assert 'provider = "route53"' in text
    assert "dns_api_token" in text and text.count("# [tls_edge.secrets]") == 1
    assert "enabled = false" in text
    assert "[tls_edge.acme.dns]" not in ADAPTER.seed_text({"domain": "example.test"})
    with pytest.raises(ValueError):
        ADAPTER.seed_text({"domain": "example.test", "tls_mode": "bogus"})
    with pytest.raises(ValueError):
        ADAPTER.seed_text({"domain": "example.test", "acme_email": 'a"b@example.test'})


def _repack(served: Path, tag: str, mutate) -> None:
    """Re-pack a served bundle after `mutate(stage_dir)`, with a CONSISTENT sha256 sidecar,
    so only the manifest/member checks (not the sidecar check) can catch the tamper."""
    import shutil
    asset = served / f"{tag}-py313.tar.xz"
    work = served / "repack"
    work.mkdir()
    with tarfile.open(asset) as archive:
        archive.extractall(work, filter="data")
    mutate(work / tag)
    asset.unlink()
    with tarfile.open(asset, "w:xz") as archive:
        archive.add(work / tag, arcname=tag)
    shutil.rmtree(work)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (served / f"{asset.name}.sha256").write_text(f"{digest}  {asset.name}\n", encoding="utf-8")


def test_bad_sidecar_sha256_is_refused(env, tmp_path):
    ns, root, answers = env
    sidecar = tmp_path / "served" / f"{TAG}-py313.tar.xz.sha256"
    sidecar.write_text(f"{'0' * 64}  {TAG}-py313.tar.xz\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(ns, "do_install", answers, version=TAG)
    assert not (root / "stack").exists()


def test_file_modified_after_manifest_is_refused(env, tmp_path):
    ns, root, answers = env
    _repack(tmp_path / "served", TAG,
            lambda stage: (stage / "marker.txt").write_text("tampered", encoding="utf-8"))
    with pytest.raises(SystemExit):
        run(ns, "do_install", answers, version=TAG)
    assert not (root / "stack").exists()


def test_unlisted_extra_file_is_refused(env, tmp_path):
    ns, root, answers = env
    _repack(tmp_path / "served", TAG,
            lambda stage: (stage / "evil.sh").write_text("#!/bin/sh\n", encoding="utf-8"))
    with pytest.raises(SystemExit):
        run(ns, "do_install", answers, version=TAG)
    assert not (root / "stack").exists()


def test_wheel_replaced_after_manifest_is_refused(env, tmp_path):
    ns, root, answers = env
    _repack(tmp_path / "served", TAG,
            lambda stage: make_wheel(stage / "wheels", "jinja2", "1.0.0").write_bytes(b"not a wheel"))
    with pytest.raises(SystemExit):
        run(ns, "do_install", answers, version=TAG)
    assert not (root / "stack").exists()


def test_locally_modified_synced_file_blocks_update_until_overwrite_local(env):
    ns, root, answers = env
    stack = root / "stack"
    run(ns, "do_install", answers, version=TAG)
    (stack / "marker.txt").write_text("operator change", encoding="utf-8")
    with pytest.raises(SystemExit):
        run(ns, "do_update", None, version=TAG2)
    assert (stack / "marker.txt").read_text() == "operator change"
    run(ns, "do_update", None, version=TAG2, overwrite_local=True)
    assert (stack / "marker.txt").read_text() == "two"
