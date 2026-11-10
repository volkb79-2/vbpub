<!-- Evidence scrub 2026-11-10: environment-specific host identities, addresses, controller paths, and key material were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-05; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/RIGOR-EVIDENCE-DESIGN-MEMO-2026-11-05.md -->
<!-- relation: rigor evidence design memo -->

# Rigor-evidence record and the release/publish workflow (design memo, 2026-11-05)

Status: design proposal, amended the same day with operator decisions (marked **[OP]**). No code, config or backlog entry
was changed by this memo. It covers every vbpub tool and dstdns as consumer. Tags: **[F]** fact with a source, **[D]**
decision this memo makes, **[OP]** operator decision, **[E]** estimate, **[Q]** open operator question (section 10).

**Amendment summary (operator, 2026-11-05):**
1. Record commits use the existing release identity with a fixed message prefix. `git notes` are evaluated as a second
   channel (section 5.7).
2. CPU speed is determined ONCE per host. The installer runs Geekbench 6 after install and persists the single-core and
   multi-core scores, the result link and the claim link in host state. cgpd reads the persisted values; a table lookup
   happens only when they are absent. The 7-Zip-at-every-start idea is dropped (sections 4.4, 4.6).
3. Raw verdicts stay unpublished.
4. Records are committed on completion only; there is no start commit.

5. **HB-1 amendment (2026-11-05, controller + HB-1 implementation):** Geekbench 7 exists and is the default major
   (6 stays supported). Wherever this memo says "Geekbench 6", read "Geekbench <tool_major>". `cpu-benchmark.json` gains
   `tool_major`, `baseline_score`, `archive_sha256_verified` and `result_id`; `machine/1` speed objects carry
   `tool_major` instead of a bare version, and `x_ref = x * single_core / baseline(tool_major)` with baselines per major
   (GB6 = 2500 on an Intel Core i7-12700; GB7 = 2500 on an AMD Ryzen 7 7700). Records and distributions of different
   majors are never mixed; a major without a baseline gives `basis: null` with a reason. The tool is
   `scripts/debian-install-v2/tools/cpu_benchmark.py`; the fallback table (`cgprofile-scores/1`) must be keyed per major.

**Amendment 2 (operator, 2026-11-05; folds in `RIGOR-BOUNDARIES-REVIEW-2026-11-05.md`):**
- **A.** R1/R2/R3 count only from assay's verified claims. A `command` lane is "declared, not judged" and holds R0 only.
  `policy.toml` is deleted: lane levels come from assay `rigor = [...]` through run-gate's derived inventory, and the
  release-gate lane set is a native run-gate `kind = "sequence"` lane (sections 3.0, 7).
- **B.** R2 queue order is a controller-local ranking outside the repository (section 5.3).
- **C.** Records are keyed by **project + commit**. A release tag is only a label on a commit. Judged-target tree identity
  decides which later commits a result is valid for. This drops the `partial` special case, so dstdns records per-merge R2
  results as ordinary records (sections 3, 5, 6.1).
- **D.** dstdns uses SemVer through cmru: prefix `dstdns-v`, conventional-commit bumps, first release 0.1.0 as `cmru
  status` computes it (section 5.8).
- Following the boundaries review, schemas are producer-owned, and the human survivor disposition becomes
  `equivalent-reviewed`.

Two questions stay open: the steal threshold and back-fill. The proposals for both are kept.

Sources read:
- `vbpub/AGENTS.md` "Development, release and rigor workflow" (the agreed workflow and the "Rigor evidence" paragraph).
- `dstdns/docs/testing/RIGOR-COVERAGE-POLICY.md` (async R2, watermark, closeout, budgets).
- cmru: `docs/SPEC.md` S-REL, S2 release-gate provisional evidence, S3.5, S4.1-S4.9, S16, `docs/CONSUMERS.md` (release bodies,
  evidence retention). KI-118..KI-120 (only on branch `fix-cmru-4-20261105`, commit `02aef3bff`, not on main).
- assay 12.0.0: `docs/CONSUMERS.md` (v17 to v18 notes, progress stream, B207 scheduling), `src/assay/observer_windows.py`,
  `analysis/src/assay_analysis/cli.py`, backlog B206, B208, B217, B218.
- run-gate 25.0.1: `SPEC.md` R-26, R-44, `run-gate.footprint.json`, backlog RG-144..RG-148.
- cgprofile 2.0.0: `docs/PROTOCOL.md` sections 9-10, `docs/RG55-INTERFACE-CONTRACT.md` sections 2.4, 3.
- `vbpub/docs/design/RESOURCE-PROFILES.md` (`resource-profile/1`, `lane-profile/1`, contamination ownership).
- Release program: `R2-PORTFOLIO-PLAN.md`, `<host>/R2-BOARD-2026-11-05.md`, `reports/host-compute-REPORT.md`,
  `reports/dstdns-parallelism-REPORT.md`, `reports/DSTDNS-CMRU-ADOPTION-MEMO-2026-11-05.md` section 5 and 6.2.
- Installers: `scripts/debian-install-v2/README.md` ("Not yet in v2", io.cost benchmark step, `state_dir`) and
  `scripts/debian-install/geekbench_runner.py` (v1 runner: result and claim URL parsing, result-page scrape).

---

## 0. Decisions at a glance

1. **[OP/D] One record per project per judged commit**, committed on `main` as
   `<project>/rigor/commits/<aa>/<commit>.json` (`urn:vbpub:rigor-record:1`). Release tags are labels inside it. Two
   generated views sit beside it: `<project>/rigor/index.json` and `<project>/RIGOR.md`.
   - A later R2 result for that commit updates the same file.
   - A result is valid at another commit exactly when its judged targets have identical trees there.
   - Raw campaign data never enters the repository.
2. **[D] Producers own their data.** Assay exports judgment facts (`assay analyze export`). Run-gate exports run, tool,
   environment and performance facts (`run-gate <lane> --rigor-out DIR`). cgpd supplies measurements and machine facts.
   Each subject has exactly one owner, so no number is computed twice.
3. **[D] A small library owns the record**: `libraries/rigor-record` (stdlib-only wheel, CLI `rigor-record`).
   - It owns `record/1`, `waivers/1` and `index/1`, plus the lifecycle and the views, and it is the single writer of
     `rigor/` on `main`.
   - It does **not** own its producers' schemas. Assay owns the assay-lane export, run-gate owns the run-gate export,
     the distribution object and the percentile vectors, and cgprofile owns `machine/1` and the Geekbench baselines.
     rigor-record vendors each byte-identical and tests against it (boundaries review section 3).
   - Producers never import rigor-record.
4. **[D] cmru only attaches and publishes, and only when a project opts in.** A new optional `record_step` runs the
   project's command before `push`. Its outputs become a release asset (`<prefix>-v<V>-rigor.json` + `.sha256`) and an
   appendix to the GitHub Release body. cmru never parses a record and never commits one.
5. **[D] KI-120 (`cmru rigor`) is superseded.** Its good parts are kept: one file per unit plus index, digests instead of
   evidence bytes, an offline `check`, and record paths ignored by change detection. Its owner (cmru), its commit path (a
   commit appended to the promote push) and its URN are dropped (section 2.4).
6. **[D] The release-time record reaches `main` through `rigor-record sync`.** It downloads the published asset, verifies
   it, commits it and regenerates the views. The same command applies post-release R2 results. One writer, one lock,
   deterministic regeneration: conflicts cannot arise from merging.
   - **[OP] Identity:** the existing release identity (the cockpit's git user and `gh` token).
   - **[OP] Message prefix:** every commit starts with `chore(rigor): `.
   - **[OP] Timing:** records are committed on completion only. There is no "running" commit.
7. **[OP/D] Each lane result carries its own identity**: the judged commit, its `targets` (path to git object id: the
   project tree plus declared out-of-project inputs), the tool set (assay, run-gate, tester image, cgprofile) and config
   digests.
   - It is stored at the commit it judged.
   - It is valid at any commit where every target is identical. That is how a campaign on a "release-input" commit
     applies to the tagged commit.
   - A result from an unpublished commit is refused unless it is explicitly attributed to a published commit with
     identical targets.
8. **[D] R2 base, per lane: the newest ancestor commit holding an R2 `pass` or `pass-with-waivers` for that lane; none
   gives whole-source.**
   - The record states whether the chain is anchored in a whole-source result.
   - A result with open survivors is never a base, so its changed lines are covered again by the fix's campaign.
9. **[D] Performance summary**: per subject (R2 candidate, xdist worker, lane run), distributions of wall, CPU, memory
   (RSS peak, cgroup peak) and IO (bytes, ops), with p50, p90, p98, p99 and max (nearest rank). A percentile is null when
   n is too small to differ from max. Contaminated subjects (including a new steal reason) are counted but excluded.
10. **[OP/D] Machine-speed normalisation: Geekbench 6, measured once per host, then reused.**
    - **Measurement.** The installer runs Geekbench 6 once after install, through debian-install-v2 or, for hosts it did
      not install, the equivalent `mdt host-setup cpu-benchmark`.
    - **Persisted in host state, never in the repository:**
      - `/var/lib/mdt/host-facts/cpu-benchmark.json` holds the single-core and multi-core scores, the CPU model, the vCPU
        count and the Geekbench result URL;
      - `cpu-benchmark.claim.json` (mode 0600) holds the claim link.
    - **Reuse.** cgpd reads the persisted file and nothing ever re-measures. Only when the file is absent or stale (the CPU
      model or vCPU count changed) does cgpd fall back to a published-score table, with the basis recorded.
    - **Formula.** Time metrics are scaled to the Geekbench 6 baseline core (score 2500).
    - **In the record:** the score values, their basis, the Geekbench version and the CPU model string, plus the vCPUs
      available, the hypervisor flag, and steal and PSI measured during the run. Never the result or claim link, never a
      host name.
11. **[D] Release bodies are static.** They show "Rigor at publication" (R0, R1, R3, review, "R2 pending") and link to the
    living record. They are never edited after publication. A withdrawal is the single exception (section 5.5).
12. **[D] Everything is optional.** A project without `record_step`, without `--rigor-out` or without cgpd releases
    exactly as today. A project that declares `record_step` makes it part of its release contract: a failure stops the
    release before anything is published.
13. **[D] `git notes` are a second, internal channel, not a replacement.**
    - Anything a third party must see stays in committed files.
    - Notes carry commit-bound machine metadata for estate tooling: R2 campaign state (replacing the dropped "running"
      commit), gate evidence digests, release provenance and review verdict hashes.
    - The namespaces are `refs/notes/<ns>`. Pushed notes are public on vbpub, so the same privacy rules apply (section
      5.7).
    - Notes on commits stay valid as an auxiliary channel. **The committed record is the source of truth.**
14. **[OP] Levels are assay-verified claims only.**
    - R0 = the lane ran at the commit and passed.
    - R1, R2 and R3 exist only as assay's verified claims.
    - A `command` lane is "declared, not judged" and holds R0 only.
    - There is no `policy.toml` and no `rigor` config key. The lane set is a run-gate `kind = "sequence"` lane, and each
      lane's levels are derived by run-gate (`--list --json`, RG-d).
15. **[OP] R2 queue order is controller-local** (a ranking file on the cockpit, outside every repository). It is not a
    project fact.
16. **[OP] dstdns uses SemVer via cmru:** prefix `dstdns-v`, conventional-commit bumps, first release 0.1.0 as `cmru
    status` computes it. Per-commit records relate to tags only through labels (section 5.8).

---

## 1. What exists today

| Fact | Source |
|---|---|
| **[F]** The release gate is R0+R1+R3+review. R2 is async, on the latest release only, and older releases are never retro-run. AGENTS also requires the evidence record and the host-independent per-lane summary, with "the release tool only attaches it". | AGENTS.md |
| **[F]** The assay verdict v18 (hard cut) carries `judgment.r2.{jobs, worker_counts, profile.status_counts, schedule}` and per-killed `first_failing_test`. Per-candidate numbers are deliberately NOT in the verdict; they live in the progress `candidate.profile`, the state records and `profile-<lane>.jsonl`. | assay CONSUMERS v17 to v18, B208 |
| **[F]** `resource-profile/1` has 15 metric keys (`rss_peak_bytes`, `memory_peak_bytes`, `swap_peak_bytes`, three CPU, four IO, five PSI), each null with a reason in `missing`. `context` is an open mapping that assay copies verbatim from the observer reply (`observer_windows.py:540`). | assay `observer_windows.py:101-117` |
| **[F]** Mode `cgroup` (only at `--jobs 1`) gives exact `memory.peak`, `io.stat` summed over devices, IO ops and PSI. `process-tree` (jobs above 1) gives sampled RSS and `/proc/<pid>/io` lower bounds, and IO ops and PSI are `REQUIRES_CGROUP_MODE`. | cgprofile PROTOCOL section 9 |
| **[F]** `assay analyze` already has `collect`, `check`, `verdict`, `report`, `progress`, `plan-estimate` and `campaign`. `campaign` is the closeout (exit 0/1/2/3). | `assay_analysis/cli.py` |
| **[F]** Run-gate is the single owner of the contamination rule (RG-144). RG-145 already ingests assay profiles into `lane-profile/1` records (`per_candidate`/`per_worker` p50/p95/max) in `.run-gate/profiles/<lane>.jsonl`, bounded to 200 runs. | RESOURCE-PROFILES section 2.1, 6.4; run-gate CHANGES |
| **[F]** `run-gate.footprint.json` is tracked, with medians and max over at most 10 history runs (`keep: 10`). `RUN_GATE_EVIDENCE_DIR` holds only failing-container logs (R-26). | run-gate SPEC R-44, R-26 |
| **[F]** The cgpd session summary (schema 1/1.1) has memory peaks, `cpu.stat` totals, PSI deltas and host PSI start/end. It has no CPU model, CPU count or steal. The `host` verb has loadavg, meminfo and PSI only. The observer socket does not report the daemon version (`observer.version` is null in every profile). | RG55 contract sections 2.4, 3; assay v18 notes |
| **[F]** cmru body handlers publish "install block + exact changelog section". S4.8: no changelog commit after publication. `oci-image-release` must not upload assets. `--extra-asset` exists on `wheel-publish` and `tarball-publish`. | cmru SPEC S4.7, S4.8, catalog |
| **[F]** cmru retains `evidence_paths` under `<project>/evidence/cmru-release/<id>/`, and `**/evidence/` is gitignored estate-wide (`.gitignore:384`). So nothing under `evidence/` can be the committed record. | cmru S3.5; `git check-ignore` |
| **[F]** vbpub is a **public** repository; dstdns is private. Every committed record and every vbpub Release body is world-readable. | `gh repo view` |
| **[F]** R2 campaigns run on test hosts from host-local checkouts. Some carry config deviations: cli-extended runs with a one-line `PYTHONPATH` change in `run-gate.toml`, and cgprofile was judged on main `ba601c9e5` = cgprofile 2.0.0 plus a release-input commit. The board names hosts and absolute paths freely: that is exactly what must not leak into a record. | R2-BOARD-2026-11-05 |
| **[F]** The 2026-10-29 portfolio counts a release as mutation-judged only with a completed R2 verdict for that tag's lane. Pilots, untagged commits and waivers do not count. | R2-PORTFOLIO-PLAN |
| **[F]** debian-install-v2 deliberately did not port v1's Geekbench layer: `UNSUPPORTED_V2_SETTINGS` rejects `run_geekbench`. It already runs an optional one-time hardware benchmark (io.cost, `run_io_benchmark`) whose results go to `state_dir/io-benchmark.json` (0600), and its README calls one-time hardware characterisation "provisioning scope". | debian-install-v2 README |
| **[F]** The v1 `geekbench_runner.py` already parses the result URL (`https://browser.geekbench.com/v6/cpu/<id>`) and the claim URL (`…/claim?key=…`) from Geekbench's stdout, and scrapes the result page when stdout lacks the scores. The free Geekbench binary uploads every result automatically. | `scripts/debian-install/geekbench_runner.py:375-436`; v2 README |

---

## 2. Boundaries and data ownership

### 2.1 Who owns which fact

| Fact | Owner (producer) | Carrier |
|---|---|---|
| Lane outcome, reason code, verdict digest, judge identity (version, artifact sha256, `assay-judge-identity/N`, verdict schema), lane canonical config digest | assay | `assay analyze export` fragment |
| R1 coverage (line and branch %, floor, excluded count), R3 canary (mechanism, targets, result) | assay | same |
| R2 candidate counts, buckets, score, worker counts, profile status counts, survivors (id, path, line, operator, `first_failing_test` of the nearest kill when known) | assay | same |
| Run identity: run-gate version and wheel sha256, effective lane config digest (after central inheritance), environment, image ref, `image_id`, OCI revision/version labels, `io_sync`, exit, duration | run-gate | `--rigor-out` fragment |
| Every resource distribution: per R2 candidate (ingested assay profiles), per xdist worker, per lane run; contamination; normalised values; `phase_seconds` distributions (from assay's progress stream, assay's own clock) | run-gate | same |
| Measurements (window and session numbers, steal, PSI) and machine facts (CPU model, CPUs, hypervisor, reference speed score) | cgpd | observer replies and session summary, consumed by run-gate |
| Release identity (tag, commit, published artifacts and their sha256) | cmru, via protected env and the build inventory | inputs to the project's `record_step` |
| Review verdict, waivers, survivor dispositions | the project (human or agent decisions) | `rigor/waivers.toml`, the review document |
| Level vocabulary and lane → level (`rigor = [...]` per assay lane), verified claims | assay | `assay.toml`, verdict `claims[]`, the assay export |
| Release-gate lane set, derived lane inventory (`rigor`, `members`) | run-gate (later the ciu v8 gate) | the `kind = "sequence"` lane, `--list --json` (RG-d), the composite fragment |
| R2 queue order | the controller (estate scheduling) | a cockpit-local ranking file, never a repository |
| Record assembly, status derivation, views, commits on `main` | rigor-record | `rigor/` |

**[D] Why per-candidate distributions belong to run-gate and not to assay.** Per-candidate distributions are computed by
run-gate, although the candidates are assay's subjects. Run-gate is the single owner of contamination (RESOURCE-PROFILES
section 2.1) and already builds `per_candidate` distributions (RG-145). Doing it in assay as well would give two numbers
for one thing. Assay's export therefore carries no resource numbers beyond the `profile.status_counts` it already writes
into the verdict.

### 2.2 Interchange schemas

| Schema | Owner and definition | Writers | Readers |
|---|---|---|---|
| `urn:vbpub:rigor-fragment:1`, `kind: "assay-lane"` | **assay** `docs/CONSUMERS.md` (normative) and its canonical schema file | assay | rigor-record (a vendored copy) |
| `urn:vbpub:rigor-fragment:1`, `kind: "run-gate-lane"` and the composite | **run-gate** SPEC (a new R-rule), carried into ciu v8 S16.9 | run-gate | rigor-record (a vendored copy) |
| `urn:vbpub:rigor-record:1` (per commit), `index/1` | rigor-record | rigor-record only | humans (via views), cmru (opaque bytes), dstdns deploy tooling (`index.json` `stable`), `rigor-record queue/base` |
| `urn:vbpub:rigor-waivers:1` (TOML) | rigor-record | project authors | rigor-record |
| `machine/1` (object inside cgpd replies) | cgprofile `PROTOCOL.md` | cgpd | run-gate (copied into fragments) |
| `mdt-host-cpu-benchmark/1` (host state, section 4.6) | debian-install-v2 README (the HB-1 tool) | the installer, `mdt host-setup cpu-benchmark` | cgpd, `mdt host-setup doctor` |
| `urn:vbpub:note:<ns>:1` (git notes lines, section 5.7) | rigor-record SPEC | rigor-record (later run-gate, cmru, nyxloom) | estate tooling only |
| `resource-profile/1`, `lane-profile/1` | unchanged, `docs/design/RESOURCE-PROFILES.md` | cgpd, assay, run-gate | run-gate |
| Distribution object, nearest-rank rule, percentile vectors | **run-gate** (normative), golden vector file | run-gate | rigor-record (structural checks only, vendored vectors) |
| `machine/1`, `basis` vocabulary, Geekbench baselines per major | **cgprofile** | cgpd | run-gate; rigor-record (vendored copy) |

**[D] Versioning.**
- Every schema carries its URN with an integer major. Additive optional keys do not bump the major. A required key, a
  removed key or a meaning change does: a hard cut, as assay does it.
- Golden fixtures live with each schema's **owner**. Consumers vendor them byte-identical and test against them. This
  reverses the first draft, where rigor-record defined its producers' schemas; the `basis` drift between CP-1 and RR-1 was
  the proof that the first draft was wrong.

### 2.3 Agnosticism rules (binding for the packages in section 7)

- **assay** knows nothing of run-gate, cmru, rigor-record or cgpd's control socket. It already speaks only the observer
  contract.
- **run-gate** knows assay only as today: it runs lanes and reads verdicts and progress. To export, it runs the lane's own
  pinned judge with `analyze export` and treats the output as an opaque file, recorded by sha256.
- **cmru** runs a declared command, uploads declared files and appends a declared text. It needs no knowledge of any
  schema, and no part of the release depends on rigor-record being installed unless the project declared the step.
- **cgpd** supplies numbers and facts. It stores nothing per release and knows no tool.
- **rigor-record** knows the vendored producer schemas, git and `gh release download`. It knows no tool internals,
  never runs a test, holds no lane → level table, and no percentile, normalisation or baseline code.

### 2.4 KI-120 re-evaluated

KI-120 (`cmru rigor record|show|check`, `urn:cmru:rigor-record:1`) is right about the WHAT and wrong about the WHO and the
HOW:

1. **Coupling.** A cmru verb that records R0-R3 has to understand assay verdicts and run-gate runs, or trust opaque input
   it cannot check. Either way the release tool becomes the third consumer of test-tool schemas. AGENTS now says the
   release tool only attaches.
2. **Timing.** R2, staging and promotion happen outside any release transaction, on hosts where `cmru release` never
   runs. A cmru verb would be used mostly outside cmru's own transaction model.
3. **Commit path.** KI-120 appends the record commit to the promote push. That candidate merges `origin/main` when main
   advanced, and a concurrent R2 update to the shared index would then conflict inside a release transaction. Section 5.4
   avoids this by having one writer that regenerates instead of merging.
4. **Optionality.** A project should be able to release with cmru but without the record, or produce the record from
   another release path (`--no-build`, hand publication).

**Kept from KI-120:**
- one file per unit plus an index (now per commit; amendment 2);
- the status vocabulary (extended in section 3.4);
- evidence bytes never committed;
- the offline `check`;
- the release-time asset;
- record paths ignored by change detection (now in KI-118's scope).

**[D] KI-120 closes as "superseded by rigor-record + cmru `record_step`" when C-1 lands.**

---

## 3. The record (keyed by project and commit) **[OP, amendment 2]**

### 3.0 Model

**A result describes the commit it was judged on.**
- A record's key is `(project, commit)`.
- A release tag is an optional **label** on a commit (`labels: [{kind: "release", tag: "dstdns-v1.2.0", …}]`). It never
  identifies a record.
- Nothing distinguishes a "release record" from a "commit record" except that label.
- R2 results judged per merge commit (dstdns changed-lines lanes), R2 campaigns on a release commit, and release-gate runs
  are all the same thing: lane results stored at the commit they judged.

**Which levels a lane result holds** (closed rule, no defaults, no config key; from `RIGOR-BOUNDARIES-REVIEW` section 1.2):
- Every lane result that passed holds **R0** for its commit.
- **R1, R2 and R3** are held only through assay's **verified claims** (`claims[].rigor` with `verified_by_assay`), carried
  by the assay fragment. A lane can hold several levels.
- A `kind = "command"` lane, including one whose argv happens to run `assay run`, is **"declared, not judged"**: it holds
  R0 and nothing else, whatever its name suggests. Adopters move coverage and canary lanes to `kind = "assay"`.
- A level that no assay claim covers at a commit is `not-judged`. It is rendered as a gap, never as a pass.

**There is no `policy.toml`.**
- The release-gate lane set is the project's run-gate `kind = "sequence"` lane: its `members`, exported as a composite
  fragment.
- Each lane's derived `rigor` comes from `run-gate --list --json` (RG-d), and the R2 lanes are those whose derived `rigor`
  contains R2.
- R2 scope comes from the assay lane's `judge.base_source` and `rigor-record base`.
- Queue order is controller-local (section 5.3).

### 3.1 Repository layout (per project)

```
<project>/RIGOR.md                          generated: human view (section 6.1); do not edit
<project>/rigor/index.json                  generated: labels (tag -> commit), latest, stable, one line per recorded commit
<project>/rigor/commits/<aa>/<40hex>.json   rigor-record/1, one per judged commit (<= 64 KiB); <aa> = first two hex digits
<project>/rigor/waivers.toml                hand-written, reviewed: lane waivers and survivor dispositions
<repo>/RIGOR.md                             generated estate view: one row per project (vbpub only, RR-5)
```

`rigor/` was checked: no `.gitignore` rule matches it (the trap that hides `evidence/` and `core/`). The record schema
keeps the URN `urn:vbpub:rigor-record:1`: no record has been published yet, so RR-2a redefines `:1` in place before the
first adoption instead of cutting `:2`.

### 3.2 Keys, identity and matching a result to other commits

- **Record key:** `(project, commit)`.
  - The commit must be reachable from origin `main` or from a pushed tag, so the record never points at a commit that
    can vanish.
  - A label `release` binds a tag. Every write re-verifies that each labelled tag still resolves to the record's commit;
    a moved tag refuses.
- **Lane-result key:** `(lane, level, toolset digest, config digest)` inside the commit's record.
  - The toolset is stored once in `toolsets{}` and referenced by id.
- **Judged targets.** Each lane result carries `targets`, a map from path to git object id at the judged commit:
  - the project directory's tree: the same unit cmru's change detection uses to decide a project changed;
  - plus every out-of-project path the lane reads by declaration: assay `link_paths`, an out-of-project `source_roots`
    entry, run-gate central config.

  The fragment states them. rigor-record never infers them. The assumption that a lane reads no undeclared file outside
  its project is the one cmru's per-project versioning already makes.
- **Matching.** A lane result judged at C is **valid at** commit D exactly when every `targets` entry has the same object
  id at D.
  - Views and `base` use the newest valid result per `(lane, level)`. This is how an R2 campaign judged on a
    "release-input" commit applies to the tagged commit with the identical project tree, and how a result carries across
    commits that touched only other projects.
  - A result is **stored only at the commit it judged**. It is never copied to D; validity is computed at read time.
- **Unpublished commits.**
  - A result judged on a commit that origin does not have (a host-local clone) is refused.
  - **Exception:** with `--attribute-to <published commit>` it is accepted when every target is identical at both
    commits. The record lists `attributed_from` and the `deviations`.
- **Config deviation.**
  - A fragment whose config digest differs from the judged commit's file is accepted only when the differing files are
    outside its `targets`' source roots and test selection. An example is cli-extended's `PYTHONPATH` line in
    `run-gate.toml`.
  - The deviation is listed (`deviations: [{file, committed_sha256, used_sha256, note}]`) and rendered with a marker.
  - A deviation in sources or tests refuses.
- **Update idempotency:** an update's key is the sha256 of the sorted fragment digests. Re-applying the same fragment set
  is a no-op.
- **Compare-and-swap:** every write increments the file's `revision`, and `update` takes `--expect-revision`.

### 3.3 Shape (abridged; every status key is always present, nothing is silently absent; all numbers are illustrative, not results)

```json
{
  "schema": "urn:vbpub:rigor-record:1",
  "project": "cgroup-profiler",
  "commit": "<40hex>",
  "revision": 3,
  "labels": [{
    "kind": "release", "tag": "cgprofile-v2.0.0", "version": "2.0.0",
    "published_at": "2026-11-04T18:02:11Z",
    "artifacts": [{"name": "cgprofile-2.0.0-py3-none-any.whl", "sha256": "<64hex>"}],
    "state": "current", "withdrawn": null
  }],
  "inventory": {"sequence": "release", "members": ["r0-r1", "r3"], "r2_lanes": ["r2"]},
  "toolsets": {
    "t1": {"assay": {"version": "12.0.0", "artifact_sha256": "<64hex>", "judge_identity": "assay-judge-identity/14",
                     "verdict_schema": "urn:assay:schema:verdict:18"},
           "run_gate": {"version": "25.0.1", "wheel_sha256": "<64hex>"},
           "tester_image": {"ref": "tester-unified:local", "image_id": "sha256:<64hex>",
                            "labels": {"org.opencontainers.image.revision": "<40hex>"}},
           "cgprofile": {"daemon_version": "2.0.0", "observer_contract": 3, "ctl_contract": 2}}
  },
  "results": [
    {"lane": "release", "kind": "sequence", "members": ["r0-r1", "r3"], "outcome": "PASS", "toolset": "t1"},
    {"lane": "r0-r1", "kind": "assay", "judged_by": "assay", "levels": {"R0": "pass", "R1": "pass"}, "toolset": "t1",
     "targets": {"scripts/cgroup-profiler": "<40hex tree>"}, "verdict_sha256": "<64hex>", "config_sha256": "<64hex>",
     "coverage": {"line_pct": 100.0, "branch_pct": 100.0, "floor": {"line": 100, "branch": 100}, "excluded_lines": 0}},
    {"lane": "r3", "kind": "assay", "judged_by": "assay", "levels": {"R0": "pass", "R3": "pass"}, "toolset": "t1",
     "targets": {"scripts/cgroup-profiler": "<40hex tree>"},
     "canary": {"mechanism": "import-break", "targets": ["lib/cgprofile/daemon.py"], "outcome": "PASS"}},
    {"lane": "shellcheck", "kind": "command", "judged_by": "run-gate", "levels": {"R0": "pass"},
     "note": "declared, not judged", "targets": {"scripts/cgroup-profiler": "<40hex tree>"}},
    {"lane": "r2", "kind": "assay", "judged_by": "assay", "levels": {"R2": "survivors"}, "toolset": "t2",
     "targets": {"scripts/cgroup-profiler": "<40hex tree>"}, "deviations": [], "attributed_from": null,
     "scope": {"kind": "changed-since", "base_commit": "<40hex>", "base_label": "cgprofile-v1.2.0", "anchored": false},
     "campaign": {"id": "<opaque>", "completed_at": "…", "attempts": 2, "closeout_exit": 1},
     "candidates": {"total": 557, "executed": 557, "resumed": 0},
     "buckets": {"killed": 519, "survived": 31, "equivalent": 0, "hung": 2, "budget_exceeded": 3, "crashed": 2},
     "score": 0.9436, "worker_counts": {"1": 557},
     "survivors": {"open": 27, "equivalent-reviewed": 3, "accepted": 1, "resolved": 0, "listed": [
       {"candidate_id": "<id>", "path": "lib/cgprofile/limits.py", "line": 120, "operator": "python:compare-swap",
        "disposition": "open", "reason": null, "resolved_by": null}]},
     "history": []}
  ],
  "review": {"status": "accept", "range": {"from": "<40hex>", "to": "<40hex>"},
             "reviewer_role": "independent-fable-high", "document_sha256": "<64hex>"},
  "qualifications": {},
  "perf": [ /* section 4; one entry per lane run that fed a level */ ],
  "machine_classes": [{"id": "m1", "cpu_model": "AMD EPYC 9634 84-Core Processor", "cpus_available": 4,
                        "hypervisor": true,
                        "speed": {"basis": "geekbench6-measured", "geekbench_version": "6.x.y", "single_core": 0, "multi_core": 0,
                                  "cpus_at_measurement": 4, "measured_on": "2026-11-04"}}]
}
```

`qualifications{}` is a generic, project-named map for post-publish gates beyond R0-R3, such as dstdns `staging-<host>`,
`rehearsal-<host>` and `promotion`.
- It uses the lane-result shape.
- Hosts appear only as the project's own qualification names, which the project chooses.
- vbpub tools use none.
- When ciu v8.2 activation receipts exist, a deployment qualification cites a receipt digest instead of storing deployment
  state.

### 3.4 Statuses and transitions

**Stored per lane result and level** (the only statuses ever written):

| Value | Meaning |
|---|---|
| `pass` | the lane passed; for R1/R2/R3 the verified claim holds |
| `fail` | allowed in a record only for a lane waived in `waivers.toml`; it renders as `pass-with-waivers` |
| `survivors` | R2 complete with at least one open survivor |
| `pass-with-waivers` | R2 complete, every survivor has a reviewed disposition |
| `incomplete` | the campaign ended without completing (closeout exit 3 on its last attempt, then stopped by the controller); committed once, at that end |
| `error` | closeout exit 2, an evidence error |

**Derived at read time for a commit, never stored:**

| Value | Rule |
|---|---|
| level summary per commit | R0 = the sequence composite valid at the commit; R1/R3 = all valid assay claims for the level; R2 = `k/n` R2 lanes of the commit's inventory with a valid result, plus the worst stored status among them |
| `not-judged` | no valid assay claim for that level at this commit (no lane, or command lanes only) |
| `pending` | the commit carries the project's **latest** release label and an R2 lane of its inventory has no valid result |
| `superseded` | the same, for an older release label: R2 is never retro-run on it |
| `running` | a campaign note (section 5.7) says a campaign for this commit is running; rendered `pending (campaign running)` |
| `waived` | a level-wide operator waiver in `waivers.toml` (id plus reason) |

There is **no `partial`**. A commit judged on 17 of 80 R2 lanes renders `R2 17/80` with the worst status among the 17.
dstdns's per-merge results are ordinary records at their merge commits.

**Allowed stored transitions per `(commit, lane, level)`** (anything else refuses):

```
(absent)   -> {pass | fail(waived only) | survivors | incomplete | error}   one commit, at completion  [OP]
incomplete -> {pass | survivors | incomplete | error}                       a resumed campaign completes
error      -> {pass | survivors | incomplete | error}                       a rerun; the old entry moves to history
survivors  -> pass-with-waivers                                             waivers reconcile every open survivor
survivors  -> survivors                                                     resolved_by marks; counts change
```

R0, R1 and R3 results are written once, from the run that produced them. A rerun of the same lane at the same commit moves
the previous entry into `history` (bounded to 5).

### 3.5 R2 scope and the base chain (per lane, per commit)

- **[D]** `rigor-record base --project P --lane L --at C` prints the newest **ancestor commit** of C holding a stored
  `pass` or `pass-with-waivers` R2 result for L, or `none`.
  - The campaign uses `--base <that commit>`, or whole-source when the answer is `none`.
  - Assay's `--request-base` takes any commit; a label is shown only when one exists.
- `scope.anchored` is true when following `base_commit` records back ends in a whole-source result for the same lane.
  RIGOR.md shows an unanchored chain as "incremental since <commit or label>; earlier code not mutation-judged".
- A `survivors` result is never a base. Fix commits usually add tests and leave the survivor's source line unchanged, so a
  changed-lines campaign based on it would not re-run that mutant. Basing on the last clean result re-covers it. Assay's
  resume and witness reuse (B106, `--reuse-from`) keep the repeated killed candidates cheap.
- **dstdns per-merge changed-lines lanes** (`judge.base_source = "request"`, judged at each package's merge commit with
  `--base <merge first parent>`) store `scope: {kind: "changed-since", base_commit: <first parent>}` at the merge commit.
  - Each such result claims exactly the lines that merge changed. No aggregation rule is needed.
  - The release view shows them as history (section 6.1), not as a claim on the tag's tree.
- Survivors at commit C are marked `resolved_by: <commit>` (with its label, if any) when a later completed result for the
  same lane at a descendant commit killed the same `candidate_id`, or the candidate no longer exists. Candidate ids are
  bound to source bytes, so an unchanged line keeps its id.

### 3.6 Waivers and survivor dispositions (`rigor/waivers.toml`)

```toml
schema = "urn:vbpub:rigor-waivers:1"

[[lane]]                      # a lane allowed to fail its level (R1 example from the dstdns memo)
lane = "worker_io_dns_operations"
level = "R1"
reason = "1040/1218 lines; P-xyz restores full coverage"
plan_item = "P-xyz"
expires_before = "1.6.0"      # the release candidate refuses at an equal or later version (KI-79 window pattern)

[[survivor]]
candidate_id = "<assay candidate id>"
disposition = "equivalent-reviewed"   # equivalent-reviewed | accepted (never assay's measured `equivalent` bucket)
reason = "swapping < and <= is unobservable: both branches clamp to the same bound"
reviewed_by = "independent-review"   # a role, never a person's name (public repository)
```

A waiver is matched by exact `candidate_id`. It expires implicitly when the id disappears, because the line changed.
`rigor-record check --waivers --version V` is the preflight a project runs before its gate. `expires_before` is compared
with the SemVer of the release label being prepared. Unlabelled commits are never refused by a waiver expiry.

### 3.7 Fragments

The two fragments are small JSON files, at most 256 KiB, kept outside the repository.

- **Assay fragment** (`assay analyze export --verdict V [--progress P] --lane L --file assay.toml --expected-commit C
  --out F`):
  - It first runs `assay verify` on the verdict.
  - It emits the section 2.1 assay facts. Since A-1a it also emits the verified `claims` set, the only source of R1, R2
    and R3, and the lane's declared out-of-project inputs for `targets`.
  - Survivors are listed up to 500; beyond that, the full list goes to a sidecar file next to F.
  - The progress file is read only for `first_failing_test` attribution and the campaign start and end times.
- **Run-gate fragment** (`run-gate <lane> --rigor-out DIR`, or `RUN_GATE_RIGOR_DIR`):
  - It writes `DIR/<lane>.run-gate.json` at lane end.
  - For an assay lane, it runs the judge's `analyze export` into `DIR/<lane>.assay.json` and records that file's sha256.
  - A sequence writes one pair per member, plus a **composite fragment** (`members`, `stop_on`, outcome) that defines the
    release-gate lane set.
  - Each lane fragment carries its derived `rigor` (assay claims, or `["R0"]` for a command lane), the judged commit and
    `targets` (the project tree id plus the declared extra inputs).
  - The export schema is run-gate's own (a new R-rule; carried into ciu v8 by CIU-V8-RIGOR). rigor-record vendors it.
  - Offline: `run-gate evidence <lane> --run-id ID|latest --out DIR` reads history and the profile sidecar, bounded by
    their retention.

  It is written at run time because history keeps only 10 runs and the profile sidecar only 200.

---

## 4. Performance summary (host-independent)

### 4.1 Subjects and metrics

| Subject | Count per release | Metrics (each a distribution) |
|---|---|---|
| R2 candidate (one test-suite run against one mutant) | tens to thousands | `wall_seconds`, `cpu_seconds`, `rss_peak_bytes`, `memory_peak_bytes` (cgroup mode only), `io_read_bytes`, `io_write_bytes`, `io_read_ops`, `io_write_ops` (cgroup mode only), `phase_seconds.{materialize, command, integrity}`, `cpu_per_wall`, `steal_fraction` |
| xdist worker (RG-146) | workers x runs | same, without phases |
| Lane run (R0/R1/R3 gate lanes; the sequence total as one derived row) | 1 for the release run, plus a rolling window | `wall_seconds`, `cpu_seconds`, `memory_peak_bytes`, IO bytes and ops |

- **IO is reported as bytes and op counts, never as a rate.** Counts describe the workload. IOPS and throughput describe the
  disk.
- **Memory is never normalised.** Byte counts do not depend on CPU speed. The page cache is inside `memory_peak_bytes`,
  which is why `rss_peak_bytes` is reported beside it, each with its source label (`memory.peak`, `sampled-sum-rss`).
- **Lane runs: one sample at release time.** For long-tail visibility, the record also carries a rolling window over the
  profile sidecar:
  - only clean runs at the same `io_sync`;
  - stated as `window: {runs, first_commit, last_commit}`;
  - at most 200 runs.
- **Mixed modes.** In a parallel campaign some metrics are `REQUIRES_CGROUP_MODE` for some candidates. Each metric states
  its own `n`, so `memory_peak_bytes` can have n=40 while `rss_peak_bytes` has n=557.

### 4.2 Distributions

```json
{"unit": "s", "n": 557, "n_excluded": {"contaminated": 3, "no_measurement": 0},
 "p50": 41.2, "p90": 77.9, "p98": 118.0, "p99": 131.4, "max": 402.7, "source": "assay-clock"}
```

- **Rule:** nearest rank, i.e. the element at 1-based rank `ceil(q*n)` of the ascending sample. It is the rule run-gate
  already uses for byte series (R-36k), and it never synthesises a value nobody measured.
- **[D] Minimum n per percentile:**
  - p90 needs n >= 10;
  - p98 needs n >= 50;
  - p99 needs n >= 100.

  Below that the field is `null`, because the value would equal max and pretend to be a tail estimate. `max` and `n` are
  always present.
- **Recomputation.** Distributions are recomputed from raw subjects for every export. Stored percentiles are never
  combined, as RG-145 already requires.

### 4.3 Contamination and steal

- **Steal becomes a contamination reason.** Run-gate's RG-144 rule gains a `steal` reason: the steal fraction of the
  host's CPU time during the subject's window exceeds a threshold, provisionally **10 %** (open question 1, section 10).
- **Steal fraction** is `host_steal_seconds / host_cpu_seconds_total` from `/proc/stat` deltas (CP-1, CP-2).
- **Handling.** A contaminated subject counts in `n_excluded` and is absent from every distribution. The record also
  carries the run's `steal_fraction` distribution, and host PSI `some` for CPU, memory and IO over the run, so a reader
  sees how quiet the measurement was.

### 4.4 Machine-speed normalisation: options

The requirement is to make time metrics comparable across hosts without a custom benchmark and without naming hosts. Only
CPU and wall time need it.

| Option | How it is obtained | For | Against | Verdict |
|---|---|---|---|---|
| **Geekbench 6, run ONCE per host at install, persisted** **[OP]** | the installer runs the free Geekbench 6 binary once at the end of install, on an idle host; it reads the scores (stdout, else the result page), then persists them with the result and claim links | Measures the actual VM slice (vCPU count, SMT sharing, hypervisor and microcode effects), so the **multi-core score is meaningful** for that host. Works for masked VM models. Calibrated: 2500 is the GB6 baseline machine (Core i7-12700). The result page is a durable, checkable reference for the operator. One run per host lifetime. | The free tier uploads each result publicly; Geekbench Browser shows system details, which is why the link never leaves host state. A single run is noisy, so the conditions are recorded and a rerun is manual. GB5 and GB6 are not comparable: the major version is pinned and recorded. Needs network access to cdn.geekbench.com and browser.geekbench.com during install. Licence terms: the free tier with upload is the operator's choice; a Pro licence would allow offline runs. | **Primary** |
| Geekbench 6 single-core, looked up by CPU model in published charts | curated table entry: model string, score, source URL, retrieval date | Nothing runs. It covers hosts that predate the installer step. | A per-model mean mixes clock, cooling and memory configs. Masked models are absent. The data source is proprietary (cite it; never scrape or redistribute the database). | **Fallback only**, used when no persisted measurement exists or it is stale. Single-core only. |
| Geekbench 6 multi-core by model (published) | same | | Describes the whole chip (an 84-core EPYC 9634), not the 4 vCPUs a VM gets. It would be wrong by an order of magnitude. | **Rejected.** Only the measured multi-core value is recorded. |
| PassMark CPU Mark / single-thread rating by model | curated table entry | Very large database, including server SKUs, with a single-thread rating. | Proprietary; site terms restrict reproducing the database. Values drift. Same vCPU-slice problem. | Rejected (Geekbench covers both cases) |
| 7-Zip `7z b`, measured | — | Open (LGPL), scriptable, measures the VM slice. | A second measurement path with its own scale. **[OP]** No repeated measurements. | **Dropped** (superseded by the one-time Geekbench run) |
| sysbench cpu | measured | Open (GPL), trivial to run. | A prime-counting loop with an L1-sized working set; a poor proxy for Python test suites. Results differ across sysbench versions and LuaJIT builds. No useful per-model reference database. | Rejected |
| SPEC CPU 2017 (int rate/speed) | published per complete system, or measured | The reference standard with audited results. | Licence fee. Hours per run and needs compilers. Published per vendor system, not per VM slice. Lookup by CPU model is ambiguous. | Rejected |
| pyperformance (Python benchmark suite) | measured in the tester image | Measures exactly what dominates our suites (the CPython interpreter). Open (MIT). | No per-CPU-model published data. Minutes per run. Results depend on the Python build. It is closer to a custom benchmark than the operator wants. | Rejected now; revisit if ST lookup proves a poor predictor |
| bogomips, MHz | /proc/cpuinfo | free | Not a performance measure | Rejected |

**[D] Recommendation.**

1. **Facts recorded** for each machine class (`machine/1`, supplied by cgpd):
   - the CPU model string verbatim;
   - `cpus_online`;
   - `cpus_available` to the lane (cgroup `cpu.max` quota divided by period, intersected with the affinity);
   - the `hypervisor` flag;
   - `smt` as reported (informational: unreliable in VMs);
   - the speed object `{basis, geekbench_version, single_core, multi_core, cpus_at_measurement, measured_on}`.
     - `basis` is `geekbench6-measured` (from host state, section 4.6) or `geekbench6-table` (fallback; `multi_core` is
       null).
     - The whole object is null with a reason (`NO_HOST_MEASUREMENT_AND_MODEL_NOT_IN_TABLE`) when neither source exists.
2. **The fallback table** (`cgprofile-scores/1`) ships inside cgprofile and is versioned with it. Each entry carries the
   model string, the GB6 single-core score, the source URL and the retrieval date. Implementers fill in real values from
   the published charts: this memo deliberately quotes none. The table is read only when host state has no valid
   measurement.
3. **Formula** (normative in the rigor-record SPEC): `x_ref = x * single_core / 2500`, for `cpu_seconds` and
   `wall_seconds`.
   - Single-core is the right divisor: a candidate or a test run is one process tree, mostly single-threaded.
   - `multi_core` is recorded as host context only. It describes the whole VM, not the lane's `cpus_available` share.
   - The unit reads "seconds on the Geekbench 6 baseline core".
   - Normalised CPU seconds are the primary host-independent time metric.
   - Normalised wall is labelled approximate: IO waits, sleeps and lock contention do not scale with CPU speed. The
     `cpu_per_wall` distribution shows how CPU-bound the subject is.
4. **Who normalises.** Run-gate normalises each subject with its own machine class before computing percentiles. A
   campaign resumed on another host class is still exact.
5. **No basis:** when there is no cgpd, or there is neither a host measurement nor a table entry, the normalised
   distributions are `null` with a reason. Raw wall time (assay's or run-gate's own clock) is always present. There is no
   silent default score.

### 4.5 Per-lane perf entry (abridged)

```json
{"lane": "r2", "level": "R2", "subject": "r2-candidate", "window": "this-campaign",
 "machine_classes": {"m1": 557}, "status_counts": {"MEASURED": 40, "PARTIAL": 517, "NO_MEASUREMENT": 0},
 "normalisation": {"basis": "geekbench6-measured", "divisor": "single_core", "reference_score": 2500, "reason": null},
 "metrics": {"wall_seconds": {…}, "wall_seconds_ref": {…}, "cpu_seconds_ref": {…}, "cpu_per_wall": {…},
             "rss_peak_bytes": {…}, "memory_peak_bytes": {…}, "io_read_bytes": {…}, "io_write_bytes": {…},
             "io_read_ops": {…}, "io_write_ops": {…}, "phase_seconds.materialize": {…}, "steal_fraction": {…}},
 "host_psi_some_avg": {"cpu": 3.1, "memory": 0.4, "io": 1.2}}
```

When B217 lands, `phase_steps` simply adds more `phase_seconds.*` keys. Nothing in this design waits for it.

### 4.6 The per-host CPU benchmark: measured once, persisted in host state **[OP]**

**Where it is stored.** The values live in host state, never in a repository. The files are owned by root under
`/var/lib/mdt/host-facts/`; host-setup already keeps its pins in `/etc/mdt/host-setup.env`. Facts go under `/var/lib`
because they are measured, not configured.

```
/var/lib/mdt/host-facts/cpu-benchmark.json        0644  read by cgpd, mdt host-setup doctor, operators
/var/lib/mdt/host-facts/cpu-benchmark.claim.json  0600  root only: the Geekbench claim link, for the operator
```

`cpu-benchmark.json` (`mdt-host-cpu-benchmark/1`):

```json
{"schema": "mdt-host-cpu-benchmark/1", "tool": "geekbench", "tool_version": "6.x.y", "tool_sha256": "<64hex>",
 "measured_at": "2026-11-04T19:10:00Z", "cpu_model": "AMD EPYC 9634 84-Core Processor", "cpus_online": 4,
 "hypervisor": true, "single_core": 0, "multi_core": 0, "score_source": "stdout | result-page",
 "result_url": "https://browser.geekbench.com/v6/cpu/<id>",
 "conditions": {"loadavg1_start": 0.1, "psi_cpu_some_avg10_max": 0.4, "steal_fraction": 0.01, "duration_seconds": 540},
 "produced_by": "debian-install-v2 <version> | mdt host-setup <version>"}
```

`cpu-benchmark.claim.json`: `{"schema": "mdt-host-cpu-benchmark-claim/1", "result_url": "…", "claim_url":
"https://browser.geekbench.com/v6/cpu/<id>/claim?key=<key>", "measured_at": "…"}`. The operator opens the claim link to
attach the result to their Geekbench account. No tool reads this file.

**The installer step** (debian-install-v2, `run_cpu_benchmark`, default **on**):
1. It runs at the end of stage 2, after the io.cost step and before the final milestone, while the host is still idle.
   `run_geekbench` is no longer rejected in `UNSUPPORTED_V2_SETTINGS`; it is replaced by `run_cpu_benchmark`.
2. It downloads the pinned Geekbench 6 tarball from `cdn.geekbench.com` and verifies its sha256 against the installer's
   pin file. The same pattern exists today for `iocost_coef_gen.py.sha256`, and there is no default version.
3. It runs the benchmark once under a hard timeout (process group killed), then records load, CPU PSI and steal during
   the run.
4. It parses the result URL and claim URL from stdout, and the single-core and multi-core scores from stdout. If stdout
   lacks them, it fetches the result page and parses the two scores (v1 `geekbench_runner.py` has both parsers; port and
   test them).
5. It writes both files atomically. One milestone notification carries the scores and the result URL; the claim link goes
   only into the 0600 file and the operator-only installer log.
6. **Failure is advisory**, like the io.cost step: the step is `warned` and the install continues.
   - A run whose upload or parse failed still writes the result URL when there is one, with `single_core: null` and a
     reason.
   - `mdt host-setup cpu-benchmark --resolve` later re-reads the result page **without re-running** the benchmark.

**Hosts not installed by v2** (the dev host, older hosts): `mdt host-setup cpu-benchmark` (vbpub, mdt-cockpit
host-setup).
- It uses the same code. That is one tool file shared by sha256 pin, the io.cost generator pattern, never a second
  implementation.
- It refuses when the file already exists, unless `--force`, and refuses when CPU PSI `some_avg10` is at or above 5.
  Measuring once per host is the rule; re-measuring is a deliberate operator act.

**How it is read** (CP-1):
- cgpd reads `cpu-benchmark.json` at start and on reload. It never measures.
- It accepts the file only when `cpu_model` and `cpus_online` equal the live values.
- On a mismatch (VM resize or migration) the measurement is `STALE`. cgpd falls back to the table with
  `basis: "geekbench6-table"` and reason `HOST_MEASUREMENT_STALE`, and `mdt host-setup doctor` warns with the rerun
  command.
- An absent file gives the table fallback with reason `NO_HOST_MEASUREMENT`.

**What reaches the record:**
- `single_core`, `multi_core`, `geekbench_version`, `cpus_at_measurement`, the measurement date (day precision) and the
  verbatim CPU model string, inside the machine class;
- never `result_url`, `claim_url`, `conditions`, `produced_by` or any host identity.

A Geekbench result id is a host identifier: the public result page shows that machine's details. The privacy lint of
section 6.3 gains the pattern `browser.geekbench.com`.

---

## 5. End-to-end workflow with cmru

### 5.1 Release time (`cmru release P`, project opted in)

1. **prepare** (unchanged; CHANGES.md generated). The project's preflight runs `rigor-record check --waivers --version V`
   and checks the review document's machine line:

   ```
   <!-- rigor-review: verdict=ACCEPT from=<prev-tag> to=<40hex> reviewer=<role> -->
   ```

   The line must bind exactly the candidate commit (the dstdns memo's `preflight.py` rule, generalised).
2. **gate:** `run-gate <release sequence lane> --worktree … --rigor-out <evidence>/rigor`.
   - The project's `kind = "sequence"` lane is the release gate: R0 plus assay-judged R1 and R3. It carries no R2 claim
     per AGENTS, and all-level assay lanes are split so it doesn't.
   - `evidence_paths` includes that directory, so cmru retains the member fragments and the composite fragment.
3. **tag, build:** unchanged.
4. **record step** (new, optional, C-1). cmru runs `[steps.<record_step>]` with these protected inputs:
   - `CMRU_RELEASE_TAG` and `CMRU_RELEASE_COMMIT`;
   - `CMRU_BUILD_INVENTORY` (the `build.json` with artifact sha256s);
   - `CMRU_GATE_EVIDENCE_DIR`;
   - `CMRU_RECORD_OUT`, an empty transaction-private directory outside the worktree. The tree must stay clean for S4.1.

   The project's command is typically:

   ```
   rigor-record assemble --project P --tag $CMRU_RELEASE_TAG --commit $CMRU_RELEASE_COMMIT \
     --fragments $CMRU_GATE_EVIDENCE_DIR/rigor --review <file> --artifacts $CMRU_BUILD_INVENTORY --out $CMRU_RECORD_OUT
   ```

   It writes exactly `record.json` and `release-body.md` (at most 8 KiB). cmru refuses any other file and any size
   overrun. A failure stops the release before publication; the candidate is retained and resumable.
   - `record.json` is the record for commit `$CMRU_RELEASE_COMMIT`, carrying the `release` label and the inventory.
   - Its R2 shows as derived `pending`.
   - Completeness is checked against the composite's `members`: a missing or extra fragment refuses.
5. **push:** the body handler appends `release-body.md` under `## Rigor at publication` (C-2). The handler uploads
   `record.json` as `<prefix>-v<V>-rigor.json` with its `.sha256` sidecar. `cmru release --dry-run` runs the step into a
   temporary directory and prints the full body.
6. **promote:** unchanged. cmru commits nothing for the record.
7. **sync:** the controller runbook's next line after `cmru release` is `rigor-record sync --project P --push`. It downloads
   the asset and verifies its sha256, schema and privacy, and that the tag resolves to the record's `commit`.
   - It then merges the record into `rigor/commits/<aa>/<commit>.json`: it creates the file, or adds the label and the
     gate results to an existing record for that commit.
   - It regenerates the views (section 5.4).

   A project publishing without a CMRU body handler (`--no-build`, hand publication) uses the explicit
   `rigor-record sync --from-file <record.json>` instead.

### 5.2 What is published where

| Place | Content | Mutable? |
|---|---|---|
| GitHub Release body | install block, changelog section, "Rigor at publication": a 5-row table (R0, R1 with line/branch % or "not judged", R3 or "not judged", review, "R2 pending: runs after release on the latest release only"), the one-line tool set and a link to `<project>/RIGOR.md` on `main` | **No.** Withdrawal banner only (section 5.5). |
| Release asset `<prefix>-v<V>-rigor.json` (+ `.sha256`) | the release commit's record at publication | No: the immutable snapshot of what was proven at publication |
| `main`: `rigor/commits/<aa>/<commit>.json` | the living record of each judged commit: labels, gate results, later R2 and qualifications | Yes, by rigor-record only |
| `main`: `rigor/index.json`, `RIGOR.md`, root `RIGOR.md` | generated views | regenerated on every write |
| Bundles and tarballs (`get.py` installs, dstdns bundle) | **unchanged.** The record is produced after build (it needs the artifact digests), so it cannot be inside the artifact it describes. The bundle manifest already pins the artifact hashes. | n/a |
| Off-repo evidence archive (cockpit `artifacts/rigor-evidence/<project>/<tag>/`) | verdicts, progress, profile jsonl, fragments, the closeout output | append-only, operator-controlled; referenced from the record by sha256 only. **[OP]** Raw verdicts stay unpublished: no release asset, no repository copy. |
| `refs/notes/*` on origin (section 5.7) | internal commit-bound metadata: campaign state, gate digests, release provenance, review hashes | append-only notes; not shown on GitHub, but fetchable by anyone on public vbpub |
| Host state `/var/lib/mdt/host-facts/` (section 4.6) | the per-host Geekbench scores and links | per host; never published |

### 5.3 Post-release R2

1. **Queue.** `rigor-record queue --json` lists, per project, the commit carrying the newest release label, with every R2
   lane of its inventory that has no valid result (derived `pending`) or whose result is `incomplete`.
   - Each item names the lane, its base (`rigor-record base --lane L --at <commit>`) and the plan estimate when one is
     recorded.
   - **[OP] Order is controller-local.** `queue --rank-file ~/.config/rigor-record/r2-ranking.toml` orders projects by the
     cockpit's own ranking (seeded from R2-PORTFOLIO-PLAN), oldest pending first among equals. Without the file, the
     order is oldest pending first.
   - No repository holds a ranking.
   - The controller, or a host-compute supervisor, starts the top item when a test-host slot frees under the AGENTS PSI
     rule. rigor-record schedules nothing itself.
2. **Start.** The campaign runs `run-gate <r2 lane> --worktree <checkout at the commit> --base <base> --rigor-out
   <campaign>/rigor`.
   - **[OP] No commit on `main` at start.**
   - The cockpit writes the campaign note instead: `rigor-record note campaign --commit C --lane L --state running
     --campaign <id> --base <base>` (section 5.7).
   - `queue` reads the notes and never offers a lane whose campaign is running.
   - A campaign may run on a different published commit with identical targets (the "release-input commit" case). Its
     result is then stored at that commit and is valid for the release commit by matching.
3. **Complete or end.** After `assay analyze campaign` exits 0, 1 or 3 on the campaign's last attempt:
   - collect only `<campaign>/rigor/*.json` to the cockpit inbox;
   - archive the raw files off-repo;
   - run `rigor-record update --project P --fragments <inbox>/<campaign> --push`. The judged commit comes from the
     fragments.

   That is the campaign's one and only commit on `main`. It also appends `state=completed` to the campaign note.
4. **Latest only.** A started campaign finishes on its commit. When a newer release label appears meanwhile, its commit
   is queued next. A release commit never judged renders `superseded`.
5. **Per-merge results (dstdns).** dstdns's async R2 job judges changed-lines lanes at each package merge commit. Those
   results go through the same `update` and are stored at their merge commits. Nothing needs to distinguish them from
   release campaigns.

### 5.4 Commit mechanics on `main`

- **Who and where. [OP]** The existing release identity (the cockpit's git user and its `gh` token) runs on the dev
  cockpit. No separate bot account is used. The cockpit is the only machine with push credentials; test hosts never get a
  token.
- **How the commits are identified.** The fixed message prefix `chore(rigor): ` plus the `Rigor-Record:` trailer let
  `git log --grep='^chore(rigor): '` separate them from human commits. `rigor-record check` refuses a commit that touches
  `rigor/` without both.
- **One writer.** The writer holds a per-repository `flock` (`~/.local/state/rigor-record/<repo>.lock`). Nothing else
  writes under `rigor/` except hand edits of `waivers.toml`, which go through normal packages.
- **Procedure for every write** (`update`, `sync`):
  1. fetch;
  2. create a temporary linked worktree at `origin/main`;
  3. apply the fragments to the per-commit JSON files they judged (a deterministic function of the old records and the
     fragments; one write may touch several commits, for example a coalesced dstdns run);
  4. regenerate `index.json` and the RIGOR.md views from all records;
  5. `git commit --only -- <project>/rigor <project>/RIGOR.md [RIGOR.md]`;
  6. push to `main`.

  On a non-fast-forward rejection it discards, re-fetches and repeats from step 2, at most 5 times with jitter. It never
  merges, never rebases other commits and never force-pushes. Finally it removes the worktree. Conflicts cannot occur:
  per-commit files have a single writer, and the views are recomputed, never merged.
- **Commit message.** `chore(rigor): <project> <commit12>[ (<label>)] <lane> <level> <status>`, with the trailer
  `Rigor-Record: <project>@<commit> rev=<n> fragments=<sha256-12>`. A multi-commit write uses
  `chore(rigor): <project> <n> commits …`, with one trailer per commit.
- **Change detection.** cmru change detection ignores `rigor/` and `RIGOR.md` by default (C-3, KI-118 re-scoped), so
  record commits never make a project "changed" or produce CHANGES entries.
- **dstdns.** It uses the same mechanics in its own repository. Its `stable` pointer in `index.json` is set by
  `rigor-record update --qualification promotion --stable`.
- **Commit volume.** One commit per release (`sync`), one per completed campaign or per batch of per-merge results, one
  per waiver reconcile. Nothing per attempt and nothing at start.

### 5.5 Superseded, survivors, fix releases, withdrawal

- **Superseded:** derived at render time from labels (section 3.4). No commit is needed when a newer release appears.
- **Survivors:**
  1. The completed campaign records `survivors` with the list at the judged commit.
  2. Each survivor gets a root-cause note in its triage package.
  3. The outcome is a waiver entry (`equivalent-reviewed` or `accepted` with a reason, reviewed), or a fix package
     (normally new assertions, sometimes a product bug) followed by an immediate patch release.
  4. A waiver commit is followed by `rigor-record update --reconcile`, which moves `survivors` to `pass-with-waivers`
     when nothing stays open.
  5. The fix release's campaign marks the earlier survivors `resolved_by: <commit>` (section 3.5).
- **Withdrawal:** only when the root cause shows a defect unsafe to leave published (AGENTS).
  1. **Publish the fix release first**, so `<prefix>-latest` moves to it naturally.
  2. `rigor-record update --withdraw <tag> --reason … --replacement <tag'>` sets that release label's
     `state = "withdrawn"`. The commit's results are untouched: they still describe that commit truthfully.
  3. `cmru release-mark T --withdrawn --reason …` (C-5) prefixes the GitHub Release title with `[WITHDRAWN]`, prepends a
     two-line banner naming T', and refuses when T is the target of `<prefix>-latest`.

  Assets and tags are never deleted: digest-pinned consumers need the audit trail.

### 5.6 Failure handling

| Failure | Effect | Recovery |
|---|---|---|
| `record_step` fails or writes an undeclared file | release stops before publication, candidate retained | fix, `cmru release --resume` |
| Body appendix over 8 KiB or a privacy-lint hit | refused in the record step (assemble lints) and again by cmru (size) | fix the input; never truncate silently |
| Asset upload succeeded, `sync` not yet run | `main` lacks the record; RIGOR.md lags | `rigor-record sync` is idempotent; `rigor-record check --published` lists published tags without a committed record |
| Push rejected 5 times | nothing committed, inbox kept | rerun `update`/`sync` |
| Fragment judged on a commit origin does not have | refused, names the commit | push it, or `--attribute-to <published commit>` when every target is identical there |
| Result valid nowhere useful (targets changed before the tag) | stored at its commit; the release view shows the lane `pending` | queue the lane for the release commit |
| Campaign attempt ends early (budget, host crash) | nothing on `main`; the campaign note gets `state=attempt-ended` | resume on the same commit; commit `incomplete` only when the controller ends the campaign |
| Closeout evidence error (exit 2) | R2 `error` | rerun; the previous entry goes to `history` |
| Tool upgraded between gate and R2 | allowed: the R2 level carries its own toolset | none |
| Repin changes judge identity mid-campaign | cold run (RIGOR policy) | none; the toolset in the record says which judge produced the verdict |
| cgpd absent or older than CP-1/CP-2 | perf `status_counts` NO_MEASUREMENT, normalisation null with reason; levels unaffected | install cgpd; a missing measurement never changes a level |
| Host has no `cpu-benchmark.json`, or it is stale | table fallback (`geekbench6-table`), or null when the model is not in the table | `mdt host-setup cpu-benchmark` once, on an idle host |
| Notes push rejected (another writer) | notes not updated | fetch notes, `git notes merge -s cat_sort_uniq`, push; at most 5 tries |

### 5.7 `git notes` as a second channel: evaluation and decision **[OP-requested]**

**Mechanics that matter.**
- A note is a blob attached to an object id (a commit, or a tag object) in a namespace ref `refs/notes/<ns>`. Each
  namespace has its own history.
- `git notes append` adds text. `git notes merge -s union|cat_sort_uniq` reconciles concurrent writers.
- Reading: `git log --notes=<ns>` and `git notes --ref <ns> show <commit>`.

| Property | Effect for us |
|---|---|
| Not fetched or pushed by default | Every reader must add `+refs/notes/*:refs/notes/*` to its fetch refspec and push notes explicitly. A fresh clone, a CI checkout or a third party sees nothing. **Con** for anything public-facing; neutral for estate-internal tooling, which configures the refspec once. |
| Invisible in the GitHub UI (removed 2014) | Third parties never see notes. **Disqualifies notes as the rigor record's home.** |
| Bound to an object id | Exact SHA binding, no ambiguity (**pro**). Lost when a commit is rebased or squash-merged (`notes.rewriteRef` helps only for local rebase and amend). The estate merges `--no-ff` and tags are immutable, so notes on `main` merge commits and on release-tag commits survive. Notes on feature-branch commits are not reliable. |
| Concurrency | All writers of one namespace share one ref, so concurrent pushes are non-fast-forward. `cat_sort_uniq` merges line-oriented append-only notes deterministically. One writer per namespace (the cockpit) avoids most of it. |
| Not in source archives or bundles | `git archive`, GitHub tarballs and cmru bundles carry no notes (**neutral**: notes must never be needed by a consumer). |
| Size | No hard limit. Every version stays in the namespace's history forever, so notes are kept small: at most 4 KiB per note. |
| Protection | GitHub branch and tag rulesets do not cover `refs/notes/*`, so anyone with push access can force-push a notes ref. Notes are therefore **advisory metadata**, never a guarantee. Every fact in a note that matters is a digest checkable against committed or archived bytes. |
| Visibility | "Invisible" is not "private". On public vbpub anyone can `git fetch origin refs/notes/*:refs/notes/*`. The privacy rules of section 6.3 apply unchanged, including the `browser.geekbench.com` pattern. |
| No commits on `main` | **Pro:** no change-detection noise, no CHANGES entries, no merge pressure on `main`, and any commit can be annotated after the fact, not only release tags. |

**What goes where.**

| Belongs in committed files | Belongs in notes |
|---|---|
| Anything a third party must read: the rigor record and its views | Machine metadata for estate tooling, keyed by the exact commit |
| Anything that must survive a clone without configuration | Transient or operational states: "a campaign is running", "attempt ended" |
| Decisions that need review: waivers | Digests and ids that point at off-repo evidence |
| Every judged result, per commit (the source of truth) | Nothing that the record already says |
| Content consumers depend on | Facts that are useful but never load-bearing for a consumer |

**[D] Recommendation.**
1. Use notes as an **additional** channel. The rigor record itself stays in files and is never duplicated into notes.
2. Format: one JSON object per line (`urn:vbpub:note:<ns>:1`), append-only, merged with `cat_sort_uniq`, at most 4 KiB
   per note.
3. Writers: only the cockpit tools that already push. Readers configure the refspec through `rigor-record notes setup`,
   an idempotent `git config --add remote.origin.fetch …` that is also run by the mdt-cockpit devcontainer setup.
4. Every note line passes the same privacy lint as a record.

**Estate use cases** (namespaces):

| Namespace | Attached to | Content | Writer | Package |
|---|---|---|---|---|
| `refs/notes/rigor-campaign` | the commit being judged | campaign id, state (`running`, `attempt-ended`, `completed`, `ended`), base commit, lane, started/ended at, machine class (no host) | rigor-record | **GN-1** (needed now: replaces the dropped start commit) |
| `refs/notes/gate` | any `main` merge commit or release candidate commit | lane, outcome, verdict sha256, toolset digest, run-gate version | run-gate (`--note-gate`, cockpit only) | GN-3 (later). It answers "was this commit gated, and with what?" beyond the 10-run history cap. |
| `refs/notes/release` | release-tag commit | cmru controller wheel sha256, build inventory digest, asset digests, release log id | cmru (optional flag) | GN-4 (later). Today this provenance lives only in gitignored logs. |
| `refs/notes/review` | reviewed commit | verdict, range, review document sha256, reviewer role | nyxloom merge-p and the release preflight | GN-5 (later). The cmru preflight can check review binding mechanically. |
| ~~`refs/notes/r2-merge` (dstdns)~~ | — | — | — | **GN-6 dropped (amendment 2).** Per-merge R2 results are now ordinary committed records at their merge commits, which is stronger than a note. |

Deliberately **not** in notes:
- the rigor record (third-party visibility);
- Geekbench links (host identity; host state only);
- anything secret;
- agent session ids or transcripts (internal, and on public vbpub they would be published).

### 5.8 dstdns: SemVer through cmru, and how per-commit records relate to tags **[OP, amendment 2]**

**Versioning.**
- dstdns releases through cmru with `[project.version] strategy = "scm"`, `bump = "conventional"` and tag prefix
  `dstdns-v`.
- The first release is **0.1.0**, exactly as `cmru status` computes it for a project with no prior tag
  (`cmru/src/cmru/version.py:832-922`). No `--set-version 1.0.0` is used. This replaces decision 2 of
  `DSTDNS-CMRU-ADOPTION-MEMO-2026-11-05.md`, which had a first release of 1.0.0.
- Bumps follow conventional commits: `fix` → patch, `feat` → minor, breaking (`!` or `BREAKING CHANGE`) → major.
- **[F] cmru applies no special 0.x rule** (`bump_version`, `version.py:371-380`). The first breaking commit after 0.1.0
  therefore produces **1.0.0**, not 0.2.0.
- The adoption memo's meaning of MAJOR ("not upgradable in place"; the release-gate guard refuses a non-major release that
  changed DDL or the config schema) applies unchanged from 0.1.0 on. A 0.x line is not a licence for silent breaks.
- **[F] Precondition:** today `cmru status` refuses dstdns (`cmru.toml.schema_version must be exactly 1`). Adoption package
  P1 replaces that file first, and then `cmru status dstdns` prints the 0.1.0 plan.

**Per-commit records and tags.**
- Every dstdns judgment is a record at the commit it judged:
  - a release-gate run at the release candidate commit;
  - the coalesced whole-target R2 run at the `main` commit it ran on;
  - each changed-lines R2 lane at its package merge commit;
  - staging and promotion qualifications at the release commit.
- A tag `dstdns-vX.Y.Z` adds one `release` label to its commit's record, through `sync` from the cmru asset.
- **What "dstdns-vX.Y.Z guarantees"** is then a pure read: the results valid at that commit, own or matched by identical
  targets.
  - dstdns's lanes are repository-root lanes, so their targets are the repository tree. A result from another commit is
    valid only for an identical tree.
  - In practice the release view shows the release commit's own gate results, its own R2 campaign results, and, as
    history, the per-merge results since the previous release.
- **The watermark becomes a query:** `rigor-record base --project dstdns --lane <l> --at <commit>`, the newest ancestor
  holding a clean R2 result for that lane. `nyxloom-trove/r2-watermark` is deleted in D-1. GN-6 notes are no longer
  needed.
- **`stable`** stays the promotion pointer in `index.json`, now a label (`stable`) on the promoted release commit. When
  ciu v8.2 activation receipts exist, promotion cites a receipt digest.

---

## 6. Reports, size limits, privacy

### 6.1 Generated views (`rigor-record render`, byte-deterministic)

**`<project>/RIGOR.md`** contains:
1. A header:
   - what R0-R3 mean in one line each;
   - "R1-R3 count only when assay judged them; command lanes are declared, not judged";
   - the schema link.
2. "Current guarantees": the latest and `stable` releases, with the derived status of each level at those commits.
3. A **release table**: one row per release-labelled commit, newest first, last 25 rows. Each cell shows the status
   valid at that commit, own or matched by identical targets. Older rows stay in `rigor/commits/`.

   Illustrative rows (not real results):

   | Release | Commit | Date | R0 | R1 line/branch | R3 | Review | R2 | Tools |
   |---|---|---|---|---|---|---|---|---|
   | cgprofile-v2.0.0 | `1a2b3c4d5e6f` | 2026-11-04 | pass | 100.0/100.0 | pass | accept | 1/1 lanes: survivors 27 open / 557 (score 94.4 %), since v1.2.0, unanchored (judged at `9f8e…`, same targets) | assay 12.0.0, run-gate 25.0.1, cgprofile 2.0.0 |
   | cgprofile-v1.2.0 | `0c0d…` | 2026-11-04 | pass | not judged | not judged | accept | superseded | … |

4. **Judged since the last release**: unlabelled commits with stored results, newest first, last 25.
   - For dstdns these are the per-merge changed-lines R2 results, rendered as `lane: killed/survived, changed-since
     <first parent>`.
   - They are history, not a claim on any tag's tree.
   - The latest release detail also lists the per-merge results between the previous release and this one as a
     summary: merges judged, survivors open.
5. Latest-release detail:
   - the lanes table, with command lanes marked "declared, not judged";
   - open survivors (up to 20, by `path:line operator`);
   - the waivers in force, with expiries;
   - deviations;
   - a perf table per lane: metric | n | p50 | p90 | p98 | p99 | max, raw and normalised, with "—" for null
     percentiles and the machine classes underneath.
6. A legend.

**`rigor/index.json`** holds:

```
{latest, stable, adopted_from,
 labels: {<tag>: <commit>},
 commits: [{commit, labels, judged_at, levels: {R0, R1, R3, review, R2: "k/n <worst>"}, r2_scope, anchored}]}
```

`commits` lists every labelled commit and the last 200 unlabelled ones.

**The root `RIGOR.md`** (vbpub) has one row per project: latest release, its five statuses, and the last release with an
anchored R2 pass.

**Drift.** `rigor-record check` validates every record, confirms the views equal their regeneration and lists published
tags without a record. It is offline and optional in a project's gate. A gate that runs it fails only on invalid or
hand-edited files, never on lag.

### 6.2 Size limits

| Item | Limit | On overflow |
|---|---|---|
| `rigor/commits/<aa>/<commit>.json` | 64 KiB | survivors beyond 200 collapse to per-file counts; the full list stays in the off-repo archive and is referenced by sha256 |
| `release-body.md` | 8 KiB | refuse |
| Fragment | 256 KiB (survivor sidecar separate) | refuse |
| `RIGOR.md` | 128 KiB | fewer table rows |

**[E] Growth:**
- vbpub: about 300 release commits a year at about 12 KiB each, plus 1-3 revisions, is 4-10 MiB of raw text a year.
- dstdns per-merge records add about 2 KiB per judged merge, so 500 merges a year add about 1 MiB.
- Packed, the total is well under 3 MiB a year.
- The `<aa>/` sharding keeps directories small.

### 6.3 Privacy (vbpub is public)

- **Never in a fragment, record, view or body:**
  - host names, FQDNs, IP addresses;
  - user names and uids;
  - container, worktree, campaign or supervisor names;
  - absolute paths;
  - environment values;
  - tokens or anything token-shaped;
  - review prose;
  - hostnames embedded in qualification names other than the project's chosen labels.
- **Enforced twice:**
  1. The schemas restrict every string by pattern: enums, hex digests, SemVer and tag patterns, project-relative POSIX
     paths (no leading `/`, no `..`), lane and test node ids, and the CPU model `^[A-Za-z0-9 ()@.,_+-]{1,96}$`.
  2. A denylist scan of the serialized output for `/home/`, `/workspaces/`, `/tmp/`, `/run/`, `@`, `ghp_`,
     `github_pat_`, `browser.geekbench.com`, IPv4 and IPv6 literals, and the operator's host-domain list from
     rigor-record's local config (never committed). A hit refuses and names the JSON path. The same lint runs on every
     git note line (section 5.7).
- **Machine classes** are identified only by `(cpu_model, cpus_available, hypervisor)`.
- **Reviewers and waiver approvers** are roles.

---

## 7. Packages

Each package is one worktree, a Sonnet implementer with a brief that leaves no design choice open, an independent review
and the project's registered gate. Sizes: S = under a day, M = one to two days.

### 7.1 rigor-record (new: `libraries/rigor-record`, cmru wheel project, stdlib only, Python 3.11+)

| Id | Scope | Depends | Size | Oracle |
|---|---|---|---|---|
| **RR-1** | `SPEC.md` (fragment/1, record/1, waivers/1, distribution object, nearest-rank and min-n rule, normalisation formula, privacy rules); `schemas/*.json`; validator; privacy lint; golden fixtures (2 assay and 2 run-gate fragments, 1 record per status, a percentile vector file) | none | M | schema tests over the goldens; lint rejects each denylist class; percentile vectors match |
| **RR-2** | `assemble`, `render` (record, body, RIGOR.md, index, deterministic), `check` (offline) | RR-1 | M | goldens round-trip byte-identically; assemble refuses a tree mismatch, an unbound review, an expired waiver |
| **RR-3** | `sync` and `update`: git procedure of section 5.4 (temporary worktree, flock, CAS, `commit --only`, bounded push retry), `gh release download` with sha256 check, the transition table, `history`, `resolved_by`, `--reconcile`, `--withdraw`, `--qualification` | RR-2 | M | tests against a local bare remote: a concurrent writer causes a retry and still converges; illegal transitions refuse; idempotent re-apply |
| **RR-1a** (amendment 2) | Producer-owned schemas: vendor `machine/1` from CP-1 (fixes the `basis` drift); drop `BASELINES`, `DEFAULT_MAJOR`, `normalise` and `distribution` from the public API (structural distribution checks stay); mark the fragment schemas as vendored copies with owners named in SPEC; `percentile-vectors.json` becomes run-gate's vendored copy after RG-b; rename the disposition `equivalent` to `equivalent-reviewed` | RR-1, CP-1 | S | vendored files are byte-identical to the owners'; a CP-1 golden validates; no baseline constant left in rigor-record |
| **RR-2a** (amendment 2) | **Per-commit keying and derived levels.** Record key `(project, commit)` with `labels[]`; layout `rigor/commits/<aa>/<commit>.json`; `results[]` per lane with `targets`; levels derived (R0 = passed member; R1/R2/R3 = verified assay claims; command = R0 "declared, not judged"); completeness = the composite's `members`; multi-level lanes; `not-judged`; no `partial`; derived `pending`/`superseded`; `index.json` with `labels` and `commits`; views of section 6.1. Drops `rigor-policy-1.json`, `validate_policy`, `--policy`, the golden `policy.toml` files and SPEC 13.1. The goldens are rebuilt (a composite fragment, a command lane, a multi-level lane, a matched-by-targets R2) | RR-2, RR-1a, A-1a (fixture shape) | M | assemble refuses a missing or extra member; a command lane never yields R1/R3; a result matches a commit with identical targets and not one with a changed target; views byte-identical to the goldens |
| **RR-3** (in flight; course correction) | As before, plus: `update` writes results to the judged commit's file (several commits per write allowed); `--attribute-to` with the target-identity check; matching at read time; no `policy.toml` read (the R2 lane set is the fragments carrying an R2 claim); `--withdraw` acts on a label. If its gate is already passed, it lands as is and the corrections fold into RR-3a. | RR-2a | M | bare-remote tests as before; an unpublished judged commit refuses; `--attribute-to` with a differing target refuses |
| **RR-4** | `queue` (latest release label's commit, R2 lanes from the record's inventory, `--rank-file` controller-local ordering, oldest pending first without it), `base --lane --at` (newest ancestor commit with a clean R2 result for the lane), `adopt` (records for already-published latest releases from retained evidence, else levels `not-judged` and review `missing`, `adopted_from` set) | RR-3 | S | fixtures with a chain: base skips a `survivors` result; anchored flag; the rank file reorders projects; no rank file means oldest first; no repository file is read for ordering |
| **RR-5** | root estate view (`render --estate`) and its vbpub adoption | RR-3 | S | regeneration equals the file |
| **GN-1** | Notes support: `rigor-record note campaign` (append a `urn:vbpub:note:rigor-campaign:1` line to the judged commit, privacy lint, 4 KiB cap, push with `cat_sort_uniq` merge and bounded retry); `notes setup` (idempotent fetch refspec); `queue` reads campaign notes and skips running campaigns; the views render `pending (campaign running)` when notes are present | RR-4 | S | bare-remote tests: two concurrent appenders converge; `queue` excludes a running tag; a lint hit refuses; absent notes leave `queue` unchanged |

### 7.2 assay

| Id | Scope | Depends | Size | Oracle |
|---|---|---|---|---|
| **A-1** | `assay analyze export` (section 3.7). Verifies the verdict first. Fragment from v18 verdicts only (reads an archived v17 verdict as `R2: error, reason: VERDICT_SCHEMA_ARCHIVED`). Survivor sidecar above 500. Vendored RR-1 schema and goldens. | RR-1 | M | export of the golden v18 verdicts validates; refuses an unverified verdict; privacy lint clean; no resource numbers in the output |
| **A-1a** (amendment 2) | Additive, no major bump: emit `claims` (the verified claim set: the only source of R1, R2 and R3) and the lane's declared out-of-project inputs (for `targets`) next to `coverage`, `canary` and `r2`. Assay `CONSUMERS.md` becomes the normative text and the canonical schema copy of the assay-lane export. | A-1 | S | an all-level lane exports four claims; an unverified claim is absent; `link_paths` appear as inputs; the golden is updated and re-vendored by RR-1a |
| **A-2** | Retire the B169/B170 postponement windows from assay's own release gate: release gate R0/R1/R3 plus review, R2 async via the record; docs (CONSUMERS "Provisional release qualification") | RR-4 for `base` | S | the gate script has no window table; doc test |

Assay needs **no** capture change: the observer reply's `context` is copied verbatim, so the CP-2 additions arrive in
profiles automatically.

### 7.3 run-gate

| Id | Scope | Depends | Size | Oracle |
|---|---|---|---|---|
| **RG-a** (re-scoped) | `--rigor-out DIR` / `RUN_GATE_RIGOR_DIR`: run-gate fragment per lane and sequence member (section 2.1 facts), plus the **composite fragment** of a `kind = "sequence"` lane (`members`, `stop_on`, outcome). Each lane fragment carries its derived `rigor` (assay claims, or `["R0"]` for a command lane), the judged commit and `targets` (the project tree id plus declared inputs). For assay lanes it runs the judge's `analyze export` and records its sha256. The export schema is run-gate's own (a new SPEC R-rule). No config key. | A-1a | M | fixture lanes produce valid fragments; a sequence writes the members plus the composite; a command lane exports `["R0"]` even when its argv runs `assay run`; nothing is written without the flag |
| **RG-d** (new) | `run-gate --list --json`: per lane `kind`, `environment`, `assay_lane`, `members` and the derived `rigor` (from `assay lanes --json`, or `["R0"]`). The three-column `--list` stays frozen (R-01). | none | S | derived rigor equals `assay lanes --json` for assay lanes; a sequence lists its members |
| **RG-b** | Distributions p50/p90/p98/p99/max with the min-n rule in `lane-profile/1` and the history `profile_summary` (additive; p95 kept for sizing); per-subject normalisation from `context.machine`; `steal` contamination reason with `[contamination] steal_fraction_max` (default 10 %, open question 1); `phase_seconds` distributions | CP-1, CP-2 | M | reference vectors from RR-1; a steal-flagged subject is excluded; mixed machine classes normalise per subject |
| **RG-c** | `run-gate evidence <lane> --run-id ID\|latest --out DIR` (offline, bounded by retention) | RG-a | S | equals the live fragment for the same run |
| **CIU-V8-RIGOR** (new, ciu docs only; filed as CIU-NN) | SPEC-V8 amendments: S16.9 gains the export (`--rigor-out`, the same schema); S16.9.2 `--list --json` gains `rigor` and `members`; S16.5 states that there is no `rigor` key; S16.12 port-map rows for RG-a, RG-b, RG-c and RG-d, with their oracles in the 8.0 parity set | none | S | review of the SPEC diff; port-map rows present |

RG-b normalises with the `speed.baseline_score` carried by the machine class. It holds no baseline table of its own
(boundaries review section 4).

### 7.4 cgprofile / cgpd

| Id | Scope | Depends | Size | Oracle |
|---|---|---|---|---|
| **CP-1** | `machine/1` (section 4.4 facts). In the `host` verb (contract 2, additive) and the session summary v1.2: `host.machine`, `host.steal_seconds`, `host.cpu_seconds_total`. The speed object is read from `/var/lib/mdt/host-facts/cpu-benchmark.json` at start and reload (accepted only when model and `cpus_online` match; never measured). The fallback `cgprofile-scores/1` table is packaged and versioned. | HB-1 (file schema) | M | fixtures: a valid file gives `geekbench6-measured`; a model or CPU-count mismatch gives `HOST_MEASUREMENT_STALE` plus the table; no file and an unknown model give null with a reason; the claim file is never opened; goldens updated and vendored |
| **CP-2** | Observer contract 3, additive: the `window-close` `context` gains `machine` and `host_steal_seconds`/`host_cpu_seconds_total`; `window-open` reply gains `daemon.version` (fills assay's null `observer.version` in a later assay patch) | CP-1 | S | golden replies; assay's existing reply validator accepts them (cross-repo fixture) |


The former CP-3 (7-Zip calibration at each daemon start) is **dropped** by operator decision.

### 7.4a Host benchmark (installer and host-setup) **[OP]**

| Id | Scope | Depends | Size | Oracle |
|---|---|---|---|---|
| **HB-1** | Shared tool `tools/cpu_benchmark.py` (stdlib) in debian-install-v2, pinned by sha256 like `iocost_coef_gen.py`. It downloads the pinned Geekbench 6 tarball with sha256 verification, runs it once under a hard timeout, records the conditions (load, CPU PSI, steal), and parses the result URL, claim URL and both scores from stdout, falling back to the result page (port and test v1 `geekbench_runner.py` parsers). It writes the two files of section 4.6 atomically (0644 and 0600). `--resolve` re-reads the result page without running. Schema `mdt-host-cpu-benchmark/1` is documented in the debian-install-v2 README. | none | M | recorded stdout and result-page fixtures (with and without scores); a sha256 mismatch refuses the run; file modes; `--resolve` never executes the binary; the claim URL appears only in the 0600 file |
| **HB-2** | debian-install-v2 step `run_cpu_benchmark` (default on): end of stage 2 after io.cost; advisory failure (`warned`); one milestone notification with the scores and result URL (no claim link); `run_geekbench` removed from `UNSUPPORTED_V2_SETTINGS` with a message pointing to the new key | HB-1 | S | mocked step tests (success, parse failure, timeout); VM harness run on the `case-b` image with the Browser egress allowlist (the v2 testing guide already describes it) |
| **HB-3** | mdt-cockpit host-setup: `mdt host-setup cpu-benchmark [--force] [--resolve]` (runs the HB-1 tool from its pinned copy; refuses when the file exists without `--force` and when CPU `some_avg10` >= 5); `doctor` reports a missing or stale measurement with the command to run; run once on the dev host, <host> and <host> (operator hands: hosts already installed) | HB-1 | S | wizard and doctor tests over fixture files; a stale model gives a warning |

### 7.5 cmru

| Id | Scope | Depends | Size | Oracle |
|---|---|---|---|---|
| **C-1** | Optional `[project.release].record_step`: runs after build and before push with the protected env of section 5.1 step 4; output allowlist (`record.json`, `release-body.md`) and size caps; dry-run into a temporary directory; failure is a pre-publication failure (resumable) | none | M | no step: byte-identical behaviour to 7.2.0 (dry-run body diff empty); an undeclared file refuses; a failed step leaves no tag publication |
| **C-2** | Body handlers (`wheel-publish`, `tarball-publish`, `oci-image-release`): append `release-body.md` under `## Rigor at publication`; upload `record.json` as `<prefix>-v<V>-rigor.json` + `.sha256`. For `oci-image-release` this is a narrow, named exception to "MUST NOT upload assets": exactly this one asset and its sidecar, read back like the body. | C-1 | M | dry-run prints the appendix; the asset is read back; the KI-78 handler guarantees stay intact |
| **C-3** | KI-118, re-scoped: `project.version.exclude_paths`, plus `rigor/` and `RIGOR.md` always ignored by change detection and note collection | none | S | KI-118's oracles plus "a rigor-only commit leaves status unchanged" |
| **C-4** | cmru's own release gate becomes R0/R1/R3 (+ review); the `mutation` lane runs async on the latest release with `--base $(rigor-record base --project cmru)`; KI-79 windows and `gate-provisional` retire (SPEC S2 provisional evidence, S16) | RR-4, C-1 | M | gate snapshot without the `provisional` state; the base selection test uses a recorded chain |
| **C-5** | `cmru release-mark <tag> --withdrawn --reason` (section 5.5) | none | S | refuses on the latest target; title and banner read back; nothing deleted |

### 7.6 ciu, nyxloom, dstdns, per-tool adoption

- **ciu:** no package. It stays agnostic. Its own releases adopt the record like any tool (AD-x).
- **N-1 (nyxloom, S, docs):**
  - TESTING-METHODOLOGY: R3 = assay canary lanes (closes dstdns V6: `nyxloom gate verify` does not exist).
  - A "Rigor record" section pointing at the rigor-record SPEC.
  - The async-R2 loop names `rigor-record queue/base/update`.
  - The merge-p/dispatch skills reference it for R2 follow-ups.
- **D-1 (dstdns, M; no longer held; replaces adoption-memo §5 interim and P12):**
  - the release gate is the `release-gate` `kind = "sequence"` lane (already the adoption memo's shape), run with
    `--rigor-out release-evidence/rigor`;
  - `cmru.toml` gets `record_step = "rigor"` → `rigor-record assemble`;
  - `release/rigor-waivers.toml` becomes `rigor/waivers.toml`;
  - `r2_campaign.py` sends both the coalesced whole-target results and the per-merge changed-lines results to
    `rigor-record update`, each stored at its judged commit, and uses `base --lane --at`;
  - `nyxloom-trove/r2-watermark` is deleted;
  - staging and promotion become `qualifications`, with `stable` a label;
  - SemVer from 0.1.0 (section 5.8);
  - a RIGOR-COVERAGE-POLICY section is added. Its "a lane declares R0/R1 or R0/R2" rule is already compatible, because
    levels come from those declarations;
  - the `requirements.txt` floor for rigor-record is marked D-672 exempt.

  It depends on RR-2a, RR-3, RG-a, RG-d, C-1, C-2 and dstdns P1 (the valid `cmru.toml`).
- **AD-<tool> (vbpub, S each; re-scoped by amendment 2):**
  1. Add one `kind = "sequence"` release lane, and point cmru `run-tests` at `run-gate <seq> --rigor-out …`.
  2. Move command-lane R1 and R3 to assay lanes (`judge.coverage` over the same `coverage.json`, `judge.canary`).
     Otherwise they stay "declared, not judged".
  3. Split every all-level assay lane so the release sequence carries no R2 claim: ciu, worktree, nyxloom
     `session-extract`, mdt-cockpit, mdt-python-debug. The pattern is pwmcp `r1`/`r2`.
  4. Turn command lanes that wrap `assay run` into `kind = "assay"`: cli-extended `r2`, cmru `mutation`.
  5. Add `waivers.toml` only when needed. No `policy.toml`.
  6. `cmru.toml` gets `record_step` and `evidence_paths`.
  7. `rigor-record adopt` for the current latest release, then the first release with a record.

  Order by R2-PORTFOLIO risk: cgroup-profiler as pilot (small, its v2.0.0 campaign is running), then ciu, pwmcp, iobench,
  cli-extended, nyxloom, cmru, run-gate, assay last (its self-qualification needs B110).
- **Later notes packages (S each, not scheduled with this wave).** Each is filed in its tool's backlog when its consumer
  needs it:
  - GN-3, run-gate `--note-gate` (`refs/notes/gate`);
  - GN-4, cmru optional release-provenance note (`refs/notes/release`);
  - GN-5, review-verdict note written by nyxloom merge-p and read by the release preflight.

  All three reuse GN-1's note writer: one implementation, a namespace per use. GN-6 is dropped: per-merge results are
  committed records now.

### 7.7 Dependency order

```
Built (committed, unmerged, per RIGOR-BOUNDARIES-REVIEW): RR-1, RR-2, A-1, C-1, C-2, C-3, CP-1, HB-1, HB-2, HB-3. RR-3 in flight.

A-1 ──> A-1a ──┐
CP-1 ──> RR-1a ┼──> RR-2a ──> RR-3 (corrected) ──> RR-4 ──> {A-2, C-4, RR-5, GN-1}
               │
A-1a ──> RG-a (+composite) ──> RG-c ;  RG-d (independent) ;  CP-1 ──> CP-2 ──> RG-b
CIU-V8-RIGOR any time, before the v8 gate port
{RR-2a, RR-3, RR-4, GN-1, RG-a, RG-d, C-1, C-2, C-3} + its r0-r1/r3 → assay move ──> AD-cgroup-profiler (pilot)
pilot ──> other AD-*, D-1 (also needs dstdns P1)
HB-3 run on dev host, <host>, <host> before the first perf entry is expected to normalise
N-1 any time (adds: "a lane's rigor is assay's `rigor`; there is no separate mapping")
```

**Parallel waves (remaining work):**
1. RR-1a, A-1a, RG-d, CIU-V8-RIGOR, and the RR-3 course correction.
2. RR-2a, RG-a and CP-2.
3. RR-3 finish (or RR-3a), RR-4, GN-1 and RG-b.
4. The pilot adoption, then the other AD-* and D-1.

RG-b can ship before the record exists: it improves `run-gate history` on its own.

---

## 8. Existing backlog items: superseded or re-scoped

| Item | Disposition |
|---|---|
| cmru **KI-120** `cmru rigor` | **Superseded** by RR-1..RR-4 + C-1/C-2. Close when C-1 lands. (Note: KI-118..KI-120 exist only on branch `fix-cmru-4-20261105`, `02aef3bff`, not on main.) |
| cmru **KI-118** `exclude_paths` | **Re-scoped** into C-3 (adds the default ignore of `rigor/` and `RIGOR.md`) |
| cmru **KI-79** deferral windows, **KI-109** provisional minor-release gate | **Retired** by C-4: R2 is no longer a release-gate member, so no window is needed |
| cmru KI-119 (cross-repo providers) | unaffected |
| dstdns adoption memo decision 7, §5, V4, P12 | **Superseded** by this memo: owner rigor-record, URN `urn:vbpub:…`, asset + `sync` instead of a promote-push commit; the dstdns side becomes D-1. V2 = C-3. V6 = N-1. |
| assay **B169/B170** provisional release qualification | **Retired** by A-2 |
| assay **B206, B208** | unchanged; the record reads their output (A-1 reads v18; run-gate reads the profiles) |
| assay **B217** per-step phase timings | unchanged in scope; when present, RG-b exports `phase_steps` as extra `phase_seconds.*` distributions. No dependency either way. |
| assay **B218**, B209, B213, B214 | unaffected (performance and durability investigations) |
| run-gate **RG-145** | **Extended** by RG-b (p90/p98/p99, min-n, per-subject normalisation); its `per_candidate` stays the single owner of candidate distributions |
| run-gate **RG-144** | **Extended** by RG-b (steal reason) |
| run-gate RG-146/RG-147 | unaffected (worker sizing, scaling); their records feed perf entries unchanged |
| run-gate R-26 `RUN_GATE_EVIDENCE_DIR` | unchanged; failure logs stay separate from rigor fragments |
| cgprofile CP-55 (observe scope) | unaffected |
| debian-install-v2 `UNSUPPORTED_V2_SETTINGS` rejection of `run_geekbench` (and README "Not yet in v2") | **Re-scoped** by HB-2: Geekbench returns as a one-time provisioning step (`run_cpu_benchmark`), not the v1 adaptive-sizing layer |
| dstdns `nyxloom-trove/r2-watermark` (RIGOR policy) | **superseded** by `rigor-record base --lane --at` over per-commit records (D-1); GN-6 dropped |
| dstdns adoption memo decision 2 (`--set-version 1.0.0` first release) | **Superseded** by amendment 2: first release 0.1.0 as `cmru status` computes it; conventional bumps; the MAJOR meaning is kept (section 5.8) |
| rigor-record `policy.toml` (`rigor-policy/1`, RR-2 SPEC 13.1, RRK-05) | **Deleted** (RR-2a): levels derived from assay claims, the lane set from the sequence lane |
| Boundaries review **[OP] 3** (dstdns R2 `partial` vs an aggregation rule) | **Resolved** by per-commit keying: neither is needed |
| R2-BOARD findings (run-gate-project R0 baseline failure under `assay-r2`, nyxloom cap overflow, cli-extended `cowtree` path) | unaffected; they block those tools' R2, and the record shows `pending` or `incomplete` honestly meanwhile |

---

## 9. Risks

- **A single Geekbench run per host is one sample.** Noise from a neighbour on a shared hypervisor goes into that host's
  score for its lifetime. Mitigation:
  - the run happens on a freshly installed, idle host;
  - the conditions (load, PSI, steal) are persisted, and `doctor` flags a run whose steal or PSI exceeded the
    contamination limits;
  - a rerun is one deliberate `--force`.

  Normalised numbers are comparable within roughly 10-20 %, not exactly. RIGOR.md says so in its legend.
- **Geekbench availability and terms.** The free tier depends on the Geekbench CDN and Browser and their terms. A broken
  download or parse is advisory: install continues, and the host falls back to the table, or to null.
- **Targets declared too narrowly.** If a lane reads an undeclared file outside its project, a result could be matched to
  a commit where that file changed.
  - Mitigation: `targets` are the whole project tree plus declared inputs (the same assumption cmru's per-project
    versioning makes).
  - Every release commit's own gate run is stored at that commit, so release claims for R0, R1 and R3 never rely on
    matching.
  - Only R2 campaigns on a "release-input" commit do.
- **Notes are unprotected refs.** A force-push can rewrite a notes namespace. Nothing load-bearing lives only in notes:
  the campaign state is re-derivable from the committed record plus the off-repo campaign directory.
- **Process-tree lower bounds at `--jobs` above 1.** RSS is sampled and IO bytes are lower bounds. Each distribution
  carries its source label, so a reader never compares a cgroup peak with a sampled RSS.
- **Writer lag.** RIGOR.md lags a release until `sync` runs. Mitigation: `sync` is the runbook line right after
  `cmru release`, and `check --published` names the gap.
- **One more estate tool.** rigor-record is deliberately small (stdlib, no daemon). The alternative of hosting it in assay
  would put git push mechanics and release assets into the judge's zipapp, which dstdns vendors.
- **Public exposure.** A privacy-lint false negative would publish an internal detail permanently in git history. Two
  layers (schema patterns plus denylist) and the pilot's review of the first records reduce it. A leaked record needs a
  history rewrite, which the operator decides.

---

## 10. Operator questions

**Decided by the operator (2026-11-05), now folded into the design:**
- Identity: the existing release identity with the `chore(rigor): ` prefix (section 5.4).
- CPU speed: measured once per host by Geekbench 6 at install, persisted in host state with the result and claim links;
  table lookup only as a fallback; 7-Zip dropped (sections 4.4, 4.6).
- Raw verdicts stay unpublished (section 5.2).
- Completion-only commits; "running" lives in a git note (sections 3.4, 5.3, 5.7).
- Amendment 2:
  - levels only from assay-verified claims, command lanes "declared, not judged", `policy.toml` deleted (section 3.0);
  - controller-local R2 queue ranking (section 5.3);
  - records keyed by project + commit, with tags as labels (section 3);
  - dstdns SemVer through cmru from 0.1.0 (section 5.8).

**Still open (the coordinator asks the operator separately; the proposals stand until then):**
1. **Steal contamination threshold:** 10 % of host CPU time during a subject's window (proposed), or another value?
2. **Adoption cutoff:** records only from adoption onward, with the current latest release adopted from retained evidence
   and older releases listed as having no record and never back-filled (proposed, consistent with "never retro-run"). Is
   that acceptable?
