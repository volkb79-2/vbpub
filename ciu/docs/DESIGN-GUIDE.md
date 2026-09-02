# CIU — Design guide: structured worktree control (WHY)

For adopters and reviewers who want the reasoning behind the machine-readable
surface, not just how to use it. The authoritative normative contract is
[SPEC.md](SPEC.md) (§S16.4, §S16.5); the adoption walkthrough is
[CONSUMERS.md](CONSUMERS.md); the capability list is `ciu capabilities`.

## Why a versioned, closed JSON surface at all

CIU's worktree lifecycle is an *environment provider* for human tools, IDEs,
test fan-out, and durable automation (nyxloom). Before this milestone an
automation consumer could only:

- parse CIU's **prose** output (fragile: phrasing changes freely);
- infer what CIU can do from its **version number** (SemVer says nothing about
  which capabilities a given build shipped);
- trust a **name or stale record** as if it were a current Git fact (a
  worktree can move, be removed, or sit on a branch it was not created on).

All three are the same failure: an automation boundary that treats *presentation
or inference* as *identity*. The decision D-009 (see `nyxloom-trove/decisions.md`)
replaces them with two explicit contracts:

1. **Structured JSON documents** (`schema_version: 1`) with a closed
   `operation` and closed `status` vocabulary. A consumer can validate the
   shape against the shipped vocabulary instead of string-matching.
2. **A versioned capability allowlist** (`ciu capabilities --json`). A consumer
   allowlists the identifiers of the contracts it actually depends on; a CIU
   that does not advertise an identifier cannot be silently trusted to provide
   the feature, and a CIU that does is pinned to the code path that shipped
   with that identifier in the same release.

## Why Git facts are freshly derived, never frozen or guessed

The durable instance record (`ciu.worktree-instance.json`) deliberately stores
**no current HEAD** and no derived state that Git already owns (decision
D-003). It stores only identity facts CIU created: logical name, display name,
branch, Git path, CIU-root offset, allocation time, base reference, lifecycle
state, and runtime identity. Inspection (`ciu worktree inspect`) therefore
re-derives current Git facts from the live `git worktree list` and
`git status --porcelain` every time.

This split has one rule: **a mismatch is a refusal, never a repair.** If the
record says branch `feature-x` but Git registers `main`, or the record's
checkout is no longer a registered worktree, CIU refuses with the discrepancy
instead of quietly reporting either value. The alternative — "repairing" the
record or reporting an inferred value — is precisely the silent-wrong-answer
anti-pattern the estate doctrine forbids: an automation consumer that reads a
"branch" from a stale record would run the wrong checkout's tests with a
convincing-looking label on the result.

The same refusal applies to `git status` being unreadable: an unknown state is
reported as a refusal (`[S16] could not read git status`), never collapsed into
"clean". And `(detached)` is its own boolean, never folded into "attached" or
"unknown".

## Why `env generate` never trusts ambient identity values (CIU-41)

`ciu.env` is the machine-identity record every later ciu command trusts, so
its generation must be immune to the shell that happens to run it. The
documented convenience pattern — a login shell sourcing a checkout's
`ciu.env` — means an agent's non-interactive shell carries ANOTHER checkout's
`DOCKER_NETWORK_INTERNAL`; the pre-2026-08 generator adopted that ambient
value unconditionally, so a fresh worktree's generate silently pointed the
new instance at the main stack's network (a Mode-B instance becoming Mode-A
with no error anywhere — the masked-default anti-pattern: invisible in every
context where you'd notice, surfacing only in exactly the fresh-worktree run).

The fix extends the S2.7 refined precedence already proven for
`PHYSICAL_REPO_ROOT`: derive from this physical root alone; adopt an ambient
value only when consistent; on mismatch warn naming the ignored value and the
S16.1 remedy. The strict alternative ("ignore ambient outright") was rejected
because consistency-checking keeps deliberate pinning working while still
failing safe, and because the warning text is the teaching moment for
`worktree add --shared-infra` — which is the *supported* mechanism for
reusing another instance's infra (per-service selective join with validation),
not whole-instance network inheritance.

## Why `dev`/`worktree` refuse an ambient REPO_ROOT that disagrees with the derived root (CIU-53)

The section above closes the masked-default hazard for the identity tuple
`env generate` writes. `REPO_ROOT` itself carries the SAME hazard one level
up, for a DIFFERENT check: `dev.resolve_repo_root` (consumed by `ciu dev` and
every `ciu worktree *` verb) decides which repo a command operates on in the
first place, before any identity is derived or read. The documented
convenience pattern — a login shell sourcing a checkout's `ciu.env` — means an
operator's or agent's shell can carry ANOTHER checkout's `REPO_ROOT` while
they stand inside a completely different, real CIU repo. The pre-fix
resolver checked that ambient value before even `--define-root`, so it
silently outranked both an explicit flag AND a successful derivation from
where the invocation actually happened — this is exactly how an operator
standing in one repo, with no `--define-root`, had a `ciu worktree list`
answered with an unrelated sibling checkout's worktrees.

The fix reorders and extends the S2.7 refined-precedence pattern
(`_compute_network_name` above): `--define-root` always wins outright (no
consistency check — an explicit flag is not second-guessed); otherwise CIU
derives by walking up from cwd for `ciu.global.defaults.toml.j2`. Where
`env generate`'s identity tuple WARNS on a mismatch and proceeds with the
derived value (the value is about to be freshly written to `ciu.env` anyway),
`resolve_repo_root` REFUSES instead: it feeds destructive verbs directly
(`worktree rm`, `branches -y`, `clean`) in the SAME invocation, with no
freshly-generated file downstream to correct a wrong guess. Silently picking
either value — the ambient one (today's bug) or the derived one (a new,
different surprise for an operator who set `REPO_ROOT` on purpose for a
legitimate reason) — trades one masked default for another. A hard stop
naming both paths and three remedies (unset `REPO_ROOT`, pass
`--define-root` explicitly, or `cd` into the intended repo) is the only
response that never silently operates on the wrong repo. Only when the
walk-up finds NOTHING at all — cwd is not inside any CIU repo, so there is no
derived answer to disagree with — does CIU fall back to ambient `REPO_ROOT`,
unchanged from today: this is the one case where trusting an already-sourced
`ciu.env` from an unrelated location is a reasonable convenience rather than
a masked default, since there is no different, correct answer being hidden.

## Why identity facts moved into a real, gitignored FILE — not a Jinja global (CIU-60)

The two sections above close the ambient-trust hazard for the values
`env generate` DERIVES and for the resolver that decides WHICH repo a verb
operates on. This one closes it for the facts a TEMPLATE consumes about the
workspace that was already discovered — the last leg of the same question an
operator asked directly: *should the `env` / `ciu.global.defaults.toml.j2`
usage be reconsidered for every `ciu` verb?*

The gap was real and asymmetric. Hooks stopped trusting ambient environment in
S9.3: a hook receives `ctx.instance_id`/`ctx.network` read from THIS
workspace's own record by exact path — `ciu.env` at the time, the
`[ciu.instance.generated]` facts file since CIU-75 (see the section below). Jinja template rendering never got
that treatment — S3.2's `env` context is still raw `os.environ`. So the exact
scenario CIU-41 and CIU-53 were filed over (a login shell that once sourced a
sibling checkout's `ciu.env`, which is a *documented* convenience) renders a
template's `{{ env.PHYSICAL_REPO_ROOT }}` as the OTHER checkout's host path —
silently, into a bind mount, with no error anywhere.

The obvious fix was to inject the facts into the render context as fresh Jinja
globals (`ciu.physical_repo_root` and friends, computed per render). That was
proposed and **rejected**, correctly: it manufactures a variable that appears
from nowhere, backed by no file, that an operator cannot inspect, diff, or
`cat` — the "magically available var" hazard this whole line of work exists to
remove. Trading an ambient value for an invisible one is not a fix; it just
moves where the surprise lives.

What shipped instead reuses a mechanism that was already there, already
gitignored, already merged into every render, and already proven out by CIU-52
for a different field: the per-checkout overlay (then named
`ciu.global.worktree.toml.j2`). `ciu env generate` upserted one CIU-owned table
into it, `[ciu.instance.generated]`, carrying the same six values it just
wrote to `ciu.env`, from the same in-memory tuple. Templates then read them
the way they read every other config value, through the ordinary merge chain,
with no new context-building code anywhere. Three properties fall out of that
choice rather than having to be engineered:

- **Every value is backed by a file.** `cat ciu.instance.generated.toml` shows
  exactly what CIU derived for this checkout. A wrong render is now diffable.
- **The primary checkout is covered for free.** `render_global_chain` reads
  this file unconditionally by exact path, with no S16 instance-record gating,
  so the write side does not gate either — which matters, because the main
  workspace is where the operator was standing when they hit the bug, and a
  worktree-only fix would have left exactly that case broken.
- **`ciu clean` already preserves it** (S3.1b), so the facts survive a
  teardown; a full reset is the explicit `--vanilla` opt-in.

The rendered `ciu.global.toml` was considered as the destination and rejected
on a mechanical fact, not taste: it has no state preservation. Only a stack's
own `ciu.toml` preserves a `[state]` table across re-render (S3.4); the global
rendered file is regenerated whole from its source layers on nearly every
verb, so anything written directly into it that is not re-derived identically
every time is silently lost. `ciu.global.toml.j2` was rejected because it is
committed — writing machine-specific host paths into a tracked file is how one
developer's mount path reaches everybody.

The write was a **surgical text replace of that one table**, not a
`tomllib` parse plus a `tomli_w` dump of the whole file. A full round-trip
would carry every VALUE across correctly and destroy every comment and every
hand-chosen bit of formatting on the way — in a file S3.1b explicitly invites
operators to edit. Owning exactly the bytes between the table's own header and
the next table (minus the trailing comment run that belongs to that next
table) was what let CIU rewrite its facts on every single `env generate`
without ever touching a line a human wrote.

## Why that mechanism was then deleted rather than hardened (ciu-P47)

Everything above is true of the design as it shipped, and the surgical replace
did its job. It was still the wrong shape, for a reason that has nothing to do
with whether the implementation was correct: **it existed only because two
owners shared one file.** A byte-level scan for a table header, a scan for the
next table, and a walk back over the trailing comment run are three chances to
be subtly wrong about a file whose contents CIU does not control — and the
docstring had to carry a known, accepted limit (a line reading exactly
`[ciu.instance.generated]` inside a multi-line string elsewhere in the file
would be mistaken for the header).

ciu-P47 removed the shared ownership instead of hardening the scan. The
CIU-owned facts moved to `ciu.instance.generated.toml`, a file nothing but CIU
ever writes, so the writer became a full-file rewrite with no preservation
logic at all, and the operator's own file — renamed
`ciu.global.instance.toml.j2` in the same change, because every checkout is an
instance and not every checkout is a git worktree — gained a stronger
guarantee than the surgical replace could give it: CIU has no writer for it.

The general lesson, which outlives this file pair: when a mechanism exists to
make two owners safe in one place, ask whether the two owners have to be in one
place. Splitting is usually cheaper than the machinery for sharing, and it
converts a property you have to keep proving into one that is true by
construction.

## Why the SECOND record then had to become the ONLY one read (CIU-75)

CIU-60 above left two records of the same six facts: `ciu.env`, which CIU
itself read, and the generated table, which templates read. Written together
from one in-memory tuple, so they agree at birth — and nothing anywhere
noticed when they later disagreed. That is the shape this codebase keeps
finding: not a wrong value, but two places a value can come from and no rule
about which wins.

The v8 proposal's F2 fork settled it in the direction the estate already
argues for — a real file, in this repo root, that you can `cat` — and CIU-75
backported it: **the table is the only record CIU reads instance identity
from**, and `ciu.env` becomes an export nobody inside CIU consults for
identity. `ciu env generate` keeps writing it, unchanged, because a shell
`source`ing it is a legitimate consumer and breaking every one of those on the
same day would be gratuitous.

Two design choices in that cutover are worth the ink, because both look
arbitrary until you try the alternative.

**The reader is a text-level scan of CIU's own block, not a config render.**
The obvious implementation is "render the merged chain and read
`ciu.instance.generated`". It fails on its own terms: the overlay is a Jinja
template whose render needs the merged config it is a layer of; six of the
twelve call sites read a checkout that is NOT this process's repo root (a
shared-infra reference, a budget candidate, a reap group) whose committed
chain may legitimately be absent or broken; and the block is merged last, so
its own bytes ARE the merged value — a render could only agree with it. The
block is plain TOML by construction (the writer emits quoted strings), so it
is readable with no context at all. The reader slices to it before parsing,
which is also why the migration helper published for consumers does the same:
an operator's own Jinja or TOML elsewhere in that file must not break the
identity read.

**Moving the twelve reads was not the cutover — the process environment was.**
The first implementation migrated all twelve call sites, documented the
boundary, and was still wrong end-to-end: STEP 1 of every verb seeded
`os.environ` from `ciu.env` and seeded it *skip-if-present*, so an inherited
value was never displaced. Around 26 internal sites read those keys straight
from ambient, and so does every `$VAR` in a rendered config — the shipped
`network_name = "$DOCKER_NETWORK_INTERNAL"` among them. The documented
convenience of a login shell sourcing a sibling checkout's `ciu.env`
therefore still won a real render, and containers would have joined the
sibling's network: CIU-41's hazard, surviving in the one place a per-site
migration cannot reach. The fix seeds the six facts from the table
**unconditionally** — override, never skip-if-present, because skip-if-present
only helps in the case that does not bite.

That override is a deliberate behaviour break: exporting `INSTANCE_ID` no
longer steers a run. It has to be. "The record is authoritative *unless* your
shell disagrees" is not an authority; it is the two-records problem again with
extra steps. The way to change identity is to change the record —
`ciu env generate`, which still honors a pre-set value when it *agrees* with
what it derives (S2.7's refined precedence), or the table itself.

The lesson generalizes past this feature: **a cutover is complete when the
old source cannot influence the answer, not when every direct read has been
rewritten.** The direct reads were the visible half. The process environment
was the half that carried the bug, and it was invisible to an oracle that
drove the migrated functions directly — and to every other test in the suite,
because `tests/conftest.py` scrubs ambient identity before each one. Proving a
cutover therefore needs an oracle of a different kind from the one that proves
each site: a real verb, a hostile ambient value, and an assertion about what
the render actually saw.

## Why templates see `ciu.*` selection facts but nothing is persisted (CIU-44)

A feature flag like reverse-proxy's "enable the MCP proxy if pwmcp is
deployed" is a fact about the SELECTION, not about the machine — so it
belongs in the render context, computed once per invocation and threaded
unchanged to every template and hook (S3.12). The two rejected alternatives
both fail the same way: writing `CIU_SERVICES_PROFILE` into `ciu.env`
conflicts with S2.7's "generated facts only" rule AND creates a stale-file
authority (generate writes X, operator runs `up --profile Y`, template sees
X); exporting an environment variable re-creates the ambient-inheritance
vector CIU-41 just closed. Outside deployment renders the key is omitted so
references fail loudly — an empty-list default would be a silent wrong answer
wearing a fallback's reputation.

## Why `clean` removes networks and names what it keeps (CIU-43)

v1's "network removal NOT performed" posture leaked one identity-scoped
network per ephemeral teardown while printing `clean complete` — twice
reproduced live on released versions. The instance-vs-main split follows from
what each checkout IS: an S16 worktree instance is ephemeral by contract, so
unconditional removal of everything carrying its identity is the correct
default and no flag should be needed to ask for it; the main workspace's
network hosts the devcontainer itself, so removal would cut the operator's
own cockpit off — hence keep-with-notice, where the output names the kept
object in plain words AND in the final success line. Endpoints are
disconnect-or-refuse-named (never silently kept), and the post-clean
invariant re-reads Docker STATE rather than trusting command exit codes or
diagnostic text.

## Why there is no compose project without `-p` (CIU-46 cutover)

The pre-CIU-46 fallback let a config-less shipped stack run under docker's
directory-derived project name. That name is a silent-invention default in
the exact sense this estate forbids: it substitutes "what the cwd happens to
be called" for identity, it is IDENTICAL for every checkout of a repo (so a
second worktree's `up` adopts the first one's containers), and it was a value
CIU itself never learned — clean's S6.4a enumeration could not see it, which
is how shipped stacks' `*_default` networks and label-prefixed volumes
survived a printed `clean complete`. Three shapes were considered:

1. **Enumerate what docker derived** — clean predicts the basename name.
   Rejected: prediction is not knowledge; any divergence between compose's
   normalization and ours silently recreates the leak.
2. **Keep the basename fallback, computed and passed as `-p`** — up/clean
   agree by construction, but the cross-checkout collision class survives.
3. **Derive the name from workspace identity** (`REPO_NAME-INSTANCE_ID-stack`
   from THIS checkout's own record, exact-path read — `ciu.env` then, the
   generated table since CIU-75) — adopted. Unique per
   checkout AND per stack; up and clean call the same function; a checkout
   that cannot produce the name refuses loudly instead of inventing one.

The basename fallback is withdrawn outright rather than deprecated: the
estate rule is derive-read-fail, never invent, and "whatever this directory
is called" is invention. Cost: deployments created before the cutover keep
their old-named objects until migrated once by hand (CONSUMERS.md §11); the
S8.7 migration guard still catches the collision on the next tagged `up`.
The S16.1 shared-infra join refusal fell out as dead code — it existed
because the fallback's name was unknowable, and now nothing is.

## Why bare `hostname:` / `internal_host` defaults are dangerous (CIU-48/CIU-49, §3.6 cockpit-alias-ambiguity)

Docker independently registers **two** network-resolvable DNS aliases for a
container: the compose service KEY (always, automatically — CIU-51, a
separate, v8-scale item this section does NOT eliminate) and whatever value a
`hostname:` line sets. Both are looked up the same way by anything on the
network. When two CIU-deployed instances of the *same stack shape* coexist on
a shared/joined network — the exact `ciu worktree` + `--shared-infra`
scenario S16.1 exists to support — a **bare** value on either axis (a
`hostname:` literally set to the plain service name, or an
`internal_host` config default rendering the plain service name) resolves to
*whichever* instance's container Docker's resolver happens to answer with:
non-deterministic from the caller's perspective, and silent — no error, no
warning, just occasionally the wrong instance's data. This is the §3.6
cockpit-alias-ambiguity hazard (matching the dstdns filing's own term), named
here so a reader can find the same term in `KNOWN_ISSUES_TODO_BACKLOG.md`'s
CIU-48/CIU-49 history.

```
# before — bare, ambiguous once a second instance joins the network
hostname: vault

# after — qualified with the SAME identity facts container_name() already
# uses, unique per (project, environment_tag) pair
hostname: {{ deploy.project_name }}-{{ deploy.environment_tag }}-vault
```

Both `hostname:` (a compose template's own declared value, CIU-48) and
`topology.services.<name>.internal_host` (an application-config default,
CIU-49) are values CIU's render layer already has the qualifying identity
facts (`deploy.project_name`/`deploy.environment_tag`) to derive uniformly —
exactly the facts `container_name()` (`src/ciu/deploy.py:138-151`) already
uses, so qualifying them is not a new derivation, only reusing the existing
one instead of leaving the field bare. **What this does NOT cover:** Compose's
automatic bare service-key alias is a mechanism Compose itself creates with
no documented per-network suppression (CIU-51) — nothing in this package
removes it. Qualifying `hostname:`/`internal_host` closes the two
consumer-controllable value defaults; the service-key alias remains available
regardless (so intra-stack bare-name reachability is not lost), and closing
it fully is out of scope here.

## Why provenance declares vendor images by reference, not digest (CIU-39)

`ciu provenance` compared every running image's OCI revision label against
the commit under test — a check that can never pass for an image ciu never
built. Vendor artifacts (vault, authentik, consul) carry no ciu bake, sat at
`unlabelled`, and pinned all-vendor deployments at `not-verified-no-evidence`
forever; `verified-match` was unreachable live, which blocked assay's
adjudicated-provenance integration (B004). Two shapes were on the table:

1. **Digest pin file** (`image@sha256:...`, verified against RepoDigests) —
   the stronger guarantee, rejected for now: it creates a pin-file
   maintenance surface no consumer has (dstdns pins tags in its service
   registry, not digests), adds `docker inspect` surface, and its failure
   mode (stale pin after a routine upstream bump) would train operators to
   ignore red provenance — a gate that cries wolf protects nothing.
2. **Declared references** — adopted. The declaration says exactly what the
   operator knows: "this exact reference is expected to be third-party."
   Reference equality is checkable from evidence provenance already collects
   (`docker ps`'s image string), so the feature has zero new docker surface —
   compared on Docker-canonical references (registry-host case, implicit
   docker.io/library defaults) so spelling differences cannot defeat pin or
   drift detection. Drift (same canonical name, different reference) is a
   mismatch because the declaration vouches for one artifact; undeclared
   unlabelled images stay `unlabelled` in the document and contribute
   nothing, so a forgotten bake of an own image is never HIDDEN — but be
   precise about the verdict: a pin converts a tree that would have warned
   `not-verified-no-evidence` into a green `verified-match` once no container
   disagrees, exactly as an own-image `match` always has. Only auditable
   config (falsely declaring an own image as vendor) escapes entirely.

A declared image is never judged by the commit label even when it carries
one: an upstream revision belongs to the upstream build. The vocabulary
widening bumps every document to `schema_version: 2`; the seven CIU-20-era
fixtures stay frozen as the schema-1 historical record, and strict consumers
refuse unknown members — fail-closed in both directions.

## Why one envelope and one closed vocabulary for every document

`create`/`ensure`/`adopt`/`add`, `inspect`, `list`, and `remove` all speak the
same envelope: `schema_version`, `operation`, `status`, `instance` (the
persisted record), plus `git` where fresh facts apply. There is exactly one
place a consumer must learn the shape, and the closed vocabulary is a single
constant in code (`WORKTREE_JSON_OPERATIONS` / `WORKTREE_JSON_STATUSES`), so a
new value cannot be introduced accidentally.

Removal keeps the same contract under partial failure: CIU captures the
validated pre-state, runs `ciu clean` **then** `git worktree remove` (the
normative order — see SPEC §S16), and only then emits `"status": "removed"`.
A failed clean or failed git removal raises the existing `WorktreeError`,
which names the retained resources; there is never a success document for a
removal that did not happen. This matters because the failure mode is
unrecoverable-by-CIU: once the checkout is gone, so is the rendered config
that told CIU what to clean, and root-owned `vol-*` directories can then only
be removed by hand.

## Why `capabilities` is a separate, sorted, closed allowlist

`worktree.identity.v1`, `worktree.inspect.v1`, `worktree.lifecycle-json.v1`,
`worktree.up.v1`, `worktree.exec-local.v1`, and `worktree.exec-target.v1` are
the identifiers advertised in this release. Each maps to a shipped code path:

- `worktree.identity.v1` — schema-v1 instance records and the create/adopt/
  ensure/add lifecycle;
- `worktree.inspect.v1` — the inspect document and the managed list document;
- `worktree.lifecycle-json.v1` — the lifecycle JSON envelopes;
- `worktree.up.v1` — exact selected-worktree `up` (S16.6);
- `worktree.exec-local.v1` — exact local `exec` (S16.6);
- `worktree.exec-target.v1` — declared container-target `exec` (S16.7).

An identifier is added to `WORKTREE_CAPABILITIES` in the same commit as the
code path it names, or not at all.

## Why `up` and `exec` take one exact selected instance

A worktree family has one primary checkout and many linked ones; each linked
checkout is a distinct CIU instance with its own `INSTANCE_ID`, network, and
`REPO_ROOT` (a hash of the *physical* path, S2). Running a command "in the
worktree" from the wrong process environment is therefore the same failure as
the one inspection guards against: the ambient `REPO_ROOT` describes the
PRIMARY checkout, so a naive `cd <worktree> && ciu up` would argue about which
instance is real, and could act on the wrong one.

Both `worktree up` and `worktree exec` therefore build the child environment
from the SELECTED instance's own facts, read by exact path (`ciu.env` before
CIU-75, `[ciu.instance.generated]` since), after stripping every CIU identity
key from the ambient environment. The selected value must
agree with the durable record (`REPO_ROOT` = the record's CIU root, and the
record's `INSTANCE_ID`/network) — a mismatch refuses rather than running with
a mixed identity. This is the same "derive or read, never invent" rule as
inspection, applied to where a command executes.

`exec` deliberately never starts anything: it is the *execute-in-this-exact-
place* primitive, not a shortcut for `up`. It requires a `--` separator and
passes argv to `subprocess.run` as a list with no shell, so spaces, globs,
`$()`, semicolons, and leading dashes arrive byte-for-byte at the child and
cannot be interpreted by a shell or misparsed as CIU flags. The child's exit
code is returned exactly — an automation lane that runs a gate command through
`exec` needs that code, not a wrapper's guess.

## Why container targets are declared aliases, not arbitrary services

`exec --target` lets an automation lane run a command *inside* the stack's
tester container. The dangerous alternative is "pick any service by name" —
an arbitrary service-selection escape hatch where a consumer could reach a
container that was never meant to be an execution boundary. CIU therefore
requires the target to be **declared** in the instance's own global config
(`[ciu.worktree.exec_targets.<alias>]`) with exactly four keys (`stack`,
`service`, `workdir`, `requires_worktree_mount`) — no arbitrary selection, no
invented defaults. The alias is a Git-safe single component, and an unknown
key or malformed value refuses before Docker is ever touched.

Selection is by the **exact** compose project/service/network identity of the
selected instance (derived with the existing naming rule and the instance's
own `DOCKER_NETWORK_INTERNAL`), and exactly one already-running container must
match. `up` is never started implicitly: a missing container is a refusal, not
an invitation to create one.

## Why the worktree-mount proof exists, and why it reads Docker's output

A container that looks like the right project/service could still be the
WRONG checkout — the primary worktree mounted while a linked worktree is
selected, or a sibling's container on a different network. By default
(`requires_worktree_mount = true`), CIU proves the container has a bind mount
whose host source is the selected Git worktree's **physical** path at a path
containing the declared `workdir` before running anything.

The proof reads only Docker's own `inspect` output, never a local filesystem
predicate on a path belonging to the other namespace. The host-side `Source`
is compared against the physical translation of the record's Git path (via
`to_physical_path` with the target's own REPO_ROOT/PHYSICAL_REPO_ROOT), and
the container-side `Destination` against the declared container `workdir` —
each value is compared in the namespace it belongs to. This is the estate's
namespace-translation rule applied to containers: an `is_file()` on a
container→host translation would ask the wrong kernel, exactly the CIU-15 /
dstdns class of incident the doctrine records. `requires_worktree_mount =
false` is the explicit opt-out for a deliberate non-source utility container;
it never weakens project/service/network uniqueness.

## Rejected alternatives

- **SemVer-based feature inference** — rejected: a version bump carries no
  per-capability signal, and consumers would parse version strings (the same
  string-matching fragility as prose). Capability identifiers are the
  allowlist primitive instead (D-009).
- **Freezing HEAD in the instance record** — rejected (D-003): the record is
  durable identity, not a cache; a frozen HEAD goes stale the moment the
  branch moves and would then be a *wrong* answer wearing a *durable* label.
- **A single unbounded JSON blob with whatever keys happen to exist** —
  rejected: closed vocabulary + a fixed schema version is what lets a
  consumer fail fast on an unexpected shape instead of silently ignoring
  unknown fields.
- **Shell-form exec (`sh -c "..."`)** — rejected: a shell rewrites argv
  (spaces, globs, `$()`, `;`), so an automation lane could not trust that
  what it asked to run is what ran. A list argv with no shell and a mandatory
  `--` is the only faithful form.
- **Arbitrary service-name target selection** — rejected: a consumer could
  reach a container never intended as an execution boundary. Declared aliases
  with a closed key set are the only surface (D-007).

## Why the implementation gate is Assay-backed (S18)

**Why a vendored zipapp, not an ambient install.** The gate must prove it ran
against the *released, immutable* Assay contract. Baking Assay into
`tester-unified` would make a consumer's evidence depend on whichever image
happened to be rebuilt, and would force a whole image rebuild to move between
versions. Vendoring the verified `.pyz` + `.sha256` next to `assay.toml` (the
cmru estate precedent, `cmru/tools/assay/`) makes the pin explicit, hash-verifiable,
and moveable in one commit. The zipapp is built from the wheel (never
`src/`), so its version metadata is trustworthy.

**Why R1 with `repository-minus-unsafe-symlinks`.** The gate's second half
(the old `nyxloom.coverage_gate` changed-line floor) exists so a changed
executable line can never ship uncovered — and pragma-excluded lines are
invisible to `--cov-fail-under=100`, so only a *diff-aware* judgment can
enforce "no pragma on changed code". Assay's R1 reproduces exactly that
floor and adds a verifiable verdict. R1+ runs in an isolated snapshot of the
committed tree; this monorepo tracks exactly three absolute-target
security-fixture symlinks (topos), which the snapshot substrate refuses, so
the lane declares them via the documented monorepo shape. That carries a
maintenance obligation on purpose: a *new* unsafe symlink anywhere reds the
lane until its owner reviews it — nobody's evidence silently widens to cover
a fixture nobody looked at.

**Why a clean-tree requirement.** The diff `base..HEAD` is committed-to-
committed. An uncommitted change under a source root would be invisible to
it, so "0 changed lines" from a dirty tree would certify a measurement that
never actually saw the change under test. Refusing a dirty tree
(`NO_MEASUREMENT`/`DIRTY_TREE`) is the only honest reading; it is why the
pre-existing untracked `_last-summary.txt` is gitignored rather than left to
poison every gate run.

**Why the cgroup slice comes only from the environment.** A literal slice in
the gate config is a shadowing default for a fact that has an authoritative
source (`$CGROUP_PARENT_DEV_BACKGROUND`, injected by the devcontainer).
Systemd silently auto-creates a typo'd slice as an unlimited transient
slice, so the gate both refuses an absent variable (`${VAR:?}`) and verifies
the named unit is `LoadState=loaded` before launching Docker — fail-closed on
both axes.

**Why the gate's status is the Assay job's.** The Assay lane's exit code IS
the gate's exit code — no trailing wrapper, pipe, or `|| true` can turn a
failed job green. The verdict JSON is written outside the snapshot (gitignored
`.assay/`), so evidence survives without dirtying the judged tree.
