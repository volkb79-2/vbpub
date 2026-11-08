# Augeaswrap

Augeaswrap is a guarded applier for host configuration. It plans requested
changes, checks that the host still matches the plan, stages and validates all
files before committing, then records a journal and exact file pre-images.
The `file` resource writes or deletes whole files in the allowed configuration
directories. The read-only `check` resource runs an argv check or asserts path
metadata. Layered resources manage whole sysctl, sysusers and tmpfiles drop-in
files, groups and service endpoint records; their callers declare whether each
file is authoritative or a fallback. Use `--root` to exercise the full flow
against a fake root. Layered resources check effective values and file order
under the selected root, then recheck caller intent during verification.
Same-basename files that mask one another are reported as `shadow.mask`; a
reviewed exception must name the exact other file with `key = "$file"`.
Planning refuses a sysusers membership change to a group removed by the same
request. See [why group removal blocks membership
changes](docs/DESIGN-GUIDE.md#membership-and-group-removal).
Sysusers names and fixed GIDs follow systemd's accepted rules, and a fixed GID
already held by another group is refused before an endpoint can publish it.
The tmpfiles native check reads the staged fragment bytes. See the
[native validation and fixed GID rationale](docs/DESIGN-GUIDE.md#native-validation-and-fixed-gids).
Group removal makes the whole transaction non-revertible; the consumer guide
documents that boundary and the caller-owned effects of tmpfiles entry types.
With `--root`, endpoint fragments and generated files are written under that
root; `skipped(root)` refers only to the live endpoint command.

The command surface is `augeaswrap plan`, `apply`, `revert`, and `journal`;
`augeaswrap endpoints` publishes and checks service-owned endpoint facts. A
failed apply stops at its first failure and keeps the partial-apply journal.
`revert` is a separate, confirmed operation and refuses when a later
transaction touched the same address; it restores the pre-image and reruns
recorded applicable post-actions. Non-file effects such as current sysctl
values, group membership, and paths created by tmpfiles are reported in the
journal and remain for the caller to manage. The library API is `augeaswrap.plan`,
`apply`, `revert`, `journal`, `load_request`, and `Request.to_toml()`.
Each `--json` command writes one JSON document, including the plan and result
for apply and revert.

See the [consumer guide](docs/CONSUMERS.md) for pasteable TOML, CLI, and Python
examples; the [specification](SPEC.md) defines the request and journal
contract; the [design guide](docs/DESIGN-GUIDE.md#guarded-applier) explains the choices behind
the guarded transaction model and the endpoint publisher. See the
[layered resource rationale](docs/DESIGN-GUIDE.md#layered-resource-shadowing)
for how intent and exact shadow acceptance work.

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
