"""Systematic allowlist coverage for the dev-host / partition-data-area / tail-swap layout.

Why this exists (live finding 2026-11-05, v1001 reinstall): stage two died with
`sfdisk option(s) are not allowlisted: ['--no-tell-kernel', '-N']`. Dry-run never reaches the root-growth gap
branch (it is guarded by `not dry_run`) and the unit fakes replaced `actions.run`, so the production allowlist
never saw that argv. Two independent nets close that here:

1. a static AST scan: EVERY literal argv in the installer modules is validated, executed or not;
2. a dynamic walk of the real stage-two sequence for the 512 GiB dev-host plan (root 3 -> 160 GiB through the
   gap branch, four tail swaps, MDT_COW btrfs data partition) against a stateful fake whose `run` is
   validated by the production `HostActions._validate`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from debian_install_v2 import installer as installer_module
from debian_install_v2 import layout_policy as lp
from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.disk_layout import GIB

from .argv_guard import validate_argv
from .test_data_area import _planner
from .test_root_growth_crash import START, FakeHost, TARGET

PACKAGE = Path(installer_module.__file__).resolve().parent
SCANNED = ["installer.py", "bootstrap.py", "layout_policy.py", "disk_layout.py", "customscript.py",
           "wizard.py", "notify.py", "failure_notify.py", "host_facts.py"]


def _render(node: ast.expr) -> str:
    """A literal argv element; f-string substitutions become a neutral operand."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values)
    return "x"


def _literal_argvs() -> list[tuple[str, int, list[str]]]:
    found = []
    for name in SCANNED:
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.List) and node.elts):
                continue
            head = node.elts[0]
            if not (isinstance(head, ast.Constant) and isinstance(head.value, str)
                    and head.value.startswith("/") and head.value.count("/") >= 2):
                continue
            if any(isinstance(e, ast.Starred) for e in node.elts):
                continue  # argv assembled from spliced lists is covered by the dynamic walk below
            found.append((name, node.lineno, [_render(e) for e in node.elts]))
    return found


def test_every_literal_installer_argv_passes_the_production_allowlist():
    argvs = _literal_argvs()
    assert len(argvs) > 100, "the scan found suspiciously few argvs; the scanner is broken"
    failures = []
    for name, line, argv in argvs:
        try:
            HostActions._validate(argv)
        except ActionError as exc:
            failures.append(f"{name}:{line} {argv} -> {exc}")
    assert not failures, "argv(s) the production allowlist would refuse:\n" + "\n".join(failures)


def test_the_scan_sees_the_root_growth_and_data_area_argvs():
    flat = [argv for _n, _l, argv in _literal_argvs()]
    assert any(a[0].endswith("sfdisk") and "-N" in a and "--no-tell-kernel" in a for a in flat)
    assert any(a[0].endswith("mkswap") for a in flat) and any(a[0].endswith("partx") for a in flat)


@pytest.mark.parametrize("argv", [
    ["/usr/sbin/sfdisk", "-f", "/dev/vda"],
    ["/usr/sbin/mkswap", "-f", "/dev/vda4"],
    ["/usr/sbin/mkswap", "--check", "/dev/vda4"],
    ["/usr/sbin/resize2fs", "-f", "/dev/vda3"],
    ["/usr/sbin/blkid", "-g"],
    ["/usr/bin/dd", "if=/dev/zero", "of=/dev/vda", "bs=1M", "oflag=direct", "status=none"],
    ["/usr/bin/dd", "--help"],
])
def test_previously_unchecked_flags_are_now_refused(argv):
    with pytest.raises(ActionError):
        HostActions._validate(argv)


@pytest.mark.parametrize("argv", [
    ["/usr/sbin/mkswap", "-L", "vbpub-swap1", "/dev/vda4"],
    ["/usr/sbin/resize2fs", "/dev/vda3"],
    ["/usr/sbin/resize2fs", "-P", "/dev/vda3"],
    ["/usr/sbin/blkid", "-s", "PARTUUID", "-o", "value", "/dev/vda4"],
    ["/usr/sbin/blkid", "-p", "-s", "TYPE", "-o", "value", "/dev/vda8"],
    ["/usr/bin/dd", "if=/dev/zero", "of=/dev/vda9", "bs=1M", "count=1", "conv=fsync"],
])
def test_the_flags_the_installer_uses_stay_accepted(argv):
    HostActions._validate(argv)


# --- dynamic walk -----------------------------------------------------------------------------------------------

class LayoutHost(FakeHost):
    """FakeHost (validated run, three-size root model) plus the swap / data-area tools of stage two."""

    def __init__(self) -> None:
        super().__init__()
        self.formatted: dict[str, str] = {}
        self.swapped: list[str] = []
        self.active: list[str] = []
        self.mounted: list[str] = []
        self.labels: dict[str, str] = {}

    def _effect(self, argv, input):
        tool = argv[0].rsplit("/", 1)[-1]
        if tool == "blkid":
            device = argv[-1]
            if "-t" in argv:
                found = [d for d, label in self.labels.items() if f"LABEL={label}" == argv[argv.index("-t") + 1]]
                if not found:
                    raise ActionError("action failed (2): blkid")
                return "".join(f"{d}\n" for d in found)
            if "PARTUUID" in argv:
                return f"uuid-{device.rsplit('/', 1)[-1]}\n"
            if "UUID" in argv:
                return "1234-abcd\n"
            if "TYPE" in argv:
                if device in self.formatted:
                    return self.formatted[device] + "\n"
                raise ActionError("action failed (2): blkid")
        if tool == "mkswap":
            self.swapped.append(argv[-1])
            return ""
        if tool == "swapon":
            if "--show=NAME,TYPE,SIZE,PRIO" in argv:
                return "".join(f"{d} partition 4G -2\n" for d in self.active)
            self.active.append(argv[-1])
            return ""
        if tool == "mkfs.btrfs":
            self.formatted[argv[-1]] = "btrfs"
            self.labels[argv[-1]] = argv[argv.index("-L") + 1]
            return ""
        if tool == "mount":
            self.mounted.append(argv[-1])
            return ""
        if tool == "findmnt":
            if argv[-1] in self.mounted:
                return f"/dev/vda8 {argv[-1]} btrfs\n"
            raise ActionError("action failed (1): findmnt")
        if tool in {"systemctl", "udevadm", "mkdir"}:
            return ""
        return super()._effect(argv, input)


def test_stage_two_layout_walk_validates_every_argv_and_ends_in_the_planned_layout(tmp_path):
    host = LayoutHost()
    installer = _planner(tmp_path)  # 512 GiB, dev-host, data_area=partition, 4 x 4 GiB tail swaps
    swap, root = installer._plan_swap_partitions()
    assert root == TARGET  # 160 GiB root
    planned = installer._parse_partition_entries(installer._write_sfdisk_plan(swap, root))
    installer.actions.dry_run = False
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    host.fs = host.table * 512
    host.table = host.kernel = 3 * GIB // 512  # the cloud image's small root
    host.fs = host.table * 512

    def live(*_a):
        entries = {n: dict(e) for n, e in planned.items()}
        entries[3]["size"] = str(host.table)
        return entries

    installer._live_partition_entries = live
    installer._disk_facts = lambda: (512 * GIB // 512, int(planned[3]["start"]), host.table)
    installer._root_filesystem_facts = lambda: (4096, 100_000)
    installer._packages = lambda *_a: None
    writes: dict[str, str] = {}
    installer._read_fstab_lines = lambda: writes.get("/etc/fstab", "").splitlines()
    installer._plan_swap_partitions = lambda **_k: (swap, root)
    installer._resolve_swap_plan = lambda **_k: (swap, root, False)
    installer.root_partition_path = "/dev/vda3"
    installer.actions.write_file = lambda path, content, mode=0o644: writes.__setitem__(path, content)
    installer.actions.run = host.run  # validates through the production allowlist

    class State:
        def load(self):
            return {"steps": host.steps}

        def mark_step(self, name, status, detail=""):
            host.steps[name] = {"status": status, "detail": detail}

    installer.state = State()
    import debian_install_v2.installer as mod
    orig_which = mod.shutil.which
    mod.shutil.which = lambda _n: "/usr/bin/growpart"
    try:
        installer._grow_root_to_plan()
        installer._activate_swap_partitions()
        installer._configure_data_area()
        # _health_gate_swap_devices reads the real /etc/fstab; its argv (swapon --show, blkid) are covered by the scan
    finally:
        mod.shutil.which = orig_which

    tools = [c[0].rsplit("/", 1)[-1] for c in host.calls]
    assert tools.index("sfdisk") < tools.index("partx") < tools.index("resize2fs")
    assert tools.count("mkswap") == 4 and tools.count("swapon") >= 4
    assert host.swapped == [f"/dev/vda{n}" for n in (4, 5, 6, 7)]
    assert host.formatted == {"/dev/vda8": "btrfs"} and host.labels == {"/dev/vda8": lp.COW_LABEL}
    assert host.mounted == ["/srv/cow"]
    assert host.table == host.kernel == TARGET
    fstab = writes["/etc/fstab"]
    assert "UUID=1234-abcd /srv/cow btrfs noatime,user_subvol_rm_allowed,nofail 0 0" in fstab
    assert fstab.count(" swap ") == 4
    for call in host.calls:  # belt and braces: nothing slipped past FakeHost.run
        validate_argv(call)
