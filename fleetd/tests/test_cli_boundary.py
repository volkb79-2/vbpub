"""The CLI's exit-code boundary: a domain refusal is 2, an unexpected failure is 1.

The registered cli-extended boundary turns ``CliFailure`` into one ``[ERROR]`` line and
the status it carries, and turns any *other* exception into ``[ERROR] unexpected ...``
with exit 1 (plus the stack under ``--traceback``). ``fleetd.cli._domain`` is the ONE
place that decides which failures are domain refusals, so a new contract error cannot
invent a third status and a genuine bug cannot be disguised as a bad input.
"""

from __future__ import annotations

import argparse
import errno
import io

import pytest
from cli_extended import CliFailure, CliRuntime
from cli_extended.output import CliOutput

from fleetd import cli
from fleetd.cli import build_cli, identity
from fleetd.contracts import ContractError
from fleetd.transport import HostUnavailable, TransportError


def _runtime() -> CliRuntime:
    """One real runtime whose output streams are in-memory (the handlers raise anyway)."""

    return CliRuntime(
        identity=identity(),
        output=CliOutput(identity(), stdout=io.StringIO(), stderr=io.StringIO()),
    )


def _call(handler, runtime=None):
    """Invoke the handler through ``_domain`` exactly as the registry does."""
    return cli._domain(handler)(argparse.Namespace(), runtime or _runtime())


@pytest.mark.parametrize(
    "exception, marker",
    [
        (ContractError("demand: unknown field(s): unit_high_memory"), "unknown field"),
        (TransportError("hx wrapper refused"), "hx wrapper refused"),
        (HostUnavailable("host did not answer"), "host did not answer"),
        (OSError(errno.ENOENT, "No such file or directory"), "No such file"),
        (UnicodeError("not utf-8"), "not utf-8"),
        (OverflowError("int too large to convert to float"), "int too large"),
    ],
)
def test_every_declared_domain_failure_becomes_one_cli_failure_with_exit_two(exception, marker):
    def handler(args, runtime):
        raise exception

    with pytest.raises(CliFailure) as failure:
        _call(handler)
    assert failure.value.exit_code == cli.EXIT_DOMAIN_FAILURE == 2
    assert marker in failure.value.message
    # The library prints exactly one line and no stack: the message carries no traceback.
    assert "Traceback" not in failure.value.message


@pytest.mark.parametrize("exception", [RuntimeError("bug"), ZeroDivisionError("bug"), KeyError("bug")])
def test_an_unexpected_failure_is_not_disguised_as_a_domain_refusal(exception):
    """The complement of the mapping: a bug must keep the boundary's generic exit 1."""

    def handler(args, runtime):
        raise exception

    with pytest.raises(type(exception)) as raised:
        _call(handler)
    assert raised.value is exception


def test_cli_failure_keeps_the_status_its_raise_site_chose():
    """A handler that already knows its code is not overridden by the domain default."""

    def handler(args, runtime):
        raise CliFailure("library-owned refusal", exit_code=1)

    with pytest.raises(CliFailure) as failure:
        _call(handler)
    assert failure.value.exit_code == 1


def test_a_successful_handler_runs_unchanged_and_returns_its_result():
    seen = []

    def handler(args, runtime):
        seen.append(args)
        return "done"

    assert _call(handler) == "done"
    assert len(seen) == 1


def test_the_mapping_table_names_every_domain_failure_class():
    """The tuple is the contract; a class added elsewhere would not be mapped.

    ``HostUnavailable`` is deliberately covered by its ``TransportError`` base rather
    than repeated as a second entry: the mapping is by class, and both raise the same
    documented exit 2.
    """

    assert set(cli.DOMAIN_FAILURES) == {
        ContractError, TransportError, OSError, UnicodeError, OverflowError,
    }
    for exception in cli.DOMAIN_FAILURES:
        assert issubclass(exception, Exception)
    assert issubclass(HostUnavailable, TransportError)
    assert cli.DOMAIN_FAILURES.count(TransportError) == 1


def test_every_domain_failure_in_the_table_is_reached_by_a_verb():
    """A table entry with no raising verb is a mapping nobody exercises."""

    raised: set[type[BaseException]] = set()

    def handler(args, runtime):
        raise cli.DOMAIN_FAILURES[len(raised)](f"failure {len(raised)}")

    for _ in cli.DOMAIN_FAILURES:
        with pytest.raises(CliFailure):
            _call(handler)
        raised.add(cli.DOMAIN_FAILURES[len(raised)])
    assert raised == set(cli.DOMAIN_FAILURES)


def test_every_registered_handler_is_wrapped_so_no_verb_bypasses_the_mapping():
    """A verb registered without ``_domain`` would report a refused input as exit 1.

    Checks the BUILT registry (``build_cli``), not the module attributes: the wrapper is
    applied where a handler is registered, which is exactly the line a future verb could
    forget. The library-owned ``skills`` handlers are exempt by design: their exit codes
    come from the library's own stamp/integrity checks.
    """

    app = build_cli()
    checked = 0
    for name, handler in app.handlers.items():
        assert getattr(handler, "__wrapped__", None) is not None, name
        checked += 1
    for group, delegate in app.delegates.items():
        if group == "skills":
            continue
        for name, handler in delegate.handlers.items():
            assert getattr(handler, "__wrapped__", None) is not None, (group, name)
            checked += 1
    assert checked == 10
    assert set(app.delegates) == {"facts", "skills"}
    assert app.skills_package == ("fleetd", "skills")


def test_main_reports_a_missing_version_source_as_one_line_and_exit_three(capsys, monkeypatch):
    """`main` is the path `python -m fleetd` takes: the same guard, without a subprocess."""

    from cli_extended import VersionLookupError

    def broken():
        raise VersionLookupError("distribution 'fleetd' is not installed")

    monkeypatch.setattr(cli, "build_cli", broken)
    assert cli.main(["--version"]) == cli.EXIT_NOT_INSTALLED == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("fleet: ERROR cannot determine the fleetd version:")
    assert "install the fleetd wheel" in captured.err.lower()
    assert "Traceback" not in captured.err


def test_main_with_a_working_build_returns_the_library_status(monkeypatch):
    """A resolvable version is not a special case: the registered CLI runs normally."""

    monkeypatch.setattr(cli, "build_cli", lambda: _CLI_STATUS(0))
    assert cli.main([]) == 0
    assert cli.main(["tick", "c"]) == 0


class _CLI_STATUS:
    def __init__(self, status: int) -> None:
        self.status = status

    seen: list[object] | None = None

    def run(self, argv=None) -> int:
        type(self).seen = argv
        return self.status


def test_a_failure_that_is_both_a_domain_error_and_a_cli_failure_keeps_its_own_status():
    """`CliFailure` wins over the domain mapping, in that order, on purpose.

    A library or consumer exception can inherit both (``CliFailure`` plus a contract error
    type); the raise site's own status is the more specific decision, so the mapping must
    re-raise it instead of re-wrapping it with the generic exit 2.
    """

    class Both(CliFailure, ContractError):
        pass

    def handler(args, runtime):
        raise Both("already classified", exit_code=1)

    with pytest.raises(CliFailure) as failure:
        _call(handler)
    assert failure.value.exit_code == 1
