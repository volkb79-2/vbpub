# Augeaswrap design guide

## Publish endpoint facts at the owning service

Host endpoint paths and GIDs change with service configuration and host
installation state. The service installer already resolves those facts, so it
publishes one fragment under `/etc/host-endpoints.d/<owner>.env`; consumers read
the resulting process environment and never recreate the service's defaults.
The shared helper sorts fragments, rejects duplicate keys, and regenerates the
aggregate and environment block under one `flock`. This keeps multiple service
installers from overwriting each other's environment data.

The aggregate is a simple `KEY=VALUE` file because pam_env, POSIX shells,
systemd `EnvironmentFile=`, and Docker env files can all consume the same
restricted alphabet. Atomic replacement protects each generated file from a
partial write. The managed block preserves unmanaged `/etc/environment` bytes,
and refuses an assignment outside the block when it would duplicate a managed
key. A publish may skip a damaged fragment owned by another service so it can
preserve the remaining valid owners; it emits a warning because that owner's
variables will be absent from the regenerated aggregate until the owner repairs
its fragment. Symlink refusal prevents a managed target from redirecting writes
to an unrelated file. The API and operational examples are in the
[consumer guide](CONSUMERS.md#host-endpoint-records); the normative contract is
in [`SPEC.md`](../SPEC.md#endpoint-records).

## Guarded applier

The applier keeps transaction journals and exact pre-images so a caller can
inspect a partial apply and choose an explicit revert. It does not keep an
owner registry or infer who should win when two callers name the same file.
That avoids turning a simple file applier into a hidden package manager: a
caller states the desired present or absent value, and the journal records
what the transaction changed. A revert checks that no later transaction
changed the same address before restoring the pre-image. It then reruns the
recorded post-actions for the restored addresses so dependent services can
reload the restored configuration. Transaction IDs preserve allocation order
using retained transaction directories, so the revert check does not mistake
two transactions from the same second for unrelated history.

Resource kinds share one frozen protocol so independent kinds can be built
without engine-specific hooks. `observe` supplies the complete precondition,
`diff` describes the requested transition, `stage` validates a private image,
`commit` writes it, `post_actions` declares argv-only follow-up commands, and
`verify` compares the host with the staged image. The protocol and context
contract are defined in the [specification](../SPEC.md#guarded-applier-protocol-hc-1a).
Each JSON invocation emits one document so automation can consume the reviewed
plan and its outcome as a single record.

Post-actions use shared phases so independently registered kinds can
coordinate system-wide ordering. The engine recognizes a small set of argv
forms; an unknown third-party command must declare its phase explicitly. This
fails closed when a kind author adds a command without deciding where it
belongs. Callers that construct `PostAction` values for unrecognized argv must
therefore pass `phase`, a breaking validation requirement recorded in the
[consumer guide](CONSUMERS.md#post-action-phases).

## JSON key updates and pending journal queries

JSON is the one structured format handled directly by a resource. `json_key`
uses Python's standard JSON parser so it can preserve object insertion order,
reject duplicate keys at every depth, and distinguish integer values from
floating-point values. It rewrites the whole object with two-space indentation
and a final newline. A key-level text splice would leave malformed whitespace
and duplicate-key ambiguity in place; canonicalizing only after a real change
keeps the first write reviewable in the plan. A missing-key deletion is a true
no-op and preserves the source bytes even when the document is noncanonical.
An already canonical file stays byte-idempotent. The file's existing mode and
owner are preserved, and revert restores its exact pre-image. The signed-64-bit
and finite-float checks apply to the requested value; existing document values
pass through unchanged during a sibling-key update.

`on_change` is mandatory because a JSON edit does not tell Augeaswrap which
service consumes the file. The caller names the unit and chooses `restart`,
`pending`, or `none`. A pending restart remains an action in the transaction
journal; Augeaswrap does not maintain a second queue or infer service intent.

The pending query compares systemd's monotonic active-enter timestamp with the
monotonic start time written in each new transaction journal. It requests
`ActiveEnterTimestampMonotonic`, `ActiveState`, and `LoadState`, and refuses
when a requested property is absent or inconsistent. This avoids wall clock
jumps changing the answer. `LoadState=not-found` identifies a missing unit; an
exact non-zero `Unit <unit> could not be found.` diagnostic does too, with a
null active-enter timestamp. A unit that has not entered `active` remains
listed so an operator can see unresolved work. Other failed systemctl queries
are explicit refusals, not empty results. Old journals without the monotonic
field remain visible with an unknown transaction time; a restart after a
reboot may be conservatively over-reported when the unit has not entered
`active` during the current boot. The query is read-only and derives its
results from the retained journals each time.

The dockerd validator receives the staged canonical copy through the shared
command-runner seam. It refuses when the installed binary is missing or rejects
the configuration, and it never starts or reloads the daemon. The fake-runner
gate proves argv construction and refusal behavior; the installed Docker
version's live acceptance is tracked separately.

## Systemd units

A unit is one whole file, or one drop-in on somebody else's unit. The address
is the file path, so pre-images and stale-revert checks use the same exact-file
model as `file`. The kind does not claim ownership of a service or infer which
drop-ins belong to it. A caller requests each file it wants and uses
`state="absent"` explicitly to remove it.

Present units and drop-ins require `intent = "authoritative"` or
`intent = "fallback"`. The unit hook captures a per-unit `TargetClass` over
the four `<unit>.d` directories and includes those bytes and directory entries
in the stale-plan fingerprint. It does not run the generic first/last shadow
analyzer: systemd directives can reset or accumulate values, so that model
would produce unsafe conclusions. `systemd-analyze verify` checks syntax;
unit directive intent analysis remains intentionally unimplemented.

The managed unit file and each existing directory in its path must be owned by
the expected root identity, regular directories/files, and not writable by
group or other. Live requests default to `root:root`; explicit non-root owners
are refused on the live host. Special mode bits are refused. Under a fake root,
the invoking uid/gid are used so an unprivileged test or image builder can
write its temporary tree. Requested unit text is valid UTF-8, contains no NUL,
and is at most 1 MiB in encoded bytes, including drop-ins whose verify step is
skipped.

Executable checks run against the selected root. The executable token must be
absolute; systemd specifiers and environment-variable references are not
expanded. The path is normalized and resolved inside the selected root before
the `/tmp`, `/var/tmp`, and `/home` refusal is applied. `..`, symlink
components, non-root-owned components, and group/other-writable components
are refused. Every executable path component must be owned by uid 0, including
under a fake root. Use `/usr` paths on merged-usr systems because `/bin`,
`/sbin`, and `/lib` are symlinks and this policy refuses symlink components.
This protects both the selected root boundary and the privilege boundary of a
service that runs as root.

Staged verification copies existing valid unit files and resolvable aliases
from the static `/usr/local/lib` and `/usr/lib` trees, the requested file,
same-unit drop-ins, and referenced dependency units from every selected search
directory. Sources are fingerprinted in the observation and checked again
while staging. Each `Exec*=` target gets an empty mode-0755 stub in the staged
root: the selected-root checker validates the real target separately, while
the stub lets `systemd-analyze` parse the unit without importing binaries from
the cockpit or host filesystem. The staged vendor tree is temporary and is
removed after transaction steps complete. A real-analyzer acceptance smoke
uses a disposable VM root, as recorded in the backlog; it does not inspect the
cockpit's live root. `systemd-analyze verify` runs against that staged root. A
plan states `verify: skipped` with a reason when the analyzer is not
available or when a host-only drop-in has no base unit in the selected root;
in either case executable and configured preflight checks still run. A
drop-in with an available base unit is verified together with the staged base
and dependencies.

Runtime actions follow the shared rev3 phases: sysusers, tmpfiles,
`daemon-reload`, sysctl, enable/start/restart in declaration order, gpasswd,
then endpoint work. The engine deduplicates identical name/argv actions and
keeps declaration order within a phase. For whole-unit removal, stop and
disable are separate journaled pre-commit actions, interleaved with deleting
that unit's file; the later phases then reload systemd and reset its failed
state. Reverting a newly created whole unit derives stop/disable plus
`daemon-reload` from the absent pre-image and does not replay enable or runtime
actions against the removed unit. Reverting a restored present unit reconciles
its saved enablement and replays only applicable runtime actions.

`enable` is declared state. On the live host, planning reads
`systemctl is-enabled -- <name>` and schedules `enable` or `disable` whenever
the observed state differs, including when the file itself is unchanged. If
the state already matches, no systemctl action is declared. Fake roots have
no live systemd manager, so their preview can show enable on a changed unit
when `enable=true`, but cannot observe or reconcile a pre-existing manager
state. Successful transaction post-images are captured after actions and
verification, so later revert checks see the final manager state. A non-zero
`is-enabled` command with no output means a new unit is not enabled; empty
output with exit zero stays an error. Static, indirect, generated, and
transient units cannot become enabled, so `enable=true` for one will not
converge. Every unit operand is preceded by `--` in systemctl argv. Preflight
commands have a 30-second timeout on the direct child; a command that spawns
descendants must manage their lifetime.

A socket restarts only when its own text changes because restarting it
unnecessarily can disrupt every listener path, while changing its client
service does not change the socket unit. The exec scanner examines physical
`Exec*=` lines; it does not join systemd backslash continuations or implement
the full systemd parser. Staged `systemd-analyze verify` remains the syntax
check when its stated inputs are available.


## Augeas edit resources

An `edit` request applies an explicit Augeas lens to one allowlisted file. The
runner copies the target under a private root and uses a private copy of the
packaged overlay lens directory. `augtool` receives an argv vector and a
script file; values are quoted as Augeas strings, and newline, carriage return,
and NUL are refused before they can change script structure. Missing
`augtool` or `augparse` produces the actionable `install augeas-tools` refusal.
No other editor is selected when Augeas is unavailable.

Observation reads the parsed tree and each touched selector. The generated
script then runs with `--new`, which leaves Augeas' output in a staged
`.augnew`. The plan compares those bytes and shows a unified diff. Apply repeats
the observation and staging checks before the applier's own atomic write;
Augeas never writes to the live target. Existing mode and ownership stay
unchanged, and the exact previous bytes and metadata are stored for revert.
New files use mode `0644` and the applier process identity because the edit
request intentionally has no separate ownership or mode controls.

Duplicate matches are guarded at the selected path or record key. A reviewed
exception names one exact selector with `accept_duplicates`, making an
intentional multi-node edit visible in the request. `records` compares key and
field values before emitting updates, appends absent records, replaces a
changed record at the same position, and removes unlisted records only when
`exclusive=true`. A changed sequence record may have its whitespace normalized
by the lens; comments and unrelated records remain intact.

Validators receive only argv and the staged file path. The `findmnt` validator
consults live device and mount state, so it is skipped under a fake root and
reported as `skipped(root)`. `sshd`, `visudo`, `systemd-analyze`, and a caller's
`cmd` validator still inspect staged content. The live Augeas 1.14.1 smoke,
including transform spelling, sequence node labels, and `findmnt` behavior
when referenced devices are absent, is recorded as AW-BL-24.

The fstab helpers are pure request builders rather than a second parser. Their
mount records follow the stock Fstab lens field order, preserve each option as
a repeated `opt` label, and refuse duplicate mount points. Swap helpers write
`sw,pri=N` and use an explicit exclusive selector because the caller supplies
the complete desired swap set. See the
[pasteable consumer example](CONSUMERS.md#augeas-backed-edits-and-fstab-helpers).


## Layered resource shadowing

The same setting can be assigned from several drop-in directories, and the
winner depends on both basename sorting and the manager's merge rule. The
caller therefore chooses `intent = "authoritative"` or `"fallback"` for each
layered resource. Augeaswrap computes the effective sequence under the chosen
root and refuses a request when another assignment makes that intent false.
An equal value is a note unless an authoritative value is not last (or first,
for a first-definition format); that still means another file controls the
result. Equal fallback values remain notes even when the fallback would
otherwise win, because no effective value conflict exists.

`accept_shadow` is an exact pair of file and setting key. A broad exception
would continue accepting unrelated settings added to the same file, so one
accepted collision cannot silently authorize another. The complete layer
snapshot, including file bytes and drop-in directory membership, is part of
the plan precondition. A changed source or a new drop-in makes apply stale.
For sysusers, a foreign user declaration can imply a group of the same name,
and a membership line can imply its group; the shared shadow check includes
those effects along with explicit group declarations.
Same-basename masking is always surfaced at file level, even when the target
file wins the directory precedence order. The caller may accept that one mask
with the exact other path and `key = "$file"`; the mask remains a plan note.
After post-actions, verification recomputes the effective order and caller
intent against the current drop-ins. Systemd's `/dev/null` drop-in masks are
captured explicitly; other symlinks are refused.

The unit layer snapshot uses the shared `TargetClass` reader, but directive
analysis needs a systemd-specific parser before it can use generic findings.
The shared API's target order, effective files, normalized assignments and
findings remain useful to formats with a single first/last merge rule; see
[`SPEC.md`](../SPEC.md#shared-shadow-api-for-layered-kinds).

### Native validation and fixed GIDs

Native validators supplement the Python checks. A `systemd-sysusers --dry-run`
can report an invalid name or GID and still exit successfully, so Augeaswrap
uses systemd's name and numeric GID rules in its request types and inspects
staged dry-run diagnostics. A requested fixed GID already held by another
group is refused during planning. Endpoint `@gid:` resolution then uses only an
existing group entry or a prior fixed declaration that is the effective group
definition and passed this check; it never guesses an automatically allocated
value.

The sysctl native-order check resolves
`/usr/lib/systemd/systemd-sysctl` before trying PATH. A missing executable or a
failed probe is a named plan refusal. A leading `-` is systemd's ignore-errors
marker, so it is not accepted as part of a requested key; otherwise a fake root
could skip the live `/proc/sys` check and miss the target in the shadow scan.

For tmpfiles validation, systemd receives the absolute private staged fragment
as its `--create` operand. `--root` selects the staged account database and
filesystem, but does not redirect an absolute operand from the host namespace.
Passing the staged file prevents an older installed fragment from satisfying
validation for different requested bytes.

### Membership and group removal

The request lists dependency order explicitly. A `sysusers` table must precede
an endpoint that uses `@gid:<group>`; that value resolves from the declared
fixed GID or an existing `/etc/group` entry. The endpoint publish action runs
after earlier sysusers actions, so the group exists before its numeric GID is
published. A group with an automatically allocated GID cannot be guessed
during planning, so that reference is refused. A group scheduled for removal
cannot back an endpoint `@gid:` value. A `sysusers` membership change for a
group removed in the same request is also refused, regardless of table order.
This prevents a later `gpasswd` action from targeting a group already deleted;
leave that membership operation out when deleting the group, or apply any
needed membership change in an earlier request. An endpoint withdrawal must
precede every unit-removal resource; planning refuses the reversed order.
Member account names have a separate rule from sysusers group definitions:
they may contain periods and may end in `$`, while group names retain the
systemd sysusers grammar. The gpasswd `-a` and `-d` options consume the next
argument as the user, so argv places the user immediately after the option and
uses `--` before the group (`gpasswd -a <user> -- <group>`). The argument
terminator protects group names without being mistaken for the option's user.
Because TOML groups array-of-table instances by kind, repeated tables of one
kind must be contiguous; the loader and serializer refuse an interleaving that
would change request order. Membership changes, group creation or removal,
live sysctl application, and tmpfiles path changes are journaled as irreversible
effects; revert restores managed fragments and does not undo those effects.
Group removal has no safe automatic inverse, and the current transaction
revert gate therefore refuses the whole transaction when a changed `[[group]]`
resource is present. Keep group removal in its own request when sibling changes
need to remain revertible.

### Membership-only sysusers requests

A present `sysusers` resource with `groups = []` carries membership actions
only. Its file image is the existing fragment image, if one exists, so applying
the request does not replace that fragment or clear its group rows. This keeps
a request to add or remove supplementary membership from silently becoming a
file cleanup request; existing stale rows remain until a caller submits an
explicit group definition update. With no existing fragment, the request does
not create an empty one. The pasteable request shape is in the
[consumer guide](CONSUMERS.md#membership-only-sysusers-requests).

For a fake root, the endpoint adapter writes the fragment and generated files
under that root during commit. The plan's `skipped(root)` status refers only to
the live endpoint CLI post-action; that subprocess does not run against the
fake root. Live `/` publication remains ordered after earlier sysusers actions.
