"""Unit tests for the Mattermost provisioning hook (nyxloom-P107).

The hook lives at `nyxloom/mattermost/hooks/post_compose_provision.py`, OUTSIDE
`src/nyxloom`, so assay's changed-line judge never considers it — the gate's
coverage scope is `--cov=src/nyxloom`. That is exactly why these tests exist as
a deliberate act rather than as a coverage by-product: two of the parsers below
shipped WRONG on their first live run and were caught only because the live
instance grew duplicates.

Both regressions are pinned here:

* `_channel_members` collected whole output lines instead of usernames, so the
  membership guard never matched and every member was re-added on every run.
* `_incoming_webhooks` did not strip mmctl's `Incoming:` prefix, so every
  webhook lookup missed — and a miss MINTS A SECOND WEBHOOK. Three `ciu up`
  runs produced six webhooks.

Neither failure is loud: both look like a successful, idempotent run.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "mattermost"
    / "hooks"
    / "post_compose_provision.py"
)


def _load_hook():
    spec = importlib.util.spec_from_file_location("_p107_provision_hook", _HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hook():
    return _load_hook()


def _fake_mmctl(output: str, rc: int = 0):
    def _impl(_container, *_args, as_json=False):
        return rc, output, ""

    return _impl


# ---------------------------------------------------------------------------
# _channel_members — real mmctl 11.10.1 output shape
# ---------------------------------------------------------------------------

_CHANNEL_USERS = (
    "1645jrsniibymkhgq818gezoer: nyxloom-daemon (daemon@nyxloom.local) channel_user\n"
    "htwfwi6dofbz5fa6kfn6ygr16a: nyxloom-admin (admin@nyxloom.local) channel_user\n"
    "m9zfp5zwop88xxx7i5bz1cy9da: nyxloom-operator (operator@nyxloom.local) channel_user\n"
    "There are 3 userss on local instance\n"  # sic — mmctl's own typo
)


def test_channel_members_extracts_usernames(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_CHANNEL_USERS))
    assert hook._channel_members("c", "nyxloom", "alerts") == {
        "nyxloom-daemon",
        "nyxloom-admin",
        "nyxloom-operator",
    }


def test_channel_members_ignores_the_trailing_count_sentence(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_CHANNEL_USERS))
    members = hook._channel_members("c", "nyxloom", "alerts")
    assert not any(m.lower().startswith("there are") for m in members)


def test_channel_members_raises_on_a_failed_probe(hook, monkeypatch):
    # A silent empty set here would make the membership guard un-guard itself.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl("boom", rc=1))
    with pytest.raises(hook.ProvisionError):
        hook._channel_members("c", "nyxloom", "alerts")


# ---------------------------------------------------------------------------
# _incoming_webhooks — the `Incoming:<TAB>` prefix is load-bearing
# ---------------------------------------------------------------------------

_WEBHOOK_LIST = (
    "Unable to list outgoing webhooks for 'enokmstek3y5xyo73tzcuc6h5y': "
    "Outgoing webhooks have been disabled by the system admin.\n"
    "There are 2 webhooks on local instance\n"
    "Incoming:\thost-installer (hzfa9hyijpnj5f4s5b3o4rfe3w)\n"
    "Incoming:\tnyxloom-daemon (4jtnt78j53dwffgonon9xenmdr)\n"
)


def test_incoming_webhooks_strips_the_prefix_and_pairs_name_to_id(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST))
    hooks = hook._incoming_webhooks("c", "nyxloom")
    assert {h["display_name"]: h["id"] for h in hooks} == {
        "host-installer": "hzfa9hyijpnj5f4s5b3o4rfe3w",
        "nyxloom-daemon": "4jtnt78j53dwffgonon9xenmdr",
    }


def test_incoming_webhooks_skips_the_outgoing_failure_line(hook, monkeypatch):
    # That line ends in a `.`, not a `)`, but it also contains " (" — a parser
    # keyed only on those two would have swallowed it as a webhook.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST))
    assert len(hook._incoming_webhooks("c", "nyxloom")) == 2


def test_parse_webhook_id_reads_the_created_id(hook):
    created = "Id: q4goa4dkctbfbyckkprf88nbgo\nDisplay Name: nyxloom-daemon\n"
    assert hook._parse_webhook_id(created) == "q4goa4dkctbfbyckkprf88nbgo"


def test_parse_webhook_id_returns_none_when_absent(hook):
    assert hook._parse_webhook_id("Display Name: nyxloom-daemon\n") is None


# ---------------------------------------------------------------------------
# _ensure_webhooks — refuse an ambiguous display name rather than guess
# ---------------------------------------------------------------------------

_CONFIG = {
    "mattermost": {
        "container_prefix": "nyxloom-prod",
        "expose_public": False,
        "app": {"port": 8065},
    }
}


def test_ensure_webhooks_builds_the_internal_url(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST))
    urls = hook._ensure_webhooks(
        "c",
        _CONFIG,
        {
            "team": "nyxloom",
            "webhooks": [
                {
                    "secret": "daemon_webhook_url",
                    "channel": "alerts",
                    "user": "nyxloom-daemon",
                    "display_name": "nyxloom-daemon",
                    "url_base": "internal",
                }
            ],
        },
    )
    assert urls == {
        "daemon_webhook_url": (
            "http://nyxloom-prod-mattermost:8065/hooks/4jtnt78j53dwffgonon9xenmdr"
        )
    }


def test_ensure_webhooks_siteurl_tracks_expose_public(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST))
    config = {
        "mattermost": {
            "container_prefix": "nyxloom-prod",
            "expose_public": True,
            "public_host": "mattermost.example.test",
            "app": {"port": 8065},
        }
    }
    urls = hook._ensure_webhooks(
        "c",
        config,
        {
            "team": "nyxloom",
            "webhooks": [
                {
                    "secret": "installer_webhook_url",
                    "channel": "installs",
                    "user": "nyxloom-installer",
                    "display_name": "host-installer",
                    "url_base": "siteurl",
                }
            ],
        },
    )
    assert urls["installer_webhook_url"].startswith("https://mattermost.example.test/hooks/")


def test_ensure_webhooks_refuses_a_duplicated_display_name(hook, monkeypatch):
    duplicated = (
        "Incoming:\tnyxloom-daemon (aaaaaaaaaaaaaaaaaaaaaaaaaa)\n"
        "Incoming:\tnyxloom-daemon (bbbbbbbbbbbbbbbbbbbbbbbbbb)\n"
    )
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(duplicated))
    with pytest.raises(hook.ProvisionError) as excinfo:
        hook._ensure_webhooks(
            "c",
            _CONFIG,
            {
                "team": "nyxloom",
                "webhooks": [
                    {
                        "secret": "daemon_webhook_url",
                        "channel": "alerts",
                        "user": "nyxloom-daemon",
                        "display_name": "nyxloom-daemon",
                    }
                ],
            },
        )
    message = str(excinfo.value)
    assert "2 incoming webhooks" in message
    # S4.23: the refusal names the count, never the ids it could have picked.
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaa" not in message


# ---------------------------------------------------------------------------
# validate_config (S9.5)
# ---------------------------------------------------------------------------


def _valid_provision_config():
    return {
        "mattermost": {
            "secrets": {"mattermost_daemon_password": {}},
            "provision": {
                "team": "nyxloom",
                "channels": [{"name": "alerts", "display_name": "Alerts"}],
                "accounts": [
                    {
                        "username": "nyxloom-daemon",
                        "email": "daemon@nyxloom.local",
                        "password_secret": "mattermost_daemon_password",
                        "channels": ["alerts"],
                    }
                ],
                "webhooks": [
                    {
                        "secret": "daemon_webhook_url",
                        "channel": "alerts",
                        "user": "nyxloom-daemon",
                        "display_name": "nyxloom-daemon",
                        "url_base": "internal",
                    }
                ],
            },
        }
    }


def test_validate_config_accepts_the_shipped_shape(hook):
    assert hook.validate_config(_valid_provision_config(), None) == []


def test_validate_config_flags_an_undeclared_password_secret(hook):
    config = _valid_provision_config()
    config["mattermost"]["provision"]["accounts"][0]["password_secret"] = "typo_password"
    findings = hook.validate_config(config, None)
    assert any("typo_password" in f and "not declared" in f for f in findings)


def test_validate_config_flags_a_webhook_pointing_at_an_unknown_channel(hook):
    config = _valid_provision_config()
    config["mattermost"]["provision"]["webhooks"][0]["channel"] = "nope"
    assert any("nope" in f for f in hook.validate_config(config, None))


def test_validate_config_flags_a_webhook_secret_a_directive_already_owns(hook):
    # S9.4a refuses this at run time; catching it in preflight turns a
    # mid-reconcile abort into a refusal before anything starts.
    config = _valid_provision_config()
    config["mattermost"]["secrets"]["daemon_webhook_url"] = {}
    assert any("daemon_webhook_url" in f and "S9.4a" in f for f in hook.validate_config(config, None))


def test_validate_config_flags_all_channels_contradicting_an_explicit_list(hook):
    config = _valid_provision_config()
    config["mattermost"]["provision"]["accounts"][0]["all_channels"] = True
    assert any("contradict" in f for f in hook.validate_config(config, None))


def test_validate_config_returns_a_list_not_a_bool(hook):
    # S9.5: a True/False return is a contract violation, never a verdict.
    assert isinstance(hook.validate_config({}, None), list)
