# Wave 3 record — and the standing successor brief for assay

**Read this first.** It supersedes `W1-RESUME.md` and `W2-RESUME.md` as the
entry point. Those remain accurate for their own waves; this one carries the
things that are **not recoverable by reading the repository**.

**State at 2026-08-18:** `assay-v2.1.0` released, tagged, published and
verified. B001/P34 (the SQL/DDL adapter) shipped in full, W0–W9. Registered
gate green three times: branch `7d4ad61d`, merge `ccf9ca55`, and the final
merge `9548b91a`. 3186 passed, 11 skipped.

---

## 1. How work is done here (none of this is in the code)

The loop that produced waves 1–3, in order. Skipping a step has cost real
defects each time it was skipped.

1. **Carve** on a strong model, in a feature worktree, against a written brief.
   The brief must list what is *stale* in the inputs and must forbid Monitors,
   background waits and git writes.
2. **Verify the carve's premises by measurement** before anyone implements.
   Especially: anything it asserts about another tool's output, and anything it
   asserts about assay's own shipped surface. See §4 — this is where carves
   are wrong.
3. **Adversarial review** on a different model, seeded with what the controller
   already measured so it attacks new ground. Ask it to confirm/refute/extend
   each seeded finding.
4. **Rule the corrections** as `A-nnn` rows in `decisions.md` *before*
   implementing. The implementer reads rulings, not review prose.
5. **Implement via one serialized Sonnet agent per package.** One at a time,
   never parallel — they share the worktree.
6. **Verify by driving the shipped entry points**, never by reading the diff.
   Run the CLI, build the artifact, hash it.
7. **Gate detached**, then merge, then **gate again against a detached worktree
   pinned at the merge commit** — never the live checkout.
8. **Release through cmru** (§5), then notify dstdns (§6).

**Models:** carve and review on Opus (codex was 401 Unauthorized; Fable was
used for wave 3's review). Implement on Sonnet. Every brief must repeat the
rules of engagement verbatim — agents drop them otherwise.

## 2. Repository constraints that will bite you

* **`/workspaces/vbpub` has a concurrent committer.** Use
  `git commit -F - --only -- <paths>`; flags **before** `--`; a new file needs
  `git add -N` first; `-F` with an absolute path outside the repo fails, so
  pipe the message via stdin. Never add+commit, reset, rebase, `--amend`,
  checkout, or stash.
* **Commit messages via a file**, not a shell heredoc with apostrophes — that
  broke a commit in this wave.
* **Run pytest from `assay/`, never the worktree root.** From the root it
  collects the wrong tree and every import fails with 71 collection errors.
  This cost a false alarm here.
* **Never `cmd | tail` (or `| head`) then read `$?`** — that is the pager's
  status. This produced a *wrong conclusion* about `ciu provenance`'s exit code
  in wave 2.
* **A background task's reported exit code is the wrapper's, not the job's.**
  Always `echo "GATE_EXIT=$?" >> log` and grep the log. A gate that FAILED was
  once reported as "exit code 0" this way.
* **Never write `--verdict-json` into the repository under test** — it dirties
  the tree and the run refuses `NO_MEASUREMENT`/`DIRTY_TREE` before any mutant.
* **Frozen carve assets are evidence.** Capture new ones; never edit them.

## 3. The registered gate

`assay/tools/tester-unified-gate.sh <worktree>`. Run it detached. Ten phases,
ending `ASSAY_REGISTERED_GATE_COMPLETE=1`; anything less is a failure however
green the console looks. It rebuilds the wheel from a private sparse clone at
the exact OID and self-hosts through it, so it is slow (~5–10 min) and it is
the only authority.

**It runs the test suite WITHOUT a docker socket.** Any committed test that
needs a daemon turns it red. W9's database tests are therefore
environment-gated with stated skip reasons. To check this cheaply, run the
suite with a `docker` shim that fails, on an otherwise full `PATH` — and keep
a must-succeed control in the same probe. A stripped `PATH` produces false
failures (it did here: `/usr/bin/false` was missing and two tests "failed").

## 4. What repeatedly goes wrong — the patterns worth carrying forward

These are the durable lessons. Each cost a wave.

* **A-279 — an oracle set that omits the feature's headline outcome is
  incomplete no matter how many failure modes it covers.** The P34 carve
  measured everything on live PostgreSQL and still shipped a canonical consumer
  command under which `killed` was *unreachable*, because not one of its
  acceptance oracles ever produced a kill. Evidence frozen at
  `carve-assets/W3/`.
* **A-284 — carves are wrong about assay's own internals, not about the
  unfamiliar thing.** Four things the P34 carve named do not exist in the tree
  (`_check_coverage_artifact_containment`, `get_adapter` being called in
  `run_lane`, a reusable path validator, a test file). Every one was in a
  section about assay; its claims about SQL, PostgreSQL and the real dstdns
  corpus survived adversarial attack intact. **Grep a carve's claims about the
  shipped tree before briefing an implementer.**
* **A-278 — a check with nothing to check is not a passing check.** Hit three
  times: an empty tag list, an empty `external_tools` tuple, an empty derived
  vocabulary. Always assert the subject is non-empty.
* **A-289 / A-272 — claims get generalised one step past their evidence.**
  §9 M6's "empty name delta" held only for explicitly-named constraints.
  Found only because W9 applies mutants to a real catalog instead of reasoning
  about one.
* **The stale-index pattern.** Bodies get updated; the index does not. Found
  this wave in `CHANGES.md`'s `[Unreleased]`, and in B001, B002, B003 and
  B004's frontmatter rows. When you update a body, grep for its other homes.
* **A-285 — the 100%-branch rule does the reviewing.** The replacement
  invariant read literally made a branch dead; the implementer found it only
  because a dead branch cannot ship quietly here.

## 5. Releasing (this is where the last release went wrong)

`./cmru.release.sh --project assay`. cmru owns
**snapshot → gate → tag → build → publish** and generates the dated
`CHANGES.md` entry.

* **NEVER hand-tag a cmru-managed project.** A manual tag is indistinguishable
  from a completed release. A hand-made local `assay-v2.1.0` on HEAD made cmru
  report "no changed projects" — because `_latest_tag_for_prefix` reads
  `git tag --list`, which includes unpushed local refs. Filed as **cmru KI-12**.
* **Clear `[Unreleased]` in `CHANGES.md` immediately after a release.** cmru
  generates the dated entry but does *not* clear the hand-written block that
  fed it, so leaving it republishes shipped work as unreleased. This recurred
  on the 2.1.0 release itself.
* cmru derives the version from conventional commits (`feat:` → minor). Use
  `--dry-run` first; it takes seconds.
* Five findings from the 2.1.0 run are filed as **cmru KI-12…KI-16**.

## 6. Consumers, and what is owed to them

* **dstdns** — notify by writing `/workspaces/dstdns/.assay-inbox/release.json`
  (contract in that directory; gitignored, no commit needed). `sha256` of the
  **`.pyz`** is required; `landed` decides which of their stopgaps retire. They
  vendor the zipapp, not the wheel.
* **A real defect was found in dstdns and reported in the 2.1.0 notify:**
  `test_corpora_table_in_migration` still returns `True` after
  `THIS IS NOT SQL AT ALL;` is injected into `20-create-corpora.sql`, and two
  sibling tests assert strings the file no longer contains. That lane is either
  red or not running.
* **cmru vendors assay 1.0.0** while 2.1.0 ships, and **dstdns vendors 2.0.0**.
  Nothing tells anyone. cmru's `depends_on` graph cannot express this: it says
  `assay depends_on cmru`, while cmru's *tests* consume assay — declaring both
  would be a cycle, which is why it is resolved by vendoring a pinned artifact.
  Being addressed as a cmru tool-deps feature (schema + spec + tests + docs).

## 7. What is next

* **B007** — ordered, bounded, multi-target R3 canary. The natural next wave:
  it is the first post-v6 schema item, and it now has a passenger, so **v7 pays
  one consumer migration for two features**.
* **B004** — provenance as VERIFIED evidence. **Do not re-carve it**; the
  958-line carve and its adversarial review are both merged and the review
  already killed four zero-schema escapes. It unblocks only when *both*
  `PROVENANCE_UNVERIFIED` ships (reserved by name, A-276) **and** ciu fixes
  **CIU-28** — `ciu provenance` compares vendor images ciu never built, so
  `verified-match` is unreachable on any live host.
* **B002/B003** are COMPLETE as of 2.1.0; what they are still owed is upstream
  in cmru KI-12…KI-16.
* **Housekeeping:** ~10 retained `cmru-release-*` worktrees here (and far more
  estate-wide) that `--abandon all-previous` cannot drain. cmru KI-16 proposes
  a timestamped name that finally makes a retention policy expressible.

## 8. Where the evidence lives

* `W3-CARVE-P34-sql-adapter.md` — the 1388-line carve, with §9's measurement log.
* `reports/assay-P34-carve-review-fable.md` — the adversarial review, five
  blocking findings, none refuted.
* `carve-assets/W3/` — the A-279 ordering pair with hashes and a MANIFEST
  stating what it does **not** prove, plus W9's witnessed verdict.
* `decisions.md` A-279…A-289 — this wave's rulings. Read these before
  designing anything; they override the carve wherever they disagree.
