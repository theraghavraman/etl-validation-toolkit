"""All network calls are intercepted by respx; no live API dependencies."""

from __future__ import annotations

import httpx
import pytest

from etlguard.config import PaginationSettings, SourceSettings
from etlguard.sources.api import ApiSource, SourceError, dig

respx = pytest.importorskip("respx")


def test_nested_json_path():
    assert dig({"data": {"items": [1, 2]}}, "data.items") == [1, 2]
    with pytest.raises(SourceError, match="Missing"):
        dig({}, "data.items")


@respx.mock
def test_page_pagination():
    route = respx.get("https://api.example.test/items")
    route.side_effect = [
        httpx.Response(200, json={"data": {"items": [{"id": 1}, {"id": 2}]}}),
        httpx.Response(200, json={"data": {"items": [{"id": 3}]}}),
    ]
    settings = SourceSettings(
        type="api", url="https://api.example.test/items", records_path="data.items",
        pagination=PaginationSettings(mode="page", page_size=2),
    )
    records = list(ApiSource(settings, sleeper=lambda _: None).read("batch"))
    assert [record.data["id"] for record in records] == [1, 2, 3]
    assert [record.lineage.source_row_number for record in records] == [1, 2, 3]
    assert route.call_count == 2
    assert "page=1" in str(route.calls[0].request.url)


@respx.mock
def test_offset_pagination():
    route = respx.get("https://api.example.test/items")
    route.side_effect = [
        httpx.Response(200, json=[{"id": 1}]),
        httpx.Response(200, json=[]),
    ]
    cfg = SourceSettings(type="api", url="https://api.example.test/items",
                         pagination=PaginationSettings(mode="offset", page_size=1, start=0))
    assert len(list(ApiSource(cfg, sleeper=lambda _: None).read("x"))) == 1
    assert "offset=1" in str(route.calls[1].request.url)


@respx.mock
def test_next_link_and_bearer_env(monkeypatch):
    monkeypatch.setenv("ETLGUARD_SAMPLE_TOKEN", "test-only")
    first = respx.get("https://api.example.test/items").mock(return_value=httpx.Response(
        200, json={"data": {"items": [{"id": 1}]}, "links": {"next": "/other"}}))
    respx.get("https://api.example.test/other").mock(return_value=httpx.Response(
        200, json={"data": {"items": [{"id": 2}]}, "links": {"next": None}}))
    cfg = SourceSettings(type="api", url="https://api.example.test/items",
                         records_path="data.items", bearer_token_env="ETLGUARD_SAMPLE_TOKEN",
                         pagination=PaginationSettings(mode="next"))
    rows = list(ApiSource(cfg, sleeper=lambda _: None).read("b"))
    assert len(rows) == 2
    assert first.calls[0].request.headers["Authorization"] == "Bearer test-only"


@respx.mock
def test_500_retry_then_success():
    route = respx.get("https://api.example.test/items")
    route.side_effect = [httpx.Response(500), httpx.Response(502), httpx.Response(200, json=[])]
    sleep_calls = []
    cfg = SourceSettings(type="api", url="https://api.example.test/items",
                         max_retries=2, backoff_seconds=0.1)
    assert list(ApiSource(cfg, sleeper=sleep_calls.append).read("b")) == []
    assert sleep_calls == [0.1, 0.2]
    assert route.call_count == 3


@respx.mock
def test_retries_exhausted_and_nonretryable_400():
    route = respx.get("https://api.example.test/items").mock(return_value=httpx.Response(500))
    cfg = SourceSettings(type="api", url="https://api.example.test/items", max_retries=1)
    with pytest.raises(SourceError, match="request failed"):
        list(ApiSource(cfg, sleeper=lambda _: None).read("b"))
    assert route.call_count == 2
    route.mock(return_value=httpx.Response(400))
    with pytest.raises(SourceError):
        list(ApiSource(cfg, sleeper=lambda _: None).read("b"))
    assert route.call_count == 3


@respx.mock
def test_next_link_cross_origin_blocked():
    respx.get("https://api.example.test/items").mock(return_value=httpx.Response(
        200, json={"items": [], "links": {"next": "https://evil.example/steal"}}))
    cfg = SourceSettings(type="api", url="https://api.example.test/items",
                         records_path="items", pagination=PaginationSettings(mode="next"))
    with pytest.raises(SourceError, match="changed origin"):
        list(ApiSource(cfg, sleeper=lambda _: None).read("b"))


def test_missing_token(monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST_TOKEN", raising=False)
    cfg = SourceSettings(type="api", url="https://api.example.test/items",
                         bearer_token_env="DOES_NOT_EXIST_TOKEN")
    with pytest.raises(SourceError, match="Missing API bearer"):
        list(ApiSource(cfg).read("b"))
