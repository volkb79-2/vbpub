# vm-lab consumer guide

## Start a Debian guest

**Every snippet in this guide runs from the repository root.** `<project>` is
the project that owns the VM (for example `debian-rollback`). Keep its VM files
in `scripts/<project>/artifacts/vm-lab/<vm>/`.

Prerequisites: a loaded host background slice (`CGROUP_PARENT_DEV_BACKGROUND`),
a Docker network (`DOCKER_NETWORK_INTERNAL`), a QEMU image that has
`qemu-system-x86_64` and `qemu-img` (`VM_LAB_QEMU_IMAGE`), the OVMF firmware
files, and a Debian installer ISO.

```
VM=scripts/<project>/artifacts/vm-lab/deb1
BASE=scripts/<project>/artifacts/vm-lab/deb1-base.qcow2
mkdir -p "$VM"

# A blank base disk. The guest writes go to an overlay in "$VM", not here.
qemu-img create -f qcow2 "$BASE" 20G

python3 scripts/vm-lab/vm_lab.py start \
  --name deb1 \
  --state-dir "$VM" \
  --base-disk "$BASE" \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
  --memory-mib 2048 \
  --cpus 2 \
  --display-device std \
  --forward 2222:22 \
  --readonly-media /path/to/debian-installer.iso \
  --serial-console
```

The state directory must be empty on a first start, so the base disk lives
beside it, not inside it.

`start` prints its admission evidence and a `[started]` line. If it refuses, it
says why (memory pressure, guest cap, missing image, and so on). Do not work
around a refusal. Lower the load or the request.

Then drive the guest:

```
S="--name deb1 --state-dir $VM"

python3 scripts/vm-lab/vm_lab.py console screendump $S --filename boot.ppm
python3 scripts/vm-lab/vm_lab.py console sendkey $S ret
python3 scripts/vm-lab/vm_lab.py console sendkey $S ctrl-alt-delete
python3 scripts/vm-lab/vm_lab.py console reset $S
python3 scripts/vm-lab/vm_lab.py console serial $S     # Ctrl-] to leave
python3 scripts/vm-lab/vm_lab.py console type $S --text "hello"
python3 scripts/vm-lab/vm_lab.py status $S
python3 scripts/vm-lab/vm_lab.py stop $S
```

`console screendump` refuses to overwrite an existing file. The picture is a
PPM file in `$VM`.

After the install, `stop` the guest, then start again with `--resume` and the
same options, including the same ISO. Resume refuses if the base disk,
firmware, image, network, slice, sizing, forwards or ISO differ from the first
start. A first start without an ISO may add one on resume. `destroy` removes the stopped container and keeps the disk state.

Reach the guest from another container on the same Docker network at
`<container address>:2222`. Nothing is published on the host.

## Record a console session

Run the foreground recorder in one terminal:

```sh
VM=scripts/<project>/artifacts/vm-lab/deb1
CAP=$VM/capture
S="--name deb1 --state-dir $VM"

python3 scripts/vm-lab/vm_lab.py console record $S --out "$CAP"
```

In another terminal, use `ctl` for commands that must be recorded with the
frames, or use the direct console verbs:

```sh
CAP=scripts/<project>/artifacts/vm-lab/deb1/capture

python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --keys ret
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --type "root"
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --type-file /path/to/secret.txt
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --qmp-json '{"execute":"query-status"}'
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --quit
```

`ctl --type-file` reads the file when the recorder executes the command; its
text is never put in the inbox and its recorded label is redacted. `--type`
with `--secret` is also redacted in `keys.jsonl`, though the text still
appears briefly in the inbox, so use throwaway values.

The recorder writes `frames/`, `manifest.jsonl`, `keys.jsonl`, `state.json`,
and `DONE`. Re-running the same `console record` command resumes numbering,
keeps the original start clock from `state.json`, and compares the first new
poll with the last kept frame. It leaves the guest running on exit unless
`--stop-guest-on-exit` is given. An explicit `ctl --quit` never stops the
guest.

### Choosing sizes

- `--memory-mib` is guest RAM. The container limit is this plus 512 MiB.
- `--cpus` is the guest CPU count and the container CPU limit. The slice CPU
  quota must allow it.
- Up to three guests run at once on the host, counting guests of every profile
  (TrueNAS included). Use `--max-concurrent-guests 1` or `2` to be stricter.
- Memory pressure above policy refuses a start. Try again later. Do not raise
  `--memory-psi-full-avg10-limit` unless a person has judged the host.

## Define a profile front for a project

Use a front when a project wants fixed sizing, its own names and labels, or its
own default state directory. `scripts/truenas/tcg_vm.py` is the worked example.

1. Put a small file in the project (for example `scripts/<project>/vm.py`).
2. Make `scripts/vm-lab` importable by path from `__file__`, not by an
   environment variable:

   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vm-lab"))
   import vm_lab
   from vm_lab import PortForward, VmProfile
   ```

3. Define the profile. Use a label key and a state label key of your own, and
   names that mention your product:

   ```python
   PROFILE = VmProfile(
       program="vm.py",
       product="Debian rollback",
       description="Run Debian rollback test guests.",
       start_help="start a Debian rollback test guest",
       vm_label="org.vbpub.debian-rollback.vm",
       state_label="org.vbpub.debian-rollback.state-dir-sha256",
       label_value="true",
       socket_alias_prefix="debrb",
       image_env="DEBIAN_ROLLBACK_QEMU_IMAGE",
       extra_disk_drive_prefix="debrbdata",
       extra_disk_serial_prefix="DEBRB",
       readonly_media_path="/media/debian-readonly.iso",
       max_concurrent_guests=3,
       default_max_concurrent_guests=3,
       guest_memory_mib=2048,
       cpus=2,
       forwards=(PortForward(name="ssh", host_port=2222, guest_port=22),),
   )
   ```

   For command-line sizing instead, leave `guest_memory_mib`, `cpus` and
   `forwards` out and pass `sizing_from_cli=True`.

4. Add your `(vm_label, label_value)` pair to `KNOWN_VM_LABELS` in
   `scripts/vm-lab/vm_lab.py`. This registry is the one list of labels that
   start admission counts, each with its own value. Without the entry, start
   refuses your profile, because its guests would not count against the shared
   cap. This is a change to vm-lab and needs its own review.
5. Build the command line and run it:

   ```python
   def main(argv=None):
       return vm_lab.main(PROFILE, argv)

   if __name__ == "__main__":
       raise SystemExit(main())
   ```

   `vm_lab.build_parser(PROFILE)` makes `--state-dir` required. To derive a
   default state directory, pass `state_dir_defaults=(start_help, other_help)`
   to `build_parser` and fill the argument in `prepare_args=` as `tcg_vm.py`
   does.

6. Register a unit lane in the project's `run-gate.toml` and test the front
   with the vm-lab tests as a model. Do not start real VMs in unit tests.

A profile front must not duplicate launcher logic. If something is missing,
change `vm_lab.py` and its tests.

## TrueNAS

TrueNAS guests are started with `scripts/truenas/tcg_vm.py`. See the
[TrueNAS guide](../../truenas/docs/CONSUMERS.md#run-a-local-truenas-tcg-guest).
