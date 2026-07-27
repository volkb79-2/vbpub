"""Safety contracts for top-level CLI operational dispatch."""

import pytest

import topos.cli as cli


@pytest.mark.parametrize(
    ("verb", "handler_name", "sentinel"),
    [
        ("damon", "_main_damon", 101),
        ("snapshot", "_main_snapshot", 102),
        ("daemon", "_main_daemon", 103),
        ("mcp", "_main_mcp", 104),
        ("gateway", "_main_gateway", 105),
        ("bpf", "_main_bpf", 106),
        ("action", "_main_action", 107),
        ("inspect-files", "_main_inspect_files", 108),
        ("report", "_main_report", 109),
        ("query", "_main_query", 110),
        ("compare", "_main_compare", 111),
        ("squeeze", "_main_squeeze", 112),
    ],
)
def test_operational_verbs_dispatch_tail_and_status(
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    handler_name: str,
    sentinel: int,
) -> None:
    received: list[list[str]] = []

    def fake_handler(argv: list[str]) -> int:
        received.append(argv)
        return sentinel

    monkeypatch.setattr(cli, handler_name, fake_handler)
    assert cli.main([verb, "--fixture", "value"]) == sentinel
    assert received == [["--fixture", "value"]]


def test_ordinary_argv_reaches_default_parser_not_operational_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[list[str]] = []

    def stop_at_parser(argv: list[str]) -> object:
        received.append(argv)
        raise RuntimeError("default parser reached")

    monkeypatch.setattr(cli, "parse_args", stop_at_parser)
    for handler_name in (
        "_main_damon",
        "_main_snapshot",
        "_main_daemon",
        "_main_mcp",
        "_main_gateway",
        "_main_bpf",
        "_main_action",
        "_main_inspect_files",
        "_main_report",
        "_main_query",
        "_main_compare",
        "_main_squeeze",
    ):
        monkeypatch.setattr(
            cli,
            handler_name,
            lambda _argv: pytest.fail("ordinary argv reached an operational handler"),
        )

    with pytest.raises(RuntimeError, match="default parser reached"):
        cli.main(["--once"])
    assert received == [["--once"]]
