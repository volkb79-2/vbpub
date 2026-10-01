# Using debian-install-v2

This guide is the adoption path: make a config, review it, and then either run
the installer on the target Debian host or produce a remote custom-script
bundle. Product capabilities are summarized in
[`../README.md`](../README.md); design rationale is in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md).

The bare usage map includes a short description of the CLI and aligned verb
summaries. It lists verb names alone; use each verb's `--help` for its full
syntax and accepted configuration/options.

## Quickstart: create and inspect settings

From a vbpub checkout, enter the project directory. The CLI works without
installing the optional wizard package; install that project-specific
requirement only if you want interactive prompts. Use the same Python
interpreter for the install and CLI run; if using a virtual environment,
activate it first (the executable resolves `python3` from `PATH`):

```bash
cd scripts/debian-install-v2
python3 -m pip install -r wizard-requirements.txt
./debian-install-v2.py wizard --output target-host.json
./debian-install-v2.py wizard --output target-host.json --from-config existing.json
```

The wizard asks which settings sections to edit. Unselected sections keep
their validated starting values (or the shipped defaults). It shows a
secret-safe summary and asks before saving. The output is JSON written with
mode `0600`; the destination directory must already exist. Press Ctrl-C at a
prompt to cancel without a traceback or partial config write.

If the optional prompt package is unavailable or cannot be imported, the CLI
prints an actionable dependency-install hint instead of a Python traceback.
Generated help and diagnostic severity tags use the shared `cli-extended`
color policy; `--color`, `--no-color`, and `NO_COLOR` behave consistently.
Primary status/plan data and JSON stay plain for piping and parsing.

The config is ordinary versioned JSON. This minimal example is accepted by
the shipped loader; omitted fields receive that loader’s current defaults:

```json
{
  "schema_version": 1,
  "fresh_install": true,
  "swap_disk_total_gb": 32,
  "swap_file_count": 8,
  "credential_mode": "systemd"
}
```

Every verb that consumes settings accepts exactly one of `--config FILE` or
`--config-json JSON`; its generated usage marks that choice as required. Prefer
the file form for normal operation, since inline JSON may be visible in process
arguments. For example:

```bash
./debian-install-v2.py plan --config target-host.json
./debian-install-v2.py plan --config-json \
  '{"schema_version":1,"fresh_install":true,"swap_disk_total_gb":32,"swap_file_count":8,"credential_mode":"systemd"}'
```

The parser rejects both a missing source and supplying both forms before any
installation logic runs.

To inspect a real target host’s partition plan, run `plan` on that host. It
reads the host’s root disk and does not write to it:

```bash
sudo ./debian-install-v2.py plan --config /root/target-host.json
sudo ./debian-install-v2.py plan --config /root/target-host.json --json
```

A plan is read-only, so it does not need a synthetic dry-run mode. A plan on
the controller would describe the controller’s own root disk, not a remote
server.

## Install directly on a Debian host

Copy the CLI/project to the Debian machine being installed, put the validated
config at a protected path, then run:

```bash
sudo ./debian-install-v2.py install --config /root/target-host.json
```

Before making changes, the CLI prints the disk/swap plan and asks for
confirmation. For unattended use, `--yes` pre-accepts that confirmation; it
does not bypass config validation or other safety checks:

```bash
sudo ./debian-install-v2.py install --config /root/target-host.json --yes
```

To rehearse the complete action plan without executing privileged commands or
writing state files:

```bash
sudo ./debian-install-v2.py install --config /root/target-host.json --dry-run
```

The live install modifies the root disk and can reboot. Use it only on the
intended fresh-install target with a valid recovery path. The systemd service
invokes `resume --yes` after reboot; operators can also inspect progress with
`status --config FILE`, run post-install checks with `verify --config FILE`,
or explicitly disable the remaining automatic stage-two service with
`disable-stage2 --config FILE`.

## Create a remote custom-script bundle

For a hoster custom-command field, first generate the provider-neutral bundle:

```bash
./debian-install-v2.py build-customscript --config target-host.json
```

The JSON result contains the validated config, `customScript` command, and
completion marker. The command’s bootstrap URL is derived from the selected
repository branch. A non-default repository must be paired with an explicit
`--bootstrap-url`; do not let a branch/repository mismatch silently target an
unrelated endpoint. The generated command downloads and executes the bootstrap
with Python’s standard-library HTTPS client; a failed or empty download exits
nonzero before the installer runs.

For a Netcup-style replacement of the controller SSH-key marker:

```bash
./debian-install-v2.py build-customscript \
  --config target-host.json --controller-ssh-placeholder
```

If the configuration contains a Telegram bot token, the generated bundle is
secret-bearing. The CLI refuses to print it by default. If you deliberately
need to capture the raw bundle, protect the file and explicitly bypass
redaction:

```bash
umask 077
./debian-install-v2.py build-customscript \
  --config target-host.json --controller-ssh-placeholder --debug-raw \
  > target-host-customscript.json
```

The provider may retain custom-script contents in its API, UI, or task logs;
`umask` only protects the local file. Avoid embedding credentials if the
provider’s storage/visibility is not acceptable. The target bootstrap writes
its transient config with mode `0600` and removes that handoff file when the
stage-one invocation exits (successfully or with an error).

To test the remote fetch/translation path without applying host operations,
set `DRY_RUN=yes` in the cloud-init environment. A remote dry run still needs
root to exercise the real bootstrap flow and will download the installer
archive, but the installer records rather than executes privileged actions.

## Finding help

With no verb, the CLI prints the grouped usage map. Use `--help` for the full
map or append it to a verb for that verb’s options and examples:

```bash
./debian-install-v2.py
./debian-install-v2.py --help
./debian-install-v2.py build-customscript --help
./debian-install-v2.py --version
```

The top-level verbs are:

- Exploration: `status`, `verify`, `plan`, `build-customscript`.
- Modification: `wizard`, `install`, `resume`, `disable-stage2`.

Use `--json` where offered for machine-readable status/plan output. For
diagnostics, `--debug` adds troubleshooting context; `--debug-raw` is only for
intentionally unredacted output and should be treated as a secret-handling
decision.
