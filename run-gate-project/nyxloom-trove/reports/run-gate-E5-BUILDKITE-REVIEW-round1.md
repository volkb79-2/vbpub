# E-5 Buildkite seams 2 and 4 — adversarial review, round 1

- **Branch:** `feature/run-gate-buildkite-seams`, tip `17077426` (4 commits since `main`)
- **Worktree:** `/workspaces/vbpub/.worktrees/run-gate-buildkite-seams`
- **Reviewer:** fresh session (not the implementer, not a fork), 2026-09-03
- **Yardstick:** `REMOTE-LANES-BUILDKITE.md` §3/§4/§6 on the branch, `LANE-AUTHORING.md` §5,
  `CONSUMERS.md` "Anti-goals", rulings E5-R1..E5-R6 in
  `run-gate-E5-BUILDKITE-CONTROLLER-LOG.md` (on `main`).

## VERDICT: NOT ACCEPT

**1 BLOCKER, 5 SHOULD-FIX, 9 NIT.**

The two scripts are careful, well-commented work and most of what the record claims
about them is true and independently reproducible here: exact §3 step shape against
three real projects, exact vendor REST paths, no network in `--dry-run` (proved by
`strace`), no token in any output, `shellcheck` clean at *every* level, exact scope
discipline, 38/38 tests green. The blocker is a demonstrated directory traversal in
`collect` that writes **outside** the destination directory, on the one code path
that has never been executed and that no test covers. The fix is two lines.

---

## 1. Scope discipline — PASS

```
$ git -C … diff --name-only main...HEAD
run-gate-project/REMOTE-LANES-BUILDKITE.md
run-gate-project/tests/test_buildkite_tools.py
run-gate-project/tools/buildkite/bk-lane.sh
run-gate-project/tools/buildkite/pipeline.sh

$ git -C … diff --stat main...HEAD -- run-gate.py tests/test_run_gate.py \
      SPEC.md README.md CONSUMERS.md CHANGES.md run-gate.toml
(empty)
```

Exactly the four permitted files. Nothing the 23.4.0 wave owns is touched. No
`run-gate.py` line changed, so the selftest lane's `--source run-gate.py`
diff-coverage floor has nothing new to judge — the implementer's "no selftest for
`17077426`, deliberately" is correct.

---

## 2. Findings

### BLOCKER

#### B1 — `collect` writes outside `<dir>`: the `commit` path component is never validated

`tools/buildkite/bk-lane.sh:254-257`

```bash
commit=$(curl -fsS "$API/$number" -H "$AUTH_HEADER" | json_field commit)
local dest="$dir/$commit"
mkdir -p "$dest"
```

The *artifact* `path` is validated (bk-lane.sh:268 refuses absolute or `..`), and
§4.2 advertises exactly that containment: "Artifact paths that are absolute or
contain `..` are refused rather than written." But the **`commit`** component,
taken straight from the build response and used as a directory name, is not checked
at all. Buildkite's own docs describe `commit` as "Ref, SHA or tag to be built" — it
is a free-form string chosen by whoever created the build, and anyone with
`write_builds` on the pipeline can create a build with an arbitrary one.

Demonstrated (real script, `curl` stubbed on `PATH`, no network):

```
$ FAKE_BUILD='{"state":"passed","commit":"../../../escape"}' \
  bk-lane.sh collect 7 $S/dest/deep/er
/…/scratchpad/dest/deep/er/../../../escape/pwned.txt
collected 1 artifact(s) of build 7 into /…/scratchpad/dest/deep/er/../../../escape
exit=0

$ find $S/escape -type f
/…/scratchpad/escape/pwned.txt          <-- OUTSIDE the destination directory
```

The control the manual promises is defeated through the component the manual does
not mention. Nothing is live yet, so nothing is exploited today — but merging ships
a collector with a traversal hole plus a doc sentence asserting containment, and the
first live `collect` is the first exercise of this code.

**Fix I would accept** (either):
- reject a `commit` that is not `[A-Za-z0-9._-]+` (a SHA always is), with a `die`
  naming the value — symmetric with the lane-name and queue-name guards already in
  the script; or
- move the commit into the same python guard that already vets artifact paths.

Please also add the one test that covers it: a `curl` stub on `PATH` (10 lines,
no network) makes the whole live path testable — see N1.

---

### SHOULD-FIX

#### S1 — `bk-lane.sh run --dry-run lint` silently creates a REAL build

`tools/buildkite/bk-lane.sh:102-112` (option loop breaks at the first non-option)
and `:160` (lane charset `[A-Za-z0-9._-]` **admits a leading `-`**).

The option loop stops at `run`, so a `--dry-run` typed after the verb is not an
option any more; it then passes the lane-name check because `-` is a legal lane
character. Demonstrated (curl stubbed, so nothing left this host):

```
$ bk-lane.sh run --dry-run lint
build 4242 for 17077426… (branch feature/run-gate-buildkite-seams), lanes: --dry-run lint
build 4242 state: passed
exit=0
--- curl calls actually attempted:
FAKECURL -fsS -X POST https://api.buildkite.com/v2/…/builds -H Authorization: Bearer <token> …
  -d {… "env": {"RUN_GATE_LANES": "--dry-run lint"} …}
```

This is the one flag whose entire job is "make no network call", and the most
natural misordering turns it into a live `POST` — with a bogus lane name that the
generator will then refuse on the agent. Given the live path has never been run,
the flag is exactly what an operator will lean on.

**Fix:** refuse a lane name starting with `-` (`-*) die "lane names cannot start
with '-'; --dry-run must come before the verb" ;;`), and/or accept `--dry-run`
anywhere in argv. A test for `run --dry-run lint` → exit 2.

#### S2 — §6 seam-1 row is untrue: nothing declares those artifacts, and the globs do not "match"

`REMOTE-LANES-BUILDKITE.md:365`

> `| 1 | Artifacts contract | … | **landed (dry-run tested)** | every remote-capable
> lane declares `artifacts` covering `.assay/<lane>/**`, `.assay/progress-<lane>.jsonl`,
> `.run-gate/history.json` … the generator emits the matching `artifact_paths` globs
> per step, asserted in the suite |`

Two false claims:

1. **No lane in the estate declares that set.** Every `artifacts =` in the repo:

```
run-gate-project/run-gate.toml:51: artifacts = ["coverage.json"]
cmru/run-gate.toml:41:            artifacts = ["coverage.json"]
cmru/run-gate.toml:61:            artifacts = [".assay/mutation-cmru.json"]
cmru/run-gate.toml:72:            artifacts = [".assay/coverage-canary-cmru.json"]

$ grep -rn "progress-\|history.json" --include=run-gate.toml .
(none)
```

Nothing declares `.run-gate/history.json` or a progress file, and this branch
touches neither `LANE-AUTHORING.md` nor any `run-gate.toml`. Seam 1 has not landed;
the branch changed the row from "nothing implemented yet" to "**landed**" on the
strength of the generator alone. E5-R5 accepted row 1 "as qualified — a docs seam
whose only executable evidence is the generator's `artifact_paths`"; **landed** is
not that qualification.

2. **"the generator emits the *matching* globs"** — the generator emits two *fixed*
globs and never sees a lane's declared `artifacts` (it cannot: `--list` does not
carry them, per the anti-goal). vbpub's own `run-gate-project/selftest` declares
`artifacts = ["coverage.json"]`, resolving to `run-gate-project/coverage.json`,
which matches **neither** `run-gate-project/.assay/**/*` nor
`run-gate-project/.run-gate/history.json`. A live run of the very first lane §7
recommends would not bring its declared artifact back.

**Fix:** restore seam 1's status to something true — "docs seam, not started
(E5-R5): the generator's two fixed globs are its only executable evidence; no lane
declares the set yet" — and replace "matching" with a plain statement that the
globs are fixed and a lane's declared `artifacts` outside `.assay/` and
`.run-gate/` do not travel. (Reconciling the two is a real design question — see
decision ask 2.)

#### S3 — `run` polls forever; a mistyped `BK_QUEUE` is the way in

`tools/buildkite/bk-lane.sh:215-222` — `while :; do … sleep "$poll_seconds"; done`
with no attempt cap and no deadline. A build parked in `scheduled` never becomes
terminal, and `BK_QUEUE`, the flag this branch adds, is validated only for charset
(`:176-178`), never against a queue any agent listens on. `BK_QUEUE=gate-alpah`
therefore yields an unattended process spinning a `GET` every 30 s until someone
notices. The §3 upload step (S4) is a second route to the same place.

**Fix:** a `BK_MAX_WAIT_MINUTES` (default matching the step's 300, or 2×) that exits
non-zero naming the last state and the build number, so `status`/`collect` can pick
it up later — §4.4 already sells that hand-off.

#### S4 — §3's stored pipeline configuration discards the generator's refusal, and pins the upload step to no queue

`REMOTE-LANES-BUILDKITE.md:135-139`

```yaml
steps:
  - label: ":pipeline: lanes"
    command: "run-gate-project/tools/buildkite/pipeline.sh run-gate-project | buildkite-agent pipeline upload"
```

Two problems in the only documented invocation of a script whose contract the same
section states as "**exit 2 is always a refusal**" (`:173`):

- `buildkite-agent` runs a command with `bash -e -c`, **not** `-o pipefail`. The
  generator's exit 2 is discarded; the pipeline's status is `buildkite-agent`'s.
  The refusal contract — the whole "it refuses rather than guesses" design — is
  inert exactly where it matters. Fix: `set -o pipefail; pipeline.sh … | buildkite-agent
  pipeline upload`, or generate to a file and upload the file.
- the upload step declares no `agents: queue:`, so in the two-host estate §7's last
  checklist line aims at ("two builds for two hosts run concurrently") the *generator*
  job lands on an arbitrary agent — or on none, if the default queue has no agent, in
  which case the build sits in `scheduled` and S3 spins. Fix: give it
  `agents: { queue: "${RUN_GATE_QUEUE}" }`, or state in prose why an arbitrary agent
  is acceptable.

#### S5 — the stated exit-code contract is not what the script does

`tools/buildkite/bk-lane.sh:77` and `REMOTE-LANES-BUILDKITE.md:258-259`:
"exit codes: 0 ok/passed, 1 the build did not pass, 2 refused."

The artifact-path refusal exits **1**, not 2 (`set -euo pipefail` propagates the
python `sys.exit`), and so does a response missing a field (`json_field`, `:138-145`):

```
$ bk-lane.sh collect 7 $S/dest      # listing carries path "../../escape/x"
bk-lane.sh: refusing artifact path '../../escape/x'
exit=1
```

So exit 1 means both "the build did not pass" and "I refused something" — the one
distinction the contract exists to make. **Fix:** route those refusals through `die`
(exit 2), or state the exception in both places.

---

### NIT

- **N1 (hollow tests — the headline).** The entire non-dry-run path has zero test
  coverage, including two things the record treats as verified:
  - **E5-R2 (`blocked` is terminal, exit 1) is asserted by no test.** Its only
    "evidence" is `test_bk_run_dry_run_prints_the_post_with_a_redacted_token:335-337`
    asserting the *word* `blocked` appears in dry-run prose. That would pass against
    an implementation that prints the word and then loops forever on `blocked`. I
    verified the real behaviour by hand with a `PATH`-stubbed `curl` (all seven
    terminal states → exit 1 except `passed` → exit 0); a stub like that is ~10
    lines, needs no network, and would make R2, the traversal guard (B1) and the
    exit-code contract (S5) genuinely tested.
  - the artifact path-traversal guard (`:268`), which §4.2 advertises as a safety
    property.
- **N2 (hollow).** `test_both_tools_are_executable_…:505`: `assert "jq " not in
  _uncommented(BK_LANE_SH)` passes for `| jq -r …`, `jq\n`, `/usr/bin/jq`.
- **N3 (hollow).** Same test at `:335-337`: `"failed" in res.stdout` is satisfied by
  the substring inside `waiting_failed`, so a `TERMINAL_STATES` that dropped `failed`
  would still pass.
- **N4.** `pipeline.sh:118-122` accepts leading zeros and emits them verbatim.
  `RUN_GATE_TIMEOUT_MINUTES=0300` → `timeout_in_minutes: 0300`, which YAML resolves as
  **octal 192**, not 300 (`yaml.safe_load('c: 0300')` → `192`). Strip leading zeros
  or refuse them.
- **N5.** `pipeline.sh:161` `for want in $requested` runs with globbing on:
  `RUN_GATE_LANES='*'` expands against the CWD (demonstrated: refused, naming
  `decoyfile proj` as lanes, after silently matching a file called `selftest`).
  Fails closed today; `set -f` around the loop removes the class.
- **N6.** `RUN_GATE_LANES="selftest selftest"` emits two identical steps with
  identical labels (verified: step count 2). De-duplicate, or say it is intentional.
- **N7.** `REMOTE-LANES-BUILDKITE.md:187` "A generated step, **verbatim from the
  generator**" — the block that follows is not verbatim; it mixes the placeholder
  `<project>` with a concrete `gate-alpha`. Say "with `<project>` standing in for the
  argument".
- **N8.** §4.3 asserts the progress file travels back, but `.assay/**/*` matching a
  file sitting *directly* in `.assay/` (`.assay/progress-<lane>.jsonl`) depends on
  Buildkite's `zglob` treating `**` as "zero or more directories". Unverified here
  and cheap to insure: add `<project>/.assay/*` as a third glob, or verify on the
  first live build before §4.3 states it as fact.
- **N9.** `bk-lane.sh status 7 bogus extra` silently ignores the extra arguments
  (exit 0). `collect` likewise. Refuse them.

---

## 3. Generator output against the three real projects (abbreviated)

`RUN_GATE_QUEUE=gate-review run-gate-project/tools/buildkite/pipeline.sh <project>`,
from the worktree root, exit 0 for all three. Raw `--list` first (three columns
everywhere — the contract the generator was written against holds against real
projects):

```
run-gate-project: selftest<TAB>command<TAB>host
cmru:             assay<TAB>assay<TAB>tester-unified
                  canary<TAB>command<TAB>tester-unified
                  coverage<TAB>command<TAB>tester-unified
                  gate<TAB>command<TAB>host
                  mutation<TAB>command<TAB>tester-unified
assay:            tester-unified<TAB>command<TAB>host
```

Emitted: 1 step, 5 steps, 1 step respectively (the task brief expected "several
lanes" in each; only `cmru` has several — `assay`'s single lane happens to be
*named* `tester-unified`).

Parsed with PyYAML 6.0.3 (system `python3`; `/opt/tester-venv/bin/python` does not
exist on this host — the selftest lane's argv uses plain `python3`, `environment =
"host"`). Every step of all three projects asserted key-by-key **and in order**:

```python
["label","command","agents","concurrency","concurrency_group",
 "timeout_in_minutes","artifact_paths","env"]        # exact list, exact order
agents == {"queue": ...}; concurrency == 1 (int);
concurrency_group == "gate/" + queue; timeout_in_minutes == 300 (int);
len(artifact_paths) == 2; env keys == {"RUN_GATE_LANE"}
→ steps: 1 / 5 / 1  keys+order OK, types OK
```

First step of `cmru`, as parsed:

```json
{"label": "run-gate: assay on gate-review",
 "command": "cd cmru && ./run-gate.py assay",
 "agents": {"queue": "gate-review"},
 "concurrency": 1,
 "concurrency_group": "gate/gate-review",
 "timeout_in_minutes": 300,
 "artifact_paths": ["cmru/.assay/**/*", "cmru/.run-gate/history.json"],
 "env": {"RUN_GATE_LANE": "assay"}}
```

Matches §3 exactly. (`RUN_GATE_LANE`, singular, is read by nothing in `run-gate.py`
— `grep -n "RUN_GATE_LANE\b"` → no hits — but it is pre-existing in §3 on `main`,
so the generator is faithful to the manual, not inventing.)

Selection and refusals, all reproduced:

| case | result |
|---|---|
| `RUN_GATE_LANES="selftest nope"` | exit 2, names `nope` **and** lists what it does show |
| `RUN_GATE_QUEUE` unset | exit 2, names `RUN_GATE_QUEUE` |
| `RUN_GATE_TIMEOUT_MINUTES=5m` | exit 2, names the value |
| two positional args | exit 2, "takes at most one argument … got 2" |
| lane named `la"ne: #x` in the listing | exit 2, names the lane, **nothing emitted** |
| lane named `sel$(touch …)\`id\`` | exit 2; `PWNED` file **not** created — the `<<EOF` heredoc expands `$listing` once and does not re-scan the result |
| four-column listing | exit 2, "three documented columns" |

**No second parser:** `grep -n "run-gate.toml"` hits **comments only** in both
scripts (`pipeline.sh:15`, `bk-lane.sh:19`); the only lane-metadata read anywhere is
`cd "$project" && ./run-gate.py --list` (`pipeline.sh:125`). CONSUMERS anti-goal
honoured; E5-R1 honoured (no fourth column added — `run-gate.py` untouched).

---

## 4. Dry-run transcripts (`BK_ORG=o BK_PIPELINE=p`, token file mode 0600)

```
$ bk-lane.sh --dry-run run lint
would create a build for 17077426cb8a30f6e74e5f80701e3c02b9f7feb7 on branch feature/run-gate-buildkite-seams with lanes: lint
curl '-fsS' '-X' 'POST' 'https://api.buildkite.com/v2/organizations/o/pipelines/p/builds' '-H' 'Authorization: Bearer <redacted>' '-H' 'Content-Type: application/json' '-d' '{"branch": "feature/run-gate-buildkite-seams", "commit": "17077426…", "env": {"RUN_GATE_LANES": "lint"}, "message": "run-gate: lint"}'
then poll every 30s until the state is terminal (passed failed canceled blocked skipped not_run waiting_failed):
curl '-fsS' 'https://api.buildkite.com/v2/organizations/o/pipelines/p/builds/<build-number>' '-H' 'Authorization: Bearer <redacted>'
exit=0

$ bk-lane.sh --dry-run status 7
curl '-fsS' 'https://api.buildkite.com/v2/organizations/o/pipelines/p/builds/7' '-H' 'Authorization: Bearer <redacted>'
exit=0

$ bk-lane.sh --dry-run collect 7 /tmp/x
would read the build to learn its commit:
curl '-fsS' 'https://api.buildkite.com/v2/organizations/o/pipelines/p/builds/7' '-H' 'Authorization: Bearer <redacted>'
would list the artifacts of build 7:
curl '-fsS' 'https://api.buildkite.com/v2/organizations/o/pipelines/p/builds/7/artifacts' '-H' 'Authorization: Bearer <redacted>'
then, for each artifact in that listing, into /tmp/x/<commit>/<path>:
curl '-fsSL' '<artifact download_url>' '-H' 'Authorization: Bearer <redacted>' '-o' '/tmp/x/<commit>/<artifact path>'
exit=0

$ BK_QUEUE=gate-b bk-lane.sh --dry-run run lint
would create a build for 17077426… on branch feature/run-gate-buildkite-seams with lanes: lint
the build's env overrides the pipeline queue: RUN_GATE_QUEUE=gate-b
curl … '-d' '{"branch": "…", "commit": "…", "env": {"RUN_GATE_LANES": "lint", "RUN_GATE_QUEUE": "gate-b"}, "message": "run-gate: lint"}'
…
exit=0
```

**Against the vendor docs (fetched 2026-09-03):**

| checked | vendor says | script does | ✓ |
|---|---|---|---|
| create-build | `POST /v2/organizations/{org.slug}/pipelines/{pipeline.slug}/builds` | identical | ✓ |
| required body | `commit`, `branch` | both sent | ✓ |
| optional body | `env`, `message` among them | both sent | ✓ |
| get one build | `GET /v2/organizations/{org}/pipelines/{pipeline}/builds/{number}` (number, not id) | identical, `check_number` enforces digits | ✓ |
| build states | `scheduled, running, passed, failed, blocked, canceled, canceling, skipped, not_run, waiting, waiting_failed` | terminal set = all but `scheduled, running, canceling, waiting` — the four transients | ✓ |
| list artifacts | `GET …/builds/{build.number}/artifacts` | identical | ✓ |
| artifact fields | `id, job_id, url, download_url, state, path, dirname, filename, mime_type, file_size, sha1sum`; `glob_path`/`original_path` deprecated → `null` | doc's list is exact; collector uses `path` + `download_url` only | ✓ |
| download | "Returns a 302 response to a URL for downloading an artifact" (≤60 s validity) | `curl -fsSL` follows it | ✓ |
| **env precedence** | "values in each successive set take precedence": **Pipeline → Build → Step → Standard** | E5-R6's premise (a build's env beats the pipeline's top-level `env`) is **confirmed by the vendor**; step `env` would beat both, and the generated steps set only `RUN_GATE_LANE`, so no collision | ✓ |

**E5-R6 behaviour verified:** `BK_QUEUE` set → `"RUN_GATE_QUEUE": "gate-b"` present in
the body; **unset → the key is absent entirely** (not sent empty), so the pipeline's
default queue stands.

**E5-R2 verified** (curl stubbed on `PATH`, no network — no test covers this, see N1):

| state | exit |
|---|---|
| `blocked` | **1** |
| `failed`, `canceled`, `skipped`, `not_run`, `waiting_failed` | 1 |
| `passed` | 0 |

**Token safety.** `grep -c "$TOKEN"` over the combined stdout+stderr of all four
dry-runs → **0**. A 0644 token file is refused naming the mode it found; a missing
file and an empty file are refused; `BK_ORG` and `BK_PIPELINE` are each refused **by
name** (exit 2). Positive note: `curl 8.14.1` strips a custom `Authorization` header
across a cross-host redirect (default since 7.58), so `-L` to the S3 target does not
leak the Buildkite token.

**No network, proved.** `unshare -n` is denied in this container, so:

```
$ strace -f -qq -e trace=network -o … bk-lane.sh --dry-run <run lint | status 7 | collect 7 /tmp/x>
$ cat …/st.*.txt | grep -oE "AF_[A-Z]+" | sort | uniq -c
(no output)
$ grep -E "socket|connect|sendto|recvfrom|bind" …/st.*.txt   → no matches
```

42 traced lines across all three verbs, every one a `SIGCHLD`. **Zero socket
syscalls of any family.** `--dry-run collect` also creates no directory
(`test_bk_collect_dry_run_…:403` asserts this and it holds).

---

## 5. Docs-truth table (§3, §4, §6)

| # | claim (file:line) | verdict | evidence |
|---|---|---|---|
| 1 | header: "REST paths in §4 were re-read from the vendor docs on 2026-09-03 and are what `bk-lane.sh` actually calls" | **TRUE** | all three paths match the vendor pages and the dry-run transcripts |
| 2 | header/§6: seams "tested through `--dry-run` only … no build has been created and no artifact downloaded" | **TRUE** | strace: zero socket syscalls; no test in `tests/` imports `urllib`/`socket`/`requests` |
| 3 | §3:150-154 "It reads `./run-gate.py --list` … It never opens `run-gate.toml`" | **TRUE** | `grep` finds `run-gate.toml` in comments only; sole read is `pipeline.sh:125` |
| 4 | §3:154 "starts no container, makes no network call and takes no `--dry-run`" | **TRUE** | pure `printf`; `--dry-run` → exit 2 "unknown option" |
| 5 | §3:162-166 `PROJECT_DIR` used for `--list` *and* verbatim in `command`/`artifact_paths`; "a plain `.` emits neither a `cd` nor a path prefix" | **TRUE** | reproduced against all three projects and with default `.` |
| 6 | §3 env table: `RUN_GATE_QUEUE` required, unset → exit 2 naming it | **TRUE** | reproduced |
| 7 | §3 env table: unknown `RUN_GATE_LANES` name → exit 2 "naming it (and listing what it does show)" | **TRUE** | reproduced verbatim |
| 8 | §3:173-177 "**exit 2 is always a refusal**" | **TRUE of the script**, **defeated by §3's own stored config** | see S4 (no `pipefail` behind the pipe) |
| 9 | §3:173-177 listing contract "verified against `run-gate.py` rev 33 `cmd_list()`" | **TRUE** | `__revision__ = 33`; real `--list` is three tab-separated columns for all three projects |
| 10 | §3:187-188 "A generated step, **verbatim from the generator**" | **FALSE (cosmetic)** | block mixes `<project>` placeholder with concrete `gate-alpha` — N7 |
| 11 | §3:208-210 "`concurrency: 1` + `concurrency_group: "gate/$RUN_GATE_QUEUE"`" | **TRUE** | `pipeline.sh:189-190` |
| 12 | §3:135-139 the stored two-key config as written | **INCOMPLETE** | drops the generator's exit status; upload step pinned to no queue — S4 |
| 13 | §3:144-145 "A build's own env overrides the pipeline's" | **TRUE** | vendor precedence: Pipeline → **Build** → Step |
| 14 | §4.2:253-256 `run` takes commit+branch from git; detached HEAD refused | **TRUE** | reproduced; `test_bk_run_refuses_outside_a_git_work_tree` covers the no-repo case |
| 15 | §4.2:257-258 terminal-state list | **TRUE** | matches the script exactly and is the vendor list minus its four transients |
| 16 | §4.2:258-259 "Exit 0 only on `passed`; 1 for any other terminal state; 2 for a refusal" | **half FALSE** | first two verified for all 7 states; "2 for a refusal" is untrue for the traversal and missing-field refusals — S5 |
| 17 | §4.2:260-263 `collect` downloads into `<dir>/<commit>/<artifact path>`; "Artifact paths that are absolute or contain `..` are refused rather than written" | **TRUE of `path`, FALSE of the containment it implies** | `..` in `path` is refused (exit 1); `..` in **`commit`** writes outside `<dir>` — B1 |
| 18 | §4.2 env table: `BK_TOKEN_FILE` "must be mode 0600 or the script refuses (exit 2, naming the mode it found)" | **TRUE** | `is mode 644; it must be 0600 …`, exit 2 |
| 19 | §4.2 env table: `BK_QUEUE` "sent … beside `RUN_GATE_LANES`; unset, the key is not sent at all" | **TRUE** | both transcripts above |
| 20 | §4.2:274-276 "Dependencies are bash, coreutils, git, curl and python3 … `jq` is deliberately not assumed" | **TRUE** | no `jq` invocation; JSON via inline `python3` stdlib |
| 21 | §4.2:286-291 artifact-object field list, `glob_path`/`original_path` null and deprecated | **TRUE** | exact match with the vendor page |
| 22 | §4.2:293-297 "`--dry-run` prints every curl invocation … token replaced by `<redacted>` … makes **no** network call … no test in this repo touches the network" | **TRUE** | strace + `grep -c "$TOKEN"` = 0 |
| 23 | §4.2:297-299 "The live path has never been executed" | **TRUE** | no test exercises it; I exercised it only against a stubbed `curl` |
| 24 | §4.3:305-311 `collect` downloads "the `artifact_paths` the §3 step declared, i.e. the verdict, the progress file and the evidence directory under `.assay/`, plus the RG-27 `history.json`" | **UNVERIFIED** | depends on `zglob` matching `.assay/**/*` against a file directly under `.assay/` — N8 |
| 25 | §4.4:313-315 "`run` prints the build number before it starts polling, so an operator can `Ctrl-C` and come back" | **TRUE** | `build 4242 for … ` printed before the first poll |
| 26 | §4.4:318-320 collect-by-commit "is not implemented, and `collect` takes the build number for now" | **TRUE** | E5-R3 honoured |
| 27 | §6:358-361 "Seams 1, 2 and 4 … have landed — dry-run tested only" | **FALSE for seam 1** | S2 |
| 28 | §6:365 row 1 "the generator emits the **matching** `artifact_paths` globs per step" | **FALSE** | globs are fixed; `run-gate-project/selftest`'s declared `coverage.json` matches neither — S2 |
| 29 | §6:366 row 2 shape and refusals | **TRUE** | all four refusals reproduced |
| 30 | §6:368 row 4 "token file must be 0600 … `collect` downloads into a commit-addressed directory" | **TRUE** | reproduced |
| 31 | §6:358-361 "`tests/test_buildkite_tools.py` runs both scripts with no network, no Buildkite account and no container" | **TRUE** | 38 tests, 0.94 s, no docker, no sockets |
| 32 | controller log: "`shellcheck -S warning` clean on both scripts … clean at every level now" | **TRUE** | `-S warning` **and** `-S style`: exit 0, no output, both files |

---

## 6. Tests

```
$ cd run-gate-project && nice -n 19 ionice -c 3 python3 -m pytest tests/test_buildkite_tools.py -q
38 passed, 1 warning in 0.94s
```

38 as claimed. Collected by the selftest lane's own target:

```
$ python3 -m pytest tests -q --collect-only | tail -1
457 tests collected in 0.25s
$ … --collect-only | grep -c "test_buildkite_tools.py"
38
```

457 = the controller's 452+2 at `81ff037f` plus this commit's 3 new tests —
arithmetic consistent, so I did **not** re-run the whole selftest. The
`--source run-gate.py` diff-coverage floor is untouched (§1: `run-gate.py` has no
changed line on this branch).

**Hollow tests:** N1 (E5-R2 asserted only as a printed word; the whole live path,
including the documented traversal guard, untested), N2 (`"jq "` substring), N3
(`"failed"` satisfied by `waiting_failed`). The two `never_reads_run_gate_toml`
tests are static substring checks — hollow by construction, but they are exactly
what the anti-goal asks for and I independently confirmed the property.

---

## 7. Safety

| check | result |
|---|---|
| `shellcheck -S warning`, both scripts | exit 0, no output |
| `shellcheck -S style` (every level), both scripts | exit 0, no output — the SC1003 note is disabled by name with an inline reason |
| `eval` | none |
| temp files / `mktemp` / `/tmp` / `trap` | none — nothing to clean up |
| unquoted expansions | only the four deliberate word-split loops (`pipeline.sh:157,161,163,183`); all operands pass a `[A-Za-z0-9._-]` gate **except** `$requested`, which also globs — N5 |
| lane names / project dir / queue name | each gated on `[A-Za-z0-9._-]` (project dir on `" $ \` \ newline`) before entering emitted YAML; a `"`-bearing lane, a `#`-bearing lane and a `$(…)`/backtick lane all refused with nothing emitted |
| command injection via `--list` | none — the `<<EOF` heredoc expands `$listing` once and does not re-scan; verified with a `$(touch …)` lane name |
| token in output | never (`grep -c` = 0 across all dry-runs); redaction is exact-match on `$AUTH_HEADER` |
| token file permissions | 0600 enforced via `stat -c '%a'` (follows symlinks, so a symlink to a 0644 file is refused too) |
| network in dry-run | zero socket syscalls under `strace -f -e trace=network` |
| token leak across redirect | `curl 8.14.1` strips `Authorization` on cross-host redirect — safe |
| **`collect` writing outside `<dir>`** | **YES — via the unvalidated `commit` component. B1.** |

---

## 8. Decision asks

1. **B1 fix shape.** Validate `commit` as `[A-Za-z0-9._-]+` in shell (symmetric with
   the existing guards), or fold it into the python guard that already vets artifact
   paths? I would take either; the shell guard is closer to the script's own idiom.
2. **Seam 1 vs. the fixed globs (S2).** The generator cannot see a lane's declared
   `artifacts` without breaking the anti-goal, yet `LANE-AUTHORING.md` §5 tells
   authors to declare them and §3 says the two must line up. Three ways out:
   (a) accept the mismatch and say so plainly in §3/§6 (cheapest, docs-only);
   (b) require remote-capable lanes to put every artifact under `.assay/` and say
   so in `LANE-AUTHORING.md` §5 (a run-gate docs change, out of this branch's scope);
   (c) expose `artifacts` through the `--list --json` form already reserved as
   **RG-45** and have the generator use it. Which, and does (a) suffice for merge?
3. **S1 (`run --dry-run` swallowed).** Refuse `-`-leading lane names, or accept the
   flag anywhere in argv, or both? I would take "refuse `-`-leading" as the minimum.
4. **S3 (unbounded poll).** Add `BK_MAX_WAIT_MINUTES` now, or file it as an RG item
   and merge with the behaviour documented as unbounded?
5. **S4 (§3 stored config).** Fix the pipe (`set -o pipefail`) and pin the upload
   step's queue in this branch, or file both? These are the two lines an operator
   will paste into the Buildkite UI at enrollment, so I would fix them here.
6. **N8 (`.assay/**/*`).** Add `<project>/.assay/*` as a third glob as insurance, or
   soften §4.3 to "verify on the first live build"?

None of 2–6 need a second review round on their own. **B1 does** — or, if the
controller judges the traversal acceptable because nothing is live and the fix is
mechanical, it can be verified in the merge commit without a full round.

---

*Reviewer scratch (curl stub, strace logs, stub projects) lives outside the repo
under the session scratchpad; no product file was modified by this review.*
