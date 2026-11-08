# Augeaswrap

Augeaswrap is a guarded applier for host configuration. It plans requested
changes, checks that the host still matches the plan, stages and validates all
files before committing, then records a journal and exact file pre-images.
The `file` resource writes or deletes whole files in the allowed configuration
directories. The read-only `check` resource runs an argv check or asserts path
metadata. Use `--root` to exercise the full flow against a fake root.

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
contract; the [design guide](docs/DESIGN-GUIDE.md#guarded-applier) explains the choices behind
the guarded transaction model and the endpoint publisher.

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
