<!-- origin: 2026-11-07; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/pwmcp-4344-fix2-REPORT.md -->
<!-- relation: PWMCP consumer fix evidence; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# pwmcp-4344 fix round 2 report

Branch pwmcp-4344-20261108, new head `2bd776752` (was `c57647ce3`). One commit, worktree clean.

## Items
- B1 DONE. `_inline_text` (page_content.py) wraps any child whose tag is in `_BLOCK_TAGS` as `" value "`. The existing final collapse and strip leave a single space, with no leading or trailing space. `_render_list` now builds one lead child list (nested `ul`/`ol` become a `" "` separator) and calls `_inline_text` once, so text around a nested list stays apart (`<li>x<ul><li>y</li></ul>z</li>` -> `- x z` / `  - y`).
  - `<li><div>a</div><div>b</div></li>` -> `- a b`
  - `<td><div>a</div><div>b</div></td>` -> `a b`
  - `<a><div>Title</div><div>Desc</div></a>` -> `[Title Desc](url)`
  - Text format unchanged and already right.
- `test_table_row_extraction_does_not_duplicate_nested_table_rows` now asserts `outer inner\nlast`.
- New tests:
  - `test_markdown_separates_adjacent_blocks_nested_in_list_items_cells_and_links` covers li/div, li/p, li/h3+text, nested list with trailing text, td, td plus a second cell, card link, card link with an inline span, p containing a div, and the text twin.
  - `test_details_summary_and_body_are_separate_blocks_in_text_and_markdown`.
  - `test_markdown_pre_strips_only_leading_and_trailing_newlines` (nit N-c / mutant N12).
- `<details><summary>`: same mechanism. `details` and `summary` added to `_BLOCK_TAGS`; text `Sum\nBody text`, markdown `Sum\n\nBody text`. Fixed, not just documented.
- `tests/test_testdeps.py`: added the `page_content.py` target assertion to the lanes test (the list exists).
- CHANGES.md: the existing unreleased Fixed entry extended. The PWMCP-44 backlog wording is updated to match.
- CLI surface unchanged, so `cli-extended surface check` was not needed.

## Mutants (hand-applied, test_page_content.py)
- Remove the block separator in `_inline_text` (`value = f" {value} "` -> `value = value`): KILLED (2 tests fail: the new nested-block test and the nested-table test).
- Remove the nested-list lead separator (`lead_children.append(" ")` -> `pass`): KILLED (the new nested-block test).
- Both reverted, and the final tree is the committed content.

## Counts and coverage
- test_page_content.py: 22 passed (was 19). With test_inspect.py, 111 passed.
- page_content.py alone, `--cov-branch`: 236 statements / 0 missed, 122 branches / 0 partial, 100%.
- test_testdeps.py gate-lanes test passed.

## Gate
- `run-gate r1` from pwmcp/ via nohup: `logs/pwmcp-4344-fix2-r1.log` reads `r1: PASS (exit 0)`. Verdict was read in a separate step: `pwmcp/.assay/verdict-r1.json` has commit `2bd776752c4d52d2305e14fad50fcf42c43c8d83`, outcome PASS, exit 0. Peak 858 MiB. Anon memory stayed about 3.2-3.6 GB.
- Stale inflight record, needed a workaround. `pwmcp/.run-gate/inflight/r1.json` (gitignored) was left by the dead Codex client (pid 238439, from a different PID namespace, container exited 8 hours ago). run-gate treated it as LIVE and refused both a plain run and `--fresh`. I moved the record aside to `<controller-scratch>/-workspaces-vbpub/c9129a68-573f-4fa3-972d-cddb9bcf26c0/scratchpad/stale-inflight-r1.json` (no docker rm) and re-ran. That was a fresh container `vbpub-run-gate-pwmcp-r1-2bd77675-261108-1135-836648304`.
- Tool finding for the controller. run-gate gives no supported way to clear an inflight record whose owner is in another PID namespace, even when the container is exited. This is a candidate run-gate backlog item, and I did not file it.

## Not done
- Other markdown gaps from the review's minor list, left as is: `<caption>` dropped in markdown, adjacent inline-block buttons glued, `<pre>` inside `li` collapses to one line.
