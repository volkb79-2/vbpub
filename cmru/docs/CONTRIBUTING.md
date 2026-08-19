# Working on cmru — things that cost someone real time

`SPEC.md` is normative: it says what cmru MUST do. This file is the other half —
what actually bites you while changing it. Every item below was paid for once.
Add to it when something costs you an hour.

---

## 1. The gate that actually decides is the mutation campaign, not coverage

cmru gates itself with **100% statement and branch coverage, a changed-source
mutation campaign, and a cause-sensitive coverage canary**. Only the first is
cheap to run, and it is the weakest of the three.

**Coverage measures that a line ran. The campaign measures whether anything
would notice if that line were wrong.** These are not close to the same thing.
A real example from this repository: a change landed with 1607 tests passing and
100% statement *and* branch coverage — and the campaign found **six surviving
mutants**, including one in the single line that decides whether a pinned
version is stale.

Run it before you believe you are done:

```bash
cd <repo root>
export CMRU_WHEEL_BUILDER_IMAGE=wheel-builder:local \
       CMRU_TESTER_UNIFIED_IMAGE=tester-unified:local \
       CMRU_TESTER_MEMORY=3g CMRU_TESTER_MEMORY_SWAP=16g CMRU_TESTER_CPUS=1.5 \
       CMRU_TESTER_CGROUP_PROBE_IMAGE=debian:trixie-slim
cmru tester-gate --cwd cmru -- /bin/sh -ec 'mkdir -p .assay && exec /opt/tester-venv/bin/python \
  tools/mutation_campaign.py --assay-zipapp tools/assay/assay-1.0.0.pyz --repo-root .. \
  --project-root . --base origin/main --max-mutants 10000 \
  --evidence .assay/mutation-cmru.json --require-candidates \
  -- /opt/tester-venv/bin/python -m pytest tests -q --cov=src/cmru --cov-branch \
     --cov-fail-under=100 --cov-report=json:coverage.json' > /tmp/mut.log 2>&1
echo "MUTATION_EXIT=$?"
```

It is slow — a container start plus a full pytest run per candidate — so run it
detached and read `.assay/mutation-cmru.json`, which names every mutant, its
operator, and its exact `Op->Op` description. Read that file rather than
guessing why something survived; it is far faster than reasoning about it.

### Survivor pattern 1 — asserting an exception TYPE is not asserting a behaviour

Real case, `tool_deps.py`:

```python
if status >= 400:
    raise NetworkUnavailable(f"... returned HTTP {status}")
try:
    return json.loads(body)
except json.JSONDecodeError as exc:
    raise NetworkUnavailable("... unparseable response") from exc
```

The mutant `GtE->Gt` lets status **exactly 400** fall past the raise into
`json.loads`. The test fed a non-JSON body, so the mutated code raised
`NetworkUnavailable` **anyway** — from the other branch. The assertion
(`pytest.raises(NetworkUnavailable)`) passed, and the mutant lived.

**Rule:** when two paths raise the same exception type, assert something that
tells them apart — the message, or a body that makes the mutated path *succeed*
instead of raising.

### Survivor pattern 2 — the equivalent mutant, which no test can kill

Real case, same file:

```python
if   pinned_key == highest_key:  ...   # current
elif pinned_key <  highest_key:  ...   # stale
else:                            ...   # ahead
```

`Lt->LtE` mutates the `elif` to `<=`. That `elif` is reachable **only** when the
keys differ, so `<` and `<=` are semantically identical there. The mutant is
*equivalent* — no assertion can distinguish it, and the gate demands every
candidate die.

**Fix the shape, not the tests:**

```python
if   pinned_key < highest_key:  ...    # stale
elif pinned_key > highest_key:  ...    # ahead
else:                           ...    # current
```

Now each comparison discriminates, an equal pin moves the mutants into the wrong
branch, and the existing equality test kills both.

**The general rule: a redundant guard that makes an operator non-discriminating
is a defect, not a style choice.** It is the mirror of an unreachable branch —
an operator that cannot matter — and the campaign is what detects it, because
coverage cannot.

---

## 2. You cannot dry-run your own unreleased changes

`S-CLI.5` makes `release` fetch `origin/main`, create a worktree at **that exact
remote commit**, and re-exec there. That is deliberate and correct: a release
must not publish from a dirty caller tree.

The consequence for a cmru contributor is easy to miss: **`cmru release
--dry-run` from your branch runs `origin/main`'s cmru, not yours.** It will
happily report success while exercising none of your changes.

To integration-test unreleased cmru changes, use the read-only verbs, which run
in-process:

```bash
PYTHONPATH=cmru/src python3 -m cmru.cli status       --config ./cmru.orchestration.toml
PYTHONPATH=cmru/src python3 -m cmru.cli dependencies --config ./cmru.orchestration.toml
PYTHONPATH=cmru/src python3 -m cmru.cli tool-deps    --config ./cmru.orchestration.toml
```

and call the plan computation directly for anything guarded (see
`version.detect_changed_projects`'s release-path keywords).

---

## 3. A gate step copied from `cmru.toml` does not run standalone

The `argv` entries in a project's `[steps.*]` depend on environment the
**orchestration** layer injects from `cmru.orchestration.toml`'s `[env]` —
images, memory, CPU limits, the cgroup probe image. Running a step by hand, the
way you do when a release goes red, fails one missing variable at a time, each
costing a container spin-up.

Export the whole `[env]` block before reproducing a step; §1's snippet shows the
current set. Tracked as **KI-17**.

---

## 4. Read the exit code from the job, never from the wrapper

A backgrounded or compound invocation reports **its own** status, not the
status of the thing you care about. During this work, two runs of the mutation
campaign that never executed a single mutant were reported as `exit code 0`;
the truth was in the log:

```bash
<long running thing> > /tmp/x.log 2>&1
echo "EXIT=$?" >> /tmp/x.log        # append the marker yourself
grep EXIT /tmp/x.log                # and read THAT
```

Never `cmd | tail` and then read `$?` — that is `tail`'s status.

---

## 5. The defect shape this codebase keeps producing

Four separate defects in one change set were the same thing: **a check that
verifies something narrower than its message claims.**

| the check did | the message claimed | the effect |
|---|---|---|
| a tag **name** exists on origin | the release baseline is verified | a local tag sharing a published name at a different commit certified a false baseline and silently dropped real work |
| `prefix + "-v" + version`, where `prefix` already ended in `-v` | "no release exists yet" | a real pin reported as the benign bootstrap state |
| HTTP 404 on the release **list** | "nothing published yet — bootstrap" | an inaccessible/renamed/private repo stopped being checked forever, fail-open |
| tag is at **or ahead of** the snapshot | "a tag was created by hand" | fired on the ordinary just-released state, advising `git tag -d` on a real release tag |

**The habit that finds these: for every status your code can report, list the
distinct real-world conditions that collapse into it — then check the benign one
does not get misfiled as the alarming one, and vice versa.** Each of the four
was found by constructing the *legitimate* state and checking it was not
misreported. None was found by reading the code.

A corollary for any new refusal, and cmru has several that block a release:
**construct the legitimate state that would trip it before you ship it.** A gate
that errors by default and cries wolf on a healthy estate gets switched off, and
then it protects nothing.

---

## 6. Three techniques, three different defect classes

None of these finds the others' defects. Budget for all three on anything that
changes release behaviour:

* **Driving the real surface** caught a refusal that fired on a legitimate state.
* **Adversarial review** caught the checks that were narrower than their messages.
* **The mutation campaign** caught assertions that executed the right code and
  proved nothing about it.
