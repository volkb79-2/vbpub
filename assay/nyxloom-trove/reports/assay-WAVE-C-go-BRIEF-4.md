# Wave C (Go) — continuation brief 4

**Cumulative delta since BRIEF-3 only.** BRIEF-1 is the seam map, BRIEF-2 and
BRIEF-3 are the earlier deltas — read all three, then this. Nothing here
re-copies any of them.

Written by **generation 3**, a fresh session seeded with BRIEF-1+2+3 plus the
controller's DA-3 ruling. You are generation 4 on the same terms.

---

## 1. BRIEF-3 §5's task list, item by item

| BRIEF-3 §5 item | state |
|---|---|
| 1. register `GoAdapter` at `{"R1"}` (A-394) | **DONE**, `367bbdf5` |
| 2. item 7 / F008-A5 — srdm covergate qualification | **NOT DONE.** Its central question is ANSWERED from covergate's source (§4); the RUN is owed |
| 3. item 3 / F008-A4 — fixture regeneration | **NOT DONE** |
| 4. item 5 / B047 item 3 — `go-cover` producer vocabulary | **DONE**, `367bbdf5`, A-398 |
| 5. item 6's remainder — `helpers[]` gate envelope | **NOT DONE**, unchanged from generation 2's PARTIAL |

**No acceptance box is ticked.** F008-A3/A4/A5 stay `absent`. REPORT §18 gives
the reason per box; the short version is that all three need a real Go lane
end to end, which §3 below makes buildable and nobody has yet built.

## 2. Registration's fallout was wider than BRIEF-3 predicted — read this before you touch the registry again

BRIEF-3 predicted three fallout sites and all three were real. The full suite
found **three more**, and the pattern is worth carrying forward: *a registry
entry is read by more things than grep for `GoAdapter` will show you, because
several of them derive from `_built_in_registry()` rather than naming a
language.* Grep for `_built_in_registry` too.

* `test_adapters_javascript_registration.py::test_the_built_in_registry_names_exactly_the_languages_this_build_reaches` — a literal dict, red by design. Updated.
* **`test_docs_examples_and_vocabulary.py`'s two mutation-operator tests — a real design correction, A-400.** The docs gate derived its required operator set from *every registered language* and promised in a comment to "expand BY ITSELF" once a Go adapter was registered. It did — **in the wrong direction**, demanding documentation for three `go:*` operators no lane can reach, which is exactly the outcome A-287 exists to prevent. Now scoped to languages registered **at R2**. Changes no set today.

**Two lessons that will cost you if you skip them.**

**(a) A-399 — registration can void a test's SUBJECT without turning it red.**
`test_run_refuses_an_unregistered_language_with_a_resolvable_infrastructure_fact`
used `language = "go"` only as a cheap way to reach `cli.py`'s adapter-refusal
call site. With `go` registered it takes `runner.py`'s
`MISSING_EXTERNAL_TOOL` preflight instead — a different call site — and would
have kept **passing** while no longer exercising the defect it was written
for. Fixed by changing the language to `sql` (R2-only, A-242) and touching no
assertion. When you land item 6 or item 3, ask the same question of every test
that mentions Go incidentally: *is this test still about what its name says?*

**(b) The vacuity guards are what caught A-400.** The companion test asserts
its excluded set is non-empty. Keep writing those.

## 3. The environment fact that unblocks items 2, 3 and 6 — MEASURED, and it changes the plan

BRIEF-1's committed probe script says this devcontainer's `/tmp` "is not
visible to the Docker daemon at the same path", which is why every Go probe so
far has been a tar-pipe. **True as written — and the obstacle is a path
TRANSLATION, not an absence.** Measured this session:

```text
$ docker inspect "$(cat /etc/hostname)" --format '{{range .Mounts}}{{.Destination}} <= {{.Source}}{{"\n"}}{{end}}'
/workspaces/vbpub <= /home/vb/volkb79-2/vbpub
/tmp              <= /home/vb/mdt--mounted-folders/tmp
```

So a bind mount CAN be built for either tree by translating the source side.
srdm's own `tools/gate.sh:48-61` already derives exactly this mapping the same
way (and refuses rather than hardcoding a home directory — copy that shape,
including the refusal).

**What this makes buildable: a transparent `go` shim.** It is the pattern
`vbpub/tester-unified-go/Dockerfile` blesses in its own header — *"`tools/go`
(a wrapper around this image) is how a cockpit gets Go ergonomics without a
cockpit Go"*. A shim is **not** the double A-334 forbids: the real `go1.25`
toolchain compiles and runs real code inside `tester-unified-go:local`, and
only the invocation is forwarded. A mocked `go version` is the forbidden
thing; this is its opposite. Do not let a reviewer conflate them, and say so
in the test's docstring.

Three seams make it reach assay's own machinery, all checked:

1. **`runner.default_scratch_root` is `tempfile.TemporaryDirectory`** — so
   `TMPDIR` places the lane's snapshot wherever you want it, including under a
   bind-mountable tree. This is the load-bearing one.
2. **`env_passthrough = ["PATH"]`** carries the shim into the lane command's
   own environment (`default_process_runner` REPLACES the child env, so a bare
   PATH inheritance is not available — the lane must declare it).
3. **`shutil.which("go")`** in A-253's preflight finds the shim and stops
   refusing, which is the gate on everything else.

**NOT yet proven — prove these before claiming anything from them:**

* that `go test -coverprofile` inside the shim writes its artifact where
  `safeio.reserve_output`'s reservation still holds. **B049 is exactly this
  failure mode** (a tool that deletes and recreates the directory assay has
  open reads as `EMPTY_COVERAGE` over a complete artifact), and it bit the
  real-vitest qualification. Assume nothing.
* that the image's uid (**1003**, per the Dockerfile's `RUN_UID`) can write
  into the snapshot directory the shim mounts.

Both are one probe each. Nothing in the REPORT depends on either.

## 4. covergate (item 2 / F008-A5) — the central question is answered; the RUN is owed

Full detail in REPORT §17 and backlog **B056**. The three facts you need:

**(a) covergate does the naive expansion.**
`shared-ramdisk-depot-manager/tools/covergate/profile.go`'s
`ParseCoverProfile` is literally `for l := start; l <= end; l++`, and
`Executable(line)` is `Executed[line] || Missing[line]`. **That is
byte-for-byte the rule assay just removed.** So the two disagree by
construction, and **assay is the correct side** — A-217's frozen
`collision-colA`/`colB` pair settles it without needing this run at all. This
is not a surprise to be discovered; A-217 predicted it in writing.

**(b) Do not report the disagreement as one number.** Classify each
difference first:
  * *extent-expansion* — expected, assay right;
  * *file-absence* — covergate's `Evaluate` `fc == nil` branch splits a
    changed file absent from the profile into `NoCode` (excluded) vs
    `Unmeasured` (counted uncovered), separated only by `HasExecutableCode`.
    **This is where the P14 "silently skipped a package" caveat project
    memory records actually lives.** It is a different question entirely.
  Averaging the two produces a conclusion about neither.

**(c) How covergate is actually invoked** (`tools/gate.sh:105-113`, verbatim
shape):
```sh
go test ./... -count=1 -coverpkg=./... -covermode=atomic -coverprofile=/tmp/srdm-cover.out
go run ./tools/covergate -profile /tmp/srdm-cover.out -base main -source internal -fail-under 75
```
Exit codes are 0 pass / 1 fail / 2 tool error / **3 no-measurement**, and it
refuses on a dirty tree or a base that resolves to HEAD. `-module srdm`
strips the import-path prefix from profile keys. Note `-coverpkg=./...` is
load-bearing and is there precisely to stop packages vanishing.

**"The same commits"** — the natural candidate is the frozen two-commit
fixture `carve-assets/P27/fixture/commit{1,2}/calc.go`, which already has an
independent hand manifest (`manifest/calc-statements.json`, authored from
source bytes before any profile existed). That manifest is the neutral third
party: it lets you say which side is right without either tool being the
judge of the other. Use it.

## 5. Your task list, in order

1. **Prove the `go` shim** (§3's two unproven seams). One probe each. Do this
   FIRST — items 2, 3 and 6 all stand on it, and if it does not hold you need
   to know before building on it, not after.
2. **Item 6's remainder** — the `helpers[]` gate envelope. Wire
   `run_lane` → `Verdict.helpers` (the `on_helper_invoked` callback already
   delivers `HelperInvocation` exactly once per lane; nothing carries it
   onward), then the DA-3-resolved qualification test:
   `tests/qualification/test_go_*_real.py`, `pytestmark = skipif`, env var
   `ASSAY_GO_QUALIFICATION=1`, modelled on
   `tests/qualification/test_javascript_real_vitest.py` — **read that file,
   its shape is the ruling**. Assert `helpers` IS present, `role=
   "statement-positions"`, identity naming `go version …`. Do NOT weaken
   `test_cli_run.py:406`'s `"helpers" not in document` (A-395). Caution:
   `Verdict._check_helpers` requires a correspondingly-judged claim per role,
   so a half-wired `helpers[]` is a schema-valid document that lies — land it
   whole or not at all.
3. **Item 3 / F008-A4** — fixture regeneration. Discharges half of B055 (read
   B055 first; the honest fix for its shortcut (1) IS this item, and its third
   acceptance box — a test that goes RED if the shipped adapter's declaration
   is ever flipped — is cheap and still unticked).
4. **Item 2 / F008-A5** — the covergate qualification, per §4.
5. **Only then** the acceptance boxes in `2-product-definition.md`, F008-A3/A4/A5,
   matching F008-A1/A2's existing `evidence:` file:line style. Not before you
   can back each one concretely.

## 6. Ledger

Decisions this generation: **A-398** (go-cover vocabulary), **A-399** (the
voided test subject), **A-400** (R2-scoped docs gate). **Next free: A-401.**
Backlog: **B056** (covergate's extent expansion). **Next free: B057.**

## 7. Gate

**Run 5, on `367bbdf5`** — verdict recorded in REPORT §22 and in the LOG,
read from the log's own `GATE_EXIT` / `ASSAY_REGISTERED_GATE_COMPLETE=1` in a
separate step, as always. Do not start work on a tip whose gate you have not
read yourself.

**The wrapper-vs-job trap fired a FOURTH time this wave** (REPORT §21), and
this instance is the most persuasive yet: the harness's own *structured
completion notification* said "exit code 0" while the log's appended marker
said `PYTEST_EXIT=1` with `3 failed`. Believing it would have shipped the
A-400 defect with a passing suite cited as evidence it was fine. A structured
notification is not the job's status. Append your own marker; read it
separately; every time.

---

## SELF-COMPACTION PROMPT

**KEEP:** BRIEF-1 in full (the seam map); BRIEF-2 and BRIEF-3 in full; this
brief in full; the wave prompt's "Wave C" section; BRIEF-1's rules block
(A-334, A-335, A-042/A-043, A-097/A-101, decisions.md append-only from
**A-401**, backlog from **B057**, `git commit -F <file> --only -- <paths>`,
the trailer, no `!` marker); §5 above as the literal next task list; the gate
command and the separate-verdict-read discipline; `tests/qualification/
test_javascript_real_vitest.py` as the pattern DA-3 rules for the Go
qualification test.

**DROP:** the registration fallout hunt (closed — §2 lists every site and all
are landed); the argument for why `go-cover`'s producer key is optional
(closed — A-398 carries it whole, including the rejected alternative); how
covergate's `Evaluate`/`hascode` internals work line by line (closed — §4 and
B056 carry every consequence; do not re-read the Go source); the derivation of
the host-path mapping (closed — §3 has the measured values and the command
that produced them).
