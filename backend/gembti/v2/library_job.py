"""라이브러리 불러오기 작업. 사용자가 누를 때만 실행하며 추천 요청마다 외부 호출하지 않는다."""
from __future__ import annotations

import hashlib
import json
import threading
import time

from gembti import db
from gembti.v2 import store
from gembti.v2.library_profile import TOP_N, build_profile
from gembti.v2.steam_library import LibraryError, SteamWebAPI, parse_identity

_running: set[str] = set()
_lock = threading.Lock()


def _fetch_meta(con, appids: list[int], client=None) -> str:
    """분석 대상 상위 게임 중 캐시 없음·만료분만 GetItems(3개씩, 2초 간격)."""
    todo = store.stale_meta(con, appids)
    if not todo:
        return "ok"
    if client is None:
        from gembti.collector.steam_client import SteamClient
        client = SteamClient()
    failed = 0
    for i in range(0, len(todo), 3):
        batch = todo[i:i + 3]
        try:
            res = client.get_items(batch)
        except Exception:  # noqa: BLE001 — 메타 실패는 'failed'로 기록하고 질문·추천 흐름은 유지
            res = None
        got = {}
        if res is not None and res.ok and res.data:
            for it in res.data.get("response", {}).get("store_items", []):
                got[it.get("appid") or it.get("id")] = it
        now = store.now_iso()
        for a in batch:
            it = got.get(a)
            if it is None:
                failed += 1
                con.execute("INSERT OR REPLACE INTO v2_game_meta VALUES (?,?,?,?,?)", (a, None, None, "failed", now))
                continue
            tags = [{"tagid": t["tagid"], "weight": t.get("weight"), "rank": k + 1}
                    for k, t in enumerate(it.get("tags") or [])]
            con.execute("INSERT OR REPLACE INTO v2_game_meta VALUES (?,?,?,?,?)",
                        (a, it.get("name"), json.dumps(tags), "ok" if tags else "no_tags", now))
    return "ok" if failed == 0 else ("partial" if failed < len(todo) else "failed")


def top_played(con, key: str) -> list[int]:
    rows = con.execute("SELECT appid FROM v2_library_games WHERE session_key = ? AND analysis_excluded = 0 "
                       "AND minutes > 0 ORDER BY minutes DESC, appid LIMIT ?", (key, TOP_N)).fetchall()
    return [r[0] for r in rows]


def refresh_profile(con, key: str, family_of: dict, series_of: dict) -> dict:
    lv = store.library_view(con, key)
    if lv is None or lv["status"] != "ok":
        return {"enabled": False, "reason": "라이브러리 미연결"}
    owned = [{"appid": g["appid"], "minutes": g["minutes"]} for g in lv["games"]]
    excluded = {g["appid"] for g in lv["games"] if g["analysis_excluded"]}
    meta = store.get_meta(con, [g["appid"] for g in lv["games"]])
    prof = build_profile(owned, meta, family_of, series_of, excluded)
    data = {"enabled": prof.enabled, "reason": prof.reason, "analyzed_count": prof.analyzed_count,
            "tagged_count": prof.tagged_count, "tagged_weight_share": prof.tagged_weight_share,
            "family_vector": {k: round(v, 6) for k, v in sorted(prof.family_vector.items())},
            "analyzed": [{"appid": r["appid"], "hours": r["hours"], "tagged": r["tagged"],
                          "share": round(r.get("share", 0.0), 4)} for r in prof.analyzed]}
    ph = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]
    con.execute("UPDATE v2_library_links SET profile_json = ?, profile_hash = ? WHERE session_key = ?",
                (json.dumps(data, ensure_ascii=False), ph, key))
    data["profile_hash"] = ph
    return data


def run_import(key: str, raw_input: str, family_of: dict, series_of: dict, api: SteamWebAPI | None = None,
               meta_client=None) -> None:
    with _lock:
        if key in _running:
            return  # 중복 요청 합치기
        _running.add(key)
    con = db.connect()
    try:
        api = api or SteamWebAPI()
        try:
            kind, val = parse_identity(raw_input)
            steamid = api.resolve_vanity(val) if kind == "vanity" else val
            cached = store.cached_library(con, steamid)
            if cached:
                games, status, fetched = cached["games"], cached["status"], cached["fetched_at"]
            else:
                res = api.owned_games(steamid)
                games, status, fetched = res.games, res.status, res.fetched_at
                store.put_cache(con, steamid, games, status, fetched)
            store.save_link(con, key, steamid, status, games, fetched)
        except LibraryError as e:
            con.execute("INSERT OR REPLACE INTO v2_library_links (session_key, steamid, steamid_hash, status, "
                        "error_code, linked_at, expires_at) VALUES (?,?,?,?,?,?,?)",
                        (key, "", "", "error", e.code, store.now_iso(), store.now_iso()))
            return
        if status == "ok":
            meta_status = _fetch_meta(con, top_played(con, key), meta_client)
            con.execute("UPDATE v2_library_links SET meta_status = ? WHERE session_key = ?", (meta_status, key))
            refresh_profile(con, key, family_of, series_of)
    finally:
        con.close()
        with _lock:
            _running.discard(key)


def start_import(key: str, raw_input: str, family_of: dict, series_of: dict) -> None:
    t = threading.Thread(target=run_import, args=(key, raw_input, family_of, series_of), daemon=True)
    t.start()


def is_running(key: str) -> bool:
    with _lock:
        return key in _running


def wait_idle(key: str, timeout: float = 30) -> None:  # 테스트용
    t0 = time.time()
    while is_running(key) and time.time() - t0 < timeout:
        time.sleep(0.05)
