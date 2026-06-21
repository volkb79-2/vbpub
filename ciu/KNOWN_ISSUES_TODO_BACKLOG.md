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
| _(none open)_ | | | |

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

_No open issues. File the next one under **Status board** above and resolve it with
code + tests + spec + docs in lockstep._

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
