# Guest session (VML-BL-08)

Status: design, 2026-11-24. Package P-rollback-31. Inputs: debian-rollback decisions
B10 (test images take commands via the QEMU guest agent or SSH; serial only for boot observation;
product images agent-free), B12 (assert observable behaviour; keep the protocol lessons) and B13
(guest orchestration lives in vm-lab); review findings H4 and H6.

Today `scripts/debian-rollback/poc/dr-serial.py` drives guests through a root shell on the serial
line. It has its own QMP client (`qmp_command`), a marker-framed `run` (`eval` plus a separate
`__DR_END_<rc>__` print), prompt detection, a password login, a boot_id-proven reboot, QMP reset and
boot-menu keys. Most of its harness fixes (prompt anchoring, the pager hang, echoed command lines,
`; ;` and trailing operators) were artefacts of using a tty as a command channel. This note decides
the channel, the vm-lab API that replaces the serial session, what is deleted, how it is tested,
and the order of the switch.

## 1. Decision: SSH for commands, serial for boot, QMP for the machine

**Test images take commands over SSH**, through the guest's forwarded port, with a per-base test
key. The QEMU guest agent is not used now.

| Criterion | QGA `guest-exec` (virtio-serial chardev) | SSH (forwarded port, test key) |
|---|---|---|
| What the test image must contain | `qemu-guest-agent`, which is not in the product. Getting it in means a test-only installer knob (the disk no longer comes from the plain product path, against B15) or an `apt install` after install, which fires the dr-state apt hook and adds a snapshot to every base (shifts sequence, counter and dedupe-window assertions). | Nothing new: the product installer already includes `openssh-server`. The test difference is one public key in the install config (CIDATA), a feature a headless real install also wants. `/root` is a data subvolume outside the OS states, so the key survives every rollback. |
| Exit status, stdout, stderr | `guest-exec` returns a pid; the host polls `guest-exec-status` for `exitcode`, base64 `out-data`/`err-data` and truncation flags. | Native: the ssh client returns the remote status; stdout and stderr are separate pipes. Status 255 is ambiguous (see section 2). |
| Timeouts | No timeout argument; the host polls against its own deadline and must kill the process with a second `guest-exec`. | Host deadline on the client process, plus `timeout -k 5 T` around the remote command so nothing keeps running after the client gives up. |
| Across a reboot | The host side of the chardev stays connected while the agent dies and restarts; in-flight requests are lost and stale replies can arrive. Every reconnect needs `guest-sync-delimited` framing: an in-band protocol again. | The connection drops and a new one is opened. A clean, visible state change. |
| Security | Unix socket in the state directory; whoever can open it has root in the guest, plus `guest-file-*` and password RPCs unless blocklisted. No network exposure. | Key auth to root. The port is reachable only on the guest's dedicated bridge (vm-lab never publishes). The private key is a test credential for throwaway guests. |
| Available in boot | After udev sees the virtio port (sysinit). | After sshd and DHCP: later. Neither covers firmware, loader or initramfs, so this difference does not decide anything; serial covers that range. |
| Fit with vm-lab | Needs new QEMU arguments (`virtio-serial`, a chardev, `virtserialport` named `org.qemu.guest_agent.0`). Reached through the state directory like QMP. | No launcher change: `--forward 2222:22` is already the documented start. The driver needs a route to the container's address on the bridge. |

Reasons, in order of weight:

1. The product image stays as it is. SSH needs no extra package, so a release-evidence disk is
   product ISO output plus one config line (B10, B15). QGA forces either a test-only install path or
   an apt run that pollutes every base.
2. A pinned host key checks a product rule for free. The debian-rollback design guide requires
   that SSH host keys do not change when an earlier state boots. With strict host-key checking every
   rollback scenario asserts it; a change is reported as its own error, not as a flaky connection.
3. Both channels remove the tty, but SSH also has no in-band framing. QGA's reconnect handshake
   after each reboot is the same class of protocol bug that the serial session produced.

Costs accepted: the driver needs a route to the guest bridge (direct on a plain host; a cockpit
container must be attached to the guest's network), the `ssh` client binary on the driver side, and
a test key per base disk. QGA stays the option for a future consumer whose guest has no network;
the API below keeps the channel behind one internal class so it can be added without changing
callers.

**What serial keeps:** boot observation (firmware, loader, kernel, initramfs and init messages),
early failure detection (panic, init failure, `Unable to mount root`) so a broken boot fails in
seconds instead of at the SSH deadline, and menu selection on loaders that read a serial terminal
(GRUB in debian-rollback). Serial is read from `<state-dir>/serial.log`, which QEMU appends for every
guest; the socket is opened only to write keys, and only for that write.

## 2. API: `vm_lab.guest`

A new module in the vm-lab package. It imports `vmscreen.qmp.QmpClient` and `vmscreen.keys`
(present in `vmscreen-v0.1.0`, so the floor `vmscreen>=0.1.0` stays). The design guide's line "the VM
tool does not import vmscreen" is corrected in the same package; there is still no third key table.

```python
class GuestError(RunnerError): ...            # base; every error carries the serial tail
class GuestTimeout(GuestError): ...           # a deadline passed
class GuestTransportError(GuestError): ...    # ssh cannot connect or authenticate, or lost the link mid-command
class GuestIdentityChanged(GuestError): ...   # the host key differs from the pinned one
class BootFailure(GuestError): ...            # a fail_on pattern appeared on serial while waiting; .match
class RebootNotProven(GuestError): ...        # boot_id unreadable before, or unchanged after
# vmscreen's QmpError passes through unchanged.

@dataclass(frozen=True)
class SshEndpoint:
    host: str; port: int; key: Path; known_hosts: Path; user: str = "root"

def ssh_endpoint(state_dir: Path, *, key: Path, guest_port: int = 22) -> SshEndpoint
    # name, network and the forward from <state-dir>/runner.json, the address from
    # `docker inspect` of that exact container name; known_hosts = <state-dir>/known_hosts.

class Result(NamedTuple):
    rc: int; out: str; err: str

class Guest:
    def __init__(self, state_dir: Path, ssh: SshEndpoint, *, log: TextIO | None = None,
                 ssh_program: str = "ssh", clock=time.monotonic, sleep=time.sleep): ...
    def wait_ready(self, timeout: float, *, not_boot_id: str | None = None,
                   fail_on: re.Pattern[bytes] | None = None, since: int | None = None) -> str
    def run(self, cmd: str, timeout: float = 300, *, input: bytes | None = None) -> Result
    def boot_id(self) -> str
    def reboot(self, timeout: float = 1200, *, fail_on: re.Pattern[bytes] | None = None) -> str
    def trigger(self, cmd: str) -> None
    def reset(self) -> None
    def send_keys(self, keys: str | list[list[str]], *, delay: float = 0.7, hold_ms: int = 100) -> None
    def serial_write(self, data: bytes) -> None
    def serial_mark(self) -> int
    def wait_serial(self, pattern: re.Pattern[bytes], timeout: float, *, since: int,
                    fail_on: re.Pattern[bytes] | None = None) -> re.Match[bytes]
    def press_until(self, keys: str | list[list[str]], until: re.Pattern[bytes], *, since: int,
                    every: float, timeout: float, fail_on: re.Pattern[bytes] | None = None) -> re.Match[bytes]
    def close(self) -> None
```

Semantics:

- **No login.** Key auth replaces the serial password login. `Guest` never types into the console
  unless the caller calls `serial_write` or `send_keys`, so the old rule "never type into the loader
  before userspace" holds by construction.
- **`run`** executes `ssh -T` with `BatchMode=yes`, `IdentitiesOnly=yes`, the endpoint's key,
  `UserKnownHostsFile=<known_hosts>`, `StrictHostKeyChecking=yes` (`accept-new` only while
  `known_hosts` is empty), `ConnectTimeout=5`, `ServerAliveInterval=5`, and a `ControlMaster`
  under a short private directory (unix path limit; one key exchange per boot instead of per
  command, which matters under TCG). The remote command is `timeout -k 5 <T> sh -c <quoted cmd>`.
  No tty: no echo, no prompt, no pager. `input` goes to the remote stdin (file transfer, hot
  patches). Output is decoded UTF-8 with replacement. Each call appends a transcript record (time,
  command, rc, stdout, stderr, elapsed) to `log`; the transcript is the evidence.
- **Status 255.** ssh uses 255 for its own failures. When the client exits 255, `run` asks the
  control master (`ssh -O check`); if the master is gone the result is `GuestTransportError`, else 255
  is the command's own status and is returned. A host-key mismatch (ssh's "Host key verification
  failed") raises `GuestIdentityChanged`.
- **Timeouts.** The local deadline is `T + 10` s; past it the client is killed and `GuestTimeout`
  raised. A remote status 124 or 137 with elapsed time at least `T` is also `GuestTimeout`; below
  `T` it is the command's own status.
- **`wait_ready`** polls `cat /proc/sys/kernel/random/boot_id` every 2 s until it answers. With
  `not_boot_id` it accepts only a different id: right after a reboot command the old boot's sshd can
  still answer, and that answer must not count. With `fail_on` it scans serial from `since` on every
  poll and raises `BootFailure` at the first match. It returns the boot id.
- **`reboot`** reads the boot id (`RebootNotProven` if unreadable: never reboot blindly), takes a
  serial mark, `trigger`s `systemctl reboot`, then `wait_ready(not_boot_id=old, fail_on=...)`. It
  returns the new id; a timeout raises `RebootNotProven` with the serial tail.
- **`trigger`** runs a command whose link loss is expected (reboot, poweroff, SysRq): started with
  `nohup … &` after a 1 s delay so the call itself returns 0; the control master is closed.
- **`reset`** is QMP `system_reset` through a short-lived `QmpClient` on the short socket path
  (`unix_socket_client_path`, because `QmpClient` has no long-path alias). The session drops its
  master and needs `wait_ready` before the next `run`.
- **`send_keys`** validates the whole sequence (`vmscreen.keys.parse_keys`/`validate_keys`) before
  sending any key, then `send_sequence` on a short-lived `QmpClient`.
- **`serial_mark` / `wait_serial`** work on byte offsets of `serial.log`. Matching strips ANSI CSI
  sequences and normalises CR/LF first; patterns are the caller's and must be line-anchored. A log
  that shrank or changed inode since the mark raises `GuestError` (no silent restart from 0).
- **`serial_write`** connects to the serial socket, writes, closes. ENOENT and ECONNREFUSED are
  retried until the deadline (QEMU is creating or restarting the chardev); any other `OSError`, such
  as `ENAMETOOLONG`, fails at once.
- **`press_until`** sends `keys` every `every` seconds until `until` matches on serial (or `fail_on`
  raises). It is the generic part of blind menu selection.

**Generic (vm-lab) versus scenario (debian-rollback).** vm-lab owns the transport, the boot_id rule,
the serial watch, reset, keys, `press_until` and `ssh_endpoint`. debian-rollback owns every pattern
and policy: the init-failure and GRUB banner expressions, which key sequence picks which entry
(computed from `bootctl --json` or the asserted order, B12), "reset after a panic and press again",
the SysRq command string, `dr-state` calls, PASS/FAIL checks, key generation for its bases and the
`ROOT_SSH_AUTHORIZED_KEY` install-config line.

## 3. What moves and what is deleted

Moves into `vm_lab.guest` (rewritten for the new channel, with their tests):

- the boot_id reboot proof, extended by the "old boot still answers" guard;
- the connect-error split (transient ENOENT/ECONNREFUSED versus permanent), now for the serial
  write path;
- the anchored-prompt lesson from `console_prompt_kind`: serial patterns are matched on
  ANSI-stripped, line-normalised text, and the kernel build banner case becomes a `wait_serial` test;
- QMP reset and `send-key`, on vmscreen's `QmpClient` instead of `qmp_command`.

Deleted from `dr-serial.py` once commands no longer go over serial: `qmp_command`, the `Guest` class
(`connect`, `_pump`, `send`, `expect`, `_shell_answers`, `_do_login`, `login`, `run`, `boot_id`,
`reboot`, `force_reboot_to_init_failure`, `select_older_entry_from_boot_menu`), `END_RE`,
`marker_wrapped_command`, `console_prompt_kind` and its prompt expressions, `DR_LOGIN_USER` and
`DR_LOGIN_PASSWORD`, the `$`/backtick/parenthesis filter in `tags()` (nothing is echoed any more; a
plain `TAG:` reader stays until P-rollback-26's `--json` reads replace it), the base64 heredoc
transfer (replaced by `run(..., input=payload)`), and the `live-inspect` scenario (a read-only look
at the Live ISO, which has no key; `vm-lab console serial` covers it by hand). The two
scenario-specific sequences become short debian-rollback functions on the vm-lab API:
`boot_to_init_failure(g)` = `trigger(sysrq)` + `wait_serial(INIT_FAILURE, fail_on=LOGIN)`, and
`select_entry(g, downs)` = `press_until` with reset on panic.

## 4. Test strategy

All fakes are boundaries with state; tests assert what the session reports and what the fake
machine ends up in, never the shape or order of calls.

- **`FakeMachine`**: boot id, booted entry, up/booting/down, host key, a "damaged default" flag. It
  appends boot text to a temporary `serial.log` as it boots, and fails with a panic line when damaged.
- **Fake QMP**: a unix-socket server speaking the QMP greeting and `qmp_capabilities`. `system_reset`
  reboots the machine (new boot id); `send-key` feeds a fake loader that moves a cursor and boots the
  selected entry on `ret`.
- **Fake SSH**: `ssh_program` points to a small executable test helper. It reads the machine state
  from a file, exits 255 with ssh's messages when the machine is down or its host key changed, and
  otherwise runs the remote command with `sh -c` locally, so real exit status, stdout and stderr flow
  through a real subprocess. `-O check` answers from the same state.
- **Fake serial socket**: a server that records written bytes into the fake loader.

Required cases: rc of a failing command; syntax-broken commands (`false;`, `false &&`, `true ||`,
`# comment`) return their shell status and never time out (carried over from the marker-wrapper
tests); a genuine remote 255 versus a lost link; host-key change; a command over its timeout; reboot
returns a new id and the old boot answering during shutdown is rejected; a damaged default raises
`BootFailure` well before the SSH deadline; `press_until` boots the entry the fake loader selected;
a serial match split across two appends; a mark after log truncation; permanent versus transient
serial-socket errors; `send_keys` with one unknown key sends nothing.

Carried over from `test-dr-serial.py` (as the cases above): exit status carried, the eval-wrapper
syntax cases, split marker (as split serial match), permanent and transient socket errors, kernel
banner not a prompt, existing-shell attach (as "`wait_ready` on a booted guest returns without a
reboot"), the boot_id rule. Deleted with their code: the marker echo test, the three login
handshake tests and the echoed-command `tags()` tests; their bug class cannot occur without a tty.
Scenario tests that remain after P-rollback-26 run on a debian-rollback fake built on the same fake
machine, asserting the resulting state.

## 5. Migration and packages

The switch is one cut-over, not a dual transport: two channels in the driver would be two code
paths to keep correct. The matrix keeps working because the old driver stays on main until new
key-bearing bases exist and the new driver has passed on them.

1. **P-31a vm-lab `vm_lab.guest` (M, about 400 lines of source and 500 of tests).** Module, fakes,
   tests, README section "Drive a guest from Python", design-guide corrections (vmscreen import,
   short-lived QMP per call). If `runner.json` does not record the guest port of each forward,
   `start` records it. No change for existing consumers. Gates vm-lab `r0-r1` + `wheel`. Depends on
   P-rollback-30.
2. **P-31b debian-rollback install key (S).** Install-config key `ROOT_SSH_AUTHORIZED_KEY` (one
   OpenSSH public key, validated by `dr-plan --check-config`), written to `/root/.ssh/authorized_keys`
   (0600) by `install-btrfs.sh`; the same option on the NoCloud seed path. Offline tests compare the
   installed file. Gate `offline`. After P-rollback-25, which moves the installer payload.
3. **Base rebuild (host time, no code).** Install GRUB and systemd-boot bases from the product ISO of
   the 31b head with a generated ed25519 key pair; the private key stays beside the base, mode 0600,
   and is excluded from evidence bundles (P-rollback-33 must enforce that).
4. **P-31c dr-serial switch (M).** Scenarios take a `vm_lab.guest.Guest`; the command line becomes
   `--state-dir DIR --key FILE SCENARIO`; the deletions of section 3; imports `vm-lab/src` and
   `libraries/vmscreen/src` relative to its own path, so a bare checkout works. Acceptance:
   `dr-serial.py` has no `socket` import, no QMP client and no `Guest` class; offline tests pass;
   boot-check, s2-s3, s5 and recovery-kernel pass live on the rebuilt bases under a lease, with the
   transcript archived. Gates `offline` + vm-lab `r0-r1`. Depends on 31a, 31b, the rebuild and
   P-rollback-26 (both rewrite the same file; transport after assertions). Closes VML-BL-08.

P-rollback-33 (matrix runner) builds on 31c; the old bases and serial driver retire with 31c's merge.
Follow-up (vm-lab backlog): `core.qmp_command` moves onto `QmpClient` with the short-path alias.

## 6. Unknowns to measure (in the 31c live check unless noted)

- Time from reset to the first SSH answer under TCG on a 2-vCPU guest, against the serial `login:`
  prompt (sshd waits for ifupdown DHCP), and per-command latency with and without the control master.
- How long the old boot's sshd keeps answering after `systemctl reboot` under TCG (sizes the poll
  interval; correctness rests on the boot_id rule either way).
- Reachability of the container address on the guest bridge from a plain host, from a cockpit
  container after `docker network connect`, and with `--internal` (31a, one probe).
- Host keys identical across states after a rollback. Expected; a difference is a product finding.
- sshd reachable after the recovery boots (an older state after kernel, library or dependency damage
  to the newer one).
- GRUB serial-terminal input over short-lived serial-socket connections, or QMP `send-key` reaching
  GRUB's `terminal_input`, whichever s5 then uses.
- The `ssh` client and a short private directory for `ControlPath` in the driver's environment.
- Not measured now, only if a no-network consumer brings QGA back: the `qemu-guest-agent` package in a
  Debian 13 test image and virtio-serial behaviour under TCG.
