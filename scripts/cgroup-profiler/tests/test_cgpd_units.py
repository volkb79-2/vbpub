"""cgpd unit print / unit check and the rendered unit goldens (NATIVE-DEPLOYMENT.md 4)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cgprofile import unitfiles
from cgprofile.cli import cgpd

GOLDENS = Path(__file__).resolve().parent / "fixtures" / "units"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGEN_ENV = "CGPROFILE_REGEN_SOCKET_GOLDENS"


@pytest.fixture()
def cli(capsys):
    def run(*argv):
        code = cgpd.build_cli().run(argv=list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return run


class TestRender:
    @pytest.mark.parametrize("name", list(unitfiles.TEMPLATES))
    def test_default_render_is_the_golden(self, name):
        text = unitfiles.render()[name]
        golden = GOLDENS / name
        if os.environ.get(REGEN_ENV) == "1":  # pragma: no cover - maintenance path
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_text(text)
        assert golden.read_text() == text

    def test_no_token_and_no_checkout_path_survives(self):
        for text in unitfiles.render().values():
            assert "@" not in text
            assert str(PROJECT_ROOT) not in text and "/workspaces" not in text and "/home" not in text

    def test_a_refusal_exit_is_never_restarted_but_a_lost_socket_is(self):
        # exit 2 is a permanent configuration error (bad config, no activation); exit 3
        # (control socket lost) stays restartable under Restart=on-failure
        service = unitfiles.render()["cgpd.service"].splitlines()
        assert "Restart=on-failure" in service
        assert "RestartPreventExitStatus=2" in service
        assert not any(line.startswith("RestartForceExitStatus") for line in service)

    def test_exec_start_follows_the_install_root_or_the_venv(self):
        assert "ExecStart=/opt/cgprofile/current/venv/bin/cgpd run\n" in unitfiles.render()["cgpd.service"]
        assert "ExecStart=/srv/cg/current/venv/bin/cgpd run\n" in unitfiles.render("/srv/cg")["cgpd.service"]
        assert "ExecStart=/srv/cg/2.0.0/venv/bin/cgpd run\n" in unitfiles.render(
            "/srv/cg", "/srv/cg/2.0.0/venv/")["cgpd.service"]

    def test_the_service_sets_no_environment_and_no_oom_score(self):
        lines = [line for line in unitfiles.render()["cgpd.service"].splitlines() if not line.startswith("#")]
        assert not any(line.startswith(("Environment", "OOMScoreAdjust")) for line in lines)
        assert "ExecReload=/bin/kill -HUP $MAINPID" in lines
        assert "Slice=cgprofile.slice" in lines and "StartLimitBurst=5" in lines
        assert "RuntimeDirectory=cgprofile/state" in lines
        assert "RuntimeDirectoryMode=0700" in lines and "RuntimeDirectoryPreserve=yes" in lines

    def test_each_socket_unit_has_one_named_listener_and_sysusers_has_default_groups(self):
        rendered = unitfiles.render()
        for name, fd_name, path in (
            ("cgprofile-control.socket", "control", "/run/cgprofile/control/cgprofile.sock"),
            ("cgprofile-observer.socket", "observer", "/run/cgprofile/observer/cgprofile.sock"),
        ):
            lines = rendered[name].splitlines()
            assert [line for line in lines if line.startswith("ListenStream=")] == [f"ListenStream={path}"]
            assert f"FileDescriptorName={fd_name}" in lines
        assert rendered["cgprofile.sysusers"] == (
            "g cgprofile-control - - -\n"
            "g cgprofile-observer - - -\n"
        )

    @pytest.mark.parametrize("bad", ["relative/path", "/with space", "/at@sign", '/quo"te', "/pct%x", "/ctl\x01"])
    def test_unusable_paths_are_refused(self, bad):
        with pytest.raises(unitfiles.UnitError):
            unitfiles.render(bad)
        with pytest.raises(unitfiles.UnitError):
            unitfiles.render("/opt/cgprofile", bad)


class TestFirstDifference:
    def test_equal(self):
        assert unitfiles.first_difference("a\nb\n", "a\nb\n") is None

    def test_a_changed_line(self):
        assert unitfiles.first_difference("a\nb\n", "a\nX\n") == "line 2: expected 'b', installed 'X'"

    def test_installed_is_shorter_and_longer(self):
        assert unitfiles.first_difference("a\nb\n", "a\n") == "line 2: expected 'b', installed '<end of file>'"
        assert unitfiles.first_difference("a\n", "a\nb\n") == "line 2: expected '<end of file>', installed 'b'"

    def test_only_the_trailing_newline(self):
        assert unitfiles.first_difference("a\n", "a") == "trailing newline differs"


class TestPrint:
    def test_both_units_with_separators(self, cli):
        code, out, _ = cli("unit", "print")
        assert code == 0
        assert out.startswith("# ---- cgprofile.slice ----\n") and "# ---- cgpd.service ----\n" in out
        assert "# ---- cgprofile-control.socket ----\n" in out
        assert "# ---- cgprofile-observer.socket ----\n" in out
        assert "# ---- cgprofile.sysusers ----\n" in out

    def test_one_unit_verbatim(self, cli):
        code, out, _ = cli("unit", "print", "--unit", "cgpd.service", "--wheel-venv", "/opt/cgprofile/2.0.0/venv")
        assert code == 0 and out == unitfiles.render(wheel_venv="/opt/cgprofile/2.0.0/venv")["cgpd.service"]

    def test_json(self, cli):
        code, out, _ = cli("unit", "print", "--json")
        doc = json.loads(out.strip().splitlines()[-1])
        assert code == 0 and set(doc["units"]) == set(unitfiles.TEMPLATES) and doc["ok"] is True

    def test_a_bad_path_is_exit_2(self, cli):
        code, _out, err = cli("unit", "print", "--install-root", "relative")
        assert code == 2 and "absolute path" in err

    def test_an_invalid_config_is_exit_2_with_the_check_hint(self, cli, tmp_path):
        config = tmp_path / "bad.toml"
        config.write_text("bogus = 1\n")
        code, _out, err = cli("--config", str(config), "unit", "print")
        assert code == 2 and "bogus" in err and "cgpd config check" in err


class TestCheck:
    @pytest.fixture()
    def installed(self, tmp_path):
        for name, text in unitfiles.render().items():
            (tmp_path / name).write_text(text)
        # the sysusers resource is installed as cgprofile.conf
        (tmp_path / "cgprofile.conf").write_text(unitfiles.render()["cgprofile.sysusers"])
        return tmp_path

    def test_matching_files(self, cli, installed):
        code, out, _ = cli("unit", "check", "--installed", str(installed / "cgpd.service"),
                           str(installed / "cgprofile.slice"))
        assert code == 0 and "units match: " in out

    def test_json(self, cli, installed):
        code, out, _ = cli("unit", "check", "--installed", str(installed / "cgpd.service"), "--json")
        assert json.loads(out.strip().splitlines()[-1])["checked"] == {"cgpd.service": str(installed / "cgpd.service")}

    def test_defaults_to_the_systemd_directory(self, cli, installed, monkeypatch):
        monkeypatch.setattr(unitfiles, "DEFAULT_UNIT_DIR", str(installed))
        monkeypatch.setattr(unitfiles, "DEFAULT_SYSUSERS_DIR", str(installed))
        assert cli("unit", "check")[0] == 0

    def test_the_sysusers_file_is_recognised_by_its_installed_name(self, cli, installed):
        code, out, _ = cli("unit", "check", "--installed", str(installed / "cgprofile.conf"))
        assert code == 0 and "units match" in out

    def test_sysusers_renders_a_line_only_for_default_named_groups(self):
        mapped = {"sockets.control": {"path": "/run/cgprofile/control/cgprofile.sock",
                                      "group": "ops", "mode": "0660"},
                  "sockets.observer": {"path": "/run/cgprofile/observer/cgprofile.sock",
                                       "group": "readers", "mode": "0660"}}
        assert unitfiles.render(config_values=mapped)["cgprofile.sysusers"] == ""
        only_observer = dict(mapped, **{"sockets.observer": dict(mapped["sockets.observer"],
                                                                 group="cgprofile-observer")})
        assert unitfiles.render(config_values=only_observer)["cgprofile.sysusers"] == "g cgprofile-observer - - -\n"

    def test_drift_names_the_first_differing_line(self, cli, installed):
        path = installed / "cgpd.service"
        path.write_text(path.read_text().replace("MemoryMax=768M", "MemoryMax=1G"))
        code, _out, err = cli("unit", "check", "--installed", str(path))
        assert code == 2
        assert "unit drift: cgpd.service:" in err and "expected 'MemoryMax=768M', installed 'MemoryMax=1G'" in err

    def test_several_drifts_are_counted(self, cli, installed):
        for name in ("cgpd.service", "cgprofile.slice"):
            (installed / name).write_text("x\n")
        code, _out, err = cli("unit", "check", "--installed", str(installed / "cgpd.service"),
                              str(installed / "cgprofile.slice"))
        assert code == 2 and "(+1 more)" in err

    def test_a_missing_file_is_drift(self, cli, installed):
        code, _out, err = cli("unit", "check", "--installed", str(installed / "nope" / "cgpd.service"))
        assert code == 2 and "cannot read" in err

    def test_a_foreign_file_name_is_refused(self, cli, installed):
        code, _out, err = cli("unit", "check", "--installed", str(installed / "other.service"))
        assert code == 2 and "not a cgprofile resource" in err

    def test_check_renders_with_the_given_venv(self, cli, installed):
        code, _out, err = cli("unit", "check", "--wheel-venv", "/srv/other/venv", "--installed",
                              str(installed / "cgpd.service"))
        assert code == 2 and "ExecStart=/srv/other/venv/bin/cgpd run" in err

    def test_check_with_a_bad_install_root(self, cli):
        assert cli("unit", "check", "--install-root", "relative")[0] == 2
