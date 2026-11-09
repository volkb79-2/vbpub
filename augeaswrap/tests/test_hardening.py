"""Safety properties of the transaction store and engine found by the HC-1a' review.

Everything runs unprivileged on a fake root. Ownership is asserted through the
``_fs._fchown`` seam (a recorder that does not change real ownership), so the tests
do not need root and do not depend on host load.
"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import signal
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import ApplyFailed, Change, PlanRefused, PostAction, Request, StalePlan
from augeaswrap import _fs, engine, transactions
from augeaswrap import check as check_module
from augeaswrap import file as file_module
from augeaswrap.check import Check
from augeaswrap.cli import build_cli
from augeaswrap.engine import apply, journal, load_request, plan, revert
from augeaswrap.file import File
from cli_extended.identity import CliIdentity

ADDRESS = "file:/etc/service.conf"


def fake_root(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "root"
    root.mkdir()
    return root


def txn_root(root: Path) -> Path:
    return root / "var/lib/augeaswrap/txn"


def file_request(path="/etc/service.conf", text="after", **options) -> Request:
    return Request("hardening", (File(path, "present", text, "0640", **options),))


def seed(root: Path, path="etc/service.conf", text="before") -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    target.chmod(0o640)
    return target


def applied(tmp_path, *, before="before", text="after"):
    root = fake_root(tmp_path)
    seed(root, text=before)
    result = apply(plan(file_request(text=text), root=root))
    return root, result.txn


def edit_journal(root, txn, mutate):
    path = txn_root(root) / txn / "journal.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def fchown_calls(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(_fs, "_fchown", lambda _descriptor, uid, gid: calls.append((uid, gid)))
    return calls


@pytest.fixture
def default_sigterm():
    previous = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    yield
    signal.signal(signal.SIGTERM, previous)


# --- owner restore (review B4: owner on commit and on revert) -------------------------------


def test_commit_passes_the_requested_owner_and_group_to_chown(tmp_path, fchown_calls):
    root = fake_root(tmp_path)
    with pytest.raises(ApplyFailed, match="verification failed"):
        # the recorder does not chown for real, so only verification notices
        apply(plan(file_request(owner=12345, group=23456), root=root))
    assert fchown_calls == [(12345, 23456)]


def test_revert_restores_the_recorded_owner_and_group(tmp_path, fchown_calls):
    root, txn = applied(tmp_path)
    assert fchown_calls == [(os.geteuid(), os.getegid())]
    fchown_calls.clear()
    pre = next((txn_root(root) / txn / "pre").glob("*.json"))
    record = json.loads(pre.read_text(encoding="utf-8"))
    record.update(uid=4242, gid=5252)
    pre.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ApplyFailed, match="verification failed for restored"):
        revert(txn, root=root)
    assert fchown_calls == [(4242, 5252)]


def test_atomic_write_sets_the_owner_before_the_mode(tmp_path, monkeypatch):
    order: list[str] = []
    real_chmod = _fs._fchmod
    monkeypatch.setattr(_fs, "_fchown", lambda *_args: order.append("chown"))
    monkeypatch.setattr(_fs, "_fchmod", lambda descriptor, mode: (order.append("chmod"), real_chmod(descriptor, mode))[1])
    root = fake_root(tmp_path)
    (root / "etc").mkdir()
    _fs.atomic_write(root, root / "etc/f", b"x", 0o4755, temp_prefix=".t-", owner=(1, 2))
    # A chown clears setuid/setgid, so the mode must be applied after it.
    assert order == ["chown", "chmod"]


# --- the pre-image store is private (review B4: M20, M28-M31) --------------------------------


def test_the_transaction_store_is_private_whatever_the_umask(tmp_path):
    previous = os.umask(0)
    try:
        root, txn = applied(tmp_path)
    finally:
        os.umask(previous)
    state = root / "var/lib/augeaswrap"
    directory = txn_root(root) / txn
    modes = {
        path: stat.S_IMODE(path.lstat().st_mode)
        for path in (
            state,
            state / "txn",
            directory,
            directory / "pre",
            directory / "journal.json",
            *(directory / "pre").iterdir(),
        )
    }
    assert not (directory / "stage").exists()
    for path, mode in modes.items():
        assert mode == (0o700 if path.is_dir() else 0o600), path
    assert {path.suffix for path in modes if path.parent.name == "pre"} == {".json", ".data"}
    # only the state directory is private; its parents keep the default mode
    assert stat.S_IMODE((root / "var").stat().st_mode) == 0o755
    assert stat.S_IMODE((root / "var/lib").stat().st_mode) == 0o755


# --- revert refusal names the later transaction (review B4: M1, M35, M36) --------------------


def apply_twice_then_restore_first_content(tmp_path):
    root, first = applied(tmp_path)
    second = apply(plan(file_request(text="third"), root=root)).txn
    # Put the file back to the first transaction's post-image, so the post-image
    # fingerprint cannot be what refuses the revert.
    (root / "etc/service.conf").write_text("after", encoding="utf-8")
    return root, first, second


@pytest.mark.parametrize(
    ("status", "post_images", "unknown"),
    [
        pytest.param("done", False, False, id="done-only"),
        pytest.param("applying", False, False, id="applying-only"),
        pytest.param("failed", True, False, id="failed-with-post-image"),
        pytest.param("failed", False, True, id="failed-unobservable"),
    ],
)
def test_revert_names_the_later_transaction_that_touched_the_address(tmp_path, status, post_images, unknown):
    root, first, second = apply_twice_then_restore_first_content(tmp_path)

    def mutate(value):
        value["status"] = status
        if not post_images:
            value["post_images"] = {}
        value["unknown_post_images"] = [ADDRESS] if unknown else []

    edit_journal(root, second, mutate)
    with pytest.raises(PlanRefused, match=f"was touched by later transaction {second}"):
        revert(first, root=root)
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "after"


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_a_later_transaction_that_never_wrote_the_address_does_not_block_revert(tmp_path, status):
    root, first, second = apply_twice_then_restore_first_content(tmp_path)

    def mutate(value):
        value.update(status=status, post_images={}, unknown_post_images=[])

    edit_journal(root, second, mutate)
    assert revert(first, root=root).txn
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "before"


# --- interrupted apply (review non-blocking 1) --------------------------------------------------


def two_file_request() -> Request:
    return Request("interrupt", (File("/etc/a.conf", "present", "one", "0640"), File("/etc/b.conf", "present", "two", "0640")))


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_an_interrupted_commit_is_journaled_and_stays_revertible(tmp_path, interruption):
    root = fake_root(tmp_path)
    with pytest.raises(interruption):
        apply(
            plan(two_file_request(), root=root),
            fail_step=lambda name: interruption() if name == "commit file:/etc/b.conf" else None,
        )
    (txn,) = [entry["id"] for entry in journal(root=root)]
    record = journal(txn, root=root)
    assert record["status"] == "interrupted"
    assert record["failed"] == {"step": "commit file:/etc/b.conf", "reason": f"interrupted by {interruption.__name__}"}
    assert record["done"] == ["commit file:/etc/a.conf"]
    assert list(record["post_images"]) == ["file:/etc/a.conf"]
    assert revert(txn, root=root).txn
    assert not (root / "etc/a.conf").exists()


def test_an_interruption_after_the_commits_is_journaled_without_a_commit_to_blame(tmp_path):
    root = fake_root(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        apply(plan(file_request(), root=root), fail_step=lambda name: KeyboardInterrupt() if name.startswith("verify") else None)
    (txn,) = [entry["id"] for entry in journal(root=root)]
    record = journal(txn, root=root)
    assert record["status"] == "interrupted" and record["failed"]["step"] == "verify file #1"
    assert revert(txn, root=root).txn
    assert not (root / "etc/service.conf").exists()


def test_sigterm_during_apply_is_journaled_and_the_handler_is_restored(tmp_path, default_sigterm):
    root = fake_root(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        apply(
            plan(two_file_request(), root=root),
            fail_step=lambda name: os.kill(os.getpid(), signal.SIGTERM) if name.endswith("b.conf") else None,
        )
    assert stopped.value.code == 128 + signal.SIGTERM
    (txn,) = [entry["id"] for entry in journal(root=root)]
    assert journal(txn, root=root)["status"] == "interrupted"
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    assert revert(txn, root=root).txn


def test_the_sigterm_handler_is_left_alone_off_the_main_thread_or_over_a_custom_one(default_sigterm):
    seen: list[object] = []

    def work():
        with transactions._sigterm_as_exit():
            seen.append(signal.getsignal(signal.SIGTERM))

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    assert seen == [signal.SIG_DFL]

    def custom(_signum, _frame):
        return None

    signal.signal(signal.SIGTERM, custom)
    with transactions._sigterm_as_exit():
        assert signal.getsignal(signal.SIGTERM) is custom
    assert signal.getsignal(signal.SIGTERM) is custom


# --- a transaction directory without a journal (review non-blocking 2) -------------------------


def test_a_crash_before_the_first_journal_write_does_not_wedge_listing_or_revert(tmp_path):
    root, txn = applied(tmp_path)
    wedged = txn_root(root) / "20990101T000000Z-0003"
    wedged.mkdir()
    with pytest.warns(RuntimeWarning, match="20990101T000000Z-0003 has no journal.json"):
        listed = journal(root=root)
    assert [entry["id"] for entry in listed] == [txn]
    with pytest.raises(PlanRefused, match="cannot read transaction"):
        journal("20990101T000000Z-0003", root=root)
    assert revert(txn, root=root).txn
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "before"


def test_an_unreadable_journal_is_still_refused_not_skipped(tmp_path):
    root, txn = applied(tmp_path)
    broken = txn_root(root) / "20990101T000000Z-0004"
    broken.mkdir()
    (broken / "journal.json").symlink_to(broken / "nowhere")
    with pytest.raises(PlanRefused, match="cannot read transaction"):
        journal(root=root)
    with pytest.raises(PlanRefused, match="later transaction .* has an unreadable journal"):
        revert(txn, root=root)


def test_cli_journal_list_warns_about_a_directory_without_a_journal(tmp_path):
    root, txn = applied(tmp_path)
    (txn_root(root) / "20990101T000000Z-0003").mkdir()
    out, err = io.StringIO(), io.StringIO()
    identity = CliIdentity(name="AUGEASWRAP", version="test", long_name="Augeaswrap tests", command="augeaswrap")
    status = build_cli(identity).run(argv=["journal", "list", "--root", str(root)], stdout=out, stderr=err, stdin=io.StringIO(""))
    assert status == 0 and txn in out.getvalue()
    assert "20990101T000000Z-0003 has no journal.json" in err.getvalue()


# --- exec check fingerprint ignores output (review non-blocking 3) ------------------------------


def varying_check() -> Check:
    return Check("present", exec=(sys.executable, "-c", "import os; print(os.urandom(8).hex())"), expect_exit=0, timeout=30)


def test_an_exec_check_with_varying_output_does_not_make_apply_stale(tmp_path):
    root = fake_root(tmp_path)
    request = Request("varying", (*file_request().resources, varying_check()))
    planned = plan(request, root=root)
    assert apply(planned).changed == (ADDRESS,)
    observation = next(value for address, value in planned.observed.items() if address.startswith("check:exec:"))
    assert set(observation) == {"argv", "returncode", "fingerprint"}


def test_exec_check_fingerprint_still_changes_with_the_exit_code():
    first = check_module._exec_observation(("true",), 0, 30)
    other = check_module._exec_observation(("false",), 1, 30)
    assert first["fingerprint"] != other["fingerprint"]


# --- subprocesses get no stdin (review non-blocking 5) ----------------------------------------


def test_every_subprocess_runs_with_stdin_closed(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def recording(argv, **kwargs):
        seen[argv[0]] = kwargs.get("stdin")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(file_module.subprocess, "run", recording)
    file_module._run_validator(("validator",), tmp_path / "staged")
    check_module._exec_observation(("check",), 0, 5)
    engine._run_post_action(Path("/"), PostAction("action", ("post-action",), 5))
    assert seen == {"validator": file_module.subprocess.DEVNULL, "check": file_module.subprocess.DEVNULL, "post-action": file_module.subprocess.DEVNULL}


# --- TOML escaping (review non-blocking 8) -------------------------------------------------------


@pytest.mark.parametrize("text", ["a\x7fb", "\x7f", "tab\there\x7f"])
def test_generated_toml_with_a_delete_character_loads_back_identically(tmp_path, text):
    original = file_request(text=text)
    rendered = original.to_toml()
    assert "\x7f" not in rendered
    path = tmp_path / "request.toml"
    path.write_text(rendered, encoding="utf-8")
    assert load_request(path) == original


# --- hooks and hand-built plans --------------------------------------------------------------------


def test_the_file_snapshot_hook_refuses_a_non_mapping_observation():
    change = Change("file", ADDRESS, "change", {}, {}, File("/etc/service.conf", "absent"))
    with pytest.raises(ValueError, match="must be a mapping"):
        file_module._file_snapshot(change, "text")


def test_apply_refuses_a_plan_whose_changes_do_not_match_its_request(tmp_path):
    root = fake_root(tmp_path)
    planned = plan(file_request(), root=root)
    with pytest.raises(StalePlan, match="plan changes do not match"):
        apply(dataclasses.replace(planned, changes=()))
    stranger = Change("file", "file:/etc/other.conf", "change", {}, {}, planned.changes[0].resource)
    with pytest.raises(StalePlan, match="plan changes do not match"):
        apply(dataclasses.replace(planned, changes=(*planned.changes, stranger)))
    assert not (root / "etc").exists()
