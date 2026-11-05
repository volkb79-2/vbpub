# Changelog

All notable changes to rigor-record are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

<!-- cmru: release history -->

## [0.1.0] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=0190edc3c778fef71d2f2df53e250c13fae5b61f -->

### Added
- feat(rigor-record): sync, update, the git write procedure and the CLI (RR-3, RR-2a) (256127e23)
- feat(rigor-record): assemble, derive, render and check on commit keying (RR-2, RR-2a) (f51488aed)
- feat(rigor-record): schemas, validator, privacy lint and goldens (RR-1, RR-1a) (821e5f894)

### Documentation
- docs(rigor-record): SPEC, README, CHANGES and backlog on the commit-keyed design (76f99a1f5)

<!-- cmru: pending release notes begin -->
- RR-1a/RR-2a: records are keyed by judged commit (`rigor/commits/<aa>/<commit>.json`, labels carry the release tags and withdrawals; unlabelled commits get their own record). Results are valid at a commit exactly when their git object-id targets match, derived on read. `policy.toml` and the `rigor-policy-1` schema are gone; `update --fragments` takes the commit from the fragments (`--attribute-to` for a published stand-in commit) and `--tag|--commit` only names the record for qualifications and reconcile. The `waive-r2` verb is dropped. R2 results store levels `R0` and `R2`. New: command lanes (R0 only), `derive` (`valid_at`, `level_summary`), vendored cgprofile `machine/1` speed shape, percentile-vector structural test. SPEC section 18.

- RR-1a (first slice): the cgprofile `machine/1` schema is vendored (`rigor-machine-1.json`, provenance in its `$comment`, owner cgroup-profiler, copied from vbpub@215f4518a) with cgprofile's goldens in `golden/machine/`; `validate_machine` checks the CP-1 basis vocabulary (`geekbench<major>-measured|-table`). The record's own machine-class `speed` still has the pre-vendoring shape until the RR-2a golden rebuild (see `reports/rigor-rr2a-CONTINUATION.md`).

- RR-3: `rigor-record sync` and `rigor-record update`. The git write procedure of the memo (single-writer flock, temporary worktree at `origin/main`, `git commit --only`, `chore(rigor): ` subject with a `Rigor-Record: <project>@<commit>` trailer per changed commit record, push retry bounded at 5 from a fresh tip so views are regenerated never merged); `sync` downloads the release's `-rigor.json` asset, checks its sha256 (injectable `ReleaseSource`, real `gh` implementation) and folds it into the commit's earlier record when there is one (existing labels kept, privacy lint on the folded record); `update` applies R2 fragments to the record of the commit they judged under the R2 transition table (completion-only commits, `history` bounded at 5, `resolved_by` on older labelled records' survivors, base chain with `anchored`; the status is read from the fragment, `--closeout-exit` is only a cross-check; an assay-only fragment without its run-gate half refuses; the judged commit must be published on origin), `--qualification [--stable]`, `--reconcile` (R2 waivers live only in `rigor/waivers.toml`), `--withdraw`, `--expect-revision` compare-and-swap, idempotent by commit trailer; `check --commits` flags generated-file commits without prefix and trailer. SPEC sections 16-17. Tests run against a local bare remote with a fake `gh`.

- RR-2: `assemble`, `render` and `check`, and the `rigor-record` command line (cli-extended; `cli-extended>=0.3.0` is now a runtime dependency of the wheel, the library modules stay stdlib-only). New schema `rigor-index-1` (the generated `rigor/index.json`), SPEC sections 13-15, goldens `golden/assemble/` and `golden/project/`. `assemble` takes the release sequence from the `run-gate-composite` fragment (no `policy.toml`), accepts both cmru inventory kinds (`cmru-local-build` and the record step's `cmru-release-inventory`), derives R1/R3 per assay claim, records declared inputs git cannot resolve as `unresolved_inputs` instead of dropping them, and refuses a tree mismatch, an unbound review, an expired waiver and an unwaived failed level; the renderers are byte-deterministic; `check` detects hand-edited commit records (`rigor/commits/<aa>/<commit>.json`) and views by regeneration. `derive` reads a commit's R0 from its own release sequence only.
- RR-1: new project. `SPEC.md` (rigor-fragment/1, rigor-record/1, rigor-waivers/1, the distribution object, nearest-rank and min-n percentiles, Geekbench normalisation, privacy rules), the JSON Schemas, a stdlib schema and semantic validator, the privacy lint, and golden fixtures.
<!-- cmru: pending release notes end -->
