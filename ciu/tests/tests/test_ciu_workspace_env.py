"""ciu-P33 / CIU-60 / ciu-P47 — generated identity facts, plus `ciu env print`.

Covers:

* O1 — `ciu env generate` writes a six-key `[ciu.instance.generated]` table to
  `ciu.instance.generated.toml`, from the SAME in-memory values it wrote into
  `ciu.env`, idempotently and WHOLESALE.
* O2 — the operator's own `ciu.global.instance.toml.j2` is never written at
  all. ciu-P47 replaced O2's old oracle ("hand-authored content in the SAME
  file survives byte for byte", which the deleted surgical block-replace
  existed to satisfy) with the stronger one the split makes available: CIU has
  no writer for that file, so there is nothing left to survive.
* O3 — those facts reach templates through `render_global_chain`'s merge, with
  no bespoke Jinja context injection, and `{{ ciu.instance.generated.* }}`
  resolves exactly as it did before the split.
* O4 — the write fires for the PRIMARY/main checkout too (no S16 record, no
  pre-existing overlay), not only for worktree instances.
* O5 — `ciu env print` emits `export KEY='value'` lines and nothing else.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli  # noqa: E402
from ciu import workspace_env  # noqa: E402
from ciu.config_model import render_global_chain, render_toml_template  # noqa: E402

OVERLAY = "ciu.global.instance.toml.j2"
FACTS_FILE = "ciu.instance.generated.toml"

FACTS = {
    "repo_name": "vbpub",
    "instance_id": "ab12cd",
    "network": "vbpub-ab12cd-network",
    "physical_repo_root": "/host/checkouts/vbpub",
    "repo_root": "/workspaces/vbpub",
    "public_fqdn": "example.test",
}


def _hermetic_generate(monkeypatch, repo_root: Path) -> Path:
    """`generate_ciu_env` with the host-fact detectors pinned.

    Only the DETECTORS are pinned — the write paths under test run for real.
    """
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root", lambda root: repo_root
    )
    monkeypatch.setattr(
        workspace_env,
        "_detect_public_fqdn",
        lambda root, require_fqdn: {
            "PUBLIC_IP": "203.0.113.7",
            "PUBLIC_FQDN": "example.test",
            "PUBLIC_TLS_CRT_PEM": "",
            "PUBLIC_TLS_KEY_PEM": "",
        },
    )
    monkeypatch.setattr(workspace_env, "_detect_docker_gid", lambda: "999")
    monkeypatch.setattr(workspace_env, "_detect_host_mdt_tmp", lambda: "")
    monkeypatch.setattr(
        workspace_env, "_detect_governance_read_iops", lambda: ("200", "fallback")
    )
    return workspace_env.generate_ciu_env(repo_root)


def _generated_table(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["ciu"]["instance"][
        "generated"
    ]


# ---------------------------------------------------------------------------
# O1 — the table is written, from the SAME values, idempotently
# ---------------------------------------------------------------------------


def test_generate_writes_the_six_generated_keys(monkeypatch, tmp_path):
    env_path = _hermetic_generate(monkeypatch, tmp_path)
    facts_file = tmp_path / FACTS_FILE

    assert facts_file.exists()
    # ciu-P47: the operator's own overlay is NOT created as a side effect of a
    # generate any more — the facts have their own file.
    assert not (tmp_path / OVERLAY).exists()
    table = _generated_table(facts_file)
    assert sorted(table) == sorted(workspace_env.GENERATED_FACTS_KEYS)

    # Same in-memory tuple as ciu.env: assert the two records AGREE, which is
    # the property a second, independent derivation could not guarantee.
    env = workspace_env.parse_workspace_env(env_path)
    assert table["repo_name"] == env["REPO_NAME"]
    assert table["instance_id"] == env["INSTANCE_ID"]
    assert table["network"] == env["DOCKER_NETWORK_INTERNAL"]
    assert table["physical_repo_root"] == env["PHYSICAL_REPO_ROOT"]
    assert table["repo_root"] == env["REPO_ROOT"] == str(tmp_path)
    assert table["public_fqdn"] == env["PUBLIC_FQDN"] == "example.test"


def test_generate_never_re_reads_ciu_env_to_build_the_table(monkeypatch, tmp_path):
    """The facts come from memory, not from a read-back of the file just
    written: deleting ciu.env mid-flight cannot change the table."""
    calls: list[Path] = []
    real = workspace_env.parse_workspace_env

    def spy(path):
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(workspace_env, "parse_workspace_env", spy)
    _hermetic_generate(monkeypatch, tmp_path)
    assert calls == []


def test_second_generate_is_byte_identical(monkeypatch, tmp_path):
    _hermetic_generate(monkeypatch, tmp_path)
    first = (tmp_path / FACTS_FILE).read_text(encoding="utf-8")
    _hermetic_generate(monkeypatch, tmp_path)
    second = (tmp_path / FACTS_FILE).read_text(encoding="utf-8")

    assert second == first
    # Wholesale rewrite, not append: exactly one table header, ever.
    assert second.count("[ciu.instance.generated]") == 1


def test_a_rewrite_replaces_a_stale_value_and_leaves_nothing_of_the_old_file(
    tmp_path,
):
    """The whole file is replaced, so a stale value cannot linger anywhere in
    it — the property the deleted surgical block-replace had to establish by
    finding and rewriting exactly the right span."""
    workspace_env.write_generated_facts(tmp_path, FACTS)
    workspace_env.write_generated_facts(
        tmp_path, {**FACTS, "instance_id": "ff99ee"}
    )
    body = (tmp_path / FACTS_FILE).read_text(encoding="utf-8")

    assert body.count("[ciu.instance.generated]") == 1
    assert 'instance_id = "ab12cd"' not in body
    assert _generated_table(tmp_path / FACTS_FILE)["instance_id"] == "ff99ee"


def test_a_rewrite_discards_anything_hand_added_to_the_ciu_owned_file(tmp_path):
    """CIU owns every byte of this file, and says so in its own banner.

    A hand edit here is not preserved — that is the deliberate difference from
    the operator's overlay next door, and the reason there is no preservation
    logic left to get wrong.
    """
    workspace_env.write_generated_facts(tmp_path, FACTS)
    path = tmp_path / FACTS_FILE
    path.write_text(
        path.read_text(encoding="utf-8") + '\n[operator]\nkeep = "me"\n',
        encoding="utf-8",
    )

    workspace_env.write_generated_facts(tmp_path, FACTS)
    body = path.read_text(encoding="utf-8")

    assert "[operator]" not in body
    assert "keep" not in body


def test_incomplete_facts_are_refused_by_name(tmp_path):
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.write_generated_facts(
            tmp_path, {k: v for k, v in FACTS.items() if k != "public_fqdn"}
        )
    assert "public_fqdn" in str(exc.value)
    assert not (tmp_path / FACTS_FILE).exists()


def test_block_key_order_is_fixed_not_mapping_order(tmp_path):
    """A caller's dict ordering must not move bytes in the file."""
    reversed_facts = dict(reversed(list(FACTS.items())))
    lines = workspace_env.render_generated_facts_block(reversed_facts)
    keys = [line.split(" = ")[0] for line in lines if " = " in line]
    assert tuple(keys) == workspace_env.GENERATED_FACTS_KEYS


def test_the_writer_never_reads_the_file_it_is_about_to_replace(
    tmp_path, monkeypatch
):
    """ciu-P47's mechanism deletion, stated as a behaviour.

    The surgical upsert had to READ the existing file to find the span it
    owned, which is why an unreadable one was a distinct failure mode with its
    own error path. A wholesale rewrite reads nothing — so a file that cannot
    be read at all is not an obstacle to writing the correct one.
    """
    (tmp_path / FACTS_FILE).write_text("[ciu.instance]\n", encoding="utf-8")

    def boom(*_a, **_kw):
        raise OSError("EIO")

    monkeypatch.setattr(Path, "read_text", boom)
    workspace_env.write_generated_facts(tmp_path, FACTS)

    monkeypatch.undo()
    assert _generated_table(tmp_path / FACTS_FILE) == FACTS


def test_an_unwritable_target_is_reported_and_leaves_no_temp_file(
    tmp_path, monkeypatch
):
    def boom(*_a, **_kw):
        raise OSError("ENOSPC")

    monkeypatch.setattr(Path, "open", boom)
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.write_generated_facts(tmp_path, FACTS)
    assert FACTS_FILE in str(exc.value)
    assert list(tmp_path.iterdir()) == []


def test_temp_file_is_cleaned_up_when_the_replace_fails(tmp_path, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("EXDEV")

    monkeypatch.setattr(workspace_env.os, "replace", boom)
    with pytest.raises(workspace_env.WorkspaceEnvError):
        workspace_env.write_generated_facts(tmp_path, FACTS)
    assert list(tmp_path.iterdir()) == []


def test_temp_file_unlink_failure_does_not_mask_the_write_error(
    tmp_path, monkeypatch
):
    """The reported failure is the WRITE failure, never the cleanup's."""
    monkeypatch.setattr(
        workspace_env.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("EXDEV"))
    )
    monkeypatch.setattr(
        Path, "unlink", lambda *a, **k: (_ for _ in ()).throw(OSError("EACCES"))
    )
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.write_generated_facts(tmp_path, FACTS)
    assert "EXDEV" in str(exc.value)


# ---------------------------------------------------------------------------
# O2 — the operator's own overlay is never written at all (ciu-P47)
# ---------------------------------------------------------------------------

HAND_AUTHORED = """\
# Operator notes: this checkout points at the staging reference.
[ciu.instance]
service_profiles = ["core", "db"]

[ciu.instance.shared_infra]
ref_path = "/repo/.worktrees/primary-ref"
network = "repo-ab12cd-network"
services = ["api", "worker"]
ref_projects = ["idp-dev-idp"]

# An ordinary sparse override, nothing to do with [ciu.instance].
[deploy]
some_key = "operator override"   # a trailing inline comment

# A hand-written comment introducing the last table.
[topology.services.vault]
internal_host = "dstdns-98535c-vault"
"""


def test_the_operators_overlay_is_untouched_by_a_generate(monkeypatch, tmp_path):
    """The post-split form of O2, and a strictly stronger claim.

    Before ciu-P47 this asserted that hand-authored content in the SAME file
    survived the upsert. Now CIU has no writer for that file at all, so the
    assertion is byte equality of the whole thing across a real generate —
    comments, inline comment, blank lines, ordering and all.
    """
    overlay = tmp_path / OVERLAY
    overlay.write_text(HAND_AUTHORED, encoding="utf-8")

    _hermetic_generate(monkeypatch, tmp_path)

    assert overlay.read_text(encoding="utf-8") == HAND_AUTHORED
    # …and the facts went somewhere else entirely.
    assert "[ciu.instance.generated]" not in overlay.read_text(encoding="utf-8")
    assert _generated_table(tmp_path / FACTS_FILE)["repo_root"] == str(tmp_path)


def test_no_writer_anywhere_opens_the_operators_overlay_during_a_generate(
    monkeypatch, tmp_path
):
    """The negative, proven at the filesystem seam rather than by inspection.

    A surviving surgical-upsert path would have to open the overlay for
    writing; nothing does. Read access is deliberately still allowed — the
    S3.3 chain renders that file — so only WRITE modes are refused here.
    """
    (tmp_path / OVERLAY).write_text(HAND_AUTHORED, encoding="utf-8")
    real_open = Path.open

    def guard(self, mode="r", *args, **kwargs):
        if self.name.startswith(OVERLAY) and any(
            flag in mode for flag in ("w", "a", "x", "+")
        ):
            raise AssertionError(f"a writer opened {self} with mode {mode!r}")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    _hermetic_generate(monkeypatch, tmp_path)

    monkeypatch.undo()
    assert (tmp_path / OVERLAY).read_text(encoding="utf-8") == HAND_AUTHORED


def test_a_stale_generated_table_left_in_the_overlay_is_not_adopted(tmp_path):
    """A pre-P47 checkout's leftover, and the hard cutover over it.

    The old file may still carry a complete-looking `[ciu.instance.generated]`
    table. It is not read, not migrated, and not deleted — `ciu migration-check`
    reports it and the operator removes it.
    """
    (tmp_path / OVERLAY).write_text(
        '[ciu.instance.generated]\nrepo_name = "STALE"\n', encoding="utf-8"
    )
    assert workspace_env.read_generated_facts(tmp_path) == {}

    workspace_env.write_generated_facts(tmp_path, FACTS)

    assert workspace_env.read_generated_facts(tmp_path) == FACTS
    assert "STALE" not in (tmp_path / FACTS_FILE).read_text(encoding="utf-8")
    assert "STALE" in (tmp_path / OVERLAY).read_text(encoding="utf-8")


def test_the_generated_file_names_itself_as_ciu_owned(tmp_path):
    """The banner sits at the TOP of the file now (CIU owns all of it), and a
    second write does not duplicate it."""
    workspace_env.write_generated_facts(tmp_path, FACTS)
    workspace_env.write_generated_facts(tmp_path, FACTS)
    body = (tmp_path / FACTS_FILE).read_text(encoding="utf-8")

    assert body.startswith("# CIU-owned (S3.1b) — GENERATED.")
    assert body.count("hand edits are silently overwritten") == 1
    # It points the reader at the file that IS theirs to edit.
    assert OVERLAY in body
    assert body.endswith("\n")


# ---------------------------------------------------------------------------
# O3 — the facts reach templates through the EXISTING merge
# ---------------------------------------------------------------------------


def test_generated_facts_land_in_the_merged_global_config(monkeypatch, tmp_path):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    _hermetic_generate(monkeypatch, tmp_path)

    merged = render_global_chain(tmp_path, tmp_path)
    generated = merged["ciu"]["instance"]["generated"]
    assert generated["physical_repo_root"] == str(tmp_path)
    assert generated["repo_root"] == str(tmp_path)
    assert sorted(generated) == sorted(workspace_env.GENERATED_FACTS_KEYS)


def test_a_jinja_template_can_read_the_facts_like_any_other_value(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    _hermetic_generate(monkeypatch, tmp_path)
    merged = render_global_chain(tmp_path, tmp_path)

    stack = tmp_path / "stack"
    stack.mkdir()
    template = stack / "ciu.defaults.toml.j2"
    template.write_text(
        "[app]\n"
        'mount = "{{ ciu.instance.generated.physical_repo_root }}"\n'
        'net = "{{ ciu.instance.generated.network }}"\n',
        encoding="utf-8",
    )
    rendered = render_toml_template(template, merged, environ={})
    assert rendered["app"]["mount"] == str(tmp_path)
    assert rendered["app"]["net"].endswith("-network")


def test_the_facts_are_not_injected_as_a_bespoke_context_field(
    monkeypatch, tmp_path
):
    """Negative of O3: with the FACTS FILE removed, the facts are gone. If
    some code path were injecting them into the Jinja context directly, they
    would survive the file's absence — that is the hazard this design rejects.
    """
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / FACTS_FILE).unlink()

    merged = render_global_chain(tmp_path, tmp_path)
    assert "generated" not in merged.get("ciu", {}).get("instance", {})


def test_the_derived_fact_outranks_the_same_key_hand_written_in_the_overlay(
    monkeypatch, tmp_path
):
    """O3's ORDERING half: generated facts merge AFTER the operator's overlay.

    ciu-P47 introduced an ordering choice that did not exist before it. While
    both lived in one file, the surgical upsert made the CIU-written table the
    last word on those six keys by construction — there was no order to get
    wrong. Two files means `render_global_chain` now picks, and the pick is
    load-bearing: an operator who hand-writes `[ciu.instance.generated]` into
    their OWN overlay (copying it out of a pre-split checkout during the §21
    migration is the obvious way to end up here) must NOT thereby shadow the
    derived value with a stale one. A compose project named from a stale
    `instance_id` collides with, or silently adopts, another checkout's
    resources.

    Pinned with a value that DIVERGES between the two orders, so the assertion
    fails if the merge line is ever moved above the overlay block rather than
    passing under either — which is exactly what the ciu-P47 reviewer
    demonstrated the suite could not previously tell apart.
    """
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    _hermetic_generate(monkeypatch, tmp_path)
    derived = _generated_table(tmp_path / FACTS_FILE)

    # The operator's own file, claiming every one of CIU's six keys.
    (tmp_path / OVERLAY).write_text(
        "[ciu.instance.generated]\n"
        + "".join(f'{key} = "OPERATOR-WINS"\n' for key in derived)
        + "\n[deploy]\nproject_name = \"mine\"\n",
        encoding="utf-8",
    )

    merged = render_global_chain(tmp_path, tmp_path)
    generated = merged["ciu"]["instance"]["generated"]

    assert generated == derived, (
        "the CIU-derived facts must survive an operator writing the same keys"
    )
    assert "OPERATOR-WINS" not in generated.values()
    # …and the overlay still wins everywhere it is not colliding with CIU's
    # own table, so this is a precedence rule about six keys, not the overlay
    # layer being demoted wholesale.
    assert merged["deploy"]["project_name"] == "mine"


# ---------------------------------------------------------------------------
# O4 — the PRIMARY/main checkout, not just worktree instances
# ---------------------------------------------------------------------------


def test_primary_checkout_with_no_instance_record_is_covered(monkeypatch, tmp_path):
    """No S16 lifecycle record anywhere, no pre-existing overlay: this is the
    workspace the operator was standing in when they hit the original bug."""
    from ciu import worktree

    assert worktree.read_own_instance_record(tmp_path) is None
    assert not (tmp_path / FACTS_FILE).exists()

    _hermetic_generate(monkeypatch, tmp_path)

    assert (tmp_path / FACTS_FILE).exists()
    assert _generated_table(tmp_path / FACTS_FILE)["repo_root"] == str(tmp_path)


def test_write_does_not_consult_the_instance_record_at_all(monkeypatch, tmp_path):
    """Negative of O4: gating on `find_instance_record(...) is not None` would
    show up as a call. There is none."""
    from ciu import worktree

    seen: list[object] = []

    def spy(*args, **kwargs):
        seen.append(args)
        return None

    monkeypatch.setattr(worktree, "read_own_instance_record", spy)
    _hermetic_generate(monkeypatch, tmp_path)
    assert seen == []
    assert (tmp_path / FACTS_FILE).exists()


def test_a_worktree_instances_overlay_and_the_facts_merge_into_one_view(
    monkeypatch, tmp_path
):
    """The other half of O4: a worktree instance whose overlay CIU-52 already
    wrote keeps every one of those values, AND the generated facts land in the
    merged config alongside them — two files, one merged `[ciu.instance]`.
    """
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[ciu]\nenv = "test"\n', encoding="utf-8"
    )
    (tmp_path / OVERLAY).write_text(HAND_AUTHORED, encoding="utf-8")

    _hermetic_generate(monkeypatch, tmp_path)

    instance = render_global_chain(tmp_path, tmp_path)["ciu"]["instance"]
    assert instance["service_profiles"] == ["core", "db"]
    assert instance["shared_infra"]["ref_projects"] == ["idp-dev-idp"]
    assert sorted(instance["generated"]) == sorted(
        workspace_env.GENERATED_FACTS_KEYS
    )


# ---------------------------------------------------------------------------
# O5 — `ciu env print`
# ---------------------------------------------------------------------------


def _run_env_print(monkeypatch, tmp_path, argv=()):
    monkeypatch.chdir(tmp_path)
    return cli._env_print(list(argv))


def test_env_print_emits_export_lines_and_nothing_else(
    monkeypatch, tmp_path, capsys
):
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    # Drop the generate step's own output (CIU-75 prints the one-release
    # ciu.env deprecation notice there) so this asserts on `env print` ALONE —
    # its stdout is the eval'able stream and must carry nothing else.
    capsys.readouterr()

    rc = _run_env_print(monkeypatch, tmp_path)
    out = capsys.readouterr()

    assert rc == 0
    assert out.err == ""
    lines = out.out.splitlines()
    assert lines
    assert all(line.startswith("export ") for line in lines)
    # Every value is single-quoted — one shape, eyeball-able.
    for line in lines:
        assert line.split("=", 1)[1].startswith("'")
        assert line.endswith("'")
    for key in workspace_env.REQUIRED_KEYS_CORE:
        assert any(line.startswith(f"export {key}=") for line in lines)


def test_env_print_has_no_side_effects(monkeypatch, tmp_path, capsys):
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    _run_env_print(monkeypatch, tmp_path)
    capsys.readouterr()

    after = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert after == before


def test_env_print_refuses_loudly_when_ciu_env_is_missing(
    monkeypatch, tmp_path, capsys
):
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    rc = _run_env_print(monkeypatch, tmp_path)
    out = capsys.readouterr()

    assert rc == 1
    assert out.out == ""
    assert "ciu env generate" in out.err


def test_env_print_reports_a_malformed_ciu_env_without_a_traceback(
    monkeypatch, tmp_path, capsys
):
    """A malformed entry is an operator mistake, not an internal fault: clean
    `[ERROR]` + exit 1, never a raw traceback.

    The empty-stdout assertion is a PIN, not a fix: `parse_workspace_env`
    already returns a fully-built dict, so the pre-fix
    `for k, v in parse_workspace_env(f).items()` could not print partially
    either. It is worth keeping because it would catch a future refactor of
    that function into a generator, which WOULD start leaking half an
    environment into `eval "$(ciu env print)"` — and half an environment is
    worse than none, because `eval` succeeds on it.
    """
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    (tmp_path / "ciu.env").write_text(
        'export REPO_ROOT="/repo"\nthis is not a valid entry\n', encoding="utf-8"
    )

    rc = _run_env_print(monkeypatch, tmp_path)
    out = capsys.readouterr()

    assert rc == 1
    assert out.err.startswith("[ERROR] ")
    assert "Traceback" not in out.err
    assert out.out == ""


def test_env_print_reports_a_directory_named_ciu_env(monkeypatch, tmp_path, capsys):
    """`exists()` is true for a DIRECTORY, so the guard has to be `is_file()`
    or the read below raises IsADirectoryError as a traceback."""
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    (tmp_path / "ciu.env").mkdir()

    rc = _run_env_print(monkeypatch, tmp_path)
    out = capsys.readouterr()

    assert rc == 1
    assert out.out == ""
    assert out.err.startswith("[ERROR] ")
    assert "Traceback" not in out.err
    assert "ciu env generate" in out.err


def test_env_print_reports_an_unreadable_ciu_env(monkeypatch, tmp_path, capsys):
    """A read that fails is an `[ERROR]` too, not a traceback.

    A non-UTF-8 byte is the case that proves the except clause needs
    `UnicodeDecodeError` named explicitly: it derives from ValueError, NOT
    from OSError, so the `(OSError, WorkspaceEnvError)` pattern this was
    modelled on does not catch it.
    """
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    (tmp_path / "ciu.env").write_bytes(b'export A="\xff\xfe"\n')

    rc = _run_env_print(monkeypatch, tmp_path)
    out = capsys.readouterr()

    assert rc == 1
    assert out.out == ""
    assert out.err.startswith("[ERROR] could not read ")
    assert "Traceback" not in out.err


def test_env_print_reports_a_bad_define_root(monkeypatch, tmp_path, capsys):
    rc = _run_env_print(
        monkeypatch, tmp_path, ["--define-root", str(tmp_path / "nope")]
    )
    out = capsys.readouterr()
    assert rc == 1
    assert "[ERROR]" in out.err


def test_env_print_honours_define_root(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _hermetic_generate(monkeypatch, repo)

    monkeypatch.chdir(elsewhere)
    rc = cli._env_print(["--define-root", str(repo)])
    out = capsys.readouterr()

    assert rc == 0
    assert f"export REPO_ROOT='{repo}'" in out.out


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plain", "'plain'"),
        ("", "''"),
        ("with space", "'with space'"),
        ("it's", "'it'\"'\"'s'"),
        ("$(rm -rf /)", "'$(rm -rf /)'"),
    ],
)
def test_shell_export_value_quoting(raw, expected):
    assert cli._shell_export_value(raw) == expected


def test_eval_of_env_print_populates_a_real_shell(monkeypatch, tmp_path):
    """O5's end: `eval "$(ciu env print)"` really does set the core keys."""
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    # An awkward value that only survives correct quoting.
    env_file = tmp_path / "ciu.env"
    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        + "export CIU_P33_PROBE=\"it's a $(nasty) value\"\n",
        encoding="utf-8",
    )

    src = str(Path(__file__).resolve().parents[2] / "src")
    runner = tmp_path / "print_env.py"
    runner.write_text(
        "import sys\n"
        f"sys.path.insert(0, {src!r})\n"
        "from ciu import cli\n"
        "sys.exit(cli._env_print([]))\n",
        encoding="utf-8",
    )
    script = (
        f'eval "$({shlex.quote(sys.executable)} {shlex.quote(str(runner))})" && '
        'printf "%s\\n%s\\n" "$DOCKER_NETWORK_INTERNAL" "$CIU_P33_PROBE"'
    )
    res = subprocess.run(
        ["bash", "-c", script], cwd=str(tmp_path), capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr
    network, probe = res.stdout.splitlines()
    assert network.endswith("-network")
    assert probe == "it's a $(nasty) value"


def test_env_print_help_is_reachable_and_names_eval(capsys):
    assert cli._wants_verb_help("env", ["print", "--help"]) is False
    with pytest.raises(SystemExit) as exc:
        cli._env_print(["--help"])
    assert exc.value.code == 0
    assert "--define-root" in capsys.readouterr().out


def test_ciu_env_print_is_reachable_from_the_public_dispatcher(
    monkeypatch, tmp_path, capsys
):
    _hermetic_generate(monkeypatch, tmp_path)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ciu", "env", "print"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    assert "export REPO_ROOT=" in capsys.readouterr().out


def test_env_verb_help_text_names_print_and_is_honest_about_eval():
    text = cli._VERB_HELP["env"]
    assert "ciu env print" in text
    assert 'eval "$(ciu env print)"' in text
    # O5's negative: never documented as applying/sourcing into this shell.
    assert "ciu env apply" not in text
    assert "ciu env source" not in text
