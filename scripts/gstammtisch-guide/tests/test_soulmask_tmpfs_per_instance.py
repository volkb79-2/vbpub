"""Per-instance tmpfs sections of soulmask_tmpfs-{setup,teardown}.sh (work item SMT-01).

These tests run the REAL shell scripts against a temporary etc, volumes root, state file, tmpfiles
directory and ramdisk directory. Nothing touches a real mount: ``mount``, ``umount``, ``mountpoint``,
``findmnt``, ``systemctl`` and (for owner/mode only) ``stat`` are PATH shims backed by a fake mount
table (``mounts.tsv``: target, fstype, source, options). Every other tool (cp, du, awk, mkdir, ...)
is the real one, operating inside pytest's tmp_path.

The oracle is the behavioural contract in HANDOFF-SMT-01: each ``TMPFS=1`` instance gets its OWN
tmpfs on each per-instance path, with owner/mode derived from the on-disk directory, never a literal.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
SBIN = PROJECT / "files" / "usr" / "local" / "sbin"
SETUP = SBIN / "soulmask_tmpfs-setup.sh"
TEARDOWN = SBIN / "soulmask_tmpfs-teardown.sh"
LIB = SBIN / "soulmask-instance-lib.sh"
SHIPPED_CONF = PROJECT / "files" / "etc" / "gstammtisch" / "soulmask_tmpfs-paths.conf"
SHIPPED_UNIT = PROJECT / "files" / "etc" / "systemd" / "system" / "soulmask_tmpfs.service"

UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"  # ROLE=main
UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"
LOGS = "WS/Saved/Logs"

SHIMS = {
    "mountpoint": r"""#!/usr/bin/env bash
[ "${1:-}" = "-q" ] && shift
awk -F'\t' -v t="$1" '$1==t{f=1} END{exit !f}' "$FAKE/mounts.tsv"
""",
    "findmnt": r"""#!/usr/bin/env bash
field=""; target=""
while [ $# -gt 0 ]; do
  case "$1" in
    -no) field="$2"; shift 2 ;;
    -nl|-n) shift ;;
    -o|--source) shift 2 ;;
    *) target="$1"; shift ;;
  esac
done
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
    "mount": r"""#!/usr/bin/env bash
echo "mount $*" >> "$FAKE/calls.log"
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
printf '%s\t%s\t%s\t%s\n' "${pos[1]}" "$fstype" "${pos[0]}" "$opts" >> "$FAKE/mounts.tsv"
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
    "systemctl": "#!/usr/bin/env bash\nexit 0\n",
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
        self.tmpfiles_dir = root / "run" / "tmpfiles.d"
        self.ramdisk = root / "ramdisk"
        for d in (self.fake, self.shims, self.etc / "instances.d", self.volumes, self.state.parent):
            d.mkdir(parents=True, exist_ok=True)
        (self.fake / "mounts.tsv").write_text("")
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
        (self.fake / "mounts.tsv").write_text("".join("\t".join(r) + "\n" for r in rows))

    def mount_table(self) -> list[list[str]]:
        return [ln.split("\t") for ln in (self.fake / "mounts.tsv").read_text().splitlines() if ln]

    def calls(self) -> list[str]:
        f = self.fake / "calls.log"
        return f.read_text().splitlines() if f.exists() else []

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
            "SOULMASK_TMPFILES_DIR": str(self.tmpfiles_dir),
            "SOULMASK_TMPFS_PER_INSTANCE_SIZE": "64M",
            "SOULMASK_TMPFS_PER_INSTANCE_MAX_AGE": "3d",
            "SOULMASK_TMPFS_PER_INSTANCE_KEEP": "WS.log",
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

    @property
    def tmpfiles_file(self) -> Path:
        return self.tmpfiles_dir / "soulmask-per-instance.conf"


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


# --- distinct per-instance mounts -----------------------------------------------------------------


def test_two_instances_get_two_distinct_dry_run_mounts_and_nothing_shared(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, r.stdout + r.stderr
    lines = pi_mount_lines(r.stdout)
    assert len(lines) == 2, r.stdout
    for uuid in (UUID_A, UUID_B):
        mine = [ln for ln in lines if ln.rstrip().endswith(rig.target(uuid))]
        assert len(mine) == 1, (uuid, lines)
        assert f"soulmask-pi-{uuid[:8]}" in mine[0]
    # the per-instance path is never part of the shared copy/bind list
    binds = [ln for ln in r.stdout.splitlines() if "--bind" in ln or "cp -a" in ln]
    assert binds and not any(LOGS in ln for ln in binds), binds
    assert not (rig.ramdisk / LOGS).exists()
    # dry run: no real action at all
    assert rig.calls() == [] and not rig.state.exists() and not rig.tmpfiles_file.exists()


def test_dry_run_flag_behaves_like_env(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, "--dry-run")
    assert r.returncode == 0 and len(pi_mount_lines(r.stdout)) == 2
    assert rig.calls() == []


def test_dry_run_prints_the_tmpfiles_action(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, DRY_RUN="1")
    assert str(rig.tmpfiles_file) in r.stdout
    assert re.search(rf"e {re.escape(rig.target(UUID_A))} - - - 3d", r.stdout), r.stdout


# --- derived options ------------------------------------------------------------------------------


def test_mount_options_derive_from_the_on_disk_directory(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.stat_map((rig.target(UUID_A), 1234, 4321, "750"), (rig.target(UUID_B), 988, 988, "755"))
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, r.stdout + r.stderr
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
    assert r.returncode == 0, r.stdout + r.stderr
    lines = pi_mount_lines(r.stdout)
    assert len(lines) == 2 and all("noswap" in ln for ln in lines)


def test_size_has_no_script_default(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    env = rig.env(DRY_RUN="1")
    del env["SOULMASK_TMPFS_PER_INSTANCE_SIZE"]
    r = subprocess.run(["bash", str(SETUP)], env=env, capture_output=True, text=True, check=False)
    assert r.returncode != 0
    assert "SOULMASK_TMPFS_PER_INSTANCE_SIZE" in r.stdout + r.stderr
    assert pi_mount_lines(r.stdout) == []


def test_malformed_size_is_refused(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP, DRY_RUN="1", SOULMASK_TMPFS_PER_INSTANCE_SIZE="lots")
    assert r.returncode != 0 and "SOULMASK_TMPFS_PER_INSTANCE_SIZE" in r.stdout + r.stderr


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", "has space", "pct%x"])
def test_unsafe_per_instance_path_is_refused(rig: Rig, bad: str) -> None:
    rig.conf(f"[per-instance-compressible]\n{bad}\n")
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode != 0, r.stdout
    assert pi_mount_lines(r.stdout) == []


# --- missing directory / existing content ---------------------------------------------------------


def test_missing_directory_is_skipped_and_never_created(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    import shutil

    shutil.rmtree(rig.volumes / UUID_B / LOGS)
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, r.stdout + r.stderr
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


# --- refusals --------------------------------------------------------------------------------------


def test_foreign_mount_at_target_is_refused_not_stacked(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.mounts((rig.target(UUID_A), "ext4", "/dev/sda1", "rw"))
    r = rig.run(SETUP)
    assert r.returncode != 0, r.stdout
    assert "refus" in (r.stdout + r.stderr).lower()
    # nothing was mounted anywhere, including for the healthy instance B
    assert [c for c in rig.calls() if c.startswith("mount ")] == []


def test_foreign_tmpfs_is_refused_even_though_the_fstype_matches(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.mounts((rig.target(UUID_A), "tmpfs", "tmpfs", "rw"))
    r = rig.run(SETUP)
    assert r.returncode != 0 and "refus" in (r.stdout + r.stderr).lower()


def test_own_tmpfs_is_kept_idempotently(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    own = (rig.target(UUID_A), "tmpfs", f"soulmask-pi-{UUID_A[:8]}", "rw,nosuid,nodev,noexec")
    rig.mounts(own)
    r = rig.run(SETUP)
    assert r.returncode == 0, r.stdout + r.stderr
    assert own[0] in "\n".join(ln for ln in r.stdout.splitlines() if "keep" in ln.lower())
    tmpfs_mounts = pi_mount_lines("\n".join(rig.calls()))
    assert len(tmpfs_mounts) == 1 and tmpfs_mounts[0].endswith(rig.target(UUID_B))
    assert f"PI_TARGET={rig.target(UUID_A)}" in rig.state.read_text()
    # second run: now everything is ours, nothing new is mounted
    before = len(rig.calls())
    r2 = rig.run(SETUP)
    assert r2.returncode == 0
    assert pi_mount_lines("\n".join(rig.calls()[before:])) == []


def test_kernel_rejecting_noswap_fails_and_never_falls_back(rig: Rig) -> None:
    rig.conf(f"[per-instance-noswap]\n{LOGS}\n")
    (rig.fake / "reject-noswap").write_text("")
    r = rig.run(SETUP)
    assert r.returncode != 0, r.stdout
    assert "noswap" in (r.stdout + r.stderr)
    attempts = [c for c in rig.calls() if c.startswith("mount -t tmpfs")]
    assert attempts and all("noswap" in c for c in attempts)
    assert rig.mount_table() == []


# --- real (shimmed) run, state file, tmpfiles, teardown --------------------------------------------


def test_setup_records_pi_targets_and_tmpfiles_and_teardown_reverses_them(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    r = rig.run(SETUP)
    assert r.returncode == 0, r.stdout + r.stderr
    state = rig.state.read_text().splitlines()
    pi = [ln[len("PI_TARGET="):] for ln in state if ln.startswith("PI_TARGET=")]
    assert sorted(pi) == sorted([rig.target(UUID_A), rig.target(UUID_B)])
    # a PI target is never recorded as a bind target
    assert not any(ln.startswith("TARGET=") and LOGS in ln for ln in state)
    assert any(ln.startswith("TARGET=") and "Engine" in ln for ln in state)
    table = {row[0]: row for row in rig.mount_table()}
    for t in pi:
        assert table[t][1] == "tmpfs" and table[t][2].startswith("soulmask-pi-")
    text = rig.tmpfiles_file.read_text()
    for t in pi:
        assert f"e {t} - - - 3d" in text
        assert f"X {t}/WS.log" in text

    td = rig.run(TEARDOWN)
    assert td.returncode == 0, td.stdout + td.stderr
    remaining = {row[0] for row in rig.mount_table()}
    assert not remaining & set(pi), remaining
    assert not rig.tmpfiles_file.exists() and not rig.state.exists()
    assert [c for c in rig.calls() if c == f"umount {pi[0]}"]


def test_teardown_refuses_when_a_per_instance_mount_is_busy(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    (rig.fake / "busy").write_text(rig.target(UUID_A) + "\n")
    td = rig.run(TEARDOWN)
    assert td.returncode != 0
    assert "busy" in (td.stdout + td.stderr).lower()
    still = {row[0] for row in rig.mount_table()}
    assert rig.target(UUID_A) in still and rig.target(UUID_B) not in still
    # the busy target stays recorded, so a later teardown can still find it
    assert f"PI_TARGET={rig.target(UUID_A)}" in rig.state.read_text()
    assert not rig.tmpfiles_file.exists()


def test_teardown_dry_run_lists_every_pi_unmount_and_changes_nothing(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    assert rig.run(SETUP).returncode == 0
    calls_before = rig.calls()
    td = rig.run(TEARDOWN, DRY_RUN="1")
    assert td.returncode == 0, td.stdout + td.stderr
    for uuid in (UUID_A, UUID_B):
        assert f"DRY-RUN: umount {rig.target(uuid)}" in td.stdout
    assert str(rig.tmpfiles_file) in td.stdout
    assert rig.calls() == calls_before and rig.tmpfiles_file.exists() and rig.state.exists()


def test_stale_tmpfiles_file_is_removed_when_no_target_is_mounted(rig: Rig) -> None:
    rig.conf(SHARED_AND_PI)
    rig.tmpfiles_dir.mkdir(parents=True)
    rig.tmpfiles_file.write_text("e /stale - - - 3d\n")
    import shutil

    for u in (UUID_A, UUID_B):
        shutil.rmtree(rig.volumes / u / LOGS)
    assert rig.run(SETUP).returncode == 0
    assert not rig.tmpfiles_file.exists()


# --- per-instance only config still works; shared behaviour unchanged -------------------------------


def test_per_instance_paths_are_mounted_even_when_no_shared_path_is_ready(rig: Rig) -> None:
    rig.conf(f"[compressible]\nNoSuchShared\n[per-instance-compressible]\n{LOGS}\n")
    r = rig.run(SETUP)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(pi_mount_lines("\n".join(rig.calls()))) == 2
    assert f"PI_TARGET={rig.target(UUID_A)}" in rig.state.read_text()


def test_unknown_section_still_warns_and_shared_behaviour_is_unchanged(rig: Rig) -> None:
    rig.conf(f"[bogus]\nignored\n[compressible]\nEngine\n")
    r = rig.run(SETUP, DRY_RUN="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARNING: unknown section header '[bogus]'" in r.stdout
    assert pi_mount_lines(r.stdout) == []
    binds = [ln for ln in r.stdout.splitlines() if "mount --bind" in ln]
    assert len(binds) == 1 and binds[0].rstrip().endswith(rig.target(UUID_A, "Engine"))
    assert "cp -a" in r.stdout


def test_shared_state_lines_are_unchanged_without_per_instance_sections(rig: Rig) -> None:
    rig.conf("[compressible]\nEngine\n")
    r = rig.run(SETUP)
    assert r.returncode == 0, r.stdout + r.stderr
    state = rig.state.read_text()
    assert f"TARGET={rig.target(UUID_A, 'Engine')}" in state and "PI_TARGET" not in state
    assert not rig.tmpfiles_file.exists()


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


def test_shipped_conf_puts_the_game_log_dir_per_instance_and_nowhere_shared() -> None:
    sec = _sections(SHIPPED_CONF)
    assert sec["per-instance-compressible"] == [LOGS]
    assert "per-instance-noswap" in sec
    assert not any(LOGS in v for k, v in sec.items() if not k.startswith("per-instance"))


def test_shipped_unit_defaults_the_per_instance_keys_not_the_script() -> None:
    unit = SHIPPED_UNIT.read_text()
    assert re.search(r"^Environment=SOULMASK_TMPFS_PER_INSTANCE_SIZE=256M$", unit, re.M)
    assert re.search(r"^Environment=SOULMASK_TMPFS_PER_INSTANCE_MAX_AGE=3d$", unit, re.M)
    assert re.search(r"^Environment=SOULMASK_TMPFS_PER_INSTANCE_KEEP=WS\.log$", unit, re.M)
    # an empty default (":-}") is "unset"; a non-empty one would be an invented literal
    assert not re.search(r"PER_INSTANCE_(SIZE|MAX_AGE):-[^}]", SETUP.read_text())
