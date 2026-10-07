"""Tests for nyxloom/mattermost/tools/mm_reachability.py.

A fake stack dir (fake ciu.toml + fake secrets) and a fake Docker/HTTP world:
no network, no docker. All secret values below are fake.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "mattermost" / "tools" / "mm_reachability.py"
_spec = importlib.util.spec_from_file_location("mm_reachability", _SCRIPT)
mr = importlib.util.module_from_spec(_spec)
sys.modules["mm_reachability"] = mr
_spec.loader.exec_module(mr)

PREFIX = "nyxloom-abc123"
CONTAINER = f"{PREFIX}-mattermost"
PUBLIC = "mm.example.test"
NETS = ["ingress_public", f"{PREFIX}-mattermost_internal"]
# 26-char Mattermost-shaped ids + one odd-shaped token (registered-redaction path)
HOOK_INT = "abcdefghjkmnpqrstuvwxyz234"
HOOK_SITE = "wxyz234567abcdefghjkmnpqrs"
HOOK_INT2 = "mnpqrstuvwxyz23456abcdefgh"
TOK_BOT = "botx9s8d7f6g5h4j3k2l1q0w9e"
TOK_ODD = "odd-token-NOT-26-chars!"
SECRETS = [HOOK_INT, HOOK_SITE, HOOK_INT2, TOK_BOT, TOK_ODD]
ID26 = re.compile(r"(?<![A-Za-z0-9])[a-z0-9]{26}(?![A-Za-z0-9])")


def make_stack(tmp_path: Path, *, int_host: str = CONTAINER, site_host: str = PUBLIC,
               expose: bool = True) -> Path:
    stack = tmp_path / "stack"
    (stack / ".ciu" / "secrets").mkdir(parents=True)
    (stack / "ciu.toml").write_text(f"""
[mattermost]
name = "mattermost"
container_prefix = "{PREFIX}"
expose_public = {str(expose).lower()}
public_host = "{PUBLIC}"
ingress_network = "ingress_public"

[mattermost.app]
name = "mattermost"
port = 8065

[mattermost.provision]
team = "tm"
channels = [
    {{ name = "alerts", private = true }},
    {{ name = "intake" }},
]

[[mattermost.provision.accounts]]
username = "svc-admin"
password_secret = "admin_password"
channels = ["alerts"]
system_admin = true

[[mattermost.provision.accounts]]
username = "svc-bot"
password_secret = "bot_password"
channels = ["intake"]
system_admin = false

[[mattermost.provision.webhooks]]
secret = "wh_int"
channel = "alerts"
user = "svc-admin"
url_base = "internal"

[[mattermost.provision.webhooks]]
secret = "wh_site"
channel = "intake"
user = "svc-bot"
url_base = "siteurl"

[[mattermost.provision.webhooks]]
secret = "wh_int2"
channel = "intake"
user = "svc-bot"
url_base = "internal"

[[mattermost.provision.tokens]]
secret = "bot_pat"
user = "svc-bot"

[[mattermost.provision.tokens]]
secret = "odd_pat"
user = "svc-bot"
""", encoding="utf-8")
    sec = stack / ".ciu" / "secrets"
    (sec / "wh_int").write_text(f"http://{int_host}:8065/hooks/{HOOK_INT}\n")
    (sec / "wh_site").write_text(f"https://{site_host}/hooks/{HOOK_SITE}\n")
    (sec / "wh_int2").write_text(f"http://{CONTAINER}:8065/hooks/{HOOK_INT2}\n")
    (sec / "bot_pat").write_text(TOK_BOT + "\n")
    (sec / "odd_pat").write_text(TOK_ODD + "\n")
    return stack


class FakeWorld:
    """Fake docker daemon + Mattermost server + DNS, driving mr.Env."""

    def __init__(self) -> None:
        self.container_networks = list(NETS)
        self.local_networks: list[str] = []
        self.local_resolves = False
        self.image_present = True
        self.run_fail = False
        self.ping_body = '{"status":"OK"}'
        self.site_url = f"https://{PUBLIC}"
        self.posts: list[tuple[str, str]] = []
        self.docker_calls: list[list[str]] = []
        self.raise_in_http: Exception | None = None
        self.tokens = {TOK_BOT: "svc-bot", TOK_ODD: "svc-bot"}
        self.users = {
            "svc-admin": {"id": "u1", "username": "svc-admin", "delete_at": 0,
                          "roles": "system_user system_admin"},
            "svc-bot": {"id": "u2", "username": "svc-bot", "delete_at": 0,
                        "roles": "system_user"},
        }

    # --- server ---
    def serve(self, vantage: str, method: str, url: str, headers: dict, body: str | None):
        p = urlsplit(url)
        host = p.hostname
        if self.raise_in_http:
            raise self.raise_in_http
        if host == PUBLIC:
            if vantage != "local":
                return mr.Response(0, error="external not probed from nets")
        elif host == CONTAINER:
            if vantage == "local" and not (self.local_resolves or self.local_networks):
                return mr.Response(0, error="URLError: [Errno -2] Name or service not known")
        else:
            return mr.Response(0, error="Could not resolve host: " + str(host))
        path = p.path
        auth = headers.get("Authorization", "")
        user = self.tokens.get(auth.removeprefix("Bearer ")) if auth.startswith("Bearer ") else None
        j = lambda o, c=200: mr.Response(c, json.dumps(o), 3.0)  # noqa: E731
        if path == "/api/v4/system/ping":
            return mr.Response(200, self.ping_body, 3.0)
        if path == "/api/v4/config/client":
            return j({"SiteURL": self.site_url})
        if path.startswith("/hooks/"):
            self.posts.append((url, body or ""))
            return mr.Response(200, "ok", 3.0)
        if user is None:
            return j({"message": "unauthorized"}, 401)
        if path == "/api/v4/users/me":
            return j(self.users[user])
        m = re.fullmatch(r"/api/v4/users/username/(.+)", path)
        if m:
            u = self.users.get(m.group(1))
            return j(u) if u else j({}, 404)
        if path == "/api/v4/teams/name/tm":
            return j({"id": "tid1"})
        if path == "/api/v4/teams/tid1/channels/name/intake":
            return j({"id": "cid2"})
        if path == "/api/v4/teams/tid1/channels/name/alerts":
            return j({}, 404)  # private: reader is not a member
        if re.fullmatch(r"/api/v4/teams/tid1/members/u[12]", path):
            return j({})
        if re.fullmatch(r"/api/v4/channels/cid2/members/u2", path):
            return j({})
        return j({}, 404)

    # --- docker ---
    def docker(self, args, stdin=None, timeout=60):
        self.docker_calls.append(list(args))
        if args[0] == "inspect":
            if args[1] == CONTAINER:
                return 0, json.dumps([{"State": {"Status": "running", "Health": {"Status": "healthy"}},
                                       "NetworkSettings": {"Networks": {n: {} for n in self.container_networks}}}]), ""
            if self.local_networks:
                return 0, json.dumps([{"NetworkSettings": {"Networks": {n: {} for n in self.local_networks}}}]), ""
            return 1, "", "No such object"
        if args[:2] == ["image", "inspect"]:
            return (0, "", "") if self.image_present else (1, "", "missing")
        if args[0] in ("pull", "rm"):
            return 0, "", ""
        if args[0] == "run":
            if self.run_fail:
                return 1, "", f"boom Authorization: Bearer {TOK_BOT} http://x/hooks/{HOOK_INT}"
            net = args[args.index("--network") + 1]
            marker = re.search(r"@@mmr[a-z]+@@", stdin).group(0)
            out = ""
            for block in stdin.split("\nnext\n"):
                un = lambda s: s.replace('\\"', '"').replace("\\\\", "\\")  # noqa: E731
                url = un(re.search(r'^url = "(.*)"$', block, re.M).group(1))
                method = re.search(r'^request = "(.*)"$', block, re.M).group(1)
                hdrs = dict(un(h).split(": ", 1) for h in re.findall(r'^header = "(.*)"$', block, re.M))
                bm = re.search(r'^data = "(.*)"$', block, re.M)
                r = self.serve(f"net:{net}", method, url, hdrs, un(bm.group(1)) if bm else None)
                out += f"{r.body}\n{marker} {r.status:03d} 0.002 {0 if r.status else 6} {r.error}\n"
            return 0, out, ""
        raise AssertionError(f"unexpected docker call {args}")

    def env(self) -> "mr.Env":
        return mr.Env(
            docker=self.docker,
            http=lambda req, t: self.serve("local", req.method, req.url, req.headers, req.body),
            resolve=lambda h: self.local_resolves,
            hostname=lambda: "devbox",
            now=lambda: datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc),
        )


def run_main(world, stack, capsys, *extra):
    rc = mr.main(["--stack-dir", str(stack), *extra], env=world.env())
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def results(world, stack, capsys, *extra):
    rc, out, _ = run_main(world, stack, capsys, "--json", *extra)
    return rc, json.loads(out)


def find(data, check, vantage=None):
    return [r for r in data["results"] if r["check"] == check and (vantage is None or r["vantage"] == vantage)]


# ---------------------------------------------------------------- discovery
def test_discovery_from_ciu_toml_and_docker(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    rc, data = results(world, stack, capsys)
    d = data["discovery"]
    assert d["container"] == CONTAINER
    assert d["internal_base"] == f"http://{CONTAINER}:8065"
    assert d["external_base"] == f"https://{PUBLIC}"
    assert d["networks"] == sorted(NETS)
    assert d["site_url"] == f"https://{PUBLIC}"
    # one throwaway probe container per network, unique mmreach- names, no host-ns flags
    runs = [c for c in world.docker_calls if c[0] == "run"]
    assert len(runs) == len(NETS)
    for c in runs:
        assert c[c.index("--name") + 1].startswith("mmreach-")
        assert not any(f in c for f in ("--net=host", "--pid=host", "--cgroupns=host"))
    assert {c[c.index("--network") + 1] for c in runs} == set(NETS)
    # no secret value or url on any docker argv
    assert not any(s in " ".join(c) for c in world.docker_calls for s in SECRETS)
    assert rc == 0


def test_expose_public_off_has_no_external_base(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path, expose=False)
    rc, data = results(world, stack, capsys)
    assert data["discovery"]["external_base"] is None
    assert find(data, "ping external")[0]["result"] == "INFO"


def test_default_image_pulled_only_if_missing(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    run_main(world, stack, capsys)
    assert not [c for c in world.docker_calls if c[0] == "pull"]
    world2 = FakeWorld()
    world2.image_present = False
    run_main(world2, stack, capsys)
    assert [c for c in world2.docker_calls if c[0] == "pull"] == [["pull", "-q", mr.DEFAULT_IMAGE]]


# ------------------------------------------------------------- happy path
def test_all_green_exit_0_and_table_shape(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    rc, out, _ = run_main(world, stack, capsys)
    assert rc == 0
    assert "CHECK" in out and "VANTAGE" in out and "RESULT" in out
    assert "SUMMARY:" in out and " 0 FAIL" in out
    # webhook display: only the 4-char prefix of the id
    assert f"http://{CONTAINER}:8065/hooks/{HOOK_INT[:4]}…" in out
    assert f"https://{PUBLIC}:443/hooks/{HOOK_SITE[:4]}…" in out


# -------------------------------------------------------------- stale host
def test_stale_internal_host_is_fail(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path, int_host="nyxloom-old000-mattermost")
    rc, data = results(world, stack, capsys)
    row = find(data, "webhook wh_int", "config")[0]
    assert row["result"] == "FAIL" and "STALE HOST nyxloom-old000-mattermost" in row["detail"]
    # the control hook with the current host stays PASS, and the stale base is unreachable
    assert find(data, "webhook wh_int2", "config")[0]["result"] == "PASS"
    assert all(r["result"] == "FAIL" for r in find(data, "webhook wh_int base"))
    assert rc == 1


def test_stale_siteurl_host_is_fail(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path, site_host="old.example.test")
    rc, data = results(world, stack, capsys)
    row = find(data, "webhook wh_site", "config")[0]
    assert row["result"] == "FAIL" and "STALE HOST old.example.test" in row["detail"]
    assert rc == 1


def test_describe_hook_never_exposes_full_id():
    d = mr.describe_hook(f"http://h:8065/hooks/{HOOK_INT}")
    assert HOOK_INT not in d["display"] and d["display"].endswith(f"/hooks/{HOOK_INT[:4]}…")
    assert (d["scheme"], d["host"], d["port"]) == ("http", "h", 8065)
    bad = mr.describe_hook(f"http://h/weird/{HOOK_INT}")
    assert HOOK_INT not in bad["display"] and bad["id"] is None


# --------------------------------------------------------------- redaction
def _assert_clean(*texts):
    for t in texts:
        for s in SECRETS:
            assert s not in t, f"secret leaked: {s[:4]}..."
        assert not ID26.search(t), "26-char id-like string in output"


def test_no_secret_in_table_or_json(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path, int_host="nyxloom-old000-mattermost")
    _, out, err = run_main(world, stack, capsys, "--post")
    _assert_clean(out, err)
    world2 = FakeWorld()
    _, out2, err2 = run_main(world2, stack, capsys, "--json", "--post")
    _assert_clean(out2, err2)
    json.loads(out2)


def test_no_secret_in_error_paths(tmp_path, capsys):
    stack = make_stack(tmp_path)
    # probe container failure whose stderr echoes a bearer token and a hook URL
    w = FakeWorld()
    w.run_fail = True
    rc, out, err = run_main(w, stack, capsys, "--post")
    _assert_clean(out, err)
    assert "boom" in out  # the error is reported, just scrubbed
    # unexpected exception whose message embeds token + hook URL
    w2 = FakeWorld()
    w2.raise_in_http = RuntimeError(f"kaboom Bearer {TOK_BOT} {TOK_ODD} http://h/hooks/{HOOK_INT}")
    rc2, out2, err2 = run_main(w2, stack, capsys, "--json")
    _assert_clean(out2, err2)
    assert rc2 == 2 and "internal error" in err2


# -------------------------------------------------------------------- post
def test_post_is_off_by_default(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    run_main(world, stack, capsys)
    assert world.posts == []


def test_post_flag_sends_one_message_per_webhook(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    rc, data = results(world, stack, capsys, "--post")
    assert len(world.posts) == 3
    for _, body in world.posts:
        assert re.fullmatch(r"\[mm_reachability test 2026-10-07T12:00:00Z\]", json.loads(body)["text"])
    rows = [r for r in data["results"] if r["check"].endswith(" post")]
    assert len(rows) == 3 and all(r["result"] == "PASS" for r in rows)
    assert rc == 0


# --------------------------------------------------------------------- PAT
def test_pat_failures_reported(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    world.tokens[TOK_ODD] = "someone-else"  # mismatching username
    del world.tokens[TOK_BOT]  # revoked -> 401
    world.users["someone-else"] = {"id": "u9", "username": "someone-else", "delete_at": 0, "roles": "system_user"}
    rc, data = results(world, stack, capsys)
    bot = find(data, "pat bot_pat")
    assert bot and all(r["result"] == "FAIL" and "401" in r["detail"] for r in bot)
    odd = find(data, "pat odd_pat")
    assert odd and all(r["result"] == "FAIL" and "username" in r["detail"] for r in odd)
    # vantages: external + each network
    assert {r["vantage"] for r in bot} == {"local", *(f"net:{n}" for n in NETS)}
    assert rc == 1


# ---------------------------------------------------------------- accounts
def test_accounts_verified_with_pat(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    rc, data = results(world, stack, capsys)
    assert find(data, "account svc-admin")[0]["result"] == "PASS"
    assert find(data, "account svc-admin role")[0]["result"] == "PASS"
    assert find(data, "account svc-bot team tm")[0]["result"] == "PASS"
    assert find(data, "account svc-bot channel intake")[0]["result"] == "PASS"
    # private channel the reader cannot see -> not verifiable, not FAIL
    assert find(data, "account svc-admin channel alerts")[0]["result"] == "INFO"


def test_account_deactivated_missing_and_wrong_role(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    world.users["svc-admin"]["delete_at"] = 1700000000
    world.users["svc-bot"]["roles"] = "system_user system_admin"
    rc, data = results(world, stack, capsys)
    assert find(data, "account svc-admin")[0]["result"] == "FAIL"
    assert find(data, "account svc-bot role")[0]["result"] == "FAIL"
    del world.users["svc-admin"]
    rc, data = results(world, stack, capsys)
    assert find(data, "account svc-admin")[0]["result"] == "FAIL"
    assert rc == 1


def test_accounts_info_when_no_pat_works(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    world.tokens.clear()
    rc, data = results(world, stack, capsys)
    accts = [r for r in data["results"] if r["check"].startswith("account ")]
    assert accts and all(r["result"] == "INFO" and "not verifiable" in r["detail"] for r in accts)


# --------------------------------------------------------- not-on-network
def test_internal_failure_from_local_is_info_when_not_on_network(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    rc, data = results(world, stack, capsys)
    row = find(data, "ping internal", "local")[0]
    assert row["result"] == "INFO" and "not on any of the container's networks" in row["detail"]
    assert data["discovery"]["local_on_container_network"] is False
    assert rc == 0


def test_internal_failure_from_local_is_fail_when_on_network(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    world.local_networks = [NETS[0]]  # this process shares a network...
    orig = world.serve

    def broken(vantage, method, url, headers, body):  # ...yet cannot reach the app
        if vantage == "local" and urlsplit(url).hostname == CONTAINER:
            return mr.Response(0, error="connection refused")
        return orig(vantage, method, url, headers, body)

    world.serve = broken
    rc, data = results(world, stack, capsys)
    assert find(data, "ping internal", "local")[0]["result"] == "FAIL"
    assert data["discovery"]["local_on_container_network"] is True
    assert rc == 1


# ------------------------------------------------------------- ping / body
def test_ping_200_with_wrong_body_fails(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    world.ping_body = "<html>captive portal</html>"
    rc, data = results(world, stack, capsys)
    assert find(data, "ping external")[0]["result"] == "FAIL"
    assert rc == 1


# --------------------------------------------------------------- exit codes
def test_exit_2_on_missing_ciu_toml(tmp_path, capsys):
    rc, out, err = run_main(FakeWorld(), tmp_path / "nope", capsys)
    assert rc == 2 and "config error" in err


def test_exit_2_on_missing_container_prefix(tmp_path, capsys):
    stack = tmp_path / "s"
    stack.mkdir()
    (stack / "ciu.toml").write_text("[mattermost]\nname='x'\n")
    rc, _, err = run_main(FakeWorld(), stack, capsys)
    assert rc == 2 and "container_prefix" in err


def test_exit_2_on_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as ei:
        mr.main(["--no-such-flag"], env=FakeWorld().env())
    assert ei.value.code == 2


def test_exit_1_when_container_missing(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    world.docker_orig = world.docker

    def docker(args, stdin=None, timeout=60):
        if args[0] == "inspect" and args[1] == CONTAINER:
            return 1, "", "Error: No such object"
        return world.docker_orig(args, stdin, timeout)

    world.docker = docker
    rc, data = results(world, stack, capsys)
    assert find(data, "container")[0]["result"] == "FAIL"
    assert rc == 1


def test_missing_secret_file_is_fail(tmp_path, capsys):
    world, stack = FakeWorld(), make_stack(tmp_path)
    (stack / ".ciu" / "secrets" / "wh_int").unlink()
    rc, data = results(world, stack, capsys)
    assert find(data, "webhook wh_int", "config")[0]["result"] == "FAIL"
    assert rc == 1
