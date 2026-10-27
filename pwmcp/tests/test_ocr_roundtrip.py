"""PWMCP-11: the client library against the REAL sidecar service (in process, fake tesseract).

Every other client test uses a canned-JSON fake, which can drift from what the service really sends.
This one asserts the full field set end to end, so a contract change on either side fails here.
"""

from __future__ import annotations

import pytest
from test_ocr_service import Fake, Running, fake, needs_pil, png, start  # noqa: F401

from pwmcp_client import ocr


@pytest.fixture
def sidecar(fake):
    server = start(fake)
    yield f"http://127.0.0.1:{server.port}"
    server.close()


@needs_pil
def test_recognize_round_trips_the_full_field_set_with_a_threshold(sidecar: str) -> None:
    result = ocr.recognize(png(100, 50), sidecar_url=sidecar, min_conf=90, scale=2, psm=7, lang="eng+osd")
    assert (result.engine, result.engine_version, result.lang, result.psm) == ("tesseract", "5.3.4", "eng+osd", 7)
    assert (result.width, result.height) == (100, 50) and result.elapsed_ms >= 0
    assert result.text == "Debian installer\nmaybe"  # COMPLETE: the 88.25 and 42.0 words are flagged, not removed
    assert [(w.text, w.conf, w.box, w.line, w.low_conf) for w in result.words] == [
        ("Debian", 96.5, (5, 5, 40, 8), 0, False),
        ("installer", 88.25, (50, 5, 45, 8), 0, True),
        ("maybe", 42.0, (5, 17, 30, 8), 1, True),
    ]
    assert [(l.text, l.conf, l.conf_mean, l.conf_min, l.low_conf_words, l.box) for l in result.lines] == [
        ("Debian installer", 92.38, 92.38, 88.25, 1, (5, 5, 90, 8)),
        ("maybe", 42.0, 42.0, 42.0, 1, (5, 17, 30, 8)),
    ]
    assert (result.low_conf_words, result.min_conf_applied, result.unscored_words) == (2, 90.0, 0)
    assert (result.drop_low_conf, result.dropped_words, result.completeness_unknown) == (False, 0, False)
    assert result.to_tsv().splitlines()[2].split("\t") == ["0", "50", "5", "45", "8", "88.25", "1", "installer"]


@needs_pil
def test_recognize_round_trips_nulls_when_no_threshold_is_given(sidecar: str) -> None:
    result = ocr.recognize(png(), sidecar_url=sidecar)
    assert result.low_conf_words is None and result.min_conf_applied is None
    assert all(w.low_conf is None for w in result.words) and all(l.low_conf_words is None for l in result.lines)
    assert result.completeness_unknown is False  # the key is present (null), so the sidecar knows the contract
    assert all(row.split("\t")[6] == "" for row in result.to_tsv().splitlines()[1:])  # empty, not "0"
    assert result.to_dict()["low_conf_words"] is None


@needs_pil
def test_recognize_round_trips_drop_low_conf(sidecar: str) -> None:
    result = ocr.recognize(png(), sidecar_url=sidecar, min_conf=90, drop_low_conf=True)
    assert result.text == "Debian" and result.dropped_words == 2 and result.drop_low_conf is True
    with pytest.raises(ocr.OcrError) as caught:
        ocr.recognize(png(), sidecar_url=sidecar, drop_low_conf=True)  # a drop needs a threshold
    assert caught.value.code == "drop_needs_min_conf"
