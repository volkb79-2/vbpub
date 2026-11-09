"""Hermetic shell acceptance tests for tools/swap-bench-window.sh."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT / "tools" / "swap-bench-window.sh"
PARTUUID = "11111111-2222-3333-4444-555555555555"
SWAP_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_PARTUUID = "22222222-3333-4444-5555-666666666666"
SIZE = str(16 * 1024**3)
TOOLS = (
    "flock", "findmnt", "lsblk", "blkid", "blockdev", "wipefs", "mkswap", "swapon",
    "swapoff", "timeout", "awk", "grep", "dd", "sync", "install", "cat", "tr", "ls",
    "basename", "id", "date", "mv", "rm", "fuser",
)


def _write_executable(path: Path, body: str) -> None:
    path.unlink(missing_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _real_tools(bindir: Path, missing: str | None = None) -> None:
    for name in TOOLS:
        if name == missing:
            continue
        if name == "fuser":
            _write_executable(bindir / name, "#!/bin/bash\nexit 1\n")
            continue
        real = shutil.which(name)
        assert real is not None, f"test host needs {name}"
        (bindir / name).symlink_to(real)


def _fake_root(tmp_path: Path, *, partlabel: str = "") -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    bindir = tmp_path / "bin"
    logs = tmp_path / "logs"
    for path in (root / "etc", root / "proc", root / "sys/class/block/vda9/holders", root / "var", bindir, logs):
        path.mkdir(parents=True, exist_ok=True)
    (root / "etc/fstab").write_text(f"PARTUUID={PARTUUID} none swap sw,pri=10 0 0\n", encoding="utf-8")
    (root / "proc/swaps").write_text(
        "Filename\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
        "/dev/vda8               partition\t16777212\t1024\t\t5\n"
        "/dev/vda9               partition\t16777212\t1024\t\t10\n",
        encoding="utf-8",
    )
    (root / "proc/meminfo").write_text("MemAvailable: 8000000 kB\n", encoding="utf-8")
    (tmp_path / "partuuid").write_text(PARTUUID + "\n", encoding="utf-8")
    (tmp_path / "uuid").write_text(SWAP_UUID + "\n", encoding="utf-8")
    (tmp_path / "label").write_text("vbpub-swap1\n", encoding="utf-8")
    (tmp_path / "partlabel-before").write_text(partlabel, encoding="utf-8")
    (tmp_path / "partlabel-after").write_text("CHANGED", encoding="utf-8")

    _real_tools(bindir)
    _write_executable(bindir / "id", "#!/bin/bash\nif [ \"${1:-}\" = -u ]; then echo 0; else exit 2; fi\n")
    _write_executable(
        bindir / "findmnt",
        "#!/bin/bash\nexit 0\n",
    )
    _write_executable(
        bindir / "blockdev",
        f"#!/bin/bash\nprintf '%s\\n' {shlex.quote(SIZE)}\n",
    )
    _write_executable(
        bindir / "lsblk",
        "#!/bin/bash\n"
        "root=$FAKE_ROOT\n"
        "case \" $* \" in\n"
        "  *' TYPE '*) printf 'part\\n' ;;\n"
        "  *' PARTTYPE '*) printf '0657fd6d-a4ab-43c4-84e5-0933c84b4f4f\\n' ;;\n"
        "  *' PARTLABEL '*) if [ -e \"$root/after-swap\" ]; then cat \"$FAKE_PARTLABEL_AFTER\"; else cat \"$FAKE_PARTLABEL_BEFORE\"; fi ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
    )
    _write_executable(
        bindir / "blkid",
        "#!/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_BLKID_LOG\"\n"
        "case \" $* \" in\n"
        f"  *' -p -s TYPE '* ) if [ -e \"$FAKE_ROOT/after-swap\" ]; then exit 0; else printf 'swap\\n'; fi ;;\n"
        f"  *' -p -s PART_ENTRY_UUID '*) cat \"$FAKE_PARTUUID\" ;;\n"
        f"  *' -p -s UUID '*) if [ -e \"$FAKE_ROOT/after-swap\" ]; then cat \"$FAKE_UUID_AFTER\"; else cat \"$FAKE_UUID_BEFORE\"; fi ;;\n"
        f"  *' -p -s LABEL '*) cat \"$FAKE_LABEL\" ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
    )
    _write_executable(
        bindir / "swapoff",
        "#!/bin/bash\n"
        "printf 'Filename Type Size Used Priority\\n' > \"$FAKE_ROOT/proc/swaps\"\n"
        ": > \"$FAKE_ROOT/after-swap\"\n",
    )
    _write_executable(
        bindir / "wipefs",
        "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$FAKE_WIPEFS_LOG\"\n",
    )
    _write_executable(bindir / "fuser", "#!/bin/bash\nexit 1\n")
    _write_executable(bindir / "mkswap", "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$FAKE_MKSWAP_LOG\"\n")
    _write_executable(bindir / "swapon", "#!/bin/bash\nexit 0\n")
    return root, bindir, logs


def _script_copy(tmp_path: Path, root: Path, bindir: Path) -> Path:
    source = SCRIPT.read_text(encoding="utf-8")
    for original, replacement in (
        ("/proc", str(root / "proc")),
        ("/sys", str(root / "sys")),
        ("/etc", str(root / "etc")),
        ("/var", str(root / "var")),
        ("PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={bindir}"),
    ):
        source = source.replace(original, replacement)
    target = tmp_path / "swap-bench-window.sh"
    target.write_text(source, encoding="utf-8")
    target.chmod(0o755)
    return target


def _environment(tmp_path: Path, root: Path, bindir: Path, logs: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": str(bindir),
        "FAKE_ROOT": str(root),
        "FAKE_PARTUUID": str(tmp_path / "partuuid"),
        "FAKE_PARTUUID_AFTER": str(tmp_path / "partuuid-after"),
        "FAKE_UUID_BEFORE": str(tmp_path / "uuid"),
        "FAKE_UUID_AFTER": str(tmp_path / "uuid-after"),
        "FAKE_LABEL": str(tmp_path / "label"),
        "FAKE_PARTLABEL_BEFORE": str(tmp_path / "partlabel-before"),
        "FAKE_PARTLABEL_AFTER": str(tmp_path / "partlabel-after"),
        "FAKE_BLKID_LOG": str(logs / "blkid.log"),
        "FAKE_WIPEFS_LOG": str(logs / "wipefs.log"),
        "FAKE_DD_LOG": str(logs / "dd.log"),
        "FAKE_MKSWAP_LOG": str(logs / "mkswap.log"),
        "FAKE_SWAPON_LOG": str(logs / "swapon.log"),
    }


def _run(tmp_path: Path, *args: str, root: Path | None = None, bindir: Path | None = None):
    if root is None or bindir is None:
        root, bindir, logs = _fake_root(tmp_path)
    else:
        logs = tmp_path / "logs"
    script = _script_copy(tmp_path, root, bindir)
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        cwd=PROJECT,
        env=_environment(tmp_path, root, bindir, logs),
        text=True,
        capture_output=True,
        check=False,
    )


def test_bash_syntax_and_identity_contract():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    text = SCRIPT.read_text(encoding="utf-8")
    assert "blkid -p -s PART_ENTRY_UUID" in text
    assert "lsblk -dno PARTLABEL" in text
    assert "PARTLABEL '.*' is not vbpub-swap" not in text


def test_begin_accepts_a_swap_partition_with_no_partlabel(tmp_path):
    result = _run(
        tmp_path,
        "begin",
        "/dev/vda9",
        "--expect-partuuid",
        PARTUUID,
        "--expect-size",
        SIZE,
    )

    assert result.returncode == 0, result.stderr
    assert "checks OK" in result.stdout
    assert "PARTLABEL=none" in result.stdout
    assert "LABEL=vbpub-swap1" in result.stdout
    assert not (tmp_path / "logs/wipefs.log").exists()


@pytest.mark.parametrize("missing", ["cat", "tr", "ls", "basename", "date", "mv", "rm"])
def test_missing_required_tool_refuses_before_any_window(tmp_path, missing):
    bindir = tmp_path / "restricted-bin"
    bindir.mkdir()
    _real_tools(bindir, missing)
    _write_executable(bindir / "id", "#!/bin/bash\nif [ \"${1:-}\" = -u ]; then echo 0; else exit 2; fi\n")
    script = SCRIPT.read_text(encoding="utf-8").replace(
        "PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={bindir}"
    )
    target = tmp_path / "swap-bench-window.sh"
    target.write_text(script, encoding="utf-8")
    target.chmod(0o755)
    result = subprocess.run(
        [
            "/bin/bash", str(target), "begin", "/dev/vda9",
            "--expect-partuuid", PARTUUID, "--expect-size", SIZE,
        ],
        cwd=PROJECT,
        env={**os.environ, "PATH": str(bindir)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert f"{missing} is not installed" in result.stderr


def test_any_live_swapoff_blocks_end_without_false_nothing_to_restore(tmp_path):
    root, bindir, logs = _fake_root(tmp_path)
    state_dir = root / "var/lib/vbpub/swapbench"
    state_dir.mkdir(parents=True)
    (state_dir / "vda9.env").write_text(
        f"DEV=/dev/vda9\nPARTUUID={PARTUUID}\nUUID={SWAP_UUID}\nLABEL=vbpub-swap1\nSIZE={SIZE}\nPARTLABEL=\n",
        encoding="utf-8",
    )
    pid = root / "proc/4242"
    pid.mkdir()
    (pid / "comm").write_text("swapoff\n", encoding="utf-8")
    (pid / "cmdline").write_bytes(b"swapoff\0-a\0")

    result = _run(
        tmp_path,
        "end",
        "/dev/vda9",
        "--expect-partuuid",
        PARTUUID,
        "--expect-size",
        SIZE,
        root=root,
        bindir=bindir,
    )

    assert result.returncode != 0
    assert "a swapoff process is still running" in result.stderr
    assert "nothing to restore" not in result.stdout


def test_end_restores_only_selected_swap_with_its_fstab_options(tmp_path):
    root, bindir, logs = _fake_root(tmp_path)
    (root / "etc/fstab").write_text(
        f"PARTUUID={PARTUUID} none swap sw,pri=10,discard=once 0 0\n"
        f"PARTUUID={OTHER_PARTUUID} none swap sw,pri=2 0 0\n",
        encoding="utf-8",
    )
    (root / "proc/swaps").write_text(
        "Filename\t\t\tType\t\tSize\t\tUsed\t\tPriority\n",
        encoding="utf-8",
    )
    state_dir = root / "var/lib/vbpub/swapbench"
    state_dir.mkdir(parents=True)
    (state_dir / "vda9.env").write_text(
        f"DEV=/dev/vda9\nPARTUUID={PARTUUID}\nUUID={SWAP_UUID}\nLABEL=vbpub-swap1\nSIZE={SIZE}\nPARTLABEL=\n",
        encoding="utf-8",
    )
    (tmp_path / "uuid-after").write_text(SWAP_UUID + "\n", encoding="utf-8")
    (tmp_path / "partlabel-after").write_text("", encoding="utf-8")
    _write_executable(
        bindir / "dd",
        "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$FAKE_DD_LOG\"\n",
    )
    _write_executable(
        bindir / "mkswap",
        "#!/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_MKSWAP_LOG\"\n"
        ": > \"$FAKE_ROOT/after-swap\"\n",
    )
    _write_executable(
        bindir / "swapon",
        "#!/bin/bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_SWAPON_LOG\"\n"
        "if [ \"${1:-}\" = -a ]; then\n"
        "  printf '/dev/vda8 partition 16777212 0 2\\n/dev/vda9 partition 16777212 0 10\\n' >> \"$FAKE_ROOT/proc/swaps\"\n"
        "elif [ \"${1:-}\" = --options ] && [ \"${3:-}\" = /dev/vda9 ]; then\n"
        "  pri=\n"
        "  IFS=, read -ra opts <<< \"$2\"\n"
        "  for opt in \"${opts[@]}\"; do case $opt in pri=*) pri=${opt#pri=} ;; esac; done\n"
        "  printf '/dev/vda9 partition 16777212 0 %s\\n' \"${pri:--2}\" >> \"$FAKE_ROOT/proc/swaps\"\n"
        "else\n"
        "  exit 2\n"
        "fi\n",
    )

    result = _run(
        tmp_path,
        "end",
        "/dev/vda9",
        "--expect-partuuid",
        PARTUUID,
        "--expect-size",
        SIZE,
        "--commit",
        root=root,
        bindir=bindir,
    )

    assert result.returncode == 0, result.stderr
    active_devices = {
        line.split()[0]
        for line in (root / "proc/swaps").read_text(encoding="utf-8").splitlines()[1:]
    }
    assert "/dev/vda9" in active_devices
    assert "/dev/vda8" not in active_devices
    assert (logs / "swapon.log").read_text(encoding="utf-8").splitlines() == [
        "--options sw,pri=10,discard=once /dev/vda9"
    ]
    assert len(list(state_dir.glob("vda9.env.done-*"))) == 1
    assert not (state_dir / "vda9.env").exists()


@pytest.mark.parametrize("changed", ["partuuid", "partlabel"])
def test_identity_recheck_detects_change_before_wipe(tmp_path, changed):
    root, bindir, logs = _fake_root(tmp_path, partlabel="OLD")
    (tmp_path / "uuid-after").write_text(SWAP_UUID + "\n", encoding="utf-8")
    if changed == "partuuid":
        (tmp_path / "partuuid-after").write_text("99999999-2222-3333-4444-555555555555\n", encoding="utf-8")
    else:
        _write_executable(
            bindir / "swapoff",
            "#!/bin/bash\n"
            "printf 'Filename Type Size Used Priority\\n' > \"$FAKE_ROOT/proc/swaps\"\n"
            ": > \"$FAKE_ROOT/after-swap\"\n",
        )

    if changed == "partuuid":
        # Replace the fake PARTUUID source when swapoff runs.
        _write_executable(
            bindir / "swapoff",
            "#!/bin/bash\n"
            "printf 'Filename Type Size Used Priority\\n' > \"$FAKE_ROOT/proc/swaps\"\n"
            ": > \"$FAKE_ROOT/after-swap\"\n"
            "value=$(<\"$FAKE_PARTUUID_AFTER\")\n"
            "printf '%s\\n' \"$value\" > \"$FAKE_PARTUUID\"\n",
        )
        (tmp_path / "partuuid-after").write_text("99999999-2222-3333-4444-555555555555\n", encoding="utf-8")

    result = _run(
        tmp_path,
        "begin",
        "/dev/vda9",
        "--expect-partuuid",
        PARTUUID,
        "--expect-size",
        SIZE,
        "--commit",
        root=root,
        bindir=bindir,
    )

    assert result.returncode != 0
    assert "changed after swapoff; no wipe done" in result.stderr
    assert not (logs / "wipefs.log").exists()
    assert "-p -s PART_ENTRY_UUID" in (logs / "blkid.log").read_text(encoding="utf-8")
