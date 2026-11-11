"""GET JSON connector: retries, bearer auth, page/offset/next-link pagination."""

from __future__ import annotations

import os
import time
from contextlib import nullcontext
from typing import Any, Callable, Iterator
from urllib.parse import urljoin, urlparse

import httpx

from etlguard.config import SourceSettings
from etlguard.sources.base import SourceRecord, lineage


class SourceError(RuntimeError):
    pass


def dig(data: Any, path: str) -> Any:
    current = data
    for part in filter(None, path.split(".")):
        if not isinstance(current, dict) or part not in current:
            raise SourceError(f"Missing JSON path: {path}")
        current = current[part]
    return current


class ApiSource:
    def __init__(
        self,
        settings: SourceSettings,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if settings.url is None:
            raise ValueError("API source missing URL")
        if urlparse(settings.url).scheme not in {"http", "https"}:
            raise ValueError("API URL must use http or https")
        self.settings = settings
        self.client = client
        self.sleeper = sleeper

    def _get(self, client: httpx.Client, url: str, params: dict[str, Any], headers: dict[str, str]) -> Any:
        cfg = self.settings
        for attempt in range(cfg.max_retries + 1):
            try:
                response = client.get(url, params=params, headers=headers, timeout=cfg.timeout_seconds)
                response.raise_for_status()
                return response.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.RequestError) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {429, 500, 502, 503, 504}
                )
                if not retryable or attempt >= cfg.max_retries:
                    raise SourceError(f"API request failed ({type(exc).__name__})") from exc
                self.sleeper(cfg.backoff_seconds * (2 ** attempt))
            except ValueError as exc:
                raise SourceError("API returned invalid JSON") from exc
        raise SourceError("unreachable API retry state")  # pragma: no cover

    def read(self, batch_id: str) -> Iterator[SourceRecord]:
        cfg = self.settings
        assert cfg.url is not None
        headers: dict[str, str] = {}
        if cfg.bearer_token_env:
            token = os.getenv(cfg.bearer_token_env)
            if not token:
                raise SourceError(f"Missing API bearer token env: {cfg.bearer_token_env}")
            headers["Authorization"] = f"Bearer {token}"
        page_cfg = cfg.pagination
        url = cfg.url
        params: dict[str, Any] = {}
        row_number = 0
        seen_urls: set[str] = set()
        ctx = nullcontext(self.client) if self.client is not None else httpx.Client()
        with ctx as client:
            assert client is not None
            for page_index in range(page_cfg.max_pages):
                if page_cfg.mode == "page":
                    params = {
                        page_cfg.page_param: page_cfg.start + page_index,
                        page_cfg.limit_param: page_cfg.page_size,
                    }
                elif page_cfg.mode == "offset":
                    params = {
                        page_cfg.offset_param: page_cfg.start + page_index * page_cfg.page_size,
                        page_cfg.limit_param: page_cfg.page_size,
                    }
                doc = self._get(client, url, params, headers)
                items = dig(doc, cfg.records_path)
                if not isinstance(items, list):
                    raise SourceError("records_path must point to a JSON array")
                for item in items:
                    if not isinstance(item, dict):
                        raise SourceError("API array entries must be JSON objects")
                    row_number += 1
                    yield SourceRecord(item, lineage(cfg.name or cfg.url, row_number, batch_id))
                if page_cfg.mode == "none":
                    return
                if page_cfg.mode in {"page", "offset"}:
                    if len(items) < page_cfg.page_size:
                        return
                    continue
                # Next-link style; missing/null/empty next is the natural end.
                try:
                    next_url = dig(doc, page_cfg.next_path)
                except SourceError:
                    return
                if not next_url:
                    return
                if not isinstance(next_url, str):
                    raise SourceError("next-link must be a string URL")
                target = urljoin(url, next_url)
                origin = urlparse(cfg.url)
                destination = urlparse(target)
                if (origin.scheme, origin.netloc) != (destination.scheme, destination.netloc):
                    raise SourceError("next-link changed origin; refusing to forward credentials")
                if target in seen_urls or target == url:
                    raise SourceError("API pagination loop detected")
                seen_urls.add(url)
                url = target
                params = {}
            raise SourceError(f"API exceeded max_pages={page_cfg.max_pages}")
