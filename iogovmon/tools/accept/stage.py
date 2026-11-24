#!/usr/bin/env python3
"""iogovmon acceptance-2 stage runner (python3 stdlib only).

What it does (all numbers live in stage.toml; the schedule is in 1-hour WINDOWS aligned to the UTC hour, W0 = first full window
boundary at least `w0_min_lead_s` after the fresh iogovmon start T0):
  A0 baseline (nothing runs) -> [A1/A2 live control phases: kind = "none" = disabled, a1_windows = a2_windows = 0 in the shipped stage.toml]
  -> STAGE-A-DONE marker -> B1 fill /srv/cow to 50% -> B2 fill /srv/cow to 80% and / to 60% -> B3 churn (2 cycles of a 30 min
  docker/repo-like burst) -> B4 fill /srv/cow to 94% and / to 85% -> B5a delete everything WITHOUT any trim (a filesystem that trims on
  delete, btrfs discard=async here, is remounted nodiscard for the phase and cron's `fstrim -a` is disabled for the run) + 2 quiet windows
  -> B5b discard restored, `fstrim -v` both filesystems + 2 quiet windows -> STAGE-DONE + summary.
  `--resume --t0 T --w0 W` replaces a running stage during A0 with the SAME phase table (see main()).
  Every loaded step is followed by *quiet* windows: nothing of ours runs, so iogovmon's gates let its benches run.
Safety (checked every `check_interval_s` while anything writes, not only at the end): `/` never above root_max_pct, `/srv/cow` never
above cow_max_pct, free inodes never below min_free_inode_pct (a violation kills our writers, logs `limit-reached`, the schedule goes on);
we write only below the two fill dirs (+ the two probe files named in stage.toml; a configured dir other than `iogov-fill` or a file other than
`iogov-*` directly under /srv/cow or /var/tmp is refused at start, never deleted); fill/overwrite/load fio use direct IO, the small-file churn worker
uses buffered writes with an fsync per batch (bounded dirty memory; the unit has MemoryHigh=3G); a STOP file stops everything,
deletes everything we created and exits cleanly; any exception does the same before exiting; a lock refuses a second instance.
We never touch the iogovmon config, its kill-switch files, the vda1/2/5/6/7 partitions or any raw device, and never use docker.
fstrim.timer is stopped at start (only if it was active; a marker file remembers it) and restored at the end, on STOP, on exception and by --cleanup.
Modes:  --run --t0 ISO [--resume --w0 ISO] | --rehearsal [flags] | --cleanup | --summary | --status
"""
import argparse, datetime as dt, fcntl, hashlib, json, math, os, random, shutil, signal, statistics, subprocess, sys, threading, time, traceback, types
import tomllib
from pathlib import Path

MiB, GiB = 1 << 20, 1 << 30
ALLOWED_PARENTS = {"/srv/cow", "/var/tmp"}  # the only parents under which an `iogov-fill` dir or an `iogov-*` file may ever be deleted


class StopRequested(Exception):
    """STOP file or SIGTERM/SIGINT: unwind, clean up, exit 0."""


def allowed_fill_dir(path):
    """A fill dir is only ever deleted (or matched as an orphan writer's target) when it is exactly `iogov-fill` under ALLOWED_PARENTS."""
    n = os.path.normpath(path)
    return os.path.basename(n) == "iogov-fill" and os.path.dirname(n) in ALLOWED_PARENTS


def allowed_file(path):
    """A stage-owned loose file (load / probe file) is only ever deleted (or matched) when it is named `iogov-*` directly under ALLOWED_PARENTS."""
    n = os.path.normpath(path)
    return os.path.basename(n).startswith("iogov-") and os.path.dirname(n) in ALLOWED_PARENTS


def names_path(arg, needle):
    """True if one command-line argument IS `needle` or a path below it (also as `--opt=PATH`, and any member of a `:`-separated fio file list).
    Matching is on path components, never on a substring: `/srv/cow/iogov-fill2/x` and `/x/srv/cow/iogov-fill/y` do not name `/srv/cow/iogov-fill`."""
    if arg.startswith("--"):
        arg = arg.split("=", 1)[1] if "=" in arg else ""
    for part in arg.split(":"):
        if part:
            p = os.path.normpath(part)
            if p == needle or p.startswith(needle.rstrip("/") + "/"):
                return True
    return False


def orphan_kind(argv, needles):
    """'fio' | 'worker' | None for one process command line (list of str): only a real writer counts, i.e. argv[0] is fio, or a python running our
    own `--churn-worker`, AND one of its arguments names one of the needles; never a shell that merely mentions a path."""
    if not argv:
        return None
    argv0, args = os.path.basename(argv[0]), argv[1:]
    if not any(names_path(a, n) for a in args for n in needles):
        return None
    if argv0 == "fio":
        return "fio"
    if argv0.startswith("python") and "--churn-worker" in args:
        return "worker"
    return None


def iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_t(s):
    """--t0 as epoch seconds or ISO-8601. A string WITHOUT a UTC offset (no Z, no +hh:mm) is interpreted as UTC, never as local time."""
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def validate_t0(t0, now, window_s, lead_s, max_age_s, max_future_s):
    """None if the start time is plausible, else the refusal text. W0 must still be ahead of us, T0 must be recent and not far in the future."""
    w0 = math.ceil((t0 + lead_s) / window_s) * window_s
    if t0 > now + max_future_s:
        return f"t0 {iso(t0)} is more than {max_future_s}s in the future (now {iso(now)})"
    if now > t0 + max_age_s:
        return f"t0 {iso(t0)} is older than {max_age_s}s (now {iso(now)})"
    if w0 < now:
        return f"W0 {iso(w0)} (from t0 {iso(t0)}) is already in the past (now {iso(now)})"
    return None


def fill_size(to_target, to_limit, chunk, min_bytes=32 * MiB):
    """Next fio --size: the smallest of the bytes still missing to the TARGET, the headroom to the HARD limit (already minus the margin)
    and the chunk, MiB aligned; 0 = stop (target reached, or no headroom left below the limit)."""
    size = min(to_target, to_limit, chunk)
    return 0 if size < min_bytes else size // MiB * MiB


def bytes_to_pct(used, avail, pct):
    """Bytes that must still be written for a filesystem with `used` / `avail` bytes to reach `pct` % df-style used."""
    return int(pct / 100.0 * (used + avail) - used)


def fs_use(path):
    """df-style used% (used / (used + available to non-root)), and free-inode %. Conservative: reserved blocks count as used."""
    v = os.statvfs(path)
    used = (v.f_blocks - v.f_bfree) * v.f_frsize
    avail = v.f_bavail * v.f_frsize
    pct = 100.0 * used / (used + avail) if used + avail else 0.0
    ino = 100.0 * v.f_ffree / v.f_files if v.f_files else 100.0  # btrfs reports no fixed inode count
    return pct, used, avail, ino


# ---------------------------------------------------------------------------------------------------------------------
# configuration: stage.toml, with the rehearsal overrides applied on top
# ---------------------------------------------------------------------------------------------------------------------
REHEARSAL_OVERRIDES = {
    "schedule": {"window_s": 6, "w0_min_lead_s": 3, "snapshot_grace_s": 2, "settle_s": 0.0, "resume_guard_s": 3},
    "limits": {"check_interval_s": 1.0, "hard_margin_bytes": 0},
    "load": {"bs": "1m", "rw": "write", "iodepth": 1, "rate_mbps": 1, "size": "64M"},
    "fill": {"chunk_bytes": 64 * MiB, "rate_mbps": 40, "max_wall_s": 120,
             # rehearsal: absolute cumulative bytes owned in each fill dir (total <= 1 GiB scratch budget)
             "bytes": {"b1_cow": 128 * MiB, "b2_cow": 256 * MiB, "b2_root": 128 * MiB, "b4_cow": 384 * MiB, "b4_root": 256 * MiB}},
    "churn": {"burst_s": 3, "files": 1500, "fio_file": "64M", "fsync_batch": 100},
}


def load_cfg(path, rehearsal):
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    if rehearsal:
        for sec, vals in REHEARSAL_OVERRIDES.items():
            cfg.setdefault(sec, {}).update(vals)
        p = cfg["paths"]
        p["log"] = str(Path(p["workdir"]) / "stage-rehearsal.log")
        p["snapdir"] = str(Path(p["workdir"]) / "snap-rehearsal")
        p["summary"] = str(Path(p["workdir"]) / "stage-summary-rehearsal.txt")
        p["phases"] = str(Path(p["workdir"]) / "stage-phases-rehearsal.json")
    return cfg


# ---------------------------------------------------------------------------------------------------------------------
class Stage:
    def __init__(self, cfg, args):
        self.cfg, self.args = cfg, args
        self.rehearsal = args.rehearsal
        self.dry_annotate = args.dry_annotate or args.rehearsal  # a rehearsal never writes into the real iogovmon journal
        p = cfg["paths"]
        self.work = Path(p["workdir"])
        self.logf = Path(p["log"])
        self.snapdir = Path(p["snapdir"])
        self.stop_file = Path(p["stop_file"])
        self.cow_dir, self.root_dir = p["cow_dir"], p["root_dir"]
        self.allowed_dirs = {self.cow_dir, self.root_dir}
        self.files_allowed = {p["load_file"], p["probe_file"]}
        self.phases = []
        self.children = {}
        self.timers = []
        self.snap_lock = threading.Lock()
        self.limit_hit = {}
        self.cur_phase = None
        self.sig_stop = None
        self.tick_s = 0.2 if self.rehearsal else 1.0
        self.root_limit_override = None
        self.pending_raise = None
        self.throttled = False
        self.root_mount, self.cow_mount = p["root_mount"], p["cow_mount"]
        self.block_dev = p["block_device"]
        self.fake_stdev = False       # rehearsal only: make the st_dev mount check fail
        self.no_limit_cap = False     # rehearsal only: let fio run past the limit so the 1 s check has to kill it mid-write
        self.rm_failed = []
        self.invalid_b5a = []         # filesystems whose nodiscard remount failed (the B5a no-trim test is then invalid there)
        self._logfh = None

    # -- logging -------------------------------------------------------------------------------------------------
    def log(self, msg):
        line = f"{iso(time.time())} {msg}"
        with self._lock_log():
            print(line, flush=True)
            if self._logfh is None:
                self._logfh = open(self.logf, "a", buffering=1)
            self._logfh.write(line + "\n")

    _log_lock = threading.Lock()

    def _lock_log(self):
        return self._log_lock

    # -- stop handling ---------------------------------------------------------------------------------------------
    def check_stop(self):
        if self.sig_stop:
            raise StopRequested(f"signal {self.sig_stop}")
        if self.stop_file.exists():
            raise StopRequested("STOP file")

    def sleep(self, seconds):
        end = time.time() + seconds
        while True:
            self.check_stop()
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(self.tick_s, left))

    def wait_until(self, t):
        late = time.time() - t
        if late > 30:
            self.log(f"WARNING: wait_until {iso(t)} already {late:.0f}s in the past")
        while time.time() < t:
            self.check_stop()
            time.sleep(min(self.tick_s, max(0.0, t - time.time())))

    # -- iogovmon interface (annotate / snapshot) --------------------------------------------------------------------
    def annotate(self, text):
        self.log(f"annotate{' (dry)' if self.dry_annotate else ''}: {text}")
        if self.dry_annotate:
            return
        cmd = ["runuser", "-u", "iogovmon", "--", "env", f"IOGOVMON_CONFIG={self.cfg['paths']['iogovmon_config']}",
               "iogovmonctl", "annotate", "--action", text]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd="/")
            if r.returncode != 0:  # never fatal: the stage log is the primary record
                self.log(f"annotate FAILED rc={r.returncode}: {(r.stderr or r.stdout)[:200]}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"annotate FAILED: {type(exc).__name__}: {exc}")

    def snapshot(self, name):
        """status + analyze of iogovmon (read-only commands) into snap/<name>.txt."""
        with self.snap_lock:
            self.snapdir.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, IOGOVMON_CONFIG=self.cfg["paths"]["iogovmon_config"])
            parts = [f"# snapshot {name} taken {iso(time.time())}", self.df_line()]
            for args in (["status"], ["analyze", "--no-plots"]):
                try:
                    r = subprocess.run(["iogovmonctl", *args], capture_output=True, text=True, timeout=180, env=env, cwd="/")
                    parts.append(f"## iogovmonctl {' '.join(args)} (rc={r.returncode})\n{r.stdout}{r.stderr}")
                except Exception as exc:  # noqa: BLE001
                    parts.append(f"## iogovmonctl {' '.join(args)} FAILED: {type(exc).__name__}: {exc}")
            (self.snapdir / f"{name}.txt").write_text("\n".join(parts))
            self.log(f"snapshot {name} written")

    def snapshot_later(self, name, delay, after=None):
        """The closing of a window is done by the NEXT iogovmon run, so a phase-end snapshot is taken `delay` s later in a timer thread;
        the next phase starts on its boundary without waiting for it."""
        def job():
            try:
                self.snapshot(name)
            except Exception as exc:  # noqa: BLE001
                self.log(f"snapshot {name} FAILED: {exc}")
            finally:
                if after:
                    try:
                        after()
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"after-snapshot step of {name} FAILED: {exc}")
        t = threading.Timer(delay, job)
        t.daemon = True
        t.start()
        self.timers.append(t)

    def df_line(self):
        out = []
        for p in (self.root_mount, self.cow_mount):
            pct, used, avail, ino = fs_use(p)
            out.append(f"{p} {pct:.1f}% used ({used / GiB:.1f} GiB used, {avail / GiB:.1f} GiB avail, {ino:.0f}% inodes free)")
        return "fs: " + "; ".join(out)

    # -- phases ----------------------------------------------------------------------------------------------------
    def begin(self, name, note=""):
        self.cur_phase = {"name": name, "start": time.time(), "note": note}
        self.log(f"PHASE-START {name} {note} | {self.df_line()}")
        self.annotate(f"acceptance-2 stage {name} START {note}".strip())
        self.pending_raise = name if self.args.rehearsal_raise_in == name else None  # raised in supervise(), with writers alive
        if self.args.rehearsal_fake_stdev == name:
            self.fake_stdev = True
            self.log(f"rehearsal: fake st_dev equality switched on in phase {name}")
        if self.args.rehearsal_stop_in == name:
            self.log(f"rehearsal: touching the STOP file in phase {name}")
            self.stop_file.write_text("rehearsal\n")

    def end(self, name, note=""):
        rec = dict(self.cur_phase or {"name": name, "start": time.time()})
        rec["end"] = time.time()
        rec["end_note"] = note
        if name in self.limit_hit:
            rec["limit_reached"] = self.limit_hit[name]
        self.phases.append(rec)
        Path(self.cfg["paths"]["phases"]).write_text(json.dumps(self.phases, indent=1))
        self.log(f"PHASE-END {name} {note} | {self.df_line()}")
        self.annotate(f"acceptance-2 stage {name} END {note}".strip())
        self.snapshot_later(name, self.cfg["schedule"]["snapshot_grace_s"])
        self.cur_phase = None

    def timed_phase(self, name, start, end, body=None, note=""):
        self.wait_until(start)
        self.begin(name, note)
        if body:
            body(end)
        self.wait_until(end)
        self.end(name)

    # -- limits and mounts -------------------------------------------------------------------------------------------
    def mount_of(self, which):
        return self.cow_mount if which == "cow" else self.root_mount

    def limit_reasons(self, which=("root", "cow")):
        """Hard-limit violations for the named filesystems only (a limit on one must not stop the other's fill)."""
        lim = self.cfg["limits"]
        out = []
        for w in which:
            path = self.mount_of(w)
            pct, _used, _avail, ino = fs_use(path)
            maxpct = lim["cow_max_pct" if w == "cow" else "root_max_pct"]
            if w == "root" and self.root_limit_override is not None:
                maxpct = self.root_limit_override
            if pct > maxpct:
                out.append(f"{path} {pct:.2f}% > {maxpct:.2f}%")
            if ino < lim["min_free_inode_pct"]:
                out.append(f"{path} free inodes {ino:.1f}% < {lim['min_free_inode_pct']}%")
        return out

    def dev(self, path):
        d = os.stat(path).st_dev
        if self.fake_stdev and os.path.abspath(path).startswith(self.cow_mount):
            return os.stat(self.root_mount).st_dev  # rehearsal fake: pretend /srv/cow is not a separate mount
        return d

    def mounts_ok(self):
        """/srv/cow must be a SEPARATE mount from / (else a 94% fill of it would be a fill of the root fs), and each fill dir's parent
        must live on the filesystem its percentage is computed for."""
        try:
            rd, cd = self.dev(self.root_mount), self.dev(self.cow_mount)
            ok = True
            if cd == rd:
                self.log(f"MOUNT CHECK FAILED: {self.cow_mount} has the same st_dev as {self.root_mount}: not a separate mount")
                ok = False
            if self.dev(os.path.dirname(self.cow_dir)) != cd:
                self.log(f"MOUNT CHECK FAILED: parent of {self.cow_dir} is not on the {self.cow_mount} filesystem")
                ok = False
            if self.dev(os.path.dirname(self.root_dir)) != rd:
                self.log(f"MOUNT CHECK FAILED: parent of {self.root_dir} is not on the {self.root_mount} filesystem")
                ok = False
            return ok
        except OSError as exc:
            self.log(f"MOUNT CHECK FAILED: {exc}")
            return False

    # -- child processes -------------------------------------------------------------------------------------------
    def spawn(self, argv, tag):
        with open(self.work / "stage-fio.log", "a") as fh:  # the child has its own duplicate; our fd is closed at once
            p = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True, cwd="/")
        self.children[p.pid] = (p, tag)
        self.log(f"spawn {tag} pid={p.pid}")
        return p

    def kill_children(self):
        for pid, (p, tag) in list(self.children.items()):
            if p.poll() is None:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        end = time.time() + 8
        for pid, (p, tag) in list(self.children.items()):
            while p.poll() is None and time.time() < end:
                time.sleep(0.1)
            if p.poll() is None:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()
            self.children.pop(pid, None)

    def supervise(self, procs, phase, until=None, target_check=None, fs=("root", "cow")):
        """Wait for all `procs` (or `until`, or target_check() true), checking STOP every tick and the hard limits (of the filesystems
        in `fs`) every check_interval_s. Returns 'exited' | 'deadline' | 'target' | 'limit'."""
        interval = self.cfg["limits"]["check_interval_s"]
        last = 0.0
        t_sup = time.time()
        while True:
            self.check_stop()
            if self.pending_raise == phase and time.time() - t_sup > 1.0:
                self.pending_raise = None
                raise RuntimeError(f"rehearsal: forced exception in phase {phase} (writers alive)")
            if all(p.poll() is not None for p in procs):
                return "exited"
            now = time.time()
            if until is not None and now >= until:
                return "deadline"
            if now - last >= interval:
                last = now
                bad = self.limit_reasons(fs)
                if bad:
                    self.log(f"limit-reached [{phase}]: {'; '.join(bad)} -> stopping our writers, schedule continues")
                    self.limit_hit.setdefault(phase, []).extend(bad)
                    self.kill_children()
                    return "limit"
                if target_check and target_check():
                    return "target"
            time.sleep(self.tick_s)

    # -- fio helpers -----------------------------------------------------------------------------------------------
    def fio_fill_cmd(self, path, size):
        cmd = ["fio", "--name=fill", f"--filename={path}", f"--size={size}", "--rw=write", "--bs=1M", "--direct=1", "--ioengine=libaio",
               "--iodepth=4", "--refill_buffers", "--buffer_compress_percentage=0", "--fallocate=none", "--end_fsync=1"]
        if self.cfg["fill"].get("rate_mbps"):
            cmd.append(f"--rate={self.cfg['fill']['rate_mbps']}m")  # rehearsal only: slow enough for the 1 s limit check to act mid-write
        return cmd

    def fill(self, phase, directory, key):
        """Fill the filesystem of `directory` to the target with a few big sequential direct-IO files. Each fio --size is capped at the
        headroom to the HARD limit minus a margin, the limit is re-checked every check_interval_s, a wall-clock cap and a no-progress
        guard end a stuck fill. Only the filesystem being filled is checked."""
        fcfg, lim = self.cfg["fill"], self.cfg["limits"]
        which = "cow" if directory == self.cow_dir else "root"
        mount = self.mount_of(which)
        if not self.mounts_ok():
            self.log(f"fill {phase} [{which}] REFUSED: mount check failed, nothing written")
            self.limit_hit.setdefault(phase, []).append(f"{which}: mount check failed")
            return
        os.makedirs(directory, exist_ok=True)
        chunk = int(fcfg["chunk_gib"] * GiB) if not self.rehearsal else int(fcfg["chunk_bytes"])
        margin = int(lim["hard_margin_bytes"])
        n = len([f for f in os.listdir(directory) if f.startswith("big-")])

        def owned():
            return sum(e.stat().st_size for e in os.scandir(directory) if e.name.startswith("big-"))

        def to_target():
            if self.rehearsal:
                return int(fcfg["bytes"][f"{key}_{which}"]) - owned()
            _p, used, avail, _i = fs_use(mount)
            return bytes_to_pct(used, avail, fcfg[f"{which}_pct"][key])

        def to_limit():
            if self.no_limit_cap:
                return 1 << 60
            _p, used, avail, _i = fs_use(mount)
            maxpct = lim["cow_max_pct" if which == "cow" else "root_max_pct"]
            if which == "root" and self.root_limit_override is not None:
                maxpct = self.root_limit_override
            return bytes_to_pct(used, avail, maxpct) - margin

        goal = ("%d MiB owned" % (fcfg["bytes"][f"{key}_{which}"] // MiB)) if self.rehearsal else "%s%% used" % fcfg[f"{which}_pct"][key]
        self.log(f"fill {phase}: {directory} -> {goal} (hard limit margin {margin // MiB} MiB, wall cap {fcfg['max_wall_s']}s)")
        t0, w0, stalls = time.time(), owned(), 0
        prev_used, prev_owned = fs_use(mount)[1], owned()
        while True:
            self.check_stop()
            if time.time() - t0 > fcfg["max_wall_s"]:
                self.log(f"fill {phase} [{which}] wall-clock cap {fcfg['max_wall_s']}s reached, aborting this fill")
                self.limit_hit.setdefault(phase, []).append(f"{which}: wall-clock cap")
                break
            bad = self.limit_reasons((which,))
            if bad:
                self.log(f"limit-reached [{phase}] before writing: {'; '.join(bad)}")
                self.limit_hit.setdefault(phase, []).extend(bad)
                break
            tt, tl = to_target(), to_limit()
            size = fill_size(tt, tl, chunk)
            if size == 0:
                if tt < 32 * MiB:
                    self.log(f"fill {phase} [{which}]: target reached")
                else:
                    self.log(f"limit-reached [{phase}]: headroom to the hard limit minus margin is {tl // MiB} MiB, stopping before the target")
                    self.limit_hit.setdefault(phase, []).append(f"{which}: headroom {tl // MiB} MiB")
                break
            path = os.path.join(directory, f"big-{n:03d}.bin")
            n += 1
            p = self.spawn(self.fio_fill_cmd(path, size), f"fill-{which}-{n}")
            res = self.supervise([p], phase, fs=(which,), target_check=lambda: to_target() < 32 * MiB)
            if res in ("target", "limit"):
                self.kill_children()
                if res == "limit":
                    break
            elif p.returncode not in (0, None):
                self.log(f"fill {phase}: fio exited rc={p.returncode}, stopping this fill")
                break
            used_now, owned_now = fs_use(mount)[1], owned()  # progress = our own files grew OR the filesystem's used space grew (btrfs statfs lags)
            stalls = stalls + 1 if (res == "exited" and used_now - prev_used < MiB and owned_now - prev_owned < MiB) else 0
            prev_used, prev_owned = used_now, owned_now
            if stalls >= 2:
                self.log(f"fill {phase} [{which}]: fio exited 0 twice but used space did not grow: NO-PROGRESS guard, aborting this fill")
                self.limit_hit.setdefault(phase, []).append(f"{which}: no progress")
                break
        os.sync()
        dt_s = max(1e-3, time.time() - t0)
        written = owned() - w0
        self.log(f"fill {phase} done: wrote {written / GiB:.2f} GiB in {dt_s:.0f}s = {written / dt_s / MiB:.0f} MiB/s | {self.df_line()}")

    def quiesce(self):
        """After a loaded step: flush everything and wait (<= 120 s) until the disk reports nothing in flight, so 'quiet' starts clean."""
        os.sync()
        ok = 0
        end = time.time() + (10 if self.rehearsal else 120)
        while time.time() < end and ok < 5:
            self.check_stop()
            try:
                r, w = Path(f"/sys/block/{self.block_dev}/inflight").read_text().split()
                ok = ok + 1 if int(r) + int(w) == 0 else 0
            except OSError:
                break
            time.sleep(1.0 if not self.rehearsal else 0.2)
        self.log(f"quiesce: inflight zero x{ok}")

    # -- the steps -------------------------------------------------------------------------------------------------
    def throttle_reset(self):
        """Idempotent: remove any runtime read-IOPS limit from iogovmon.slice and VERIFY it is gone. Returns True only if it is."""
        r = self.sh(["systemctl", "set-property", "--runtime", "iogovmon.slice", "IOReadIOPSMax="])
        now = self.sh(["systemctl", "show", "iogovmon.slice", "-p", "IOReadIOPSMax", "--value"]).stdout.strip()
        ok = r.returncode == 0 and now == ""
        self.log(f"throttle RESET: rc={r.returncode} iogovmon.slice IOReadIOPSMax=[{now}] {'ok' if ok else 'RESTORE FAILED'} {r.stderr.strip()[:120]}")
        return ok

    def throttle(self, on):
        """kind=iothrottle (OFF in the shipped config): a runtime read-IOPS limit on iogovmon.slice (the probe's own cgroup) = a KNOWN,
        reversible change of the read latency iogovmon sees."""
        L = self.cfg["load"]
        if not on:
            if self.throttle_reset():
                self.throttled = False  # stays True when the restore failed, so cleanup tries again
            return
        r = self.sh(["systemctl", "set-property", "--runtime", "iogovmon.slice", f"IOReadIOPSMax={L['read_dev']} {L['read_iops']}"])
        now = self.sh(["systemctl", "show", "iogovmon.slice", "-p", "IOReadIOPSMax", "--value"]).stdout.strip()
        self.log(f"throttle ON: rc={r.returncode} iogovmon.slice IOReadIOPSMax=[{now}] {r.stderr.strip()[:120]}")
        self.throttled = True  # even on a non-zero rc: whatever was applied must be reset

    def a1_load_body(self, end):
        """A1: the calibrated change runs the WHOLE phase (kind none: nothing; iothrottle: held then removed; fio: restarted if it dies)."""
        L = self.cfg["load"]
        kind = L.get("kind", "none")
        if kind == "none":
            self.log("A1 control kind=none: nothing is applied")
            return
        if kind == "iothrottle":
            self.throttle(True)
            try:
                while time.time() < end - 1:
                    self.check_stop()
                    time.sleep(self.tick_s)
            finally:
                self.throttle(False)
            return
        path = self.cfg["paths"]["load_file"]
        while time.time() < end - 1:
            runtime = int(max(5, end - time.time() + 30))
            cmd = ["fio", "--name=load", f"--filename={path}", f"--size={L['size']}", f"--rw={L['rw']}", f"--bs={L['bs']}",
                   f"--iodepth={L['iodepth']}", "--direct=1", "--ioengine=libaio", "--time_based", f"--runtime={runtime}", "--fallocate=none"]
            if L["rate_mbps"]:
                cmd.append(f"--rate={L['rate_mbps']}m")
            p = self.spawn(cmd, "a1-load")
            res = self.supervise([p], "A1-positive-control", until=end)
            if res in ("deadline", "limit"):
                break
            self.log(f"A1 load exited early (rc={p.returncode}); restarting")
            time.sleep(1)
        self.kill_children()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def churn_burst(self, phase, start, burst_s):
        """B3: burst_s of docker/repo-like work: a small-file churn worker on / plus a 4k random-overwrite fio on a big file on /srv/cow."""
        C = self.cfg["churn"]
        deadline = start + burst_s
        if not self.mounts_ok():  # same guard as fill(): /srv/cow must be a separate mount, else the 4k overwrite file would load the root fs
            self.log(f"churn burst {phase} REFUSED: mount check failed, nothing written")
            self.limit_hit.setdefault(phase, []).append("mount check failed")
            return
        os.makedirs(self.root_dir, exist_ok=True)
        os.makedirs(self.cow_dir, exist_ok=True)
        ini = self.work / "fio-churn.ini"
        big = os.path.join(self.cow_dir, "overwrite-big.bin")
        ini.write_text(
            "[global]\nioengine=libaio\ndirect=1\nrefill_buffers=1\nbuffer_compress_percentage=0\nfallocate=none\n"
            f"filename={big}\nsize={C['fio_file']}\n"
            "[prefill]\nrw=write\nbs=1M\niodepth=4\n"
            "[overwrite]\nstonewall\nrw=randwrite\nbs=4k\niodepth=" + str(C["fio_iodepth"]) + "\ntime_based=1\nruntime=" + str(int(burst_s + 600)) + "\n")
        worker = self.spawn([sys.executable, os.path.abspath(__file__), "--churn-worker", "--dir", os.path.join(self.root_dir, "churn"),
                             "--deadline", str(deadline), "--files", str(C["files"]), "--min-kib", str(C["min_kib"]),
                             "--max-kib", str(C["max_kib"]), "--rewrite-pct", str(C["rewrite_pct"]), "--delete-pct", str(C["delete_pct"]),
                             "--fsync-batch", str(C["fsync_batch"]), "--seed", str(int(start))], "churn-worker")
        fio = self.spawn(["fio", str(ini)], "churn-fio-overwrite")
        res = self.supervise([worker, fio], phase, until=deadline)
        self.log(f"churn burst {phase} ended: {res} (worker rc={worker.poll()}, fio rc={fio.poll()} before the kill)")
        self.kill_children()

    def delete_files(self):
        """B5a: delete every file the stage created and flush; NO trim of any kind is issued here."""
        self.rm_created("B5a")
        os.sync()
        self.log(f"B5a deleted, no trim issued | {self.df_line()}")

    def trim_all(self):
        """B5b: fstrim both filesystems and record the trimmed bytes."""
        out = []
        for mp in (self.root_mount, self.cow_mount):
            t0 = time.time()
            try:
                r = subprocess.run(["fstrim", "-v", mp], capture_output=True, text=True, timeout=1800, cwd="/")
                msg = (r.stdout + r.stderr).strip()
            except Exception as exc:  # noqa: BLE001
                msg = f"FAILED {type(exc).__name__}: {exc}"
            self.log(f"fstrim {mp}: {msg} ({time.time() - t0:.0f}s)")
            out.append(msg)
        self.annotate("acceptance-2 B5 fstrim: " + " | ".join(out))

    def bad_paths(self):
        """Configured fill dirs / stage files that fail the deletion allowlists; a run refuses to start with any (cleanup skips them and reports failure)."""
        return sorted([d for d in self.allowed_dirs if not allowed_fill_dir(d)] + [f for f in self.files_allowed if not allowed_file(f)])

    def rm_created(self, why):
        """Delete only exactly-named fill dirs: basename iogov-fill, parent in ALLOWED_PARENTS, no symlink, realpath unchanged. EVERY skip
        and every deletion error is logged and remembered in self.rm_failed (cleanup then reports failure)."""
        for d in sorted(self.allowed_dirs):
            n = os.path.normpath(d)
            if not allowed_fill_dir(n):
                self.log(f"SKIPPED {d}: not an allowed fill dir path (basename iogov-fill under {sorted(ALLOWED_PARENTS)})")
                self.rm_failed.append(d)
                continue
            if os.path.islink(n):
                self.log(f"SKIPPED {n}: is a symlink")
                self.rm_failed.append(n)
                continue
            if not os.path.exists(n):
                self.log(f"skip {n}: not present")
                continue
            if os.path.realpath(n) != n:
                self.log(f"SKIPPED {n}: realpath differs ({os.path.realpath(n)})")
                self.rm_failed.append(n)
                continue
            t0, errs = time.time(), []
            shutil.rmtree(n, onexc=lambda fn, pth, exc: errs.append(f"{pth}: {exc}"))
            self.log(f"removed {n} ({why}, {time.time() - t0:.0f}s){' WITH ERRORS: ' + '; '.join(errs[:3]) if errs else ''}")
            if errs:
                self.rm_failed.append(n)
        for f in sorted(self.files_allowed):
            if not allowed_file(f):  # the configured name is data, not trust: never unlink a path outside the allowlist
                self.log(f"SKIPPED file {f}: not an allowed stage file path (iogov-* directly under {sorted(ALLOWED_PARENTS)})")
                self.rm_failed.append(f)
                continue
            try:
                os.unlink(f)
                self.log(f"removed file {f}")
            except FileNotFoundError:
                self.log(f"skip file {f}: not present")
            except OSError as exc:
                self.log(f"FAILED to remove {f}: {exc}")
                self.rm_failed.append(f)

    def kill_orphans(self, proc="/proc"):
        """Processes (fio, the churn worker) whose command line names one of our fill dirs or load files and that this run does not own.
        A configured path outside the allowlists is never a needle, and a needle matches whole path components only (see names_path)."""
        mine = {os.getpid(), os.getppid()} | set(self.children)
        needles = ([os.path.normpath(x) for x in self.allowed_dirs if allowed_fill_dir(x)]
                   + [os.path.normpath(x) for x in self.files_allowed if allowed_file(x)] + [str(self.work / "fio-churn.ini")])
        killed = []
        for entry in os.scandir(proc):
            if not entry.name.isdigit() or int(entry.name) in mine:
                continue
            try:
                argv = [a.decode(errors="replace") for a in Path(f"{proc}/{entry.name}/cmdline").read_bytes().split(b"\0") if a]
            except OSError:
                continue
            cmd = " ".join(argv)
            if orphan_kind(argv, needles):
                try:
                    os.kill(int(entry.name), signal.SIGKILL)  # the single pid, never its process group (that could contain this very process)
                    killed.append(f"{entry.name}:{cmd[:60]}")
                except (ProcessLookupError, PermissionError):
                    pass
        self.log(f"orphan scan: killed {killed or 'none'}")

    # fstrim.timer: stopped for the run (it would fall into quiet windows), restored only if it was active before ----------------
    def fstrim_stop(self):
        name, marker = self.cfg["paths"]["fstrim_timer"], self.work / "fstrim-timer-was-active"
        if marker.exists():
            self.log(f"{name}: marker already present (stopped by an earlier run, not restored yet): not touching it")
            return
        active = self.sh(["systemctl", "is-active", name]).stdout.strip() == "active"
        if not active:
            self.log(f"{name} was not active: nothing to stop, nothing to restore")
            return
        marker.write_text(iso(time.time()) + "\n")
        r = self.sh(["systemctl", "stop", name])
        self.log(f"{name} STOPPED for the run (was active): rc={r.returncode} {r.stderr.strip()[:100]}")

    def fstrim_restore(self):
        name, marker = self.cfg["paths"]["fstrim_timer"], self.work / "fstrim-timer-was-active"
        if not marker.exists():
            return True
        r = self.sh(["systemctl", "start", name])
        ok = r.returncode == 0
        self.log(f"{name} RESTORED (was active before): rc={r.returncode} {r.stderr.strip()[:100]}")
        if ok:
            marker.unlink()
        return ok

    # cron's `fstrim -a` (/etc/cron.d/kernel, daily 10:33 local) would trim in the middle of a quiet window: the fstrim line is commented
    # out for the run (marker + backup copy) and put back by the exact reverse edit -------------------------------------------------
    CRON_MARK = b"#IOGOV-A2-DISABLED# "

    def _atomic_write(self, f, data):
        """Write bytes next to the target (a dotted temp name is ignored by cron) and os.replace it, keeping mode and owner."""
        st = os.stat(f)
        tmp = f.with_name("." + f.name + ".iogov-tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, st.st_mode & 0o7777)
            try:
                os.write(fd, data)
                os.fchmod(fd, st.st_mode & 0o7777)
                os.fchown(fd, st.st_uid, st.st_gid)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, f)
        finally:
            if tmp.exists():
                tmp.unlink()

    def cron_fstrim_off(self):
        f, mk = Path(self.cfg["paths"]["cron_fstrim_file"]), self.work / "cron-fstrim-disabled"
        if mk.exists():
            self.log(f"{f}: cron-fstrim marker already present, not touching it")
            return
        if not f.exists():
            self.log(f"{f}: not present, nothing to disable")
            return
        raw = f.read_bytes()
        out, n = [], 0
        for ln in raw.splitlines(keepends=True):  # bytes: CRLF line ends are preserved
            if b"fstrim" in ln and not ln.lstrip().startswith(b"#"):
                out.append(self.CRON_MARK + ln)
                n += 1
            else:
                out.append(ln)
        if not n:
            self.log(f"{f}: no active fstrim line")
            return
        shutil.copy2(f, self.work / "cron-kernel.before")
        mk.write_text(json.dumps({"orig_sha256": hashlib.sha256(raw).hexdigest(), "t": iso(time.time())}))  # marker FIRST: a crash now is recoverable
        self._atomic_write(f, b"".join(out))
        self.log(f"{f}: {n} fstrim line(s) DISABLED for the run (original sha256 {hashlib.sha256(raw).hexdigest()[:16]}, backup {self.work / 'cron-kernel.before'})")

    def cron_fstrim_restore(self):
        """Reverse edit on the CURRENT content (a change made meanwhile is kept); success = no mark left. Identity is judged by sha256."""
        f, mk = Path(self.cfg["paths"]["cron_fstrim_file"]), self.work / "cron-fstrim-disabled"
        if not mk.exists():
            return True
        try:
            orig = json.loads(mk.read_text()).get("orig_sha256")
        except (OSError, ValueError):
            orig = None
        if not f.exists():
            self.log(f"{f}: gone while the cron edit was active; marker removed")
            mk.unlink()
            return False
        raw = f.read_bytes()
        new = b"".join(ln[len(self.CRON_MARK):] if ln.startswith(self.CRON_MARK) else ln for ln in raw.splitlines(keepends=True))
        if new != raw:
            self._atomic_write(f, new)
        identical = hashlib.sha256(new).hexdigest() == orig
        left = self.CRON_MARK in new
        self.log(f"{f}: fstrim line(s) RESTORED, sha256 identical to the original: {identical}"
                 f"{'' if identical else ' (file changed meanwhile or no sha recorded: only our marks were removed, other changes kept)'}"
                 f"{' MARKS STILL PRESENT' if left else ''}")
        if not left:
            mk.unlink()
        return not left

    # filesystems that trim on every delete (mount option discard / discard=async): remounted nodiscard for the B5a no-trim test ----------
    def sh(self, argv, timeout=30):
        """Run a helper command with a timeout; NEVER raises: a hang returns rc 124, any other failure rc 125 (both logged)."""
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd="/")
        except subprocess.TimeoutExpired:
            self.log(f"TIMEOUT after {timeout}s: {' '.join(argv)[:90]}")
            return types.SimpleNamespace(returncode=124, stdout="", stderr=f"timeout after {timeout}s")
        except Exception as exc:  # noqa: BLE001
            self.log(f"COMMAND FAILED {' '.join(argv)[:60]}: {type(exc).__name__}: {exc}")
            return types.SimpleNamespace(returncode=125, stdout="", stderr=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def discard_token(opts):
        return next((o for o in opts.split(",") if o == "discard" or o.startswith("discard=")), None)

    def nodiscard_on(self):
        mk = self.work / "discard-restore.json"
        saved = json.loads(mk.read_text()) if mk.exists() else {}
        for mount in (self.root_mount, self.cow_mount):
            opts = self.sh(["findmnt", "-no", "OPTIONS", mount]).stdout.strip()
            tok = self.discard_token(opts)
            if tok is None:
                self.log(f"{mount}: no discard mount option [{opts[:90]}]: the filesystem does not trim on delete")
                continue
            saved[mount] = tok
            mk.write_text(json.dumps(saved))
            r = self.sh(["mount", "-o", "remount,nodiscard", mount])
            now = self.sh(["findmnt", "-no", "OPTIONS", mount]).stdout.strip()
            ok = r.returncode == 0 and self.discard_token(now) is None
            self.log(f"{mount}: remount,nodiscard rc={r.returncode} options now [{now[:90]}] "
                     f"{'ok (was ' + tok + ')' if ok else 'NODISCARD FAILED: the no-trim test is NOT valid for this filesystem'} {r.stderr.strip()[:100]}")
            self.annotate(f"acceptance-2 B5a {mount} remount nodiscard {'ok' if ok else 'FAILED'} (was {tok})")
            if not ok:
                self.invalid_b5a.append(mount)
                self.log(f"B5a-INVALID-discard-active: {mount} still trims deletes; the no-trim result of this phase is NOT valid for it (schedule continues)")
                self.annotate(f"acceptance-2 B5a-INVALID-discard-active ({mount}: nodiscard remount failed, deletes are trimmed)")

    def discard_restore(self):
        mk = self.work / "discard-restore.json"
        if not mk.exists():
            return True
        all_ok = True
        for mount, tok in json.loads(mk.read_text()).items():
            r = self.sh(["mount", "-o", f"remount,{tok}", mount])
            now = self.sh(["findmnt", "-no", "OPTIONS", mount]).stdout.strip()
            ok = r.returncode == 0 and self.discard_token(now) == tok
            self.log(f"{mount}: remount,{tok} rc={r.returncode} options now [{now[:90]}] {'RESTORED' if ok else 'RESTORE FAILED'} {r.stderr.strip()[:100]}")
            all_ok &= ok
        if all_ok:
            mk.unlink()
        return all_ok

    def load_phases(self):
        f = Path(self.cfg["paths"]["phases"])
        return json.loads(f.read_text()) if f.exists() else []

    def journald_snapshot(self, tag):
        """journald vacuums at 85% used on `/`: keep its disk usage and the last 2000 lines before the heavy fills."""
        self.snapdir.mkdir(parents=True, exist_ok=True)
        parts = [f"# journald snapshot {tag} {iso(time.time())}"]
        for argv in (["journalctl", "--disk-usage"], ["journalctl", "-n", "2000", "--no-pager", "-o", "short-iso"]):
            r = subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd="/")
            parts.append(f"## {' '.join(argv)} (rc={r.returncode})\n{r.stdout}{r.stderr}")
        (self.snapdir / f"journald-{tag}.txt").write_text("\n".join(parts))
        self.log(f"journald snapshot {tag} written")

    def cleanup(self, why, full=False):
        """Kill writers, reset the throttle, delete our files, restore fstrim.timer / discard / the cron line. EVERY step is isolated: a failed or
        hung step is logged and never skips the later ones. Returns True only when everything succeeded and nothing of ours remains."""
        self.log(f"cleanup ({why}): killing writers, deleting everything we created")
        ok = True

        def step(name, fn):
            nonlocal ok
            try:
                if fn() is False:
                    ok = False
                    self.log(f"cleanup step {name}: reported failure")
            except Exception as exc:  # noqa: BLE001
                ok = False
                self.log(f"cleanup step {name} FAILED: {type(exc).__name__}: {exc}")

        if why != "normal":
            step("cancel timers", lambda: [t.cancel() for t in self.timers])
        step("kill children", self.kill_children)
        if full:
            step("kill orphans", self.kill_orphans)
        if full or self.throttled or self.cfg["load"].get("kind") == "iothrottle":
            step("throttle reset", self.throttle_reset)
        step("remove our files", lambda: self.rm_created(why))
        step("restore fstrim.timer", self.fstrim_restore)
        step("restore discard", self.discard_restore)
        step("restore cron fstrim line", self.cron_fstrim_restore)
        try:
            left = [d for d in self.allowed_dirs if os.path.exists(d)] + [f for f in self.files_allowed if os.path.exists(f)]
        except Exception as exc:  # noqa: BLE001
            left = [f"(could not check: {exc})"]
        if left or self.rm_failed:
            ok = False
        self.log(f"cleanup done; remaining: {left or 'none'}; failures: {self.rm_failed or 'none'}; ok={ok}")
        return ok

    # -- the schedule ----------------------------------------------------------------------------------------------
    def quiet_after(self, tag, windows):
        """After a loaded step: flush, a tail until the next window boundary, then `windows` quiet windows."""
        W = self.cfg["schedule"]["window_s"]
        self.quiesce()
        first = math.ceil((time.time() + self.cfg["schedule"]["settle_s"]) / W) * W
        if first - time.time() > 0.5:
            self.timed_phase(f"{tag}-tail", time.time(), first, note="(quiet tail to the window boundary)")
        self.timed_phase(f"{tag}-quiet", first, first + windows * W, note=f"({windows} quiet windows)")
        return first + windows * W

    def schedule(self, t0, resume=False):
        S = self.cfg["schedule"]
        W = S["window_s"]
        w0 = math.ceil((t0 + S["w0_min_lead_s"]) / W) * W
        self.log(f"T0={iso(t0)} W0={iso(w0)} window={W}s")
        (self.work / "stage-state.json").write_text(json.dumps({"t0": t0, "w0": w0, "window_s": W, "rehearsal": self.rehearsal}))
        a0, a1, a2 = S["a0_windows"], S["a1_windows"], S["a2_windows"]
        if resume:
            self.phases = self.load_phases()  # the replaced stage's A-pre record stays in the table
            self.log(f"RESUMED: replacing a running stage with the same T0 and W0; phase table kept ({len(self.phases)} finished phase record(s))")
            self.annotate(f"acceptance-2 stage RESUMED (replacement stage, same T0 {iso(t0)} and W0 {iso(w0)}; timetable unchanged)")
        if resume and time.time() >= w0:
            self.cur_phase = {"name": "A0-baseline", "start": w0, "note": "(nothing running; RESUMED mid-phase)"}
            self.log(f"PHASE-CONTINUE A0-baseline since {iso(w0)} (RESUMED)")
            self.wait_until(w0 + a0 * W)
            self.end("A0-baseline", "(resumed)")
        else:
            self.timed_phase("A-pre", time.time(), w0, note="(iogovmon runs; waiting for W0)")
            self.timed_phase("A0-baseline", w0, w0 + a0 * W, note="(nothing running)")
        kind = self.cfg["load"].get("kind", "none")
        if a1:
            self.timed_phase("A1-control", w0 + a0 * W, w0 + (a0 + a1) * W, body=self.a1_load_body, note=f"(control kind={kind})")
        a_end = w0 + (a0 + a1 + a2) * W
        if a2:
            self.timed_phase("A2-recovery", w0 + (a0 + a1) * W, a_end, note="(control off)")
        self.log(f"STAGE-A-DONE (a0={a0} a1={a1} a2={a2} windows, control kind={kind})")
        self.annotate(f"acceptance-2 STAGE-A-DONE (baseline {a0} windows; live control phases: {a1}+{a2} windows, kind={kind})")
        marker = self.work / ("STAGE-A-DONE" if not self.rehearsal else "STAGE-A-DONE-rehearsal")
        self.snapshot_later("STAGE-A-DONE", S["snapshot_grace_s"], after=lambda: marker.write_text(f"{iso(time.time())}\n"))  # marker even if the snapshot failed
        # ---- B1 .. B5: event-driven (a fill ends when its target is reached), then aligned to the next boundary
        self.wait_until(a_end)
        self.begin("B1-fill", "(/srv/cow to 50%)")
        self.fill("B1-fill", self.cow_dir, "b1")
        self.end("B1-fill")
        self.quiet_after("B1", S["b1_quiet"])
        self.begin("B2-fill", "(/srv/cow to 80%, / to 60%)")
        self.fill("B2-fill", self.cow_dir, "b2")
        self.fill("B2-fill", self.root_dir, "b2")
        self.end("B2-fill")
        nxt = self.quiet_after("B2", S["b2_quiet"])
        for cyc in (1, 2):
            b = nxt  # the burst starts on the window boundary where the previous quiet phase ended
            self.wait_until(b)
            self.begin(f"B3-c{cyc}-burst", "(small-file churn + 4k random overwrite)")
            self.churn_burst(f"B3-c{cyc}-burst", b, self.cfg["churn"]["burst_s"])
            self.end(f"B3-c{cyc}-burst")
            self.quiesce()
            nxt = math.ceil((b + self.cfg["churn"]["burst_s"]) / W) * W + S["b3_extra_quiet"] * W
            self.timed_phase(f"B3-c{cyc}-quiet", time.time(), nxt, note="(quiet to the window end + extra windows)")
        try:
            self.journald_snapshot("pre-B4")
        except Exception as exc:  # noqa: BLE001
            self.log(f"journald snapshot FAILED: {exc}")
        self.begin("B4-fill", "(/srv/cow to 94%, / to 85%)")
        self.fill("B4-fill", self.cow_dir, "b4")
        if self.args.rehearsal_force_limit:  # rehearsal only: a limit just above today's usage, so the root fill must trip it
            self.root_limit_override = fs_use(self.root_mount)[0] + 0.02
            self.no_limit_cap = True  # no pre-capped size: the 1 s check must kill fio mid-write
            self.log(f"rehearsal: forced root limit {self.root_limit_override:.2f}% (size cap off)")
        self.fill("B4-fill", self.root_dir, "b4")
        self.root_limit_override = None
        self.no_limit_cap = False
        self.end("B4-fill")
        self.quiet_after("B4", S["b4_quiet"])
        self.begin("B5a-delete", "(delete everything we created WITHOUT any trim; discard off)")
        self.nodiscard_on()
        self.delete_files()
        self.end("B5a-delete", "B5a-INVALID-discard-active" if self.invalid_b5a else "")
        self.quiet_after("B5a", S["b5a_quiet"])
        self.begin("B5b-fstrim", "(discard restored, fstrim -v both filesystems)")
        self.discard_restore()
        self.trim_all()
        self.end("B5b-fstrim")
        self.quiet_after("B5b", S["b5b_quiet"])
        # ---- done
        self.log("STAGE-DONE")
        self.annotate("acceptance-2 STAGE-DONE")
        done = self.work / ("STAGE-DONE" if not self.rehearsal else "STAGE-DONE-rehearsal")
        def finish():
            try:
                build_summary(self)
            except Exception as exc:  # noqa: BLE001
                self.log(f"build_summary FAILED: {type(exc).__name__}: {exc}")
            finally:
                done.write_text(f"{iso(time.time())}\n")
        self.snapshot_later("STAGE-DONE", S["snapshot_grace_s"], after=finish)
        self.sleep(S["snapshot_grace_s"] + 5)
        for t in self.timers:
            t.join(timeout=600)


# ---------------------------------------------------------------------------------------------------------------------
# churn worker (a separate process so it can be killed): docker/repo-like small-file work
# ---------------------------------------------------------------------------------------------------------------------
def churn_worker(a):
    rnd = random.Random(a.seed)
    deadline = a.deadline
    n = a.files
    made_dirs = set()

    def path_of(root, i):
        return os.path.join(root, f"d{i % 16:02x}", f"e{(i // 16) % 16:02x}", f"f{(i // 256) % 16:02x}", f"file-{i:07d}.dat")

    def write_files(root, idxs, mode):
        batch = []
        for k, i in enumerate(idxs):
            if time.time() >= deadline:
                break
            p = path_of(root, i)
            d = os.path.dirname(p)
            if d not in made_dirs:
                os.makedirs(d, exist_ok=True)
                made_dirs.add(d)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if mode == "rewrite" else 0), 0o600)
            os.write(fd, os.urandom(rnd.randint(a.min_kib, a.max_kib) * 1024))
            batch.append(fd)
            if len(batch) >= a.fsync_batch:
                for f in batch:
                    os.fsync(f)
                    os.close(f)
                batch = []
        for f in batch:
            os.fsync(f)
            os.close(f)

    prev = None
    rnum = 0
    while time.time() < deadline:
        root = os.path.join(a.dir, f"r{rnum}")
        made_dirs.clear()
        t0 = time.time()
        write_files(root, range(n), "create")
        write_files(root, rnd.sample(range(n), int(n * a.rewrite_pct / 100.0)), "rewrite")
        for i in rnd.sample(range(n), int(n * a.delete_pct / 100.0)):
            if time.time() >= deadline:
                break
            try:
                os.unlink(path_of(root, i))
            except FileNotFoundError:
                pass
        print(f"churn round {rnum} done in {time.time() - t0:.0f}s", flush=True)
        if prev and time.time() < deadline:
            shutil.rmtree(prev, ignore_errors=True)  # a 'docker prune' of the previous round
        prev = root
        rnum += 1


# ---------------------------------------------------------------------------------------------------------------------
# summary: per phase counts from the iogovmon journal + the recognizer state
# ---------------------------------------------------------------------------------------------------------------------
def build_summary(S):
    phases = json.loads(Path(S.cfg["paths"]["phases"]).read_text()) if Path(S.cfg["paths"]["phases"]).exists() else []
    jdir = Path(S.cfg["paths"]["journal_dir"])
    recs = []
    for f in sorted(jdir.glob("journal-*.jsonl")):
        for line in f.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") in ("probe", "bench", "skip", "recommendation", "run_end"):
                recs.append(r)

    def med(v):
        return round(statistics.median(v), 1) if v else None

    lines = [f"iogovmon acceptance-2 stage summary, generated {iso(time.time())}{' (REHEARSAL)' if S.rehearsal else ''}", ""]
    for ph in phases:
        in_ph = [r for r in recs if ph["start"] <= r["t"] < ph["end"]]
        cold_q = [x for r in in_ph if r["type"] == "probe" and r["lane"] == "r_cold" and r.get("quiet") for x in r["lat_us"]]
        wit_q = [x for r in in_ph if r["type"] == "probe" and r["lane"] == "r_witness" and r.get("quiet") for x in r["lat_us"]]
        ticks = [r for r in in_ph if r["type"] == "probe" and r["lane"] == "r_cold"]
        benches = [r for r in in_ph if r["type"] == "bench"]
        lanes = {}
        for b in benches:
            for name, ln in (b.get("lanes") or {}).items():
                if not isinstance(ln, dict) or not ln.get("valid"):
                    continue
                base = name.split("@")[0]
                try:
                    if base == "w_over_4k":
                        lanes.setdefault(base, []).append(statistics.median(ln["lat"]["lat_us"]))
                    elif base in ("w_fresh_qd1", "w_fresh_qd4"):
                        lanes.setdefault(base, []).append(ln["rate_durable_bps"] / MiB)
                    elif base == "r_seq":
                        lanes.setdefault(base, []).append(ln["rate_bps"] / MiB)
                except (KeyError, TypeError, statistics.StatisticsError):
                    pass
        status = {}
        for b in benches:
            k = b["status"] + (f"({str(b.get('abort'))[:40]})" if b.get("abort") else "")
            status[k] = status.get(k, 0) + 1
        skips = {}
        for r in in_ph:
            if r["type"] == "skip":
                for reason in r.get("reasons", []):
                    k = f"{r['kind']}:{str(reason).split(':')[0]}"
                    skips[k] = skips.get(k, 0) + 1
        recos = [f"{r.get('lane')}|{r.get('kind')}" for r in in_ph if r["type"] == "recommendation"]
        dur = (ph["end"] - ph["start"]) / 3600.0
        lines.append(f"== {ph['name']}  {iso(ph['start'])} .. {iso(ph['end'])} ({dur:.2f} h){'  LIMIT-REACHED ' + str(ph['limit_reached']) if ph.get('limit_reached') else ''}")
        lines.append(f"   ticks {len(ticks)} (quiet {sum(1 for r in ticks if r.get('quiet'))}); r_cold median {med(cold_q)} us n={len(cold_q)}; "
                     f"r_witness median {med(wit_q)} us n={len(wit_q)}")
        lines.append(f"   benches {len(benches)} {status or ''}; bench-lane medians: " +
                     (", ".join(f"{k}={med(v)}{' us' if k == 'w_over_4k' else ' MiB/s'}" for k, v in sorted(lanes.items())) or "none"))
        lines.append(f"   skips {skips or 'none'}; recognizer events {recos or 'none'}")
    lines += ["", "== recognizer lane state (agg.json, current)"]
    try:
        agg = json.loads(Path(S.cfg["paths"]["agg_file"]).read_text())
        for name, ln in sorted(agg.get("lanes", {}).items()):
            rows = ln.get("rows", [])
            last = rows[-1] if rows else {}
            lines.append(f"   {name}: phase {ln.get('phase')} learn {len(ln.get('learn', []))} windows_seen {ln.get('windows_seen')} "
                         f"insufficient {ln.get('insufficient_windows')} last x_log2 {last.get('x')} events {[e['kind'] for e in ln.get('events', [])]}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"   (agg.json unreadable: {exc})")
    Path(S.cfg["paths"]["summary"]).write_text("\n".join(lines) + "\n")
    S.log(f"summary written: {S.cfg['paths']['summary']}")


# ---------------------------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="/root/iogovmon-accept2/stage.toml")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--t0", help="ISO time (or epoch) of the fresh iogovmon start; W0 is computed from it")
    ap.add_argument("--rehearsal", action="store_true", help="same phases, seconds long, 1 GiB scratch budget, annotations dry")
    ap.add_argument("--dry-annotate", action="store_true", help="only log the iogovmon annotations")
    ap.add_argument("--rehearsal-force-limit", action="store_true")
    ap.add_argument("--rehearsal-stop-in", metavar="PHASE")
    ap.add_argument("--rehearsal-raise-in", metavar="PHASE")
    ap.add_argument("--resume", action="store_true", help="replace a running stage during A0: same --t0 and --w0, same phase table")
    ap.add_argument("--w0", help="--resume only: the W0 of the stage being replaced (checked against W0 computed from --t0)")
    ap.add_argument("--rehearsal-fake-stdev", metavar="PHASE", help="rehearsal: make the /srv/cow st_dev check fail from this phase on (START = at startup)")
    ap.add_argument("--journal-dir", help="--summary only: read this journal dir instead of the live one")
    ap.add_argument("--phases-file", help="--summary only: phases json to use")
    ap.add_argument("--summary-out", help="--summary only: write the summary here")
    ap.add_argument("--cleanup", action="store_true", help="delete everything a crashed run may have left (needs the lock)")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--churn-worker", action="store_true", help=argparse.SUPPRESS)
    for opt, typ in (("--dir", str), ("--deadline", float), ("--files", int), ("--min-kib", int), ("--max-kib", int), ("--rewrite-pct", float),
                     ("--delete-pct", float), ("--fsync-batch", int), ("--seed", int)):
        ap.add_argument(opt, type=typ, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.churn_worker:
        return churn_worker(a)
    cfg = load_cfg(a.config, a.rehearsal)
    S = Stage(cfg, a)
    if a.status:
        print(Path(cfg["paths"]["log"]).read_text().splitlines()[-15:] if Path(cfg["paths"]["log"]).exists() else "no log yet")
        return 0
    if a.summary:
        if a.journal_dir:
            cfg["paths"]["journal_dir"] = a.journal_dir
        if a.phases_file:
            cfg["paths"]["phases"] = a.phases_file
        if a.summary_out:
            cfg["paths"]["summary"] = a.summary_out
        build_summary(S)
        return 0
    if not (a.run or a.rehearsal or a.cleanup):
        ap.error("one of --run --rehearsal --cleanup --summary --status is required")
    S.work.mkdir(parents=True, exist_ok=True)
    lockfh = open(cfg["paths"]["lock_file"], "w")
    try:
        fcntl.flock(lockfh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("REFUSED: another stage run holds the lock", file=sys.stderr)
        return 3
    if a.cleanup:
        ok = S.cleanup("--cleanup", full=True)  # also kills orphaned fio of our fill dirs, resets the throttle unconditionally, restores fstrim.timer
        return 0 if ok else 4
    if S.bad_paths():
        print(f"REFUSED: configured path(s) outside the deletion allowlists (iogov-fill dirs / iogov-* files under {sorted(ALLOWED_PARENTS)}): {S.bad_paths()}",
              file=sys.stderr)
        return 2
    if S.stop_file.exists():
        print(f"REFUSED: {S.stop_file} exists (remove it first)", file=sys.stderr)
        return 2
    left = [d for d in S.allowed_dirs if os.path.exists(d) and os.listdir(d)]
    if left:
        print(f"REFUSED: leftovers in {left}; run --cleanup first", file=sys.stderr)
        return 2
    if a.run and not a.t0:
        ap.error("--run needs --t0")
    t0 = utc_t(a.t0) if a.t0 else time.time()
    sc = cfg["schedule"]
    if a.resume:
        if not (a.run or a.rehearsal) or not a.t0 or not a.w0:
            ap.error("--resume needs --run (or --rehearsal), --t0 and --w0")
        W = sc["window_s"]
        w0_arg, w0_calc = utc_t(a.w0), math.ceil((t0 + sc["w0_min_lead_s"]) / W) * W
        if abs(w0_arg - w0_calc) > 1:
            print(f"REFUSED: --w0 {iso(w0_arg)} does not match W0 {iso(w0_calc)} computed from --t0 {iso(t0)}", file=sys.stderr)
            return 2
        try:
            st = json.loads((S.work / "stage-state.json").read_text())
            same = abs(st["t0"] - t0) < 1 and abs(st["w0"] - w0_arg) < 1
        except Exception:  # noqa: BLE001
            st, same = None, False
        if not same:
            print(f"REFUSED: stage-state.json ({S.work / 'stage-state.json'}: {st}) does not match --t0 {iso(t0)} / --w0 {iso(w0_arg)}", file=sys.stderr)
            return 2
        b_start = w0_arg + (sc["a0_windows"] + sc["a1_windows"] + sc["a2_windows"]) * W
        if time.time() >= b_start - sc["resume_guard_s"]:
            print(f"REFUSED: resume only before B1 starts minus {sc['resume_guard_s']}s: B1 starts {iso(b_start)}, now {iso(time.time())}", file=sys.stderr)
            return 2
    elif a.run:
        why = validate_t0(t0, time.time(), sc["window_s"], sc["w0_min_lead_s"], sc["t0_max_age_s"], sc["t0_max_future_s"])
        if why:
            print(f"REFUSED: {why}", file=sys.stderr)
            return 2
    if a.rehearsal_fake_stdev == "START":
        S.fake_stdev = True
    if not S.mounts_ok():
        print("REFUSED: mount check failed (see the log)", file=sys.stderr)
        return 2

    def on_signal(signum, _frame):
        S.sig_stop = signal.Signals(signum).name

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    rc = 0
    S.log(f"stage start pid={os.getpid()} mode={'rehearsal' if a.rehearsal else 'run'} cfg={a.config}")
    try:
        S.fstrim_stop()
        S.cron_fstrim_off()
        S.schedule(t0, resume=a.resume)
        if not S.cleanup("normal"):
            rc = 4
    except StopRequested as exc:
        S.log(f"STOPPED ({exc}): clean exit")
        for name, fn in (("cleanup", lambda: S.cleanup("stopped")),
                         ("annotate", lambda: S.annotate(f"acceptance-2 stage STOPPED ({exc}); all stage files removed")),
                         ("snapshot", lambda: S.snapshot("stopped")),
                         ("rename STOP", lambda: S.stop_file.rename(S.stop_file.with_name(f"STOP.consumed-{int(time.time())}")) if S.stop_file.exists() else None)):
            try:
                if fn() is False and name == "cleanup":
                    rc = 4
            except Exception as exc2:  # noqa: BLE001
                S.log(f"STOP branch step {name} FAILED: {type(exc2).__name__}: {exc2}")
                rc = 4
    except BaseException as exc:  # noqa: BLE001
        S.log(f"EXCEPTION {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        for name, fn in (("cleanup", lambda: S.cleanup("exception")),
                         ("annotate", lambda: S.annotate(f"acceptance-2 stage ABORTED by exception {type(exc).__name__}; all stage files removed"))):
            try:
                fn()
            except Exception as exc2:  # noqa: BLE001
                S.log(f"exception branch step {name} FAILED: {type(exc2).__name__}: {exc2}")
        rc = 1
    S.log(f"stage exit rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
