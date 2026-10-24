"""Steam 공개 API 클라이언트 (오프라인 수집 전용, 서비스 경로에서 import 금지).

기준 문서 9.1: 요청 간격 2초, 동시 1개, timeout 20초, 429/5xx 최대 3회 재시도,
Retry-After 우선, 없으면 5→15→45초. 401/403은 즉시 중지.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

API = "https://api.steampowered.com"
REQUEST_INTERVAL_S = 2.0
TIMEOUT_S = 20.0
BACKOFF_S = (5, 15, 45)
USER_AGENT = "gembti-collector/1.3 (offline catalog build)"


class SteamAuthStop(RuntimeError):
    """401/403: 수집 즉시 중지."""


@dataclass
class FetchResult:
    ok: bool
    status: int | None
    data: dict | None
    error: str | None
    attempts: int
    fetched_at: str


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class SteamClient:
    def __init__(self, interval: float = REQUEST_INTERVAL_S, sleep=time.sleep):
        self._client = httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
        self._interval = interval
        self._sleep = sleep
        self._last = 0.0

    def _pace(self) -> None:
        wait = self._last + self._interval - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def get_json(self, url: str, params: dict) -> FetchResult:
        attempts = 0
        last_err = None
        status = None
        while True:
            self._pace()
            attempts += 1
            try:
                r = self._client.get(url, params=params)
                status = r.status_code
            except httpx.HTTPError as e:  # 네트워크·timeout은 일시 오류로 취급
                last_err = f"{type(e).__name__}: {e}"
                status = None
                r = None
            if r is not None and status in (401, 403):
                raise SteamAuthStop(f"HTTP {status} from {url}")
            if r is not None and status == 200:
                try:
                    return FetchResult(True, status, r.json(), None, attempts, _now_iso())
                except ValueError as e:
                    last_err = f"invalid json: {e}"
                    return FetchResult(False, status, None, last_err, attempts, _now_iso())
            retryable = r is None or status == 429 or (status is not None and status >= 500)
            if r is not None and not retryable:
                return FetchResult(False, status, None, f"HTTP {status}", attempts, _now_iso())
            if r is not None:
                last_err = f"HTTP {status}"
            if attempts > len(BACKOFF_S):
                return FetchResult(False, status, None, last_err, attempts, _now_iso())
            delay = BACKOFF_S[attempts - 1]
            if r is not None and r.headers.get("Retry-After", "").isdigit():
                delay = int(r.headers["Retry-After"])
            self._sleep(delay)

    def get_tag_list(self, language: str) -> FetchResult:
        return self.get_json(f"{API}/IStoreService/GetTagList/v1/", {"language": language})

    def get_items(self, appids: list[int]) -> FetchResult:
        payload = {
            "ids": [{"appid": a} for a in appids],
            "context": {"language": "koreana", "country_code": "KR"},
            "data_request": {
                "include_basic_info": True,
                "include_assets": True,
                "include_tag_count": 20,
                "include_release": True,
                "include_platforms": True,
                "include_supported_languages": True,
                "include_reviews": True,
                "include_ratings": True,
            },
        }
        return self.get_json(f"{API}/IStoreBrowseService/GetItems/v1/",
                             {"input_json": json.dumps(payload, separators=(",", ":"))})
