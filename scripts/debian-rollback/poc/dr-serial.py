#!/usr/bin/env python3
"""PoC driver: scripts a guest through its serial socket (QEMU chardev, server mode).

  dr-serial.py SOCKET SCENARIO [--log FILE]

SCENARIO is one of: boot-check, s2-s3, kernel-upgrade, s5. Scenarios are NOT idempotent: s2-s3 ends with the earlier
state as the default and both s2-s3 and kernel-upgrade require "running state == default" at the start, so run each
of them in its own fresh guest (new overlay on the installed base); boot-check may share a guest with either. The
apt hook's dedupe window (dr-state, default 900 s) is another reason: a second apt run of the same state within the
window takes no snapshot, so scenarios that count new snapshots need a guest without a recent one. The guest must be running (dr-qemu.sh start) with a
login prompt on ttyS0 and root password "root" (the PoC installer default). The log is a plain transcript
and is the evidence; the PASS/FAIL lines are a convenience. Exit status is non-zero when any check fails.

How output is read (the echoed command line is part of what comes back on a tty, so nothing may be matched
against it):
- a command ends with a marker: `CMD; echo __DR_END_$?__`; the reader waits for `__DR_END_<digits>__`, which does
  not occur in the echo (that one has `$?`);
- values are printed as `echo TAG:$(...)` and read with tag()/tags(), which accept only lines whose value has no
  `$`, backtick or parenthesis, so the echoed (and the wrapped) command line never matches;
- every reboot is proven with /proc/sys/kernel/random/boot_id (before/after must differ) and waits for a fresh
  `login:` prompt instead of probing the old shell.
Standard library only. Host-side: it touches nothing but the socket.
"""
import os
import re
import select
import socket
import sys
import time

END_RE = re.compile(rb"__DR_END_(\d+)__")
FAILS = []


class Guest:
    def __init__(self, path, log):
        self.path, self.log = path, log
        self.qmp_path = None  # the state dir's qmp.sock, set by main()
        self.sock = None
        self.buf = b""

    def connect(self, timeout=600):
        deadline = time.monotonic() + timeout
        while True:
            try:
                s = socket.socket(socket.AF_UNIX)
                s.connect(self.path)
                s.setblocking(False)
                self.sock = s
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)

    def _pump(self, wait=0.5):
        try:
            r, _, _ = select.select([self.sock], [], [], wait)
        except (OSError, ValueError):
            return False
        if not r:
            return True
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            data = b""
        if not data:  # QEMU closed the chardev: reconnect
            self.sock.close()
            self.connect()
            return True
        self.buf += data
        self.log.write(data.decode("utf-8", "replace"))
        self.log.flush()
        return True

    def send(self, text):
        self.sock.sendall(text.encode())

    def expect(self, pattern, timeout):
        rx = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern if isinstance(pattern, bytes) else pattern.encode())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            m = rx.search(self.buf)
            if m:
                out = self.buf[: m.end()]
                self.buf = self.buf[m.end():]
                return out, m
            self._pump()
        raise TimeoutError(f"timeout {timeout}s waiting for {pattern!r}; tail: {self.buf[-300:]!r}")

    def _shell_answers(self, wait):
        """True when a shell answers a probe within `wait` seconds (the echoed probe has `$((20+22))`, only the
        shell's answer has 42)."""
        self.buf = b""
        self.send("echo __DR_READY_$((20+22))__\n")
        try:
            self.expect(rb"__DR_READY_42__", wait)
            return True
        except TimeoutError:
            return False

    def _do_login(self):
        """Type the credentials at an existing login prompt, then probe until the shell answers (login(1) discards
        input typed while it sets the session up, which is slow under TCG)."""
        self.send("root\n")
        self.expect(rb"[Pp]assword: ?", 60)
        self.send("root\n")
        deadline = time.monotonic() + 400
        while time.monotonic() < deadline:
            if self._shell_answers(10):
                return
        raise TimeoutError("no working shell after login")

    def login(self, timeout=900):
        if self._shell_answers(6):  # a previous scenario may have left a root shell on the line
            return
        self.send("\n")
        self.expect(rb"login: ?", timeout)
        self._do_login()

    def run(self, cmd, timeout=300):
        self.buf = b""
        self.send(f"{cmd}; echo __DR_END_$?__\n")
        out, m = self.expect(END_RE, timeout)
        rc = int(m.group(1))
        text = END_RE.sub(b"", out).decode("utf-8", "replace")
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\r\n", "\n").replace("\r", "\n")
        return rc, text

    def boot_id(self):
        _, out = self.run("echo BOOTID:$(cat /proc/sys/kernel/random/boot_id)")
        return tag(out, "BOOTID")

    def reboot(self, timeout=1200):
        """Reboot and return the new boot id; raises unless the guest really came back as a new boot."""
        old = self.boot_id()
        if not old:
            raise RuntimeError("cannot read the boot id before the reboot; not rebooting blindly")
        self.send("reboot\n")
        self.buf = b""
        self.expect(rb"login: ?", timeout)  # a prompt of the NEW boot: shutdown output contains none
        self._do_login()
        new = self.boot_id()
        if not old or not new or old == new:
            raise RuntimeError(f"reboot not proven: boot_id before={old!r} after={new!r}")
        return new


def qmp_command(path, command, timeout=15):
    """One QMP command on the guest's unix socket (the state dir's qmp.sock); returns the parsed reply."""
    import json
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(path)
    f = s.makefile("rw")
    try:
        json.loads(f.readline())  # greeting
        f.write(json.dumps({"execute": "qmp_capabilities"}) + "\n")
        f.flush()
        json.loads(f.readline())
        f.write(json.dumps({"execute": command}) + "\n")
        f.flush()
        while True:  # events may precede the reply
            msg = json.loads(f.readline())
            if "return" in msg or "error" in msg:
                return msg
    finally:
        s.close()


def tags(out, name):
    """All values printed as `NAME:value`. The echoed command line (also when wrapped by the tty) has `$`, a backtick
    or parentheses in it and is skipped, so it can never satisfy a check."""
    vals = []
    for v in re.findall(rf"^{re.escape(name)}:(.*?)\s*$", out, re.M):
        if v and not re.search(r"[$`()]", v):
            vals.append(v)
    return vals


def tag(out, name):
    v = tags(out, name)
    return v[-1] if v else None


def say(log, line):
    log.write(f"\n##### {line}\n")
    log.flush()
    print(line, flush=True)


def check(log, label, ok, detail=""):
    if not ok:
        FAILS.append(label)
    say(log, f"{'PASS' if ok else 'FAIL'}: {label} {detail}".rstrip())
    return ok


def state_list(g):
    """Names of the states, as dr-state sees them."""
    _, out = g.run("echo STATES:$(dr-state list | awk '{print $1}' | tr '\\n' ,)")
    v = tag(out, "STATES")
    return [s for s in (v or "").split(",") if s]


def default_state(g):
    """The state `dr-state list` marks as default (what a plain reboot boots)."""
    _, out = g.run("echo DEF:$(dr-state list | awk '/\\(default\\)/{print $1}')")
    return tag(out, "DEF")


def running_state(out_cmdline):
    m = re.search(r"rootflags=subvol=(@os-\S+)", out_cmdline or "")
    return m.group(1) if m else None


MOUNT_PATHS_OUTSIDE = ["/home", "/root", "/srv", "/var/www", "/var/log", "/var/lib/docker", "/var/lib/containers",
                       "/var/lib/postgresql", "/var/lib/mysql", "/usr/local", "/.top"]


def scenario_boot_check(g, log):
    g.login()
    paths = " ".join(MOUNT_PATHS_OUTSIDE + ["/opt", "/etc"])
    rc, out = g.run("echo KERNEL:$(uname -r); echo CMDLINE:$(cat /proc/cmdline); echo ROOTSRC:$(findmnt -no SOURCE /); "
                    "echo SYSTEMD:$(systemctl is-system-running); "
                    "echo IP:$(ip -color=never -4 -br addr show scope global | head -1 | tr -s ' ' _); "
                    "echo DNS:$(getent hosts deb.debian.org | head -1 | tr -s ' ' _); "
                    "echo ESPFILES:$(ls /boot/efi/EFI/dr | tr '\\n' ,); "
                    f"for m in {paths}; do if mountpoint -q $m; then echo MP:$m; else echo NOTMP:$m; fi; done")
    cmdline = tag(out, "CMDLINE") or ""
    check(log, "booted a state subvolume (from /proc/cmdline)", running_state(cmdline) is not None, cmdline[:100])
    check(log, "root is that subvolume", (tag(out, "ROOTSRC") or "").endswith(f"[/{running_state(cmdline)}]"), tag(out, "ROOTSRC") or "")
    check(log, "systemd reports running", tag(out, "SYSTEMD") == "running", tag(out, "SYSTEMD") or "")
    check(log, "guest has a global IPv4 address", re.match(r"eth0_UP_\d+\.\d+\.\d+\.\d+/", tag(out, "IP") or "") is not None, tag(out, "IP") or "")
    check(log, "name resolution works", re.match(r"[0-9a-f:.]+_+.*deb\.debian\.org", tag(out, "DNS") or "") is not None, tag(out, "DNS") or "")
    check(log, "ESP holds grub.cfg and grubenv", {"grub.cfg", "grubenv"} <= set((tag(out, "ESPFILES") or "").split(",")))
    mp = set(tags(out, "MP"))
    notmp = set(tags(out, "NOTMP"))
    check(log, "outside paths (D18) are separate mounts", set(MOUNT_PATHS_OUTSIDE) <= mp, str(sorted(set(MOUNT_PATHS_OUTSIDE) - mp)))
    check(log, "/opt and /etc are inside the state (D18)", {"/opt", "/etc"} <= notmp)


def scenario_s2_s3(g, log):
    """S2 apt install snapshots first; S3 boot the earlier state once; S10 /etc rolls back; the one-shot returns to
    the default on the next reboot; S4 set-default keeps the earlier state."""
    g.login()
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    state0 = running_state(tag(out, "CMDLINE"))
    check(log, "running state known", state0 is not None, str(state0))
    if not state0:
        return
    if not check(log, "the running state is the default (precondition: a plain reboot returns here)",
                 default_state(g) == state0, f"running={state0} default={default_state(g)}"):
        return
    before = state_list(g)
    g.run("mkdir -p /var/lib/docker /srv; echo m1 > /home/marker; echo m2 > /srv/marker; "
          "echo m3 > /var/lib/docker/marker; echo etc1 > /etc/dr-etc-marker")
    _, out = g.run("echo MK:$(cat /home/marker)_$(cat /srv/marker)_$(cat /var/lib/docker/marker)_$(cat /etc/dr-etc-marker)")
    check(log, "markers written", tag(out, "MK") == "m1_m2_m3_etc1", str(tag(out, "MK")))
    # S2
    _, out = g.run("apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hello "
                   ">/var/log/dr-apt.log 2>&1; echo APTRC:$?; echo HELLO:$(command -v hello || echo none)", 900)
    check(log, "apt install hello succeeded", tag(out, "APTRC") == "0" and tag(out, "HELLO") == "/usr/bin/hello",
          f"rc={tag(out, 'APTRC')} hello={tag(out, 'HELLO')}")
    after = state_list(g)
    new = [s for s in after if s not in before and s.startswith("@os-pre-")]
    check(log, "apt hook created exactly one new snapshot", len(new) == 1, str(new))
    _, out = g.run("echo MENU:$(grep -c '^menuentry' /boot/efi/EFI/dr/grub.cfg)")
    check(log, "menu has one entry per state", tag(out, "MENU") == str(len(after)), f"menu={tag(out, 'MENU')} states={after}")
    if len(new) != 1:
        return
    pre = new[0]
    g.run("echo etc2 >> /etc/dr-etc-marker; echo m1b >> /home/marker")
    # S3
    _, out = g.run(f"dr-state boot-once {pre}; echo BORC:$?")
    check(log, "boot-once accepted", tag(out, "BORC") == "0", str(tag(out, "BORC")))
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001 - a reboot that cannot be proven fails the scenario
        check(log, "reboot into the earlier state", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline); echo HELLO:$(command -v hello || echo none); "
                   "echo HOME:$(tr '\\n' + < /home/marker); echo SRV:$(cat /srv/marker); echo DOCKER:$(cat /var/lib/docker/marker); "
                   "echo ETC:$(tr '\\n' + < /etc/dr-etc-marker)")
    check(log, "booted the earlier state", running_state(tag(out, "CMDLINE")) == pre, str(running_state(tag(out, "CMDLINE"))))
    check(log, "package absent in the earlier state", tag(out, "HELLO") == "none", str(tag(out, "HELLO")))
    check(log, "/home data written after the snapshot survives", tag(out, "HOME") == "m1+m1b+", str(tag(out, "HOME")))
    check(log, "service data survives (/srv, /var/lib/docker)", tag(out, "SRV") == "m2" and tag(out, "DOCKER") == "m3")
    check(log, "/etc is the earlier one (S10)", tag(out, "ETC") == "etc1+", str(tag(out, "ETC")))
    # one-shot must be consumed: the next plain reboot returns to the default state
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "second reboot", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "the one-shot returned to the default on the next reboot", running_state(tag(out, "CMDLINE")) == state0,
          str(running_state(tag(out, "CMDLINE"))))
    # S4
    _, out = g.run(f"dr-state set-default {pre}; echo SDRC:$?")
    check(log, "set-default accepted", tag(out, "SDRC") == "0")
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot after set-default", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "earlier state is the default after set-default (S4)", running_state(tag(out, "CMDLINE")) == pre,
          str(running_state(tag(out, "CMDLINE"))))


UPDATES_SOURCES = ("echo 'deb http://deb.debian.org/debian trixie-updates main' > /etc/apt/sources.list.d/dr-updates.list; "
                   "echo 'deb http://deb.debian.org/debian-security trixie-security main' >> /etc/apt/sources.list.d/dr-updates.list")
BACKPORTS_SOURCES = UPDATES_SOURCES + ("; echo 'deb http://deb.debian.org/debian trixie-backports main' "
                                       ">> /etc/apt/sources.list.d/dr-updates.list")
DIST_UPGRADE = ("DEBIAN_FRONTEND=noninteractive apt-get -y -qq -o Dpkg::Options::=--force-confold "
                "-o Dpkg::Options::=--force-confdef dist-upgrade >/var/log/dr-upgrade.log 2>&1")
OFFER_UPGRADABLE = "apt list --upgradable 2>/dev/null | grep -c '^linux-image'"
BACKPORTS_INSTALL = ("DEBIAN_FRONTEND=noninteractive apt-get -y -qq -o Dpkg::Options::=--force-confold "
                     "-o Dpkg::Options::=--force-confdef install -t trixie-backports linux-image-amd64 "
                     ">/var/log/dr-upgrade.log 2>&1")
OFFER_BACKPORTS = "apt-get -s -t trixie-backports install linux-image-amd64 2>/dev/null | grep -c '^Inst linux-image'"


def scenario_kernel_upgrade(g, log):
    """A kernel change: apt dist-upgrade brings a newer kernel into the running state. The menu must show it for
    that state and keep the old kernel for the snapshot; boot new, boot the earlier state once (old kernel, its
    modules), and check that the one-shot returns to the default on the next reboot."""
    _kernel_flow(g, log, UPDATES_SOURCES, OFFER_UPGRADABLE, DIST_UPGRADE)


def scenario_kernel_backports(g, log):
    """S11: the same flow with the kernel from trixie-backports instead of the stable update."""
    _kernel_flow(g, log, BACKPORTS_SOURCES, OFFER_BACKPORTS, BACKPORTS_INSTALL)


def _kernel_flow(g, log, sources_cmd, offer_cmd, install_cmd):
    g.login()
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline); echo KERNEL:$(uname -r)")
    state0 = running_state(tag(out, "CMDLINE"))
    old = tag(out, "KERNEL")
    check(log, "running state and kernel recorded", bool(state0 and old), f"{state0} {old}")
    if not (state0 and old):
        return
    if not check(log, "the running state is the default (precondition: a plain reboot returns here)",
                 default_state(g) == state0, f"running={state0} default={default_state(g)}"):
        return
    efi_probe = ("echo EFIFILES:$(ls -1 /boot/efi/EFI | tr '\\n' ,)_$(ls -1 /boot/efi/EFI/dr | tr '\\n' ,)"
                 "_$(sha256sum /boot/efi/EFI/BOOT/BOOTX64.EFI | cut -c1-16)")
    _, out = g.run(efi_probe)
    efi_before = tag(out, "EFIFILES")
    # the disk of run 2a was installed without the updates and security suites (the installer now adds them)
    g.run(sources_cmd)
    _, out = g.run(f"apt-get update -qq >/dev/null 2>&1; echo UPDRC:$?; echo OFFER:$({offer_cmd})", 900)
    offer = tag(out, "OFFER")
    if not check(log, "a newer kernel package is on offer", offer is not None and offer.isdigit() and int(offer) > 0,
                 f"updrc={tag(out, 'UPDRC')} offer={offer}"):
        return
    before = state_list(g)
    _, out = g.run(f"{install_cmd}; echo UPGRC:$?; "
                   "echo ARCHKB:$(du -sk /var/cache/apt/archives | cut -f1); echo KNEW:$(readlink /vmlinuz); "
                   "echo BOOTFILES:$(ls -1 /boot | grep -E '^(vmlinuz|initrd.img)-' | tr '\\n' ,); "
                   "echo MENU:$(grep -o 'Debian @os-[^ ]* (kernel [^)]*)' /boot/efi/EFI/dr/grub.cfg | tr ' ' _ | tr -d '()' | tr '\\n' ,)", 5400)
    check(log, "dist-upgrade succeeded", tag(out, "UPGRC") == "0", f"rc={tag(out, 'UPGRC')} downloaded_kb={tag(out, 'ARCHKB')}")
    _, efi_out = g.run(efi_probe)
    check(log, "ESP boot files untouched by the upgrade (no grub-install side effect)",
          bool(efi_before) and tag(efi_out, "EFIFILES") == efi_before, f"before={efi_before} after={tag(efi_out, 'EFIFILES')}")
    knew = tag(out, "KNEW") or ""
    new = knew.rsplit("vmlinuz-", 1)[-1] if "vmlinuz-" in knew else ""
    check(log, "a newer kernel is installed (/vmlinuz points at it)", bool(new) and new != old, f"old={old} new={new}")
    files = (tag(out, "BOOTFILES") or "").split(",")
    check(log, "old and new kernel and initrd are both in /boot",
          all(f in files for f in (f"vmlinuz-{old}", f"initrd.img-{old}", f"vmlinuz-{new}", f"initrd.img-{new}")), str(files))
    menu = dict(re.findall(r"Debian_(@os-[^_,]+(?:-[^_,]+)*)_kernel_([^_,]+)", tag(out, "MENU") or ""))
    check(log, "menu: the running state names the NEW kernel", menu.get(state0) == new, str(menu))
    after = state_list(g)
    pre = [s for s in after if s not in before and s.startswith("@os-pre-")]
    check(log, "apt hook created exactly one snapshot for the upgrade", len(pre) == 1, str(pre))
    if len(pre) == 1:
        check(log, "menu: that snapshot keeps the OLD kernel", menu.get(pre[0]) == old, str(menu))
    if not (new and len(pre) == 1):
        return
    pre = pre[0]
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot into the new kernel", False, repr(exc))
        return
    _, out = g.run("echo KERNEL:$(uname -r); echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "default boot runs the new kernel in the running state",
          tag(out, "KERNEL") == new and running_state(tag(out, "CMDLINE")) == state0, f"{tag(out, 'KERNEL')} {running_state(tag(out, 'CMDLINE'))}")
    _, out = g.run(f"dr-state boot-once {pre}; echo BORC:$?")
    check(log, "boot-once accepted", tag(out, "BORC") == "0")
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot into the earlier state", False, repr(exc))
        return
    _, out = g.run("echo KERNEL:$(uname -r); echo CMDLINE:$(cat /proc/cmdline); echo MODDIRS:$(ls -1 /lib/modules | tr '\\n' ,); "
                   "modprobe loop; echo MODRC:$?; echo LOOP:$(lsmod | grep -c '^loop')")
    check(log, "earlier state boots its OLD kernel", tag(out, "KERNEL") == old and running_state(tag(out, "CMDLINE")) == pre,
          f"{tag(out, 'KERNEL')} {running_state(tag(out, 'CMDLINE'))}")
    check(log, "earlier state's modules match its kernel (modprobe loads a module)", tag(out, "MODRC") == "0" and (tag(out, "LOOP") or "0") != "0",
          f"modrc={tag(out, 'MODRC')} loop={tag(out, 'LOOP')} moddirs={tag(out, 'MODDIRS')}")
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "second reboot", False, repr(exc))
        return
    _, out = g.run("echo KERNEL:$(uname -r); echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "the one-shot returned to the default (new kernel, running state) on the next reboot",
          tag(out, "KERNEL") == new and running_state(tag(out, "CMDLINE")) == state0, f"{tag(out, 'KERNEL')} {running_state(tag(out, 'CMDLINE'))}")


def _precondition(g, log):
    """(state0, kernel) of a guest whose running state is the default, else None (a check was recorded)."""
    g.login()
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline); echo KERNEL:$(uname -r)")
    state0 = running_state(tag(out, "CMDLINE"))
    kernel = tag(out, "KERNEL")
    if not (state0 and kernel):
        check(log, "running state and kernel recorded", False, f"{state0} {kernel}")
        return None
    if not check(log, "the running state is the default (precondition: a plain reboot returns here)",
                 default_state(g) == state0, f"running={state0}"):
        return None
    return state0, kernel


def _update_dr_state(g, log, needle="subvol_parent_uuid", what="parent-aware dedupe window"):
    """Install the dr-state from the seed ISO (attached as read-only media at /dev/sr0) over the one on the disk, so a
    scenario can test the current tool without a 21-minute reinstall. Returns True when the new version is in place
    (`needle` is a name that only the new version contains)."""
    _, out = g.run("mkdir -p /mnt/seed; mount -o ro /dev/sr0 /mnt/seed; echo MNTRC:$?; "
                   "install -m 0755 /mnt/seed/poc/dr-state /usr/local/sbin/dr-state; echo INSRC:$?; umount /mnt/seed; "
                   f"echo NEWFEATURE:$(grep -c '{needle}' /usr/local/sbin/dr-state)")
    return check(log, f"dr-state updated from the seed medium ({what} present)",
                 tag(out, "MNTRC") == "0" and tag(out, "INSRC") == "0" and (tag(out, "NEWFEATURE") or "0") not in ("0", ""),
                 f"mnt={tag(out, 'MNTRC')} install={tag(out, 'INSRC')} feature={tag(out, 'NEWFEATURE')}")


def scenario_s8(g, log):
    """S8: many states and pruning. 30 snapshots, a boot with a 31-entry menu, `prune --keep 5` keeps the newest five,
    and a one-shot target outside the newest N is protected and still boots."""
    pc = _precondition(g, log)
    if not pc or not _update_dr_state(g, log, "cmd_prune", "prune command"):
        return
    state0, _ = pc
    before = state_list(g)
    _, out = g.run("echo USEDKB:$(df -k --output=used / | tail -1 | tr -d ' ')")
    used0 = tag(out, "USEDKB")
    _, out = g.run("fails=0; for i in $(seq 1 30); do dr-state snapshot --reason s8 >/dev/null 2>&1 || fails=$((fails+1)); done; "
                   "echo SNAPFAILS:$fails", 3000)
    states = state_list(g)
    made = sorted(s for s in states if s not in before and s.startswith("@os-pre-"))
    check(log, "30 snapshots were created", tag(out, "SNAPFAILS") == "0" and len(made) == 30, f"fails={tag(out, 'SNAPFAILS')} made={len(made)}")
    _, out = g.run("echo MENU:$(grep -c '^menuentry' /boot/efi/EFI/dr/grub.cfg); echo USEDKB:$(df -k --output=used / | tail -1 | tr -d ' ')")
    check(log, "the menu lists every state", tag(out, "MENU") == str(len(states)), f"menu={tag(out, 'MENU')} states={len(states)}")
    say(log, f"S8 disk use of / : {used0} KB before, {tag(out, 'USEDKB')} KB with 30 snapshots (btrfs shares the extents)")
    if len(made) != 30:
        return
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot with a 31-entry menu", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "the default entry boots with 31 entries in the menu", running_state(tag(out, "CMDLINE")) == state0, str(running_state(tag(out, "CMDLINE"))))
    # prune: keep the newest 5
    _, out = g.run("echo PRUNED:$(dr-state prune --keep 5 | grep -c '^deleted')", 900)
    check(log, "prune --keep 5 deleted 25 snapshots", tag(out, "PRUNED") == "25", str(tag(out, "PRUNED")))
    left = state_list(g)
    check(log, "what is left: the running state and exactly the newest five", sorted(left) == sorted([state0] + made[-5:]), str(left))
    _, out = g.run("echo MENU:$(grep -c '^menuentry' /boot/efi/EFI/dr/grub.cfg); echo USEDKB:$(df -k --output=used / | tail -1 | tr -d ' ')")
    check(log, "the menu follows the pruning", tag(out, "MENU") == "6", str(tag(out, "MENU")))
    say(log, f"S8 disk use of / after pruning: {tag(out, 'USEDKB')} KB")
    # protection: a one-shot target outside the newest N must survive a prune
    target = made[-5]
    _, out = g.run(f"dr-state boot-once {target}; echo BORC:$?; echo PRUNED:$(dr-state prune --keep 2 | grep -c '^deleted')")
    check(log, "prune --keep 2 deleted the unprotected two", tag(out, "BORC") == "0" and tag(out, "PRUNED") == "2", f"{tag(out, 'BORC')} {tag(out, 'PRUNED')}")
    left = state_list(g)
    check(log, "the one-shot target survived next to the newest two", sorted(left) == sorted([state0, target] + made[-2:]), str(left))
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot into the protected target", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "the protected snapshot still boots", running_state(tag(out, "CMDLINE")) == target, str(running_state(tag(out, "CMDLINE"))))


def scenario_s6(g, log):
    """S6: the kernel package an earlier state needs is removed in the running state (what an apt autoremove of old
    kernels does). Kernels live inside each state, so the earlier state must still boot its own kernel."""
    pc = _precondition(g, log)
    if not pc:
        return
    state0, old = pc
    g.run(UPDATES_SOURCES)
    g.run("apt-get update -qq >/dev/null 2>&1")
    before = state_list(g)
    _, out = g.run(f"{DIST_UPGRADE}; echo UPGRC:$?; echo KNEW:$(readlink /vmlinuz)", 5400)
    knew = tag(out, "KNEW") or ""
    new = knew.rsplit("vmlinuz-", 1)[-1] if "vmlinuz-" in knew else ""
    check(log, "dist-upgrade brought a newer kernel", tag(out, "UPGRC") == "0" and bool(new) and new != old, f"old={old} new={new}")
    pre = [s for s in state_list(g) if s not in before and s.startswith("@os-pre-")]
    if not (new and check(log, "the upgrade made exactly one snapshot (it holds the old kernel)", len(pre) == 1, str(pre))):
        return
    pre = pre[0]
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot into the new kernel", False, repr(exc))
        return
    _, out = g.run("echo KERNEL:$(uname -r)")
    check(log, "running the new kernel before removing the old one", tag(out, "KERNEL") == new, str(tag(out, "KERNEL")))
    _, out = g.run(f"DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq linux-image-{old} >/var/log/dr-s6.log 2>&1; echo RMRC:$?; "
                   f"echo OLDFILES:$(ls -1 /boot | grep -c -- '-{old}$'); "
                   "echo MENU:$(grep -o 'Debian @os-[^ ]* (kernel [^)]*)' /boot/efi/EFI/dr/grub.cfg | tr ' ' _ | tr -d '()' | tr '\\n' ,)", 900)
    check(log, "the old kernel package was removed in the running state", tag(out, "RMRC") == "0" and tag(out, "OLDFILES") == "0",
          f"rc={tag(out, 'RMRC')} oldfiles={tag(out, 'OLDFILES')}")
    menu = dict(re.findall(r"Debian_(@os-[^_,]+(?:-[^_,]+)*)_kernel_([^_,]+)", tag(out, "MENU") or ""))
    check(log, "menu: the running state names the new kernel, the earlier state still names the old one",
          menu.get(state0) == new and menu.get(pre) == old, str(menu))
    _, out = g.run(f"dr-state boot-once {pre}; echo BORC:$?")
    check(log, "boot-once accepted", tag(out, "BORC") == "0")
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot into the earlier state", False, repr(exc))
        return
    _, out = g.run("echo KERNEL:$(uname -r); echo CMDLINE:$(cat /proc/cmdline); modprobe loop; echo MODRC:$?")
    check(log, "the earlier state still boots its old kernel after the package was removed elsewhere",
          tag(out, "KERNEL") == old and running_state(tag(out, "CMDLINE")) == pre, f"{tag(out, 'KERNEL')} {running_state(tag(out, 'CMDLINE'))}")
    check(log, "and its modules load", tag(out, "MODRC") == "0")


def scenario_s7(g, log):
    """S7: an unattended upgrade takes a snapshot too. unattended-upgrade drives dpkg through libapt, which runs the
    DPkg::Pre-Invoke hook; this checks it instead of assuming it."""
    pc = _precondition(g, log)
    if not pc or not _update_dr_state(g, log):
        return
    g.run(UPDATES_SOURCES)
    g.run("apt-get update -qq >/dev/null 2>&1")
    # DR_STATE=none: no snapshot for this install, so the dedupe window is empty when the unattended run starts
    _, out = g.run("DR_STATE=none DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unattended-upgrades "
                   ">/var/log/dr-s7-install.log 2>&1; echo INSRC:$?", 900)
    check(log, "unattended-upgrades installed", tag(out, "INSRC") == "0", str(tag(out, "INSRC")))
    before = state_list(g)
    # a long window: under TCG an unattended run can outlast the default 900 s, which would split it in two snapshots
    _, out = g.run("DR_SNAPSHOT_MIN_INTERVAL=7200 unattended-upgrade -v >/var/log/dr-s7.log 2>&1; echo UURC:$?; "
                   "echo UPGRADED:$(grep -c 'Packages that will be upgraded' /var/log/unattended-upgrades/unattended-upgrades.log); "
                   "echo NOTHING:$(grep -c 'No packages found that can be upgraded unattended' /var/log/unattended-upgrades/unattended-upgrades.log)", 5400)
    after = state_list(g)
    new = [s for s in after if s not in before and s.startswith("@os-pre-")]
    check(log, "the unattended run upgraded something (otherwise this scenario proves nothing)",
          tag(out, "UURC") == "0" and (tag(out, "UPGRADED") or "0") != "0", f"rc={tag(out, 'UURC')} upgraded={tag(out, 'UPGRADED')} nothing={tag(out, 'NOTHING')}")
    check(log, "the unattended run took exactly one snapshot first (dpkg runs many times, the window dedupes)", len(new) == 1, f"{len(new)}: {new}")


def scenario_s9(g, log):
    """S9: the root filesystem is full before an apt run. Either the snapshot step fails clearly and apt changes
    nothing, or the install completes; never a half state. Afterwards the space is freed and apt works again."""
    pc = _precondition(g, log)
    if not pc:
        return
    state0, _ = pc
    before = state_list(g)
    _, out = g.run("i=0; while fallocate -l 256M /srv/fill.$i 2>/dev/null; do i=$((i+1)); done; "
                   "j=0; while fallocate -l 4M /srv/fillb.$j 2>/dev/null; do j=$((j+1)); done; "
                   "echo FILLED:big$i-small$j; echo AVAILKB:$(df -k --output=avail /srv | tail -1 | tr -d ' ')", 1800)
    check(log, "the filesystem is full", (tag(out, "AVAILKB") or "999999999").isdigit() and int(tag(out, "AVAILKB") or "999999999") < 4096,
          f"filled={tag(out, 'FILLED')} availkb={tag(out, 'AVAILKB')}")
    _, out = g.run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hello >/var/log/dr-s9.log 2>&1; echo APTRC:$?; "
                   "echo DPKGAUDIT:$(dpkg --audit | wc -l); "
                   "echo HELLO:$(dpkg-query -W -f='${Status}' hello 2>/dev/null | tr ' ' _ || true); "
                   "echo LASTLINES:$(tail -n 3 /var/log/dr-s9.log | tr -d '()$`' | tr '\\n' '|' | tr ' ' _)")
    rc = tag(out, "APTRC")
    hello = tag(out, "HELLO") or ""
    installed = hello == "install_ok_installed"   # not half-installed, not config-files, not unpacked
    check(log, "no half state: dpkg has nothing to repair", tag(out, "DPKGAUDIT") == "0", str(tag(out, "DPKGAUDIT")))
    check(log, "outcome is all-or-nothing (apt rc 0 and installed, or apt rc != 0 and not installed)",
          (rc == "0" and installed) or (rc not in (None, "0") and not installed), f"rc={rc} hello={hello} last={tag(out, 'LASTLINES')}")
    say(log, f"S9 outcome: {'install completed' if rc == '0' else 'apt refused or failed'} (rc={rc})")
    _, out = g.run("rm -f /srv/fill.* /srv/fillb.*; sync; btrfs filesystem sync /; sleep 5; echo AVAILKB:$(df -k --output=avail /srv | tail -1 | tr -d ' ')")
    check(log, "space is free again", (tag(out, "AVAILKB") or "0").isdigit() and int(tag(out, "AVAILKB") or "0") > 1000000, str(tag(out, "AVAILKB")))
    _, out = g.run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hello >/var/log/dr-s9b.log 2>&1; echo APTRC:$?; echo HELLO:$(command -v hello || echo none)", 600)
    check(log, "apt works again after the space is back", tag(out, "APTRC") == "0" and tag(out, "HELLO") == "/usr/bin/hello", f"{tag(out, 'APTRC')} {tag(out, 'HELLO')}")
    after = state_list(g)
    check(log, "no state disappeared and at most one new snapshot exists (no partial leftovers from the failed run)",
          all(s in after for s in before) and len([s for s in after if s not in before]) <= 1, str(after))
    _, out = g.run("echo MENU:$(grep -c '^menuentry' /boot/efi/EFI/dr/grub.cfg); echo SUBVOLS:$(ls -1d /.top/@os-* | wc -l)")
    check(log, "menu entries match the state subvolumes", tag(out, "MENU") == tag(out, "SUBVOLS"), f"menu={tag(out, 'MENU')} subvols={tag(out, 'SUBVOLS')}")
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot after the full-disk episode", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "the system still boots its state after the episode", running_state(tag(out, "CMDLINE")) == state0, str(running_state(tag(out, "CMDLINE"))))


def scenario_window_negative(g, log):
    """The dedupe window must not suppress the snapshot of a different state: after a rollback (booted into the earlier
    state A, whose own snapshot is minutes old), an apt run in A still takes a snapshot, whose btrfs parent is A. A
    window that ignored the lineage would block it, so the check also proves the window is open for A's name."""
    pc = _precondition(g, log)
    if not pc or not _update_dr_state(g, log):
        return
    state0, _ = pc
    before = state_list(g)
    _, out = g.run("apt-get update -qq >/dev/null 2>&1; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hello "
                   ">/var/log/dr-wn1.log 2>&1; echo APTRC:$?", 900)
    after = state_list(g)
    new = [s for s in after if s not in before and s.startswith("@os-pre-")]
    check(log, "first apt run (running state) took exactly one snapshot", tag(out, "APTRC") == "0" and len(new) == 1, f"rc={tag(out, 'APTRC')} {new}")
    if len(new) != 1:
        return
    a = new[0]
    _, out = g.run(f"dr-state boot-once {a}; echo BORC:$?")
    check(log, "boot-once accepted", tag(out, "BORC") == "0", str(tag(out, "BORC")))
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot into the earlier state", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    if not check(log, "running the earlier state A", running_state(tag(out, "CMDLINE")) == a, str(running_state(tag(out, "CMDLINE")))):
        return
    before2 = state_list(g)
    _, out = g.run(f"dr-state _window-blocks {a} 900 $(date -u +%s); echo WB:$?")
    check(log, "A's own name is inside the window (a lineage-blind window would block the next apt run)", tag(out, "WB") == "0", str(tag(out, "WB")))
    _, out = g.run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tree >/var/log/dr-wn2.log 2>&1; echo APTRC:$?; "
                   "echo TREE:$(command -v tree || echo none)", 600)
    after2 = state_list(g)
    new2 = [s for s in after2 if s not in before2 and s.startswith("@os-pre-")]
    check(log, "apt run in the rolled-back state succeeded", tag(out, "APTRC") == "0" and tag(out, "TREE") == "/usr/bin/tree", f"{tag(out, 'APTRC')} {tag(out, 'TREE')}")
    check(log, "and took exactly one snapshot of its own", len(new2) == 1, str(new2))
    if len(new2) == 1:
        _, out = g.run(f"echo PARENT:$(btrfs subvolume show /.top/{new2[0]} | awk '$1==\"Parent\" && $2==\"UUID:\" {{print $3}}'); "
                       f"echo AUUID:$(btrfs subvolume show /.top/{a} | awk '$1==\"UUID:\" {{print $2; exit}}')")
        p, au = tag(out, "PARENT"), tag(out, "AUUID")
        check(log, "that snapshot is a copy of A (btrfs parent uuid)", bool(p) and p == au, f"parent={p} a={au}")


FAKE_BTRFS = ("printf '%s\\n' '#!/bin/sh' "
              "'if [ \"$1\" = subvolume ] && [ \"$2\" = snapshot ]; then echo \"ERROR: cannot snapshot: No space left on device\" >&2; exit 1; fi' "
              "'exec /usr/bin/btrfs \"$@\"' > /usr/local/sbin/btrfs; chmod 755 /usr/local/sbin/btrfs")


def scenario_s9b(g, log):
    """S9b: the apt hook's own failure path. A real full filesystem makes apt fail before our hook (S9), so this
    SIMULATES the hook's failure: a wrapper in /usr/local/sbin makes `btrfs subvolume snapshot` fail with the ENOSPC
    message. The Pre-Invoke hook must then stop apt before dpkg changes anything (exit 100), leave no snapshot and no
    half state; with the wrapper removed apt works and snapshots again."""
    pc = _precondition(g, log)
    if not pc or not _update_dr_state(g, log):
        return
    state0, _ = pc
    before = state_list(g)
    g.run("apt-get update -qq >/dev/null 2>&1")
    _, out = g.run(FAKE_BTRFS + "; echo WRAPRC:$?; btrfs subvolume snapshot /.top/x /.top/y >/dev/null 2>&1; echo WRAPFAIL:$?")
    check(log, "the failing-snapshot wrapper is in place", tag(out, "WRAPRC") == "0" and tag(out, "WRAPFAIL") == "1", f"{tag(out, 'WRAPRC')} {tag(out, 'WRAPFAIL')}")
    _, out = g.run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hello >/var/log/dr-s9b.log 2>&1; echo APTRC:$?; "
                   "echo DPKGAUDIT:$(dpkg --audit | wc -l); echo HELLO:$(command -v hello || echo none); "
                   "echo SNAPERR:$(grep -c 'No space left on device' /var/log/dr-s9b.log)", 600)
    check(log, "apt refused to run dpkg when the snapshot failed (rc != 0)", tag(out, "APTRC") not in (None, "0"), str(tag(out, "APTRC")))
    check(log, "nothing was installed and dpkg has nothing to repair", tag(out, "HELLO") == "none" and tag(out, "DPKGAUDIT") == "0", f"hello={tag(out, 'HELLO')} audit={tag(out, 'DPKGAUDIT')}")
    check(log, "the failure message reached the apt log", (tag(out, "SNAPERR") or "0") != "0", str(tag(out, "SNAPERR")))
    check(log, "no snapshot was created", state_list(g) == before, str(state_list(g)))
    _, out = g.run("rm -f /usr/local/sbin/btrfs; hash -r; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hello "
                   ">/var/log/dr-s9b2.log 2>&1; echo APTRC:$?; echo HELLO:$(command -v hello || echo none)", 600)
    after = state_list(g)
    new = [s for s in after if s not in before and s.startswith("@os-pre-")]
    check(log, "with the wrapper gone apt installs again and snapshots first", tag(out, "APTRC") == "0" and tag(out, "HELLO") == "/usr/bin/hello" and len(new) == 1, f"{tag(out, 'APTRC')} {tag(out, 'HELLO')} {new}")


def scenario_s5(g, log):
    """S5: the running state's initramfs is broken. The default entry cannot boot; the earlier state, chosen in the
    GRUB menu with keys over the serial line, can. Then the earlier state is made the default (recovery)."""
    pc = _precondition(g, log)
    if not pc:
        return
    state0, kver = pc
    before = state_list(g)
    _, out = g.run("dr-state snapshot --reason s5; echo SNAPRC:$?")
    check(log, "manual snapshot taken", tag(out, "SNAPRC") == "0", str(tag(out, "SNAPRC")))
    after = state_list(g)
    good = [s for s in after if s not in before and s.startswith("@os-pre-")]
    if not check(log, "exactly one new state", len(good) == 1, str(good)):
        return
    good = good[0]
    # corrupt (not remove) the initramfs of the RUNNING state, so its menu entry stays but cannot boot
    _, out = g.run(f"echo broken > /boot/initrd.img-{kver}; echo BRK:$(wc -c < /boot/initrd.img-{kver})")
    check(log, "initramfs of the running state is broken", tag(out, "BRK") == "7", str(tag(out, "BRK")))
    # menu order decides how many keys reach the good entry: the menu opens on the saved default (state0)
    _, out = g.run("echo ORDER:$(grep '^menuentry' /boot/efi/EFI/dr/grub.cfg | sed 's/.*--id \\(@os-[^ ]*\\).*/\\1/' | tr '\\n' ,)")
    order = [s for s in (tag(out, "ORDER") or "").split(",") if s]
    if not check(log, "both states are in the menu", state0 in order and good in order, str(order)):
        return
    downs = order.index(good) - order.index(state0)
    g.send("reboot\n")
    g.buf = b""
    try:
        seen, _ = g.expect(rb"Kernel panic|not syncing|Unable to mount root|Gave up waiting for root", 900)
    except TimeoutError as exc:
        check(log, "the default entry fails to boot because of the broken initramfs", False, str(exc)[-160:])
        return
    check(log, "the default entry fails to boot because of the broken initramfs (unpack failure then panic, no login prompt)",
          b"Initramfs unpacking failed" in seen and b"login: " not in seen, seen[-160:].decode("utf-8", "replace"))
    # the guest hangs in the panic (assumed from the kernel default panic=0, not waited out); reset it from
    # outside (QMP) and pick the earlier entry in the menu
    try:
        reply = qmp_command(g.qmp_path, "system_reset")
    except Exception as exc:  # noqa: BLE001 - a QMP failure is a failed check, not a traceback
        check(log, "guest reset through QMP", False, repr(exc))
        return
    check(log, "guest reset through QMP", "return" in reply, str(reply))
    g.buf = b""
    try:
        g.expect(rb"GNU GRUB", 600)
    except TimeoutError as exc:
        check(log, "the GRUB menu appears again", False, str(exc)[-160:])
        return
    check(log, "the GRUB menu appears again", True)
    time.sleep(1.5)
    for _ in range(abs(downs)):  # Down (or Up) from the saved default to the good entry
        g.send("\x1b[B" if downs > 0 else "\x1b[A")
        time.sleep(0.7)
    g.send("\r")
    g.buf = b""
    g.expect(rb"login: ?", 900)
    g._do_login()
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline); echo KERNEL:$(uname -r); echo DEF:$(dr-state list | awk '/\\(default\\)/{print $1}')")
    check(log, "the earlier state booted from the menu", running_state(tag(out, "CMDLINE")) == good, str(running_state(tag(out, "CMDLINE"))))
    check(log, "the saved default still names the broken state (a menu choice is not a repair)", tag(out, "DEF") == state0, str(tag(out, "DEF")))
    # recovery: make the good state the default and prove it with a reboot with no key press
    _, out = g.run(f"dr-state set-default {good}; echo SDRC:$?")
    check(log, "set-default accepted", tag(out, "SDRC") == "0")
    try:
        g.reboot()
    except Exception as exc:  # noqa: BLE001
        check(log, "reboot after recovery", False, repr(exc))
        return
    _, out = g.run("echo CMDLINE:$(cat /proc/cmdline)")
    check(log, "plain reboot now boots the good state", running_state(tag(out, "CMDLINE")) == good, str(running_state(tag(out, "CMDLINE"))))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    sock, name = sys.argv[1], sys.argv[2]
    logpath = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else f"/tmp/dr-serial-{name}.log"
    scenarios = {"boot-check": scenario_boot_check, "s2-s3": scenario_s2_s3, "kernel-upgrade": scenario_kernel_upgrade,
                 "s5": scenario_s5, "s6": scenario_s6, "s7": scenario_s7, "s9": scenario_s9,
                 "backports": scenario_kernel_backports, "s8": scenario_s8,
                 "window-negative": scenario_window_negative, "s9b": scenario_s9b}
    if name not in scenarios:
        print(f"unknown scenario {name}; one of {sorted(scenarios)}")
        return 2
    with open(logpath, "a") as log:
        g = Guest(sock, log)
        g.qmp_path = os.path.join(os.path.dirname(os.path.abspath(sock)), "qmp.sock")
        g.connect()
        scenarios[name](g, log)
    if FAILS:
        print(f"{len(FAILS)} check(s) failed: {FAILS}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
