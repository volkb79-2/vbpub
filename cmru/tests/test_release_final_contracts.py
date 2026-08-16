"""Exact witnesses for release REST request and pagination contracts."""
from __future__ import annotations

import json

import pytest

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


def test_release_publish_rejects_response_without_upload_coordinate():
    client = GitHubReleases("owner", "repo", "token")
    client.get_release_by_tag = lambda tag: {"id": 7}
    with pytest.raises(SystemExit) as error:
        client.publish("demo-v1", "title", "notes", [])
    assert error.value.code == 1
