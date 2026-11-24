#!/usr/bin/env python3
"""refprobe: an INDEPENDENT dense latency probe for the iogovmon acceptance-2 run (python3 stdlib only).

Two probe files of random (incompressible) data, one per filesystem: `/var/tmp/iogov-refprobe/probe.bin` on `/` (ext4) and
`/srv/cow/iogov-refprobe/probe.bin` on the separate `/srv/cow` mount (btrfs); 1 GiB each, written once with O_DIRECT sequential writes.
Then every `--interval` s (default 10) per file: 8 random 4 KiB-aligned O_DIRECT reads and 1 random 4 KiB O_DIRECT overwrite followed by
fdatasync, each timed with CLOCK_MONOTONIC_RAW, appended (buffered, flushed every 60 s, one file per filesystem and UTC day, total cap
200 MB) to /root/iogovmon-accept2/refprobe/<fs>-YYYYMMDD.csv as `ts_utc_ns,fs,op,offset,latency_us,errno`  (op = read | wr_sync).
VERSION 2 adds two COLD read sets next to the hot 1 GiB file (op `read-hot`, was `read`; `wr_sync` is the overwrite of the hot file):
  read-big   8 random 4 KiB O_DIRECT reads per 10 s of /srv/cow/iogov-refprobe/probe-big.bin (24 GiB random data, written once; refused if
             /srv/cow would be above 60% used after the write): too big for a backend cache, a stand-in for iogovmon's cold reads;
  read-fill  8 random (file, offset) reads per 10 s over ALL regular files currently under the two stage fill dirs (rescanned every 300 s,
             files weighted by size, files may vanish mid-run): reads blocks the stage wrote, spread over the whole filled area.
Independence: it never reads or writes iogovmon files, config, timers or state, is not gated by iogovmon, and iogovmon's gates do not see it
except as ~4 short IOs per second. It keeps running through the whole stage (also during fills) to give latency-vs-fill curves.
Safety: refuses to start unless /srv/cow is a separate mount (st_dev differs from /); refuses to create a probe file when its
filesystem is above 80% used; writes only its two files and the CSVs; lock file (second start refuses, exit 3); STOP file
/root/iogovmon-accept2/REFPROBE-STOP or SIGTERM/SIGINT = flush the CSV and exit 0 (probe files are kept for a restart); `--cleanup` deletes the two
probe dirs and the CSVs (refuses while a probe holds the lock). Modes: --run [--size-mib N --interval S --duration S --suffix X] | --cleanup
"""
import argparse, datetime as dt, fcntl, mmap, os, random, shutil, signal, stat, struct, sys, time
from pathlib import Path

WORK = Path("/root/iogovmon-accept2")
LOCK, STOP = WORK / "refprobe.lock", WORK / "REFPROBE-STOP"
TARGETS = {"root": ("/", "/var/tmp"), "cow": ("/srv/cow", "/srv/cow")}  # name -> (mount point, parent dir of the probe dir)
ALLOWED_PARENTS = {"/var/tmp", "/srv/cow"}
FILL_DIRS = {"root": "/var/tmp/iogov-fill", "cow": "/srv/cow/iogov-fill"}  # the stage fill dirs (read only here)
CAP_BYTES = 200 * 10 ** 6
BIG_MAX_USED_PCT = 60.0
FILL_RESCAN_S = 300.0  # os.walk over up to ~300k churn files: rescan every 5 min so the 10 s cadence is never delayed
MAX_USED_PCT = 80.0
BLK = 4096
RAW = time.CLOCK_MONOTONIC_RAW
stop_flag = []


def log(msg):
    print(f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}", flush=True)


def used_pct(path):
    v = os.statvfs(path)
    used, avail = (v.f_blocks - v.f_bfree) * v.f_frsize, v.f_bavail * v.f_frsize
    return 100.0 * used / (used + avail) if used + avail else 0.0


class Probe:
    def __init__(self, name, size, suffix, delete_on_exit=False):
        self.name, self.size, self.delete_on_exit = name, size, delete_on_exit
        self.mount, parent = TARGETS[name]
        self.dir = os.path.join(parent, "iogov-refprobe" + suffix)
        self.path = os.path.join(self.dir, "probe.bin")
        self.fd = None
        self.rbuf, self.wbuf = mmap.mmap(-1, BLK), mmap.mmap(-1, BLK)  # page aligned, as O_DIRECT needs

    def create(self):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        if os.path.isfile(self.path) and os.path.getsize(self.path) == self.size:  # reuse needs no free space: a restart at 85-94% must work
            log(f"{self.name}: reusing existing {self.path}")
            return
        if used_pct(self.mount) > MAX_USED_PCT:  # the guard applies to CREATING a probe file only
            raise SystemExit(f"REFUSED: {self.mount} is above {MAX_USED_PCT}% used, not creating {self.path}")
        t0 = time.time()
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_DIRECT, 0o600)
        try:
            buf = mmap.mmap(-1, 1 << 20)
            for off in range(0, self.size, 1 << 20):
                if stop_flag or STOP.exists():
                    raise SystemExit("stopped while creating the probe file")
                buf[:] = os.urandom(1 << 20)  # fresh random bytes every MiB: incompressible, not dedupable
                os.pwritev(fd, [buf], off)
            os.fsync(fd)
        finally:
            os.close(fd)
        log(f"{self.name}: created {self.path} ({self.size >> 20} MiB random, O_DIRECT) in {time.time() - t0:.1f}s")

    def open(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_DIRECT)

    def timed(self, op, off, fn):
        err, t0 = 0, time.clock_gettime_ns(RAW)
        try:
            fn()
        except OSError as exc:
            err = exc.errno or -1
        lat = (time.clock_gettime_ns(RAW) - t0) // 1000
        return (time.time_ns(), self.name, op, off, lat, err)

    def cycle(self):
        rows, nblk = [], self.size // BLK
        if self.fd is None:
            try:
                self.open()
            except OSError as exc:
                return [(time.time_ns(), self.name, "open", 0, 0, exc.errno or -1)]
        for _ in range(8):
            off = random.randrange(nblk) * BLK
            rows.append(self.timed("read-hot", off, lambda: os.preadv(self.fd, [self.rbuf], off)))
        off = random.randrange(nblk) * BLK
        self.wbuf[:] = os.urandom(BLK)

        def overwrite():
            os.pwritev(self.fd, [self.wbuf], off)
            os.fdatasync(self.fd)
        rows.append(self.timed("wr_sync", off, overwrite))
        return rows

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.delete_on_exit:
            remove_probe_dir(self.dir)


def create_big(path, size, mount):
    """One big file of random data, O_DIRECT sequential writes. Fast generator: a 64 MiB random pool, a different 1 MiB slice per chunk and a
    unique 8-byte stamp in every 4 KiB block (all blocks distinct, still incompressible). Returns True if the file is usable."""
    if os.path.isfile(path) and os.path.getsize(path) == size:  # reuse first: the 60% guard is for creating only
        log(f"big: reusing existing {path}")
        return True
    total = os.statvfs(mount)
    cap = total.f_blocks * total.f_frsize
    if used_pct(mount) + 100.0 * size / cap > BIG_MAX_USED_PCT:
        log(f"big: REFUSED, {mount} would be above {BIG_MAX_USED_PCT}% used after writing {size >> 30} GiB")
        return False
    t0, pool, buf = time.time(), os.urandom(64 << 20), mmap.mmap(-1, 1 << 20)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_DIRECT, 0o600)
    try:
        for i, off in enumerate(range(0, size, 1 << 20)):
            if stop_flag or STOP.exists():
                raise SystemExit("stopped while creating the big probe file")
            p = (i * 997 * BLK) % ((64 << 20) - (1 << 20))
            buf[:] = pool[p:p + (1 << 20)]
            for j in range(256):
                buf[j * BLK:j * BLK + 8] = struct.pack("<Q", i * 256 + j)
            os.pwritev(fd, [buf], off)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        os.unlink(path)
        raise
    os.close(fd)
    if used_pct(mount) > BIG_MAX_USED_PCT:
        os.unlink(path)
        log(f"big: {mount} above {BIG_MAX_USED_PCT}% used after the write: file removed, no read-big set")
        return False
    log(f"big: created {path} ({size >> 30} GiB random, O_DIRECT) in {time.time() - t0:.0f}s")
    return True


class BigProbe:
    """read-big: cold random reads of the big file (no hot set: the file is far larger than any cache)."""

    def __init__(self, path):
        self.path, self.fd, self.rbuf = path, None, mmap.mmap(-1, BLK)
        self.size = os.path.getsize(path)

    def cycle(self):
        if self.fd is None:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECT)
        rows = []
        for _ in range(8):
            off = random.randrange(self.size // BLK) * BLK
            err, t0 = 0, time.clock_gettime_ns(RAW)
            try:
                os.preadv(self.fd, [self.rbuf], off)
            except OSError as exc:
                err = exc.errno or -1
            rows.append((time.time_ns(), "cow", "read-big", off, (time.clock_gettime_ns(RAW) - t0) // 1000, err))
        return rows

    def close(self):
        if self.fd is not None:
            os.close(self.fd)


class FillProbe:
    """read-fill: random reads over the regular files under the stage fill dirs (rescanned every `rescan_s`); a vanished file is skipped."""

    def __init__(self, fill_dirs, rescan_s=60.0):
        self.dirs, self.rescan_s, self.files, self.cum, self.last = fill_dirs, rescan_s, [], [], 0.0
        self.rbuf = mmap.mmap(-1, BLK)

    def rescan(self):
        files, total = [], 0
        for name, d in self.dirs.items():
            if os.path.isdir(d) and not os.path.islink(d):
                for dp, _dn, fns in os.walk(d):
                    for fn in fns:
                        p = os.path.join(dp, fn)
                        try:
                            st = os.lstat(p)
                        except OSError:
                            continue
                        if stat.S_ISREG(st.st_mode) and st.st_size >= BLK:
                            total += st.st_size
                            files.append((name, p, st.st_size, total))
        self.files = files
        self.last = time.monotonic()

    def cycle(self):
        if time.monotonic() - self.last >= self.rescan_s:
            self.rescan()
        if not self.files:
            return []
        rows, total = [], self.files[-1][3]
        for _ in range(8):
            x = random.randrange(total)
            name, path, size, _c = next(f for f in self.files if f[3] > x)
            off = random.randrange(size // BLK) * BLK
            try:
                fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
            except OSError:
                continue  # vanished (or not openable): skipped
            try:
                err, t0 = 0, time.clock_gettime_ns(RAW)
                try:
                    n = os.preadv(fd, [self.rbuf], off)
                except OSError as exc:
                    n, err = -1, exc.errno or -1
                lat = (time.clock_gettime_ns(RAW) - t0) // 1000
            finally:
                os.close(fd)
            if n == 0 or err in (2, 5):  # beyond EOF after a truncate / vanished mid-read: no meaningful latency
                continue
            rows.append((time.time_ns(), name, "read-fill", off, lat, err))
        return rows

    def close(self):
        pass


def remove_probe_dir(d):
    n = os.path.normpath(d)
    if not os.path.basename(n).startswith("iogov-refprobe") or os.path.dirname(n) not in ALLOWED_PARENTS or os.path.islink(n):
        log(f"SKIPPED {d}: not an allowed probe dir")
        return False
    if os.path.exists(n):
        shutil.rmtree(n)
        log(f"removed {n}")
    return True


class Csv:
    def __init__(self, data_dir, flush_s):
        self.dir, self.flush_s, self.rows, self.last, self.capped = Path(data_dir), flush_s, [], time.monotonic(), False
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    def add(self, rows):
        self.rows += rows

    def flush(self, force=False):
        if not force and time.monotonic() - self.last < self.flush_s:
            return
        self.last = time.monotonic()
        if not self.rows:
            return
        try:  # ENOSPC / EIO on the CSV filesystem must never stop the probe: log, drop the buffered rows, keep probing
            total = sum(f.stat().st_size for f in self.dir.glob("*.csv"))
            if total > CAP_BYTES:
                if not self.capped:
                    log(f"CSV cap {CAP_BYTES} B reached: dropping further rows")
                self.capped, self.rows = True, []
                return
            by_file = {}
            for r in self.rows:
                day = dt.datetime.fromtimestamp(r[0] / 1e9, dt.timezone.utc).strftime("%Y%m%d")
                by_file.setdefault(self.dir / f"{r[1]}-{day}.csv", []).append(r)
            for path, rs in by_file.items():
                new = not path.exists()
                with open(path, "a") as fh:
                    if new:
                        fh.write("ts_utc_ns,fs,op,offset,latency_us,errno\n")
                    fh.write("".join(f"{a},{b},{c},{d},{e},{f}\n" for a, b, c, d, e, f in rs))
        except Exception as exc:  # noqa: BLE001
            print(f"CSV flush FAILED ({type(exc).__name__}: {exc}); {len(self.rows)} buffered rows dropped, probing continues", file=sys.stderr, flush=True)
        self.rows = []


def cleanup(suffix):
    lockfh = open(LOCK, "w")
    try:
        fcntl.flock(lockfh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("REFUSED: a refprobe holds the lock (stop it first: touch REFPROBE-STOP)", file=sys.stderr)
        return 3
    ok = True
    for name, (_m, parent) in TARGETS.items():
        ok &= remove_probe_dir(os.path.join(parent, "iogov-refprobe" + suffix))
    d = WORK / ("refprobe" + suffix)
    if d.is_dir() and not d.is_symlink():
        for f in d.glob("*.csv"):
            f.unlink()
        try:
            d.rmdir()
        except OSError:
            log(f"{d} not empty after removing the CSVs, left in place")
    log("cleanup done")
    return 0 if ok else 4


def run(a):
    suffix = a.suffix
    size = a.size_mib << 20
    rd, cd = os.stat("/").st_dev, os.stat("/srv/cow").st_dev
    if cd == rd or os.stat("/var/tmp").st_dev != rd or os.stat("/srv/cow").st_dev != cd:
        print("REFUSED: /srv/cow is not a separate mount from / (st_dev rule)", file=sys.stderr)
        return 2
    if STOP.exists():
        print(f"REFUSED: {STOP} exists (remove it first)", file=sys.stderr)
        return 2
    lockfh = open(LOCK, "w")
    try:
        fcntl.flock(lockfh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("REFUSED: another refprobe holds the lock", file=sys.stderr)
        return 3
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, f: stop_flag.append(s))
    probes = [Probe(n, size, suffix, a.delete_on_exit) for n in ("root", "cow")]
    extra = []  # BigProbe / FillProbe objects
    csv = Csv(WORK / ("refprobe" + suffix), a.flush)
    rc = 0
    try:
        for p in probes:
            p.create()
        big_path = os.path.join(probes[1].dir, "probe-big.bin")
        if create_big(big_path, a.big_mib << 20, "/srv/cow"):
            extra.append(BigProbe(big_path))
        extra.append(FillProbe(dict(FILL_DIRS), FILL_RESCAN_S))
        log(f"VERSION 2: sets read-hot (2 x {a.size_mib} MiB files, + wr_sync), "
            f"read-big ({'on' if any(isinstance(e, BigProbe) for e in extra) else 'OFF'}, {a.big_mib} MiB), read-fill (stage fill dirs, rescan {FILL_RESCAN_S:.0f}s); interval {a.interval}s; data {csv.dir}")
        t_end = time.monotonic() + a.duration if a.duration else None
        every = probes + extra
        nxt = [time.monotonic() + i * a.interval / len(every) for i in range(len(every))]  # the sets are staggered over one interval
        while not stop_flag and not STOP.exists():
            now = time.monotonic()
            for i, p in enumerate(every):
                if now >= nxt[i]:
                    try:
                        csv.add(p.cycle())
                    except Exception as exc:  # noqa: BLE001  one failing set must not stop the others
                        log(f"{type(p).__name__} cycle failed: {type(exc).__name__}: {exc}")
                    nxt[i] += a.interval
            csv.flush()
            if t_end and now >= t_end:
                break
            time.sleep(0.25)
        log("stopping: " + ("signal" if stop_flag else "STOP file" if STOP.exists() else "duration reached"))
    except SystemExit as exc:
        log(str(exc))
        rc = 2
    except BaseException as exc:  # noqa: BLE001
        log(f"EXCEPTION {type(exc).__name__}: {exc}")
        rc = 1
    finally:
        try:
            csv.flush(force=True)
        except Exception as exc:  # noqa: BLE001
            print(f"final CSV flush FAILED: {exc}", file=sys.stderr, flush=True)
        for p in probes + extra:
            try:
                p.close()
            except Exception as exc:  # noqa: BLE001
                print(f"close FAILED: {exc}", file=sys.stderr, flush=True)
        if a.delete_on_exit:
            remove_probe_dir(probes[1].dir)
        if STOP.exists():
            STOP.rename(STOP.with_name(f"REFPROBE-STOP.consumed-{int(time.time())}"))
    return rc


def main():
    global WORK, LOCK, STOP
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--size-mib", type=int, default=1024)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds (0 = run until stopped)")
    ap.add_argument("--flush", type=float, default=60.0)
    ap.add_argument("--big-mib", type=int, default=24 * 1024, help="size of probe-big.bin (read-big set)")
    ap.add_argument("--suffix", default="", help="rehearsal: '-rehearsal' uses separate probe dirs and data dir")
    ap.add_argument("--delete-on-exit", action="store_true", help="rehearsal: remove the probe files when the run ends")
    ap.add_argument("--workdir", default=str(WORK), help="lock, STOP file and CSV data dir live here (default: the on-host acceptance dir; must match stage.toml)")
    a = ap.parse_args()
    WORK = Path(a.workdir)
    LOCK, STOP = WORK / f"refprobe{a.suffix}.lock", WORK / f"REFPROBE-STOP{a.suffix}"  # a rehearsal has its own lock and STOP file
    if a.cleanup:
        return cleanup(a.suffix)
    if a.run:
        return run(a)
    ap.error("--run or --cleanup required")


if __name__ == "__main__":
    sys.exit(main())
