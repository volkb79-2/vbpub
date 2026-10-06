"""Local fakes for exercising the rendered get.py (W1-INSTALLER): no network, no root.

* ``render_ns``      render get.py for a temp root and exec it into a namespace;
* ``make_wheel``     a real (pure-python) wheel pip can install, optionally with a
                     console script and requirements;
* ``make_bundle``    a release bundle (.tar.xz + .sha256 sidecar) with a verified manifest;
* ``use_bundles``    make ``_download_asset`` copy from a local directory;
* ``install``/``update``  run the rendered commands as "root" against the temp root;
* ``snapshot``       a byte-level listing of a tree (for "root unchanged" oracles).
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from unittest import mock

from cmru.getpy import render_get_py

TAG_PREFIX = "demo-v"


def render_ns(tmp_path: Path, **kw) -> dict:
    """Render and exec get.py with its system root at ``tmp_path/system``."""
    params = dict(
        project_name="demo", repo_owner="o", repo_name="r", tag_prefix=TAG_PREFIX,
        install_dir_system=str(tmp_path / "system"), install_dir_user="demo",
    )
    params.update(kw)
    ns: dict = {}
    exec(compile(render_get_py(**params), "<rendered-get.py>", "exec"), ns)
    return ns


def root_of(ns: dict) -> Path:
    return Path(ns["INSTALL_DIR_SYSTEM"])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_wheel(
    directory: Path, dist: str, version: str, *, requires: Iterable[str] = (),
    console: Optional[str] = None,
) -> Path:
    """A valid wheel. ``console`` names a script that prints ``<dist> <version>``."""
    directory.mkdir(parents=True, exist_ok=True)
    mod = dist.replace("-", "_")
    dist_info = f"{mod}-{version}.dist-info"
    files: Dict[str, bytes] = {
        f"{mod}/__init__.py": (
            f"VERSION = {version!r}\n"
            "def main():\n"
            f"    print({dist!r}, {version!r})\n"
        ).encode(),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {r}\n" for r in requires)
        ).encode(),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if console:
        files[f"{dist_info}/entry_points.txt"] = (
            f"[console_scripts]\n{console} = {mod}:main\n"
        ).encode()
    record = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = ("\n".join(record) + "\n").encode()
    path = directory / f"{mod}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def manifest_entry(wheel: Path) -> dict:
    data = wheel.read_bytes()
    return {"wheel": wheel.name, "sha256": sha(data), "size": len(data)}


def make_bundle(
    workdir: Path, tag: str, *, files: Optional[Dict[str, bytes]] = None,
    wheels: Iterable[Tuple[str, Path]] = (), manifest: object = "auto",
    manifest_extra: Optional[dict] = None, hash_files: Optional[Iterable[str]] = None,
    signature: Optional[bytes] = None, variant: Optional[str] = None,
    extra_members: Iterable[Tuple[str, bytes]] = (), top: Optional[str] = None,
    manifest_name: str = "manifest.json", sign_with: Optional[Path] = None,
    sign_comment: Optional[str] = None, modes: Optional[Dict[str, int]] = None,
    links: Iterable[Tuple[str, str, str]] = (),
) -> Tuple[Path, bytes]:
    """Write ``<tag>[-<variant>].tar.xz`` + ``.sha256`` into ``workdir``.

    ``wheels`` is [(distribution, wheel path)], shipped under ``vendor/`` and listed in the
    manifest as ``manifest[dist]``. ``hash_files`` names the ``files`` entries to cover in the
    manifest ``files`` map; the default covers EVERY shipped file except the wheels (the
    installer refuses unlisted members, so a bundle is complete unless a test says
    otherwise: ``extra_members`` are never listed). ``modes`` sets tar modes by member name;
    ``links`` is [(kind "sym"|"hard", name, target)] (hard targets are bundle-relative).
    ``manifest`` may be "auto" (built), ``None`` (omitted), or raw bytes.
    Returns (bundle path, manifest bytes)."""
    workdir.mkdir(parents=True, exist_ok=True)
    files = dict(files or {})
    top = tag if top is None else top
    if hash_files is None:
        hash_files = list(files)
    for _dist, wheel in wheels:
        files[f"vendor/{wheel.name}"] = wheel.read_bytes()
    if manifest == "auto":
        doc: dict = {"schema_version": 1, "project": "demo", "tag": tag}
        for dist, wheel in wheels:
            doc[dist] = manifest_entry(wheel)
        doc["files"] = {
            rel: {"sha256": sha(files[rel]), "size": len(files[rel])} for rel in hash_files
        }
        doc.update(manifest_extra or {})
        manifest_bytes: Optional[bytes] = (
            json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    else:
        manifest_bytes = manifest  # type: ignore[assignment]
    if sign_with is not None and manifest_bytes is not None:
        comment = sign_comment or (
            f"project=demo tag={tag} manifest_sha256={sha(manifest_bytes)}")
        signature = minisign_sign(sign_with, manifest_bytes, comment, workdir / "_sign")
    name = f"{tag}-{variant}.tar.xz" if variant else f"{tag}.tar.xz"
    asset = workdir / name
    with tarfile.open(asset, "w:xz") as tf:
        def add(rel: str, data: bytes) -> None:
            info = tarfile.TarInfo(name=f"{top}/{rel}" if top else rel)
            info.size = len(data)
            if modes and rel in modes:
                info.mode = modes[rel]
            tf.addfile(info, io.BytesIO(data))
        for rel, data in files.items():
            add(rel, data)
        for kind, name, target in links:
            link = tarfile.TarInfo(name=f"{top}/{name}")
            link.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
            link.linkname = target if kind == "sym" else f"{top}/{target}"
            tf.addfile(link)
        if manifest_bytes is not None:
            add(manifest_name, manifest_bytes)
        if signature is not None:
            add(manifest_name + ".minisig", signature)
        for rel, data in extra_members:
            add(rel, data)
    (workdir / f"{name}.sha256").write_text(f"{sha(asset.read_bytes())}  {name}\n")
    return asset, manifest_bytes or b""


def use_bundles(ns: dict, workdir: Path) -> List[str]:
    """Serve ``_download_asset`` from ``workdir``; the returned list records requests."""
    requested: List[str] = []

    def fake_download(tag, name, dest, token):
        requested.append(name)
        shutil.copy2(workdir / name, dest)

    ns["_download_asset"] = fake_download
    return requested


def args(**kw) -> argparse.Namespace:
    base = dict(version=None, scope="system", variant=None, config=None)
    base.update(kw)
    return argparse.Namespace(**base)


@contextlib.contextmanager
def as_root(ns: dict):
    """Pretend to be root: `geteuid()` is 0. The temp dirs belong to the test user, so the
    installer's "owned by the effective uid" rule is pointed at the real uid (a test that
    wants a foreign owner replaces ``ns["_expected_owner"]`` itself)."""
    real_uid = ns["os"].getuid()
    original = ns["_expected_owner"]
    ns["_expected_owner"] = lambda: real_uid
    try:
        with mock.patch.object(ns["os"], "geteuid", return_value=0):
            yield
    finally:
        ns["_expected_owner"] = original


def install(ns: dict, **kw) -> None:
    with as_root(ns):
        ns["do_install"](args(**kw), None)


def update(ns: dict, **kw) -> None:
    with as_root(ns):
        ns["do_update"](args(**kw), None)


def rollback(ns: dict, **kw) -> None:
    with as_root(ns):
        ns["do_rollback"](args(**kw), None)


def snapshot(root: Path) -> Dict[str, object]:
    """Path -> (kind, bytes|link target|mode) for everything under root except the lock."""
    if not root.exists():
        return {}
    out: Dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if rel == ".lock":
            continue
        if path.is_symlink():
            out[rel] = ("link", str(path.readlink()))
        elif path.is_dir():
            out[rel] = ("dir",)
        else:
            out[rel] = ("file", path.read_bytes())
    return out


def minisign_keypair(directory: Path, name: str = "k") -> Tuple[str, Path]:
    """(base64 public key, secret key path) from the real minisign binary (no password)."""
    directory.mkdir(parents=True, exist_ok=True)
    pub, sec = directory / f"{name}.pub", directory / f"{name}.key"
    subprocess.run(["minisign", "-G", "-W", "-f", "-p", str(pub), "-s", str(sec)],
                   check=True, capture_output=True)
    return pub.read_text().splitlines()[1].strip(), sec


def minisign_sign(secret: Path, data: bytes, comment: str, workdir: Path) -> bytes:
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / "to-sign"
    target.write_bytes(data)
    sig = workdir / "to-sign.minisig"
    subprocess.run(["minisign", "-S", "-s", str(secret), "-m", str(target), "-x", str(sig),
                    "-t", comment], check=True, capture_output=True)
    return sig.read_bytes()
