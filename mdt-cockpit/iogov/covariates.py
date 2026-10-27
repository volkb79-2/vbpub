"""Covariates recorded with every run (never used as a lookup table) and the report-only prevention checker.

Everything is a read of a file or a statvfs; each source is optional and a missing one is recorded as unavailable, never guessed.
"""

from __future__ import annotations

import json
import os
import re

from .collectors import _read


def fill(cfg, statvfs=os.statvfs) -> dict:
    out = {}
    for path in list(cfg.data_paths) + [cfg.scratch_dir]:
        probe = path
        while probe != "/" and not os.path.exists(probe):
            probe = os.path.dirname(probe.rstrip("/")) or "/"
        try:
            sv = statvfs(probe)
        except OSError:
            continue
        total = sv.f_blocks * sv.f_frsize
        free = sv.f_bavail * sv.f_frsize
        out[path] = {"total_bytes": total, "free_bytes": free, "used_pct": round(100.0 * (total - sv.f_bfree * sv.f_frsize) / total, 2) if total else None}
    return out


def mount_options(cfg, path: str) -> dict | None:
    """Mount point, fstype and options of the longest matching mount in <procfs>/mounts."""

    text = _read(os.path.join(cfg.procfs_root, "mounts"))
    if not text:
        return None
    real = os.path.realpath(path)
    best = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mp = parts[1].replace("\\040", " ")
        if real == mp or mp == "/" or real.startswith(mp.rstrip("/") + "/"):
            if best is None or len(mp) > len(best["mount"]):
                best = {"mount": mp, "fstype": parts[2], "options": parts[3].split(","), "device": parts[0]}
    return best


def fstrim_info(cfg) -> dict:
    """Last fstrim time (systemd timer stamp mtime) and, if the operator keeps one, the last `fstrim -v` trimmed bytes."""

    info: dict = {"last_run": None, "source": None, "trimmed_bytes": None}
    try:
        info["last_run"] = os.stat(cfg.fstrim_stamp_file).st_mtime
        info["source"] = "timer-stamp"
    except OSError:
        pass
    text = _read(cfg.fstrim_log_file)
    if text:
        m = None
        for m in re.finditer(r"\((\d+) bytes\) trimmed", text):
            pass
        if m:
            info["trimmed_bytes"] = int(m.group(1))
        try:
            mt = os.stat(cfg.fstrim_log_file).st_mtime
            if info["last_run"] is None or mt > info["last_run"]:
                info["last_run"], info["source"] = mt, "fstrim-log"
        except OSError:
            pass
    return info


def lvm_issue_discards(cfg) -> bool | None:
    text = _read(cfg.lvm_conf)
    if text is None:
        return None
    val = None
    for line in text.splitlines():
        m = re.match(r"\s*issue_discards\s*=\s*(\d)", line)
        if m:
            val = m.group(1) == "1"
    return bool(val)  # absent means the lvm default: off


def provider_signal(cfg, wall) -> dict | None:
    try:
        with open(cfg.provider_signal_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mt = os.stat(cfg.provider_signal_file).st_mtime
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    blob = json.dumps(data, sort_keys=True)
    if len(blob) > 4096:  # never embed an unbounded operator file in the journal: hash and size only
        import hashlib
        data = {"sha256": hashlib.sha256(blob.encode()).hexdigest(), "size": len(blob), "truncated": True,
                **{k: data[k] for k in ("used_bytes", "total_bytes", "snapshot_created") if isinstance(data.get(k), (int, float))}}
    data["file_age_s"] = max(0.0, wall() - mt)
    return data


def discard_capability(disks) -> dict:
    out = {}
    for d in disks:
        v = _read(os.path.join(d.sys_path, "queue", "discard_max_bytes"))
        out[d.kname] = int(v) if v and v.strip().isdigit() else None
    return out


def io_prio_available(cfg, disks) -> dict:
    return {
        "cgroup_io.prio.class": any(os.path.exists(os.path.join(p, "io.prio.class")) for p in cfg.governed_cgroups + [cfg.cgroup_root]),
        "mq_deadline_prio_aging": {d.kname: os.path.exists(os.path.join(d.sys_path, "queue/iosched/prio_aging_expire")) for d in disks},
    }


def collect(cfg, disks, wall, statvfs=os.statvfs) -> dict:
    mounts = {p: mount_options(cfg, p) for p in cfg.data_paths}
    return {
        "fill": fill(cfg, statvfs),
        "mounts": mounts,
        "fstrim": fstrim_info(cfg),
        "lvm_issue_discards": lvm_issue_discards(cfg),
        "discard_max_bytes": discard_capability(disks),
        "provider": provider_signal(cfg, wall),
        "io_prio": io_prio_available(cfg, disks),
    }


def prevention(cfg, covariates: dict, wall) -> list[dict]:
    """Report-only findings from the recorded covariates: {level, code, text}. Never an action."""

    out = []

    def add(level, code, text):
        out.append({"level": level, "code": code, "text": text})

    for path, f in covariates.get("fill", {}).items():
        if f.get("used_pct") is not None and f["used_pct"] > cfg.fill_warn_pct:
            add("warn", "fill", f"{path}: filesystem {f['used_pct']}% full (> {cfg.fill_warn_pct:g}%): throughput is expected to fall before it does")
    ft = covariates.get("fstrim", {})
    if ft.get("last_run") is None:
        add("info", "fstrim_unknown", "no fstrim record available (timer stamp / fstrim log): cannot tell how long free space has been untrimmed")
    else:
        days = (wall() - ft["last_run"]) / 86400.0
        if days > cfg.fstrim_max_days:
            add("warn", "fstrim_old", f"last fstrim {days:.1f} days ago (> {cfg.fstrim_max_days:g}); free space is untrimmed")
    discard_mount = any("discard" in (m or {}).get("options", []) for m in covariates.get("mounts", {}).values())
    if covariates.get("mounts") and not discard_mount and ft.get("last_run") is None:
        add("warn", "no_discard_path", "data filesystems are not mounted with `discard` and no periodic fstrim is recorded")
    if covariates.get("lvm_issue_discards") is False:
        add("info", "lvm_issue_discards", "lvm.conf issue_discards is off: removing an LV does not discard its space")
    for dev, v in covariates.get("discard_max_bytes", {}).items():
        if v == 0:
            add("warn", "no_discard_support", f"{dev}: discard_max_bytes is 0, trim cannot reach the provider")
    prov = covariates.get("provider")
    if prov:
        snap = prov.get("snapshot_created")
        try:
            age_h = (wall() - float(snap)) / 3600.0 if snap else None
        except (TypeError, ValueError):
            age_h = None
            add("info", "snapshot_unreadable", f"provider snapshot_created {snap!r} is not a number")
        if age_h is not None:
            if age_h > cfg.snapshot_warn_h:
                add("warn", "snapshot_age", f"provider snapshot is {age_h:.1f} h old (> {cfg.snapshot_warn_h:g} h)")
        ub, tb = prov.get("used_bytes"), prov.get("total_bytes")
        if isinstance(ub, (int, float)) and isinstance(tb, (int, float)) and tb:
            pct = 100.0 * ub / tb
            if pct > cfg.provider_used_warn_pct:
                add("warn", "provider_used", f"provider reports {pct:.0f}% of the allocation used (> {cfg.provider_used_warn_pct:g}%)")
    else:
        add("info", "provider_unknown", "no provider-side signal file: snapshot age and provider usage unknown (annotate events by hand)")
    return out
