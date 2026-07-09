from __future__ import annotations

import hashlib
import json
import os
import socket
import tarfile
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

from groop import __version__
from groop.config import GroopConfig
from groop.model import Frame
from groop.record.ring import HistoryRing
from groop.record.writer import RecordWriter

try:
    import zstandard as _zstd
except ImportError:  # pragma: no cover - depends on optional extra.
    _zstd = None


def default_snapshot_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "groop" / "incidents"


def create(
    entity_key: str,
    ring: HistoryRing,
    frame: Frame,
    config: GroopConfig,
    *,
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    previous_frames: Iterable[Frame] = (),
    providers_status: dict[str, object] | None = None,
    systemctl_show: str | None = None,
    docker_inspect: dict[str, object] | None = None,
    now: float | None = None,
) -> Path:
    _ = ring
    ts = frame.ts if now is None else now
    out_dir = config.snapshots.dir or default_snapshot_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(entity_key)
    suffix = ".tar.zst" if _zstd is not None else ".tar"
    bundle_path = _unique_bundle_path(out_dir, f"groop-incident-{int(ts)}-{slug}", suffix)
    with tempfile.TemporaryDirectory(prefix="groop-snapshot-") as tmp:
        root = Path(tmp)
        frames = [*list(previous_frames)[-(config.snapshots.frames - 1) :], frame]
        _write_frames(root / "frames.jsonl", frames, config)
        _copy_cgroup_files(root / "entity" / "cgroup", cgroup_root, entity_key)
        (root / "entity").mkdir(exist_ok=True)
        (root / "entity" / "systemctl-show.txt").write_text(systemctl_show or "not collected\n", encoding="utf-8")
        docker_summary = _redact_docker(docker_inspect or {}, redact=config.snapshots.redact)
        (root / "entity" / "docker-inspect.json").write_text(json.dumps(docker_summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (root / "providers-status.json").write_text(json.dumps(providers_status or {}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        manifest = _manifest(root, entity_key=entity_key, frame=frame, config=config, privacy_redacted=config.snapshots.redact)
        (root / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _write_archive(root, bundle_path)
    return bundle_path


def inspect_bundle(path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="groop-inspect-") as tmp:
        root = Path(tmp)
        _extract_archive(path, root)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        mismatches = _hash_mismatches(root, manifest)
        status = "ok" if not mismatches else f"hash-mismatch:{len(mismatches)}"
        return "\n".join(
            (
                f"bundle: {path}",
                f"status: {status}",
                f"entity: {manifest.get('entity_key')}",
                f"ts: {manifest.get('ts')}",
                f"frames: {_frame_count(root / 'frames.jsonl')}",
                f"files: {len(manifest.get('files', []))}",
                f"privacy_redacted: {manifest.get('privacy_redacted')}",
                f"notable_files: {', '.join(_notable_files(manifest)) or '-'}",
                f"hash_failures: {', '.join(mismatches) if mismatches else '-'}",
            )
        )


def _write_frames(path: Path, frames: list[Frame], config: GroopConfig) -> None:
    with RecordWriter(path, config=config, started_at=frames[0].ts if frames else None) as writer:
        for frame in frames:
            writer.write_frame(frame)


def _copy_cgroup_files(dst: Path, cgroup_root: Path, entity_key: str) -> None:
    src = cgroup_root if entity_key == "" else cgroup_root / entity_key
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    wanted_prefixes = ("memory.", "cpu.", "io.", "pids.", "cgroup.")
    for path in sorted(src.iterdir()):
        if not path.is_file() or not path.name.startswith(wanted_prefixes):
            continue
        try:
            (dst / path.name).write_bytes(path.read_bytes())
        except OSError:
            continue
    for ancestor in _ancestor_keys(entity_key):
        ancestor_src = cgroup_root if ancestor == "" else cgroup_root / ancestor
        ancestor_dst = dst / "ancestors" / _slug(ancestor)
        for name in ("memory.min", "memory.low", "memory.high", "memory.max"):
            path = ancestor_src / name
            if path.is_file():
                ancestor_dst.mkdir(parents=True, exist_ok=True)
                try:
                    (ancestor_dst / name).write_bytes(path.read_bytes())
                except OSError:
                    pass


def _manifest(root: Path, *, entity_key: str, frame: Frame, config: GroopConfig, privacy_redacted: bool) -> dict[str, object]:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rel = path.relative_to(root).as_posix()
        files.append({"path": rel, "sha256": _sha256(path), "bytes": path.stat().st_size})
    return {
        "schema_version": 1,
        "groop_version": __version__,
        "host_id": socket.gethostname(),
        "ts": frame.ts,
        "entity_key": entity_key,
        "privacy_redacted": privacy_redacted,
        "config_digest": config.digest(),
        "created_at": time.time(),
        "files": files,
    }


def _write_archive(root: Path, bundle_path: Path) -> None:
    if bundle_path.suffix == ".zst":
        assert _zstd is not None
        with tempfile.NamedTemporaryFile(suffix=".tar") as tmp_tar:
            with tarfile.open(tmp_tar.name, "w") as tar:
                _add_tree(tar, root)
            tmp_tar.seek(0)
            compressor = _zstd.ZstdCompressor()
            bundle_path.write_bytes(compressor.compress(tmp_tar.read()))
        return
    with tarfile.open(bundle_path, "w") as tar:
        _add_tree(tar, root)


def _extract_archive(path: Path, dst: Path) -> None:
    if path.suffix == ".zst":
        if _zstd is None:
            raise RuntimeError("zstandard is required to inspect .tar.zst bundles")
        data = _zstd.ZstdDecompressor().decompress(path.read_bytes())
        with tempfile.NamedTemporaryFile(suffix=".tar") as tmp_tar:
            tmp_tar.write(data)
            tmp_tar.flush()
            with tarfile.open(tmp_tar.name, "r") as tar:
                _safe_extract(tar, dst)
        return
    with tarfile.open(path, "r") as tar:
        _safe_extract(tar, dst)


def _add_tree(tar: tarfile.TarFile, root: Path) -> None:
    for path in sorted(root.rglob("*")):
        tar.add(path, arcname=path.relative_to(root).as_posix())


def _safe_extract(tar: tarfile.TarFile, dst: Path) -> None:
    dst_resolved = dst.resolve()
    members = tar.getmembers()
    for member in members:
        target = (dst / member.name).resolve()
        if target != dst_resolved and dst_resolved not in target.parents:
            raise RuntimeError(f"refusing unsafe archive member: {member.name}")
    tar.extractall(dst, members=members, filter="data")


def _hash_mismatches(root: Path, manifest: dict[str, object]) -> list[str]:
    mismatches: list[str] = []
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path"))
        path = root / rel
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            mismatches.append(rel)
    return mismatches


def _notable_files(manifest: dict[str, object]) -> list[str]:
    wanted = {
        "frames.jsonl",
        "manifest.json",
        "providers-status.json",
        "entity/systemctl-show.txt",
        "entity/docker-inspect.json",
    }
    out = []
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path"))
        if path in wanted or path.startswith("entity/cgroup/"):
            out.append(path)
    return sorted(out)


def _redact_docker(payload: dict[str, object], *, redact: bool) -> dict[str, object]:
    allowed = {"Id", "Name", "Image", "Config", "State"}
    out = {key: value for key, value in payload.items() if key in allowed}
    if redact and isinstance(out.get("Config"), dict):
        config = dict(out["Config"])
        config.pop("Env", None)
        config.pop("Labels", None)
        out["Config"] = config
    return out


def _frame_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line and json.loads(line).get("type") == "frame")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(entity_key: str) -> str:
    text = entity_key.strip("/") or "root"
    out = "".join(ch if ch.isalnum() else "-" for ch in text)
    return "-".join(part for part in out.split("-") if part)[:80] or "root"


def _unique_bundle_path(out_dir: Path, stem: str, suffix: str) -> Path:
    path = out_dir / f"{stem}{suffix}"
    if not path.exists():
        return path
    for index in range(1, 10_000):
        path = out_dir / f"{stem}-{index}{suffix}"
        if not path.exists():
            return path
    raise RuntimeError(f"could not allocate unique snapshot path in {out_dir}")


def _ancestor_keys(entity_key: str) -> list[str]:
    parts = [part for part in entity_key.split("/") if part]
    out = [""]
    for index in range(1, len(parts)):
        out.append("/".join(parts[:index]))
    return out
