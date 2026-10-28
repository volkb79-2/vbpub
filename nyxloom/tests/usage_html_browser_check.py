"""Offline PWMCP acceptance probe for the usage HTML's JavaScript behavior.

Run from nyxloom/ with ``python tests/usage_html_browser_check.py``. If the
PWMCP Python client is not on the current interpreter, the script re-execs
under the interpreter named by the installed ``pwmcp`` console script.
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import sys
from collections import Counter
from pathlib import Path


NYXLOOM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NYXLOOM_ROOT / "src"))


def _use_pwmcp_interpreter() -> None:
    if importlib.util.find_spec("pwmcp_client") is not None:
        return
    executable = shutil.which("pwmcp")
    if executable is None:
        raise SystemExit("PWMCP browser client is unavailable; interaction result is unverified")
    try:
        first_line = Path(executable).read_text(encoding="utf-8").splitlines()[0]
        command = shlex.split(first_line.removeprefix("#!"))
    except (OSError, IndexError, ValueError) as exc:
        raise SystemExit("cannot resolve the installed PWMCP Python runtime") from exc
    if not command or not Path(command[0]).is_file():
        raise SystemExit("cannot resolve the installed PWMCP Python runtime")
    environment = os.environ.copy()
    source = str(NYXLOOM_ROOT / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source if not existing else os.pathsep.join((source, existing))
    os.execve(command[0], [command[0], str(Path(__file__).resolve()), *sys.argv[1:]], environment)


from test_usage_html import HOSTILE_TOPIC, _report  # noqa: E402

from nyxloom.session_extract.usage.html import render_html  # noqa: E402


def _assert_active_totals_match_ledger(report: dict, page) -> None:
    calls = report["calls"]
    totals = report["totals"]
    assert totals["calls"] == len(calls)
    assert totals["raw_token_units"] == sum(call["raw_token_units"] for call in calls)
    assert totals["weighted_units"] == sum(call["weighted_units"] for call in calls)
    assert totals["api_equivalent_usd"] == sum(call["api_equivalent_usd"] for call in calls)
    expected = (
        f"Raw {totals['raw_token_units']:,} · weighted {totals['weighted_units']:.2f} · "
        f"API-equivalent ${totals['api_equivalent_usd']:.8f}"
    )
    assert page.locator("#active-call-count").text_content() == f"Showing {len(calls)} of {len(calls)} calls"
    assert page.locator("#active-totals").text_content() == expected


def _assert_joined_chart_points_match_data(report: dict, page) -> None:
    chart = report["extensions"]["chart_data"]["datasets"]
    expected_context = [
        point["call_id"]
        for session in chart["context_over_time"]["sessions"]
        for series in session["series"]
        for point in series["points"]
    ]
    expected_swim = [
        point["call_id"]
        for lane in chart["swim_lanes"]["lanes"]
        for point in lane["points"]
    ]
    rendered_context_rows = page.locator(
        "#context-chart [data-call-id]",
    ).evaluate_all("elements => elements.map(element => element.dataset.callId)")
    rendered_swim_rows = page.locator(
        "#swim-chart [data-call-id]",
    ).evaluate_all("elements => elements.map(element => element.dataset.callId)")
    visible_nodes = {
        node["node_id"]
        for root in report["nodes"]
        for node in _walk_nodes(root)
    }
    context_owners = page.locator(
        "#context-chart [data-call-id]",
    ).evaluate_all("elements => elements.map(element => element.dataset.nodeId)")
    swim_owners = page.locator(
        "#swim-chart [data-call-id]",
    ).evaluate_all("elements => elements.map(element => element.dataset.nodeId)")
    assert Counter(rendered_context_rows) == Counter(expected_context)
    assert Counter(rendered_swim_rows) == Counter(expected_swim)
    assert set(context_owners) <= visible_nodes
    assert set(swim_owners) <= visible_nodes


def _walk_nodes(node: dict):
    yield node
    for child in node.get("children", []):
        yield from _walk_nodes(child)


def run_browser_check() -> None:
    from pwmcp_client.session import BrowserLease

    full_report = _report()
    full_report["calls"][1]["conflicted"] = True
    html = render_html(full_report, weights_file="usage-weights.toml")
    requests: list[str] = []
    with BrowserLease.connect(label="nyxloom-usage-html-acceptance") as lease:
        context = lease.browser.new_context()
        try:
            page = context.new_page()
            page.on("request", lambda request: requests.append(request.url))
            page.set_content(html, wait_until="load", timeout=30_000)
            page.wait_for_function(
                "document.getElementById('usage-report-ready')?.dataset.ready === '1'",
                timeout=30_000,
            )
            _assert_active_totals_match_ledger(full_report, page)
            _assert_joined_chart_points_match_data(full_report, page)
            assert page.locator(
                "tr[data-node-id='session-001:agent-a'] td:nth-child(7)"
            ).text_content() == "1"
            assert "Raw 270 · weighted 20.00 · API-equivalent $0.06000000" in (
                page.locator("#active-totals").text_content() or ""
            )
            assert page.locator("img").count() == 0
            provenance = page.locator("#footer-provenance").text_content() or ""
            for expected in (
                "Generated 2026-10-28T12:00:00Z",
                "window [2026-10-28T08:00:00Z, 2026-10-28T12:00:00Z)",
                "3 calls across 2 transcript files", "usage-weights.toml retrieved 2026-10-28",
            ):
                assert expected in provenance
            assert "lower bound" in (page.locator("#lower-bound-note").text_content() or "")

            point = page.locator("#context-chart [data-call-id='call-a']")
            point.hover()
            tooltip = page.locator("#usage-tooltip").text_content() or ""
            for expected in (
                "Session: session-001", "Agent: agent-a", HOSTILE_TOPIC,
                "Model: claude-sonnet-5-5", "Effort: medium",
                "Time: 2026-10-28T10:00:00Z", "Tokens: in 20",
            ):
                assert expected in tooltip

            page.locator("th button[data-sort='cost']").click()
            child_order = page.locator("tr.node-row[data-depth='1']").evaluate_all(
                "rows => rows.map(row => row.dataset.nodeId)"
            )
            assert child_order == ["session-001:agent-b", "session-001:agent-a"]
            page.locator("th button[data-sort='cost']").click()

            page.locator("button[data-node-select='session-001:agent-a']").click()
            agent_row = page.locator("tr.usage-node-row[data-node-id='session-001:agent-a']")
            other_row = page.locator("tr.usage-node-row[data-node-id='session-001:agent-b']")
            assert "selected" in (agent_row.get_attribute("class") or "")
            assert "dimmed" in (other_row.get_attribute("class") or "")
            assert "dimmed" not in (
                page.locator("#context-chart g[data-node-id='session-001:agent-a']").get_attribute("class") or ""
            )
            page.locator("#context-chart [data-call-id='call-b']").click()
            assert "selected" in (other_row.get_attribute("class") or "")
            assert "dimmed" in (agent_row.get_attribute("class") or "")
            page.locator("#context-chart [data-call-id='call-a']").focus()
            page.keyboard.press("Enter")
            assert "selected" in (agent_row.get_attribute("class") or "")
            assert "dimmed" in (other_row.get_attribute("class") or "")

            page.locator("#anonymize-labels").check()
            anonymous_topic = page.locator(
                "button[data-node-select='session-001:agent-a']"
            ).text_content() or ""
            assert "topic-" in anonymous_topic and HOSTILE_TOPIC not in anonymous_topic
            embedded_topic = page.locator("#usage-report-data").evaluate(
                "element => JSON.parse(element.textContent).calls.find(call => call.call_id === 'call-a').topic"
            )
            assert embedded_topic == HOSTILE_TOPIC

            page.locator("#model-filter").select_option("claude-fable-5-1")
            assert "Showing 1 of 3 calls" in (page.locator("#active-call-count").text_content() or "")
            page.locator("#model-filter").select_option("")
            page.locator("#effort-filter").select_option("high")
            assert "Showing 1 of 3 calls" in (page.locator("#active-call-count").text_content() or "")
            page.locator("#effort-filter").select_option("")
            page.locator("#topic-filter").fill(HOSTILE_TOPIC)
            assert "Showing 1 of 3 calls" in (page.locator("#active-call-count").text_content() or "")
            page.locator("#topic-filter").fill("")
            page.locator("#cost-chart [data-group-id]").first.hover()
            grouped_tooltip = page.locator("#usage-tooltip").text_content() or ""
            for expected in ("Session:", "Agent:", "Topic:", "Model:", "Effort:", "Time:", "Tokens:"):
                assert expected in grouped_tooltip
            page.locator("[data-measure='weighted']").click()
            cost_cell = page.locator(
                "tr.usage-node-row[data-node-id='session-001:agent-a'] td:nth-child(12)"
            )
            assert "7.00 weighted" in (cost_cell.text_content() or "")
            page.locator("[data-measure='usd']").click()
            assert "$0.02000000" in (cost_cell.text_content() or "")

            page.locator("#end-brush").evaluate(
                "element => { element.value = '400'; element.dispatchEvent(new Event('input', {bubbles:true})); }"
            )
            assert "Showing 1 of 3 calls" in (page.locator("#active-call-count").text_content() or "")

            folded_report = _report()
            folded_report["presentation"]["depth"] = 0
            folded_report["nodes"][0]["omitted_descendants"] = 2
            folded_report["nodes"][0]["children"] = []
            folded_page = context.new_page()
            folded_page.on("request", lambda request: requests.append(request.url))
            folded_page.set_content(
                render_html(folded_report, weights_file="usage-weights.toml"),
                wait_until="load",
                timeout=30_000,
            )
            folded_page.wait_for_function(
                "document.getElementById('usage-report-ready')?.dataset.ready === '1'",
                timeout=30_000,
            )
            _assert_active_totals_match_ledger(folded_report, folded_page)
            _assert_joined_chart_points_match_data(folded_report, folded_page)
            assert folded_page.locator("#usage-rows tr.usage-node-row").count() == 1
            assert "Showing 3 of 3 calls" in (
                folded_page.locator("#active-call-count").text_content() or ""
            )
            assert "Raw 270 · weighted 20.00 · API-equivalent $0.06000000" in (
                folded_page.locator("#active-totals").text_content() or ""
            )
            folded_page.close()
            assert not requests
        finally:
            context.close()
    print("usage HTML BrowserLease acceptance: PASS (full and folded ledgers and joined chart points reconcile; 0 network requests)")


def main() -> int:
    _use_pwmcp_interpreter()
    run_browser_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
