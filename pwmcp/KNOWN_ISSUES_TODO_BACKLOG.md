# PWMCP — known issues, TODO, and backlog

This file records consumer-facing product gaps for later triage. Normative
behavior remains in the product specification and consumer documentation.

Last updated: 2026-10-01.

## PWMCP-01 — Provide a rendered-page inspection command

**Status: OPEN — candidate.**

Consumers sometimes need one page's JavaScript-rendered text or screenshot
without writing a browser client. Today the `pwmcp` command only exposes
`doctor` and `contract`; consumers must either call MCP tools directly or
install the release-bundled Python client plus the matching Playwright package
and write a `BrowserLease` script. Raw MCP SDK snippets also couple consumers
to transport and SDK API details.

Consider adding a small command such as `pwmcp inspect URL --format text|snapshot|screenshot --output PATH` 
that connects to
the existing remote browser and can return rendered body text, an accessibility
snapshot, or a screenshot. It should reuse the release contract and managed
browser lease, support an explicit wait condition, clean up the lease on every
exit path, and avoid requiring local browser binaries. The current use case
that surfaced this gap was reading a JavaScript-rendered GitHub project page
from a devcontainer.

Acceptance should include a documented one-command consumer example and a
repeatable output mode suitable for saving a page snapshot to a file. This is a
proposal only; implementation shape and security boundaries still need review.
