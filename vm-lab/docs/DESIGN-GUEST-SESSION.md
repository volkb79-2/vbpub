# Guest session (VML-BL-08)

Status: design, 2026-11-24, package P-rollback-31. Inputs: debian-rollback decisions B10 (test
images take commands via QGA or SSH, serial for boot observation, product images agent-free), B12
(observable behaviour; protocol lessons kept), B13 (guest orchestration in vm-lab); findings H4, H6.

`scripts/debian-rollback/poc/dr-serial.py` drives guests through a root shell on the serial line,
with its own QMP client, a marker-framed `run`, prompt detection, a password login, a
boot_id-proven reboot, QMP reset and menu keys. Most of its harness fixes (prompt anchoring, the
pager hang, echoed command lines, trailing operators) came from using a tty as a command channel.

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
container must be attached to the guest's network by exact name, which P-31a's
`attached_to_network` helper owns), the `ssh` client binary on the driver side, and a test key per
base disk. QGA stays the option for a future consumer whose guest has no network; the API below keeps
the channel behind one internal class so it can be added without changing callers. SSH answering
also covers part of the "booted well" question (debian-rollback F12/Q7): sshd is listening.

**What serial keeps:** boot observation (firmware, loader, kernel, initramfs and init messages),
early failure detection (panic, init failure, `Unable to mount root`) so a broken boot fails in
seconds instead of at the SSH deadline, and menu selection on loaders that read a serial terminal
(GRUB in debian-rollback). Serial is read from `<state-dir>/serial.log`, which QEMU appends for every
guest; the socket is opened only to write keys, and only for that write.

**After exec-breaking damage there is no command channel.** Once a scenario moves the ELF loader
(recovery-library) or otherwise breaks `execve`, nothing new starts: sshd execs `sshd-session` per
connection, a mux channel execs the shell, and `timeout`, `setsid`, `sleep` and `sh` are dynamic.
QGA `guest-exec` fails the same way; the serial driver works today only because its bash was
already running and its SysRq string uses built-ins. Rule: from the damage on, only serial
observation and QMP remain. Damage and reboot are ONE `trigger` payload: every program is started
before the damage, an already running interpreter (perl) does the damage, and after it only that
process's built-ins run (`print` of `s`, `u`, `b` to `/proc/sysrq-trigger`), or the driver sends QMP
`system_reset` after the payload's own sync. debian-rollback uses this form for every damage step.

## 2. API: `vm_lab.guest`

A new library module in the vm-lab package for drivers. It imports `vmscreen.qmp.QmpClient`,
`vmscreen.keys`, `vmscreen.sources.make_mark`/`read_serial` and `vmscreen.serialtext` (all in
`vmscreen-v0.1.0` and free of `cli-extended`, so the floor `vmscreen>=0.1.0` stays). `cli`, `core`,
`console` and `text` never import `vm_lab.guest`, so the CLI still runs without vmscreen and
degrades to OCR. P-31a states this in the three places that now say "vm-lab does not import
vmscreen": DESIGN-GUIDE "Two key tables" and "Why vmscreen is a subprocess", and the README install
paragraph. `vm_lab.console`'s key table stays (`console type` must work without vmscreen); the
session uses `vmscreen.keys`, so there is no third table.

```python
class GuestError(RunnerError): ...            # base; every error carries the serial tail
class GuestTimeout(GuestError): ...           # a deadline passed
class GuestTransportError(GuestError): ...    # ssh cannot connect or authenticate, or lost the link mid-command
class GuestIdentityChanged(GuestError): ...   # the host key differs from the pinned one
class BootFailure(GuestError): ...            # a fail_on pattern appeared on serial while waiting; .match
class RebootNotProven(GuestError): ...        # boot_id unreadable before, or unchanged after
class SerialInputUnavailable(GuestError): ... # serial_write on a guest started without --serial-console
# vmscreen's QmpError passes through; its NoExactText from read_serial becomes GuestError.

@dataclass(frozen=True)
class SshEndpoint:
    host: str; port: int; alias: str; key: Path; known_hosts: Path; user: str = "root"

def ssh_endpoint(state_dir: Path, credentials: Path, *, profile: VmProfile = GENERIC_PROFILE,
                 private_dir: Path, guest_port: int = 22) -> SshEndpoint
    # runner.json: name, network; port of the forward to guest port 22 (`<fwd>_guest_port` for
    # CLI-sized profiles, VmProfile.forwards for fixed ones); address: `docker inspect` of that exact
    # name; alias = guest name. Copies the key from `credentials` into private_dir (0700) at 0600 and
    # writes known_hosts there from the base's recorded host keys, under the alias.

@contextmanager
def attached_to_network(network: str, *, plain_host: bool) -> Iterator[None]
    # cockpit: `docker network connect` of the calling container to that exact network, disconnect
    # on exit if this call connected it; plain_host: nothing.

class Result(NamedTuple):
    rc: int; out: str; err: str

class Guest:
    def __init__(self, state_dir: Path, ssh: SshEndpoint, *, profile: VmProfile = GENERIC_PROFILE,
                 log: TextIO | None = None, ssh_program: str = "ssh",
                 clock=time.monotonic, sleep=time.sleep): ...
    def wait_ready(self, timeout: float, *, not_boot_id: str | None = None,
                   fail_on: re.Pattern[str] | None = None, since: str | None = None) -> str
    def run(self, cmd: str, timeout: float = 300, *, input: bytes | None = None) -> Result
    def boot_id(self) -> str
    def reboot(self, timeout: float = 1200, *, fail_on: re.Pattern[str] | None = None) -> str
    def trigger(self, payload: str) -> None
    def reset(self) -> None
    def send_keys(self, keys: str | list[list[str]], *, delay: float = 0.7, hold_ms: int = 100) -> None
    def serial_write(self, data: bytes) -> None
    def serial_mark(self) -> str
    def wait_serial(self, pattern: re.Pattern[str], timeout: float, *, since: str,
                    fail_on: re.Pattern[str] | None = None) -> re.Match[str]
    def press_until(self, keys: str | list[list[str]], until: re.Pattern[str], *, since: str,
                    every: float, timeout: float, fail_on: re.Pattern[str] | None = None) -> re.Match[str]
    def close(self) -> None
```

Semantics:

- **No login.** Key auth replaces the password login. Nothing is typed into the console except by
  `serial_write` or `send_keys`, so "never type into the loader before userspace" holds.
- **Isolated ssh.** Every invocation: `-F /dev/null -T`, `BatchMode=yes`, `IdentitiesOnly=yes`,
  `IdentityFile=<key>`, `IdentityAgent=none`, `ForwardAgent=no`, `UserKnownHostsFile=<known_hosts>`,
  `GlobalKnownHostsFile=/dev/null`, `StrictHostKeyChecking=yes`, `UpdateHostKeys=no`,
  `CheckHostIP=no`, `HostKeyAlias=<guest name>` (the address can change on resume),
  `ServerAliveInterval=5`, `ServerAliveCountMax=24` (120 s of silence before a link counts as dead;
  a 15 s budget dies under TCG load, and verdicts must not depend on load). There is no TOFU:
  `known_hosts` comes from the base's recorded host keys (section 5), and an empty one is a
  `GuestError` at construction. stdin is `/dev/null` unless `input` is given.
- **Connections.** `wait_ready` polls with `ControlMaster=no`, `ControlPath=none` and
  `ConnectTimeout=5`. Only after it has proven the boot does the session open the master explicitly
  (`-M -N -f`, `ControlPersist=900`, `ConnectTimeout=30`) under a short private 0700 directory
  (unix path limit). Master establishment is retried until the caller's deadline; a started command
  is never retried. Commands then use `ControlMaster=no` with that `ControlPath` and
  `ProxyCommand=/bin/false`: one key exchange per boot instead of per command, which matters under
  TCG. The mux client never uses the ProxyCommand, so when the master is gone a command cannot fall
  back to its own connection (to whichever boot answers); it fails with 255 and a diagnostic, which
  is a `GuestTransportError` (fake case: master gone, `run` raises and the command never executes).
- **`run`** passes one remote string, `shlex.join(["timeout", "-k", "5", str(T), "bash", "-c",
  cmd])`: `cmd` is quoted once by `shlex.join`; the login shell's single parse yields
  `timeout … bash -c <cmd>`; the string is passed to ssh as one argv element. `bash`, not `sh`: commands run in bash today (`read -t`), and dash would turn
  some into silent `|| :` passes. No tty: no echo, prompt or pager. `input` goes to the remote stdin
  (file transfer, hot patches). Output is UTF-8 with replacement; each call appends a transcript
  record (time, command, rc, stdout, stderr, elapsed) to `log`.
- **Status 255** is ssh's own failure code, and a slave can exit 255 before the master notices the
  lost link, so `-O check` alone can still succeed. 255 is returned as the command's own status only
  when all three hold: stderr has no ssh client diagnostic (`ssh:`, `mux_client`, `Control socket`,
  `Connection … closed`, `Broken pipe`, `kex_`), `ssh -O check` succeeds, and a follow-up `true`
  through the master returns 0. Otherwise `GuestTransportError` (fail closed). "Host key verification
  failed" raises `GuestIdentityChanged`.
- **Timeouts.** The local deadline is `T + 10` s; past it the client is killed and `GuestTimeout`
  raised. A remote status 124 or 137 with elapsed time at least `T` is also `GuestTimeout`; below
  `T` it is the command's own status.
- **`wait_ready`** polls `cat /proc/sys/kernel/random/boot_id` every 2 s until it answers. With
  `not_boot_id` it accepts only a different id: right after a reboot command the old boot's sshd can
  still answer, and that answer must not count. With `fail_on` it scans serial from `since` on every
  poll and raises `BootFailure` at the first match. It returns the boot id and opens the master.
- **`reboot`** reads the boot id (`RebootNotProven` if unreadable: never reboot blindly), takes a
  serial mark, `trigger("sleep 1; exec systemctl reboot")`, then `wait_ready(not_boot_id=old,
  fail_on=...)`. It returns the new id; a timeout raises `RebootNotProven` with the serial tail.
- **`trigger`** starts a payload whose link loss is expected, detached (`setsid -f bash -c <payload>
  </dev/null >/dev/null 2>&1`), so the call returns 0 at once; the master is closed and the session
  needs `wait_ready`. The payload must not exec after a damage step (section 1) and reports its own
  progress, for example to `/dev/ttyS0` (into `serial.log`). Detaching relies on logind
  `KillUserProcesses=no`, the Debian default.
- **`reset`** is QMP `system_reset` on a short-lived `QmpClient` at
  `unix_socket_client_path(profile, state_dir, "qmp.sock", "qmp")` (hence the `profile` argument;
  `QmpClient` has no long-path alias). QMP serves one client at a time, so no connection is held
  across a wait. The master is dropped; the session needs `wait_ready`.
- **`send_keys`** validates the whole sequence (`vmscreen.keys.parse_keys`/`validate_keys`), then
  `send_sequence` on a short-lived `QmpClient`.
- **`serial_mark` / `wait_serial`**: `serial_mark()` is `make_mark(serial.log)` (offset, inode, head
  and tail hashes: truncate-and-regrow is caught); `wait_serial` polls `read_serial(serial.log,
  mark=since)`, rendered by `serialtext.render`. Patterns are line-anchored, on rendered text. A
  stale mark becomes `GuestError`. No local ANSI handling.
- **`serial_write`** raises `SerialInputUnavailable` unless `runner.json` has `serial_console: true`
  (only then does `serial.sock` exist), then connects via `unix_socket_client_path`, writes, closes.
  ENOENT and ECONNREFUSED are retried until the deadline; any other `OSError` (`ENAMETOOLONG`) fails.
- **`press_until`** sends `keys` every `every` seconds until `until` matches on serial (or `fail_on`
  raises): the generic part of blind menu selection.

**Generic (vm-lab) versus scenario (debian-rollback).** vm-lab owns the transport, the boot_id rule,
the serial watch, reset, keys, `press_until`, `ssh_endpoint` and `attached_to_network`.
debian-rollback owns every pattern and policy: the init-failure and GRUB banner expressions, which
key sequence picks which entry (computed from `bootctl --json` or the asserted order, B12), "reset
after a panic and press again", the damage-and-reboot payloads, `dr-state` calls, PASS/FAIL checks,
key generation and host-key recording for its bases, and the `ROOT_SSH_AUTHORIZED_KEY` line.
Evidence for P-rollback-33 is `serial.log` plus the command transcript.

## 3. What moves and what is deleted

Moves into `vm_lab.guest` (rewritten for the new channel, with their tests):

- the boot_id reboot proof, extended by the "old boot still answers" guard;
- the connect-error split (transient ENOENT/ECONNREFUSED versus permanent), now for the serial
  write path;
- the anchored-prompt lesson from `console_prompt_kind`: serial patterns are line-anchored and run
  on vmscreen-rendered text; the kernel build banner case becomes a `wait_serial` glue test;
- the built-ins-only reboot of `force_reboot_to_init_failure`, generalised into the one-payload rule;
- QMP reset and `send-key`, on vmscreen's `QmpClient` instead of `qmp_command`.

Deleted from `dr-serial.py` once commands no longer go over serial: `qmp_command`, the `Guest` class
(`connect`, `_pump`, `send`, `expect`, `_shell_answers`, `_do_login`, `login`, `run`, `boot_id`,
`reboot`, `force_reboot_to_init_failure`, `select_older_entry_from_boot_menu`), `END_RE`,
`marker_wrapped_command`, `console_prompt_kind` and its prompt expressions, `DR_LOGIN_USER` and
`DR_LOGIN_PASSWORD`, the `$`/backtick/parenthesis filter in `tags()` (nothing is echoed any more; a
plain `TAG:` reader stays until P-rollback-26's `--json` reads replace it), the base64 heredoc
transfer (replaced by `run(..., input=payload)`), and the `live-inspect` scenario (a read-only look
at the Live ISO, which has no key; `vm-lab console serial` covers it by hand). The
scenario-specific sequences become short debian-rollback functions on the vm-lab API:

- `damage_and_reboot(g, damage)`: one `trigger` payload `sleep 1; exec perl -M<modules> -e
  <program>` (every module loaded before the damage). The program does the damage (rename plus
  directory fsync, as `move_package_file_command` does now),
  prints a progress line to `/dev/ttyS0`, then writes `s`, `u`, `b` to `/proc/sysrq-trigger` with
  `select`-based pauses: perl built-ins only, no exec after the damage. With a mark taken before, the
  driver then `wait_serial(INIT_FAILURE, fail_on=LOGIN)`s; that is `boot_to_init_failure`.
- `select_entry(g, downs)`: `press_until` with QMP reset on panic.

## 4. Test strategy

All fakes are boundaries with state; tests assert what the session reports and what the fake
machine ends up in, never the shape or order of calls.

- **`FakeMachine`**: boot id, booted entry, up/booting/down, host key, "damaged default" and
  "exec broken" flags. It appends boot text to a temporary `serial.log` as it boots, and fails with a
  panic line when damaged.
- **Fake QMP**: a unix-socket server speaking the QMP greeting and `qmp_capabilities`. `system_reset`
  reboots the machine (new boot id); `send-key` feeds a fake loader that moves a cursor and boots the
  selected entry on `ret`.
- **Fake SSH**: `ssh_program` points to a small executable test helper. It reads the machine state
  from a file, exits 255 with ssh's messages when the machine is down, exec is broken or the host key
  changed, and otherwise runs the remote string with the login shell's single parse and `bash -c`
  locally, so real exit status, stdout and stderr flow through a real subprocess. `-M`/`-O check`
  answer from the same state, including a master that notices a lost link late (`-O check` still
  succeeds once after the slave's 255).
- **Fake serial socket**: a server that records written bytes into the fake loader.

Required cases: rc of a failing command; syntax-broken commands (`false;`, `false &&`, `true ||`,
`# comment`) return their shell status and never time out (carried over from the marker-wrapper
tests); a bash-only command (`read -r -t 1 x </dev/null; echo $?`) behaves as in bash; a genuine
remote 255 versus a lost link, also with the late master exit (must be `GuestTransportError`);
host-key change; a command over its timeout; a started command is not retried after link loss;
reboot returns a new id and the old boot answering during shutdown is rejected; a damaged default
raises `BootFailure` well before the SSH deadline; after a damage payload every ssh connection is
refused and `boot_to_init_failure` still succeeds from serial alone; `press_until` boots the entry
the fake loader selected; a mark after log truncation; permanent versus transient serial-socket
errors; `serial_write` without `--serial-console` raises `SerialInputUnavailable`; `send_keys` with
one unknown key sends nothing. Serial rendering itself is vmscreen's and is not retested here.

Carried over from `test-dr-serial.py` (as the cases above): exit status carried, the eval-wrapper
syntax cases, permanent and transient socket errors, kernel banner not a prompt, existing-shell
attach (as "`wait_ready` on a booted guest returns without a reboot"), the boot_id rule. The split
marker test has no successor: a subprocess pipe has no split reads, and split serial text is
vmscreen's. Scenario tests that remain after P-rollback-26 run on a debian-rollback fake built on the
same fake machine, asserting the resulting state.

**Decision (deviation from the review's Keep list and B12's "protocol tests kept"):** the marker-echo
test, the three login-handshake tests and the echoed-command `tags()` tests are deleted with the code
they test, because their bug class (a tty echoing input, in-band framing, prompts) cannot occur on
the new channel. Their lessons are kept as the carried-over cases above. P-31c's acceptance states
it: those tests are gone, every carried-over case exists in vm-lab and passes, and dr-serial has no
serial command path left that they could guard.

## 5. Migration and packages

The switch is one cut-over, not a dual transport: two channels in the driver would be two code
paths to keep correct. The matrix keeps working because the old driver stays on main until new
key-bearing bases exist and the new driver has passed on them.

1. **P-31a vm-lab `vm_lab.guest` (M, about 450 lines of source and 550 of tests).** Module,
   `attached_to_network`, fakes, tests, README section "Drive a guest from Python", the three
   vmscreen-import corrections of section 2. No launcher change: `runner.json` already records
   `serial_console` and `<fwd>_guest_port` for CLI-sized profiles, and fixed profiles carry their guest
   ports in `VmProfile.forwards`. No change for existing consumers. Gates vm-lab `r0-r1` + `wheel`.
   Starts after P-rollback-30 is merged (it changes the same README and start code).
2. **P-31b debian-rollback install key (S).** Install-config key `ROOT_SSH_AUTHORIZED_KEY` (one
   OpenSSH public key, validated by `dr-plan --check-config`), written to `/root/.ssh/authorized_keys`
   (0600) by `install-btrfs.sh`; the same option on the NoCloud seed path. At the end of the install
   the installer prints the target's host public keys as `DR-HOSTKEY <type> <base64>` marker lines on
   the console. Offline tests compare the installed file and the marker lines. Gate `offline`. After
   P-rollback-25, which moves the installer payload.
3. **Base rebuild (host time, no code).** Once 31b, P-rollback-24, -25 and -26 are on main, build the
   ISO from the head P-31c is tested against and install GRUB and systemd-boot bases with a generated
   ed25519 key pair. The rebuild records the `DR-HOSTKEY` lines from the install run's `serial.log`
   as the base's host keys, beside the base with the key pair; no first-contact trust is needed. The
   private key is never in the repository or in a state directory: `ssh_endpoint` copies it into the
   driver's private 0700 directory at 0600. If the live run must happen earlier, against bases from
   an older head, it is dev evidence with hot-patching (B15), not release evidence.
4. **P-31c dr-serial switch (M).** Scenarios take a `vm_lab.guest.Guest`; the command line becomes
   `--state-dir DIR --credentials DIR SCENARIO`; the deletions of section 3; recovery scenarios use
   `damage_and_reboot`; imports `vm-lab/src` and `libraries/vmscreen/src` relative to its own path, so
   a bare checkout works. Acceptance: `dr-serial.py` has no `socket` import, no QMP client and no
   `Guest` class; the test decision of section 4 holds; offline tests pass; boot-check, s2-s3, s5,
   recovery-kernel and recovery-library pass live on the rebuilt bases under a lease, with
   `serial.log` and the transcript archived; neither contains private key material (a check, also a
   P-rollback-33 acceptance line). Gates `offline` + vm-lab `r0-r1`. Depends on 31a, 31b, the rebuild
   and P-rollback-26 (both rewrite the same file; transport after assertions). Closes VML-BL-08.

P-rollback-33 (matrix runner) builds on 31c; the old bases and serial driver retire with 31c's merge.
Follow-up (vm-lab backlog): `core.qmp_command` moves onto `QmpClient` with the short-path alias.

## 6. Unknowns to measure (in the 31c live check unless noted)

- Reset to first SSH answer under TCG on a 2-vCPU guest, against the serial `login:` prompt (sshd
  waits for DHCP); per-command latency with and without the master.
- How long the old boot's sshd answers after `systemctl reboot` (sizes the poll; correctness rests
  on the boot_id rule).
- Longest link silence and master-establishment time under TCG load, against the 120 s
  (`ServerAliveInterval=5` x 24) and 30 s budgets; budgets are raised, retries never added.
- Container address reachable from a plain host, from a cockpit container through
  `attached_to_network`, and with `--internal` (31a, one probe).
- Host keys identical across states after a rollback (expected; a difference is a product finding).
- The damaged state: after the library payload ssh is refused (expected), serial shows the
  payload's progress line before the init failure, and sshd answers after the recovery boot; same
  for kernel and dependency damage. The detached payload survives its ssh session.
- GRUB input over short-lived serial-socket connections, or QMP `send-key` reaching GRUB.
- `ssh` and a short private `ControlPath` directory in the driver's environment.
- Only if a no-network consumer brings QGA back: `qemu-guest-agent` in a Debian 13 test image and
  virtio-serial under TCG.
