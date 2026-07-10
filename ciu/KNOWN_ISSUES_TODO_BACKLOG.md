# CIU — Known Issues, TODO & Backlog

> **This is the canonical CIU issue tracker.** File CIU bugs and enhancements **here**, in
> the CIU product repo — not in consumer repos. Consumers (e.g. dstdns) that discover a CIU
> gap while building/operating a stack should report it here and keep only a pointer on their
> side. Each issue is fixed in this repo with **code + tests + spec + docs in lockstep** —
> a status of FIXED means the code, tests, SPEC change, and docs all landed together.
>
> Normative behaviour is defined in [`docs/SPEC.md`](docs/SPEC.md) (`S-xx` IDs). When an issue
> changes behaviour, the SPEC change is part of the fix, and the SPEC ID is cited in the entry.

Last updated: 2026-06-21.

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
| CIU-9 | `reset_service` volume cleanup silently no-ops in DooD when the operator can write the logical path | High | OPEN |

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
