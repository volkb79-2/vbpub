# vm-lab

A general launcher for QEMU guests without KVM. Each guest runs in its own
Docker container under the host background slice, with UEFI firmware (OVMF), a
per-guest firmware variable store, a copy-on-write overlay over a read-only base
disk, optional extra disks and an ISO, and a QMP socket for keys, screenshots
and reset.

A *profile* carries every product-specific value (labels, names, sizing,
ports). `scripts/truenas/tcg_vm.py` is one profile front. A project without its
own front uses the command line here.

Two TrueNAS traces remain in the launcher, on purpose, because the host cap is
shared: the registry of known VM labels (`KNOWN_VM_LABELS`) lists the TrueNAS
label so TrueNAS guests are counted, and the start lock file keeps its
historical TrueNAS filename so a TrueNAS start and a vm-lab start take the same
lock. See the [design guide](docs/DESIGN-GUIDE.md#one-lock-and-one-cap-for-every-profile).

The tool owns no VM state. Every verb that touches a guest takes `--state-dir`,
and the project that owns the VM keeps that directory in its own
`scripts/<project>/artifacts/vm-lab/<vm>/`.

Start with the [consumer guide](docs/CONSUMERS.md). The reasons behind the
design are in the [design guide](docs/DESIGN-GUIDE.md).

## Command line

Run from the repository root:

```
python3 scripts/vm-lab/vm_lab.py <verb> [options]
```

| Verb | What it does |
| --- | --- |
| `start` | Admit and start a guest. Needs `--name`, `--base-disk`, `--ovmf-code`, `--ovmf-vars`, `--state-dir`, `--memory-mib`, `--cpus` and at least one `--forward`. |
| `status` | Print container and QMP state as JSON. |
| `stop` | Ask the guest to power down (ACPI) and wait. Never force-kills. |
| `terminate-paused` | Stop the QEMU process of a guest QEMU reports as paused. State is kept. |
| `destroy` | Remove a stopped container. Disk and firmware state are kept. |
| `rebind-media` | Point a stopped guest at an ISO with identical bytes at a new path. |
| `console screendump` | Save the current screen as a PPM file in the state directory. |
| `console sendkey KEY` | Send one QEMU key name or a hyphen-separated combination. |
| `console reset` | Hard-reset the guest (QMP `system_reset`), like the reset button. |
| `console serial` | Attach the terminal to the guest serial socket. Leave with Ctrl-]. |

`--state-dir` is required on every verb that takes it (all except `destroy`).

### `start` options

- `--memory-mib N`: guest RAM, 512 to 8192. The container memory limit is
  `N + 512` MiB with swap disabled. Required.
- `--cpus N`: guest CPUs, 1 to 4. Also the container CPU limit. Required.
- `--forward HOST:GUEST`: forward a TCP port of the container to a guest port.
  Repeat for more (up to 8). At least one is required. Nothing is forwarded by
  default. Ports are reachable at the container address on its Docker network.
  They are not published on the host.
- `--image`: QEMU image. Without it, the variable `VM_LAB_QEMU_IMAGE` is used.
  With neither, start refuses.
- `--network`, `--cgroup-parent`: default to `DOCKER_NETWORK_INTERNAL` and
  `CGROUP_PARENT_DEV_BACKGROUND`. Start refuses when neither is set.
- `--readonly-media ISO`, `--data-disk-size SIZE` (repeat, up to 8),
  `--vnc-console`, `--serial-console`, `--resume`.
- `--max-concurrent-guests N` (1 to 3, default 3) and
  `--memory-psi-full-avg10-limit`: see the admission rules below.

## Host protection

Start refuses unless all of these hold: the systemd slice is loaded and has a
finite `memory.max`; memory pressure (PSI) at the slice and its ancestors is
under policy; the slice CPU quota allows `--cpus`; the Docker network and image
exist; and fewer than the allowed number of guests are running.

All profiles share one start lock (in the repository's common Git directory)
and one guest count. A running TrueNAS guest and a running vm-lab guest count
against the same cap. Verbs that act on one guest refuse a container that does
not carry the calling profile's labels.

## Profiles

A profile is a frozen `VmProfile` passed explicitly. There is no global current
profile and no environment variable that selects one. Its fields are:

- `program`, `product`, `description`, `start_help`: names used in messages and help.
- `vm_label`, `state_label`, `label_value`: the Docker labels on the container.
- `socket_alias_prefix`: prefix of the short `/tmp` alias for long socket paths.
- `image_env`: variable that names the QEMU image.
- `extra_disk_drive_prefix`, `extra_disk_serial_prefix`: names of extra disks inside the guest.
- `readonly_media_path`: where the ISO is mounted in the container.
- `max_concurrent_guests`, `default_max_concurrent_guests`.
- `guest_memory_mib`, `cpus`, `forwards`: fixed sizing. With `sizing_from_cli=True`
  they come from `--memory-mib`, `--cpus` and `--forward` instead.

See [How a project defines its own profile](docs/CONSUMERS.md#define-a-profile-front-for-a-project).

## Tests

```
cd scripts/vm-lab && run-gate unit --worktree <worktree>
```

The tests use no Docker and no QEMU.
