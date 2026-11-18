# vm-lab design guide

## Why it exists

`scripts/truenas/tcg_vm.py` launches QEMU guests without KVM inside a Docker
container, so TCG CPU use and guest memory stay out of the interactive
devcontainer. A second project, Debian snapshot and rollback, needs the same
launcher with Debian guests. Copying the file would give two launchers to keep
correct. So the generic part moved here, and TrueNAS became one consumer.

TrueNAS behavior did not change. Its command line, messages, exit codes, Docker
arguments and QEMU command line are the same as before the move. Its tests
still run unchanged except for the module that names are imported from and
patched on.

## A top-level project, a package and an image (2026-11-17)

The tool outgrew `scripts/`: it ships a CLI and a container image, depends on the
`vmscreen` wheel, is consumed by several projects (TrueNAS, debian-rollback) and
is installed into the mdt-cockpit image as a wheel. It is therefore a project of
its own, `vm-lab/`, with the package `vm_lab` in `src/` and the console script
`vm-lab`. Decisions:

- **Three modules, one package.** `core` (profiles, admission, lifecycle, disks,
  the parser), `console` (QMP recorder, typing), `text` (screen text). `__init__`
  re-exports only what profile fronts import by name; tests that replace a
  function patch `vm_lab.core`, because the functions call each other there.
- **Bare checkouts keep working.** The wrapper `vm-lab/vm-lab` sets `PYTHONPATH`
  relative to itself, like `libraries/cowtree/repo-path`; the TrueNAS front puts
  `vm-lab/src` on `sys.path` from its own location. Neither needs an install.
- **The image is built, not pulled.** `src/vm_lab/qemu-image/Dockerfile` (package data, so the wheel carries it) carries the same
  plain package set as the debian-install-v2 test-VM image (QEMU for TCG, no KVM,
  no privileges) minus what only that harness uses. `vm-lab image build` runs
  `docker build` on it (default tag `vm-lab-qemu:local`). OVMF is not in the image:
  the caller's host files are bind-mounted, so firmware changes need no rebuild.
  `start` resolves the image as flag, then `VM_LAB_QEMU_IMAGE`, then the local
  tag if Docker has it, else it refuses and says to build; an unreachable Docker
  is reported as unreachable, not as "not built". Only the generic profile has
  the tag fallback; the TrueNAS front names its image through its own variable.
- **The admission lock works from a wheel.** The lock file is found by asking Git
  next to the module, which only a checkout answers. When that fails, the
  directory comes from `--lock-dir` or `VM_LAB_ADMISSION_LOCK_DIR`, validated
  (absolute, existing, writable), else the start is refused naming both: no
  default path is invented, because a lock in a guessed directory would silently
  stop excluding the other starters. The probe stays first so that checkout,
  wrapper, editable and TrueNAS use cannot change; the file name stays the one
  shared historical name. Two starters exclude each other only when they resolve
  the same directory, so the cockpit image sets the variable to the repository's
  common Git directory.
- **No paths in the package.** Every verb takes `--state-dir` and `--base-disk`;
  consumers derive them from the path resolver (`cowtree.toml`, `repo-path`), and
  the package does not import cowtree.
- **Environment defaults stay** (`DOCKER_NETWORK_INTERNAL`,
  `CGROUP_PARENT_DEV_BACKGROUND`), and a missing network says where it comes from
  instead of only naming the variable, because the fix is to read the value
  (`ciu env print`), not to invent one.
- **Not part of this move:** release registration (cmru, assay lane), a vmscreen
  version floor, a ciu service for the image. They are open items in
  `KNOWN_ISSUES_TODO_BACKLOG.md`.

## A profile instead of globals

Everything product-specific is a field of a frozen `VmProfile`, passed
explicitly to each function that needs it. The earlier file had these as module
constants. Constants worked for one product. With two, the choice would need a
module-level "current profile" or an environment variable, and either one can be
wrong without any visible sign. An explicit argument cannot.

The cost is a profile argument on the functions that use labels, names or
sizing. The benefit is that the TrueNAS values live in one place, in
`tcg_vm.py`. Two TrueNAS traces stay in `vm_lab.core` on purpose: the TrueNAS
entry in the label registry and the historical lock filename. Both exist
because the host cap is shared (see below).

`tcg_vm.py` keeps its old module-level names and function signatures, bound to
the TrueNAS profile. Other files import from it as before.

## One lock and one cap for every profile

The host is shared with a production workload. Two launchers that each count
only their own guests could together exceed the safe number of TCG guests.

- There is one start lock file. Its path and name are unchanged
  (`truenas-tcg-vm-admission.lock` in the common Git directory). The name is
  historical: a TrueNAS start must keep taking the same lock a vm-lab start
  takes, and renaming the file would split them into two locks. Every profile
  takes it, so two starts never pass the count and pressure checks together.
- Admission counts running containers that carry any known VM label.
  `KNOWN_VM_LABELS` in `vm_lab.core` is the one registry. Each entry is a
  `(label key, label value)` pair and is queried with its own value, so a
  profile whose value is not `true` is still counted. The registry lists the
  TrueNAS label because a TrueNAS guest that is already running must keep being
  counted against the same cap. A new profile must add its pair, and start
  refuses a profile whose pair is missing, so its guests can never go uncounted.
- Verbs that act on one guest (`status`, `stop`, `console`, `destroy`, and the
  rest) still require the calling profile's own labels. One tool cannot stop or
  destroy another product's guest by mistake.

## Sizing is required, not defaulted

A launcher that quietly picks 3 GiB and 2 CPUs gives every consumer the
TrueNAS answer. A wrong default here is a host-capacity decision made by
accident. So the generic command line has no default for `--memory-mib`,
`--cpus` or `--forward`. Each is required, and each is range checked
(512 to 8192 MiB, 1 to 4 CPUs, valid ports).

The container memory limit is guest RAM plus 512 MiB for QEMU overhead. With
swap disabled, a guest that outgrows the limit is killed by the kernel, not
slowed. 3072 MiB gives 3,758,096,384 bytes, which is the TrueNAS value, so the
rule reproduces the old constant. The memory-headroom admission check uses the
computed limit.

A profile may instead fix its sizing. TrueNAS does. That keeps the TrueNAS
front free of new options.

## State belongs to the consumer

The tool takes `--state-dir` and keeps nothing of its own. Each VM has exactly
one owning project, and its files live in that project's `vm-lab/`.
The generic command line therefore requires `--state-dir` on every verb that
takes it. TrueNAS keeps its older default of `artifacts/vm-lab/<name>` beside
`tcg_vm.py`, filled in by the TrueNAS front, until it moves to `vm-lab/`
(mdt-cockpit MDT-044 follow-up).

## Forwards are not published

QEMU user-mode networking listens inside the container. A forwarded port is
reachable at the container's address on its Docker network, from containers on
the same network. The launcher adds no `--publish`, so nothing is exposed on the
host. This is the TrueNAS behavior, kept for every profile. A consumer that
needs host exposure must ask for it as a separate, reviewed change.

## `console reset`

Sends QMP `system_reset`. It checks the guest the same way as `sendkey`: managed
by this profile, state directory matches, container running. It is also on the
TrueNAS front. It is the only addition to the TrueNAS command line.

## Console QMP connections are short-lived

QMP serves one client. The former pwmcp recorder held that client for its
whole run, so the VM tool's own `sendkey`, `reset`, `screendump` and `stop`
verbs could not work at the same time. The recorder now opens one QMP
connection per poll and one per queued command, uses it, and closes it. Every
call has the recorder's `--qmp-timeout` (30 seconds by default).

This also lets `console type` and `console ctl` coexist with the recorder.
The cost is one local Unix-socket connect per poll, which is negligible next
to a screendump and image comparison.

## Recorder frames pass through the state directory

QEMU runs in a container with only `/state` writable. A host-side output path
inside `--out` is therefore not visible to QEMU. The recorder asks QEMU for a
PNG at the hidden temporary name `/state/.vm-lab-console-record.png`, reads
it with Pillow as RGB, and saves a kept frame in `DIR/frames/`.

The hidden name cannot be requested by `console screendump`, whose filename
rules require an ordinary visible name. An already existing temporary file is
refused rather than overwritten. Each poll creates the file itself with
`O_EXCL` (mode 0600; QEMU runs as the same user, so the recorder must too, and
overwrites it in place) and remembers its device and inode. If QEMU replaced the
file by a rename, a regular file of the recorder's own uid at the name is adopted
after the successful screendump; anything else stays foreign. The file is removed after every read and in the
recorder's final cleanup, including error exits, but only while the path still
has that identity: a file another recorder or process put at the name is never
deleted, and at a poll it makes that poll a skipped one. Pillow is imported
only by `console record`; the other console verbs do not need it.

Transient console failures are tolerated by skipping and counting. A QMP
screendump error or an unreadable/truncated PNG skips one poll and sleeps for
the normal interval; any successful poll resets the counter, while ten
consecutive failures end the recording with an explicit reason. This matches
the old recorder's behavior during mode changes and boot transitions without
letting a dead screen path spin forever. A queued command failure is recorded
in `keys.jsonl` and does not stop the recording. Stop-on-exit is armed only
when the validated recorder loop is ready to run: refusing a conflicting
temporary file or failing to import Pillow must not power down a guest that
this invocation never recorded.

## Two key tables, one set of names

The key table behind `console type` (US-layout ASCII to QEMU key names, in
`vm_lab.console`) and the key table of `libraries/vmscreen` (its `send` verb)
are deliberately two copies: the VM tool does not import vmscreen (it runs it as a
subprocess for screen text), and vmscreen keeps its own. Both tables must list the same key names. The tests
cannot compare them mechanically: `libraries/vmscreen/docs/cli-surface.json`
names the `--keys` option but not the key names, so the rule is stated here and
checked by review when either table changes (VMS-BL-03).

## No process killing

The former standalone launcher owned its QEMU process and kept a pidfile so it
could signal QEMU as a last resort. The VM tool starts QEMU in a managed
container and has a graceful `stop` verb. `--stop-guest-on-exit` calls that
same stop path for cap, max-runtime and error exits, but not for an explicit
`console ctl --quit`, which only ends the recorder. Without the option the
guest remains running. The recorder never kills a pid. SIGTERM and SIGHUP end
the recording cleanly; the first one claims the stop, and every further
SIGTERM or SIGHUP is ignored until the guest stop and `DONE` are finished, so a
repeated signal (a supervisor that retries) cannot abort the stop and leave the
guest running. A failed stop is reported on stderr and never changes the
`DONE` reason or the exit status.

## The display device is an explicit choice

Standard VGA and `virtio-vga` are not interchangeable for every guest:
TrueNAS's kernel console produced garbage screendumps with standard VGA under
QEMU 10. A silent default would make every consumer inherit whichever choice
the first consumer needed. Generic starts therefore require
`--display-device {std,virtio}`. TrueNAS keeps its historical `std` device,
and its fixed profile does not expose the new option. `virtio` adds the same
`-device virtio-vga` the removed private launcher used.

## Raw bases and reflink clones

Decided 2026-11-17 (AGENTS.md "Directory conventions and VM files"). `start`
has two disk models, chosen by `--base-format` (default `auto`: the first four
bytes, `QFI\xfb` is qcow2, anything else raw).

- **qcow2 base (unchanged).** The base is bind-mounted read-only into the QEMU
  container and the guest writes to a qcow2 overlay in the state directory. The
  generated script and the `docker run` arguments are byte for byte what they
  were, because TrueNAS depends on them.
- **raw base.** The guest's disk is `<state-dir>/disk.raw`, a reflink copy of the
  base made once on the host with `cp --reflink=always -- <base> <state>/disk.raw`.

Why raw: on btrfs a raw file reflinked from a base costs no space and no time
until the guest writes, needs no `qemu-img` chain to keep alive, and a clone is
an ordinary file that can be copied, promoted or deleted on its own. A qcow2
overlay ties the guest to its backing file for good.

Why the base is not mounted: nothing in the container needs it. Mounting it
would let a run (or a QEMU bug) open the shared base for writing, and would tie
the run to a bind-mount path that has to be translated to a host path. The base
is only ever a `cp` source, so the container gets one writable mount, the state
directory. A symlink is refused as a raw base, so the file that is copied is the
file that was named.

Why `cache=unsafe` always: a raw run disk is disposable. Relaxed fsync is the
estate default for such disks. A host crash may corrupt one; killing QEMU does
not, because the host page cache survives it. Anything that judges storage
durability must not use this model.

Why the clone failure is loud: `cp --reflink=always` fails on a different
filesystem or one without reflinks (`Invalid cross-device link`, `Operation not
supported`). Silently falling back to a full copy would turn an instant clone
into minutes of IO and a doubled disk footprint, and would hide a misplaced
base. `start` exits 1 with one line naming both paths and the cause and removes
any partial `disk.raw`, so the state directory is still empty for a retry.
`--allow-full-copy` is the explicit opt-in: it retries once with
`--reflink=auto` and prints `[disk] full copy of <base> (no reflink possible)`.
The clone happens before firmware is staged or `runner.json` is written, and
only on a first start; `--resume` reuses `disk.raw`. The copy is written to
`.disk.raw.partial` (`.<name>.raw.partial` for a promote) under umask 0077 and
renamed onto its final name only after `cp` succeeded and the size matches, and
the temporary file is removed on any failure that raises an exception,
including Ctrl-C, so the final name is either complete or absent and never
exists with the base's mode. `disk promote` turns SIGTERM and SIGHUP into an
exception for the length of the command (first signal only; later ones are
ignored during cleanup; handlers restored afterwards), so a killed promote
removes `<name>.raw`, the digest and both partials like Ctrl-C does. `start` does
not: a start killed by SIGTERM, and any command killed by SIGKILL, leaves the
hidden `.<name>.partial` behind, a retry refuses it, and the operator removes it
by hand (the refusal says so). Wrapping `start` was rejected as machinery for a
rare case; it runs the same copy but its state directory is still empty for a retry.
A full copy of a large base holds the start admission lock for its whole
duration (up to the one-hour copy timeout), so another `start` is refused as
"in progress" meanwhile; a reflink is instant and does not.

State: `runner.json` records `disk_format`. For raw it records the base path and
its size for information only: a raw guest no longer depends on the base file
once it is cloned, so a resume ignores those two values and works even when the
base was renamed, replaced or deleted (everything else, including the format,
is still compared). A qcow2 guest records the base's device, inode, size and
mtime, and a changed base blocks its resume. A guest
recorded before this change has no `disk_format` and resumes as qcow2. A resume
whose recorded and requested formats differ is refused, naming both. The
fixed-size TrueNAS front keeps qcow2 as its default and does not open the base
file unless `--base-format` is given.

Why `disk promote` needs a stopped guest: QEMU holds the raw file open and
writes to it, so a reflink of a running guest's disk is a crash-consistent
snapshot at best and a torn image at worst. The check looks for any running
managed container, of any profile, whose state label matches the state
directory; a Docker failure while asking is an error, not "stopped". The check
and the copy run under the same admission lock a `start` takes, so a
`start --resume` cannot boot the disk in between. The lock is held until the
promotion is finished, that is also while the copied file is hashed (a read of
the whole image), so every `start` is refused as "in progress" for that time;
the lock is simple and the wait is the cost of a stable check. Promotion
reflinks `disk.raw` to `<dest>/<name>.raw`, hashes the final file (streamed in
1 MiB chunks, so the digest is of the bytes that were written), writes
`<name>.raw.sha256` in `sha256sum` format and sets both to 0444. It refuses to
overwrite and removes both files if any step fails, so a half-promoted base
cannot be mistaken for a good one.

## Screen text: three engines, one poll loop

`console text` and `console wait-for-text` (module `vm_lab.text`) read the
screen as text with three engines, because no single reader covers every screen:

- **serial** reads `<state>/serial.log` with `vmscreen text --source serial`.
  **Why it is the exact-text source of UEFI guests:** OVMF shows a graphical
  framebuffer (GOP), never the VGA text buffer, so the vmscreen VGA engine
  finds no text and OCR needs a sidecar; but a guest with its console on
  ttyS0 prints everything it says, exactly, into the serial log. vmscreen
  already renders the stream (escape sequences stripped, CR/BS applied), so the
  VM tool adds no terminal handling of its own. **When `auto` uses it:** QEMU is
  given `-serial file:/state/serial.log` for EVERY guest, so the file existing
  says nothing about `--serial-console`; a TrueNAS or BIOS guest that merely
  prints to ttyS0 would otherwise shadow the VGA and OCR engines on every poll.
  The launcher therefore records `"serial_console": true|false` in `runner.json`
  at each start (it is a record, not part of the resume identity; a resume
  rewrites it to the value of that start), and `auto` uses the serial engine
  only when it says true and the log exists. A state directory without the
  record means false. `--engine serial` is the explicit choice and works for any
  guest whose log exists. `auto` also needs text in the log (after the mark): an
  empty result means "not yet", never a wait failure by itself, so the next
  engines get their turn.
  vmscreen connects to QMP for every `text` call, serial included, so the guest
  must be running.

- **vmscreen** reads the VGA text buffer through QMP and cross-checks it
  against a screendump, so the text is exact, not recognised. It applies only
  while the guest is in an 80+ column VGA text mode (SeaBIOS, a boot loader
  that stays in text mode); not for Linux's framebuffer console.
- **OCR** takes a PNG screendump through the VM tool's own QMP path
  (`/state/.vm-lab-console-text.png`, refused if it exists, removed in every
  path) and recognises it with `pwmcp ocr`. It works for graphical screens
  (UEFI GOP, framebuffer console, themed GRUB) but is only as good as the OCR,
  so a pattern that triggers a keystroke should be a distinctive line.

**Engine order.** `auto` tries serial first (exact and cheap: a file read), then
vmscreen, then OCR (the only one that needs a sidecar and a screendump). It uses
OCR where vmscreen reports that no exact source applies or that its text is
unverified. If vmscreen cannot be started at all, `auto` says so once on stderr
and uses OCR only (the serial engine also runs through vmscreen, so it is off
too); with `--engine vmscreen` or `--engine serial` that is an error. When the
log exists but is still quiet and OCR cannot work, `auto` returns the empty
serial text with one stderr note (vmscreen's own last-resort rule) so a wait
keeps polling.

**Mark semantics.** A serial log accumulates across boots and resets, so a wait
for `login:` can be satisfied by the previous boot. `console mark` prints
vmscreen's mark of the log's current end (offset plus inode and head/tail
fingerprints, so a truncated, replaced or rewritten log is refused rather than
re-read from a wrong offset); `--since-mark` passes it to every serial read.
A `wait-for-text` without a mark takes one at its start, before its first read
(a failing implicit mark is an error, never a silent unmarked wait), so with
`--engine serial` it sees only text written after the wait began. In `auto` that
promise is weaker: a quiet serial log falls through to VGA and OCR, which read
the current screen and may still show text from before the wait; a strict wait
uses `--engine serial`. A mark whose log was truncated or replaced makes vmscreen
exit 4, the same code as "no source applies"; the VM tool tells them apart by
vmscreen's reason and fails closed (exit 1, "the serial log was truncated or
replaced since the mark was taken") for an explicit or implicit mark, in `auto`
and forced alike, because such a mark can never apply again. While serial
applies, each `auto` poll costs one extra vmscreen call (a QMP connection)
before VGA and OCR. `--since-start` was deliberately not
added: the implicit mark is that, and a second spelling would only invite a
disagreement about "start". Only the serial engine can honour a mark; the others
ignore an explicit one with one stderr note. A mark taken after the action it
should precede loses that action's early output: take it first.

**Why vmscreen is a subprocess, and where it comes from.** The VM tool is a
script with no dependencies; making it import vmscreen would give it
vmscreen's dependency (`cli-extended`) and tie its import path to a wheel. As a
subprocess, a machine without vmscreen degrades to OCR instead of breaking the
other verbs, and QMP stays strictly sequential: QMP serves one client, so the VM
tool never holds a connection while vmscreen connects, and the engines run one
after the other.

vmscreen must be **installed**: `pip install` of `libraries/vmscreen` (an
editable install is enough). It does not run from a bare source tree, because
its CLI takes its version from the installed `vmscreen` distribution
("distribution 'vmscreen' is not installed"); the VM tool does not paper over
that by editing `PYTHONPATH`. Once per run the tool probes the `vmscreen`
executable on PATH with `text --help` (that must exit 0 and list
`--qemu-dump-dir`, so a missing install and an older copy without the option,
added by VML-03a, are both failures with a reason). If that probe fails, or
there is no such executable, it probes `python -m vmscreen` of the Python that
runs the VM tool the same way: a stale or broken script on PATH must not hide a
working module install. The first form that passes is kept. Only when both fail
is vmscreen "not available"; `auto` then says so once (naming both reasons) and
uses OCR, and `--engine vmscreen` is an error. Nothing is probed again, and
`--engine ocr` never starts the probe.

**Order of checks per poll.** Read the screen; `PATTERN` first (a match wins
over everything else on the same screen); the fail patterns; the boot-loop
counter; the timeout; then sleep. The loop counter counts transitions from
"not on the screen" to "on the screen". Only a successfully read screen moves
that state: a menu that stays up for many polls counts once, and a poll that
failed or on which the forced engine cannot read the screen (unverified,
graphical) is "unknown" and leaves the state unchanged. In `auto` the engine
may change between polls (vmscreen, then OCR); an OCR misreading of the loop
pattern can then look like the menu going away and coming back, so prefer a
pattern OCR reads reliably, or force one engine. The last sleep is shortened to
the deadline and there is one last poll at the deadline. Every external call
of a poll (the vmscreen probe and run, the QMP screendump, `pwmcp`) is bounded
by the time left to the deadline, with a floor of one second; a subprocess that
hits its limit is a skipped poll, so a hung reader ends in exit 3 shortly
after the deadline. The bound is re-taken before each call of a poll (the
reader turns the time left at the start of the poll into a deadline and
subtracts what earlier calls of the same poll used), so a slow first call
shrinks the next one; the one-second floor can still add up to one second per
call after the deadline has passed. Usage errors (exit 2) print no
JSON object even with `--json`. `console text` has no deadline and keeps the fixed
defaults (30 s probe/QMP, 90 s vmscreen, 120 s pwmcp).

**Failures.** A failed read is skipped and counted; ten in a row end the wait
with exit 1 and the last error. An engine that cannot work from the start is
an immediate exit 1 instead of ten retries: vmscreen that cannot start with
`--engine vmscreen`, `pwmcp` missing from PATH, or a `pwmcp ocr` call that
fails before OCR has ever worked (typically: the sidecar is not enabled). That
last rule is by design: a transient failure (a busy sidecar) on the very first
OCR call is also an immediate error, because it cannot be told from a sidecar
that was never enabled; after one successful OCR read, `pwmcp` failures are
skipped polls (exit 2 of `pwmcp`, "no sidecar configured", is always immediate).
Every error ends as one `vm-lab: <message>` line on stderr and exit 1, and with
`--json` also as one `{"result": "error", "error": "<message>"}` object on
stdout (both verbs). The exit codes (0 matched, 1 error, 2 usage, 3 timeout, 4 fail pattern, 5 boot
loop) are module constants and are named in both verbs' `--help`.

## Open items (not built yet)

- None for the console verbs: boot-loop detection is `console wait-for-text
  --loop-pattern`.
- Failure injection: no verb cuts power, drops a disk, or corrupts the overlay
  on purpose.
- Serial boot-menu setup: nothing puts a boot menu on the serial line, so a menu
  can only be checked by screenshot (`console screendump`) and keys
  (`console sendkey`).
- The Debian install harness (`scripts/debian-install-v2/testing/vm/vmctl`,
  BIOS only, no keyboard control) can move to this tool later.
