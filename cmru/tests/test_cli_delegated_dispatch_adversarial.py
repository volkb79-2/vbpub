from cmru import cli, changelog, getpy, handlers, resolve, standards, tester_gate


def test_cli_delegates_handler_and_tester_gate_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(handlers, "main", lambda argv: calls.append(("handler", argv)))
    monkeypatch.setattr(tester_gate, "main", lambda argv: calls.append(("tester-gate", argv)))
    cli.main(["handler", "wheel", "--project", "demo"])
    cli.main(["tester-gate", "--json", "--project", "demo"])
    assert calls == [
        ("handler", ["wheel", "--project", "demo"]),
        ("tester-gate", ["--json", "--project", "demo"]),
    ]


def test_cli_delegates_standards_resolve_and_get_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(standards, "standards_main", lambda argv: calls.append(("standards", argv)))
    monkeypatch.setattr(resolve, "resolve_main", lambda argv: calls.append(("resolve", argv)))
    monkeypatch.setattr(getpy, "getpy_main", lambda argv: calls.append(("get", argv)))
    cli.main(["standards", "--check"])
    cli.main(["resolve", "--project", "demo"])
    cli.main(["get-py", "--project", "demo", "--output", "x.whl"])
    assert calls == [
        ("standards", ["--check"]),
        ("resolve", ["--project", "demo"]),
        ("get", ["--project", "demo", "--output", "x.whl"]),
    ]


def test_cli_changelog_unknown_project_refuses_before_backfill(monkeypatch, tmp_path, capsys):
    project = cli.ProjectConfig("demo", {}, {}, changelog="CHANGES.md")
    config = (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "t", "user"),
        cli.ReleaseEnvConfig({}, None),
    )
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    monkeypatch.setattr(changelog, "backfill_release_changelog", lambda *args: (_ for _ in ()).throw(AssertionError("backfill")))
    try:
        cli.main(["changelog", "--project", "missing", "--backfill-tag", "demo-v1"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("unknown changelog project must refuse")
    assert "Unknown project: missing" in capsys.readouterr().err
