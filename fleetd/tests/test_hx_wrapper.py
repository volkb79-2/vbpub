"""Which hx wrapper fleetd launches: --hx, then FLEETD_HX, then the source checkout (FD-023).

Nothing here launches a wrapper or contacts a host; the paths are only resolved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleetd import cli, transport as transport_module
from fleetd.cli import main
from fleetd.transport import HX_ENVIRONMENT, HxNotConfigured, HxTransport, TransportError, resolve_hx_wrapper



@pytest.fixture
def checkout(monkeypatch, tmp_path):
    """A source checkout of its own: the package three levels below a root holding the wrapper."""
    monkeypatch.delenv(HX_ENVIRONMENT, raising=False)
    wrapper = tmp_path / "scripts" / "controller" / "hx.sh"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(transport_module, "__file__", str(tmp_path / "fleetd" / "src" / "fleetd" / "transport.py"))
    return wrapper


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """Make the package look installed: no source checkout beside it, no FLEETD_HX."""
    monkeypatch.delenv(HX_ENVIRONMENT, raising=False)
    monkeypatch.setattr(transport_module, "__file__", str(tmp_path / "site" / "lib" / "fleetd" / "transport.py"))
    return tmp_path


def test_the_source_checkout_default_still_works_without_any_setting(checkout):
    assert resolve_hx_wrapper() == checkout
    assert HxTransport().wrapper == checkout


def test_the_environment_beats_the_checkout_default(monkeypatch, tmp_path):
    monkeypatch.setenv(HX_ENVIRONMENT, str(tmp_path / "from-env.sh"))
    assert resolve_hx_wrapper() == tmp_path / "from-env.sh"
    assert resolve_hx_wrapper(environ={HX_ENVIRONMENT: str(tmp_path / "given.sh")}) == tmp_path / "given.sh"


def test_the_argument_beats_the_environment_and_the_checkout_default(monkeypatch, tmp_path):
    monkeypatch.setenv(HX_ENVIRONMENT, str(tmp_path / "from-env.sh"))
    assert resolve_hx_wrapper(tmp_path / "from-arg.sh") == tmp_path / "from-arg.sh"
    assert HxTransport(str(tmp_path / "from-arg.sh")).wrapper == tmp_path / "from-arg.sh"
    assert HxTransport(tmp_path / "from-arg.sh", environ={HX_ENVIRONMENT: "/elsewhere/hx.sh"}).wrapper == tmp_path / "from-arg.sh"


def test_an_empty_environment_value_counts_as_unset(monkeypatch, checkout):
    monkeypatch.setenv(HX_ENVIRONMENT, "")
    assert resolve_hx_wrapper() == checkout


def test_a_bare_name_is_made_absolute_so_the_launch_never_searches_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert resolve_hx_wrapper("hx") == tmp_path / "hx"
    monkeypatch.setenv(HX_ENVIRONMENT, "hx")
    assert resolve_hx_wrapper() == tmp_path / "hx"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert resolve_hx_wrapper("~/bin/hx") == tmp_path / "home" / "bin" / "hx"


def test_an_installed_fleetd_with_no_setting_refuses_with_one_actionable_line(installed):
    with pytest.raises(HxNotConfigured, match=r"--hx PATH or set FLEETD_HX") as caught:
        resolve_hx_wrapper()
    assert "\n" not in str(caught.value)
    assert isinstance(caught.value, TransportError)
    # An hx on PATH is never picked up on the way.
    with pytest.raises(HxNotConfigured):
        HxTransport(environ={"PATH": "/usr/bin"})


def test_the_cli_exits_3_with_the_install_hint_when_nothing_names_a_wrapper(installed, capsys):
    state = installed / "state"
    assert main(["status", "c", "--state-dir", str(state)]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--hx PATH or set FLEETD_HX" in captured.err
    assert "Traceback" not in captured.err and "could not launch" not in captured.err
    assert not state.exists()


def test_the_cli_hands_its_argument_to_the_transport_and_the_argument_wins(monkeypatch, installed, capsys):
    seen = []

    def record(wrapper=None, **_kwargs):
        seen.append(wrapper)
        raise HxNotConfigured("stop before any state is read")

    monkeypatch.setattr(cli, "HxTransport", record)
    monkeypatch.setenv(HX_ENVIRONMENT, str(installed / "from-env.sh"))
    assert main(["status", "c", "--hx", str(installed / "from-arg.sh"), "--state-dir", str(installed / "s")]) == 3
    assert seen == [installed / "from-arg.sh"]
    assert main(["status", "c", "--state-dir", str(installed / "s")]) == 3
    assert seen[-1] is None
    capsys.readouterr()


def test_the_cli_runs_with_the_environment_wrapper_when_only_the_environment_is_set(monkeypatch, installed, capsys):
    monkeypatch.setenv(HX_ENVIRONMENT, str(installed / "from-env.sh"))
    # The wrapper is only resolved, never launched: the missing state is the refusal.
    assert main(["status", "c", "--state-dir", str(installed / "state")]) == 2
    assert "campaign state does not exist" in capsys.readouterr().err
