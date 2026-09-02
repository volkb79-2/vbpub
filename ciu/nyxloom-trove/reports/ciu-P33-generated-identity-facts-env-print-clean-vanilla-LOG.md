# ciu-P33 — generated identity facts into the worktree overlay, `ciu env print`, `ciu clean --vanilla`

**Handoff:**
`nyxloom-trove/handoffs/ciu-P33-generated-identity-facts-env-print-clean-vanilla.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `9ea11db5` (the
handoff's own stale-backlog-number correction), confirmed with
`git status --porcelain && git log --oneline -3` before any edit — tree was
clean, HEAD as briefed.

**Status: COMPLETE — two ACCEPT-conditional review rounds, all fixes applied
(§4b), one claim in this LOG retracted as measurably wrong (§4b.1).**
All seven oracles met. **3261 tests pass; coverage 100.00% line + branch**
under the real gate (`.venv/bin/python run-ciu-tests.py`).
No `scope.forbid` file touched. No `escalate_if`
condition hit — both were evaluated explicitly against the shipped code and
neither fired (§3). One design refinement was made INSIDE the mandated
algorithm rather than around it, because the mandate as written lost a byte
O2 demands be kept; it is the first thing a reviewer should look at (§4.1).

**The single most important fact for review, stated once, up front:**

> The write touches ONLY the bytes between `[ciu.instance.generated]` and the
> next table header — and not even all of those. Everything else in
> `ciu.global.worktree.toml.j2` survives byte for byte, including comments,
> blank-line spacing, inline trailing comments and unrelated tables, before,
> between and after the block. This was verified not only in tests but
> against the real shipped CLI in a scratch workspace (§7).

---

## 1. Reading, before any code

The handoff in full, then, in the order it names them — noting first that
**its line citations are stale** (the repo has moved since it was carved), so
each target was located by symbol, not by line:

- `src/ciu/workspace_env.py` — `GENERATED_IDENTITY_KEYS` / `REQUIRED_KEYS_CORE`
  (they are at 40–62 as cited, still accurate), and `generate_ciu_env`, cited
  at "around line 892" and actually at **892** — the one citation that held.
  Read its whole body to confirm all six values are in scope as locals before
  the `ciu.env` write: `network_values["REPO_NAME"/"INSTANCE_ID"/
  "DOCKER_NETWORK_INTERNAL"]`, `physical_root`, `repo_root`,
  `public_values["PUBLIC_FQDN"]`. They are. No `escalate_if` #1.
- `src/ciu/worktree.py` — `_worktree_overlay_text` at **813** (cited 518) and
  `_write_worktree_overlay` at **866** (cited 594). Read both closely: the
  `json.dumps` value formatting, the header-comment style, the
  `tmp` + `fsync` + `os.replace` durability pattern, and the
  refuse-if-exists guard the handoff correctly says must NOT be relaxed.
- `src/ciu/config_model.py` — `render_global_chain`'s
  `worktree_overrides_path` read at **600** (cited 492–501). Confirmed
  unconditional, by exact path, no S16 gating. O4 is therefore achievable
  with zero read-side change, exactly as the handoff predicted.
- `src/ciu/deploy.py` — `action_clean` at **3161**, `--clean`'s argparse
  registration at **3638** in the `Actions` group, the `Control` group's
  sibling modifiers (`-y`, `--ignore-errors`, `--dry-run`, `--strict`,
  `--no-preflight`) at 3654+, and the action dispatch at **3857**.
- `src/ciu/cli.py` — `clean`'s dispatch at **1751** (cited 1631), `_env_show`
  / `_env_generate` / `_wants_verb_help`, and the `_VERB_HELP` dict.
- `docs/SPEC.md` S3.1b (266) and S16.1a (3089, for prose style); S6.4/S6.4a
  (721/763); S10.1 (1351). `docs/CONFIG.md` file-role table (72) and the
  S16.1 worked example (~1001).
- `KNOWN_ISSUES_TODO_BACKLOG.md`, for the live next-free number (§2).

Also grepped for an existing shell-quoting helper before writing one, as O5
directs: the codebase uses stdlib `shlex.quote` in five modules
(`activate.py`, `cli.py`, `transport_ssh.py`, `workspace_env.py`). `env
print` is built on it (§4.3) rather than on a second escaping implementation.

## 2. The backlog number — the handoff's warning was live, and correct

The handoff suggests CIU-60 "or whatever the next free number actually is —
re-verify". Verified against both checkouts, live:

- This worktree's `KNOWN_ISSUES_TODO_BACKLOG.md` tops out at **CIU-58**.
- The shared `main` checkout's copy tops out at **CIU-59**, and its CIU-59 is
  the SAME `DEVCONTAINER_NAME`-duplication finding that is CIU-58 here — a
  concurrent filing on main renumbered it.

So the naive "next free" answer from inside this worktree (CIU-59) would have
collided on merge with an unrelated entry. **CIU-60 was confirmed absent from
both** and used; CIU-61 (the follow-up in §4a) was verified the same way.

The handoff's suggestion was right, but only by luck of being re-checked —
recording the mechanism here because it kept recurring: the reviewer
subsequently found the divergence had widened AGAIN across CIU-55–59 (main
independently gained another new CIU-55 filing), shifting the range by one
more. **The controller is reconciling this at merge time**, the same way the
P26/P27 merge was reconciled; nothing in this package depends on the
intermediate numbers, only on CIU-60/CIU-61 being free, which they were and
are. The durable lesson for this wave is narrower than "re-verify the
number": while a long-lived worktree and `main` are both accepting backlog
filings, the two files' ID spaces drift continuously and neither is
authoritative on its own.

## 3. The two `escalate_if` conditions — evaluated, neither fired

**#1 (a value only knowable after `ciu.env` is written).** Does not arise.
All six facts are locals in `generate_ciu_env` before the `ciu.env`
`write_text`, and the new call sits immediately after that write reusing
those same locals. Nothing is re-derived; `ciu.env` is never read back. This
is asserted, not just claimed —
`test_generate_never_re_reads_ciu_env_to_build_the_table` spies on
`parse_workspace_env` and requires **zero** calls during a generate, and
`test_generate_writes_the_six_generated_keys` asserts the two records AGREE
key by key, which is exactly the property a second derivation could not
guarantee.

**#2 (a construct text scanning cannot handle).** The pathological case is a
line reading exactly `[ciu.instance.generated]` inside a multi-line TOML
string (`"""…"""`) elsewhere in the file. It is *representable* but cannot
occur in a sparse override of the shape S3.1b documents, and the only
alternative — a full-file round-trip — fails the far more likely case (an
operator comment) that O2 exists to protect. Shipping per the mandate, with
the limit stated explicitly in `upsert_generated_facts`' docstring rather
than left implicit. Flagging it here so the reviewer can overrule if they
disagree with that trade; it is a judgment call, not an oversight.

## 4. Design decisions worth reviewing

### 4.1 The mandated region rule loses a byte O2 requires — narrowed by one clause

**This is the one place the implementation is not a literal transcription of
the design mandate, and it is deliberate.** The mandate says: delete from the
header line "up to (not including) the next line that starts with `[` at
column 0". Implemented exactly that way first, then smoke-tested against a
file shaped like O2's fixture. Result:

```
[ciu.instance.generated]
...six keys...

# a trailing hand-written comment      <-- SILENTLY EATEN
[topology.services.vault]
```

That comment is inside the mandated region, so a literal implementation
destroys it — while O2 says a hand-written comment "placed BEFORE, BETWEEN,
or AFTER where `[ciu.instance.generated]` will be inserted" must survive
BYTE FOR BYTE. The mandate and the oracle disagree, and the oracle is the
contract.

Narrowed by exactly one clause: the region is the mandated span **minus its
trailing run of blank/comment lines**. That run is provably never ours — the
last line this writer emits is always a `key = value` — and in TOML's
ordinary reading a comment immediately above a table header belongs to that
header. It is also idempotent: the same run is re-detected and re-emitted
unchanged on every later run, which
`test_content_before_between_and_after_the_block_all_survive` asserts by
re-running the upsert and requiring a byte-identical file.

No other deviation from the mandate. Table name, location, the six
snake_case key names, `json.dumps` value formatting, `print` over
`apply`/`source`, and `--vanilla` being additive-only are all as specified.

### 4.2 The banner comment lives INSIDE the block, not above it

O7 asks the table to carry CIU-52's "do not hand-edit" precedent. Placing
that banner above the header would have made it un-owned — the replace starts
AT the header — so every `env generate` would have prepended another copy,
growing a duplicate banner per run. Below the header it is inside the owned
region and is replaced with itself.
`test_the_owned_block_names_itself_as_ciu_owned` runs the upsert twice and
requires exactly one occurrence.

### 4.3 `env print` always single-quotes, built on `shlex.quote`

`shlex.quote` returns shell-inert values bare, so a literal reading of O5's
`export KEY='value'` shape would not hold for e.g. `IS_NATIVE=0`. Rather
than write a second escaping implementation (O5 explicitly says reuse the
existing helper), `_shell_export_value` calls `shlex.quote` and wraps the
untouched case in single quotes. That is lossless — `shlex.quote` returns
the input unchanged ONLY when every character is shell-inert — and gives one
uniform, eyeball-able line shape. Parameterised over five inputs including
`""`, `it's` and `$(rm -rf /)`; and proved end-to-end through a **real bash
subprocess** running `eval "$(… env print)"` over a value containing both a
single quote and a command substitution
(`test_eval_of_env_print_populates_a_real_shell`).

### 4.4 `--vanilla` runs on SUCCESS only — a narrowing, stated for the record

O6 says `--vanilla` removes the three files "after doing everything plain
clean already does". Implemented as: after everything, **and only when
everything succeeded**; a failed clean keeps all three and warns, naming
them. Reasoning, which mirrors the existing S16.9 lease-release decision
directly above it in the same function: `ciu.env` is the workspace identity a
retry and any manual cleanup resolve from, and CIU-46 made a missing/key-less
`ciu.env` a hard REFUSAL of clean's own enumeration. Deleting it over a
half-torn-down workspace would take away the record naming what is still
standing, and would make the retry impossible. Every observable O6 states is
still met on the success path.
`test_vanilla_is_skipped_when_the_teardown_failed` pins the behaviour, S6.4b
documents it normatively, and `--help` says it.

### 4.5 An OSError removing a present file fails the clean

O6 requires an already-ABSENT file to be a silent no-op, which it is (per
file, `FileNotFoundError` → `continue`). It says nothing about a file that is
present and cannot be removed; that is an error and sets rc=1, because a
`--vanilla` that left one standing did not do what it said. Same
never-fold-indeterminacy-into-success posture as the surrounding S6.4a
passes.

### 4.6 `_atomic_write_text` uses `"w"`, not `_write_worktree_overlay`'s `"x"`

Same pattern (temp + `fsync` + `os.replace`), one mode difference. The
worktree writer runs once, at instance creation; this one runs on every `env
generate`, so a temp file left behind by a crash under the same PID must not
wedge every future generate. Noted in the function's docstring.

### 4.7 `config_model.py` gains a comment, no code

O3's whole point is that no read-side code is needed. The only change there
is a comment at the `worktree_overrides_path` read recording that this seam is
the entire read path for `[ciu.instance.generated]` and that there is
deliberately no Jinja injection anywhere. `test_the_facts_are_not_injected_as
_a_bespoke_context_field` guards the negative: delete the FILE, and the facts
are gone from the merged config — which would not be true of a context
injection.

## 4a. One out-of-scope file touched: `.gitignore` (and why it had to be)

**`.gitignore` is not in `scope.touch`. It is also not in `scope.forbid`.**
It was edited, one entry, for a reason that only surfaced after the change
worked:

`ciu env generate` now writes `ciu.global.worktree.toml.j2` unconditionally.
The integration suite (`tests/tests/test_ciu_test_repo.py`) deliberately
bootstraps against the **committed** `test-repo/` fixture — seven of its
tests do — so the first full gate run left an untracked
`test-repo/ciu.global.worktree.toml.j2` in the working tree. That file is
declared gitignored by SPEC S3.1b **and** by CIU's own published
`.gitignored.ciu` consumer sample rules, but CIU's own `.gitignore` never
listed it: nothing wrote it at a tracked repo root before, because
`_write_worktree_overlay`'s only call site is worktree creation into a fresh
checkout. Left alone it would red the dirty-tree gate (S18.4) on every run
and invite an accidental commit of this machine's host paths.

Added `**/ciu.global.worktree.toml.j2`, with the reasoning in a comment.
This makes CIU's own repo match its own SPEC and its own published rules; it
is a correction of an omission the change EXPOSED, not new behaviour.

The consumer-facing half of the same omission — `ciu init`'s
`_GITIGNORE_ENTRIES` in `scaffold.py`, which lists four entries where
`.gitignored.ciu` lists eight — is **out of scope** (`scaffold.py` is not in
`scope.touch`) and is filed as **CIU-61** rather than fixed here. It now
matters more than it did: a freshly scaffolded consumer repo will gain an
untracked file carrying that developer's `physical_repo_root`, instance id
and public FQDN.

Flagging both explicitly for the controller's ruling: the `.gitignore` edit
can be reverted if it should have been an escalation instead, but the gate
does not pass without it.

## 4b. Review fixes (ACCEPT-conditional, applied on top of `0854af81`)

Both findings were correct and are fixed. Neither changed a design decision;
both were the implementation failing to match what the package already
claimed.

### 4b.1 `_env_print`'s third failure mode escaped as a traceback

`parse_workspace_env(env_file)` sat OUTSIDE the function's own `try`, so a
malformed `ciu.env` — a hand-edited line, an unquoted value — produced a raw
`WorkspaceEnvError` traceback, while the two adjacent failure modes in the
same function (unresolvable root, absent file) both returned a clean
`[ERROR] …` and exit 1. Three failure modes, two presentations. This wave has
held new verbs to exactly this bar before (P16's `ciu status`).

The parse now sits inside a `try`, so all of them are identical to an
operator. Verified against the real CLI:

```console
$ ciu env print
[ERROR] Invalid ciu.env entry: 'this is not a valid entry'
rc=1
```

**Correction (second review round).** The first version of this section — and
the commit message of `ef275e01` — claimed a second benefit that does not
exist: that printing was previously "interleaved" with parsing, so a
malformed entry halfway down used to leave a partial environment on stdout.
That is wrong. `parse_workspace_env` returns a fully-built `Dict`, so the
pre-fix `for key, value in parse_workspace_env(env_file).items():` evaluated
the call to completion before the loop began; a malformed file printed
nothing then either. Measured directly against the pre-fix expression to
confirm rather than re-reasoned: **0 bytes** of stdout.

The empty-stdout assertion in
`test_env_print_reports_a_malformed_ciu_env_without_a_traceback` is therefore
a **regression PIN, not evidence of a fix** — worth keeping, because it would
catch a future refactor of `parse_workspace_env` into a generator, which
genuinely WOULD start leaking half an environment into
`eval "$(ciu env print)"`. The test docstring now says so explicitly, so the
next reader is not misled the way this LOG was. The only real change in this
finding is the traceback → `[ERROR]` one.

### 4b.2 `upsert_generated_facts`' docstring overstated what CIU owns

The docstring still described the UN-narrowed algorithm ("up to (not
including) the next line beginning a table at column 0") — the version §4.1
records replacing, because it eats a comment written below the block. The
inline comment at the `keep` loop was correct; the docstring was the stale
copy, and it overstated CIU's blast radius, which is the one direction a
docstring about this function must never be wrong in.

Rewritten to state the actual boundary: from the header line up to and
including the last line **this writer itself emits**, with the trailing
blank/comment run walked back over and carried across untouched. The
"Behaviour" list's third bullet was corrected in the same pass (it also
described only the blank-separator case). No code change.

### 4b.3 "All THREE failure modes" was not exhaustive (second review round)

The §4b.1 comment and test docstring both claimed `ciu env print` returned a
clean `[ERROR]` for all its failure modes. It did not. Three more still
raw-tracebacked, all reachable by an ordinary operator:

| Case | Why it escaped |
|---|---|
| `ciu.env` is a **directory** | the guard was `exists()`, which is true for a directory; the read then raises `IsADirectoryError` |
| `ciu.env` is **unreadable** | `PermissionError` — an `OSError`, never in the except clause |
| `ciu.env` has a **non-UTF-8 byte** | `UnicodeDecodeError` from `read_text(encoding="utf-8")` |

Fixed with the in-repo precedent the reviewer cited
(`worktree.py`'s `_resolve_budget_candidates`: `is_file()` plus
`except (OSError, WorkspaceEnvError)`) — **plus one addition, because that
precedent does not actually close the third case.** `UnicodeDecodeError`
derives from `ValueError`, not `OSError`, and `WorkspaceEnvError` is a
*different* `ValueError` subclass, so a non-UTF-8 byte escapes both arms.
Confirmed rather than assumed:

```
WorkspaceEnvError MRO: ['WorkspaceEnvError', 'ValueError', 'Exception', ...]
UnicodeDecodeError is OSError?           False
UnicodeDecodeError is ValueError?        True
UnicodeDecodeError is WorkspaceEnvError? False
```

So the except clause is `(OSError, UnicodeDecodeError, WorkspaceEnvError)`,
with `UnicodeDecodeError` named explicitly rather than folded into a bare
`ValueError` so the surprising reason it is listed stays visible.

Two structural notes on the fix:

- The `resolve_env_root` failure kept its **own** `try` and its own message.
  Folding it into one block with the read would have made a bad
  `--define-root` report as `could not read ciu.env: Repository root does not
  exist: …`, which names the wrong thing. Each error now says what actually
  failed.
- The print loop stays OUTSIDE the `try`. `parse_workspace_env` returns a
  complete dict (§4b.1), so nothing is lost by that, and it keeps a
  `BrokenPipeError` from `ciu env print | head` out of the error path, where
  it would be misreported as a `ciu.env` problem.

Three new/updated tests:
`test_env_print_reports_a_directory_named_ciu_env`,
`test_env_print_reports_an_unreadable_ciu_env` (the non-UTF-8 case — the one
that proves the precedent's tuple is insufficient), and the corrected
docstring on the malformed-entry test.

**Follow-up filed: CIU-62.** `worktree.py:_resolve_budget_candidates` has the
identical latent gap — it reads every registered *sibling* worktree's
`ciu.env` during `ciu up`'s S16.3 budget check, so one bad byte in any
sibling escapes as a traceback, defeating that function's own documented
promise of "a loud `[S16.3]` failure". The fix is the same one token. It was
FILED rather than applied: `worktree.py` is in `scope.touch`, but making an
unrequested behavioural change in a different subsystem during a
record-level fix round is how a clean round turns into another one. One word
from the controller and it is a one-line commit. Worth grepping the whole
repo for the same pair at that point — this is a class, not a site.

## 5. Files changed

**Source (5)**

| File | Change |
|---|---|
| `src/ciu/workspace_env.py` | `GENERATED_FACTS_TABLE/HEADER/KEYS` + two comment-block constants; `render_generated_facts_block`; `upsert_generated_facts`; `_atomic_write_text`; the new call at the tail of `generate_ciu_env` (+ its docstring) |
| `src/ciu/cli.py` | `_shell_export_value`; `_env_print`; `env print` dispatch; `_wants_verb_help` widened to `("generate", "print")`; `_VERB_HELP` `env` and `clean` entries |
| `src/ciu/deploy.py` | `VANILLA_RESET_FILES`; `_remove_vanilla_reset_files`; `action_clean(vanilla=False)` + its step and docstring; `--vanilla` argparse; dispatch forward; epilog example; two new `config_constants` imports |
| `src/ciu/config_model.py` | comment only (§4.7) |
| `.gitignore` | one entry, out of `scope.touch` — see §4a |

**Tests (2 new files, 55 tests)**

| File | Covers |
|---|---|
| `tests/tests/test_ciu_workspace_env.py` (40) | O1, O2, O3, O4, O5 |
| `tests/tests/test_ciu_deploy_clean_vanilla.py` (15) | O6 |

`tests/tests/test_ciu_workspace_env.py` did not previously exist despite
being named in `scope.touch`; created. `test_ciu_deploy_clean_vanilla.py`
matches the `test_ciu_deploy*.py` glob in `scope.touch`.

**Docs (7)** — `docs/SPEC.md` (S3.1b extended with five normative rules; new
S6.4b; S10.1 extended), `docs/CONFIG.md` (file-role row + a new
`[ciu.instance.generated]` worked-example section before the S16.1 one),
`docs/CIU.md` (both verb tables + the `ciu.env` section), `docs/CONSUMERS.md`
(what clean does NOT remove + `--vanilla`; new §11a on reading identity in
templates and shells), `docs/DESIGN-GUIDE.md` (new section, §6),
`CHANGES.md`, `KNOWN_ISSUES_TODO_BACKLOG.md` (CIU-60 row + detail, including
its `.gitignore` side-finding; plus the CIU-61 and CIU-62 follow-up rows,
CIU-61 with detail).

No `scope.forbid` file touched: `src/ciu/dev.py`, `src/ciu/engine.py`,
`src/ciu/composefile.py`, `nyxloom-trove/{backlog,decisions,roadmap}.md` are
all unmodified.

## 6. The DESIGN-GUIDE section is the answer to the operator's question

O7 asks for it to be recorded "as such", and it is: the new section names the
operator's question, states the asymmetry it exposes (hooks got S9.3,
templates never did), records that the bespoke-Jinja-global proposal was
**rejected and why**, and gives the two mechanical facts that decided the
destination — `ciu.global.toml` has no state preservation (only S3.4's
per-stack `[state]` does, so anything written there is regenerated away), and
`ciu.global.toml.j2` is committed (machine-specific host paths would reach
every developer). It sits immediately after the CIU-41 and CIU-53 ambient
sections, which is where a reader following that thread arrives.

## 7. Oracle-by-oracle evidence

| Oracle | Evidence |
|---|---|
| **O1** table written, from the same values, idempotent | `test_generate_writes_the_six_generated_keys` (six keys exactly; every one asserted EQUAL to its `ciu.env` counterpart), `test_generate_never_re_reads_ciu_env_to_build_the_table` (zero `parse_workspace_env` calls), `test_second_generate_is_byte_identical` (+ exactly one table header), `test_upsert_replaces_a_stale_value_without_duplicating_the_table`, `test_block_key_order_is_fixed_not_mapping_order` |
| **O2** byte-for-byte preservation | `test_hand_authored_content_survives_byte_for_byte`, `test_content_before_between_and_after_the_block_all_survive` (literal line-by-line assertions on the surrounding text, incl. a comment BELOW the block, plus a re-run byte-identity check), `test_a_full_toml_round_trip_would_have_failed_this` (four exact comment strings, incl. an inline trailing comment, asserted verbatim — the mutant-killer for a parse-and-dump shortcut) |
| **O3** facts reach templates via the existing merge | `test_generated_facts_land_in_the_merged_global_config`, `test_a_jinja_template_can_read_the_facts_like_any_other_value` (renders `{{ ciu.instance.generated.physical_repo_root }}` through `render_toml_template`), `test_the_facts_are_not_injected_as_a_bespoke_context_field` (negative) |
| **O4** main checkout covered | `test_primary_checkout_with_no_instance_record_is_covered` (asserts `read_own_instance_record(...) is None` first, then that the overlay is created from nothing), `test_write_does_not_consult_the_instance_record_at_all` (spy: zero calls — the negative), `test_a_worktree_instance_shaped_overlay_is_extended_not_replaced` |
| **O5** `env print` | `test_env_print_emits_export_lines_and_nothing_else` (+ empty stderr, + every `REQUIRED_KEYS_CORE` key), `test_env_print_has_no_side_effects` (byte-compares every file before/after), `test_env_print_refuses_loudly_when_ciu_env_is_missing` (rc=1, empty stdout, names `ciu env generate`), `test_env_print_reports_a_malformed_ciu_env_without_a_traceback` (§4b.1), `test_env_print_reports_a_directory_named_ciu_env` + `test_env_print_reports_an_unreadable_ciu_env` (§4b.3 — every failure mode is `[ERROR]` + rc=1 + empty stdout, none a traceback), `test_eval_of_env_print_populates_a_real_shell` (real bash), `test_shell_export_value_quoting` (5 cases), `test_env_verb_help_text_names_print_and_is_honest_about_eval` (asserts the words "apply"/"source" do NOT appear — O5's negative) |
| **O6** `clean --vanilla` | Default half: `test_plain_clean_leaves_all_three_files_untouched` (byte-compares), `test_plain_clean_is_the_default_when_vanilla_is_not_passed` (signature default), `test_plain_clean_prints_nothing_about_vanilla`, `test_main_defaults_vanilla_false_for_a_plain_clean`. New-flag half: `test_vanilla_removes_exactly_the_three_files` (+ asserts committed inputs survive), `test_vanilla_on_an_already_clean_workspace_succeeds`, `test_vanilla_tolerates_any_single_file_already_absent` (×3), `test_vanilla_reports_a_removal_that_failed`, `test_vanilla_is_skipped_when_the_teardown_failed`, `test_deploy_argparse_accepts_vanilla_and_defaults_it_false`, `test_main_forwards_vanilla_to_action_clean` |
| **O7** docs + `--help` | SPEC S3.1b/S6.4b/S10.1, CONFIG.md, CIU.md, CONSUMERS.md, DESIGN-GUIDE.md as listed in §5. `--help` pinned by test: `test_env_verb_help_text_names_print_and_is_honest_about_eval`, `test_cli_clean_help_documents_vanilla` (requires all three filenames), `test_env_print_help_is_reachable_and_names_eval` |

### Live verification against the real CLI, not only tests

A scratch workspace was seeded with a hand-authored overlay
(`# my own notes` / `[deploy]` / an inline trailing comment), then driven with
the installed `ciu` entrypoint:

- `ciu env generate` appended the block and left all three hand-authored
  lines byte-identical, inline comment included;
- a second `ciu env generate` produced an identical md5;
- `render_global_chain` on that directory returned the six facts, and a real
  Jinja template rendered `{{ ciu.instance.generated.physical_repo_root }}`
  to this workspace's own host path;
- `eval "$(ciu env print)"` in a real interactive-shell invocation set
  `REPO_ROOT` and `DOCKER_NETWORK_INTERNAL` correctly;
- `ciu clean --help` shows the `--vanilla` paragraph.

## 8. Gate output (read in a separate step from the run itself)

Final run, after the §4b review fixes:

```
$ .venv/bin/python run-ciu-tests.py
...
src/ciu/cli.py                                     841      0    304      0   100%
src/ciu/config_model.py                            336      0    166      0   100%
src/ciu/deploy.py                                 1627      0    706      0   100%
src/ciu/workspace_env.py                           509      0    202      0   100%
src/ciu/worktree.py                               1702      0    696      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             9688      0   3948      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 3261 passed in 19.07s =============================
```

3206 → 3261 tests (+55). Every one of the four modules this package touches
is at 100% line AND branch. Two intermediate iterations were needed to reach
it: the first full run was 99.96% with three uncovered spots — `cli.py:1617`
(the `env print` dispatch line, needing a test through the public
dispatcher), and in `workspace_env.py` the `1013->1015` branch (appending
after a file already ending in a blank line) and line `1036` (a generated
block butted directly against the next table with no separator). All three
now have named tests; **no existing test needed changing**, and nothing was
xfail'd or excluded. The §4b review fixes added three more tests and four
more statements to `cli.py` across two rounds; the gate stayed at 100.00%
through both.

## 9. Notes for the reviewer

1. **§4.1 first.** It is the only place the implementation narrows the design
   mandate, and the reason is that the mandate as written fails O2's own
   "AFTER" case. If the controller prefers the literal mandate, the change is
   one `while` loop.
2. **§4.4 second.** `--vanilla` on success only is a narrowing of O6's
   sequencing language, taken for the same safety reason the S16.9 lease
   release directly above it is gated. It is normative in S6.4b and in
   `--help`; if it should be unconditional, it is a two-line change and one
   test.
3. `escalate_if` #2 was resolved in favour of shipping with the limitation
   documented (§3). That is a judgment call about a construct that cannot
   occur in a conforming file; overrule if you disagree.
4. The CIU-60 numbering was verified against BOTH checkouts (§2), because
   the "next free" number inside this worktree would have collided. CIU-61
   (the scaffold follow-up) was verified free in both the same way. The
   CIU-55–59 divergence the reviewer found is the controller's to reconcile
   at merge; this package does not depend on those numbers.
5. **§4a — one out-of-scope file (`.gitignore`) was touched**, because the
   gate does not pass without it. Ruling welcome.
6. **§4b records the review fixes**, all applied. Round one: `_env_print`'s
   malformed-`ciu.env` traceback (now `[ERROR]` + exit 1) and
   `upsert_generated_facts`' stale docstring (now states the narrower,
   actual ownership boundary). Round two: §4b.1's "no longer prints
   partially" claim was **wrong and is retracted in place** (measured: the
   pre-fix code printed 0 bytes too — the assertion is a pin, not a fix),
   and §4b.3 closes the three failure modes that still tracebacked
   (directory, unreadable, non-UTF-8).
7. **§4b.3's last paragraph is the one thing still open.** The
   `(OSError, WorkspaceEnvError)` precedent I was pointed at does not itself
   cover `UnicodeDecodeError` (it is a `ValueError`, not an `OSError`), so
   the precedent SITE — `worktree.py:_resolve_budget_candidates`, which reads
   every sibling worktree's `ciu.env` during `ciu up` — has the same latent
   traceback. Filed as **CIU-62** rather than fixed unrequested. One word and
   it is a one-line commit.
