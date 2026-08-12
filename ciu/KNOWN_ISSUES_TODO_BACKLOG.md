# CIU — Known Issues, TODO & Backlog

> **This is the canonical CIU issue tracker.** File CIU bugs and enhancements **here**, in
> the CIU product repo — not in consumer repos. Consumers (e.g. dstdns) that discover a CIU
> gap while building/operating a stack should report it here and keep only a pointer on their
> side. Each issue is fixed in this repo with **code + tests + spec + docs in lockstep** —
> a status of FIXED means the code, tests, SPEC change, and docs all landed together.
>
> Normative behaviour is defined in [`docs/SPEC.md`](docs/SPEC.md) (`S-xx` IDs). When an issue
> changes behaviour, the SPEC change is part of the fix, and the SPEC ID is cited in the entry.

Last updated: 2026-08-12 (CIU-27 filed: S17.2 documents a `ciu provenance`
`--no-preflight` option that the CLI does not accept; decision required before
changing either contract or implementation. CIU-24 FIXED — code + tests + spec + docs,
`ciu-P03-worktree-concurrency-budget`, S16.3. Earlier the same day: CIU-22
FIXED, `ciu-P02-worktree-shared-infra-join`, S16.1; CIU-20/21/23 FIXED,
`ciu-P01-worktree-isolation-primitives`; CIU-26 filed as the deferred
real-Postgres proof for CIU-23's shipped provisioner. CIU-25 remains OPEN —
see `nyxloom-trove/backlog.md`).

**Audit 2026-07-21 (post CIU-9/10/11).** Every entry below was re-verified against
the current `src/` tree. All three recent fixes are present and intact:
CIU-9 (`engine.py:_rmtree_with_fallback` resolves the physical path first and routes
DooD removals through `privileged_rmtree` unconditionally, `engine.py:398-448`),
CIU-10 (`workspace_env.py:_detect_physical_repo_root` reconciles a pre-set
`PHYSICAL_REPO_ROOT` against mountinfo, `workspace_env.py:432`), and CIU-11 (shared
`enforce_standalone_root(invocation_dir)` helper in `workspace_env.py:211`, called by
both `deploy.py:1635` with `Path.cwd()` and `engine.py:1035` with `working_dir`).
CIU-COMMENT-ENV is present (`config_model.py:_split_toml_line_at_comment` +
TOML-aware `expand_env_vars_or_fail`). **No open code items** remain in this file;
nothing required implementation or escalation. One stale cross-reference in the
CIU-9 detail was sharpened (see below). Separately, the reserved-but-unimplemented
`ciu up --host --thin` slot was implemented as the docker-optional push→activate
path (SPEC S14.6, code + tests + docs) — tracked as CIU-12 in git history, not a
bug entry here.

## How issues get here

Most CIU issues are surfaced by **dstdns**, the first large CIU consumer, while running a
disposable-greenfield workflow (`ciu clean` → rebuild → `ciu up`, repeatedly). That workflow
exercises teardown/re-render far harder than a normal deploy. Capture the originating note
verbatim, then distil it into a structured issue below: mechanism, a live repro, the fix
(code + tests + spec + docs), and the cited `S-xx` IDs.

---

## Status board

| # | Title | Severity | Status |
|---|---|---|---|
| CIU-9 | `reset_service` volume cleanup silently no-ops in DooD when the operator can write the logical path | High | FIXED |
| CIU-10 | Pre-set `PHYSICAL_REPO_ROOT` contamination from a sibling repo's sourced `ciu.env` corrupts `ciu env generate` for a nested repo | High | FIXED |
| CIU-11 | `standalone_root` (S1.2) guard did not fire on `ciu render`: `deploy.py` detected the standalone root from the already-resolved `repo_root` (the contaminated value) instead of the invocation dir, so `ciu render` from a sibling repo with a stale `$REPO_ROOT` rendered the *other* repo's stacks silently; `ciu up` (engine) checked `working_dir` and was correct. Fixed by a shared `enforce_standalone_root(invocation_dir)` helper both paths call. | High | FIXED |
| CIU-13 | A stack's `[<root>.governance]` table does not merge with the global `[governance]` default (S15.10), so adding ONE key silently disables governance entirely and creates an **unconfined** container — a fail-open on a safety mechanism | High | FIXED |
| CIU-14 | `governance.ksm_optin` bind-mounts the configured shim path unconditionally, with no existence check — a missing source file silently phantom-mounts an empty directory instead of failing, so KSM opt-in contributes zero savings with no error surfaced anywhere but container-internal `ld.so` stderr | Medium | FIXED |
| CIU-15 | CIU-14's own fix stats the **physical** (Docker-daemon) path to prove the shim exists. That path is by definition not resolvable from inside a devcontainer, so `ciu up` fails `[S15.11] ... not an existing file` on **every** DooD render even with the shim present — an unconditional fail-closed in exactly the environment the check protects | High | FIXED |
| CIU-16 | `ciu version` is not a verb (only `ciu --version`), inconsistent with the estate's other CLIs | Low | FIXED |
| CIU-17 | No CLI-level `--ksm` / `--no-ksm` override for an ad-hoc run; toggling KSM requires editing `governance.ksm_optin` in the TOML layer. Raised alongside CIU-14 and explicitly ruled out of its scope as a convenience feature, then never filed on its own | Low | FIXED |
| CIU-19 | `reset_service`'s orphan sweep filtered on `<prefix>.component=<service>` ALONE, which is not instance-scoped — a second checkout of the same repo labels its containers identically, so `ciu clean` in one instance deleted the same-named service out of **every** instance on the host. Observed live while building `ciu worktree` (S16): cleaning a worktree instance removed the PRIMARY instance's `db-init`. With a full stack up, that is someone's database | High | FIXED |
| CIU-18 | Image provenance is STAMPED but not ENFORCED. `bake` now sets `org.opencontainers.image.revision` (with a `-dirty` suffix) on every baked image, so a container can be traced to its commit — but nothing yet REFUSES to run a live/integration lane against an image whose label does not match the commit under test. Until it does, a live result can still silently describe an unknown artifact, which the consuming project's own policy (dstdns AGENTS.md §4.1a) already calls a defect. FIXED as a TEST-time gate (`ciu provenance`, S17.2) over RUNNING containers — not a deploy-time one: at deploy the question is "did I bake?", which surfaces immediately, whereas the question that yields bad EVIDENCE is asked against an already-running stack. `ciu test` (D7) will call it when that surface exists | Medium | FIXED |
| CIU-20 | `ciu provenance` has no machine-readable output: the success path is silent and the failure path is prose + exit code, so a downstream evidence consumer (assay) cannot record *what was checked and what it found* — only "no refusal happened", which is not the same fact. FIXED: `deploy.verify_running_provenance` now ALWAYS builds and returns a `ProvenanceResult` (never raises, never bare `None`); `ciu provenance --json` (S17.3, `store_true` matching `ciu diagnose --json`) prints the closed six-value `overall` grammar, with the `containers: null` (could not enumerate) vs `containers: []` (enumerated, found nothing) distinction that closes the docker-unavailable false-green. `cli._provenance` is the sole place deciding prose/raise/warn from the verdict | Low | FIXED |
| CIU-21 | No way for a process INSIDE a container to learn the image's own `org.opencontainers.image.revision`: the label is readable only from the docker-daemon side, so an in-container test runner cannot verify its own provenance without an outside co-process. FIXED: every rendered overlay carries `CIU_IMAGE_REVISION=<revision>` per service (S17.4), read from that service's OWN baked label via `deploy._image_revision_label` (never `get_git_hash()`), built in `engine.py` and passed into `composefile.generate_overlay` as data (`composefile.py` gains no docker import) — unconditional, independent of `governance.enabled`/`exempt_services`, append-never-clobber on the shared `environment` merge key | Low | FIXED |
| CIU-22 | No shared-infra-join for `ciu worktree`: `worktree add` gives a new instance its OWN full stack, but heavy/rarely-diverging infra (identity, secrets, observability, reverse-proxy) has to stand up N times too — no way to join a new worktree's diverging tier onto an EXISTING instance's shared infra networks. FIXED: `worktree add --shared-infra REF --shared-infra-services S1,S2 --shared-infra-ref-projects R1,R2` validates REF at add-time (registered worktree, live network, every declared reference project's liveness AND-combined) and records the intent into the new worktree's own `ciu.env` (S16.1); `ciu up`, in the new worktree's own process after Compose succeeds, joins ONLY the declared diverging-tier service containers onto the reference network via imperative `docker network connect` — the new instance's own `DOCKER_NETWORK_INTERNAL` never changes. Already-exists detection is Docker STATE (re-inspect after a non-zero connect), never Docker diagnostic text; a genuine failure rolls back only this invocation's own successful connects, in reverse order, never `docker compose down` | Medium | FIXED |
| CIU-23 | No lightweight per-worktree DATA isolation: the only isolation `ciu worktree` offers is a full separate container stack, but the common real-lane case (schema diverges per package, nothing else does) needs only a namespaced database on a shared server, not N full Postgres containers. FIXED: `worktree add --data-isolation <profile>` provisions a database/schema namespaced by the instance's own `INSTANCE_ID` (never its `NAME` — collision-safe across repo clones) via an injectable `DataIsolationProvisioner` (S16.2; `worktree.PostgresProvisioner` ships as the real default). `worktree rm` drops it BEFORE `ciu clean`, idempotently, with a retry-safe terminal state on a partial failure; `--force` masking a failed drop WARNS (unlike `ciu clean`'s own silent force path). The real-server proof is deferred — see CIU-26 | Medium | FIXED |
| CIU-24 | No concurrency budget for worktree instances: nothing caps how many can run at once against a host's actual capacity, so K parallel isolated real-lane gates can OOM or starve a shared host with no warning. FIXED (S16.3): the PRIMARY *Git* worktree's own CIU root's global `[ciu.worktree] max_concurrent_instances` (plus a `CIU_MAX_CONCURRENT_WORKTREES` ambient override) caps deployed instances; `ciu up`/`--shipped` enforce it under a `<git-common-dir>/ciu-worktree-budget.lock` held only across the exact-project-label Docker count decision and the real `docker compose up` call, resolved from candidates translated through the git-root-to-CIU-root offset and rendered against each candidate's OWN `ciu.env`. Deliberately NOT a `[governance]` value (does not participate in CIU-13's merge); an already-deployed current instance may always rerun even over cap | Low | FIXED |
| CIU-25 | No leak detector for worktree instances: an orphaned stack (crashed child, killed session, forgotten `worktree rm`) is never reaped, silently consuming host resources indefinitely | Low | OPEN |
| CIU-26 | CIU-23's `worktree.PostgresProvisioner` (the real, shipped S16.2 data-isolation default) has no live-server proof: its naming/ordering/force/idempotency contract is proven in-gate only against a FAKE `DataIsolationProvisioner`, because `tester-unified:local` has no live Postgres server to provision against. Needs an integration lane (outside this repo's own gate) that runs `PostgresProvisioner` against a real Postgres and confirms `provision`/`drop` actually create/remove a database, not just that the mechanism dispatches correctly | Low | OPEN |
| CIU-27 | S17.2 states that `ciu provenance --no-preflight` skips the check, but the actual parser accepts only `--ignore-mismatch`/`--force`, `--json`, and `--define-root`; the documented behavior cannot be invoked | Medium | OPEN — product decision required |

### CIU-27 — `provenance --no-preflight` is specified but not implemented

**Evidence (2026-08-12 audit):** `docs/SPEC.md` S17.2 says
`--no-preflight` skips the provenance check. The shipped `ciu provenance`
parser rejects that option with argparse exit 2; its accepted options are
`--ignore-mismatch` (alias `--force`), `--json`, and `--define-root`.

**Decision required:** either implement a real `--no-preflight` behavior with
tests, or withdraw/correct the normative S17.2 claim. This audit deliberately
does neither: silently changing a test-evidence gate or rewriting its contract
would make a product decision under the guise of documentation maintenance.

## Resolved / not-a-gap

| # | Title | Verdict |
|---|---|---|
| CIU-1 | "No config-file render+mount directive" | **NOT A GAP** — CIU S5 implements it; the consumer must *adopt* it, not request it. (An agent reading only the consumer repo cannot conclude a provider lacks a capability — check the provider SPEC/source first.) |
| CIU-COMMENT-ENV | `expand_env_vars_or_fail` expanded `$VAR`/`${VAR}` tokens inside TOML comment lines | **FIXED** — `expand_env_vars_or_fail` is now TOML-aware: it strips comment content (from an unquoted `#` to end-of-line) before applying `ENV_VAR_PATTERN.sub`, using a minimal quote-tracking scan to distinguish `#` in a quoted value from a comment delimiter. Comment text is preserved verbatim; only value portions are expanded. Surfaced by dstdns `ciu.global.defaults.toml.j2:697` which carried `cmru-node-${value.node_id}` in a comment, causing every ciu-driven observability/SkyWalking deploy to fail with "missing required env var". Fixed in `config_model.py`; nine regression tests added to `test_ciu_config_model.py`. See SPEC ID S3.2. |

> The CIU-2 … CIU-8 family (configfile fan-out, complete teardown, hook readiness, the dev-loop
> verb, the consumption-channel scan, per-verb help, and the sparse per-stack override) has been
> implemented and **released**. The behaviour now lives in the SPEC (S3.1a, S4.20, S5.3, S5a,
> S6.4, S9.3, S10.4) with tests and docs in lockstep; the per-issue rationale is preserved in the
> git history (`git log`) and the release notes for the tag that shipped them. Closed entries are
> not retained here — the SPEC is the canonical record of behaviour, this file tracks only what is
> still open.

---

### CIU-9 detail: `reset_service` volume cleanup silently no-ops in DooD

**Mechanism (confirmed):** `_rmtree_with_fallback` (`src/ciu/engine.py:398`) only translates a
`vol-*` hostdir to its physical path (S1.4, via `to_physical_path`) **inside the
`except PermissionError` branch** of a local `shutil.rmtree(vol_dir)` call. In a DooD deployment
(`REPO_ROOT != PHYSICAL_REPO_ROOT`, S1.4/S1.9 — dstdns's case: `REPO_ROOT=/workspaces/dstdns`,
`PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns`), a local `shutil.rmtree` on the *logical* path only
raises `PermissionError` when the hostdir's owning UID doesn't match the operator (the S6.7
Pattern-(a) fixed-UID-image case: postgres 999, pgAdmin 5050, etc. — this is the only case the
fallback was written for). When a service instead runs container-side as
`CONTAINER_UID:DOCKER_GID` (the operator's own UID/GID — dstdns's `consul-server` stack does this),
the local `shutil.rmtree` on the logical path **succeeds without error**, so the function returns
at line 406 and the physical-path branch never runs. The logical-path directory it just wiped is
not necessarily the same directory the Docker daemon actually bind-mounted into the container
(that one lives under `PHYSICAL_REPO_ROOT` on the real host) — success on the wrong path is
indistinguishable from success on the right one, so `reset_service` reports the volume removed
and moves on having touched nothing the daemon cares about.

**Live repro (dstdns, `infra/consul-server`, 2026-07-10):** `ciu clean -y` reported
`vol-consul-data`/`vol-consul-config` removed (no error). A subsequent `ciu up --profile dev`
started a Consul server that immediately crash-looped:
`refusing to rejoin cluster because server has been offline for more than the configured
server_rejoin_age_max (168h0m0s) - consider wiping your data dir`. `find` on
`$REPO_ROOT/infra/consul-server/vol-consul-data` (from inside the devcontainer, i.e. the logical
path) showed the directory genuinely empty. `docker exec <consul-container> find /consul/data`
showed it full of raft/serf state dated months earlier (`Feb 1`/`Feb 2`, files owned `1003:994` —
the operator's own UID:GID, confirming the fixed-UID branch never applied and no `PermissionError`
was ever raised). Running the stack's own `infra/consul-server/cleanup-consul.sh` — which routes
the removal through `docker run --rm -v <path>:/cleanup alpine rm -rf /cleanup/*`, i.e. always via
the daemon's own path resolution regardless of local permission — immediately fixed it; Consul
came up clean on the next restart. That script exists only because this project already hand-rolled
the workaround CIU's generic reset should be doing.

**Suspected fix:** `_rmtree_with_fallback` should not gate the physical-path removal on catching a
`PermissionError` from the logical-path attempt. In any DooD context (`to_physical_path(vol_dir) !=
vol_dir`), removal must go through the daemon-resolved physical path unconditionally — a
local-path success proves nothing about the physical path's state. One option: always compute
`to_physical_path` first; if it differs from the logical path, always route through
`privileged_rmtree(physical)` (which already does the correct `docker run -v ... rm -rf`, S6.5) and
skip the local attempt entirely; only use local `shutil.rmtree` when the two paths are identical
(true native-host case, S1.9).

**Open question (not yet traced):** separately, the *rendered* `ciu.compose.yml` for
`consul-server` in the same repro still showed the **logical** path
(`/workspaces/dstdns/infra/consul-server/vol-consul-data:/consul/data`) as the bind-mount source,
even though `create_hostdirs` (`engine.py:447`, called at `engine.py:1003`) is documented to
rewrite `hostdir[purpose]` to the absolute *physical* path in-place (S6.2) before template
rendering, and the call did not raise (so `PHYSICAL_REPO_ROOT` was resolvable at that point — ruling
out a simple missing-env-var explanation). If hostdir values are in fact reaching the compose
template pre-rewrite, then containers are being bind-mounted against the *logical* path in the
first place, which would make CIU-9's `_rmtree_with_fallback` fix necessary-but-insufficient — the
create step would also need tracing (does `engine.py:1003`'s call site actually feed template
rendering from the same mutated `merged` object, or from an earlier-captured copy?). Left open
for whoever picks this up; the workaround above (`cleanup-consul.sh`-style forced-physical removal)
is confirmed to work regardless of which of the two mechanisms is the actual live path, since it
sidesteps path resolution entirely by shelling out through the daemon.

**Workaround in use (dstdns):** `infra/consul-server/cleanup-consul.sh` (already in-repo, predates
this write-up). No other `vol-*` service reset failures have been observed yet in DooD — this may
be specific to services that avoid the fixed-image-UID pattern.

**Resolution:**

**1. Main fix.** `_rmtree_with_fallback` (`src/ciu/engine.py`) now resolves the physical path (S1.4)
*first*, before deciding how to remove anything. When `to_physical_path(vol_dir) != vol_dir` (DooD,
S1.4/S1.9) it routes through `privileged_rmtree(physical)` unconditionally — the local
`shutil.rmtree` attempt is skipped entirely, not merely tried first. On a true native host
(logical == physical, S1.9) the local `shutil.rmtree` is used directly, with the pre-existing
`PermissionError` → S6.5 root-helper degrade preserved for fixed-UID data (S6.7 Pattern (a)). When
`to_physical_path` cannot resolve a DooD context at all (`ValueError`, no `REPO_ROOT`/
`PHYSICAL_REPO_ROOT`), the function now falls back to treating the removal as native-host (same
externally-observable behaviour as before this fix for non-DooD callers). SPEC updated: **S6.4**
("DooD path routing (CIU-9, normative)" — `docs/SPEC.md`).

**2. The "open question" (create_hostdirs → template rendering) — traced, confirmed NOT a bug.**
Code reading of `engine.py`'s `main_execution` (the 17-step S8.3 pipeline) shows `merged` is not
reassigned between step 8 (`create_hostdirs(merged, working_dir, repo_root=repo_root)`, `engine.py`
~line 1003) and step 13 (`composefile.guard_config(merged, specs)` → `composefile.render_compose`,
`engine.py` ~lines 1143/1151). `create_hostdirs`/`_scan_section` mutate the nested `hostdir` dict
**in place** (`hostdir[purpose] = str(_to_physical(path))`, `engine.py` ~line 604) — no copy is made
anywhere in that call chain, so the S6.2 physical-path rewrite lands directly in the same `merged`
object that flows onward. `composefile.guard_config` → `_replace_entries`
(`composefile.py` ~line 193) does `copy.deepcopy(config)`, but only takes that deep copy **after**
`create_hostdirs` already mutated `merged` — so the copy `render_compose` receives already carries
the physical paths, not the pre-rewrite logical ones. A new end-to-end test,
`TestCIU9HostdirRewriteFeedsRender::test_physical_path_reaches_rendered_compose_in_dood`
(`tests/tests/test_ciu_hostdir_creation.py`), exercises exactly this call sequence with a
DooD-style `repo_root != physical_root` and asserts the rendered compose text contains the
**physical** hostdir path and not the logical one — it passes against the current code
unmodified, confirming the mutation-propagation mechanism already works correctly. No separate
follow-up issue was filed for this create_hostdirs→render sub-path — it is not a second live bug in
the current codebase. (This predates, and is unrelated to, the later CIU-10, which is the sibling-repo
`PHYSICAL_REPO_ROOT` contamination bug, not this render-propagation question.) (The live repro's rendered compose
showing the logical path most likely reflects a stale artifact from a run predating this
investigation, or an environment/ordering detail outside `engine.py`'s own pipeline — not a defect
in `create_hostdirs`'s propagation to Jinja rendering as traced here.)

**Tests:** `tests/tests/test_ciu_reset_service.py` → `TestRmtreeWithFallbackDooD` (5 new tests:
DooD routes through the physical path unconditionally even when a local rmtree would silently
"succeed" on the wrong directory; native host still uses local rmtree directly; native-host
`PermissionError` still degrades to the S6.5 helper; no-DooD-context (`ValueError`) preserves prior
native-host behaviour). `tests/tests/test_ciu_hostdir_creation.py` → `TestCIU9HostdirRewriteFeedsRender`
(1 new end-to-end test, described above). Full suite: `python run-ciu-tests.py` — 892 passed,
coverage 74.75% (floor 73%).

---

### CIU-10 detail: pre-set `PHYSICAL_REPO_ROOT` contamination across sibling repos

**Mechanism (confirmed):** `_detect_physical_repo_root` (`src/ciu/workspace_env.py`) treated a
pre-set `PHYSICAL_REPO_ROOT` environment variable as winning **unconditionally**, before even
consulting `/proc/self/mountinfo` (the 2026-07-15 mountinfo longest-prefix-match fix, otherwise
correct — see S2.7). This is a legitimate manual-override mechanism, but it is also a contamination
vector: a devcontainer's login shell auto-`source`s its **primary** workspace's `ciu.env` (e.g.
`~/.bashrc`'s `if [[ -n "$REPO_ROOT" && -f "$REPO_ROOT/ciu.env" ]]; then source
"$REPO_ROOT/ciu.env"; fi` hook), which exports `PHYSICAL_REPO_ROOT` into every subsequent shell in
that devcontainer. Running `ciu env generate` (or anything that calls `generate_ciu_env`) for an
**unrelated, nested** repo from that same shell then inherited the primary workspace's
`PHYSICAL_REPO_ROOT` unconditionally, corrupting the nested repo's `PHYSICAL_REPO_ROOT` /
`REPO_NAME` / `INSTANCE_ID` / `DOCKER_NETWORK_INTERNAL` — and, downstream, its bind-mount sources
(materializing empty directories at the wrong host path) and its Docker network attachment.

**Live repro (2026-07-16):** `/workspaces/vbpub/nyxloom/ciu.env` (nyxloom = a ciu root nested
inside `vbpub`, itself a sibling of the devcontainer's primary `dstdns` workspace) showed
`REPO_ROOT="/workspaces/vbpub/nyxloom"` (correct) but `PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/dstdns"`,
`REPO_NAME="dstdns"`, `INSTANCE_ID="98535c"` — dstdns's own identity, byte-for-byte, leaked into
nyxloom's generated env. Confirmed via direct repro: with `PHYSICAL_REPO_ROOT` unset,
`_detect_physical_repo_root(Path("/workspaces/vbpub/nyxloom"))` correctly returns
`/home/vb/volkb79-2/vbpub/nyxloom` via mountinfo (nyxloom has no dedicated bind mount of its own —
it's nested under the `/workspaces/vbpub` bind, so longest-prefix-match resolves it through that
bind plus the relative offset); with `PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns` pre-set (as it
is in the live devcontainer, per the `.bashrc` mechanism above), the old code returned that stale
dstdns value unconditionally, reproducing the exact live bug.

**Fix:** `_detect_physical_repo_root` now checks a pre-set `PHYSICAL_REPO_ROOT` against the
mountinfo-derived value for `repo_root` before honoring it. The pre-set value wins only when (a) it
agrees with mountinfo, or (b) mountinfo yields no match at all (nothing to check against — the
legitimate native-host / mountinfo-unavailable manual-override case is preserved unchanged). When
mountinfo yields a *different* value, the mountinfo-derived value wins instead and a warning is
printed to stderr naming the ignored pre-set value and repo_root. SPEC (`docs/SPEC.md` S2.7),
`docs/CIU.md`, and `docs/CONFIG.md` updated to document the consistency check alongside the
existing precedence table.

**Tests:** `tests/tests/test_physical_root_mount_table.py` — `TestFallbackWhenMountinfoYieldsNothing
::test_preset_env_still_wins_over_mountinfo` reconciled to the refined contract (now exercises the
"mountinfo has no entry" sub-case); new `TestPresetEnvConsistency` class adds
`test_preset_env_wins_when_consistent_with_mountinfo` (manual-override preserved) and
`test_preset_env_ignored_when_inconsistent_with_repo_root` (the exact contamination regression, incl.
asserting the stderr warning); new `TestRegressionBoundNestedPresetEnvContamination` class exercises
`generate_ciu_env` end-to-end for a nyxloom-shaped nested layout with a contaminating dstdns preset,
asserting the generated `ciu.env` carries nyxloom's own identity, not dstdns's. Full suite:
`PYTHONPATH=src python -m pytest tests -q` — 931 passed.

---

### CIU-COMMENT-ENV detail (archived for reference)

**Mechanism:** `expand_env_vars_or_fail` applied `ENV_VAR_PATTERN.sub` over the entire
post-Jinja2-rendered TOML text, including comment lines. A `#` comment carrying any
`$TOKEN`/`${TOKEN}` pattern caused a false-positive "missing required env var" error.

**Live repro:** dstdns `ciu.global.defaults.toml.j2:697` contained:
```toml
#     bind_name = "cmru-node-${value.node_id}"
```
After Jinja2 render the comment remained verbatim; `expand_env_vars_or_fail` raised
`ValueError: Missing required environment values: value.node_id`, blocking all
ciu-driven SkyWalking/observability deploys (SW2 tier). The deploy team had to bypass
ciu and render compose by hand — a regression from the SPEC F deployment model.

**Fix:** Process the TOML text line-by-line. For each line, `_split_toml_line_at_comment`
tracks basic-string (`"..."`) and literal-string (`'...'`) quoting state to find the first
unquoted `#`. Expansion is applied only to the value portion; the comment portion is
passed through unchanged.

**Tests:** Nine new tests in `test_ciu_config_model.py` under the
`CIU-COMMENT-ENV: TOML-aware comment handling` section.

---

### CIU-13 detail: a partial `[<root>.governance]` table silently disables governance (fail-open)

**Reported by:** dstdns, 2026-08-03, while sizing the gating `test-runner` container.
**Severity:** High — the failure mode is an **unconfined container on a shared
production host**, produced silently by what looks like a one-key tuning edit.

**This is not an implementation/spec mismatch.** The behaviour is intentional and
documented: S15.10 says the global table applies only to a stack that declares
"none of its own", and `governance.resolve_stack_governance`'s docstring states it
outright — *"The two layers do not deep-merge (S15.10): a stack with its own table,
however small, fully owns its governance config and does not inherit unset keys from
the global default."* The report is that **the intended design fails open**, which
for a resource-governance mechanism is the wrong direction.

**Mechanism.** Two layers merge with *different* rules, which is the source of the
surprise:

* **S15.2** — a stack's table *is* shallow-merged, but over `GOVERNANCE_DEFAULTS`
  (code-level, `enabled = false`). "Any key it omits falls through" is true here.
* **S15.10** — the global `[governance]` in `ciu.global.toml` is *not* a merge layer
  at all. It is an all-or-nothing substitute used only when the stack has no table.

So "governance config merges" is true at one layer and false at the other. The
result is a cliff rather than a gradient:

| Stack declares | Effective config |
|---|---|
| no `[<root>.governance]` at all | the global table — fully governed |
| `[<root>.governance]` with **one** key | `GOVERNANCE_DEFAULTS` + that key ⇒ `enabled = false` ⇒ **ungoverned** |

**Live repro (verbatim, dstdns @ `1f5306e8`).** Global config
(`ciu.global.toml.j2`) declares the estate default:

```toml
[governance]
enabled = true
cgroup_parent = "dev-background.slice"
ksm_optin = "tools/ksm-optin/ksm-optin.so"
mem_limit = "2g"
device = "/dev/vda"
```

Intent: raise *only* `mem_limit` for the one stack that runs `pytest -n auto` under
coverage. Edit made to `tools/test-runner/ciu.defaults.toml.j2`:

```toml
[test_runner.governance]
mem_limit = "8g"
```

`ciu up --dir tools/test-runner -y` then printed exactly one line about it:

```
[GOVERNANCE] disabled ([<root>.governance].enabled is false)
```

and produced:

```
$ docker inspect dstdns-98535c-test-runner \
    --format 'CgroupParent={{.HostConfig.CgroupParent}} Memory={{.HostConfig.Memory}}'
CgroupParent= Memory=0
```

No cgroup parent, no memory cap, on a host whose whole point is keeping dev work
from starving a production tenant. The container the edit was meant to *raise* from
2g to 8g came back with **no limit at all**, and nothing in the output says "your
override turned this off".

**Why this is worth changing.** S15.2 already contains the correct instinct in the
adjacent case: `resolve_cgroup_parent` refuses to guess, and "governance is enabled
but no cgroup_parent is resolvable" is a hard `[S15.2]` abort — explicitly to avoid
silently misplacing a container. But the layer *above* it can silently switch
governance off wholesale, which is a strictly worse outcome (no placement **and** no
limits) reached with no error at all. The guard and the trap are one function apart.

The log line is also indistinguishable from the legitimate case. `[GOVERNANCE]
disabled ([<root>.governance].enabled is false)` is exactly what a deliberate opt-out
prints; nothing marks that an `enabled = true` global was overridden into `false` by
a table that never mentioned `enabled`.

**Fix options** (consumer's view — CIU owns the call):

1. **Make S15.10 a merge layer**, consistent with S15.2: resolve
   `GOVERNANCE_DEFAULTS` → global `[governance]` → stack `[<root>.governance]`,
   shallow, last-wins. Most intuitive, matches how every other default in CIU reads,
   and makes the one-key override do what it looks like it does.
2. **Keep all-or-nothing but fail closed**: if a global `[governance]` with
   `enabled = true` exists and a stack declares a table that omits `enabled`, abort
   with an `[S15.x]` error naming both, in the same spirit as the existing
   `cgroup_parent` abort. Preserves "a stack fully owns its config" while making the
   ownership transfer explicit.
3. **Minimum viable**: keep the semantics, but when governance resolves to disabled
   *because* a partial stack table displaced an enabled global, log it as a WARNING
   that names the cause and the keys that were dropped — not the same INFO line a
   deliberate opt-out prints.

(1) is the consumer preference. Whichever is chosen, S15.1's declaration block would
read better if it stated the merge base explicitly — the current text can be read as
"omitted keys fall through to the global", which is what a reader of S15.2 will
assume.

**Consumer-side pointer:** dstdns `tools/test-runner/ciu.defaults.toml.j2` carries a
comment recording the trap and restates all five global keys as the workaround.
Remove it if this is fixed upstream.

**SPEC IDs touched by a fix:** S15.1 (declaration/merge-base wording), S15.2
(defaults and merge), S15.10 (global default resolution).

**Resolution (option 1 chosen).** `governance.resolve_stack_governance` (`src/ciu/governance.py`)
now merges rather than replaces: the global `[governance]` table (when present) is
the base layer, and the stack's own `[<root>.governance]` table (when present) is
shallow-merged over it, key by key, last-wins — mirroring `resolve_config`'s own
S15.2 merge rule exactly. The dstdns repro (`tools/test-runner` restating only
`mem_limit`) now keeps `enabled = true` and every other global key, since the stack
only overrides the one key it actually names; opting out is still possible, but now
requires restating `enabled = false` explicitly rather than happening as a side
effect of any partial table. `resolve_stack_governance` returns `None` only when
BOTH layers are absent; a `None` `stack_governance` still falls through to the global
table unchanged, and a `None` `global_governance` still lets the stack table stand
alone — only the "both present" case changed. SPEC updated: **S15.10** rewritten
around the merge (with the CIU-13 mechanism and fix recorded inline), **S15.1**'s
declaration block reordered narratively (base → stack), **S15.2** cross-referenced.
`composefile.generate_overlay`'s docstring and this file's consumer-side workaround
note may both be retired now that the upstream fix has landed.

**Tests:** `tests/tests/test_ciu_governance.py::TestResolveStackGovernance` —
`test_empty_stack_table_inherits_global_in_full` (replaces the old
`test_empty_stack_table_still_wins_over_global`, which encoded the pre-fix
behavior),
`test_ciu13_one_key_stack_override_inherits_rest_of_global` (the exact dstdns
repro shape, asserting the merged result AND that `resolve_config` on top of it
keeps `enabled = True`), `test_stack_can_still_opt_out_by_restating_enabled_false`
(the merge must not make opting out impossible), plus a defensive-copy regression
test for the global source table. Full suite:
`CGROUP_PARENT_DEV_BACKGROUND=<slice> python run-ciu-tests.py` — 1653 passed,
100% coverage on every file this change touches (`governance.py`, `deploy.py`,
`composefile.py`); the handful of pre-existing engine.py/vault failures in this
sandbox are unrelated devcontainer-env contamination (missing
`CGROUP_PARENT_DEV_BACKGROUND` / a leaked foreign `REPO_ROOT`), reproduced
identically against the pre-fix code and tracked separately (see
`vbpub-cgroup-parent-env-gap` in the operator's notes) — not a regression from
this fix.

---

### Related enhancement (not a CIU-13 sub-item): expanded S15 resource coverage

While fixing CIU-13, three governance gaps identified alongside it (memory floor,
IO proportional share, bandwidth caps — the values `systemd-cgls`/plain Docker
inspection can't show) were closed in the same pass, since they touch the same
`governance.py` merge/injection code:

- **S15.14 `io_weight`** — proportional IO share (`blkio_config.weight`,
  Docker's `10..1000` scale), injected independent of `device` resolution.
  Documents the same BFQ-vs-iocost `io.weight`/`io.bfq.weight` scheduler trap
  the wings-cgroups sibling project measured — CIU cannot detect the active
  scheduler from an overlay generator, so this is a documented caveat, not a
  runtime check.
- **S15.15 `read_bps` / `write_bps`** — per-device bandwidth caps
  (`blkio_config.device_read_bps`/`device_write_bps`), symmetric with the
  existing `read_iops`/`write_iops`. No baseline-derived default exists for
  bandwidth (S15.4's `RIOPS_MAX` formula is IOPS-specific), so both default to
  `0` (uncapped), explicit-opt-in-only.
- **S15.16 `mem_min`** — a declared `memory.min`-equivalent floor. There is no
  Docker/compose field for a per-container memory floor, so this is NEVER
  injected into the overlay; it is checked, not enforced, by a new D-G9 check 3
  in `governance_slice_preflight` (`deploy.py`) that probes the resolved
  `cgroup_parent` slice's live `MemoryMin=` via `systemctl show` and fails
  closed (`[S15.16]`, exit 2) when it doesn't meet the declared floor —
  explicitly documenting that host-side slice provisioning (a static `.slice`
  unit, optionally automated by a companion such as
  `modern-debian-tools-python-debug`'s `host-setup`) is required for this key
  to mean anything; CIU itself never depends on such a companion being present.

**Self-review finding, same pass (fixed, not filed separately per the CIU-12
precedent above):** testing S15.12/S15.16 against CIU's own devcontainer
found both preflights false-aborting there — `systemctl` IS present (a
devcontainer-feature shim at `/usr/local/bin/systemctl`), so the pre-existing
`shutil.which("systemctl") is None` skip never engaged, but the shim (when
systemd isn't actually running) prints a fixed human-readable notice and
exits `0` instead of erroring, and that notice contains no `LoadState=`/
`MemoryMin=` line at all — the existing parse read that absence as a
definitive **false** (slice missing / no floor), silently turning every
devcontainer run into a hard abort. Fixed with `governance._systemd_is_pid1()`
(`sd_booted(3)`'s own `/run/systemd/system`-existence check, monkeypatchable
for tests), consulted before either probe trusts `systemctl`'s output;
verified against this repo's live devcontainer (previously `(False, ...)`,
now correctly `(None, ...)` — inconclusive, not "missing"). Tests:
`TestSystemdIsPid1` (the real, unmocked check) plus a dedicated
shim-detection test in each of `TestCheckSliceUnit`/`TestCheckSliceMemoryMin`.

Code + tests + SPEC (S15.14/S15.15/S15.16) landed together with the CIU-13 fix
above; not filed as a separate numbered issue per this file's convention that
proactively-implemented enhancements (cf. the `--host --thin` note in the
2026-07-21 audit above) are tracked via git history rather than a bug entry here.

---

### CIU-14 detail: `ksm_optin` bind-mounts a missing shim silently instead of failing

**Reported by:** dstdns, 2026-08-05, provisioning a Mode-B worktree instance
(`round1-secondary-stack`) for isolated package-gate testing.
**Severity:** Medium — not a security fail-open like CIU-13, but a silent
efficacy fail-open: the operator believes KSM opt-in is active (governance
notes report `ksm_optin=<path>`, not `off`) while it is doing nothing at all,
with zero savings and zero error surfaced outside container-internal logs.

**Mechanism.** `governance.py:889-899` (`generate_overlay`'s injection loop):
```python
ksm_src = str(config.get("_ksm_optin_source") or "")
if ksm_src:
    frag["environment"] = [f"LD_PRELOAD={KSM_PRELOAD_TARGET}"]
    frag["volumes"] = [{
        "type": "bind",
        "source": ksm_src,
        "target": KSM_PRELOAD_TARGET,
        "read_only": True,
    }]
```
`ksm_src` (resolved in `composefile.py:781-789` from `governance.ksm_optin`,
made physical via `to_physical_path`) is used as a bind-mount source with no
`Path(ksm_src).is_file()` check. Docker's own bind-mount behavior silently
creates an empty directory at a missing host source path rather than
erroring, so the container starts "successfully" with an empty directory at
`/opt/ksm/ksm-optin.so` and `LD_PRELOAD` pointed at it.

**Live repro (dstdns, 2026-08-05).** `tools/ksm-optin/ksm-optin.so` was, at
the time, a **gitignored build artifact** present only in checkouts where it
had been built by hand (dstdns's main checkout, built 2026-07-17 — see
dstdns's own fix for this half, `.gitignore`/tracking the artifact, filed
separately as a consumer-side change, not a CIU issue). A fresh
`git worktree add` does not carry it. `ciu up`'s bind mount into the new
worktree's containers silently backfilled an empty directory. Every one of
the new instance's 8 containers logged (visible only via `docker logs`, not
surfaced by `ciu up` itself):
```
ERROR: ld.so: object '/opt/ksm/ksm-optin.so' from LD_PRELOAD cannot be preloaded (cannot read file data): ignored.
```
and showed `ksm_merge_any: no` / `ksm_process_profit: 0` in `/proc/<pid>/ksm_stat`
for every process — KSM opt-in contributed **zero** savings to the entire
instance, with the governance notes line still reporting
`ksm_optin=tools/ksm-optin/ksm-optin.so` (present, "on") the whole time.

**Suggested fix.** Before emitting the bind-mount fragment in
`generate_overlay` (or earlier, right after `_ksm_optin_source` is resolved
in `composefile.py`), check `Path(ksm_src).is_file()`. If it's configured but
missing: fail the render/up with a clear error naming the missing path and
the config key that requested it (`governance.ksm_optin = "<path>"`), rather
than silently emitting a working-looking bind-mount fragment. This is the
same class of fix as CIU-13 (a governance mechanism that should fail closed,
not open) — consider whether other `governance` bind-mount-from-config-path
injections have the same gap (worth a quick audit of `generate_overlay` for
other unconditional `frag["volumes"]` sites built from a config-supplied
path).

**Out of scope for this entry, noted for completeness:** a separate,
optional enhancement was suggested alongside this bug — a CLI-level
`--ksm`/`--no-ksm` toggle for ad-hoc runs, as an alternative to editing
`governance.ksm_optin` in the TOML layer. That's a convenience feature
request, not a fix for this bug (a CLI toggle wouldn't have caught a
configured-but-missing shim either) — worth considering separately, not
conflated with CIU-14's fail-loud fix above.

**Resolution (2026-08-05).** `composefile.generate_overlay` now resolves the
configured path to its physical Docker-daemon path and requires
`Path.is_file()` before adding `LD_PRELOAD` or the bind fragment. Missing
files, directories, and broken symlinks raise a `[S15.11]` configuration
error (exit 2) that names both `governance.ksm_optin` and the resolved path.
Regression coverage verifies relative logical-to-physical paths, valid absolute
paths, and rejection before an overlay is written. The normative contract is
documented in `docs/SPEC.md` S15.11 and the user-facing configuration/feature
docs.

**Superseded in part by CIU-15 below**: that regression coverage was written
over a population in which the physical path was always locally stat-able, so
it could not see that the check stats the wrong one of the two paths.

---

### CIU-15 detail: CIU-14's existence check stats the physical path, which no devcontainer can see

**Reported by:** dstdns, 2026-08-06, recreating its `test-runner` after fixing a
consumer-side `PHYSICAL_REPO_ROOT` defect.
**Severity:** High — CIU-14 converted a silent fail-open into an *unconditional*
fail-closed for every DooD/devcontainer consumer. `ciu up` cannot render at all
while `governance.ksm_optin` is set, however healthy the shim is.

**Mechanism.** `composefile.generate_overlay` (CIU-14's fix):

```python
physical_ksm_path = to_physical_path(
    ksm_path, repo_root=repo_root, physical_root=physical_root
)
if not physical_ksm_path.is_file():        # <-- stats the DAEMON's path, locally
    raise ValueError("[S15.11] ... not an existing file: ...")
```

`to_physical_path` (S1.4) translates a logical in-container path to the path the
**Docker daemon** sees. In a devcontainer those two are different by
construction — `/workspaces/dstdns` vs `/home/vb/volkb79-2/dstdns` — and the
physical one does not exist *inside* the container at all. `Path.is_file()` runs
in the container. It therefore returns `False` **always**, and the render aborts
with a message asserting the file is missing while the file is present and
correct.

**Live repro (dstdns, 2026-08-06).** `tools/ksm-optin/ksm-optin.so` present and
tracked; visible in-container at `/workspaces/dstdns/tools/...` and on the host
at `/home/vb/volkb79-2/dstdns/tools/...` (both verified, 13736 bytes). `ciu up
--dir tools/test-runner` fails at `[STEP 15/17] Generating overlay...` with
`[S15.11] ... not an existing file:
/home/vb/volkb79-2/dstdns/tools/ksm-optin/ksm-optin.so`.

**Why the tests passed.** `test_ksm_governance_resolves_relative_shim_to_physical_overlay_path`
created the shim at the logical path **and again at the physical path**, and
`test_ksm_governance_rejects_missing_shim_before_writing_overlay` passed
`repo_root == physical_root`. Both encode a world where the daemon's view is
locally stat-able — true on a native host, never true in a devcontainer. The
oracle was sound; the population it ran over did not contain the only case that
matters. Neither test could fail for this bug regardless of the assertion.

**Resolution (2026-08-06).** Existence is read on the **logical** path
(`ksm_path`); the physical path remains the bind SOURCE and is now named
alongside it in the error message so an operator sees both. An external absolute
path passes through `to_physical_path` unchanged (S1.4), so logical == physical
there and the check is unaffected. The over-specified test now creates the shim
only at the logical path and asserts the physical copy is absent; a new test
(`test_ksm_governance_accepts_shim_unreachable_at_the_physical_path`) pins the
devcontainer shape with a `physical_root` that does not exist at all. Both fail
against the pre-fix source and pass after — verified by reverting only
`composefile.py`. Full suite 1691 passed.

**Generalisable lesson, worth applying beyond this entry:** a path-translation
helper's output must never be validated with a *local* filesystem call. The
whole point of `to_physical_path` is that its result addresses a different
namespace. Any `is_file()`/`is_dir()`/`exists()` applied to its return value is
asking the wrong kernel. See the CIU-14 note about auditing other
`frag["volumes"]` sites — that audit should now also check for this inverse
error, not just the missing-check one.

### CIU-20 detail: `ciu provenance` emits no machine-readable verdict

**Reported by:** assay, 2026-08-11, during a cross-project review of ciu's S17
against assay's open decision A-O12 (provenance verification for S3/S4 lanes).
**Severity:** Low — S17.2's gate is correct and complete for its own purpose
(refusing a live lane against a stale image). This is a missing *output*
surface, not a wrong behaviour.

**Mechanism.** `verify_running_provenance` (`deploy.py:556-637`) returns `None`
or raises `ValueError`. Its outputs are a prose `warn()` string and an exit
code, and — the load-bearing part — **the success path returns silently**:

```python
    mismatches: list[tuple[str, str, str]] = []
    for name, image in _running_containers(project_prefix):
        actual = _image_revision_label(image)
        if actual and actual != expected:
            mismatches.append((name, image, actual))

    if not mismatches:
        return
```

For gate wiring (a consumer's script runs `ciu provenance` before its test
command) that is exactly right. For **evidence** it is insufficient twice over:

1. A consumer must not parse prose. assay's own precedent (A-204) is byte-copy,
   never interpret — an interpreting reader becomes a shared oracle coupled to
   another tool's human-facing text.
2. "No refusal happened" is not the same fact as "checked, and matched". assay's
   A-025 doctrine forbids recording the absence of an adverse signal as a
   positive fact — it is the same class as its own `0/0 is not 100%` rule.

Three of the four non-refusal paths are also indistinguishable from success to a
caller reading only the exit code: unlabelled-skipped, absent-image, and
dirty-tree-warns all exit 0, and each is a *different* fact about how much was
verified.

**What's needed.** `ciu provenance --json [PATH|-]` (precedent: `ciu diagnose
--json`, `cli.py:59`), emitting one closed, bounded JSON document alongside the
existing exit-code behaviour:

```json
{
  "schema_version": 1,
  "instance": "<project>-<env_tag>",
  "commit_under_test": "<revision as get_git_hash() renders it, -dirty suffix included>",
  "tree_state": "clean" | "dirty" | "not-a-checkout",
  "containers": [
    {"name": "...", "image": "...", "labelled_revision": "..." | null,
     "status": "match" | "mismatch" | "unlabelled"}
  ],
  "overall": "verified-match" | "mismatch" | "not-verified-dirty"
           | "not-verified-unknown" | "refused-no-identity"
}
```

Requirements that make it usable as evidence rather than as a log:

- **A verified match must be recorded, not silent.** The positive fact is the
  thing a downstream artifact needs.
- **The vocabularies must be closed and stable.** The consumer refuses a member
  it does not recognise rather than guessing, so adding one later is a
  `schema_version` bump, not a silent widening.
- **`labelled_revision` is the image's own label, verbatim**, never
  `get_git_hash()`. Those are different claims — see CIU-21.
- The four honest non-refusals stay distinguishable in `overall`, so a consumer
  can tell "verified and matched" from "nothing was verifiable".

**Why it belongs in CIU, not in the consumer.** The instance-prefix scoping and
the label are ciu's own facts (S16/S17.1 — only ciu knows which running
containers belong to this instance). assay runs *inside* the container at
S3/S4, on the far side of its own topological boundary (assay decision A-030),
and must never shell out to docker. And a consumer parsing `ciu provenance`'s
prose would couple two estate tools through unstable human-facing text.

**Proposed SPEC ID:** S17.3 — machine-readable provenance verdict.

**What it unblocks downstream** (context, not ciu's work): assay's first Tier-2
*adjudicated* evidence integration — a lane declaring `(adjudicated,
"image-provenance")`, a bounded reader for this document, and a status mapping
(`verified-match` → PASS, `mismatch` → FAIL, `not-verified-*`/`refused-*` →
NO_MEASUREMENT-class). That is an assay package which resolves assay's A-O12 and
answers its A-O10 ("which Tier 2 integration is built first"). Named here only
so the vocabulary is designed once, for its real consumer.

### CIU-21 detail: an in-container process cannot read its own image's revision label

**Reported by:** assay, 2026-08-11, investigating whether an injected env var
could replace the co-process in CIU-20.
**Severity:** Low — CIU-20 is the complete answer on its own. This is a
convenience surface that removes a co-process from the consumer's critical path,
and it is strictly optional relative to CIU-20.

**Mechanism.** `org.opencontainers.image.revision` (S17.1) is an OCI **label**.
`_image_revision_label` (`deploy.py:666`) reads it with `docker image inspect`,
i.e. from the daemon side. A process running *inside* the container has no
access to it, so an in-container test runner cannot verify its own provenance at
all without an outside co-process.

The injection mechanism for fixing this already exists and needs nothing new:
`governance.py:1025` already injects an env var into every non-exempt service
for the KSM opt-in, and `composefile.py:936` treats `environment` as an
append-never-clobber MERGE key, so S15.3's author-precedence rule for scalar
keys does not apply (S15.11 states this explicitly):

```python
            frag["environment"] = [f"LD_PRELOAD={KSM_PRELOAD_TARGET}"]
```

**What's needed.** Inject `CIU_IMAGE_REVISION=<value>` into every non-exempt
service, where `<value>` is **read back from that service's image's own
`org.opencontainers.image.revision` label** — the same value S17.1 baked.

Three requirements, and the first is the whole point of the entry:

1. **The value MUST come from the image label, never from
   `engine.get_git_hash()`.** Those are different claims: the label is the
   image's baked truth, `get_git_hash()` is the host working tree's current
   view. A consumer that compares an injected `get_git_hash()` against its own
   `git rev-parse HEAD` of the same mounted source is comparing a value to
   itself — it always matches, including in exactly the case S17 exists to
   catch (a stale image running against a newer checkout). That is not a weaker
   check; it is a check that cannot fail, which is worse than none.
2. **Append, do not assign.** `governance.py:1025` currently does
   `frag["environment"] = [...]`. A second `frag["environment"] = [...]` would
   silently drop the KSM `LD_PRELOAD` entry and disable the shim with no error
   anywhere — the same silent-efficacy-fail-open class as CIU-14.
3. **Omit the variable when the label is absent**, rather than injecting an
   empty or placeholder value. This mirrors S17.1's own rule that nothing is
   stamped when the revision is unknown, because "a label reading `dev` looks
   like an answer and would be trusted as one". Note that the overlay is
   generated at Step 15 of the up pipeline (`engine.py:1368`), before Step 16's
   `docker compose up`, so on a plain `ciu up` with no prior bake the image may
   not exist yet and the label is legitimately unavailable.

**The cost this creates, which the consumer must be told about rather than
discovering.** `composefile.py` currently invokes no docker at all — the overlay
generator is pure text/YAML over the rendered compose. Reading labels at overlay
time gives it a docker dependency, and S4.17/S8.1's rationale for a separate
overlay is that it carries machine-derived *wiring*. Whether that trade is worth
it is ciu's call; it is the reason this is filed as a separate, lower-priority
entry rather than folded into CIU-20.

**Why it belongs in CIU, not in the consumer.** Only ciu knows the mapping from
service to image and holds the daemon-side access to inspect it; and the
injection point is ciu's own generated overlay. A consumer cannot inject into a
container it does not launch.

**Proposed SPEC ID:** S17.4 — in-container revision exposure (optional; depends
on nothing in S17.3 and vice versa).

**Honest limits, so this is not over-adopted.** An env var is weaker evidence
than a label in two ways a consumer should be told: a compose author, an `.env`
file, or `docker compose run -e` can set the same variable and win the per-key
env merge (ciu's own code comment already notes this for `LD_PRELOAD`), whereas
changing a label requires rebuilding the image; and an env var is visible only
to processes inside the container while it runs, so unlike the label it cannot
serve a post-hoc audit or S17.2's own gate over an already-running stack. This
entry therefore complements CIU-20 and does not replace it.

### CIU-22 detail: no shared-infra-join for `ciu worktree`

**Reported by:** assay/vbpub (cross-project review), 2026-08-11, corroborated
by dstdns's own reconciliation program hitting the need in practice the same
day while designing real-lane (integration/schema/E2E) isolation.
**Severity:** Medium — S16 already gives full isolation; this is about not
paying for isolation on the tiers that never diverge.

**The problem, concretely.** A project's stack is not one uniform thing.
dstdns's own tiering (independently derived, and it lines up with existing
CIU practice — S15.10's global-vs-stack governance split draws the same kind
of line): a **state/schema tier** (Postgres, Redis, app services, object
storage) that genuinely diverges per package under test — different schema,
different code, different data; and an **identity/secrets/observability
tier** (an IdP, a secrets manager, tracing, a reverse proxy) that is heavy,
slow to start, and essentially never differs between one worktree and
another. `ciu worktree add` today gives a new instance the whole stack or
nothing — there is no way to bring up only the diverging tier and join it to
an *existing* instance's already-running shared-infra services. Standing up
N full copies of the heavy tier (dstdns names its IdP specifically as
expensive and finicky to replicate) to get isolation on the cheap tier is a
real cost multiplier that scales with concurrent worktrees.

**What's needed.** A join mode: `ciu worktree add <pkg> --shared-infra
<instance-ref>` (or an equivalent profile pair), where the new worktree's own
`ciu.env`/compose overlay wires its data+app tier's containers onto the named
existing instance's network(s) for the shared services, rather than
generating fresh ones. The new instance still gets its OWN `INSTANCE_ID`
(S2) and its own diverging containers; only the shared-tier network
membership is borrowed.

**Why it belongs in CIU, not in a consumer.** Network naming, instance
identity, and compose overlay generation are already exclusively CIU's
(S1-S2, S15.3); a consumer script reaching in to attach a container to
another instance's network by hand would be exactly the kind of re-derivation
D7 already argues CIU should own instead of every consumer inventing badly.

**Proposed SPEC ID:** S16.1 — shared-infra join for worktree instances.

### CIU-23 detail: no lightweight per-worktree DATA isolation

**Reported by:** assay/vbpub, 2026-08-11, same source as CIU-22.
**Severity:** Medium — full-container isolation (what S16 gives today) is
correct but expensive for the common real-lane case.

**The problem.** The only isolation `ciu worktree` currently offers is a
full separate container per service. For the specific, common shape of a
Postgres-backed real/integration lane, that is more than the isolation
requirement actually demands: what has to diverge is the **schema and data**,
not a running `postgres` process. dstdns's own `scripts/schema-gate.sh`
already prototypes exactly this cheaper pattern by hand — a throwaway,
uniquely-named database provisioned on a live server, torn down via `trap` on
every exit path — because no first-class primitive exists. Two packages'
worktrees can legitimately have *incompatible* schemas at once (one deletes a
table the other still declares); a namespaced database per instance on one
shared Postgres server gives that isolation at a fraction of the cost of N
full Postgres containers, memory and startup time both.

**What's needed.** A first-class lightweight data-isolation mode: given a
shared Postgres (or similar) service and a worktree's `INSTANCE_ID`, provision
a uniquely-named database/schema on it, apply the worktree's own init/schema
scripts, and guarantee teardown on `worktree rm` (S16's existing
clean-before-remove ordering already gives a natural hook for this). The
project still owns *what* the init scripts do (D7's WHAT/HOW split); CIU
would own *provisioning and naming* the isolated slot, the same WHERE
responsibility it already has for full containers.

**Why it belongs in CIU, not in a consumer.** Every consumer with a
Postgres-backed real lane will reinvent `schema-gate.sh`'s pattern by hand
otherwise — naming collisions, cleanup-on-every-exit-path, and the
instance-identity binding are the same problem S16 already solved for whole
containers, one level down.

**Proposed SPEC ID:** S16.2 — namespaced data isolation for worktree
instances.

### CIU-24 detail: no concurrency budget for worktree instances

**Reported by:** assay/vbpub, 2026-08-11, same source as CIU-22/23.
**Severity:** Low — a real risk once concurrent gating is actually used, not
a defect in anything shipped today.

**The problem.** Nothing today caps how many `ciu worktree` instances can be
up at once against what the host can actually sustain. A program that wants
to gate K packages in parallel (the whole point of per-worktree isolation —
without it, "exactly one gate at a time" is a standing rule specifically
because a shared stack contaminates evidence) has no signal from CIU about
how large K can safely be; it can only find out by OOMing the host.

**What's needed.** A concurrency budget, keyed to the same mechanism CIU
already uses for resource governance: `governance.py` already resolves
`cgroup_parent` from `$CGROUP_PARENT_DEV_BACKGROUND` (S15.8); extending that
to a declared max-concurrent-instances gate (refuse `worktree add`, or
`ciu up` inside one, past the configured cap) reuses an existing mechanism
rather than inventing a new one.

**Why it belongs in CIU, not in a consumer.** Instance count and host
resource governance are already CIU's domain (S15); a consumer has no way to
see how many *other* worktree instances currently exist on the host, since
that is cross-repo, host-wide state only CIU can see.

**Proposed SPEC ID:** S16.3 — worktree instance concurrency budget.

### CIU-25 detail: no leak detector for worktree instances

**Reported by:** assay/vbpub, 2026-08-11, same source as CIU-22/23/24.
**Severity:** Low — a hygiene gap, not a correctness one; compounds CIU-24
(a leaked instance quietly eats into the concurrency budget above).

**The problem.** `ciu worktree rm` cleans up correctly when it runs (S16's
own normative clean-then-remove order) — but nothing catches the case where
it never runs at all: a crashed dispatcher, a killed session, a forgotten
manual cleanup. The stack (and, per S16, the volumes a plain `rm -rf` cannot
touch) is left running indefinitely with nothing surfacing that it happened.

**What's needed.** `ciu worktree list --stale` (or equivalent), identifying
instances whose git worktree no longer exists on disk, or whose containers
have been running past some staleness signal with no corresponding live
process; and a reap path — either an explicit `ciu worktree gc` a human/CI
job runs periodically, or an on-child-death hook a dispatcher can register.

**Why it belongs in CIU, not in a consumer.** `worktree list` already exists
and already enumerates exactly the state needed to detect this (S16); a
consumer cannot see other instances' worktrees at all, so leak detection is
structurally CIU's to own, same reasoning as CIU-24.

**Proposed SPEC ID:** S16.4 — stale worktree instance detection and reap.

### CIU-26 detail: no real-Postgres proof for the S16.2 shipped provisioner

**Reported by:** self-filed on landing `ciu-P01-worktree-isolation-primitives`
(CIU-23's own handoff), 2026-08-12, per that handoff's own instruction to give
the deferral an owner rather than leaving it merely remembered.
**Severity:** Low — the MECHANISM (naming, ordering, force semantics,
idempotent retry) is fully proven in-gate against a fake; what's missing is
proof that the real class's SQL actually works against a real server.

**The problem.** S16.2's `worktree.PostgresProvisioner` — the real,
shipped-by-default `DataIsolationProvisioner` — talks to Postgres via
`docker exec <container> psql ...` (the same docker-exec idiom
`provisioning.py`'s existing `pg:` probes already use). `tester-unified:local`
has no live Postgres server, so this package's gate exercises the FULL
naming/ordering/force/idempotency contract only against an injected FAKE
provisioner (the deliberate test seam — see S16.2 and the package's own
`nyxloom-trove/reports/ciu-P01-worktree-isolation-primitives-LOG.md`).
`PostgresProvisioner` itself is only unit-tested against a mocked
`procutil.docker` (confirms the command SHAPE — `DROP DATABASE IF EXISTS`,
the right container, error propagation) — never against a database that
actually exists.

**What's needed.** An integration lane (outside this repo's own 100%-unit
gate — it needs a real Postgres container) that runs `PostgresProvisioner.
provision()` then `.drop()` against it and confirms the database was
actually created and actually removed, not just that the mechanism dispatched
the right SQL string.

**Why it belongs in CIU, not in a consumer.** `PostgresProvisioner` is CIU's
own shipped code; a consumer adopting `--data-isolation` has no way to verify
CIU's own provisioner works before trusting it with real schema-isolation
data.

**Proposed venue:** not S16.2 itself (that SPEC id already covers the
mechanism); a dedicated integration-test lane, analogous to how S17.2's own
real-Postgres-shaped precedents are exercised — outside the unit gate.
