"""Exact witnesses for release REST request and pagination contracts."""
from __future__ import annotations

import json

import pytest

from cmru import release as release_module
from cmru.release import GitHubReleases


def test_release_create_includes_explicit_target_commitish_in_json_payload():
    client = GitHubReleases("owner", "repo", "token")
    seen = {}
    def request(method, url, data=None, content_type=None):
        seen.update(method=method, url=url, data=data, content_type=content_type)
        return 201, '{"id":7}'
    client._request = request
    assert client.create_release("demo-v1", "Demo 1", "notes", "abc123") == {"id": 7}
    payload = json.loads(seen["data"])
    assert seen["method"] == "POST" and payload["target_commitish"] == "abc123"


def test_release_list_paginates_until_short_page():
    client = GitHubReleases("owner", "repo", "token")
    pages = iter([
        (200, json.dumps([{"id": 1}, {"id": 2}])),
        (200, json.dumps([{"id": 3}])),
    ])
    client._request = lambda *args, **kwargs: next(pages)
    assert client.list_releases(per_page=2) == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_release_publish_rejects_response_without_upload_coordinate(monkeypatch, capsys):
    """The refusal is the release RECORD, not a transport answer, and no socket is opened.

    Stubbing only ``get_release_by_tag`` left the update PATCH going out to
    api.github.com, so this test needed the network to reach its assertion (it
    failed with a transport error instead on a network-less gate host). The
    transport boundary is ``cmru.release.urlopen``: faking it here lets the real
    ``_request`` build its request (headers, method) while proving that nothing
    leaves the process.
    """
    client = GitHubReleases("owner", "repo", "token")
    client.get_release_by_tag = lambda tag: {"id": 7}  # a record with no upload_url

    sent = []

    class _Response:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"id": 7}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(request, *args, **kwargs):
        sent.append(request)
        return _Response()

    monkeypatch.setattr(release_module, "urlopen", urlopen)

    with pytest.raises(SystemExit) as error:
        client.publish("demo-v1", "title", "notes", [])
    assert error.value.code == 1

    # It refused the record it read, after exactly the one metadata PATCH it needed
    # to reach that check, and every request it built stayed inside the stub.
    assert [request.get_method() for request in sent] == ["PATCH"]
    assert all(request.full_url.startswith("https://api.github.com/") for request in sent)
    assert "missing id/upload_url" in capsys.readouterr().err
