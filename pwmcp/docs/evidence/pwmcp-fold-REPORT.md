<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/pwmcp-fold-REPORT.md -->
<!-- relation: PWMCP fold evidence; verbatim source text follows -->

# PWMCP client findings fold report

Date: 2026-11-07  
Branch: `pwmcp-fold-20261107`  
Worktree: `/workspaces/vbpub/.worktrees/pwmcp-fold-20261107`  
Final branch HEAD: `c096a9c83f23e4f842eeeca710177aac96c4fd2a`  
R1-tested implementation commit: `55747e192db772cd7535691c7f4c74dc8bd86210`

## Result

All three operator findings are implemented and PWMCP-42 is recorded as R1-passing on this branch. The branch is not released. Independent review remains pending.

The registered gate `nice -n 10 run-gate r1` passed in `tester-unified` on `55747e192db772cd7535691c7f4c74dc8bd86210` (2026-11-07 05:48 UTC). Assay reports **R0 PASS**, **R1 PASS**, **2,507/2,507 executable lines**, and **802/802 branches** (100%). The test inventory had 1,367 passing tests and 4 skips: the immediately preceding same-test-set run reported 1,366 passed, 1 failed, and 4 skipped; the tested commit changed only the parser code that fixed that final failing assertion. The concise successful gate output did not retain a separate pytest count line.

The tests-first commit `963360cb8efcb64959277effa0e9d2340b1f8a0a` changed only `client/tests/test_inspect.py`; its registered R1 run failed before feature implementation. That is the revert proof: the added regression checks reject the old behavior. The detailed failed-test stdout from that initial red run was not retained. Subsequent full R1 runs also found and drove fixes for a notification HTTP-status classification error, a leaking test monkeypatch, and an SSE response-id classification error. The final parser-only fix was then included in the passing R1 run.

Other successful checks: `git diff --check`, Python `compileall` for client sources/tests, `scripts/resolve-playwright-version.py --check`, and `PYTHONPATH=client/src cli-extended surface check --config client/pyproject.toml --cli pwmcp`.

## Findings

| Item | Status | Resolution |
|---|---|---|
| Crash hint and first navigation | **PASS** | Browser/page lifecycle messages (`Page crashed`, `Target closed`, destroyed execution context, and closed browser variants) are classified separately from endpoint failures. The hint now says PWMCP is reachable and recommends retrying, reducing page load, checking browser memory and `/dev/shm`, or setting `--retries N`. Inspection retries one recognized first-navigation crash automatically by default, emits a warning with the attempt number, and `--retries 0` disables that recovery. |
| JavaScript evaluation and HTML output | **PASS** | Added `pwmcp eval` for JSON-valued page evaluation and selector attribute/property reads (`--selector`, `--attr` or `--property`, `--all`, `--timeout`, global `--json`). Added `inspect --format html` for page or selector `outerHTML`/`innerHTML`, capped at 512 KiB with an explicit truncation notice. Both use a standard-library Streamable HTTP MCP client; installing the PWMCP client does not require the third-party `mcp` package. |
| `pwmcp-fetch` recipe drift | **PASS** | The packaged skill now records MCP SDK 2.3.0's unchanged API, supplies the required `browser_click.target`, and documents `pwmcp eval`, HTML inspection, retries, and crash guidance. A drift test checks referenced `browser_*` tool names against the version-pinned Playwright MCP tool contract and CLI flags against the registered parser. The skill version stamp was advanced with the r5 client version so `pwmcp skills install` refreshes it. |

### Root cause and retry choice

The old broad failure hint treated a browser/page crash as evidence that PWMCP itself was unreachable. The client now recognizes browser lifecycle text independently of `McpUnavailable` connectivity errors and `McpToolError` responses. The default one-time retry is limited to a recognized crash on inspection; general failures keep existing retry behavior, and explicit `--retries N` continues to request additional attempts.

No fresh-context crash defect was confirmed in the server implementation. The stack configuration gives Chromium a 2 GiB `/dev/shm`; no `--disable-dev-shm-usage` setting was found, and there is no PWMCP-owned memory cap on the unified browser service. The 512 MiB author-set cap in `ciu.defaults.toml.j2` applies to the optional OCR sidecar. Based on those facts and the transient operator report, retry is a bounded recovery, not a claim about the crash's underlying cause. No stack or host resource limit was changed.

### MCP and security boundary

The pinned Playwright MCP server already exposes `browser_navigate` and `browser_evaluate`, so no new server tool or gateway route was needed. The client implements only the protocol operations it needs (`initialize`, `tools/list`, `tools/call`, and session close) using Python's standard library. HTML extraction sends fixed JavaScript; `pwmcp eval` sends the caller's expression and runs it with the page origin's permissions. It does not call the upstream unsafe-code tool or create a new server permission. Existing MCP endpoint controls remain in force: the gateway Host allowlist, internal network boundary, and external TLS/basicAuth configuration. The Host allowlist protects the endpoint from DNS rebinding; it is not a destination-site allowlist. This distinction is documented in `docs/SECURITY.md`.

## Change and evidence trail

- `963360cb8` — tests-only red proof for the crash hint/default retry.
- `74bd2c68e` — client implementation, skill updates, security/user documentation, CLI surface, release projection, CHANGES and PWMCP-42 entry.
- `1e20ace6a` — fixes found by R1 and closes remaining coverage branches.
- `55747e192` — SSE mismatched request IDs now reach the request-id validator, which reports a wrong response ID instead of a misleading missing-response error.
- `c096a9c83` — records the R1 result in `KNOWN_ISSUES_TODO_BACKLOG.md`; this documentation-only commit follows the tested implementation commit.

PWMCP-42 is the next free PWMCP-4x identifier, checked across local and remote branch refs before use. `CHANGES.md` records its Unreleased feature/fix entries. README, DESIGN-GUIDE, CONSUMERS, SECURITY, CLI reference, and the packaged skill were updated in the implementation change.

## Remaining work and open questions

- Independent review is pending; no release, merge, push, tag, R2, or R3 was performed.
- The precise cause of the reported browser crash remains unproven. Stack facts above were recorded; host-owned limits were left unchanged.
- The `pwmcp-fetch` tool contract fixture pins the exact tool names and argument requirements used by the skill; it does not claim to be a complete upstream Playwright MCP inventory.
