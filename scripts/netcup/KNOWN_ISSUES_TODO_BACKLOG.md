# netcup — known issues, TODO, and backlog

Created 2026-10-21 from the INT-03 review
(`/home/vscode/vbpub-release-program-20261019/review-INT-03.md`). IDs are
`NC-N`, allocated sequentially in this file (no other allocator exists).

Normative behavior belongs in [`CLI-SPEC.md`](CLI-SPEC.md); rationale in
[`DESIGN-GUIDE.md`](DESIGN-GUIDE.md). A FIXED entry means code, tests, and
docs landed together.

## Status

| ID | Summary | Severity | Status |
|---|---|---|---|
| NC-01 | No test covers the 401 -> token refresh -> retried bodyless POST path; the retry must still carry `Content-Type: application/json` and no body | Minor | OPEN |

---

## NC-01 — the 401 refresh-and-retry path of a bodyless POST is untested

**Status:** OPEN. Filed 2026-10-21 from the INT-03 review. Severity Minor. Type: test gap; no production change is expected.

**Observed.** `NetcupSCPClient.post` retries once after a 401 when a refresh token is configured: it calls `refresh_access_token()` and then `_http_json` again. SCP validates `Content-Type` even for bodyless JSON actions (for example preparing a user-ISO upload), so the retried request must still carry `Content-Type: application/json` with no body. `_http_json` (`netcup_scp_client.py:490-497`) sets that header itself for a bodyless POST/PUT/PATCH, so current behavior is correct. The only test is `tests/test_install_host.py:703` (the first attempt only); nothing exercises the retry, so a regression that rebuilt the retry headers without the content type would pass the suite.

**Why netcup owns it.** The request construction is this client's, and a missing header on the retry would fail only against the live API.

**Required test (one test, no production change).** Stub the transport so the first POST to a bodyless endpoint returns 401 and the refresh succeeds. Assert that exactly two requests were made, and that the second carries `Content-Type: application/json`, has `data is None`, and uses the refreshed access token.

**Oracles.**
1. The two-request sequence above passes on current main.
2. Controlled wrong implementation: removing the content-type default for bodyless POST in `_http_json` makes the new test fail on the retried request (and the existing first-attempt test fail as well).
3. A 401 with no refresh token configured still raises without a retry (regression guard, unchanged behavior).
