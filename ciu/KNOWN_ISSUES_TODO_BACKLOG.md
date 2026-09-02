# CIU — known issues, TODO, and backlog

This is CIU's temporary canonical product issue tracker. Consumer projects keep
only pointers to issues filed here. Once nyxloom's per-entry backlog schema is
available, these open entries move to that format and
`nyxloom-trove/backlog.md` becomes canonical.

Normative behavior belongs in [`docs/SPEC.md`](docs/SPEC.md). A FIXED issue
means code, behavioral tests, SPEC, and user documentation landed together. A
WITHDRAWN issue means the claimed product behavior was removed or never
adopted after its premise was disproved; it must not remain described as a
shipped capability.

Last updated: 2026-09-01 — **F4 and F7 BACKPORTED EARLY (ciu-P46), ahead of
the v8 cutover.** `docs/CIU-V8-TESTING-GATE-PROPOSAL.md`'s secrets-audit table
lines 880 (F4, "Vault bootstrap out of `[state]`") and 883 (F7,
"Vault-presence static rule") are part of the v8 redesign of secrets and
identity (§10 / `SPEC-V8.md` §S10, §S4.1), whose full schema cutover
(V8-1..V8-20) is a large separate program that has not started. Both pieces
are self-contained, already-decided in the design docs, and depend on no v8
schema or registry work, so they were landed on today's v7 model now rather
than held: F4's exposure (a Vault root token written in plaintext into the
stack's ordinarily-rendered, ordinarily-readable `[state]` table, outside
every S4 leak-prevention mechanism) is live in every consumer today, and F7's
gap turns a static config defect into a mid-`ciu up` runtime failure.
Landed as: `persist: "secret"` (S9.4a), S4.16 source #3 migrated onto the
secret store with NO fallback, `ciu check`'s new `vault-presence` (S13.4d),
`state-secrets` (S3.4a) and `migration` stages, and the `ciu migration-check`
verb + rule registry (S13.7). The registry is deliberately extensible: the
follow-up overlay-file split/rename (ciu-P47) adds one detector body and
touches no plumbing.

**CIU-38 is explicitly NOT affected and stays OPEN/deferred.** A mid-design
framing suggested `persist: "secret"` would unblock per-service Vault AppRole
provisioning; that was wrong and was corrected before ciu-P46 was dispatched.
AppRole credentials route through **Vault itself** — a hook mints them into
Vault with its own `hvac`/HTTP calls and the consumer reads them back with an
ordinary `ASK_VAULT` directive — which works today with zero new CIU
mechanism (see `docs/V8-REALIZATION-GRAPH.md`'s traced example).
`persist: "secret"` exists solely for values that no directive **could**
express, Vault's own root token/unseal key being the only current instance.

Previously, 2026-08-31 — **CIU-54 FIXED (ciu-P45).** The 8 `cli.py` call
sites CIU-53's own follow-up named (`render`/`up`/`down`/`health --host`,
`up --layout`, `layouts`, `host-secrets`, `ssh`) resolved `repo_root` via a
bare `REPO_ROOT`-or-cwd fallback with no `--define-root` consideration at
all — a third, informal resolution strategy alongside `dev.resolve_repo_root`
(CIU-53) and `deploy.resolve_repo_root`. Design pass picked candidate (a):
route all 8 through `deploy.resolve_repo_root` (the SAME resolver each
verb's own local/profile branch already uses), not `dev.resolve_repo_root`'s
walk-up, because these verbs' remote-push/listing usage shape is a
`deploy.py` sibling, not a `dev`/`worktree` local-repo-identity question —
and because walk-up would have made e.g. plain `ciu up` resolve one way and
`ciu up --host x` resolve a DIFFERENT way. A new `_extract_define_root()`
consumes the flag before any per-site parser (or `_parse_layout_argv`'s
forbidden-flag guard) sees it, with `allow_abbrev=False` so ciu-P29's pinned
`--d`/`--r` abbreviation tests are unaffected. **Breaking**: no cwd
fallback — ambient `REPO_ROOT` or `--define-root` is now REQUIRED on these 8
verbs; nil blast radius inside this monorepo, `docs/CONSUMERS.md` #19 and
`docs/SPEC.md` S1.1a carry the detail. Full scope landed, all 8 sites, one
mechanism.

Previously, 2026-08-31 — **CIU-59, CIU-61, CIU-84, CIU-85 FIXED (ciu-P44,
a four-item bundle); CIU-86 FILED.** CIU-59: `workspace_env.
detect_devcontainer_name()` factors the four-times-duplicated devcontainer/
hostname fallback, correcting one real semantic drift found while verifying
(an explicitly-empty `DEVCONTAINER_NAME` now falls through to `HOSTNAME`
everywhere, matching the majority shape instead of one site's divergent
nested `.get`). CIU-61: `.gitignored.ciu` ITSELF had the bug the row's
premise assumed away (`ciu.global.toml.j2`/`**/ciu.toml.j2` wrongly listed
as ignored — they are committed override templates, S3.1a/CIU-8) — corrected
first, then `ciu init`'s `_GITIGNORE_ENTRIES` reconciled against it (three
real entries added) with a new test enforcing both files agree from now on.
CIU-84: a full sweep, not a one-line patch — four ungated `_run`-level
`info()` calls plus a second class entirely (two `[WARN]` deprecation
notices in `provisioning._probe_stack`, reached only through `action_check`'s
`--live` branch); a test now asserts `ciu check --json`'s stdout is `json.
loads`-able as ONE document. Fixing S13.4a's own stale doc text ("the
orchestrator's own [INFO] lines still precede the document") surfaced that
`ciu graph --format json` shares the SAME `_run`-level leak (now also
fixed) plus a narrower, `action_graph`-internal-only remainder, filed as
**CIU-86**. CIU-85: `_CIU_IDENTITY_ENV_KEYS` now derives its identity half
from `GENERATED_FACT_ENV_KEYS` (adding `PUBLIC_FQDN` by construction);
`_clean_in` gained the strip its two siblings already had; a THIRD sibling
builder (`_generate_env_in`) found carrying the same stale hand-written
literal was folded into the same fix. See each row for full detail.

Previously, 2026-08-31 — **CIU-75 FIXED (ciu-P42, after a round-1 REJECT
completed the cutover and a round-2 docs sweep); CIU-83 FIXED (controller,
`CHANGES.md` entries added for ciu-P43's four items before tagging 7.7.0);
CIU-82, CIU-84, CIU-85 FILED.** The v8 F2
identity cutover landed: `[ciu.instance.generated]` in
`ciu.global.worktree.toml.j2` is now the SOLE instance-fact source CIU reads,
and `ciu.env` is a legacy write-only export (still written, unchanged key set,
never read back). BREAKING, ships as **ciu 7.7.0**; new normative SPEC section
**S3.1c** owns identity-source precedence. Twelve call sites classified and
migrated — **and, after review round 1, the STEP-1 process-environment seed
that made all twelve insufficient on their own**: it read `ciu.env`
skip-if-present, so a sibling checkout's sourced identity still won at ~26
ambient-reading sites and in every rendered `$DOCKER_NETWORK_INTERNAL`.
`docs/CONSUMERS.md` §11b carries the consumer migration, including the dstdns
shapes found by a real, twice-run sweep. CIU-82 tracks the dstdns-side
notification that must be filed in that repo's own backlog.

The cutover rebased onto ciu-P43 below and merged with CIU-80's
`HookContext.identity_unreadable`: that flag is kept and re-pointed at the
overlay, and CIU-75 additionally makes its absent-vs-unreadable boundary
honest (P43's `is_file()` guard called a DIRECTORY where the record belongs
"absent"). Read CIU-80's row below as pre-CIU-75 history: the field is the
same field, the record it is about is now the overlay's generated table.
CIU-82 was numbered around P43's CIU-81, which is FIXED below. **CIU-83
FILED** by the same rebase (ciu-P43 landed no `CHANGES.md` entries, so
`[7.7.0]` announced only one of its two breaking changes) **and FIXED** by
the controller before tagging 7.7.0 — see its own row below.

Previously, 2026-08-31 — **CIU-79, CIU-80, CIU-81, CIU-77 FIXED (ciu-P43, a
four-item bundle).** CIU-79: `ciu dev`'s `_build_dev_image` resolves
`build.context`/`dockerfile` against `repo_root` now, sharing CIU-71's
repo-root-relative convention instead of the stack-dir-relative one it wrongly
used before. CIU-80: `HookContext` gains additive `identity_unreadable: bool`
(shape (b), per controller ruling — non-breaking) so a hook can tell a
genuinely unmanaged workspace apart from one whose `ciu.env` exists but could
not be parsed; both S3.12 identity readers (`deploy._workspace_identity`,
`engine.main_execution`'s STEP-12 read) set it identically, as the MANDATORY
pair this row's own text required. CIU-81: `scaffold.py`'s two Jinja render
paths (`_render_jinja`, `build_files`'s preflight `Environment`) adopt
`StrictUndefined`, matching `config_model.render_jinja2_text` — verified first
that every shipped scaffold template needed no lenient-Undefined behavior (the
two TOML templates carry zero Jinja syntax by render time; the one template
with real Jinja refs is never Jinja-rendered by `scaffold.py` at all). CIU-77:
the vendored gate judge bumped `assay-2.3.0.pyz` -> `assay-3.2.0.pyz`, verified
against 3.2.0's real CLI/config contract first (not assumed compatible) —
`assay.toml`'s body needed zero changes, only comment-block version mentions;
the row's own named risks (withdrawn mutation operators, judge provenance,
request-supplied base) all turned out inapplicable to ciu's R0/R1,
static-base lane. Full detail in each row below.

Previously, 2026-08-31 — **CIU-62, CIU-64, CIU-65, CIU-67, CIU-68 FIXED;
CIU-66 BLOCKED; CIU-80 FILED** (ciu-P41, a four-item bundle). CIU-67+CIU-68
were one live failure with two causes: `[deploy.health].timeout` (one probe
attempt's duration) was also serving as the S7.7 gate's overall wait budget,
and the gate that makes a `stack:*:healthy|completed` requirement reliable
was not running by default and could not be discovered from `ciu up --help`.
Now: a distinct `gate_timeout` key, a per-container budget DERIVED from each
service's own healthcheck, a gate that turns itself on for the refs that
need it, and a bounded poll so a dependency reported `starting` is waited
out rather than failed. CIU-64+CIU-65: `ciu up` runs `ciu check`'s static
pipeline itself before STEP 1 and refuses on ERROR findings (`--skip-check`
is break-glass and announces itself), and a hook's `validate_config` finding
can now carry a `WARN`/`ERROR` severity so "worth knowing" and "must block"
are finally different things. CIU-62: seven `ciu.env`-reading `except`
clauses widened to name all three failure modes — one MORE site than the
entry listed (`engine.py`'s S3.12 identity read, found at review) — plus one
deliberate semantics change: `ciu clean` no longer reads an unreadable
`ciu.env` as "this workspace has no identity network"; the two HookContext
identity sites keep their `{}` degradation for symmetry but now WARN on it,
and CIU-80 is filed for the stricter variant. **CIU-66 was attempted and
stopped**: `container_name()` mirrors a convention that consumer compose
TEMPLATES implement, so changing its signature breaks every ciu lookup, and
the stack qualifier it needs is not even expressible in the template context
yet — its row now carries the full blast radius and names the small additive
package that must come first. Merged onto ciu-P40's CIU-70 by hand: the
per-phase probe loop is the one place where CIU-70's `stacks=probe_graph`
and CIU-68(b)'s bounded retry meet, and taking either side of that conflict
wholesale silently reverts the other.

Previously, 2026-08-31 — **CIU-63 FIXED** (ciu-P39). `lint_graph`'s
requires-satisfied pass now recognizes a `stack:<path>:healthy|completed`
ref via the same `_STACK_RE` + `_resolve_declared_stack_path` resolution the
cycle-detection pass already used, satisfied whenever it resolves to a real
declared stack -- no `provides` self-declaration required. Shape (b) from
the original filing was implemented (remove the redundancy at its source),
not shape (a) (document around it); `docs/SPEC.md` S13.2/S13.3 updated.

Previously, 2026-08-31 — **CIU-71 FIXED, CIU-79 FILED** (ciu-P37).
CIU-71: every real `docker compose` invocation (`execute_docker_compose_with_logs`,
covering both native `up` and `--shipped`; `reset_service`'s `down`) now
passes `--project-directory <repo_root>`, so a stack's `build.context`
resolves repo-root-relative as intended; independent adversarial review ran
a live acceptance probe confirming the mechanism, then found three
documentation gaps (worked example needed `dockerfile:` too since Compose
resolves it relative to `context`, not `--project-directory`; the SPEC's
own justification claimed CIU's other paths are already repo-root-relative
when the code says they are stack-dir-relative; `--project-directory` also
relocates `.env` lookup, undocumented) — all three fixed as docs-only
follow-up, no engine.py behavior change. A third review pass caught the
`.env` fix itself framing the relocation backwards (a stack-local `.env` is
DROPPED unconditionally, not "shadowed" only when a repo-root `.env`
exists) — corrected in `docs/CONSUMERS.md` §18. CIU-79: `ciu dev`'s
`_build_dev_image` has the same relative-`context` defect in a `docker
build` invocation, found while confirming CIU-71's own fix was complete;
filed rather than fixed (out of CIU-71's scope, different command, no
`--project-directory` equivalent for bare `docker build`).

Previously, 2026-08-31 — **CIU-69, CIU-76 FIXED** (ciu-P36). CIU-69:
`exec_targets` added to `WORKTREE_TABLE_KEYS`; one test declares all three
`[ciu.worktree]` key families together, asserting budget/lease/exec-target
resolution all accept the combined table; `docs/CONFIG.md`'s stale two-key
description (flagged, deferred, then authorized by review) is now three
keys. CIU-76: `apply_lease` gained the same `now:` override its
`acquire_lease`/`make_lease_perpetual` calls already accepted, threaded
through both; the one test that actually hit the real-clock-vs-frozen-NOW
coincidence now passes `now=NOW` explicitly; independent review found the
`--perpetual` threading itself was untested (deleting it left the suite
green) and a direct oracle was added proving it.

Previously, 2026-08-31 — **CIU-78 FILED AND FIXED.** Two
`test_ciu_deploy_actions.py` tests hardcoded `sys.dont_write_bytecode is
False` after a save/restore, when the real gate's `assay.toml` environment
(`PYTHONDONTWRITEBYTECODE=1`) starts it `True` — independently found by both
ciu-P36 and ciu-P38 while gating unrelated fixes. Fixed directly (capture the
ambient value, compare restoration against that instead of a hardcoded
assumption); `provisioning.py`'s identical untested pattern left as an open
coverage gap, not folded into this fix.

Previously, 2026-08-31 — **CIU-76, CIU-77 FILED**, both found while ciu-P36
gated the CIU-69 fix. CIU-76: `apply_lease` has no `now:` override, making any
test of its time-based behavior fragile against the real advancing clock (one
such test, `test_re_expiring_after_an_extend_becomes_lease_expired_again`,
reproducibly fails on clean `main` today for exactly this reason — not a
lease-logic defect). CIU-77: ciu's own vendored self-test judge
(`tools/assay/assay-2.3.0.pyz`) is three major versions behind assay's actual
current release (3.2.0) — deliberately not bumped immediately, since assay
3.0.0 was itself breaking and five implementers are mid-flight gating against
the just-fixed 2.3.0 pin (`b8102bc2`, a separate same-day fix that only made
the stale pin internally consistent with the also-stale vendored artifact,
not a latest-version bump).

Later, 2026-08-31 — **CIU-74 FIXED (ciu-P38).** `render_jinja2_text` now
renders with `StrictUndefined`; `ciu.instances` made always-present
(defaulting to `{}`) at all three context-assembly sites so the sanctioned
`'api' in ciu.instances` idiom survives. See the entry below for detail and
`nyxloom-trove/reports/ciu-P38-REPORT.md` for the real post-rebase gate
verdict (this package's own R1 changed-lines coverage judgment PASSES at
100%). CIU-76 and CIU-78 -- both flagged during this package's own gate
runs as pre-existing failures unrelated to this fix -- are independently
fixed on `main` above; ciu-P38 rebased past both rather than re-filing or
re-fixing them.

Previously, 2026-08-31 — **CIU-75 FILED, CIU-55 RETRIAGED.** CIU-75 backports
v8 proposal F2 (identity source becomes the overlay TOML only, `ciu.env`
demoted to a legacy write-only export) ahead of the full v8 cutover, to ship
as a deliberately breaking **ciu 7.6.0**. CIU-55 (per-lane gate invocation
timing) is retriaged to run-gate RG-27 — the operator's re-read is that
run-gate, not ciu, is the layer with direct invocation visibility in the
current pre-v8 architecture; CIU-55's entry stays as a pointer, not deleted.

Previously, 2026-08-30 — **CIU-71 collision corrected, CIU-74 assigned.**
The 2026-08-26 v8-design-session backfill (`docs/CIU-V8-TESTING-GATE-PROPOSAL.md`
rev 2.0) misfiled the "leaf-typo templates render empty" `StrictUndefined`
finding as CIU-71, colliding with the same-day dstdns-P147b build-context
finding (D-244) that had already claimed CIU-71 the same day. CIU-71 stays
the build-context entry; the StrictUndefined finding is renumbered **CIU-74**
(next free ID) here and at all seven references in
`docs/CIU-V8-TESTING-GATE-PROPOSAL.md`. No behavioral change, backlog data
hygiene only.

Previously, 2026-08-30 — **CIU-72, CIU-73 FILED** from the assay 3.1.0
design review (`assay/nyxloom-trove/reports/assay-3.1-js-adapter-design-review-2026-08-30.md`),
both against the v8 gate (SPEC-V8 S15.3/S16): CIU-72 — the LaneResult copies
only `judge_provenance` and never the verdict's `helpers[]` (the Go oracle's
identity), and `ciu check` cannot see that a `javascript`/`go` assay lane
needs Node/Go in its environment because it reads lane names only — consume
`assay lanes --json` (assay B044) and derive `request_base` from it; CIU-73 —
assay's snapshot carries committed objects only, so a JS lane's `node_modules`
must come from the environment (offline npm cache baked or mounted); the demo
has no language-bound assay lane to exercise it. Operator ruling 2026-08-30:
file both AND annotate SPEC-V8/the demo with pointers (done in the same pass).

Previously, 2026-08-26 — **CIU-67, CIU-68 FILED**: a genuinely fresh
`ciu clean && ciu up` (the standard documented dstdns bring-up) failed at
phase_2 with `stack:infra/vault:healthy` reporting `starting`. Root cause is
two compounding gaps: `deploy.health.timeout` is silently reused for both a
per-probe Docker HEALTHCHECK duration (correctly small) and the S7.7
inter-phase gate's overall wait budget (needs to be much larger, CIU-67);
and that gate isn't part of `ciu up`'s default action sequence, isn't
documented in `ciu up --help`, and the one-shot `stack:*` probe it exists to
protect has zero retry of its own (CIU-68). Neither is a repeat of CIU-45's
mistake — both reproduced live, both real gaps in shipped behavior, not
"the mechanism exists and dstdns didn't use it."

Previously, 2026-08-26 — **CIU-66 FILED**: `container_name()`/CIU-51's
proposed `qname()` fold in project+instance only, never the declaring
stack — two stacks naming a service identically (e.g. two `postgres`)
compute the same container name, a hard Docker collision, not just the
softer DNS-alias ambiguity CIU-51 tracks. Currently latent in dstdns (no
live duplicate today — authentik shares db-core's one `postgres` instance
via its own schema, confirmed by reading `infra/authentik/ciu.defaults.toml.j2:56`,
not a second `postgres` service), but structurally unguarded against the
next stack that reuses a common name. Operator-proposed fix
(`<project>-<instance_id>-<stack-name>-<service>[-<replica>]`) is the
right shape and folds directly into CIU-51's `qname()` signature rather
than shipping alongside it.

Previously, 2026-08-26 — **CIU-48/CIU-49: dstdns's own follow-up FIXED**
(dstdns@41898e90). Both entries' ciu-side scaffold/docs work (ciu-P30) had
already shipped PARTIAL; dstdns has now propagated the qualified
`hostname:`/`internal_host` pattern into all 31 of its own compose
templates and its shared `ciu.global.defaults.toml.j2`, re-verified
2026-08-26 (zero bare sites remain outside dead/archived code). Table rows
and detail sections updated in place rather than re-filed, since the
underlying finding is unchanged — only which side of the ciu/consumer
boundary still owes work.

Previously, 2026-08-26 — **CIU-63, CIU-64, CIU-65 FILED** (dstdns/vbpub joint
ciu v8 design session): all three found while dstdns tried to close the
provisioning-graph gaps that session's own `V8-REALIZATION-GRAPH.md` had
identified (an undeclared vault-liveness dependency on 4 stacks, an
unexpressed schema-completion dependency on 3 stacks). Both turned out
already expressible via the shipped `stack:<name>:healthy|completed` ref
kind — live-verified against a real dstdns checkout's own containers — so
neither is a ciu bug in the CIU-45 sense (the mechanism exists, was
correctly used once dstdns knew to look for it). What CIU-63 catches
instead: `ciu check`'s static graph lint doesn't know `stack:*` refs are
resolved by live docker-inspect, not by a `provides` declaration, so using
one forces every referenced stack to redundantly self-declare
`provides = ["stack:X:..."]` — undocumented anywhere, discoverable only by
hitting the refusal and reading `lint_graph`'s source. CIU-64/CIU-65 are
smaller, adjacent workflow gaps found along the way: `ciu check` (and a
hook's `validate_config()`) never runs automatically before `ciu up`, and
`validate_config`'s `list[str]` return has no severity, despite
`warn_policy.py`'s `WARN`/`ERROR`/`NEVER` vocabulary already existing for
exactly this.

Previously, 2026-08-25 — **CIU-53 FIXED, CIU-54 FILED** (ciu-P32):
`dev.resolve_repo_root` checked ambient `REPO_ROOT` before `--define-root` —
the reverse of SPEC S1.1's own documented order, i.e. the CODE was violating
its own documented contract. Live-reproduced: an operator standing inside a
real ciu-managed repo, no `--define-root`, got a DIFFERENT sibling checkout's
worktrees back, because that checkout's ambient `REPO_ROOT` (from its
sourced `ciu.env`) silently outranked deriving the root from where they were
actually standing — the CIU-41 masked-default hazard one level up, for the
resolver that decides WHICH repo `ciu dev`/`ciu worktree *` operate on.
**CIU-53 FIXED:** `--define-root` now always wins outright; otherwise CIU
derives by walking up from cwd, and a successful derivation that disagrees
with a pre-set `REPO_ROOT` now REFUSES (tagged `[S1.1]`, naming both paths
and three remedies) rather than silently preferring either value — this
resolver feeds destructive verbs (`worktree rm`, `branches -y`, `clean`), so
a masked default is worse than a hard stop here (unlike `env generate`'s
identity tuple, which warns-and-proceeds because a fresh file is about to be
written anyway). Only when the walk-up finds NOTHING does CIU fall back to
ambient `REPO_ROOT`, unchanged from today. **CIU-54 FILED, OPEN:** ~8 OTHER
`cli.py` call sites resolve `repo_root` via a bare
`os.environ.get("REPO_ROOT", Path.cwd())` with no `--define-root`
consideration and no walk-up at all — a different, larger resolution
strategy, named but explicitly not touched by ciu-P32.

Previously, 2026-08-25 — **CIU-52 FIXED** (ciu-P31): reference-service
addressing ships as SPEC S16.1a's OPTIONAL, alias-keyed
`[ciu.instance.shared_infra.ref_services.<alias>]` table plus
`--shared-infra-ref-services`, CIU-deriving the reference's qualified
container name from the REFERENCE's own rendered config and authenticating it
against live Docker before recording it as this instance's
`topology.services.<alias>.internal_host`. The filing's own illustrative TOML
misread the shipped schema (it paired `services` with `ref_projects`); the
corrected understanding — `services` are the JOINER's own containers,
`ref_projects` the REFERENCE's projects, two independent lists about two
different instances — is recorded in that entry's disposition below, and S12's
`services[*].aliases` reservation is withdrawn rather than implemented. The
filed text is brought over from `main` in the same commit (this branch's copy
of this file predates `vbpub@4ccf7d4d`), so the row reads "filed, then fixed"
rather than ever appearing OPEN.

Previously, 2026-08-25 — **CIU-48 and CIU-49 PARTIAL** (ciu-P30): both
filed 2026-08-25 (`vbpub@4ccf7d4d`, from dstdns's §3.6 cockpit
multi-instance DNS-alias-ambiguity investigation,
`dstdns/nyxloom-trove/GUIDE.md` §3.6) alongside three siblings (CIU-50/51
still tracked on `main` only, not duplicated in this branch's copy of this
file pending merge; CIU-52 brought over and closed by ciu-P31 above).
Investigation found ciu ships NO default for either Compose
`hostname:` or `topology.services.*.internal_host` — both are entirely
consumer-declared (grep confirms `src/`'s templates set neither); the
filing's "31 dstdns templates + one hand-maintained override" fix is
therefore out of reach from a ciu-only session. **Shipped here:** a
correctly-qualified `hostname:` line in ciu's own `ciu init` scaffold
(`stack.compose.yml.j2`), plus DESIGN-GUIDE/CONFIG.md/CONSUMERS.md guidance
naming the hazard and prescribing the qualified pattern. **Not shipped, and
not shippable from this repo alone:** propagating the corrected pattern into
dstdns's own already-authored templates and its hand-maintained override —
that remains dstdns's own follow-up. Hence PARTIAL, not FIXED. See the CIU-48
and CIU-49 detail sections below for the full filed text plus this
disposition.

Previously, 2026-08-22 — backlog wave on
`feat/ciu-backlog-wave-39-42-46-47` shipped four fixes and CIU-25's git
half: **CIU-39 FIXED** (declared vendor baseline `[deploy.provenance]
vendor_images`; provenance documents now `schema_version: 2`; `verified-
match` reachable live — unblocks assay B004), **CIU-42 FIXED** (mechanism
reading adopted: `produced_by` ASK_VAULT producer declaration, S13.6
upfront refusal), **CIU-46 FIXED** (cutover per operator decision: the
basename fallback is WITHDRAWN — config-less shipped/reset deployments use
the workspace-identity compose project `{REPO_NAME}-{INSTANCE_ID}-{stack}`;
BREAKING, one-time migration in CONSUMERS §11), and **CIU-47 FIXED**
(S2.7 refined precedence extended to `PUBLIC_FQDN`). **CIU-25 partially
addressed**: `ciu worktree branches` ships the grounded GIT-layer survey +
prune (S16.8, `worktree.branches.v1`); the Docker-resource detector/reap
contract remains OPEN below. An independent three-reviewer adversarial pass
over the whole wave then landed hardening commits (`2a6176d4`..`040df76e`):
the branch-prune half-prune blocker, produced_by deployed-stack semantics,
Docker-canonical provenance comparison, shipped-mode root resolution, and
docs/example repairs — all gate-green at `040df76e`.

Previously, 2026-08-22 — released as **ciu-v6.4.0** (tag + wheel published;
CHANGES §[6.4.0]): CIU-41, CIU-43, CIU-44 and their adversarial-review repairs
went through the tester-unified gate and shipped. Backlog reconciliation the
same day: status table re-sorted by ID; two dstdns-P112 second-reproduction
notes that had been appended to CIU-23's WITHDRAWAL section relocated to
CIU-41 and CIU-42 where they belong; the dangling empty `### CIU-39 detail`
heading filled with a real stub. **CIU-46 FILED, OPEN** — the S6.4a residual:
a shipped stack under the legacy compose-project fallback escapes clean's
network/volume enumeration. **CIU-47 FILED, OPEN** — `PUBLIC_FQDN` ambient
adoption during generate, the masked-default family CIU-41 fixed for the
identity tuple.

Previously, 2026-08-21 — **CIU-41, CIU-43, CIU-44 marked FIXED** by the
consumer-wave branch `feat/ciu-consumer-wave-41-43-44`: S2.7 refined
precedence extended to the derived identity tuple during generate (CIU-41),
S6.4a identity-scoped network removal + compose-label volume pass with the
instance-vs-main split (CIU-43), and S3.12 deployment-selection facts in the
render/hook context (CIU-44). **CIU-45 WITHDRAWN**, same day it was filed.
dstdns's
own fresh adversarial code review of the P120 package that filed it reproduced
the actual failure from source and found it was a misdiagnosis: a missing
`provides` array in one dstdns stack (`infra/vault`), not a ciu limitation —
the `post_compose`-hook-as-provider pattern this issue claimed was impossible
already ships in the same repo (`infra/consul-server`). Full disposition below
under `## CIU-45`; `dstdns/nyxloom-trove/decisions.md` D-170.

Previously, 2026-08-21 — **CIU-45 FILED, OPEN** from dstdns P120's O7 live
attempt (`dstdns/nyxloom-trove/reports/dstdns-P120-REPORT.md` §O7): `requires`
provisions rather than verifies, so a path a non-ciu hook provisions
out-of-band can never pass the static provisioning-graph lint. This is the
second dstdns config-wave upstream ask filed the same day as CIU-44 (both from
the same carve-review round, D-162).

Previously, 2026-08-20 — **CIU-41..43 FILED, OPEN** from dstdns P111's
Mode-B live pass (findings F2/F3/F4 in
`dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §9): `ciu env generate`
silently inherits an ambient `DOCKER_NETWORK_INTERNAL` (CIU-41), no way to
express cross-profile `ASK_VAULT` producer dependencies (CIU-42), and
`ciu clean` leaves instance-scoped networks behind while reporting
`clean complete` (CIU-43).

Previously updated 2026-08-19 — merged from main: dstdns's five configuration/
landscape capability asks **CIU-34..38 FILED, OPEN** (renumbered from
CIU-29..33 on main because this branch had already allocated CIU-29; recorded
vbpub@b4d7c749), four of them carved as `ciu-P08..P11`
(`nyxloom-trove/handoffs/`, wave brief
`nyxloom-trove/ciu-config-wave-BRIEF-2026-08-19.md`); and assay's provenance
defect renumbered **CIU-28 → CIU-39** at this merge (this branch had also
independently allocated CIU-28 for worktree identity — assay-side references
updated the same day). Same day: **CIU-36 marked FIXED** by ciu-P08
(landscape_id validation + docs; S3.11) and **CIU-37 marked FIXED** by ciu-P09
(schema-validated configfile render; S5.7).

Last reconciled: 2026-08-17, automation-safe worktree lifecycle milestone.

## Current status

| ID | Summary | Severity | Status |
|---|---|---:|---|
| CIU-23 | PostgreSQL-specific worktree data-isolation provider was grounded in a false consumer premise | Medium | WITHDRAWN |
| CIU-25 | No grounded stale worktree/stack detector and explicit reap transaction | Low | FIXED 2026-08-25 across THREE packages that are jointly the evidence — **ciu-P26** (the ownership lease, record schema v2, + `ciu.instance`/`ciu.repo-root` labels, S16.9) supplies the ownership signal; **ciu-P27** (`ciu worktree reap`, S16.10 + `worktree.reap.v1`/`worktree.lease.v1`) is the detector and the reap transaction; the git half shipped earlier as `ciu worktree branches` (S16.8 + `worktree.branches.v1`, 2026-08-22, **HOTFIXED by ciu-P28**). Reading ciu-P27 alone is not enough to reconstruct the substrate it depends on — see detail |
| CIU-26 | No live proof for CIU-23's PostgreSQL provider | Low | OBSOLETE |
| CIU-28 | Automation-safe worktree identity, allocation, adoption, and resume | Medium | FIXED — shipped `71f5ec79` (P04-P06), Assay-qualified in P07 (2026-08-20) |
| CIU-29 | Structured worktree control, capability discovery, exact up, and exact execution | Medium | FIXED — **P04–P06 SHIPPED** (S16.5–S16.7, checkpoint-B review 2026-08-19) + P07 qualification (2026-08-20), closes this row |
| CIU-34 | No `layout` object naming a host→bundles plan (dstdns config/landscape ask) | Medium | FIXED — `[deploy.layouts.<name>]` + `ciu up --layout` / `ciu layouts` (ciu-P10, S7.5c); **HOTFIXED 2026-08-25 (ciu-P29)** — the mutual-exclusion guard was abbreviation-blind and could silently deploy the wrong profile to every host, see the CIU-34 detail below |
| CIU-35 | No host-scoped home for pre-Vault local secrets (SSH bootstrap key, Tailscale authkey) | Medium | FIXED — `[deploy.hosts.<h>.secrets]` + `ciu host-secrets` (ciu-P11, S14.3a) |
| CIU-36 | No `landscape_id` identity dimension | Low | FIXED — S3.11 validation + docs (ciu-P08, 2026-08-19) |
| CIU-37 | Rendered app config not validatable against an app-provided JSON schema | Medium | FIXED — S5.7 schema-validated render (ciu-P09, 2026-08-19) |
| CIU-38 | No per-service Vault AppRole provisioning/delivery | Medium | OPEN — consumer-side-first (dstdns D-106); stays as the upstreaming ask |
| CIU-39 | `provenance` adjudicates vendor images ciu never built → `verified-match` unreachable live (was CIU-28 on main; blocks assay B004) | High | FIXED — `[deploy.provenance] vendor_images` declared baseline; running reference equal to a declaration is `vendor-pinned`, same-name/different-reference is vendor drift (`mismatch`); verdicts at `schema_version: 2`; digest pinning deliberately not built (S17.5, DESIGN-GUIDE; 2026-08-22). Unblocks assay B004's verified half |
| CIU-40 | Gate-layering refactor (estate D-110 + D-111): **`run-gate.py`** built as a vbpub mini-project (argparse, usage() with lane list + in-file revision, own tests for the docker/cgroup/pin-verify/clean-tree mechanics), reading a per-project **`run-gate.toml`** it alone parses (orchestration only; assay lanes reference `assay.toml` by name — judgment stays there); vbpub projects symlink it, external repos copy (revision = drift detector); nyxloom.toml [gates] becomes a thin argv pointer; overarching+project AGENTS.md name it the canonical entry IN the same carve; DE-VENDOR `tools/assay/*.pyz` once assay is baked into tester-unified from in-repo source (keep only the version pin) | Medium | FIXED — estate-wide adoption landed on vbpub main (`4c6eb2b6`, 2026-08-22); ciu's lane is now `./run-gate.py ciu`; de-vendor stays pending the assay image-bake |
| CIU-41 | `ciu env generate` silently inherits an ambient `DOCKER_NETWORK_INTERNAL`, so a fresh-worktree generate joins the MAIN stack's network (masked default; inconsistent with the S2.7 handling of `PHYSICAL_REPO_ROOT` in the same run) | Medium | FIXED — S2.7 refined precedence extended to the derived identity tuple (`REPO_NAME`/`INSTANCE_ID`/`DOCKER_NETWORK_INTERNAL`): ambient adopted only when consistent; mismatch → derived value + stderr warning naming the S16.1 `--shared-infra` remedy; post-generate bootstrap steps parse the just-written file by exact path (2026-08-21). Follow-up candidate (same contamination family, out of filed scope): PUBLIC_FQDN ambient adoption at generate time
| CIU-42 | No way to express that a stack's `ASK_VAULT` path is produced by another profile's provisioning — a partial profile selection (`core,db`) fails at the consuming stack with only the path name, not the missing producer | Low | FIXED — mechanism reading adopted: ASK_VAULT-only inline key `produced_by = "<profile>"`; a partial selection excluding the producer refuses upfront naming every unmet tuple (stack, secret, path, producer, selection, both remedies); unknown profile names are configuration errors; undeclared secrets keep today's behavior (S13.6, 2026-08-22) |
| CIU-43 | `ciu clean` reports `clean complete` while leaving instance-scoped networks behind (workspace network + compose `*_default`); by-design per `action_clean`'s docstring, a leak for ephemeral Mode-B instances | Medium | FIXED — S6.4a: clean removes the identity network (read from this workspace's own ciu.env) + each selected stack's `<compose-project>_default`, disconnecting lingering endpoints first (unremovable endpoint named, clean fails); instance-vs-main split (S16 record ⇒ unconditional, main keeps its workspace network but names the keep); post-clean invariant extended to networks from Docker STATE; volume pass gains an exact compose-label enumeration catching bare-project-prefix volumes (the 6.3.0 second reproduction's `<project>-vault-*` leak). Controlled-wrong oracle: no-op network removal fails the invariant (2026-08-21). Known residual: shipped stacks run under run_shipped's legacy-project fallback when project/env tags are absent, which escapes the compose-label enumeration — needs the same gate or label pass if a shipped stack ever hits it (RESOLVED by CIU-46's cutover, 2026-08-22)
| CIU-44 | Templates cannot see the SELECTED profile/stack set at render time: `CIU_SERVICES_PROFILE` is unset on the `--profile` argv path (`cli.py:1005-1015`; `workspace_env.py:875-877` leaves it commented out), so a feature flag like reverse-proxy's `enable_pwmcp_mcp` cannot be derived from "is infra/pwmcp deployed" and any render-time precondition is unreachable or always-fails. Ask: expose the resolved deployed-stack set (or profile list) to the Jinja context so a template can fail loudly when it references an undeployed upstream (§4.2a) | Medium | FIXED — S3.12: `ciu.selected_profiles` + `ciu.deployed_stacks` in every deployment render's Jinja context (up/dev/render-toml/preflight/check/graph), computed once per invocation and threaded unchanged to hooks (`ctx.selected_profiles`/`ctx.deployed_stacks` + `ctx.instance_id`/`ctx.network` from the workspace's own ciu.env); omitted elsewhere so references fail loudly; nothing persisted into ciu.env or exported to compose env (2026-08-21) |
| CIU-45 | ~~`requires` PROVISIONS rather than VERIFIES~~ — **misdiagnosis, see disposition below**. The lint rule is a plain `requires`/`provides` completeness check; a `post_compose` hook registering itself as a provider already ships (`infra/consul-server/ciu.defaults.toml.j2:9-17`). The actual dstdns failure was a missing `provides` array in one unrelated stack, fixed declaratively in-repo | — | **WITHDRAWN 2026-08-21** — see `## CIU-45` below for the full disposition; `dstdns/nyxloom-trove/decisions.md` D-170 |
| CIU-46 | Shipped stacks run under the legacy directory-derived compose project when `deploy.project_name`/`environment_tag` are absent (`engine.py run_shipped`), and clean's S6.4a enumeration then sees no projects at all — legacy-project networks/volumes survive a reported-clean teardown | Low | FIXED — **cutover, BREAKING**: the basename fallback is WITHDRAWN; config-less shipped/reset deployments derive the workspace-identity project `{REPO_NAME}-{INSTANCE_ID}-{stack}` from THIS checkout's ciu.env (`engine.identity_compose_project_name`, exact-path parsed), and clean enumerates the SAME names via the compose-label passes; missing/identity-less ciu.env refuses instead of silently skipping; no `-p`-less compose invocation remains anywhere in ciu; the S16.1 cannot-derive join refusal fell out as unreachable and is withdrawn. One-time migration for pre-existing deployments: CONSUMERS §11 (S6.4a item 7 + S8.7, 2026-08-22). Follow-up candidates named by the adversarial review: (a) non-round-tripping stack dirnames refuse (Vault/vault collision class closed); (b) two DIFFERENT dirs with the SAME normalized basename still collide in config-less mode — documented limit, tagged naming is the escape hatch; a stack-path label stamp at up would close it |
| CIU-47 | `ciu env generate` adopts an ambient `PUBLIC_FQDN` with no consistency check (`workspace_env.py` bare reads) — the same masked-default family CIU-41 fixed for the identity tuple; a main checkout's sourced `ciu.env` leaks its FQDN into a fresh worktree's generated file | Low | FIXED — S2.7 refined precedence extended to PUBLIC_FQDN: derived from THIS workspace's own inputs first (config entry → reverse DNS of the detected IP); ambient adopted only when equal or when detection yields no sourced value (offline host keeps the operator override silently); on mismatch the derived value is written and a warning names the ignored one; PUBLIC_FQDN joins GENERATED_IDENTITY_KEYS so post-generate steps act on the written record. PUBLIC_IP/PUBLIC_TLS_* stay plain pre-set-wins (out of scope). Controlled wrong: restoring the bare fallback fails oracle 1 (2026-08-22) |
| CIU-48 | Compose's `hostname:` field independently registers a bare, network-resolvable DNS alias — a second source of the §3.6 cockpit multi-instance ambiguity, separate from the automatic service-key alias (CIU-51) | High | PARTIAL (ciu's own product surface) / dstdns's operator pain FIXED — ciu-P30 shipped a correctly-qualified `hostname:` default in ciu's own `ciu init` scaffold + DESIGN-GUIDE/CONFIG.md/CONSUMERS.md guidance; dstdns propagated the pattern into all 31 of its own templates (dstdns@41898e90, 2026-08-25, re-verified 2026-08-26) (see detail) |
| CIU-49 | App-config `topology.services.*.internal_host`-style Jinja defaults render the bare service name instead of the already-computed qualified `{project}-{instance_id}-{service}` form, forcing consumers to hand-maintain per-worktree overrides (dstdns's `dstdns-mstest` template) | High | PARTIAL (ciu's own product surface) / dstdns's operator pain FIXED — ciu-P30 shipped CONFIG.md's `[topology.services.<name>]` section a SHOULD-level qualified-form prescription + CONSUMERS.md worked example; ciu ships no `internal_host` default of its own to change (S4.16/S7.4 is entirely consumer-declared), so dstdns fixed its own defaults template directly (dstdns@41898e90, 2026-08-25, re-verified 2026-08-26) (see detail) |
| CIU-52 | Implement S12's reserved `shared_infra.services[*].aliases` — after joining a reference instance's network, the joining instance has no CIU-declared name to call the reference's shared service by | High | FIXED — ciu-P31 shipped SPEC S16.1a: a new OPTIONAL alias-keyed `[ciu.instance.shared_infra.ref_services.<alias>]` table + `--shared-infra-ref-services ALIAS[,ALIAS=REF_SERVICE]`, deriving the reference's qualified container name from the REFERENCE's OWN rendered config (read-only, environ-isolated), authenticating it against live Docker before writing this instance's `[topology.services.<alias>]` block, and re-verifying before any join-time connect. Shipped shape deliberately differs from the filing: `services` (the JOINER's own containers) and `ref_projects` (the REFERENCE's projects) are NOT paired, so `services[*].aliases` could only ever have addressed the joiner's own copy of a service — the S12 reservation is withdrawn (see detail) |
| CIU-53 | `dev.resolve_repo_root` (consumed by `ciu dev`/`ciu worktree *`) checked ambient `REPO_ROOT` before `--define-root` — the REVERSE of SPEC S1.1's own documented order, i.e. the code violated its own documented contract; live-reproduced: standing inside a real ciu-managed repo with no `--define-root`, a sibling checkout's ambient `REPO_ROOT` silently won over deriving from cwd (CIU-41 masked-default hazard, one level up, for the resolver that picks WHICH repo destructive verbs operate on) | High | FIXED — ciu-P32: `--define-root` now always wins outright (no consistency check); otherwise CIU derives by walking up from cwd, and a successful derivation that disagrees with a pre-set `REPO_ROOT` REFUSES (`[S1.1]`-tagged, naming both paths + three remedies) instead of silently preferring either value — this resolver feeds destructive verbs (`worktree rm`, `branches -y`, `clean`), so a masked default is worse than a hard stop, unlike `env generate`'s warn-and-proceed identity tuple (a fresh file is about to be written anyway). Walk-up-finds-nothing still falls back to ambient `REPO_ROOT`, unchanged. All ~8 `cli.py` call sites verified to propagate the refusal as a clean `[ERROR] ...` + non-zero exit. SPEC.md/CONFIG.md/CIU.md/DESIGN-GUIDE.md corrected; `--help` names the hazard (see detail) |
| CIU-54 | 8 `cli.py` call sites (the `--host` remote branches of `render`/`up`/`down`/`health`, `up --layout`, `layouts`, `host-secrets`, `ssh`) resolve `repo_root` via a bare `os.environ.get("REPO_ROOT", Path.cwd())`, with NO `--define-root` consideration and NO walk-up at all — a separate, larger resolution strategy from `dev.resolve_repo_root`, closer to `deploy.py`'s own resolver, not closed by CIU-53 | Medium | FIXED — ciu-P45: all 8 sites now route through a new `_resolve_repo_root_deploy()`/`deploy.resolve_repo_root` (candidate (a); none of the 8 accepted `--define-root` at all, confirming the design pass's own precondition), the SAME resolver each verb's own local/profile branch already uses, via a new `_extract_define_root()` that consumes the flag before any per-site parser or `_parse_layout_argv`'s forbidden-flag guard sees it (`allow_abbrev=False`, so ciu-P29's pinned `--d`/`--r` abbreviation tests are unaffected). **Breaking**: no cwd fallback — ambient `REPO_ROOT` or `--define-root` is now REQUIRED; nil blast radius inside this monorepo (no shipped script relies on the old cwd fallback), `docs/CONSUMERS.md` #19 carries the migration note, `docs/SPEC.md` S1.1a documents the two-resolver split. Full scope landed, no subset deferral — see detail |
| CIU-55 | No per-lane gate invocation timing is measured or persisted anywhere — a controller deciding whether to run full R1+R2 rigor before merging, or defer R2 and merge provisionally, has no data and must guess | Medium | RETRIAGED 2026-08-31 -> run-gate RG-27 (`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`); this entry kept as a pointer, see detail |
| CIU-56 | The 100% gate's coverage of `src/ciu/hook_templates/post_compose_db.py` is SCHEDULING LUCK, not measurement: a hook module loaded the way CIU actually loads hooks (`hooks_runner._load_hook_module`, `spec_from_file_location` under a synthetic non-`ciu` module name) is not measured by `--cov=ciu` at all unless the SAME file was also imported normally in that worker process. Under `-n auto --dist load`, xdist splits `test_ciu_scaffold_hooks.py` across workers, so the shipped template's `run()` body is sometimes measured and sometimes not — the gate flips to 99.85% on any change to the suite's test COUNT, with zero source changes | High | FIXED — `run-ciu-tests.py` now runs `-n auto --dist loadfile`, keeping every test file's functions on one worker; verified 100.00% coverage across 5 consecutive runs with zero flips. The module-level-import half of the proposed fix was not additionally applied — `--dist loadfile` alone closed it (see detail) |
| CIU-57 | `tests/conftest.py`'s autouse ambient-env-scrub fixture (ciu-P13) never included `CIU_KSM`, despite CHANGES.md's own history recording multiple prior one-off `CIU_KSM=off` pins scattered across individual test fixtures to work around exactly this class of leak — local patches on individual flakes, never a fix of the shared fixture's actual coverage | Medium | FIXED — `CIU_KSM` added to `_AMBIENT_ENV_VARS`. Live-caught while investigating CIU-56: `test_absolute_governance_ksm_path_is_preserved_in_overlay` (`test_ciu_composefile_branch109.py`) intermittently failed `KeyError: 'volumes'` under `--dist loadfile` because `governance.resolve_ksm_optin` reads `CIU_KSM` fresh on every call and the test never pins it; a contaminated/leftover value silently changes which branch `composefile.generate_overlay` takes. No raw (non-monkeypatch) `os.environ["CIU_KSM"]` assignment or ambient shell value was found as the exact source — the fix closes the class regardless of the precise vector, matching the existing pattern for the other 6 scrubbed vars (see detail) |
| CIU-58 | Multiple tests build their fixture tree via `shutil.copytree` FROM the real, checked-in `test-repo/` directory (a SHARED, on-disk, non-per-test-isolated source) rather than from a synthetic/generated source — a concurrent xdist worker rendering into (or otherwise mutating) that same shared source directory races the `copytree` read, observed as a `shutil.copytree`/`os.scandir` failure over entries that include unexpected generated artifacts (`ciu.compose.yml`, `__pycache__`) alongside the template files | Medium | OPEN — found live while stress-testing the CIU-56/CIU-57 fixes (2026-08-25), not caused by either; not investigated further — a structurally different, likely broader test-fixture-isolation problem across the suite, out of scope for this wave. A future package should enumerate every `copytree`/direct-read use of `test-repo/` (or any other shared non-tmp_path source) and either isolate a pristine copy once per session (not per test) or generate the fixture tree synthetically per test. **Review-added facts (ciu-P26 reviewer):** 9 test files read `test-repo/`, 3 via `copytree` — CIU-56's `--dist loadfile` fix removes only SAME-file races, cross-file concurrent access remains possible, so it likely makes this rarer without closing it; the directory currently holds gitignored, accumulating residue (`applications/app-config/ciu.compose.yml` + two `__pycache__` dirs) that is NOT tracked fixture content, so the race surface drifts between a fresh clone/CI and a developer machine — a cheap partial mitigation (render into `tmp_path`, or clean the residue) shrinks the surface without the full audit |
| CIU-59 | `os.environ.get("DEVCONTAINER_NAME") or os.environ.get("HOSTNAME", "")` is duplicated FOUR times with no factored helper — three pre-existing in `workspace_env.py` (`:598`, `:742`, `:964`) and a fourth added by ciu-P26 (`worktree.py:395`) | Low | FIXED — ciu-P44. `workspace_env.detect_devcontainer_name()` factored in, right before its first call site (`_detect_host_mdt_tmp`); all four sites (`_detect_host_mdt_tmp`, `_connect_devcontainer_to_network`, `generate_ciu_env`'s `ciu env print` report row, `worktree._host_identity`) now call it. Verified the fourth site's line-number drift the row flagged ("verify yourself, this may have drifted") — actual lines at fix time were `:697`/`:834`/`:1445` and `worktree.py:395`, not the row's `:598`/`:742`/`:964`. One real semantic difference found while verifying "matches semantically" (the row's own caveat): the THIRD site (`generate_ciu_env`) used a nested `os.environ.get("DEVCONTAINER_NAME", os.environ.get("HOSTNAME", ""))`, which returns an explicitly-empty `DEVCONTAINER_NAME` verbatim (key present, so the nested default never applies) rather than falling through to `HOSTNAME` the way the other three sites' `or`-form already did — the helper standardizes on the majority `or` shape, so an explicitly-empty `DEVCONTAINER_NAME` now falls through to `HOSTNAME` at that site too (documented in the helper's own docstring as a deliberate fix, not an incidental behavior change). `worktree._host_identity`'s own `or "unknown-host"` final fallback (not shared by the other three) is preserved as that call site's own addition. Tests: four new direct unit tests in `test_ciu_workspace_env_branch106.py` (`detect_devcontainer_name` prefers the container name / falls back to `HOSTNAME` / treats an explicit empty value as absent — the semantic-fix regression proof / empty when neither is set); the three pre-existing `_host_identity` tests in `test_ciu_worktree_lease.py` continue to cover the refactored call site's two main branches plus the never-empty-holder fallback unchanged |
| CIU-60 | Jinja TEMPLATE rendering's `env` context is still raw ambient `os.environ` (S3.2) — hooks got the safe treatment in S9.3, templates never did, so a shell that once sourced a sibling checkout's `ciu.env` renders THAT checkout's `PHYSICAL_REPO_ROOT` into a bind mount, silently. The level below CIU-53 (which repo a verb acts on): facts ABOUT the already-discovered workspace | High | FIXED — ciu-P33: `ciu env generate` now upserts a CIU-owned `[ciu.instance.generated]` table (six snake_case keys, from the SAME in-memory values it writes to `ciu.env`) into the gitignored per-checkout overlay `ciu.global.worktree.toml.j2`, so templates read them as `{{ ciu.instance.generated.* }}` through the merge `render_global_chain` ALREADY performs — no bespoke Jinja global (that proposal was rejected by the operator as a new invisible-variable hazard), a real `cat`-able file behind every value, and NOT gated on an S16 record so the primary checkout is covered too. Text-level surgical block replace, never a `tomllib`+`tomli_w` full-file round-trip: operator comments/formatting/unrelated tables survive byte for byte. Shipped with the same conversation's two QOL asks: `ciu env print` (`eval "$(ciu env print)"` — named `print`, never `apply`/`source`, because a subprocess cannot mutate its parent shell) and `ciu clean --vanilla` (additive; plain `clean` still leaves `ciu.global.toml`/`ciu.env`/the overlay untouched) — see detail |
| CIU-61 | `ciu init`'s `_GITIGNORE_ENTRIES` (`scaffold.py`) writes only 4 entries — `ciu.env`, `ciu.global.toml`, `**/.ciu/`, `**/ciu.compose.yml` — and omits `ciu.global.worktree.toml.j2`, which CIU's own published `.gitignored.ciu` sample rules DO list. Harmless until CIU-60; now that `ciu env generate` upserts `[ciu.instance.generated]` into that file on every run, a freshly scaffolded consumer repo gains an untracked, machine-specific file (host paths, instance id) that a developer can commit by accident. `ciu.worktree-instance.json` and `**/ciu.toml`/`**/ciu.toml.j2` are missing from the same list for the same reason | Medium | FIXED — ciu-P44. Read both files fully before deciding, per the row's own instruction, and found `.gitignored.ciu` ITSELF had a real bug the row's premise assumed away: it wrongly listed `ciu.global.toml.j2` and `**/ciu.toml.j2` as gitignored, contradicting SPEC S3.1a/CIU-8 (both are COMMITTED, hand-authored sparse override templates, never auto-created — the exact class `ciu.global.defaults.toml.j2`'s override sits beside) and contradicting ciu's OWN `.gitignore` in this repo, which already gets this right with an explicit callout comment. Blindly deriving `_GITIGNORE_ENTRIES` from the old `.gitignored.ciu` would therefore have shipped a WORSE bug (a scaffolded consumer's `ciu init` gitignoring operator-authored config it should track) than the one being fixed. Mechanism: `.gitignored.ciu` corrected first (removed the two wrongly-ignored template patterns, added an explanatory callout mirroring `.gitignore`'s own), THEN `_GITIGNORE_ENTRIES` reconciled against the corrected file — three real entries added (`ciu.worktree-instance.json`, `ciu.global.worktree.toml.j2`, `**/ciu.toml`), `ciu.global.toml.j2`/`**/ciu.toml.j2` deliberately NOT added (pinned by name in a dedicated test). `_GITIGNORE_ENTRIES` stays the hand-maintained runtime source (it ships inside the wheel and must not depend on a repo-root file existing post-install); `.gitignored.ciu` stays the documentation source; `tests/test_init_scaffolding.py::test_gitignore_entries_match_gitignored_ciu_sample` parses `.gitignored.ciu`'s real patterns and asserts the two sets are IDENTICAL (both directions — also catches a pattern added to `_GITIGNORE_ENTRIES` first), so this is a TESTED reconciliation, not a rederivation, closing the drift mechanically without a runtime coupling. `test_gitignore_entries_omit_the_committed_override_templates` pins the exclusion by name. `test_gitignore_fully_satisfied_appends_nothing`'s pre-seeded fixture updated to the 7-entry list. SPEC S19 updated |

| CIU-62 | A `ciu.env`-reading `except` clause not covering `UnicodeDecodeError` (a `ValueError` subclass, NOT an `OSError` subclass — `WorkspaceEnvError` is a *different* `ValueError` subclass, so neither name catches it) is a CLASS across this codebase, not one site: 6 sites, 4 distinct clause shapes, 3 gap profiles, across two subsystems (`worktree.py`, `deploy.py`) | Medium | FIXED — ciu-P41 (see the **FIXED — ciu-P41** paragraph at the end of this cell; the filed site-by-site analysis is kept above it because it is the map the fix was built from). Originally found by ciu-P33's second review round while applying `(OSError, WorkspaceEnvError)` to `cli._env_print` and discovering it left the non-UTF-8 case open (`_env_print` closed it by naming `UnicodeDecodeError` explicitly). The reviewer's own class-wide grep, before recommending any fix, found: **`worktree.py:1144`, `:1239`, `:3866`** — bare `except OSError` — **malformed entry AND non-UTF-8 both escape, strictly worse than the originally-filed site since the COMMON case (a malformed entry) fails, not just the exotic byte**; **`worktree.py:4271`** (the originally-filed site, S16.3's `_resolve_budget_candidates`) **+ `deploy.py:1886`** — `(OSError, WorkspaceEnvError)` / `(WorkspaceEnvError, OSError)` — miss non-UTF-8 only; **`deploy.py:2842`** — bare `except WorkspaceEnvError` — misses OSError AND non-UTF-8, **and swallows the exception to return `""`, so widening it changes what "no network" means — a design decision, not a token**; **`worktree.py:2888`, `:3252`** — `(OSError, ValueError)` — already correct, possibly by accident (a bare `ValueError` also catches `UnicodeDecodeError` since it's a subclass, but is broader than necessary and could mask an unrelated bug). Severity raised Low→Medium given three sites fail on the COMMON malformed-entry case, not just the rare non-UTF-8 byte. Fixing this properly needs a test per site under this repo's 100%-coverage gate — a small package, not a one-line patch. **FIXED — ciu-P41**: line numbers had all moved, so every site was re-derived by grepping the clause shapes. SEVEN narrow sites (not the six filed) now name `(OSError, UnicodeDecodeError, WorkspaceEnvError)`, matching `cli.py:805`'s shipped ordering: `worktree.py`'s `_preflight_shared_infra_for_add` + `connect_shared_infra_after_up` (S16.1) + `_clean_in` (S16) + `_resolve_budget_candidates` (S16.3), `deploy.py`'s `_workspace_identity`, and — found by the P41 reviewer, NOT in this entry's own list — `engine.py`'s S3.12 hook-identity read, the REAL-RUN twin of `_workspace_identity` building the same two `HookContext` fields, where a non-UTF-8 `ciu.env` crashed `ciu up` at STEP 12 while `ciu check` degraded cleanly on the same file; both now degrade to `{}` identically, since a preflight seeing an identity its own `run()` will not is the divergence S3.12/CIU-44 exists to prevent. `worktree.py:2888`/`:3252`'s `(OSError, ValueError)` left alone as this entry advises; `engine.py:1338`'s `except WorkspaceEnvError: raise` is NOT this class of defect (it re-raises, swallowing nothing) and is untouched. `deploy.py`'s `_workspace_identity_network` was the flagged DESIGN decision and was taken deliberately, not widened: an ABSENT `ciu.env` still means "no identity network" (legitimate — `ciu env generate` was never run) and stays green, but a PRESENT-but-unreadable one now raises and `action_clean` reports `workspace identity network unresolvable (S6.4a)` and fails the clean. Folding indeterminacy into `""` dropped the network from the removal pass AND from the S6.4a clause-5 survivor check in one move, so an instance clean could announce the zero-objects invariant satisfied over its own surviving network — the exact leak S6.4a was written to end, and the opposite of what its sibling volume/network/container enumerations already do (review B3: "never fold 'could not enumerate' into 'nothing to remove'"). SPEC S6.4a clause 1 rewritten. 13 tests, each driving a REAL malformed / non-UTF-8 `ciu.env` rather than a monkeypatched raiser, so each proves the exception type the shipped reader actually produces; the legitimate absent-file state is constructed as its own test so this cannot become a superset refusal. `test_identity_env_parse_failure_reads_as_no_network` asserted the pre-fix contract verbatim and was replaced by three tests encoding the new one. Controlled wrong implementation verified by hand on the `engine.py` site: restoring `(WorkspaceEnvError, OSError)` fails the non-UTF-8 case and passes the malformed-entry case — exactly the gap profile this entry attributes to that clause shape. Review addendum: the two HookContext identity sites (`deploy._workspace_identity`, `engine`'s STEP-12 read) KEEP their `{}` degradation — that symmetry is what stops a preflight seeing an identity its own `run()` will not — but they no longer do it SILENTLY: both now warn, naming the unreadable file and the `ciu env generate` repair. The deploy-side warning goes to STDERR, not through `warn()`, because `warn()` writes to stdout and `ciu check --json` (S13.4a) may put only the JSON document there; `engine`'s stays on stdout, its own idiom, having no machine-readable stdout channel to protect. **The stricter variant — both sites raise, or `HookContext` grows a third `identity_unreadable` state — is filed as CIU-80, which records that the two sites must change as a PAIR** |
| CIU-63 | `lint_graph` (`provisioning.py:134-156`) treats every `requires` entry as a plain string that must appear in SOME stack's `provides` array, regardless of ref kind — including `stack:<name>:healthy\|completed`, which is actually resolved by an entirely different mechanism (`_probe_stack`, live `docker inspect`, called only during a real `ciu up`/`--live` check) and never by reading any `provides` declaration. Live-reproduced against a real dstdns checkout (2026-08-26, ciu v8 design session): `_probe_stack` correctly returns `satisfied=True` for `stack:infra/vault:healthy` and `stack:infra/db-init:completed` against the checkout's own live containers, yet `ciu check` (both with and without `--live`) refuses BOTH with `[ERROR] ... requires 'stack:X:...' but nobody provides it` until the referenced stack redundantly self-declares `provides = ["stack:X:..."]` — a declaration `_probe_stack` never reads, added purely to satisfy the unrelated static string-matcher. No comment, SPEC section, or `--help` text anywhere documents that a `stack:*` ref needs this self-declaration; a consumer discovers it only by hitting the refusal and reading `lint_graph`'s source, exactly the kind of gap CIU-45's own "grep the mechanism before trusting the claim" lesson was about — except this direction: the mechanism to express the dependency already exists and works, but the tool's own static validator doesn't know it's the one that resolves this ref kind | Medium | FIXED — ciu-P39: shape (b) implemented, not (a). `lint_graph`'s requires-satisfied pass now recognizes a `stack:<path>:healthy|completed` ref via the same `_STACK_RE` match and `_resolve_declared_stack_path` resolution the cycle-detection pass already used, and treats it as satisfied whenever it resolves to a real declared stack -- no `provides` self-declaration required or read. Every other ref kind, and a `stack:*` ref that does not resolve to a real declared stack, keep the exact prior behavior. (a)'s minimal doc-only fix was not taken: (b) removes the redundancy at its source instead of documenting around it, since nothing about a `stack:*` ref's truth ever depended on any `provides` array to begin with. `docs/SPEC.md` S13.2/S13.3 updated; `ciu check --help`/`ciu --help` were checked and carry no ref-kind list to correct. 6 new tests in `test_ciu_provisioning.py` (positive: real stack, no self-declared provides, in `:healthy`/`:completed`/bare-selector form; negative: a non-resolving selector, an ambiguous selector, and a non-stack ref, all still error) |
| CIU-64 | `ciu check` — including a hook's `validate_config()` (S9.5) — runs only on the explicit `ciu check` verb, never as part of `ciu up`'s own preflight, despite being documented as genuinely side-effect-free (`run()` is never invoked by check). An operator who deploys directly via `ciu up` without a separate `ciu check` first gets no benefit from either the graph lint or any hook's static validation — the exact "relies on someone remembering" shape this tool's own `--check-env`/`validate_config` machinery exists to eliminate elsewhere | Low | FIXED — ciu-P41: implemented as proposed, and specified as new **SPEC S13.4c**. `deploy.check_preflight()` runs `action_check(..., live=False)` and raises `ValueError` on a non-zero return, which `engine._exit_code_for` maps to exit 2 — the same class and shape as the `[S7.x]` provisioning-graph refusal it was modelled on. Wired into BOTH of `_run`'s preflight blocks (the real one and the `--dry-run` one), reusing the SAME `rendered` selection the deploy preflights already computed, so there is one render, not two. Three placement decisions, each recorded because they were judgment calls: it runs FIRST among the preflights (an operator with a config defect gets one complete S13.4a report rather than being stopped by whichever narrower check fires first); it runs under `--dry-run` too (a dry run exists to find exactly this class of defect, and the check is side-effect-free either way); and `--skip-check` ANNOUNCES itself with a `[WARN]` naming what was skipped, because a silently skipped gate is a gate that is not there. WARN-severity findings (CIU-65) never reach the refusal — they print as `note: [WARN] …` and the deploy proceeds. **NOT covered: `ciu up --dir <stack>`** (single-stack mode) — the preflight sits in the multi-stack `deploy_needs_preflight` block where every other preflight lives; extending it is a separate question about a different code path, flagged rather than done silently. Docs (S9.5 and CONSUMERS §14 both asserted `validate_config` is called "**never** during `ciu up`", now false): SPEC S9.5 + new S13.4c, README feature 7, CONSUMERS §14, `ciu up --help`, CHANGES.md. 7 end-to-end tests driving `deploy.main()` through a REAL `check_preflight` and a REAL `validate_config` hook. Controlled wrong implementation: `test_skip_check_defaults_to_off` fails if `--skip-check` were ever defaulted on |
| CIU-65 | `validate_config(config, ctx) -> list[str]` (S9.5) returns a bare list of strings with no severity — every hook author's finding is implicitly the same weight, and there is no way for a finding to be "worth knowing" without also being "must block". ciu already has the exact severity vocabulary this needs and doesn't reuse: `warn_policy.py`'s `ciu.exit_on` (`WARN`/`ERROR`/`NEVER`) and `should_exit_on(severity, ...)`, used elsewhere in this same codebase for exactly this WARN-vs-ERROR distinction. The shipped reference hook (`src/ciu/hook_templates/post_compose_db.py::validate_config`) returns a flat `list[str]` today, so this is the actual current shape, not a hypothetical | Low | FIXED — ciu-P41. Filed from `dstdns/vbpub@ciu/docs/V8-REALIZATION-GRAPH.md` (operator directive: "non-empty list[str] should be typed so we have cases of WARN vs ERROR findings... we already have a enum setting that controls behaviour"). Implemented in SPEC S9.5 as the tuple form, not a `Finding` dataclass: a finding is either a bare message string (`ERROR`, unchanged — no existing hook changes weight) or a 2-element `tuple` **or `list`** `(severity, message)`. Lists are accepted deliberately: a hook assembling findings from JSON or a comprehension produces lists, and refusing them would be a trap with no safety value. `deploy.classify_hook_finding()` is the classifier; WARN routes to `report.note` (tagged `[WARN]`, and `note()` gained a `hook=` kwarg so a WARN names its hook exactly as an ERROR does), ERROR to `report.fail`, unchanged. **Severity-string strictness (the decision):** `str(v).strip().upper()` — the SAME normalization `warn_policy._validate_exit_on` already applies to this exact vocabulary — matched against exactly `{WARN, ERROR}`. An unrecognized severity is REFUSED as its own ERROR finding naming the accepted values, never defaulted in EITHER direction: defaulting to ERROR would merely be noisy, but defaulting to WARN (or accepting anything truthy as WARN) would let a typo — `"warning"`, `"Error!"` — silently downgrade a blocking finding to an advisory note, a masked default whose runs look identical to healthy ones. `NEVER` is deliberately EXCLUDED from the finding vocabulary (it is an `exit_on` *threshold*, "abort at nothing", not a property a finding can have) and is refused like any other unknown value, with its own test. **DECISION, do not "fix" this back in:** routing is deliberately NOT wired through `should_exit_on`/`ciu.exit_on`/`$CIU_EXIT_ON`, contrary to this entry's own original proposal. `_CheckReport`'s `.fail`/`.note` split IS the severity mechanism for `ciu check`; keying it off ambient config would make the same config with the same hooks produce a DIFFERENT machine-readable `--json` verdict depending on shell state — the exact ambient-state coupling S9.3/CIU-41 removed from hooks in the first place. `ciu.exit_on` governs CIU's own runtime warn sites (DESIGN-NOTES D6), not a hook's static findings. Independently reviewed and endorsed at the ciu-P41 review round; stated in SPEC S9.5 and CONSUMERS §14 as well as here. 11 tests covering both shapes, case/whitespace normalization, list-vs-tuple, the refused unknown severity, `NEVER` specifically, a wrong-length sequence still failing closed, the reworded contract-violation message, and the classifier's own unit table. The shipped reference hook (`hook_templates/post_compose_db.py`) still returns a flat `list[str]` — still correct and still blocking; demonstrating a WARN there would force a `template_revision` bump and was deliberately left out |
| CIU-66 | `deploy.container_name(config, service_name)` (`src/ciu/deploy.py:162-174`) computes `f"{project}-{env_tag}-{service_name}"` — no stack identifier anywhere in its inputs or output — and no code path in ciu checks whether two DIFFERENT stacks in the same deployment declare the same `service_name`. Confirmed by reading the function's signature and by grepping ciu's source for any cross-stack service-name uniqueness check: none exists. Two stacks both naming a service `postgres` (or `redis`, or any other generic name a stack author might independently pick) would compute the IDENTICAL `container_name`, which Docker refuses as a hard duplicate — not the softer DNS-alias ambiguity CIU-51 already tracks, a deploy-time failure. **Currently latent, not live**: grepped every `name = "..."` service declaration across dstdns's own active stacks (2026-08-26) and found no current duplicate (authentik shares db-core's single `postgres` instance via its own database/schema — `infra/authentik/ciu.defaults.toml.j2:56` — rather than declaring a second `postgres` service, so today's dstdns deployment does not trigger this). The gap is structural, not hypothetical: nothing in ciu guards against the NEXT stack that reuses a common name. **CIU-51's own proposed fix does not close this**: its `qname()` example derives from the same `{project}-{env_tag}-...` identity `container_name()` already uses, with no stack parameter added — a stack-name collision would survive CIU-51 being fully shipped exactly as currently drafted | Medium | OPEN — BLOCKED (ciu-P41) — see the **BLOCKED** paragraph at the end of this cell for the established blast radius and the concrete next step. Filed from `dstdns/vbpub@ciu/docs/V8-REALIZATION-GRAPH.md` (operator-proposed fix, 2026-08-26, on spotting the gap): fold the declaring stack's own name into the identity — `container_name()`/`qname()` become `f"{project}-{env_tag}-{stack_name}-{service_name}"`, i.e. `<project>-<instance_id>-<stack-name>-<service>[-<replica>]`. This is the SAME shape as `CIU-V8-TESTING-GATE-PROPOSAL.md` §1.15's `<stack>.<service>` compound identifier, so the fix should land as one change to CIU-51's `qname()` signature (adding a required `stack` argument) rather than a separate primitive — otherwise v8 ships two independent stack-qualification schemes, one for config addressing and one for container naming, that could drift from each other. Cross-reference CIU-51 (which this refines) and CIU-50 (the `instance_id` rename touches the same identity tuple). **BLOCKED — ciu-P41 attempted this and stopped; blast radius established, endorsed at review, no code changed.** The proposed fix CANNOT land as a `container_name()` signature change, and landing it as one would be destructive rather than merely incomplete. **`container_name()` does not name anything.** It MIRRORS a convention that CONSUMER-AUTHORED Jinja compose templates implement literally — `container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ <root_key>.<svc>.name }}`, in ciu's own scaffold `src/ciu/templates/stack.compose.yml.j2:7` and in all six `test-repo/` fixtures — and ciu only ever READS the value back: `grep -rn '"container_name"\|'"'"'container_name'"'"'' src/ciu/` returns exactly ONE hit, `deploy.py`'s `definition.get("container_name")` in `resolve_selection_health_containers`. ciu never writes the key. So changing the function alone makes every ciu lookup compute a name no container has, breaking at once: `provisioning.py`'s `_probe_pg`/`_probe_minio` (`pg:`/`minio:` probes), `_stack_container_name` (every `stack:*:healthy\|completed` ref), `worktree.py`'s CIU-52 `--shared-infra-ref-services` resolution, and `deploy.py`'s `run_health_gate` (test-only today). The P41 REVIEWER found FOUR MORE sites that hand-assemble the same `{project}-{env_tag}-{service}` shape without calling `container_name()` at all, and would therefore silently DIVERGE rather than break loudly: **`dev.py:299`, `engine.py:1516`, `deploy_pkg/health.py:265`, `deploy.py:3124`/`:3878`**. **Blocking sub-finding: the new name is not expressible today.** `render_ciu_context` (`deploy_pkg/profiles.py:364`) exposes exactly `selected_profiles` and `deployed_stacks` — there is no "the stack this render belongs to" fact anywhere in the template context, so a consumer template cannot emit `{project}-{env}-{stack}-{service}` even if it wanted to. **CONCRETE NEXT STEP, and it is a small standalone package:** expose a per-stack identity fact in `render_ciu_context` — additive, breaks nothing, and is the prerequisite for BOTH this entry and CIU-51's `qname()`. Do that first, as its own package. Only then is the rest reachable: ciu's own scaffold + all six `test-repo/` compose templates; a migration for every consumer's templates (dstdns has 31) with either a transition where ciu resolves BOTH name shapes or a flag day; a stack qualifier in the `--shared-infra-ref-services alias=service` CLI grammar (a user-facing break, CONSUMERS §8); and the four hand-assembled sites above. Two facts argue for the v8 cut rather than now, and this entry already says both: it is **latent, not live** (no duplicate service name exists in dstdns today), and it "should land as one change to CIU-51's `qname()` signature". Full analysis: `nyxloom-trove/reports/ciu-P41-REPORT.md` item 3 |
| CIU-67 | `deploy.health.timeout` is read for two semantically incompatible purposes with no distinct config key: (1) a Docker `HEALTHCHECK` field — how long ONE probe attempt (`wget`/`vault status`/etc.) may run before that attempt is considered failed, correctly a few seconds; (2) via `resolve_selection_health_containers`'s `default_timeout_s = _seconds(health_cfg.get("timeout", "30s"))` (`deploy.py:1453`), the S7.7 inter-phase health GATE's overall wait budget for a container to transition to `healthy` at all — which needs to be on the order of that container's `start_period` plus several `interval`s (minutes), not one probe's duration. A consumer authoring `[deploy.health].timeout` with only meaning (1) in mind (the field's own shape — interval/timeout/retries/start_period — IS Docker's own HEALTHCHECK syntax, giving no hint it's dual-purposed) silently sets meaning (2) to the same tiny number. Live-reproduced (dstdns, 2026-08-26, D-212): `[deploy.health] timeout = "5s"` (a correct, deliberate value for per-probe Docker timeout, documented inline as tuned for "this host's ~730ms write-await") meant every health-enabled container with no per-service `health_timeout` override got a 5-SECOND overall S7.7 gate budget — `pgadmin` (whose own healthcheck `start_period=240s`, `interval=10s` is equally deliberate) failed the gate on every single fresh deploy, 5 seconds after phase start, despite being on track to become healthy normally within its own declared grace period | Medium | FIXED — ciu-P41: `[deploy.health].gate_timeout` added as the distinct key, specified in SPEC S7.7 and documented in CONFIG.md (the `[deploy.health]` row, the `health_timeout` fallback paragraph, and a pasteable example that shows `timeout` and `gate_timeout` side by side answering different questions). A container's gate budget now resolves most-specific-first: the phase entry's `health_timeout` (the existing escape hatch, untouched and still the winner) → `gate_timeout` → otherwise DERIVED per container by `deploy.derive_gate_budget_s()` from that container's own rendered healthcheck. `resolve_gate_timeout_s()` returns `None` when the key is absent, never `0` — an absent key is not a value, and `None` is what selects the derivation. **DEPARTURE from this entry's proposal, endorsed at review: the derivation is `start_period + retries × interval`, not bare `start_period`.** A container only reports healthy on a successful probe and probes land on `interval` boundaries, so a budget of exactly `start_period` can expire one interval BEFORE the first post-grace probe runs; `start_period + retries × interval` is Docker's own worst case for a container still legitimately converging. Fields the healthcheck omits use Docker's documented defaults (interval 30s, retries 3, start_period 0s) — READ facts about daemon behaviour, not invented numbers — giving `DEFAULT_GATE_BUDGET_S = 90s`; a service declaring no healthcheck gets the same 90s and never waits on it (it classifies `no-healthcheck`, a READY status resolved on the gate's first poll). The live reproduction is pinned verbatim as a test: `timeout = "5s"` beside a service declaring `start_period: 240s, interval: 10s, retries: 3` must resolve to **270s**, not 5s. Note for the record: NO existing test pinned the old `timeout`→gate coupling, which is why the defect shipped. dstdns's 14 explicit `health_timeout` workaround overrides still win and can now be removed |
| CIU-68 | The S7.7 inter-phase health gate — the ONLY mechanism that makes a `stack:*:healthy` provisioning-preflight ref reliable across a phase boundary — is not part of `ciu up`'s default action sequence (`build_action_sequence` returns `["deploy"]` when no action flags are passed; `health_after_phase = "deploy" in actions and "healthcheck" in actions` is therefore `False` unless BOTH `--deploy` and `--healthcheck` are passed explicitly), and neither `--deploy` nor `--healthcheck` appears anywhere in `ciu up --help`'s printed flag list — an operator has no way to discover the flag from the tool itself. Separately, `provisioning_preflight`'s live probe path (`deploy.py:661-679`) calls `provisioning_pkg.probe_ref` exactly once per requirement with zero retry — `_probe_stack` (`provisioning.py:538`) is a single `docker inspect`, so a dependency reported `starting` (not yet converged, but on track) is treated identically to a dependency that will never satisfy. Live-reproduced (dstdns, 2026-08-26, D-212): a bare `ciu up` — the exact command this project's own docs prescribe as the standard bring-up (and, by grep, likely most consumers') — failed a genuinely fresh deploy at phase_2 because `stack:infra/vault:healthy` reported `starting`, one preflight call, no retry, immediate hard stop; vault reported healthy shortly after, unobserved | High | FIXED — ciu-P41 (see the **FIXED — ciu-P41** paragraph at the end of this cell). Filed from the same investigation as CIU-67 (fix these together: CIU-67 makes the gate's timeout correct once enabled; this makes the gate actually run by default and gives the one-shot probe a bounded retry). Proposed: (a) either make `health_after_phase` default `True` whenever any stack in the run declares a `stack:*:healthy\|completed` requirement (self-selecting — a run with no such refs pays nothing), or document `--deploy --healthcheck` prominently in `ciu up --help` and dstdns's own bring-up docs (done as an immediate step, `dstdns@<pending>`) as the correct invocation; (b) give `provisioning_preflight`'s live-probe path a bounded poll (reusing CIU-67's proposed `gate_timeout`/`start_period`-derived budget) instead of one-shot-and-fail specifically for the `starting` case, distinct from a genuinely absent/misconfigured dependency. **FIXED — ciu-P41, BOTH halves (the entry offers (a) as either/or; both were done, since discoverability was independently broken).** Specified in SPEC S7.7 as two new normative blocks. (a) The gate is now SELF-SELECTING: `deploy.selection_stack_health_requirement()` scans the rendered selection and `health_after_phase` turns on whenever any stack declares a `stack:*:healthy\|completed` requirement, announcing the ref responsible; a run with no such ref is unchanged and pays nothing. The computation moved to AFTER `rendered` exists, since the answer is derived from the rendered selection. `--deploy`, `--healthcheck` and `--check` are now listed in `ciu up --help` under a new Actions block regardless. (b) `ProbeResult` gained `retryable: bool = False` — the default keeps every existing construction fail-promptly, which is correct for them — set `True` in exactly the branches meaning "not satisfied YET, but on track", and `provisioning_preflight` polls only those, to `gate_timeout` (or the 90s default) at the gate's own 5s cadence, with ONE shared deadline per phase so ten retryable requirements do not each get a fresh full budget in sequence. **DEPARTURE, endorsed at review: retryability also covers `stack:*:completed` whose container is still RUNNING**, not only `:healthy`/`starting` as this entry proposes — it is the identical mistake in the other terminal (a one-shot job that has not finished yet is not a job that failed), and this entry's own trigger condition names both terminals. Everything else still fails PROMPTLY — absent container, `unhealthy`, non-zero exit, unavailable daemon, unparseable state — because a poll that waited on everything would turn every real misconfiguration into a long silent stall; there is a test asserting the non-retryable path never sleeps at all. A string-sniffing implementation (matching `"starting"` in `result.reason`) was REJECTED as AGENTS.md's "type for behaviour" anti-pattern; the flag is set at the branch that knows the condition. 33 tests in `tests/tests/test_ciu_health_gate_budget.py` (shared with CIU-67), including the self-selecting gate end-to-end through `deploy.main()` for both ref terminals and the bounded poll on a deterministic injected clock. `test_ciu_cli_parser.py`'s per-verb-help leak sentinel changed from `--deploy` to `--stop`: it asserted `--deploy` was ABSENT from `ciu up --help`, which is the exact absence this entry calls a defect |
| CIU-69 | `[ciu.worktree]` is documented as carrying THREE key families — S16.3 `max_concurrent_instances`, S16.9 `lease_ttl_hours`, and S16.7 `exec_targets.<alias>` (CONSUMERS §8; CHANGES "closed four-key grammar") — but `worktree.py:4009 WORKTREE_TABLE_KEYS = frozenset({"max_concurrent_instances", "lease_ttl_hours"})` and `_validate_worktree_table` (`worktree.py:4012-4024`) refuse ANY other key, and that validator runs on `ciu up`'s S16.3 budget path (`resolve_max_concurrent_instances`, `worktree.py:4108-4122`) and its S16.9 lease path (`resolve_lease_ttl_hours`, `worktree.py:4198`) whenever the table is present at all. Source-reproduced 2026-08-30 against `ciu 7.5.1.dev24`: `_validate_worktree_table({"max_concurrent_instances": 3, "exec_targets": {"tester": {...}}})` raises `WorktreeError: [S16.3] unknown key(s) in [ciu.worktree]: exec_targets`. A consumer that follows CONSUMERS §8 and declares an exec target in the same global config that also carries a budget or a lease TTL therefore has EVERY `ciu up` in a managed instance refuse — the two shipped contracts are mutually exclusive inside one table. ciu's own suite never combines them (`tests/tests/test_ciu_worktree.py` declares each family in isolation), which is why it went unnoticed; dstdns declares only `max_concurrent_instances` today | Medium | FIXED — ciu-P36: `WORKTREE_TABLE_KEYS` gains `exec_targets` (`worktree.py:4009`); `test_all_three_families_coexist_in_one_table` (`tests/tests/test_ciu_worktree_lease.py`) declares all three families in ONE `[ciu.worktree]` table, asserting `resolve_max_concurrent_instances`/`resolve_lease_ttl_hours`/`resolve_exec_targets_config` all accept it; controlled wrong implementation verified — removing `exec_targets` from the set fails that test with exactly `[S16.3] unknown key(s) in [ciu.worktree]: exec_targets`. `docs/CONFIG.md`'s `[ciu.worktree]` table and heading updated to name all three keys (was stale at two, flagged then fixed in the same package after review authorized it). Owning SPEC sections: S16.3, S16.7, S16.9 — none needed a text change (no literal key-set enumeration to update) |
| CIU-70 | `_probe_pg` (`src/ciu/provisioning.py:345-383`) and `_probe_minio` (`:386-408`) resolve the container they `docker exec` into as `container_name(config, "postgres")` / `container_name(config, "minio")` — the LITERAL service keys `postgres` and `minio` — and `_probe_pg` additionally runs `psql -U postgres` unconditionally. Nothing in SPEC S13.2's ref-kind table (`pg:role/<name>` → "`psql` → `pg_roles`"; `minio:user/<name>` → `mc admin user info`) or CONFIG.md states that the Postgres/MinIO service must be keyed exactly `postgres`/`minio`, and no config key exists to point the probe elsewhere. Source-confirmed 2026-08-30 (`ciu 7.5.1.dev24`). A consumer whose service is keyed anything else (`postgres_primary`, `pg`, `db`) gets `pg role 'x' not found (rc=1)` — byte-identical to the message for a genuinely missing role — so a correct deployment fails provisioning preflight with a misleading reason; and a consumer running TWO Postgres services keyed `postgres` in different stacks (exactly dstdns's `db-core/postgres` + `skywalking/postgres` situation from CIU-66, which derive the SAME container name) has the probe interrogate whichever container won the name, silently. dstdns happens to key its services `postgres`/`minio` and runs one MinIO, which is why the coupling has never surfaced | Medium | FIXED — ciu-P40 implemented option **(b)**. `probe_ref` takes the requires/provides graph (`stacks=`); `_probe_pg`/`_probe_minio` resolve the container from the stack whose `provides` carries the exact ref, through the existing `_stack_container_name` path, and refuse loudly instead of guessing when nothing provides the ref or when providers resolve to different containers. The graph handed to a probe spans every RENDERED stack (`deploy.provisioning_graph`), not the phase's `selection` — a cross-phase provider is in an earlier phase by construction, and `rendered` is itself selection-scoped per invocation, so the wide graph can never reach an unselected stack. Reason strings now split "container absent/stopped — NOT checked" from "does not exist (query ran, no matching row)" (`psql -tAc` exits 0 on an empty result, so exit 0 is the only status absence honestly follows from) and from "could not be checked (rc=N)". SPEC S13.2 + FEATURES.md updated; controlled wrong implementation reproduced red (`pg role 'api' not found (rc=1)` against `dstdns-dev-postgres`) and green (`pg role 'api' exists` against `dstdns-dev-pg`) — see `nyxloom-trove/reports/ciu-P40-REPORT.md`. NOT fixed by this package, deliberately: `psql -U postgres` is still unconditional (needs a new public config key), and two declared stack paths sharing a final segment still collapse onto one container name (that is CIU-66). Original filing: from the v8 design session (`docs/CIU-V8-TESTING-GATE-PROPOSAL.md` rev 2.0 §4.7/§4.11). In v8 the probe target is a declared fact (a `pg:`/`minio:` ref resolves to the RealizedService whose `provides`/`init_provides` carries it, through the same identity derivation everything else uses), so this entry is the backport-able half: (a) document the naming requirement in S13.2 + `ciu check --help`'s ref-kind list (minimal), or (b) resolve the container from the stack that `provides` the ref — the provider is already known to `lint_graph` — instead of a literal name, and make the reason string distinguish "container absent" (docker exec `No such container`) from "role/user absent". Controlled wrong implementation: a fixture with the Postgres service keyed `pg` and a `pg:role/x` ref must fail today with `not found (rc=1)` and pass after (b) with the role actually checked. Owning SPEC section: S13.2 |
| CIU-71 | A stack's relative `build.context` (e.g. `infra/mock-targets/ciu.defaults.toml.j2`'s `build_context = "."`) resolves against the COMPOSE FILE's own directory, not the repo root, because `ciu` never invokes `docker compose` with `--project-directory <repo-root>` — grepped the installed `ciu` package's own source, no such flag exists at any `docker compose` call site. Live-reproduced (dstdns-P147b, 2026-08-30, `ciu 7.5.1`, first-ever bring-up of `infra/mock-targets` — the ONLY stack in that consumer repo with a `build:` section at all, so no prior stack ever exercised this path): `ciu up --dir infra/mock-targets` failed `resolve : lstat .../infra/mock-targets/tests: no such file or directory`, because the stack's Dockerfile `COPY`s repo-root-relative paths (`tests/fixtures/mock_data`) that only resolve if `.` means the repo root, which it does not under Compose's own default | Medium | FIXED — ciu-P37: `execute_docker_compose_with_logs` (the ONE function both native `up` and `--shipped` call) and `reset_service`'s `down` construction both now require `repo_root` and pass `--project-directory <repo_root>`; fix (a) from this row's own proposal, chosen as prescribed rather than (b). New `tests/tests/test_ciu_compose_project_directory.py` reproduces the live failure mode with a real Dockerfile/COPY/build.context fixture and a confirmed-real controlled-wrong-implementation revert. Independent adversarial review ran a live `docker compose` acceptance probe confirming the mechanism itself, then required three documentation corrections (S8.1a, README.md, CONSUMERS.md #18): `dockerfile:` moves with `context:` (Compose resolves it relative to context, not `--project-directory` directly — live-confirmed: `failed to read dockerfile: open Dockerfile: no such file or directory` without it); the claim that CIU's other paths are already repo-root-relative was INVERTED — hostdirs, ASK_FILE secrets, and configfile schema/template paths all resolve stack-dir-relative, confirmed by reading `engine.py`/`materialize.py`/`composefile.py`; and `--project-directory` also relocates bare `.env` lookup (live-confirmed), accepted as correct behavior rather than compensated for with a second `--env-file` flag, since CIU itself never relies on bare `.env`. See `nyxloom-trove/reports/ciu-P37-REPORT.md` for the verbatim gate verdict and live probe output. |
| CIU-74 | `render_jinja2_text` (`src/ciu/config_model.py:386-397`) builds `jinja2.Template(text)` with the library-default `Undefined`, so a mistyped LEAF key renders as the empty string with no error. Live-reproduced 2026-08-30 (`ciu 7.5.1.dev24`): `{{ deploy.project_name }}-{{ deploy.environment_tg }}-postgres` against an ordinary `[deploy]` context renders `dstdns--postgres`; only the same typo one level deeper (`{{ deploy.health.timeout }}` with no `[deploy.health]` table) raises — which is the sole case SPEC S3.12 / S7.5b's "fails loudly (Jinja `UndefinedError`) rather than silently" promise actually covers. dstdns's 19 stack `ciu.compose.yml.j2` templates assemble container names, network names and labels by hand from `deploy.project_name` / `deploy.environment_tag` / `deploy.network_name` (70 / 68 / 59 references), so one dropped letter yields a syntactically valid compose file that joins the wrong network or names a container `dstdns--postgres`, caught only if something downstream happens to fail. The same default governs the compose renders at `composefile.py:287` and `:973` and every S3.2 TOML-template render | Medium | FIXED — ciu-P38 (`8416ce93`): `render_jinja2_text` now builds `jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True).from_string(text)`, exactly the proposal above. The S7.5b interaction is resolved the honest-shape way: `ciu.instances` is now an ALWAYS-PRESENT mapping (defaulting to `{}`) in every context-assembly site that merges the `ciu` table in (`config_model._make_render_context`, `composefile.render_compose`, `composefile.render_configfiles`), so `'api' in ciu.instances` keeps working with no fan-out declared — `ciu.selected_profiles`/`ciu.deployed_stacks` keep their existing, deliberately opposite, fail-loud-when-absent contract unchanged. Both oracles pass: the `dstdns--postgres` repro now raises naming `environment_tg`; a controlled-wrong-implementation test (bare `jinja2.Template(...)`) confirms it still silently renders `dstdns--postgres`, proving the strict test exercises the fix. SPEC S3.2 / S7.5d, CONSUMERS.md §16, README.md updated. Real gate (`./run-gate.py ciu`) verdict: R1 (assay's changed-lines 100% coverage judgment against this fix) PASSES at 100%; rebased past main's independent CIU-76/CIU-78 fixes before the final gate run — see `nyxloom-trove/reports/ciu-P38-REPORT.md` §4 for the post-rebase verdict and evidence |
| CIU-75 | Backport of v8's F2 identity decision: the per-instance overlay TOML becomes the SOLE source of instance/identity facts; `ciu.env` is demoted to a legacy, write-only export (`ciu env generate` keeps writing it for shells/tooling that still source it; `ciu env print`, already shipped by CIU-60, is the forward-looking export) and is never read back by ciu internals. Source-confirmed 2026-08-31: 12 call sites across `worktree.py` (1137, 1229, 2635, 2884, 3250, 3859, 4266), `engine.py` (948, 1182, 1484) and `deploy.py` (1882, 2838) read or existence-check `ciu.env` by exact path today — a larger surface than the v8 proposal doc's illustrative `paths.py`/`workspace_env.py` citation, and not all 12 are necessarily identity-fact reads (some may be plain existence checks); each site needs individual classification before this can be called complete | Medium | FIXED — ciu-P42, ships as **ciu 7.7.0** (BREAKING). All 12 sites classified individually (11 fact-reads migrated to `read_generated_facts`; the 1 pure existence check re-pointed at `has_generated_facts`, deliberately, because `ciu clean` now derives both its identity network and its identity compose project from the table). New SPEC section **S3.1c** owns identity-source precedence. **Review round 1 found the cutover incomplete and it was completed, not re-scoped:** the 12 sites were only half the surface — `bootstrap_workspace_env` still seeded `os.environ` from `ciu.env` at STEP 1 of every verb, skip-if-present, so a shell that had sourced a SIBLING checkout's export won at ~26 ambient-reading sites INCLUDING the `$DOCKER_NETWORK_INTERNAL` in every rendered template (live repro: `deploy.network_name` = the sibling's network). S3.1c clause 2a now seeds the six facts from the table, OVERRIDING ambient; `ciu.env` is read at startup for MACHINE facts only, by exact path, and can no longer abort a verb (its four bootstrap reads never had CIU-62's three-exception treatment, so a corrupt export crashed `ciu up` with a raw `UnicodeDecodeError`). Also from that round: the deprecation notice moved to stderr on bootstrap-triggered regeneration (it was breaking `ciu check --json`), and `ciu worktree reap` now reports the checkouts it could not delegate to `ciu clean` (bare `docker rm` leaves `vol-*` on disk — not a refusal, and it was silent). **Review round 2 was docs-only**: round 1's sweep of the stale `ciu.env`-as-read-source claims had been partial (it never touched `CONFIG.md` or `FEATURES.md` at all), so every `.md` under `docs/` plus the root README was re-grepped exhaustively rather than sampled — S16 `worktree rm`, S16.9's lease holder, S11's validation catalog, S6.4b, S8.2, CONFIG.md's hook-context/layer-model/reference-render/worktree-control passages, FEATURES.md, ARCHITECTURE.md, CIU.md, CIU-DEPLOY.md and DESIGN-GUIDE.md were corrected, DESIGN-GUIDE gained the missing WHY section for this decision, the published consumer helper gained the fourth indeterminacy check (and was executed against real records rather than eyeballed), and CIU-82's dstdns inventory was corrected again. Oracle: `tests/tests/test_ciu_identity_cutover_ciu75.py` — every site with `ciu.env` deleted AND corrupted, the converse (remove the OVERLAY and the same sites stop answering), and a hostile-ambient identity driven through a REAL verb. `docs/CONSUMERS.md` §11b carries the consumer migration. Detail + the dstdns sweep below; CIU-82 tracks the consumer-side notification |
| CIU-76 | `apply_lease` (`src/ciu/worktree.py:1512`, the `ciu worktree lease` CLI verb behind S16.9) has no `now:` override — it always acquires/extends relative to REAL wall-clock time (`_utc_now()`), even though the lower-level `acquire_lease`/`make_lease_perpetual` it calls both already accept one. Any test exercising `apply_lease`'s time-based behavior against a frozen fixture clock is therefore non-deterministic as the environment's real clock advances. Reproduced 2026-08-31: `tests/tests/test_ciu_worktree_reap.py::TestLeaseLifecycleChangesTheNextSurvey::test_re_expiring_after_an_extend_becomes_lease_expired_again` fails on a clean `main` checkout (`b8102bc2`) with `assert 'owned' == 'lease-expired'` — its `NOW` fixture is hardcoded `datetime(2026, 8, 25, ...)`; the container's real date has since advanced to 2026-08-31, so `apply_lease`'s real-clock 1h extend expires well after the test's frozen `NOW + 2 days` check point, and the survey correctly (by real time) still reports `owned`. Not a lease-logic defect — `_lease_is_expired`/`acquire_lease` are internally consistent; this is a test-determinism gap only, found while ciu-P36 was gating an unrelated fix (CIU-69) | Medium | FIXED — ciu-P36: `apply_lease` gained `now: datetime \| None = None`, threaded to both its `acquire_lease(...)`/`make_lease_perpetual(...)` calls (`--release` unaffected, not time-based); `test_re_expiring_after_an_extend_becomes_lease_expired_again` now passes `now=NOW`, verified deterministic by monkeypatching `_utc_now` to raise. Grepped all 18 `apply_lease(` call sites under `tests/` for the same latent fragility — only that one test hit it (documented per-site reasoning in `nyxloom-trove/reports/ciu-P36-REPORT.md`). Independent adversarial review then found the `--perpetual` threading itself untested (deleting `now=now` from the `make_lease_perpetual` call left the whole suite green); `test_perpetual_honours_an_injected_now` (`tests/tests/test_ciu_worktree_lease.py`) now asserts the injected instant directly, with a confirmed controlled-wrong-implementation failure |
| CIU-77 | ciu's own gate (`run-gate.toml [lanes.ciu]`) vendors and self-pins `tools/assay/assay-2.3.0.pyz` — three MAJOR versions behind assay's actual current release (`assay 3.2.0`, confirmed 2026-08-31). CIU-77 is NOT "bump the pin string": assay 3.0.0 was itself a breaking release for assay (`feat(assay)!: drop the withdrawn operator spellings at the v8 cut`, judge provenance now required per its own CHANGES.md) — the gate's shell harness (`assay run ciu --file assay.toml --verdict-json ...`) and `assay.toml`'s `schema_version = 2` / `[lanes.ciu.judge]` shape both need verifying against 3.2.0's actual CLI/config contract before any version bump, not assumed compatible. No refresh tooling exists for the vendored `.pyz` — it appears to be manually copied in; that gap should probably close alongside this fix so the drift doesn't recur | Medium | FIXED — ciu-P43. Verified against 3.2.0's real CLI/config contract BEFORE bumping, per this row's own requirement — not assumed compatible: (1) `assay lanes --json --file assay.toml` against ciu's UNMODIFIED `assay.toml` under a real installed 3.2.0 parsed clean (`base_source: "declared"`, `enforcement: "gate"`, `scope: "S1"`, `rigor: ["R0","R1"]` all round-tripped) — the `LANE_SCHEMA_VERSION` ciu's `schema_version = 2` targets has been unchanged since before 2.3.0, so `assay.toml`'s body needed ZERO changes, only its two comment-block version mentions; (2) `assay run --help` at 3.2.0 confirmed `assay run <lane> --file PATH --verdict-json PATH` is byte-identical CLI surface to what the gate's shell harness already constructs; (3) the three risk areas the row named all turned out inapplicable to ciu's lane specifically: the v8-cut withdrawn mutation-operator spellings (A-331) are R2/mutation-only and ciu's lane declares only `rigor = ["R0","R1"]`; judge provenance (B018) is opt-in via `--require-judge-provenance`, never passed by the gate harness; request-supplied base (B019) is opt-in via `judge.base_source = "request"`, and ciu's lane keeps its static `judge.base = "origin/main"` (confirmed `base_source: "declared"` above) — the shared `run-gate.py` harness (already RG-25/RG-26-updated ahead of this fix, independent of it) only appends `--request-base` to a lane whose inventory reports `base_source == "request"`, so ciu's lane never receives it. Only the VERDICT schema moved (v7->v8, `VERDICT_SCHEMA_VERSION`), and `run-gate.py`'s shell harness never parses verdict JSON itself — the gate's pass/fail is `assay run`'s own exit status (`set -euo pipefail`), so that axis has no ciu-side impact either. The vendored `.pyz` was rebuilt from the EXACT `assay-v3.2.0` tagged commit (a throwaway detached worktree at that tag + assay's own `gate/distribution/build_release.py --repo .. --outdir <scratch>`, the same mechanism assay's real releases use — `ASSAY_RELEASE_MANIFEST=created tag=assay-v3.2.0` confirmed a genuine tagged build, not a dev/SCM-fallback version), sha256-verified, and its `--version` output (`assay 3.2.0`) matches the new pin exactly. `run-gate.toml [lanes.ciu]`/`[lanes.ciu.pins.assay]` and `assay.toml`'s comment both repointed to `tools/assay/assay-3.2.0.pyz`; `README.md`, `docs/CONSUMERS.md` §12, and `nyxloom-trove/nyxloom.toml` updated to match. The three orphaned older vendored zipapps (`assay-2.1.0.pyz`, `assay-2.2.0.pyz`, and the just-superseded `assay-2.3.0.pyz`, each with its `.sha256`) were confirmed referenced nowhere else in ciu and deleted — the actual drift-recurrence mechanism this row flagged, since nothing had ever pruned a prior version on bump. No new automated refresh TOOLING was built (the nice-to-have half of this row): the verified-working manual SOP (worktree at the exact `assay-v<X>` tag + `build_release.py` + `sha256sum -c` + vendor + prune) is recorded here and in the LOG for the next bump to follow, rather than adding new code+tests to this already-largest item in the ciu-P43 bundle — a dedicated follow-up can script it if the manual SOP proves to recur often enough to be worth automating. `./run-gate.py ciu --worktree <this-worktree>` run for real against the bumped pin — see ciu-P43-REPORT.md for the verbatim verdict |
| CIU-78 | Two tests in `tests/tests/test_ciu_deploy_actions.py` (`test_check_suppresses_bytecode_writes_while_importing_hooks`, `test_check_restores_the_bytecode_flag_after_a_failed_import`) asserted `sys.dont_write_bytecode is False` after `deploy.action_check`'s save/restore of the flag (`deploy.py:2085-2095`; `provisioning.py:834-839` has the identical pattern, still untested either way). The save/restore logic itself was correct — it restores to whatever the AMBIENT value was — but the assertions hardcoded the assumption that ambient is `False`. `assay.toml`'s own declared gate environment (`env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }`, line 25) sets `sys.dont_write_bytecode` to `True` at interpreter startup, so both assertions failed in the REAL gate. Independently found and filed by both ciu-P38 (gating CIU-74) and ciu-P36 (gating CIU-69, described only as "pre-existing bytecode failures" there before the mechanism was pinned down) | Medium | FIXED — both assertions now capture `ambient = sys.dont_write_bytecode` before the call and compare restoration against that captured value instead of a hardcoded `False`; verified green both with and without `PYTHONDONTWRITEBYTECODE=1` set. `provisioning.py:834-839`'s identical pattern remains genuinely untested (no assertion exists there to be wrong, just no coverage) — a future small package should add it, not fold in here since it's new coverage, not a fix |
| CIU-79 | `ciu dev`'s `_build_dev_image` (`src/ciu/dev.py:317-330`) has the SAME defect class CIU-71 fixed for `docker compose`, in a plain `docker build` invocation: `context = build.get("context", ".")` resolves relative to `stack_dir` (`run_fn(argv, cwd=str(stack_dir))`), never the repo root, and `dockerfile = build.get("dockerfile", "Dockerfile")` resolves relative to THAT context (`str(Path(context) / dockerfile)` — the exact rule CIU-71's own fix and docs now cite as `ciu dev`'s own precedent for how `dockerfile` moves with `context`). A `ciu dev` profile whose Dockerfile `COPY`s a repo-root-relative path — the same shape the CIU-71 live repro hit under `ciu up` — fails the same way under `ciu dev`, uncorrected: `ciu dev` runs an ordinary `docker build`, not `docker compose`, so there is no `--project-directory` equivalent to reach for; the fix shape is different (resolve `context` to an absolute, repo-root-relative path before building the argv, mirroring what CIU-71 now does one layer up) | Medium | FIXED — ciu-P43: `_build_dev_image` now takes `repo_root` and resolves `context` as `(Path(repo_root) / context).resolve()` before joining `dockerfile` onto it and appending it as the build's trailing positional argument — `run_dev` (the only caller) already had `repo_root` in scope. `test_build_context_resolves_against_repo_root_not_stack_dir` (`tests/tests/test_ciu_workspace_dev_remaining_boundaries.py`) reproduces the exact controlled-wrong-implementation shape this row specified: a Dockerfile `COPY`ing a repo-root-relative path, `context = "."`, asserting the COPY source is reachable from the resolved build context (manually confirmed failing against the pre-fix stack-dir resolution). The two pre-existing tests that pinned the old (buggy) stack-dir-relative argv shape were updated to the corrected repo-root-relative one. `docs/SPEC.md` S5a.1 and its S8.1a note, `docs/CONSUMERS.md` #18, and `README.md`'s DooD bullet now document that `ciu dev`'s `build.context`/`dockerfile` share S8.1a's repo-root-relative convention. **This is a breaking change**: an existing `[<root>.dev].build` profile whose Dockerfile lives in the stack dir (relying on the old, buggy stack-dir-relative resolution) now fails until `dockerfile` is repointed repo-root-relative — CONSUMERS #18 carries the migration note and repair; nil blast radius inside this monorepo (no shipped fixture/template declares `dev.build.context`) |
| CIU-80 | The two S3.12 workspace-identity readers — `deploy._workspace_identity` (the `ciu check` preflight's HookContext) and `engine.main_execution`'s own read at the STEP-12 context build (the real run's) — degrade an UNREADABLE `ciu.env` to `{}`, so `HookContext.instance_id` / `.network` become `None`. As of ciu-P41 (CIU-62) both also WARN, so the degradation is at least announced; but `None` still means two different things to a hook that reads it: "this workspace is genuinely unmanaged, no `ciu env generate` has run here" and "the record exists and CIU could not parse it". A hook cannot distinguish them from the context alone, and a hook that legitimately branches on identity (writing per-instance state, choosing a network) will silently take the unmanaged branch against a corrupt-but-present record. This is the same absence-for-emptiness shape CIU-62 fixed at `deploy._workspace_identity_network` (where `ciu clean` was under-cleaning silently); it was NOT fixed at these two because the correct answer here is genuinely less obvious — the `{}` degradation is what keeps preflight and real run answering the identity question identically (S3.12/CIU-44), and a preflight that saw an identity its own `run()` will not is a worse failure than the one being avoided | Low | FIXED — ciu-P43, shape (b) per the controller's ruling (non-breaking, keeps the whole ciu-P43 bundle additive). `HookContext` (`hooks_runner.py`) gains `identity_unreadable: bool = False`. `deploy._workspace_identity` now returns `(facts, identity_unreadable)` instead of a bare `dict` — its single call site and the two intermediate functions it threads through (`_check_stack_config`, `_check_hooks_for_stack`) all gained the new parameter — and `engine.main_execution`'s STEP-12 read gained a local `_hook_identity_unreadable` flag, set identically in its own `except (OSError, UnicodeDecodeError, WorkspaceEnvError)` clause. Both sites set the flag `True` ONLY on present-but-unreadable, `False` on genuinely-absent (the pre-existing `{}` degradation is otherwise unchanged — no CHANGES-worthy break). Changed as the MANDATORY pair this row specified: `test_identity_unreadable_agrees_between_check_preflight_and_real_run` (`tests/tests/test_ciu_render_selection_context.py`) drives ONE malformed `ciu.env` fixture through both `deploy._workspace_identity` and a real `engine.main_execution` run with a probe hook, asserting `ctx.identity_unreadable` agrees between them (both `True`) and that both are distinct from the legitimate-absent case (`test_workspace_identity_degradation_warns_on_stderr`'s `payload is None` branch, asserting `False`). `docs/SPEC.md` S9.3's HookContext contract, `docs/CONSUMERS.md` (§10's hook-identity bullet list and §"Teach a hook to validate its own config"), and `docs/CONFIG.md`'s hook-facts paragraph all updated to name the new field |
| CIU-81 | `scaffold.py` (`ciu init`) has two Jinja render paths CIU-74 never touched: `_render_jinja` (`scaffold.py:91-107`, the actual template-writing render) and `build_files`'s own inline `Environment(keep_trailing_newline=True)` (`scaffold.py:275-310`, the "validation-first: render + parse everything before writing" preflight). Both still render with the library-default lenient `Undefined`, unlike `config_model.render_jinja2_text` (the real S3.2 step 1) which has used `StrictUndefined` since CIU-74. The preflight is the one that matters most: its own comment says "parseability alone once shipped a repo that died at step 7" — it exists specifically to catch scaffold-template defects before `ciu init` writes them out, but now certifies those templates under WEAKER semantics than the pipeline that actually consumes them at a real `ciu up`. A scaffold template with a mistyped leaf variable can pass `ciu init` clean and only fail at the consumer's first real deploy — a false certification (AGENTS.md "a check is only as strong as what it actually compares"). Found by ciu-P38's CIU-74 review (blocker 4) while confirming "one render function, three call sites" was true of `render_jinja2_text` specifically but not of the whole codebase; `scaffold.py:91`'s docstring, which falsely claimed parity with the production engine, was corrected in the same review round (not itself a fix for this gap, just for the false claim) | Medium | FIXED — ciu-P43. Verified first, per this row's own requirement: every shipped scaffold template (`src/ciu/templates/global.defaults.toml.j2`, `stack.defaults.toml.j2`, `stack.compose.yml.j2`) was inspected — the two TOML templates carry ZERO Jinja `{{ }}`/`{% %}` syntax by the time `build_files` renders them (every `@@PLACEHOLDER@@` is substituted by plain `str.replace` before the Jinja env ever sees the text; only `$VAR`-style tokens remain, a DIFFERENT, later substitution mechanism, not Jinja), so no legitimate lenient-Undefined use exists to preserve; `stack.compose.yml.j2` (the one file with real Jinja refs) is shipped verbatim and is never Jinja-rendered by `scaffold.py` at all — it renders for real, under `config_model.render_jinja2_text`'s StrictUndefined, at the consumer's own `ciu up`. `StrictUndefined` adopted at both named sites (`_render_jinja`, `build_files`'s inline `Environment`), matching `config_model.render_jinja2_text`'s exact construction. Both preflight render call sites (global template, per-stack `ciu.defaults.toml.j2`) also gained a `try/except TemplateError` converting a genuine future undefined-reference defect into a clean `SystemExit` naming the template and the Jinja error, instead of a raw traceback — the natural completion of "this preflight exists to catch scaffold-template defects", not scope creep, since it's the exact code path this fix touches. Tests: `test_render_jinja_strict_undefined_raises_on_typo` (the helper itself); `test_build_files_global_preflight_catches_undefined_reference` + `test_build_files_stack_preflight_catches_undefined_reference` (`tests/test_init_scaffolding.py`) inject an undefined-reference template via the existing `monkeypatch.setattr(scaffold, "_template", ...)` pattern (mirroring `test_build_files_guard_rejects_global_without_shared_vars`) and assert the clean `SystemExit` at each of the two named sites; 100% line+branch coverage confirmed for `scaffold.py` against just this file's tests. `docs/SPEC.md` S19 updated. Owning SPEC section: S3.2 (render pipeline); `ciu init`'s own S19 |
| CIU-82 | dstdns vendors a second implementation of a read CIU-75 moved: `scripts/ciu/workspace_env.py`, a stub that parses `ciu.env` into a dict, reachable as `ciu.workspace_env` only inside the test-runner container (which puts `scripts/` on `PYTHONPATH` and has no real `ciu` wheel). It and its sibling `scripts/ciu/config_constants.py` are read by `scripts/config_helper.py:30,31` (`DOCKER_NETWORK_INTERNAL`) and `scripts/url_builder.py:17,18` (`REPO_ROOT`), so both hold identity CIU no longer consults and nothing detects the drift | Medium | OPEN — filed by ciu-P42 (CIU-75), 2026-08-31; re-audited at `dstdns@96fcf762`. Not ciu's code to change: the estate rule files a consumer finding in the CONSUMER's backlog, and this entry exists so the notification is not lost between repos. Full caller inventory, the two pre-existing broken imports found in the same sweep, and the six `source ciu.env` shell sites are in the detail entry below and in `docs/CONSUMERS.md` §11b |
| CIU-83 | ciu-P43's four items (CIU-77/79/80/81) landed with SPEC/CONSUMERS/CONFIG updates but no `CHANGES.md` entry at all — `git show 815c50d6 --stat` does not list the file. `## [7.7.0]` is the section all five of this checkpoint's items ship in, so as it stands the release notes describe CIU-75 only, while CIU-79 is BREAKING by ciu-P43's own merge message: two breaking changes, one announced | Medium | FIXED — controller, 2026-08-31, before tagging 7.7.0. `## [7.7.0]`'s opening blockquote now names CIU-79 as the release's second breaking change; the Adoption/Migration Notes section gained a CIU-79 paragraph; `### Changed` gained CIU-79's entry, `### Added` gained CIU-80's, `### Fixed` (new) gained CIU-81's and CIU-77's, and `### Documentation`/`### Testing` gained supplementary bullets for all four — matching the depth already present for CIU-75, drawn from ciu-P43's merge commit (`815c50d6`) and each item's backlog row |
| CIU-84 | `ciu check --json` writes `[INFO]` to stdout before the JSON document, breaking a naive `\| jq` — S13.4a and `_emit_check_report`'s docstring both say the document is the only thing the action writes, and `action_check` honors that, but `_run` reaches it past `deploy.py:4322`'s unconditional `info(f"Active service profile(s): …")` | Medium | FIXED — ciu-P44. Full sweep of the reachable call graph from `_run`'s check-action branch, as the row asked, not a one-line patch: (1) `_run` itself had FOUR unconditional `info()` calls reachable ahead of dispatch/during it (`Active service profile(s)`, `No action specified; defaulting to --deploy`, the S7.7 health-gate note, `>>> action: {action}`) — all four now route through a new local `_run_info` closure that prints to stderr instead whenever `--json`/`--format json` is set, rather than tracking exactly which action combination would reach the document (the simpler "no `_run`-level prose on stdout under a json-shaped flag, full stop" invariant is also the more robust one). (2) A SECOND class, in a different module, found by tracing `action_check`'s own `--live` branch into `provisioning.probe_ref`/`_probe_stack`: two unconditional `[WARN]` deprecation notices (the `one_shot`/`:completed` migration warnings, V8-PREP-5) printed straight to stdout with no `json_output` awareness at all — `probe_ref`/`_probe_stack` don't take that parameter, so both were moved to stderr UNCONDITIONALLY (matching CIU-75/CIU-62's own "STDERR, not a style preference" idiom) rather than threading a new parameter through a probe layer that has no other json-mode concept. (3) `action_check` itself, `_check_stack_config`, `_check_hooks_for_stack`, `render_selected_stacks`, and everything else in between were re-verified clean (either no raw stdout write, or already gated through `say`/`complain`) — `bootstrap_workspace_env`'s own deprecation-notice path (CIU-75) was independently confirmed to already route to stderr unconditionally, not touched. (4) While correcting SPEC S13.4a's own text (which had documented the pre-fix leak as INTENDED — "the orchestrator's own `[INFO]` lines still precede the document on stdout, exactly as for `ciu graph --format json`"), found that same sentence named a second, real sibling leak: `ciu graph --format json` shares the identical `_run`-level defect (S13.5's own "only the graph itself goes to stdout" claim was equally false for the same reason). `_run_info`'s gate now also covers `graph_format == "json"`. A THIRD, narrower gap surfaced by the same check — `action_graph`'s OWN internal `info()`/`error()` calls (not `_run`-level) are still ungated — is a separate surface (different function, needs its own parameter threading) and is filed as **CIU-86** rather than folded in here. Test: `test_check_json_stdout_is_exactly_one_json_document` (`test_ciu_deploy_direct79.py`) drives `deploy._run(["--check", "--json"])` end to end with the REAL `action_check` (not mocked) against an empty selection and asserts `json.loads()` on the ENTIRE captured stdout succeeds — the row's own required oracle, not a substring check — plus `test_run_info_routes_to_stderr_under_json_output` (the narrower `_run_info` unit proof) and five existing `test_ciu_provisioning.py` tests updated from asserting `[WARN]` in stdout to asserting it in stderr (and absent from stdout) for the `_probe_stack` fix. SPEC S13.4a's stale "[INFO] lines still precede the document" sentence corrected; S13.5 cross-referenced |
| CIU-85 | `worktree._clean_in` (`:1267`) builds its child environment as `dict(os.environ)` + the target's identity, without the `_CIU_IDENTITY_ENV_KEYS` strip its two siblings (`_sanitized_target_env:2962`, `_resolve_budget_candidates:4406`) perform — so the caller's `CIU_SERVICES_PROFILE`, which is in that tuple but is NOT an overlay fact and therefore not in the overwrite, leaks into the child `ciu clean`. Separately, `PUBLIC_FQDN` is missing from `_CIU_IDENTITY_ENV_KEYS` despite being one of the six identity facts since CIU-47 | Low | FIXED — ciu-P44. `_CIU_IDENTITY_ENV_KEYS` now derives its identity half from `GENERATED_FACT_ENV_KEYS.values()` (`workspace_env.py`'s canonical fact->env-name table), adding `PUBLIC_FQDN` by construction, plus the one hand-added non-fact member `CIU_SERVICES_PROFILE` — replacing the old six-item hand-written literal that had drifted. `_clean_in` now builds `{k: v for k, v in os.environ.items() if k not in _CIU_IDENTITY_ENV_KEYS}` before overlaying `identity`, matching `_sanitized_target_env`/`_resolve_budget_candidates`. A THIRD sibling builder found during the fix, `_generate_env_in` (`:1221`, strips before running `ciu env generate`), carried its own separately hand-written six-key literal (identical to the pre-fix `_CIU_IDENTITY_ENV_KEYS`, so also missing `PUBLIC_FQDN`) — not named in this row originally, but the same class of drift and the same one-line fix, so folded in rather than left half-done: it now references `_CIU_IDENTITY_ENV_KEYS` too. Tests: `test_identity_env_keys_match_the_canonical_fact_table_plus_profile` pins the derivation; `test_clean_strips_the_callers_service_profile_selection` is the controlled-wrong-implementation proof for the `CIU_SERVICES_PROFILE` leak (reverting the strip makes it fail); `test_clean_strips_a_stale_ambient_public_fqdn_not_carried_by_target` proves an FQDN-less target no longer inherits the caller's stale `PUBLIC_FQDN` (all three in `tests/tests/test_ciu_worktree.py`, `TestWorktreeSubprocessEnvironment`); `test_generate_env_strips_primary_instance_identity`'s existing `_IDENTITY_KEYS` fixture gained `PUBLIC_FQDN`, extending its existing loop assertion for free. SPEC S16.6 updated to name the same seven keys (six derived + one hand-added) instead of the old hand-listed six |
| CIU-86 | `action_graph`'s own `info()`/`error()` calls (`deploy.py`'s `"No stacks with requires/provides — nothing to graph"` note and the two `validate_stack_shape`/`validate_stack_provisioning` error paths) write unconditionally to stdout via `deploy.py`'s plain `print(..., flush=True)` helpers, with no gate on `fmt == "json"` — so `ciu graph --format json`'s own docstring claim ("diagnostics go to the logger (stderr); only the graph itself goes to stdout", matching SPEC S13.5) is false on any of those three paths, though the common "the graph rendered cleanly" case is unaffected since `print(provisioning_pkg.render_graph(...))` is the only stdout write on that path | Low | OPEN — found by ciu-P44 while sweeping CIU-84's stdout-purity class one layer up: SPEC S13.4a's own (now-corrected) text named `ciu graph --format json` as sharing the `_run`-level leak CIU-84 fixed ("exactly as for `ciu graph --format json`"), which led to checking S13.5's own claim against `action_graph`'s actual body. The `_run`-level half of this (the orchestrator's `>>> action: graph`/`Active service profile(s)` lines) IS fixed by CIU-84's `_run_info` gate, now also keyed on `graph_format == "json"` — this row is only the remaining, `action_graph`-internal half, which needs `fmt`/a `json_output`-shaped parameter threaded into `action_graph`'s own `info`/`error` calls (or their two call sites switched to a local gated closure, mirroring `action_check`'s own `say`/`complain`) plus tests pinning stderr for the two error paths and the empty-graph note |
The approved milestone decisions and serial package order are in
[`nyxloom-trove/decisions.md`](nyxloom-trove/decisions.md) and
[`nyxloom-trove/roadmap.md`](nyxloom-trove/roadmap.md).

## How issues get here

Capture the originating observation, then re-verify it against the provider and
the alleged consumer before treating it as a product requirement. Every open
entry must state:

1. the observed mechanism and a live or source-grounded reproduction;
2. why CIU, rather than a consumer, owns the behavior;
3. the public contract and refusal states;
4. behavioral oracles including a controlled wrong implementation; and
5. the SPEC owner.

Defaults follow the estate rule: derive a fact, read its authoritative source,
or fail. Do not use a literal or ambient value as a substitute for information
available from the selected worktree or its configuration.

## CIU-23 — withdraw PostgreSQL-specific data isolation

**Disposition:** WITHDRAWN on 2026-08-17. Package A removed the implementation
after re-verification disproved its consumer premise.

### What was claimed

CIU-23 claimed that dstdns's `scripts/schema-gate.sh` demonstrated the need for
a uniquely named database on a shared PostgreSQL server. CIU consequently
shipped:

- `worktree add --data-isolation <profile>`;
- `DataIsolationProvisioner` and a default `PostgresProvisioner`;
- `CIU_DATA_ISOLATION_ENTITY`, `CIU_DATA_ISOLATION_PROFILE`, and
  `CIU_DATA_ISOLATION_DSN`; and
- S16.2 create/drop ordering around `worktree rm`.

### Re-verification

The premise was false when the issue was filed. dstdns committed its schema
gate on 2026-08-10, one day before CIU-23, and that original commit explicitly
uses a disposable PostgreSQL container. It rejects a scratch database on the
existing instance because obtaining CREATEDB requires the PostgreSQL superuser
and would put gate activity and deployed data in one blast radius.

No estate consumer uses the CIU flag, provider classes, or emitted environment
fields. The implementation is not a general data-slot abstraction: its profile
is a local Docker container name, it assumes the `postgres` administrative
user, it creates/drops a database without applying consumer schema, and its DSN
does not establish the connectivity/authentication facts a consumer needs.

### Required withdrawal

Remove the CLI flag, provider protocol/default, env fields, create/drop paths,
tests that assert the withdrawn behavior, and S16.2 user-facing contract. Keep
the historical record in Git/release history and state plainly in migration
notes that the next release removes a recently shipped but unused API.

A future general provision/drop hook requires a real consumer and a new issue;
it must be project-declared rather than a PostgreSQL default embedded in CIU.

**SPEC ownership:** remove S16.2 and reserve no replacement behavior.

### CIU-34..38 detail: five asks from dstdns's configuration/landscape decision — OPEN

**Filed by:** dstdns controller session, 2026-08-19, out of the configuration /
landscape / remote-deployment decision recorded in
`dstdns/docs/spec-configuration-and-landscape.md` (D-094…D-101). Per this file's
rule the asks live here; dstdns keeps only the pointer (spec §11). None is a
defect in shipped behaviour — each is a capability the decided model needs from
its deploy tool. Verified against `docs/CONFIG.md` + `src/` before filing (a
feature dstdns has not adopted is not a feature ciu is missing).

**CIU-34 — `layout`.** **FIXED** on 2026-08-19 (ciu-P10): `[deploy.layouts.<name>]`
now names a host→bundles plan plus the deployment's `environment`
(closed `dev|test|staging|prod`, the durable home of the environment value —
dstdns D-105 Q2). `ciu up --layout <name>` resolves + validates the layout
(unknown layout / bad `environment` / unknown bundle / unknown host / empty
hosts table → tagged `[S7.5c]` abort before any transport opens), then drives
the SPEC-J push (S14.2) to each host in declaration order with
`CIU_SERVICES_PROFILE` set to the host's bundles and
`CIU_LAYOUT` / `CIU_LAYOUT_HOST` / `CIU_DEPLOY_ENVIRONMENT` exported to the
remote command; a host failure aborts naming the not-yet-deployed remainder.
`--layout` is mutually exclusive with `--host`/`--profile`/`--dir`/`--thin`/
`--bootstrap`/`--rollback` (prefix-aware, so `--profile=core` is caught too —
see checkpoint C below); `ciu layouts` lists declarations. Evidence:
`Layout`/`resolve_layout`/`list_layouts` in `src/ciu/deploy_pkg/layouts.py`
(18 model tests in `tests/tests/test_ciu_deploy_layouts.py`, 19 CLI tests in
`tests/tests/test_ciu_cli_layouts.py` — fake ssh seams only, no live
transport); venv run (`.venv/bin/python run-ciu-tests.py`), 100% line+branch
— the iteration signal, not the ship gate; tester-unified gate run by the
controller at checkpoint review. Docs: SPEC S7.5c, CONFIG.md
`[deploy.layouts.<name>]` section, CHANGES.md. **Checkpoint C review
(2026-08-20)** found and fixed 3 blocking findings against the original
ciu-P10 merge: an empty `bundles = []` list was accepted and resolved to
"deploy every phase" on the remote (`resolve_profiles`' empty-list fallback,
the same shape as the 2026-07-16 dstdns incident) instead of being refused;
the `--host`/`--profile` mutual-exclusion check missed the `--profile=core`
equals form and didn't guard `--dir`/`--thin`/`--bootstrap`/`--rollback` at
all; and the push implementation was duplicated between `--host` and
`--layout` (already drifted — the layout path lacked the `docker_optional`
advisory) and is now one shared `_push_host` helper. Pre-checkpoint-C baseline
was 16 model / 12 CLI tests, not the 14 model tests this row previously
claimed (the P10 LOG's own count of 13 CLI tests was also off by one — see
its appended correction note); the 18/19 above are current-tree totals after
checkpoint C's added tests.

**CIU-34 hotfix (ciu-P29, 2026-08-25) — the mutual-exclusion guard was
abbreviation-blind; `--layout=NAME` never dispatched.** A retrospective
adversarial review reproduced a **silent wrong-profile production deploy** in
already-released behaviour (layouts shipped v6.3.0; every release since is
affected). **The claim this row made above — "`--layout` is mutually exclusive
with `--host`/`--profile`/`--dir`/`--thin`/`--bootstrap`/`--rollback`
(prefix-aware, so `--profile=core` is caught too)" — was true only for exact
and `=` spellings and is corrected here.** Checkpoint C made the guard
prefix-aware for the `=` form but left it a denylist of EXACT flag names,
while the remote parser it exists to protect (`deploy.parse_args`) is built
without `allow_abbrev=False`, i.e. with argparse's default
`allow_abbrev=True`. An abbreviation therefore passed the local guard, was
forwarded verbatim after the layout's own `export CIU_SERVICES_PROFILE=...`,
and resolved on the remote: `ciu up --layout prod --prof=core` against a
3-host prod layout exited **0** having pushed to all three hosts, with
`backend` (bundles `db,worker-io`) deploying `core`. The guard now registers
the forbidden long options on a local parser with the SAME `allow_abbrev`
semantics the remote uses and lets argparse resolve the spelling before the
check runs — every abbreviation length covered by construction, the resolved
flags consumed rather than forwarded, the refusal naming the resolved flag and
landing before any inventory lookup (zero transport). Separately, the
`"--flag" in argv` verb-dispatch tests missed every `=` form:
`ciu up --layout=NAME` skipped the layout path entirely, and — worse, because
`ciu-deploy` declares `--host` for its help text but never reads it (S10.2) —
`ciu up --host=web` (and `down`/`health`/`render`) parsed cleanly and ran a
LOCAL deploy of the active profile instead of the intended SPEC-J push, exit 0
and silent. Dispatch is now exact-or-`=` on `--layout`/`--host`/`--dir` via
one shared `_flag_given` predicate, **plus argparse-resolved abbreviations for
`--host`** — see the round-2 correction immediately below. Evidence:
`_flag_given` / `_parse_layout_argv` in
`src/ciu/cli.py`; 96 added tests in `tests/tests/test_ciu_cli_layouts.py`
(19 → **115** in that file across both rounds, superseding the "19 CLI tests"
count stated in the checkpoint-C paragraph above), including the review's exact
3-host reproduction
asserting ZERO transport calls, every abbreviation length of all six forbidden
flags in both forms, and `--layout=`/`--host=`/`--dir=` producing push
sequences identical to their space forms; venv run
(`.venv/bin/python run-ciu-tests.py`), 2714 passed, 100% line+branch — the
iteration signal, not the ship gate. Docs: SPEC S7.5c + S10.4, CHANGES.md.

**CIU-34 hotfix, round 2 (adversarial review REJECT, same day).** The round-1
fix above made dispatch exact-or-`=` and documented the claim *"an abbreviation
still fails loudly at whichever parser it reaches, so it can never deploy the
wrong thing."* **That claim was FALSE for `--host`, and this entry corrects
it.** Because `deploy.py` declares `--host` and reads it nowhere, `--hos=edge-a`
/ `--ho=edge-a` / `--hos edge-a` / `--ho edge-a` all parsed CLEANLY downstream,
had the host silently discarded, and ran a LOCAL deploy — **16 of the 20
verb × spelling combinations across `up`/`down`/`health`/`render` returned exit
0 having contacted zero remote hosts.** The `=`-only fix did not close the
hazard, it narrowed it. `--host` dispatch is now abbreviation-aware, resolved
by argparse (registering all three dispatch modifiers on one parser, so an
abbreviation ambiguous BETWEEN them would stay loudly ambiguous rather than be
claimed by whichever branch is tested first). `--layout`/`--dir` remain
exact-or-`=` **deliberately, and this is the corrected rule**: the question is
never "is an abbreviation possible" but "what does the fall-through parser DO
with it" — `ciu-deploy` has no `--layout`/`--dir`, so `--lay x` and `--di=/srv`
are `unrecognized arguments` and `--d /srv` is genuinely ambiguous there
against `--define-root PATH`; all fail loudly, and widening them would invent a
divergence rather than close one. That premise is pinned against the REAL
`deploy.parse_args` by
`test_dispatch_abbreviation_premise_against_the_real_deploy_parser`, plus a
distinct-second-character invariant test so a future colliding flag fails at
authoring time. The round-2 tests wrap the genuine `deploy.main`/`parse_args`
rather than a stub — a stubbed probe is vacuous here, since a dispatch
regression would hit the stub instead of performing the real local deploy, and
would mask both the bug and the fix.

**Follow-up filed by the round-2 review — `deploy.py` declares a flag it never
reads (root cause of the above), OPEN.** `deploy.py:3592` registers
`--host NAME` with the help text "Remote host name (from hosts inventory):
push-deploy via SSH (SPEC J)", and **nothing in the file ever reads
`args.host`** (grep confirms zero consumers). It exists only so the flag shows
up in `ciu-deploy --help`. That is what turned every `--host` dispatch miss
from a loud error into a silent local deploy: a "lying" flag that documents a
behaviour it does not implement. Fixing the DISPATCH layer in `cli.py` closes
the hazard for the real invocation surface — the `ciu` verb CLI — which is why
ciu-P29 stopped there (`deploy.py` was `scope.forbid` for that package). The
dead flag itself remains, and anyone invoking `ciu-deploy` directly still gets
a silently-ignored `--host`. A small future cleanup package should either make
`ciu-deploy --host` do what its help says or remove the declaration and point
at `ciu up --host`; it should NOT simply be deleted without checking S10.2's
help-surface contract. Lower urgency than the dispatch hazard, but it is the
root cause and should not be lost.

**Follow-up spotted, NOT fixed here (candidate for its own entry).** `ciu
bake`'s `--profile`-vs-positional-targets mutual exclusion (`_bake` in
`src/ciu/cli.py`) uses the same exact-or-`=` predicate this hotfix just
replaced in the layout guard: `any(a == "--profile" or
a.startswith("--profile=") ...)`. `ciu bake --prof=core web` therefore does
NOT trip the conflict; `--prof=core` is instead treated as a positional build
TARGET and handed to `docker buildx bake`. That is a loud failure rather than
a silent wrong deploy, and `bake` is outside ciu-P29's scope, so it was left
alone — but it is the same latent class and should be closed with the same
argparse-resolution approach.

**CIU-35 — host-scoped local secrets.** **FIXED** on 2026-08-19 (ciu-P11):
`[deploy.hosts.<h>.secrets]` now holds `ASK_EXTERNAL`/`GEN_LOCAL` entries
(SSH bootstrap key, Tailscale single-use authkey) resolvable *before* any
Vault exists on the target, later movable to Vault by the existing
directives. Entries are parsed with the existing `directives.parse_value`
(read-only) and only the two kinds are accepted at host scope — any other
directive is a tagged `[S14.3a]` error naming host+entry+reason.
`materialize_host_secrets` persists under the project store's
`hosts/<host>/<entry_name>` namespace (0700 dirs, atomic write, flock; the
per-stack global-uniqueness rule S4.6 deliberately does not apply across host
namespaces). `get_host` validates the subtable but pops it before return —
transport callers never see directives. `ciu host-secrets <host>
[--materialize | --list | --path NAME] [-y]` is explicit-only and never
prints values; nothing materializes implicitly inside `ssh`/`up --host`.
Evidence: 32 tests in `tests/tests/test_ciu_host_secrets.py` (fake seams,
tmp_path stores; closed-kind refusal, pop-before-return, store namespace,
resolution order, no-value-printing, no implicit materialization); venv run
(`.venv/bin/python run-ciu-tests.py`), 100% line+branch — the iteration
signal, not the ship gate; tester-unified gate run by the controller at
checkpoint review. Docs: SPEC S14.3a, CONFIG.md section + pre-Vault rationale
+ worked example, CHANGES.md. Also documented: the `CIU_SECRET_<NAME>` env
override is NOT host-scoped — the same exported value lands in every host's
namespace (known limitation, unsafe for single-use keys). **Checkpoint C
review (2026-08-20)** found and fixed 1 blocking finding (P11-B1): a pasted
value instead of a directive (e.g. a Tailscale authkey typo'd into
`[deploy.hosts.<h>.secrets]`) flowed verbatim into
`directives.parse_value`'s "[S4.2] Unknown directive '<token>'" message,
which `hosts.py` re-raised unchanged and the CLI printed to stderr — from
every `get_host()` caller, not just `ciu host-secrets`. `hosts.py` now raises
a fixed, non-leaking `[S14.3a]` reason instead of interpolating the upstream
message. Pre-checkpoint-C baseline was 31 tests, not the 30 this row
previously claimed (the P11 LOG's own count of 31 was already correct); the
32 above is the current-tree total after checkpoint C's added test.

**CIU-36 — `landscape_id` dimension.** A first-class identity value (beside
project/instance) exposed to templates and to S16 worktree instances, so a
consumer can render its Consul KV root (`dstdns/<landscape_id>/…`) and mesh ACL
tags from one source. **FIXED** on 2026-08-19 (ciu-P08): `[deploy].landscape_id`
is now validated as a DNS-label-safe slug (`^[a-z][a-z0-9-]{0,62}$`) on the
final merged global config (incl. the worktree overlay) with a tagged S3.11
abort, and documented in CONFIG.md + SPEC.md S3.11 with an explicit
disambiguation from the configfile-context `instance_id` (a per-service replica
index, not the workspace `INSTANCE_ID`). Evidence: 6 behavioral tests in
`tests/tests/test_ciu_config_model_landscape.py`; gate 100% line+branch.
Templates read it via `{{ deploy.landscape_id }}` with no plumbing change.

**CIU-37 — schema-validated render.** `[<root>.<service>.configfile.app]` (or the
render step) accepts `schema = "path/to/config-schema.json"` and validates the
rendered TOML against it, failing the render with the key path — the app's
generated JSON schema is the source, ciu only checks. **FIXED** on 2026-08-19
(ciu-P09): optional `schema` key per configfile entry, validated against the
app's JSON Schema (Draft 2020-12, TOML targets only) immediately after the
atomic write and before mount emission; violation names service, configfile
(per-instance suffix when `instances > 1`), and key path, and removes the
invalid rendered file. `jsonschema` is an optional extra (`ciu[schema]`);
declared schemas fail loudly when it is absent, never silently skip. Evidence:
10 behavioral tests in `tests/tests/test_ciu_configfile_schema.py`; gate 100%
line+branch. Runs on the up/dev path (engine step 12) — `ciu render` renders
TOML configs only; a dedicated `ciu render --configfiles` verb remains a
possible follow-up candidate (not in this package).

**CIU-38 — per-service AppRole.** Vault stack provisions one AppRole + policy per
declared service and a template helper delivers `role_id` + a `secret_id` file
path into that service's rendered config (no secret VALUES rendered). dstdns
decided runtime Vault fetch (SM2, D-098); if this lands upstream dstdns consumes
it, otherwise dstdns builds it locally and notes the delta here.

### CIU-39 detail — `verified-match` is unreachable for vendor images

Renumbered from main's CIU-28 at the 2026-08-19 merge (assay-side references
updated the same day). Mechanism (source: `deploy.py` `_provenance_result`):
a container's provenance status comes from its
`org.opencontainers.image.revision` label vs the checkout commit; an image
ciu never built (pulled from a vendor registry — dstdns runs vault,
authentik, consul this way) has no such label, is `unlabelled`, and never
contributes `match`. A deployment whose containers are all vendor artifacts
can therefore never leave `not-verified-no-evidence`: provenance has no
disposition that says "this image is a pinned vendor artifact, expected
unlabelled". Blocks assay B004 (coverage-judge qualification on pinned
artifacts).

This entry predates this tracker's five-point format; before it is carved,
capture the live reproduction (one `ciu provenance` run against an
all-vendor deployment) and decide the contract shape — candidates: a
declared vendor-baseline source (digest pin file) provenance can verify
against, or an explicit per-container `vendor-pinned` disposition distinct
from `unlabelled` — per [How issues get here](#how-issues-get-here).

**SPEC ownership:** S17 (provenance semantics).

### CIU-39 disposition — FIXED 2026-08-22

The live reproduction was never captured against a running all-vendor
deployment (no docker daemon in the carving environment); the contract was
decided from source + the assay B004 requirements instead, and the
controlled-wrong oracles pin the semantics. Operator decisions: declared
REFERENCES (not a digest pin file — DESIGN-GUIDE records why), and a schema
bump to `schema_version: 2` with the seven CIU-20 fixtures kept frozen as the
historical v1 grammar record and nine new v2 fixtures carrying the
assertions. `[deploy.provenance] vendor_images`; reference equality →
`vendor-pinned`; same canonical image NAME at another reference → vendor drift
(`mismatch`; references compared on Docker-canonical spellings); undeclared
unlabelled stays `unlabelled` in the document and contributes nothing — a
forgotten bake stays VISIBLE per container, while the overall flips from
no-evidence-WARN to green exactly as an own-image match always made it.
Malformed declarations refuse exit 2 — a silently ignored declaration would certify
exactly the deployment it was written to vouch for. `verified-match` is now
reachable for all-vendor deployments; B004's remaining blocker is assay-side
(`PROVENANCE_UNVERIFIED`, A-276).

## CIU-25 — stale worktree/stack detection and reap

**Status:** FIXED (2026-08-25, ciu-P26 + ciu-P27; git half 2026-08-22,
hotfixed by ciu-P28). Read the two 2026-08-25 sections below together — the
substrate and the verb are one answer, filed as two packages.

Originally PARTIAL (2026-08-22) — the GIT half shipped as
`ciu worktree branches` (SPEC S16.8, capability `worktree.branches.v1`):
a closed seven-category survey (base/mainline/current/managed-instance/
prunable/merged-dirty/unmerged) with per-branch attributes (#changed files
vs the merge-base, ahead/behind, last-commit age, dirty, ciu-instance
linkage), and `-y` removing EXACTLY the Git-provable prunable category
(worktree remove + `branch -d`, both re-verified by Git itself). No age
heuristic, no process inference — the constraints below are honored by
construction. The Docker-resource half (containers/volumes of a crashed
instance) remains OPEN with the same ownership/lease precondition.

**HOTFIX 2026-08-25 (ciu-P28)** — the git half as first shipped (v7.0.0,
`c92377fb`) violated the "must not destroy resources" constraint above in
four ways, each reproduced end-to-end by two independent retrospective
adversarial reviews: `-y` bare-removed the checkout of a LIVE CIU-managed
instance with no `ciu clean` first (the exact orphaned-container /
stranded-root-owned-`vol-*` outcome this entry exists to prevent); it judged
mergedness against the invoking linked worktree's HEAD instead of the
primary's, falsely reporting merged branches unmerged AFTER destroying their
checkouts; it self-destructed when invoked from a checkout whose own branch
was prunable, aborting the whole run with no document and silently skipping
every later candidate; and `--json` exited 0 on a partial prune. Fixed by
the new `managed-instance` category (never pruned — use `ciu worktree rm`),
a primary-worktree-rooted destructive pass with a third read-only
HEAD pre-check, an invoking-checkout guard plus a no-escape per-branch loop,
and one hoisted exit-code decision. Document `schema_version` is now `2`.
See `nyxloom-trove/reports/ciu-P28-hotfix-worktree-branches-prune-safety-LOG.md`.
**The Docker-resource half must not repeat this shape**: any future reap that
touches a MANAGED instance goes through clean-then-remove, never a bare
resource deletion.

`worktree rm` cleans before removing a checkout when it runs, but a crashed
dispatcher or forgotten teardown can leave containers and volumes running.
The old proposal to infer staleness from process lifetime or elapsed time is
not grounded: long-lived worktrees can be legitimate, and a missing process is
not proof that an instance is abandoned.

Before carving this issue, define an explicit ownership/lease signal and a
transactional reap contract. A future implementation must distinguish at least:

- registered and operator-owned;
- registered with an expired explicit lease;
- Git registration present but checkout path missing;
- Docker resources present but no CIU identity record; and
- a partially failed earlier cleanup.

It must not destroy resources based only on age, basename similarity, or a
missing local process. CIU-28's identity record is a prerequisite substrate,
not itself permission to reap.

**SUBSTRATE SHIPPED 2026-08-25 (ciu-P26, SPEC S16.9)** — the "explicit
ownership/lease signal" this entry demanded now exists, and nothing else does.
Shipped: record schema **v2**, adding one optional `lease` field
(`holder`/`acquired_at_utc`/`renewed_at_utc`/`expires_at_utc`/`mode`) whose
closed `mode` vocabulary GOVERNS the expiry — `held` REQUIRES
`expires_at_utc`, `perpetual` FORBIDS it, so "long-lived on purpose" is a
first-class declared state and never something an age heuristic has to guess
(the exact hazard this entry names). Naive, offset-less lease timestamps are
refused rather than parsed as local time. v1 records read forever and a READ
never rewrites one — only an operation that legitimately mutates the lease
writes v2. Policy key `[ciu.worktree].lease_ttl_hours`, with **no default**:
absent means no lease is ever acquired, so nothing already running gains new
expiry risk. `ciu up` acquires/renews for a MANAGED instance only (never a
PRIMARY checkout, never a dry run), BEFORE the compose call; `ciu clean` and
`ciu worktree rm` clear it **on success only** — a failed teardown, `--force`
included, keeps the claim, because erasing it would manufacture "unowned" out
of "unknown". `ciu worktree lease LOGICAL (--extend D | --perpetual |
--release)` is the explicit operator verb and queries no Docker state, so it
works on a stopped instance. Ownership labels `ciu.instance` /
`ciu.repo-root` are stamped on every container, volume and network a managed
`ciu up` creates, read from that workspace's own `ciu.env` by exact path
(never ambient — CIU-41). See
`nyxloom-trove/reports/ciu-P26-ciu25-lease-schema-and-labels-LOG.md`.

**REAP VERB SHIPPED 2026-08-25 (ciu-P27, SPEC S16.10)** — `ciu worktree reap
[-y] [--category C1,C2] [--dry-run] [--json]`, capability
`worktree.reap.v1` (shipped alongside `worktree.lease.v1`, which ciu-P26
implemented but left unadvertised). **This entry is only FIXED when read
together with ciu-P26**: the reap verb is exactly as safe as the lease/label
substrate it consults, and a reader following ciu-P27's evidence alone could
not reconstruct where its proof comes from.

The five states this entry demanded now map onto a closed SEVEN-category
partition, every group landing in exactly one:

| Demanded state | Shipped category | Destroyed by `-y`? |
|---|---|---|
| registered and operator-owned | `owned` | never |
| registered with an expired explicit lease | `lease-expired` | yes |
| Git registration present but checkout path missing | `checkout-missing` | yes |
| Docker resources present but no CIU identity record | `orphaned` / `unattributable` | `orphaned` yes; `unattributable` **never** |
| a partially failed earlier cleanup | `partial-cleanup` | yes |
| *(added)* attribution unresolvable | `ambiguous` | **never** |

The constraint "must not destroy resources based only on age, basename
similarity, or a missing local process" is honored BY CONSTRUCTION: none of
those three is an input to any decision in the module. Proven negatively by
test — a year-old lease-less instance, a fully-stopped instance, and two
worktrees with the IDENTICAL directory basename and one-character-apart
instance ids all survive `-y` untouched.

`unattributable` and `ambiguous` are not merely off by default, they are
**structurally unreachable**: `--category` refuses their names (exit 2)
rather than selecting them, so no flag combination destroys a group CIU
cannot attribute. The ciu-P28 lesson is binding: a group whose checkout
survives is disposed of by `ciu clean -y` run inside it, never by a bare
resource deletion, and a clean that fails is reported rather than
second-guessed.

**Two named narrowings from the carve, both deliberate.**
`partial-cleanup` was carved as "recovery-required OR a group with some (not
all) of its resources present OR a previously-failed reap". The middle clause
is WITHDRAWN as undecidable and unsafe — nothing records what "all" would be
for a group, and `ciu down` preserves volumes on purpose, so an owned,
valid-leased, merely-stopped instance would have qualified and lost its
data; the third is not persisted anywhere. Only the record's own DECLARED
`state: "recovery-required"` remains. Separately, `checkout-missing` is
decided from the group's `ciu.repo-root` label rather than from a record,
because the instance record lives INSIDE the checkout and a vanished checkout
takes its record with it.

**Still open (small, named).** `ciu up --shipped` leases but is not
label-stamped — a generated fragment under a vendored stack would survive
every `clean`, which skips `reset_service` for shipped stacks; closing it
needs a shipped-stack artifact lifecycle. Consequence for reap: a shipped
stack's resources are attributed by identity-form compose project name only,
and fall to `unattributable` when the project is config-derived. Also by
design: the identity network of an instance whose checkout AND record are
both gone carries no label (created by `ciu env generate`, outside compose)
and is out of reap's reach — remove it by hand.

**SPEC ownership:** S16.8 (branch hygiene), S16.9 (lease + labels), S16.10
(the reap verb).

## CIU-26 — deferred PostgreSQL proof

**Disposition:** OBSOLETE on 2026-08-17 because CIU-23 was withdrawn.

CIU-26 asked for a live PostgreSQL proof of the default provider. Building that
lane would validate an unused, incorrectly grounded abstraction. Package A
removed the provider. CIU-26 is therefore OBSOLETE, not FIXED: no
live-provider claim remains to prove.

## CIU-28 — automation-safe worktree identity and lifecycle

**Reported by:** nyxloom/vbpub, 2026-08-17, while qualifying CIU as an automated
environment provider.

### Observed gap

The current `worktree add NAME` conflates a logical identity, branch, directory
basename, and lookup key. It always creates a new branch, cannot adopt or resume
an existing checkout, and persists no durable lifecycle record. Runtime
`INSTANCE_ID` is only six SHA-256 hex characters derived from physical path;
collision checking currently occurs only in a later S16.3 deployment-cap path.

### Required contract

1. Preserve simple `worktree add NAME` behavior for people while separating
   logical name, display name, branch, Git worktree path, and CIU-root offset in
   the internal/public model.
2. Persist a schema-versioned, atomic, non-secret record at
   `<target-ciu-root>/ciu.worktree-instance.json`. It owns logical identity,
   allocation timestamp, requested Git/path facts, runtime identity, selected
   profile/shared-infra presence, and one closed lifecycle state:
   `allocating`, `ready`, or `recovery-required`. Current HEAD is derived, not
   frozen in the record. Credentials and DSNs are forbidden.
3. Add a sparse, non-secret, gitignored
   `<target-ciu-root>/ciu.global.worktree.toml.j2` merged after the committed
   global defaults and project override. It owns durable per-worktree global
   configuration, including selected service profiles and shared-infrastructure
   intent, and survives both `ciu clean` and `ciu env generate`. `ciu.env`
   returns to generated machine/runtime facts only; the lifecycle record does
   not become a second authority for overlay values.
4. Logical names are unique within one Git worktree family; independent clones
   may reuse them. Host runtime/network identities must still reject collision.
5. Support explicit create-new, adopt-existing, and idempotent ensure/resume.
   Create refuses an occupied identity before side effects. Adopt is the only
   operation allowed to take ownership of unmanaged state. Ensure reuses an
   exact ready match and completes only a mechanically recognizable interrupted
   CIU-owned allocation. Mismatch refuses; repair is explicit.
6. Generated display names use UTC
   `<prefix>-<YYYYMMDD_HHMMSS>-<feature-description>`. Prefix means project OR
   component, supplied by the caller. Generated branch and directory basename
   are exactly equal. Allocate under the Git-family lock and add a suffix only
   for an actual same-second collision. Resume retains the original name.
7. Before Git/env side effects, reject conflicting logical identity, target
   path, or active branch. After generating the target's own `ciu.env` but
   before marking ready, reject duplicate `INSTANCE_ID` or network identity.
   A partial attempt remains inspectable and cannot masquerade as ready.
8. Lifecycle operations provide schema-versioned JSON with closed status and
   recovery vocabularies. Human output remains presentation only.

### Behavioral oracles

- Two generated allocations sharing an injected UTC second receive distinct
  names under one family lock; a retry of either resolves its original record.
- Matching ensure creates no branch, directory, env, or record write. A
  one-field branch/path/logical mismatch refuses.
- A forced runtime-hash collision refuses before ready and leaves a declared
  recovery state, never two usable instances with one network identity.
- An existing unmanaged checkout is refused by ensure and accepted only by
  explicit adopt after all facts validate.
- Nested CIU roots retain the exact Git-root-to-CIU-root offset in every
  checkout; no code treats the Git root as the CIU root by convenience.
- Regenerating `ciu.env` and running `ciu clean` preserve the worktree overlay;
  the selected profiles/shared-infrastructure intent still resolve from it.

**SPEC ownership:** replace/extend S16 identity and lifecycle.

## CIU-29 — structured control and exact execution

**Reported by:** nyxloom/vbpub, 2026-08-17, from the same qualification.

### Observed gap

Lifecycle output is prose-only, there is no exact inspect result, and automation
must source `ciu.env` or infer features from SemVer. There is also no operation
that explicitly starts a selected worktree instance or executes in its exact
local/container environment without inherited sibling-root contamination.

### Required contract

1. Add versioned JSON for lifecycle, list, inspect, and remove. Report logical
   identity, display/branch/path facts, CIU-root offset, primary/detached state,
   lifecycle state, current Git revision, runtime ID/network, selected profile,
   and non-secret optional-feature presence. Partial failures name exact
   retained resources and a closed recovery status.
2. Add a versioned `ciu capabilities --json` document with closed identifiers
   for public machine contracts. Its schema version is independent of package
   SemVer. Consumers allowlist capabilities; unknown identifiers are not
   interpreted as compatible.
3. Add `ciu worktree up <logical-id>`. It resolves one managed record, parses
   that target's `ciu.env` by exact path, replaces conflicting inherited CIU
   root/identity variables, and invokes CIU's existing up path for that target.
   It is the explicit start operation.
4. Add `ciu worktree exec <logical-id> -- <argv>` for non-container consumers.
   It uses the selected checkout/CIU root and exact target env, no shell, and
   propagates the child exit code. It never starts or cleans anything.
5. Add `ciu worktree exec <logical-id> --target <alias> -- <argv>` for container
   consumers. Aliases are declared in project config and are the only
   automation selection surface; arbitrary service selection is forbidden.
   Each alias declares an exact stack, service, workdir, and optional
   `requires_worktree_mount = false` (default is true).
6. Resolve a target against the selected instance's exact Compose project,
   service label, and own network. Zero or multiple running containers refuse.
   When mount verification is enabled, inspect Docker mounts and prove the
   selected worktree maps to the declared container workdir before execution.
   Invoke `docker exec` without a shell and propagate the exact exit code.
7. No exec mode implicitly invokes `up`. Nyxloom requires a container alias for
   cockpit-doctrine projects; CIU retains local exec for non-container users.
8. Every command accepts/resolves an explicit CIU root and reports it in JSON.
   Ambient `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, network, instance, and profile
   values from a sibling checkout cannot redirect the selected operation.

### Boundary with Assay and workflow tools

CIU owns WHERE: exact checkout, generated environment, stack/container, and
argv transport. Assay owns evidence judgment. Nyxloom owns workflow policy and
the decision to require container targets. CIU neither imports Assay nor parses
its verdict. A caller may run a pinned Assay artifact as the child argv.

### Behavioral oracles

- Similar basenames and stale ambient root variables cannot redirect inspect,
  up, local exec, or container exec.
- Missing/malformed record or env refuses before any child or Docker mutation.
- Local and container argv preserve argument boundaries and representative exit
  codes without `shell=True`.
- Target alias absence, zero/multiple label matches, wrong instance network,
  and wrong/missing worktree mount each refuse before payload execution.
- Capability output changes only with a reviewed public contract and contains
  no inferred future compatibility.

**SPEC ownership:** S16 machine interface and versioned capability schema; S17
continues to own provenance semantics.

## CIU-41 — `ciu env generate` silently inherits an ambient `DOCKER_NETWORK_INTERNAL`

**Filed by:** dstdns P111 (auth-config-cutover, Mode-B live pass), 2026-08-20.
Provenance: `dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §9 F2 and §11
item 5. Reproduced live, then source-confirmed against `src/ciu/workspace_env.py`
before filing.

### Observed mechanism and reproduction

In the dstdns devcontainer, `~/.bashrc` sources the MAIN checkout's `ciu.env`,
so every shell — interactive and agent alike — carries
`DOCKER_NETWORK_INTERNAL=dstdns-98535c-network`. Running `ciu env generate` in a
fresh worktree (`/workspaces/dstdns/.worktrees/p111-auth-cutover`):

- derived a correct fresh `INSTANCE_ID` (`e893b0`) from the physical-path hash;
- but **kept the ambient network name**: the generated `ciu.env` carried the
  MAIN stack's `dstdns-98535c-network` instead of the derived
  `p111-auth-cutover-e893b0-network`.

Net effect: an intended Mode-B (own-network) instance silently becomes a Mode-A
attach — containers running worktree code join the main stack's network. The
SAME run handles exactly this contamination species correctly for
`PHYSICAL_REPO_ROOT` (S2.7 refined precedence: a pre-set env value wins only
when consistent with the mountinfo-derived value, else the derived value is
used and a stderr warning names the ignored ambient one), so the handling is
internally inconsistent.

Source mechanism: `_compute_network_name` (`src/ciu/workspace_env.py`, ~line
563) returns
`os.environ.get("DOCKER_NETWORK_INTERNAL", network_name)` — the ambient value
wins unconditionally, with no consistency check and no warning. Parallel bare
env reads exist at ~761/931/974. This is the **masked default** anti-pattern
(dstdns AGENTS §4.2a #3): invisible in every interactive shell because the
ambient value is correct for the main checkout, surfacing only in the one
context where it matters — a generate for a different workspace — which is
exactly the non-interactive agent context. It is also the same defect family
S2.7's docstring records for `PHYSICAL_REPO_ROOT` (the 2026-07 dstdns→nyxloom
leak); the network name never received the fix.

**Workaround used in the field:**
`env -u DOCKER_NETWORK_INTERNAL -u INSTANCE_ID -u PHYSICAL_REPO_ROOT -u REPO_ROOT -u REPO_NAME ciu env generate`
→ correct `p111-auth-cutover-e893b0-network`.

**Second reproduction (2026-08-20, dstdns P112 Mode-B):** verbatim recurrence in the very next package — `ciu env generate` inherited main's `DOCKER_NETWORK_INTERNAL` and would have silently produced a Mode-A stack; caught only because the P111 write-up primed the operator's agent to check. Two consecutive packages → priority bump warranted.

### Why CIU owns it

`env generate` is the identity-computation verb; its output is the record every
later ciu command trusts (S16's worktree cross-checks compare the record
AGAINST `ciu.env`, so a contaminated generate poisons the identity at birth and
the cross-checks then defend the wrong value). A consumer cannot fix this by
documentation: any consumer whose login shell sources a checkout's `ciu.env` —
the documented convenience pattern — has the ambient value in every derived
shell.

### Proposed contract

Extend the S2.7 refined-precedence pattern from `PHYSICAL_REPO_ROOT` to the
derived identity tuple (`REPO_NAME` / `INSTANCE_ID` /
`DOCKER_NETWORK_INTERNAL`) during `env generate`: a pre-set value wins ONLY
when consistent with the value derived for THIS repo root; on mismatch, use the
derived value and warn on stderr naming the ignored ambient value. (Stricter
alternative reading: generate's entire job is computing fresh identity, so it
ignores ambient identity values outright and takes overrides only via explicit
flags; ambient-env precedence would remain for the read path of
already-generated workspaces.)

### Oracles

- Generate in a worktree with the main instance's `DOCKER_NETWORK_INTERNAL`
  exported → generated `ciu.env` carries the derived
  `<repo>-<instance>-network`, and a warning names the ignored ambient value.
- Generate with a consistent pre-set value (equal to derived) → silent, output
  unchanged.
- Controlled wrong implementation: restoring the bare
  `os.environ.get(..., derived)` fallback must fail the first oracle.

**SPEC ownership:** S2 (workspace environment), extending S2.7's precedence
contract to the derived identity values.

## CIU-42 — cross-profile `ASK_VAULT` producers are inexpressible; partial profile selections fail with only the path name

**Filed by:** dstdns P111 (Mode-B live pass), 2026-08-20. Provenance:
`dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §9 F3 and §11 item 4.

### Observed mechanism and reproduction

A Mode-B instance deployed with `CIU_SERVICES_PROFILE="core,db"` (per dstdns
GUIDE §3.4b), then incrementally
`ciu up --dir applications/{controller,webapp-server}`. Both app stacks declare
`ASK_VAULT` secrets that only the `identity` profile's provisioning (or a hook)
writes — `authentik/bootstrap_token` (controller) and
`vault/webapp-server/token` (webapp-server) — and `ASK_VAULT` correctly refuses
when the path is absent. So a `core,db` selection cannot start the app tier at
all, and the refusal names the missing *path* but not its *producer*. The
identity profile is multi-GB (Authentik) and the host had 2.9 GB available, so
"just deploy identity" was not an option. Resolved in the field by seeding both
paths in the INSTANCE's own Vault with disposable placeholders — consistent
with the stacks' own comments ("an identity-less deploy has nothing for this
check to protect") and touching no shared state.

**Second reproduction (2026-08-20, dstdns P112 Mode-B):** `core,db` again could not start controller/webapp-server without the two identity-profile `ASK_VAULT` paths; resolved the same way (disposable placeholders in the instance's own Vault, disclosed). Two consecutive packages → priority bump warranted.

### Two readings — both presented deliberately

1. **Doc gap.** Partial-profile selections that exclude a producing profile are
   simply unsupported without manual seeding; then ciu's documentation
   (CONFIG.md, secrets × profiles interaction) should say so and describe the
   placeholder-seeding recipe as the sanctioned pattern. (dstdns is folding the
   recipe into its own GUIDE §3.4b regardless — the consumer-side half of this
   finding.)
2. **Mechanism gap.** A stack cannot DECLARE that an `ASK_VAULT:<path>` is
   produced by another profile's provisioning. With such a declaration (e.g. a
   `produced_by = "<profile>"` annotation beside the directive, or an S13
   typed reference), `ciu up` under a partial selection could refuse UPFRONT
   naming the missing producer — "`authentik/bootstrap_token` is provisioned
   by profile `identity`, which is not in your selection; deploy it or seed
   the path" — instead of failing at the individual consuming stack with only
   the bare path. S13 (`requires`/`provides`) already has the right vocabulary
   shape.

### Why CIU owns it

`ASK_VAULT`'s refusal contract and profile selection are both ciu's contracts;
the consumer can document around their interaction but cannot express the
dependency to the tool.

### Oracles (mechanism reading)

- A partial selection missing a declared producer refuses pre-deploy, naming
  producer profile + path + the seeding alternative.
- A selection including the producer profile is unaffected.
- An undeclared `ASK_VAULT` keeps today's behavior exactly.
- Controlled wrong implementation: dropping the declaration lookup regresses to
  the bare-path refusal and must fail the first oracle.

**SPEC ownership:** S4 (`ASK_VAULT` refusal contract) + S13 (declaration) if
the mechanism reading is chosen; CONFIG.md only if the doc reading is chosen.

## CIU-43 — `ciu clean` leaves instance-scoped networks behind while reporting `clean complete`

**Filed by:** dstdns P111 (Mode-B live pass teardown), 2026-08-20. Provenance:
`dstdns/nyxloom-trove/reports/dstdns-P111-REPORT.md` §8.3 and §9 F4. Reproduced
live; source-checked against `src/ciu/deploy.py` before filing. dstdns GUIDE
§3.3 has carried this consumer-side for a while ("a success message is not
sufficient"); this filing moves it upstream.

### Observed mechanism and reproduction

After a full Mode-B pass (instance `e893b0`, container/volume prefix
`p111-auth-cutover-e893b0-`), `ciu clean -y` from the worktree under its own
`ciu.env` printed `clean complete`. Leftover check by exact instance prefix:

- containers: none. instance-prefixed volumes: none.
- networks: **two remained** — `p111-auth-cutover-e893b0-network` (the
  workspace network `env generate`/`up` creates via `ensure_workspace_network`)
  and `p111-auth-cutover-e893b0-vault_default` (the compose-created default
  network of the vault stack).

Manual fallback per dstdns GUIDE §3.3: disconnect the named
`dstdns-devcontainer-vb` endpoint from the first network, then
`docker network rm` on both fully-resolved names — after which zero objects
with the prefix remained.

Secondary observation from the same teardown (D-130 amendment in the REPORT):
the named Vault volumes `p111-auth-cutover-vault-{data,logs}` carry the
PROJECT prefix (`<branch>-vault-*`), not the instance prefix, and also
survived — worth checking whether `action_clean`'s project-prefixed volume
pass covers nested/sibling compose projects' volume naming, though the
headline of this issue is networks.

### Design-vs-regression — both readings

`action_clean`'s own docstring (`src/ciu/deploy.py`, ~line 1679) says the
network survival is deliberate: "Network removal is NOT performed (v1 had no
explicit --clean-networks; the network is left in place)." That is defensible
for the long-lived MAIN workspace, whose network the devcontainer itself stays
connected to. It is wrong for ephemeral Mode-B worktree instances: every
instance creates identity-scoped networks that nothing ever removes, so they
accumulate one teardown at a time — and `clean complete` overstates what
happened either way. Note CIU-19 ("instance-scoped cleanup", FIXED, S6.4)
covered containers and volumes; networks were left outside its scope. And even
granting the deliberate-keep reading, the compose-created `*_default` network
is not covered by the stated v1 rationale at all — `docker compose down`
normally removes the networks it created, so its survival suggests the
per-stack reset path isn't reaching compose's own network cleanup (plausibly
because step 1 already force-removed the containers, or because an external
endpoint — the cockpit — pinned it).

### Proposed contract

A full `ciu clean` removes the identity-scoped networks it (or its compose
runs) created: disconnect lingering endpoints it can name (or refuse, naming
them), then remove. If keeping the invoking workspace's own network is desired
(the devcontainer-residence case), keep it explicitly and SAY so — the success
message must name anything deliberately left behind, never claim `clean
complete` over surviving identity-scoped objects. A `--clean-only-networks` /
`--keep-network` flag pair is one shape; unconditional removal for S16 worktree
instances plus keep-with-notice for the main workspace is another.

### Oracles

- Mode-B instance `up` → `clean` leaves ZERO Docker objects carrying the
  instance identity (containers, volumes, networks — including compose
  `*_default` names).
- `clean` with the devcontainer still connected to the instance network either
  disconnect-then-removes or refuses naming the endpoint — never silently
  keeps.
- Main-workspace `clean` that deliberately keeps the workspace network names it
  in output.
- Controlled wrong implementation: restoring today's no-network-removal path
  must fail the first oracle.

**SPEC ownership:** S6.4 cleanup semantics.

## CIU-45 — WITHDRAWN: `requires` does not "provision rather than verify"

**Disposition:** WITHDRAWN on 2026-08-21, one day after filing. The finding itself is void — not
superseded, not already fixed, but based on a misdiagnosis that a second, independent reproduction
disproved.

**What was claimed:** that ciu's provisioning-graph lint demands a declarative `GEN_TO_VAULT` row
for every `requires` entry, so a Vault path minted entirely out-of-band by a `post_compose` hook
(never by a `GEN_TO_VAULT` directive) could never satisfy it — and that no mechanism exists for a
`post_compose` hook to register itself as a provider in the graph.

**Why it's false:** `ciu/src/ciu/provisioning.py:90-113`'s lint rule is "every `requires` ref
appears in some stack's `provides` array" — a plain declarative string list in a stack's own
`ciu.defaults.toml.j2`, unrelated to `GEN_TO_VAULT`/secret-directive machinery. A `post_compose`
hook registering itself as a provider is not a missing capability; it is the SHIPPED, already-used
pattern at `infra/consul-server/ciu.defaults.toml.j2:9-17` in the very consumer repo that filed this
issue — a hook mints per-service Vault tokens and the stack declares `provides = ["vault:secret/
consul/<svc>/token", ...]` alongside it. dstdns P120's actual failure was that a DIFFERENT stack
(`infra/vault`) simply never added its own `provides` array for the AppRole credentials its hook
mints — a six-line, in-repo, declarative omission, not a ciu gap. No runtime provisioning was ever
attempted in the original reproduction (`docker ps -a` showed zero containers); the static preflight
lint had already refused before any hook ran, so "provisions rather than verifies" was never
actually observed, only inferred.

**Reproduction that found this:** dstdns's own fresh adversarial code reviewer, dispatched blind
against the P120 package that filed this issue, independently re-derived the failure from source
(`provisioning.py`) rather than trusting the original report, proved the six-line fix restores
`ciu check` to green on every profile, and named the exact in-repo precedent above. Full account:
`dstdns/nyxloom-trove/decisions.md` D-170.

**Lesson for future filings:** a "ciu structurally cannot express X" claim needs an actual grep for
the mechanism named in ciu's own error text before it is trusted, not just confirmation that a
predicted refusal occurred. The consumer's own repo already had two working examples of the pattern
this issue claimed was impossible.

## CIU-46 — shipped stacks under the legacy compose-project fallback escape clean's S6.4a enumeration

**Filed by:** ciu controller session, 2026-08-22, from the residual recorded
in CIU-43's FIXED row during the consumer-wave adversarial review.
Source-grounded: `engine.py` `run_shipped` (~line 1638) falls back to
`shipped_project = None` (legacy directory-derived compose project) with only
a warning when `deploy.project_name`/`environment_tag` are absent — shipped
mode does not require a full deploy config (S8.5) — while clean's
`_stack_compose_projects` (`deploy.py`) returns `[]` under exactly that
condition, so the S6.4a network/volume enumeration never learns the legacy
project name and its `*_default` network and label-prefixed volumes survive a
reported-clean teardown.

### Why CIU owns it

S6.4a's contract is "clean leaves zero identity-scoped Docker objects, or
fails loudly". The shipped path silently re-creates the CIU-43 leak species
for any shipped stack deployed without tags; the consumer cannot fix this
from its side — the enumeration is ciu's.

### Proposed contract

When clean encounters a selected shipped stack whose compose project cannot
be derived (tags absent), either (a) enumerate the legacy directory-derived
project name with the same compose-label filters and clean it, or (b) refuse
clean for that stack naming the missing tags — never silently skip. Match
whatever `run_shipped`'s fallback actually named at up time.

### Oracles

- Shipped stack deployed via the legacy fallback (no tags) → clean removes
  its `*_default` network and label-prefixed volumes, or refuses naming the
  tags; `clean complete` over a survivor is the controlled-wrong
  implementation and must fail the oracle.
- Tagged shipped stacks keep today's exact behavior.

**SPEC ownership:** S6.4a (cleanup semantics), extending its shipped-stack
coverage.

## CIU-47 — `env generate` adopts an ambient `PUBLIC_FQDN` with no consistency check

**Filed by:** ciu controller session, 2026-08-22, from the follow-up
candidate named out-of-scope in CIU-41's FIXED row. Source-grounded:
`workspace_env.py` reads `PUBLIC_FQDN` from the ambient environment at
generate time (bare `os.environ.get` at ~lines 278 and 1060) with no
comparison against anything derived for THIS workspace — the identical
masked-default shape CIU-41 fixed for `REPO_NAME`/`INSTANCE_ID`/
`DOCKER_NETWORK_INTERNAL`. Any consumer whose login shell sources a main
checkout's `ciu.env` (the documented convenience pattern) carries main's
`PUBLIC_FQDN` into a fresh worktree's generated file, where it becomes the
worktree's recorded public name.

### Why CIU owns it

Same reasoning as CIU-41: `env generate` is the identity-computation verb and
its output is the record every later command trusts; the S2.7 refined
precedence now covers the identity tuple but deliberately stopped at the
filed scope, leaving this sibling unfixed.

### Proposed contract

Extend S2.7 refined precedence to `PUBLIC_FQDN`: during generate, an ambient
value is adopted only when consistent with the value this workspace's own
inputs derive; on mismatch use the derived value and warn on stderr naming
the ignored ambient value. (Note `PUBLIC_FQDN` is host-derived rather than
path-derived — decide what "consistent with this workspace" means: likely
re-detection via the same detection path, with an explicit flag as the only
override.)

### Oracles

- Generate in a worktree with main's `PUBLIC_FQDN` exported → generated
  `ciu.env` carries the workspace-derived value and a warning names the
  ignored ambient one.
- Consistent pre-set value → silent, output unchanged.
- Controlled wrong implementation: restoring the bare
  `os.environ.get("PUBLIC_FQDN", ...)` fallback must fail the first oracle.

**SPEC ownership:** S2 (workspace environment), extending S2.7.

## CIU-48 — Compose `hostname:` independently registers a bare, ambiguous DNS alias

**Filed by:** dstdns controller session, 2026-08-25, from the §3.6
cockpit-alias-ambiguity investigation
(`dstdns/nyxloom-trove/GUIDE.md` §3.6,
`dstdns/nyxloom-trove/CONTROLLER-BRIEF.md`). Empirically confirmed, not
assumed — reproduced live:

```
$ docker run -d --name test-svcA --hostname aliasname busybox sleep 60
$ docker run --rm busybox nslookup aliasname
Non-authoritative answer:
Name:   aliasname
Address: 172.25.0.2
```

`aliasname` (the `--hostname`/Compose `hostname:` value) resolves from a
third container independently of the container's actual name — a SECOND,
separate mechanism from Compose's automatic service-key alias (CIU-51),
and one CIU already controls directly at template-render time. dstdns's own
compose templates set `hostname:` to the bare service name in 31 locations
(e.g. `infra/db-core/ciu.compose.yml.j2:59`:
`hostname: {{ db_core.postgres.name }}` → renders bare `postgres`).

### Why CIU owns it

`hostname:` is a value CIU's own template rendering already controls, using
identity facts (`deploy.project_name`/`deploy.environment_tag`) it already
computes uniformly for every deployment (`container_name()`,
`src/ciu/deploy.py:138-151` — confirmed nothing worktree-conditional in its
derivation). This is not a consumer-side workaround; the fix belongs in the
same render layer that already produces `container_name`.

### Proposed contract

```jinja
# before (31 sites across dstdns alone)
hostname: {{ db_core.postgres.name }}

# after — same variables container_name() already uses
hostname: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ db_core.postgres.name }}
```

A template-default value change, applied uniformly (main included, not
`ciu worktree`-specific) — the ambiguity only *manifests* as a worktree
problem because main rarely coexists with a second same-shaped instance;
the underlying defect is present on every deployment. Open question to
audit before changing (not assumed either way): does anything currently
rely on the container's self-reported hostname matching its bare service
name for a reason unrelated to DNS (log self-identification, TLS SNI)? The
service-key alias (CIU-51) remains available regardless, so intra-stack
bare-name reachability is not lost by this change alone.

### Oracles

- From a container attached to two independent CIU-deployed stacks'
  networks simultaneously (the exact §3.6 scenario), resolving a service's
  `hostname:`-derived qualified name returns exactly one deployment's
  container, deterministically.
- Controlled wrong implementation: a `hostname:` value that's unique-looking
  but not derived from `deploy.project_name`/`environment_tag` (e.g. a
  random suffix) — reintroduces a second identity axis to keep in sync,
  the exact staleness class this closes.

**SPEC ownership:** the compose-template rendering layer that already
supplies `container_name` (same code path, `src/ciu/deploy.py` +
consumer `*/ciu.compose.yml.j2` templates).

### Disposition (ciu-P30, 2026-08-25) — PARTIAL

Shipped: ciu's own `ciu init`-generated scaffold
(`src/ciu/templates/stack.compose.yml.j2`) now sets `hostname:` to the exact
same expression `container_name:` already uses, so a FRESHLY scaffolded
stack gets a correctly-qualified `hostname:` by default. Plus a new
DESIGN-GUIDE.md section naming this hazard by its §3.6 term, and a CONSUMERS.md
worked example showing the qualified pattern to paste into a hand-authored
template. **Not shipped:** any change to dstdns's 31 already-authored compose
templates — those live in the dstdns repository, not reachable or editable
from a ciu session. Propagating the corrected pattern into them is dstdns's
own follow-up work. This row is PARTIAL, not FIXED, so as not to overclaim
that a ciu release alone closes dstdns's actual operator pain.

### Follow-up (dstdns@41898e90, 2026-08-25) — dstdns's own side now FIXED

All 31 sites qualified: `hostname:` now matches the adjacent
`container_name:` expression in every active dstdns compose template
(`ciu.compose.yml.j2` under `applications/`, `infra/`, `infra-global/`, and
root). Re-verified 2026-08-26: a grep across every live template turns up
zero remaining bare `hostname:` lines; the only unqualified stragglers are
in `legacy-experiments/` and `retired-legacy/`, both dead/archived, out of
scope by design. One deliberate, explicitly-commented exception remains:
`pwmcp_mcp`'s `internal_host` stays bare (an externally-joined
`[pwmcp.consumer]` alias, not a locally `hostname:`-qualified service — see
CIU-49's parallel note). This entry's own scope (ciu-side scaffold + docs)
stays PARTIAL — that's ciu's own product surface, unaffected by what one
consumer does with it — but the "dstdns's actual operator pain" this row
exists to track is now closed. — App-config `internal_host`-style defaults render the bare service name, not the already-qualified form

**Filed by:** dstdns controller session, 2026-08-25, same investigation as
CIU-48 (file together). `ciu.global.defaults.toml.j2`'s
`topology.services.<svc>.internal_host` (and equivalent per-stack defaults)
currently default to the bare name:

```toml
[topology.services.vault]
internal_host = "vault"
```

Application config built from this template inherits the §3.6 ambiguity —
except here the consumer is the APPLICATION's own outbound connection code,
not Docker DNS directly. This is already hand-worked-around exactly once:
dstdns's `test/multistack-v1` worktree (documented as the permanent live
Mode-B template) manually overrides it:

```toml
internal_host = "dstdns-mstest-f2d1cb-vault"  # instance config: scoped (GUIDE 3.6)
```

### Why CIU owns it

Same reasoning as CIU-48: the qualifying identity facts are already
computed by CIU for every deployment; a consumer hand-maintaining a
per-worktree override is exactly the staleness hazard CIU's own instance
identity machinery exists to make unnecessary — the NEXT worktree template
someone copies from `dstdns-mstest` carries a wrong, copy-pasted instance
ID the moment it diverges.

### Proposed contract

```toml
# proposed default, ciu.global.defaults.toml.j2
[topology.services.vault]
internal_host = "{{ deploy.project_name }}-{{ deploy.environment_tag }}-vault"
```

Template default, uniform across every deployment, not `ciu worktree`-
specific (same reasoning as CIU-48). Once shipped, dstdns's hand-maintained
override becomes redundant and can be deleted. **Does not yet cover the
shared-infra case** (a joining instance needs the REFERENCE instance's
qualified name, not its own) — that's CIU-52, filed separately rather than
conflated here.

### Oracles

- Render the same template for two different `deploy.environment_tag`
  values (simulating two coexisting instances): the two `internal_host`
  outputs differ and each is independently correct, never requiring a hand
  override to differ.
- Controlled wrong implementation: hardcoding the qualified form as a
  literal per-stack override instead of deriving it from
  `deploy.project_name`/`environment_tag` in the shared template —
  reintroduces the exact per-worktree drift hazard this closes.

**SPEC ownership:** CIU's global config-default template layer
(`ciu.global.defaults.toml.j2` and equivalent per-project defaults).

### Disposition (ciu-P30, 2026-08-25) — PARTIAL

Shipped: `docs/CONFIG.md`'s `[topology.services.<name>]` section now carries
a SHOULD-level prescription to qualify `internal_host` with the same
`container_name()` derivation, an unambiguous annotation of the existing
worked example, and a link to the new DESIGN-GUIDE hazard section; plus a
CONSUMERS.md worked example. **Not shipped:** ciu ships no `internal_host`
default of its own anywhere (`global.defaults.toml.j2` has no `[topology]`
block at all — confirmed by grep) — there is no ciu-shipped default to
change. dstdns's hand-maintained `dstdns-mstest` override remains
unmodified; removing it in favor of the prescribed qualified pattern is
dstdns's own follow-up, out of reach from this repo. PARTIAL, not FIXED.

### Follow-up (dstdns@41898e90, 2026-08-25) — dstdns's own side now FIXED

`ciu.global.defaults.toml.j2`'s `[topology.services.*]` block now derives
every `internal_host` as `$REPO_NAME-$INSTANCE_ID-<service>` (the shell-env
form of the same identity facts `container_name()` uses), for every
service including the ones this entry named (`vault`, `postgres`, etc.) —
re-verified 2026-08-26, zero bare `internal_host` defaults remain in the
live template. The literal `dstdns-mstest-f2d1cb-vault`-style hand override
this entry quoted is gone from the `test/multistack-v1` worktree's current
checked-in config (checked directly — no `internal_host` line at all in
either its `ciu.global.toml.j2` or rendered `ciu.global.toml`); whether that
means it was already cleaned up independently or never survived a rebase is
not reconstructed here, but the hazard this entry tracks — a hand-maintained
per-worktree override drifting from a since-fixed shared default — is
closed either way. **Still not covered, by design (per this entry's own
"Proposed contract" note above):** the shared-infra cross-instance case,
where a JOINING instance needs the REFERENCE instance's qualified name —
that's CIU-52 (FIXED separately, ciu-P31). Same pwmcp exception as CIU-48.

## CIU-50 — `deploy.environment_tag` should be `deploy.instance_id` (naming clarity, not a defect)

**Filed by:** dstdns operator directive, 2026-08-25, same investigation.
`environment_tag` is CIU's per-deployment INSTANCE identifier (the
component making `container_name()`'s output unique) but reads as though it
names a deployment ENVIRONMENT (dev/staging/prod) — a different, more
common concept in infra tooling. This ambiguity cost real investigation
time this session (the field's actual purpose had to be re-derived from
source rather than inferred from its name).

### Why CIU owns it

Pure naming-clarity request on CIU's own config schema; no behavior change.

### Proposed contract

Rename `deploy.environment_tag` → `deploy.instance_id` (namespace TBD —
`ciu.instance_id` was also suggested; settle against CIU's existing config
structure). Measured blast radius: 9 files in ciu's own `src/` (core site
`src/ciu/deploy.py`, including a hard-fail validation message quoting the
literal key name), 46 files in dstdns's own templates — grep-and-replace
scale, but a SCHEMA KEY rename (unlike CIU-48/49's pure default-value
changes), so every consumer's rendered config keys change shape. Proposed
as a v8-timed cutover rather than a silent rename on stable current-CIU
config, per this project's own no-dual-naming greenfield doctrine — not
proposed as an immediate backport for this reason specifically.

### Oracles

- Every config surface (rendered TOML, generated docs, CLI help) names
  `instance_id` consistently, no dual-naming transition period.
- Controlled wrong implementation: a partial rename leaving some consumer
  templates on the old key while ciu's own source moves to the new one —
  silent breakage, not a clean rename.

**SPEC ownership:** CIU's deployment config schema (`deploy.*` namespace,
`src/ciu/deploy.py`) — land alongside v8's other `deploy.*` schema work if
any lands in the same pass.

## CIU-51 — Eliminating Compose's automatic bare service-key alias (the full §3.6 fix; v8-scale, not a backport)

**Filed by:** dstdns operator directive, 2026-08-25, same investigation —
explicitly requested as "how would that change look like." This is the
harder half CIU-48/49 do not close: Compose ALWAYS registers a service's
top-level YAML key as a network-scoped DNS alias, confirmed against
Compose's own docs (`services` reference: "aliases declares *alternative*
hostnames... the service name itself remains resolvable") — no documented
mechanism suppresses this per-network.

### Concrete before/after

Today (representative pattern across dstdns's compose templates):

```yaml
services:
  vault:                                    # bare compose service KEY — this is what gets auto-aliased
    container_name: dstdns-98535c-vault     # already qualified (unrelated mechanism, doesn't help)
  consul-server:
    depends_on:
      vault: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "curl", "http://vault:8200/v1/sys/health"]
```

To eliminate the bare alias, the KEY itself must already be qualified,
because the key is what Compose auto-aliases:

```yaml
services:
  dstdns-98535c-vault:                      # key IS the qualified form now
    container_name: dstdns-98535c-vault
  dstdns-98535c-consul-server:
    depends_on:
      dstdns-98535c-vault: {condition: service_healthy}   # every reference must follow
    healthcheck:
      test: ["CMD", "curl", "http://dstdns-98535c-vault:8200/v1/sys/health"]
```

Every service key, every `depends_on:` entry, every intra-stack healthcheck/
init-script reference to a sibling by bare name has to move together in one
atomic pass per stack — a partial migration is a Compose parse-time error
(loud, at least, unlike today's silent ambiguity) rather than a
hand-editable transition.

### Why CIU owns it, and what CIU would need to provide

A template-level qualifying primitive, so authors don't hand-interpolate
`{{ deploy.project_name }}-{{ deploy.environment_tag }}-` at every service
key and reference (pure repetition-as-correctness — the exact staleness
hazard class CIU-48/49 also close, but at compose-structure scale instead
of a single default value):

```jinja
services:
  {{ qname('vault') }}:
    container_name: {{ qname('vault') }}
  {{ qname('consul-server') }}:
    depends_on:
      {{ qname('vault') }}: {condition: service_healthy}
```

where `qname()` is a single CIU-provided template function deriving from
the same identity facts `container_name()` already uses — one source of
truth referenced everywhere, never re-typed.

### Why this is explicitly NOT proposed as a backport

Unlike CIU-48/49 (pure default-VALUE changes), this is a breaking
compose-template STRUCTURE change requiring a new template primitive to
exist before any consumer template can adopt it, and a partial migration is
actively worse than the status quo. This is exactly the class of change
v8's `docs/SPEC-RECONCILIATION-2026-08-24.md` "Priority 1: Structural
clarity" bucket exists to absorb deliberately, in one coordinated pass
across all consumer templates — not proposed as a same-day patch on live
current-CIU deployments. CIU-48/49, by contrast, close most of the actual
operator pain (everything except direct ad-hoc bare-name DNS lookups from
outside CIU's own config layer) without this larger change.

**Superseded in one respect by CIU-66 (2026-08-26):** the `qname('vault')`
example above derives from project+instance only, same as `container_name()`
today — it does not add a stack identifier, so two different stacks naming a
service identically would still collide under this proposal exactly as
drafted. CIU-66 proposes `qname()` take a required `stack` argument; land
that signature here rather than shipping `qname()` first and revising its
signature later.

### Oracles

- After this change, the bare service name is NOT resolvable via DNS from
  ANY container in the stack — `getent hosts vault` from a sibling
  container fails (NXDOMAIN), where today it succeeds (ambiguously, when
  multi-homed).
- Controlled wrong implementation: qualifying the service KEY but adding a
  compose long-form `aliases:` entry that re-adds the bare name "for
  convenience" — reintroduces the alias through the back door while
  looking migrated.

**SPEC ownership:** new surface (compose-template Jinja global
functions/filters) — cross-reference
`docs/SPEC-RECONCILIATION-2026-08-24.md` §2b/§2e (`CIU-V8-PREP-3`/`-6`)
as the nearest existing v8 work touching service naming/addressing; land
alongside it.

## CIU-52 — Implement S12's reserved `shared_infra.services[*].aliases`

**Filed by:** dstdns controller session, 2026-08-25, same investigation —
the naming half of the operator's "pass the existing instance's container
names to the new instance" proposal. `docs/SPEC.md:1152-1159` (S12) already
reserves `ciu.instance.shared_infra.services[*].aliases` as
not-yet-implemented. The join mechanism itself IS shipped and appears
production-hardened: `docs/SPEC.md:2482-2560` (S16.1/CIU-22),
`ciu worktree add NAME --shared-infra REF --shared-infra-services S1[,S2]
--shared-infra-ref-projects R1[,R2]` — concurrency-safe liveness
re-validation, Docker-state idempotency, scoped rollback. What's missing:
after joining a reference instance's network, the joining instance has no
CIU-declared name to call the shared service by.

### Why CIU owns it

Additive to an already-shipped mechanism (S16.1) — implementing a reserved
field, not new architecture.

### Proposed contract

```toml
# worktree B's config, joining worktree A's vault
[[shared_infra.services]]
name = "vault"
ref_project = "dstdns"
ref_instance_id = "98535c"          # A's instance_id — derived, not hand-typed
aliases = ["vault"]                 # what B's own templates may call it
```

CIU resolves `aliases` into B's own `topology.services.vault.internal_host`
(CIU-49's field) automatically at join time, pointing at A's already-
qualified `container_name` output — B's config keeps a short local name
while the underlying resolution is CIU-managed, not a bare Docker DNS alias
subject to CIU-51's ambiguity at all.

### Oracles

- After `ciu worktree add ... --shared-infra-services vault`, the joining
  instance's rendered `internal_host` resolves to the reference instance's
  qualified `container_name`, with no hand-written override needed (contrast
  dstdns's current hand-maintained `dstdns-mstest` override for the
  non-shared case, CIU-49).
- Controlled wrong implementation: injecting the reference instance's BARE
  service name instead of its qualified `container_name` — reproduces the
  exact bug this closes, relocated to the shared-infra path.
- Fixture: instance A (reference) + instance B (diverging, shared-infra
  join) + instance C (unrelated, own vault) coexisting — B's resolution is
  scoped to A specifically, unaffected by C.

**SPEC ownership:** `docs/SPEC.md` S12 (declares the field) + S16.1/CIU-22
(the join mechanism this attaches to).

### Disposition (ciu-P31, 2026-08-25) — FIXED

Shipped as SPEC **S16.1a**: a new OPTIONAL, alias-keyed
`[ciu.instance.shared_infra.ref_services.<alias>]` table
(`{service, container, port?}`), the `--shared-infra-ref-services
ALIAS[,ALIAS=REF_SERVICE]` flag on `add`/`create`/`ensure`/`adopt`, add-time
derivation from the REFERENCE's OWN rendered config (`write_rendered=False`,
`environ=<the reference's ciu.env>`) through the same
`deploy.container_name()` used everywhere else, authentication of that
derived name against live Docker state before it is written, an emitted
`[topology.services.<alias>]` block in the joining instance's worktree
overlay, and join-time re-verification before any `docker network connect`.
All three filed oracles are covered as real tests, including the
controlled-wrong (bare-name) mutant and the adversarial three-instance
fixture where C is connected to A's network carrying the identical
`com.docker.compose.service=vault` label.

**Shipped shape differs from the filing's illustrative TOML, deliberately —
the filing misread the shipped schema.** It assumed `shared_infra.services`
and `shared_infra.ref_projects` are paired (one service entry carrying its
owning reference project, with `aliases` hanging off it). They are not, and
never were: **`services` names THIS (joining) instance's OWN diverging-tier
containers** — `connect_shared_infra_after_up`'s target-discovery loop filters
on `com.docker.compose.project=<THIS instance's compose project>` — while
**`ref_projects` names the REFERENCE's compose projects**, consulted only by
`_check_reference_network_and_projects` for AND-combined liveness. They are
two independent lists about two different instances (the shipped fixture uses
two services against one ref project), so neither can name a reference-side
service, and `services[*].aliases` could only ever have addressed the
joiner's own copy of a service — pointing this instance's `vault` at the
reference's `vault` would have been actively wrong. Hence the third,
independent `ref_services` axis; the S12 reservation is withdrawn rather than
implemented. Recorded here explicitly so a future reader does not repeat the
filing's own misreading.

## CIU-53 — `dev.resolve_repo_root` checked ambient REPO_ROOT before `--define-root`

**Filed by:** operator live reproduction, 2026-08-25, in the vbpub/dstdns
joint devcontainer. Corroborated by the worktree-identity-wave retrospective
review's HIGH finding #2 (`dev.py:40-44`, ambient `REPO_ROOT` overriding an
explicit `--define-root`, contradicting CIU-29 req 8's own oracle).

### Observed mechanism and reproduction

Running `ciu worktree list` (and every `ciu dev`/`ciu worktree *` verb) with
no `--define-root`, from inside a real ciu-managed repo, while the shell's
`REPO_ROOT` carried a DIFFERENT, sibling checkout's value (from that
checkout's sourced `ciu.env` — the documented convenience pattern CIU-41
already named): the ambient value silently won, so the command operated on
the WRONG repo. `dev.resolve_repo_root`'s pre-fix body:

```python
env_root = os.environ.get("REPO_ROOT")
if env_root:
    return Path(env_root).resolve()
if define_root:
    return Path(define_root).resolve()
...  # walk-up, only reached when neither is set
```

`REPO_ROOT` was checked **before** `define_root` — the reverse of SPEC S1.1's
own documented order (`--define-root` → `REPO_ROOT` env → walk-up). The CODE
was violating its own documented contract; an explicit `--define-root` did
not even win over a conflicting ambient value. Separately, the previously
*documented* order itself still had a masked-default gap: even with
`--define-root` correctly checked first, an ambient `REPO_ROOT` would still
silently outrank a successful walk-up derivation whenever `--define-root`
was omitted — the exact CIU-41 hazard family, one level up, for the resolver
that decides WHICH repo a command operates on before any identity is even
read.

### Why CIU owns it

`resolve_repo_root` feeds `ciu dev` and every `ciu worktree *` verb,
including destructive ones (`worktree rm`, `worktree branches -y`, and by
extension anything the selected root's `ciu clean` later removes). A
consumer cannot work around a resolver that silently guesses which repo it
operates on; the fix has to live in the resolver itself.

### Disposition — FIXED 2026-08-25 (ciu-P32)

`dev.resolve_repo_root`'s precedence is now: `define_root` (explicit) always
wins outright, no consistency check — an explicit flag is not second-guessed
by a shell variable. Otherwise CIU walks up from `start_dir` for
`ciu.global.defaults.toml.j2`. When that walk-up SUCCEEDS: no ambient
`REPO_ROOT` → use the derived root silently (identical to today); a
consistent ambient value → silent; a DISAGREEING ambient value → REFUSE with
a `[S1.1]`-tagged `ValueError` naming both paths and three remedies (unset
`REPO_ROOT`, pass `--define-root`, or `cd` into the intended repo) — a
refusal rather than `env generate`'s warn-and-proceed, because this value
selects a repo for destructive verbs directly, not a value about to be
freshly written to a generated file. When the walk-up finds NOTHING at all,
CIU falls back to ambient `REPO_ROOT` if set, else `start_dir` — today's
ultimate fallback, unchanged (there is no derived answer for an ambient value
to disagree with in that case). All ~8 real call sites in `cli.py` (`_ksm`,
`_provenance`, `_status`, `_bake`, `_worktree`'s main body, `_worktree_exec`
via its injected resolver, and the `dev` verb inline in `main()`) now funnel
through one `_resolve_repo_root_cli` helper that turns the `ValueError` into
a clean `[ERROR] ...` message + `SystemExit(2)`, matching this codebase's
standard CLI error convention — never a raw traceback, never a caller that
proceeds anyway.

### Oracles

- A real ciu-managed tree, cwd nested inside it, no `--define-root`, ambient
  `REPO_ROOT` set to a different real path → REFUSE naming both paths.
- Same tree, no ambient `REPO_ROOT` at all → derive silently, unaffected
  (the common case never regresses).
- `--define-root` given, ambient `REPO_ROOT` conflicting → `--define-root`
  wins outright, no refusal.
- Walk-up finds nothing, ambient `REPO_ROOT` set → falls back to it
  (unchanged). Walk-up finds nothing, no ambient → falls back to `start_dir`
  (unchanged).
- Every real `cli.py` call site surfaces the refusal as `[ERROR] ...` +
  non-zero exit, not a raw traceback.

**SPEC ownership:** S1.1 (repo-root resolution).

## CIU-54 — 8 other `cli.py` call sites resolve REPO_ROOT via a bare `os.environ.get` fallback

**Filed by:** ciu-P32, as the explicitly-named follow-up from CIU-53's O6
oracle (do not silently widen scope to fix these here).

### Observed mechanism

Independent of `dev.resolve_repo_root` (CIU-53), 8 call sites in `cli.py`
resolve `repo_root` via a bare `Path(os.environ.get("REPO_ROOT", Path.cwd()))`
with NO `--define-root` consideration and NO walk-up at all:

- the `--host` remote branch of `render` (`elif verb == "render"`, `--host`
  path)
- `layouts`
- the `--layout` and `--host` branches of `up` (two separate sites)
- the `--host` branch of `down`
- the `--host` branch of `health`
- `host-secrets`
- `ssh`

This is a THIRD resolution strategy in this codebase, alongside
`dev.resolve_repo_root` (CIU-53, fixed) and `deploy.resolve_repo_root`
(`src/ciu/deploy.py`, which already refuses on an explicit
`--define-root`/ambient `REPO_ROOT` disagreement — a useful existing
precedent, but a different function, in a `scope.forbid` file for ciu-P32).
None of these 8 sites derive from cwd at all; they trust `REPO_ROOT` (or cwd
if unset) unconditionally, with no ambient-consistency check and no walk-up
fallback.

### Why this is a separate ask, not an extension of CIU-53

These sites are all on REMOTE/push-deploy paths (`--host`, `ssh`,
`host-secrets`) or listing verbs (`layouts`) — a different usage shape from
`dev`/`worktree`'s local-repo-identity question, closer to `deploy.py`'s own
resolver than to `dev.resolve_repo_root`. Unifying all of these under one
resolution strategy touches more verbs and is closer to a `deploy.py`
refactor — real scope creep beyond CIU-53's `dev.py`/`cli.py` fix.

### Proposed contract

Not yet designed. At minimum, candidates to evaluate: (a) route these
through `deploy.resolve_repo_root` (already implements a `--define-root`
refusal-on-disagreement, just needs each site to pass its own `--define-root`
value where one exists); (b) route through `dev.resolve_repo_root`/
`_resolve_repo_root_cli` if walk-up-from-cwd is actually desired for these
verbs too. Needs a design pass naming which of these 8 verbs actually accept
a `--define-root` flag today (several currently do not) before either is a
safe change.

### Disposition — FIXED 2026-08-31 (ciu-P45)

Re-derived the 8 sites myself (line numbers had drifted, count held at 8):
`render --host`, `layouts`, `up --layout`, `up --host`, `down --host`,
`health --host`, `host-secrets`, `ssh`. Verified the design pass's own
precondition first — read every one of these 8 sites' local argparse wiring
directly: **none of them registered `--define-root`/`--root-folder` at
all**, confirming the entry's own hint. Chose **candidate (a)**: all 8 now
call a new `_resolve_repo_root_deploy()` wrapper around
`deploy.resolve_repo_root` (the sibling of `_resolve_repo_root_cli`, which
wraps `dev.resolve_repo_root`), because each of these 8 sites' verb ALREADY
routes its own local/profile branch through `deploy.main` →
`deploy.resolve_repo_root` (e.g. plain `ciu up` with no modifier) — routing
the `--host`/`--layout`/listing branches through the SAME function keeps a
verb's resolution identical across all of its own branches, rather than
leaving a third, bespoke strategy in place or adopting
`dev.resolve_repo_root`'s walk-up (which would have made e.g. `ciu up`
resolve one way and `ciu up --host x` resolve a DIFFERENT way — a worse
inconsistency than the one being fixed). Walk-up fits `dev`/`worktree`'s
local-repo-identity question, not these verbs' remote-push/listing shape —
confirmed against the code, not just asserted.

**Mechanism.** A new `_extract_define_root(rest)` pulls
`--define-root`/`--root-folder` out of `rest` FIRST, at every one of the 8
sites, before any other local parsing — consumed there, never left to leak
into a remote argv (`render`/`up`/`down`/`health --host`'s `ssh_exec`/
`_push_host` calls, `ssh`'s `cmd_argv`) or reach `_parse_layout_argv`'s
forbidden-flag guard. It runs with `allow_abbrev=False` deliberately: it
precedes each site's own flag vocabulary, so it cannot verify an
abbreviation is unambiguous the way a single shared parser can, and
`up --layout`'s own pinned abbreviation tests (ciu-P29,
`test_up_layout_refuses_every_abbreviated_forbidden_flag_*_form`) require
bare `--d`/`--r` to still resolve to `--dir`/`--rollback` in
`_parse_layout_argv`'s guard — exactly the prefixes `--define-root`/
`--root-folder` would otherwise have claimed first. `_resolve_repo_root_deploy`
then mirrors `_resolve_repo_root_cli`'s exit contract: any `ValueError`
(`deploy.WorkspaceEnvError` included) becomes `[ERROR] ...` + `SystemExit(2)`,
never a raw traceback.

**Breaking.** `deploy.resolve_repo_root` requires ambient `REPO_ROOT` (or an
explicit `--define-root`) — no cwd fallback. A caller of one of these 8
verbs that previously relied on the cwd fallback (never having sourced
`ciu.env`) now gets a clean refusal instead of a silent cwd guess. On
`render`/`up`/`down`/`health`, `--define-root` was ALREADY documented
verb-wide in `--help`, so this also closes a real doc/behavior mismatch on
the `--host` branch specifically, not just an internal-consistency nicety.
Investigated blast radius directly (not asserted): no shipped `.sh`/CI
script under this repo invokes any of these 8 verbs without first sourcing
`ciu.env`; `docs/CONSUMERS.md` #19 carries the full migration note and
`docs/SPEC.md` S1.1a documents the two-resolver split. `fix(ciu)!:` commit
marker used.

**Full scope landed** — no subset deferral; all 8 sites fixed in one
package, same mechanism throughout.

### Oracles

- Every one of the 8 sites accepts `--define-root`/`--root-folder` and
  resolves against it (verified per-site, including the two `up --layout`/
  `up --host` sites and `layouts`/`host-secrets`, which previously took no
  local flags at all).
- `--define-root` is consumed LOCALLY and never appears in the one remote
  argv string forwarded to a `--host`/`ssh` target (verified for `render`/
  `up`/`down`/`health --host`, `up --layout`, and `ssh`'s `-- cmd` boundary
  specifically — a `--define-root` literal appearing AFTER `--` is remote
  command text, never a local flag).
- `--define-root` disagreeing with a set ambient `REPO_ROOT` REFUSES with a
  `[S1.1]`-tagged message naming both paths (verified on `ssh` and
  `up --layout`), matching `deploy.resolve_repo_root`'s existing contract.
- No ambient `REPO_ROOT` and no `--define-root` REFUSES with `[ERROR]
  REPO_ROOT not set...` rather than silently using cwd (verified on `ssh`
  and `host-secrets`).
- `up --layout`'s forbidden-flag guard still catches every abbreviation
  length of its own 6 flags, including bare `--d`/`--r`, unaffected by the
  new flag (ciu-P29's pinned suite, re-run green, unmodified).
- Real gate green at 100% line+branch coverage (see `ciu-P45-REPORT.md`).

**SPEC ownership:** S1.1a (new sub-clause, `docs/SPEC.md`) — extends S1.1's
walk-up resolver with the deploy-routed resolver's own order, and names
which verbs use which.

## CIU-55 — No per-lane gate invocation timing is measured, so provisional-merge
rigor tradeoffs are guesses, not decisions

> **RETRIAGED 2026-08-31 -> run-gate RG-27.** The operator's re-read: in the
> CURRENT (pre-v8, pre-gate-absorption) architecture, run-gate is the layer
> that actually invokes each lane and has direct, unmediated visibility into
> start/stop timestamps and exit status — CIU would have to wrap or
> intercept run-gate's own invocation loop to get the same data first-hand,
> which is more machinery than the "CIU already owns a per-instance
> persisted-state file" argument below saves. The full design (bounded
> per-commit history, a latest-result slot that accepts unsuccessful/aborted
> runs without polluting history, a query verb in both machine- and
> human-readable form) now lives at `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md`
> RG-27. The reasoning below is kept as the recorded design discussion that
> produced this ID, not as an active spec.

**Filed by:** dstdns operator directive, 2026-08-25, during a controller
handoff-mandate interview (`dstdns/nyxloom-trove/decisions.md` D-204). The
mandate under discussion proposed a lighter merge policy — merge on R0+R1 +
adversarial review, defer R2 to a sidetrack, "good enough" provisional merge
to unblock dependent packages, higher rigor run async later. Asked to adopt
it, the operator declined to decide blind: *"we need estimates/last
measurements how long a specific rigor for each lane [takes]. something ciu
could handle in v8 when managing lane invocation and persist measurement
results to a table in `ciu.global.toml` or a separate file? then we can make
smarter choices, e.g. skip a long rigor, do a provisional merge ... and run
higher rigor async on remote host."*

### Observed mechanism (the gap, not a defect)

Per the current dstdns gate-layering split (`run-gate` owns invocation
mechanics, `assay` owns judgment policy, CIU owns runner lifecycle —
`vbpub@4c6eb2b6`, CIU-40), lane duration is currently informal: a controller
or implementer notices a gate command took "a while" from wall-clock
observation while waiting on it, and that observation is lost the moment the
terminal scrolls past it. Nothing durable records:

- how long lane X took THIS run, on THIS worktree/instance, for THIS commit
- how that compares to lane X's typical/historical duration
- which lanes are cheap-to-always-run vs. expensive-enough-to-consider-
  deferring

Without this, "defer R2, run it async" is not a measured tradeoff — it is a
guess dressed as a policy, and the dstdns core-workflow track explicitly
declined to adopt it blind on 2026-08-25 (D-204) for exactly this reason: the
just-completed wave's full-R1+R2 discipline caught two real defects (a
silently-reintroduced fix, a vacuous test oracle) that a deferred-R2 policy
would have let ship.

### Concrete shape (illustrative, not a commitment)

```toml
# ciu.global.toml (or a sibling, e.g. ciu.lane-timing.toml) — per CIU instance
[lane_timing."gate.schema"]
last_duration_s = 41.2
last_run_at = "2026-08-25T14:03:11Z"
last_worktree = "p132-worker-io-execution-repair"
last_commit = "9b334adf"
last_outcome = "pass"
p50_duration_s = 38.7      # rolling stat across N most recent invocations
p95_duration_s = 52.0
sample_count = 14

[lane_timing."gate.assay-dlq"]
last_duration_s = 612.4    # an R2-class lane, visibly the expensive one
...
```

A controller could then ask CIU (or a thin CLI: `ciu lane-timing show
<lane>`) "what does this lane typically cost" BEFORE deciding whether to
gate synchronously or dispatch it async and merge provisionally pending its
result — an informed skip, not a blind one.

### Why CIU owns it, not run-gate or assay

`run-gate` invokes lanes but is deliberately mechanics-only per CIU-40's own
layering (argv/docker/cgroup/pin-verify — no judgment, no persisted state
across runs). `assay` owns mutation-judgment policy, not timing. CIU already
owns runner LIFECYCLE (the worktree/instance a gate runs inside,
`ciu.global.toml`/`ciu.env` as the existing per-instance persisted-state
surface) — it is the only layer with both (a) a stable per-instance identity
to key measurements against and (b) a config file already treated as the
place durable CIU-instance facts live. Timing capture itself could be a thin
wrapper CIU hands `run-gate` (start/stop timestamps around the invocation it
already shells out to) rather than CIU parsing lane semantics.

### Proposed contract (not yet designed — this entry makes the ask findable)

- CIU records start/end wall-clock time + outcome (pass/fail/error) for
  every lane invocation it launches, keyed by lane name + instance identity
  + commit.
- Persisted to a CIU-owned file (`ciu.global.toml` lane-timing table, or a
  sibling file if commingling with deployment identity config is
  undesirable — needs a design call).
- A query surface (`ciu lane-timing show <lane>` at minimum) so a controller
  can read recent/typical duration before deciding sync-vs-async/defer.
- Explicitly OUT of scope for this entry: CIU does not decide the
  rigor/defer POLICY itself — that stays a consumer (dstdns controller)
  decision informed by the data CIU now provides. CIU's job stops at
  measuring and persisting.

### Oracles

Not yet written — needs a design pass first (file location, retention/rollup
policy — unbounded history vs. rolling window, concurrent-instance write
safety if two worktrees' gates run simultaneously against the same
`ciu.global.toml`).

- Controlled wrong implementation to watch for once built: recording only
  the LAST invocation with no rolling stat — a single slow outlier run (host
  under load from an unrelated process) would then look like the lane's
  permanent cost, defeating the entire point of informed decision-making.

**SPEC ownership:** new surface — no existing SPEC.md section owns
invocation-timing telemetry; nearest neighbor is CIU-40's runner-lifecycle
split. v8-timed: this is new persisted state and a new query surface, not a
same-day patch on current CIU, and dstdns's own merge-rigor decision (D-204)
does not block on it — it explicitly deferred adopting any lighter policy
until this kind of data exists.

## CIU-56 — the gate's hook-template coverage is scheduling luck, not measurement

**Filed by:** ciu-P26, 2026-08-25, after the 100% gate flipped to 99.85% on a
change that touches no hook code whatsoever.

### Observed mechanism (reproduced, not inferred)

`hooks_runner._load_hook_module` loads a hook the way CIU really does — by
FILE PATH, via `importlib.util.spec_from_file_location`, under a synthetic
module name `_ciu_hook_<stem>_<id>` that is deliberately not inside the `ciu`
package namespace. **`--cov=ciu` does not measure such a module at all**
unless the same file was ALSO imported normally, as
`ciu.hook_templates.post_compose_db`, earlier in that same worker process:

```
$ .venv/bin/python -m pytest \
    "tests/tests/test_ciu_scaffold_hooks.py::test_shipped_template_run_defaults_ready_and_reports_missing_secret" \
    --cov=ciu --cov-branch --cov-report=term-missing -q -n 0
src/ciu/hook_templates/post_compose_db.py   19  19   4  0    0%   27-82
1 passed

$ # ... the SAME test, preceded in-process by the normal-import test:
$ .venv/bin/python -m pytest \
    ".../test_shipped_template_module_shape" \
    ".../test_shipped_template_run_defaults_ready_and_reports_missing_secret" \
    --cov=ciu --cov-branch -q -n 0 -p no:randomly
src/ciu/hook_templates/post_compose_db.py   19   6   4  1   61%   46, 75-82
```

The test passes and `run()` genuinely executes in BOTH runs. Only the second
one is measured. So the shipped template's `run()` body is not actually
covered by the gate; it merely LOOKS covered whenever xdist happens to place
the two kinds of test in one worker.

`run-ciu-tests.py` runs `-n auto` with xdist's default `--dist load`, which
distributes test-by-test and therefore SPLITS `test_ciu_scaffold_hooks.py`
across workers. Whether the co-location happens is scheduling luck, and the
luck changes with the suite's test COUNT.

### Why this is a gate-integrity bug, not a flake to retry

Reproduced on the **clean baseline** (`HEAD = 6f80e2cf`, zero source changes)
by adding one file of 122 trivial `assert True` tests: 2 of 3 `-n auto` runs
then reported `post_compose_db.py` at 17% and the gate at 99.85%. Removing
that file restored 6/6 green. ciu-P26's own +122 real tests reproduce it 6/6.
Every future package that adds tests will keep tripping it, and — worse — the
15 statements in question have never really been measured.

Green under `-n 0` (serial), and under `-n auto --dist loadfile`, which keeps
a file's tests in one worker. `run-ciu-tests.py` forwards extra argv, so
`.venv/bin/python run-ciu-tests.py --dist loadfile` is green today.

### Proposed fix (needs a file outside ciu-P26's scope.touch)

Either, or preferably both:

1. `tests/tests/test_ciu_scaffold_hooks.py` — add a module-level
   `import ciu.hook_templates.post_compose_db  # noqa: F401` so EVERY worker
   that runs any test from that file has the module normally imported, making
   the path-loaded execution measurable regardless of scheduling. One line,
   no behavior change.
2. `run-ciu-tests.py` — add `--dist loadfile`, so a test file's coverage can
   never depend on cross-worker placement again. This is the general fix; the
   same latent trap applies to any other file whose coverage needs two
   different tests to share a process.

A follow-up should also ask the broader question the reproducer exposes: is
any OTHER path-loaded module (consumer hooks under `tests/`, scaffolded hook
copies) silently unmeasured today?

**SPEC ownership:** none — this is gate/test-infrastructure, not normative
behavior.

## CIU-57 — `CIU_KSM` missing from the autouse ambient-env scrub fixture

**Filed by:** controller, 2026-08-25, while verifying the CIU-56 fix
(`--dist loadfile`) by re-running the gate several times in a row.

### Observed mechanism

`tests/conftest.py`'s `_scrub_ambient_identity_env` (ciu-P13) clears a
closed list of ambient env vars before every test body via
`monkeypatch.delenv`, specifically so a test's outcome cannot depend on
whatever the invoking shell (or an earlier test) happened to leave set.
`CIU_KSM` was never in that list, despite `governance.resolve_ksm_optin`
reading it fresh on every call and CHANGES.md's own history recording at
least two prior one-off fixture-level pins
(`pin CIU_KSM=off in build_repo`, `pin CIU_KSM=off in the S4.12 refresh
test`) as "flake hunt" fixes for exactly this class of leak — local
patches on individual symptoms, never a fix of the shared fixture's
actual coverage.

Live-reproduced while re-running the gate under the new `--dist loadfile`
(CIU-56's fix): `test_absolute_governance_ksm_path_is_preserved_in_overlay`
(`test_ciu_composefile_branch109.py`) intermittently failed
`KeyError: 'volumes'`. Root cause traced to
`composefile.generate_overlay` (`src/ciu/composefile.py:1227`):
`ksm_rel = governance_mod.resolve_ksm_optin(str(gov_cfg.get("ksm_optin") or ""))`
— if `CIU_KSM` is set to anything in `("0", "off", "false", "no", "")` at
call time, the test's own configured `ksm_optin` (a real shim path) is
silently overridden to empty, so neither the `BUILTIN_KSM` branch nor the
`elif ksm_rel:` branch fires, `_ksm_optin_source` is never set, and the
resulting compose service never gets a `volumes` key at all — exactly the
observed `KeyError`.

**Exact contamination vector not pinned down**: no raw (non-monkeypatch)
`os.environ["CIU_KSM"] = ...` assignment exists anywhere in `src/` or
`tests/` (grepped), the one test that sets it
(`test_spec_contracts.py:151`) does so via `monkeypatch.setenv` (which
auto-reverts at that test's own teardown), and the ambient devcontainer
shell does not have it set either. The fix closes the class regardless of
the precise vector — the same reasoning already applied to the other 6
scrubbed vars, none of which needed their leak source individually
diagnosed before being added.

### Fix

Added `"CIU_KSM"` to `tests/conftest.py`'s `_AMBIENT_ENV_VARS`. Verified:
5 consecutive full-gate runs green afterward (this specific test did not
recur as a failure across ~10 total gate runs following the fix).

**SPEC ownership:** none — gate/test-infrastructure.

## CIU-58 — shared, on-disk `test-repo/` fixture source is not test-isolated, races under parallel workers

**Filed by:** controller, 2026-08-25, found live while stress-testing the
CIU-56/CIU-57 fixes with repeated full gate runs — not caused by either.

### Observed mechanism

`test_ciu_render_selection_context.py::test_engine_threads_selection_into_configfiles_and_hooks`
failed once (of roughly 10 repeated full-gate runs) inside its own
`_add_stack` helper's `shutil.copytree(SRC_APP, dst)` call, where `SRC_APP`
is `test-repo/applications/app-config` — a real, checked-in directory in
the repository, not a fresh per-test tmp_path. The failing run's captured
`entries` listing included `ciu.compose.yml` (a rendered, normally-
gitignored artifact) and `__pycache__` alongside the template's own
source files, suggesting the shared source directory's on-disk state can
be mutated (rendered into, executed) by something else running
concurrently, and `copytree`'s directory-entry enumeration can be
racing that mutation — a classic TOCTOU between listing entries and
copying each one.

This is a **structurally different** problem from CIU-57 (no shared env
var involved) and was not investigated further: pinning down which OTHER
test, fixture, or process actually mutates `test-repo/applications/
app-config` — and whether it is another test in a sibling worker, a
leftover artifact from an interactively-run `ciu` command against this
same tree (this repo's own `test-repo/` is also used for manual smoke
testing), or something else — needs a dedicated investigation, not a
guess.

### Why not fixed now

Out of scope for this wave (identity-facts/CIU-25 groundwork), and
genuinely rare in this run (1 of ~10). Filing it rather than letting it be
rediscovered from scratch, per this backlog's own established practice.

### Proposed direction (not designed)

Enumerate every test that reads `test-repo/` (or any other shared,
non-`tmp_path` source) via `copytree`/direct read, and either: (a) isolate
one pristine copy of the needed subtree once per test SESSION (not per
test) into a location nothing else touches, or (b) generate the fixture
tree synthetically per test instead of copying from a shared, mutable,
checked-in directory at all.

**SPEC ownership:** none — gate/test-infrastructure.

## CIU-60 — Jinja TEMPLATE rendering's `env` context is still raw ambient `os.environ`; hooks got the safe S9.3 treatment, templates never did

**Filed by:** ciu-P33 (controller, from the operator architecture discussion
of 2026-08-25 — the same devcontainer live-bug thread that produced CIU-53),
alongside the fix, so a future reader finds the "why" without re-deriving it.

### The operator's question

Verbatim in substance: *"should the `env` / `ciu.global.defaults.toml.j2`
usage be reconsidered for every `ciu` verb"* — followed by the three
constraints that closed the design: *"we cannot write generated vars to
`ciu.global.toml.j2` because this gets committed"*, *"we could write to
`ciu.global.toml` instead of using `ciu.env`"*, and (separately) *"`ciu env
apply` or `ciu env source`"* / *"does `ciu clean` also remove
`ciu.global.toml`? … maybe have a `ciu clean --vanilla`"*.

### Observed gap

S9.3 already stops hooks from trusting ambient state: a hook receives
`ctx.instance_id`/`ctx.network` read from THIS workspace's own `ciu.env` by
exact path. S3.2's template render context never got that treatment — `env`
is raw `os.environ`. So the documented convenience of a login shell having
sourced a sibling checkout's `ciu.env` (the CIU-41/CIU-47/CIU-53
contamination path, live-reproduced three times this session) makes a
template's `{{ env.PHYSICAL_REPO_ROOT }}` render the OTHER checkout's host
path — silently, into a bind mount, with no error anywhere. CIU-53 closed the
level ABOVE this (which repo a verb operates on); this is facts ABOUT the
already-discovered workspace.

### Why the obvious fix was rejected

The first controller proposal was a fresh-every-render Jinja context
injection: `ciu.physical_repo_root` and friends, computed per render. The
operator rejected it, correctly — it manufactures a variable that appears
from nowhere, backed by no file, that cannot be inspected or diffed. That is
the same "magically available var" hazard the whole session was fighting;
trading an ambient value for an invisible one is not a fix.

### Resolution (shipped by ciu-P33)

SPEC S3.1b gains a CIU-owned `[ciu.instance.generated]` table inside the
already-shipped, already-gitignored, already-clean-surviving per-checkout
overlay `ciu.global.worktree.toml.j2` — CIU-52's exact precedent, a different
field. `ciu env generate` upserts six snake_case keys (`repo_name`,
`instance_id`, `network`, `physical_repo_root`, `repo_root`, `public_fqdn`)
from the SAME in-memory values it writes to `ciu.env`, never re-derived and
never read back. Templates then reach them as
`{{ ciu.instance.generated.physical_repo_root }}` through the merge
`render_global_chain` ALREADY performs on this file — no new context-building
code anywhere, and a real file behind every value. The write is not gated on
an S16 instance record, because the read side is not either: gating would have
left the primary/main checkout (where the operator was standing) unfixed.

The write is a text-level surgical block replace, not a `tomllib` +
`tomli_w` full-file round-trip: the latter carries every VALUE across
correctly while destroying every comment and reformatting every table in a
file S3.1b explicitly invites operators to edit. CIU owns exactly the bytes
from its table header to the next table (minus that region's trailing
comment run, which belongs to the following table); everything else survives
byte for byte, forever. Hand-edits inside the owned table are silently
overwritten, and the block says so inline.

`ciu.global.toml` was rejected as the destination on a mechanical fact: it has
no state preservation. Only a stack's own `ciu.toml` preserves `[state]`
across re-render (S3.4); the global rendered file is regenerated whole from
its source layers on nearly every verb.

The two QOL asks from the same conversation shipped with it:

- **`ciu env print`** (S10.1) — prints the existing `ciu.env` as `export
  KEY='value'` lines for `eval "$(ciu env print)"`. Deliberately NOT `apply`
  or `source`: a subprocess cannot mutate its parent shell's environment, so
  either of those names would document a capability no implementation can
  provide.
- **`ciu clean --vanilla`** (S6.4b) — additionally removes `ciu.global.toml`,
  `ciu.env` and `ciu.global.worktree.toml.j2`. Purely additive; plain `clean`
  still leaves all three untouched (regression-guarded), and `--vanilla` runs
  only after a teardown that actually succeeded.

### Side-finding: the file SPEC calls gitignored was not, here

SPEC S3.1b and CIU's own published `.gitignored.ciu` sample rules both list
`ciu.global.worktree.toml.j2` as gitignored. CIU's OWN `.gitignore` did not —
invisible until now, because nothing wrote this file at a TRACKED repo root:
`_write_worktree_overlay`'s only call site is worktree creation, always into
a fresh checkout. With `env generate` now upserting into it unconditionally,
the integration suite (`test_ciu_test_repo.py`, which generates into the
committed `test-repo/` fixture) immediately left an untracked file that would
red the dirty-tree gate (S18.4) on every run. Fixed in the same commit by
adding `**/ciu.global.worktree.toml.j2`. The consumer-facing half of the same
omission — `ciu init`'s scaffolded `.gitignore` — is filed as **CIU-61**;
`scaffold.py` was outside this package's scope.

**SPEC ownership:** S3.1b (`[ciu.instance.generated]`), S6.4b
(`clean --vanilla`), S10.1 (`ciu env print`).

## CIU-61 — FIXED (ciu-P44) — `ciu init`'s scaffolded `.gitignore` omits `ciu.global.worktree.toml.j2` (and two others)

**Filed by:** ciu-P33, 2026-08-25, as the named follow-up from the
`.gitignore` side-finding above.

### Observed

`scaffold.py`'s `_GITIGNORE_ENTRIES` writes four entries into a consumer's
`.gitignore`: `ciu.env`, `ciu.global.toml`, `**/.ciu/`,
`**/ciu.compose.yml`. CIU's own published `.gitignored.ciu` sample-rules file
lists eight, including `ciu.global.worktree.toml.j2`,
`ciu.worktree-instance.json`, `**/ciu.toml` and `**/ciu.toml.j2`. Two
hand-maintained copies of one list, already drifted.

### Why it matters now

Before CIU-60, `ciu.global.worktree.toml.j2` only ever appeared in a checkout
a managed worktree command had just created. `ciu env generate` now upserts
`[ciu.instance.generated]` into it on every run, in every checkout including
the primary one. A freshly `ciu init`-ed consumer repo therefore gains an
untracked file carrying machine-specific facts — this developer's host path
(`physical_repo_root`), instance id, and public FQDN — which is exactly the
class of value that should never reach a tracked file, and which an
unsuspecting `git add -A` will commit.

### Proposed direction (not designed)

Reconcile the two lists. Prefer deriving `_GITIGNORE_ENTRIES` from
`.gitignored.ciu` (or vice versa) over adding a fifth literal to a list that
has already proven it drifts; the per-entry "why" comments the scaffolder
attaches would need a home in whichever file becomes the source.

**SPEC ownership:** S19.1 (`ciu init` scaffold), S3.1b (the file's declared
gitignored status).

## CIU-71 — a stack's relative `build.context` resolves against the compose file's own directory, not the repo root, because `ciu` never passes `--project-directory`

**Filed by:** dstdns-P147b, 2026-08-30 (`dstdns@1171d8d3`,
`nyxloom-trove/decisions.md` D-244; worktree
`/workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e`).

### Observed

`ciu up --dir infra/mock-targets` (dstdns) failed:

```
resolve : lstat /workspaces/dstdns/.worktrees/p147b-vertical-corpus-e2e/infra/mock-targets/tests: no such file or directory
```

`infra/mock-targets/ciu.defaults.toml.j2` declares
`[mock_targets.image] build_context = "."`, rendered into the stack's
`ciu.compose.yml.j2` as a relative `build: context: .`. The stack's own
Dockerfile `COPY`s from `tests/` (a repo-root-relative path, e.g.
`COPY tests/fixtures/mock_data ...`), which only resolves correctly if
Compose treats `.` as the REPO ROOT. It does not: `docker compose`
resolves a relative build `context:` against the **compose file's own
directory** (`infra/mock-targets/`) unless invoked with
`--project-directory <repo-root>` — which `ciu` never passes (grepped the
installed `ciu` package's own source: no such flag exists anywhere in its
`docker compose` invocation call sites).

### Why this was invisible until now

`infra/mock-targets` is the ONLY stack in the dstdns consumer repo with a
`build:` section at all — every other stack references a pre-baked
`image:` tag, so `ciu`'s compose invocation never needed to get a build
context right before. dstdns-P147b's own package was the first-ever bring-up
of `infra/mock-targets`.

### Workaround used (disclosed, not a fix)

An untracked, gitignored, per-instance `infra/mock-targets/ciu.toml.j2`
overlay setting `build_context = "../.."` (stack-relative, since Compose's
default DOES resolve against the compose file's own directory) — destroyed
automatically at `ciu worktree rm`, never reaching the tracked checkout.

### Fix

Two independent directions, either sufficient alone:
1. **ciu (this repo):** always invoke `docker compose` with
   `--project-directory <repo-root>` (`PHYSICAL_REPO_ROOT`/`REPO_ROOT`
   already resolved elsewhere in the codebase) so every stack's relative
   paths — build contexts included — resolve against the repo root
   uniformly, matching what a stack author would reasonably expect from a
   tool that already centralizes path resolution for bind mounts.
2. **Consumer-side (no ciu change):** document that a stack's own
   `build.context` must be REPO-ROOT-relative as authored (e.g. `"."`
   really means "the compose file's own directory", so a Dockerfile that
   `COPY`s repo-root-relative paths must instead declare
   `build_context = "../.."`-style paths, stack-relative). Weaker: every
   future stack author must rediscover this the same way P147b did.

**SPEC ownership:** wherever `ciu`'s compose-invocation construction lives
(the `docker compose ... up -d` call site building the argv from rendered
stack config) — not yet cross-referenced to a SPEC section number by this
filer; the next triager should locate and record it.

## CIU-72 — v8 gate: the LaneResult must carry the verdict's `helpers[]`; `ciu check`/`ciu gate doctor` must verify environment fitness for language-bound assay lanes via `assay lanes --json`; `request_base` is derivable, not restated

**Filed by:** the assay 3.1.0 design review, 2026-08-30
(`assay/nyxloom-trove/reports/assay-3.1-js-adapter-design-review-2026-08-30.md`
§4 D3; assay **B044** is the judge-side half). **SPEC ownership:** SPEC-V8
S16.9 (LaneResult), S15.3 stage 12, S16.4/S16.5, S16.10 (`ciu gate doctor`).
Pointer notes were added at those sections in the same pass (operator ruling
2026-08-30: file AND annotate).

### Observed (in the spec, before any code exists)

1. S16.9's LaneResult copies `judge_provenance` and the resolved `REF` from
   the verdict and nothing else. assay's verdict also carries `helpers[]`
   (`verdict.schema.json` `properties.helpers`: `role`/`tool`/
   `resolved_path`/`identity` — "every external helper an adapter actually
   invoked… so a coverage or mutation claim is reproducible against a known
   tool identity", assay A-230a). The Go adapter (assay A-217/A-239) records
   its statement-position oracle there. A Go verdict's gate envelope without
   it is not reproducible from the LaneResult alone — exactly the property
   S16.9 exists to guarantee for `judge_provenance`.
2. S15.3 stage 12 reads `assay.toml` for lane names only (a deliberate
   boundary; keep it). Consequence: a `judge.language = "javascript"` lane
   in an environment without Node, or a Go lane without its helper, passes
   `ciu check` and fails at run time — `NO_MEASUREMENT`/
   `MISSING_EXTERNAL_TOOL` at best; with `npx` and no `--no-install`, an
   unpinned registry fetch (assay B041). The fitness fact is knowable
   statically.
3. S16.5's `request_base = true` restates the assay lane's own
   `judge.base_source = "request"` — one fact, two spellings (the proposal's
   P1); when they disagree the failure is assay's run-time refusal, not a
   `ciu check` finding.

### Fix

- **S16.9:** `helpers?: [...]` copied verbatim from the verdict (present iff
  the verdict carries it; never `[]`).
- **S15.3 stage 12 / S16.10:** when the judge is reachable in the lane's
  environment (the same "reachable" as the S16.3.1 floor check — one exec,
  not two), run `assay lanes --json --file assay.toml` (assay B044,
  `inventory_schema: 1`) once per gate run and, per assay lane: (a) every
  `external_tools` entry and `argv0` resolves on PATH inside the environment
  (`command -v`), else ERROR `[S16.4] lane 'ui-unit' needs 'node' in
  environment 'tester'`; (b) `request_base` is derived from `base_source ==
  "request"`; an explicit `request_base` key stays legal as a restatement
  that must AGREE (`[S16.5] request_base = false but assay lane 'p129…'
  delegates its base`). `ciu gate doctor` prints the per-lane table.
- Boundary respected: ciu never parses `assay.toml` beyond lane names; it
  asks the judge, exactly as it asks `assay --version`.

### Backport to the current gate (run-gate v23) — filed 2026-08-30

(b) and (c) are backportable on run-gate's current `schema_version = 1`
(the proposal's own §4.11 "ship now" class) and are filed as run-gate
**RG-25** (per-lane toolchain fitness in `doctor`/`--check-env`) and
**RG-26** (`run-gate <lane> --base REF` → `--request-base REF`, with the
delegating lanes DERIVED from `assay lanes --json` — no restated key at
all, which v7 gets for free and S16.5 should keep in mind). Both depend on
assay B044. (a) is v8-only: run-gate emits no LaneResult envelope of any
kind (`run-gate.py` writes no result JSON — the verdict file IS the result,
and it already carries `judge_provenance` and, later, `helpers[]`), and
the proposal freezes run-gate at v23 with a one-release overlap.

### Acceptance

- [ ] S16.9/S15.3/S16.5/S16.10 amended (the pointer notes become normative
      text); a LaneResult fixture carrying `helpers`;
- [ ] a check-stage test: a javascript lane + an environment image without
      `node` → ERROR naming lane/tool/environment; with `node` → passes;
- [ ] a `request_base` disagreement test;
- [ ] the floor check and the inventory call share one judge-reachable
      code path.

---

## CIU-73 — v8 demo/spec: a language-bound (`javascript`) assay lane, and the dependency-closure cache on the tester environment; say once that closures are the environment's

**Filed by:** the assay 3.1.0 design review, 2026-08-30 (§3 G1, §4 D5;
assay **B041** is the judge-side half). **SPEC ownership:** S16.4
(`extra_mounts`, `image_from`), S16.5, Appendix A (migration); the demo's
`[testing]` and `assay.toml`.

### Observed

- assay's snapshot is `git read-tree <commit>` into a temp dir — committed
  objects only (assay `isolation.py:577`, A-161/A-184). A JS lane's
  `node_modules` (gitignored, in-tree) never exists inside it; Python (venv)
  and Go (`GOMODCACHE`) closures are out-of-tree and never met this.
- dstdns's `tools/test-runner` image ships Node and runs `npm ci` into the
  bind-mounted checkout at gate time (`Dockerfile:17-43`); nothing reaches
  the snapshot.
- The v8 demo's only UI lane is `kind = "command"` (typecheck + browser
  tests); no assay lane in the model is language-bound, so nothing exercised
  whether an S16.4 environment can express "this lane's closure lives here".

### Fix

- **S16.4**, one sentence: an environment also provides a lane's dependency
  closure — an offline package cache baked into the image or mounted via
  `extra_mounts`; assay's lane rebuilds the in-tree closure offline from the
  committed lockfile (assay B041 pattern (a)); `link_paths` (assay B041 (b))
  is the lane's declared alternative and is recorded in the verdict.
- **Demo** (done in this pass, with pointers to this entry):
  `[testing.environments.tester] extra_mounts = ["/var/cache/dstdns/npm:/opt/npm-cache"]`;
  `[testing.lanes.ui-unit] kind = "assay"` → assay lane `ui_unit`
  (`javascript`, offline `npm ci`, `--no-install` runner, `producer` to
  follow assay B045); the demo README row.
- **Appendix A** migration note: the tester image bakes the npm cache from
  the committed lockfile (dstdns-side), or the host provides the directory
  named in `extra_mounts`.

### Backport to the current gate (run-gate v23) — filed 2026-08-30

Needs no ciu or run-gate code at all: `exec` environments get the cache
from the runner STACK (baked into the image from the committed lockfile, or
a compose volume in the runner's `ciu.compose.yml.j2` — consumer config);
ephemeral environments use `RUN_GATE_EXTRA_MOUNTS=<host>=/opt/npm-cache`
(`run-gate.py:34`, existing knob). The doc sentence now lives in run-gate
`CONSUMERS.md` (`kind = "assay"` section, "Where a lane's dependency
closure lives", 2026-08-30). Only the demo/spec half of this entry is v8's.

### Acceptance

- [ ] S16.4 sentence + Appendix A note;
- [ ] the demo lane validates (`validate_demo.py`) and CIU-72's stage-12
      check reports `node` fitness for it;
- [ ] `ciu gate doctor` lists the mount.

---

## CIU-75 — FIXED (ciu-P42) — backport v8 F2 identity: the overlay becomes the sole instance-fact source, `ciu.env` demoted to a legacy write-only export (BREAKING, ships as ciu 7.7.0)

**Status: FIXED 2026-08-31 by ciu-P42**, pending release as **ciu 7.7.0**.
All four acceptance criteria met — see the checked boxes at the end of this
entry. Implementation: SPEC **S3.1c** (new section, identity-source
precedence), `workspace_env.read_generated_facts` / `has_generated_facts` /
`identity_env_from_facts` / `read_instance_identity_env` /
`seed_identity_env`, twelve migrated call sites plus the STEP-1 environment
seed, `tests/tests/test_ciu_identity_cutover_ciu75.py`, `docs/CONSUMERS.md`
§11b, `CHANGES.md` `[7.7.0]`. Report:
`nyxloom-trove/reports/ciu-P42-REPORT.md`.

**Round-1 review: the first implementation was REJECTED as an incomplete
cutover, and completing it — not narrowing the claim — is what closed this.**
The twelve sites of the gap analysis below were real but were not the whole
surface. `bootstrap_workspace_env` — STEP 1 of `ciu up`/`check`/`render`/
`graph` — still seeded `os.environ` from `ciu.env`, and it seeded
skip-if-present, so an inherited value was never displaced. Roughly 26
internal sites read `REPO_ROOT` / `PHYSICAL_REPO_ROOT` /
`DOCKER_NETWORK_INTERNAL` / `PUBLIC_FQDN` straight from ambient, and so does
every `$VAR` in a rendered config — including the shipped
`network_name = "$DOCKER_NETWORK_INTERNAL"`. A shell that had sourced a
SIBLING checkout's `ciu.env` therefore still won, with nothing corrupted and
nothing hand-edited (the reviewer reproduced a render naming the sibling's
network). Clause 1 of S3.1c was true of the twelve sites and false of the
product. Now S3.1c clause 2a seeds the six facts from the table, OVERRIDING
ambient; `ciu.env` is read at startup for MACHINE facts only, by exact path,
and cannot abort a verb. Three further round-1 findings landed with it: the
deprecation notice was breaking `ciu check --json` (stdout, ahead of the
document) and is now on stderr for bootstrap-triggered regeneration; `ciu
worktree reap` now reports a checkout it could not delegate to `ciu clean`
instead of silently bare-`docker rm`-ing it; and the stale
`ciu.env`-as-read-source claims left standing across SPEC and CONSUMERS were
swept.

**Version note (2026-08-31, controller):** originally filed targeting
`ciu 7.6.0`. The Checkpoint-1 backlog wave (CIU-62/63/64/65/67/68/69/70/71/74
+ CIU-76 test-determinism fix) released first and consumed `ciu-v7.6.0` as
its own natural minor bump. This entry now targets **ciu 7.7.0** instead —
still an explicit override of the estate's normal "breaking waits for the
next major" convention (the number moved by one, the deliberate-override
reasoning below is unchanged).

**Filed by:** operator directive, 2026-08-31. The v8 proposal's F2 fork
(`docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §4.3.1 table, §4.1.3 "Identity
source") decided: identity facts move fully into the overlay TOML, `ciu.env`
becomes legacy output — "the main repo is also a worktree — just a special
one." CIU-60 (FIXED, current 7.5.x) already upserts
`[ciu.instance.generated]` into the gitignored overlay
`ciu.global.worktree.toml.j2` on every `ciu env generate`, but per the v8
doc's own V8-2 gap note, `ciu.env` **remains the thing ciu internals
actually read** — the overlay write was additive, not a cutover. The
operator's instruction: backport the cutover half of F2 now, ahead of v8's
other schema-revision-8 changes (file renames, `[testing]` absorption, etc.
— those stay v8-only), released as **ciu 7.7.0** (see the version note
above) — an explicit override of
the estate's usual "breaking changes wait for the next major" convention,
because this specific change is self-contained and does not require v8's
other, much larger schema changes to ship safely on its own.

### The gap

`ciu.env` is read (or existence-checked) by exact path at 12 call sites,
source-confirmed 2026-08-31: `worktree.py:1137,1229,2635,2884,3250,3859,4266`;
`engine.py:948,1182,1484`; `deploy.py:1882,2838`. This is a materially larger
surface than the v8 proposal doc's own citation (`paths.py:70/78`,
`workspace_env.py:1167ff`), which was either illustrative or written against
a different checkout state — do not trust it as the exhaustive site list.
Each of the 12 needs individual classification before a fix can be called
complete:

- some are plain **existence checks** (`.is_file()`) used as a worktree/
  instance-readiness signal — these may be fine to keep, or may need to
  check for the overlay's presence instead, depending on what "ready" means
  post-cutover;
- some **read identity/instance fields** out of the file's contents — these
  are the ones that must move to reading the merged overlay/rendered config
  instead;
- none should be assumed identical in shape to the two the v8 doc names;
  each is a real read of real production code and needs its own before/
  after pair, not a blanket sed.

### Proposed contract

1. Every site above that reads `ciu.env` for **facts** (not merely checking
   its existence) switches to reading the same facts from the merged
   overlay / rendered instance config — the same source CIU-60 already
   populates.
2. `ciu env generate` keeps **writing** `ciu.env` — unchanged key set, so a
   consumer's login shell or cockpit alias that still does
   `source ciu.env` keeps working byte-for-byte. Only the READ side inside
   ciu itself moves; this is a write-only legacy export from here on.
3. `ciu env print` (shipped, CIU-60) is documented as the forward path;
   `ciu.env`-sourcing consumers get one release of a WARN (not a refusal)
   nudging them toward `eval "$(ciu env print)"`, matching this codebase's
   existing one-release-WARN-then-refuse precedent (e.g. the
   `ciu.global.worktree.toml.j2` rename in the v8 migration shape).
4. A test proves the cutover, not just the intent: delete or corrupt
   `ciu.env` after a normal `ciu env generate`, then exercise every verb
   whose 12 call sites above read facts from it — none may change behavior
   or fail. Only the overlay/rendered config is load-bearing afterward.

### Why this is breaking, and why 7.6 anyway

Any external tooling that relies on ciu **regenerating** `ciu.env` in
response to identity changes it detects by reading `ciu.env` back (a
plausible but unconfirmed pattern — needs a grep across dstdns and any other
consumer before release) breaks silently unless it's re-pointed at the
overlay or `ciu env print`. This is exactly the kind of "shipped = merge +
release + consumer migration notes" case AGENTS.md's user-facing-docs rule
exists for: CONSUMERS.md gets a dedicated migration section, and
CHANGES.md's entry is marked BREAKING, before this can be called done.

### Acceptance

- [x] all 12 sites individually classified (existence-check vs. fact-read)
      and, for fact-reads, migrated to the overlay/rendered config — 11
      fact-reads migrated; 1 (`worktree._reap_uses_clean`) was a pure
      existence check and was RE-POINTED at the overlay's generated table
      rather than left alone, because post-cutover `ciu clean` derives its
      identity network and identity project from that table, so the old check
      would have certified a readiness that no longer holds. Per-site table
      in the report. **Plus the 13th site the gap analysis did not name and
      review round 1 found**: `bootstrap_workspace_env`'s seeding of
      `os.environ`, which fed every ambient reader in the process (S3.1c
      clause 2a). A per-site migration alone left the sibling-checkout leak
      wide open;
- [x] `ciu env generate` still writes `ciu.env`, byte-identical key set, one
      release of WARN pointing at `ciu env print` — the WARN is emitted by
      BOTH `ciu env generate` (stdout) and `ciu env` (stderr, so the verb's
      `key=value` stdout stays parseable). **Stream corrected in round 1**: a
      regeneration triggered from inside another verb's STEP 1 announces on
      stderr, because `deploy._run` is `ciu check --json`'s entry point and a
      stdout notice there preceded the JSON document (S3.1c clause 3);
- [x] the delete-`ciu.env`-after-generate test above, green, covering every
      verb touched — `tests/tests/test_ciu_identity_cutover_ciu75.py`, which
      runs the whole site sweep TWICE (deleted, and replaced by undecodable
      bytes) plus a converse case proving the overlay is what carries the
      answers. **Round 1 added the oracle that the site sweep could not
      provide**: a hostile/stale ambient identity driven through a REAL verb
      (`ciu secrets list` — full STEP 1 + `render_global_chain`, no daemon),
      asserting the RECORD reaches `deploy.network_name` and the sibling value
      does not. The site sweep drove the twelve helpers directly, which is
      exactly why it could not see the seeding hole; `tests/conftest.py`'s
      autouse ambient-identity scrubber meant no other test could either;
- [x] CONSUMERS.md migration section + CHANGES.md BREAKING entry — landed;
      **release as ciu 7.7.0 still pending** (tag/release is the merge step's,
      not this package's);
- [x] a grep across `dstdns` (and any other reachable consumer) for
      `ciu.env`-reading tooling — dstdns WAS reachable
      (`/workspaces/dstdns`, HEAD `a1098ad8`; **re-audited exhaustively in
      round 1 at `dstdns@96fcf762`**, which found THREE importers of the
      vendored stub rather than one, a sibling `config_constants.py` stub the
      first pass missed, and no key-extraction shell site at all — corrected
      inventory in CIU-82 and in `docs/CONSUMERS.md` §11b). The risk is
      **confirmed, not ruled out**: all shapes still work this release, and
      all are named in §11b. The load-bearing one is
      `dstdns/scripts/ciu/workspace_env.py`, a vendored stub that PARSES
      `ciu.env` into a dict for `scripts/config_helper.py` inside the
      test-runner container — a second implementation of a read CIU has now
      moved. **Follow-up owed in dstdns's own backlog** (out of this
      package's worktree scope): re-point that stub at the overlay's
      `[ciu.instance.generated]` table before `ciu.env` stops being written.
      See CIU-82 below for the ciu-side tracking of that notification.

**SPEC ownership:** RESOLVED — `docs/SPEC.md` **S3.1c** ("Identity-source
precedence", CIU-75) is the new normative section. S2.7 still covers
derivation and S3.1b the write side; S3.1c owns which record CIU READS, the
reader's three outcomes, the readiness signal, and the child/candidate
environment rule.

---

## CIU-82 — notify dstdns: its vendored `ciu.env` parser must move to the overlay before `ciu.env` stops being written

**Filed by:** ciu-P42 (CIU-75 implementation), 2026-08-31. (Numbered 82, not 81:
CIU-81 was already taken by ciu-P38's scaffold.py `StrictUndefined` filing —
the real-ID-collision hazard this backlog has hit before.)

CIU-75's consumer sweep found `dstdns/scripts/ciu/workspace_env.py` — a
vendored stub reimplementing `load_workspace_env`/`ensure_workspace_env` by
PARSING `ciu.env`. It is reachable as `ciu.workspace_env` only inside the
test-runner container, which puts `scripts/` on `PYTHONPATH`
(`tools/test-runner/Dockerfile:168`, `tools/test-runner/ciu.compose.yml.j2:36`)
and has no real `ciu` wheel; in the devcontainer the installed package shadows
it entirely. `scripts/ciu/` has no `__init__.py` — it is a PEP 420 namespace
package, which is why that shadowing works at all.

**Re-audited 2026-08-31 at `dstdns@96fcf762` (ciu-P42 review round 1). Three
importers, not one, plus a sibling stub the first audit missed:**

- `scripts/config_helper.py:30` —
  `from ciu.workspace_env import load_workspace_env, ensure_workspace_env, WorkspaceEnvError`;
  calls at `:140-142`, then reads `DOCKER_NETWORK_INTERNAL` at `:145`. Its
  error text at `:148-149` names `ciu.env`.
- `scripts/url_builder.py:18` — the same three symbols; `load_workspace_env`
  at `:74`, `ensure_workspace_env(["REPO_ROOT"])` at `:75`. NOT named in the
  first audit.
- `tests/smoke/test-deployment-validation.py:144-148` — a `sys.path` hack that
  inserts `scripts` and then imports bare `workspace_env`, which lives one
  directory deeper. `scripts/workspace_env.py` does not exist, so this probe
  has only ever raised `ModuleNotFoundError` into a swallowed
  `"Failed to read ciu.env: {stderr}"` at `:151-153`. Pre-existing, not caused
  by CIU-75, but it is in the code that must be re-pointed.
- `scripts/ciu/config_constants.py` — the sibling stub both live importers
  also depend on (`url_builder.py:17`, `config_helper.py:31`, for
  `GLOBAL_CONFIG_RENDERED`); its own docstring says it mirrors ciu's. Any
  migration touches this package too. Two adjacent defects the same sweep
  found: `scripts/render_template.py:20` imports `STACK_CONFIG_ACTIVE`, which
  exists in NEITHER the stub nor real ciu (so that module is unconditionally
  unimportable, and four `infra/*/start.sh` scripts invoke it), and
  `tools/admin-debug/render_ddcli_config.py:44` hardcodes
  `GLOBAL_CONFIG_RENDERED = "ciu.global.toml"` rather than importing it.

**Shell — NINE `source` statements across EIGHT files** (count corrected in
review round 2; the earlier "six places" was wrong). Re-verified line by line
at `dstdns@e1712adc`:

| file:line | note |
|---|---|
| `scripts/ciu-env.sh:66` | the canonical loader; asserts `REPO_ROOT`/`PHYSICAL_REPO_ROOT` at `:68`; sourced in turn by `scripts/devcontainer-exec.sh:28` and `scripts/admin-debug-exec.sh:26` |
| **`scripts/devcontainer-exec.sh:96`** | **the migration-relevant one — see below** |
| `env-workspace-setup-generate.sh:256` | generate-then-source |
| `.devcontainer/finalize.post.d/10-dstdns-ciu.sh:29` | post-create |
| `.devcontainer/finalize.post.d/10-dstdns-ciu.sh:68` | the same file again, inside the `~/.bashrc` block it writes — the ambient source for every interactive shell |
| `.vscode/run-ciu-render.sh:12` · `run-ciu-render-all.sh:13` · `run-deploy.sh:16` · `copilot-cmd.sh:53` | editor task wrappers |

**`scripts/devcontainer-exec.sh:83-104` (`get_network_name`, called at `:148`
and `:183`) is the site to migrate first**, and it was missing from this
entry's first version. It sources `ciu.env` *specifically to fetch*
`DOCKER_NETWORK_INTERNAL` and hard-fails when the file is absent
("ciu.env not found - run env-workspace-setup-generate.sh") — live code
consuming one identity fact, i.e. exactly the shape CIU-75 moved, whose
whole-file `source` is incidental. It escaped the first audit because the
source target is the VARIABLE `"$env_file"`, not the literal `ciu.env`: a
lesson for the next sweep of this kind, since a grep for the filename beside
`source` finds eight of the nine and misses the one that matters most.

There is NO `grep`/`sed`/`awk`/`cut` extraction from `ciu.env` anywhere in
dstdns — every site above is a whole-file `source`, which keeps working. The
one grep-the-file recipe is in a HANDOFF DOC
(`nyxloom-trove/handoffs/dstdns-P147-vertical-corpus-e2e.md:473`) and is
already wrong: it greps `CIU_INSTANCE_ID`, a key `ciu.env` has never had.
Note `scripts/ciu-env.sh:48-50` documents "ciu.env wins over anything already
in the environment" — post-CIU-75 that precedence AGREES with ciu's own
(S3.1c clause 2a), where before it could silently disagree.

**Not affected, and worth stating so nobody migrates it needlessly:** dstdns's
templates consume all six identity keys as `$VAR`
(`ciu.global.defaults.toml.j2:51,52,74,75,76,134,135,136,431,438…`, plus a
dozen `${PHYSICAL_REPO_ROOT:?…}` compose guards). Those resolve from the
process environment ciu itself now seeds from the overlay, so they are
strictly MORE correct after 7.7.0 — a stale sibling value can no longer reach
them.

This is not ciu's code to change, and the estate rule is that a finding about
a consumer is filed in the CONSUMER's backlog. This entry exists only so the
notification is not lost between repos.

- [ ] file the equivalent item in dstdns's backlog (re-point the stub at
      `ciu.global.worktree.toml.j2`'s `[ciu.instance.generated]` table, which
      is already present in every checkout and needs no `ciu` import — the
      block-scanning `read_ciu_identity` helper published in
      `ciu/docs/CONSUMERS.md` §11b is copy-pasteable), naming ALL THREE
      importers and the `config_constants.py` dependency;
- [ ] re-point `scripts/devcontainer-exec.sh:83-104` — the one shell site that
      reads a specific identity fact rather than sourcing for convenience;
- [ ] fix `tests/smoke/test-deployment-validation.py`'s import path while
      re-pointing it — it has never worked, and its failure is swallowed;
- [ ] the lower-risk shapes (the `source ciu.env` sites, the handoff recipe,
      the `cat ciu.env` CI artifact in `.github/workflows/ciu-env-cicd-test.yml`)
      can follow;
- [ ] only after all consumers are off it may a later ciu release stop
      WRITING `ciu.env` (S3.1c clause 2 keeps the write until then).

---

## CIU-83 — ciu-P43's four items are in SPEC/CONSUMERS but in no `CHANGES.md` entry, so 7.7.0's release notes omit a BREAKING change

**FIXED — controller, 2026-08-31, before tagging 7.7.0.** `ciu/CHANGES.md`'s
`## [7.7.0]` section now carries entries for all four ciu-P43 items, matching
the depth already present for CIU-75: the opening blockquote and the
Adoption/Migration Notes section both now name CIU-79 as the release's second
breaking change (with the CONSUMERS #18 migration pointer); `### Changed`
carries CIU-79's full entry; `### Added` carries CIU-80's; a new `### Fixed`
section carries CIU-81's and CIU-77's; `### Documentation` and `### Testing`
each gained supplementary bullets for all four. Content drawn from ciu-P43's
merge commit (`815c50d6`, quoted below) and each item's own backlog row —
not written from memory. Checked against the checkpoint's full merge list
(ciu-P42, ciu-P43, run-gate-P02, run-gate-P03), per this entry's own
prescription.

**Filed by:** ciu-P42, 2026-08-31, found while rebasing onto ciu-P43
(`815c50d6`) — not a defect in this package, and not fixed here.

`git show 815c50d6 --stat` does not list `ciu/CHANGES.md`: CIU-77 (vendored
judge `assay-2.3.0` → `3.2.0`), CIU-79, CIU-80 and CIU-81 landed with SPEC,
CONSUMERS, CONFIG and report updates but no changelog entry at all. Nothing is
undocumented in the normative sense — the SPEC and CONSUMERS halves are there —
but `## [7.7.0]` is the section all five of this checkpoint's items ship in,
and it currently describes CIU-75 only (plus one CIU-75 × CIU-80 interaction
bullet this package added, which is the sole changelog mention CIU-80 has).

Why this is worth an entry rather than a silent fix by the next package:

- **CIU-79 is BREAKING** by ciu-P43's own merge message — a `[<root>.dev].build`
  profile whose Dockerfile sits in the stack dir now fails until `dockerfile`
  is repointed repo-root-relative. A reader of 7.7.0's notes, which open by
  declaring the release's one deliberate breaking change (CIU-75), would not
  learn about it. Two breaking changes, one announced.
- **CIU-77 changed the gate's judge**, which is exactly the kind of fact a
  consumer pinning ciu's own toolchain reads release notes for.
- The estate rule is that user-facing docs land WITH the change; a release
  section assembled at tag time from memory is the failure mode that rule
  exists to prevent.

- [x] add `[7.7.0]` entries for CIU-77, CIU-79 (marked BREAKING, with the
      migration CONSUMERS #18 already carries), CIU-80 and CIU-81, before
      7.7.0 is tagged;
- [x] whoever tags 7.7.0: check the section against the merge list for the
      whole checkpoint, not against one package's report.

---

## CIU-84 — FIXED (ciu-P44) — `ciu check --json` still writes `[INFO]` to stdout ahead of the document (S13.4a)

**Filed by:** ciu-P42 review round 2, 2026-08-31. **Pre-existing — NOT caused
by CIU-75**, and deliberately not fixed there: CIU-75's own contribution to
this class (the deprecation notice on the bootstrap-regeneration path) was
fixed in review round 1, and widening the fix to unrelated emitters would have
been scope creep in a release already carrying a breaking change.

S13.4a and `_emit_check_report`'s own docstring say the JSON document is "the
only thing this action writes". `action_check` honors that for its own prose
(`if not json_output` guards at `deploy.py:2924,2928,3122`), but `_run`
reaches it through code that does not: `deploy.py:4322`'s
`info(f"Active service profile(s): …")` prints unconditionally, on stdout,
before dispatch. So `ciu check --json | jq` sees at least one `[INFO]` line
first.

The same hazard CIU-75 hit at STEP 1 (a stdout notice ahead of the document),
one layer up, and the same fix shape: route pre-dispatch diagnostics to stderr
when `--json` is set, or gate them. Worth a sweep of every `info()`/`warn()`
reachable from `_run` before `action_check`, not a one-line patch, since the
next emitter added would silently reintroduce it.

- [ ] find every stdout write reachable on the `--json` path before the
      document is emitted (`info`, `warn`, `_log_info`, `_log_warn`, and the
      engine's `check_runtime_dependencies` banner);
- [ ] route or gate them, and add a test asserting `ciu check --json`'s stdout
      parses as ONE JSON document — the only assertion that keeps this closed.

---

## CIU-85 — FIXED (ciu-P44) — `_clean_in`'s child environment skips the identity strip its two siblings perform, and `PUBLIC_FQDN` is missing from `_CIU_IDENTITY_ENV_KEYS`

**Filed by:** ciu-P42 review round 2, 2026-08-31 (reviewer-reported,
independently confirmed). Neutralized in practice today; filed because the
neutralization is incidental, not designed.

CIU has three builders of "an environment representing ANOTHER checkout"
(S3.1c clause 7). Two strip the caller's identity first —
`_sanitized_target_env` (`worktree.py:2962`) and `_resolve_budget_candidates`
(`:4406`) both build `{k: v for k, v in os.environ.items() if k not in
_CIU_IDENTITY_ENV_KEYS}`. The third, `_clean_in` (`worktree.py:1267`), does
`env = dict(os.environ); env.update(identity)` — no strip.

Why it is harmless *today*: `_clean_in` refuses outright when the target's
table carries no identity (`:1259`), so `identity` always overwrites all six
keys it defines, and since CIU-75 STEP 1 has already override-seeded them
anyway. But `_CIU_IDENTITY_ENV_KEYS` also carries `CIU_SERVICES_PROFILE`,
which is NOT an overlay fact and therefore not in `identity` — so the caller's
profile selection *does* leak into the child `ciu clean`, where its two
siblings would have stripped it.

Two related asymmetries in the same tuple:

- **`PUBLIC_FQDN` is absent from `_CIU_IDENTITY_ENV_KEYS`** even though it is
  one of the six identity facts (it joined the identity tuple in CIU-47).
  Currently masked because `identity_env_from_facts` carries an empty
  `public_fqdn` through as `""`, so the target's value — empty or not — still
  overwrites the caller's. Masked, not absent.
- The tuple is hand-maintained beside `GENERATED_FACT_ENV_KEYS`, which is the
  same two-lists-that-must-agree shape CIU-75 removed elsewhere by deriving
  one from the other (`LEGACY_IDENTITY_ENV_KEYS`).

- [ ] give `_clean_in` the same strip its siblings use;
- [ ] derive the identity half of `_CIU_IDENTITY_ENV_KEYS` from
      `GENERATED_FACT_ENV_KEYS` (adding `PUBLIC_FQDN` by construction) and keep
      only the non-fact members (`CIU_SERVICES_PROFILE`) listed by hand;
- [ ] one test per builder asserting a caller-set `CIU_SERVICES_PROFILE` and
      `PUBLIC_FQDN` never reach the child — the assertion that would have
      caught this.

---

## Compact resolved index

Detailed history for closed work lives in the normative SPEC, release notes,
archived handoffs/reports, and Git history rather than this active tracker.

| IDs | Disposition | Normative/result pointer |
|---|---|---|
| CIU-1 | NOT A GAP | S5 already supplied config render/mount behavior |
| CIU-2–CIU-8 | FIXED and released | S3.1a, S4.20, S5.3, S5a, S6.4, S9.3, S10.4 |
| CIU-9 | FIXED | S6.4 DooD physical-path removal |
| CIU-10 | FIXED | S2 workspace-root reconciliation |
| CIU-11 | FIXED | S1.2 standalone-root enforcement |
| CIU-12 | FIXED | S14.6 docker-optional push/activate |
| CIU-13 | FIXED | S15.10 governance merge |
| CIU-14–CIU-15 | FIXED | S15.11 logical-vs-physical KSM validation |
| CIU-16–CIU-17 | FIXED | CLI version verb and KSM override documentation |
| CIU-18 | FIXED | S17.2 provenance enforcement |
| CIU-19 | FIXED | S6.4 instance-scoped cleanup |
| CIU-20 | FIXED | S17.3 structured provenance verdict |
| CIU-21 | FIXED | S17.4 in-container image revision |
| CIU-22 | FIXED | S16.1 shared-infrastructure join |
| CIU-24 | FIXED | S16.3 worktree concurrency budget |
| CIU-27 | FIXED | S17.2 explicit no-preflight break-glass behavior |
| CIU-36 | FIXED | S3.11 `[deploy].landscape_id` validation + docs |
| CIU-37 | FIXED | S5.7 schema-validated configfile render (`ciu[schema]` extra) |

`CIU-COMMENT-ENV` is fixed under S3.2: environment expansion ignores TOML
comments while preserving comment text.
