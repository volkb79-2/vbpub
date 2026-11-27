#!/usr/bin/env python3
"""P-plat-19m: the DAMON hot-set driver of the acceptance kit (DESIGN-ACCEPT-KIT-V2.md 4).

One paddr kdamond, one `stat` scheme per **leaf** memcg, two AND-ed ops-filters per scheme:

* `ops_filters/0`: `type memcg`, `memcg_path <leaf>`, `matching N`, `allow N` -- rejects every page that is
  not the leaf's, so the young filter below never runs on (or mkold's) another cgroup's pages;
* `ops_filters/1`: `type young`, `matching Y`, `allow Y` -- the leaf's young pages pass; the leaf's old pages
  match nothing and fall to the reverse of the last decision, which after an allow filter is a reject.

DAMOS decides on the **first** filter a page matches (`DAMON-GUIDE.md:278-279`), so the chain is an AND, not
the OR that "both filters allow" would make it. The per-unit number is Sigma `tried_regions/*/sz_filter_passed`
after one `update_schemes_tried_regions`; `refresh_ms` is 0 because the kit drives the updates itself and the
kernel's own periodic refresh would alias the scheme's 8 s apply interval (r2 N5).

    accept-hotset.py preflight             --evidence DIR --units "u=rel ..." [--admin DIR] ...
    accept-hotset.py setup                 --evidence DIR --state FILE --units "u=rel ..." [--admin DIR] ...
    accept-hotset.py refresh-schemes --step N      (same args, plus the running kdamond from the WAL)
    accept-hotset.py sample          --step N      (same args)
    accept-hotset.py off|on                        (same args)

`preflight` checks DAMON_STAT, cgprofilectl, sysfs presence/ownership, and paddr ranges without writing sysfs.
`setup` checks existing daemon state/counts and refuses (exit 3) when DAMON_STAT is not `N`, when any kdamond is not `off`, when no
empty off daemon can be taken, when the kernel lacks `paddr`/`stats/sz_ops_filter_passed`/the `ops_filters` chain,
or when a live cgprofile session multiplexes kdamonds (4.4). It syncs owner/count/index records to the WAL before
growing `nr_kdamonds` or `nr_contexts`, creates the context before reading `avail_operations`, and starts only that
daemon. Restore shrinks only the recorded growth after confirming the recorded daemon and affected daemons are off.
Exit 0 ok, 3 refused, 2 usage or crash. Test hooks: `--fixture` (a JSON per-unit sample source, no sysfs),
`--damon-stat PATH`, `--cgroup DIR`, `--lib DIR`, `--proc DIR`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

MIB = 1024 ** 2
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LIB = os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "scripts", "damon-analysis", "lib"))
SAMPLE_US, AGGR_US, UPDATE_US = 400_000, 8_000_000, 8_000_000
MIN_REGIONS, MAX_REGIONS = 10, 1000
MAX_U32 = 2 ** 32 - 1
MAX_SZ = 2 ** 63 - 1
HOTSET_COLUMNS = ("utc", "step", "unit", "memcg_leaves", "sz_filter_passed_one_apply", "sz_tried", "regions_hot_bytes_info")
TOPOLOGY_LOG = "hotset-topology.jsonl"
SELF_TEST_CURRENT = MIB   # above this, sz_filter_passed 0 is a refused floor, never "no hot pages" (4.1)
RETRY_ATTEMPTS = 3


class Refusal(Exception):
    pass


class Inconclusive(Refusal):
    pass


def say(msg: str) -> None:
    print(f"accept-hotset: {msg}")


def note(msg: str) -> None:
    print(f"accept-hotset: NOTE {msg}")


def read_int(path: str, default: int | None = None) -> int:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        if default is None:
            raise
        return default


def append_wal(state: str, *lines: str) -> None:
    """Make ownership and planned DAMON growth durable before the sysfs write."""
    try:
        with open(state, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(f"{line}\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise Refusal(f"cannot sync DAMON write-ahead records to {state}: {exc}") from exc


def write_text(path: str, value: object) -> None:
    try:
        with open(path, "w") as fh:
            fh.write(f"{value}\n")
    except OSError as exc:
        raise Refusal(f"cannot write {path}: {exc}") from exc


def read_lines(path: str) -> list[str]:
    try:
        with open(path) as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def parse_units(raw: str) -> list[tuple[str, str]]:
    out = []
    for item in raw.split():
        unit, sep, rel = item.partition("=")
        if not sep or not unit or not rel:
            raise Refusal(f"--units entry {item!r} is not unit=relpath")
        out.append((unit, rel))
    return out


def leaf_cgroups(cg: str, rel: str) -> list[str]:
    """The leaf memcgs of one unit. A service is its own leaf; a slice is every descendant cgroup that has memory
    accounting and no child that has it -- the slice's own directory reads ~0 (the memcg filter matches the folio's
    memcg id exactly, never its descendants, 4.2)."""
    base = os.path.join(cg, rel)
    if not os.path.isdir(base):
        return []
    leaves = []
    for root, _dirs, files in os.walk(base):
        if "memory.current" not in files:
            continue
        try:
            children = os.listdir(root)
        except (FileNotFoundError, NotADirectoryError):
            continue   # a short-lived session scope vanished after os.walk yielded it
        if not os.path.isfile(os.path.join(root, "memory.current")):
            continue
        if any(os.path.isfile(os.path.join(root, d, "memory.current")) for d in children):
            continue   # a slice or an intermediate cgroup: measured as its leaves, never as itself
        leaves.append(os.path.relpath(root, cg))
    return leaves


def hot_leaves(units: list[tuple[str, str]], cg: str) -> list[tuple[str, str]]:
    return [(unit, leaf) for unit, rel in units for leaf in leaf_cgroups(cg, rel)]


def resolve_kdamond(args) -> int:
    if args.kdamond is not None:
        return args.kdamond
    for line in reversed(read_lines(args.state or "")):   # the WAL line setup wrote before it wrote `on`
        if line.startswith("kdamond=") and line[8:].strip().isdigit():
            return int(line[8:].strip())
    return 0


def damon_stat_state(path: str) -> str:
    if not os.path.isfile(path):
        return "absent"
    try:
        with open(path) as fh:
            raw = fh.read().strip()
    except OSError as exc:
        raise Refusal(f"cannot read {path}: {exc}; whether the kernel's damon_stat owns DAMON is unknown") from exc
    if raw == "N":
        return "N"
    if raw == "Y":
        raise Refusal(
            f"DAMON_STAT is Y at {path}: it owns the kernel-wide DAMON interface and every manual kdamond fails "
            "EBUSY. Refusing, nothing written; units/mdt-damon-stat.conf (DAMON_STAT_DISABLE_DEFAULT) and `install.sh "
            "--require-damon-disable` are the host policy that turns it off, never this kit")
    raise Refusal(f"{path} holds {raw!r}, which is neither N nor Y; refusing rather than guessing")


def cgprofile_sessions() -> None:
    """Refuse unless the endpoint is configured and cgprofile confirms no live sessions."""
    if not os.environ.get("CGPROFILE_CONTROL_SOCKET"):
        raise Refusal("CGPROFILE_CONTROL_SOCKET is unset; source the host endpoint record before running the kit")
    exe = shutil.which("cgprofilectl")
    if exe is None:
        raise Refusal("cgprofilectl is not installed; cannot rule out a live session that owns DAMON")
    try:
        r = subprocess.run([exe, "--json", "status"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal(f"cgprofilectl --json status could not run ({exc}); whether a cgprofile session is live is unknown") from exc
    output = (r.stdout + ("\n" if r.stdout and r.stderr else "") + r.stderr).strip()
    if len(output) > 512:
        output = output[:509] + "..."
    if r.returncode != 0:
        raise Refusal(f"cgprofilectl --json status exited {r.returncode}; output={output!r}; cannot rule out a live session")
    match = re.search(r'"sessions_live"\s*:\s*"?(\d+)"?', r.stdout)
    if match is None:
        raise Refusal(f"cgprofilectl --json status has no sessions_live count; output={output!r}")
    if int(match.group(1)) != 0:
        raise Refusal(f"cgprofilectl reports sessions_live={match.group(1)}: a live cgprofile session holds kdamonds")


def check_damon_sysfs(admin: str) -> None:
    """Read-only check of the DAMON admin interface and the caller's ownership/write access."""
    euid = os.geteuid()
    if euid != 0 and os.path.abspath(admin) == "/sys/kernel/mm/damon/admin":
        raise Refusal("the real DAMON sysfs interface requires root; refusing before setup")
    kdamonds = os.path.join(admin, "kdamonds")
    nr_path = os.path.join(kdamonds, "nr_kdamonds")
    if not os.path.isdir(kdamonds) or not os.path.isfile(nr_path):
        raise Refusal(f"{admin}/kdamonds/nr_kdamonds is absent: DAMON sysfs is unavailable")
    for path in (kdamonds, nr_path):
        try:
            owner = os.stat(path).st_uid
        except OSError as exc:
            raise Refusal(f"cannot stat DAMON sysfs {path}: {exc}") from exc
        expected = 0 if euid == 0 else euid
        if owner != expected:
            raise Refusal(f"DAMON sysfs {path} is owned by uid {owner}, expected uid {expected}; refusing")
        if not os.access(path, os.R_OK | os.W_OK):
            raise Refusal(f"DAMON sysfs {path} is not readable and writable by uid {euid}; refusing")


def preflight(args) -> int:
    """Check all hot-set environmental facts without writing DAMON sysfs."""
    status = damon_stat_state(args.damon_stat)
    cgprofile_sessions()
    if not args.fixture:
        check_damon_sysfs(args.admin)
        entries_or_refuse(parse_units(args.units), args.cgroup)
        physical_ranges(args.iomem, mem_total_bytes(args.proc))
        # Do not select a daemon here: an old WAL may own an on kdamond that must
        # be restored after this read-only environment check. setup() selects it
        # again immediately before recording ownership and writing sysfs.
    print(f"damon_stat_enabled={status}")
    print("cgprofile_sessions=0")
    print("damon_sysfs=ready" if not args.fixture else "damon_sysfs=fixture")
    return 0


def set_ops_filters(scheme_dir: str, memcg_path: str) -> None:
    """The AND chain of 4.1. `nr_filters` goes first because that write is what creates the filter directories."""
    ops = os.path.join(scheme_dir, "ops_filters")
    if not os.path.isdir(ops):
        raise Refusal(f"{ops} is missing: this kernel has no ops_filters, so no per-leaf hot set is measurable")
    write_text(os.path.join(ops, "nr_filters"), 2)
    for idx in ("0", "1"):
        if not os.path.isfile(os.path.join(ops, idx, "allow")):
            raise Refusal(f"{ops}/{idx}/allow is missing after nr_filters=2: no ops-filter chain on this kernel")
    write_text(os.path.join(ops, "0", "type"), "memcg")
    # DAMON's memcg_path is relative to the cgroups mount, not a host
    # filesystem path such as /sys/fs/cgroup/system.slice/ssh.service.
    if not memcg_path.startswith("/"):
        raise Refusal(f"{memcg_path!r} is not a cgroup-mount-relative memcg path")
    write_text(os.path.join(ops, "0", "memcg_path"), memcg_path)
    write_text(os.path.join(ops, "0", "matching"), "N")
    write_text(os.path.join(ops, "0", "allow"), "N")
    write_text(os.path.join(ops, "1", "type"), "young")
    write_text(os.path.join(ops, "1", "matching"), "Y")
    write_text(os.path.join(ops, "1", "allow"), "Y")


def mem_total_bytes(proc: str) -> int:
    for line in read_lines(os.path.join(proc, "meminfo")):
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                break
    raise Refusal(f"no MemTotal in {proc}/meminfo: the paddr target cannot be sized")


def physical_ranges(iomem: str, mem_total: int) -> list[tuple[int, int]]:
    """Return merged, end-exclusive System RAM ranges from /proc/iomem.

    DAMON paddr targets are physical addresses. MemTotal is a byte count, not
    the highest physical address; using it as an endpoint misses RAM above
    address holes. /proc/iomem reports inclusive resource endpoints.
    """
    pattern = re.compile(r"^\s*([0-9a-fA-F]+)-([0-9a-fA-F]+)\s*:\s*System RAM\s*$")
    found: list[tuple[int, int]] = []
    try:
        with open(iomem, encoding="ascii") as fh:
            for line in fh:
                match = pattern.match(line)
                if match:
                    start, end = (int(value, 16) for value in match.groups())
                    if end < start:
                        raise Refusal(f"{iomem} has a reversed System RAM range: {line.strip()}")
                    found.append((start, end + 1))
    except OSError as exc:
        raise Refusal(f"cannot read {iomem}: {exc}; physical DAMON target ranges are unknown") from exc
    if not found:
        raise Refusal(f"{iomem} has no readable System RAM ranges; physical DAMON target ranges are unknown")
    found.sort()
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    if sum(end - start for start, end in merged) < mem_total:
        raise Refusal(f"{iomem} System RAM ranges are smaller than MemTotal ({mem_total} bytes); refusing an incomplete paddr target")
    return merged


def load_lib(path: str, admin: str):
    sys.path.insert(0, path)
    try:
        import damon_analysis as da
    except Exception as exc:   # an unimportable lib is a refusal, never a fallback (4.1 "never end - start")
        raise Refusal(f"scripts/damon-analysis/lib is not importable from {path} ({exc}); an accept checkout "
                      "needs mdt-cockpit/ AND scripts/damon-analysis/lib/") from exc
    da.SYSFS_ADMIN = admin
    da.KDAMONDS_DIR = os.path.join(admin, "kdamonds")
    return da


def select_kdamond(admin: str) -> tuple[int, int, str]:
    """Select a new or empty off kdamond without changing sysfs.

    Writing nr_kdamonds recreates every kdamond directory, so it is only
    allowed from zero. With existing daemons, the kit may take an empty off
    one whose contexts/nr_contexts is zero; it refuses to overwrite staged
    inputs from any non-empty daemon.
    """
    nrd = os.path.join(admin, "kdamonds")
    nr = read_int(os.path.join(nrd, "nr_kdamonds"), -1)
    if nr < 0:
        raise Refusal(f"{nrd}/nr_kdamonds is unreadable: the DAMON sysfs interface is absent or not root-owned")
    empty: list[int] = []
    for idx in range(nr):
        st = read_lines(os.path.join(nrd, str(idx), "state"))
        if not st:
            raise Refusal(f"{nrd}/{idx}/state is unreadable")
        if st[0].strip() != "off":
            raise Refusal(f"kdamond {idx} state is {st[0].strip()!r}, not off: the kit requires every existing kdamond off")
        contexts = os.path.join(nrd, str(idx), "contexts")
        count = read_int(os.path.join(contexts, "nr_contexts"), -1)
        if count < 0:
            raise Refusal(f"{contexts}/nr_contexts is unreadable; refusing to take or overwrite this kdamond")
        if count == 0:
            empty.append(idx)
    if nr == 0:
        return 0, nr, "created"
    if not empty:
        raise Refusal(f"all {nr} existing kdamonds have contexts; refusing to overwrite a kdamond the kit did not take")
    return empty[-1], nr, "taken"


def restore_owned_kdamond(admin: str, state: str) -> None:
    """Undo only the kdamond named in the synced ownership WAL, while it is off."""
    values: dict[str, str] = {}
    for line in read_lines(state):
        key, sep, value = line.partition("=")
        if sep and key.startswith("damon_"):
            values[key] = value
    owner = values.get("damon_owner")
    if owner not in ("created", "taken"):
        return
    started = values.get("damon_started")
    if started not in (None, "0", "1"):
        raise Refusal("DAMON start ownership WAL is invalid; leaving its sysfs state untouched")
    try:
        original_nr = int(values["damon_nr_kdamonds"])
        kd = int(values["damon_kdamond"])
        original_contexts = int(values["damon_nr_contexts"])
    except (KeyError, ValueError) as exc:
        raise Refusal("DAMON ownership WAL is incomplete; leaving its sysfs state untouched") from exc
    kdamonds = os.path.join(admin, "kdamonds")
    nr_path = os.path.join(kdamonds, "nr_kdamonds")
    current_nr = read_int(nr_path, -1)
    expected_nr = original_nr + (1 if owner == "created" else 0)
    if current_nr not in (original_nr, expected_nr):
        raise Refusal(f"DAMON nr_kdamonds is {current_nr}, expected {original_nr} or {expected_nr}; leaving it untouched")
    kd_dir = os.path.join(kdamonds, str(kd))
    if current_nr == original_nr and owner == "created":
        return
    state_path = os.path.join(kd_dir, "state")
    current_state = read_lines(state_path)
    if not current_state:
        raise Refusal(f"{state_path} is unreadable; leaving DAMON counts untouched")
    if current_state[0].strip() != "off":
        if started == "0":
            raise Refusal(f"kdamond {kd} is on without a successful kit start record; leaving it and the WAL untouched")
        write_text(state_path, "off")
        current_state = read_lines(state_path)
        if not current_state or current_state[0].strip() != "off":
            raise Refusal(f"kdamond {kd} did not become off; leaving DAMON counts untouched")
    if owner == "created":
        for idx in range(current_nr):
            other_state = read_lines(os.path.join(kdamonds, str(idx), "state"))
            if not other_state or other_state[0].strip() != "off":
                raise Refusal(f"kdamond {idx} is not confirmed off; refusing nr_kdamonds restore")
        append_wal(state, f"damon_restore_nr_kdamonds={original_nr}")
        write_text(nr_path, original_nr)
        if read_int(nr_path, -1) != original_nr:
            raise Refusal("nr_kdamonds did not return to its original value")
        return
    if current_nr != original_nr:
        raise Refusal("taken kdamond count changed; refusing to modify its contexts")
    contexts_path = os.path.join(kd_dir, "contexts", "nr_contexts")
    current_contexts = read_int(contexts_path, -1)
    if current_contexts == original_contexts:
        return
    if current_contexts != 1 or original_contexts != 0:
        raise Refusal(f"kdamond {kd} contexts/nr_contexts is {current_contexts}, expected 0 or 1; leaving it untouched")
    append_wal(state, f"damon_restore_nr_contexts={kd}|{original_contexts}")
    write_text(contexts_path, original_contexts)
    if read_int(contexts_path, -1) != original_contexts:
        raise Refusal(f"kdamond {kd} contexts/nr_contexts did not return to zero")


def configure(da, admin: str, kd: int, entries: list[tuple[str, str]],
              ranges: list[tuple[int, int]], commit: bool) -> None:
    ctx = 0
    da.SysfsInterface.create_context(kd, ctx)
    da.SysfsInterface.set_operations(kd, ctx, "paddr")
    da.SysfsInterface.set_intervals(kd, ctx, SAMPLE_US, AGGR_US, UPDATE_US)
    da.SysfsInterface.set_nr_regions(kd, ctx, MIN_REGIONS, MAX_REGIONS)
    da.SysfsInterface.set_nr_targets(kd, ctx, 1)   # recreates the target array: the schemes below are configured after it
    regions = os.path.join(admin, "kdamonds", str(kd), "contexts", str(ctx), "targets", "0", "regions")
    write_text(os.path.join(regions, "nr_regions"), len(ranges))   # the kernel creates these regions on that write
    for i, (start, end) in enumerate(ranges):
        idx = str(i)
        if not os.path.isdir(os.path.join(regions, idx)):
            raise Refusal(f"{regions}/{idx} does not exist after nr_regions={len(ranges)}: this kernel does not expose physical regions")
        write_text(os.path.join(regions, idx, "start"), start)
        write_text(os.path.join(regions, idx, "end"), end)
    schemes = os.path.join(admin, "kdamonds", str(kd), "contexts", str(ctx), "schemes")
    write_text(os.path.join(schemes, "nr_schemes"), len(entries))
    for i, (unit, leaf) in enumerate(entries):
        sdir = da.SysfsInterface.create_scheme(kd, ctx, i)
        if not os.path.isdir(sdir):
            raise Refusal(f"scheme {i} ({unit} {leaf}) was not created under {schemes}")
        if read_int(os.path.join(sdir, "stats", "sz_ops_filter_passed"), -1) < 0:
            raise Refusal(f"{sdir}/stats/sz_ops_filter_passed is unreadable: this kernel cannot report the filter-passed "
                          "bytes, and the regions-based end-start number is the machine's hot set, never a leaf's (4.1)")
        write_text(os.path.join(sdir, "action"), "stat")
        da.SysfsInterface.set_scheme_access_pattern(kd, ctx, i, 0, MAX_SZ, 0, MAX_U32, 0, MAX_U32)
        set_ops_filters(sdir, "/" + leaf.lstrip("/"))
    write_text(os.path.join(admin, "kdamonds", str(kd), "refresh_ms"), 0)   # N5: no periodic refresh aliasing aggr_us
    if commit:
        da.SysfsInterface.kdamond_commit(kd)


def entries_or_refuse(units: list[tuple[str, str]], cg: str) -> list[tuple[str, str]]:
    entries = hot_leaves(units, cg)
    if not entries:
        raise Refusal(f"no leaf memcg under {cg} for the units given: a slice is measured as its leaves (4.2)")
    return entries


def topology_snapshot(units: list[tuple[str, str]], cg: str) -> tuple[list[dict], dict[str, list[str]]]:
    """Return readable leaf identities and unit-local gaps. Identity is relpath plus directory inode."""
    leaves: list[dict] = []
    gaps: dict[str, list[str]] = {}
    for unit, rel in units:
        base = os.path.join(cg, rel)
        try:
            if not os.path.isdir(base):
                gaps.setdefault(unit, []).append(f"unit cgroup {rel} is absent")
                continue
            found = leaf_cgroups(cg, rel)
        except OSError as exc:
            gaps.setdefault(unit, []).append(f"cannot enumerate {rel}: {exc}")
            continue
        readable = 0
        for leaf in found:
            try:
                identity = os.stat(os.path.join(cg, leaf)).st_ino
                current = read_int(os.path.join(cg, leaf, "memory.current"))
            except (FileNotFoundError, NotADirectoryError) as exc:
                gaps.setdefault(unit, []).append(f"leaf {leaf} vanished during identity read: {exc}")
                continue
            except (OSError, ValueError) as exc:
                gaps.setdefault(unit, []).append(f"leaf {leaf} is unreadable: {exc}")
                continue
            leaves.append({"unit": unit, "path": leaf, "inode": identity, "current": current})
            readable += 1
        if readable == 0 and unit not in gaps:
            gaps.setdefault(unit, []).append(f"no readable leaf memcg under {rel}")
    leaves.sort(key=lambda row: (row["unit"], row["path"], row["inode"]))
    return leaves, gaps


def topology_identity(leaves: list[dict]) -> tuple[tuple[str, str, int], ...]:
    return tuple(sorted((row["unit"], row["path"], int(row["inode"])) for row in leaves))


def topology_record_leaves(leaves: list[dict]) -> list[dict]:
    return [{"unit": row["unit"], "path": row["path"], "inode": int(row["inode"])} for row in leaves]


def append_topology(evidence: str, record: dict) -> None:
    path = os.path.join(evidence, TOPOLOGY_LOG)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise Refusal(f"cannot record DAMON cgroup topology in {path}: {exc}") from exc


def topology_history(evidence: str) -> list[dict]:
    path = os.path.join(evidence, TOPOLOGY_LOG)
    records = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    raise Refusal(f"{path}:{line_number} is not valid topology evidence: {exc}") from exc
                if not isinstance(record, dict):
                    raise Refusal(f"{path}:{line_number} is not a topology record")
                records.append(record)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise Refusal(f"cannot read DAMON topology evidence {path}: {exc}") from exc
    return records


def configured_topology(evidence: str, *, required: bool = True) -> list[dict] | None:
    for record in reversed(topology_history(evidence)):
        if record.get("kind") != "configured":
            continue
        leaves = record.get("leaves")
        if not isinstance(leaves, list):
            raise Refusal(f"{TOPOLOGY_LOG} has a configured record without its leaf identities")
        result = []
        for row in leaves:
            if (not isinstance(row, dict) or not isinstance(row.get("unit"), str)
                    or not isinstance(row.get("path"), str) or not isinstance(row.get("inode"), int)):
                raise Refusal(f"{TOPOLOGY_LOG} has a malformed configured leaf identity")
            result.append({"unit": row["unit"], "path": row["path"], "inode": row["inode"]})
        return result
    if required:
        raise Refusal(f"{TOPOLOGY_LOG} has no configured leaf identities; refusing a sample without its commit baseline")
    return None


def record_topology_change(evidence: str, step: int, source: str, before: list[dict] | None,
                           after: list[dict], kind: str = "topology_change") -> None:
    if before is None or topology_identity(before) == topology_identity(after):
        return
    append_topology(evidence, {"kind": kind, "step": step, "source": source,
                               "before": topology_record_leaves(before), "after": topology_record_leaves(after)})


def unit_for_memcg_path(memcg_path: str, units: list[tuple[str, str]]) -> str:
    """Resolve a scheme by configured relpath components, independent of current cgroup enumeration."""
    if not memcg_path.startswith("/") or memcg_path.startswith("//"):
        raise Refusal(f"scheme memcg_path {memcg_path!r} is not an absolute cgroup-mount-relative path")
    path_parts = memcg_path[1:].split("/") if memcg_path != "/" else []
    if any(part in ("", ".", "..") for part in path_parts):
        raise Refusal(f"scheme memcg_path {memcg_path!r} is not a normalized cgroup path")
    matches = []
    for unit, rel in units:
        rel_parts = rel.strip("/").split("/") if rel.strip("/") else []
        if any(part in ("", ".", "..") for part in rel_parts):
            raise Refusal(f"configured unit {unit!r} has a non-normalized cgroup relpath {rel!r}")
        if path_parts[:len(rel_parts)] == rel_parts:
            matches.append(unit)
    if len(matches) != 1:
        why = "no configured unit" if not matches else f"multiple configured units {', '.join(matches)}"
        raise Refusal(f"scheme memcg_path {memcg_path!r} belongs to {why}; refusing attribution")
    return matches[0]


def configure_topology(da, args, units: list[tuple[str, str]], kd: int, ranges: list[tuple[int, int]],
                       step: int, source: str, previous: list[dict] | None) -> tuple[list[dict], dict[str, list[str]]]:
    """Commit fresh schemes and retain only identities that survived the commit."""
    before, _before_gaps = topology_snapshot(units, args.cgroup)
    if not before:
        raise Inconclusive(f"no configured leaf memcg remains under {args.cgroup}; the sample is INCONCLUSIVE")
    configure(da, args.admin, kd, [(row["unit"], row["path"]) for row in before], ranges, commit=True)
    after, after_gaps = topology_snapshot(units, args.cgroup)
    if previous is not None:
        record_topology_change(args.evidence, step, source, previous, before)
    if topology_identity(before) != topology_identity(after):
        record_topology_change(args.evidence, step, source, before, after, kind="commit_race")
        before_by_path = {row["path"]: row for row in before}
        after_by_path = {row["path"]: row for row in after}
        for path, row in before_by_path.items():
            live = after_by_path.get(path)
            if live is not None and live["inode"] == row["inode"]:
                continue
            if live is not None:
                reason = f"leaf {path} changed directory inode during commit ({row['inode']} -> {live['inode']})"
                record_topology_drop(args, step, row["unit"], path, reason, row["inode"], live["inode"])
            elif any(entry["path"].startswith(path.rstrip("/") + "/") for entry in after):
                reason = f"leaf {path} gained child cgroups during commit and was no longer a leaf"
                record_topology_drop(args, step, row["unit"], path, reason, row["inode"])
            else:
                reason = f"leaf {path} disappeared or became unreadable during commit"
                record_topology_drop(args, step, row["unit"], path, reason, row["inode"])
        for path, row in after_by_path.items():
            prior = before_by_path.get(path)
            if prior is None:
                reason = f"leaf {path} appeared during commit and was not included in this apply"
                record_topology_drop(args, step, row["unit"], path, reason, None, row["inode"])
        for unit, reasons in after_gaps.items():
            for reason in reasons:
                record_topology_drop(args, step, unit, None, reason)
        stable = set(topology_identity(before)) & set(topology_identity(after))
        if not stable:
            raise Inconclusive(f"no configured leaf identity survived the DAMON commit under {args.cgroup}; retrying with current topology")
    elif not after:
        raise Inconclusive(f"no configured leaf memcg remains under {args.cgroup}; the sample is INCONCLUSIVE")
    append_topology(args.evidence, {"kind": "configured", "step": step, "source": source,
                                   "leaves": topology_record_leaves(before),
                                   "observed_after_commit": topology_record_leaves(after)})
    for unit, reasons in after_gaps.items():
        for reason in reasons:
            record_topology_drop(args, step, unit, None, reason)
    return before, after_gaps


def setup(args) -> int:
    status = damon_stat_state(args.damon_stat)
    cgprofile_sessions()
    units = parse_units(args.units)
    if args.fixture:   # no sysfs at all: the fixture supplies the samples, the WAL still needs its index
        print(f"damon_stat_enabled={status}")
        print("kdamond=fake")
        return 0
    check_damon_sysfs(args.admin)
    da = load_lib(args.lib, args.admin)
    if not da.SysfsInterface.is_available():
        raise Refusal(f"{args.admin}/kdamonds is absent: DAMON sysfs is unavailable")
    initial_topology, _initial_gaps = topology_snapshot(units, args.cgroup)
    if not initial_topology:
        raise Refusal(f"no leaf memcg under {args.cgroup} for the units given: a slice is measured as its leaves (4.2)")
    entries = [(row["unit"], row["path"]) for row in initial_topology]
    ranges = physical_ranges(args.iomem, mem_total_bytes(args.proc))
    idx, original_nr, owner = select_kdamond(args.admin)
    # These synced records establish ownership before either count write. A
    # write to nr_kdamonds rebuilds every child, while a write to nr_contexts
    # creates contexts/0; the restore uses only the object explicitly named.
    append_wal(args.state, f"damon_owner={owner}", f"damon_nr_kdamonds={original_nr}",
               f"damon_kdamond={idx}", "damon_nr_contexts=0", "damon_started=0", f"kdamond={idx}")
    try:
        nrd = os.path.join(args.admin, "kdamonds")
        if owner == "created":
            write_text(os.path.join(nrd, "nr_kdamonds"), original_nr + 1)
            if read_int(os.path.join(nrd, "nr_kdamonds"), -1) != original_nr + 1:
                raise Refusal("nr_kdamonds did not create the selected kdamond")
        # Context 0 does not exist at nr_contexts=0. This is the write that
        # creates it, and it is WALed above before avail_operations is read.
        da.SysfsInterface.create_context(idx, 0)
        context_count = read_int(os.path.join(nrd, str(idx), "contexts", "nr_contexts"), -1)
        if context_count != 1:
            raise Refusal(f"kdamond {idx} contexts/nr_contexts={context_count} after context creation")
        avail = read_lines(os.path.join(nrd, str(idx), "contexts", "0", "avail_operations"))
        if not avail:
            raise Refusal(f"kdamonds/{idx}/contexts/0/avail_operations is unreadable")
        if "paddr" not in avail[0].split():
            raise Refusal(f"avail_operations [{avail[0].strip()}] has no paddr: this kernel cannot monitor physical regions")
        configure(da, args.admin, idx, entries, ranges, commit=False)
        state_path = os.path.join(nrd, str(idx), "state")
        state_before_on = read_lines(state_path)
        if not state_before_on or state_before_on[0].strip() != "off":
            raise Refusal(f"kdamond {idx} became {state_before_on[0].strip() if state_before_on else 'unreadable'} before setup could start it")
        da.SysfsInterface.kdamond_on(idx)
        # Record successful ownership only after the sysfs transition succeeds. If the
        # process dies between on and this fsynced WAL append, restore leaves an on
        # daemon alone and reports pending rather than stopping a possible foreign owner.
        append_wal(args.state, "damon_started=1")
        st = read_lines(os.path.join(nrd, str(idx), "state"))
        if not st or st[0].strip() != "on":
            raise Refusal(f"kdamond {idx} did not start (state {st[0].strip() if st else 'unreadable'!r})")
        current_topology, _current_gaps = topology_snapshot(units, args.cgroup)
        if not current_topology:
            raise Refusal(f"no configured leaf memcg remains under {args.cgroup}; setup is INCONCLUSIVE")
        if topology_identity(current_topology) != topology_identity(initial_topology):
            ranges = physical_ranges(args.iomem, mem_total_bytes(args.proc))
            entries, _gaps = configure_topology(da, args, units, idx, ranges, 0, "setup", initial_topology)
        else:
            append_topology(args.evidence, {"kind": "configured", "step": 0, "source": "setup",
                                           "leaves": topology_record_leaves(current_topology)})
    except Exception as exc:
        try:
            restore_owned_kdamond(args.admin, args.state)
        except Exception as restore_exc:
            raise Refusal(f"setup failed ({exc}); immediate DAMON restore is pending ({restore_exc}); WAL kept at {args.state}") from exc
        raise
    say(f"kdamond {idx}: {len(entries)} leaf schemes, paddr, refresh_ms 0, {SAMPLE_US // 1000}ms/{AGGR_US // 1000}s")
    print(f"damon_stat_enabled={status}")
    print(f"kdamond={idx}")
    return 0


def refresh(args) -> int:
    if args.fixture:
        return 0
    units = parse_units(args.units)
    da = load_lib(args.lib, args.admin)
    kd = resolve_kdamond(args)
    ranges = physical_ranges(args.iomem, mem_total_bytes(args.proc))
    last_error: Exception | None = None
    previous = configured_topology(args.evidence, required=False)
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            entries, _gaps = configure_topology(da, args, units, kd, ranges, args.step, "step_refresh", previous)
            say(f"kdamond {kd}: {len(entries)} leaf schemes re-enumerated for step {args.step}")
            return 0
        except (Refusal, OSError, ValueError) as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS:
                note(f"scheme refresh attempt {attempt}/{RETRY_ATTEMPTS} failed ({exc}); re-enumerating cgroups and retrying")
    if isinstance(last_error, Inconclusive):
        raise last_error
    raise Refusal(f"scheme refresh failed after {RETRY_ATTEMPTS} attempts; refusing: {last_error}") from last_error


def append_rows(path: str, rows: list[tuple[str, dict]], step: int) -> None:
    new = not os.path.isfile(path)
    utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(path, "a") as fh:
            if new:
                fh.write(",".join(HOTSET_COLUMNS) + "\n")
            for unit, vals in rows:
                fh.write(f"{utc},{step},{unit},{vals['leaves']},{vals['passed']},{vals['tried']},{vals['hot']}\n")
    except OSError as exc:
        raise Refusal(f"cannot append to {path}: {exc}") from exc


def record_refusal(evidence: str, unit: str, why: str) -> None:
    """One `damon_measure=failed:<unit>:<why>` in manifest.env, once per unit (4.1's self-test)."""
    marker = os.path.join(evidence, "hotset-refused")
    if unit in read_lines(marker):
        return
    try:
        with open(marker, "a") as fh:
            fh.write(f"{unit}\n")
        with open(os.path.join(evidence, "manifest.env"), "a") as fh:
            fh.write(f"damon_measure=failed:{unit}:{why}\n")
    except OSError as exc:
        note(f"cannot record the refused floor of {unit}: {exc}")


def fixture_rows(args, units: list[tuple[str, str]]) -> list[tuple[str, dict]]:
    try:
        with open(args.fixture) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Refusal(f"--fixture {args.fixture} is not readable JSON: {exc}") from exc
    table = data.get("units", {})
    rows, refused = [], []
    for unit, _rel in units:
        vals = table.get(unit)
        if vals is None:
            rows.append((unit, {"leaves": 0, "passed": 0, "tried": 0, "hot": 0}))
            continue
        row = {"leaves": int(vals.get("memcg_leaves", 1)), "passed": int(vals.get("per_apply", 0)),
               "tried": int(vals.get("sz_tried", 0)), "hot": int(vals.get("regions_hot_bytes", 0))}
        rows.append((unit, row))
        if row["passed"] == 0 and int(vals.get("current", 0)) > SELF_TEST_CURRENT:
            refused.append((unit, f"sz_filter_passed 0 with memory.current {int(vals.get('current', 0))}"))
    for unit, why in refused:
        record_refusal(args.evidence, unit, why)
        note(f"{unit}: {why}; that unit's floor is refused, never read as no hot pages (4.1's self-test)")
    return rows


def record_topology_drop(args, step: int, unit: str, path: str | None, reason: str,
                         configured_inode: int | None = None, current_inode: int | None = None) -> None:
    event = {"kind": "dropped", "step": step, "unit": unit, "path": path, "reason": reason}
    if configured_inode is not None:
        event["configured_inode"] = configured_inode
    if current_inode is not None:
        event["current_inode"] = current_inode
    append_topology(args.evidence, event)
    record_refusal(args.evidence, unit, f"topology step {step}: {reason}")


def sample_attempt(args, units: list[tuple[str, str]], da, kd: int, cg: str) -> tuple[list[tuple[str, dict]], list[tuple[str, str]]]:
    """Apply once and read only scheme identities that still match the committed cgroup topology."""
    current, current_gaps = topology_snapshot(units, cg)
    if not current:
        for unit, _rel in units:
            reasons = current_gaps.get(unit) or ["no readable leaf remains for this configured unit"]
            for reason in reasons:
                record_topology_drop(args, args.step, unit, None, reason)
        raise Inconclusive(f"no configured leaf memcg remains under {cg}; the sample is INCONCLUSIVE")
    configured = configured_topology(args.evidence)
    if topology_identity(current) != topology_identity(configured):
        ranges = physical_ranges(args.iomem, mem_total_bytes(args.proc))
        committed, _committed_gaps = configure_topology(
            da, args, units, kd, ranges, args.step, "sample", configured)
    else:
        committed = configured

    da.SysfsInterface.kdamond_update_stats(kd)
    da.SysfsInterface.kdamond_update_tried_regions(kd)   # exactly one apply per sample
    after, after_gaps = topology_snapshot(units, cg)
    committed_by_path = {row["path"]: row for row in committed}
    after_by_path = {row["path"]: row for row in after}
    drops: list[tuple[str, str | None, str, int | None, int | None]] = []
    for unit, reasons in after_gaps.items():
        for reason in reasons:
            drops.append((unit, None, reason, None, None))
    for path, row in committed_by_path.items():
        live = after_by_path.get(path)
        if live is None:
            same_path = next((entry for entry in after if entry["path"] == path), None)
            if same_path is not None:
                reason = f"leaf {path} changed directory inode after commit ({row['inode']} -> {same_path['inode']})"
                drops.append((row["unit"], path, reason, row["inode"], same_path["inode"]))
            elif any(entry["path"].startswith(path.rstrip("/") + "/") for entry in after):
                reason = f"leaf {path} gained child cgroups after commit and was no longer a leaf"
                drops.append((row["unit"], path, reason, row["inode"], None))
            else:
                reason = f"leaf {path} disappeared or became unreadable after commit"
                drops.append((row["unit"], path, reason, row["inode"], None))
        elif live["inode"] != row["inode"]:
            reason = f"leaf {path} changed directory inode after commit ({row['inode']} -> {live['inode']})"
            drops.append((row["unit"], path, reason, row["inode"], live["inode"]))
    for row in after:
        prior = committed_by_path.get(row["path"])
        if prior is None:
            drops.append((row["unit"], row["path"],
                          f"leaf {row['path']} appeared after commit and was not included in this apply",
                          None, row["inode"]))
        elif prior["inode"] != row["inode"]:
            # The path's replacement was already recorded as the configured leaf's drop above.
            continue
    if not after:
        for unit, _rel in units:
            if not any(drop[0] == unit for drop in drops):
                drops.append((unit, None, "no readable leaf remained after the DAMON apply", None, None))
    for unit, path, reason, configured_inode, current_inode in drops:
        record_topology_drop(args, args.step, unit, path, reason, configured_inode, current_inode)
    if not after:
        raise Inconclusive(f"no configured leaf memcg remained after the DAMON apply under {cg}; the sample is INCONCLUSIVE")
    if not any(after_by_path.get(row["path"], {}).get("inode") == row["inode"] for row in committed):
        raise Inconclusive(f"no committed leaf identity remained after the DAMON apply under {cg}; the sample is INCONCLUSIVE")

    per: dict[str, dict] = {}
    schemes = os.path.join(args.admin, "kdamonds", str(kd), "contexts", "0", "schemes")
    nr_schemes = read_int(os.path.join(schemes, "nr_schemes"), -1)
    if nr_schemes <= 0:
        raise Refusal(f"{schemes}/nr_schemes is {nr_schemes}; configured DAMON schemes are unknown")
    seen_paths: set[str] = set()
    for i in range(nr_schemes):
        sdir = da.SysfsInterface._scheme_dir(kd, 0, i)
        path_lines = read_lines(os.path.join(sdir, "ops_filters", "0", "memcg_path"))
        if not path_lines or not path_lines[0].strip():
            raise Refusal(f"scheme {i} has no readable ops_filters/0/memcg_path; refusing to guess its unit")
        memcg_path = path_lines[0].strip()
        unit = unit_for_memcg_path(memcg_path, units)
        path = memcg_path[1:]
        committed_leaf = committed_by_path.get(path)
        if committed_leaf is None:
            raise Refusal(f"scheme {i} memcg_path {memcg_path!r} has no configured leaf identity; refusing a sysfs/configuration mismatch")
        if committed_leaf["unit"] != unit:
            raise Refusal(f"scheme {i} memcg_path {memcg_path!r} disagrees with its configured unit; refusing attribution")
        if path in seen_paths:
            raise Refusal(f"multiple schemes configure memcg_path {memcg_path!r}; refusing duplicate attribution")
        seen_paths.add(path)
        live = after_by_path.get(path)
        if live is None or live["inode"] != committed_leaf["inode"]:
            continue  # the change was recorded above; this leaf contributes no bytes to this apply
        tried = os.path.join(sdir, "tried_regions")
        if not os.path.isdir(tried):
            raise Refusal(f"{tried} disappeared during sample; refusing an incomplete hot-set count")
        passed = hot = 0
        try:
            names = sorted(os.listdir(tried))
        except OSError as exc:
            raise Refusal(f"cannot enumerate {tried} during sample: {exc}") from exc
        for name in names:
            rdir = os.path.join(tried, name)
            if not os.path.isdir(rdir):
                if name.isdigit():
                    raise Refusal(f"tried region {rdir} disappeared during sample")
                continue
            try:
                passed += read_int(os.path.join(rdir, "sz_filter_passed"))
                end = read_int(os.path.join(rdir, "end"))
                start = read_int(os.path.join(rdir, "start"))
            except (OSError, ValueError) as exc:
                raise Refusal(f"tried region {rdir} changed during sample: {exc}") from exc
            if end < start:
                raise Refusal(f"tried region {rdir} has end {end} before start {start}")
            hot += end - start
        stats = da.SysfsInterface.read_stats(kd, 0, i)
        if stats.get("sz_tried", -1) < 0 or stats.get("sz_ops_filter_passed", -1) < 0:
            raise Refusal(f"scheme {i} statistics disappeared during sample")
        acc = per.setdefault(unit, {"leaves": 0, "passed": 0, "tried": 0, "hot": 0})
        acc["leaves"] += 1
        acc["passed"] += passed
        acc["tried"] += stats["sz_tried"]
        acc["hot"] += hot
    if seen_paths != set(committed_by_path):
        raise Refusal(f"configured DAMON schemes do not cover the committed leaf identities; refusing an incomplete sample")
    rows = [(unit, acc) for unit, acc in sorted(per.items())]
    refused = []
    for unit, _acc in rows:
        current = sum(row["current"] for row in after if row["unit"] == unit)
        if per[unit]["passed"] == 0 and current > SELF_TEST_CURRENT:
            refused.append((unit, f"sz_filter_passed 0 with memory.current {current}"))
    return rows, refused


def sample(args) -> int:
    units = parse_units(args.units)
    if args.fixture:
        append_rows(os.path.join(args.evidence, "hotset.csv"), fixture_rows(args, units), args.step)
        return 0
    da = load_lib(args.lib, args.admin)
    kd, cg = resolve_kdamond(args), args.cgroup
    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            rows, refused = sample_attempt(args, units, da, kd, cg)
            break
        except (Refusal, OSError, ValueError) as exc:
            last_error = exc
            if attempt < RETRY_ATTEMPTS:
                note(f"sample attempt {attempt}/{RETRY_ATTEMPTS} failed ({exc}); re-enumerating cgroups and retrying")
    else:
        if isinstance(last_error, Inconclusive):
            raise last_error
        raise Refusal(f"hot-set sample failed after {RETRY_ATTEMPTS} attempts; refusing: {last_error}") from last_error
    append_rows(os.path.join(args.evidence, "hotset.csv"), rows, args.step)
    for unit, why in refused:
        record_refusal(args.evidence, unit, why)
        note(f"{unit}: {why}; that unit's floor is refused, never read as no hot pages (4.1's self-test)")
    say(f"step {args.step}: {len(rows)} units, {sum(r['passed'] for _u, r in rows)} filtered bytes in one apply")
    return 0


def kdamond_state_cmd(args, value: str) -> int:
    if args.fixture:
        say(f"kdamond fake -> {value}")
        return 0
    kd = resolve_kdamond(args)
    path = os.path.join(args.admin, "kdamonds", str(kd), "state")
    write_text(path, value)
    st = read_lines(path)
    if not st or st[0].strip() != value:
        raise Refusal(f"kdamond {kd} state {st[0].strip() if st else 'unreadable'!r} != {value!r} after the write")
    say(f"kdamond {kd} -> {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("preflight", "setup", "refresh-schemes", "sample", "off", "on"))
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--state", help="the WAL; setup appends kdamond=<idx> to it, the other commands read it")
    ap.add_argument("--units", default="")
    ap.add_argument("--admin", default="/sys/kernel/mm/damon/admin")
    ap.add_argument("--cgroup", default="/sys/fs/cgroup")
    ap.add_argument("--proc", default="/proc")
    ap.add_argument("--iomem", default="/proc/iomem")
    ap.add_argument("--lib", default=DEFAULT_LIB)
    ap.add_argument("--damon-stat", default="/sys/module/damon_stat/parameters/enabled")
    ap.add_argument("--kdamond", type=int, help="override the index the WAL records")
    ap.add_argument("--step", type=int, default=0)
    ap.add_argument("--fixture")
    try:
        args = ap.parse_args(argv)
        if args.command != "preflight":
            os.makedirs(args.evidence, exist_ok=True)
        if args.command == "setup" and not args.state:
            raise Refusal("setup needs --state: the WAL gets kdamond=<idx> before `on`")
        handler = {"preflight": preflight, "setup": setup, "refresh-schemes": refresh, "sample": sample,
                   "off": lambda a: kdamond_state_cmd(a, "off"),
                   "on": lambda a: kdamond_state_cmd(a, "on")}[args.command]
        return handler(args)
    except Inconclusive as exc:
        print(f"accept-hotset: {exc}", file=sys.stderr)
        return 4
    except Refusal as exc:
        print(f"accept-hotset: {exc}", file=sys.stderr)
        return 3
    except OSError as exc:   # a missing or unreadable sysfs file is a refusal, never an internal error
        print(f"accept-hotset: {exc.filename or 'sysfs'}: {exc.strerror or exc}; refusing, nothing done", file=sys.stderr)
        return 3
    except SystemExit as exc:   # argparse's usage exit
        return 2 if exc.code else 0
    except Exception as exc:    # a crash is never a measurement
        print(f"accept-hotset: internal error {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
