"""CIU-206 — one operator credential for every checkout (``ASK_HOST``), and a
notice when an environment value replaces a stored ``ASK_EXTERNAL`` secret.

``ASK_HOST:<entry>`` reads ``<host secrets dir>/<entry>``
(``$CIU_HOST_SECRETS_DIR``, else ``~/.ciu/secrets``), never the environment.
A missing file is prompted for ONCE on a TTY and created 0600 there; a
non-interactive run aborts naming the path. The value is delivered as a copy
refreshed every run into the stack's in-checkout store, so the overlay path is
remapped by S1.4 and the Docker daemon can reach it under DooD.

Oracles: two checkouts share one host file with one prompt in total; the
overlay references the remapped in-checkout copy, never the host file; the
value never appears in output.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import composefile  # noqa: E402
from ciu.secrets import directives  # noqa: E402
from ciu.secrets import materialize as mat  # noqa: E402
from ciu.secrets.directives import parse_value  # noqa: E402

VALUE = "s3cr3t-Value-for-tests-only"


def _spec(directive, name="mail_password"):
    return parse_value(name, directive, "app.secrets")


def _run(specs, stack, root, *, env, assume_yes=True, prompt_fn=None, tty=False, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(mat.sys.stdin, "isatty", lambda: tty)
    return mat.materialize(
        specs, stack_dir=stack, repo_root=root, vault=None, assume_yes=assume_yes,
        env=env, chown_fn=lambda *a, **k: None, prompt_fn=prompt_fn,
    )


def _checkout(base: Path, name: str) -> tuple[Path, Path]:
    root = base / name
    stack = root / "tooling" / "app"
    stack.mkdir(parents=True)
    return root, stack


@pytest.fixture
def host_dir(tmp_path):
    d = tmp_path / "host-secrets"
    return d


def _host_file(host_dir: Path, entry: str, content: str) -> Path:
    """An operator-created host secret: private dirs and file."""
    target = host_dir / entry
    host_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(host_dir, 0o700)
    current = host_dir
    for part in Path(entry).parent.parts:
        current = current / part
        current.mkdir(exist_ok=True)
        os.chmod(current, 0o700)
    target.write_text(content, encoding="utf-8")
    os.chmod(target, 0o600)
    return target


# --- grammar --------------------------------------------------------------


def test_ask_host_is_a_directive_with_an_entry_locator():
    spec = _spec("ASK_HOST:netcup/api_password")
    assert spec.kind == "ASK_HOST" and spec.locator == "netcup/api_password"
    assert "ASK_HOST" in directives.DIRECTIVES


@pytest.mark.parametrize("bad", [
    "ASK_HOST:/abs/path", "ASK_HOST:../escape", "ASK_HOST:a/../b",
    "ASK_HOST:a//b", "ASK_HOST:.hidden", "ASK_HOST:UPPER", "ASK_HOST:a b", "ASK_HOST:a/",
])
def test_ask_host_entry_must_be_a_relative_lowercase_name(bad):
    # The message is ASK_HOST's own, not the generic unknown-directive refusal.
    with pytest.raises(ValueError, match=r"\[S4\.14a\] ASK_HOST entry"):
        _spec(bad)


def test_ask_host_needs_an_entry():
    with pytest.raises(ValueError, match="'ASK_HOST' requires a non-empty locator"):
        _spec("ASK_HOST:")


def test_ask_host_outside_a_secrets_table_is_a_misplaced_directive():
    found = directives.find_misplaced({"app": {"password": "ASK_HOST:x"}}, stack_root_key="app")
    assert found and found[0][0].endswith("password")


def test_ask_host_rejects_a_vault_field_selector():
    with pytest.raises(ValueError, match="#field"):
        _spec("ASK_HOST:x#field")


# --- resolution -------------------------------------------------------------


def test_two_checkouts_share_one_host_file_with_one_prompt(tmp_path, host_dir, monkeypatch, capsys):
    """THE ACCEPTANCE TEST: the second checkout neither prompts nor keeps an
    authoritative copy; its store file is a refreshed copy of the host file."""
    env = {"CIU_HOST_SECRETS_DIR": str(host_dir)}
    prompts: list[str] = []

    def prompt(text):
        prompts.append(text)
        return VALUE

    root_a, stack_a = _checkout(tmp_path, "a")
    root_b, stack_b = _checkout(tmp_path, "b")
    spec = _spec("ASK_HOST:mail/password")

    got_a = _run([spec], stack_a, root_a, env=env, assume_yes=False, prompt_fn=prompt,
                 tty=True, monkeypatch=monkeypatch)["mail_password"]
    got_b = _run([spec], stack_b, root_b, env=env, assume_yes=False, prompt_fn=prompt,
                 tty=True, monkeypatch=monkeypatch)["mail_password"]

    assert len(prompts) == 1
    host_file = host_dir / "mail" / "password"
    assert host_file.read_text(encoding="utf-8") == VALUE
    assert stat.S_IMODE(host_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(host_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((host_dir / "mail").stat().st_mode) == 0o700
    for got, stack in ((got_a, stack_a), (got_b, stack_b)):
        assert got.value == VALUE
        assert got.file == mat.stack_store(stack) / "mail_password"
        assert got.file.read_text(encoding="utf-8") == VALUE
    # The host file is the only source: rotating it reaches the next run.
    host_file.write_text("rotated-value", encoding="utf-8")  # keeps its 0600 mode
    again = _run([spec], stack_b, root_b, env=env)["mail_password"]
    assert again.file.read_text(encoding="utf-8") == "rotated-value"
    out = capsys.readouterr()
    assert VALUE not in out.out + out.err
    assert VALUE not in prompts[0]


def test_ask_host_never_reads_the_environment(tmp_path, host_dir):
    root, stack = _checkout(tmp_path, "a")
    env = {
        "CIU_HOST_SECRETS_DIR": str(host_dir),
        "mail/password": "from-env", "CIU_SECRET_MAIL_PASSWORD": "from-env",
    }
    with pytest.raises(ValueError, match=r"\[S4\.14a\]"):
        _run([_spec("ASK_HOST:mail/password")], stack, root, env=env)
    assert not (mat.stack_store(stack) / "mail_password").exists()


def test_non_interactive_run_without_the_host_file_aborts_naming_the_path(tmp_path, host_dir, monkeypatch):
    root, stack = _checkout(tmp_path, "a")
    with pytest.raises(ValueError) as exc:
        _run([_spec("ASK_HOST:mail/password")], stack, root,
             env={"CIU_HOST_SECRETS_DIR": str(host_dir)}, assume_yes=False, tty=False,
             monkeypatch=monkeypatch, prompt_fn=lambda _t: pytest.fail("must not prompt"))
    assert str(host_dir / "mail" / "password") in str(exc.value)
    assert not host_dir.exists()


def test_yes_never_prompts_even_on_a_tty(tmp_path, host_dir, monkeypatch):
    root, stack = _checkout(tmp_path, "a")
    with pytest.raises(ValueError, match=r"\[S4\.14a\]"):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
             assume_yes=True, tty=True, monkeypatch=monkeypatch,
             prompt_fn=lambda _t: pytest.fail("must not prompt"))


def test_an_empty_answer_aborts_and_creates_nothing(tmp_path, host_dir, monkeypatch):
    root, stack = _checkout(tmp_path, "a")
    with pytest.raises(ValueError, match=r"\[S4\.14a\]"):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
             assume_yes=False, tty=True, monkeypatch=monkeypatch, prompt_fn=lambda _t: "")
    assert not (host_dir / "x").exists()


def test_a_single_trailing_newline_is_not_part_of_the_value(tmp_path, host_dir):
    root, stack = _checkout(tmp_path, "a")
    _host_file(host_dir, "x", VALUE + "\n")
    _host_file(host_dir, "y", VALUE + "\r\n")
    got = _run([_spec("ASK_HOST:x"), _spec("ASK_HOST:y", name="other")], stack, root,
               env={"CIU_HOST_SECRETS_DIR": str(host_dir)})
    assert got["mail_password"].value == VALUE
    assert got["other"].value == VALUE


@pytest.mark.parametrize("content", ["", "\n"])
def test_an_empty_host_file_aborts(tmp_path, host_dir, content):
    root, stack = _checkout(tmp_path, "a")
    _host_file(host_dir, "x", content)
    with pytest.raises(ValueError, match="is empty"):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})


def test_a_non_regular_host_entry_aborts(tmp_path, host_dir):
    root, stack = _checkout(tmp_path, "a")
    host_dir.mkdir()
    os.chmod(host_dir, 0o700)
    (host_dir / "x").mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_group_or_world_readable_host_file_is_refused_without_the_value(tmp_path, host_dir, mode):
    root, stack = _checkout(tmp_path, "a")
    os.chmod(_host_file(host_dir, "x", VALUE), mode)
    with pytest.raises(ValueError) as exc:
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})
    assert f"{mode:04o}" in str(exc.value) and "chmod 600" in str(exc.value)
    assert VALUE not in str(exc.value)
    assert not (mat.stack_store(stack) / "mail_password").exists()


@pytest.mark.parametrize("which", ["root", "sub"])
def test_a_store_directory_writable_by_others_is_refused(tmp_path, host_dir, which):
    root, stack = _checkout(tmp_path, "a")
    _host_file(host_dir, "mail/x", VALUE)
    os.chmod(host_dir if which == "root" else host_dir / "mail", 0o770)
    with pytest.raises(ValueError, match="writable by group or others"):
        _run([_spec("ASK_HOST:mail/x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})


def test_a_final_symlink_out_of_the_store_is_refused(tmp_path, host_dir):
    """Review round 1 blocker: a link must not make CIU deliver an arbitrary
    host file as a container secret."""
    root, stack = _checkout(tmp_path, "a")
    outside = tmp_path / "outside-secret"
    outside.write_text("not-for-containers", encoding="utf-8")
    os.chmod(outside, 0o600)
    host_dir.mkdir()
    os.chmod(host_dir, 0o700)
    (host_dir / "x").symlink_to(outside)
    with pytest.raises(ValueError, match="links are refused"):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})
    assert not (mat.stack_store(stack) / "mail_password").exists()


def test_an_intermediate_symlink_out_of_the_store_is_refused_for_read_and_create(
    tmp_path, host_dir, monkeypatch
):
    root, stack = _checkout(tmp_path, "a")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chmod(elsewhere, 0o700)
    (elsewhere / "x").write_text(VALUE, encoding="utf-8")
    os.chmod(elsewhere / "x", 0o600)
    host_dir.mkdir()
    os.chmod(host_dir, 0o700)
    (host_dir / "mail").symlink_to(elsewhere)
    env = {"CIU_HOST_SECRETS_DIR": str(host_dir)}
    with pytest.raises(ValueError, match="is a symlink or not a directory"):
        _run([_spec("ASK_HOST:mail/x")], stack, root, env=env)
    with pytest.raises(ValueError, match="is a symlink or not a directory"):
        _run([_spec("ASK_HOST:mail/new")], stack, root, env=env, assume_yes=False, tty=True,
             monkeypatch=monkeypatch, prompt_fn=lambda _t: VALUE)
    assert sorted(p.name for p in elsewhere.iterdir()) == ["x"]


def test_a_store_root_that_is_a_file_is_refused(tmp_path, host_dir):
    root, stack = _checkout(tmp_path, "a")
    host_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="is not a directory"):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})


def test_a_symlinked_store_root_is_the_operators_choice(tmp_path, host_dir):
    root, stack = _checkout(tmp_path, "a")
    real = tmp_path / "real-store"
    _host_file(real, "x", VALUE)
    host_dir.symlink_to(real)
    got = _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})
    assert got["mail_password"].value == VALUE


def test_a_concurrent_first_run_that_won_the_lock_is_not_prompted_again(
    tmp_path, host_dir, monkeypatch
):
    """Review round 1: two first runs must not both prompt. The second one
    re-reads under the store lock and finds the first one's file."""
    import contextlib

    root, stack = _checkout(tmp_path, "a")

    real_flock = mat._flock

    @contextlib.contextmanager
    def other_checkout_finished_first(path):
        if Path(path).name == ".ciu-host-secrets.lock":
            _host_file(host_dir, "x", "from-the-other-checkout")
            yield
            return
        with real_flock(path):  # the ordinary stack lock
            yield

    monkeypatch.setattr(mat, "_flock", other_checkout_finished_first)
    got = _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
               assume_yes=False, tty=True, monkeypatch=monkeypatch,
               prompt_fn=lambda _t: pytest.fail("must not prompt after the lock"))
    assert got["mail_password"].value == "from-the-other-checkout"


def test_the_prompt_runs_under_the_store_lock(tmp_path, host_dir, monkeypatch):
    import contextlib

    root, stack = _checkout(tmp_path, "a")
    held = []

    real_flock = mat._flock

    @contextlib.contextmanager
    def lock(path):
        if Path(path).name != ".ciu-host-secrets.lock":
            with real_flock(path):  # the ordinary stack lock
                yield
            return
        held.append(Path(path))
        yield
        held.append(None)

    def prompt(_t):
        assert held and held[-1] is not None, "prompted outside the store lock"
        return VALUE

    monkeypatch.setattr(mat, "_flock", lock)
    _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
         assume_yes=False, tty=True, monkeypatch=monkeypatch, prompt_fn=prompt)
    assert held == [host_dir / ".ciu-host-secrets.lock", None]


def test_the_creation_race_reads_the_winner(tmp_path, host_dir, monkeypatch):
    root, stack = _checkout(tmp_path, "a")
    real_link = os.link

    def lost_race(src, dst, **kw):
        _host_file(host_dir, "x", "winner")
        return real_link(src, dst, **kw)  # raises FileExistsError

    monkeypatch.setattr(mat.os, "link", lost_race)
    got = _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
               assume_yes=False, tty=True, monkeypatch=monkeypatch, prompt_fn=lambda _t: VALUE)
    assert got["mail_password"].value == "winner"
    assert sorted(p.name for p in host_dir.iterdir()) == [".ciu-host-secrets.lock", "x"]  # no temp file left


def test_a_failed_host_write_leaves_no_temp_file(tmp_path, host_dir, monkeypatch):
    root, stack = _checkout(tmp_path, "a")

    def broken(_src, _dst, **_kw):
        raise PermissionError("read-only")

    monkeypatch.setattr(mat.os, "link", broken)
    with pytest.raises(PermissionError):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
             assume_yes=False, tty=True, monkeypatch=monkeypatch, prompt_fn=lambda _t: VALUE)
    assert [p.name for p in host_dir.iterdir()] == [".ciu-host-secrets.lock"]


@pytest.mark.parametrize("raw", ["", "relative/dir"])
def test_the_host_secrets_dir_must_be_absolute(tmp_path, raw):
    root, stack = _checkout(tmp_path, "a")
    with pytest.raises(ValueError, match="CIU_HOST_SECRETS_DIR"):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": raw})


def test_the_default_host_secrets_dir_is_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert mat.host_secrets_dir({}) == tmp_path / "home" / ".ciu" / "secrets"


def test_reset_removes_the_stack_copy_but_never_the_host_file(tmp_path, host_dir):
    root, stack = _checkout(tmp_path, "a")
    _host_file(host_dir, "x", VALUE)
    spec = _spec("ASK_HOST:x")
    _run([spec], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})
    deleted = mat.reset_secrets(stack, root, [spec])
    assert deleted == [mat.stack_store(stack) / "mail_password"]
    assert (host_dir / "x").read_text(encoding="utf-8") == VALUE


# --- the Docker-daemon boundary (DooD) --------------------------------------


def test_the_overlay_references_the_remapped_in_checkout_copy(tmp_path, host_dir):
    """Under DooD the daemon sees PHYSICAL_REPO_ROOT, not the cockpit's paths.
    The host file lives outside the checkout (it would pass S1.4 unchanged and
    name a path the daemon does not have), so the overlay must reference the
    in-checkout copy, remapped."""
    root, stack = _checkout(tmp_path, "a")
    _host_file(host_dir, "x", VALUE)
    got = _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)})
    physical = Path("/srv/daemon-view/a")
    overlay = composefile.generate_overlay(
        stack, got, [], repo_root=root, physical_root=physical,
    )
    doc = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    source = doc["secrets"]["mail_password"]["file"]
    assert source == str(physical / "tooling" / "app" / ".ciu" / "secrets" / "mail_password")
    assert str(host_dir) not in overlay.read_text(encoding="utf-8")


# --- ASK_EXTERNAL: an env value that replaces a stored one is disclosed -------


def test_env_replacing_a_stored_ask_external_value_emits_a_notice(tmp_path, capsys):
    root, stack = _checkout(tmp_path, "a")
    spec = _spec("ASK_EXTERNAL:SMTP_PASSWORD", name="smtp_password")
    _run([spec], stack, root, env={"SMTP_PASSWORD": "first-value"})
    capsys.readouterr()
    _run([spec], stack, root, env={"SMTP_PASSWORD": "second-value"})
    err = capsys.readouterr().err
    assert "[NOTICE] [CIU-206]" in err
    assert "smtp_password" in err and "SMTP_PASSWORD" in err
    assert "first-value" not in err and "second-value" not in err
    assert (mat.stack_store(stack) / "smtp_password").read_text(encoding="utf-8") == "second-value"


def test_the_notice_names_the_ciu_secret_variable_when_that_was_the_source(tmp_path, capsys):
    root, stack = _checkout(tmp_path, "a")
    spec = _spec("ASK_EXTERNAL:SMTP_PASSWORD", name="smtp_password")
    _run([spec], stack, root, env={"SMTP_PASSWORD": "first-value"})
    capsys.readouterr()
    _run([spec], stack, root, env={"CIU_SECRET_SMTP_PASSWORD": "other-value"})
    err = capsys.readouterr().err
    assert "CIU_SECRET_SMTP_PASSWORD" in err and "other-value" not in err


def test_no_notice_for_a_first_value_or_an_unchanged_one(tmp_path, capsys):
    root, stack = _checkout(tmp_path, "a")
    spec = _spec("ASK_EXTERNAL:SMTP_PASSWORD", name="smtp_password")
    _run([spec], stack, root, env={"SMTP_PASSWORD": "same-value"})
    _run([spec], stack, root, env={"SMTP_PASSWORD": "same-value"})
    assert "NOTICE" not in capsys.readouterr().err


def test_an_unreadable_stored_value_counts_as_replaced(tmp_path, capsys):
    root, stack = _checkout(tmp_path, "a")
    spec = _spec("ASK_EXTERNAL:SMTP_PASSWORD", name="smtp_password")
    store = mat.stack_store(stack) / "smtp_password"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"\xff\xfe")
    _run([spec], stack, root, env={"SMTP_PASSWORD": "new-value"})
    assert "[NOTICE] [CIU-206]" in capsys.readouterr().err


def test_a_planted_file_at_the_temp_name_is_never_written_through(tmp_path, host_dir, monkeypatch):
    """The temp file is created exclusively: a pre-existing entry at its name
    (for example a hard link to another file) aborts instead of receiving the
    value."""
    root, stack = _checkout(tmp_path, "a")
    host_dir.mkdir()
    os.chmod(host_dir, 0o700)
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    monkeypatch.setattr(mat._stdlib_secrets, "token_hex", lambda _n: "fixed")
    os.link(victim, host_dir / f".tmp-host-secret-{os.getpid()}-fixed")
    with pytest.raises(FileExistsError):
        _run([_spec("ASK_HOST:x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
             assume_yes=False, tty=True, monkeypatch=monkeypatch, prompt_fn=lambda _t: VALUE)
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert not (host_dir / "x").exists()


def test_created_store_directories_are_exactly_0700_whatever_the_umask(tmp_path, host_dir, monkeypatch):
    root, stack = _checkout(tmp_path, "a")
    host_dir.mkdir()
    os.chmod(host_dir, 0o700)
    old = os.umask(0o277)
    try:
        _run([_spec("ASK_HOST:mail/x")], stack, root, env={"CIU_HOST_SECRETS_DIR": str(host_dir)},
             assume_yes=False, tty=True, monkeypatch=monkeypatch, prompt_fn=lambda _t: VALUE)
    finally:
        os.umask(old)
    assert stat.S_IMODE((host_dir / "mail").stat().st_mode) == 0o700
    assert (host_dir / "mail" / "x").read_text(encoding="utf-8") == VALUE
