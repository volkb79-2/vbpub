# P69 review (frontier pass #2) - Web UI scoping and analysis

**Verdict: MERGE after review-fixes** (commit `b86ad6e` on the branch).
Docs-only, as required: the diff touches `groop/docs/**` and
`groop/handoff/reports/**` and not one line under `src/`.

## The DECISIONS-INBOX disposition (the thing this review had to decide)

Pass #1 loudly flagged that the implementer created `groop/docs/DECISIONS-INBOX.md`
and filed D-001..D-003 itself, violating workflow §8 ("Implementer ideas stay in
REPORTs; only the reviewer promotes them to the inbox"). It added a PROCESS
WARNING banner to the top of the file and left the disposition to me.

**Disposition: ADOPTED as reviewer-promoted, all three entries, content intact.**

The violation is real, but **the fault is the carve, not the implementer.** P69's
Deliverable 3 explicitly instructed the agent to write inbox entries and even told
it to create the file if absent. It followed its brief. Faithful execution of a bad
instruction is a carving bug to fix upstream, not analysis to throw away.

I reviewed the three entries on their merits rather than on their provenance:

- **D-001** (v1 web stack): a genuine product call. Its load-bearing technical
  claim -- that a `groop[web]` extra *cannot* make same-wheel package data
  conditional, so static assets grow the plain `pip install groop` wheel -- is
  correct, and it is the claim the recommendation turns on.
- **D-002** (who may view browser telemetry, and who authenticates them): the
  right question, and the one that actually gates P67.
- **D-003** (is the web UI a v2-tag requirement): correctly framed as a release-
  scope call, with a phased recommendation.

Each carries options with trade-offs, a recommendation (not just options -- oracle
4), context pointers, and a resume prompt. Discarding them to satisfy a process
technicality would have destroyed real analysis and left the user with three
undocumented open questions.

Changes I made in adopting them:

- **Removed the PROCESS WARNING banner.** The audience for this file is the user
  picking up an OPEN decision; a header shouting about an internal workflow breach
  is noise to them. The breach belongs in this review report, which is where it
  now lives.
- **Re-attributed `raised-by`** from the implementer to the reviewer (naming P69's
  analysis as the origin), so the file's provenance is honest rather than
  laundered.
- **Rebuilt the header against the actual reference schema.** The implementer
  invented a header missing the how-to-use and who-may-file sections, because it
  could not read the reference -- the handoff pointed at the repo-relative path
  `dstdns/docs/ai-dev/DECISIONS-INBOX.md`, which does not exist in this repo. It is
  a *sibling workspace* (`/workspaces/dstdns/...`). I read the real file and
  restored both missing sections.

## Findings

1. **`flagged-by-pass-1: yes` - inbox process violation.** Disposed of above. Pass
   #1 could flag it but structurally could not resolve it: only the reviewer can
   promote. Credit where due -- it did not quietly ship the violation.

2. **`flagged-by-pass-1: no` - the carve bug is still live.** The prior review
   commit diagnosed the root cause in its message but did not fix it anywhere a
   future carver would look. Any carver can write another "produce DECISIONS-INBOX
   entries" deliverable tomorrow. Fixed on `main` by adding an explicit prohibition
   to the handoff-authoring guide in `groop/README.md`.

3. **`flagged-by-pass-1: no` - the handoff cited an unreadable reference path.**
   `dstdns/docs/ai-dev/DECISIONS-INBOX.md` is repo-relative and does not resolve.
   This is why the schema came out wrong. Noted in the authoring-guide fix.

4. **`flagged-by-pass-1: no` - curly quotes / non-ASCII** in the new docs. Main's
   docs contain zero curly quotes; standing hygiene contract is ASCII by default.
   De-smart-quoted.

5. **`flagged-by-pass-1: no` - README work-package row** left at `Queued`. Folded
   into the reviewer's merge-hygiene commit on `main`.

## Citation audit (the package's falsifiability oracle)

P69's Required Contracts demand that *every* claim about the daemon surface carry a
`file:line` pointer into merged code, because "an analysis that restates the docs is
worthless". That makes citation accuracy the thing worth auditing. I spot-checked
the load-bearing ones:

| Claim | Cited | Verified |
|---|---|---|
| Exactly five capabilities | `api.py:54-75` | `CAPABILITIES = ("hello","current","history","entity","health")` |
| 4 MiB response cap | `api.py:61-63` | `DEFAULT_MAX_RESPONSE_BYTES = 4*1024*1024` |
| Default authz hook is a no-op allow | `api.py:177-183` | `_default_auth_hook(...) -> return None` |
| `request_health` is legacy, non-envelope | `client.py:161-178` | confirmed |
| 16 KiB health response cap | `client.py:182-196` | `MAX_HEALTH_RESPONSE_BYTES` |
| Process list is read locally, not over the API | `drill.py:318-329` | `_process_block(cgroup_root, ..., proc_root)` |
| Socket is `0660 root:groop` | `DAEMON.md:15-21` | confirmed |

Seven for seven. The analysis is genuinely grounded in the code, not in the docs --
which is the entire reason this package existed.

Acceptance oracles 1-5 are met: it names concrete gaps with citations (no server
push; no projection/downsampling; no typed versioned `health` in the P63 client; no
process endpoint), every page maps to named ops with a response budget at 89
entities, and it returns a concrete verdict on P67 rather than a shrug.

## The finding that matters downstream: P67 is not dispatchable as written

P69's central conclusion is that **P67's carved handoff puts auth/TLS out of scope
and only requires an ephemeral loopback test**, which is not a production trust
contract for a package whose entire purpose is to open an HTTP port onto a
daemon whose current boundary is a `0660 root:groop` Unix socket. It specifies four
contract groups P67 must add (safe bind default, authn + redaction ceiling,
origin/CSRF discipline, read-only routing enforcement).

I agree, and this is the highest-value output of the package. **P67 must not be
dispatched in its current form.** Acted on in this wave's carve: P67's handoff is
re-carved with those contracts before it goes anywhere near a worker.

## Gates

Docs-only, so the oracle is that the suite is *unchanged*, which I verified from
`main` rather than trusting the branch:

```
main (baseline)              1101 passed, 2 skipped in 137.20s   (clean venv, py3.14.6)
diff scope                   groop/docs/** + groop/handoff/reports/** only, 0 source files
git diff --check             OK
```
