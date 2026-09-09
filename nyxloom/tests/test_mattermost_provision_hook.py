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
import re
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


# ---------------------------------------------------------------------------
# _team_exists (nyxloom-P110)
#
# The bug this replaces was not subtle in effect and completely invisible in
# code: `mmctl --local team search <missing>` exits **0**, so a returncode
# read said "the team exists", `_ensure_team` skipped `team create`, and the
# next step aborted the deploy with `unable to find team "nyxloom"`. It could
# only ever fire on a genuinely empty instance, which is precisely the case
# nobody had run.
# ---------------------------------------------------------------------------

_TEAM_FOUND = "nyxloom: nyxloom (9999teamid9999999999999999)\n"
# Verbatim 11.10.1 capture, including the exit code that made this a bug.
_TEAM_MISSING = "Unable to find team 'nyxloom'\n"


def test_team_exists_true_for_a_real_row(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_TEAM_FOUND))
    assert hook._team_exists("c", "nyxloom") is True


def test_team_exists_false_on_the_not_found_sentinel_despite_rc_zero(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl(_TEAM_MISSING, rc=0))
    assert hook._team_exists("c", "nyxloom") is False


def test_team_exists_requires_an_exact_name_not_a_prefix_hit(hook, monkeypatch):
    # `mmctl team search probe` prints `probe-team: ...` — mmctl matches on a
    # prefix, so "the search returned a row" is not "the team exists". A
    # substring hit would make `_ensure_team` skip creating `nyxloom` because
    # an unrelated `nyxloom-archive` happened to exist.
    monkeypatch.setattr(
        hook,
        "_mmctl",
        _fake_mmctl("nyxloom-archive: Archive (8888888888888888888888888)\n"),
    )
    assert hook._team_exists("c", "nyxloom") is False


def test_team_exists_raises_when_the_probe_itself_fails(hook, monkeypatch):
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl("", "boom", rc=1))
    with pytest.raises(hook.ProvisionError):
        hook._team_exists("c", "nyxloom")


def test_team_exists_refuses_a_drifted_row_rather_than_reporting_absent(hook, monkeypatch):
    # Reporting "absent" on an unparseable row would send `_ensure_team` off to
    # re-create a team that exists; `team create` fails, and `_must` then
    # aborts every subsequent deploy.
    monkeypatch.setattr(hook, "_mmctl", _fake_mmctl("nyxloom | nyxloom | id\n"))
    with pytest.raises(hook.ProvisionError) as excinfo:
        hook._team_exists("c", "nyxloom")
    assert "team search" in str(excinfo.value)


def test_team_exists_ignores_the_count_sentence_on_stdout(hook, monkeypatch):
    monkeypatch.setattr(
        hook, "_mmctl", _fake_mmctl("There are 1 teams on local instance\n" + _TEAM_FOUND)
    )
    assert hook._team_exists("c", "nyxloom") is True


def test_ensure_team_creates_when_the_instance_is_empty(hook, monkeypatch):
    calls: list[tuple] = []

    def _impl(_container, *args, as_json=False):
        calls.append(args)
        if args[:2] == ("team", "search"):
            return 0, _TEAM_MISSING, ""
        return 0, "New team nyxloom successfully created\n", ""

    monkeypatch.setattr(hook, "_mmctl", _impl)
    assert hook._ensure_team("c", {"team": "nyxloom"}) is True
    assert any(a[:2] == ("team", "create") for a in calls)


# ---------------------------------------------------------------------------
# _demote_unintended_admins (nyxloom-P110)
#
# Mattermost promotes the FIRST-EVER account on an empty server to
# system_admin (measured: account #1 -> "system_admin system_user", #2 ->
# "system_user"). Without this step a fresh `ciu up` handed server
# administration to whichever entry heads the accounts list.
# ---------------------------------------------------------------------------

_ACCOUNTS = [
    {"username": "nyxloom-admin", "system_admin": True},
    {"username": "nyxloom-daemon", "system_admin": False},
    {"username": "nyxloom-operator", "system_admin": True},
    {"username": "nyxloom-installer", "system_admin": False},
]


def _roles_stub(hook, monkeypatch, roles: dict[str, set[str]]):
    monkeypatch.setattr(hook, "_user_roles", lambda _c, u: roles.get(u, {"system_user"}))
    calls: list[tuple] = []
    monkeypatch.setattr(
        hook, "_must", lambda _c, *args, what="": calls.append(args) or ""
    )
    return calls


def test_demote_strips_the_auto_granted_role_from_a_created_account(hook, monkeypatch):
    calls = _roles_stub(
        hook, monkeypatch, {"nyxloom-daemon": {"system_admin", "system_user"}}
    )
    demoted = hook._demote_unintended_admins(
        "c", {"accounts": _ACCOUNTS}, ["nyxloom-daemon", "nyxloom-operator"]
    )
    assert demoted == ["nyxloom-daemon"]
    assert calls == [("roles", "member", "nyxloom-daemon")]


def test_demote_never_touches_an_account_this_run_did_not_create(hook, monkeypatch):
    # P107's rule, unchanged: an operator's manual promotion of a pre-existing
    # account must survive every reconcile. This is why the step keys off the
    # created list and not off the declared roles.
    #
    # `created` is deliberately NON-EMPTY and simply does not contain the
    # promoted account. An empty list would prove nothing: the function
    # short-circuits on `if not created`, so a version that ignored the
    # created list entirely still passed (caught by deliberate mutation).
    calls = _roles_stub(
        hook, monkeypatch, {"nyxloom-daemon": {"system_admin", "system_user"}}
    )
    assert (
        hook._demote_unintended_admins(
            "c", {"accounts": _ACCOUNTS}, ["nyxloom-installer"]
        )
        == []
    )
    assert calls == []


def test_demote_short_circuits_when_nothing_was_created(hook, monkeypatch):
    calls = _roles_stub(
        hook, monkeypatch, {"nyxloom-daemon": {"system_admin", "system_user"}}
    )
    assert hook._demote_unintended_admins("c", {"accounts": _ACCOUNTS}, []) == []
    assert calls == []


def test_demote_leaves_a_declared_system_admin_alone(hook, monkeypatch):
    calls = _roles_stub(
        hook, monkeypatch, {"nyxloom-operator": {"system_admin", "system_user"}}
    )
    assert (
        hook._demote_unintended_admins("c", {"accounts": _ACCOUNTS}, ["nyxloom-operator"])
        == []
    )
    assert calls == []


def test_demote_is_a_no_op_when_the_role_was_never_granted(hook, monkeypatch):
    # The live instance's shape: `nyxloom-admin` already exists, so nothing the
    # hook creates is ever account #1 and nothing is ever promoted.
    calls = _roles_stub(hook, monkeypatch, {})
    assert (
        hook._demote_unintended_admins("c", {"accounts": _ACCOUNTS}, ["nyxloom-daemon"])
        == []
    )
    assert calls == []


# ---------------------------------------------------------------------------
# The SHIPPED files, not a synthetic dict (P109's precedent).
#
# Every assertion below pins something a live measurement proved wrong; a
# hand-built config fixture would pass while the file that actually deploys
# regressed.
# ---------------------------------------------------------------------------

_STACK = _HOOK_PATH.parents[1]
_DEFAULTS_TEXT = (_STACK / "ciu.defaults.toml.j2").read_text(encoding="utf-8")
_COMPOSE_TEXT = (_STACK / "ciu.compose.yml.j2").read_text(encoding="utf-8")
_FALLBACK_TEXT = (_STACK / "docker-compose.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "key, value",
    [
        # Measured: ENABLEOPENSERVER=false alone still let
        # `POST /api/v4/users?iid=<invite_id>` create an account (201).
        ("MM_TEAMSETTINGS_ENABLEUSERCREATION", "false"),
        ("MM_TEAMSETTINGS_ENABLEOPENSERVER", "false"),
        # Measured default: true.
        ("MM_SERVICESETTINGS_ENABLEOAUTHSERVICEPROVIDER", "false"),
        ("MM_SERVICESETTINGS_ENABLEEMAILINVITATIONS", "false"),
        ("MM_SERVICESETTINGS_ENABLESECURITYFIXALERT", "false"),
        # Measured default: 4320 hours (180 days).
        ("MM_SERVICESETTINGS_SESSIONLENGTHWEBINHOURS", "168"),
        # Measured default: false, with no rate limiting at the tls-edge layer
        # either (ARCHITECTURE.md F5 is unimplemented).
        ("MM_RATELIMITSETTINGS_ENABLE", "true"),
        # Measured default: true — leaks every account's email AND roles to
        # any authenticated user.
        ("MM_PRIVACYSETTINGS_SHOWEMAILADDRESS", "false"),
        ("MM_PRIVACYSETTINGS_SHOWFULLNAME", "false"),
    ],
)
def test_compose_template_carries_the_p110_hardening(key, value):
    assert f'{key}: "{value}"' in _COMPOSE_TEXT


@pytest.mark.parametrize(
    "key, value",
    [
        ("MM_TEAMSETTINGS_ENABLEUSERCREATION", "false"),
        ("MM_SERVICESETTINGS_ENABLEOAUTHSERVICEPROVIDER", "false"),
        ("MM_RATELIMITSETTINGS_ENABLE", "true"),
        ("MM_PRIVACYSETTINGS_SHOWEMAILADDRESS", "false"),
    ],
)
def test_prerendered_fallback_carries_the_same_hardening(key, value):
    # The plain-compose fallback receives NO ciu overlay, so it has to carry
    # these by hand — the same reason it carries governance caps by hand, and
    # the same trap (NL-6) if the two drift.
    assert f'{key}: "{value}"' in _FALLBACK_TEXT


def test_rate_limit_keying_follows_the_exposure_branch():
    # Behind tls-edge every request arrives from the Traefik container, so
    # VaryByRemoteAddr would put the whole internet in one bucket; with no
    # proxy in front there is no X-Forwarded-For to key on. Both halves must
    # therefore live inside the `expose_public` conditional.
    exposed = _COMPOSE_TEXT.split("{% if mattermost.expose_public %}")
    keyed = [
        block
        for block in exposed[1:]
        if "MM_RATELIMITSETTINGS_VARYBYHEADER" in block.split("{% endif %}")[0]
    ]
    assert keyed, "VARYBYHEADER must be emitted only when expose_public is true"
    assert 'MM_RATELIMITSETTINGS_VARYBYHEADER: "X-Forwarded-For"' in _COMPOSE_TEXT


def test_expose_public_stays_false_in_the_committed_defaults():
    # nyxloom-P110 hardens and documents the flip; it does not perform it. The
    # controller flips exposure live, per the README recipe.
    #
    # Anchored to the START of a line: the file also mentions
    # "`expose_public = false`" inside a comment about the installer webhook's
    # URL base, and a substring check therefore passed happily with the real
    # assignment flipped to `true` (caught by deliberate mutation).
    assignments = [
        line
        for line in _DEFAULTS_TEXT.splitlines()
        if line.startswith("expose_public")
    ]
    assert assignments == ["expose_public = false"]


def test_all_four_accounts_are_declared_with_admin_first():
    # The operator's requirement is "our 4 users are created on fresh stack
    # create". Measured on a genuinely empty instance, P107's config produced
    # THREE — nothing created `nyxloom-admin`. Order matters because
    # Mattermost promotes the first-ever account to system_admin.
    order = [
        line.split("=", 1)[1].strip().strip('"')
        for line in _DEFAULTS_TEXT.splitlines()
        if line.startswith("username = ")
    ]
    assert order == [
        "nyxloom-admin",
        "nyxloom-daemon",
        "nyxloom-operator",
        "nyxloom-installer",
    ]


def test_fallback_does_not_drift_from_the_template():
    """Every literal MM_* setting must match between the two compose files.

    Stronger than the hand-picked keys above, and the reason it exists: the
    pre-rendered `docker-compose.yml` receives NO ciu overlay, so it carries
    the whole env block (and the governance caps) by hand. Two files edited by
    hand drift, and NL-6 is what that costs — weeks of an unconfined container
    because one path silently disagreed with the other.

    Compared only where comparison is meaningful:

    * Keys whose TEMPLATE value contains a Jinja expression are skipped —
      SITEURL, the DSN and LISTENADDRESS are the same setting rendered, not a
      disagreement.
    * `MM_RATELIMITSETTINGS_VARYBYHEADER` is expected in the template ONLY: it
      is emitted inside the `expose_public` branch, and the fallback is the
      `expose_public = false` rendering, where keying on an absent
      X-Forwarded-For would be wrong.
    """
    pattern = re.compile(r'^\s+(MM_[A-Z0-9_]+): "(.*)"$', re.M)
    template = dict(pattern.findall(_COMPOSE_TEXT))
    fallback = dict(pattern.findall(_FALLBACK_TEXT))

    exposed_only = {"MM_RATELIMITSETTINGS_VARYBYHEADER"}
    literal = {k: v for k, v in template.items() if "{{" not in v}

    missing = sorted(set(literal) - set(fallback) - exposed_only)
    assert not missing, f"template settings absent from the fallback: {missing}"

    extra = sorted(set(fallback) - set(template))
    assert not extra, f"fallback carries settings the template does not: {extra}"

    mismatched = {
        k: (literal[k], fallback[k])
        for k in set(literal) & set(fallback)
        if literal[k] != fallback[k]
    }
    assert not mismatched, f"value drift between the two compose files: {mismatched}"
