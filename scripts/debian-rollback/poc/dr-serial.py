#!/usr/bin/env python3
"""PoC driver: scripts a guest through its serial socket (QEMU chardev, server mode).

  dr-serial.py SOCKET SCENARIO [--log FILE]

SCENARIO is one of: boot-check, s2-s3, kernel-upgrade. Scenarios are NOT idempotent: s2-s3 ends with the earlier
state as the default and both s2-s3 and kernel-upgrade require "running state == default" at the start, so run each
of them in its own fresh guest (new overlay on the installed base); boot-check may share a guest with either. The guest must be running (dr-qemu.sh start) with a
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


def scenario_kernel_upgrade(g, log):
    """A kernel change: apt dist-upgrade brings a newer kernel into the running state. The menu must show it for
    that state and keep the old kernel for the snapshot; boot new, boot the earlier state once (old kernel, its
    modules), and check that the one-shot returns to the default on the next reboot."""
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
    g.run("echo 'deb http://deb.debian.org/debian trixie-updates main' > /etc/apt/sources.list.d/dr-updates.list; "
          "echo 'deb http://deb.debian.org/debian-security trixie-security main' >> /etc/apt/sources.list.d/dr-updates.list")
    _, out = g.run("apt-get update -qq >/dev/null 2>&1; echo UPDRC:$?; "
                   "echo OFFER:$(apt list --upgradable 2>/dev/null | grep -c '^linux-image')", 900)
    offer = tag(out, "OFFER")
    if not check(log, "a newer kernel package is on offer", offer is not None and offer.isdigit() and int(offer) > 0,
                 f"updrc={tag(out, 'UPDRC')} offer={offer}"):
        return
    before = state_list(g)
    _, out = g.run("DEBIAN_FRONTEND=noninteractive apt-get -y -qq -o Dpkg::Options::=--force-confold "
                   "-o Dpkg::Options::=--force-confdef dist-upgrade >/var/log/dr-upgrade.log 2>&1; echo UPGRC:$?; "
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


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    sock, name = sys.argv[1], sys.argv[2]
    logpath = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else f"/tmp/dr-serial-{name}.log"
    scenarios = {"boot-check": scenario_boot_check, "s2-s3": scenario_s2_s3, "kernel-upgrade": scenario_kernel_upgrade}
    if name not in scenarios:
        print(f"unknown scenario {name}; one of {sorted(scenarios)}")
        return 2
    with open(logpath, "a") as log:
        g = Guest(sock, log)
        g.connect()
        scenarios[name](g, log)
    if FAILS:
        print(f"{len(FAILS)} check(s) failed: {FAILS}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
