# Augeaswrap

Augeaswrap is a guarded applier for host configuration. It plans requested
changes, checks that the host still matches the plan, stages and validates all
files before committing, then records a journal and exact file pre-images.
The `file` resource writes or deletes whole files in the allowed configuration
directories. The `unit` resource manages systemd unit files and drop-ins with
root-owned, non-writable paths, normalized executable checks, a staged view of
systemd search paths and discovered dependencies, and optional preflight
commands. Present units declare `intent` and the desired `enable` state; live
enablement is reconciled even when the file is unchanged. Unit text is limited
to 1 MiB of UTF-8 and cannot contain NUL. Staged verification creates empty
executable stubs for checked `Exec*=` targets; use `/usr` paths on merged-usr
hosts because symlink components such as `/bin` are refused. Static, indirect,
generated, and transient units cannot be enabled. Third-party post-actions with
unrecognized argv must declare a phase. See the
[systemd unit design](docs/DESIGN-GUIDE.md#systemd-units) for the staged verify
and action-order choices. The read-only `check` resource runs an argv check or
asserts path metadata. Use `--root` to exercise the full flow against a fake
root.

The command surface is `augeaswrap plan`, `apply`, `revert`, and `journal`;
`augeaswrap endpoints` publishes and checks service-owned endpoint facts. A
failed apply stops at its first failure and keeps the partial-apply journal.
`revert` is a separate, confirmed operation and refuses when a later
transaction touched the same file; it restores the pre-image and reruns the
recorded post-actions for that file. The library API is `augeaswrap.plan`,
`apply`, `revert`, `journal`, `load_request`, and `Request.to_toml()`.
Each `--json` command writes one JSON document, including the plan and result
for apply and revert.

See the [consumer guide](docs/CONSUMERS.md) for pasteable TOML, CLI, and Python
examples; the [specification](SPEC.md) defines the request and journal
contract; the [guarded-applier design](docs/DESIGN-GUIDE.md#guarded-applier)
and [systemd unit design](docs/DESIGN-GUIDE.md#systemd-units) explain the
transaction model, unit safety checks, and action phases.

## Development

Install Augeaswrap and its first-party CLI dependency from the local checkout:

```bash
python3 -m pip install --editable ./libraries/cli-extended --editable ./augeaswrap
```

Run its registered gates from this project directory:

```bash
run-gate r0-r1
run-gate r3
```
