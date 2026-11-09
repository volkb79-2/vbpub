"""Fake-root contract tests for the Augeas-backed edit resource."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from augeaswrap.formats.fstab import mount_item, mount_ops, swap_item, swap_op

from augeaswrap import (
    Edit,
    PlanRefused,
    Request,
    apply,
    augtool,
    load_request,
    plan,
    revert,
    shadow,
)
from augeaswrap import edit as edit_module


class FakeRunner:
    def __init__(self, *, tree: str = "", matches=None, new_bytes: bytes | None = None):
        self.tree = tree
        self.matches = {} if matches is None else dict(matches)
        self.new_bytes = new_bytes
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.validators: list[tuple[tuple[str, ...], bytes]] = []
        self.validator_returncode = 0
        self.validator_stderr = ""
        self.validator_oserror: Exception | None = None

    def __call__(self, argv, *, timeout=30.0, input_text=None):
        argv = tuple(argv)
        if argv and argv[0] == "augtool":
            script_path = Path(argv[-1])
            script = script_path.read_text(encoding="utf-8")
            self.calls.append((argv, script))
            if "--new" in argv:
                if self.new_bytes is not None:
                    root = Path(argv[1].split("=", 1)[1])
                    target = root / "etc/fstab"
                    target.with_name("fstab.augnew").write_bytes(self.new_bytes)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if "print \"/files" in script:
                return SimpleNamespace(returncode=0, stdout=self.tree, stderr="")
            query = next((line[6:] for line in script.splitlines() if line.startswith("match ")), None)
            if query is not None:
                expression = json.loads(query)
                found = self.matches.get(expression, ())
                return SimpleNamespace(returncode=0, stdout="\n".join(found) + ("\n" if found else ""), stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        if self.validator_oserror is not None:
            raise self.validator_oserror
        operand = Path(argv[-1])
        data = operand.read_bytes() if operand.is_file() else b""
        self.validators.append((argv, data))
        return SimpleNamespace(returncode=self.validator_returncode, stdout="", stderr=self.validator_stderr)


class ScriptedAugtool:
    """Stateful fake that returns a fresh parsed tree after each staged write."""

    def __init__(self, states, mutations):
        self.states = states
        self.mutations = list(mutations)
        self.calls: list[tuple[bytes, str, bool]] = []

    def __call__(self, *, path, lens, source, script, save_new=False):
        del path, lens
        self.calls.append((source, script, save_new))
        if save_new:
            if not self.mutations:
                raise AssertionError("unexpected augtool mutation")
            return augtool.AugtoolOutput("", "", self.mutations.pop(0))
        if 'print "/files' in script:
            tree = self.states[source][0]
            return augtool.AugtoolOutput(tree, "")
        query = next((line[6:] for line in script.splitlines() if line.startswith("match ")), None)
        if query is None:
            raise AssertionError(f"unexpected augtool script: {script!r}")
        expression = json.loads(query)
        matches = self.states[source][1].get(expression, ())
        output = "\n".join(matches) + ("\n" if matches else "")
        return augtool.AugtoolOutput(output, "")


def fstab_tree(rows):
    base = "/files/etc/fstab"
    lines = []
    for label, fields in rows:
        for name, value in fields.items():
            values = value if isinstance(value, list) else [value]
            for index, item in enumerate(values, start=1):
                field = f"{name}[{index}]" if name == "opt" else name
                lines.append(f"{base}/{label}/{field} = {item}")
    return "\n".join(lines) + ("\n" if lines else "")


def fstab_state(rows, *, all_records=True, swaps=None):
    base = "/files/etc/fstab"
    matches = {}
    if all_records:
        matches[base + "/*"] = tuple(f"{base}/{label}" for label, _fields in rows)
    if swaps is not None:
        matches[base + "/*[vfstype='swap']"] = tuple(f"{base}/{label}" for label, fields in rows if fields.get("vfstype") == "swap")
    return fstab_tree(rows), matches


def use_scripted_augtool(monkeypatch, runner):
    monkeypatch.setattr(augtool.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(augtool, "run", runner)


def diff_once(resource, root):
    ctx = SimpleNamespace(root=root)
    observed = resource.observe(ctx)
    return resource.diff(ctx, observed)[0]


def mutation_scripts(runner):
    return [script for _source, script, save_new in runner.calls if save_new]


def fake_root(tmp_path: Path, content: bytes | None = b"old\n") -> Path:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "etc/fstab"
    if content is not None:
        target.parent.mkdir(parents=True)
        target.write_bytes(content)
    return root


def use_fake_runner(monkeypatch, runner: FakeRunner) -> None:
    monkeypatch.setattr(augtool.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(shadow, "run_command", runner)


def request(resource: Edit) -> Request:
    return Request("test edit 1.0", (resource,))


def make_edit(ops, **kwargs) -> Edit:
    values = {
        "file": "/etc/fstab",
        "lens": "Fstab",
        "state": "present",
        "ops": ops,
        "create": False,
        "on_change": "none",
    }
    values.update(kwargs)
    return Edit(**values)


def test_plan_uses_fake_runner_exact_script_argv_and_unified_diff(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"old\n")
    hostile = ' -disk/[x]= spaces "quoted" \\ end '
    runner = FakeRunner(new_bytes=b'new="value"\n')
    use_fake_runner(monkeypatch, runner)
    resource = make_edit([{"set": "entry/value", "value": hostile}])

    planned = plan(request(resource), root=root)

    assert planned.has_changes
    change = planned.changes[0]
    assert change.before["exists"] is True
    assert len(change.before["sha256"]) == 64
    assert change.before["mode"] == 0o644
    assert change.before["uid"] == os.geteuid()
    assert change.before["gid"] == os.getegid()
    assert "--- a/etc/fstab" in change.details
    assert "+++ b/etc/fstab" in change.details
    assert "-old" in change.details and '+new="value"' in change.details
    command = 'set "/files/etc/fstab/entry/value" " -disk/[x]= spaces \\"quoted\\" \\\\ end "'
    expected_script = f"load\n{command}\nprint /augeas//error\nsave\n"
    augtool_calls = [call for call in runner.calls if call[0][0] == "augtool"]
    mutation_calls = [(argv, script) for argv, script in augtool_calls if "--new" in argv]
    assert [script for _argv, script in mutation_calls] == [expected_script, expected_script]
    assert len(augtool_calls) == 6  # tree + touched-path match + transform, in plan and plan-stage
    argv = mutation_calls[0][0]
    assert argv[0] == "augtool"
    assert argv[1].startswith("--root=")
    assert argv[2:8] == (
        "--noautoload",
        "--nostdinc",
        "-I",
        str(Path(argv[-1]).parent / "lenses"),
        "-I",
        augtool._DIST_LENSES,
    )
    assert argv[8:11] == ("-t", 'Fstab incl "/etc/fstab"', "--new")
    assert argv[11] == "-f" and argv[12].endswith("commands.aug")


def test_records_script_uses_ordered_fields_and_repeated_opt_labels(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"# kept\n")
    source = b"# kept\n"
    staged = b"# kept\nUUID=x /mnt ext4 noatime,defaults 0 2\n"
    item = {
        "spec": "UUID=x",
        "file": "/mnt",
        "vfstype": "ext4",
        "opt": ["noatime", "defaults"],
        "dump": "0",
        "passno": "2",
    }
    runner = ScriptedAugtool(
        {
            source: fstab_state([]),
            staged: fstab_state([("01[1]", item)]),
        },
        [staged],
    )
    use_scripted_augtool(monkeypatch, runner)
    resource = make_edit(
        [{"records": "*", "key": "file", "exclusive": False, "items": [item]}]
    )
    change = diff_once(resource, root)
    expected_script = (
        "load\n"
        "set \"/files/etc/fstab/01[1]/spec\" \"UUID=x\"\n"
        "set \"/files/etc/fstab/01[1]/file\" \"/mnt\"\n"
        "set \"/files/etc/fstab/01[1]/vfstype\" \"ext4\"\n"
        "rm \"/files/etc/fstab/01[1]/opt\"\n"
        "set \"/files/etc/fstab/01[1]/opt[1]\" \"noatime\"\n"
        "set \"/files/etc/fstab/01[1]/opt[2]\" \"defaults\"\n"
        "set \"/files/etc/fstab/01[1]/dump\" \"0\"\n"
        "set \"/files/etc/fstab/01[1]/passno\" \"2\"\n"
        "print /augeas//error\nsave\n"
    )
    assert mutation_scripts(runner) == [expected_script]
    assert change.after["sha256"] == hashlib.sha256(staged).hexdigest()


def test_equal_staged_bytes_are_a_noop_even_when_augtool_emits_augnew(monkeypatch, tmp_path):
    source = b"original\n"
    root = fake_root(tmp_path, source)
    runner = FakeRunner(new_bytes=source)
    use_fake_runner(monkeypatch, runner)
    planned = plan(request(make_edit([{"set": "entry/value", "value": "same"}])), root=root)
    assert not planned.has_changes
    assert planned.changes[0].action == "noop"


def test_records_duplicate_key_refuses_before_edit_script_runs(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    tree = """/files/etc/fstab/01[1]/file = /data
/files/etc/fstab/01[2]/file = /data
"""
    selector = "/files/etc/fstab/*"
    runner = FakeRunner(tree=tree, matches={selector: (selector[:-1] + "01[1]", selector[:-1] + "01[2]")})
    use_fake_runner(monkeypatch, runner)
    resource = make_edit(
        [
            {
                "records": "*",
                "key": "file",
                "exclusive": False,
                "items": [{"file": "/data", "spec": "UUID=x", "opt": ["defaults"]}],
            }
        ]
    )
    with pytest.raises(PlanRefused) as failure:
        plan(request(resource), root=root)
    assert failure.value.problems[0].code == "edit.duplicate"
    assert not any("--new" in argv for argv, _script in runner.calls)


@pytest.mark.parametrize("kind", ["set", "clear"])
def test_set_and_clear_duplicate_guards_allow_only_explicit_selector_acceptance(monkeypatch, tmp_path, kind):
    root = fake_root(tmp_path)
    selector = "/files/etc/fstab/entry/value"
    runner = FakeRunner(matches={selector: (selector + "[1]", selector + "[2]")}, new_bytes=b"changed\n")
    use_fake_runner(monkeypatch, runner)
    operation = {kind: "entry/value"}
    if kind == "set":
        operation["value"] = "x"
    with pytest.raises(PlanRefused) as failure:
        plan(request(make_edit([operation])), root=root)
    assert failure.value.problems[0].code == "edit.duplicate"

    accepted = make_edit([operation], accept_duplicates=["entry/value"])
    assert plan(request(accepted), root=root).has_changes


def test_rm_duplicate_guard_refuses_but_explicit_acceptance_allows_it(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    selector = "/files/etc/fstab/entry/name"
    runner = FakeRunner(matches={selector: (selector + "[1]", selector + "[2]")}, new_bytes=b"changed\n")
    use_fake_runner(monkeypatch, runner)
    with pytest.raises(PlanRefused) as failure:
        plan(request(make_edit([{"rm": "entry/name"}])), root=root)
    assert failure.value.problems[0].code == "edit.duplicate"

    accepted = make_edit([{"rm": "entry/name"}], accept_duplicates=["entry/name"])
    assert plan(request(accepted), root=root).has_changes


def test_records_append_multiple_new_sequence_items_when_tree_is_empty(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"# kept\n")
    source = b"# kept\n"
    first = b"# kept\nUUID=1 /one ext4 defaults 0 2\n"
    final = b"# kept\nUUID=1 /one ext4 defaults 0 2\nUUID=2 /two xfs defaults 0 2\n"
    first_item = {"spec": "UUID=1", "file": "/one"}
    second_item = {"spec": "UUID=2", "file": "/two"}
    runner = ScriptedAugtool(
        {
            source: fstab_state([]),
            first: fstab_state([("01[1]", first_item)]),
            final: fstab_state([("01[1]", first_item), ("01[2]", second_item)]),
        },
        [first, final],
    )
    use_scripted_augtool(monkeypatch, runner)
    resource = make_edit(
        [
            {
                "records": "*",
                "key": "file",
                "exclusive": False,
                "items": [first_item, second_item],
            }
        ]
    )

    change = diff_once(resource, root)

    assert mutation_scripts(runner) == [
        'load\nset "/files/etc/fstab/01[1]/spec" "UUID=1"\nset "/files/etc/fstab/01[1]/file" "/one"\nprint /augeas//error\nsave\n',
        'load\nins 01 after "/files/etc/fstab/01[1]"\nset "/files/etc/fstab/01[2]/spec" "UUID=2"\nset "/files/etc/fstab/01[2]/file" "/two"\nprint /augeas//error\nsave\n',
    ]
    assert change.after["sha256"] == hashlib.sha256(final).hexdigest()


def test_mount_ops_add_two_new_mounts_to_distinct_sequence_nodes(monkeypatch, tmp_path):
    source = b"empty fstab\n"
    first_bytes = b"one mount\n"
    final_bytes = b"two mounts\n"
    first = mount_item("UUID=one", "/one", "ext4", ["defaults"], "0", "2")
    second = mount_item("UUID=two", "/two", "xfs", ["defaults"], "0", "2")
    states = {
        source: fstab_state([]),
        first_bytes: fstab_state([("01[1]", first)]),
        final_bytes: fstab_state([("01[1]", first), ("01[2]", second)]),
    }
    runner = ScriptedAugtool(states, [first_bytes, final_bytes])
    use_scripted_augtool(monkeypatch, runner)
    resource = make_edit(mount_ops([first, second]))
    change = diff_once(resource, fake_root(tmp_path, source))

    assert mutation_scripts(runner) == [
        'load\nset "/files/etc/fstab/01[1]/spec" "UUID=one"\nset "/files/etc/fstab/01[1]/file" "/one"\nset "/files/etc/fstab/01[1]/vfstype" "ext4"\nrm "/files/etc/fstab/01[1]/opt"\nset "/files/etc/fstab/01[1]/opt[1]" "defaults"\nset "/files/etc/fstab/01[1]/dump" "0"\nset "/files/etc/fstab/01[1]/passno" "2"\nprint /augeas//error\nsave\n',
        'load\nins 01 after "/files/etc/fstab/01[1]"\nset "/files/etc/fstab/01[2]/spec" "UUID=two"\nset "/files/etc/fstab/01[2]/file" "/two"\nset "/files/etc/fstab/01[2]/vfstype" "xfs"\nrm "/files/etc/fstab/01[2]/opt"\nset "/files/etc/fstab/01[2]/opt[1]" "defaults"\nset "/files/etc/fstab/01[2]/dump" "0"\nset "/files/etc/fstab/01[2]/passno" "2"\nprint /augeas//error\nsave\n',
    ]
    assert change.after["sha256"] == hashlib.sha256(final_bytes).hexdigest()


def test_swap_op_reobserves_before_replacing_second_changed_swap(monkeypatch, tmp_path):
    source, after_first, final = b"two swaps\n", b"first changed\n", b"both changed\n"
    first_old = swap_item("UUID=one", 1)
    second_old = swap_item("UUID=two", 2)
    first_new = swap_item("UUID=one", 10)
    second_new = swap_item("UUID=two", 20)
    unrelated = {"spec": "UUID=data", "file": "/data", "vfstype": "ext4", "opt": ["defaults"], "dump": "0", "passno": "2"}
    states = {
        source: fstab_state([("01[1]", first_old), ("01[2]", second_old)], swaps=()),
        # The staged lens observation places the original second swap at [3].
        # Reusing the first tree would incorrectly treat [2] as that original row.
        after_first: fstab_state([("01[1]", unrelated), ("01[2]", first_new), ("01[3]", second_old)], swaps=()),
        final: fstab_state([("01[1]", unrelated), ("01[2]", first_new), ("01[3]", second_new)], swaps=()),
    }
    runner = ScriptedAugtool(states, [after_first, final])
    use_scripted_augtool(monkeypatch, runner)
    resource = make_edit([swap_op([first_new, second_new])])
    change = diff_once(resource, fake_root(tmp_path, source))

    assert mutation_scripts(runner) == [
        'load\nins 01 after "/files/etc/fstab/01[1]"\nset "/files/etc/fstab/01[2]/spec" "UUID=one"\nset "/files/etc/fstab/01[2]/file" "none"\nset "/files/etc/fstab/01[2]/vfstype" "swap"\nrm "/files/etc/fstab/01[2]/opt"\nset "/files/etc/fstab/01[2]/opt[1]" "sw"\nset "/files/etc/fstab/01[2]/opt[2]" "pri=10"\nset "/files/etc/fstab/01[2]/dump" "0"\nset "/files/etc/fstab/01[2]/passno" "0"\nrm "/files/etc/fstab/01[1]"\nprint /augeas//error\nsave\n',
        'load\nins 01 after "/files/etc/fstab/01[3]"\nset "/files/etc/fstab/01[4]/spec" "UUID=two"\nset "/files/etc/fstab/01[4]/file" "none"\nset "/files/etc/fstab/01[4]/vfstype" "swap"\nrm "/files/etc/fstab/01[4]/opt"\nset "/files/etc/fstab/01[4]/opt[1]" "sw"\nset "/files/etc/fstab/01[4]/opt[2]" "pri=20"\nset "/files/etc/fstab/01[4]/dump" "0"\nset "/files/etc/fstab/01[4]/passno" "0"\nrm "/files/etc/fstab/01[3]"\nprint /augeas//error\nsave\n',
    ]
    assert change.after["sha256"] == hashlib.sha256(final).hexdigest()


def test_mount_ops_mixed_new_changed_and_unchanged_records(monkeypatch, tmp_path):
    source, after_new, final = b"mixed initial\n", b"after new\n", b"mixed final\n"
    old_changed = mount_item("UUID=changed", "/changed", "ext4", ["defaults"], "0", "2")
    changed = mount_item("UUID=changed", "/changed", "ext4", ["noatime"], "0", "2")
    unchanged = mount_item("UUID=same", "/same", "xfs", ["defaults"], "0", "2")
    new = mount_item("UUID=new", "/new", "ext4", ["defaults"], "0", "2")
    rows_initial = [("01[1]", old_changed), ("01[2]", unchanged)]
    rows_after_new = [*rows_initial, ("01[3]", new)]
    rows_final = [("01[1]", changed), ("01[2]", unchanged), ("01[3]", new)]
    runner = ScriptedAugtool(
        {
            source: fstab_state(rows_initial),
            after_new: fstab_state(rows_after_new),
            final: fstab_state(rows_final),
        },
        [after_new, final],
    )
    use_scripted_augtool(monkeypatch, runner)
    change = diff_once(make_edit(mount_ops([new, changed, unchanged])), fake_root(tmp_path, source))

    assert mutation_scripts(runner) == [
        'load\nins 01 after "/files/etc/fstab/01[2]"\nset "/files/etc/fstab/01[3]/spec" "UUID=new"\nset "/files/etc/fstab/01[3]/file" "/new"\nset "/files/etc/fstab/01[3]/vfstype" "ext4"\nrm "/files/etc/fstab/01[3]/opt"\nset "/files/etc/fstab/01[3]/opt[1]" "defaults"\nset "/files/etc/fstab/01[3]/dump" "0"\nset "/files/etc/fstab/01[3]/passno" "2"\nprint /augeas//error\nsave\n',
        'load\nins 01 after "/files/etc/fstab/01[1]"\nset "/files/etc/fstab/01[2]/spec" "UUID=changed"\nset "/files/etc/fstab/01[2]/file" "/changed"\nset "/files/etc/fstab/01[2]/vfstype" "ext4"\nrm "/files/etc/fstab/01[2]/opt"\nset "/files/etc/fstab/01[2]/opt[1]" "noatime"\nset "/files/etc/fstab/01[2]/dump" "0"\nset "/files/etc/fstab/01[2]/passno" "2"\nrm "/files/etc/fstab/01[1]"\nprint /augeas//error\nsave\n',
    ]
    assert change.after["sha256"] == hashlib.sha256(final).hexdigest()


def test_exclusive_swap_removal_observes_addition_before_removing_old_rows(monkeypatch, tmp_path):
    source, after_add, final = b"old swap\n", b"added swap\n", b"clean swaps\n"
    old_swap = swap_item("UUID=old", 1)
    new_swap = swap_item("UUID=new", 10)
    mount = mount_item("UUID=data", "/data", "ext4", ["defaults"], "0", "2")
    states = {
        source: fstab_state([("01[1]", old_swap), ("01[2]", mount)], swaps=()),
        after_add: fstab_state([("01[1]", old_swap), ("01[2]", mount), ("01[3]", new_swap)], swaps=()),
        final: fstab_state([("01[1]", mount), ("01[2]", new_swap)], swaps=()),
    }
    runner = ScriptedAugtool(states, [after_add, final])
    use_scripted_augtool(monkeypatch, runner)
    change = diff_once(make_edit([swap_op([new_swap])]), fake_root(tmp_path, source))

    assert mutation_scripts(runner) == [
        'load\nins 01 after "/files/etc/fstab/01[2]"\nset "/files/etc/fstab/01[3]/spec" "UUID=new"\nset "/files/etc/fstab/01[3]/file" "none"\nset "/files/etc/fstab/01[3]/vfstype" "swap"\nrm "/files/etc/fstab/01[3]/opt"\nset "/files/etc/fstab/01[3]/opt[1]" "sw"\nset "/files/etc/fstab/01[3]/opt[2]" "pri=10"\nset "/files/etc/fstab/01[3]/dump" "0"\nset "/files/etc/fstab/01[3]/passno" "0"\nprint /augeas//error\nsave\n',
        'load\nrm "/files/etc/fstab/01[1]"\nprint /augeas//error\nsave\n',
    ]
    assert change.after["sha256"] == hashlib.sha256(final).hexdigest()


@pytest.mark.parametrize("mode", ["record-edit", "exclusive-remove"])
def test_record_step_that_does_not_change_staged_bytes_refuses(monkeypatch, tmp_path, mode):
    source = b"unchanged\n"
    if mode == "record-edit":
        resource = make_edit(
            [{"records": "*", "key": "file", "exclusive": False, "items": [{"file": "/new"}]}]
        )
        state = fstab_state([])
        message = "generated records edit did not change staged bytes"
    else:
        old_swap = swap_item("UUID=old", 1)
        resource = make_edit([swap_op([])])
        state = fstab_state([("01[1]", old_swap)], swaps=())
        message = "generated records removal did not change staged bytes"
    runner = ScriptedAugtool({source: state}, [source])
    use_scripted_augtool(monkeypatch, runner)

    with pytest.raises(PlanRefused, match=message):
        diff_once(resource, fake_root(tmp_path, source))


def test_records_equal_item_is_idempotent_and_does_not_rewrite(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"UUID=x /mnt ext4 defaults 0 2\n")
    base = "/files/etc/fstab"
    record = base + "/01[1]"
    tree = "\n".join(
        [
            f"{record}/spec = UUID=x",
            f"{record}/file = /mnt",
            f"{record}/vfstype = ext4",
            f"{record}/opt[1] = defaults",
            f"{record}/dump = 0",
            f"{record}/passno = 2",
        ]
    )
    runner = FakeRunner(tree=tree, matches={base + "/*": (record,)})
    use_fake_runner(monkeypatch, runner)
    item = {"spec": "UUID=x", "file": "/mnt", "vfstype": "ext4", "opt": ["defaults"], "dump": "0", "passno": "2"}

    planned = plan(
        request(make_edit([{"records": "*", "key": "file", "exclusive": False, "items": [item]}])),
        root=root,
    )

    assert not planned.has_changes
    assert not any("--new" in argv for argv, _script in runner.calls)


def test_records_replacement_keeps_position_and_exclusive_removes_unlisted(monkeypatch, tmp_path):
    source, replaced, final = b"old\n", b"replaced\n", b"final\n"
    root = fake_root(tmp_path, source)
    base = "/files/etc/fstab"
    first, second = base + "/01[1]", base + "/01[2]"
    item = {"spec": "UUID=x", "file": "/mnt", "vfstype": "ext4", "opt": ["noatime"], "dump": "0", "passno": "2"}
    current = {"spec": "UUID=x", "file": "/mnt", "vfstype": "ext4", "opt": ["defaults"], "dump": "0", "passno": "2"}
    old_swap = {"spec": "UUID=swap", "file": "none", "vfstype": "swap", "opt": ["sw"], "dump": "0", "passno": "0"}
    runner = ScriptedAugtool(
        {
            source: fstab_state([("01[1]", current), ("01[2]", old_swap)]),
            replaced: fstab_state([("01[1]", item), ("01[2]", old_swap)]),
            final: fstab_state([("01[1]", item)]),
        },
        [replaced, final],
    )
    use_scripted_augtool(monkeypatch, runner)
    operation = {"records": "*", "key": "file", "exclusive": True, "items": [item]}

    change = diff_once(make_edit([operation]), root)

    assert mutation_scripts(runner) == [
        (
            "load\n"
            f'ins 01 after "{first}"\n'
            f'set "{base}/01[2]/spec" "UUID=x"\n'
            f'set "{base}/01[2]/file" "/mnt"\n'
            f'set "{base}/01[2]/vfstype" "ext4"\n'
            f'rm "{base}/01[2]/opt"\n'
            f'set "{base}/01[2]/opt[1]" "noatime"\n'
            f'set "{base}/01[2]/dump" "0"\n'
            f'set "{base}/01[2]/passno" "2"\n'
            f'rm "{first}"\nprint /augeas//error\nsave\n'
        ),
        f'load\nrm "{second}"\nprint /augeas//error\nsave\n',
    ]
    assert change.after["sha256"] == hashlib.sha256(final).hexdigest()


def test_records_duplicate_key_acceptance_names_the_selector(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    base = "/files/etc/fstab"
    first, second = base + "/01[1]", base + "/01[2]"
    tree = f"{first}/file = /mnt\n{second}/file = /mnt\n"
    runner = FakeRunner(tree=tree, matches={base + "/*": (first, second)})
    use_fake_runner(monkeypatch, runner)
    resource = make_edit(
        [{"records": "*", "key": "file", "exclusive": False, "items": [{"file": "/mnt"}]}],
        accept_duplicates=["*"],
    )

    planned = plan(request(resource), root=root)

    assert not planned.has_changes


def test_missing_tool_refusal_and_create_false_refusal(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"old\n")
    resource = make_edit([{"set": "entry/value", "value": "x"}])
    monkeypatch.setattr(augtool.shutil, "which", lambda name: None if name == "augtool" else f"/usr/bin/{name}")
    with pytest.raises(PlanRefused) as missing:
        plan(request(resource), root=root)
    assert missing.value.problems[0].code == "edit.augtool.missing"
    assert "install augeas-tools" in missing.value.problems[0].message

    runner = FakeRunner(new_bytes=b"created\n")
    use_fake_runner(monkeypatch, runner)
    missing_parent = tmp_path / "missing"
    missing_parent.mkdir()
    missing_root = fake_root(missing_parent, None)
    with pytest.raises(PlanRefused) as create:
        plan(request(resource), root=missing_root)
    assert create.value.problems[0].code == "edit.create.missing"


def test_create_true_starts_from_empty_and_uses_documented_new_file_metadata(monkeypatch, tmp_path):
    root = fake_root(tmp_path, None)
    runner = FakeRunner(new_bytes=b"created\n")
    use_fake_runner(monkeypatch, runner)
    planned = plan(
        request(make_edit([{"set": "entry/value", "value": "x"}], create=True)),
        root=root,
    )
    assert planned.changes[0].before == {"exists": False}
    assert planned.changes[0].after["mode"] == 0o644
    assert planned.changes[0].after["uid"] == os.geteuid()
    assert next(script for argv, script in runner.calls if "print \"/files" in script) == (
        'load\nprint "/files/etc/fstab"\nprint /augeas//error\n'
    )


def test_path_allowlist_and_symlink_components_are_refused(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="allowed configuration directory"):
        make_edit([{"rm": "entry"}], file="/var/lib/private.conf")

    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/link").symlink_to(tmp_path, target_is_directory=True)
    runner = FakeRunner()
    use_fake_runner(monkeypatch, runner)
    resource = make_edit([{"rm": "entry"}], file="/etc/link/fstab")
    with pytest.raises(PlanRefused) as failure:
        plan(request(resource), root=root)
    assert failure.value.problems[0].code == "edit.path.unsafe"

    (root / "etc/link").unlink()
    (root / "etc/fstab").mkdir()
    with pytest.raises(PlanRefused) as target_type:
        plan(request(make_edit([{"rm": "entry"}])), root=root)
    assert target_type.value.problems[0].code == "edit.path.unsafe"


def test_validator_argv_is_staged_and_findmnt_is_skipped_for_fake_root(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"old\n")
    runner = FakeRunner(new_bytes=b"new\n")
    use_fake_runner(monkeypatch, runner)
    planned = plan(request(make_edit([{"set": "entry/value", "value": "x"}], validate="findmnt")), root=root)
    assert "validate: skipped(root)" in planned.changes[0].details
    assert runner.validators == []

    runner = FakeRunner(new_bytes=b"new\n")
    use_fake_runner(monkeypatch, runner)
    plan(request(make_edit([{"set": "entry/value", "value": "x"}], validate="sshd")), root=root)
    assert len(runner.validators) == 1
    argv, staged_data = runner.validators[0]
    assert argv[:3] == ("sshd", "-t", "-f")
    assert Path(argv[3]).is_absolute()
    assert staged_data == b"new\n"


@pytest.mark.parametrize(
    ("validator", "expected"),
    [
        ("sshd", ("sshd", "-t", "-f")),
        ("sudoers", ("visudo", "-cf")),
        ("systemd", ("systemd-analyze", "verify")),
    ],
)
def test_native_validator_argv_shapes(monkeypatch, tmp_path, validator, expected):
    root = fake_root(tmp_path, b"old\n")
    runner = FakeRunner(new_bytes=b"new\n")
    use_fake_runner(monkeypatch, runner)
    plan(request(make_edit([{"set": "entry/value", "value": "x"}], validate=validator)), root=root)
    assert runner.validators[0][0][: len(expected)] == expected
    assert Path(runner.validators[0][0][-1]).is_absolute()


def test_findmnt_live_argv_shape_uses_staged_tab_file(monkeypatch, tmp_path):
    staged = tmp_path / "private" / "fstab.augnew"
    staged.parent.mkdir()
    staged.write_bytes(b"UUID=x /mnt ext4 defaults 0 2\n")
    calls = []

    def fake_run(argv, *, timeout):
        calls.append((tuple(argv), timeout))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shadow, "run_command", fake_run)
    edit_module._validate_staged(SimpleNamespace(root=Path("/")), "/etc/fstab", staged, "findmnt")
    assert calls == [(("findmnt", "--verify", "--tab-file", str(staged)), 30.0)]


def test_validator_failure_and_missing_binary_refuse_with_diagnostic(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    runner = FakeRunner(new_bytes=b"new\n")
    runner.validator_returncode = 1
    runner.validator_stderr = "invalid sshd config"
    use_fake_runner(monkeypatch, runner)
    with pytest.raises(PlanRefused) as failure:
        plan(request(make_edit([{"set": "entry/value", "value": "x"}], validate="sshd")), root=root)
    assert failure.value.problems[0].code == "edit.validate.failed"
    assert "invalid sshd config" in failure.value.problems[0].message

    runner = FakeRunner(new_bytes=b"new\n")
    runner.validator_oserror = FileNotFoundError("sshd missing")
    use_fake_runner(monkeypatch, runner)
    with pytest.raises(PlanRefused) as missing:
        plan(request(make_edit([{"set": "entry/value", "value": "x"}], validate="sshd")), root=root)
    assert missing.value.problems[0].code == "edit.validate.failed"
    assert "sshd missing" in missing.value.problems[0].message


def test_cmd_validator_requires_and_replaces_one_staged_path(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    runner = FakeRunner(new_bytes=b"new\n")
    use_fake_runner(monkeypatch, runner)
    plan(
        request(
            make_edit(
                [{"set": "entry/value", "value": "x"}],
                validate=["cmd", "validator", "--", "%s"],
            )
        ),
        root=root,
    )
    assert runner.validators[0][0][0:2] == ("validator", "--")
    assert runner.validators[0][0][2].endswith("/fstab")
    with pytest.raises(ValueError, match="exactly one %s"):
        make_edit([{"rm": "entry"}], validate=["cmd", "validator"])


def test_absent_deletes_whole_file_and_declares_reload(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"old\n")
    runner = FakeRunner()
    use_fake_runner(monkeypatch, runner)
    resource = Edit("/etc/fstab", "Fstab", "absent", (), False, "daemon-reload")
    planned = plan(request(resource), root=root)
    assert planned.changes[0].action == "change"
    assert planned.changes[0].details == ("state=absent",)
    actions = resource.post_actions(list(planned.changes))
    assert [action.argv for action in actions] == [("systemctl", "daemon-reload")]
    assert actions[0].phase == "daemon_reload"
    assert runner.calls == []


def test_pending_daemon_reload_is_deferred_and_no_change_requests_no_action():
    pending = make_edit([{"rm": "entry"}], on_change="pending")
    change = edit_module.Change("edit", "edit:/etc/fstab", "change", {}, {}, pending)
    action = pending.post_actions([change])[0]
    assert action.argv == ("systemctl", "daemon-reload")
    assert action.phase == "daemon_reload" and action.pending is True
    assert pending.post_actions([]) == []
    assert make_edit([], on_change="none").post_actions([change]) == []


def test_edit_roundtrip_is_strict_and_to_toml_keeps_ops(monkeypatch, tmp_path):
    resource = make_edit(
        [
            {
                "records": "*",
                "key": "file",
                "exclusive": False,
                "items": [{"spec": "UUID=x", "file": "/mnt", "vfstype": "ext4", "opt": ["defaults"], "dump": "0", "passno": "2"}],
            }
        ],
        validate="findmnt",
        on_change="daemon-reload",
    )
    request_value = request(resource)
    request_file = tmp_path / "request.toml"
    request_file.write_text(request_value.to_toml(), encoding="utf-8")
    round_trip = load_request(request_file)
    assert round_trip == request_value
    with pytest.raises(ValueError, match="unknown edit key"):
        load_request(
            {
                "augeaswrap": 1,
                "writer": "bad request",
                "edit": [{"file": "/etc/fstab", "lens": "Fstab", "state": "present", "ops": [], "create": False, "on_change": "none", "unexpected": True}],
            }
        )


def test_edit_set_rm_and_clear_ops_round_trip_through_toml(tmp_path):
    resource = make_edit(
        [
            {"set": "entry/name", "value": "value"},
            {"rm": "entry/old"},
            {"clear": "entry/children"},
        ]
    )
    request_value = request(resource)
    request_path = tmp_path / "set-rm-clear.toml"
    request_path.write_text(request_value.to_toml(), encoding="utf-8")
    loaded = load_request(request_path)
    assert loaded == request_value
    assert loaded.to_toml() == request_value.to_toml()


def test_apply_revert_restores_exact_bytes_and_metadata_without_replaying_augtool(monkeypatch, tmp_path):
    root = fake_root(tmp_path, b"before\n")
    target = root / "etc/fstab"
    target.chmod(0o640)
    runner = FakeRunner(new_bytes=b"after\n")
    use_fake_runner(monkeypatch, runner)
    resource = make_edit([{"set": "entry/value", "value": "x"}], on_change="daemon-reload")
    planned = plan(request(resource), root=root)
    result = apply(planned)
    assert target.read_bytes() == b"after\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    augtool_call_count = len(runner.calls)
    revert(result.txn, root=root)
    assert target.read_bytes() == b"before\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert len(runner.calls) == augtool_call_count
    assert result.txn is not None


def test_absent_edit_round_trip_requires_no_ops_and_false_create():
    absent = Edit("/etc/fstab", "Fstab", "absent", (), False, "none")
    assert absent.state == "absent"
    with pytest.raises(ValueError, match="absent edit"):
        Edit("/etc/fstab", "Fstab", "absent", ({"rm": "01"},), False, "none")
    with pytest.raises(ValueError, match="absent edit"):
        Edit("/etc/fstab", "Fstab", "absent", (), True, "none")
