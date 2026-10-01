from cmru import cli, changelog, getpy, handlers, resolve, standards, tester_gate


def test_cli_delegates_handler_and_tester_gate_registered_grammars(capsys):
    registered = cli._build_cli()
    assert "handler" in registered.delegates
    assert "tester-gate" in registered.delegates
    assert cli.main(["handler", "wheel-build", "--help"]) == 0
    assert "--cwd PATH" in capsys.readouterr().out
    assert cli.main(["tester-gate", "--help"]) == 0
    assert "--cwd DIR" in capsys.readouterr().out


def test_cli_delegates_standards_resolve_and_get_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(standards, "_run_standards", lambda args, _runtime: calls.append(("standards", args.target)))
    monkeypatch.setattr(resolve, "_run_resolve", lambda args, _runtime: calls.append(("resolve", args.target)))
    monkeypatch.setattr(getpy, "_run_getpy", lambda args, _runtime: calls.append(("get", args.target, args.output)))
    assert cli.main(["standards", "demo"]) == 0
    assert cli.main(["resolve", "demo"]) == 0
    assert cli.main(["get-py", "demo", "--output", "x.py"]) == 0
    assert calls == [
        ("standards", "demo"),
        ("resolve", "demo"),
        ("get", "demo", "x.py"),
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
    assert cli.main(["changelog", "missing", "--backfill-tag", "demo-v1"]) == 2
    assert "unknown project(s): missing" in capsys.readouterr().err
