"""Steam 공개 프로필 입력 파싱과 라이브러리 조회 어댑터 (library_policy v2.0).

- 허용 입력: SteamID64(17자리) · https://steamcommunity.com/profiles/<id> · https://steamcommunity.com/id/<vanity>.
  사용자가 준 URL을 그대로 fetch하지 않고, 파싱한 값으로 공식 Web API만 부른다.
- 운영자 API 키(환경변수 STEAM_WEB_API_KEY)로 호출한다. 사용자에게 키·비밀번호·쿠키를 받지 않는다.
- 계정 소유 증명이 아니다(공개 정보 입력 방식). OpenID 로그인은 범위 밖.
- 로그·오류 메시지에 키와 SteamID 원문을 남기지 않는다.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

API = "https://api.steampowered.com"
TIMEOUT_S = 20.0
BACKOFF_S = (5, 15, 45)
STEAMID_RE = re.compile(r"^7656119\d{10}$")
VANITY_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}$")
ALLOWED_HOSTS = {"steamcommunity.com", "www.steamcommunity.com"}


class LibraryError(Exception):
    """사용자에게 보여줄 수 있는 분류 코드만 담는다."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_identity(raw: str) -> tuple[str, str]:
    """('steamid', id) 또는 ('vanity', name). 그 밖은 LibraryError('invalid_input')."""
    s = (raw or "").strip()
    if len(s) > 200:
        raise LibraryError("invalid_input")
    if STEAMID_RE.match(s):
        return "steamid", s
    if "://" not in s and s.startswith(("steamcommunity.com/", "www.steamcommunity.com/")):
        s = "https://" + s
    try:
        u = urlparse(s)
    except ValueError:
        raise LibraryError("invalid_input")
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in ALLOWED_HOSTS or u.port:
        raise LibraryError("invalid_input")
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "profiles" and STEAMID_RE.match(parts[1]):
        return "steamid", parts[1]
    if len(parts) >= 2 and parts[0] == "id" and VANITY_RE.match(parts[1]):
        return "vanity", parts[1]
    raise LibraryError("invalid_input")


@dataclass
class OwnedResult:
    status: str  # ok / empty / private_or_unavailable
    games: list[dict]  # appid, name, minutes(None 가능), minutes_2w(None 가능), info_status
    fetched_at: str


class SteamWebAPI:
    def __init__(self, key: str | None = None, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.key = key if key is not None else os.environ.get("STEAM_WEB_API_KEY")
        self._client = httpx.Client(timeout=TIMEOUT_S, transport=transport)
        self._sleep = sleep

    def _get(self, path: str, params: dict) -> dict:
        if not self.key:
            raise LibraryError("no_api_key")
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self._client.get(f"{API}/{path}", params={**params, "key": self.key, "format": "json"})
            except httpx.TimeoutException:
                code, retry_after = "timeout", None
            except httpx.HTTPError:
                code, retry_after = "upstream_error", None
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError:
                        raise LibraryError("upstream_error")
                if r.status_code in (401, 403):
                    raise LibraryError("api_key_rejected")
                if r.status_code == 400:
                    raise LibraryError("invalid_input")
                code = "rate_limited" if r.status_code == 429 else "upstream_error"
                retry_after = r.headers.get("Retry-After")
                if r.status_code < 500 and r.status_code != 429:
                    raise LibraryError(code)
            if attempt > len(BACKOFF_S):
                raise LibraryError(code)
            self._sleep(int(retry_after) if retry_after and retry_after.isdigit() else BACKOFF_S[attempt - 1])

    def resolve_vanity(self, vanity: str) -> str:
        d = self._get("ISteamUser/ResolveVanityURL/v1/", {"vanityurl": vanity, "url_type": 1})
        resp = d.get("response") or {}
        if resp.get("success") == 1 and STEAMID_RE.match(str(resp.get("steamid", ""))):
            return str(resp["steamid"])
        raise LibraryError("vanity_not_found")

    def owned_games(self, steamid: str) -> OwnedResult:
        d = self._get("IPlayerService/GetOwnedGames/v1/",
                      {"steamid": steamid, "include_appinfo": 1, "include_played_free_games": 1})
        resp = d.get("response")
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        # 비공개 프로필·게임 목록 비공개는 빈 response({})로 온다. 원인을 단정하지 않는다.
        if not resp or "game_count" not in resp:
            return OwnedResult("private_or_unavailable", [], now)
        if resp.get("game_count", 0) == 0:
            return OwnedResult("empty", [], now)
        games = []
        for g in resp.get("games", []):
            pf = g.get("playtime_forever")
            games.append({"appid": int(g["appid"]), "name": g.get("name"),
                          "minutes": int(pf) if pf is not None else None,
                          "minutes_2w": int(g["playtime_2weeks"]) if g.get("playtime_2weeks") is not None else None,
                          "info_status": "ok" if pf is not None else "playtime_unknown"})
        return OwnedResult("ok", games, now)
