import asyncio
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from tnt.cli import build_cli
from tnt.errors import ProfileError
from tnt.profile import Profile
from tnt.session import open_session
from tnt.ssh import SshHandoffError, build_ssh_argv, run_interactive_ssh
from tnt.tui.app import TntApp


def ssh_profile(tmp_path, **overrides):
    key = tmp_path / "identity ; $(touch should-not-run)"
    key.write_text("placeholder private key")
    key.chmod(0o600)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("nas.example.invalid ssh-ed25519 AAAATEST\n")
    known_hosts.chmod(0o600)
    fields = {
        "name": "lab",
        "url": "wss://nas.example.invalid/api/current",
        "username": "operator",
        "ssh_user": "admin",
        "ssh_key_files": (str(key),),
        "ssh_known_hosts": str(known_hosts),
    }
    fields.update(overrides)
    return Profile(**fields), key, known_hosts


def expected_argv(profile, executable="ssh"):
    return [
        executable,
        "-F", "/dev/null",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={profile.ssh_known_hosts}",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "VerifyHostKeyDNS=no",
        "-o", "UpdateHostKeys=no",
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "IdentityFile=none",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "HostbasedAuthentication=no",
        "-tt",
        "-l", profile.ssh_user,
        *[part for key in profile.ssh_key_files for part in ("-i", key)],
        "--", "nas.example.invalid",
    ]


def test_ssh_argv_is_shell_free_interactive_and_uses_only_pinned_sources(tmp_path):
    key = tmp_path / "identity with spaces;$(touch pwned)"
    profile, _, known_hosts = ssh_profile(tmp_path, ssh_key_files=(str(key), str(tmp_path / "second-key")))
    key.write_text("placeholder private key")
    key.chmod(0o600)
    second = Path(profile.ssh_key_files[1])
    second.write_text("placeholder second key")
    second.chmod(0o400)

    argv = build_ssh_argv(profile)

    assert argv == expected_argv(profile)
    assert "-F" in argv and argv[argv.index("-F") + 1] == "/dev/null"
    assert "StrictHostKeyChecking=yes" in argv
    assert "UserKnownHostsFile=" + str(known_hosts) in argv
    assert "GlobalKnownHostsFile=/dev/null" in argv
    assert "VerifyHostKeyDNS=no" in argv and "IdentityAgent=none" in argv and "IdentityFile=none" in argv
    assert argv[-2:] == ["--", "nas.example.invalid"]
    assert "pwned" in argv[argv.index("-i") + 1]
    assert not (tmp_path / "pwned").exists()


def test_interactive_ssh_runner_uses_argv_without_shell_or_output_capture(tmp_path):
    profile, _, _ = ssh_profile(tmp_path)
    calls = []

    class Result:
        returncode = 37

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return Result()

    assert run_interactive_ssh(profile, executable="ssh-stub", runner=runner) == 37
    assert calls == [(expected_argv(profile, "ssh-stub"), {"check": False, "shell": False})]


def test_ssh_runner_maps_signals_and_hides_local_startup_errors(tmp_path):
    profile, _, _ = ssh_profile(tmp_path)

    class Signalled:
        returncode = -9

    assert run_interactive_ssh(profile, runner=lambda *_args, **_kwargs: Signalled()) == 137

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("/private/path/to/ssh")

    with pytest.raises(SshHandoffError, match="could not start") as caught:
        run_interactive_ssh(profile, runner=unavailable)
    assert "/private/path" not in str(caught.value)


@pytest.mark.parametrize("overrides,match", [
    ({"ssh_user": None}, "SSH user"),
    ({"ssh_user": "-oProxyCommand=touch /tmp/pwned"}, "SSH user"),
    ({"ssh_key_files": ()}, "identity file"),
    ({"ssh_known_hosts": None}, "known-hosts file"),
    ({"url": "wss://-oProxyCommand=touch.invalid/api/current"}, "safe SSH hostname"),
    ({"ssh_known_hosts": "/tmp/known hosts,ProxyCommand=touch"}, "known-hosts path"),
])
def test_ssh_refuses_untrusted_profile_arguments_before_launch(tmp_path, overrides, match):
    profile, _, _ = ssh_profile(tmp_path)
    profile = replace(profile, **overrides)
    with pytest.raises(SshHandoffError, match=match) as caught:
        build_ssh_argv(profile)
    assert "/tmp/pwned" not in str(caught.value)
    assert "known hosts" not in str(caught.value)


def test_ssh_refuses_symlinks_and_unsafe_identity_modes_without_echoing_paths(tmp_path):
    profile, key, known_hosts = ssh_profile(tmp_path)
    key.chmod(0o644)
    with pytest.raises(SshHandoffError, match="identity file") as key_error:
        build_ssh_argv(profile)
    assert str(key) not in str(key_error.value)

    key.chmod(0o600)
    link = tmp_path / "known_hosts-link"
    link.symlink_to(known_hosts)
    profile = replace(profile, ssh_known_hosts=str(link))
    with pytest.raises(SshHandoffError, match="known-hosts file") as hosts_error:
        build_ssh_argv(profile)
    assert str(link) not in str(hosts_error.value)


def test_ssh_refuses_empty_hostnames_and_non_regular_known_hosts(tmp_path):
    profile, _, _ = ssh_profile(tmp_path, url="wss:///api/current")
    with pytest.raises(SshHandoffError, match="safe SSH hostname"):
        build_ssh_argv(profile)

    directory = tmp_path / "known_hosts-directory"
    directory.mkdir()
    profile, _, _ = ssh_profile(tmp_path, ssh_known_hosts=str(directory))
    with pytest.raises(SshHandoffError, match="regular file owned"):
        build_ssh_argv(profile)


def test_ssh_known_hosts_must_be_readable_and_not_group_or_world_writable(tmp_path):
    profile, _, known_hosts = ssh_profile(tmp_path)
    known_hosts.chmod(0o666)
    with pytest.raises(SshHandoffError, match="known-hosts file"):
        build_ssh_argv(profile)


def test_ssh_requires_absolute_paths_and_a_valid_local_executable(tmp_path):
    profile, _, _ = ssh_profile(tmp_path, ssh_known_hosts="relative/known_hosts")
    with pytest.raises(SshHandoffError, match="absolute known-hosts"):
        build_ssh_argv(profile)

    profile, _, _ = ssh_profile(tmp_path)
    with pytest.raises(SshHandoffError, match="OpenSSH executable"):
        build_ssh_argv(profile, executable="bad\x00ssh")


def test_ssh_rejects_malformed_url_without_exposing_parser_errors(tmp_path):
    profile, _, _ = ssh_profile(tmp_path, url="wss://[2001:db8::1/api/current")
    with pytest.raises(SshHandoffError, match="safe SSH hostname"):
        build_ssh_argv(profile)


def test_ssh_requires_child_process_status(tmp_path):
    profile, _, _ = ssh_profile(tmp_path)
    with pytest.raises(SshHandoffError, match="returned no process status"):
        run_interactive_ssh(profile, runner=lambda *_args, **_kwargs: object())


def test_ssh_refuses_when_nofollow_file_open_is_unavailable(tmp_path, monkeypatch):
    profile, _, _ = ssh_profile(tmp_path)
    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(SshHandoffError, match="cannot be safely opened"):
        build_ssh_argv(profile)


@pytest.mark.parametrize("host,expected", [
    ("[2001:db8::2]", "2001:db8::2"),
    ("nasbücher.example.invalid", "xn--nasbcher-95a.example.invalid"),
])
def test_ssh_normalizes_valid_ipv6_and_idna_hostnames(tmp_path, host, expected):
    profile, _, _ = ssh_profile(tmp_path, url=f"wss://{host}/api/current")
    assert build_ssh_argv(profile)[-1] == expected


def _stub_ssh(tmp_path, monkeypatch, *, status=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "ssh-argv.json"
    executable = bin_dir / "ssh"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['TNT_SSH_TEST_ARGV'], 'w', encoding='utf-8') as stream:\n"
        "    json.dump(sys.argv[1:], stream)\n"
        "if 'TNT_SSH_TEST_PHASE' in os.environ:\n"
        "    with open(os.environ['TNT_SSH_TEST_PHASE_CAPTURE'], 'w', encoding='utf-8') as phase:\n"
        "        with open(os.environ['TNT_SSH_TEST_PHASE'], encoding='utf-8') as state:\n"
        "            phase.write(state.read())\n"
        f"sys.exit({status})\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("TNT_SSH_TEST_ARGV", str(capture))
    monkeypatch.delenv("TNT_SSH_TEST_PHASE", raising=False)
    return capture


def _write_config(path, profile):
    path.write_text(
        "schema_version = 1\n"
        'default_host = "lab"\n\n'
        "[hosts.lab]\n"
        f"url = {json.dumps(profile.url)}\n"
        f"username = {json.dumps(profile.username)}\n"
        f"ssh_user = {json.dumps(profile.ssh_user)}\n"
        f"ssh_key_files = {json.dumps(list(profile.ssh_key_files))}\n"
        f"ssh_known_hosts = {json.dumps(profile.ssh_known_hosts)}\n"
    )
    path.chmod(0o600)


def test_cli_ssh_uses_named_host_without_loading_api_credentials_and_rejects_remote_command(
        tmp_path, monkeypatch, capsys):
    profile, _, _ = ssh_profile(tmp_path)
    config = tmp_path / "tnt.toml"
    _write_config(config, profile)
    capture = _stub_ssh(tmp_path, monkeypatch, status=23)

    code = build_cli().run(argv=["ssh", "lab", "--config", str(config)])
    out = capsys.readouterr()
    assert code == 23 and not out.out and not out.err
    assert json.loads(capture.read_text()) == expected_argv(profile)[1:]
    assert str(profile.ssh_key_files[0]) not in out.out + out.err

    code = build_cli().run(argv=["ssh", "--host", "lab", "--config", str(config)])
    out = capsys.readouterr()
    assert code == 23 and not out.out and not out.err
    assert json.loads(capture.read_text()) == expected_argv(profile)[1:]

    capture.unlink()
    code = build_cli().run(argv=["ssh", "lab", "--", "whoami", "--option-looking"])
    out = capsys.readouterr()
    assert code != 0 and not capture.exists()
    assert "unexpected" in out.err.lower() or "unrecognized" in out.err.lower()


def test_tui_suspends_and_restores_around_stub_ssh(tmp_path, monkeypatch, server, capsys):
    profile, _, _ = ssh_profile(tmp_path)
    session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    session.profile = profile
    capture = _stub_ssh(tmp_path, monkeypatch, status=19)
    app = TntApp(session)
    state = []
    phase = tmp_path / "terminal-phase"
    phase_capture = tmp_path / "ssh-observed-phase"
    phase.write_text("restored")
    monkeypatch.setenv("TNT_SSH_TEST_PHASE", str(phase))
    monkeypatch.setenv("TNT_SSH_TEST_PHASE_CAPTURE", str(phase_capture))

    @contextmanager
    def suspend():
        state.append("suspended")
        phase.write_text("suspended")
        try:
            yield
        finally:
            state.append("restored")
            phase.write_text("restored")

    async def run():
        async with app.run_test() as pilot:
            assert any(command.title == "Open interactive SSH session"
                       for command in app.get_system_commands(app.screen))
            assert any(binding[0] == "s" and binding[1] == "ssh" for binding in app.BINDINGS)
            monkeypatch.setattr(app, "suspend", suspend)
            app.action_ssh()
            await pilot.pause()

    asyncio.run(run())

    out = capsys.readouterr()
    assert state == ["suspended", "restored"]
    assert phase_capture.read_text() == "suspended" and phase.read_text() == "restored"
    assert json.loads(capture.read_text()) == expected_argv(profile)[1:]
    assert str(profile.ssh_key_files[0]) not in out.out + out.err
    session.close()


def test_tui_refuses_ssh_when_terminal_cannot_suspend(tmp_path, monkeypatch, server):
    from textual.app import SuspendNotSupported

    profile, _, _ = ssh_profile(tmp_path)
    session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True)
    session.profile = profile
    capture = _stub_ssh(tmp_path, monkeypatch)
    app = TntApp(session)

    def suspend_unavailable():
        raise SuspendNotSupported("test terminal")

    async def run():
        async with app.run_test() as pilot:
            monkeypatch.setattr(app, "suspend", suspend_unavailable)
            app.action_ssh()
            await pilot.pause()

    asyncio.run(run())
    assert not capture.exists()
    session.close()


def test_tui_refuses_ssh_without_selected_host(tmp_path, monkeypatch):
    capture = _stub_ssh(tmp_path, monkeypatch)
    app = TntApp(None, hosts={}, default_host=None, secret_entries={})
    notices = []

    async def run():
        async with app.run_test() as pilot:
            monkeypatch.setattr(app, "notify", lambda message, **kwargs: notices.append((message, kwargs)))
            app.action_ssh()
            await pilot.pause()

    asyncio.run(run())
    assert not capture.exists()
    assert notices == [("Choose a host before opening SSH.", {"title": "No host selected"})]
    app.session_controller.close()


def test_profile_loader_rejects_ssh_users_that_could_become_options(tmp_path):
    path = tmp_path / "tnt.toml"
    path.write_text('''schema_version = 1
[hosts.lab]
url = "wss://nas.example.invalid/api/current"
username = "operator"
ssh_user = "-oProxyCommand=touch"
''')
    path.chmod(0o600)
    from tnt.profile import load_profiles

    with pytest.raises(ProfileError, match="ssh_user"):
        load_profiles(path)
