"""The shipped host-side liveness units must stay wired to real things.

These are deployment artifacts, not Python, so nothing else in the suite
would notice them rotting. The specific rot that matters is silent: a unit
that invokes a CLI verb which no longer exists, or that escalates through the
very transport it is supposed to bypass, still installs cleanly and still
reports success — right up until the outage it was meant to catch.

CR-16's review found exactly this shape of defect one level down: the
`nyxloomd/ciu.toml` INSTANCE file still carried the old health budget, so a
widened timeout argued for in the template never reached the deployment. A
shipped file that nothing checks is a claim, not a mechanism.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UNIT_DIR = Path(__file__).resolve().parents[1] / "nyxloomd" / "systemd"
SERVICE = UNIT_DIR / "nyxloom-liveness.service"
TIMER = UNIT_DIR / "nyxloom-liveness.timer"
ALARM = UNIT_DIR / "nyxloom-liveness-alarm@.service"
README = UNIT_DIR / "README.md"


def _body(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_shipped_units_exist():
    for path in (SERVICE, TIMER, ALARM, README):
        assert path.exists(), f"missing shipped unit: {path.name}"


def test_the_service_invokes_a_cli_verb_that_actually_exists(tmp_state):
    """The unit's own argv must still be accepted by the shipped CLI.

    Taken OUT of the unit file and run, rather than hardcoded here: renaming
    `--liveness` in the CLI without updating the unit then fails in this
    suite, instead of at 3am as an `unrecognized arguments` line in a journal
    nobody is tailing. The CLI builds its parser inside `main()`, so driving
    `main()` is also the only honest way to ask -- and it exercises the whole
    path the unit actually takes, not a parser reconstructed to resemble it.
    """
    from nyxloom import cli

    match = re.search(r"^ExecStart=\S*nyxloom\s+(.+)$", _body(SERVICE), re.M)
    assert match, "the service does not invoke the nyxloom CLI"
    argv = match.group(1).split()
    assert argv[0] == "doctor", f"unexpected verb: {argv[0]}"

    # An empty registry is the benign case: nothing to report, exit 0. What
    # is being asserted is that argparse ACCEPTED the unit's argv -- an
    # unknown flag raises, because the CLI builds its parser with
    # exit_on_error=False.
    assert cli.main(argv) == 0


def test_the_alarm_path_does_not_route_through_the_daemons_own_transport():
    """The whole point of the second path.

    CR-16's acceptance is that a broken notification transport is reported
    through a DIFFERENT path. An alarm handler that reached for ntfy would
    collapse the two into one, exactly in the case where that one is broken.
    """
    # Assert on the EXECUTABLE half only. The comments deliberately discuss
    # ntfy in order to explain why it is absent, so a substring scan over the
    # whole file would fail on the unit's own rationale.
    directives = [ln for ln in _body(ALARM).splitlines() if ln.startswith("ExecStart")]
    assert directives, "the alarm handler runs nothing"
    joined = " ".join(directives).lower()
    for token in ("ntfy", "webhook", "notify"):
        assert token not in joined, (
            f"the alarm handler reaches {token!r}, which is the transport it "
            "exists to bypass")


def test_the_alarm_is_reached_when_the_probe_fails():
    """A failing probe that escalates nowhere is the state CR-16 already had.

    `OnFailure=` is the only thing turning a failed unit into an alarm, so its
    absence is the difference between this package existing and not.
    """
    body = _body(SERVICE)
    assert "OnFailure=nyxloom-liveness-alarm@" in body, (
        "the probe does not escalate on failure -- a failed unit in "
        "systemctl is the same dead end as Docker's `unhealthy`")


def test_the_alarm_handler_cannot_be_silenced_by_its_own_optional_hook():
    """The operator hook is optional; the local alarms are not.

    A missing or failing `/etc/nyxloom/liveness-alarm-hook` must not suppress
    the journal and wall alarms, so its directive carries systemd's `-`
    prefix. Without that, adding a broken hook would silently disable the
    alarm entirely -- worse than having no hook.
    """
    hooks = [ln for ln in _body(ALARM).splitlines()
             if ln.startswith("ExecStart") and "liveness-alarm-hook" in ln]
    assert len(hooks) == 1, "expected exactly one operator hook directive"
    assert hooks[0].startswith("ExecStart=-"), (
        "the operator hook is not failure-tolerant: a broken hook would "
        "suppress the alarms that follow it")


def test_the_timer_does_not_alarm_on_every_boot():
    """`OnBootSec=0` would alarm during every reboot, because a daemon that
    just started legitimately has not reconciled yet -- and an alarm that
    cries wolf on schedule is one nobody reads by the second week."""
    body = _body(TIMER)
    match = re.search(r"^OnBootSec=(\S+)$", body, re.M)
    assert match, "the timer declares no OnBootSec"
    assert match.group(1) not in ("0", "0s", "0min"), (
        "the timer probes immediately at boot, before any pass can complete")
    assert re.search(r"^Persistent=true$", body, re.M), (
        "a suspended or powered-off host should catch up rather than skip")


def test_the_readme_states_what_is_not_covered():
    """The units cannot report that the host itself died. Saying so is the
    difference between a documented boundary and an assumed one."""
    body = _body(README).lower()
    assert "not sufficient" in body, "the local-only default is oversold"
    assert "host" in body and "outside nyxloom" in body, (
        "the README does not state the boundary of what this detects")
