# assay v1 (P00–P14) — post-series adversarial review

**Reviewer:** gpt-5.6-sol, high reasoning effort, `--sandbox read-only`, resumed
codex session `019fd977-f091-7dd1-8af5-38c41db89507` (the same review →
guidance → repair thread whose original pre-P02 pass found 23 confirmed
defects and reshaped the series carving).

**Date:** 2026-08-07, immediately after the P00–P14 series was merged to
`main` and reported gate-green (1529 passed, 100% statement and branch
coverage).

**Scope of this pass:** the actual shipped code across all fifteen packages
— not the carving/process this time, which the original pre-P02 pass already
covered. Sol had read access to the full `vbpub` monorepo and was asked to
review what shipped, name low-hanging fruit, recommend estate integration
order, carve concrete next-step packages for real Go usability and a
TypeScript/React adapter, and answer several specific questions the
controller posed.

**Why this exists:** the P02–P14 series was reviewed and merged package by
package, each time independently re-verified by the controller. That
discipline caught real defects at nearly every step (see `decisions.md`,
A-090 through A-133) but never stepped back to ask whether the *assembled
product* — not each package's own diff — actually does what the design
promises. This review is that step back, and it found that it does not, yet:
several confirmed defects, and a design-level gap (no rigor above R0 is
reachable through the actual CLI) that no single package's own review could
have caught, because it is a property of the *absence* of a package, not a
defect within one.

**Token cost, for context:** this read-only review pass alone used
approximately 1.58M tokens. Whoever resumes this session for further work
should expect a large fixed orientation cost per turn, mitigated somewhat by
resuming (rather than starting fresh) since the session's own prior context
carries forward.

---

## Sol's judgment, verbatim

> My judgment is blunt: assay's foundational model is mostly sound, but the
> shipped product is not yet safe to adopt as the estate's gate. The green
> suite proves its library components; it does not prove the executable
> product described by the design. Three confirmed defects can produce false
> acceptance, and the CLI cannot exercise R1–R3 at all.
>
> CLI integration should be the first new capability in v1.1, after two
> prerequisite correctness repairs. Go and React work should not start
> before that path exists.

---

## Ranked shipped-code findings

### 1. CONFIRMED — CRITICAL: declared R1–R3 lanes cannot run

`src/assay/cli.py` (`_cmd_run`) always constructs exactly one R0 claim. It
never calls `evaluate_r1`, mutation execution, canary execution, or
attestation loading. `assemble_verdict` then rejects any R1+ lane because
its declared claims are missing. There is also no `assay mutate`
subcommand, despite A-031 naming one.

Concrete failure: a correctly configured R1 lane completes its command,
then terminates `ERROR/BAD_LANE_CONFIG` instead of producing a coverage
judgment. R2 and R3 are equally unreachable.

Established by reading `_cmd_run`, `build_parser`, `runner.evaluate_r1`,
`mutation.py`, and `canary.py`, and confirming via `rg` that no CLI call
site exists for any of the R1+ producers. This contradicts DESIGN-GUIDE
§§1, 6, and 12 and decision A-031. It is not a defensible v1/library
boundary — it is a gap.

### 2. CONFIRMED — CRITICAL: `assay verify` accepts semantically contradictory claims

`src/assay/verify.py` reconstructs the dataclass graph and checks the
top-level rollup, but never re-judges the R1, R2, or R3 *payload* itself.
Sol altered valid fixtures in memory and fed them to `verify_document`:

- An R1 claim's `coverage.pct` set to `0.0` while `status` stayed `PASS` —
  **accepted** (empty failure list).
- An R2 claim with a genuine surviving mutant added while `status` stayed
  `PASS` — **accepted**.
- An R3 claim with the transformed canary outcome set to `PASS` (i.e. the
  canary never actually failed) while `status` stayed `PASS` — **accepted**.

This directly falsifies A-129's own claim that the verifier rejects a
"coverage mismatch." The deeper obstacle: schema v2 does not record
`fail_under`/`allow_excluded`, so an independent consumer cannot even in
principle re-derive whether an R1 status was correct from the payload
alone — the policy that decided PASS/FAIL is never recorded in the
artifact.

### 3. CONFIRMED — CRITICAL: valid patch contents can erase or shift changed lines

`src/assay/diff.py`'s `parse_added_lines` is not stateful about whether it
is reading a hunk header or a hunk body — it checks `line.startswith("+++
")` unconditionally on every line, including deep inside a hunk body.

Two reproductions:

- A source file gains a genuinely new line whose own content begins `++`
  (e.g. `++ value`). Once diffed, that becomes the literal patch line `+++
  value`. The parser misreads it as a new-file header rather than an added
  line — the real added line silently vanishes from the changed-line set.
- Git's `\ No newline at end of file` marker line advances the new-side
  line counter, so a real added line 1 gets reported as line 2.

**Independently confirmed by the controller** by reading `parse_added_lines`
directly: the `line.startswith("+++ ")` check has no accompanying state
tracking for "am I between hunks, awaiting a header, or inside one" — the
bug is real and exactly as described.

Concrete failure: changed executable lines can be omitted or misattributed,
producing a false PASS (a genuinely uncovered added line never gets checked
at all) or a false failure (a line attributed to the wrong number).

### 4. CONFIRMED — HIGH: ambiguous coverage data is accepted and can become order-dependent

`src/assay/evaluate.py` builds `cov_by_repo_path` via a bare dict
comprehension (`{adapter.normalize_coverage_key(raw_key): file_cov for
raw_key, file_cov in profile.files.items()}`) with no collision detection,
and `FileCoverage` does not enforce that executed/missing/excluded line
buckets are disjoint.

**Independently confirmed by the controller** by reading the comprehension
directly: it is unambiguously last-key-wins with zero collision handling.

Reproductions:
- A single file's coverage data placing line 1 in both `executed` and
  `missing` simultaneously produces `PASS 100.0` while still reporting that
  same line as missing — self-contradictory and accepted.
- Two raw coverage keys that both normalize to the same repo path: which
  one "wins" depends purely on their order in the source JSON. Reversing
  that order flips the verdict from PASS to FAIL on the same underlying
  claim.

Concrete failure: equivalent input can receive opposite verdicts depending
on JSON object ordering, and internally contradictory input can be
laundered to PASS.

### 5. CONFIRMED — HIGH: Git pathname parsing can miss dirty source

`src/assay/git.py` parses `git status --porcelain` output by
newline-delimited string slicing; `src/assay/attestation.py` similarly
parses non-NUL `git diff --name-only` output. Git quotes unusual filenames
in both of these display modes, and filenames may themselves legally
contain characters (including newlines) that break line-oriented parsing.
Rename parsing also relies on matching the display string `" -> "`.

Concrete failure: a modified tracked source file whose name contains
spaces, tabs, newlines, quotes, or other quoted bytes can be misread as a
different path — `check_dirty_tree` can then fail to see it, allowing
judgment to proceed over an uncommitted file. The fix needs `-z`
(NUL-delimited) transport and explicit decoding/rejection, not more
display-format parsing.

### 6. CONFIRMED — HIGH: a verdict's commit does not bind the code that actually ran

`src/assay/cli.py` records `HEAD`, then executes the live working tree.
There is no pre-run whole-worktree cleanliness requirement — R1 checks
dirty files only beneath source roots, and only after the command has
already run.

Concrete failure: uncommitted edits to `assay.toml` (e.g. lowering
`fail_under`), a test, a command wrapper, or any support code outside the
narrow R1 dirty-check can affect the result without the recorded commit
changing at all. **This is live in the currently-shipped R0 CLI path
today**, not only a future R1+ concern — every `assay run` invocation
records `HEAD` and runs the live tree regardless of rigor level.

Recommendation: `assay run` should refuse a dirty worktree before external
adoption. Schema v3 should also bind the artifact to the effective judge
declaration and the resolved comparison base, not merely lane/argv/env/commit.

### 7. CONFIRMED — HIGH: attestation staleness mishandles reviewed directories

`src/assay/attestation.py` accepts a directory as a valid "reviewed path"
(a real Git tree entry), but its staleness check is exact string membership
against a flat changed-file set (`any(path in changed for path in
record.reviewed_paths)`), not real Git path semantics.

Sol reproduced this directly: an ancestor attestation declaring review of
`assay/src` (a directory) returned `PASS` even with descendant files inside
that directory genuinely modified since the attested commit.

Fix: compare each reviewed path using real Git path semantics (e.g. `git
diff --quiet <old> <new> -- <path>`), never flat membership in a changed-file
list.

### 8. CONFIRMED — HIGH before untrusted use: attestation loading permits traversal and resource exhaustion

`src/assay/attestation.py` forms `attestations_dir / f"{key}.json"` without
validating `key` at all. `../outside` escapes the declared directory.
Symlinks are followed. JSON size, reviewed-path count, individual path
length, and subprocess count are all unbounded. Untrusted
`attested_commit` strings are passed to Git's revision parser without first
requiring or canonicalizing a real commit object ID.

Concrete failures this permits: reading an attacker-selected file outside
the attestation directory; launching effectively unbounded Git subprocess
work from one attestation; feeding option-like or pathological
revision/path strings directly to Git; unbounded memory use parsing an
oversized file.

Must be repaired before assay ever accepts attestations from an untrusted
source. Lower urgency under the current single-tenant CI assumption, where
whoever can supply an attestation file already controls the environment.

### 9. CONFIRMED — MEDIUM: lane path and environment declarations admit silent ambiguity

`src/assay/config.py` rejects an absolute source root but accepts a
`../sibling` root or a symlink resolving outside the project, as long as
the resolved target is a directory. `src/assay/runner.py` lets a name
declared in `env_passthrough` silently overwrite a same-named, explicitly
declared fixed `env` value.

Concrete failures: a lane can measure a sibling project despite the
diagnostic claiming containment; a declared *fixed* environment value can
still change with the ambient environment. **The env-passthrough override
is live in the currently-shipped R0 path today** — every `assay run`
resolves environment this way regardless of rigor. Both are cheap
loader-level rejections to add.

### 10. CONFIRMED — HIGH before adoption: shipped wheels report a placeholder version

`pyproject.toml` depends on `setuptools_scm`, but the real self-hosting
build (P14) deliberately runs where that plugin is absent from every
interpreter in the gate image, so every wheel built there records
`assay_version: "0.0.0"`. That was an acceptable, deliberate, documented
choice for P13/P14's own installation proof (A-069/A-124) — it is not
acceptable once other projects actually consume assay's artifacts, since
two functionally different builds could both claim the same "version."

Recommendation: real consumers should depend on a tagged, hash-pinned
wheel, never a monorepo-relative `PYTHONPATH` import. The self-hosting gate
itself should install `setuptools_scm==10.0.5` into its own build closure
so its own proof exercises the declared build backend for real.

### 11. CONFIRMED integration hazards (not current library-contract violations — real risks for whichever package wires each rigor level in)

- R1 currently reads whatever coverage artifact already exists on disk. A
  command that exits successfully but fails to rewrite that artifact could
  receive judgment from stale output.
- `run_mutation` performs its own internal baseline run. A naive CLI
  integration would therefore run the lane's command twice (once for R0,
  again inside R2) unless the R0 result is deliberately reused.
- `run_python_canary` edits and commits into the repository it is given.
  This is a correct, documented assumption for a disposable fixture — but a
  real CLI integration must hand it an isolated copy, never a consumer's
  live worktree.
- `Verdict.ended` currently comes from the R0 command result alone. If
  R2/R3 execution happens after R0, a naive integration would record an
  `ended` timestamp earlier than the judgment it's supposed to bound.
- `judge.mutation.jobs <= 0` is not validated at the config layer and would
  reach an executor construction error instead of a clean `BAD_LANE_CONFIG`.
- `enforcement = "advisory"` is parsed and displayed but has no runtime
  consumer anywhere. Recommendation: keep it as declared policy, serialize
  it into schema v3, and leave the actual blocking decision to the
  environment/CI tool — do not have assay itself change its own exit-code
  semantics based on it.

### 12. SUSPECTED — MEDIUM: Go coverprofile block expansion may overattribute lines

The Go coverage parser expands every profile block from its start line
through its end line without consulting the start/end *column* data Go's
own coverprofile format carries. This may misclassify a closing brace or
other non-statement portion of a multi-line block as an executable changed
line. The existing srdm implementation behaves the same way, so this is not
being changed on inspection alone — it needs comparison against genuine
`go test -coverprofile` output, which the carved Go package (P22) is
scoped to do before any ruling.

---

## What sol would fix first

Before dispatching any new capability or estate-adoption package:

1. **P15 — repository and measurement input integrity.** Fix the patch
   parser, NUL-safe Git path transport, coverage-bucket overlap, normalized
   coverage-key collisions, source-root containment, and the
   env/env_passthrough collision. These all share one claim: every
   measurement identity is represented exactly once before judgment.
2. **P16 — independently checkable schema v3.** Record the effective judge
   policy and comparison inputs in the artifact; make `assay verify`
   actually rederive R1/R2/R3 status from payload + policy, not just check
   schema shape and rollup; add complete contradictory-artifact negatives.
   Preserve the sibling `declared_evidence[]`/`evidence[]` array shape
   unchanged.

Then, in order: **P17** (Python R1 CLI, end to end), **P18** (Python R2 CLI
— closes both of P12's already-known deliberate gaps), **P19** (isolated
Python R3 CLI), **P20** (bounded/hardened attestation declaration and
loading), **P21** (a real, versioned wheel release contract).

Two of the three "permanent" unreachable reason-code pairs stop being
acceptable once R1 is a real CLI path: `FORMAT_MISMATCH` and
`UNREADABLE_ARTIFACT` must become complete R1 claims rather than
terminating with no artifact at all. Initial `HEAD` resolution failure may
legitimately remain a pre-artifact `GIT_FAILED` — there is no honest commit
identity to put in an artifact at that point.

Then Go (each depends on the corresponding Python CLI package landing
first): **P22** (genuine Go R1, via a derived `tester-unified-go` image —
sol notes this base image already exists with a real Go toolchain, so
decision A-O04's original framing was partly stale: the missing piece is a
derived gate image adding Python + the pinned assay wheel, not adding
Python to srdm's own product toolchain), **P23** (genuine Go R2, via a
small Go-toolchain helper binary — an explicit external tool built into the
Go gate image, never a hidden dependency in the wheel itself), **P24**
(genuine Go R3).

Then React: **P25** (real lcov/Istanbul parsing against genuinely
Vitest-generated output, never hand-fixtures pretending to be one), **P26**
(a TypeScript/TSX adapter built on the real TypeScript compiler API, never
a regex/text-only classifier). Sol flags a real, more fundamental blocker
here: `webapp-ui-react` currently has **no discovered `*.test.ts(x)` /
`*.spec.ts(x)` suite and no configured coverage provider** — a coverage
parser over an empty test suite would only institutionalize
`NO_MEASUREMENT`, not provide real rigor. **P27** is a `dstdns`-side
adoption package: shadow the old frontend gate against the new one on the
same diffs before ever deleting the old gate.

Full YAML frontmatter for all twelve assay packages (P15–P26) plus the
`dstdns` adoption package is what this review's follow-up carving pass is
materializing directly as real handoff files under
`nyxloom-trove/handoffs/` — this report is the reasoning behind them, not
the handoffs themselves.

---

## Direct answers to the controller's specific questions

**Should CLI wiring for R1–R3 be the first v1.1 capability, ahead of Go and
React work?**
Yes — but only after P15 and P16's correctness repairs land underneath it.
This was never a legitimate, deliberate v1/v2 library-vs-product boundary:
the design, decision A-031, and the lane schema itself all describe
*executable* declared rigor. The P14 successor brief's "library surfaces
for other consumers" framing records what actually happened during the
series, not what the design ever authorized.

**Should P12's two known wiring gaps close inside the same CLI-wiring
package as R1?**
No. P17 should establish one-command/one-diff/one-artifact orchestration
and real R1 only. P18 should separately own: constructing mutation targets
from the resolved diff, parsing and enforcing `jobs`/`operators`, reusing
the R0 result already obtained rather than re-running the baseline, and
scratch isolation through to the final R2 claim. That is one independently
attackable R2 claim, not miscellaneous wiring bolted onto R1 — combining
them would make it hard to tell whether a future defect belongs to change
measurement, coverage, mutation selection, or execution.

**Is `assay_version: "0.0.0"` survivable for real consumers?**
No. Real consumers need an exact released wheel, pinned by version and
SHA-256 hash, never a monorepo-relative `PYTHONPATH` import. The
self-hosting gate itself should install a real `setuptools_scm` into its
own build closure so its own proof exercises the declared build backend.
Tag `assay-v0.1.0` once the v1.1 contract is actually ready — tagging
itself remains a controller action after review, not something an
implementation package does on its own.

**Is the underlying design sound?**
Mostly. The six outcomes, the three evidence tiers, computed-claims-versus-
sibling-attested/adjudicated-evidence, the adapter/format-parser
separation, and the cause-sensitive canary model are all sound as designed.
Three design corrections are needed before wider adoption: (1) a verdict
must bind the *effective judge policy* and the *resolved comparison input*,
not merely lane/argv/environment/commit; (2) a library surface is not a
product capability until a supported producer path actually reaches it —
this whole review exists because that distinction was blurred; (3) an
"independent verifier" must rederive status from payload plus policy —
schema validity and producer-internal self-consistency alone are
insufficient, as finding #2 demonstrates concretely. This warrants schema
v3 before wider adoption; doing it now is cheaper than migrating every
future consumer later.

**Security posture, before assay is pointed at untrusted input or run
outside this trusted monorepo context?**
Validate evidence keys as a closed, safe identifier — never a path
fragment. Require regular files beneath the resolved attestation
directory; reject symlinks outright. Bound file bytes, JSON nesting depth,
reviewed-path count, and individual string length. Require reviewed paths
to be repo-relative, normalized, non-empty, NUL-free, and structurally
unable to escape their root. Resolve every externally-supplied commit
reference to a full commit OID before any comparison. Use `--`/
end-of-options boundaries on every Git path and revision argument passed
to a subprocess. Bound total Git subprocess work per attestation. Apply
comparable size limits to any other externally-supplied coverage/XML/JSON
artifact, not just attestations.

More importantly, independent of all of the above: `assay run` executes an
arbitrary, lane-declared command. An untrusted branch's `assay.toml` must
never run with secrets, host credentials, Docker control, or elevated
filesystem access, regardless of how well the attestation/evidence path
specifically is hardened. The gate container itself remains the actual
security boundary — no network access unless explicitly required, never
privileged mode, never the host Docker socket, minimal writable mounts.

**Should the swapped-roles arrangement (sol carves/reviews, controller
implements/merges) continue for v1.1?**
Yes — keep the independent carver/reviewer split. But change the cadence:
add an explicit capability-reachability ledger that tracks library, config,
CLI, installed-wheel, and real-consumer status *separately* for every
capability, so a gap like this one is visible without a dedicated review
pass to find it. Give every producer package its own installed-wheel
end-to-end test, not only unit-level coverage. Run a small integrated
consumer checkpoint after every two or three packages, not only at the very
end. Close each tranche with a fresh review of the *shipped code*, not only
of each package's own diff. Reuse a stable, package-neutral orientation
brief to reduce the large fixed per-turn cost this kind of review carries —
but never weaken the independent review itself to save that cost. The
process succeeded at what it was built to catch — fourteen packages,
independently reviewed, shipped with the specific defects each one's own
oracles were designed to prevent. It still allowed one estate-level
reachability hole straight through, because that hole was a property of
what no package did, which package-level diligence structurally cannot see.

---

## Estate adoption order

| Order | Project | Capability | Actual blocker |
|---|---|---|---|
| 0 | assay | Python R1 through the installed CLI | P15–P17 |
| 1 | topos | Shadow R1 | Needs a versioned wheel and equivalent base/path resolution behavior; otherwise the best available fidelity baseline |
| 2 | ciu | Shadow R1, then R3 | CLI R1/R3 reachability and an isolated canary mechanism |
| 3 | cmru | R1 | Needs a coverage-producing test command (`pytest-cov` or equivalent) first — currently has no changed-line gate at all |
| 4 | dstdns (Python) | R1 | Separate package-time judgment from post-merge verification; install a real wheel in its own runner |
| 5 | nyxloom | R1, then R2/R3 | The hardest Python parity target in the estate; mutation/canary behavior must match before deleting anything existing |
| 6 | srdm | Go R1, then R2/R3 | Needs a combined Python+Go gate runtime and P22–P24 — Python does not need to enter srdm's own shipped product toolchain |
| 7 | dstdns (React) | TypeScript R1 | Needs a real coverage provider and real frontend unit/control tests before P25–P26 are even meaningful |
| later | netcup-api-filter | Cobertura R1 | Needs a real Cobertura corpus and real multi-class/multi-file conformance testing |
| defer | mdt and other largely untested services | none yet | No meaningful test surface exists yet to judge |

Every migration should run the old gate and the pinned assay wheel in
shadow on the same commits/diffs before anything is deleted — parity and a
deliberate negative mutation both demonstrated first, in that order.

---

## Findings sol would not carry forward as debt

- **A-O14 (output-write `OSError`)**: leave it. Operator error; doesn't
  justify expanding the closed reason-code vocabulary now.
- **The three historical "permanently unreachable" reason-code pairs as one
  category**: revise only the R1-shaped two once P17 makes R1 a real
  producer terminal. Do not force an artifact into existence for the case
  where initial `HEAD` resolution itself never succeeded — there is no
  honest commit identity to record there.
- **P00/P01 handoff lint debt**: leave it historical, as already recorded.
- **Cobertura real-world sample testing**: defer until the netcup-api-filter
  adoption actually needs it.
- **The suspected Go column-semantics issue**: investigate mechanically
  inside P22 against real `go test` output; do not preemptively rewrite the
  parser on inspection alone.
- **`enforcement = "advisory"`**: do not make assay itself return success
  for an adverse verdict. Serialize the declared policy in schema v3 and
  let the calling environment apply it.

## Least-confidence areas (sol's own words)

- Not every one of the 1,529 tests was exhaustively inspected — the review
  covered producer paths, schema/verification, Git/diff, coverage models,
  adapters, mutation, canary, attestation, configuration, packaging, and a
  targeted sample of tests. A defect confined to an unreviewed
  error-message or serialization edge may remain.
- The Go block-column concern is suspected, not confirmed against live `go
  test` output.
- The exact TypeScript/Istanbul multiline reconciliation must be settled
  from real generated coverage profiles, not decided in advance — this is
  exactly why P25/P26 carry an `escalate_if` for it rather than a ruling.
- The estate migration *ordering* after topos/ciu depends on scheduling
  outside this review's own remit, but the technical prerequisites and the
  "shadow before replacement" discipline are firm regardless of order.
