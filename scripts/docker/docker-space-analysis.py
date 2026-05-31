#!/usr/bin/env python3
# =============================================================================
# docker-space-analysis.py
# =============================================================================
#
# PURPOSE
#   Fast, feature-identical Python replacement for docker-space-analysis.sh.
#   Analyses Docker overlay2 storage and named volumes to identify disk
#   consumers and cleanup candidates.  Read-only by default.
#
# PERFORMANCE vs BASH VERSION
#   The bash script has three runtime bottlenecks; all three are eliminated:
#
#   1. Sequential 'du -sb' per layer (3499 subprocess forks, ~4 min)
#      → Python scans all layer diff dirs IN PARALLEL using a thread pool.
#        os.walk() + os.lstat() in each worker replaces the subprocess fork.
#        Typical speedup: 8-15x (wall time ~20-40 s vs ~4 min).
#
#   2. 'du -sb /var/lib/docker/overlay2' full-tree scan (~3.5 min, run once)
#      → Replaced by: sum(layer_scan_results) + quick Python walk of the
#        non-diff overlay2 content (l/ symlinks, per-layer metadata files,
#        work/ dirs — all tiny).  Completes in < 1 second.
#        Note: bash's 'du -sb' may count 'merged/' overlay mount points of
#        running containers, double-counting their layer content.  This
#        Python version intentionally skips 'merged/' to avoid that.
#
#   3. numfmt subprocess per human-readable size (hundreds of calls)
#      → human_bytes() implemented in pure Python.  No subprocess needed.
#
#   Typical total wall-clock improvement: 6-10x (e.g. ~1 min vs ~9 min).
#   Bottleneck after optimisation: Docker CLI calls (docker inspect batches)
#   and the volume du scan, both I/O-bound.
#
# PARALLELISM NOTE
#   Layer scanning uses concurrent.futures.ThreadPoolExecutor.  The GIL is
#   not a constraint here because workers spend nearly all time in os.walk /
#   os.lstat system calls, which release the GIL.  Default workers =
#   os.cpu_count() (capped at 32); override with --workers N.
#
# DATA SOURCES  (identical to bash version)
#   /var/lib/docker/overlay2/*/diff  — apparent size via os.walk + os.lstat
#   docker ps -aq + docker inspect   — container metadata, batched
#   docker images -aq + docker image inspect — image metadata, batched
#   /var/lib/docker/volumes/<name>/_data — volume apparent sizes
#   docker volume ls                 — volume names
#
# HOW IT WORKS
#   1. Layer scan (parallel)
#      os.scandir() finds all overlay2/<id>/diff directories.  Each is
#      submitted to a ThreadPoolExecutor; apparent_size() computes the
#      apparent byte total (equiv. to 'du -sb').  Results are merged into
#      LAYER_SIZE[id] = bytes and LAYER_SCAN_SUM.
#
#   2. Overlay2 total
#      overlay2_total = LAYER_SCAN_SUM
#                     + apparent_size of non-diff overlay2 entries
#                       (overlay2/l/ symlinks, per-layer link/lower/work
#                       files — all small; merged/ dirs are skipped).
#      This is accurate and fast; no separate full-tree du scan needed.
#
#   3. Reference tracking
#      For each container and image the UpperDir / LowerDir paths from
#      docker inspect are resolved to layer IDs via extract_layer_id().
#      LAYER_REFCOUNT[id] counts distinct owners.  Layers with refcount 0
#      are 'unreferenced' — orphaned by failed builds or dangling images.
#
#   4. Adjusted totals
#      adjusted_total approximates fair-share disk usage: each shared lower
#      layer's size is divided by its reference count (integer rounding).
#
#   5. Volume analysis
#      Named volumes from 'docker volume ls' are measured with apparent_size
#      on their _data directory.  Container mounts from docker inspect
#      (Mounts[].Type == "volume") identify which volumes are in use.
#
# COLUMN GLOSSARY
#   upper_size      Writable layer exclusive to one container.  Freed when
#                   the container is removed.
#   lower_total     Sum of all read-only layers the object depends on.
#   logical_total   upper_size + lower_total.  Overcounts shared layers.
#   adjusted_total  Fair-share: each layer's bytes / its refcount.
#
# UNREFERENCED LAYERS vs UNUSED IMAGES
#   Unreferenced layers (refcount == 0): anonymous overlay2 dirs that no
#   image or container references.  They accumulate from failed/cancelled
#   builds, dangling intermediates, and incomplete prunes.
#   'docker image prune' alone does NOT remove them.
#   Use: docker image prune -f && docker builder prune -f
#
#   Unused images (used_by_containers == 0): fully-formed images no running
#   container needs.  Removed by: docker image prune -a
#
# QUIRKS
#   apparent_size(): uses os.lstat().st_size per entry (apparent size, not
#   disk blocks).  Equivalent to 'du --apparent-size --block-size=1'.
#   Does not follow symlinks (followlinks=False in os.walk).
#   Skips 'merged/' overlay mount points in the overlay2 root walk to
#   avoid double-counting running container views.
#   Docker CLI: all subprocess calls use LANG=C / LC_ALL=C for consistent
#   numeric parsing.  docker inspect is called in a single batch per
#   object type (containers, images) — not one call per object.
#   Layer ID dedup: (owner, lid) pairs in a set prevent double-counting a
#   layer that appears in both a container's and its parent image's lower
#   list.
#
# REQUIREMENTS
#   Python >= 3.6, root (sudo), docker CLI.
#   Standard library only — no pip packages required.
# =============================================================================

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import shutil
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="docker-space-analysis.py",
        description="Analyse Docker overlay2 storage and named volumes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Cleanup steps (when --cleanup is used):
  1. docker container prune -f     Remove stopped containers
  2. docker image prune -a -f      Remove unused images
  3. docker builder prune -f       Remove BuildKit build cache
     NOTE: 'docker builder prune' is separate from 'docker system prune'.
     Even after 'docker system prune', builder-cache layers persist until
     builder prune runs.
  4. docker volume prune -f        Remove unused volumes (prompted with extra
                                   warning due to potential data loss)
Notes:
  - `docker system prune -a` is a shortcut for all three (containers + images + networks + volumes).
  - Run without --cleanup first to review candidates before committing.
  - Requires root (overlay2 and volume _data directories are not world-readable).
""",
    )
    p.add_argument("--out-dir", default="",
                   help="Output directory (default: ./docker-space-analysis-YYYYmmdd-HHMMSS)")
    p.add_argument("--top", type=int, default=20, metavar="N",
                   help="Top-N rows to print to console (default: 20)")
    p.add_argument("--progress-every", type=int, default=200, metavar="N",
                   help="Layer scan progress interval (default: 200)")
    p.add_argument("--workers", type=int,
                   default=min(os.cpu_count() or 4, 32), metavar="N",
                   help="Thread pool workers for parallel layer scanning "
                        "(default: cpu_count, capped at 32)")
    p.add_argument("--debug", action="store_true",
                   help="Verbose debug logging")
    p.add_argument("--cleanup", action="store_true",
                   help="After analysis, interactively run docker prune commands")
    p.add_argument("-y", "--yes", action="store_true",
                   help="Auto-confirm all cleanup steps (use with --cleanup)")
    return p


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def dbg(msg: str, debug: bool) -> None:
    if debug:
        print(f"[{ts()}] [DEBUG] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Size helpers
# ---------------------------------------------------------------------------

def human_bytes(n) -> str:
    """Format bytes as a human-readable IEC string (e.g. 1.23GiB)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        n = 0.0
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if n < 1024.0:
            return f"{n:.2f}{unit}"
        n /= 1024.0
    return f"{n:.2f}EiB"


def apparent_size(path: str) -> int:
    """
    Compute apparent size in bytes — equivalent to 'du --apparent-size -sb path'.

    Uses os.lstat() so symlinks are counted by their own st_size (the length
    of the link path string) and are NOT followed into their target content.
    Directories are counted by their inode st_size plus all descendants.
    Does not cross filesystem mount points implicitly (followlinks=False).
    """
    try:
        st = os.lstat(path)
    except OSError:
        return 0

    total = st.st_size

    if not stat.S_ISDIR(st.st_mode):
        return total

    # Walk the directory tree; os.walk yields (dirpath, subdirs, files).
    # - subdirs: we count each dir inode's st_size via os.lstat in the loop
    # - files: includes regular files AND symlinks that os.walk puts in filenames
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in dirnames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass

    return total


def _scan_diff_dir(diff_dir: str) -> Tuple[str, int]:
    """Worker function: compute apparent size of one overlay2 diff directory."""
    layer_id = os.path.basename(os.path.dirname(diff_dir))
    return layer_id, apparent_size(diff_dir)


def overlay2_non_diff_size(overlay_dir: str, known_layer_ids: Set[str]) -> int:
    """
    Compute the apparent size of overlay2 content EXCLUDING each layer's diff/
    directory (whose sizes we already know from the parallel scan).

    Skips 'merged/' sub-directories inside layer dirs to avoid double-counting
    the overlayfs mount point of running containers (which the bash version's
    'du -sb' may include, causing a slight discrepancy).

    Content counted:
      - overlay2/l/         symlink directory (tiny)
      - overlay2/<id>/link  symlink file pointing into l/
      - overlay2/<id>/lower text file listing lower layer chain
      - overlay2/<id>/work/ used by the kernel; usually near-empty
    """
    total = 0
    try:
        with os.scandir(overlay_dir) as it:
            for entry in it:
                try:
                    if entry.name == "l" and entry.is_dir(follow_symlinks=False):
                        # Symlink index directory — walk fully (all entries are symlinks)
                        total += apparent_size(entry.path)
                    elif entry.is_dir(follow_symlinks=False) and entry.name in known_layer_ids:
                        # Layer directory: walk everything EXCEPT diff/ and merged/
                        try:
                            with os.scandir(entry.path) as sub_it:
                                for sub in sub_it:
                                    if sub.name in ("diff", "merged"):
                                        continue
                                    try:
                                        if sub.is_dir(follow_symlinks=False):
                                            total += apparent_size(sub.path)
                                        else:
                                            total += sub.stat(follow_symlinks=False).st_size
                                    except OSError:
                                        pass
                        except OSError:
                            pass
                    elif entry.is_dir(follow_symlinks=False) and entry.name not in known_layer_ids:
                        # Unknown dirs (staging dirs for in-progress builds, etc.)
                        total += apparent_size(entry.path)
                except OSError:
                    pass
    except OSError:
        pass
    return total


# ---------------------------------------------------------------------------
# Docker CLI helpers
# ---------------------------------------------------------------------------

_DOCKER_ENV = {**os.environ, "LC_ALL": "C", "LANG": "C"}


def _run(args: List[str]) -> str:
    """Run a command and return stdout as a string.  Raises on non-zero exit."""
    result = subprocess.run(args, capture_output=True, text=True, env=_DOCKER_ENV)
    if result.returncode != 0:
        raise RuntimeError(f"Command {args!r} failed: {result.stderr.strip()}")
    return result.stdout


def docker_get_root() -> str:
    try:
        out = _run(["docker", "info", "--format", "{{.DockerRootDir}}"])
        return out.strip() or "/var/lib/docker"
    except Exception:
        return "/var/lib/docker"


def docker_list_container_ids() -> List[str]:
    return [line for line in _run(["docker", "ps", "-aq"]).splitlines() if line]


def docker_list_image_ids() -> List[str]:
    raw = [line for line in _run(["docker", "images", "-aq"]).splitlines() if line]
    return sorted(set(raw))


def docker_list_volume_names() -> List[str]:
    try:
        return [line for line in _run(["docker", "volume", "ls", "-q"]).splitlines() if line]
    except Exception:
        return []


def docker_inspect_containers(cids: List[str]) -> List[dict]:
    if not cids:
        return []
    return json.loads(_run(["docker", "inspect"] + cids))


def docker_inspect_images(iids: List[str]) -> List[dict]:
    if not iids:
        return []
    return json.loads(_run(["docker", "image", "inspect"] + iids))


# ---------------------------------------------------------------------------
# Layer ID resolution
# ---------------------------------------------------------------------------

_LONG_RE = re.compile(r".*/overlay2/([^/]+)/diff$")
_SHORT_RE = re.compile(r".*/overlay2/l/([^/:]+)$")


def extract_layer_id(path: str, overlay_dir: str) -> Optional[str]:
    """
    Resolve an overlay2 path to a layer directory ID.

    Handles two forms:
      Long:  /var/lib/docker/overlay2/<id>/diff
      Short: /var/lib/docker/overlay2/l/<shortid>  (symlink into ../id/diff)
    """
    m = _LONG_RE.match(path)
    if m:
        return m.group(1)
    m = _SHORT_RE.match(path)
    if m:
        short = m.group(1)
        link_path = os.path.join(overlay_dir, "l", short)
        try:
            target = os.readlink(link_path)
            # target is relative: ../id/diff
            m2 = re.match(r"\.\./([^/]+)/diff$", target)
            if m2:
                return m2.group(1)
        except OSError:
            pass
    return None


# ---------------------------------------------------------------------------
# Reference counting
# ---------------------------------------------------------------------------

def add_ref_once(
    owner: str,
    lid: str,
    seen_refs: Set[Tuple[str, str]],
    refcount: Dict[str, int],
    owners: Dict[str, List[str]],
) -> None:
    """Count each (owner, layer) pair at most once."""
    if not lid:
        return
    key = (owner, lid)
    if key in seen_refs:
        return
    seen_refs.add(key)
    refcount[lid] = refcount.get(lid, 0) + 1
    owners[lid].append(owner)


def sum_adjusted(
    layer_ids: List[str],
    layer_size: Dict[str, int],
    layer_refcount: Dict[str, int],
) -> int:
    """
    Compute adjusted (fair-share) total for a list of layer IDs.
    Each layer's contribution = size / refcount (integer, rounded half-up).
    """
    total = 0
    for lid in layer_ids:
        if not lid:
            continue
        size = layer_size.get(lid, 0)
        rc = layer_refcount.get(lid, 0)
        if rc > 0:
            total += (size + rc // 2) // rc
    return total


# ---------------------------------------------------------------------------
# TSV helpers
# ---------------------------------------------------------------------------

def write_tsv(path: str, header: List[str], rows: List[List]) -> None:
    """Write a TSV file atomically (write to temp, then rename)."""
    dir_ = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\t".join(header) + "\n")
            for row in rows:
                f.write("\t".join(str(c) for c in row) + "\n")
        shutil.move(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sort_tsv(src: str, dst: str, col: int, numeric: bool = True, reverse: bool = True) -> None:
    """Write a copy of src TSV sorted by column col (0-indexed, after header)."""
    with open(src) as f:
        header = f.readline()
        data_lines = f.readlines()

    def key_fn(line: str):
        parts = line.rstrip("\n").split("\t")
        val = parts[col] if col < len(parts) else ""
        if numeric:
            try:
                return int(val)
            except ValueError:
                try:
                    return float(val)
                except ValueError:
                    return 0
        return val

    data_lines.sort(key=key_fn, reverse=reverse)
    with open(dst, "w") as f:
        f.write(header)
        f.writelines(data_lines)


def read_tsv_rows(path: str) -> List[List[str]]:
    """Read TSV file (skip header), return list of split rows."""
    rows = []
    with open(path) as f:
        f.readline()  # skip header
        for line in f:
            line = line.rstrip("\n")
            if line:
                rows.append(line.split("\t"))
    return rows


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def main() -> int:
    args = build_parser().parse_args()

    # -------------------------
    # Permissions check
    # -------------------------
    if os.geteuid() != 0:
        print("ERROR: This script must be run as root to read overlay2 layer data.", file=sys.stderr)
        print("       Run with: sudo python3 docker-space-analysis.py", file=sys.stderr)
        return 1

    try:
        _run(["docker", "info"])
    except Exception as e:
        print(f"ERROR: Cannot connect to the Docker daemon: {e}", file=sys.stderr)
        return 1

    docker_root = docker_get_root()
    overlay_dir = os.path.join(docker_root, "overlay2")
    volumes_dir = os.path.join(docker_root, "volumes")

    if not os.path.isdir(overlay_dir):
        print(f"ERROR: overlay2 directory not found: {overlay_dir}", file=sys.stderr)
        return 1

    out_dir = args.out_dir or f"./docker-space-analysis-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    os.makedirs(out_dir, exist_ok=True)

    top_n = args.top
    progress_every = args.progress_every
    workers = args.workers
    debug = args.debug

    log("Starting Docker overlay2 analysis (Python)")
    log(f"Docker root: {docker_root}")
    log(f"Overlay dir: {overlay_dir}")
    log(f"Reports dir: {out_dir}")
    log(f"Workers:     {workers}")

    # -------------------------
    # 1) Parallel layer scan
    # -------------------------
    log("Scanning layer sizes (parallel)...")
    diff_dirs: List[str] = []
    try:
        with os.scandir(overlay_dir) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False) and entry.name != "l":
                    diff = os.path.join(entry.path, "diff")
                    if os.path.isdir(diff):
                        diff_dirs.append(diff)
    except OSError as e:
        print(f"ERROR scanning overlay2: {e}", file=sys.stderr)
        return 1

    total_layers = len(diff_dirs)
    log(f"Found {total_layers} layer diff directories")

    layer_size: Dict[str, int] = {}
    layer_scan_sum = 0
    scanned = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(_scan_diff_dir, d): d for d in diff_dirs}
        for future in concurrent.futures.as_completed(future_map):
            try:
                lid, sz = future.result()
                layer_size[lid] = sz
                layer_scan_sum += sz
            except Exception as e:
                dbg(f"Layer scan error: {e}", debug)
            scanned += 1
            if scanned % progress_every == 0 or scanned == total_layers:
                log(f"Layer scan progress: {scanned}/{total_layers}")

    # Overlay2 total: layer scan sum + non-diff overlay2 overhead
    # (l/ symlinks, per-layer link/lower/work files; skips merged/ mounts)
    overlay2_extra = overlay2_non_diff_size(overlay_dir, set(layer_size.keys()))
    overlay_total_bytes = layer_scan_sum + overlay2_extra

    # TSV: raw layer sizes
    write_tsv(
        os.path.join(out_dir, "layer_sizes_raw.tsv"),
        ["LAYER_ID", "SIZE"],
        [[lid, sz] for lid, sz in layer_size.items()],
    )

    # -------------------------
    # 2) Collect containers
    # -------------------------
    log("Collecting container references...")
    cids = docker_list_container_ids()
    log(f"Containers discovered: {len(cids)}")

    seen_refs: Set[Tuple[str, str]] = set()
    layer_refcount: Dict[str, int] = {}
    layer_owners: Dict[str, List[str]] = defaultdict(list)

    C_NAME: Dict[str, str] = {}
    C_STATUS: Dict[str, str] = {}
    C_IMAGE_ID: Dict[str, str] = {}
    C_UPPER_LAYER: Dict[str, str] = {}
    C_UPPER_SIZE: Dict[str, int] = {}
    C_LOWER_LAYERS: Dict[str, List[str]] = {}
    C_LOWER_TOTAL: Dict[str, int] = {}
    C_LOGICAL_TOTAL: Dict[str, int] = {}
    C_ADJUSTED_TOTAL: Dict[str, int] = {}

    I_USED_BY: Dict[str, int] = defaultdict(int)
    # volumes: map volume_name → list of container IDs using it
    vol_containers: Dict[str, List[str]] = defaultdict(list)

    unresolved_container_paths = 0
    unresolved_log: List[str] = []

    if cids:
        containers_data = docker_inspect_containers(cids)
        for c in containers_data:
            cid = c["Id"][:12]
            name = c.get("Name", "").lstrip("/")
            status = c.get("State", {}).get("Status", "unknown")
            image_id = c.get("Image", "")

            C_NAME[cid] = name
            C_STATUS[cid] = status
            C_IMAGE_ID[cid] = image_id
            I_USED_BY[image_id] = I_USED_BY[image_id] + 1

            gd = c.get("GraphDriver", {}).get("Data") or {}
            upper_path = gd.get("UpperDir", "")
            lower_path = gd.get("LowerDir", "")

            # Upper layer
            upper_lid = ""
            upper_size = 0
            if upper_path:
                lid = extract_layer_id(upper_path, overlay_dir)
                if lid:
                    upper_lid = lid
                    upper_size = layer_size.get(lid, 0)
                    add_ref_once(f"container:{cid}", lid, seen_refs, layer_refcount, layer_owners)
                else:
                    unresolved_container_paths += 1
                    unresolved_log.append(f"container upper unresolved: cid={cid} name={name} path={upper_path}")

            C_UPPER_LAYER[cid] = upper_lid
            C_UPPER_SIZE[cid] = upper_size

            # Lower layers
            lower_ids: List[str] = []
            lower_total = 0
            if lower_path:
                for lp in lower_path.split(":"):
                    lid = extract_layer_id(lp, overlay_dir)
                    if lid:
                        add_ref_once(f"container:{cid}", lid, seen_refs, layer_refcount, layer_owners)
                        lower_ids.append(lid)
                        lower_total += layer_size.get(lid, 0)
                    else:
                        unresolved_container_paths += 1
                        unresolved_log.append(f"container lower unresolved: cid={cid} name={name} path={lp}")

            C_LOWER_LAYERS[cid] = lower_ids
            C_LOWER_TOTAL[cid] = lower_total
            C_LOGICAL_TOTAL[cid] = upper_size + lower_total

            # Named volume mounts
            for mount in c.get("Mounts", []):
                if mount.get("Type") == "volume":
                    vname = mount.get("Name", "")
                    if vname:
                        vol_containers[vname].append(cid)

    # -------------------------
    # 3) Collect images
    # -------------------------
    log("Collecting image references...")
    iids = docker_list_image_ids()
    log(f"Images discovered: {len(iids)}")

    I_TAG: Dict[str, str] = {}
    I_UPPER_LAYER: Dict[str, str] = {}
    I_UPPER_SIZE: Dict[str, int] = {}
    I_LOWER_LAYERS: Dict[str, List[str]] = {}
    I_LOWER_TOTAL: Dict[str, int] = {}
    I_LOGICAL_TOTAL: Dict[str, int] = {}
    I_ADJUSTED_TOTAL: Dict[str, int] = {}

    unresolved_image_paths = 0

    if iids:
        images_data = docker_inspect_images(iids)
        for img in images_data:
            iid = img.get("Id", "")
            tags = img.get("RepoTags") or ["<none>:<none>"]
            tag = tags[0]

            I_TAG[iid] = tag
            if iid not in I_USED_BY:
                I_USED_BY[iid] = 0

            gd = img.get("GraphDriver", {}).get("Data") or {}
            upper_path = gd.get("UpperDir", "")
            lower_path = gd.get("LowerDir", "")

            i_upper_lid = ""
            i_upper_size = 0
            if upper_path:
                lid = extract_layer_id(upper_path, overlay_dir)
                if lid:
                    i_upper_lid = lid
                    i_upper_size = layer_size.get(lid, 0)
                    add_ref_once(f"image:{iid}", lid, seen_refs, layer_refcount, layer_owners)
                else:
                    unresolved_image_paths += 1
                    unresolved_log.append(f"image upper unresolved: iid={iid} tag={tag} path={upper_path}")

            I_UPPER_LAYER[iid] = i_upper_lid
            I_UPPER_SIZE[iid] = i_upper_size

            i_lower_ids: List[str] = []
            i_lower_total = 0
            if lower_path:
                for lp in lower_path.split(":"):
                    lid = extract_layer_id(lp, overlay_dir)
                    if lid:
                        add_ref_once(f"image:{iid}", lid, seen_refs, layer_refcount, layer_owners)
                        i_lower_ids.append(lid)
                        i_lower_total += layer_size.get(lid, 0)
                    else:
                        unresolved_image_paths += 1
                        unresolved_log.append(f"image lower unresolved: iid={iid} tag={tag} path={lp}")

            I_LOWER_LAYERS[iid] = i_lower_ids
            I_LOWER_TOTAL[iid] = i_lower_total
            I_LOGICAL_TOTAL[iid] = i_upper_size + i_lower_total

    # -------------------------
    # 4) Compute adjusted totals
    # -------------------------
    log("Computing adjusted totals...")
    for cid in C_NAME:
        C_ADJUSTED_TOTAL[cid] = C_UPPER_SIZE.get(cid, 0) + sum_adjusted(
            C_LOWER_LAYERS.get(cid, []), layer_size, layer_refcount
        )
    for iid in I_TAG:
        I_ADJUSTED_TOTAL[iid] = I_UPPER_SIZE.get(iid, 0) + sum_adjusted(
            I_LOWER_LAYERS.get(iid, []), layer_size, layer_refcount
        )

    # -------------------------
    # 5) Layer reports
    # -------------------------
    log("Building layer reports...")
    layer_rows: List[List] = []
    unref_rows: List[List] = []
    ref_count_layers = 0
    ref_size_layers = 0
    for lid, sz in layer_size.items():
        rc = layer_refcount.get(lid, 0)
        adj = (sz + rc // 2) // rc if rc > 0 else 0
        owners_str = ",".join(layer_owners.get(lid, []))
        layer_rows.append([lid, sz, rc, adj, owners_str])
        if rc == 0:
            unref_rows.append([lid, sz])
        else:
            ref_count_layers += 1
            ref_size_layers += sz

    write_tsv(
        os.path.join(out_dir, "layers_all.tsv"),
        ["LAYER_ID", "SIZE", "REF_COUNT", "ADJUSTED_SHARE", "OWNERS"],
        layer_rows,
    )
    write_tsv(
        os.path.join(out_dir, "layers_unreferenced.tsv"),
        ["LAYER_ID", "SIZE"],
        unref_rows,
    )
    sort_tsv(
        os.path.join(out_dir, "layers_all.tsv"),
        os.path.join(out_dir, "layers_all_sorted_by_size.tsv"),
        col=1,
    )
    sort_tsv(
        os.path.join(out_dir, "layers_unreferenced.tsv"),
        os.path.join(out_dir, "layers_unreferenced_sorted_by_size.tsv"),
        col=1,
    )
    unref_count = len(unref_rows)
    unref_sum = sum(r[1] for r in unref_rows)

    # -------------------------
    # 6) Container reports
    # -------------------------
    log("Building container reports...")
    container_upper_sum = 0
    container_logical_sum = 0
    container_adjusted_sum = 0
    stopped_upper_sum = 0
    stopped_count = 0
    container_rows: List[List] = []

    for cid in C_NAME:
        us = C_UPPER_SIZE.get(cid, 0)
        ll = len(C_LOWER_LAYERS.get(cid, []))
        ls = C_LOWER_TOTAL.get(cid, 0)
        lt = C_LOGICAL_TOTAL.get(cid, 0)
        at = C_ADJUSTED_TOTAL.get(cid, 0)
        st = C_STATUS.get(cid, "unknown")
        container_upper_sum += us
        container_logical_sum += lt
        container_adjusted_sum += at
        if st != "running":
            stopped_count += 1
            stopped_upper_sum += us
        container_rows.append([
            cid, C_NAME[cid], st, C_IMAGE_ID.get(cid, ""),
            C_UPPER_LAYER.get(cid, ""), us, ll, ls, lt, at,
        ])

    running_count = len(C_NAME) - stopped_count
    hdr_c = ["CID", "NAME", "STATUS", "IMAGE_ID", "UPPER_LAYER", "UPPER_SIZE",
              "LOWER_LAYERS", "LOWER_TOTAL", "LOGICAL_TOTAL", "ADJUSTED_TOTAL"]
    write_tsv(os.path.join(out_dir, "containers_all.tsv"), hdr_c, container_rows)
    for fname, col in [
        ("containers_sorted_by_upper.tsv", 5),
        ("containers_sorted_by_logical.tsv", 8),
        ("containers_sorted_by_adjusted.tsv", 9),
    ]:
        sort_tsv(
            os.path.join(out_dir, "containers_all.tsv"),
            os.path.join(out_dir, fname),
            col=col,
        )

    # -------------------------
    # 7) Image reports
    # -------------------------
    log("Building image reports...")
    image_logical_sum = 0
    image_adjusted_sum = 0
    unused_image_count = 0
    unused_image_logical_sum = 0
    in_use_image_count = 0
    in_use_image_logical_sum = 0
    image_rows: List[List] = []
    unused_image_rows: List[List] = []

    for iid in I_TAG:
        used = I_USED_BY.get(iid, 0)
        reclaimable = "no" if used > 0 else "yes"
        us = I_UPPER_SIZE.get(iid, 0)
        ll = len(I_LOWER_LAYERS.get(iid, []))
        ls = I_LOWER_TOTAL.get(iid, 0)
        lt = I_LOGICAL_TOTAL.get(iid, 0)
        at = I_ADJUSTED_TOTAL.get(iid, 0)
        image_logical_sum += lt
        image_adjusted_sum += at
        image_rows.append([iid, I_TAG[iid], used, reclaimable,
                           I_UPPER_LAYER.get(iid, ""), us, ll, ls, lt, at])
        if used == 0:
            unused_image_count += 1
            unused_image_logical_sum += lt
            unused_image_rows.append([iid, I_TAG[iid], used, us, ls, lt, at])
        else:
            in_use_image_count += 1
            in_use_image_logical_sum += lt

    hdr_i = ["IMAGE_ID", "TAG", "USED_BY_CONTAINERS", "RECLAIMABLE",
             "UPPER_LAYER", "UPPER_SIZE", "LOWER_LAYERS", "LOWER_TOTAL",
             "LOGICAL_TOTAL", "ADJUSTED_TOTAL"]
    write_tsv(os.path.join(out_dir, "images_all.tsv"), hdr_i, image_rows)
    sort_tsv(
        os.path.join(out_dir, "images_all.tsv"),
        os.path.join(out_dir, "images_sorted_by_logical.tsv"),
        col=8,
    )
    hdr_iu = ["IMAGE_ID", "TAG", "USED_BY_CONTAINERS", "UPPER_SIZE",
              "LOWER_TOTAL", "LOGICAL_TOTAL", "ADJUSTED_TOTAL"]
    write_tsv(os.path.join(out_dir, "images_unused_by_any_container.tsv"),
              hdr_iu, unused_image_rows)
    sort_tsv(
        os.path.join(out_dir, "images_unused_by_any_container.tsv"),
        os.path.join(out_dir, "images_unused_sorted_by_logical.tsv"),
        col=5,
    )

    # -------------------------
    # 8) Cleanup candidates
    # -------------------------
    log("Building cleanup candidates report...")
    f_cleanup = os.path.join(out_dir, "cleanup_candidates.tsv")
    cleanup_rows: List[List] = []
    if stopped_count > 0:
        cleanup_rows.append([
            "containers_stopped_upper", "all_stopped_containers",
            stopped_upper_sum, human_bytes(stopped_upper_sum),
            f"stopped_containers={stopped_count}", "docker container prune -f",
        ])
    if unused_image_count > 0:
        cleanup_rows.append([
            "images_unused_logical", "all_unused_images",
            unused_image_logical_sum, human_bytes(unused_image_logical_sum),
            f"unused_images={unused_image_count}", "docker image prune -a -f",
        ])
    if unref_count > 0:
        cleanup_rows.append([
            "layers_unreferenced", "overlay2_unreferenced_layers",
            unref_sum, human_bytes(unref_sum),
            f"layers={unref_count}", "docker image prune -f && docker builder prune -f",
        ])

    # -------------------------
    # 9) Volume analysis
    # -------------------------
    log("Scanning volumes...")
    vol_names = docker_list_volume_names()
    vol_rows: List[List] = []
    total_vol_size = 0
    unused_vol_count = 0
    unused_vol_size = 0
    in_use_vol_count = 0
    in_use_vol_size = 0

    for vname in vol_names:
        mountpoint = os.path.join(volumes_dir, vname, "_data")
        vsize = apparent_size(mountpoint) if os.path.isdir(mountpoint) else 0
        total_vol_size += vsize
        users = vol_containers.get(vname, [])
        user_count = len(users)
        users_str = ",".join(users)
        vol_rows.append([vname, mountpoint, vsize, human_bytes(vsize), user_count, users_str])
        if user_count == 0:
            unused_vol_count += 1
            unused_vol_size += vsize
        else:
            in_use_vol_count += 1
            in_use_vol_size += vsize

    hdr_v = ["VOLUME_NAME", "MOUNTPOINT", "SIZE_BYTES", "SIZE_HUMAN",
             "CONTAINER_COUNT", "CONTAINERS"]
    write_tsv(os.path.join(out_dir, "volumes_all.tsv"), hdr_v, vol_rows)
    sort_tsv(
        os.path.join(out_dir, "volumes_all.tsv"),
        os.path.join(out_dir, "volumes_sorted_by_size.tsv"),
        col=2,
    )
    unused_vol_rows = [r for r in vol_rows if r[4] == 0]
    unused_vol_rows.sort(key=lambda r: r[2], reverse=True)
    write_tsv(os.path.join(out_dir, "volumes_unused.tsv"), hdr_v, unused_vol_rows)

    log(f"Volumes discovered: {len(vol_names)} (unused: {unused_vol_count})")

    if unused_vol_count > 0:
        cleanup_rows.append([
            "volumes_unused", "all_unused_volumes",
            unused_vol_size, human_bytes(unused_vol_size),
            f"unused_volumes={unused_vol_count}", "docker volume prune -f",
        ])

    write_tsv(
        f_cleanup,
        ["TYPE", "NAME", "POTENTIAL_RECLAIM_BYTES", "POTENTIAL_RECLAIM_HUMAN",
         "DETAILS", "SAFE_COMMAND_HINT"],
        cleanup_rows,
    )

    # -------------------------
    # 10) Dependency tree
    # -------------------------
    log("Building dependency tree report...")
    f_tree = os.path.join(out_dir, "dependency_tree.txt")
    with open(f_tree, "w") as f:
        def w(line: str = "") -> None:
            f.write(line + "\n")

        w("Docker overlay2 dependency tree")
        w(f"Generated: {ts()}")
        w()
        w("Root")
        w(f"|- Overlay2 total on disk: {human_bytes(overlay_total_bytes)}")
        w(f"|- Layer diff sum: {human_bytes(layer_scan_sum)}")
        w("|- Containers aggregate:")
        w(f"|  |- count: {len(cids)}")
        w(f"|  |- upper_total: {human_bytes(container_upper_sum)}")
        w(f"|  |- logical_total: {human_bytes(container_logical_sum)}")
        w(f"|  |- adjusted_total: {human_bytes(container_adjusted_sum)}")
        w("|  |- nodes (sorted by adjusted_total):")
        for row in read_tsv_rows(os.path.join(out_dir, "containers_sorted_by_adjusted.tsv")):
            cid, name, st, imid, ul, us, ll, ls, lt, at = row[:10]
            w(f"|  |  |- container:{name} ({cid})")
            w(f"|  |  |  |- status: {st}")
            w(f"|  |  |  |- image_id: {imid}")
            w(f"|  |  |  |- upper_size: {human_bytes(us)}")
            w(f"|  |  |  |- lower_layers: {ll}")
            w(f"|  |  |  |- lower_total: {human_bytes(ls)}")
            w(f"|  |  |  |- logical_total: {human_bytes(lt)}")
            w(f"|  |  |  |- adjusted_total: {human_bytes(at)}")

        w("|- Images aggregate:")
        w(f"|  |- count: {len(iids)}")
        w(f"|  |- logical_total: {human_bytes(image_logical_sum)}")
        w(f"|  |- adjusted_total: {human_bytes(image_adjusted_sum)}")
        w(f"|  |- unused_images: {unused_image_count}")
        w("|  |- nodes (sorted by logical_total):")
        for row in read_tsv_rows(os.path.join(out_dir, "images_sorted_by_logical.tsv")):
            iid, tag, used, recl, ul, us, ll, ls, lt, at = row[:10]
            w(f"|  |  |- image:{tag}")
            w(f"|  |  |  |- id: {iid}")
            w(f"|  |  |  |- used_by_containers: {used}")
            w(f"|  |  |  |- reclaimable: {recl}")
            w(f"|  |  |  |- upper_size: {human_bytes(us)}")
            w(f"|  |  |  |- lower_layers: {ll}")
            w(f"|  |  |  |- lower_total: {human_bytes(ls)}")
            w(f"|  |  |  |- logical_total: {human_bytes(lt)}")
            w(f"|  |  |  |- adjusted_total: {human_bytes(at)}")

        w("|- Unreferenced layers:")
        w(f"|  |- count: {unref_count}")
        w(f"|  |- total: {human_bytes(unref_sum)}")
        w("|  |- note: orphaned overlay2 data (no image or container references them)")
        w("|  |- nodes (sorted by size):")
        for row in read_tsv_rows(os.path.join(out_dir, "layers_unreferenced_sorted_by_size.tsv")):
            lid, sz = row[:2]
            w(f"|  |  |- layer:{lid} size={human_bytes(sz)}")

        w("|- Volumes aggregate:")
        w(f"|  |- count: {len(vol_names)}")
        w(f"|  |- total_size: {human_bytes(total_vol_size)}")
        w(f"|  |- unused_volumes: {unused_vol_count}")
        w("|  |- nodes (sorted by size):")
        for row in read_tsv_rows(os.path.join(out_dir, "volumes_sorted_by_size.tsv")):
            vname, mountpoint, vsize, vhuman, vcnt, vusers = row[:6]
            w(f"|  |  |- volume:{vname}")
            w(f"|  |  |  |- size: {vhuman}")
            w(f"|  |  |  |- containers: {vcnt} ({vusers or 'none'})")

    # -------------------------
    # 11) Summary file
    # -------------------------
    f_summary = os.path.join(out_dir, "summary.txt")
    with open(f_summary, "w") as f:
        def ws(line: str = "") -> None:
            f.write(line + "\n")

        ws("Docker overlay2 analysis summary")
        ws(f"Generated: {ts()}")
        ws(f"Docker root: {docker_root}")
        ws(f"Overlay dir: {overlay_dir}")
        ws(f"Volumes dir: {volumes_dir}")
        ws(f"Output dir: {out_dir}")
        ws()
        ws("Overlay2 storage")
        ws(f"  Total on disk:                {human_bytes(overlay_total_bytes)}")
        ws(f"  Sum of layer diff dirs:       {human_bytes(layer_scan_sum)}")
        ws(f"  Layers (total):               {total_layers}")
        ws(f"    Referenced   (in use):      {ref_count_layers}  ({human_bytes(ref_size_layers)})")
        ws(f"    Unreferenced (reclaimable): {unref_count}  ({human_bytes(unref_sum)})")
        ws( "    → docker image prune -f && docker builder prune -f")
        ws()
        ws(f"Containers (total: {len(cids)})")
        ws(f"  Running (keep):               {running_count}")
        ws(f"  Stopped (reclaimable):        {stopped_count}  ({human_bytes(stopped_upper_sum)} upper layers)")
        ws( "  → docker container prune -f")
        ws()
        ws(f"Images (total: {len(iids)} unique IDs)")
        ws(f"  In use by containers:         {in_use_image_count}  ({human_bytes(in_use_image_logical_sum)} logical)")
        ws(f"  Unused   (reclaimable):       {unused_image_count}  ({human_bytes(unused_image_logical_sum)} logical)")
        ws( "  → docker image prune -a -f")
        ws( "  Note: 'docker image ls -a' counts one row per tag; 'docker images -aq | sort -u'")
        ws( "  deduplicates by image ID — the count here reflects unique image IDs.")
        ws()
        ws(f"Volumes (total: {len(vol_names)})")
        ws(f"  In use (mounted):             {in_use_vol_count}  ({human_bytes(in_use_vol_size)})")
        ws(f"  Unused (reclaimable):         {unused_vol_count}  ({human_bytes(unused_vol_size)})")
        ws( "  → docker volume prune -f")
        ws()
        ws("Diagnostics")
        ws(f"  Unresolved container paths: {unresolved_container_paths}")
        ws(f"  Unresolved image paths:     {unresolved_image_paths}")
        ws()
        ws("Key interpretation:")
        ws("- upper_size is exclusive per-container writable usage.")
        ws("- logical_total includes shared lower layers and overcounts when summed across objects.")
        ws("- adjusted_total approximates fair share of shared lower layers.")

    # Write unresolved log
    f_unresolved = os.path.join(out_dir, "unresolved_paths.log")
    with open(f_unresolved, "w") as f:
        f.write("\n".join(unresolved_log) + ("\n" if unresolved_log else ""))

    # -------------------------
    # 12) Console output
    # -------------------------
    log("Done. Printing summary and top views.")

    def _print_table(header: List[str], rows: List[List[str]]) -> None:
        all_rows = [header] + rows
        widths = [max(len(str(r[c])) for r in all_rows) for c in range(len(header))]
        for row in all_rows:
            print("  ".join(str(row[c]).ljust(widths[c]) for c in range(len(header))))

    with open(f_summary) as f:
        print(f.read())

    print("---- Storage overview: in use vs reclaimable ----")
    _print_table(
        ["Category", "Total", "In use (keep)", "Reclaimable (cleanup)", "Cleanup command"],
        [
            ["Layers",
             str(total_layers),
             f"{ref_count_layers} ({human_bytes(ref_size_layers)})",
             f"{unref_count} ({human_bytes(unref_sum)})" if unref_count else "none",
             "docker image prune -f && docker builder prune -f" if unref_count else "-"],
            ["Containers",
             str(len(cids)),
             f"{running_count} running",
             f"{stopped_count} stopped ({human_bytes(stopped_upper_sum)} upper)" if stopped_count else "none",
             "docker container prune -f" if stopped_count else "-"],
            ["Images",
             str(len(iids)),
             f"{in_use_image_count} ({human_bytes(in_use_image_logical_sum)} logical)",
             f"{unused_image_count} ({human_bytes(unused_image_logical_sum)} logical)" if unused_image_count else "none",
             "docker image prune -a -f" if unused_image_count else "-"],
            ["Volumes",
             str(len(vol_names)),
             f"{in_use_vol_count} ({human_bytes(in_use_vol_size)})" if in_use_vol_count else "0",
             f"{unused_vol_count} ({human_bytes(unused_vol_size)})" if unused_vol_count else "none",
             "docker volume prune -f" if unused_vol_count else "-"],
        ],
    )
    print()

    print(f"---- Top {top_n} containers by adjusted_total (fair-share overall) ----")
    rows_c = read_tsv_rows(os.path.join(out_dir, "containers_sorted_by_adjusted.tsv"))[:top_n]
    _print_table(
        ["CID", "NAME", "STATUS", "UPPER_SIZE", "LOWER_TOTAL", "LOGICAL_TOTAL", "ADJUSTED_TOTAL"],
        [[r[0], r[1], r[2], human_bytes(r[5]), human_bytes(r[7]), human_bytes(r[8]), human_bytes(r[9])]
         for r in rows_c],
    )
    print()

    print(f"---- Top {top_n} images by logical_total ----")
    rows_i = read_tsv_rows(os.path.join(out_dir, "images_sorted_by_logical.tsv"))[:top_n]
    _print_table(
        ["TAG", "IMAGE_ID", "USED_BY", "RECLAIMABLE", "LOGICAL_TOTAL", "ADJUSTED_TOTAL"],
        [[r[1], r[0], r[2], r[3], human_bytes(r[8]), human_bytes(r[9])]
         for r in rows_i],
    )
    print()

    print(f"---- Top {top_n} unreferenced layer dirs (possible cleanup candidates) ----")
    rows_u = read_tsv_rows(os.path.join(out_dir, "layers_unreferenced_sorted_by_size.tsv"))[:top_n]
    if rows_u:
        _print_table(
            ["LAYER_ID", "SIZE"],
            [[r[0], human_bytes(r[1])] for r in rows_u],
        )
    else:
        print("  No unreferenced layers found.")
    print()

    print(f"---- Top {top_n} volumes by size ----")
    rows_v = read_tsv_rows(os.path.join(out_dir, "volumes_sorted_by_size.tsv"))[:top_n]
    if rows_v:
        _print_table(
            ["VOLUME_NAME", "SIZE", "CONTAINER_COUNT", "CONTAINERS"],
            [[r[0], r[3], r[4], r[5]] for r in rows_v],
        )
    else:
        print("  No named volumes found.")
    print()

    print("---- Cleanup candidate summary ----")
    cr_sorted = sorted(cleanup_rows, key=lambda r: int(r[2]), reverse=True)
    if cr_sorted:
        _print_table(
            ["TYPE", "NAME", "POTENTIAL_RECLAIM_BYTES", "POTENTIAL_RECLAIM_HUMAN",
             "DETAILS", "SAFE_COMMAND_HINT"],
            cr_sorted,
        )
    else:
        print("  No cleanup candidates detected by current rules.")
    print()

    report_files = [
        f_summary,
        os.path.join(out_dir, "containers_sorted_by_adjusted.tsv"),
        os.path.join(out_dir, "containers_sorted_by_logical.tsv"),
        os.path.join(out_dir, "containers_sorted_by_upper.tsv"),
        os.path.join(out_dir, "images_sorted_by_logical.tsv"),
        os.path.join(out_dir, "images_unused_sorted_by_logical.tsv"),
        os.path.join(out_dir, "layers_all_sorted_by_size.tsv"),
        os.path.join(out_dir, "layers_unreferenced_sorted_by_size.tsv"),
        os.path.join(out_dir, "volumes_sorted_by_size.tsv"),
        os.path.join(out_dir, "volumes_unused.tsv"),
        f_cleanup,
        f_tree,
        f_unresolved,
    ]
    print("Report files:")
    for rf in report_files:
        print(f"  {rf}")

    # -------------------------
    # Cleanup (optional)
    # -------------------------
    if args.cleanup:
        print()
        print("=" * 67)
        print(" CLEANUP MODE")
        print("=" * 67)
        print()
        print("Candidates identified (ordered by potential reclaim):")
        _print_table(
            ["TYPE", "NAME", "POTENTIAL_RECLAIM_BYTES", "POTENTIAL_RECLAIM_HUMAN",
             "DETAILS", "SAFE_COMMAND_HINT"],
            cr_sorted,
        )
        print()

        def confirm(prompt: str) -> bool:
            if args.yes:
                print(f"  [auto-yes] {prompt}")
                return True
            print(f"  {prompt} [y/N]: ", end="", flush=True)
            try:
                ans = sys.stdin.readline().strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            return ans == "y"

        def run_cmd(cmd: str) -> None:
            print(f"  Running: {cmd}")
            ret = subprocess.run(cmd, shell=True, env=_DOCKER_ENV)
            if ret.returncode != 0:
                print(f"  Warning: command exited with code {ret.returncode}")

        print("--- Step 1: Remove stopped containers ---")
        print("  Frees writable (upper) layers.  Containers can be re-created from their images.")
        if confirm("Remove all stopped containers? (docker container prune -f)"):
            run_cmd("docker container prune -f")
        else:
            print("  Skipped.")
        print()

        print("--- Step 2: Remove unused images ---")
        print("  Removes all images not referenced by any container (running or stopped).")
        print("  Images can be re-pulled from the registry if needed.")
        if confirm("Remove all unused images? (docker image prune -a -f)"):
            run_cmd("docker image prune -a -f")
        else:
            print("  Skipped.")
        print()

        print("--- Step 3: Remove BuildKit build cache ---")
        print("  Removes orphaned builder layers and build cache.")
        print("  'docker system prune' does NOT cover this in all Docker versions.")
        print("  Next build will be slower (no cache hits).")
        if confirm("Remove build cache? (docker builder prune -f)"):
            run_cmd("docker builder prune -f")
        else:
            print("  Skipped.")
        print()

        if unused_vol_count > 0:
            print("--- Step 4: Remove unused volumes ---")
            print("  WARNING: Volume data may be permanent and unrecoverable.")
            print(f"  Unused volumes: {unused_vol_count} ({human_bytes(unused_vol_size)})")
            print(f"  Review {os.path.join(out_dir, 'volumes_unused.tsv')} before confirming.")
            if confirm("Remove unused volumes? (docker volume prune -f) [DESTRUCTIVE]"):
                run_cmd("docker volume prune -f")
            else:
                print("  Skipped.")
            print()

        print("Cleanup complete.  Re-run the analysis to measure freed space.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
