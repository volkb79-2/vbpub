from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from pathlib import PurePosixPath

import pytest

from conftest import GitRepo, prepared_snapshot

from assay import mutation, runner
from assay.adapters import sql as sql_adapter_module
from assay.adapters.sql import SqlAdapter
from assay.errors import AssayError, ReasonCode


def test_sql_context_reads_unchanged_sources_from_prepared_manifest(
    git_repo: GitRepo, tmp_path: Path
):
    index_source = "CREATE UNIQUE INDEX idx ON s.t (a);\n"
    attachment_source = (
        "ALTER TABLE s.t ADD CONSTRAINT uq UNIQUE USING INDEX idx;\n"
    )
    git_repo.write("src/01-index.sql", index_source)
    git_repo.write("src/02-attach.sql", attachment_source)
    head = git_repo.commit_all("add SQL index and attachment")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        assert set(prepared.paths_under(PurePosixPath("src"))) == {
            PurePosixPath("src/01-index.sql"),
            PurePosixPath("src/02-attach.sql"),
        }
        context_sources = runner._mutation_context_sources(
            prepared=prepared,
            snapshot_repo_top=prepared.spec.repo_top,
            source_root_paths=(git_repo.path / "src",),
            adapter=SqlAdapter(),
            deadline=runner.LaneDeadline(expires_at=60.0, monotonic=lambda: 0.0),
        )
        target = mutation.MutationTarget(
            path="src/01-index.sql",
            text=index_source,
            lines=frozenset({1}),
        )
        jobs = mutation.collect_mutation_sites(
            (target,),
            adapter=SqlAdapter(),
            operators=("sql:drop-unique",),
            limit=10,
            context_sources=context_sources,
        )

    assert jobs == ()


def test_sql_context_ignores_malformed_test_sources_but_keeps_ddl_candidates(
    git_repo: GitRepo, tmp_path: Path
):
    index_source = "CREATE UNIQUE INDEX idx ON s.t (a);\n"
    git_repo.write("src/01-index.sql", index_source)
    git_repo.write("src/tests/test_malformed.sql", "SELECT 'unterminated\n")
    head = git_repo.commit_all("add DDL and malformed SQL test fixture")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        context_sources = runner._mutation_context_sources(
            prepared=prepared,
            snapshot_repo_top=prepared.spec.repo_top,
            source_root_paths=(git_repo.path / "src",),
            adapter=SqlAdapter(),
            deadline=runner.LaneDeadline(expires_at=60.0, monotonic=lambda: 0.0),
        )
        target = mutation.MutationTarget(
            path="src/01-index.sql",
            text=index_source,
            lines=frozenset({1}),
        )
        jobs = mutation.collect_mutation_sites(
            (target,),
            adapter=SqlAdapter(),
            operators=("sql:drop-unique",),
            limit=10,
            context_sources=context_sources,
        )

    assert len(jobs) == 1
    assert jobs[0].site.lineno == 1


def test_sql_context_enumerates_many_roots_with_deadline_checks_in_one_manifest_pass(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    for index in range(130):
        git_repo.write(f"src/migrations/{index:03d}.sql", "SELECT 1;\n")
    head = git_repo.commit_all("add many SQL context paths")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        calls += 1
        return 2.0 if calls >= 6 else 0.0

    deadline = runner.LaneDeadline(
        expires_at=1.0, monotonic=monotonic, honors_termination=False
    )

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        blob_reads = 0
        original_read_regular_file = prepared.read_regular_file

        def tracked_read_regular_file(path, *, timeout):
            nonlocal blob_reads
            blob_reads += 1
            return original_read_regular_file(path, timeout=timeout)

        monkeypatch.setattr(prepared, "read_regular_file", tracked_read_regular_file)
        with pytest.raises(AssayError) as caught:
            runner._mutation_context_sources(
                prepared=prepared,
                snapshot_repo_top=prepared.spec.repo_top,
                source_root_paths=(
                    git_repo.path / "src",
                    git_repo.path / "src" / "migrations",
                ),
                adapter=SqlAdapter(),
                deadline=deadline,
            )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert calls == 6
    assert blob_reads == 0


def test_sql_context_union_of_overlapping_roots_reads_each_manifest_path_once(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    git_repo.write("src/root.sql", "SELECT 0;\n")
    git_repo.write("src/migrations/one.sql", "SELECT 1;\n")
    git_repo.write("src/migrations/two.sql", "SELECT 2;\n")
    head = git_repo.commit_all("add overlapping SQL source roots")
    scratch = tmp_path / "snapshot-seed"
    scratch.mkdir()

    class CountingEntries:
        def __init__(self, entries):
            self.entries = entries
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            assert self.iterations == 1
            return iter(self.entries)

    with prepared_snapshot(git_repo, commit=head, scratch_root=scratch) as prepared:
        counting_entries = CountingEntries(prepared._manifest.entries)
        original_manifest = prepared._manifest

        class CountingManifest:
            entries = counting_entries

            def by_path(self):
                return original_manifest.by_path()

        monkeypatch.setattr(prepared, "_manifest", CountingManifest())
        context = runner._mutation_context_sources(
            prepared=prepared,
            snapshot_repo_top=prepared.spec.repo_top,
            source_root_paths=(
                git_repo.path / "src",
                git_repo.path / "src" / "migrations",
            ),
            adapter=SqlAdapter(),
            deadline=runner.LaneDeadline(expires_at=60.0, monotonic=lambda: 0.0),
        )
        assert context is not None
        assert counting_entries.iterations == 1
        assert tuple(path for path, _ in context) == (
            "src/migrations/one.sql",
            "src/migrations/two.sql",
            "src/root.sql",
        )


def test_sql_context_invalidates_type_after_cross_file_column_drop_and_readd():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    table_source = "CREATE TABLE public.task (status text);\n"
    replacement_source = (
        "ALTER TABLE public.task DROP COLUMN status;\n"
        "ALTER TABLE public.task ADD COLUMN status public.task_status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, table_source, replacement_source)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_resolves_reverse_ordered_temp_relation_rename_graph_once(
    monkeypatch,
):
    relation_count = 48
    sources = [
        (
            f"rename-{index:03}.sql",
            "ALTER TABLE chain_{old} RENAME TO {new};\n".format(
                old=index,
                new=(f"chain_{index + 1}" if index < relation_count - 1 else "text"),
            ),
        )
        for index in reversed(range(relation_count))
    ]
    sources.append(("seed.sql", "CREATE TEMP TABLE chain_0 (value text);\n"))
    original = sql_adapter_module._collect_temporary_relation_facts
    scanned: list[bytes] = []

    def counted(
        source: bytes,
        masked: bytes,
        *,
        check_deadline=None,
        dollar_body_ranges=None,
    ):
        scanned.append(source)
        return original(
            source,
            masked,
            check_deadline=check_deadline,
            dollar_body_ranges=dollar_body_ranges,
        )

    monkeypatch.setattr(sql_adapter_module, "_collect_temporary_relation_facts", counted)
    context = SqlAdapter().prepare_mutation_context_with_sources_bounded(
        sources, remaining=lambda: 60.0
    )

    assert len(scanned) == len(sources)
    assert b"text" in context.temporary_relation_names

    check_source = (
        "CREATE SCHEMA tenant;\n"
        "SET search_path TO pg_catalog, tenant;\n"
        "CREATE TABLE tenant.task (status text "
        "CHECK (status IN ('open', 'closed')));\n"
    )
    assert SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({3}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    ) == ()


def test_sql_context_checks_lane_deadline_while_resolving_dependencies():
    moments = iter((0.0, 0.0, 0.0, 0.0, 2.0))
    deadline = runner.LaneDeadline(expires_at=1.0, monotonic=lambda: next(moments))

    with pytest.raises(AssayError) as caught:
        SqlAdapter().prepare_mutation_context_with_sources_bounded(
            (("seed.sql", "CREATE TEMP TABLE seed (value text);\n"),),
            remaining=deadline.remaining,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_sql_context_releases_each_non_target_lex_mask_before_the_next_source(
    monkeypatch,
):
    class TrackedMask(bytes):
        next_marker = 0
        live_markers: dict[int, int] = {}
        released_markers: set[int] = set()

        def __new__(cls, value):
            instance = super().__new__(cls, value)
            marker = cls.next_marker
            cls.next_marker += 1
            cls.live_markers[id(instance)] = marker
            return instance

        def __del__(self):
            marker = type(self).live_markers.pop(id(self), None)
            if marker is not None:
                type(self).released_markers.add(marker)

    original = sql_adapter_module.lex_sql
    markers: list[int] = []

    def tracked(source, *, check_deadline=None):
        if len(markers) == 2:
            assert markers[0] in TrackedMask.released_markers
        result = original(source, check_deadline=check_deadline)
        mask = TrackedMask(result.mask)
        markers.append(TrackedMask.next_marker - 1)
        return replace(result, mask=mask)

    monkeypatch.setattr(sql_adapter_module, "lex_sql", tracked)
    sources = (
        (f"context-{index}.sql", "SELECT 1;\n") for index in range(3)
    )
    deadline = runner.LaneDeadline(
        expires_at=60.0, monotonic=lambda: 0.0, honors_termination=False
    )

    SqlAdapter().prepare_mutation_context_with_sources_bounded(
        sources, remaining=deadline.remaining
    )

    assert len(markers) == 3
    assert markers[0] in TrackedMask.released_markers


def test_search_path_schema_reader_honors_the_lane_deadline(monkeypatch):
    source = 'SET search_path TO "' + ("schema" * 2000) + '";\n'
    original = sql_adapter_module._read_search_path_schemas
    entered_reader = False
    reader_checks = 0

    def observe_reader(
        source_arg, masked, value_start, *, check_deadline=None
    ):
        nonlocal entered_reader
        entered_reader = True
        assert check_deadline is not None

        def observe_deadline():
            nonlocal reader_checks
            reader_checks += 1
            return check_deadline()

        return original(
            source_arg,
            masked,
            value_start,
            check_deadline=observe_deadline,
        )

    monkeypatch.setattr(
        sql_adapter_module, "_read_search_path_schemas", observe_reader
    )
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 2.0 if entered_reader and reader_checks >= 4 else 0.0,
        honors_termination=False,
    )

    with pytest.raises(AssayError) as caught:
        SqlAdapter().prepare_mutation_context_with_sources_bounded(
            (("path.sql", source),), remaining=deadline.remaining
        )

    assert entered_reader
    assert reader_checks == 4
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_set_config_search_path_scan_honors_the_lane_deadline():
    source = b"SELECT set_config('" + b"x" * 8192 + b"', 'value', false);"
    checks = 0

    def check_deadline():
        nonlocal checks
        checks += 1
        if checks == 5:
            raise RuntimeError("deadline expired")
        return 1.0

    with pytest.raises(RuntimeError, match="deadline expired"):
        sql_adapter_module._set_config_search_path_writes(
            source, source, check_deadline=check_deadline
        )

    assert checks == 5


def test_set_config_deadline_checks_progress_when_escaped_quotes_advance_by_two():
    class TrackingBytes(bytes):
        def __new__(cls, value):
            instance = super().__new__(cls, value)
            instance.max_index = -1
            return instance

        def __getitem__(self, key):
            if isinstance(key, int):
                self.max_index = max(self.max_index, key)
            return super().__getitem__(key)

    source = TrackingBytes(
        b" " + b"set_config('" + b"''" * 4096 + b"', 'value', false);"
    )
    closing_quote_index = source.index(b"', 'value'")
    checks = 0

    def check_deadline():
        nonlocal checks
        checks += 1
        if checks == 5:
            assert source.max_index < closing_quote_index
            raise RuntimeError("deadline expired during escaped quote scan")
        return 1.0

    with pytest.raises(
        RuntimeError, match="deadline expired during escaped quote scan"
    ):
        sql_adapter_module._set_config_search_path_writes(
            source, source, check_deadline=check_deadline
        )

    assert checks == 5


def test_transaction_keyword_reads_receive_the_lane_deadline(monkeypatch):
    original = sql_adapter_module._next_sql_keyword
    deadline_check = lambda: 1.0
    callbacks = []

    def observe_keyword(source, masked, pos, *, check_deadline=None):
        callbacks.append(check_deadline)
        return original(
            source, masked, pos, check_deadline=check_deadline
        )

    monkeypatch.setattr(sql_adapter_module, "_next_sql_keyword", observe_keyword)
    source = b"BEGIN; COMMIT;"
    ranges = sql_adapter_module._explicit_transaction_ranges(
        source,
        source,
        dollar_body_ranges=(),
        check_deadline=deadline_check,
    )

    assert ranges == ((0, 6),)
    assert callbacks and all(callback is deadline_check for callback in callbacks)


@pytest.mark.parametrize(
    ("scanner", "source", "extra"),
    (
        (
            sql_adapter_module._scan_weaken_delete_action,
            b"REFERENCES item(id) ON DELETE RESTRICT;",
            (),
        ),
        (
            sql_adapter_module._scan_drop_trigger,
            b"CREATE TRIGGER audit AFTER INSERT ON item FOR EACH ROW EXECUTE FUNCTION audit_row();",
            ((),),
        ),
    ),
)
def test_remaining_sql_candidate_scanners_check_the_deadline(scanner, source, extra):
    def expired():
        raise RuntimeError("deadline expired")

    with pytest.raises(RuntimeError, match="deadline expired"):
        scanner(source, source, *extra, check_deadline=expired)


@pytest.mark.parametrize(
    "source",
    (b"/*" + b"x" * 8192 + b"*/", b"--" + b"x" * 8192),
)
def test_sql_trivia_scan_checks_deadline_inside_large_comment(source: bytes):
    moments = iter((0.0, 2.0))
    deadline = runner.LaneDeadline(expires_at=1.0, monotonic=lambda: next(moments))

    with pytest.raises(AssayError) as caught:
        sql_adapter_module._skip_sql_trivia(
            source, 0, check_deadline=deadline.remaining
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_sql_identifier_scan_checks_deadline_inside_long_identifier():
    source = b"a" * 8192
    moments = iter((0.0, 2.0))
    deadline = runner.LaneDeadline(expires_at=1.0, monotonic=lambda: next(moments))

    with pytest.raises(AssayError) as caught:
        sql_adapter_module._read_sql_identifier(
            source, 0, check_deadline=deadline.remaining
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT


def test_sql_context_checks_deadline_inside_temporary_view_comment():
    comment = "/*" + "x" * 8192 + "*/"
    source = (
        "CREATE TEMP VIEW pg_temp.derived AS "
        f"SELECT * FROM {comment} seed;"
    )
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        calls += 1
        return 2.0 if calls >= 15 else 0.0

    deadline = runner.LaneDeadline(expires_at=1.0, monotonic=monotonic)

    with pytest.raises(AssayError) as caught:
        SqlAdapter().prepare_mutation_context_with_sources_bounded(
            (("view.sql", source),), remaining=deadline.remaining
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert calls >= 15


def test_bounded_sql_context_reuses_lexed_ranges_for_statement_events(monkeypatch):
    source = (
        "BEGIN; SET LOCAL ROLE app_user; "
        "SET search_path TO pg_catalog, pg_temp; "
        "CREATE TYPE item_kind AS ENUM ('one'); COMMIT;"
    )
    original = sql_adapter_module.lex_sql
    callbacks = []

    def tracked(source_arg, *, check_deadline=None):
        callbacks.append(check_deadline)
        return original(source_arg, check_deadline=check_deadline)

    monkeypatch.setattr(sql_adapter_module, "lex_sql", tracked)
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 0.0,
        honors_termination=False,
    )

    SqlAdapter().prepare_mutation_context_with_sources_bounded(
        (("context.sql", source),), remaining=deadline.remaining
    )

    assert len(callbacks) == 1
    assert callbacks[0].__self__ is deadline
    assert callbacks[0].__func__ is runner.LaneDeadline.remaining


def test_sql_target_mask_is_not_retained_between_context_scan_and_generation(monkeypatch):
    source = "CREATE TABLE item (value integer CHECK (value > 0));\n"
    target = mutation.MutationTarget(
        path="src/item.sql",
        text=source,
        lines=frozenset({1}),
    )
    original = sql_adapter_module.lex_sql
    lexed_sources: list[bytes] = []

    def tracked(source_arg, *, check_deadline=None):
        lexed_sources.append(source_arg)
        return original(source_arg, check_deadline=check_deadline)

    monkeypatch.setattr(sql_adapter_module, "lex_sql", tracked)
    jobs = mutation.collect_mutation_sites(
        (target,),
        adapter=SqlAdapter(),
        operators=("sql:drop-check",),
        limit=10,
        context_sources=((target.path, source),),
        deadline_check=lambda: 1.0,
    )

    assert len(jobs) == 1
    assert lexed_sources.count(source.encode("utf-8")) == 2


def test_sql_whole_target_context_does_not_retain_masks_across_targets(monkeypatch):
    class TrackedMask(bytes):
        next_marker = 0
        live_markers: dict[int, int] = {}
        released_markers: set[int] = set()

        def __new__(cls, value):
            instance = super().__new__(cls, value)
            marker = cls.next_marker
            cls.next_marker += 1
            cls.live_markers[id(instance)] = marker
            return instance

        def __del__(self):
            marker = type(self).live_markers.pop(id(self), None)
            if marker is not None:
                type(self).released_markers.add(marker)

    sources = tuple(
        (
            f"src/{index}.sql",
            f"CREATE TABLE item_{index} (value integer CHECK (value > {index}));\n",
        )
        for index in range(3)
    )
    targets = tuple(
        mutation.MutationTarget(
            path=path,
            text=text,
            lines=frozenset({1}),
        )
        for path, text in sources
    )
    original = sql_adapter_module.lex_sql
    markers: list[int] = []

    def tracked(source, *, check_deadline=None):
        assert all(
            marker in TrackedMask.released_markers for marker in markers[:-1]
        )
        result = original(source, check_deadline=check_deadline)
        mask = TrackedMask(result.mask)
        markers.append(TrackedMask.next_marker - 1)
        return replace(result, mask=mask)

    monkeypatch.setattr(sql_adapter_module, "lex_sql", tracked)
    jobs = mutation.collect_mutation_sites(
        targets,
        adapter=SqlAdapter(),
        operators=("sql:drop-check",),
        limit=100,
        context_sources=sources,
        deadline_check=lambda: 1.0,
    )

    assert len(jobs) == 3
    assert len(markers) == 6


def test_sql_target_ignores_conflicting_context_text_and_relexes_target(monkeypatch):
    context_source = "CREATE TABLE item (value integer CHECK (value < 0));\n"
    target_source = "CREATE TABLE item (value integer CHECK (value > 0));\n"
    target = mutation.MutationTarget(
        path="src/item.sql",
        text=target_source,
        lines=frozenset({1}),
    )
    original = sql_adapter_module.lex_sql
    lexed_sources: list[bytes] = []

    def tracked(source_arg, *, check_deadline=None):
        lexed_sources.append(source_arg)
        return original(source_arg, check_deadline=check_deadline)

    monkeypatch.setattr(sql_adapter_module, "lex_sql", tracked)
    jobs = mutation.collect_mutation_sites(
        (target,),
        adapter=SqlAdapter(),
        operators=("sql:drop-check",),
        limit=10,
        context_sources=((target.path, context_source),),
        deadline_check=lambda: 1.0,
    )

    assert len(jobs) == 1
    assert lexed_sources.count(context_source.encode("utf-8")) == 0
    assert lexed_sources.count(target_source.encode("utf-8")) == 2


def test_sql_context_table_column_matcher_honors_lane_deadline(monkeypatch):
    source = "CREATE TABLE item (" + ("value integer, " * 3000)
    source_bytes = source.encode()
    table_open = source_bytes.index(b"(")
    matcher_started = False
    original_matcher = sql_adapter_module._find_matching_close_paren

    def observe_matcher(masked, open_index, *, check_deadline=None):
        nonlocal matcher_started
        if check_deadline is not None and open_index == table_open:
            matcher_started = True
        return original_matcher(
            masked, open_index, check_deadline=check_deadline
        )

    monkeypatch.setattr(
        sql_adapter_module, "_find_matching_close_paren", observe_matcher
    )
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 2.0 if matcher_started else 0.0,
        honors_termination=False,
    )

    with pytest.raises(AssayError) as caught:
        SqlAdapter().prepare_mutation_context_with_sources_bounded(
            (("table.sql", source),), remaining=deadline.remaining
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert matcher_started


def test_unique_target_parenthesis_matcher_honors_lane_deadline(monkeypatch):
    source = "CREATE TABLE item (value integer, UNIQUE (" + (
        "value, " * 3000
    )
    source_bytes = source.encode()
    unique_open = source_bytes.index(b"(", source_bytes.index(b"UNIQUE"))
    matcher_started = False
    original_matcher = sql_adapter_module._find_matching_close_paren

    def observe_matcher(masked, open_index, *, check_deadline=None):
        nonlocal matcher_started
        if check_deadline is not None and open_index == unique_open:
            matcher_started = True
        return original_matcher(
            masked, open_index, check_deadline=check_deadline
        )

    monkeypatch.setattr(
        sql_adapter_module, "_scan_drop_check", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        sql_adapter_module, "_find_matching_close_paren", observe_matcher
    )
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 2.0 if matcher_started else 0.0,
        honors_termination=False,
    )

    with pytest.raises(AssayError) as caught:
        SqlAdapter().generate_mutation_sites_with_context_for_source_bounded(
            source,
            {1},
            operators=("sql:drop-unique",),
            limit=10,
            context=None,
            source_identity="schema.sql",
            remaining=deadline.remaining,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert matcher_started


def test_foreign_key_target_parenthesis_matcher_honors_lane_deadline(monkeypatch):
    source = "ALTER TABLE item ADD CONSTRAINT fk FOREIGN KEY (" + (
        "value, " * 3000
    )
    source_bytes = source.encode()
    foreign_key_open = source_bytes.index(b"(", source_bytes.index(b"FOREIGN KEY"))
    matcher_started = False
    original_matcher = sql_adapter_module._find_matching_close_paren

    def observe_matcher(masked, open_index, *, check_deadline=None):
        nonlocal matcher_started
        if check_deadline is not None and open_index == foreign_key_open:
            matcher_started = True
        return original_matcher(
            masked, open_index, check_deadline=check_deadline
        )

    monkeypatch.setattr(
        sql_adapter_module, "_find_matching_close_paren", observe_matcher
    )
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 2.0 if matcher_started else 0.0,
        honors_termination=False,
    )

    with pytest.raises(AssayError) as caught:
        SqlAdapter().generate_mutation_sites_with_context_for_source_bounded(
            source,
            {1},
            operators=("sql:drop-foreign-key",),
            limit=10,
            context=None,
            source_identity="schema.sql",
            remaining=deadline.remaining,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert matcher_started


def test_not_null_target_action_split_honors_lane_deadline(monkeypatch):
    source = "ALTER TABLE item " + ("DROP COLUMN old, " * 3000) + (
        "ALTER COLUMN value SET NOT NULL;"
    )
    split_started = False
    split_returned = False
    split_checks = 0
    original_split = sql_adapter_module._split_top_level

    def observe_split(region, *, check_deadline=None):
        nonlocal split_started, split_returned
        if region.lstrip().startswith(b"DROP COLUMN old, "):
            split_started = True
        result = original_split(region, check_deadline=check_deadline)
        if region.lstrip().startswith(b"DROP COLUMN old, "):
            split_returned = True
        return result

    def monotonic() -> float:
        nonlocal split_checks
        if split_started:
            split_checks += 1
            return 2.0 if split_checks >= 2 else 0.0
        return 0.0

    monkeypatch.setattr(sql_adapter_module, "_split_top_level", observe_split)
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=monotonic,
        honors_termination=False,
    )

    with pytest.raises(AssayError) as caught:
        SqlAdapter().generate_mutation_sites_with_context_for_source_bounded(
            source,
            {1},
            operators=("sql:drop-not-null",),
            limit=10,
            context=None,
            source_identity="schema.sql",
            remaining=deadline.remaining,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert split_started
    assert not split_returned
    assert split_checks >= 2


def test_widen_check_in_list_split_honors_lane_deadline(monkeypatch):
    source = "CREATE TABLE item (value integer CHECK (value IN (" + (
        "1, " * 3000
    ) + "2)));"
    split_started = False
    split_returned = False
    split_checks = 0
    original_split = sql_adapter_module._split_top_level

    def observe_split(region, *, check_deadline=None):
        nonlocal split_started, split_returned
        is_member_list = region.startswith(b"1, 1, ")
        if is_member_list:
            split_started = True
        result = original_split(region, check_deadline=check_deadline)
        if is_member_list:
            split_returned = True
        return result

    def monotonic() -> float:
        nonlocal split_checks
        if split_started:
            split_checks += 1
            return 2.0 if split_checks >= 2 else 0.0
        return 0.0

    monkeypatch.setattr(
        sql_adapter_module, "_scan_drop_check", lambda *_a, **_k: []
    )
    monkeypatch.setattr(sql_adapter_module, "_split_top_level", observe_split)
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=monotonic,
        honors_termination=False,
    )

    with pytest.raises(AssayError) as caught:
        SqlAdapter().generate_mutation_sites_with_context_for_source_bounded(
            source,
            {1},
            operators=("sql:widen-check-in",),
            limit=10,
            context=None,
            source_identity="schema.sql",
            remaining=deadline.remaining,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert split_started
    assert not split_returned
    assert split_checks >= 2


def test_widen_check_parenthesis_scan_honors_lane_deadline(monkeypatch):
    source = "CREATE TABLE item (value integer CHECK (value IN (" + (
        "1, " * 3000
    ) + "2)));"
    source_bytes = source.encode()
    check_open = source_bytes.index(b"CHECK (") + len(b"CHECK ")
    matcher_started = False
    original_matcher = sql_adapter_module._find_matching_close_paren

    def observe_matcher(masked, open_index, *, check_deadline=None):
        nonlocal matcher_started
        if check_deadline is not None and open_index == check_open:
            matcher_started = True
        return original_matcher(
            masked, open_index, check_deadline=check_deadline
        )

    monkeypatch.setattr(sql_adapter_module, "_scan_drop_check", lambda *_a, **_k: [])
    monkeypatch.setattr(
        sql_adapter_module, "_find_matching_close_paren", observe_matcher
    )
    deadline = runner.LaneDeadline(
        expires_at=1.0,
        monotonic=lambda: 2.0 if matcher_started else 0.0,
        honors_termination=False,
    )

    with pytest.raises(AssayError) as caught:
        SqlAdapter().generate_mutation_sites_with_context_for_source_bounded(
            source,
            {1},
            operators=("sql:widen-check-in",),
            limit=10,
            context=None,
            source_identity="schema.sql",
            remaining=deadline.remaining,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert matcher_started


def test_sql_context_checks_deadline_inside_temporary_table_comment(monkeypatch):
    comment = "/*" + "x" * 8192 + "*/"
    source = f"CREATE TEMP TABLE {comment} seed (value integer);"
    source_bytes = source.encode()
    table_name_start = source_bytes.index(b"TABLE") + len(b"TABLE")
    original = sql_adapter_module._read_create_table_identity
    table_deadline_seen = False
    table_reader_returned = False
    table_reader_started = False
    table_checks = 0
    calls = 0

    def tracked(source_arg, masked, pos, *, check_deadline=None):
        nonlocal table_deadline_seen, table_reader_returned, table_reader_started
        if source_arg == source_bytes and pos == table_name_start:
            table_deadline_seen = check_deadline is not None
            table_reader_started = True
        result = original(
            source_arg, masked, pos, check_deadline=check_deadline
        )
        if source_arg == source_bytes and pos == table_name_start:
            table_reader_returned = True
        return result

    def monotonic() -> float:
        nonlocal calls, table_checks
        calls += 1
        if not table_reader_started:
            return 0.0
        table_checks += 1
        return 2.0 if table_checks >= 2 else 0.0

    monkeypatch.setattr(
        sql_adapter_module, "_read_create_table_identity", tracked
    )
    deadline = runner.LaneDeadline(expires_at=1.0, monotonic=monotonic)

    with pytest.raises(AssayError) as caught:
        SqlAdapter().prepare_mutation_context_with_sources_bounded(
            (("table.sql", source),), remaining=deadline.remaining
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert table_deadline_seen
    assert not table_reader_returned
    assert table_checks == 2


def test_sql_context_checks_deadline_inside_long_view_alias(monkeypatch):
    alias = "a" * 8192
    source = (
        "CREATE TEMP VIEW pg_temp.derived AS "
        f"SELECT * FROM seed AS {alias};"
    )
    source_bytes = source.encode()
    alias_start = source.index(alias)
    original = sql_adapter_module._read_sql_identifier
    alias_deadline_seen = False
    alias_reader_returned = False
    alias_started = False
    alias_checks = 0

    def tracked(source_arg, pos, *, check_deadline=None):
        nonlocal alias_deadline_seen, alias_reader_returned, alias_started
        tracks_alias = source_arg == source_bytes and pos == alias_start
        if tracks_alias:
            alias_deadline_seen = check_deadline is not None
            alias_started = True
        result = original(source_arg, pos, check_deadline=check_deadline)
        if tracks_alias:
            alias_reader_returned = True
        return result

    def monotonic() -> float:
        nonlocal alias_checks
        if not alias_started:
            return 0.0
        alias_checks += 1
        return 2.0 if alias_checks >= 2 else 0.0

    monkeypatch.setattr(sql_adapter_module, "_read_sql_identifier", tracked)
    deadline = runner.LaneDeadline(expires_at=1.0, monotonic=monotonic)

    with pytest.raises(AssayError) as caught:
        SqlAdapter().prepare_mutation_context_with_sources_bounded(
            (("view.sql", source),), remaining=deadline.remaining
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert alias_deadline_seen
    assert not alias_reader_returned
    assert alias_checks == 2


def test_sql_context_checks_deadline_between_temporary_view_join_segments():
    joins = " ".join(
        f"JOIN relation_{index} ON true" for index in range(64)
    )
    source = f"CREATE TEMP VIEW pg_temp.derived AS SELECT * FROM seed {joins};"
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        calls += 1
        return 2.0 if calls >= 80 else 0.0

    deadline = runner.LaneDeadline(expires_at=1.0, monotonic=monotonic)

    with pytest.raises(AssayError) as caught:
        SqlAdapter().prepare_mutation_context_with_sources_bounded(
            (("view.sql", source),), remaining=deadline.remaining
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert calls >= 80


def test_sql_view_relation_after_prejoin_alias_still_reads_comma_relation():
    source = (
        "CREATE TEMP TABLE temp_table (value integer);\n"
        "CREATE VIEW public.derived AS "
        "SELECT * FROM seed AS prejoin, pg_temp.temp_table "
        "JOIN other ON true;\n"
    )

    context = SqlAdapter().prepare_mutation_context((source,))

    assert b"derived" in context.temporary_relation_names


def test_sql_context_resolves_reverse_ordered_temp_type_rename_graph_once(
    monkeypatch,
):
    type_count = 48
    sources = [
        (
            f"rename-{index:03}.sql",
            "ALTER TYPE pg_temp.kind_{old} RENAME TO {new};\n".format(
                old=index,
                new=(f"kind_{index + 1}" if index < type_count - 1 else "text"),
            ),
        )
        for index in reversed(range(type_count))
    ]
    sources.append(
        ("seed.sql", "CREATE TYPE pg_temp.kind_0 AS ENUM ('open', 'closed');\n")
    )
    original = sql_adapter_module._collect_declared_type_facts
    scanned: list[bytes] = []

    def counted(
        source: bytes,
        masked: bytes,
        *,
        dollar_body_ranges=None,
        check_deadline=None,
    ):
        scanned.append(source)
        return original(
            source,
            masked,
            dollar_body_ranges=dollar_body_ranges,
            check_deadline=check_deadline,
        )

    monkeypatch.setattr(sql_adapter_module, "_collect_declared_type_facts", counted)
    context = SqlAdapter().prepare_mutation_context_with_sources_bounded(
        sources, remaining=lambda: 60.0
    )

    assert len(scanned) == len(sources)
    assert (b"pg_temp", b"text") in context.declared_type_names


def test_sql_context_invalidates_column_types_after_cross_file_table_drop():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    original_table = "CREATE TABLE public.task (status text);\n"
    drop_table = "DROP TABLE IF EXISTS public.task CASCADE;\n"
    recreate_without_declared_columns = (
        "CREATE TABLE public.task AS "
        "SELECT 'open'::public.task_status AS status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, original_table, drop_table, recreate_without_declared_columns)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_create_table_as_does_not_borrow_a_later_table_type():
    context_source = (
        "CREATE TYPE mood AS ENUM ('open', 'closed');\n"
        "CREATE TABLE t AS SELECT 'open'::mood AS state;\n"
        "CREATE TABLE decoy (state text);\n"
    )
    check_source = "ALTER TABLE t ADD CHECK (state IN ('open', 'closed'));\n"
    context = SqlAdapter().prepare_mutation_context((context_source,))

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_does_not_trust_if_not_exists_column_types():
    context_source = (
        "CREATE TYPE mood AS ENUM ('open', 'closed');\n"
        "CREATE TABLE t AS SELECT 'open'::mood AS state;\n"
        "CREATE TABLE IF NOT EXISTS t (state text);\n"
    )
    check_source = "ALTER TABLE t ADD CHECK (state IN ('open', 'closed'));\n"
    context = SqlAdapter().prepare_mutation_context((context_source,))

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_does_not_apply_unqualified_column_types_to_qualified_table():
    context_source = (
        "CREATE SCHEMA archive;\n"
        "SET search_path TO archive;\n"
        "CREATE TABLE task (state text);\n"
        "CREATE TABLE public.task AS SELECT 'open'::mood AS state;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (state IN ('open', 'closed'));\n"
    )
    context = SqlAdapter().prepare_mutation_context(
        ("CREATE TYPE mood AS ENUM ('open', 'closed');\n", context_source)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_does_not_invalidate_a_qualified_other_schema_table():
    table_source = "CREATE TABLE public.task (status text);\n"
    unrelated_drop = "DROP TABLE archive.task;\n"
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    context = SqlAdapter().prepare_mutation_context((table_source, unrelated_drop))

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(sites) == 1


def test_sql_context_preserves_bounded_varchar_typmod_across_files():
    table_source = "CREATE TABLE public.task (status varchar(5));\n"
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'close'));\n"
    )
    context = SqlAdapter().prepare_mutation_context((table_source,))

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_refuses_unqualified_type_when_a_temp_table_can_shadow_it():
    permanent_table = "CREATE TABLE public.task (status text);\n"
    temporary_table = 'CREATE TEMP TABLE "task" (status varchar(5));\n'
    check_source = (
        'ALTER TABLE "task" ADD CHECK (status IN (\'open\', \'closed\'));\n'
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert sites == ()


def test_sql_context_refuses_type_facts_shadowed_by_select_into_temp():
    permanent_table = "CREATE TABLE public.task (status text);\n"
    temporary_table = (
        "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
        "SELECT 'open'::public.task_status AS status INTO TEMP TABLE task;\n"
    )
    check_source = (
        "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert baseline_sites == ()
    assert sites == ()


def test_sql_context_refuses_type_facts_shadowed_by_select_into_pg_temp():
    permanent_table = "CREATE TABLE public.task (status text);\n"
    temporary_table = (
        "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
        "SELECT 'open'::public.task_status AS status INTO pg_temp.task;\n"
    )
    check_source = "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert baseline_sites == ()
    assert sites == ()


def test_sql_context_refuses_permanent_type_for_pg_temp_qualified_reference():
    permanent_table = "CREATE TABLE task (status text);\n"
    unqualified_check = (
        "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    explicit_temp_check = (
        "ALTER TABLE pg_temp.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        unqualified_check,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    temp_schema_sites = SqlAdapter().generate_mutation_sites_with_context(
        explicit_temp_check,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )

    assert baseline_sites == ()
    assert temp_schema_sites == ()


def test_sql_context_recognizes_tables_created_in_pg_temp_schema():
    permanent_table = "CREATE TABLE task (status text);\n"
    temporary_table = (
        "CREATE TYPE task_status AS ENUM ('open', 'closed');\n"
        "CREATE TABLE pg_temp.task AS SELECT 'open'::task_status AS status;\n"
    )
    check_source = "ALTER TABLE task ADD CHECK (status IN ('open', 'closed'));\n"
    baseline = SqlAdapter().prepare_mutation_context((permanent_table,))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (permanent_table, temporary_table)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert baseline_sites == ()
    assert sites == ()


def test_sql_context_invalidates_type_facts_for_both_sides_of_table_rename():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    original_table = "CREATE TABLE public.task (status text);\n"
    rename_and_recreate = (
        "ALTER TABLE public.task RENAME TO old_task;\n"
        "CREATE TABLE public.task AS "
        "SELECT 'open'::public.task_status AS status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((enum_source, original_table))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, original_table, rename_and_recreate)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(baseline_sites) == 1
    assert sites == ()


def test_sql_context_invalidates_type_facts_after_table_schema_move():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    original_table = "CREATE TABLE public.task (status text);\n"
    move_and_recreate = (
        "CREATE SCHEMA archive;\n"
        "ALTER TABLE public.task SET SCHEMA archive;\n"
        "CREATE TABLE public.task AS "
        "SELECT 'open'::public.task_status AS status;\n"
    )
    check_source = (
        "ALTER TABLE public.task ADD CHECK (status IN ('open', 'closed'));\n"
    )
    baseline = SqlAdapter().prepare_mutation_context((enum_source, original_table))
    baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=baseline,
    )
    context = SqlAdapter().prepare_mutation_context(
        (enum_source, original_table, move_and_recreate)
    )

    sites = SqlAdapter().generate_mutation_sites_with_context(
        check_source,
        frozenset({1}),
        operators=("sql:widen-check-in",),
        limit=1000,
        context=context,
    )

    assert len(baseline_sites) == 1
    assert sites == ()


def test_sql_context_invalidates_type_after_quoted_cross_file_alter_identifiers():
    enum_source = "CREATE TYPE public.task_status AS ENUM ('open', 'closed');\n"
    cases = (
        ("status", ("status",), 'DROP COLUMN "status"'),
        (
            "status",
            ("status",),
            'ADD COLUMN IF NOT EXISTS "status" public.task_status',
        ),
        (
            "status_type",
            ("status_type",),
            'ALTER COLUMN "status_type" TYPE public.task_status',
        ),
        (
            "status_set",
            ("status_set",),
            'ALTER COLUMN "status_set" SET DATA TYPE public.task_status',
        ),
        (
            "status_before_rename",
            ("status_before_rename", "status_after_rename"),
            'RENAME COLUMN "status_before_rename" TO "status_after_rename"',
        ),
        (
            "status_after_rename",
            ("status_before_rename", "status_after_rename"),
            'RENAME COLUMN "status_before_rename" TO "status_after_rename"',
        ),
        (
            "status_star",
            ("status_star",),
            '* ALTER COLUMN "status_star" TYPE public.task_status',
        ),
    )

    for column, declared_columns, action in cases:
        # Each action gets a fresh text-only baseline. Its check has a real
        # widening site until that specific action's identifiers are parsed.
        table_source = (
            "CREATE TABLE public.task ("
            + ", ".join(f"{name} text" for name in declared_columns)
            + ");\n"
        )
        check_source = (
            "ALTER TABLE public.task ADD CHECK ("
            f"{column} IN ('open', 'closed'));\n"
        )
        baseline = SqlAdapter().prepare_mutation_context((enum_source, table_source))
        baseline_sites = SqlAdapter().generate_mutation_sites_with_context(
            check_source,
            frozenset({1}),
            operators=("sql:widen-check-in",),
            limit=1000,
            context=baseline,
        )
        assert len(baseline_sites) == 1

        altered = SqlAdapter().prepare_mutation_context(
            (
                enum_source,
                table_source,
                f'ALTER TABLE public."task" {action};\n',
            )
        )
        altered_sites = SqlAdapter().generate_mutation_sites_with_context(
            check_source,
            frozenset({1}),
            operators=("sql:widen-check-in",),
            limit=1000,
            context=altered,
        )

        assert altered_sites == ()
