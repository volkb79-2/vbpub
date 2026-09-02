# Wave C (Go) — continuation brief 5

**Cumulative delta since BRIEF-4 only.** BRIEF-1 is the seam map; BRIEF-2, -3
and -4 are the earlier deltas; read all four, then this. Nothing here re-copies
any of them.

Written by **generation 4**, a fresh session seeded with BRIEF-1..4 plus the
controller's 2026-09-01 entry (`vbpub@237b9585`, rulings DA-4..DA-7). You are
generation 5 on the same terms.

---

## 1. BRIEF-4 §5's task list, as amended by the controller, item by item

| task | state |
|---|---|
| 0. record A-401/A-402, edit F008-A5's text | **DONE**, `2f0cd223` |
| 1. the in-image harness (build the pyz, mount, run one real R1 Go lane) | **DONE as a harness.** The lane runs; the CLI form is BLOCKED — §2 |
| 2. `helpers[]` wiring + the DA-3 qualification test | **DONE**, `77d9d6b9` + `9714361c` |
| 3. F008-A4 fixture regeneration | **NOT DONE — blocked behind B059** |
| 4. F008-A5 per DA-6 | **NOT DONE — blocked behind B059** |
| 5. the acceptance boxes | **none ticked**, deliberately — §4 |

Two things landed that were not on the list, both found by running the
harness rather than by reading:

* **A-403** (`8d7f8740`) — the shipped zipapp could not reach the Go oracle at
  all. FIXED.
* **B059 / DA-8** (`854d20c3`) — no Go lane reachable through the shipped CLI
  can resolve its own coverage keys. MEASURED, FILED, **not** fixed: it is a
  product/design fork.

## 2. The blocker you inherit — read B059 before you plan anything

`4-backlog.md`'s **B059** and REPORT §26 carry the measurement; REPORT §27 is
the decision ask. The one-paragraph version, so you can decide whether to wait
for a ruling before touching items 3 and 4:

A Go cover profile keys records by IMPORT PATH
(`example.invalid/harness/internal/calc/calc.go`); `git diff` names the same
file `internal/calc/calc.go`. `GoAdapter.module_path` strips the difference,
and **nothing sets it through the CLI** — `cli._built_in_registry` builds
`GoAdapter()` with the `""` default, `config._KNOWN_JUDGE_FIELDS` has no key,
`assay run` has no flag. So `assay run` on any real Go module refuses
`ERROR`/`UNREADABLE_ARTIFACT`, with a message about the profile and the tree
not being the same revision — which names the wrong cause.

**No fixture layout dodges it** (an import path is the module path plus the
directory, so the key equals the repo-relative path only for an empty module
path, which `go.mod` forbids), and **DA-6's prescribed srdm lane hits it**:
`srdm/internal/...` would resolve to
`shared-ramdisk-depot-manager/srdm/internal/...`, under no source root.

**Do not improvise the fix.** Three shapes are defensible; §4.2a's
DERIVE-then-READ preference and A-007's own reasoning select derivation from
`go.mod`, while the most recent precedent (A-328's optional `judge.base_source`
key, added with no lane-schema bump) selects a declared key. That is a genuine
fork, and REPORT §27 lays out all three with their costs. If the controller
rules it, record the ruling as a decision row and cite it.

**The control half is the good news and you should not re-derive it.** With
`module_path` supplied, everything downstream works: the real `go1.25.14`
oracle runs and returns statement lines `{6}` and `{11}` for a fixture whose
naive expansion would be `{5,6,7}`/`{10,11,12}`. Measured, in-image, and the
transcript is in B059.

## 3. Load-bearing facts a successor must not re-derive

**The in-image harness works, and here is its shape** (REPORT §25 has the full
transcript). Build the zipapp with
`python3 assay/gate/distribution/build_release.py --repo <worktree> --outdir
<dir>` — it builds from HEAD's **committed OID**, so it measures the committed
tree and a test using it can only pass on a clean commit. Derive the host bind
source with `docker inspect "$HOSTNAME"` (`/workspaces/vbpub` AND `/tmp` are
both bind-mounted, so a `/tmp` scratch tree can be handed to a sibling
container by translating the source side). Run with `--network=none` and
`--cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND"`. **Create the fixture git
repository INSIDE the container** — the image runs as uid 1003 (`gate`) and
the cockpit is 1000, so building it there makes git's files owned by the uid
that reads them and no `safe.directory` override is needed anywhere.

**`build_release.py` writes a `zipapp-staging/` directory next to `--outdir`
and never removes it.** With `--outdir assay/dist` that lands at
`assay/zipapp-staging`, which is NOT gitignored and will make the self-hosted
gate lane refuse `DIRTY_TREE`. Build into a directory outside the worktree.
(Not filed: it is a one-line property of a builder this wave did not otherwise
touch, and the workaround is to pass a different `--outdir`. File it if you
disagree.)

**A-403's lesson generalises and is worth carrying.** The module had reasoned
to the wrong design from two TRUE premises ("`go run .` needs a real
directory"; "a wheel install materialises package data as real files") — a
zipapp is not a wheel install, and no test ran the zipapp against the Go path.
When you add anything that reads packaged data, ask which install shapes the
suite actually exercises. `verdict.schema_text`'s
`files(__package__).joinpath(...)` is the pattern.

**The Go image's facts, re-verified this generation** (do not re-run unless
you change the image): `/usr/bin/python3` = Python 3.13.5, `go version
go1.25.14 linux/amd64`, uid 1003 / user `gate`, Debian trixie, no pip, no
ensurepip. `tester-unified-go:local` is 1.01 GB; `tester-unified:local` is
8.94 GB.

**DA-5's `tester-unified-go/Dockerfile` half is DONE** (`21708344`, its own
`docs(tester-unified-go):` commit, comment-only, verified with a real
`docker build` of the edited file). Both halves it asked for are in place: the
header paragraph recording the inherited interpreter and citing A-402, and
the assertion that can actually fail —
`test_go_r1_real.py::test_the_go_gate_image_still_carries_the_interpreter_the_judge_needs`,
which asserts `python3 --version >= 3.11` INSIDE the image, so a base-image
change that drops the judge's interpreter goes red instead of turning every Go
qualification into a skip that reads like "not enabled". **No `apt-get` layer
was added** and none should be: the package is already there, and installing
it would make the image's Python a different interpreter from the one a
consumer inherits.

## 4. Why no acceptance box is ticked, and which one is closest

**F008-A3 is one ruling away.** REPORT §29 is a real, end-to-end,
statement-granular Go R1 verdict from the shipped zipapp against the real
toolchain, with `executable: 1` where the removed rule would have said 3 — the
exact evidence A3 asks for. It was produced through the LIBRARY entry point,
because the CLI one refuses (§2). Ticking A3 while `assay run` cannot judge a
Go module would record a capability this build does not have at the boundary a
consumer uses. When DA-8 lands, re-run the qualification through
`python3 <pyz> run …` — the module's assertions are about the verdict
document, not the entry point, and should survive the move unchanged — and
then tick it with that transcript.

**F008-A4 and F008-A5 are blocked outright** behind B059. REPORT §31 says why
regenerating fixture bytes alone would be worse than not doing it (A-234's own
warning).

## 5. Your task list, in order

1. **Get DA-8 ruled**, or rule it if you are the controller. Everything below
   is downstream of it, and nothing else in the wave's remaining scope can
   start without it.
2. **Item 3 / F008-A4** — fixture regeneration, runnable through the §3
   harness once DA-8 lands. Discharges B057's first box.
3. **Item 2 / F008-A5** — the covergate qualification per DA-6, with the
   classification (extent-expansion vs file-absence) done BEFORE either side
   is called wrong.
4. **F008-A3's tick**: re-run `tests/qualification/test_go_r1_real.py` through
   `python3 <pyz> run …` once the CLI form works, and tick A3 with that
   transcript. The module's assertions are about the verdict document, so the
   move should change no assertion — if it does, that is a finding.
5. **The other acceptance boxes**, each citing something you ran.

## 6. Ledger

Decisions this generation: **A-401** (F008-A5 reworded, DA-4), **A-402** (the
in-image consumer harness, DA-5), **A-403** (the oracle is staged out of the
artifact). **Next free: A-404.**
Backlog: **B059** (the CLI Go lane cannot resolve its own coverage keys).
**Next free: B060.**
Decision asks open: **DA-8** (REPORT §27).
Backlog boxes ticked: B047 items 1/2/3/5; B057's third.

## 7. Gate

**Run 7: PASS on `9714361c`**, the tip you inherit — 11 phases,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, and the installed wheel
`assay-4.0.1.dev29+g9714361c` names the judged commit itself. A separate grep
for `FAILED`/`ERROR`/`DIRTY` returned nothing. Devcontainer full suite on the
same tree: `3860 passed, 16 skipped`, `PYTEST_EXIT=0` (the three new skips are
the Go qualification module — correct: no Go here or in `tester-unified`). Go
qualification, run separately with `ASSAY_GO_QUALIFICATION=1`: `3 passed`,
`PYTEST_EXIT=0`. Full transcript in REPORT §30.

**Two commits follow run 7, and neither touches anything the gate judges:**
`21708344`, a comment-only header in `tester-unified-go/Dockerfile` (outside
`assay/` entirely, verified with a real `docker build`), and the docs commit
carrying this brief plus the LOG/REPORT sections. No source, test, packaging,
vocabulary or decision-file changes after `9714361c`.

**Commit before you gate, and do not edit the worktree while it runs.**
Generation 3 lost a gate run to an untracked brief file; the self-hosted lane
refuses `DIRTY_TREE` and is right to. This generation wrote nothing into the
worktree between starting run 7 and reading its marker.

**The wrapper-vs-job trap did NOT fire this generation** — both background
jobs' completion notifications agreed with their own appended markers. Six
instances across three generations and two agreeing ones do not make the rule
optional; they make it cheap. The markers were read separately anyway, which
is the only reason this sentence can say which was true.

---

## SELF-COMPACTION PROMPT

**KEEP:** BRIEF-1 in full (the seam map); BRIEF-2, BRIEF-3 and BRIEF-4 in
full; this brief in full; the wave prompt's "Wave C" section; the controller's
2026-09-01 entry (DA-4..DA-7, `vbpub@237b9585`); BRIEF-1's rules block (A-334,
A-335, A-042/A-043, A-097/A-101, decisions.md append-only from **A-404**,
backlog from **B060**, `git commit -F <file> --only -- <paths>`, the trailer,
no `!` marker); §2 (B059/DA-8) and §3 (the harness recipe) as the two things
every remaining item stands on; §5 as the literal task list; the gate command
and the separate-verdict-read discipline;
`tests/qualification/test_go_r1_real.py` as the working example of an
in-image, opt-in qualification.

**DROP:** how the zipapp failed to reach the oracle (closed — A-403 and the
regression test carry it whole; do not re-investigate `HELPER_DIR`); the
`helpers[]` wiring's three sub-decisions (closed — REPORT §28 and
`test_runner_helpers_envelope.py` carry each with its rejected alternative);
the re-verification of the Go image's python3/go/uid facts (closed — §3 has
the measured values, and the qualification test now asserts them on every
run); the argument for why the qualification drives the library entry point
(closed — §2 is the whole reason, and it ends when DA-8 is ruled).
