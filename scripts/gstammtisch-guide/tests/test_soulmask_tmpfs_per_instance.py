"""Per-instance tmpfs of soulmask_tmpfs-{setup,teardown}.sh, soulmask_logs-prune.sh, toggle (SMT-01).

These tests run the REAL shell scripts against a temporary etc, volumes root, state file and ramdisk
directory. Nothing touches a real mount: ``mount``, ``umount``, ``mountpoint``, ``findmnt``,
``systemctl``, ``docker``, ``df`` and (for owner/mode only) ``stat`` are PATH shims backed by a fake
mount table (``mounts.tsv``: target, fstype, source, options). The path-containment checks use REAL
directories and REAL symlinks (the scripts call the real ``realpath``). Every other tool (cp, du, find,
awk, mkdir, ...) is the real one, operating inside pytest's tmp_path.

The oracle is the behavioural contract in HANDOFF-SMT-01 plus review SMT-01: each ``TMPFS=1`` instance
gets its OWN tmpfs on each per-instance path, owner/mode derived from the on-disk directory, a symlink
can never redirect the mount, the size stays bounded by the prune script, and a per-instance problem
never blocks the shared tmpfs.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
SBIN = PROJECT / "files" / "usr" / "local" / "sbin"
SETUP = SBIN / "soulmask_tmpfs-setup.sh"
TEARDOWN = SBIN / "soulmask_tmpfs-teardown.sh"
TOGGLE = SBIN / "soulmask_tmpfs-toggle.sh"
PRUNE = SBIN / "soulmask_logs-prune.sh"
LIB = SBIN / "soulmask-instance-lib.sh"
ETC = PROJECT / "files" / "etc"
SHIPPED_CONF = ETC / "gstammtisch" / "soulmask_tmpfs-paths.conf"
UNITS = ETC / "systemd" / "system"
SHIPPED_UNIT = UNITS / "soulmask_tmpfs.service"
PRUNE_UNIT = UNITS / "soulmask-logs-prune.service"
POLICY_ENV = ETC / "gstammtisch" / "soulmask_tmpfs.env"
PRUNE_TIMER = UNITS / "soulmask-logs-prune.timer"

UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"  # ROLE=main
UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"
LOGS = "WS/Saved/Logs"

SHIMS = {
    "mountpoint": r"""#!/usr/bin/env bash
[ "${1:-}" = "-q" ] && shift
awk -F'\t' -v t="$1" '$1==t{f=1} END{exit !f}' "$FAKE/mounts.tsv"
""",
    "findmnt": r"""#!/usr/bin/env bash
field=""; target=""; ftype=""
while [ $# -gt 0 ]; do
  case "$1" in
    -no) field="$2"; shift 2 ;;
    -nl|-n|-rn|-r) shift ;;
    -t) ftype="$2"; shift 2 ;;
    -o|--source) shift 2 ;;
    *) target="$1"; shift ;;
  esac
done
if [ -z "$target" ] && [ -n "$ftype" ]; then   # list mode: findmnt -rn -t tmpfs -o TARGET,SOURCE
  awk -F'\t' -v t="$ftype" '$2==t{print $1" "$3}' "$FAKE/mounts.tsv"
  exit 0
fi
line=$(awk -F'\t' -v t="$target" '$1==t' "$FAKE/mounts.tsv" | tail -n1)
[ -n "$line" ] || exit 1
IFS=$'\t' read -r tg fs src opts <<< "$line"
case "$field" in
  FSTYPE) echo "$fs" ;;
  SOURCE) echo "$src" ;;
  OPTIONS) echo "$opts" ;;
  *) echo "$tg $src $fs $opts" ;;
esac
""",
    # Models the kernel: a tmpfs mount reports its options normalised (size in KiB) and with rw/relatime.
    "mount": r"""#!/usr/bin/env bash
echo "mount $*" >> "$FAKE/calls.log"
# what the state file recorded BEFORE this mount (proves it is written after every mount)
{ echo "--"; grep '^PI_TARGET=' "$SOULMASK_TMPFS_STATE_FILE" 2>/dev/null || true; } >> "$FAKE/state-snapshots.log"
fstype=none; opts=""; pos=()
while [ $# -gt 0 ]; do
  case "$1" in
    -t) fstype="$2"; shift 2 ;;
    -o) opts="$2"; shift 2 ;;
    --bind) fstype=bind; shift ;;
    *) pos+=("$1"); shift ;;
  esac
done
case "$opts" in remount,*) exit 0 ;; esac
if [ -e "$FAKE/reject-noswap" ] && [[ ",$opts," == *,noswap,* ]]; then
  echo "mount: ${pos[1]}: unknown filesystem option 'noswap'" >&2
  exit 32
fi
if [ -e "$FAKE/fail-on" ] && grep -qxF "${pos[1]}" "$FAKE/fail-on"; then
  echo "mount: ${pos[1]}: wrong fs type" >&2
  exit 32
fi
stored="$opts"
if [ "$fstype" = tmpfs ]; then
  IFS=, read -ra toks <<< "$opts"; out=(rw)
  for tk in "${toks[@]}"; do
    case "$tk" in
      size=*)
        v="${tk#size=}"; n="${v%[kKmMgG]}"; u="${v:${#n}}"
        case "$u" in k|K) kib="$n" ;; m|M) kib=$((n*1024)) ;; g|G) kib=$((n*1048576)) ;; *) kib=$(( (n+4095)/4096*4 )) ;; esac
        out+=("size=${kib}k") ;;
      *) out+=("$tk") ;;
    esac
  done
  out+=(relatime)
  stored=$(IFS=,; echo "${out[*]}")
fi
printf '%s\t%s\t%s\t%s\n' "${pos[1]}" "$fstype" "${pos[0]}" "$stored" >> "$FAKE/mounts.tsv"
# TOCTOU hook: after the first mount, swap a path for a symlink
if [ -e "$FAKE/swap-after-first" ]; then
  read -r victim dest < "$FAKE/swap-after-first"
  rm -f "$FAKE/swap-after-first"
  rm -rf "$victim"
  ln -s "$dest" "$victim"
fi
""",
    "umount": r"""#!/usr/bin/env bash
echo "umount $*" >> "$FAKE/calls.log"
t="$1"
if [ -e "$FAKE/busy" ] && grep -qxF "$t" "$FAKE/busy"; then
  echo "umount: $t: target is busy." >&2
  exit 32
fi
if awk -F'\t' -v t="$t" '$1==t{f=1} END{exit !f}' "$FAKE/mounts.tsv"; then
  awk -F'\t' -v t="$t" '$1!=t' "$FAKE/mounts.tsv" > "$FAKE/mounts.new"
  mv "$FAKE/mounts.new" "$FAKE/mounts.tsv"
  exit 0
fi
echo "umount: $t: not mounted." >&2
exit 32
""",
    "mv": r"""#!/usr/bin/env bash
echo "mv $*" >> "$FAKE/mv.log"
PATH="${PATH#"$SHIMDIR":}" exec mv "$@"
""",
    "systemctl": r"""#!/usr/bin/env bash
if [ "${1:-}" = "is-active" ] && [ -e "$FAKE/docker-inactive" ]; then exit 3; fi
exit 0
""",
    # docker ps --filter name=^<uuid>$ --format {{.Names}} ; a name listed in $FAKE/running is "running"
    "docker": r"""#!/usr/bin/env bash
echo "docker $*" >> "$FAKE/docker.log"
if [ -e "$FAKE/docker-down" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi
if [ "${1:-}" = "ps" ]; then
  pat=""
  for a in "$@"; do case "$a" in name=*) pat="${a#name=}" ;; esac; done
  # docker's name filter is an UNANCHORED regex: without ^...$ a substring matches
  if [ -n "$pat" ]; then grep -E -- "$pat" "$FAKE/running" 2>/dev/null || true
  else cat "$FAKE/running" 2>/dev/null || true; fi
fi
exit 0
""",
    # usage = real du of the target, size = $FAKE/df.size
    "df": r"""#!/usr/bin/env bash
case " $* " in
  *" --output=used,size "*)
    for last; do :; done
    size=$(cat "$FAKE/df.size" 2>/dev/null) || exit 1
    used=$(du -sb "$last" | cut -f1)
    echo "Used 1B-blocks"; echo "$used $size" ;;
  *) PATH="${PATH#"$SHIMDIR":}" exec df "$@" ;;
esac
""",
    "stat": r"""#!/usr/bin/env bash
if [ "${1:-}" = "-c" ] && [ "${2:-}" = "%u %g %a" ] && [ -f "$FAKE/stat.map" ]; then
  while read -r p u g m; do
    if [ "$p" = "$3" ]; then echo "$u $g $m"; exit 0; fi
  done < "$FAKE/stat.map"
fi
PATH="${PATH#"$SHIMDIR":}" exec stat "$@"
""",
}


class Rig:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.fake = root / "fake"
        self.shims = root / "shims"
        self.etc = root / "etc"
        self.volumes = root / "volumes"
        self.state = root / "run" / "soulmask_tmpfs.state"
        self.ramdisk = root / "ramdisk"
        for d in (self.fake, self.shims, self.etc / "instances.d", self.volumes, self.state.parent):
            d.mkdir(parents=True, exist_ok=True)
        (self.fake / "mounts.tsv").write_text("")
        (self.fake / "df.size").write_text("1000000000")
        for name, body in SHIMS.items():
            p = self.shims / name
            p.write_text(body)
            p.chmod(0o755)
        self.add_instance(UUID_A, role="main")
        self.add_instance(UUID_B)

    # -- fixtures ---------------------------------------------------------
    def add_instance(self, uuid: str, role: str = "client", logs: bool = True) -> Path:
        (self.etc / "instances.d" / f"{uuid}.env").write_text(f"TMPFS=1\nROLE={role}\n")
        vol = self.volumes / uuid
        vol.mkdir(parents=True, exist_ok=True)
        if role == "main":
            (vol / "Engine").mkdir(exist_ok=True)
        if logs:
            (vol / LOGS).mkdir(parents=True, exist_ok=True)
        return vol

    def conf(self, text: str) -> None:
        (self.etc / "soulmask_tmpfs-paths.conf").write_text(text)

    def target(self, uuid: str, rel: str = LOGS) -> str:
        return str(self.volumes / uuid / rel)

    def stat_map(self, *rows: tuple[str, int, int, str]) -> None:
        (self.fake / "stat.map").write_text("".join(f"{p} {u} {g} {m}\n" for p, u, g, m in rows))

    def mounts(self, *rows: tuple[str, str, str, str]) -> None:
        with (self.fake / "mounts.tsv").open("a") as f:
            f.write("".join("\t".join(r) + "\n" for r in rows))

    def own_mount(
        self, uuid: str, uid: int = 988, gid: int = 988, mode: str = "755", size_kib: int = 65536,
        noswap: bool = False, extra: str = "nosuid,nodev,noexec", in_state: bool = True,
    ) -> str:
        """Pre-existing mount of ours at uuid's logs dir, with the options the kernel would show."""
        t = self.target(uuid)
        opts = f"rw,{extra + ',' if extra else ''}relatime,size={size_kib}k,mode={mode},uid={uid},gid={gid}"
        if noswap:
            opts += ",noswap"
        self.mounts((t, "tmpfs", f"soulmask-pi-{uuid[:8]}", opts))
        if in_state:
            with self.state.open("a") as f:
                f.write(f"PI_TARGET={t}\nPI_DERIVED={t} {uid} {gid} {mode} {uuid}\n")
        return t

    def mount_table(self) -> list[list[str]]:
        return [ln.split("\t") for ln in (self.fake / "mounts.tsv").read_text().splitlines() if ln]

    def calls(self) -> list[str]:
        f = self.fake / "calls.log"
        return f.read_text().splitlines() if f.exists() else []

    def running(self, *names: str) -> None:
        (self.fake / "running").write_text("".join(n + "\n" for n in names))

    # -- execution --------------------------------------------------------
    def env(self, **extra: str) -> dict[str, str]:
        e = {
            "PATH": f"{self.shims}:{os.environ['PATH']}",
            "HOME": str(self.root),
            "FAKE": str(self.fake),
            "SHIMDIR": str(self.shims),
            "LIB": str(LIB),
            "GSTAMMTISCH_ETC": str(self.etc),
            "SOULMASK_VOLUMES_ROOT": str(self.volumes),
            "SOULMASK_TMPFS_STATE_FILE": str(self.state),
            "SOULMASK_TMPFS_RAMDISK": str(self.ramdisk),
            "SOULMASK_TMPFS_PER_INSTANCE_SIZE": "64M",
            "SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS": "1",
            "SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT": "75",
        }
        e.update(extra)
        return e

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), *args],
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def run_without(self, script: Path, drop: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = self.env(**extra)
        del env[drop]
        return subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, timeout=60, check=False
        )


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


SHARED_AND_PI = f"""
[incompressible]
[compressible]
Engine
[per-instance-compressible]
{LOGS}
"""


def pi_mount_lines(out: str) -> list[str]:
    """Per-instance mount actions only (the shared ramdisk mount carries no soulmask-pi- tag)."""
    return [ln for ln in out.splitlines() if "mount -t tmpfs" in ln and "soulmask-pi-" in ln]


def shared_bind_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "mount --bind" in ln]


def text(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


# --- distinct per-instance mounts -----------------------------------------------------------------


def test_two_instances_get_two_distinct_dry_run_mounts_and_nothing_shared(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, text(r)
    lines = pi_mount_lines(r.stdout)
    assert len(lines) == 2, r.stdout
    for uuid in (UUID_A, UUID_B):
        mine = [ln for ln in lines if ln.rstrip().endswith(rig.target(uuid))]
        assert len(mine) == 1, (uuid, lines)
        assert f"soulmask-pi-{uuid[:8]}" in mine[0]
    # the per-instance path is never part of the shared copy/bind list
    binds = [ln for ln in r.stdout.splitlines() if "DRY-RUN:" in ln and ("--bind" in ln or "cp -a" in ln)]
    assert binds and not any(LOGS in ln for ln in binds), binds
    assert not (rig.ramdisk / LOGS).exists()
    # dry run: no real action at all, and the prune pass is announced, not run
    assert rig.calls() == [] and not rig.state.exists()
    assert "DRY-RUN: " in r.stdout and "soulmask_logs-prune.sh" in r.stdout
    assert "tmpfiles" not in r.stdout.lower()


def test_dry_run_flag_behaves_like_env(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, "--dry-run")
    assert r.returncode == 0 and len(pi_mount_lines(r.stdout)) == 2
    assert rig.calls() == []


# --- derived options ------------------------------------------------------------------------------


def test_mount_options_derive_from_the_on_disk_directory(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.stat_map((rig.target(UUID_A), 1234, 4321, "750"), (rig.target(UUID_B), 988, 988, "755"))
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, text(r)
    by_target = {ln.split()[-1]: ln for ln in pi_mount_lines(r.stdout)}
    a, b = by_target[rig.target(UUID_A)], by_target[rig.target(UUID_B)]
    assert "uid=1234,gid=4321,mode=750" in a
    assert "uid=988,gid=988,mode=755" in b
    for ln in (a, b):
        assert "size=64M" in ln
        assert all(o in ln for o in ("nosuid", "nodev", "noexec"))
        assert "noswap" not in ln  # compressible: zswap-eligible


def test_noswap_section_mounts_with_noswap(rig: Rig) -> None:
    rig.conf(f"[per-instance-noswap]\n{LOGS}\n")
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, text(r)
    lines = pi_mount_lines(r.stdout)
    assert len(lines) == 2 and all("noswap" in ln for ln in lines)


# --- config validation: loud, and never blocks the shared part -----------------------------------


@pytest.mark.parametrize(
    "var",
    [
        "SOULMASK_TMPFS_PER_INSTANCE_SIZE",
        "SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS",
        "SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT",
    ],
)
def test_per_instance_policy_has_no_script_default(rig: Rig, var: str) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run_without(SETUP, var, DRY_RUN="1")
    assert r.returncode != 0
    assert var in text(r)
    assert pi_mount_lines(r.stdout) == []
    # R-07: the per-instance problem did not stop the shared tmpfs
    assert len(shared_bind_lines(r.stdout)) == 1


@pytest.mark.parametrize(
    ("var", "bad"),
    [
        ("SOULMASK_TMPFS_PER_INSTANCE_SIZE", "lots"),
        ("SOULMASK_TMPFS_PER_INSTANCE_SIZE", "0"),       # tmpfs: size=0 means UNLIMITED
        ("SOULMASK_TMPFS_PER_INSTANCE_SIZE", "0M"),
        ("SOULMASK_TMPFS_PER_INSTANCE_SIZE", "50%"),     # relative to RAM
        ("SOULMASK_TMPFS_PER_INSTANCE_SIZE", "1.5G"),
        ("SOULMASK_TMPFS_PER_INSTANCE_SIZE", "-4M"),
        ("SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS", "-1"),
        ("SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS", "two"),
        ("SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT", "0"),
        ("SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT", "100"),
        ("SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT", "abc"),
    ],
)
def test_malformed_policy_values_are_refused(rig: Rig, var: str, bad: str) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, DRY_RUN="1", **{var: bad})
    assert r.returncode != 0 and var in text(r)
    assert pi_mount_lines(r.stdout) == []


@pytest.mark.parametrize("good", ["1", "4096", "64k", "256M", "1g"])
def test_valid_sizes_are_accepted(rig: Rig, good: str) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, DRY_RUN="1", SOULMASK_TMPFS_PER_INSTANCE_SIZE=good)
    assert r.returncode == 0, text(r)
    assert len(pi_mount_lines(r.stdout)) == 2


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", "has space", "pct%x", "glob*", "a/b?c"])
def test_unsafe_per_instance_path_is_refused(rig: Rig, bad: str) -> None:
    rig.conf(f"[compressible]\nEngine\n[per-instance-compressible]\n{bad}\n")
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode != 0, r.stdout
    assert pi_mount_lines(r.stdout) == []
    assert len(shared_bind_lines(r.stdout)) == 1  # shared part still done


@pytest.mark.parametrize(
    "conf",
    [
        "[per-instance-compressible]\nWS/Saved\nWS/Saved/Logs\n",                       # nested
        "[per-instance-compressible]\nWS/Saved/Logs\n[per-instance-noswap]\nWS/Saved/Logs\n",  # repeated
        "[compressible]\nEngine\n[per-instance-compressible]\nEngine/Logs\n",            # inside a shared path
        "[compressible]\nWS/Saved/Logs/x\n[per-instance-compressible]\nWS/Saved/Logs\n",  # contains a shared path
    ],
)
def test_nested_repeated_or_shared_overlapping_paths_are_refused(rig: Rig, conf: str) -> None:
    rig.conf(conf)
    (rig.volumes / UUID_A / "Engine" / "Logs").mkdir()
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode != 0, text(r)
    assert pi_mount_lines(r.stdout) == []


# --- missing directory / existing content ---------------------------------------------------------


def test_missing_directory_is_skipped_and_never_created(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    shutil.rmtree(rig.volumes / UUID_B / LOGS)
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, text(r)
    lines = pi_mount_lines(r.stdout)
    assert len(lines) == 1 and lines[0].rstrip().endswith(rig.target(UUID_A))
    assert "doesn't exist yet" in r.stdout
    assert not (rig.volumes / UUID_B / LOGS).exists()


def test_existing_logs_get_a_hidden_content_notice_and_stay_put(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    old = rig.volumes / UUID_A / LOGS / "WS.log"
    old.write_bytes(b"x" * 5000)
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0
    notice = [ln for ln in r.stdout.splitlines() if "NOTICE" in ln and rig.target(UUID_A) in ln]
    assert len(notice) == 1 and "hidden" in notice[0]
    assert old.read_bytes() == b"x" * 5000


# --- containment (SMT-R-01): real directories, real symlinks ---------------------------------------


def _escape_dir(rig: Rig) -> Path:
    outside = rig.root / "host-secret"
    (outside / "WS" / "Saved" / "Logs").mkdir(parents=True)
    (outside / "keep.txt").write_text("host data")
    return outside


def _assert_no_mount_on(rig: Rig, r: subprocess.CompletedProcess[str], uuid: str, outside: Path) -> None:
    assert r.returncode != 0, text(r)
    assert "symlink" in text(r) or "outside" in text(r)
    for ln in pi_mount_lines(r.stdout) + [c for c in rig.calls() if "soulmask-pi-" in c]:
        assert not ln.rstrip().endswith(rig.target(uuid)), ln
        assert str(outside) not in ln, ln
    assert (outside / "keep.txt").read_text() == "host data"


def test_symlinked_leaf_is_refused_and_the_other_instance_still_mounts(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    outside = _escape_dir(rig)
    shutil.rmtree(rig.volumes / UUID_B / LOGS)
    (rig.volumes / UUID_B / "WS" / "Saved" / "Logs").symlink_to(outside / "WS" / "Saved" / "Logs")
    r = rig.run(SETUP, DRY_RUN="1")
    _assert_no_mount_on(rig, r, UUID_B, outside)
    mounted = pi_mount_lines(r.stdout)
    assert len(mounted) == 1 and mounted[0].rstrip().endswith(rig.target(UUID_A))


def test_symlinked_parent_is_refused(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    outside = _escape_dir(rig)
    shutil.rmtree(rig.volumes / UUID_B / "WS")
    (rig.volumes / UUID_B / "WS").symlink_to(outside / "WS")
    r = rig.run(SETUP, DRY_RUN="1")
    _assert_no_mount_on(rig, r, UUID_B, outside)


def test_symlink_staying_inside_the_volume_is_still_refused(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    inside = rig.volumes / UUID_B / "other-dir"
    inside.mkdir()
    shutil.rmtree(rig.volumes / UUID_B / LOGS)
    (rig.volumes / UUID_B / "WS" / "Saved" / "Logs").symlink_to(inside)
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode != 0 and "symlink" in text(r)
    assert not any(ln.rstrip().endswith(rig.target(UUID_B)) for ln in pi_mount_lines(r.stdout))


def test_symlinked_volume_is_refused(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    outside = rig.root / "elsewhere"
    shutil.move(str(rig.volumes / UUID_B), str(outside))
    (rig.volumes / UUID_B).symlink_to(outside)
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode != 0 and "symlink" in text(r)
    assert not any(ln.rstrip().endswith(rig.target(UUID_B)) for ln in pi_mount_lines(r.stdout))


def test_path_swapped_for_a_symlink_after_planning_is_caught_by_the_recheck_and_rolled_back(rig: Rig) -> None:
    rig.conf(f"[per-instance-compressible]\n{LOGS}\n")
    outside = _escape_dir(rig)
    # right after the FIRST mount (instance A) the container "swaps" B's Logs for a symlink
    (rig.fake / "swap-after-first").write_text(f"{rig.target(UUID_B)} {outside / 'WS' / 'Saved' / 'Logs'}\n")
    r = rig.run(SETUP)
    assert r.returncode != 0, text(r)
    assert not any(row[0] == rig.target(UUID_B) for row in rig.mount_table())
    # A's mount of this run was rolled back
    assert f"umount {rig.target(UUID_A)}" in rig.calls()
    assert rig.mount_table() == []
    assert (outside / "keep.txt").read_text() == "host data"


# --- refusals --------------------------------------------------------------------------------------


def test_foreign_mount_at_target_is_refused_not_stacked_and_others_proceed(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.mounts((rig.target(UUID_A), "ext4", "/dev/sda1", "rw"))
    r = rig.run(SETUP)
    # EX_TEMPFAIL: shared part done, per-instance error recorded (the unit's SuccessExitStatus=75)
    assert r.returncode == 75, text(r)
    assert "refus" in text(r).lower()
    st = rig.state.read_text().splitlines()
    assert "PI_ERRORS=1" in st and any(ln.startswith("PI_ERROR=") and "not ours" in ln for ln in st)
    tmpfs_calls = [c for c in rig.calls() if c.startswith("mount -t tmpfs") and "soulmask-pi-" in c]
    assert len(tmpfs_calls) == 1 and tmpfs_calls[0].endswith(rig.target(UUID_B))  # A never stacked
    assert sum(1 for row in rig.mount_table() if row[0] == rig.target(UUID_A)) == 1
    # shared part completed and is recorded
    assert any(row[0] == rig.target(UUID_A, "Engine") for row in rig.mount_table())
    assert f"TARGET={rig.target(UUID_A, 'Engine')}" in rig.state.read_text()


def test_foreign_tmpfs_is_refused_even_though_the_fstype_matches(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.mounts((rig.target(UUID_A), "tmpfs", "tmpfs", "rw"))
    r = rig.run(SETUP)
    assert r.returncode != 0 and "refus" in text(r).lower()


def test_own_tmpfs_is_kept_idempotently(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    own = rig.own_mount(UUID_A)
    r = rig.run(SETUP)
    assert r.returncode == 0, text(r)
    assert own in "\n".join(ln for ln in r.stdout.splitlines() if "keeping" in ln.lower())
    tmpfs_mounts = pi_mount_lines("\n".join(rig.calls()))
    assert len(tmpfs_mounts) == 1 and tmpfs_mounts[0].endswith(rig.target(UUID_B))
    state = rig.state.read_text()
    assert f"PI_TARGET={own}" in state and f"PI_DERIVED={own} 988 988 755 {UUID_A}" in state
    # second run: everything is ours with the expected options, nothing new is mounted
    before = len(rig.calls())
    r2 = rig.run(SETUP)
    assert r2.returncode == 0, text(r2)
    assert pi_mount_lines("\n".join(rig.calls()[before:])) == []


@pytest.mark.parametrize(
    ("kwargs", "missing"),
    [
        ({"size_kib": 1024}, "size="),            # unit SIZE changed since the mount
        ({"extra": "nodev,noexec"}, "nosuid"),
        ({"extra": "nosuid,nodev"}, "noexec"),
        ({"noswap": True}, "unexpected noswap"),  # mounted noswap, configured compressible
    ],
)
def test_kept_mount_with_stale_options_is_refused(rig: Rig, kwargs: dict, missing: str) -> None:
    rig.conf(SHARED_AND_PI)
    rig.own_mount(UUID_A, **kwargs)
    r = rig.run(SETUP)
    assert r.returncode != 0 and "refusing to keep" in text(r) and missing in text(r)
    assert not any(c.endswith(rig.target(UUID_A)) for c in rig.calls() if c.startswith("mount -t tmpfs"))


def test_kept_noswap_section_mount_without_noswap_is_refused(rig: Rig) -> None:
    rig.conf(f"[per-instance-noswap]\n{LOGS}\n")
    rig.own_mount(UUID_A, noswap=False)
    r = rig.run(SETUP)
    assert r.returncode != 0 and "noswap" in text(r)


def test_mount_with_our_tag_that_is_not_in_the_state_file_is_refused(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.own_mount(UUID_A, in_state=False)
    r = rig.run(SETUP)
    assert r.returncode != 0 and "not recorded" in text(r)


def test_kernel_rejecting_noswap_fails_and_never_falls_back(rig: Rig) -> None:
    rig.conf(f"[per-instance-noswap]\n{LOGS}\n")
    (rig.fake / "reject-noswap").write_text("")
    r = rig.run(SETUP)
    assert r.returncode != 0, r.stdout
    assert "noswap" in text(r)
    attempts = [c for c in rig.calls() if c.startswith("mount -t tmpfs")]
    assert attempts and all("noswap" in c for c in attempts)
    assert rig.mount_table() == []


# --- partial failure: rollback, and independence from the shared part ------------------------------


def test_second_instance_mount_failure_rolls_back_the_first_but_shared_completes(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    (rig.fake / "fail-on").write_text(rig.target(UUID_B) + "\n")
    r = rig.run(SETUP)
    assert r.returncode != 0, text(r)
    assert f"umount {rig.target(UUID_A)}" in rig.calls()
    assert not any(row[2].startswith("soulmask-pi-") for row in rig.mount_table())
    state = rig.state.read_text()
    assert "PI_TARGET" not in state and f"TARGET={rig.target(UUID_A, 'Engine')}" in state
    # the shared tmpfs and its bind are in place (the game can start)
    assert any(row[0] == rig.target(UUID_A, "Engine") for row in rig.mount_table())
    # and a teardown unwinds it cleanly
    td = rig.run(TEARDOWN)
    assert td.returncode == 0, text(td)
    assert rig.mount_table() == []


def test_a_failing_shared_part_still_applies_the_per_instance_mounts(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    (rig.volumes / UUID_A / "Engine" / "payload.bin").write_bytes(b"x" * 20000)  # payload >= the 1-byte tmpfs
    r = rig.run(SETUP, SOULMASK_TMPFS_SIZE="1")  # payload >= tmpfs size -> shared part exits 1
    assert r.returncode != 0, text(r)
    mounted = [row for row in rig.mount_table() if row[2].startswith("soulmask-pi-")]
    assert sorted(row[0] for row in mounted) == sorted([rig.target(UUID_A), rig.target(UUID_B)])
    assert f"PI_TARGET={rig.target(UUID_A)}" in rig.state.read_text()


# --- real (shimmed) run, state file, teardown ------------------------------------------------------


def test_setup_records_pi_targets_and_teardown_reverses_them(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP)
    assert r.returncode == 0, text(r)
    state = rig.state.read_text().splitlines()
    pi = [ln[len("PI_TARGET="):] for ln in state if ln.startswith("PI_TARGET=")]
    assert sorted(pi) == sorted([rig.target(UUID_A), rig.target(UUID_B)])
    for uuid in (UUID_A, UUID_B):
        assert any(
            ln.startswith(f"PI_DERIVED={rig.target(uuid)} ") and ln.endswith(f" {uuid}") for ln in state
        )
    # a PI target is never recorded as a bind target
    assert not any(ln.startswith("TARGET=") and LOGS in ln for ln in state)
    assert any(ln.startswith("TARGET=") and "Engine" in ln for ln in state)
    table = {row[0]: row for row in rig.mount_table()}
    for t in pi:
        assert table[t][1] == "tmpfs" and table[t][2].startswith("soulmask-pi-")
    assert not (rig.root / "run" / "tmpfiles.d").exists()  # one pruning mechanism only

    td = rig.run(TEARDOWN)
    assert td.returncode == 0, text(td)
    remaining = {row[0] for row in rig.mount_table()}
    assert not remaining & set(pi), remaining
    assert not rig.state.exists()
    assert f"umount {pi[0]}" in rig.calls()
    # the container check ran for each instance by exact name
    dl = (rig.fake / "docker.log").read_text()
    assert f"name=^{UUID_A}$" in dl and f"name=^{UUID_B}$" in dl


def test_state_rewrite_does_not_orphan_earlier_mounts(rig: Rig) -> None:
    # earlier run mounted A's logs and a shared bind; this run's plan no longer mentions them
    rig.conf(f"[per-instance-compressible]\n{LOGS}\n")
    rig.own_mount(UUID_A)
    old_bind = str(rig.volumes / UUID_A / "Engine")
    rig.mounts((old_bind, "bind", "x", "rw"))
    with rig.state.open("a") as f:
        f.write(f"TARGET={old_bind}\n")
    # instance A flips to TMPFS=0 -> not in this run's plan at all
    (rig.etc / "instances.d" / f"{UUID_A}.env").write_text("TMPFS=0\nROLE=main\n")
    rig.add_instance(UUID_B, role="main")
    r = rig.run(SETUP)
    assert r.returncode == 0, text(r)
    state = rig.state.read_text()
    assert f"PI_TARGET={rig.target(UUID_A)}" in state     # still mounted -> still recorded
    assert f"TARGET={old_bind}" in state
    assert f"PI_TARGET={rig.target(UUID_B)}" in state


def test_teardown_refuses_when_a_per_instance_mount_is_busy(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    (rig.fake / "busy").write_text(rig.target(UUID_A) + "\n")
    td = rig.run(TEARDOWN)
    assert td.returncode != 0
    assert "busy" in text(td).lower()
    still = {row[0] for row in rig.mount_table()}
    assert rig.target(UUID_A) in still and rig.target(UUID_B) not in still
    state = rig.state.read_text()
    assert f"PI_TARGET={rig.target(UUID_A)}" in state and f"PI_DERIVED={rig.target(UUID_A)} " in state
    assert f"PI_TARGET={rig.target(UUID_B)}" not in state


def test_teardown_refuses_a_target_whose_container_is_running(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    rig.running(UUID_A, "some-other-container")
    table_before = rig.mount_table()
    state_before = rig.state.read_text()
    calls_before = rig.calls()
    td = rig.run(TEARDOWN)
    assert td.returncode == 1
    assert "RUNNING" in text(td) and UUID_A in text(td)
    # R2-05: the WHOLE teardown is refused before anything is unmounted — the
    # other instance's tmpfs, the shared binds and the ramdisk included
    assert rig.mount_table() == table_before
    assert [c for c in rig.calls()[len(calls_before):] if c.startswith("umount")] == []
    assert rig.state.read_text() == state_before


def test_teardown_refuses_when_only_a_shared_bind_target_belongs_to_a_running_game(rig: Rig) -> None:
    rig.conf("[compressible]\nEngine\n")
    assert rig.run(SETUP).returncode == 0
    table_before = rig.mount_table()
    rig.running(UUID_A)
    td = rig.run(TEARDOWN)
    assert td.returncode == 1 and "RUNNING" in text(td)
    assert rig.mount_table() == table_before


def test_teardown_finds_a_tagged_mount_the_state_file_does_not_know(rig: Rig) -> None:
    # setup was killed between the mount and the state write
    t = rig.own_mount(UUID_A, in_state=False)
    td = rig.run(TEARDOWN)
    assert td.returncode == 0, text(td)
    assert "found tagged per-instance tmpfs not in the state file" in text(td)
    assert t not in {row[0] for row in rig.mount_table()}
    # ... and it is refused while that instance's game runs
    t2 = rig.own_mount(UUID_A, in_state=False)
    rig.running(UUID_A)
    td2 = rig.run(TEARDOWN)
    assert td2.returncode == 1 and "RUNNING" in text(td2)
    assert t2 in {row[0] for row in rig.mount_table()}


@pytest.mark.parametrize("where", ["below-root-wrong-tag", "outside-root"])
def test_a_stray_tagged_mount_is_skipped_loudly_and_does_not_block_the_teardown(rig: Rig, where: str) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    if where == "below-root-wrong-tag":
        stray = str(rig.volumes / UUID_A / "WS" / "stray")
        src = "soulmask-pi-deadbeef"
    else:
        stray = str(rig.root / "elsewhere" / "Logs")
        src = f"soulmask-pi-{UUID_A[:8]}"
    rig.mounts((stray, "tmpfs", src, "rw"))
    td = rig.run(TEARDOWN)
    assert td.returncode == 0, text(td)
    assert "WARNING: SKIPPING stray tmpfs" in text(td) and stray in text(td)
    assert f"umount '{stray}'" in text(td)                 # the manual remedy is printed
    left = {row[0] for row in rig.mount_table()}
    assert left == {stray}                                 # everything else was unwound, the stray untouched
    assert f"umount {stray}" not in rig.calls()


def test_a_recorded_target_of_unknown_instance_still_refuses(rig: Rig) -> None:
    t = str(rig.root / "elsewhere" / "Logs2")
    rig.mounts((t, "tmpfs", f"soulmask-pi-{UUID_A[:8]}", "rw"))
    rig.state.write_text(f"PI_TARGET={t}\n")
    td = rig.run(TEARDOWN)
    assert td.returncode == 1 and "cannot tell which instance" in text(td)


def test_state_file_is_replaced_atomically_and_leaves_no_temp_file(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    mv_calls = (rig.fake / "mv.log").read_text().splitlines()
    assert f"mv -f {rig.state}.new {rig.state}" in mv_calls
    assert not Path(str(rig.state) + ".new").exists()
    assert rig.state.read_text().startswith("# soulmask_tmpfs state")


def test_recorded_error_messages_are_one_line_without_equals_signs(rig: Rig) -> None:
    rig.conf(f"[per-instance-compressible]\nWS/a=b/Logs\n")
    d = rig.volumes / UUID_A / "WS"
    (d / "real").mkdir()
    (d / "a=b").symlink_to(d / "real")          # a symlink whose NAME contains '='
    (d / "real" / "Logs").mkdir()
    r = rig.run(SETUP)
    assert r.returncode == 75, text(r)
    msgs = [ln for ln in rig.state.read_text().splitlines() if ln.startswith("PI_ERROR=")]
    assert msgs and all("=" not in m[len("PI_ERROR="):] and "\n" not in m for m in msgs)
    assert any("symlink" in m for m in msgs)
    keys = {ln.split("=", 1)[0] for ln in rig.state.read_text().splitlines() if "=" in ln}
    assert keys <= {"RAMDISK", "TMPFS_SIZE", "READONLY", "SOURCE_UUID", "PI_ERRORS", "PI_ERROR",
                    "TARGET", "PI_TARGET", "PI_DERIVED"}


def test_setup_refusal_of_an_unrecorded_tagged_mount_names_a_remedy_that_works(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.own_mount(UUID_A, in_state=False)
    r = rig.run(SETUP)
    assert r.returncode == 75 and "not recorded" in text(r)
    assert "soulmask_tmpfs-teardown.sh" in text(r) and "also finds tagged mounts" in text(r)
    # and that remedy really works: teardown removes it, the next setup mounts it
    assert rig.run(TEARDOWN).returncode == 0
    assert rig.run(SETUP).returncode == 0
    assert rig.target(UUID_A) in {row[0] for row in rig.mount_table()}


def test_state_is_written_after_every_per_instance_mount(rig: Rig) -> None:
    rig.conf(f"[per-instance-compressible]\n{LOGS}\n")
    assert rig.run(SETUP).returncode == 0
    segments = (rig.fake / "state-snapshots.log").read_text().split("--\n")[1:]
    assert len(segments) == 2                        # one snapshot before each of the two mounts
    assert f"PI_TARGET={rig.target(UUID_A)}" not in segments[0]
    assert f"PI_TARGET={rig.target(UUID_A)}" in segments[1]   # A recorded before B was mounted


def test_teardown_container_check_is_an_exact_name_match(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    rig.running(UUID_A + "-sidecar", UUID_A[:8])   # similar names are not the instance
    td = rig.run(TEARDOWN)
    assert td.returncode == 0, text(td)


def test_teardown_with_unreachable_docker_refuses_unless_the_daemon_is_down(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    (rig.fake / "docker-down").write_text("")
    td = rig.run(TEARDOWN)                      # docker.service is "active" per the shim -> cannot tell
    assert td.returncode != 0 and "cannot determine" in text(td)
    assert rig.target(UUID_A) in {row[0] for row in rig.mount_table()}
    (rig.fake / "docker-inactive").write_text("")  # the daemon itself is stopped (e.g. shutdown)
    td2 = rig.run(TEARDOWN)
    assert td2.returncode == 0, text(td2)
    assert not any(row[2].startswith("soulmask-pi-") for row in rig.mount_table())


def test_teardown_without_a_known_instance_refuses(rig: Rig) -> None:
    t = str(rig.root / "elsewhere" / "Logs")       # not below VOLUMES_ROOT and not recorded
    rig.mounts((t, "tmpfs", f"soulmask-pi-{UUID_A[:8]}", "rw"))
    rig.state.write_text(f"PI_TARGET={t}\n")
    td = rig.run(TEARDOWN)
    assert td.returncode == 1 and "cannot tell which instance" in text(td)
    assert t in {row[0] for row in rig.mount_table()}


def test_teardown_unmounts_children_before_parents(rig: Rig) -> None:
    parent, child = rig.target(UUID_A, "WS/Saved"), rig.target(UUID_A, "WS/Saved/Logs")
    tag = f"soulmask-pi-{UUID_A[:8]}"
    rig.mounts((parent, "tmpfs", tag, "rw"), (child, "tmpfs", tag, "rw"))
    rig.state.write_text(
        f"PI_TARGET={parent}\nPI_TARGET={child}\n"
        f"PI_DERIVED={parent} 1 1 755 {UUID_A}\nPI_DERIVED={child} 1 1 755 {UUID_A}\n"
    )
    td = rig.run(TEARDOWN)
    assert td.returncode == 0, text(td)
    ums = [c for c in rig.calls() if c.startswith("umount ")]
    assert ums.index(f"umount {child}") < ums.index(f"umount {parent}")


def test_teardown_dry_run_lists_every_pi_unmount_and_changes_nothing(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    calls_before = rig.calls()
    state_before = rig.state.read_text()
    td = rig.run(TEARDOWN, DRY_RUN="1")
    assert td.returncode == 0, text(td)
    for uuid in (UUID_A, UUID_B):
        assert f"DRY-RUN: umount {rig.target(uuid)}" in td.stdout
    assert rig.calls() == calls_before and rig.state.read_text() == state_before


# --- per-instance only config still works; shared behaviour unchanged -------------------------------


def test_per_instance_paths_are_mounted_even_when_no_shared_path_is_ready(rig: Rig) -> None:
    rig.conf(f"[compressible]\nNoSuchShared\n[per-instance-compressible]\n{LOGS}\n")
    r = rig.run(SETUP)
    assert r.returncode == 0, text(r)
    assert len(pi_mount_lines("\n".join(rig.calls()))) == 2
    assert f"PI_TARGET={rig.target(UUID_A)}" in rig.state.read_text()


def test_unknown_section_still_warns_and_shared_behaviour_is_unchanged(rig: Rig) -> None:
    rig.conf("[bogus]\nignored\n[compressible]\nEngine\n")
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, text(r)
    assert "WARNING: unknown section header '[bogus]'" in r.stdout
    assert pi_mount_lines(r.stdout) == []
    binds = shared_bind_lines(r.stdout)
    assert len(binds) == 1 and binds[0].rstrip().endswith(rig.target(UUID_A, "Engine"))
    assert "cp -a" in r.stdout


def test_shared_state_lines_are_unchanged_without_per_instance_sections(rig: Rig) -> None:
    rig.conf("[compressible]\nEngine\n")
    r = rig.run(SETUP)
    assert r.returncode == 0, text(r)
    state = rig.state.read_text()
    assert f"TARGET={rig.target(UUID_A, 'Engine')}" in state and "PI_TARGET" not in state


def test_setup_runs_one_prune_pass_after_writing_the_state(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rec = rig.root / "recorder.sh"
    rec.write_text('#!/usr/bin/env bash\ngrep -c "^PI_TARGET=" "$SOULMASK_TMPFS_STATE_FILE" > "$FAKE/prune-saw"\n')
    rec.chmod(0o755)
    r = rig.run(SETUP, SOULMASK_PRUNE_SCRIPT=str(rec))
    assert r.returncode == 0, text(r)
    assert (rig.fake / "prune-saw").read_text().strip() == "2"


# --- the prune script ------------------------------------------------------------------------------


def _backup(d: Path, name: str, size: int, age: int) -> Path:
    p = d / name
    p.write_bytes(b"x" * size)
    t = 2_000_000_000 - age
    os.utime(p, (t, t))
    return p


def _prune_rig(rig: Rig, uuid: str = UUID_A, **kw) -> Path:
    t = Path(rig.own_mount(uuid, **kw))
    return t


def test_prune_keeps_live_log_and_newest_backups_and_touches_nothing_else(rig: Rig) -> None:
    d = _prune_rig(rig)
    live = _backup(d, "WS.log", 10, 0)
    new = _backup(d, "WS-backup-2026.10.20-03.00.00.log", 10, 100)
    mid = _backup(d, "WS-backup-2026.10.19-03.00.00.log", 10, 200)
    old = _backup(d, "WS-backup-2026.10.18-03.00.00.log", 10, 300)
    other = _backup(d, "other.log", 10, 900)
    wrong_ext = _backup(d, "WS-backup-x.txt", 10, 900)
    space = _backup(d, "WS-backup- sp.log", 10, 900)
    sub = d / "WS-backup-dir.log"
    sub.mkdir()
    (sub / "inner.log").write_text("keep")
    link = d / "WS-backup-link.log"
    link.symlink_to(other)
    r = rig.run(PRUNE)
    assert r.returncode == 0, text(r)
    assert live.exists() and new.exists()                    # KEEP_BACKUPS=1 -> only the newest backup stays
    assert not mid.exists() and not old.exists()
    for keep in (other, wrong_ext, space, sub / "inner.log"):
        assert keep.exists(), keep
    assert link.is_symlink() and other.exists()


def test_prune_with_zero_backups_kept_still_never_deletes_the_live_log(rig: Rig) -> None:
    d = _prune_rig(rig)
    live = _backup(d, "WS.log", 10, 0)
    b = _backup(d, "WS-backup-a.log", 10, 100)
    r = rig.run(PRUNE, SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS="0")
    assert r.returncode == 0, text(r)
    assert live.exists() and not b.exists()


def test_prune_deletes_oldest_backups_above_the_high_water_mark(rig: Rig) -> None:
    d = _prune_rig(rig)
    live = _backup(d, "WS.log", 100_000, 0)
    b1 = _backup(d, "WS-backup-1.log", 100_000, 100)   # newest
    b2 = _backup(d, "WS-backup-2.log", 100_000, 200)
    b3 = _backup(d, "WS-backup-3.log", 100_000, 300)   # oldest
    (rig.fake / "df.size").write_text("450000")        # 4 files + dir = ~90% > 75%
    r = rig.run(PRUNE, SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS="5")
    assert r.returncode == 0, text(r)
    assert not b3.exists()                              # exactly the oldest went
    assert live.exists() and b1.exists() and b2.exists()


def test_prune_fails_loudly_when_the_live_log_alone_is_over_the_mark(rig: Rig) -> None:
    d = _prune_rig(rig)
    live = _backup(d, "WS.log", 100_000, 0)
    (rig.fake / "df.size").write_text("110000")
    r = rig.run(PRUNE)
    assert r.returncode == 1 and "live log alone" in text(r)
    assert live.exists()


def test_prune_does_nothing_to_a_directory_that_is_not_our_mount(rig: Rig) -> None:
    d = rig.volumes / UUID_A / LOGS
    b = _backup(d, "WS-backup-1.log", 10, 100)
    rig.state.write_text(f"PI_TARGET={d}\n")           # recorded, but NOT mounted (plain ext4 dir)
    r = rig.run(PRUNE, SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS="0")
    assert r.returncode == 0 and "NOT pruning" in text(r)
    assert b.exists()


def test_prune_refuses_a_mount_with_a_foreign_source(rig: Rig) -> None:
    d = rig.volumes / UUID_A / LOGS
    b = _backup(d, "WS-backup-1.log", 10, 100)
    rig.mounts((str(d), "tmpfs", "tmpfs", "rw"))
    rig.state.write_text(f"PI_TARGET={d}\n")
    r = rig.run(PRUNE, SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS="0")
    assert r.returncode == 1 and "not one of ours" in text(r)
    assert b.exists()


@pytest.mark.parametrize(
    ("var", "bad"),
    [
        ("SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS", ""),
        ("SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS", "-1"),
        ("SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT", "0"),
        ("SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT", "100"),
        ("SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT", "x"),
    ],
)
def test_prune_refuses_invalid_policy_and_deletes_nothing(rig: Rig, var: str, bad: str) -> None:
    d = _prune_rig(rig)
    b = _backup(d, "WS-backup-1.log", 10, 100)
    r = rig.run(PRUNE, **{var: bad})
    assert r.returncode == 2 and var in text(r)
    assert b.exists()


def test_prune_has_no_policy_defaults(rig: Rig) -> None:
    d = _prune_rig(rig)
    b = _backup(d, "WS-backup-1.log", 10, 100)
    r = rig.run_without(PRUNE, "SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT")
    assert r.returncode == 2 and b.exists()


def test_a_clean_setup_records_zero_errors_and_exits_zero(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP)
    assert r.returncode == 0, text(r)
    assert "PI_ERRORS=0" in rig.state.read_text().splitlines()


def test_prune_keeps_failing_while_the_last_setup_recorded_per_instance_errors(rig: Rig) -> None:
    d = _prune_rig(rig)
    live = _backup(d, "WS.log", 10, 0)
    old = _backup(d, "WS-backup-a.log", 10, 100)
    new = _backup(d, "WS-backup-b.log", 10, 50)
    with rig.state.open("a") as f:
        f.write("PI_ERRORS=2\nPI_ERROR=refusing per-instance mount: X is a symlink\nPI_ERROR=second problem\n")
    r = rig.run(PRUNE)
    assert r.returncode == 1, text(r)
    assert "recorded 2 per-instance error" in text(r) and "X is a symlink" in text(r)
    # it still did its pruning work
    assert live.exists() and new.exists() and not old.exists()
    # a clean setup run (PI_ERRORS=0) makes it pass again
    rig.state.write_text(rig.state.read_text().replace("PI_ERRORS=2", "PI_ERRORS=0"))
    assert rig.run(PRUNE).returncode == 0


def test_prune_dry_run_deletes_nothing_and_says_what_it_would(rig: Rig) -> None:
    d = _prune_rig(rig)
    live = _backup(d, "WS.log", 10, 0)
    a = _backup(d, "WS-backup-a.log", 10, 100)
    b = _backup(d, "WS-backup-b.log", 10, 200)
    r = rig.run(PRUNE, "--dry-run")
    assert r.returncode == 0, text(r)
    assert f"DRY-RUN: rm -f -- {b}" in r.stdout
    assert live.exists() and a.exists() and b.exists()


# --- toggle status ---------------------------------------------------------------------------------


def test_toggle_status_counts_are_single_lines_and_exclude_pi_targets_from_bind_counts(rig: Rig) -> None:
    rig.state.write_text(
        f"READONLY=0\nPI_TARGET={rig.target(UUID_A)}\nPI_TARGET={rig.target(UUID_B)}\n"
    )
    rig.ramdisk.mkdir()  # the real df -h runs on it
    rig.mounts((str(rig.ramdisk), "tmpfs", "tmpfs", "rw"))
    out = rig.run(TOGGLE).stdout
    lines = [ln.strip() for ln in out.splitlines()]
    assert "[soulmask_tmpfs] Bind mounts: 0 in state file" in lines
    assert "[soulmask_tmpfs] Per-instance tmpfs mounts: 2 in state file" in lines
    assert "0" not in lines                                    # the old "0\n0" glitch
    assert any(f"{UUID_A}: 0 bind path(s), 1 per-instance tmpfs" in ln for ln in lines)


def test_toggle_status_warns_about_recorded_per_instance_errors(rig: Rig) -> None:
    rig.state.write_text("READONLY=0\nPI_ERRORS=1\nPI_ERROR=refusing per-instance mount: X is a symlink\n")
    rig.ramdisk.mkdir()
    rig.mounts((str(rig.ramdisk), "tmpfs", "tmpfs", "rw"))
    out = rig.run(TOGGLE).stdout
    assert "WARNING: last setup run recorded 1 per-instance error(s):" in out
    assert "X is a symlink" in out


def test_toggle_status_with_no_per_instance_targets_prints_zero_once(rig: Rig) -> None:
    rig.state.write_text(f"READONLY=0\nTARGET={rig.target(UUID_A, 'Engine')}\n")
    rig.ramdisk.mkdir()  # the real df -h runs on it
    rig.mounts((str(rig.ramdisk), "tmpfs", "tmpfs", "rw"))
    out = rig.run(TOGGLE).stdout
    lines = [ln.strip() for ln in out.splitlines()]
    assert "[soulmask_tmpfs] Per-instance tmpfs mounts: 0 in state file" in lines
    assert "0" not in lines
    assert any(f"{UUID_A}: 1 bind path(s), 0 per-instance tmpfs" in ln for ln in lines)


# --- shipped files ---------------------------------------------------------------------------------


def _sections(path: Path) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = ""
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            cur = line.strip("[]")
            out.setdefault(cur, [])
        elif cur:
            out[cur].append(line)
    return out


def _env(unit: Path) -> dict[str, str]:
    return dict(
        m.groups() for m in re.finditer(r"^Environment=([A-Z_]+)=(.*)$", unit.read_text(), re.M)
    )


def test_shipped_conf_puts_the_game_log_dir_per_instance_and_nowhere_shared() -> None:
    sec = _sections(SHIPPED_CONF)
    assert sec["per-instance-compressible"] == [LOGS]
    assert "per-instance-noswap" in sec
    assert not any(LOGS in v for k, v in sec.items() if not k.startswith("per-instance"))


POLICY_KEYS = (
    "SOULMASK_TMPFS_PER_INSTANCE_SIZE",
    "SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS",
    "SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT",
)


def _envfile(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k] = v
    return out


def test_policy_has_one_source_read_by_every_unit_that_needs_it() -> None:
    policy = _envfile(POLICY_ENV)
    assert policy == {
        "SOULMASK_TMPFS_PER_INSTANCE_SIZE": "512M",
        "SOULMASK_TMPFS_PER_INSTANCE_KEEP_BACKUPS": "3",
        "SOULMASK_TMPFS_PER_INSTANCE_HIGH_WATER_PCT": "75",
    }
    for unit in (SHIPPED_UNIT, PRUNE_UNIT):
        body = unit.read_text()
        # mandatory (no '-' prefix) so a missing file fails the unit loudly
        assert re.search(r"^EnvironmentFile=/etc/gstammtisch/soulmask_tmpfs\.env$", body, re.M), unit
        env = _env(unit)
        assert not any(k in env for k in POLICY_KEYS), f"{unit.name} carries its own copy of the policy"
        assert "MAX_AGE" not in body.replace("max_age", "")  # no second (age) mechanism
    assert "soulmask_logs-prune.sh" in PRUNE_UNIT.read_text()


def test_policy_values_pass_the_scripts_own_validators() -> None:
    policy = _envfile(POLICY_ENV)
    src = ". " + str(LIB) + "\n" + "".join(f'export {k}="{v}"\n' for k, v in policy.items())
    r = subprocess.run(
        ["bash", "-c", src + 'soulmask_pi_validate_prune_env && soulmask_pi_size_kib "$SOULMASK_TMPFS_PER_INSTANCE_SIZE"'],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(512 * 1024)


def test_the_unit_treats_exit_75_as_success_and_setup_uses_that_code() -> None:
    assert re.search(r"^SuccessExitStatus=75$", SHIPPED_UNIT.read_text(), re.M)
    assert re.search(r"^PI_EXIT_CODE=75\b", SETUP.read_text(), re.M)
    timer = PRUNE_TIMER.read_text()
    assert re.search(r"^OnUnitActiveSec=10min$", timer, re.M) and "WantedBy=timers.target" in timer


def test_scripts_default_none_of_the_policy_values_and_use_one_mechanism() -> None:
    for script in (SETUP, PRUNE, LIB):
        src = script.read_text()
        assert not re.search(r"PER_INSTANCE_(SIZE|KEEP_BACKUPS|HIGH_WATER_PCT):-[^}]", src), script
    for script in (SETUP, TEARDOWN):
        assert "tmpfiles.d" not in script.read_text(), script


@pytest.mark.parametrize("script", [SETUP, TEARDOWN, TOGGLE, PRUNE])
def test_shipped_scripts_are_executable(script: Path) -> None:
    # setup runs the prune script directly (and systemd ExecStart= needs the bit)
    assert os.access(script, os.X_OK), script
