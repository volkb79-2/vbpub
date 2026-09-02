"""
Tests for ``ciu.deploy_pkg.phases.service_health_timeout`` (CIU-QOL-8 / O1)
and ``ciu.deploy_pkg.phases.service_one_shot`` (ciu-P23, V8-PREP-5 / O3).

Both accessors mirror ``service_shipped``/``service_health_enabled``'s exact
validation pattern: absent -> a fixed default; a bool is returned verbatim
(or a string, for ``service_health_timeout`` — duration parsing is the
CALLER's job, via the existing ``deploy._seconds()`` helper, this accessor
never parses); any other type -> a tagged ``[S7.2]`` ValueError.

NOTE: as of this package, ``resolve_selection_health_containers`` does not
yet call ``service_health_timeout`` (see this package's LOG — the deploy.py
wiring (O3) is BLOCKED because it would break several existing tests outside
this package's ``scope.touch``). Likewise, ``service_one_shot`` (ciu-P23) is
DECLARATION + shape validation only: `deploy.py`'s post-up wait loop is not
wired to it (out of scope, forbidden file — see ciu-P23's LOG). This file
tests both accessors themselves, in isolation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg.phases import service_health_timeout, service_one_shot  # noqa: E402


class TestServiceHealthTimeout:
    def test_absent_defaults_none(self):
        assert service_health_timeout({"path": "infra/api"}) is None

    def test_string_duration_returned_verbatim_unparsed(self):
        assert service_health_timeout({"health_timeout": "300s"}) == "300s"
        assert service_health_timeout({"health_timeout": "5s"}) == "5s"

    @pytest.mark.parametrize("bad", [300, 300.0, True, False, [], {}, None])
    def test_non_string_present_aborts(self, bad):
        # None is handled by the "absent" branch above (service.get default),
        # so an EXPLICIT None value must be indistinguishable from absent —
        # confirmed separately below. Every other non-str type must raise.
        if bad is None:
            assert service_health_timeout({"health_timeout": None}) is None
            return
        with pytest.raises(ValueError, match=r"\[S7\.2\].*health_timeout.*string duration"):
            service_health_timeout({"health_timeout": bad})

    def test_non_string_error_names_the_bad_type(self):
        with pytest.raises(ValueError, match=r"got int 300"):
            service_health_timeout({"health_timeout": 300})


class TestServiceOneShot:
    def test_absent_defaults_false(self):
        assert service_one_shot({"path": "infra/db-init"}) is False

    def test_true_returned_verbatim(self):
        assert service_one_shot({"one_shot": True}) is True

    def test_false_returned_verbatim(self):
        assert service_one_shot({"one_shot": False}) is False

    @pytest.mark.parametrize("bad", [1, 0, "true", 1.0, [], {}, None])
    def test_non_bool_present_aborts(self, bad):
        # An explicit None is NOT the same as absent for `dict.get` with a
        # default (unlike service_health_timeout's None-means-absent
        # special case) -- one_shot mirrors service_shipped/health exactly,
        # so every non-bool, INCLUDING an explicit None, must raise.
        with pytest.raises(ValueError, match=r"\[S7\.2\].*one_shot.*bool"):
            service_one_shot({"one_shot": bad})

    def test_non_bool_error_names_the_bad_type(self):
        with pytest.raises(ValueError, match=r"got str 'yes'"):
            service_one_shot({"one_shot": "yes"})
