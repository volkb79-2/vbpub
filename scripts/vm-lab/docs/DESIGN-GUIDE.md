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

## A profile instead of globals

Everything product-specific is a field of a frozen `VmProfile`, passed
explicitly to each function that needs it. The earlier file had these as module
constants. Constants worked for one product. With two, the choice would need a
module-level "current profile" or an environment variable, and either one can be
wrong without any visible sign. An explicit argument cannot.

The cost is a profile argument on the functions that use labels, names or
sizing. The benefit is that the TrueNAS values live in one place, in
`tcg_vm.py`. Two TrueNAS traces stay in `vm_lab.py` on purpose: the TrueNAS
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
  `KNOWN_VM_LABELS` in `vm_lab.py` is the one registry. Each entry is a
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
one owning project, and its files live in that project's `artifacts/vm-lab/`.
The generic command line therefore requires `--state-dir` on every verb that
takes it. TrueNAS keeps its older default of `artifacts/vm-lab/<name>` beside
`tcg_vm.py`, filled in by the TrueNAS front.

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
refused rather than overwritten, and the file is removed after every read and
in the recorder's final cleanup, including error exits. Pillow is imported
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

## No process killing

The former standalone launcher owned its QEMU process and kept a pidfile so it
could signal QEMU as a last resort. The VM tool starts QEMU in a managed
container and has a graceful `stop` verb. `--stop-guest-on-exit` calls that
same stop path for cap, max-runtime and error exits, but not for an explicit
`console ctl --quit`, which only ends the recorder. Without the option the
guest remains running. The recorder never kills a pid.

## The display device is an explicit choice

Standard VGA and `virtio-vga` are not interchangeable for every guest:
TrueNAS's kernel console produced garbage screendumps with standard VGA under
QEMU 10. A silent default would make every consumer inherit whichever choice
the first consumer needed. Generic starts therefore require
`--display-device {std,virtio}`. TrueNAS keeps its historical `std` device,
and its fixed profile does not expose the new option. `virtio` adds the same
`-device virtio-vga` the removed private launcher used.

## Screen text: two engines, one poll loop

`console text` and `console wait-for-text` (module `vm_lab_text.py`) read the
screen as text with two engines, because no single reader covers every screen:

- **vmscreen** reads the VGA text buffer through QMP and cross-checks it
  against a screendump, so the text is exact, not recognised. It applies only
  while the guest is in an 80+ column VGA text mode (SeaBIOS, a boot loader
  that stays in text mode); not for Linux's framebuffer console.
- **OCR** takes a PNG screendump through the VM tool's own QMP path
  (`/state/.vm-lab-console-text.png`, refused if it exists, removed in every
  path) and recognises it with `pwmcp ocr`. It works for graphical screens
  (UEFI GOP, framebuffer console, themed GRUB) but is only as good as the OCR,
  so a pattern that triggers a keystroke should be a distinctive line.

`auto` tries vmscreen first and uses OCR where vmscreen reports that no exact
source applies or that its text is unverified. If vmscreen cannot be started
at all, `auto` says so once on stderr and uses OCR only; with
`--engine vmscreen` that is an error.

**Why vmscreen runs as a subprocess from source.** The VM tool is a script
with no dependencies; making it import vmscreen would give it vmscreen's
dependency (`cli-extended`) and a wheel to install and keep in step. Running
`python -m vmscreen` with `PYTHONPATH=<repo>/libraries/vmscreen/src` needs only
the checkout, and a Python without `cli-extended` degrades to OCR instead of
breaking the other verbs. Known limit: vmscreen's CLI resolves its version
from the installed `vmscreen` distribution and refuses to run from a bare
source tree ("distribution 'vmscreen' is not installed"), so the exact-text
engine works only in a Python where vmscreen is installed (an editable install
of `libraries/vmscreen` is enough; the source directory on `PYTHONPATH` then
decides which code runs). Elsewhere `auto` uses OCR and says why. A subprocess also keeps QMP strictly sequential:
QMP serves one client, so the VM tool never holds a connection while vmscreen
connects, and the engines run one after the other.

**Order of checks per poll.** Read the screen; `PATTERN` first (a match wins
over everything else on the same screen); the fail patterns; the boot-loop
counter; the timeout; then sleep. The loop counter counts transitions from
"not on the screen" to "on the screen", so a boot menu that stays up for many
polls counts once; a skipped (failed) poll leaves the state unchanged, a poll
on which the forced engine cannot read the screen counts as "not on the
screen". The last sleep is shortened to the deadline so the timeout is not
overshot, and there is one last poll at the deadline.

**Failures.** A failed read is skipped and counted; ten in a row end the wait
with exit 1 and the last error. An engine that cannot work from the start is
an immediate exit 1 instead of ten retries: vmscreen that cannot start with
`--engine vmscreen`, `pwmcp` missing from PATH, or a `pwmcp ocr` call that
fails before OCR has ever worked (typically: the sidecar is not enabled). The
exit codes (0 matched, 1 error, 2 usage, 3 timeout, 4 fail pattern, 5 boot
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
