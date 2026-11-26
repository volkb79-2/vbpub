"""P-plat-19m: the acceptance kit v2 under tools/accept/. Hermetic: fixture CSVs, fake docker/systemctl on PATH, fake /proc, cgroup and DAMON sysfs roots."""
from __future__ import annotations

import contextlib
import csv
import fcntl
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "tools" / "accept"
MIB = 1024 ** 2
spec = importlib.util.spec_from_file_location("accept_eval_t", KIT / "accept-eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)
hspec = importlib.util.spec_from_file_location("accept_hotset_t", KIT / "accept-hotset.py")
hs = importlib.util.module_from_spec(hspec)
hspec.loader.exec_module(hs)

UNITS = ["ssh.service", "docker.service", "containerd.service", "systemd-journald.service", "mdt-psi-guard.service",
         "cgprofiled.service", "user.slice", "dev-interactive.slice"]
HEAD = ["utc", "elapsed", "phase", "U", "init_current", "W", "swap_used", "pswpin", "pswpout", "zswpout", "zswpwb", "dev.current", "dev.mem_max",
        "dev.swap_current"]
HEAD += [f"{u}.{c}" for u in UNITS for c in ("current", "active", "refault_anon", "refault_file")]
HEAD += ["system.psi_some_avg10", "user.psi_some_avg10", "cgprofile.psi_some_avg10"]
HEAD += ["system.psi_full_avg10", "user.psi_full_avg10", "cgprofile.psi_full_avg10"]
HEAD += [f"{t}.{c}" for t in ("dev", "gates", "bg") for c in ("max_local", "oom", "oom_kill")] + ["vmstat_oom_kill"]
HEAD += ["step", "k_target", "dev_max", "tier_max", "dummy_min", "dummy_unprotected", "mem_available", "mem_free", "root_some_avg10",
         "root_full_avg10", "containerd_restarts", "limit_source", "dev.min", "tier.min"]
PROBE_HEAD = ["utc", "step", "probe", "kind", "rc", "seconds", "detail"]
HOT_HEAD = ["utc", "step", "unit", "memcg_leaves", "sz_filter_passed_one_apply", "sz_tried", "regions_hot_bytes_info"]
STEPS = 6
DEV_MAX_START = 8 * 1024 * MIB - 1560 * MIB   # MemTotal - K0 1560M: 2.1's label arithmetic
K0 = 1560 * MIB
PROBE_GOOD = ("sshd", "journald", "dockerd", "containerd", "cgprofiled", "psi_guard")


def probes_text(steps: list[int], *, seconds: float = 0.1, ssh: float = 0.4, ssh_of: dict[int, list[float]] | None = None,
                rc_of: dict[str, str] | None = None, nrestarts_of: dict[int, int] | None = None,
                seconds_of: dict[str, list[float]] | None = None,
                seconds_at: dict[tuple[int, str], list[float]] | None = None) -> str:
    """probes.csv: 8 liveness samples and 10 ssh samples per step. `ssh_of` overrides one step's 10 samples;
    `seconds_of` overrides one probe's seconds for every step, `seconds_at` one probe's for one step."""
    out = [",".join(PROBE_HEAD)]
    for s in steps:
        for name in PROBE_GOOD:
            r = (rc_of or {}).get(name, "0")
            nr = (nrestarts_of or {}).get(s, 0)
            secs = (seconds_at or {}).get((s, name)) or (seconds_of or {}).get(name) or [seconds] * 8
            for k in range(8):   # 8 liveness samples per judged window (3.3)
                sec = secs[k] if k < len(secs) else secs[-1]
                out.append(f"2026-01-01T00:00:00Z,{s},{name},liveness,{r},{sec},nrestarts={nr}")
        samples = (ssh_of or {}).get(s, [ssh] * 10)
        for secs in samples:
            out.append(f"2026-01-01T00:00:00Z,{s},ssh,ssh,0,{secs},target=root@127.0.0.1")
    return "\n".join(out) + "\n"


def write_run(d: Path, *, steps: tuple[int, ...] = (0, 1, 2), verdicts: tuple[str, ...] = ("PASS", "PASS", "PASS"),
              causes: tuple[str, ...] = ("", "", ""), refault: int = 0, oom: int = 0, at_ceiling: bool = True, psi: float = 0.1,
              hot: dict[str, int] | None = None, mem_free_low: bool = True, u_scale: int = 1, blank: tuple[str, ...] = (),
              kit: str = "2", tested_k: int = K0) -> Path:
    """A v2 evidence directory: samples.csv (settle+judge rows per step), probes.csv, steps/N.env, hotset.csv,
    manifest.env and the snapshot. A `--live-step` judge over this fixture reproduces the steps/*.env verdicts."""
    d.mkdir(parents=True, exist_ok=True)
    hot = hot or {"docker.service": 200 * MIB, "ssh.service": 4 * MIB, "containerd.service": 40 * MIB,
                  "systemd-journald.service": 20 * MIB, "mdt-psi-guard.service": 4 * MIB, "cgprofiled.service": 20 * MIB}
    with (d / "samples.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, HEAD)
        w.writeheader()
        for i, s in enumerate(steps):
            dev_max = DEV_MAX_START + s * 128 * MIB
            for t in range(101):
                row = {k: "" for k in HEAD}
                row.update(utc="00:00:00", elapsed=str(t), phase="settle" if t < 20 else "judge", step=s, k_target=K0 - s * 128 * MIB,
                           dev_max=dev_max, tier_max=dev_max, U=str(max(t, 1) * MIB * u_scale), init_current=str(50 * MIB), W=str(30 * MIB),
                           **{"dev.mem_max": dev_max, "dev.current": dev_max - (8 * MIB if at_ceiling else 400 * MIB),
                              "mem_free": str(8 * MIB if mem_free_low else 120 * MIB), "mem_available": str(1024 * MIB),
                              "dev.max_local": str(t), "vmstat_oom_kill": str(oom if t == 100 else 0),
                              "dev.oom": "0", "dev.oom_kill": str(oom if t == 100 else 0),
                              "gates.oom": "0", "gates.oom_kill": "0", "bg.oom": "0", "bg.oom_kill": "0",
                              "system.psi_some_avg10": str(psi), "user.psi_some_avg10": "0.1", "cgprofile.psi_some_avg10": "0.1",
                              "system.psi_full_avg10": "0.1", "user.psi_full_avg10": "0.1", "cgprofile.psi_full_avg10": "0.1",
                              "root_some_avg10": "0.9", "root_full_avg10": "0.4", "containerd_restarts": "0",
                              **{f"{u}.active": str(v) for u, v in hot.items()},
                              **{f"{u}.refault_file": str(refault * t // 100) for u in hot},
                              **{c: "" for c in blank}})
                w.writerow(row)
    (d / "probes.csv").write_text(probes_text(list(steps)))
    (d / "hotset.csv").write_text(",".join(HOT_HEAD) + "\n" + "".join(
        f"2026-01-01T00:00:00Z,{s},{u},1,{v},0,0\n" for s in steps for u, v in hot.items()))
    envlines = [f"kit_version={kit}", "mem_total=8589934592", "swap_total=17179869184", f"k_tested={tested_k}", f"squeeze_step={STEPS}",
                f"step_bytes={128 * MIB}", "squeeze_dwell=300", "probe_image=tester-unified:local", "damon_stat_enabled=N"]
    passed = [s for s, v in zip(steps, verdicts) if v == "PASS"]
    failed = [s for s, v in zip(steps, verdicts) if v == "FAIL"]
    envlines += [f"k_last_pass={tested_k - max(passed) * 128 * MIB}" if passed else "k_last_pass=",
                 f"k_first_fail={tested_k - min(failed) * 128 * MIB}" if failed else "k_first_fail=",
                 f"squeeze_last_pass_step={max(passed) if passed else ''}", f"squeeze_first_fail_step={min(failed) if failed else ''}"]
    (d / "manifest.env").write_text("\n".join(envlines) + "\n")
    (d / "host-setup.env.snapshot").write_text('HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_MIN="docker.service=231M systemd-journald.service=24M"\n'
                                               "DEV_HOST_MEMORY_MARGIN=512M\n")
    (d / "steps").mkdir(exist_ok=True)
    for s, v, c in zip(steps, verdicts, causes):
        (d / "steps" / f"{s}.env").write_text(f"verdict={v}\ncause={c}\ndetail=fixture step {s}\n")
    return d


def run_eval(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ev.main(list(argv))
    return rc, out.getvalue() + err.getvalue()


def run_hotset(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = hs.main(list(argv))
    return rc, out.getvalue() + err.getvalue()


# ---------- the fakes the shell tests put on PATH ----------
FAKE_DOCKER = r'''printf '%s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  info) echo "${FAKE_DOCKER_ROOT:-/tmp}" ;;
  run)
    # M3: the evidence filesystem stops accepting appends BEFORE the ladder's first cgroup write. The fake replaces
    # the state FILE with a directory, so `printf >> $STATE` fails (EISDIR) exactly as a full or read-only evidence
    # filesystem would; the header line is already there, so this breaks the step's MAX append.
    [ -n "${FAKE_STATE_IS_DIR:-}" ] && { rm -f "$FAKE_STATE_IS_DIR"; mkdir -p "$FAKE_STATE_IS_DIR"; }
    name=; parent=; memmax=; swmax=
    while [ $# -gt 0 ]; do case "$1" in --name) name=$2 ;; --cgroup-parent) parent=$2 ;; --memory) memmax=$2 ;; --memory-swap) swmax=$2 ;; esac; shift; done
    [ "${FAKE_FAIL_RUN:-}" = "$name" ] && exit 1
    id="id-$name"; d="$MDT_ACCEPT_CGROUP/dev.slice/$parent/docker-$id.scope"; mkdir -p "$d"
    echo "${FAKE_MEMMAX_DUMMY:-$memmax}" > "$d/memory.max"
    echo "${FAKE_SWAPMAX_DUMMY:-$(( swmax - memmax ))}" > "$d/memory.swap.max"   # docker subtracts the RAM cap from --memory-swap
    echo 0 > "$d/memory.min"
    printf 'anon %s\nfile 0\nkernel 0\nshmem 0\nfile_mapped 0\n' "${FAKE_ANON:-0}" > "$d/memory.stat"
    echo "${FAKE_ANON:-0}" > "$d/memory.current"; printf 'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n' > "$d/memory.events"
    echo "$id" ;;
  kill)  # -s USR1 <name>: the dummy's anon grows by one STEP (and an optional foreign write lands mid-run)
    # FAKE_KILL_SLEEP: the host stalls on the signal for that long, so the RUN's elapsed time grows well past the
    # probes' own durations before the next step's probes (H2's test relies on that, never on host load)
    sleep "${FAKE_KILL_SLEEP:-0}"
    [ -n "${FAKE_DRIFT_FILE:-}" ] && echo "${FAKE_DRIFT_VALUE:-}" > "$FAKE_DRIFT_FILE"
    d=$(ls -d "$MDT_ACCEPT_CGROUP/dev.slice/"*"/docker-id-$4.scope" 2>/dev/null | head -n 1)
    [ -n "$d" ] || exit 1
    anon=$(awk '$1=="anon"{print $2}' "$d/memory.stat"); anon=$((anon + ${FAKE_STEP_BYTES:-134217728}))
    printf 'anon %s\nfile 0\nkernel 0\nshmem 0\nfile_mapped 0\n' "$anon" > "$d/memory.stat"; echo "$anon" > "$d/memory.current"
    # L5: the host falls under the 128M floor while the kit WAITS for the dummy to grow (the <= 60 s grow window)
    [ -n "${FAKE_LOW_MEM_AFTER_SIGNAL:-}" ] && printf 'MemTotal: 8388608 kB\nMemFree: 1048576 kB\nMemAvailable: 65536 kB\nSwapTotal: 16777216 kB\nSwapFree: 16000000 kB\n' > "$MDT_ACCEPT_PROC/meminfo"
    ;;
  rm) [ "${2:-}" = -f ] && [ -n "${MDT_ACCEPT_CGROUP:-}" ] && rm -rf "$MDT_ACCEPT_CGROUP/dev.slice/"*"/docker-id-${3:-}.scope"
      sleep "${FAKE_RM_SLEEP:-0}" ;;   # M2: a squeezed dockerd stalls `rm`; the kit must bound it
  exec) exit 0 ;;
esac
exit 0
'''
FAKE_SYSTEMCTL = r'''printf 'systemctl %s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  show)
    if [ "$3" = MainPID ] && [ "$5" = mdt-psi-guard.service ]; then
      sleep "${FAKE_PSI_SHOW_SLEEP:-0}"   # a slow read makes the probe's OWN duration visible in probes.csv
      # the daemon's utime+stime advances by one tick on every read: a 1 Hz loop inside a tick-driven window
      n=0; [ -f "$FAKE_TICKS" ] && n=$(cat "$FAKE_TICKS" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_TICKS"
      pid=${FAKE_PSI_PID:-4242}
      # the REAL psi_guard probe branch reads /proc/<pid>/stat ($14+$15), never the seam: keep that file advancing
      [ -n "${MDT_ACCEPT_PROC:-}" ] && printf '4242 (mdt-psi-guard) S 1 4242 0 0 0 0 0 0 0 0 0 %s 0 0\n' "$n" > "$MDT_ACCEPT_PROC/$pid/stat" 2>/dev/null
      echo "$pid"
    else
      cat "$FAKE_SHOW/$3-$5" 2>/dev/null
    fi ;;
  daemon-reload) [ -n "${FAKE_RELOAD_WRITE:-}" ] && { ( sleep "${FAKE_RELOAD_DELAY:-0}"; printf %s "$FAKE_RELOAD_WRITE" > "$FAKE_MAX_FILE" ) >/dev/null 2>&1 & } ;;
  is-active) echo "${FAKE_ACTIVE:-active}" ;;
esac
exit 0
'''''
FAKE_SSH = r'''cat "$FAKE_BOOT_ID" 2>/dev/null    # `ssh -o BatchMode=yes <target> cat /proc/.../boot_id` in the preflight
exit 0
'''
FAKE_CGPROFILECTL = r'''printf 'cgprofilectl %s\n' "$*" >> "$FAKE_LOG"
[ "$1" = --json ] && echo "{\"version\": \"test\", \"sessions_live\": ${FAKE_SESSIONS_LIVE:-0}}"
exit 0
'''


def fake_bins(d: Path) -> Path:
    """The three fakes the shell tests need on PATH. `ssh` must answer the preflight's boot_id round trip."""
    d.mkdir(parents=True, exist_ok=True)
    for name, body in (("docker", FAKE_DOCKER), ("systemctl", FAKE_SYSTEMCTL), ("ssh", FAKE_SSH),
                       ("cgprofilectl", FAKE_CGPROFILECTL)):
        (d / name).write_text("#!/bin/sh\n" + body)
        (d / name).chmod(0o755)
    return d


class ShellKitBase(unittest.TestCase):
    """The fake host: a cgroupfs tree, a fake /proc, fake docker/systemctl/ssh, a fixture hot-set JSON and a fake judge.

    Every run is tick-driven (`MDT_ACCEPT_INTERVAL=0`), so no wall clock enters a verdict and the suite stays fast.
    """
    DEV_MAX_START = 8 * 1024 * MIB - 1560 * MIB   # MemTotal - MDT_EXCLUDED_RAM 0 - K0 1560M: 2.1's label arithmetic
    TIER_MIN = 64 * MIB

    def setUp(self) -> None:
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        bindir = fake_bins(self.tmp / "bin")
        self.show = self.tmp / "show"
        self.show.mkdir()
        self.log = self.tmp / "fake.log"
        self.log.write_text("")
        self.cg, self.proc = self.tmp / "cg", self.tmp / "proc"
        (self.cg / "dev.slice" / "dev-gates.slice").mkdir(parents=True)
        (self.cg / "dev.slice" / "dev-background.slice").mkdir()
        (self.cg / "system.slice" / "cgprofile.slice").mkdir(parents=True)
        (self.cg / "cgprofile.slice").mkdir()
        (self.cg / "user.slice").mkdir()
        for rel, val in (("dev.slice/memory.max", self.DEV_MAX_START), ("dev.slice/dev-gates.slice/memory.max", self.DEV_MAX_START),
                         ("dev.slice/dev-background.slice/memory.max", self.DEV_MAX_START),
                         ("dev.slice/memory.min", 512 * MIB), ("dev.slice/dev-gates.slice/memory.min", self.TIER_MIN),
                         ("dev.slice/memory.current", 6 * 1024 * MIB), ("dev.slice/dev-gates.slice/memory.current", 1024 * MIB),
                         ("system.slice/memory.min", 452 * MIB), ("cgprofile.slice/memory.min", 128 * MIB), ("user.slice/memory.min", 64 * MIB)):
            (self.cg / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.cg / rel).write_text(f"{val}\n")
        (self.cg / "dev.slice" / "memory.stat").write_text("anon 0\nkernel 0\nshmem 0\nfile_mapped 0\n")
        (self.cg / "dev.slice" / "memory.events.local").write_text("low 0\nhigh 0\nmax 7\noom 0\noom_kill 0\n")
        (self.cg / "dev.slice" / "memory.events").write_text("low 0\nhigh 0\nmax 999\noom 0\noom_kill 0\n")
        (self.cg / "dev.slice" / "memory.swap.current").write_text("0\n")
        for rel in ("memory.pressure", "dev.slice/memory.pressure", "dev.slice/dev-gates.slice/memory.pressure",
                    "dev.slice/dev-background.slice/memory.pressure", "system.slice/memory.pressure", "cgprofile.slice/memory.pressure",
                    "user.slice/memory.pressure"):
            (self.cg / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.cg / rel).write_text("some avg10=0.10 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.05 avg60=0.00 avg300=0.00 total=0\n")
        self.proc.mkdir()
        (self.proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemFree: 1048576 kB\nMemAvailable: 3145728 kB\nSwapTotal: 16777216 kB\n"
                                           "SwapFree: 16000000 kB\n")
        (self.proc / "vmstat").write_text("nr_free_pages 1\npswpin 4\npswpout 8\nzswpout 9\nzswpwb 2\noom_kill 3\n")
        (self.proc / "4242").mkdir()
        (self.proc / "4242" / "stat").write_text("4242 (mdt-psi-guard) S 1 4242 " + "0 " * 10 + "0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n")
        (self.proc / "zoneinfo").write_text("Node 0, zone DMA\n        high 30\n")
        (self.tmp / "damon_stat-N").write_text("N\n")
        self.boot = self.tmp / "boot_id"
        self.boot.write_text("11111111-2222-3333-4444-555555555555\n")
        for s, val in (("dev.slice", self.DEV_MAX_START), ("dev-gates.slice", self.DEV_MAX_START),
                       ("system.slice", 452 * MIB), ("cgprofile.slice", 128 * MIB), ("user.slice", 64 * MIB)):
            (self.show / f"MemoryMin-{s}").write_text(f"{val}\n")
        (self.show / "MemoryMin-cgprofile.slice").write_text(f"{128 * MIB}\n")
        self.henv = self.tmp / "host-setup.env"
        self.henv.write_text("HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_KEEP=1560M\nMDT_EXCLUDED_RAM=0\nSECRET_TOKEN=x\n")
        self.ev = self.tmp / "ev"
        self.fixture = self.tmp / "hotset.json"
        self.fixture.write_text(json.dumps({"units": {u: {"memcg_leaves": 1, "per_apply": 20 * MIB, "sz_tried": 40 * MIB,
                                                         "regions_hot_bytes": 0, "current": 200 * MIB}
                                                  for u in ("docker.service", "ssh.service")}}))
        self.env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_LOG=str(self.log), FAKE_SHOW=str(self.show),
                        FAKE_DOCKER_ROOT=str(self.tmp), FAKE_BOOT_ID=str(self.boot), FAKE_ANON="0",
                        FAKE_DEEPEST=str(self.DEV_MAX_START + 6 * 128 * MIB), FAKE_STEP_BYTES=str(128 * MIB),
                        MDT_ACCEPT_CGROUP=str(self.cg), MDT_ACCEPT_PROC=str(self.proc), MDT_ACCEPT_HOST_ENV=str(self.henv),
                        MDT_ACCEPT_BOOT_ID=str(self.boot), MDT_ACCEPT_INTERVAL="0", MDT_ACCEPT_PAGESIZE="4096",
                        MDT_ACCEPT_LOCK=str(self.tmp / "lock"), MDT_ACCEPT_MIN_DISK="1", MDT_ACCEPT_MIN_EVID_DISK="1",
                        MDT_ACCEPT_SSH_TARGET="root@127.0.0.1",
                        MDT_ACCEPT_HOTSET_JSON=str(self.fixture), MDT_ACCEPT_HOTSET_EVERY="1",
                        MDT_ACCEPT_DAMON_STAT=str(self.tmp / "damon_stat-N"),
                        # 2 ticks per step (one settling, one judged): every tick costs ~40 fake-process spawns plus one
                        # hot-set sample, and no shell test asserts a per-tick count beyond that. The smoke lane is
                        # budget-bound (see run-gate.toml), so the fake host runs the shortest ladder that still covers
                        # the seams (settling row, judged row, probes, hot set, guards, drift readback).
                        MDT_ACCEPT_DWELL_TICKS="2", MDT_ACCEPT_SETTLE_TICKS="1", MDT_ACCEPT_MAX_TICKS="12",
                        MDT_ACCEPT_UNITS="docker.service=system.slice/docker.service ssh.service=system.slice/docker.service",
                        MDT_ACCEPT_PROBE_CMD_SSHD="true", MDT_ACCEPT_PROBE_CMD_JOURNALD="true",
                        MDT_ACCEPT_PROBE_CMD_DOCKERD="true", MDT_ACCEPT_PROBE_CMD_CONTAINERD="true",
                        MDT_ACCEPT_PROBE_CMD_CGPROFILED="true", MDT_ACCEPT_PROBE_CMD_PSI_GUARD="true",
                        MDT_ACCEPT_PROBE_CMD_SSH="true",
                        MDT_ACCEPT_SQUEEZE_STATE=str(self.ev / "squeeze-state.env"),
                        FAKE_TICKS=str(self.tmp / "psi-ticks"))

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def run_kit(self, *args: str, ticks: str = "12", **extra: str) -> subprocess.CompletedProcess:
        """`extra` overrides the fake host's env; a `None` value REMOVES the key from it (so a seam like
        MDT_ACCEPT_PROBE_CMD_PSI_GUARD can be turned off and accept-run.sh's own branch runs)."""
        env = dict(self.env, MDT_ACCEPT_MAX_TICKS=ticks)
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        return subprocess.run([str(KIT / "accept-run.sh"), "--evidence", str(self.ev), "--warmup", "0", *args],
                              env=env, capture_output=True, text=True, timeout=120)

    def judge_env(self, verdict: str = "PASS", cause: str = "") -> dict[str, str]:
        """A fake `accept-eval.py --live-step`: the ladder tests drive PASS/FAIL/INCONCLUSIVE without the real judge."""
        j = self.tmp / "judge.sh"
        j.write_text(f"#!/bin/sh\nmkdir -p \"$2\"/steps; printf 'verdict={verdict}\\ncause={cause}\\ndetail=fake judge\\n' > \"$2\"/steps/$4.env\n")
        j.chmod(0o755)
        return {"MDT_ACCEPT_JUDGE": str(j)}


def fake_one(name: str, body: str, d: Path) -> None:
    """One fake executable in `d` with `#!/bin/sh` and the given body."""
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("#!/bin/sh\n" + body)
    (d / name).chmod(0o755)
