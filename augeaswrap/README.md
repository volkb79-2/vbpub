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
hosts because symlink components such as `/bin` are refused. Per-unit drop-in
layers are included in saved-plan preconditions, although systemd's directive
reset and accumulation rules are not reduced to the generic shadow model.
Static, indirect, generated, and transient units cannot be enabled.
Third-party post-actions with
unrecognized argv must declare a phase. See the
[systemd unit design](docs/DESIGN-GUIDE.md#systemd-units) for the staged verify
and action-order choices. The read-only `check` resource runs an argv check or
asserts path metadata. Layered resources manage whole sysctl, sysusers and tmpfiles drop-in
files, groups and service endpoint records; their callers declare whether each
file is authoritative or a fallback. Use `--root` to exercise the full flow
against a fake root. Layered resources check effective values and file order
under the selected root, then recheck caller intent during verification.
Same-basename files that mask one another are reported as `shadow.mask`; a
reviewed exception must name the exact other file with `key = "$file"`.
Planning refuses a sysusers membership change to a group removed by the same
request. See [why group removal blocks membership
changes](docs/DESIGN-GUIDE.md#membership-and-group-removal). Sysusers can add
and remove supplementary membership; member account names may contain periods
and may end in `$`, while group definitions keep systemd's stricter name rule.
With `groups = []`, a present sysusers request only runs membership actions and
leaves an existing fragment's group rows unchanged, including stale rows. See
the [membership-only request behavior](docs/DESIGN-GUIDE.md#membership-only-sysusers-requests)
for why. The design guide also explains the separate name rules and gpasswd
argument order.
Sysusers names and fixed GIDs follow systemd's accepted rules, and a fixed GID
already held by another group is refused before an endpoint can publish it.
The tmpfiles native check reads the staged fragment bytes. See the
[native validation and fixed GID rationale](docs/DESIGN-GUIDE.md#native-validation-and-fixed-gids).
Group removal makes the whole transaction non-revertible; the consumer guide
documents that boundary and the caller-owned effects of tmpfiles entry types.
With `--root`, endpoint fragments and generated files are written under that
root; `skipped(root)` refers only to the live endpoint command.
Use `--root` to exercise the full flow against a fake root.

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

The `json_key` resource updates one nested JSON key, preserves file metadata,
and canonicalizes the whole document on its first changed write. It can run
`dockerd --validate` against the staged Docker config and can record a restart
as pending. `augeaswrap journal --pending` lists those restarts until systemd
shows that the unit entered `active` after the transaction, and keeps missing
units listed for resolution. See the
[JSON key and pending journal design](docs/DESIGN-GUIDE.md#json-key-updates-and-pending-journal-queries)
for the format, timestamp, and validator choices.

See the [consumer guide](docs/CONSUMERS.md) for pasteable TOML, CLI, and Python
examples; the [specification](SPEC.md) defines the request and journal
contract; the [guarded-applier design](docs/DESIGN-GUIDE.md#guarded-applier)
explains the transaction model and endpoint publisher. See the
[systemd unit design](docs/DESIGN-GUIDE.md#systemd-units) for unit safety checks
and action phases, and the
[layered resource rationale](docs/DESIGN-GUIDE.md#layered-resource-shadowing)
for how intent and exact shadow acceptance work.

## Augeas-backed edits

The `edit` resource uses an explicit Augeas lens to update selected nodes in an
allowlisted file. It stages the result, shows a unified diff, validates the
staged file, and records exact bytes and metadata for revert. The first format
helpers build safe mount and swap records for `/etc/fstab`; they preserve other
rows and comments, and can request `findmnt` validation plus `daemon-reload`.
See the [fstab consumer example](docs/CONSUMERS.md#augeas-backed-edits-and-fstab-helpers)
and the [Augeas edit design](docs/DESIGN-GUIDE.md#augeas-edit-resources) for
the request contract, duplicate guards, and staged validation choices.

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
