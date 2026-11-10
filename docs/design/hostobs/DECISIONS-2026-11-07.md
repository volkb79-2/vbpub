<!-- origin: 2026-11-07; status: decided; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/hostobs-DECISIONS-20261107.md -->
<!-- relation: operator decisions -->

# hostobs: operator decisions (interview 2026-11-07), binding addendum to hostobs-PLAN-v3.md

1. PROCESS MODEL: probe + actuator SPLIT FROM DAY ONE (two processes; unprivileged read-only probe, small privileged actuator with a closed verb list that re-derives targets itself). Supersedes plan v3's 'one root daemon in v1'.
2. FAST PROTOCOL: design for a fast and efficient communication protocol (binary frames) for volume and latency-critical clients (TUI, focus recorder, sessions); JSON only for low volume / higher latency tolerated paths (ctl, shell launchers, debugging).
3. ctl: `hostobsctl` and `hostobsd` verbs in PYTHON on cli-extended now; a Rust ctl (sharing the TUI's client crate) later ONLY IF measured to be needed. (cli-extended is Python-only; no Rust contract crate now.)
4. PYTHON CLIENT LIBRARY: a sibling Python library for hostobs; our Python tools take it as an OPTIONAL dependency: if absent, hostobs-related functionality is unavailable but the tools keep working. A generic lane/tool config key `require_profiling` (default NOT set => runs degraded with a visible notice); a lane that sets `require_profiling = true` refuses naming the missing package/endpoint. Evidence lanes (assay R2/R3, observer-enabled run-gate lanes) set it explicitly.
5. RUST RIGOR (0.x): cargo test + clippy + coverage floor + cargo-mutants on the core crates + golden-fixture conformance + VM lanes for privileged verbs; assay Rust adapter becomes a 1.0 prerequisite.
6. v1 SCOPE: accept the plan's cuts (no crash-safe sessions, no runtime config verbs, no BPF, no HTML reports in the daemon) BUT docker-name/systemd-unit ENRICHMENT and DAMON are IN v1.
7. REINSTALL: v1001 now (then r1002 after tnt R2 ~2026-11-09 11:30Z) on today's stack with the data-area-v2 recipe; hostobs arrives later by another clean reinstall.
8. cgprofile: finish 3.0.0 (rename) and keep releasing it until hostobs is stable and feature-complete.
9. Fresh design only: no migrations, no legacy, rollout on empty hosts.
10. Implementers: Codex gpt-6-luna while available (the 5h limit hit ~14:02 UTC, reset 'at 3:33 PM' operator-local = 14:33 UTC if CET); Sonnet implementers while Codex is blocked; switch back to Codex when available.

## Clarification 2026-11-07 (operator, after the astra opinion)
- Host history storage is ALREADY decided and in the sampler config: tiered with reduced granularity per step (hot 1 s x 60 s, warm 5 s x 300 s on tmpfs, cold 60 s steps on disk capped at 1 GiB, writer queue 8 MiB with counted drops), all configurable via the settings file in /etc (/etc/hostobs/hostobsd.toml). Astra critique 2 ("unbounded memory") therefore applies ONLY to the session evidence path (samples kept until stop) and the dictionary under cgroup churn, not to host history.
- Plan v4 action: session samples ride the same tier machinery (or a bounded spool) with a per-session byte budget reserved at admission (refuse at start, never drop later); dictionary entries of inactive cgroups retire to disk segments. Open detail: hot tier on tmpfs (sampler) vs RAM only (operator wording); cold keep_s 1 day in sampler vs 14-day cold tier in the iogovmon line of plan v3: confirm.

## Rust methodology decisions (operator interview, 2026-11-08)
- Mutation engine for Rust in assay: **cargo-mutants** (assay runs it, parses `outcomes.json`, enforces caps/survivors/provenance); no own mutator now, seam stays open.
- Coverage bar: **stable line + region** (cargo-llvm-cov, lcov into assay's existing parser): 100% diff-line coverage plus a crate floor; **branch coverage on stable is a documented known issue**.
- Toolchain/crates: tester image carries the Rust toolchain and tools; dependencies **vendored** (`cargo vendor`, `--offline --locked`, no network in gates); **no frozen pins**: versions are updated by **cmru under the 14-day freshness rule** (exceptions for compatibility or fast CVE fixes). Interpretation to confirm: at any moment Cargo.lock + the vendor dir pin exactly one state (needed for `--locked`), cmru moves that state forward on its schedule (backlog CMRU-NATIVE).
- Extras NOT up front, planned via backlog after the first releases: cargo-semver-checks, miri + sanitizers in a VM lane, benchmarks with regression gate. **Fuzzing is its own topic for assay/run-gate** (later). Baseline per commit: fmt, clippy -D warnings, nextest + doctests, llvm-cov floor, cargo-deny, proptest round trips, golden fixtures shared with Python. `#![forbid(unsafe_code)]` everywhere except actuator/kernel crates.
- Operator rule 2026-11-07: simple first; hard/rare things become documented known issues / by design.

## Rust methodology: answers 2026-11-08 (second interview; amends the first block)
- Dependencies for gates: **NOT vendored in git.** The tester image `tester-unified-rust` (sibling of tester-unified; stable toolchain only, no MSRV lane) carries the toolchain, cargo tools and a pre-fetched crate cache for every Rust project's Cargo.lock; cmru builds it with network (`cargo fetch --locked`, checksums in Cargo.lock); lanes run `--offline --locked` with a precheck that fails loudly naming the missing crate when image and lock disagree. Docker serves the image locally on every host. Costs: image rebuild on every Cargo.lock change (cmru 14-day updates, dependency-adding branches), image size, bare checkout not offline-buildable outside the image. (Supersedes the vendoring decision of the first block; CMRU-NATIVE is now: crates.io version source + lock update + image rebuild, no `cargo vendor`.)
- Coverage/mutation: R2 is WAIVED for now to move fast (important tests + reviews up front, per-release waiver documented like the Python waivers); target remains the full R1-R3. R1 initial floors: 90% line / 85% region per crate (+ assay's 100% diff-line rule). Mutation end-state policy: diff-scoped 100% killed, exclusions only in `.cargo/mutants.toml`, no `#[mutants::skip]`; whole-crate campaigns advisory on hosts.
- Image: sibling `tester-unified-rust`, stable only, rust toolchain + cargo tools + dev-dependencies move together in one cmru-driven reviewed update (14-day rule); R3 canary 'import break' = panicking `#[cfg(test)]` module (no objection).

## Interview round 3 (2026-11-08): plan v4 decisions
- Spans: separate `spans/1` document referenced from the sealed summary (accepted).
- Sessions: exact nearest-rank percentiles with a bounded session length (default cap 24 h; `truncated` seal at the cap) (accepted).
- TUI engine: operator answer "1, keep existing topos python code for now": interpreted as ratatui (Rust, binary frames) as the TARGET engine while the existing Python topos stays in service until hostobs M3 (no rewrite now, no new investment in Python collectors). TO CONFIRM with operator.
- hostobs on hosts: "1, full probe + actuator on r1002/v1001": interpreted as full probe+actuator on r1002/v1001 and observe-level only on the controller host (the option-1 text). TO CONFIRM.
- iobench stop rules: io-full PSI is not a stop signal (see reports/ib-vq-*).

## Interview round 7 (2026-11-08)
- Codec: NEW FIXED-WIDTH TYPED COLUMNS for live frames, history and disk (option 1); FlatBuffers only as a fallback if spike SP-4 shows the custom codec costs too much to own.
