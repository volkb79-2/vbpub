"""P102 — Close query validation coverage tranche (22 lines, 20 arcs).

Each test calls Query.from_dict with a specific invalid input and asserts
the exact typed exception and message. Targets only the declared line/branch
set; P103 owns remaining engine.py gaps.
"""

from __future__ import annotations

import pytest
from topos.query import Query, InvalidQueryError, UnknownFieldError, MetricRef, SortSpec
from topos.query.engine import _validate, Caps, _parse_metric_token


class TestQueryFromDict:
    """Cover every validation branch in Query.from_dict."""

    def test_from_dict_non_dict(self):
        """line 138: non-dict input raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="must be a mapping"):
            Query.from_dict("not_a_dict")

    def test_from_dict_missing_shape(self):
        """line 143: missing 'shape' raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="requires a 'shape'"):
            Query.from_dict({"metrics": []})

    def test_from_dict_metric_spec_unknown_field(self):
        """line 151: metric spec with extra field raises UnknownFieldError."""
        with pytest.raises(UnknownFieldError, match="unknown metric field"):
            Query.from_dict({"shape": "summary", "metrics": [{"name": "ram", "unknown_key": 1}]})

    def test_from_dict_metric_spec_no_name(self):
        """line 153: metric spec without 'name' raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="requires a 'name'"):
            Query.from_dict({"shape": "summary", "metrics": [{"semantic": "rate"}]})

    def test_from_dict_metric_spec_invalid_type(self):
        """line 156: non-str, non-dict metric raises InvalidQueryError (arc 148->156)."""
        with pytest.raises(InvalidQueryError, match="invalid metric spec"):
            Query.from_dict({"shape": "summary", "metrics": [42]})

    def test_from_dict_selector_not_dict(self):
        """line 159: non-dict selector raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="selector must be a mapping"):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "selector": "bad"})

    def test_from_dict_sort_as_dict(self):
        """line 173: sort as dict builds Query with sort.metric=='ram'."""
        q = Query.from_dict({"shape": "summary", "metrics": ["ram"], "sort": {"metric": "ram", "order": "asc"}})
        assert q.sort is not None
        assert q.sort.metric == "ram"

    def test_from_dict_sort_extra_field(self):
        """line 176: sort dict with unknown field raises UnknownFieldError."""
        with pytest.raises(UnknownFieldError, match="unknown sort field"):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "sort": {"metric": "ram", "unknown": 1}})

    def test_from_dict_sort_no_metric(self):
        """line 178: sort dict without 'metric' raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="sort spec requires a 'metric'"):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "sort": {"order": "asc"}})

    def test_from_dict_sort_invalid_type(self):
        """line 185: non-str, non-dict sort raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="invalid sort spec"):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "sort": 42})

    def test_from_dict_caps_not_dict(self):
        """line 188: non-dict caps raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="caps must be a mapping"):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "caps": "bad"})


class TestValidationPaths:
    """Cover validation branches in _as_int, _parse_metric_token, etc."""

    def _make_query(self, **overrides) -> Query:
        defaults = dict(shape="summary", metrics=(MetricRef(name="ram"),),
                        window_spec="all", selector=None, projection="flat",
                        visibility="all", sort=None,
                        caps=Caps(100, 1000, 100000, "error"))
        defaults.update(overrides)
        return Query(**defaults)

    def test_as_int_rejects_bool(self):
        """line 212: _as_int rejects bool."""
        with pytest.raises(InvalidQueryError, match="must be an integer"):
            Query.from_dict({"shape": "summary", "metrics": ["ram"], "caps": {"max_rows": True}})

    def test_parse_metric_token_colon(self):
        """lines 219-220: _parse_metric_token parses name:semantic."""
        mr = _parse_metric_token("ram:rate")
        assert mr.name == "ram"
        assert mr.semantic == "rate"

    def test_visibility_unknown(self):
        """line 256: unknown visibility raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="unknown visibility"):
            _validate(self._make_query(visibility="bad"))

    def test_on_exceed_unknown(self):
        """line 260: unknown caps.on_exceed raises InvalidQueryError."""
        cap = Caps(100, 1000, 100000, "bad")
        with pytest.raises(InvalidQueryError, match="unknown caps.on_exceed"):
            _validate(self._make_query(caps=cap))

    def test_caps_negative_int(self):
        """line 269: negative max_rows raises InvalidQueryError."""
        cap = Caps(-1, 1000, 100000, "error")
        with pytest.raises(InvalidQueryError, match="non-negative integer"):
            _validate(self._make_query(caps=cap))

    def test_sort_order_unknown(self):
        """line 308: unknown sort order raises InvalidQueryError."""
        with pytest.raises(InvalidQueryError, match="unknown sort order"):
            _validate(self._make_query(sort=SortSpec(metric="ram", stat=None, order="bad")))


class TestMixedBranches:
    """Additional branches that need specific conditions."""

    def test_from_dict_sort_str_branch(self):
        """line 172: sort as str builds SortSpec with metric='ram'."""
        q = Query.from_dict({"shape": "summary", "metrics": ["ram"], "sort": "ram"})
        assert q.sort is not None
        assert q.sort.metric == "ram"
        assert q.sort.order == "desc"
