# CIU — Known Issues, TODO & Backlog

> **This is the canonical CIU issue tracker.** File CIU bugs and enhancements **here**, in
> the CIU product repo — not in consumer repos. Consumers (e.g. dstdns) that discover a CIU
> gap while building/operating a stack should report it here and keep only a pointer on their
> side. Each issue is fixed in this repo with **code + tests + spec + docs in lockstep** —
> a status of FIXED means the code, tests, SPEC change, and docs all landed together.
>
> Normative behaviour is defined in [`docs/SPEC.md`](docs/SPEC.md) (`S-xx` IDs). When an issue
> changes behaviour, the SPEC change is part of the fix, and the SPEC ID is cited in the entry.

Last updated: 2026-07-16.

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
unmodified, confirming the mutation-propagation mechanism already works correctly. No CIU-10 was
filed: this is not a second live bug in the current codebase. (The live repro's rendered compose
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
