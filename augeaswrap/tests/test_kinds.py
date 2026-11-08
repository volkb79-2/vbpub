"""The kind registry and the revert contract, exercised with a toy ``note`` kind.

The toy kind is a stand-in for what HC-1b-u/-s, HC-2 and HC-3 add: it is registered
from outside the engine, loads from TOML, serializes back, renders extra plan lines
and reverts through the generic hooks, with no engine edit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from augeaswrap import ApplyFailed, Change, PlanRefused, PostAction, Request, _fs, kinds
from augeaswrap.engine import apply, journal, load_request, plan, preview_revert, request_to_toml, revert
from augeaswrap.kinds import KindSpec, RevertHooks, register_kind, require_keys


def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir(parents=True)
    return root


@dataclass(frozen=True)
class Note:
    """A file below /etc/notes.d named after the note; ``text=None`` means absent."""

    name: str
    text: str | None = None
    reload: bool = False
    actions: tuple[PostAction, ...] = ()
    kind: ClassVar[str] = "note"

    def addresses(self):
        return (f"note:{self.name}",)

    def _path(self, ctx):
        return Path(ctx.root) / "etc/notes.d" / self.name

    def observe(self, ctx):
        info = _fs.regular_or_missing(Path(ctx.root), self._path(ctx))
        text = None if info is None else self._path(ctx).read_text(encoding="utf-8")
        return {self.addresses()[0]: {"exists": info is not None, "text": text}}

    def diff(self, ctx, observed):
        address = self.addresses()[0]
        current = observed[address]
        action = "noop" if current["text"] == self.text else "change"
        return [Change(self.kind, address, action, current, {"exists": self.text is not None, "text": self.text}, self)]

    def stage(self, ctx, change):
        return self.text

    def commit(self, ctx, staged):
        path = self._path(ctx)
        if staged is None:
            _fs.unlink_and_sync(Path(ctx.root), path)
        else:
            _fs.atomic_write(Path(ctx.root), path, staged.encode(), 0o644, temp_prefix=".note-")
        return self.observe(ctx)[self.addresses()[0]]

    def post_actions(self, changes):
        return [*self.actions, *([PostAction("note-reload", ("true",), 5)] if self.reload else [])]

    def verify(self, ctx):
        return []


def note_parse(table):
    require_keys(table, allowed={"name", "text"}, required={"name"}, where="note")
    return Note(table["name"], table.get("text"))


def note_values(resource):
    return {"name": resource.name} | ({} if resource.text is None else {"text": resource.text})


def note_snapshot(change, observed):
    return {"exists": observed["exists"], "text": observed["text"]}


def note_restore(address, record, actions):
    if not isinstance(record.get("exists"), bool):
        raise ValueError("note pre-image needs an exists flag")
    return Note(address.removeprefix("note:"), record["text"] if record["exists"] else None, actions=actions)


def note_lines(change, plan_value):
    return [f"note render hook saw {change.address}"]


@pytest.fixture
def register():
    names = []

    def add(spec: KindSpec) -> KindSpec:
        names.append(spec.kind)
        return register_kind(spec)

    yield add
    for name in names:
        kinds._REGISTRY.pop(name)


@pytest.fixture
def note_kind(register):
    return register(
        KindSpec(
            kind="note",
            parse=note_parse,
            to_toml=note_values,
            render=note_lines,
            revert=RevertHooks(snapshot=note_snapshot, restore=note_restore),
        )
    )


def note_path(root: Path, name: str = "a") -> Path:
    return root / "etc/notes.d" / name


def request(*resources) -> Request:
    return Request("note writer", tuple(resources))


# --- registration -----------------------------------------------------------------------


def test_core_kinds_are_registered_in_load_order_and_names_are_validated(register):
    assert kinds.registered_kinds()[:2] == ("file", "check")
    assert kinds.is_revertible("file") and not kinds.is_revertible("check") and not kinds.is_revertible("nope")
    assert kinds.kind_spec("nope") is None
    spec = KindSpec(kind="x", parse=note_parse, to_toml=note_values, revert=None)
    for bad in ("", "writer", "augeaswrap"):
        with pytest.raises(ValueError, match="invalid resource kind name"):
            register_kind(KindSpec(kind=bad, parse=note_parse, to_toml=note_values, revert=None))
    with pytest.raises(ValueError, match="invalid resource kind name"):
        register_kind(KindSpec(kind=5, parse=note_parse, to_toml=note_values, revert=None))  # type: ignore[arg-type]
    register(spec)
    with pytest.raises(ValueError, match="already registered"):
        register_kind(spec)
    assert kinds.registered_kinds()[-1] == "x"


def test_revert_has_no_default_so_a_kind_must_state_its_support():
    with pytest.raises(TypeError, match="revert"):
        KindSpec(kind="x", parse=note_parse, to_toml=note_values)  # type: ignore[call-arg]


# --- loading, serializing, rendering ---------------------------------------------------


def test_a_registered_kind_loads_round_trips_and_renders_without_engine_edits(tmp_path, note_kind):
    path = tmp_path / "request.toml"
    original = request(Note("a", "hello"), Note("gone"))
    path.write_text(original.to_toml(), encoding="utf-8")
    assert "[[note]]" in original.to_toml()
    assert load_request(path) == original
    assert load_request(path).to_toml() == original.to_toml()
    assert load_request({"augeaswrap": 1, "writer": "w", "note": [{"name": "a"}]}).resources == (Note("a"),)
    root = fake_root(tmp_path)
    planned = plan(original, root=root)
    text = planned.render()
    assert "note render hook saw note:a" in text
    assert "revert: unsupported" not in text
    assert planned.as_dict()["changes"][0]["revertible"] is True


def test_unknown_kinds_stay_refused_until_registered(tmp_path):
    with pytest.raises(ValueError, match="unknown request key"):
        load_request({"augeaswrap": 1, "writer": "w", "note": [{"name": "a"}]})
    with pytest.raises(ValueError, match="no TOML serializer is registered for 'note'"):
        request_to_toml(request(Note("a", "x")))


# --- plan states revert support before apply --------------------------------------------


def test_plan_states_revert_unsupported_before_apply_and_revert_refuses_after(tmp_path, register):
    register(KindSpec(kind="note", parse=note_parse, to_toml=note_values, revert=None))
    root = fake_root(tmp_path)
    planned = plan(request(Note("a", "hello")), root=root)
    assert "         revert: unsupported" in planned.render().splitlines()
    assert planned.as_dict()["changes"][0]["revertible"] is False
    result = apply(planned)
    assert note_path(root).read_text(encoding="utf-8") == "hello"
    with pytest.raises(PlanRefused, match="note resources declare no revert support"):
        revert(result.txn, root=root)
    assert note_path(root).read_text(encoding="utf-8") == "hello"
    # A noop line does not repeat the statement, and an unregistered kind is unsupported too.
    assert "revert:" not in plan(request(Note("a", "hello")), root=root).render()


def test_a_kind_that_is_not_registered_is_unsupported_in_the_plan(tmp_path):
    planned = plan(request(Note("a", "hello")), root=fake_root(tmp_path))
    assert "         revert: unsupported" in planned.render().splitlines()


# --- generic revert through the hooks ----------------------------------------------------


def test_a_registered_kind_is_reverted_by_the_generic_engine(tmp_path, note_kind):
    root = fake_root(tmp_path)
    note_path(root).parent.mkdir(parents=True)
    note_path(root).write_text("before", encoding="utf-8")
    created = apply(request(Note("new", "fresh"), Note("a", "after", reload=True)), root=root)
    assert note_path(root).read_text(encoding="utf-8") == "after"
    assert journal(created.txn, root=root)["actions"][0]["status"] == "skipped(root)"
    preview = preview_revert(created.txn, root=root)
    assert {change.address: change.action for change in preview.changes} == {"note:a": "change", "note:new": "change"}
    reverted = revert(created.txn, root=root)
    assert note_path(root).read_text(encoding="utf-8") == "before"
    assert not note_path(root, "new").exists()
    # the recorded post-action ran again for the restored address
    assert reverted.actions == ("note-reload: skipped(root)",)
    assert journal(created.txn, root=root)["reverted_by"] == reverted.txn
    # and the revert is itself a transaction of the same kind, so it can be reverted
    assert revert(reverted.txn, root=root).txn
    assert note_path(root).read_text(encoding="utf-8") == "after"


def test_revert_refuses_drift_and_later_transactions_for_a_registered_kind(tmp_path, note_kind):
    root = fake_root(tmp_path)
    first = apply(request(Note("a", "one")), root=root)
    note_path(root).write_text("drifted", encoding="utf-8")
    with pytest.raises(PlanRefused, match="changed outside Augeaswrap"):
        revert(first.txn, root=root)
    note_path(root).write_text("one", encoding="utf-8")
    apply(request(Note("a", "two")), root=root)
    note_path(root).write_text("one", encoding="utf-8")
    with pytest.raises(PlanRefused, match="touched by later transaction"):
        revert(first.txn, root=root)


def test_a_restore_hook_that_rejects_the_record_refuses_the_revert(tmp_path, note_kind):
    root = fake_root(tmp_path)
    result = apply(request(Note("a", "one")), root=root)
    pre = next((root / "var/lib/augeaswrap/txn" / result.txn / "pre").glob("*.json"))
    record = json.loads(pre.read_text(encoding="utf-8"))
    record["exists"] = "maybe"
    pre.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PlanRefused, match="note pre-image needs an exists flag"):
        revert(result.txn, root=root)
    assert note_path(root).read_text(encoding="utf-8") == "one"


# --- snapshot records ---------------------------------------------------------------------


def with_snapshot(register, snapshot):
    register(
        KindSpec(
            kind="note",
            parse=note_parse,
            to_toml=note_values,
            revert=RevertHooks(snapshot=snapshot, restore=note_restore),
        )
    )


@pytest.mark.parametrize("reserved", ["schema", "kind", "address", "data_file"])
def test_a_snapshot_may_not_use_reserved_keys(tmp_path, register, reserved):
    with_snapshot(register, lambda change, observed: {"exists": False, "text": None, reserved: 1})
    root = fake_root(tmp_path)
    with pytest.raises(PlanRefused, match=f"reserved key '{reserved}'"):
        apply(request(Note("a", "x")), root=root)
    assert not note_path(root).exists()


def test_snapshot_bytes_are_stored_privately_and_checked_against_their_digest(tmp_path, register):
    import base64
    import hashlib

    def snapshot(change, observed):
        data = (observed["text"] or "").encode()
        return {"exists": observed["exists"], "text": observed["text"], "sha256": hashlib.sha256(data).hexdigest(), "data_b64": base64.b64encode(data).decode()}

    seen = {}

    def restore(address, record, actions):
        seen.update(record)
        return note_restore(address, record, actions)

    register(KindSpec(kind="note", parse=note_parse, to_toml=note_values, revert=RevertHooks(snapshot=snapshot, restore=restore)))
    root = fake_root(tmp_path)
    note_path(root).parent.mkdir(parents=True)
    note_path(root).write_text("before", encoding="utf-8")
    result = apply(request(Note("a", "after")), root=root)
    pre = root / "var/lib/augeaswrap/txn" / result.txn / "pre"
    blob = next(pre.glob("*.data"))
    assert blob.read_bytes() == b"before" and blob.stat().st_mode & 0o777 == 0o600
    assert "data_b64" not in next(pre.glob("*.json")).read_text(encoding="utf-8")
    revert(result.txn, root=root)
    assert base64.b64decode(seen["data_b64"]) == b"before" and seen["text"] == "before"
    assert note_path(root).read_text(encoding="utf-8") == "before"


def test_a_snapshot_whose_bytes_do_not_match_its_digest_is_refused_before_any_commit(tmp_path, register):
    with_snapshot(register, lambda change, observed: {"exists": True, "text": "x", "sha256": "0" * 64, "data_b64": "eA=="})
    root = fake_root(tmp_path)
    with pytest.raises(PlanRefused, match="sha256 does not match its data_b64"):
        apply(request(Note("a", "x")), root=root)
    assert not note_path(root).exists()
    assert journal(root=root) == []


def test_a_failing_snapshot_apply_is_reported_and_leaves_no_transaction(tmp_path, register):
    def snapshot(change, observed):
        raise RuntimeError("snapshot broke")

    with_snapshot(register, snapshot)
    root = fake_root(tmp_path)
    with pytest.raises(PlanRefused, match="snapshot broke"):
        apply(request(Note("a", "x")), root=root)
    assert not note_path(root).exists()


def test_apply_failure_midway_is_revertible_for_a_registered_kind(tmp_path, note_kind):
    root = fake_root(tmp_path)
    with pytest.raises(ApplyFailed) as failure:
        apply(request(Note("a", "one"), Note("b", "two")), root=root, fail_step=lambda name: "boom" if "note:b" in name else None)
    assert note_path(root).read_text(encoding="utf-8") == "one" and not note_path(root, "b").exists()
    revert(failure.value.txn, root=root)
    assert not note_path(root).exists()


def test_require_keys_reports_unknown_missing_and_non_string_keys():
    with pytest.raises(ValueError, match="unknown note key"):
        require_keys({"x": 1}, allowed={"name"}, required=set(), where="note")
    with pytest.raises(ValueError, match="missing note field"):
        require_keys({}, allowed={"name"}, required={"name"}, where="note")
    with pytest.raises(ValueError, match="note keys must be strings"):
        require_keys({1: 1}, allowed={"name"}, required=set(), where="note")
    with pytest.raises(ValueError, match="array of tables"):
        kinds.resource_tables({"note": "x"}, "note")
    assert kinds.resource_tables({}, "note") == []


def test_an_empty_request_serializes_to_the_schema_header_only():
    assert request_to_toml(Request("w")) == 'augeaswrap = 1\nwriter = "w"\n'
