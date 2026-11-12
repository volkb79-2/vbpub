# console-capture

This directory keeps the TrueNAS-specific console helpers:

- `truenas_vm_console_capture.py` captures TrueNAS's SPICE web console through
  the pwmcp managed browser.
- `truenas_rpc.py` is the minimal TrueNAS JSON-RPC client used to obtain a
  throwaway VM's web-console URI.
- `web_console_session.py` drives browser console sessions.
- `WEB-CONSOLES.md` records the browser-console measurements and constraints.

Generic QEMU launching, console recording, key queuing and text typing belong
to the VM tool in `scripts/vm-lab/`. It is the repository's one QEMU launcher
and changed-frame recorder.

## Capturing a console with the VM tool

Run these commands from the repository root. They show a TrueNAS guest with
an existing installed base image and an optional ISO. The state directory
belongs to pwmcp; QEMU runs in the host background slice after the VM tool's
admission checks.

```sh
VM=pwmcp/artifacts/vm-lab/truenas-console
BASE=pwmcp/artifacts/vm-lab/truenas-console-base.qcow2
export VM_LAB_QEMU_IMAGE=<image-with-qemu-system-x86_64-and-qemu-img>

# The state directory must be empty on the first start. Replace BASE with your
# existing installed TrueNAS image; the tool creates the writable overlay in
# the state directory and mounts the base read-only.
mkdir -p "$VM"

python3 scripts/vm-lab/vm_lab.py start \
  --name truenas-console \
  --state-dir "$VM" \
  --base-disk "$BASE" \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
  --memory-mib 3072 \
  --cpus 2 \
  --forward 2222:22 \
  --display-device virtio \
  --readonly-media downloads/<optional-truenas-iso>

S="--name truenas-console --state-dir $VM"
CAP="$VM/console-record"

# Foreground recorder. Leave it running while using the commands below.
python3 scripts/vm-lab/vm_lab.py console record $S --out "$CAP"
```

From a second terminal, drive the running recorder:

```sh
S="--name truenas-console --state-dir $VM"
CAP="$VM/console-record"

python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --keys ret
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --type "root"
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --quit

# Direct typing uses the same short-lived QMP path as the recorder.
python3 scripts/vm-lab/vm_lab.py console type $S --text "root"
```

The recorder writes `frames/NNNN_<elapsed>s.png`, `manifest.jsonl`,
`keys.jsonl`, `state.json`, and `DONE`. Resume it by running the same
`console record` command again in the same output directory. A recorder that
ends, including one ended by `ctl --quit`, leaves the guest running unless
it was started with `--stop-guest-on-exit`. Stop the guest explicitly when
finished:

```sh
python3 scripts/vm-lab/vm_lab.py stop --name truenas-console --state-dir "$VM"
```

Starting and stopping the recorder does not hold the QMP socket between
polls, so `console sendkey`, `console reset`, `console screendump` and `stop`
remain available while it runs. `--type-file` keeps the secret text out of
the recorder inbox and redacts its length in `keys.jsonl`; the file itself
must be protected by the caller.

### Differences from the former console-capture launcher

- There is no `--own-qemu` recorder mode and no pidfile or signal fallback.
  `--stop-guest-on-exit` uses the VM tool's graceful `stop` path on every
  recorder exit except an explicit `console ctl --quit`.
- `--base-disk` must name an existing QEMU image. The VM tool mounts it
  read-only and writes `disk-overlay.qcow2` in `--state-dir`; it does not
  create a blank base disk.
- QEMU now runs in a Docker container under the host background slice with
  memory, CPU, admission and count checks. It no longer runs directly inside
  the devcontainer.
- The optional ISO is passed with `--readonly-media`; bare download names
  are not resolved by the VM tool. Use an explicit path.

## TrueNAS API client TLS

The JSON-RPC client logs in with `PASSWORD_PLAIN`, so it verifies the server
certificate and host name by default. A throwaway TrueNAS test install
normally has a self-signed certificate. For that case ONLY (operator
decision, 2026-10-27: TLS verification may be off only for throwaway
TrueNAS test infrastructure helpers, explicitly and documented):

- `TRUENAS_INSECURE_TLS=1` disables verification and prints a `WARNING` to
  stderr on every connection. Any other value (unset, empty, `0`) keeps
  verification on.
- `TRUENAS_CA_FILE=/path/cert.pem` keeps verification on and trusts that
  certificate or CA (preferred when you can export it).
- Never set the insecure variable for a host that holds real data or real
  credentials.

The web-console helper requires Pillow. QEMU's PNG screendump support is
also required by the VM tool recorder.
