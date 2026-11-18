#!/usr/bin/env python3
"""PoC driver: scripts a guest through its serial socket (QEMU chardev, server mode).

  dr-serial.py SOCKET SCENARIO [--log FILE]

SCENARIO is one of: boot-check, s2-s3. The guest must be running (dr-qemu.sh start) with a login
prompt on ttyS0 and root password "root" (the PoC installer default). Output is a plain transcript
of the commands and their output; nothing here is parsed as a verdict, the transcript is evidence.

Command results use a marker: a command is sent as `CMD; echo __DR_END_$?__`, and the reader waits
for `__DR_END_<digits>__` which does not occur in the echoed command line (that one has `$?`).
Standard library only. Host-side: it never touches anything but the socket.
"""
import re
import socket
import sys
import time

END_RE = re.compile(rb"__DR_END_(\d+)__")


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
        import select
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
        if not data:  # QEMU closed the chardev (guest reset): reconnect
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
        """True when a shell answers a probe within `wait` seconds. The echoed probe has `$((20+22))`, only the
        shell's answer has 42."""
        self.buf = b""
        self.send("echo __DR_READY_$((20+22))__\n")
        try:
            self.expect(rb"__DR_READY_42__", wait)
            return True
        except TimeoutError:
            return False

    def login(self, timeout=900):
        # a previous scenario may have left a root shell on the line
        if self._shell_answers(6):
            return
        self.send("\n")
        self.expect(rb"login: ?$|login: ", timeout)
        self.send("root\n")
        self.expect(rb"[Pp]assword: ?", 60)
        self.send("root\n")
        # login(1) discards input typed while it sets the session up (slow under TCG), so probe until the
        # shell answers. The echoed probe has `$((20+22))`, only the shell's answer has 42.
        deadline = time.monotonic() + 400
        while time.monotonic() < deadline:
            if self._shell_answers(10):
                return
        raise TimeoutError("no working shell after login")

    def run(self, cmd, timeout=300):
        self.buf = b""
        self.send(f"{cmd}; echo __DR_END_$?__\n")
        out, m = self.expect(END_RE, timeout)
        rc = int(m.group(1))
        text = END_RE.sub(b"", out).decode("utf-8", "replace")
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).replace("\r\n", "\n")  # plain lines for the checks
        return rc, text


def say(log, line):
    log.write(f"\n##### {line}\n")
    log.flush()
    print(line, flush=True)


def check(log, label, ok, detail=""):
    say(log, f"{'PASS' if ok else 'FAIL'}: {label} {detail}".rstrip())
    return ok


def scenario_boot_check(g, log):
    g.login()
    rc, out = g.run("uname -r; cat /proc/cmdline; findmnt -no SOURCE,OPTIONS /; systemctl is-system-running; "
                    "ip -color=never -4 -br addr show scope global; getent hosts deb.debian.org | head -1; "
                    "dr-state list; ls /boot/efi/EFI/dr /boot/efi/EFI/BOOT; "
                    "sed -n 1,40p /boot/efi/EFI/dr/grub.cfg; "
                    "for m in /home /root /srv /var/www /var/log /var/lib/docker /var/lib/containers "
                    "/var/lib/postgresql /var/lib/mysql /usr/local /.top /opt /etc; do "
                    "if mountpoint -q $m; then echo MP:$m; else echo NOTMP:$m; fi; done")
    check(log, "boot-check commands ran", rc == 0)
    check(log, "booted a state subvolume", "rootflags=subvol=@os-" in out)
    check(log, "system is running (systemd)", re.search(r"^running\s*$", out, re.M) is not None)
    check(log, "guest has a global IPv4 address", re.search(r"^eth0\s+UP\s+\d+\.\d+\.\d+\.\d+/", out, re.M) is not None)
    check(log, "name resolution works", re.search(r"^[0-9a-f:.]+\s+.*deb\.debian\.org\s*$", out, re.M) is not None)
    outside = ["/home", "/root", "/srv", "/var/www", "/var/log", "/var/lib/docker", "/var/lib/containers",
               "/var/lib/postgresql", "/var/lib/mysql", "/usr/local", "/.top"]
    check(log, "outside paths (D18) are separate mounts",
          all(f"MP:{p}" in out for p in outside), str([p for p in outside if f"MP:{p}" not in out]))
    check(log, "/opt and /etc are inside the state (D18)", "NOTMP:/opt" in out and "NOTMP:/etc" in out)


def scenario_s2_s3(g, log):
    g.login()
    rc, out = g.run("mkdir -p /var/lib/docker /srv; echo m1 > /home/marker; echo m2 > /srv/marker; "
                    "echo m3 > /var/lib/docker/marker; echo etc1 > /etc/dr-etc-marker; dr-state list")
    check(log, "markers written", rc == 0)
    # S2: apt install takes a snapshot of the running state first (apt hook)
    rc, out = g.run("apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq hello 2>&1 | tail -5; "
                    "command -v hello; dr-state list", 900)
    check(log, "apt install hello", "/usr/bin/hello" in out, out[-200:])
    pre = re.findall(r"(@os-pre-\S+)", out)
    check(log, "snapshot created by the apt hook", len(pre) >= 1, str(pre))
    if not pre:
        return
    rc, out = g.run("echo etc2 >> /etc/dr-etc-marker; echo m1b >> /home/marker; "
                    "cat /boot/efi/EFI/dr/grub.cfg | grep -c menuentry")
    # S3: boot the earlier state once, check package gone, outside data kept, /etc rolled back (S10)
    rc, out = g.run(f"dr-state boot-once {pre[0]} && echo ok")
    check(log, "boot-once set", "ok" in out)
    g.send("reboot\n")
    time.sleep(5)
    g.buf = b""
    g.login(1200)
    rc, out = g.run("cat /proc/cmdline; command -v hello; echo ---; cat /home/marker; echo ---; "
                    "cat /srv/marker; cat /var/lib/docker/marker; echo ---; cat /etc/dr-etc-marker")
    check(log, "booted the earlier state", f"rootflags=subvol={pre[0]}" in out, out[:160])
    check(log, "package absent in the earlier state", "/usr/bin/hello" not in out)
    check(log, "/home data written after the snapshot survives", "m1b" in out)
    check(log, "service data survives (/srv, /var/lib/docker)", "m2" in out and "m3" in out)
    check(log, "/etc is the earlier one (S10)", out.rstrip().endswith("etc1") and "etc2" not in out)
    # S4: make it permanent, reboot, still the earlier state
    rc, out = g.run(f"dr-state set-default {pre[0]} && echo ok")
    g.send("reboot\n")
    time.sleep(5)
    g.buf = b""
    g.login(1200)
    rc, out = g.run("cat /proc/cmdline")
    check(log, "earlier state is the default after set-default (S4)", f"rootflags=subvol={pre[0]}" in out)


def _vkey(v):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", v) if p]


def _reboot(g, timeout=1200):
    g.send("reboot\n")
    time.sleep(5)
    g.buf = b""
    g.login(timeout)


def scenario_kernel_upgrade(g, log):
    """A kernel change: apt dist-upgrade brings a newer kernel into the running state; the menu must show it for
    that state and keep the old kernel for the snapshot; boot new, boot the earlier state once (old kernel and its
    modules), and check that the one-shot returns to the default on the next reboot."""
    g.login()
    rc, out = g.run("uname -r; dr-state list")
    old = out.strip().splitlines()[1].strip() if len(out.strip().splitlines()) > 1 else ""
    check(log, "running kernel recorded", bool(old), old)
    # the disk of run 2a was installed without the updates and security suites
    g.run("echo 'deb http://deb.debian.org/debian trixie-updates main' > /etc/apt/sources.list.d/dr-updates.list; "
          "echo 'deb http://deb.debian.org/debian-security trixie-security main' >> /etc/apt/sources.list.d/dr-updates.list")
    rc, out = g.run("apt-get update -qq 2>&1 | tail -3; apt list --upgradable 2>/dev/null | grep -E '^linux-image' | cut -c1-120", 900)
    if not check(log, "a newer kernel package is on offer", re.search(r"linux-image", out) is not None, out[-200:]):
        return
    rc, out = g.run("DEBIAN_FRONTEND=noninteractive apt-get -y -qq dist-upgrade 2>&1 | tail -5; echo ===; ls /boot; "
                    "echo ===; dr-state list; echo ===; grep -E '^menuentry' /boot/efi/EFI/dr/grub.cfg", 5400)
    kernels = sorted(set(re.findall(r"^vmlinuz-(\S+)$", out, re.M)), key=_vkey)
    new = kernels[-1] if kernels else ""
    check(log, "a newer kernel was installed next to the old one", bool(new) and new != old and old in kernels, str(kernels))
    menu = dict(re.findall(r'menuentry "Debian (@os-\S+) \(kernel (\S+)\)"', out))
    check(log, "menu: the running state names the NEW kernel", menu.get("@os-0001") == new, str(menu))
    pre = [s for s in menu if s.startswith("@os-pre-")]
    check(log, "menu: the snapshot keeps the OLD kernel", bool(pre) and all(menu[s] == old for s in pre), str(menu))
    if not (new and pre):
        return
    _reboot(g)
    rc, out = g.run("uname -r")
    check(log, "default boot runs the new kernel", out.strip().splitlines()[-1].strip() == new, out.strip()[-60:])
    rc, out = g.run(f"dr-state boot-once {pre[0]} && echo ok")
    _reboot(g)
    rc, out = g.run("uname -r; cat /proc/cmdline | tr ' ' '\\n' | grep rootflags; ls /lib/modules; modprobe loop && echo MODOK")
    check(log, "earlier state boots its OLD kernel", out.strip().splitlines()[1].strip() == old if len(out.strip().splitlines()) > 1 else False, out.strip()[:120])
    check(log, "earlier state's modules match its kernel (modprobe works)", "MODOK" in out)
    _reboot(g)
    rc, out = g.run("uname -r")
    check(log, "the one-shot returned to the default on the next reboot", out.strip().splitlines()[-1].strip() == new, out.strip()[-60:])


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
