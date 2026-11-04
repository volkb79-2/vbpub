"""truenas_rpc: TLS verification is on unless TRUENAS_INSECURE_TLS is explicitly set, and the opt-out warns."""

from __future__ import annotations

import importlib.util
import ssl
import sys
from pathlib import Path

import pytest

from _testdeps import optional_module

PATH = Path(__file__).resolve().parents[1] / "tools" / "console-capture" / "truenas_rpc.py"


@pytest.fixture
def rpc():
    optional_module("websockets", "truenas_rpc imports the websockets client")
    spec = importlib.util.spec_from_file_location("truenas_rpc_under_test", PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "maybe"])
def test_verification_stays_on_unless_explicitly_disabled(rpc, value, capsys):
    environ = {} if value is None else {"TRUENAS_INSECURE_TLS": value}
    assert rpc.insecure_tls_requested(environ) is False
    ctx = rpc.make_ssl_context(insecure=rpc.insecure_tls_requested(environ), environ=environ)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_explicit_opt_out_disables_verification_and_warns(rpc, value, capsys):
    environ = {"TRUENAS_INSECURE_TLS": value}
    assert rpc.insecure_tls_requested(environ) is True
    ctx = rpc.make_ssl_context(insecure=True, environ=environ)
    assert ctx.verify_mode == ssl.CERT_NONE and ctx.check_hostname is False
    err = capsys.readouterr().err
    assert "WARNING" in err and "TRUENAS_INSECURE_TLS" in err


def test_ca_file_keeps_verification_on(rpc, tmp_path):
    with pytest.raises((FileNotFoundError, ssl.SSLError, OSError)):
        rpc.make_ssl_context(environ={"TRUENAS_CA_FILE": str(tmp_path / "missing.pem")})
