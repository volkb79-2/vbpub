# Tracing Claude Code's real auto-compaction API call with mitmproxy

Operational HOWTO for `design-context-lifecycle-experiments.md`'s `E-010`
(proposed, not yet run). Read that entry first for WHY this experiment
exists and what a result would change; this doc is only the HOW.

**What this is not**: `docs/research-external-compaction.md` already covers
the *documented* external compaction mechanism (Claude Code's `/compact
<instructions>`, resumed headless — Pattern (a) in
`design-context-lifecycle.md`). This experiment is about the *undocumented*
internal path: the real API call Claude Code's own binary makes when its
internal token-threshold auto-compaction fires, with nobody asking for it.
Different question, different mechanism, not a replacement for the /compact
work already validated elsewhere.

## 0. What we're trying to learn (one paragraph)

Whether a compaction API call is distinguishable from a normal turn by its
request body alone (a distinctive system prompt? a param? nothing at all,
just an unusually large transcript?), and whether its response is a normal
Messages API streaming shape we could plausibly fabricate later. Nothing
here modifies or blocks traffic — this pass is pure observation.

## 1. Prerequisites

- Debian 13 host, working inside the devcontainer (per session context).
- A terminal you are willing to dedicate to this experiment and NOT use for
  your normal Claude Code work while tracing is active (see the scoping
  note in §4 — this deliberately does not touch your default shell/harness
  invocation at all).
- Enough disk for trace files. A single real compaction call in this
  session's own `compactMetadata` has carried `preTokens` up to ~940,000 —
  the captured request body for a call like that will be several MB of
  JSON at minimum, likely more with headers/formatting overhead.

## 2. Install mitmproxy

`pipx` is the cleanest install for a CLI tool — an isolated venv, no
pollution of the devcontainer's system Python:

```bash
sudo apt-get update && sudo apt-get install -y pipx   # if pipx isn't already present
pipx ensurepath
pipx install mitmproxy
exec $SHELL   # reload PATH so `mitmdump`/`mitmproxy` resolve
mitmdump --version
```

(A plain `pip install --user mitmproxy` works too if `pipx` isn't wanted;
avoid a bare system-wide `pip install` as root.)

## 3. First-run config: generate the CA cert, don't install it system-wide

Running mitmproxy once generates its CA cert into a confdir. Use a
dedicated confdir for this experiment so nothing mixes with any other
mitmproxy use, and so cleanup is just deleting one directory:

```bash
mkdir -p ~/.mitm-compaction-experiment
mitmdump --set confdir=~/.mitm-compaction-experiment -p 8080 &
sleep 1
kill %1   # just needed it to generate the cert; nothing to capture yet
ls ~/.mitm-compaction-experiment/mitmproxy-ca-cert.pem
```

**Deliberately not doing here**: `sudo cp ... /usr/local/share/ca-certificates/ && update-ca-certificates`
(system-wide trust) or any DNS/iptables redirection. That's the *later*,
harder-to-cleanly-undo step worth doing only if this experiment's findings
justify building the full transparent interception — not needed just to
capture a few traces. Scoped-to-one-shell (§4) is fully reversible: two env
vars, unset when done, nothing else on the box changes.

## 4. Start the capture

In its own terminal, start `mitmdump` writing every flow to a file, filtered
to Anthropic's API host so you aren't also capturing unrelated traffic
(package registries, telemetry, etc. if `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`
isn't set):

```bash
mitmdump \
  --set confdir=~/.mitm-compaction-experiment \
  -p 8080 \
  --save-stream-file ~/.mitm-compaction-experiment/traces.mitm \
  --set flow_detail=1
```

Leave this running. It logs one line per flow to its own terminal as a
live sanity check that traffic is actually flowing through it.

## 5. Point ONE Claude Code invocation at the proxy

In a **different, dedicated** terminal — never your normal working
session:

```bash
export HTTPS_PROXY=http://127.0.0.1:8080
export HTTP_PROXY=http://127.0.0.1:8080
export NODE_EXTRA_CA_CERTS=~/.mitm-compaction-experiment/mitmproxy-ca-cert.pem
claude
```

Verify it's actually routed: the `mitmdump` terminal should immediately
show flow lines once Claude Code makes its first API call. If nothing
shows up, check `claude`'s own startup output for a TLS/proxy connection
error first — that means the CA/proxy env vars aren't being picked up,
not that compaction silently bypassed the proxy.

Confirm you're NOT accidentally proxying your real, normal Claude Code
terminal — that session's traffic (including this very conversation, if it
were run this way) would also be captured in full plaintext. Keep this to
one clearly-labeled disposable terminal for the experiment.

## 6. Trigger a real compaction

Two ways, both worth capturing separately since they may differ (see
`design-context-lifecycle.md` Pattern (a) for why the manual case is a
documented, different mechanism):

- **Manual**: once the proxied session has some real conversation, run
  `/compact` (optionally `/compact <retention instructions>`). Known-working
  per `research-external-compaction.md`, so this should reliably produce a
  traceable call even if auto-compaction is slow to trigger.
- **Real auto-compaction**: drive genuine context growth (real work, or a
  synthetic loop of large tool-output-generating commands) until the
  internal threshold fires on its own. Slower and less controllable, but
  it's the actual mechanism this experiment cares about — the manual case
  is a useful cross-check, not a substitute.

Cross-reference the resulting `compact_boundary` record's timestamp and
`compactMetadata` in the session's own JSONL
(`~/.claude/projects/<project>/<session-id>.jsonl`) against the trace file's
timestamps to find the exact flow.

## 7. Stop and clean up the client side

```bash
unset HTTPS_PROXY HTTP_PROXY NODE_EXTRA_CA_CERTS
```
Then Ctrl-C the `mitmdump` terminal. Nothing else to undo — no system trust
store, no DNS, no iptables were touched.

## 8. Reading and analyzing the trace

**Quick interactive look** (TUI, good for a first pass / eyeballing which
flow is the compaction call by request size or timing):

```bash
mitmproxy --set confdir=~/.mitm-compaction-experiment -r ~/.mitm-compaction-experiment/traces.mitm
```
`m` toggles request/response view on a selected flow; `/` searches.

**Non-interactive dump to text** (good for grepping):

```bash
mitmdump -r ~/.mitm-compaction-experiment/traces.mitm -n --flow-detail 4 > traces-readable.txt
```

**Scripted extraction to JSON** (best for actually correlating against the
session JSONL's `preTokens`/timestamp, and for keeping only the flow(s) that
matter rather than a multi-MB blob of every turn):

```python
#!/usr/bin/env python3
"""Pull request/response bodies out of a mitmproxy save-stream file."""
from mitmproxy.io import FlowReader
import json, sys

with open(sys.argv[1], "rb") as f:
    for i, flow in enumerate(FlowReader(f).stream()):
        req = flow.request
        resp = flow.response
        try:
            req_body = json.loads(req.get_text())
        except Exception:
            req_body = None
        out = {
            "index": i,
            "timestamp": req.timestamp_start,
            "path": req.path,
            "req_bytes": len(req.raw_content or b""),
            "resp_bytes": len(resp.raw_content or b"") if resp else None,
            "req_system_prompt_preview": (
                (req_body.get("system") or "")[:300] if isinstance(req_body, dict) else None
            ),
            "req_message_count": (
                len(req_body.get("messages", [])) if isinstance(req_body, dict) else None
            ),
        }
        print(json.dumps(out))
```

Run it and look for the flow whose `req_bytes`/`req_message_count` lines up
with the real `preTokens` from the session's own `compactMetadata` around
the same timestamp — that's almost certainly the compaction call. Once
identified, dump ONLY that flow's full request/response bodies for closer
inspection (its `system_prompt_preview` and any distinctive param is what
answers §0's question).

## 9. Handling the trace afterward

- The trace file (and anything extracted from it) contains full plaintext
  of real conversation content. Treat it like any other secrets-bearing
  artifact: not committed, not world-readable, deleted once the experiment
  is done extracting what's needed.
- If a compaction call's request body is large enough to be unwieldy, the
  useful part for this experiment is almost always the *tail* (the last
  few messages plus whatever wrapper/instruction/system-prompt frames the
  request) — the bulk of the transcript in the middle is exactly the kind
  of content this whole `session_extract` package already knows how to
  compress; there's no need to manually read all of it.

## Open points / what's left to do

Recorded here rather than left implicit — this doc is the mechanics, not
the decision of whether to actually run this:

1. **This experiment has not been run yet.** Nothing in §§0–9 has been
   executed; this is a pre-registered protocol (matching the convention
   `design-context-lifecycle-experiments.md` uses for `V6`).
2. **Whether to run it at all is still an open call.** The lower-risk
   alternative already recommended in this session — externally racing
   Claude Code's own auto-compaction via `--resume --fork-session` (`V6`,
   `reference/LESSONS.md` L24) — gets a similar practical outcome (mechanical
   extraction instead of the LLM's own compaction) without any of this
   experiment's reverse-engineering or version-coupling risk. This
   experiment is worth running as *research* (understanding the mechanism,
   satisfying real curiosity, possibly informing other work) even if its
   conclusion is "not worth productionizing" — that's a legitimate outcome,
   not a failure of the experiment.
3. **Auto-triggering a real compaction is not fully controllable.** §6's
   "drive genuine context growth" step doesn't have a reliable recipe yet
   for forcing the internal threshold to fire on demand within a short
   experiment session — may need several attempts, or accepting the
   manual-`/compact` case as the primary data point with auto-compaction
   captured opportunistically.
4. **No decision yet on separate-host vs. devcontainer-local.** This doc
   assumes devcontainer-local (lower setup cost, sufficient for a
   observation-only pass). A separate host only becomes relevant if this
   moves toward the *active interception* phase (fabricating responses),
   where isolating the proxy from the traced workload's own blast radius
   becomes a more real concern than it is for pure logging.
5. **The extraction script in §8 is untested** — written against
   mitmproxy's documented `FlowReader`/flow object API, not yet run against
   a real `.mitm` file. Likely needs small fixes once real data exists
   (`get_text()` exceptions on non-JSON bodies, streaming responses not
   being a single `raw_content` blob, etc.).
6. **If run, findings should be folded back** into `E-010` (results) and
   into `adapters/claude_code.py`'s module docstring if anything learned
   about the compaction call's shape is relevant to `session_extract`
   itself (it currently only reads the JSONL side-effects of compaction,
   never the API call that produces them).
