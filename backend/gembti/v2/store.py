"""v2 세션·라이브러리 연결 저장, 캐시, 만료 삭제 (library_policy v2.0).

- SteamID 원문은 해당 세션의 v2_library_links 행에만 둔다(백업 제외, 마지막 연결 후 7일 뒤 삭제).
- 서버 라이브러리 캐시는 SteamID 해시 키로 24시간. 게임 태그 메타 캐시는 개인 식별 없이 공유, 7일 신선도.
- 연결 해제 시 연결·소유 목록·파생 프로필·해당 캐시를 지운다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

from gembti.service import KST, now_iso

LIBRARY_RETENTION_DAYS = 7
LIBRARY_CACHE_HOURS = 24
META_FRESH_DAYS = 7
_SALT = os.environ.get("GEMBTI_ID_SALT", "gembti-v2-steamid")


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def steamid_hash(steamid: str) -> str:
    return hmac.new(_SALT.encode(), steamid.encode(), hashlib.sha256).hexdigest()


def new_session(con: sqlite3.Connection) -> tuple[str, str]:
    key, owner = secrets.token_hex(16), secrets.token_urlsafe(24)
    now = now_iso()
    con.execute("INSERT INTO v2_sessions VALUES (?,?,?,?)", (key, _hash("gembti-v2-owner|" + owner), now, now))
    return key, owner


def check_session(con: sqlite3.Connection, key: str, owner: str) -> None:
    row = con.execute("SELECT owner_key_hash FROM v2_sessions WHERE session_key = ?", (key,)).fetchone()
    if row is None or not hmac.compare_digest(row[0], _hash("gembti-v2-owner|" + (owner or ""))):
        raise PermissionError("not found")
    con.execute("UPDATE v2_sessions SET last_seen = ? WHERE session_key = ?", (now_iso(), key))


def _ago(iso: str | None, **delta) -> bool:
    if not iso:
        return True
    return datetime.fromisoformat(iso) < datetime.now(KST) - timedelta(**delta)


def cached_library(con: sqlite3.Connection, steamid: str) -> dict | None:
    row = con.execute("SELECT payload_json, status, fetched_at FROM v2_library_cache WHERE steamid_hash = ?",
                      (steamid_hash(steamid),)).fetchone()
    if row and not _ago(row["fetched_at"], hours=LIBRARY_CACHE_HOURS):
        return {"games": json.loads(row["payload_json"]), "status": row["status"], "fetched_at": row["fetched_at"]}
    return None


def put_cache(con: sqlite3.Connection, steamid: str, games: list[dict], status: str, fetched_at: str) -> None:
    con.execute("INSERT OR REPLACE INTO v2_library_cache VALUES (?,?,?,?)",
                (steamid_hash(steamid), json.dumps(games), status, fetched_at))


def save_link(con: sqlite3.Connection, key: str, steamid: str, status: str, games: list[dict], fetched_at: str | None,
              error: str | None = None) -> None:
    now = datetime.now(KST)
    con.execute("DELETE FROM v2_library_games WHERE session_key = ?", (key,))
    con.execute("INSERT OR REPLACE INTO v2_library_links (session_key, steamid, steamid_hash, status, error_code, "
                "linked_at, expires_at, fetched_at, use_for_reco, exclude_owned, meta_status) "
                "VALUES (?,?,?,?,?,?,?,?,1,1,?)",
                (key, steamid, steamid_hash(steamid), status, error, now.isoformat(timespec="seconds"),
                 (now + timedelta(days=LIBRARY_RETENTION_DAYS)).isoformat(timespec="seconds"), fetched_at,
                 "pending" if status == "ok" else None))
    con.executemany("INSERT INTO v2_library_games VALUES (?,?,?,?,?,?,0)",
                    [(key, g["appid"], g.get("name"), g.get("minutes"), g.get("minutes_2w"), g["info_status"])
                     for g in games])


def unlink(con: sqlite3.Connection, key: str) -> None:
    row = con.execute("SELECT steamid_hash FROM v2_library_links WHERE session_key = ?", (key,)).fetchone()
    con.execute("DELETE FROM v2_library_games WHERE session_key = ?", (key,))
    con.execute("DELETE FROM v2_library_links WHERE session_key = ?", (key,))
    if row:
        con.execute("DELETE FROM v2_library_cache WHERE steamid_hash = ?", (row[0],))


def library_view(con: sqlite3.Connection, key: str) -> dict | None:
    link = con.execute("SELECT * FROM v2_library_links WHERE session_key = ?", (key,)).fetchone()
    if link is None:
        return None
    games = [dict(r) for r in con.execute(
        "SELECT appid, name, minutes, minutes_2w, info_status, analysis_excluded FROM v2_library_games "
        "WHERE session_key = ? ORDER BY COALESCE(minutes, -1) DESC, appid", (key,))]
    return {"status": link["status"], "error_code": link["error_code"], "fetched_at": link["fetched_at"],
            "expires_at": link["expires_at"], "use_for_reco": bool(link["use_for_reco"]),
            "exclude_owned": bool(link["exclude_owned"]), "meta_status": link["meta_status"],
            "profile": json.loads(link["profile_json"]) if link["profile_json"] else None,
            "profile_hash": link["profile_hash"], "games": games}


def stale_meta(con: sqlite3.Connection, appids: list[int]) -> list[int]:
    out = []
    for a in appids:
        row = con.execute("SELECT fetched_at, status FROM v2_game_meta WHERE appid = ?", (a,)).fetchone()
        if row is None or _ago(row["fetched_at"], days=META_FRESH_DAYS):
            out.append(a)
    return out


def get_meta(con: sqlite3.Connection, appids: list[int]) -> dict[int, list[dict]]:
    out = {}
    for a in appids:
        row = con.execute("SELECT tags_json, status FROM v2_game_meta WHERE appid = ?", (a,)).fetchone()
        if row and row["status"] == "ok" and row["tags_json"]:
            out[a] = json.loads(row["tags_json"])
    return out


def purge_v2(con: sqlite3.Connection) -> dict:
    """만료 정리: 라이브러리 연결 7일, 서버 캐시 24시간, v2 서술 30일."""
    now = datetime.now(KST)
    exp_links = [r[0] for r in con.execute("SELECT session_key FROM v2_library_links WHERE expires_at < ?",
                                           (now.isoformat(timespec="seconds"),))]
    for k in exp_links:
        unlink(con, k)
    cache = con.execute("DELETE FROM v2_library_cache WHERE fetched_at < ?",
                        ((now - timedelta(hours=LIBRARY_CACHE_HOURS)).isoformat(timespec="seconds"),)).rowcount
    texts = con.execute("DELETE FROM v2_free_texts WHERE expires_at < ?", (now.isoformat(timespec="seconds"),)).rowcount
    return {"library_links": len(exp_links), "library_cache": cache, "v2_free_texts": texts}
