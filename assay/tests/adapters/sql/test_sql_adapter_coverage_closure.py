"""Coverage closure for ``assay.adapters.sql`` / ``sql_lex``: each test drives
one guarded branch with a hand-built input and asserts the returned value (or
the deadline exception propagating) the branch produces."""

from __future__ import annotations

import re

import pytest

from assay.adapters import sql as S
from assay.adapters import sql_lex as L


class Boom(Exception):
    pass


def _boom():
    raise Boom


def _mk(text: str):
    src = text.encode()
    try:
        return src, L.lex_sql(src).mask
    except Exception:  # unterminated trivia: lexer refuses, helper under test must cope
        return src, src


# --------------------------------------------------------------- sql_lex


def test_lex_matches_at_and_find_next_edges():
    ok = lambda: None
    assert L._matches_at(b"ab", b"abc", 0, check_deadline=ok) is False
    assert L._matches_at(b"abd", b"abc", 0, check_deadline=ok) is False
    assert L._matches_at(b"abc", b"abc", 0, check_deadline=ok) is True
    assert L._find_next(b"abc", b"", 2, check_deadline=ok) == 2
    # first byte matches but the full needle does not, then absent entirely
    assert L._find_next(b"xaybz", b"ab", 0, check_deadline=ok) == -1
    assert L._find_next(b"xaabz", b"ab", 0, check_deadline=ok) == 2


def test_lex_dollar_tag_deadline_checked_on_long_tag():
    tag = b"$" + b"a" * 5000 + b"$ x $" + b"a" * 5000 + b"$"
    calls = []
    L._lex_once(tag, check_deadline=lambda: calls.append(1))
    assert len(calls) >= 2
    with pytest.raises(Boom):
        L._lex_once(b"$" + b"a" * 9000, check_deadline=_boom)


# ------------------------------------------------- references / unique helpers


def test_flat_close_paren_deadline_nested_and_unclosed():
    big = b"(" + b"a" * 5000 + b")"
    calls = []
    assert S._find_flat_close_paren(big, 0, check_deadline=lambda: calls.append(1)) == 5001
    assert len(calls) == 2
    with pytest.raises(Boom):
        S._find_flat_close_paren(b"(" + b"a" * 5000, 0, check_deadline=_boom)
    assert S._find_flat_close_paren(b"(a(b)", 0) is None
    assert S._find_flat_close_paren(b"(abc", 0) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("x", None),
        ("ON", None),
        ("ON(", None),
        ("ON FOO", None),
        ("ON DELETE", None),
        ("ON DELETE RESTRICT", 18),
        ("ON UPDATE CASCADE", 17),
        ("ON DELETE NO ACTION", 19),
        ("ON DELETE NO FOO", None),
        ("ON DELETE SET", None),
        ("ON DELETE SET FOO", None),
        ("ON DELETE SET NULL", 18),
        ("ON DELETE SET NULL (a, b)", 25),
        ("ON DELETE SET NULL (a, (b)", None),
        ("ON DELETE SET DEFAULT", 21),
    ],
)
def test_reference_action_end(text, expected):
    assert S._reference_action_end(text.encode(), 0) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("MATCH", None),
        ("MATCH  BOGUS", None),
        ("MATCH SIMPLE", 12),
        ("NOT DEFERRABLE", 14),
        ("NOT VALID", 9),
        ("NOT FOO", None),
        ("DEFERRABLE", 10),
        ("INITIALLY DEFERRED", 18),
        ("INITIALLY IMMEDIATE", 19),
        ("INITIALLY FOO", None),
        ("FOO", None),
    ],
)
def test_reference_modifier_end(text, expected):
    assert S._reference_modifier_end(text.encode(), 0) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("x", None),
        ("REFERENCES", None),
        ("REFERENCES (a)", None),
        ("REFERENCES t (a(", None),
        ("REFERENCES t", 12),
        ("REFERENCES t (a) ON DELETE CASCADE NOT VALID", 44),
        ("REFERENCES t NOT DEFERRABLE ON UPDATE RESTRICT", 46),
    ],
)
def test_extend_references_clause(text, expected):
    assert S._extend_references_clause(text.encode(), 0) == expected


def test_extend_references_clause_deadline():
    with pytest.raises(Boom):
        S._extend_references_clause(b"REFERENCES t", 0, check_deadline=_boom)


def test_keyword_after_whitespace_requires_whitespace():
    assert S._sql_keyword_after_whitespace(b"NOTDEFERRABLE", 3, b"DEFERRABLE") is None
    assert S._sql_keyword_after_whitespace(b"NOT  DEFERRABLE", 3, b"DEFERRABLE") == 15


@pytest.mark.parametrize(
    "text,expected",
    [
        ("NOT DEFERRABLE", 14),
        ("DEFERRABLE", 10),
        ("INITIALLY IMMEDIATE", 19),
        ("INITIALLY DEFERRED", 18),
        ("INITIALLY", None),
        ("NOT FOO", None),
        ("FOO", None),
    ],
)
def test_unique_deferrability_end(text, expected):
    assert S._unique_deferrability_end(text.encode(), 0) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("FOO", None),
        ("USING FOO", None),
        ("USING INDEX", None),
        ("USING INDEX TABLESPACE", None),
        ("USING INDEX TABLESPACE ts", 25),
        ("USING INDEX TABLESPACE 1", None),
        ("USING INDEX idx", 15),
        ("USING INDEX s.idx", 17),
        ("USING INDEX 1", None),
    ],
)
def test_unique_using_index_end(text, expected):
    src, masked = _mk(text)
    assert S._unique_using_index_end(src, masked, 0) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("FOO", None),
        (" USING FOO", None),
        (" USING INDEX", 12),
        (" USING INDEX TABLESPACE ts", None),
    ],
)
def test_using_index_attachment_end(text, expected):
    assert S._using_index_attachment_end(text.encode(), 0) == expected
    if text.startswith(" "):
        with pytest.raises(Boom):
            S._using_index_attachment_end(text.encode(), 0, check_deadline=_boom)


def test_iter_index_attachment_spans_primary_without_key_skipped():
    assert list(S._iter_index_attachment_spans(b"PRIMARY FOO USING INDEX i")) == []
    assert list(S._iter_index_attachment_spans(b"PRIMARY KEY USING INDEX i")) == [(0, 23)]
    with pytest.raises(Boom):
        list(S._iter_index_attachment_spans(b"UNIQUE USING INDEX", check_deadline=_boom))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("UNIQUE NULLS DISTINCT (a)", 25),
        ("UNIQUE NULLS NOT DISTINCT (a)", 29),
        ("UNIQUE NULLS NOT FOO (a)", 6),
        ("UNIQUE NULLS  FOO (a)", 6),
        ("UNIQUE NULLS(a)", 6),
        ("UNIQUE NULLS NOT(a)", 6),
        ("UNIQUE (a", None),
        ("UNIQUE (a) INCLUDE", None),
        ("UNIQUE (a) INCLUDE (b", None),
        ("UNIQUE (a) INCLUDE (b)", 22),
        ("UNIQUE (a) WITH", None),
        ("UNIQUE (a) WITH x", None),
        ("UNIQUE (a) WITH (x", None),
        ("UNIQUE (a) WITH (fillfactor=70)", 31),
    ],
)
def test_unique_constraint_end(text, expected):
    src, masked = _mk(text)
    assert S._unique_constraint_end(src, masked, 6) == expected


def test_keyword_end_refuses_longer_word():
    assert S._sql_keyword_end(b"DEFERRABLEX", 0, b"DEFERRABLE") is None
    assert S._sql_keyword_end(b"deferrable;", 0, b"DEFERRABLE") == 10


# ------------------------------------------------------- trivia / identifiers


def _counter():
    calls = []
    return calls, lambda: calls.append(1)


def test_skip_trivia_variants():
    assert S._skip_sql_trivia(b"-- x", 0) == 4
    assert S._skip_sql_trivia(b"-- x\ny", 0) == 5
    calls, cb = _counter()
    assert S._skip_sql_trivia(b" " * 5000 + b"x", 0, check_deadline=cb) == 5000
    assert len(calls) >= 2
    calls, cb = _counter()
    assert S._skip_sql_trivia(b"--" + b"c" * 5000 + b"\nx", 0, check_deadline=cb) == 5003
    assert len(calls) >= 2
    assert S._skip_sql_trivia(b"-- c", 0, check_deadline=lambda: None) == 4
    calls, cb = _counter()
    assert S._skip_sql_trivia(b"/*" + b"c" * 5000 + b"*/x", 0, check_deadline=cb) == 5004
    assert len(calls) >= 2
    assert S._skip_sql_trivia(b"/* /* */ */x", 0) == 11
    assert S._skip_sql_trivia(b"/* x", 0) is None


def test_read_sql_identifier_variants():
    assert S._read_sql_identifier(b'"a""b" x', 0) == (b'"a""b"', 6)
    assert S._read_sql_identifier(b'"abc', 0) is None
    assert S._read_sql_identifier(b"/* x", 0) is None
    assert S._read_sql_identifier(b"", 0) is None
    assert S._read_sql_identifier(b"1a", 0) is None
    ok = lambda: None
    assert S._read_sql_identifier(b"1a", 0, check_deadline=ok) is None
    assert S._read_sql_identifier(b"ab_$1 x", 0, check_deadline=ok) == (b"ab_$1", 5)
    assert S._read_sql_identifier(b'"a""b"', 0, check_deadline=ok) == (b'"a""b"', 6)
    assert S._read_sql_identifier(b'"abc', 0, check_deadline=ok) is None
    calls, cb = _counter()
    assert S._read_sql_identifier(b"a" * 5000, 0, check_deadline=cb) == (b"a" * 5000, 5000)
    assert len(calls) >= 2
    calls, cb = _counter()
    long_q = b'"' + b"a" * 5000 + b'"'
    assert S._read_sql_identifier(long_q, 0, check_deadline=cb) == (long_q, 5002)
    assert len(calls) >= 2


def test_read_single_quoted_string_variants():
    assert S._read_sql_single_quoted_string(b"x", 0) is None
    assert S._read_sql_single_quoted_string(b"'a''b' z", 0) == (b"a'b", 6)
    assert S._read_sql_single_quoted_string(b"'abc", 0) is None
    calls, cb = _counter()
    body = b"a" * 5000
    assert S._read_sql_single_quoted_string(b"'" + body + b"'", 0, check_deadline=cb) == (body, 5002)
    assert len(calls) >= 2


def test_set_schema_value_variants():
    src, masked = _mk("SET SCHEMA 'a'")
    assert S._set_schema_value(src, masked, 11) == (True, b"a")
    assert S._set_schema_value(src, masked, 11, check_deadline=lambda: None) == (True, b"a")
    with pytest.raises(Boom):
        S._set_schema_value(src, masked, 11, check_deadline=_boom)
    src, masked = _mk("SET SCHEMA 'a' x;")
    assert S._set_schema_value(src, masked, 11) == (True, None)
    src, masked = _mk("SET SCHEMA bare")
    assert S._set_schema_value(src, masked, 11) == (True, None)
    src, masked = _mk("SET other 'a'")
    assert S._set_schema_value(src, masked, 10) == (False, None)
    assert S._set_schema_value(src, masked, None) == (False, None)
    src, masked = _mk("SET SCHEMA 'a' /* open")
    assert S._set_schema_value(src, masked, 11) == (True, None)


def test_read_search_path_schemas_variants():
    def sp(text, **kw):
        src, masked = _mk(text)
        return S._read_search_path_schemas(src, masked, text.index("=") + 1, **kw)

    assert sp("SET search_path = a, B") == (b"a", b"b")
    assert sp("SET search_path = $user, public;") == (b"$user", b"public")
    assert sp("SET search_path = ;") is None
    assert sp("SET search_path = 1") is None
    assert sp("SET search_path = a b") is None
    assert sp("SET search_path = a /* open") == (b"a",)
    assert sp("SET search_path = a, ;") == (b"a",)
    assert S._read_search_path_schemas(b"", b"", None) is None


def test_search_path_may_create_in_temp_schema():
    known = frozenset({b"app"})
    assert S._search_path_schemas_may_create_in_temp_schema(None, known) is True
    assert S._search_path_schemas_may_create_in_temp_schema((b"pg_temp", b"app"), known) is True
    assert S._search_path_schemas_may_create_in_temp_schema((b"app", b"pg_temp"), known) is False
    assert S._search_path_schemas_may_create_in_temp_schema((b"other",), known) is False


# ------------------------------------------------------- schema statements

_CREATE_SCHEMA = re.compile(rb"CREATE\s+SCHEMA", re.I)
_DROP_SCHEMA = re.compile(rb"DROP\s+SCHEMA", re.I)
_ALTER_SCHEMA = re.compile(rb"ALTER\s+SCHEMA", re.I)


def _stmt(rx, text):
    src, masked = _mk(text)
    return src, masked, rx.match(src)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CREATE SCHEMA S", b"s"),
        ("CREATE SCHEMA IF x", None),
        ("CREATE SCHEMA IF NOT x", None),
        ("CREATE SCHEMA IF NOT EXISTS s", None),
        ("CREATE SCHEMA ;", None),
        ("CREATE SCHEMA AUTHORIZATION bob", None),
        ("CREATE SCHEMA s AUTHORIZATION bob", None),
        ('CREATE SCHEMA "Authorization"', b"Authorization"),
    ],
)
def test_schema_name_created_by_statement(text, expected):
    src, masked, m = _stmt(_CREATE_SCHEMA, text)
    assert S._schema_name_created_by_statement(src, masked, m) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("DROP SCHEMA a", (b"a",)),
        ("DROP SCHEMA IF x", None),
        ("DROP SCHEMA IF EXISTS a, B", (b"a", b"b")),
        ("DROP SCHEMA 1", None),
        ("DROP SCHEMA a,", None),
        ("DROP SCHEMA a /* open", (b"a",)),
    ],
)
def test_schema_names_dropped_by_statement(text, expected):
    src, masked, m = _stmt(_DROP_SCHEMA, text)
    assert S._schema_names_dropped_by_statement(src, masked, m) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ALTER SCHEMA A RENAME TO B", (b"a", b"b")),
        ("ALTER SCHEMA 1", None),
        ("ALTER SCHEMA a OWNER TO b", None),
        ("ALTER SCHEMA a RENAME x", None),
        ("ALTER SCHEMA a RENAME TO 1", None),
    ],
)
def test_schema_renamed_by_statement(text, expected):
    src, masked, m = _stmt(_ALTER_SCHEMA, text)
    assert S._schema_renamed_by_statement(src, masked, m) == expected


def test_schema_altered_by_statement():
    src, masked, m = _stmt(_ALTER_SCHEMA, "ALTER SCHEMA A")
    assert S._schema_altered_by_statement(src, masked, m) == b"a"
    src, masked, m = _stmt(_ALTER_SCHEMA, "ALTER SCHEMA 1")
    assert S._schema_altered_by_statement(src, masked, m) is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("GRANT USAGE ON SCHEMA s TO r", ((), ())),
        ("REVOKE", ((), ())),
        ("REVOKE GRANT OPTION FOR USAGE ON SCHEMA s FROM r", ((), ())),
        ("REVOKE GRANT OPTION x", ((), ())),
        ("REVOKE SELECT ON SCHEMA s FROM r", ((), ())),
        ("REVOKE USAGE ON", None),
        ("REVOKE USAGE ON TABLE t FROM r", ((), ())),
        ("REVOKE USAGE ON SCHEMA s FROM r", ((b"s",), (b"r",))),
        ("REVOKE ALL ON SCHEMA s, T FROM GROUP r, Q", ((b"s", b"t"), (b"r", b"q"))),
        ("REVOKE ALL ON SCHEMA 1", None),
        ("REVOKE USAGE ON SCHEMA s", None),
        ("REVOKE USAGE ON SCHEMA s /* open", None),
        ("REVOKE USAGE ON SCHEMA s TO r", None),
        ("REVOKE USAGE ON SCHEMA s FROM 1", None),
    ],
)
def test_schemas_with_revoked_usage_by_statement(text, expected):
    src, masked = _mk(text)
    assert S._schemas_with_revoked_usage_by_statement(src, masked, 0, len(src)) == expected


def test_revoke_keyword_past_statement_end_is_ignored():
    src, masked = _mk("REVOKE USAGE ON SCHEMA s FROM r")
    assert S._schemas_with_revoked_usage_by_statement(src, masked, 0, 8) == ((), ())


def test_role_context_events_set_role_with_unreadable_name():
    src, masked = _mk("SET ROLE 'x';")
    assert S._role_context_change_events(src, masked) == ((0, "session", None),)


# ---------------------------------------- role / transaction / index readers


def test_role_context_events_reset_to_default_name_is_none():
    src, masked = _mk("SET ROLE DEFAULT;")
    assert S._role_context_change_events(src, masked) == ((0, "session", None),)
    src, masked = _mk("SET LOCAL ROLE bob;")
    assert S._role_context_change_events(src, masked) == ((0, "local", b"bob"),)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("COMMIT AND NO CHAIN;", [(0, "commit")]),
        ("COMMIT AND CHAIN;", [(0, "commit-chain")]),
        ("ROLLBACK WORK AND CHAIN;", [(0, "rollback-chain")]),
        ("ROLLBACK TO sp;", [(0, "uncertain-transaction")]),
        ("PREPARE TRANSACTION 'x';", [(0, "prepared")]),
        ("SAVEPOINT a;", [(0, "uncertain-transaction")]),
        ("START TRANSACTION;", [(0, "begin")]),
        ("SELECT 1;", []),
    ],
)
def test_transaction_control_events(text, expected):
    src, masked = _mk(text)
    assert list(S._transaction_control_events(src, masked)) == expected


def test_qualified_identifier_details_unreadable_tails():
    assert S._read_qualified_sql_identifier_details(b"a /* open", 0) is None
    assert S._read_qualified_sql_identifier_details(b"a.", 0) is None
    assert S._read_qualified_sql_identifier_details(b"A.B", 0) == (b"a", b"b", 3)
    assert S._read_qualified_sql_identifier_details(b"A x", 0) == (None, b"a", 1)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", None),
        ("CONCURRENTLY", None),
        ("IF x", None),
        ("IF NOT x", None),
        ("IF NOT EXISTS idx ON t", (None, b"idx", 17)),
        ("CONCURRENTLY idx", (None, b"idx", 16)),
        ("ON t", (None, None, 0)),
    ],
)
def test_read_create_index_name_details(text, expected):
    assert S._read_create_index_name_details(text.encode(), 0) == expected


def test_read_created_index_identity():
    src, masked = _mk("")
    assert S._read_created_index_identity(src, masked, 0) is None
    src, masked = _mk("idx")
    assert S._read_created_index_identity(src, masked, 0) == (None, b"idx")
    src, masked = _mk("idx ON s.t")
    assert S._read_created_index_identity(src, masked, 0) == (b"s", b"idx")
    src, masked = _mk("idx ON ONLY t")
    assert S._read_created_index_identity(src, masked, 0) == (None, b"idx")


def test_attachment_index_reference():
    text = "ALTER TABLE s.t ADD UNIQUE USING INDEX i"
    src, masked = _mk(text)
    start = text.index("UNIQUE")
    end = text.index("INDEX") + 5
    calls, cb = _counter()
    assert S._attachment_index_reference(src, masked, start, end, check_deadline=cb) == (b"s", b"i")
    assert len(calls) >= 1
    text = "ALTER TABLE t ADD UNIQUE USING INDEX "
    src, masked = _mk(text)
    assert S._attachment_index_reference(src, masked, text.index("UNIQUE"), len(text)) is None
    text = "CREATE TABLE t (a int, UNIQUE USING INDEX i)"
    src, masked = _mk(text)
    assert S._attachment_index_reference(
        src, masked, text.index("UNIQUE"), text.index("INDEX") + 5
    ) == (None, b"i")
    text = "ALTER TABLE t ADD UNIQUE USING INDEX i"
    src, masked = _mk(text)
    assert S._attachment_index_reference(
        src, masked, text.index("UNIQUE"), text.index("INDEX") + 5, check_deadline=lambda: None
    ) == (None, b"i")


# ------------------------------------------------------- table readers


def test_next_sql_keyword_variants():
    assert S._next_sql_keyword(b"/* x", b"/* x", 0) is None
    ok = lambda: None
    assert S._next_sql_keyword(b"abc d", b"abc d", 0, check_deadline=ok) == (b"ABC", 3)
    assert S._next_sql_keyword(b"1abc", b"1abc", 0, check_deadline=ok) is None
    assert S._next_sql_keyword(b"  ", b"  ", 0, check_deadline=ok) is None
    calls, cb = _counter()
    long = b"a" * 5000
    assert S._next_sql_keyword(long, long, 0, check_deadline=cb) == (long.upper(), 5000)
    assert len(calls) >= 3


def test_if_not_exists_and_identity_readers_reject_partial_prefixes():
    def tbl(text):
        src, masked = _mk(text)
        return src, masked, re.match(rb"CREATE TABLE", src)

    src, masked, m = tbl("CREATE TABLE IF EXISTS t (a int)")
    assert S._create_table_is_if_not_exists(src, masked, m) is False
    src, masked, m = tbl("CREATE TABLE IF NOT EXISTS t (a int)")
    assert S._create_table_is_if_not_exists(src, masked, m) is True
    src, masked, m = tbl("CREATE TABLE IF NOT t")
    assert S._create_table_is_if_not_exists(src, masked, m) is False
    src, masked, m = tbl("CREATE TABLE t")
    assert S._create_table_is_if_not_exists(src, masked, m) is False

    src, masked = _mk("IF EXISTS")
    assert S._read_create_table_identity(src, masked, 0) is None
    src, masked = _mk("IF NOT EXISTS s.t")
    assert S._read_create_table_identity(src, masked, 0) == (b"s", b"t", 17)
    src, masked = _mk("IF")
    assert S._read_create_table_identity(src, masked, 0) is None

    src, masked = _mk("IF x")
    assert S._read_alter_table_identity(src, masked, 0) is None
    src, masked = _mk("IF EXISTS ONLY s.t *")
    assert S._read_alter_table_identity(src, masked, 0) == ((b"s", b"t"), 20)
    src, masked = _mk("1")
    assert S._read_alter_table_identity(src, masked, 0) is None


def test_read_action_identifier_variants():
    assert S._read_action_identifier(b"/* x", b"/* x", 0) is None
    assert S._read_action_identifier(b"1", b"1", 0) is None
    assert S._read_action_identifier(b"abc", b"   ", 0) is None
    assert S._read_action_identifier(b" Abc", b" Abc", 0) == (b"abc", 4)
    assert S._read_action_identifier(b'"A"', b'   ', 0) == (b"A", 3)


def test_collect_dropped_tables_lists_and_unreadable():
    src, masked = _mk("DROP TABLE a, s.b;")
    expected = frozenset({(None, b"a"), (b"s", b"b")})
    assert S._collect_dropped_tables(src, masked) == expected
    calls, cb = _counter()
    assert S._collect_dropped_tables(src, masked, check_deadline=cb) == expected
    assert len(calls) >= 2
    src, masked = _mk("DROP TABLE ;")
    assert S._collect_dropped_tables(src, masked) == frozenset()


# --------------------------------------------------- temporary relation facts


_TEMP_SQL = """
CREATE TEMP TABLE t1 (a int);
CREATE TEMP TABLE ;
CREATE TEMP VIEW v1 AS SELECT 1;
CREATE TEMP VIEW ;
SELECT 1 INTO TEMP t2;
SELECT 1 INTO TEMP ;
SELECT 1 INTO pg_temp.t3 FROM x;
SELECT 1 INTO public.t4;
SELECT 1 INTO ;
CREATE TABLE pg_temp.t5 (a int);
CREATE TABLE ;
CREATE VIEW pg_temp.v2 AS SELECT * FROM t1, "q"."w", (select 1) z, f(a, b), ONLY x2;
CREATE VIEW ;
CREATE VIEW pubv AS SELECT 1 FROM t1 JOIN LATERAL (select 1) y ON true WHERE a IN (1, 2);
ALTER TABLE pg_temp.t5 RENAME TO t6, ADD COLUMN c int;
ALTER TABLE public.k RENAME TO z;
ALTER TABLE ;
ALTER TABLE t1 OWNER TO me;
ALTER TABLE t1 RENAME x;
ALTER TABLE t1 RENAME TO ;
"""


def test_collect_temporary_relation_facts_exercises_all_statement_kinds():
    src, masked = _mk(_TEMP_SQL)
    calls, cb = _counter()
    plain = S._collect_temporary_relation_facts(src, masked)
    timed = S._collect_temporary_relation_facts(src, masked, check_deadline=cb)
    assert plain == timed
    assert len(calls) > 20
    names, renames, views = plain
    assert {b"t1", b"v1", b"t2", b"t3", b"t5", b"v2"} <= names
    assert b"t4" not in names
    assert (b"t5", b"t6") in renames
    assert (None, b"t1", b"v2") in views
    assert (b"q", b"w", b"v2") in views
    assert (None, b"x2", b"v2") in views
    with pytest.raises(Boom):
        S._collect_temporary_relation_facts(src, masked, check_deadline=_boom)


def test_collect_temporary_relation_names_propagates_renames_and_views():
    src, masked = _mk(_TEMP_SQL)
    names = S._collect_temporary_relation_names(src, masked, [b"extra"])
    assert {b"t1", b"t6", b"v2", b"pubv", b"extra"} <= names


def test_collect_temporary_relation_names_ignores_nontemp_schema_edge_and_chains():
    src, masked = _mk(
        "CREATE TEMP TABLE a (x int); ALTER TABLE a RENAME TO b; ALTER TABLE b RENAME TO c;"
        "CREATE VIEW va AS SELECT 1 FROM other.a;"
    )
    names = S._collect_temporary_relation_names(src, masked)
    assert names == frozenset({b"a", b"b", b"c", b"va"})


def test_view_statement_end_chunking():
    assert S._view_statement_end(b"abc;d", 0, None) == 3
    assert S._view_statement_end(b"abc;d", 0, lambda: None) == 3
    assert S._view_statement_end(b"a" * 5000 + b";", 0, lambda: None) == 5000
    assert S._view_statement_end(b"abc", 0, lambda: None) == -1
    assert S._view_statement_end(b"a" * 5000, 0, lambda: None) == -1


def test_view_relation_references_chunking_and_forms():
    def refs(text, **kw):
        src, masked = _mk(text)
        return S._view_relation_references(src, masked, 0, len(src), **kw)

    text = "SELECT 1 FROM ONLY a, LATERAL b, (select 1 from n) c, \"Q\" , f(x, y), z WHERE k IN (1,2)"
    expected = frozenset({(None, b"a"), (None, b"b"), (None, b"n"), (None, b"z"), (None, b"Q"), (None, b"f")})
    assert refs(text) == expected
    assert refs(text, check_deadline=lambda: None) == expected
    assert refs("SELECT 1 FROM /* open") == frozenset()
    assert refs("SELECT 1 FROM /* open", check_deadline=lambda: None) == frozenset()
    assert refs("SELECT 1 FROM (a") == frozenset()
    assert refs("SELECT 1 FROM ") == frozenset()
    assert refs("SELECT 1 FROM a, (select 1) q, b") == frozenset({(None, b"a"), (None, b"b")})
    assert refs("SELECT 1 FROM a, ") == frozenset({(None, b"a")})
    assert refs("SELECT 1 FROM 1") == frozenset()
    pad = "x" * 5000
    big = f"SELECT '{pad}' FROM a" 
    assert refs(big, check_deadline=lambda: None) == frozenset({(None, b"a")})
    big2 = f"SELECT 1 /* {pad} */ FROM a"
    assert refs(big2, check_deadline=lambda: None) == frozenset({(None, b"a")})
    long_tail = "SELECT 1 FROM a " + "(" + pad + ")"
    calls, cb = _counter()
    assert refs(long_tail, check_deadline=cb) == frozenset({(None, b"a")})
    assert len(calls) >= 4
    with pytest.raises(Boom):
        refs(text, check_deadline=_boom)


def test_temporary_facts_without_trailing_semicolon_and_view_chunk_boundary():
    src, masked = _mk("CREATE VIEW pg_temp.last AS SELECT 1 FROM t1; ALTER TABLE pg_temp.t5 RENAME TO t6")
    names, renames, views = S._collect_temporary_relation_facts(src, masked)
    assert names == frozenset({b"last"})
    assert renames == ((b"t5", b"t6"),)
    assert views == ((None, b"t1", b"last"),)
    src, masked = _mk("CREATE VIEW pg_temp.open_ended AS SELECT 1 FROM t2")
    _, _, views = S._collect_temporary_relation_facts(src, masked)
    assert views == ((None, b"t2", b"open_ended"),)
    calls, cb = _counter()
    _, _, views = S._collect_temporary_relation_facts(src, masked, check_deadline=cb)
    assert views == ((None, b"t2", b"open_ended"),)


def test_view_references_clause_at_chunk_overlap_and_trailing_alias():
    def refs(text, **kw):
        src, masked = _mk(text)
        return S._view_relation_references(src, masked, 0, len(src), **kw)

    # a FROM that starts exactly at the 4096 chunk boundary is yielded once,
    # by the following chunk, not twice
    text = "x" * 4095 + " FROM a"
    assert refs(text, check_deadline=lambda: None) == frozenset({(None, b"a")})
    text = "x" * 4094 + " FROM a"
    assert refs(text, check_deadline=lambda: None) == frozenset({(None, b"a")})
    text = "x" * 4093 + " FROM a"
    assert refs(text, check_deadline=lambda: None) == frozenset({(None, b"a")})
    # a quoted alias is skipped as one identifier; the comma list continues
    assert refs('SELECT 1 FROM a "al", b') == frozenset({(None, b"a"), (None, b"b")})
    assert refs('SELECT 1 FROM a "al", b', check_deadline=lambda: None) == frozenset(
        {(None, b"a"), (None, b"b")}
    )
    assert refs('SELECT 1 FROM a "al', check_deadline=lambda: None) == frozenset({(None, b"a")})


_RELOCATE_SQL = """
ALTER TABLE ;
ALTER TABLE s.t RENAME TO u;
ALTER TABLE t SET SCHEMA x;
ALTER TABLE a RENAME x;
ALTER TABLE a RENAME TO ;
ALTER TABLE a SET other;
ALTER TABLE a SET SCHEMA ;
ALTER TABLE b OWNER TO me;
ALTER TABLE z RENAME TO y
"""


def test_collect_relocated_tables():
    src, masked = _mk(_RELOCATE_SQL)
    expected = frozenset(
        {
            (b"s", b"t"),
            (b"s", b"u"),
            (None, b"t"),
            (b"x", b"t"),
            (None, b"z"),
            (None, b"y"),
        }
    )
    assert S._collect_relocated_tables(src, masked) == expected
    calls, cb = _counter()
    assert S._collect_relocated_tables(src, masked, check_deadline=cb) == expected
    assert calls


_ALTERED_SQL = """
ALTER TABLE ;
ALTER TABLE t OWNER TO me;
ALTER TABLE t DROP CONSTRAINT c;
ALTER TABLE t DROP COLUMN IF NOT x;
ALTER TABLE t DROP COLUMN IF EXISTS d1;
ALTER TABLE t DROP COLUMN IF EXISTS CONSTRAINT c;
ALTER TABLE t DROP d2;
ALTER TABLE t ADD COLUMN IF NOT EXISTS a1 int;
ALTER TABLE t ADD COLUMN IF x;
ALTER TABLE t ADD COLUMN IF NOT x;
ALTER TABLE t ADD COLUMN IF NOT;
ALTER TABLE t ADD CONSTRAINT k UNIQUE (a);
ALTER TABLE t ADD a2 int;
ALTER TABLE t ALTER COLUMN IF EXISTS c1 TYPE int;
ALTER TABLE t ALTER COLUMN IF NOPE c2 TYPE int;
ALTER TABLE t ALTER c3 SET DATA TYPE text;
ALTER TABLE t ALTER c4 SET NOT NULL;
ALTER TABLE t ALTER c5 DROP DEFAULT;
ALTER TABLE t ALTER ;
ALTER TABLE t RENAME COLUMN r1 TO r2;
ALTER TABLE t RENAME TO newt;
ALTER TABLE t RENAME CONSTRAINT a TO b;
ALTER TABLE t RENAME ;
ALTER TABLE t RENAME r3 x;
ALTER TABLE t RENAME r4 TO ;
ALTER TABLE t RENAME r5 TO r6
"""


def test_collect_altered_columns():
    src, masked = _mk(_ALTERED_SQL)
    t = (None, b"t")
    expected = frozenset(
        (t, name)
        for name in (b"d1", b"d2", b"a1", b"a2", b"c1", b"c3", b"r1", b"r2", b"r5", b"r6")
    )
    assert S._collect_altered_columns(src, masked) == expected
    calls, cb = _counter()
    assert S._collect_altered_columns(src, masked, check_deadline=cb) == expected
    assert calls


def test_table_column_declarations_skip_constraints_and_unparsable_clauses():
    text = 'CREATE TABLE t (a int COLLATE "C", PRIMARY KEY (a), "unique" text, 1x, b varchar(5)[])'
    src, masked = _mk(text)
    start = text.index("(")
    end = text.rindex(")")
    for kw in ({}, {"check_deadline": lambda: None}):
        types, collated = S._table_column_declarations(src, masked, (None, b"t"), start, end, **kw)
        assert types == frozenset(
            {((None, b"t"), b"a", b"int"), ((None, b"t"), b"b", b"varchar(5)[]")}
        )
        assert collated == frozenset({((None, b"t"), b"a")})


def test_collect_table_column_metadata_with_deadline_and_skips():
    text = (
        "CREATE TABLE IF NOT EXISTS skipme (a int);"
        "CREATE TABLE s.t (a int COLLATE \"C\");"
        "CREATE TABLE AS SELECT 1;"
    )
    src, masked = _mk(text)
    calls, cb = _counter()
    types, collated = S._collect_table_column_metadata(src, masked, check_deadline=cb)
    assert types == frozenset({((b"s", b"t"), b"a", b"int")})
    assert collated == frozenset({((b"s", b"t"), b"a")})
    assert calls


def test_create_table_column_list_variants():
    def cl(text, **kw):
        src, masked = _mk(text)
        m = re.match(rb"CREATE TABLE", masked)
        return S._create_table_column_list(masked, m, **kw)

    assert cl("CREATE TABLE ;") is None
    assert cl("CREATE TABLE t AS SELECT 1") is None
    assert cl("CREATE TABLE t (a int") is None
    assert cl("CREATE TABLE t (a int; b) ;") is None
    got = cl("CREATE TABLE t (a int)")
    assert got is not None and got[1:] == (15, 21)


def test_declared_type_names_edges_and_deadline():
    text = (
        "CREATE TYPE pg_temp.mood AS ENUM ('a');"
        "ALTER TYPE pg_temp.mood RENAME TO feeling;"
        "ALTER TYPE feeling RENAME TO emotion;"
        "ALTER TYPE other.x RENAME TO y;"
        "ALTER TYPE ;"
        "ALTER TYPE q OWNER TO me;"
        "ALTER TYPE q RENAME x;"
        "ALTER TYPE q RENAME TO ;"
        "CREATE DOMAIN dom AS int;"
        "CREATE TABLE tt (a int);"
        "CREATE VIEW vv AS SELECT 1;"
        "CREATE TYPE ;"
    )
    src, masked = _mk(text)
    calls, cb = _counter()
    plain = S._collect_declared_type_names(src, masked, [(b"pg_temp", b"zz")])
    timed = S._collect_declared_type_names(src, masked, [(b"pg_temp", b"zz")], check_deadline=cb)
    assert plain == timed and calls
    for item in (
        (b"pg_temp", b"mood"),
        (b"pg_temp", b"feeling"),
        (b"pg_temp", b"emotion"),
        (None, b"dom"),
        (None, b"tt"),
        (None, b"vv"),
    ):
        assert item in plain
    assert (b"other", b"y") not in plain
    names, renames = S._collect_declared_type_facts(src, masked)
    assert (b"pg_temp", b"mood", b"feeling") in renames
    assert (None, b"q", b"x") not in renames
    # unqualified renames follow temporary types in the pending walk
    src2, masked2 = _mk("ALTER TYPE a RENAME TO b; ALTER TYPE b RENAME TO c;")
    got = S._collect_declared_type_names(src2, masked2, [(b"pg_temp", b"a")])
    assert {(b"pg_temp", b"b"), (b"pg_temp", b"c")} <= got


# ------------------------------------------------- search_path value scanners


def _value_starts(text, **kw):
    src, masked = _mk(text)
    try:
        return S._search_path_value_starts(src, masked, **kw)
    except Exception:
        return S._search_path_value_starts(
            src,
            masked,
            dollar_body_ranges=(),
            configuration_ranges=(),
            transaction_ranges=(),
            **kw,
        )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/* c */ SET search_path = a;", ((8, 25, False),)),
        ("SET /* open", ()),
        ("SET ;", ()),
        ("SET LOCAL ;", ()),
        ("SET search_path /* open", ()),
        ("SET SCHEMA 'a';", ((0, 11, False),)),
        ("SET LOCAL SCHEMA 'a';", ((0, 17, True),)),
        ("SET other = 1;", ()),
        ("SET search_path TO a;", ((0, 18, False),)),
        ("SET search_path FROM CURRENT;", ((0, None, False),)),
        ("SET search_path FROM x;", ()),
        ("SET search_path x;", ()),
        ('SET search_path "to";', ()),
        ("SET search_path 1;", ()),
        ("SET search_path;", ()),
        ("UPDATE t SET a=1;", ()),
        ("BEGIN; SET search_path = a; COMMIT;", ((7, 24, True),)),
        ("DO $$ BEGIN SET search_path = a; UPDATE t SET b=1; END $$;", ((12, 29, True),)),
        ("SET SESSION search_path = a;", ((0, 25, False),)),
        ("ALTER ROLE r SET search_path = a;", ()),
        ('SET "search_path" = a;', ((0, 19, False),)),
        ("SET LOCAL x.y = 1;", ()),
        ("SET search_path FROM /* open", ()),
    ],
)
def test_search_path_value_starts(text, expected):
    assert _value_starts(text) == expected
    assert _value_starts(text, check_deadline=lambda: None) == expected


def test_search_path_value_starts_set_before_first_statement_range():
    src, masked = _mk("SET search_path = a;")
    got = S._search_path_value_starts(
        src,
        masked,
        configuration_ranges=(),
        transaction_ranges=(),
        dollar_body_ranges=(),
    )
    assert got == ((0, 17, False),)
    # a SET that precedes every statement start is not a statement head
    import unittest.mock as mock

    with mock.patch.object(S, "_sql_statement_ranges", return_value=((5, 19),)):
        assert (
            S._search_path_value_starts(
                src, masked, configuration_ranges=(), transaction_ranges=(), dollar_body_ranges=()
            )
            == ()
        )


def _pg_updates(text, **kw):
    src, masked = _mk(text)
    try:
        return S._pg_settings_search_path_updates(src, masked, **kw)
    except Exception:
        return S._pg_settings_search_path_updates(
            src, masked, dollar_body_ranges=(), transaction_ranges=(), **kw
        )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("UPDATE pg_settings SET setting='x' WHERE name='search_path';", ((0, False),)),
        ("UPDATE ONLY pg_catalog.pg_settings SET setting='x'", ((0, False),)),
        ("UPDATE pg_settings SET setting='x' WHERE name='other';", ()),
        ("UPDATE pg_settings SET (a, setting) = ROW(1,2);", ((0, False),)),
        ("UPDATE pg_settings SET (a, b) = ROW(1,2);", ()),
        ("UPDATE pg_settings SET (a, setting = 1;", ()),
        ("UPDATE pg_settings SET other = 1;", ()),
        ("UPDATE pg_settings;", ()),
        ("UPDATE t SET setting=1;", ()),
        ("UPDATE s.pg_settings SET setting=1;", ()),
        ("UPDATE", ()),
        ("UPDATE pg_settings SET /* open", ()),
        ("UPDATE pg_settings SET setting 5;", ()),
        ("UPDATE pg_settings SET 1=2;", ()),
        ("UPDATE pg_settings SET (setting = 1;", ()),
        ("BEGIN; UPDATE pg_settings SET setting=1; COMMIT;", ((7, True),)),
        ("DO $$ BEGIN UPDATE pg_settings SET setting=1; END $$;", ((12, True),)),
        ("UPDATE pg_settings SET a=1, setting = 2;", ((0, False),)),
        ("UPDATE pg_settings SET ( a , setting ) = (1,2);", ((0, False),)),
        ("UPDATE pg_settings SET (setting x) = (1);", ()),
        ("UPDATE /* open", ()),
    ],
)
def test_pg_settings_search_path_updates(text, expected):
    assert _pg_updates(text) == expected
    assert _pg_updates(text, check_deadline=lambda: None) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("SELECT set_config('search_path','a',false);", (7,)),
        ("SELECT set_config('work_mem','1',false);", ()),
        ("-- set_config(", ()),
        ("SELECT set_config;", ()),
        ("SELECT set_config(x,'1',false);", (7,)),
        ("SELECT set_config( /* open", (7,)),
        ("SELECT set_config('abc", (7,)),
        ("SELECT set_config('a' || 'b', '1', false);", (7,)),
        ("SELECT set_config('a''b','1',false);", ()),
        ("SELECT set_config('a\\\\b','1',false);", (7,)),
        ("SELECT set_config('a' /* open", (7,)),
        ("SELECT set_config('a'", (7,)),
    ],
)
def test_set_config_search_path_writes(text, expected):
    src, masked = _mk(text)
    assert S._set_config_search_path_writes(src, masked) == expected
    assert S._set_config_search_path_writes(src, masked, check_deadline=lambda: None) == expected


# --------------------------------------- temporary-creation event state machine

_PREFIX = "CREATE SCHEMA s; SET search_path = s, pg_temp; "


def _may_be_temp(script, **kw):
    src, masked = _mk(script)
    events = S._search_path_temporary_creation_events(src, masked, **kw)
    return S._search_path_may_be_temporary_at(events, len(src) + 1)


@pytest.mark.parametrize(
    "tail,expected",
    [
        ("", False),
        ("SELECT 1;", False),
        ("DO $$ BEGIN CREATE SCHEMA x; END $$;", True),
        ("DO $$ BEGIN DROP SCHEMA x; END $$;", True),
        ("DO $$ BEGIN ALTER SCHEMA x RENAME TO y; END $$;", True),
        ("DROP SCHEMA IF x;", True),
        ("DROP SCHEMA other;", False),
        ("DROP SCHEMA s;", True),
        ("ALTER SCHEMA s RENAME TO t;", True),
        ("ALTER SCHEMA s RENAME TO t; SET search_path = t, pg_temp;", False),
        ("ALTER SCHEMA s OWNER TO bob;", True),
        ("ALTER SCHEMA ;", True),
        ("REVOKE USAGE ON SCHEMA s FROM bob;", True),
        ("REVOKE USAGE ON SCHEMA s FROM public;", False),
        ("REVOKE USAGE ON SCHEMA nope FROM bob;", False),
        ("REVOKE USAGE ON SCHEMA pg_catalog FROM bob;", False),
        ("REVOKE USAGE ON SCHEMA;", True),
        ("REVOKE SELECT ON TABLE t FROM bob;", False),
        ("SET ROLE bob;", True),
        ("BEGIN; SET LOCAL ROLE bob; COMMIT;", True),
        ("SET LOCAL ROLE bob;", False),
        ("SAVEPOINT x;", True),
        ("PREPARE TRANSACTION 'x';", True),
        ("EXECUTE 'x';", False),
        ("DO $$ BEGIN EXECUTE 'x'; END $$;", True),
        ("SELECT set_config('search_path', 'x', false);", True),
        ("RESET search_path;", True),
        ("GRANT bob TO alice;", True),
    ],
)
def test_temporary_creation_events_schema_state(tail, expected):
    assert _may_be_temp(_PREFIX + tail) is expected
    assert _may_be_temp(_PREFIX + tail, check_deadline=lambda: None) is expected


def test_commit_after_uncertain_savepoint_forgets_schemas_created_inside():
    tail = " SET search_path = t, pg_temp;"
    assert _may_be_temp("BEGIN; CREATE SCHEMA t; COMMIT;" + tail) is False
    assert _may_be_temp("BEGIN; SAVEPOINT a; CREATE SCHEMA t; COMMIT;" + tail) is True


def test_rollback_restores_schema_state_and_chain_keeps_baseline():
    tail = " SET search_path = t, pg_temp;"
    assert _may_be_temp("BEGIN; CREATE SCHEMA t; ROLLBACK;" + tail) is True
    assert _may_be_temp("BEGIN; CREATE SCHEMA t; ROLLBACK AND CHAIN; CREATE SCHEMA t; COMMIT;" + tail) is False
    assert _may_be_temp("BEGIN; COMMIT AND CHAIN; CREATE SCHEMA t; ROLLBACK;" + tail) is True
    assert _may_be_temp("BEGIN; BEGIN; CREATE SCHEMA t; COMMIT;" + tail) is False


# ------------------------------------------- transaction / reset / shadow scans


def _with_fallback(fn, text, **kw):
    src, masked = _mk(text)
    try:
        return fn(src, masked, **kw)
    except Exception:
        return fn(src, masked, dollar_body_ranges=(), **kw)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("BEGIN; SELECT 1; COMMIT;", ((0, 16),)),
        ("BEGIN; X; COMMIT AND CHAIN; Y; END;", ((0, 30),)),
        ("BEGIN; X; COMMIT AND NO CHAIN; Y;", ((0, 9),)),
        ("BEGIN; ROLLBACK TO x; SELECT 1;", ((0, 31),)),
        ("BEGIN; PREPARE TRANSACTION 'x'; SELECT 1;", ((0, 6),)),
        ("BEGIN; SELECT 1;", ((0, 16),)),
        ("SELECT 1; BEGIN; BEGIN; SELECT 2; ABORT WORK;", ((9, 33),)),
        ("COMMIT;", ()),
        (
            "START TRANSACTION; SELECT 1; ROLLBACK TRANSACTION AND CHAIN; SELECT 2; COMMIT AND;",
            ((0, 70),),
        ),
        ("  ;  BEGIN; COMMIT AND NO", ((3, 11),)),
    ],
)
def test_explicit_transaction_ranges(text, expected):
    assert _with_fallback(S._explicit_transaction_ranges, text) == expected
    assert _with_fallback(S._explicit_transaction_ranges, text, check_deadline=lambda: None) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("RESET ;", ()),
        ("RESET SESSION ;", ()),
        ("RESET search_path;", (0,)),
        ("RESET SESSION search_path;", (0,)),
        ("RESET LOCAL ALL;", (0,)),
        ("RESET other;", ()),
        ('RESET "all";', ()),
        ("RESET all;", (0,)),
        ("DISCARD ALL;", (0,)),
        ("DISCARD PLANS;", ()),
        ('DISCARD "all";', ()),
        ("DISCARD ;", ()),
        ("ALTER ROLE r RESET ALL; ALTER ROLE r RESET search_path;", ()),
        ("DO $$ BEGIN RESET ALL; DISCARD ALL; END $$;", (12, 23)),
        (
            "CREATE FUNCTION f() RETURNS int AS 'x' LANGUAGE sql SET search_path = a; DISCARD ALL;",
            (73,),
        ),
        ("ALTER ROLE r DISCARD ALL;", ()),
    ],
)
def test_search_path_reset_offsets(text, expected):
    assert _with_fallback(S._search_path_reset_offsets, text) == expected
    assert _with_fallback(S._search_path_reset_offsets, text, check_deadline=lambda: None) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("SELECT set_config('search_path','a',false);", True),
        ("DO $$ BEGIN EXECUTE 'x'; END $$;", True),
        ("SET search_path = pg_catalog, public;", False),
        ("SET search_path = public;", True),
        ("SET search_path = ;", True),
        ("BEGIN; SET search_path = pg_catalog; COMMIT;", True),
        ("SET SCHEMA 'a';", False),
        ("SET SCHEMA 'pg_temp';", True),
        ("SET SCHEMA bare;", True),
        ("SET search_path FROM CURRENT;", True),
        ("RESET ALL;", True),
        ("SELECT 1;", False),
        ("SET search_path = 1;", True),
    ],
)
def test_search_path_may_shadow_catalog(text, expected):
    assert _with_fallback(S._search_path_may_shadow_catalog, text) is expected
    assert _with_fallback(S._search_path_may_shadow_catalog, text, check_deadline=lambda: None) is expected


def test_search_path_may_shadow_catalog_accepts_prebuilt_path_settings():
    src, masked = _mk("SET SCHEMA 'a';")
    assert S._search_path_may_shadow_catalog(src, masked, ((0, 11, False),)) is False
    assert S._search_path_may_shadow_catalog(src, masked, ((0, None, False),)) is True


def test_non_session_configuration_ranges_and_timeline_defaults():
    text = "ALTER ROLE r SET x = 1; SET search_path = pg_catalog, pg_temp; CREATE FUNCTION f() RETURNS int AS 'x' LANGUAGE sql SET y = 1;"
    src, masked = _mk(text)
    calls, cb = _counter()
    ranges = S._non_session_configuration_ranges(src, masked, check_deadline=cb)
    # routine DDL ranges come first, then role/database/system GUC DDL
    assert ranges == ((62, len(text)), (0, 23))
    assert calls
    plain = S._build_search_path_timeline(src, masked)
    timed = S._build_search_path_timeline(src, masked, check_deadline=cb)
    assert plain == timed
    offsets, temp_shadow, proven, path_shadow = plain
    assert offsets == (24,)
    assert (temp_shadow, proven, path_shadow) == ((False,), (True,), (False,))
    assert S._search_path_facts_at(plain, 25) == (False, True, False)
    assert S._search_path_facts_at(plain, 24) == (True, False, False)


# --------------------------------------------------- mutation context / scanners


def _ctx(*texts, **kw):
    return S._collect_sql_mutation_context(
        [(f"f{i}.sql", t) for i, t in enumerate(texts)], **kw
    )


def test_context_flags_unresolvable_index_attachment():
    ctx = _ctx("ALTER TABLE t ADD CONSTRAINT k UNIQUE USING INDEX ;")
    assert ctx.unresolved_index_attachment is True
    ctx = _ctx("ALTER TABLE t ADD CONSTRAINT k UNIQUE USING INDEX i;")
    assert ctx.unresolved_index_attachment is False
    assert ctx.attached_indexes == frozenset({(None, b"i")})


def test_context_propagates_type_renames_across_sources_and_runs_with_deadline():
    texts = ("CREATE TYPE pg_temp.a AS ENUM ('x');", "ALTER TYPE a RENAME TO b; ALTER TYPE b RENAME TO c;")
    plain = _ctx(*texts)
    calls, cb = _counter()
    timed = _ctx(*texts, check_deadline=cb)
    assert plain == timed and calls
    assert {(b"pg_temp", b"a"), (b"pg_temp", b"b"), (b"pg_temp", b"c")} <= plain.declared_type_names
    # relation renames propagate across sources as well
    rel = _ctx("CREATE TEMP TABLE r1 (a int);", "ALTER TABLE pg_temp.r1 RENAME TO r2;")
    assert {b"r1", b"r2"} <= rel.temporary_relation_names


def _sites(fn, text, *args, **kw):
    lexed = L.lex_sql(text.encode())
    return [
        (s.operator, text.encode()[s.start : s.end], s.replacement)
        for s in fn(text.encode(), lexed.mask, *args, **kw)
    ]


def test_scan_drop_unique_with_unresolved_attachment_skips_index_sites():
    text = "ALTER TABLE t ADD CONSTRAINT k UNIQUE USING INDEX ; CREATE UNIQUE INDEX i ON t (a);"
    assert _sites(S._scan_drop_unique, text) == []
    # an unrelated, resolvable attachment keeps unlinked indexes mutable
    text = "ALTER TABLE t ADD UNIQUE USING INDEX i; CREATE UNIQUE INDEX j ON t (a);"
    assert _sites(S._scan_drop_unique, text) == [("sql:drop-unique", b"CREATE UNIQUE INDEX", b"CREATE INDEX")]
    calls, cb = _counter()
    assert _sites(S._scan_drop_unique, text, check_deadline=cb)
    assert calls


def test_search_declaration_keyword_windows_and_deadline():
    pad = b"x" * 70000
    data = pad + b" DECLARE"
    calls, cb = _counter()
    match = S._search_declaration_keyword(data, 0, len(data), S._DECLARE_RE, check_deadline=cb)
    assert match is not None and match.start() == 70001
    assert len(calls) >= 4
    calls, cb = _counter()
    assert S._search_declaration_keyword(b"abc", 0, 3, S._DECLARE_RE, check_deadline=cb) is None
    assert calls
    assert S._search_declaration_keyword(b"abc", 0, 3, S._DECLARE_RE, check_deadline=None) is None


def test_declare_section_spans_with_deadline_and_without_begin():
    text = b"x $$ DECLARE v int; BEGIN NULL; END $$; y $$ DECLARE w int $$; z $$ plain $$"
    lexed = L.lex_sql(text)
    plain = S._declare_section_spans(lexed.mask, lexed.dollar_bodies)
    calls, cb = _counter()
    timed = S._declare_section_spans(lexed.mask, lexed.dollar_bodies, check_deadline=cb)
    assert plain == timed and calls
    assert len(plain) == 2
    assert text[plain[0][0] : plain[0][1]] == b"DECLARE v int; "
    assert plain[1][1] == lexed.dollar_bodies[1][1]


def test_column_definition_clauses_skips_unparsable_tables_and_non_add_actions():
    text = (
        "CREATE TABLE t1 (a int, b int);"
        "CREATE TABLE t2 AS SELECT 1;"
        "CREATE TABLE t3 (a int;"
        "ALTER TABLE ;"
        "ALTER TABLE t1 ALTER b SET NOT NULL, ADD COLUMN c int;"
    )
    src = text.encode()
    lexed = L.lex_sql(src)
    for kw in ({}, {"check_deadline": lambda: None}):
        spans = S._column_definition_clauses(src, lexed.mask, **kw)
        assert [src[a:b].strip() for a, b in spans] == [b"a int", b"b int", b"ADD COLUMN c int"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("", None),
        ("t PARTITION x", None),
        ("t PARTITION OF", None),
        ("t PARTITION OF p (a int)", 17),
        ("t OF", None),
        ("t OF typ (a WITH OPTIONS)", 9),
        ("t USING", None),
        ("t USING heap (a int)", 13),
        ("t", None),
        ("t AS SELECT 1", None),
        ("t /* open", None),
        ("t (a int)", 2),
    ],
)
def test_read_create_table_columns_open(text, expected):
    src, masked = _mk(text)
    assert S._read_create_table_columns_open(src, masked, 0) == expected
    assert S._read_create_table_columns_open(src, masked, 0, check_deadline=lambda: None) == expected


def test_schema_events_rename_of_unknown_schema_and_rollback_without_begin_are_inert():
    assert _may_be_temp(_PREFIX + "ALTER SCHEMA nope RENAME TO x;") is False
    assert _may_be_temp(_PREFIX + "ROLLBACK;") is False


def test_revoke_grant_without_option_is_read_as_privileges():
    src, masked = _mk("REVOKE GRANT x;")
    assert S._schemas_with_revoked_usage_by_statement(src, masked, 0, len(src)) == ((), ())


def test_attachment_reference_with_unreadable_alter_table_name():
    text = "ALTER TABLE 1 ADD UNIQUE USING INDEX i"
    src, masked = _mk(text)
    got = S._attachment_index_reference(src, masked, text.index("UNIQUE"), text.index("INDEX") + 5)
    assert got == (None, b"i")


def test_altered_columns_ignores_unreadable_column_after_drop_and_add():
    src, masked = _mk("ALTER TABLE t DROP COLUMN 1; ALTER TABLE t ADD COLUMN 1;")
    assert S._collect_altered_columns(src, masked) == frozenset()


def test_declared_type_facts_ignore_unnamed_tables_and_views():
    src, masked = _mk("CREATE TABLE ; CREATE VIEW ;")
    assert S._collect_declared_type_facts(src, masked) == (frozenset(), ())


def test_context_view_over_qualified_other_schema_is_not_a_temp_dependency():
    ctx = _ctx("CREATE TEMP TABLE a (x int); CREATE VIEW public.v AS SELECT 1 FROM other.a;")
    assert ctx.temporary_relation_names == frozenset({b"a"})
    ctx = _ctx("CREATE TEMP TABLE a (x int); CREATE VIEW public.v AS SELECT 1 FROM pg_temp.a;")
    assert ctx.temporary_relation_names == frozenset({b"a", b"v"})


# ----------------------------------------------------------- drop-not-null


_NOT_NULL_SQL = """CREATE TABLE t (
  a int NOT NULL NO INHERIT,
  b int CONSTRAINT nn NOT NULL,
  c serial NOT NULL,
  d int GENERATED ALWAYS AS IDENTITY NOT NULL,
  e int PRIMARY KEY NOT NULL,
  f int NOT NULL,
  CONSTRAINT nn2 NOT NULL g,
  NOT NULL h
);
ALTER TABLE t ADD COLUMN IF x serial NOT NULL;
ALTER TABLE t ADD COLUMN IF NOT EXISTS y serial NOT NULL;
ALTER TABLE t ADD COLUMN z int NOT NULL;
ALTER TABLE t ALTER a SET NOT NULL;
ALTER TABLE t ALTER a DROP NOT NULL;
SELECT 1 WHERE a IS NOT NULL;
"""


def test_scan_drop_not_null_rules_and_deadline():
    src = _NOT_NULL_SQL.encode()
    lexed = L.lex_sql(src)
    def run(**kw):
        return [
            (s.lineno, s.description, src[s.start : s.end], s.replacement)
            for s in S._scan_drop_not_null(src, lexed.mask, [], **kw)
        ]

    plain = run()
    calls, cb = _counter()
    assert run(check_deadline=cb) == plain and calls
    # NO INHERIT (a), named constraint (b), table-level NOT NULL (g, h) and
    # DROP NOT NULL / IS NOT NULL (lines 15/16) yield no site.
    assert plain == [
        (4, "NOT NULL -> omitted (SERIAL implies NOT NULL)", b"NOT NULL", b" "),
        (5, "NOT NULL -> omitted (IDENTITY implies NOT NULL)", b"NOT NULL", b" "),
        (6, "NOT NULL -> omitted (PRIMARY KEY implies NOT NULL)", b"NOT NULL", b" "),
        (7, "NOT NULL -> NULL", b"NOT NULL", b"NULL"),
        (11, "NOT NULL -> NULL", b"NOT NULL", b"NULL"),
        (12, "NOT NULL -> omitted (SERIAL implies NOT NULL)", b"NOT NULL", b" "),
        (13, "NOT NULL -> NULL", b"NOT NULL", b"NULL"),
        (14, "SET NOT NULL -> DROP NOT NULL", b"SET NOT NULL", b"DROP NOT NULL"),
    ]


def test_implicit_not_null_reason_add_column_if_without_exists():
    clause = b"ADD COLUMN IF x serial NOT NULL"
    src, masked = _mk(clause.decode())
    assert S._implicit_not_null_reason(src, masked, 0, clause) is None
    clause = b"ADD COLUMN 1 NOT NULL"
    src, masked = _mk(clause.decode())
    assert S._implicit_not_null_reason(src, masked, 0, clause) is None
    assert S._implicit_not_null_reason(src, masked, 0, None) is None


# ----------------------------------------------- foreign key / delete action


@pytest.mark.parametrize(
    "text,expected",
    [
        ("FOREIGN x", []),
        ("FOREIGN KEY x", []),
        ("FOREIGN KEY (a) x", []),
        ("FOREIGN KEY (a REFERENCES p", [("sql:drop-foreign-key", b"REFERENCES p", b"CHECK (true)")]),
        ("FOREIGN KEY (a) REFERENCES (b)", []),
        (
            "FOREIGN KEY (a) REFERENCES p (id)",
            [("sql:drop-foreign-key", b"FOREIGN KEY (a) REFERENCES p (id)", b"CHECK (true)")],
        ),
        ("GRANT REFERENCES ON t TO u;", []),
        ("REVOKE REFERENCES ON t FROM u;", []),
        ("CREATE TABLE q (a int REFERENCES (b));", []),
        (
            "CREATE TABLE q (a int REFERENCES p);",
            [("sql:drop-foreign-key", b"REFERENCES p", b"CHECK (true)")],
        ),
    ],
)
def test_scan_drop_foreign_key(text, expected):
    assert _sites(S._scan_drop_foreign_key, text) == expected
    calls, cb = _counter()
    assert _sites(S._scan_drop_foreign_key, text, check_deadline=cb) == expected
    assert calls


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ON DELETE;", []),
        ("ON DELETE NO FOO", []),
        ("ON DELETE SET NULL", []),
        ("ON UPDATE RESTRICT", []),
        ("ON DELETEX RESTRICT", []),
        ("ON DELETE RESTRICT", [("sql:weaken-delete-action", b"RESTRICT", b"CASCADE")]),
        ("ON DELETE  NO  ACTION", [("sql:weaken-delete-action", b"NO  ACTION", b"CASCADE")]),
    ],
)
def test_scan_weaken_delete_action(text, expected):
    assert _sites(S._scan_weaken_delete_action, text) == expected
    calls, cb = _counter()
    assert _sites(S._scan_weaken_delete_action, text, check_deadline=cb) == expected
    assert calls


# ------------------------------------------------------------- widen-check-in


def _widen(text, **kw):
    src = text.encode()
    lexed = L.lex_sql(src)
    return [
        (src[s.start : s.end], s.replacement)
        for s in S._scan_widen_check_in(src, lexed.mask, **kw)
    ]


def test_widen_check_in_outside_any_table_and_unreadable_alter_is_inert():
    assert _widen("CHECK (a IN (1, 2));") == []
    assert _widen("CHECK (a IN ('x', 'y'));") == []
    calls, cb = _counter()
    assert _widen("ALTER TABLE ; CREATE TABLE t (a int CHECK (a IN (1, 2)));", check_deadline=cb) == [
        (b")", b", 3)")
    ]
    assert calls


def test_widen_check_in_inline_check_on_altered_but_undeclared_column():
    text = "CREATE TABLE t (a int CHECK (a IN (1, 2))); ALTER TABLE t ALTER z TYPE text;"
    assert _widen(text) == [(b")", b", 3)")]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CREATE TABLE t (a int, CHECK (a NOT IN (1)));", []),
        ("CREATE TABLE t (CHECK (zz NOT IN (1, 2)));", []),
        ("CREATE TABLE t (a smallint, CHECK (a NOT IN (1, 99999)));", []),
        ("CREATE TABLE t (a text, CHECK (a NOT IN (1, 2)));", []),
        ("ALTER TABLE t ADD x int; SELECT 1 WHERE CHECK (a IN (1, 2));", []),
        ("CREATE TABLE t (a smallint, CHECK (a NOT IN (1, 2)));", [(b"2", b"1")]),
        ("CREATE TABLE t (a smallint, CHECK ((a) IN (1, 2)));", []),
        ("CREATE TABLE t (a text, CHECK (a IN (1, 2)));", []),
        ("CREATE TABLE t (a smallint, CHECK (a IN (1, 99999)));", []),
        ("CREATE TABLE t (a smallint, CHECK (a IN (1, 2)));", [(b")", b", 3)")]),
        ("CREATE TABLE t (a smallint, CHECK (a IN (32767)));", [(b")", b", -32768)")]),
        ("CREATE TABLE t (a text, CHECK (a IN ('x', 'y')));", [(b")", b", '__assay_widened__')")]),
        ("CREATE TABLE t (a text, CHECK (a IN (1, 'y')));", []),
        ("CREATE TABLE t (a text, CHECK (a IN ()));", []),
    ],
)
def test_widen_check_in_branches(text, expected):
    assert _widen(text) == expected
    assert _widen(text, check_deadline=lambda: None) == expected


def test_configuration_ranges_skip_ddl_outside_every_statement_range(monkeypatch):
    src, masked = _mk("SELECT 1; ALTER ROLE r SET x = 1;")
    monkeypatch.setattr(S, "_sql_statement_ranges", lambda *a, **k: ((0, 9),))
    assert S._non_session_configuration_ranges(src, masked) == ()


def test_unknown_transaction_event_value_leaves_state_untouched(monkeypatch):
    src, masked = _mk("CREATE SCHEMA s; SET search_path = s, pg_temp; SELECT 1;")
    base = S._search_path_temporary_creation_events(src, masked)
    monkeypatch.setattr(S, "_transaction_control_events", lambda *a, **k: ((50, "bogus"),))
    got = S._search_path_temporary_creation_events(src, masked)
    assert [state for _, state in got][-1] == [state for _, state in base][-1] is False
    assert any(offset == 50 for offset, _ in got)


def test_unknown_schema_state_event_kind_is_rejected(monkeypatch):
    src, masked = _mk("CREATE SCHEMA s; SELECT 1;")
    monkeypatch.setattr(
        S,
        "_search_path_state_events",
        lambda *args, **kwargs: [(50, "future-kind", None)],
    )

    with pytest.raises(ValueError, match="unknown SQL schema-state event kind 'future-kind'"):
        S._search_path_temporary_creation_events(src, masked)


def test_view_references_skip_a_letter_the_source_does_not_read_as_identifier():
    # mask and source disagree on the byte after the first relation: the
    # scanner must not guess a relation name from the mask alone
    masked = b"SELECT 1 FROM a b"
    source = b"SELECT 1 FROM a '"
    assert S._view_relation_references(source, masked, 0, len(source)) == frozenset({(None, b"a")})
