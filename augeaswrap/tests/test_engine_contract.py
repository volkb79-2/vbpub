"""Engine and transaction contract tests: misbehaving resources, tampered journals, loader limits.

Every test uses a fake root and a scriptable in-memory resource, so the results do not
depend on host load, the real filesystem layout, or installed validators.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import (
    ApplyFailed,
    Change,
    PlanRefused,
    PostAction,
    Problem,
    Request,
    Result,
    StalePlan,
)
from augeaswrap import engine, transactions
from augeaswrap import file as file_module
from augeaswrap.check import Check, PathMode
from augeaswrap.engine import apply, journal, load_request, plan, revert
from augeaswrap.file import File


def fake_root(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "root"
    root.mkdir()
    return root


def txn_root(root: Path) -> Path:
    return root / "var/lib/augeaswrap/txn"


class Fake:
    """A scriptable resource; ``hooks`` replace individual protocol methods."""

    def __init__(self, kind="fake", addrs=("fake:a",), **hooks):
        self.kind = kind
        self.addrs = addrs
        self.hooks = hooks
        self.value = 0
        self.want = 1

    def _call(self, name, default, *args):
        hook = self.hooks.get(name)
        return hook(self, *args) if hook else default(*args)

    def addresses(self):
        return self._call("addresses", lambda: self.addrs)

    def observe(self, ctx):
        return self._call("observe", lambda _ctx: {a: {"value": self.value} for a in self.addrs}, ctx)

    def diff(self, ctx, observed):
        def default(_ctx, seen):
            return [
                Change(
                    self.kind,
                    a,
                    "noop" if seen[a]["value"] == self.want else "change",
                    seen[a],
                    {"value": self.want},
                    self,
                )
                for a in self.addrs
            ]

        return self._call("diff", default, ctx, observed)

    def stage(self, ctx, change):
        return self._call("stage", lambda _ctx, _change: self.want, ctx, change)

    def commit(self, ctx, staged):
        def default(_ctx, value):
            self.value = value
            return {"value": value}

        return self._call("commit", default, ctx, staged)

    def post_actions(self, changes):
        return self._call("post_actions", lambda _changes: [], changes)

    def verify(self, ctx):
        return self._call("verify", lambda _ctx: [], ctx)


def req(*resources):
    return Request("fake writer", tuple(resources))


def file_request(path="/etc/service.conf", text="after"):
    return Request("service install 1", (File(path, "present", text, "0640"),))


def applied_file(tmp_path, *, before="before", text="after", path="/etc/service.conf"):
    root = fake_root(tmp_path)
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(before, encoding="utf-8")
    result = apply(plan(file_request(path, text), root=root))
    assert result.txn
    return root, result.txn


def edit_journal(root, txn, mutate):
    path = txn_root(root) / txn / "journal.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value), encoding="utf-8")


# --- root and request normalization ----------------------------------------------------


def test_root_must_be_a_nonempty_path_to_a_directory(tmp_path):
    request = req(Fake())
    for bad in (None, b"/tmp", ""):
        with pytest.raises(Exception, match="non-empty filesystem path"):
            plan(request, root=bad)
    not_a_directory = tmp_path / "plain-file"
    not_a_directory.write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="not a directory"):
        plan(request, root=not_a_directory / "sub")


def test_plan_accepts_a_toml_path_or_a_mapping_and_refuses_other_types(tmp_path):
    root = fake_root(tmp_path)
    path = tmp_path / "request.toml"
    path.write_text(file_request().to_toml(), encoding="utf-8")
    assert plan(path, root=root).has_changes
    assert plan(str(path), root=root).has_changes
    mapping = {"augeaswrap": 1, "writer": "w", "file": [{"path": "/etc/m.conf", "state": "present", "text": "t", "mode": "0644"}]}
    assert plan(mapping, root=root).has_changes
    with pytest.raises(TypeError, match="request must be"):
        plan(123, root=root)


APPLY_MAPPING = {
    "augeaswrap": 1,
    "writer": "w",
    "file": [{"path": "/etc/m.conf", "state": "present", "text": "t", "mode": "0644"}],
}


@pytest.mark.parametrize("kind", ["plan", "request", "mapping", "str-path", "pathlike-path"])
def test_apply_accepts_each_documented_input_type(tmp_path, kind):
    root = fake_root(tmp_path)
    toml_path = tmp_path / "request.toml"
    toml_path.write_text(file_request("/etc/m.conf", "t").to_toml(), encoding="utf-8")
    source = {
        "plan": lambda: plan(file_request("/etc/m.conf", "t"), root=root),
        "request": lambda: file_request("/etc/m.conf", "t"),
        "mapping": lambda: APPLY_MAPPING,
        "str-path": lambda: str(toml_path),
        "pathlike-path": lambda: toml_path,
    }[kind]()
    # A Plan carries its root; every other input needs the root passed explicitly.
    result = apply(source) if kind == "plan" else apply(source, root=root)
    assert result.changed == ("file:/etc/m.conf",)
    assert result.txn
    assert (root / "etc/m.conf").read_text(encoding="utf-8") == "t"
    if kind == "plan":
        with pytest.raises(StalePlan, match="changed after plan"):
            apply(source)
        source = file_request("/etc/m.conf", "t")
    again = apply(source, root=root)
    assert again.txn is None and not again.has_changes
    assert [entry["id"] for entry in journal(root=root)] == [result.txn]


def test_apply_dry_run_with_a_request_plans_without_writing(tmp_path):
    root = fake_root(tmp_path)
    planned = apply(file_request("/etc/m.conf", "t"), root=root, dry_run=True)
    assert planned.has_changes and planned.root == str(root)
    assert not (root / "etc").exists() and not (root / "var").exists()


def test_apply_refuses_a_plan_made_for_another_root_and_defaults_other_inputs_to_slash(tmp_path):
    first, second = fake_root(tmp_path / "a"), fake_root(tmp_path / "b")
    planned = plan(file_request("/etc/m.conf", "t"), root=first)
    with pytest.raises(StalePlan, match="apply root differs"):
        apply(planned, root=second)
    assert not (second / "etc").exists()
    # Without a root a Request, mapping or path is planned against "/". A noop resource
    # keeps that read-only, and the plan carries the "/" root it applied against.
    quiet = Fake()
    quiet.want = quiet.value
    assert apply(req(quiet)) == Result(None)
    assert apply(req(quiet), dry_run=True).root == "/"


# --- resource contract violations at plan time -----------------------------------------


@pytest.mark.parametrize(
    ("resource", "message"),
    [
        (Fake(kind=""), "resource kind"),
        (Fake(addresses=lambda self: ["fake:a"]), "invalid addresses"),
        (Fake(addresses=lambda self: ()), "invalid addresses"),
        (Fake(addresses=lambda self: ("fake:a", "fake:a")), "duplicate addresses"),
    ],
)
def test_invalid_resource_identity_is_refused(tmp_path, resource, message):
    with pytest.raises(PlanRefused, match=message):
        plan(req(resource), root=fake_root(tmp_path))


@pytest.mark.parametrize(
    ("hooks", "message"),
    [
        ({"observe": lambda self, ctx: ["not", "a", "mapping"]}, "observe must return a mapping"),
        ({"observe": lambda self, ctx: {}}, "observe omitted fake:a"),
        ({"observe": lambda self, ctx: {"fake:a": {}, "fake:z": {}}}, "returned unknown address fake:z"),
        ({"diff": lambda self, ctx, seen: ()}, "diff must return list"),
        ({"diff": lambda self, ctx, seen: []}, "one change or noop per address"),
        (
            {"diff": lambda self, ctx, seen: [Change("other", "fake:a", "noop", {}, {}, None)]},
            "mismatched change kind",
        ),
        (
            {"diff": lambda self, ctx, seen: [Change("fake", "fake:a", "noop", {}, {}, Fake())]},
            "change for another resource",
        ),
        (
            {"diff": lambda self, ctx, seen: [Change("fake", "fake:a", "noop", {"x": object()}, {}, None)]},
            "planning fake failed",
        ),
        ({"post_actions": lambda self, changes: ("not", "a", "list")}, "post_actions must return"),
    ],
)
def test_misbehaving_resources_fail_the_plan_with_a_named_problem(tmp_path, hooks, message):
    with pytest.raises(PlanRefused, match=message):
        plan(req(Fake(**hooks)), root=fake_root(tmp_path))


def test_two_resources_cannot_claim_the_same_address(tmp_path):
    with pytest.raises(PlanRefused, match="duplicate address fake:a"):
        plan(req(Fake(), Fake()), root=fake_root(tmp_path))


def test_conflicting_post_action_timeouts_are_refused_and_bindings_need_resources(tmp_path):
    def first(self, changes):
        return [PostAction("reload", ("true",), 5.0, phase="runtime")]

    def second(self, changes):
        return [PostAction("reload", ("true",), 6.0, phase="runtime")]

    with pytest.raises(PlanRefused, match="conflicting timeouts"):
        plan(req(Fake(addrs=("fake:a",), post_actions=first), Fake(addrs=("fake:b",), post_actions=second)), root=fake_root(tmp_path))
    with pytest.raises(ValueError, match="has no resource"):
        engine._post_action_bindings([Change("fake", "fake:a", "change", {}, {})])


def test_unknown_post_action_forms_need_a_declared_phase():
    with pytest.raises(ValueError, match="needs an explicit phase"):
        engine._action_phase(PostAction("custom", ("custom-command",), 5.0))


def test_conflicting_post_action_phases_are_refused(tmp_path):
    def first(self, _changes):
        return [PostAction("same", ("custom-command",), 5.0, phase="runtime")]

    def second(self, _changes):
        return [PostAction("same", ("custom-command",), 5.0, phase="endpoint")]

    with pytest.raises(PlanRefused, match="conflicting phases"):
        plan(
            req(
                Fake(addrs=("fake:a",), post_actions=first),
                Fake(addrs=("fake:b",), post_actions=second),
            ),
            root=fake_root(tmp_path),
        )


def test_before_commit_action_must_bind_to_one_address(tmp_path):
    def before_commit(_self, _changes):
        return [PostAction("prepare", ("custom-command",), 5.0, phase="before_commit")]

    root = fake_root(tmp_path)
    planned = plan(
        req(Fake(addrs=("fake:a", "fake:b"), post_actions=before_commit)),
        root=root,
    )
    with pytest.raises(ValueError, match="must apply to one address"):
        transactions._execute_plan(planned, root)


def test_missing_postimage_is_recorded_as_unknown(tmp_path):
    class UnobservableAfterCommit(Fake):
        def __init__(self):
            super().__init__()
            self.committed = False

        def observe(self, _ctx):
            if self.committed:
                return {}
            return {"fake:a": {"value": 0}}

        def commit(self, _ctx, staged):
            self.committed = True
            return {"value": staged}

    root = fake_root(tmp_path)
    result = apply(plan(req(UnobservableAfterCommit()), root=root))
    assert result.txn
    assert journal(result.txn, root=root)["unknown_post_images"] == ["fake:a"]


def test_post_actions_follow_rev3_phases_and_keep_runtime_request_order(tmp_path):
    specs = [
        ("endpoint", ("augeaswrap", "endpoints", "publish")),
        ("member", ("gpasswd", "-a", "runner", "service")),
        ("restart one", ("systemctl", "restart", "--", "one.service")),
        ("sysctl", ("sysctl", "-p", "60-example.conf")),
        ("daemon reload", ("systemctl", "daemon-reload")),
        ("tmpfiles", ("systemd-tmpfiles", "--create", "60-example.conf")),
        ("sysusers", ("systemd-sysusers", "60-example.conf")),
        ("restart two", ("systemctl", "restart", "--", "two.service")),
    ]
    resources = [
        Fake(
            addrs=(f"fake:{name}",),
            post_actions=lambda self, changes, name=name, argv=argv: [PostAction(name, argv, 5.0)],
        )
        for name, argv in specs
    ]
    planned = plan(req(*resources), root=fake_root(tmp_path))

    assert [item["name"] for item in planned.as_dict()["post_actions"]] == [
        "sysusers",
        "tmpfiles",
        "daemon reload",
        "sysctl",
        "restart one",
        "restart two",
        "member",
        "endpoint",
    ]
    assert [item["phase"] for item in planned.as_dict()["post_actions"]] == [
        "sysusers",
        "tmpfiles",
        "daemon_reload",
        "sysctl",
        "runtime",
        "runtime",
        "gpasswd",
        "endpoint",
    ]


def test_post_action_refreshes_bound_address_postimage(monkeypatch, tmp_path):
    root = fake_root(tmp_path)

    def actions(_self, _changes):
        return [PostAction("update observed state", ("update",), 5.0, phase="runtime")]

    resource = Fake(post_actions=actions)

    def run(_root, _action):
        resource.value = 2
        return "update observed state: done"

    monkeypatch.setattr(transactions, "_run_post_action", run)
    result = apply(plan(req(resource), root=root))
    record = journal(result.txn, root=root)

    assert record["post_images"]["fake:a"] == engine._observation_fingerprint({"value": 2})


def test_plan_renders_empty_noop_details_and_post_actions(tmp_path):
    root = fake_root(tmp_path)
    assert "no resources" in plan(Request("w", ()), root=root).render()

    target = root / "etc/service.conf"
    target.parent.mkdir()
    target.write_text("after", encoding="utf-8")
    target.chmod(0o640)
    noop = plan(file_request(), root=root)
    assert not noop.has_changes
    rendered = noop.render()
    assert "noop" in rendered and "no changes" in rendered and "---" not in rendered

    create_only = Request("w", (File("/etc/service.conf", "present", "other", "0640", create_only=True),))
    assert "preserved-existing-file" in plan(create_only, root=root).render()

    def post_actions(self, changes):
        return [PostAction("reload fake", ("systemctl", "reload", "fake"), 5.0)]

    planned = plan(req(Fake(post_actions=post_actions)), root=root)
    fake_text = planned.render()
    assert "post-action reload fake" in fake_text and "skipped(root)" in fake_text
    assert planned.as_dict()["post_actions"][0]["status"] == "skipped(root)"
    # Rendering a plan that names the real root never runs anything.
    real = dataclasses.replace(planned, root="/")
    assert "(pending)" in real.render()
    assert real.as_dict()["post_actions"][0]["status"] == "pending"


# --- stale plans ----------------------------------------------------------------------


def test_apply_refuses_plans_whose_resources_changed_after_planning(tmp_path):
    root = fake_root(tmp_path)

    fake = Fake()
    planned = plan(req(fake), root=root)
    fake.hooks["addresses"] = lambda self: ["not", "a", "tuple"]
    with pytest.raises(StalePlan, match="addresses changed after plan"):
        apply(planned)

    first, second = Fake(addrs=("fake:a",)), Fake(addrs=("fake:b",))
    planned = plan(req(first, second), root=root)
    second.hooks["addresses"] = lambda self: ("fake:a",)
    with pytest.raises(StalePlan, match="duplicate addresses after plan"):
        apply(planned)

    fake = Fake()
    planned = plan(req(fake), root=root)
    with pytest.raises(StalePlan, match="do not cover"):
        apply(dataclasses.replace(planned, preconditions=()))

    fake = Fake()
    planned = plan(req(fake), root=root)
    fake.hooks["observe"] = lambda self, ctx: {}
    with pytest.raises(StalePlan, match="could not be re-observed"):
        apply(planned)

    def explode(self, ctx):
        raise OSError("unreadable")

    fake = Fake()
    planned = plan(req(fake), root=root)
    fake.hooks["observe"] = explode
    with pytest.raises(StalePlan, match="unreadable"):
        apply(planned)

    fake = Fake()
    planned = plan(req(fake), root=root)
    fake.hooks["observe"] = lambda self, ctx: {"fake:a": {"value": object()}}
    with pytest.raises(StalePlan, match="invalid observation"):
        apply(planned)

    fake = Fake()
    planned = plan(req(fake), root=root)
    fake.value = 7
    with pytest.raises(StalePlan, match="changed after plan"):
        apply(planned)
    assert not txn_root(root).exists()


def test_apply_dry_run_returns_the_plan_without_touching_state(tmp_path):
    root = fake_root(tmp_path)
    planned = plan(file_request(), root=root)
    assert apply(planned, dry_run=True) is planned
    assert not (root / "var").exists()


# --- apply failure handling -----------------------------------------------------------


def test_stage_failures_at_apply_time_discard_the_transaction(tmp_path):
    root = fake_root(tmp_path)
    calls = []

    def stage(self, ctx, change):
        calls.append(change.address)
        if len(calls) > 1:
            raise RuntimeError("stage broke")
        return self.want

    planned = plan(req(Fake(stage=stage)), root=root)
    with pytest.raises(PlanRefused, match="staging failed for fake:a: stage broke"):
        apply(planned)
    assert list(txn_root(root).iterdir()) == []

    refusing = Fake(stage=lambda self, ctx, change: (_ for _ in ()).throw(PlanRefused([Problem("x.y", "stage refused")])) if ctx.txn_id else self.want)
    planned = plan(req(refusing), root=root)
    with pytest.raises(PlanRefused, match="stage refused"):
        apply(planned)
    assert list(txn_root(root).iterdir()) == []


def test_preimage_write_failure_discards_the_transaction(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    planned = plan(file_request(), root=root)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(transactions, "_write_preimage", boom)
    with pytest.raises(PlanRefused, match="cannot save transaction pre-images: disk full"):
        apply(planned)
    assert list(txn_root(root).iterdir()) == []


def test_allocation_failure_removes_the_half_built_transaction(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    original = transactions._fs.ensure_directory

    def failing(root_arg, path, **kwargs):
        if path.name == "stage":
            raise OSError("no space")
        return original(root_arg, path, **kwargs)

    monkeypatch.setattr(transactions._fs, "ensure_directory", failing)
    with pytest.raises(OSError, match="no space"):
        apply(plan(file_request(), root=root))
    assert list(txn_root(root).iterdir()) == []


@pytest.mark.parametrize(
    ("fail_step", "message"),
    [
        (lambda name: False, None),
        (lambda name: RuntimeError("custom failure"), "custom failure"),
        (lambda name: f"named {name}", "named commit fake:a"),
        (lambda name: True, "injected failure at commit fake:a"),
        (1, "injected failure at commit fake:a"),
        ("commit fake:a", "injected failure at commit fake:a"),
        (2, "injected failure at verify fake #1"),
        (3, None),
    ],
)
def test_fail_step_hook_accepts_callables_indexes_and_names(tmp_path, fail_step, message):
    root = fake_root(tmp_path)
    planned = plan(req(Fake()), root=root)
    if message is None:
        assert apply(planned, fail_step=fail_step).txn
        return
    with pytest.raises(ApplyFailed, match=message) as failure:
        apply(planned, fail_step=fail_step)
    assert journal(failure.value.txn, root=root)["status"] == "failed"


def test_interrupt_after_last_step_is_journaled_without_index_error(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    planned = plan(req(Fake()), root=root)

    class InterruptAfterSteps:
        def __enter__(self):
            return self

        def __exit__(self, _type, error, _traceback):
            if error is None:
                raise KeyboardInterrupt
            return False

    def transaction_scope():
        return InterruptAfterSteps()

    monkeypatch.setattr(transactions, "_sigterm_as_exit", transaction_scope)
    with pytest.raises(KeyboardInterrupt):
        apply(planned)
    saved = journal(root=root)[0]
    assert saved["status"] == "interrupted"
    assert saved["failed"]["step"] == "finalize"
    assert saved["not_run"] == []


def test_unobservable_post_image_after_a_failed_commit_blocks_revert(tmp_path):
    root = fake_root(tmp_path)
    state = {"committed": False}

    def commit(self, ctx, staged):
        state["committed"] = True
        raise RuntimeError("commit broke")

    def observe(self, ctx):
        return {} if state["committed"] else {"fake:a": {"value": self.value}}

    planned = plan(req(Fake(commit=commit, observe=observe)), root=root)
    with pytest.raises(ApplyFailed) as failure:
        apply(planned)
    assert journal(failure.value.txn, root=root)["unknown_post_images"] == ["fake:a"]
    with pytest.raises(PlanRefused, match="could not be observed"):
        revert(failure.value.txn, root=root)


def test_verify_must_return_a_list_of_problems(tmp_path):
    root = fake_root(tmp_path)
    with pytest.raises(ApplyFailed, match=r"verify must return list\[Problem\]"):
        apply(plan(req(Fake(verify=lambda self, ctx: "bad")), root=root))
    with pytest.raises(ApplyFailed, match="verify said no"):
        apply(plan(req(Fake(verify=lambda self, ctx: [Problem("v.no", "verify said no")])), root=fake_root(tmp_path / "second")))


def test_failed_commit_that_changed_nothing_leaves_nothing_to_revert(tmp_path):
    root = fake_root(tmp_path)

    def commit(self, ctx, staged):
        raise RuntimeError("commit broke")

    with pytest.raises(ApplyFailed) as failure:
        apply(plan(req(Fake(commit=commit)), root=root))
    txn = failure.value.txn
    assert journal(txn, root=root)["post_images"] == {}
    assert transactions.preview_revert(txn, root=root).has_changes is False
    assert revert(txn, root=root).txn is None


def test_revert_of_a_non_file_transaction_is_unsupported(tmp_path):
    root = fake_root(tmp_path)
    result = apply(plan(req(Fake()), root=root))
    assert result.txn
    with pytest.raises(PlanRefused, match="fake resources declare no revert support"):
        revert(result.txn, root=root)


def test_revert_failure_keeps_the_original_transaction_revertible(tmp_path, monkeypatch):
    root, txn = applied_file(tmp_path)

    def boom(self, ctx, staged):
        raise RuntimeError("restore broke")

    monkeypatch.setattr(file_module._RestoreFile, "commit", boom)
    with pytest.raises(ApplyFailed, match="restore broke"):
        revert(txn, root=root)
    assert journal(txn, root=root)["reverted_by"] is None
    monkeypatch.undo()
    assert revert(txn, root=root).txn
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "before"


def test_revert_ignores_later_transactions_that_did_not_touch_the_address(tmp_path):
    root, first = applied_file(tmp_path)
    other = root / "etc/other.conf"
    other.write_text("old", encoding="utf-8")
    assert apply(plan(file_request("/etc/other.conf", "new"), root=root)).txn
    assert revert(first, root=root).txn
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "before"
    assert other.read_text(encoding="utf-8") == "new"


# --- journal and pre-image tampering --------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda j: j.update(status="applying"), "not complete"),
        (lambda j: j.update(reverted_by="20990101T000000Z-0001"), "already reverted"),
        (lambda j: j.update(post_images=[]), "invalid post-images"),
        (lambda j: j.update(post_images={"file:/etc/service.conf": 5}), "invalid post-images"),
        (lambda j: j.update(unknown_post_images="x"), "invalid unknown post-images"),
        (lambda j: j.update(unknown_post_images=[5]), "invalid unknown post-images"),
        (lambda j: j.update(changes="x"), "invalid changes"),
        (lambda j: j.update(changes=[1]), "invalid changes"),
        (lambda j: j["post_images"].update({"file:/etc/extra.conf": "abc"}), "without one change per address"),
        (lambda j: j.update(changes=j["changes"] * 2), "without one change per address"),
        (lambda j: j.update(actions="x"), "invalid actions"),
        (lambda j: j.update(actions=[1]), "invalid actions"),
        (lambda j: j.update(actions=[{"name": 1}]), "invalid actions"),
        (
            lambda j: j.update(
                actions=[{"name": "a", "argv": ["x"], "timeout": 1, "addresses": [], "status": "done", "pending": True}]
            ),
            "invalid pending action state",
        ),
        (
            lambda j: j.update(actions=[{"name": "a", "argv": ["x"], "timeout": -1, "addresses": [], "status": "done"}]),
            "invalid actions: post-action timeout",
        ),
        (lambda j: j["changes"][0].update(kind="other"), "other resources declare no revert support"),
        (lambda j: j["changes"][0].update(kind=["unhashable"]), r"\['unhashable'\] resources declare no revert support"),
    ],
)
def test_revert_refuses_a_tampered_journal(tmp_path, mutate, message):
    root, txn = applied_file(tmp_path)
    edit_journal(root, txn, mutate)
    with pytest.raises(PlanRefused, match=message):
        revert(txn, root=root)
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "after"


def test_revert_replays_only_actions_that_ran_against_a_reverted_address(tmp_path):
    root, txn = applied_file(tmp_path)
    ignored = [
        {"name": "elsewhere", "argv": ["x"], "timeout": 1, "addresses": ["file:/zzz"], "status": "done"},
        {"name": "never ran", "argv": ["y"], "timeout": 1, "addresses": ["file:/etc/service.conf"], "status": "not-run"},
    ]
    edit_journal(root, txn, lambda j: j.update(actions=ignored))
    result = revert(txn, root=root)
    assert result.txn and result.actions == ()


def test_revert_refuses_a_journal_address_with_a_relative_path(tmp_path):
    root, txn = applied_file(tmp_path)

    def relocate(value):
        value["changes"][0]["address"] = "file:etc/service.conf"
        value["post_images"] = {"file:etc/service.conf": next(iter(value["post_images"].values()))}

    edit_journal(root, txn, relocate)
    # Give the relocated address a pre-image of its own so the restore hook is what refuses.
    old_json, _data = pre_files(root, txn)
    record = json.loads(old_json.read_text(encoding="utf-8"))
    record.update(address="file:etc/service.conf", path="etc/service.conf")
    new_name = hashlib.sha256(b"file:etc/service.conf").hexdigest() + ".json"
    (old_json.parent / new_name).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PlanRefused, match="must be an absolute path"):
        revert(txn, root=root)


def pre_files(root, txn):
    directory = txn_root(root) / txn / "pre"
    return next(directory.glob("*.json")), next(directory.glob("*.data"))


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("delete-json", "cannot read it"),
        ("garbage-json", "cannot read it"),
        ("wrong-schema", "wrong schema, address or kind"),
        ("wrong-kind", "wrong schema, address or kind"),
        ("data-path", "invalid data reference"),
        ("delete-data", "cannot read its bytes"),
        ("data-digest", "digest differs"),
        ("bad-mode", "invalid metadata"),
        ("bad-uid", "invalid metadata"),
        ("wrong-path", "does not match"),
        ("bad-exists", "must be a boolean"),
    ],
)
def test_revert_refuses_tampered_preimages(tmp_path, tamper, message):
    root, txn = applied_file(tmp_path)
    metadata_path, data_path = pre_files(root, txn)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if tamper == "delete-json":
        metadata_path.unlink()
    elif tamper == "garbage-json":
        metadata_path.write_text("{not json", encoding="utf-8")
    elif tamper == "delete-data":
        data_path.unlink()
    elif tamper == "data-digest":
        data_path.write_bytes(b"tampered")
    else:
        update = {
            "wrong-schema": {"schema": 2},
            "wrong-kind": {"kind": "other"},
            "data-path": {"data_file": "../escape"},
            "bad-mode": {"mode": 0o10000},
            "bad-uid": {"uid": -1},
            "wrong-path": {"path": "/etc/elsewhere.conf"},
            "bad-exists": {"exists": "yes"},
        }[tamper]
        metadata.update(update)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(PlanRefused, match=message):
        revert(txn, root=root)
    assert (root / "etc/service.conf").read_text(encoding="utf-8") == "after"


def test_journal_listing_ignores_noise_and_rejects_unreadable_entries(tmp_path):
    root = fake_root(tmp_path)
    assert journal(root=root) == []
    root, txn = applied_file(tmp_path / "applied")
    directory = txn_root(root)
    (directory / "junk").write_text("not a transaction", encoding="utf-8")
    (directory / "20990101T000000Z-0001").write_text("a file named like a transaction", encoding="utf-8")
    (directory / "20990101T000000Z-0002").symlink_to(directory / txn, target_is_directory=True)
    assert [entry["id"] for entry in journal(root=root)] == [txn]
    edit_journal(root, txn, lambda j: j.update(schema=2))
    with pytest.raises(PlanRefused, match="invalid journal"):
        journal(txn, root=root)
    with pytest.raises(PlanRefused, match="invalid format"):
        journal("../../etc", root=root)


def test_restore_resource_rejects_misuse_directly(tmp_path):
    root = fake_root(tmp_path)
    image = {"exists": True, "sha256": "0" * 64, "data_b64": "eA==", "mode": 0o644, "uid": 0, "gid": 0}
    resource = file_module._RestoreFile("/etc/x.conf", image)
    address = "file:/etc/x.conf"
    with pytest.raises(ValueError, match="must be a mapping"):
        resource.diff(None, {address: "text"})
    with pytest.raises(TypeError, match="requires a staged image"):
        resource.commit(SimpleNamespace(root=root), object())
    context = SimpleNamespace(root=root, expected={})
    assert resource.verify(context)[0].code == "revert.verify.expected"
    staged = file_module._RestoredImage(False, None, None, None, None, {"exists": False})
    assert resource.verify(SimpleNamespace(root=root, expected={address: staged})) == []
    staged = file_module._RestoredImage(True, b"x", 0o644, 0, 0, dict(image))
    assert resource.verify(SimpleNamespace(root=root, expected={address: staged}))[0].code == "revert.verify.failed"


# --- strict loader and TOML serializer ------------------------------------------------


def write_toml(tmp_path, body):
    path = tmp_path / "request.toml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('augeaswrap = 1\nwriter = "w"\nfile = "x"\n', "file must be an array of tables"),
        ('augeaswrap = 1\nwriter = "w"\nfile = [1]\n', "file must be an array of tables"),
        ('augeaswrap = 1\nwriter = "a\\nb"\n', "writer is required"),
        ('augeaswrap = 1\nwriter = "w"\n[[check]]\nstate = "present"\nexec = ["true"]\nexpect_exit = 0\ntimeout = 5\n[check.path_mode]\npath = "/x"\n', "exactly one of exec or path_mode"),
        ('augeaswrap = 1\nwriter = "w"\n[[check]]\nstate = "present"\nexec = "true"\nexpect_exit = 0\ntimeout = 5\n', "exec must be an argv array"),
        ('augeaswrap = 1\nwriter = "w"\n[[check]]\nstate = "present"\n', "requires exec or path_mode"),
        ('augeaswrap = 1\nwriter = "w"\n[[check]]\nstate = "present"\nexpect_exit = 0\npath_mode = {path = "/x", owner = 0, group = 0, mode = "0644", if_exists = "absent"}\n', "apply only to exec checks"),
        ('augeaswrap = 1\nwriter = "w"\n[[check]]\nstate = "present"\npath_mode = "/x"\n', "path_mode must be a table"),
        ('augeaswrap = 1\nwriter = "w"\n[[check]]\nstate = "present"\npath_mode = {path = "/x"}\n', "missing path_mode field"),
    ],
)
def test_strict_loader_rejects_malformed_checks_and_tables(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        load_request(write_toml(tmp_path, body))


def test_loader_rejects_non_string_mapping_keys():
    with pytest.raises(ValueError, match="keys must be strings"):
        load_request({1: "x", "augeaswrap": 1, "writer": "w"})


def test_toml_refuses_interleaved_resource_kinds_and_endpoint_unit_removal_order(tmp_path):
    interleaved = write_toml(
        tmp_path,
        'augeaswrap = 1\nwriter = "w"\n'
        '[[file]]\npath = "/etc/a"\nstate = "present"\ntext = "a"\nmode = "0644"\n'
        '[[check]]\nstate = "absent"\n'
        '[[file]]\npath = "/etc/b"\nstate = "present"\ntext = "b"\nmode = "0644"\n',
    )
    with pytest.raises(ValueError, match="interleaves 'file'"):
        load_request(interleaved)

    request = Request(
        "ordered",
        (
            File("/etc/a", "present", "a", "0644"),
            Check("absent", path_mode=PathMode("/etc/check", 0, 0, "0644", "absent")),
            File("/etc/b", "present", "b", "0644"),
        ),
    )
    with pytest.raises(ValueError, match="cannot serialize interleaved 'file'"):
        request.to_toml()

    engine._validate_toml_resource_order(
        'writer = """literal header-looking content:\n[[file]]\n"""\n'
        "note = '''another header-looking line:\n[[check]]\n'''\n"
        "[[endpoint]]\nname = 'example'\n"
    )

    endpoint = SimpleNamespace(kind="endpoint", state="absent")
    unit = SimpleNamespace(kind="unit", state="absent")
    engine._validate_dependency_order(SimpleNamespace(resources=(endpoint, unit)))
    with pytest.raises(PlanRefused, match="endpoint withdrawal must precede unit removal"):
        engine._validate_dependency_order(SimpleNamespace(resources=(unit, endpoint)))


def test_toml_order_scanner_handles_escaped_multiline_quotes_and_comments():
    assert engine._toml_multiline_state('value = """open \\\"""', None) == '"""'
    assert engine._toml_multiline_state('escaped \\\"""', '"""') == '"""'
    assert engine._toml_multiline_state('"""', '"""') is None
    assert engine._toml_multiline_state("# [[file]]", None) is None
    assert engine._toml_multiline_state('value = "ordinary \\\" quote"', None) is None
    assert engine._toml_multiline_state('value = "unterminated', None) is None
    assert engine._toml_multiline_state("value = 'ordinary'", None) is None


def test_post_action_with_unobservable_bound_address_is_journaled_unknown(monkeypatch, tmp_path):
    root = fake_root(tmp_path)

    def actions(_self, _changes):
        return [PostAction("drop observed address", ("drop",), 5.0, phase="runtime")]

    def observe(self, _ctx):
        return {} if self.value == 2 else {"fake:a": {"value": self.value}}

    resource = Fake(post_actions=actions, observe=observe)

    def run(_root, _action):
        resource.value = 2
        return "drop observed address: done"

    monkeypatch.setattr(transactions, "_run_post_action", run)
    result = apply(plan(req(resource), root=root))
    record = journal(result.txn, root=root)

    assert record["unknown_post_images"] == ["fake:a"]


@pytest.mark.parametrize("action_fails", [False, True])
def test_revert_image_hooks_refresh_post_action_addresses_on_both_paths(monkeypatch, tmp_path, action_fails):
    root = fake_root(tmp_path)
    resource = Fake(
        post_actions=lambda _self, _changes: [
            PostAction("update hooked state", ("update",), 5.0, phase="runtime")
        ]
    )
    post_images = []
    pre_images = []

    def post_image(selected_resource, _ctx, address):
        post_images.append((address, selected_resource.value))
        return {"restore_value": selected_resource.value}

    def pre_image(change):
        pre_images.append(change.address)
        return {"restore_value": change.before["value"]}

    hooks = SimpleNamespace(
        snapshot=lambda _change, _observed: {"fake": True},
        restore=lambda *_args: resource,
        post_image=post_image,
        pre_image=pre_image,
    )
    original_kind_spec = transactions.kind_spec
    monkeypatch.setattr(
        transactions,
        "kind_spec",
        lambda kind: SimpleNamespace(shadow_check=None, revert=hooks)
        if kind == "fake"
        else original_kind_spec(kind),
    )

    def run(_root, _action):
        resource.value = 2
        if action_fails:
            raise RuntimeError("update failed after changing state")
        return "update hooked state: done"

    monkeypatch.setattr(transactions, "_run_post_action", run)
    if action_fails:
        with pytest.raises(ApplyFailed):
            apply(plan(req(resource), root=root))
        record = journal(root=root)[0]
    else:
        result = apply(plan(req(resource), root=root))
        record = journal(result.txn, root=root)

    assert post_images.count(("fake:a", 2)) >= 2
    assert pre_images
    assert record["post_images"]["fake:a"] == engine._observation_fingerprint({"restore_value": 2})


def test_post_action_completion_journal_failure_is_recorded_on_the_action_step(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Fake(
        post_actions=lambda _self, _changes: [
            PostAction("update observed state", ("update",), 5.0, phase="runtime")
        ]
    )

    def run(_root, _action):
        resource.value = 2
        return "update observed state: done"

    original_write_journal = transactions._write_journal
    injected = False

    def fail_action_completion_once(selected_root, txn_dir, journal_value):
        nonlocal injected
        if not injected and journal_value["actions"][0]["status"] == "done":
            injected = True
            raise OSError("journal completion unavailable")
        original_write_journal(selected_root, txn_dir, journal_value)

    monkeypatch.setattr(transactions, "_run_post_action", run)
    monkeypatch.setattr(transactions, "_write_journal", fail_action_completion_once)
    with pytest.raises(ApplyFailed, match="post-action update observed state"):
        apply(plan(req(resource), root=root))

    record = journal(root=root)[0]
    assert injected
    assert record["failed"]["step"] == "post-action update observed state"
    assert record["actions"][0]["status"] == "failed"
    assert record["post_images"]["fake:a"] == engine._observation_fingerprint({"value": 2})


def test_request_round_trips_every_resource_shape(tmp_path):
    original = Request(
        "round trip",
        (
            File("/etc/a.conf", "present", "text", "0644", owner=0, group=0, create_only=True, validate=("cmd", "check", "%s")),
            File("/etc/b.conf", "absent"),
            Check("present", exec=("true",), expect_exit=0, timeout=2.5),
            Check("present", path_mode=PathMode("/etc/a.conf", 0, 0, "0644", "must-match")),
            Check("absent", path_mode={"path": "/etc/c", "owner": 0, "group": 0, "mode": 0o600, "if_exists": "absent"}),
        ),
    )
    reloaded = load_request(write_toml(tmp_path, original.to_toml()))
    assert reloaded == original
    assert reloaded.to_toml() == original.to_toml()
    assert 'validate = ["cmd", "check", "%s"]' in original.to_toml()
    assert engine._toml_value({"a": [1, True, 1.5]}) == '{ "a" = [1, true, 1.5] }'
    with pytest.raises(ValueError, match="cannot encode NoneType"):
        engine._toml_value(None)
