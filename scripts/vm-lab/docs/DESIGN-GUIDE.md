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
`tcg_vm.py`, and nothing in `vm_lab.py` names TrueNAS except one label constant
(see the shared cap below).

`tcg_vm.py` keeps its old module-level names and function signatures, bound to
the TrueNAS profile. Other files import from it as before.

## One lock and one cap for every profile

The host is shared with a production workload. Two launchers that each count
only their own guests could together exceed the safe number of TCG guests.

- There is one start lock file. Its path and name are unchanged
  (`truenas-tcg-vm-admission.lock` in the common Git directory). Every profile
  takes it, so two starts never pass the count and pressure checks together.
- Admission counts running containers that carry any known VM label.
  `ALL_VM_LABELS` in `vm_lab.py` lists them. It holds the TrueNAS label and the
  generic one. A new profile with its own label must be added to that tuple, or
  its guests would not count.
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

## Open items (not built yet)

- Boot-loop detection: nothing watches for a guest that resets repeatedly.
- Failure injection: no verb cuts power, drops a disk, or corrupts the overlay
  on purpose.
- Serial boot-menu setup: nothing puts a boot menu on the serial line, so a menu
  can only be checked by screenshot (`console screendump`) and keys
  (`console sendkey`).
- The Debian install harness (`scripts/debian-install-v2/testing/vm/vmctl`,
  BIOS only, no keyboard control) can move to this tool later.
