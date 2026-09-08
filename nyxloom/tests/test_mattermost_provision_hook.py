"""Unit tests for the Mattermost provisioning hook (nyxloom-P107).

The hook lives at `nyxloom/mattermost/hooks/post_compose_provision.py`, OUTSIDE
`src/nyxloom`, so assay's changed-line judge never considers it — the gate's
coverage scope is `--cov=src/nyxloom`. These tests are therefore a deliberate
act, not a coverage by-product, and every one of them pins a failure that
actually happened rather than a hypothetical:

* `_channel_members` collected whole output lines instead of usernames, so the
  membership guard never matched and every member was re-added on every run.
* `_incoming_webhooks` did not strip mmctl's `Incoming:` prefix, so every
  lookup missed — and a webhook-lookup miss MINTS A SECOND WEBHOOK. Three
  `ciu up` runs produced six webhooks.
* The first cut of the drift guard added to prevent a repeat scanned STDOUT
  only for mmctl's count sentence, which is on STDERR — so it refused every
  deploy against a healthy server. The second cut used that sentence's N as an
  entity count; it is a PRINTED-LINE count (an empty channel prints
  `No users found` and reports "1"), so it refused every empty channel. Both
  were caught on the live instance, and the guard now checks a property that
  needs neither: every stdout row must be recognised.
* `_channel_names` stripped a `*` prefix that mmctl does not emit, while
  missing the ` (private)` / ` (archived)` suffixes it does.

None of these are loud. The first two look like a successful idempotent run.
That is why `_fake_mmctl` below models mmctl's real STREAM SPLIT instead of
collapsing stdout and stderr: a fixture that merges them cannot catch the
third one at all.
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


def _fake_mmctl(stdout: str, stderr: str = "", rc: int = 0):
    """Stub `_mmctl` with mmctl's REAL stream split.

    Entity rows go to stdout; the `There are N <things> on local instance`
    sentence and the `Unable to list outgoing webhooks` noise go to STDERR
    (verified against 11.10.1 with `2>/dev/null` and `2>&1 1>/dev/null`).
    """

    def _impl(_container, *_args, as_json=False):
        return rc, stdout, stderr

    return _impl


# ---------------------------------------------------------------------------
# fixtures — real 11.10.1 output, split by stream
# ---------------------------------------------------------------------------

_CHANNEL_USERS = (
    "aaaadaemonaaaaaaaaaaaaaaaa: nyxloom-daemon (daemon@nyxloom.local) channel_user\n"
    "bbbbadminbbbbbbbbbbbbbbbbb: nyxloom-admin (admin@nyxloom.local) channel_user\n"
    "ccccoperatorcccccccccccccc: nyxloom-operator (operator@nyxloom.local) channel_user\n"
)
_CHANNEL_USERS_ERR = "There are 3 userss on local instance\n"  # sic — mmctl's typo

_WEBHOOK_LIST = (
    "Incoming:\thost-installer (ddddinstallerddddddddddddd)\n"
    "Incoming:\tnyxloom-daemon (eeeedaemonhookeeeeeeeeeeee)\n"
)
_WEBHOOK_LIST_ERR = (
    "Unable to list outgoing webhooks for '9999teamid9999999999999999': "
    "Outgoing webhooks have been disabled by the system admin.\n"
    "There are 2 webhooks on local instance\n"
)

_CHANNEL_LIST = (
    "alerts\n"
    "installs\n"
    "off-topic\n"
    "town-square\n"
    "p107-probe-arch (archived)\n"
    "p107-probe-priv (private)\n"
)
_CHANNEL_LIST_ERR = "There are 6 channels on local instance\n"


# ---------------------------------------------------------------------------
# _channel_members
# ---------------------------------------------------------------------------


def test_channel_members_extracts_usernames(hook, monkeypatch):
    monkeypatch.setattr(
        hook, "_mmctl", _fake_mmctl(_CHANNEL_USERS, _CHANNEL_USERS_ERR)
    )
    assert hook._channel_members("c", "nyxloom", "alerts") == {
        "nyxloom-daemon",
        "nyxloom-admin",
        "nyxloom-operator",
    }


def test_channel_members_ignores_a_count_sentence_on_stdout(hook, monkeypatch):
    # Defensive: the sentence is stderr today, but a future mmctl moving it to
    # stdout must not turn into a member named "There are 3 userss...".
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl(_CHANNEL_USERS + _CHANNEL_USERS_ERR, _CHANNEL_USERS_ERR),
    )
    members = hook._channel_members("c", "nyxloom", "alerts")
    assert not any(m.lower().startswith("there are") for m in members)


def test_channel_members_raises_on_a_failed_probe(hook, monkeypatch):
    # A silent empty set here would make the membership guard un-guard itself.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl("boom", rc=1))
    with pytest.raises(hook.ProvisionError):
        hook._channel_members("c", "nyxloom", "alerts")


def test_channel_members_requests_all_pages(hook, monkeypatch):
    # F3: `channel users list` pages at 200 by default; an unpaged read
    # silently under-reports past that and re-triggers the re-add loop.
    seen: list[tuple] = []

    def _impl(_container, *args, as_json=False):
        seen.append(args)
        return 0, _CHANNEL_USERS, _CHANNEL_USERS_ERR

    monkeypatch.setattr(hook, "_mmctl", _impl)
    hook._channel_members("c", "nyxloom", "alerts")
    assert "--all" in seen[0]


def test_channel_members_refuses_on_a_drifted_row_format(hook, monkeypatch):
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl("nyxloom-daemon\nnyxloom-admin\n", "There are 2 userss on local instance\n"),
    )
    with pytest.raises(hook.ProvisionError):
        hook._channel_members("c", "nyxloom", "alerts")


# ---------------------------------------------------------------------------
# _incoming_webhooks
# ---------------------------------------------------------------------------


def test_incoming_webhooks_strips_the_prefix_and_pairs_name_to_id(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST, _WEBHOOK_LIST_ERR))
    hooks = hook._incoming_webhooks("c", "nyxloom")
    assert {h["display_name"]: h["id"] for h in hooks} == {
        "host-installer": "ddddinstallerddddddddddddd",
        "nyxloom-daemon": "eeeedaemonhookeeeeeeeeeeee",
    }


def test_incoming_webhooks_ignores_the_outgoing_failure_noise(hook, monkeypatch):
    # That line ends in `.` and contains " (" — a parser keyed only on those
    # would swallow it. It is on stderr in reality; fed through stdout here so
    # the row filter itself is what is being tested.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl(_WEBHOOK_LIST_ERR + _WEBHOOK_LIST, _WEBHOOK_LIST_ERR),
    )
    assert len(hook._incoming_webhooks("c", "nyxloom")) == 2


def test_parse_webhook_id_reads_the_created_id(hook):
    created = "Id: ffffcreatedhookffffffffff\nDisplay Name: nyxloom-daemon\n"
    assert hook._parse_webhook_id(created) == "ffffcreatedhookffffffffff"


def test_parse_webhook_id_returns_none_when_absent(hook):
    assert hook._parse_webhook_id("Display Name: nyxloom-daemon\n") is None


# ---------------------------------------------------------------------------
# F1 — the guard that defends against the ACTUAL incident: an EMPTY parse
# ---------------------------------------------------------------------------
#
# The six-webhook incident was NOT "saw two, picked wrong". It was
# `_incoming_webhooks` returning [] because the `Incoming:` prefix was not
# stripped: an empty parse reads as "nothing exists", so the caller creates —
# every run, unbounded and silent. `len(found) > 1` can never see that state.


def test_incoming_webhooks_refuses_when_the_row_format_drifts(hook, monkeypatch):
    drifted = (
        "IncomingHook:\thost-installer (ddddinstallerddddddddddddd)\n"
        "IncomingHook:\tnyxloom-daemon (eeeedaemonhookeeeeeeeeeeee)\n"
    )
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(drifted, _WEBHOOK_LIST_ERR))
    with pytest.raises(hook.ProvisionError) as excinfo:
        hook._incoming_webhooks("c", "nyxloom")
    message = str(excinfo.value)
    assert "2 row(s)" in message
    # S4.23: the refusal must not echo a row — a webhook row carries an id.
    assert "eeeedaemonhookeeeeeeeeeeee" not in message


def test_ensure_webhooks_does_not_mint_over_an_unparseable_list(hook, monkeypatch):
    # The end-to-end shape of the incident: the caller must ABORT, not create.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl(
            "IncomingHook:\tnyxloom-daemon (eeeedaemonhookeeeeeeeeeeee)\n",
            "There are 1 webhooks on local instance\n",
        ),
    )
    with pytest.raises(hook.ProvisionError):
        hook._ensure_webhooks("c", _CONFIG, _one_webhook_spec())


def test_incoming_webhooks_does_not_depend_on_the_count_sentence(hook, monkeypatch):
    # The guard deliberately no longer consults `There are N ...`: that N is a
    # printed-LINE count, not an entity count. Parsing must succeed with the
    # sentence absent entirely.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST, ""))
    assert len(hook._incoming_webhooks("c", "nyxloom")) == 2


def test_incoming_webhooks_accepts_a_genuinely_empty_instance(hook, monkeypatch):
    # reported == 0 and parsed == 0 is the legitimate first-run state; the
    # guard must NOT fire here or nothing could ever be provisioned.
    empty_err = (
        "Unable to list outgoing webhooks for '9999teamid9999999999999999': "
        "Outgoing webhooks have been disabled by the system admin.\n"
        "There are 0 webhooks on local instance\n"
    )
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl("", empty_err))
    assert hook._incoming_webhooks("c", "nyxloom") == []


def test_empty_channel_sentinel_is_not_drift(hook, monkeypatch):
    # THE false positive that the count-sentence guard produced: mmctl prints
    # `No users found` on stdout for an empty channel and reports
    # `There are 1 userss on local instance` on stderr — one "user", zero
    # users. Treating that N as an entity count refuses a healthy channel.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl("No users found\n", "There are 1 userss on local instance\n"),
    )
    assert hook._channel_members("c", "nyxloom", "empty") == set()


def test_noise_lines_are_never_counted_as_unrecognised_rows(hook):
    # The count sentence and the outgoing-webhook warning are stderr today,
    # but that is mmctl's choice, not a contract: if either moves to stdout it
    # must not read as a drifted data row and start refusing deploys.
    assert hook._is_noise("There are 4 channels on local instance")
    assert hook._is_noise("No users found")
    assert hook._is_noise(
        "Unable to list outgoing webhooks for 'x': Outgoing webhooks have been disabled."
    )
    assert not hook._is_noise("alerts")


def test_outgoing_webhook_rows_are_skipped_not_treated_as_drift(hook, monkeypatch):
    # This stack disables outgoing webhooks; re-enabling them must not start
    # failing deploys over rows this parser never claimed to read.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl(
            _WEBHOOK_LIST + "Outgoing:\tsomething (999999999999999999999999x)\n",
            "There are 3 webhooks on local instance\n",
        ),
    )
    assert len(hook._incoming_webhooks("c", "nyxloom")) == 2


# ---------------------------------------------------------------------------
# F2 — private/archived channel suffixes (verified against mmctl 11.10.1)
# ---------------------------------------------------------------------------


def test_channel_names_strips_the_private_suffix(hook, monkeypatch):
    # `private = true` is a shipped config option. Without stripping, the next
    # run does not match the declared name, tries to re-create the channel,
    # and _must aborts the deploy — permanently, on every later run.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_CHANNEL_LIST, _CHANNEL_LIST_ERR))
    names = hook._channel_names("c", "nyxloom")
    assert "p107-probe-priv" in names
    assert "p107-probe-priv (private)" not in names


def test_channel_names_drops_archived_channels(hook, monkeypatch):
    # `all_channels = true` feeds this list straight into _channel_members;
    # an archived name would make mmctl fail and abort every later `ciu up`.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_CHANNEL_LIST, _CHANNEL_LIST_ERR))
    names = hook._channel_names("c", "nyxloom")
    assert not any("archived" in n for n in names)
    assert "p107-probe-arch" not in names


def test_channel_names_does_not_strip_a_star_prefix_that_does_not_exist(hook, monkeypatch):
    # The removed defence. A channel legitimately named `*weird` must survive;
    # mmctl marks private channels with a SUFFIX, never a prefix.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl("*weird\n", "There are 1 channels on local instance\n"),
    )
    assert hook._channel_names("c", "nyxloom") == ["*weird"]


def test_channel_names_returns_exactly_the_active_channels(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_CHANNEL_LIST, _CHANNEL_LIST_ERR))
    assert hook._channel_names("c", "nyxloom") == [
        "alerts",
        "installs",
        "off-topic",
        "town-square",
        "p107-probe-priv",
    ]


def test_channel_names_counts_archived_toward_the_drift_check(hook, monkeypatch):
    # An all-archived team parses to zero ACTIVE names, which must not be
    # mistaken for a format drift.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl("old (archived)\n", "There are 1 channels on local instance\n"),
    )
    assert hook._channel_names("c", "nyxloom") == []


def test_channel_names_refuses_an_unknown_kind_suffix(hook, monkeypatch):
    # mmctl marks channel kinds with a parenthesised suffix. An unknown one
    # (` (shared)`, ` (deleted)`, ...) would otherwise become part of the
    # name, match no declaration, and send _ensure_channels off to re-create a
    # channel that already exists — the abort-forever failure mode.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl("alerts\nsomething (shared)\n", "There are 2 channels on local instance\n"),
    )
    with pytest.raises(hook.ProvisionError) as excinfo:
        hook._channel_names("c", "nyxloom")
    assert "1 row(s)" in str(excinfo.value)


def test_channel_names_empty_team_is_not_drift(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl("", "There are 0 channels on local instance\n"))
    assert hook._channel_names("c", "nyxloom") == []


def test_ensure_memberships_names_archiving_as_a_cause(hook, monkeypatch):
    # A declared channel that has been archived disappears from the active
    # list; the operator needs to be told which of the two causes it is.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_CHANNEL_LIST, _CHANNEL_LIST_ERR))
    with pytest.raises(hook.ProvisionError) as excinfo:
        hook._ensure_memberships(
            "c",
            {
                "team": "nyxloom",
                "accounts": [{"username": "nyxloom-daemon", "channels": ["gone"]}],
            },
        )
    assert "ARCHIVED" in str(excinfo.value)


# ---------------------------------------------------------------------------
# _ensure_webhooks — URL bases and the ambiguity refusal
# ---------------------------------------------------------------------------

_CONFIG = {
    "mattermost": {
        "container_prefix": "nyxloom-prod",
        "expose_public": False,
        "app": {"port": 8065},
    }
}


def _one_webhook_spec(secret="daemon_webhook_url", url_base="internal"):
    return {
        "team": "nyxloom",
        "webhooks": [
            {
                "secret": secret,
                "channel": "alerts",
                "user": "nyxloom-daemon",
                "display_name": "nyxloom-daemon",
                "url_base": url_base,
            }
        ],
    }


def test_ensure_webhooks_builds_the_internal_url(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST, _WEBHOOK_LIST_ERR))
    urls = hook._ensure_webhooks("c", _CONFIG, _one_webhook_spec())
    assert urls == {
        "daemon_webhook_url": (
            "http://nyxloom-prod-mattermost:8065/hooks/eeeedaemonhookeeeeeeeeeeee"
        )
    }


def test_ensure_webhooks_siteurl_tracks_expose_public(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_WEBHOOK_LIST, _WEBHOOK_LIST_ERR))
    config = {
        "mattermost": {
            "container_prefix": "nyxloom-prod",
            "expose_public": True,
            "public_host": "mattermost.example.test",
            "app": {"port": 8065},
        }
    }
    spec = _one_webhook_spec(secret="installer_webhook_url", url_base="siteurl")
    spec["webhooks"][0]["display_name"] = "host-installer"
    urls = hook._ensure_webhooks("c", config, spec)
    assert urls["installer_webhook_url"].startswith(
        "https://mattermost.example.test/hooks/"
    )


def test_ensure_webhooks_refuses_a_duplicated_display_name(hook, monkeypatch):
    duplicated = (
        "Incoming:\tnyxloom-daemon (aaaaaaaaaaaaaaaaaaaaaaaaaa)\n"
        "Incoming:\tnyxloom-daemon (bbbbbbbbbbbbbbbbbbbbbbbbbb)\n"
    )
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl(duplicated, "There are 2 webhooks on local instance\n"),
    )
    with pytest.raises(hook.ProvisionError) as excinfo:
        hook._ensure_webhooks("c", _CONFIG, _one_webhook_spec())
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
    assert any(
        "daemon_webhook_url" in f and "S9.4a" in f
        for f in hook.validate_config(config, None)
    )


def test_validate_config_flags_all_channels_contradicting_an_explicit_list(hook):
    config = _valid_provision_config()
    config["mattermost"]["provision"]["accounts"][0]["all_channels"] = True
    assert any("contradict" in f for f in hook.validate_config(config, None))


def test_validate_config_returns_a_list_not_a_bool(hook):
    # S9.5: a True/False return is a contract violation, never a verdict.
    assert isinstance(hook.validate_config({}, None), list)
