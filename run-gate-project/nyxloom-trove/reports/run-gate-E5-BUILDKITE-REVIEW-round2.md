# E-5 Buildkite seams 2 and 4 — adversarial review, round 2

- **Branch:** `feature/run-gate-buildkite-seams`, tip `ef8396df` (4 commits since round 1's `31efccca`)
- **Worktree:** `/workspaces/vbpub/.worktrees/run-gate-buildkite-seams`
- **Reviewer:** same session as round 1, 2026-09-03
- **Yardstick:** rulings **E5-R7..E5-R16** in `run-gate-E5-BUILDKITE-CONTROLLER-LOG.md` (on `main`),
  plus everything round 1 established.

## VERDICT: ACCEPT

**0 BLOCKER, 1 SHOULD-FIX, 7 NIT.**

Every round-1 finding is genuinely fixed, and I re-ran each demonstration rather
than reading the diff for it. The suite is no longer decorative: a 12-mutation
sweep against a scratch copy of both scripts killed **12 of 12** — the exact
opposite of round 1's N1, where the whole live path was untested. The remaining
SHOULD-FIX is three lines and one test; I would fold it into the merge commit
rather than spend a third round on it, and I say so with the finding.

---

## 1. Round-1 findings — re-tested, not re-read

| round 1 | ruling | status | the demonstration, re-run at `ef8396df` |
|---|---|---|---|
| **B1** `collect` writes outside `<dir>` via `commit` | E5-R7 | **FIXED** | `check_path_component` (bk-lane.sh:239-245) gates it before `mkdir`. `commit="../../../escape"` → **exit 2**, message names the value, nothing written outside; `..`, `.`, `a/b`, `refs/heads/main` and empty all refused; `HEAD` accepted as a plain directory name |
| **S1** `run --dry-run lint` creates a real build | E5-R9 | **FIXED** | `--dry-run` honoured in all three positions (before the verb, after it, last) → exit 0 and **zero** curl calls recorded by the stub; `run -lint` → exit 2 as an unknown option; `run -- -lint` → exit 2 from the lane guard naming `starts with '-'` |
| **S2** §6 row 1 "landed" + "matching" globs | E5-R8/R15 | **FIXED** | row 1 now reads "**docs seam, not started** (E5-R5)" and states the globs are FIXED, that no lane declares the set, and that `run-gate-project/selftest`'s `coverage.json` matches none of them. The authoring rule landed on `main` in `LANE-AUTHORING.md` §5 (see N4) |
| **S3** unbounded polling | E5-R10 | **FIXED** | `BK_MAX_WAIT_MINUTES=1 BK_POLL_SECONDS=30` on a build parked in `scheduled` → **exit 3** after POST + three polls, message names build and last state and hands over `bk-lane.sh status 99` |
| **S4** stored config pipes into upload, no queue | E5-R11 | **FIXED** | §3:140-144 is now `command: \|` with two statements writing `pipeline.yml` then uploading it, and an `agents: queue:` on the upload step. Both halves of the reasoning check out against the vendor (§5 below) |
| **S5** refusals exit 1, not 2 | E5-R12 | **FIXED for refusals** | short response → exit 2; malformed listing → exit 2; escaping artifact path → exit 2; escaping commit → exit 2. But the contract's stronger claim, "on **every** path", is still false on two failure paths — SF1 below |
| N1 hollow / live path untested | E5-R7 | **FIXED** | see the mutation sweep, §4 |
| N2 `"jq "` substring | — | **FIXED** | now `re.search(r"\bjq\b", …)` on both scripts (`:642-650`) |
| N3 `"failed"` satisfied by `waiting_failed` | — | **FIXED** | replaced by a parametrised live-path test asserting the actual exit code per state |
| N4 leading-zero timeout → YAML octal | — | **FIXED** | `RUN_GATE_TIMEOUT_MINUTES=0300` → exit 2 naming the octal trap; same guard added to `BK_POLL_SECONDS`/`BK_MAX_WAIT_MINUTES` |
| N5 `RUN_GATE_LANES` globs the CWD | — | **FIXED** | `set -f` (pipeline.sh:163); `RUN_GATE_LANES='*'` now refuses the literal `*` instead of expanding |
| N6 duplicate lanes → duplicate steps | — | **FIXED** | refused in *both* tools: generator exit 2, and `bk-lane.sh run lint lint` → exit 2 "named twice" |
| N7 "verbatim from the generator" | — | **FIXED** | §3:208-210 now says "with `<project>` standing in for the `PROJECT_DIR` argument" |
| N8 `.assay/**/*` may miss the progress file | — | **FIXED** | third glob `<project>/.assay/*` emitted first; §4.3 softened to "to be verified on the first live build" |
| N9 extra args ignored | E5-R13 | **FIXED** | `status`/`collect` refuse them by name; `run` deliberately not (R13, reasoned) |

---

## 2. Scope discipline — PASS

```
$ git -C … diff --name-only main...HEAD
run-gate-project/REMOTE-LANES-BUILDKITE.md
run-gate-project/nyxloom-trove/reports/run-gate-E5-BUILDKITE-REVIEW-round1.md
run-gate-project/tests/test_buildkite_tools.py
run-gate-project/tools/buildkite/bk-lane.sh
run-gate-project/tools/buildkite/pipeline.sh

$ git -C … diff --stat main...HEAD -- run-gate.py tests/test_run_gate.py SPEC.md \
      README.md CONSUMERS.md CHANGES.md run-gate.toml LANE-AUTHORING.md
(empty)
$ git -C … status --porcelain
(empty)
```

The four product files plus my round-1 report. `run-gate.py` still has no changed
line, so the selftest lane's `--source run-gate.py` diff-coverage floor has nothing
new to judge — no selftest needed for this tip, as the implementer says.

---

## 3. Findings

### SHOULD-FIX

#### SF1 — "the exit-code contract, true of every path" is still false on `collect`'s two failure paths

`tools/buildkite/bk-lane.sh:57-65` (script header), `:120-122` (`--help`),
`REMOTE-LANES-BUILDKITE.md:305-312` (the table) all assert four codes, emphatically:
*"EXIT CODES — the whole contract, true of every path"* / *"**Exit codes — the whole
contract, on every path**"* / *"1 the build did not pass — a terminal state other
than `passed`, and **nothing else**"*.

Two paths in `collect` escape it. Neither goes through `die`, so `set -e` propagates
a raw status:

```
# a) an artifact download fails (404, 5xx, or the new stall bound tripping)
$ FAKE_DL_FAIL=yes bk-lane.sh collect 7 $S/dl
exit=22          <-- curl's own exit code, outside the documented set

# b) mkdir cannot create the tree (a file sits where a directory must go)
$ bk-lane.sh collect 7 $S/mk
mkdir: cannot create directory '…/abc123/.assay': File exists
exit=1           <-- the code reserved for "the build did not pass, and NOTHING ELSE"
```

`collect` never reports a build's verdict at all, so an exit 1 from it can *only*
be this failure — which is precisely the collision E5-R12 was created to remove,
reintroduced on a different path. A caller that branches on 0/1/2/3 (the whole
point of stating the contract) mis-handles both: 22 is undefined, and 1 is read as
"the build did not pass".

The mutation sweep confirms nothing tests it: **M13** — replacing the download with
`curl … || true`, so a failed download is silently ignored and `collect` reports
success — **survives all 66 tests**.

**Fix I would accept** (three lines and one test, no design question):

```bash
mkdir -p "$dest" || die "cannot create '$dest'"                                    # :372
mkdir -p "$dest/$(dirname "$path")" || die "cannot create the directory for '$path'"  # :410
curl -fsSL "${CURL_DOWNLOAD_BOUNDS[@]}" "$url" -H "$AUTH_HEADER" -o "$dest/$path" \
    || die "downloading artifact '$path' failed"                                   # :411
```

plus a stub test with a failing download asserting exit 2 (the stub already has the
shape for it — give it a `FAKE_DL_FAIL` branch). **Recommendation: fold this into
the merge commit.** It needs no judgment, and M13 is the test that proves it landed.

---

### NIT

- **N1 — the build number from the create response is guarded with the wrong charset,
  and untested.** E5-R7 ruled "`[0-9]+` on the build number"; bk-lane.sh:312 uses
  `check_path_component`, i.e. `[A-Za-z0-9._-]`. The *safety* half holds — a
  `"number": "../../x"` is refused (exit 2) — but `abc` and `1.2` are accepted and go
  straight into the poll URL:

  ```
    number=../../x    exit=2
    number=abc        exit=0   polled: builds/abc
    number=1.2        exit=0   polled: builds/1.2
  ```

  `check_number` (`:228-232`) already enforces exactly `[0-9]+` and already dies.
  One-word fix. Mutation **M14** (deleting the guard entirely) also **survives all
  66 tests**, so this line has no test at all.

- **N2 — the documented wall-clock bound understates by up to `BK_POLL_SECONDS`.**
  §4.2:320 and bk-lane.sh:36-38 say the bound is "`BK_MAX_WAIT_MINUTES` plus at most
  one 120 s request per poll". The budget is compared *before* the sleep
  (`:321`, `:327-328`), so the last sleep can overshoot it entirely:

  ```
  $ BK_MAX_WAIT_MINUTES=1 BK_POLL_SECONDS=600 bk-lane.sh run lint
  state: scheduled — polling again in 600s
  build 9 is still scheduled after 1 minutes of waiting; …
  exit=3          # budget 60 s, slept 600 s
  ```

  Either add `+ BK_POLL_SECONDS` to the stated bound, or clamp the final sleep to
  the remaining budget.

- **N3 — §3's enrollment snippet writes `pipeline.yml` into the checkout root.**
  `run-gate.py`'s `check_clean_tree` (`:689-698`) and `worktree_is_dirty` (`:812-822`)
  both use `git status --porcelain`, which reports untracked files — so a stray
  `pipeline.yml` in the checkout would fail every `clean_tree` lane on that host and
  mark every run dirty in the RG-27 trend series. It is safe **only** because the
  agent's default `git-clean-flags` is `-ffxdq` (vendor-confirmed) and each job
  re-cleans; a non-default clean flag or a custom checkout hook (§2 already sets
  `hooks-path`) turns it into a host-wide red. Writing to `"${TMPDIR:-/tmp}"/pipeline.yml`
  and uploading from there removes the coupling for free.

- **N4 — `LANE-AUTHORING.md` §5 on `main` misquotes the generator's globs.** The rule
  §3 and §6 row 1 now point at says the globs are `<project>/.assay/**`,
  `<project>/.assay/*` and `<project>/.run-gate/history.json`; the generator emits
  `<project>/.assay/*`, `<project>/.assay/**/*` and `<project>/.run-gate/history.json`
  — `.assay/**` vs `.assay/**/*`. Harmless to authors (the rule is "keep it under
  `.assay/`"), but the file is now the cited authority for the exact strings. That
  file is on `main` and outside this branch's scope — decision ask 2.

- **N5 — the header understates what the stub covers.** `REMOTE-LANES-BUILDKITE.md:15-17`
  says the live path is tested "for the collector"; the stub also covers `run`'s live
  path (all seven terminal states, the wait budget, exit 3). §4.2:342-348 says it
  correctly; the header should match.

- **N6 — stale section comment in the suite.** `tests/test_buildkite_tools.py:319`
  still reads `# seam 4 — tools/buildkite/bk-lane.sh (--dry-run only; no network, ever)`.
  The module docstring was corrected; this banner was not.

- **N7 — a lane name repeated in `RUN_GATE_LANES` while also being unknown is listed
  twice** rather than caught as a duplicate (`RUN_GATE_LANES="nope nope"` →
  `names lane(s) nope nope that … does not show`). Cosmetic; the duplicate check
  (pipeline.sh:178-180) only sees names that made it into `selected`.

---

## 4. Hollow-test hunt — a mutation sweep, not an opinion

Both scripts and the suite were copied to a scratch tree (no product file touched),
each mutation applied to the copy, the suite re-run serially under `nice -n 19
ionice -c 3`. Baseline on the copy: **66 passed**. A mutation that leaves 66 passing
is a hollow spot.

| # | mutation | result |
|---|---|---|
| M1 | drop the `.assay/*` glob | **3 failed** |
| M2 | `check_path_component` becomes a no-op | **34 failed** |
| M3 | drop the `-`-leading lane guard | **1 failed** |
| M4 | drop `--max-time` from the API bounds | **4 failed** |
| M5 | add `--max-time` to the download bounds | **1 failed** |
| M6 | `exit 3` becomes `exit 1` | **1 failed** |
| M7 | `run` always exits 0 | **6 failed** |
| M8 | drop the artifact `..` guard | **2 failed** |
| M9 | drop `set -f` in pipeline.sh | **1 failed** |
| M10 | drop the duplicate-lane guard | **1 failed** |
| M11 | drop the leading-zero guard | **2 failed** |
| M12 | `--dry-run` only before the verb again | **1 failed** |
| M15 | drop the *absolute*-path guard only | **1 failed** |
| M16 | token mode check accepts anything | **1 failed** |
| M17 | `show_curl` stops redacting the token | **3 failed** |
| M18 | `BK_QUEUE` sent even when unset | **2 failed** |
| **M13** | **a failed download silently ignored (`\|\| true`)** | **66 passed — SURVIVES (SF1)** |
| **M14** | **build number from the response unchecked** | **66 passed — SURVIVES (N1)** |

16 of 18 killed, and the two survivors are exactly the two findings above — found
independently by reading, then confirmed mechanically. Round 1's N1 ("E5-R2 asserted
by a word in prose") is comprehensively answered: M7 alone breaks six tests.

Suite runs:

```
$ nice -n 19 ionice -c 3 python3 -m pytest tests/test_buildkite_tools.py -q
66 passed, 1 warning in 2.74s          (identical in random and declared order)

$ python3 -m pytest tests -q --collect-only | tail -1
485 tests collected in 0.28s           (457 at 17077426, minus 38, plus 66 ✓)
$ … --collect-only | grep -c test_buildkite_tools.py
66
```

---

## 5. Vendor re-checks for the new claims

| claim | verdict | evidence |
|---|---|---|
| §3:149-154 "`buildkite-agent` runs a command with `bash -e -c` and *not* `-o pipefail`, so `pipeline.sh \| upload` would discard the generator's exit status" | **TRUE** | agent config docs: `shell` — "The shell command used to interpret build commands"; **default `"/bin/bash -e -c"`**; no `pipefail` anywhere in the default |
| §3:142-144 `buildkite-agent pipeline upload pipeline.yml` is valid usage | **TRUE** | CLI docs: `buildkite-agent pipeline upload [file] [options...]`, with the example `buildkite-agent pipeline upload my-custom-pipeline.yml` |
| §3:155-161 an `agents: queue:` on the upload step is needed because with none "it lands on an arbitrary agent — or on none, sitting in `scheduled` forever" | **TRUE** | consistent with the build-state list and with S3's own failure mode |
| §3:162-165 a build overriding `RUN_GATE_QUEUE` still runs its generator step on the literal queue, and the generated steps carry the override | **TRUE** | vendor precedence Pipeline → **Build** → Step (re-confirmed round 1); the generator reads `RUN_GATE_QUEUE` from its job env |
| §4.2/§4.3 downloads bounded by stall: "`--speed-time 60 --speed-limit 1024` … under 1 KiB/s for a minute fails, while a large transfer that keeps moving finishes" | **TRUE** | `man curl`: `-Y, --speed-limit` "If a transfer is slower than this set speed (in bytes per second) for a given number of seconds, it gets aborted. The time period is set with -y, --speed-time"; `-m, --max-time` is per-transfer total; `--connect-timeout` "only limits the connection phase" |
| §4.2 "every API request separately capped at 120 s" | **TRUE** | `CURL_API_BOUNDS` at bk-lane.sh:89, used by every API call and every dry-run line from the same array |
| §4.2:320 "the true wall-clock bound is `BK_MAX_WAIT_MINUTES` plus at most one 120 s request per poll" | **FALSE (small)** | N2 — the last sleep can overshoot by up to `BK_POLL_SECONDS` |
| §4.2:305-312 "Exit codes — the whole contract, on every path" | **FALSE** | SF1 — exit 22 and exit 1 on `collect`'s failure paths |
| §6:429 row 1 "no lane in the estate declares the set … every `artifacts =` in the repo today is a single file … `run-gate-project/selftest`'s own `coverage.json` matches none of the globs" | **TRUE** | the four declarations are `["coverage.json"]`×2, `[".assay/mutation-cmru.json"]`, `[".assay/coverage-canary-cmru.json"]`; `grep` for `progress-`/`history.json` in any `run-gate.toml`: none |
| §6:421-425 "Seams 2 and 4 … tested with no network … nothing here has yet run against a live agent or the real API" | **TRUE** | strace below; no test imports `urllib`/`socket`/`requests` |
| header:15-17 the live path is tested "for the collector" | **understated** | N5 — the stub covers `run` too |

Everything the round-1 table marked TRUE and that this branch did not touch was
spot-checked and still holds (REST paths, artifact fields, terminal-state list,
`BK_QUEUE` semantics, token-file rules, dependency list).

---

## 6. Re-run demonstrations

**Generator, three real projects** (`RUN_GATE_QUEUE=gate-review`, exit 0 each),
parsed with PyYAML 6.0.3 and asserted key-by-key **and in order**, with the third
glob now first:

```
run-gate-project  steps=1  keys+order OK, 3 globs OK, types OK
cmru              steps=5  keys+order OK, 3 globs OK, types OK
assay             steps=1  keys+order OK, 3 globs OK, types OK
```

```yaml
steps:
  - label: "run-gate: tester-unified on gate-review"
    command: "cd assay && ./run-gate.py tester-unified"
    agents:
      queue: "gate-review"
    concurrency: 1
    concurrency_group: "gate/gate-review"
    timeout_in_minutes: 300
    artifact_paths:
      - "assay/.assay/*"
      - "assay/.assay/**/*"
      - "assay/.run-gate/history.json"
    env:
      RUN_GATE_LANE: "tester-unified"
```

New generator refusals, all exit 2 and all naming the value: duplicate lane
(`selftest selftest`), `RUN_GATE_TIMEOUT_MINUTES=0300` (naming the octal trap),
`=0`, and `RUN_GATE_LANES='*'` now refused as the literal `*` rather than expanded.

**Dry-run, every verb** — bounds visible and coming from the same arrays as the real
calls:

```
$ bk-lane.sh run --dry-run lint
would create a build for ef8396df… on branch feature/run-gate-buildkite-seams with lanes: lint
curl '-fsS' '--connect-timeout' '10' '--max-time' '120' '-X' 'POST' '…/builds' '-H' 'Authorization: Bearer <redacted>' '-H' 'Content-Type: application/json' '-d' '{… "env": {"RUN_GATE_LANES": "lint"} …}'
then poll every 30s, for at most 300 minutes, until the state is terminal (passed failed canceled blocked skipped not_run waiting_failed):
curl '-fsS' '--connect-timeout' '10' '--max-time' '120' '…/builds/<build-number>' '-H' 'Authorization: Bearer <redacted>'
exit=0     curl calls recorded by the stub: 0

$ bk-lane.sh --dry-run collect 7 <dir>   (download line)
curl '-fsSL' '--connect-timeout' '10' '--speed-time' '60' '--speed-limit' '1024' '<artifact download_url>' '-H' 'Authorization: Bearer <redacted>' '-o' '<dir>/<commit>/<artifact path>'
```

**Live path, curl+sleep stubbed on `PATH`:** seven terminal states → `passed` 0, the
other six 1. Exit 3 as quoted in §1. `collect` happy path lands bytes at
`<dir>/<commit>/.assay/verdict.json`.

**Argument parsing, re-probed** (the `set -- ${positional[@]+"${positional[@]}"}`
idiom at `:167` is new, so I checked it does not word-split):

- destination directory **containing a space** → one file at `…/in box/abc123/.assay/verdict.json`, exit 0, and no stray `…/in` directory;
- destination containing a **glob character** → literal `st*ar` directory created, not expanded;
- the dry-run `-o` line quotes the spaced path correctly.

**Token and network.** `grep -c "$TOKEN"` over all four dry-run verbs plus `--help`
→ **0**. 0644 token refused naming the mode; `BK_ORG` refused by name.

```
$ strace -f -qq -e trace=network -o … bk-lane.sh --dry-run <run|status|collect>
traced lines: 68        socket|connect|sendto|recvfrom|bind|AF_* matches: none
```

**Zero socket syscalls of any family**, all three verbs, with the real `curl` on
`PATH`.

**shellcheck:** `-S style` (every level) on both scripts — exit 0, no output.

---

## 7. Decision asks

1. **SF1 — fold or file?** My recommendation: fold the three `|| die`s and one stub
   test into the merge commit. It is mechanical, M13 is the test that proves it, and
   it costs less than a third round. If you would rather ship the code as-is, the
   alternative is a doc change: stop claiming "every path" and name the two
   exceptions — but I would not, because the contract is what makes the tool
   automatable.
2. **N4 — `LANE-AUTHORING.md` §5 on `main` quotes `.assay/**` where the generator
   emits `.assay/**/*`.** That file is outside this branch. Correct it on `main`
   alongside the merge, or leave it (the authoring rule is right; only the quoted
   string is off by `/*`)?
3. **N1 — swap `check_path_component` for the existing `check_number` at
   bk-lane.sh:312**, which is E5-R7's letter and a strictly better message? One word,
   and it retires the second surviving mutation.
4. **N3 — write the generated pipeline to `"${TMPDIR:-/tmp}"/pipeline.yml`** instead
   of the checkout root, so the enrollment snippet cannot interact with
   `check_clean_tree` under a non-default `git-clean-flags`? Free, and §7's checklist
   is where an operator would otherwise discover it.
5. **N2/N5/N6/N7** are one-line corrections. Take them here, or let them ride into
   the first live-build pass, which will touch §4.3 anyway?

None of these needs another review round. My round-2 verdict does not depend on
which way 2–5 go.

---

*Reviewer scratch (stub harness, strace logs, the mutation copies of both scripts)
lives outside the repo under the session scratchpad; no product file was modified by
this review.*
