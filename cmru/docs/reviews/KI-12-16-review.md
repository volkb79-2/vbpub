READY WITH CORRECTIONS

# Adversarial review — cmru KI-12 + KI-16 (package A)

Reviewed in `/workspaces/vbpub/.worktrees/cmru-KI-12-16` against the diff of
`cmru/` vs HEAD plus the three untracked test files
(`tests/test_ki12_release_plan_baseline.py`, `tests/test_ki12_cli_wiring.py`,
`tests/test_ki16_worktree_naming.py`). The controller's verifications (three
tag states, 1:1 naming round-trip, old-worktree compatibility, 1457 passed /
100% coverage) were not repeated. All probes below ran read-only against
`/workspaces`; scratch repos lived in `/tmp/cmru-a-review/`.

Verdict rationale: one blocking finding (B1) — a fourth tag state that evades
the new refusal and gets certified with a false "already released" message —
plus corrections. The change is still a strict improvement over main (before
it, *every* local-only tag silently disabled a project; after it, only the
name-colliding subset does), the fix is small and local, and the real estate
is clean today (measured below), so "ready with corrections" rather than "not
ready". B1 must land before the next real release runs through this code.

---

## 1. Blocking findings

### B1 — Fourth tag state: a local tag whose NAME exists on origin at a DIFFERENT commit silently disables the project and prints a false "already released" certification

**What is wrong.** `_tag_pushed_to_origin` (version.py:77) checks only that a
ref *named* `refs/tags/<tag>` exists on origin. It captures `git ls-remote`'s
stdout — which contains the remote SHA that would expose a mismatch — and
throws it away. Every subsequent decision (`_git_log`, the equal/ahead/behind
classification) then runs against the **local** tag object. So S12.2a's
guarantee ("the release plan's baseline MUST reflect the pushed repository")
holds only for the tag's name, not its position.

**The measurement** (`/tmp/cmru-a-review/e1`). Origin legitimately holds
`demo-v1.0.0` released at commit C1. A new commit C2 lands and is pushed
(it is origin/main = the snapshot). The operator hand-tags `demo-v1.0.0`
locally at C2 — the *exact* KI-12 incident, with the one added ingredient
that the name already exists on origin:

```
local  tag commit: 69969e2ae2b238f559bf18c18d10ed08647440ba          (= snapshot HEAD)
remote tag line  : a29e264e50b2...  refs/tags/demo-v1.0.0            (different object)
remote peeled    : 04900b4d6750...  refs/tags/demo-v1.0.0^{}         (different commit)

detect_changed_projects(..., require_pushed_baseline=True, abort_on_tag_at_head=True)
[INFO] Unchanged, skipping: demo (already released as demo-v1.0.0 at the snapshot commit; nothing new since)
PLAN: []
```

The INFO line is **false**: commit 69969e2a was never released anywhere;
origin's `demo-v1.0.0` is a different commit. The project has real unreleased
work relative to what was actually published, and the plan silently drops it
— the same *wrong answer* class KI-12 was filed to kill, now endorsed by an
explicit "already released" message. Reachable states: hand-tagging the
version you expect the next release to mint after another machine/CI already
released it (the incident shape); a tag moved upstream that `git fetch`
refuses to update locally (tag clobber protection — fetch never auto-updates
a differing existing tag); an operator "repairing" a failed release with
`tag -d` + re-tag. The same gap also mis-positions the baseline in the
"behind" and "ahead" analyses (the `git log <tag>..HEAD` window is computed
from the local object).

**Consequence if shipped.** A silently empty (or wrongly-windowed) release
for any of the seven products, certified with an informative message that
actively asserts the opposite — strictly harder to diagnose than the KI-12
incident, because the new output *names a baseline that looks verified*.

**Smallest fix.** `_tag_pushed_to_origin` already has the data: parse the
ls-remote stdout it currently discards and require object identity, e.g.
resolve the remote's peeled commit (query patterns `refs/tags/<tag>` and
`refs/tags/<tag>^{}` — the peeled line exists for annotated tags, measured
in e1) and compare against local `git rev-parse <tag>^{commit}`. On mismatch,
raise a distinct refusal ("tag <t> exists on origin but points at <r>, your
local tag points at <l> — fetch/repair the tag before releasing"). Zero
extra network calls. One new unit test per direction (local-at-HEAD/remote
-elsewhere, and the inverse) in `test_ki12_release_plan_baseline.py`.

---

## 2. Corrections (should land with the change)

### C1 — `--allow-tag-at-head` is misnamed, its help text describes the pre-correction rule, and it silently suppresses the equal-state message the docs promise unconditionally

Confirmed (brief item 3): after the correction, the "equal" (at-head) state
never aborts, so the flag only downgrades the **ahead** refusal — but
`cli.py:2124` still advertises "when a project's latest tag already sits **at
or ahead of** the snapshot commit". Worse, measured end-to-end
(`/tmp/cmru-a-review/e9`, child `--dry-run`, pushed tag exactly at snapshot):

```
default:              [INFO] Unchanged, skipping: demo (already released as demo-v1.0.1 at the snapshot commit; nothing new since)
--allow-tag-at-head:  (line absent — plain silent skip)
```

S12.2b ("it is reported as an informative skip") and RELEASE-TRANSACTIONS.md
("*equal* … is reported as an informative skip, never an error") state the
equal-state report unconditionally; the code prints it only when
`abort_on_tag_at_head=True`, because the print sits inside that conditional
(version.py:406-413). Fix: rename the flag `--allow-tag-ahead` (or at
minimum rewrite the help to say "strictly ahead"), and hoist the
equal-state print out of the `abort_on_tag_at_head` guard on the release
path so the flag downgrades exactly the one refusal and nothing else — which
is what the SPEC, the RELEASE-TRANSACTIONS text, and the flag's own new test
name (`test_allow_tag_at_head_downgrades_exactly_the_ahead_refusal_and_nothing_else`)
all claim.

### C2 — Both new refusals surface as raw Python tracebacks in the child, and every plan-time refusal retains a do-nothing worktree

Measured (`/tmp/cmru-a-review/e9`): with a local-only tag at HEAD, the child
(`release --_transaction-child --dry-run`) lets the `RuntimeError` escape
`cli.main` uncaught — in production that is a traceback on stderr and exit 1
from the `cmru` subprocess. The parent's `rc != 0` branch (cli.py:2306-2313)
then prints `Release transaction failed; retained <path> on branch <branch>
for inspection/resume` and keeps the worktree — for a refusal computed before
anything ran, whose worktree holds nothing worth inspecting. Each KI-12
refusal therefore *generates* one retained worktree (and, until KI-15 lands,
the spurious `error: unable to delete … remote ref does not exist` noise),
compounding the accumulation KI-16 is trying to make manageable. KI-12's own
spec sketch shows a formatted `[ERROR]` block, not a traceback. Fix: wrap the
child's plan computation (`detect_changed_projects` call, cli.py:2330) in
`try/except RuntimeError` → `log_error(str(exc)); sys.exit(2)`; ideally give
plan-stage refusals a distinct exit code the parent maps to "abort, nothing
retained".

### C3 — The tracker was not updated: KI-12 as written still specifies the dangerous rule this change deliberately rejected

`git status` shows `KNOWN_ISSUES_TODO_BACKLOG.md` untouched; `grep -in
correction` over the file and the full diff finds no CORRECTION blockquote
anywhere in the tree. KI-12 and KI-16 are still `*open*`, and KI-12(b) still
reads "a tag **at or ahead** of the snapshot … detect, warn, **abort**" with
the sample error advising `git tag -d assay-v2.1.0` — advice that would
delete a legitimate pushed release tag in the ordinary just-released state.
Anyone re-reading the tracker as specification (as this review was directed
to) re-derives the wrong rule. Per the estate's own docs-merge-with-change
rule, the entries must be corrected in the same merge: record the three-state
correction in KI-12, mark both entries shipped, and keep KI-16's *unshipped*
half — the retention policy / `cmru gc` sweep ("pair it with a retention
policy") — tracked as open, since no gc/age-sweep exists in this change
(grep over cli.py: no `gc` verb, no worktree age cleanup; only the
pre-existing asset `--remove-assets`).

### C4 — S12.2a overclaims: a newer tag existing only on origin is invisible to the plan, and the transaction's own fetch does not bring it in

Measured (`/tmp/cmru-a-review/e8`): clone B releases `demo-v1.1.0` (commit +
tag pushed, main promoted). Clone A (holding only `demo-v1.0.0`) runs exactly
the transaction's fetch, then the guarded plan at the fetched snapshot:

```
$ git fetch --prune origin main        # fetch_origin_main's exact command
   f505dab..e62cd2b  main -> origin/main
$ git tag --list 'demo-v*'
demo-v1.0.0                            # v1.1.0 NOT auto-followed (git 2.55)
PLAN: [('demo', 'demo-v1.0.0', 'minor')]   # → would mint demo-v1.1.0 again
```

The plan proposes the version origin already has; the run would tag locally,
then fail at tag-push *after* promotion — manufacturing exactly the
"half-completed release" state S12.2b aborts on. This gap is pre-existing
(not a regression of this change) and structurally out of reach of the
chosen "verify the locally-selected candidate" design — but S12.2a's bold
sentence claims more than that design can deliver. Either adopt the
backlog's other option (derive the baseline from `git ls-remote --tags
origin "refs/tags/<prefix>*"` — same per-project call count as today's
per-tag check, and it closes B1 for free), or scope the SPEC sentence to
what is actually guaranteed ("the locally selected baseline must exist on
origin [as the same object]; a newer origin-only tag is out of scope") and
note the residual gap. Recommendation: the ls-remote-derived baseline; it is
the single change that retires B1, C4, and the per-tag network round trip
together.

---

## 3. Non-blocking findings

* **N1 — No timeout or prompt guard on `git ls-remote`** (version.py:90).
  A hanging remote or an interactively-prompting credential flow blocks the
  release indefinitely; nothing can *silently* swallow the failure (every
  non-0/2 exit raises — verified 0/2/128 empirically, below — and in
  non-interactive contexts git fails rather than prompts), but "cannot hang"
  is not guaranteed. This matches the tool's existing posture — `grep
  timeout=` over `transaction.py`/`version.py` finds none on any fetch/push
  either — so it is consistency, not a regression. An unscoped plan now
  makes up to 7 sequential remote round trips (one per project); batching
  into one `ls-remote --tags origin` call (see C4) removes 6 of them.
* **N2 — Unrelated histories are classified "behind"** (measured,
  `/tmp/cmru-a-review/e2`): a tag on an orphan commit (neither ancestor nor
  descendant) → `_tag_head_relationship` returns `behind` → silent skip.
  Only reachable when the path-scoped log is *also* empty across disjoint
  histories (degenerate — a project whose paths have no commits in HEAD's
  history), and the silent skip equals the old behaviour, so: docstring/SPEC
  nuance only. S12.2b's "exactly one of three states" is not exhaustive; say
  "on a related history" or name the fourth case. The last sentence of
  `_tag_covers_head`'s docstring ("Also true, trivially, for the ordinary
  … opposite … being False") is not parseable English; rewrite it.
* **N3 — `git worktree add` adopts a pre-existing EMPTY directory** rather
  than failing (measured: rc=0 on git 2.55, `/tmp/cmru-a-review/e5`).
  S-CLI.5b's "`git worktree add` itself fails closed if the target path is
  somehow already taken" and the new collision test only hold for a
  *non-empty* path. Nothing is clobbered by adopting an empty dir, so this
  is a wording/test nuance: say "non-empty", or pre-check `path.exists()`
  and refuse.
* **N4 — Mutation survivor: nothing pins the timestamp's format or UTC.**
  `_BRANCH_RE`'s `\d{8}-\d{6}` accepts a `%d%m%Y` mutant and naive local
  time equally; chronological sortability — the point of KI-16 — is asserted
  by no test. Add one comparing the branch's timestamp prefix against
  `datetime.now(timezone.utc)` within tolerance.
* **N5 — `release_cmd` re-runs *unguarded* detection inside the guarded
  transaction** (version.py:509, reached from `_release_projects_sequentially`
  and the dry-run path). Today it is only exploitable by a tag appearing
  between the guarded plan and the per-project tagging (the worktree shares
  refs with the operator's repo, so a mid-run hand tag in the main checkout
  is visible instantly). Prefer passing the guarded plan down (or the same
  kwargs) so the plan that gates is the plan that tags.
* **N6 — Equal-state double listing** (measured, e9): the project appears in
  both the informative line and the generic `Unchanged, skipping: demo`
  line. Cosmetic; folding the reason into the one list line is also exactly
  KI-13's requested fix.

---

## 4. What I tried to break and could not

* **The real estate, today** (read-only, `GIT_TERMINAL_PROMPT=0 timeout 30`):
  for all six tagged projects the local max-semver tag exists on origin
  **with an identical SHA** (`ciu-v6.0.3` 4a9d84bb…, `cmru-v4.0.1` c20e5f8d…,
  `assay-v2.1.0` e7557788…, `topos-v0.2.0` eea69af3…, `pwmcp-v1.62.0-r1`
  d7d2f2fc…, `tls-edge-v1.2.1` dd43abd1…); `modern-debian-tools-python-debug`
  (git_tag=false, in project_order) has **no** prefix tags, so it takes the
  first-release path that never touches origin (its own unit test:
  `test_latest_tag_for_prefix_require_pushed_first_release_needs_no_remote_check`).
  No project in the estate trips either new refusal right now, and B1's
  precondition (name collision with object mismatch) does not currently hold
  anywhere.
* **A legitimate state tripping a new refusal** — hunted specifically, per
  the controller's prediction, and did not find one: equal never aborts
  (measured end-to-end); "ahead" aborts are genuine (half-completed release,
  or a concurrent release promoted after our fetch — in which case aborting
  a stale snapshot is correct and the message's "re-run once origin/main
  includes that commit" is the right remedy); `--project`-scoped runs cannot
  be aborted by an unscoped project's state (plan computed over
  `scoped_for_plan` *before* detection; new wiring test pins it);
  `--allow-tag-at-head` cannot mask an unrelated project's require-pushed
  refusal (dedicated test); `--resume` cannot manufacture "ahead" (a prior
  attempt's tag is always on the retained branch's own history → equal or
  behind); `--project` is validated (`Unknown or non-orchestrated project`,
  exit 2, cli.py:2175) before `scoped_for_plan` indexing, so no KeyError.
* **Lightweight vs annotated tags** (measured, e2): a lightweight pushed tag
  passes `_tag_pushed_to_origin` (True) and classifies correctly (`equal`);
  `^{commit}` peeling handles both.
* **ls-remote exit-code contract** (measured, git 2.55): found=0, absent=2,
  no-such-remote=128 — matches the code's 0/2/other split exactly; the
  no-origin case raises "cannot verify" (unit-tested), and in-child the
  raise propagates to a non-zero exit — loud (if ugly, see C2), never
  swallowed.
* **A tag whose object is missing/unresolvable**: `_git_log` swallows git
  failures (pre-existing), but the new classification then runs
  `rev-parse <tag>^{commit}` / `merge-base` which raise loudly — on the
  release path this is an *improvement* over main's silent empty-skip.
* **Naming fallout beyond the branch** (grep over `src/`, `tools/`,
  `templates/`): nothing parses the old `<12-hex>` token; log/artifact
  coordinates are tag- or commit-based (`logs/cmru-release/<tag>`,
  `<commit-date>_<full-commit>`); `_release_token` (scope/progress files) is
  the branch's last path component — unique under both schemes; refspecs and
  guards all match on the preserved `cmru/release/` / `cmru/build/`
  prefixes; both `create_workspace` call sites pass `scope`. Length: worst
  real project name gives a 71-char dirname, far under NAME_MAX; a
  hypothetical >220-char name would fail loudly at `git worktree add`.
  Scope sanitisation handles unicode, punctuation, whitespace-only and
  empty input (parametrised tests), and `/` cannot survive into a name.
* **SPEC texts vs built behaviour**: S-CLI.5b and S12.2a/b/c each checked
  sentence-by-sentence; the deviations found are exactly C1 (equal-state
  reporting conditioned on the flag), C4 (S12.2a's MUST overclaims), N2
  ("exactly one of three states"), N3 ("fails closed"), and S12.2b's
  parenthetical "(and is pushed — S12.2a already ruled out the unpushed
  case)" which, until B1 is fixed, is true of the name only. Everything
  else in the new sections matches the code as measured.
* **The new tests as a mutation target**: the three new files (51 tests,
  all passing here — control rc=0) use real repos with real bare origins,
  paired must-succeed controls throughout, and message-content assertions
  including the negative ones that matter (`"hand" not in`, `"tag -d" not
  in`); knob-wiring is pinned per-kwarg. The survivors I could construct are
  N4 (timestamp format/UTC) and the empty-dir nuance in N3 — nothing that
  inverts a behaviour.

---

## 5. Measurements (commands and real output)

All scratch repos under `/tmp/cmru-a-review/`; every git command against
`/workspaces` was read-only. Python probes ran with
`PYTHONPATH=/workspaces/vbpub/.worktrees/cmru-KI-12-16/cmru/src`.

**M1 — origin reachability control (real estate):**
```
$ GIT_TERMINAL_PROMPT=0 timeout 30 git -C /workspaces/vbpub ls-remote origin HEAD
a3ae580d7eb3e2d64c966f02a3ef528d3325346c  HEAD          # rc=0
```

**M2 — estate tag audit** (estate_check.py: `_latest_tag_for_prefix` per
configured prefix + `timeout 30 git ls-remote --exit-code --tags origin
refs/tags/<tag>` + local `rev-parse`): all six tagged projects rc=0 with
remote SHA == local tag object SHA (values in §4);
`modern-debian-tools-python-debug-v` → "(no local tag)".

**M3 — B1 (e1):** setup and output quoted in §1. Key lines: local
`demo-v1.0.0^{commit}` = 69969e2a…, remote `refs/tags/demo-v1.0.0` =
a29e264e… (peeled 04900b4d…), guarded `detect_changed_projects` printed
`already released as demo-v1.0.0 at the snapshot commit` and returned `[]`,
python rc=0.

**M4 — unrelated history (e2):** orphan branch, tag pushed there;
`_tag_head_relationship` → `behind` (rc=0).

**M5 — lightweight tag (e2):** `light-v1.0.0` (no `-a`) pushed;
`_tag_pushed_to_origin` → True; `_tag_head_relationship` → `equal` (rc=0).

**M6 — ls-remote exit codes (e2, git 2.55.0):** found `rc=0`; absent
`rc=2`; nonexistent remote `rc=128`. (Each `$?` captured directly after the
command, no pager in the pipeline.)

**M7 — empty-dir worktree add (e5, git 2.55.0):**
`mkdir -p .worktrees/cmru-release-…-deadbeef` then `git worktree add -b … <same path> HEAD`
→ `Preparing worktree (new branch '…')`, **rc=0** (adopted, not refused).

**M8 — C4 (e8):** second clone pushes commit + `demo-v1.1.0`, promotes main;
first clone runs `git fetch --prune origin main` (rc=0, `f505dab..e62cd2b`),
`git tag --list 'demo-v*'` still shows only `demo-v1.0.0`; guarded plan at a
worktree on the fetched snapshot: `PLAN: [('demo', 'demo-v1.0.0', 'minor')]`.

**M9 — C2/C1/N6 (e9):** real one-project orchestration + project config,
bare origin, child invoked in-process as
`cli.main(["release","--_transaction-child","--dry-run","--config",…])`.
With a local-only tag at HEAD: `RUNTIME_ERROR ESCAPED TO TOP LEVEL: latest
local tag 'demo-v1.0.0' … not present on origin …` (uncaught → traceback in
production). With the tag pushed at HEAD (equal): default flags print the
informative skip **plus** the generic `Unchanged, skipping: demo`; with
`--allow-tag-at-head` the informative line is absent. Intermediate config
refusals (`targets.registry is required`, `[steps] must … missing push`)
exited 2 cleanly, serving as the paired must-succeed controls for the
harness itself.

**M10 — tracker check:** `git status --short` shows
`KNOWN_ISSUES_TODO_BACKLOG.md` unmodified; `grep -in correction` over the
backlog, the full `git diff HEAD -- cmru/`, and `cmru/docs/` finds no
CORRECTION text; KI-12/KI-16 headers still read `*open*` and KI-12(b) still
contains the `git tag -d assay-v2.1.0` sample advice.

**M11 — new-test control:** `python3 -m pytest tests/test_ki12_release_plan_baseline.py
tests/test_ki12_cli_wiring.py tests/test_ki16_worktree_naming.py -q` →
`51 passed`; paired control run rc=0.
