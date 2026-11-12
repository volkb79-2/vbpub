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
| `console type` | Type US-layout ASCII text through QMP `send-key`; the whole text is validated before any key is sent. |
| `console record` | Run the changed-frame recorder in the foreground; it also executes commands queued with `console ctl`. |
| `console ctl` | Queue keys, text, a QMP command, or `--quit` for a running recorder. |
| `console text` | Print the current screen as text once: exact VGA text through vmscreen, else OCR through `pwmcp ocr`. |
| `console wait-for-text PATTERN` | Poll the screen text until `PATTERN` (a regular expression) shows up; also stops on a fail pattern or a detected boot loop. |

`--state-dir` is required on every verb that takes it (all except `destroy`).

### Console options

`console record` requires Pillow in the Python environment that runs the VM
tool. The other console verbs do not.

| Verb | Option | Meaning |
| --- | --- | --- |
| `console type` | `--text TEXT` | Type the given US-layout ASCII text. |
| `console type` | `--text-file FILE` | Read the text when the verb runs and strip exactly one trailing newline. |
| `console record` | `--out DIR` | Recording output directory. Required. |
| `console record` | `--interval S` | Poll interval; default 3.0, positive. |
| `console record` | `--diff-threshold F` | Minimum changed-pixel fraction; default 0.00002, positive. |
| `console record` | `--cursor-w PX` | Cursor-box width for blink suppression; default 24, at least 1. |
| `console record` | `--cursor-h PX` | Cursor-box height for blink suppression; default 40, at least 1. |
| `console record` | `--cap N` | Maximum kept frames; default 2000, at least 1. |
| `console record` | `--max-runtime S` | Runtime measured from the original start; default 14400, positive. |
| `console record` | `--qmp-timeout S` | Timeout for every QMP connection; default 30, positive. |
| `console record` | `--stop-guest-on-exit` | Gracefully stop the guest on every exit after the recording loop started, except `ctl --quit`. A recorder refused at start never stops the guest. |
| `console ctl` | `--keys K` | Queue comma-separated sends; `+` joins one chord. |
| `console ctl` | `--type TEXT [--secret]` | Queue text, optionally redacting it in `keys.jsonl` only. |
| `console ctl` | `--type-file FILE` | Queue a file to read at execution time; one trailing newline is stripped. |
| `console ctl` | `--qmp-json JSON` | Queue one QMP command object. |
| `console ctl` | `--quit` | End the recorder without stopping the guest. |
| `console text`, `console wait-for-text` | `--engine {auto,vmscreen,ocr}` | Screen reader. `vmscreen` reads the VGA text buffer exactly (text modes only), `ocr` recognises a PNG screendump with `pwmcp ocr` (needs pwmcp's OCR sidecar), `auto` (default) tries `vmscreen` and uses `ocr` where it does not apply. |
| `console text`, `console wait-for-text` | `--json` | `text`: one `{"text", "engine", "exact"}` object. `wait-for-text`: one result object (see below). |
| `console wait-for-text` | `PATTERN` | Python regular expression, searched in the screen text with `re.MULTILINE`. Not empty. |
| `console wait-for-text` | `--literal` | Treat `PATTERN` as plain text. |
| `console wait-for-text` | `--timeout S` | Seconds to wait on a monotonic clock. Required, positive. |
| `console wait-for-text` | `--interval S` | Seconds between polls; default 2, positive. |
| `console wait-for-text` | `--fail-pattern REGEX` | Stop at once with exit 4 when it matches a screen. Repeatable. |
| `console wait-for-text` | `--loop-pattern REGEX` | Boot-loop detection: count how often it goes from absent to present on the screen. |
| `console wait-for-text` | `--loop-count N` | Stop with exit 5 when the loop pattern has appeared `N` times; default 3, at least 2. Needs `--loop-pattern`. |

`console ctl` accepts exactly one command option. A value that starts with `-`
needs the equals form, for example `--type=-x` or `--text=-x`.

### Screen text: `console text` and `console wait-for-text`

`console text` prints the screen text once on stdout and one line
`vm-lab: engine=<name> exact=<true|partial|n/a>` on stderr. `exact` is
vmscreen's verdict (`true`: every cell agrees with the screendump, `partial`:
all but a possible cursor cell); OCR text is never exact (`n/a`).

`console wait-for-text` exit codes (module constants in `vm_lab_text.py`):

| Exit | Meaning |
| --- | --- |
| 0 | `PATTERN` matched; stdout has the matching line(s). |
| 1 | Error: an engine is unavailable, or 10 screen reads in a row failed. |
| 2 | Usage: invalid regular expression, `--loop-count` without `--loop-pattern`, bad number. Nothing connects first. |
| 3 | Timeout. |
| 4 | A `--fail-pattern` matched. |
| 5 | Boot loop: the loop pattern appeared `--loop-count` times. |

Per poll the order is: read the screen, `PATTERN` (a match wins), the fail
patterns, the loop counter, the timeout, then sleep `--interval`. A read that
fails (QMP error, unreadable image, an engine that had worked before) is
skipped. On timeout, fail pattern and boot loop, stderr gets one `vm-lab:`
line and, without `--json`, the last screen text. With `--json` stdout gets one
object `{"result": "matched"|"timeout"|"failed"|"boot-loop", "engine", "exact",
"elapsed_s", "pattern", "matched_text", "loop_count", "last_text"}`; the exit
code is the same. Exit 1 prints only a `vm-lab:` line on stderr.

vmscreen runs as `python -m vmscreen` from `libraries/vmscreen/src` of this
repository and needs `cli-extended` in the Python that runs the VM tool; OCR
needs `pwmcp` on PATH and pwmcp's OCR sidecar enabled. See
[docs/CONSUMERS.md](docs/CONSUMERS.md) for a worked example and
[docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) for the reasons.

### `start` options

- `--memory-mib N`: guest RAM, 512 to 8192. The container memory limit is
  `N + 512` MiB with swap disabled. Required.
- `--cpus N`: guest CPUs, 1 to 4. Also the container CPU limit. Required.
- `--forward HOST:GUEST`: forward a TCP port of the container to a guest port.
  Repeat for more (up to 8). At least one is required. Nothing is forwarded by
  default. Ports are reachable at the container address on its Docker network.
  They are not published on the host.
- `--display-device {std,virtio}`: required. `std` uses QEMU's standard VGA;
  `virtio` adds `virtio-vga`, needed when the standard device produces garbage
  screendumps for the guest's kernel console.
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
- `display_device`: the fixed display device, or unset until a
  `sizing_from_cli=True` profile resolves the required `--display-device`.

See [How a project defines its own profile](docs/CONSUMERS.md#define-a-profile-front-for-a-project).

## Tests

```
cd scripts/vm-lab && run-gate unit --worktree <worktree>
```

The tests use no Docker and no QEMU.
