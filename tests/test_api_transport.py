"""Additional real httpx adapter tests that run without respx or a network."""

from __future__ import annotations

import httpx
import pytest

from etlguard.config import PaginationSettings, SourceSettings
from etlguard.sources.api import ApiSource, SourceError


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_httpx_mock_page_pagination_and_lineage():
    def handler(request):
        assert request.method == "GET"
        page = request.url.params["page"]
        if page == "1":
            return httpx.Response(200, json={"data": {"items": [{"id": 1}, {"id": 2}]}})
        return httpx.Response(200, json={"data": {"items": [{"id": 3}]}})

    settings = SourceSettings(type="api", url="https://api.test/feed", records_path="data.items",
                              pagination=PaginationSettings(mode="page", page_size=2))
    records = list(ApiSource(settings, client=client_for(handler)).read("batch-abc"))
    assert [record.data["id"] for record in records] == [1, 2, 3]
    assert [record.lineage.source_row_number for record in records] == [1, 2, 3]
    assert all(record.lineage.batch_id == "batch-abc" for record in records)


def test_offset_pagination_httpx_mock():
    offsets = []

    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(200, json=[{"id": offset}] if offset == 0 else [])

    cfg = SourceSettings(type="api", url="https://api.test/feed",
                         pagination=PaginationSettings(mode="offset", page_size=1, start=0))
    assert len(list(ApiSource(cfg, client=client_for(handler)).read("id"))) == 1
    assert offsets == [0, 1]


def test_next_link_pagination_and_auth(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_FEED_TOKEN", "placeholder")
    urls = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer placeholder"
        urls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(200, json={"data": {"items": [{"id": 1}]},
                                             "links": {"next": "/next"}})
        return httpx.Response(200, json={"data": {"items": [{"id": 2}]},
                                         "links": {"next": None}})

    cfg = SourceSettings(type="api", url="https://api.test/start", records_path="data.items",
                         bearer_token_env="SYNTHETIC_FEED_TOKEN",
                         pagination=PaginationSettings(mode="next"))
    assert len(list(ApiSource(cfg, client=client_for(handler)).read("batch"))) == 2
    assert urls == ["https://api.test/start", "https://api.test/next"]


def test_httpx_mock_retry_and_backoff():
    attempts = []
    sleeps = []

    def handler(_request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500)
        return httpx.Response(200, json=[])

    cfg = SourceSettings(type="api", url="https://api.test/feed", max_retries=2,
                         backoff_seconds=0.05)
    assert list(ApiSource(cfg, client=client_for(handler), sleeper=sleeps.append).read("id")) == []
    assert len(attempts) == 3
    assert sleeps == [0.05, 0.1]


def test_api_failure_and_wrong_json_shapes():
    cfg = SourceSettings(type="api", url="https://api.test/feed", max_retries=0)
    bad_status = client_for(lambda request: httpx.Response(400))
    with pytest.raises(SourceError, match="request failed"):
        list(ApiSource(cfg, client=bad_status).read("id"))
    wrong_list = client_for(lambda request: httpx.Response(200, json={"not": "a list"}))
    with pytest.raises(SourceError, match="JSON array"):
        list(ApiSource(cfg, client=wrong_list).read("id"))
    wrong_row = client_for(lambda request: httpx.Response(200, json=[4]))
    with pytest.raises(SourceError, match="JSON objects"):
        list(ApiSource(cfg, client=wrong_row).read("id"))
    invalid_json = client_for(lambda request: httpx.Response(200, text="not-json"))
    with pytest.raises(SourceError, match="invalid JSON"):
        list(ApiSource(cfg, client=invalid_json).read("id"))


def test_api_retries_exhausted_and_network_failure():
    cfg = SourceSettings(type="api", url="https://api.test/feed", max_retries=1,
                         backoff_seconds=0)
    bad = client_for(lambda request: httpx.Response(503))
    with pytest.raises(SourceError, match="request failed"):
        list(ApiSource(cfg, client=bad, sleeper=lambda _: None).read("id"))

    def disconnected(request):
        raise httpx.ConnectError("offline", request=request)

    offline = client_for(disconnected)
    with pytest.raises(SourceError, match="request failed"):
        list(ApiSource(cfg, client=offline, sleeper=lambda _: None).read("id"))


def test_bad_next_link_loop_wrong_origin_and_max_pages():
    base = dict(type="api", url="https://api.test/feed", records_path="items")
    same = client_for(lambda request: httpx.Response(200, json={
        "items": [], "links": {"next": "/feed"}}))
    cfg = SourceSettings(**base, pagination=PaginationSettings(mode="next"))
    with pytest.raises(SourceError, match="loop"):
        list(ApiSource(cfg, client=same).read("id"))
    other = client_for(lambda request: httpx.Response(200, json={
        "items": [], "links": {"next": "https://other.test/private"}}))
    with pytest.raises(SourceError, match="origin"):
        list(ApiSource(cfg, client=other).read("id"))
    cfg = SourceSettings(**base, pagination=PaginationSettings(mode="page", max_pages=2, page_size=1))
    endless = client_for(lambda request: httpx.Response(200, json={"items": [{"id": 1}]}))
    with pytest.raises(SourceError, match="max_pages"):
        list(ApiSource(cfg, client=endless).read("id"))


def test_missing_token_next_link_non_string_and_api_url_validation(monkeypatch):
    monkeypatch.delenv("NO_TOKEN_IN_TEST", raising=False)
    cfg = SourceSettings(type="api", url="https://api.test/feed", bearer_token_env="NO_TOKEN_IN_TEST")
    with pytest.raises(SourceError, match="bearer"):
        list(ApiSource(cfg).read("id"))
    cfg = SourceSettings(type="api", url="https://api.test/feed", records_path="items",
                         pagination=PaginationSettings(mode="next"))
    bad = client_for(lambda request: httpx.Response(200, json={
        "links": {"next": 123}, "items": []}))
    with pytest.raises(SourceError, match="next-link"):
        list(ApiSource(cfg, client=bad).read("id"))
    with pytest.raises(ValueError, match="http"):
        ApiSource(SourceSettings(type="api", url="file:///etc/passwd"))
