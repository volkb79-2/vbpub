# ciu-P47 — adversarial review verdict (ACCEPT-conditional)

Fresh reviewer, independent control worktree, independent gate run (PASS,
`8f174836`, R0/R1 both PASS, 100% line+branch, confirms the implementer's
own reported verdict). Full detail lives in the reviewer's own transcript;
this file is the durable, actionable summary for the fix pass.

**Verdict on the mechanism (C1/C2/C3): ACCEPT.** The reviewer specifically
tried to break the CIU-80-interaction judgment call (call 1) by mutating
`render_global_chain` to use the stricter reader and confirmed exactly one
test catches it — the claim held. Template-binding identity was verified
by DIFFERENTIAL EXECUTION (same fixture rendered on this branch vs. pre-P47
`main`, byte-identical output), not just inspection. `ciu migration-check`'s
C3 activation was verified end-to-end against the real production constant,
no monkeypatch. Judgment calls 2, 3 (partially — see B-note below), 4, 5,
6, 7, 8 all independently re-verified as correct. **Do not rework C1/C2/C3.**

One real quote worth keeping in mind for future packages: *"P47's
implementer did a real, documented 3-pass sweep specifically to avoid P46's
gap, and still shipped P46's exact defect class — because the sweep's
pattern list … does not match the prose that actually went stale."* Grep-
based sweeps miss synonyms and paraphrase; this is a durable lesson, not
just feedback on this one package.

## Blockers to fix

**B1 — `docs/SPEC.md`'s S3.1c clause 5 (~line 426-433) still describes the
DELETED text-region-scan reader.** It says the read is "scoped to CIU's own
block... the block is plain TOML by construction even though the file is a
Jinja template" — all false now (`generated_facts_document` is a whole-file
`tomllib.loads()` of a non-template file). Rewrite clause 5 to say a
whole-file plain-TOML parse of `ciu.instance.generated.toml`, keeping the
still-valid REASONS a chain render must not be required (targets another
checkout; merged last so its own bytes are the value). Also fix
`SPEC.md:398` and `SPEC.md:417-418`, which both say "overlay" where they
now mean the generated-facts file specifically (post-split, "overlay" =
the operator's hand-edited file only).

**B2 — `docs/DESIGN-GUIDE.md:227-241` describes the deleted reader
mechanism in the present tense**, four separate false statements in one
paragraph (the reader "slices to it," the file "is a Jinja template," the
CONSUMERS §11b helper "does the same" slicing — it was reworked in this
very package to stop doing that — and that operator content coexists in
that file). The WRITER narrative 26 lines above (172-201) was correctly
past-tensed with a good "why deleted" section; this reader paragraph was
missed. Past-tense it the same way, point forward to the new section.

**B3 — five stale "overlay" references in `src/` naming the wrong file
(P46's exact blocker class, verbatim), plus three in docs/tests:**
- `src/ciu/deploy.py:2236-2237`, `:3938`
- `src/ciu/worktree.py:2276`, `:2991`, `:4395`
- `docs/SPEC.md:1691` (S3.12)
- `tests/conftest.py:60-64` (`write_instance_facts` docstring — in the SAME
  hunk whose function call was already renamed)
- `tests/tests/test_ciu_worktree.py:1228`

All say "overlay" where they now mean the generated-facts file. Mechanical
fix, no behavior change: replace "overlay" with "generated facts file" /
`ciu.instance.generated.toml` at each site.

**B4 — real, functional: the merge-order precedence between the operator's
overlay and the generated-facts file is now unpinned by any test.**
`config_model.py:719-736` and the new SPEC.md S3.1b clause 5 both assert
generated facts merge AFTER the operator's overlay (so an operator can't
accidentally shadow a derived fact by hand-writing the same key). The
reviewer moved the merge line to BEFORE the overlay block, ran the full
suite, and got **3526 passed, zero failures** — nothing catches the flip.
Verified with a concrete before/after value: `instance_id` diverges between
"OPERATOR-WINS" and "DERIVED" depending on order, and nothing pins which
one ships. Pre-P47 this was structurally impossible to get wrong (one
file, one table, no ordering choice); the split introduced an ordering
choice in code with no guard. **Fix: add a test** in
`test_ciu_workspace_env.py`'s O3 section seeding both files with a
colliding `[ciu.instance.generated]` key and asserting `render_global_
chain` returns the DERIVED value, not the operator's. The reviewer
confirmed this test fails under the flipped order and passes as shipped —
write it, don't just take that on faith.

**B5 — the two new filenames are not gitignored at the vbpub REPO ROOT,
reintroducing assay's "B017 class" dirty-tree exposure.** `ciu/.gitignore`
correctly covers both new names (`**/ciu.global.instance.toml.j2`,
`**/ciu.instance.generated.toml`, measured via `git check-ignore -v`).
vbpub's ROOT `.gitignore:190-194` separately carries an un-globbed
`ciu.global.worktree.toml.j2` — added specifically for "B017 class, THIRD
occurrence (vbpub's own worktrees, 2026-08-29)" — which the rename silently
retires as a mitigation (the retired filename stays covered; the two new
ones aren't). **Resolved as a decision, don't re-ask**: fix this in THIS
package — add `ciu.global.instance.toml.j2` and `ciu.instance.generated.
toml` beside the existing line 194 in vbpub's root `.gitignore` (keep line
194 too, it costs nothing and still protects any leftover retired file).
This is a one-line mechanical extension of an already-established
mitigation pattern, not a new design decision — no need to file it
elsewhere or leave it dangling.

## Also fix (non-blocking, but unambiguous — no decision needed)

**N1** — `ciu/.gitignore:32-34`'s comment asserts a causal link
("the old spelling is NOT kept here BECAUSE a leftover copy is exactly
what `ciu migration-check`'s retired-overlay rule wants visible") that the
reviewer proved false by direct testing: the detector is a bare
`Path.exists()` check, gitignore state never enters it. This also
contradicts `migration_check.py`'s own docstring and CONSUMERS §21, which
both correctly say gitignore state and detection are independent. Restate
the comment as plain hygiene ("no longer a CIU-generated artifact"),
drop the migration-check causality claim.

**N2** — `docs/CONSUMERS.md` §11b's published consumer helper leaks a bare
`AttributeError` (instead of the documented `ValueError`) on two shapes
this package newly hardened CIU's own reader against: `[ciu.instance.
generated]` present as a non-table, and a non-dict ancestor. Add
`isinstance` guards on the `.get` chain; update the "all FOUR
indeterminacy cases" count in the docstring/§21 to match once fixed (or to
the correct count if you decide not to guard all of them — your call, but
state which).

**N3** — `docs/ARCHITECTURE.md:18`'s function inventory doesn't list
`write_generated_facts`/`generated_facts_document`/`generated_facts_path`.
Add them.

## Process for this fix pass

- Same worktree/branch you already have (`worktree-agent-a5db4950c58078004`).
- Fix B1-B5 + N1-N3 exactly as prescribed above. Nothing else.
- B4 requires a NEW test, not just a doc fix — the gate must go green
  again after adding it; re-run `./run-gate.py ciu --worktree <path>` (or
  equivalent for your tree), read the verdict in a separate step, never
  off a piped tail.
- B5 touches a file OUTSIDE `ciu/` (vbpub's root `.gitignore`) — that's
  expected and approved for this fix, not scope creep.
- Commit with the same trailer convention as your prior commits.
- Append to `nyxloom-trove/reports/ciu-P47-{LOG,REPORT}.md` (don't rewrite
  prior entries) describing this fix pass and its gate verdict.
- Do not merge. Report back the new commit hash(es) and gate verdict.
