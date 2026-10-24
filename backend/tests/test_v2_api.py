"""v2 API 통합: 질문 흐름·라이브러리(가짜 Steam)·필터·재현성·삭제·백업. 실제 Steam 호출 없음."""
import random
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from gembti import config, db, maintenance
from gembti.app import app
from gembti.v2 import catalog_v2, library_job, store
from gembti.v2.policy import get_v2
from gembti.v2.steam_library import LibraryError, SteamWebAPI, parse_identity

P = get_v2()
STEAMID = "76561198000000001"


def tag_rows(pairs):
    seen, rows = set(), []
    for t, w in pairs:  # 같은 태그가 겹치면 앞의 것만
        if t not in seen:
            seen.add(t)
            rows.append({"tagid": t, "weight": w, "rank": len(rows) + 1})
    return rows


def v2_games():
    rng = random.Random(3)
    fams = [f for f in P.family_by_id]
    out = []
    for i in range(80):
        f1, f2 = fams[i % 16], fams[(i * 7 + 3) % 16]
        t1 = P.family_by_id[f1]["tags"][0]["tagid"]
        t2 = P.family_by_id[f2]["tags"][-1]["tagid"]
        tr = rng.choice([600, 1200, 6000, 60000, 150000])
        out.append({"appid": 800000 + i, "name": f"V2 Fixture {i}", "short_description": "fixture",
                    "short_description_lang": "other", "header_image": None,
                    "store_url": f"https://store.steampowered.com/app/{800000 + i}/", "windows": "true",
                    "mac": "false", "linux": "false", "korean": "true" if i % 2 else "false", "english": "true",
                    "series_id": f"s{i}", "developer_key": f"d{i % 13}", "fetched_at": "2026-10-24T00:00:00+0900",
                    "tags": tag_rows([(t1, 1000), (t2, 600), (1685 if i % 2 else 1775, 400)]),
                    "review": {"review_score": 8, "review_score_desc": "Very Positive",
                               "total_positive": int(tr * 0.92), "total_negative": tr - int(tr * 0.92),
                               "total_reviews": tr, "source": "test", "scope": {"languages": "all"},
                               "fetched_at": "2026-10-24T00:00:00+0900"}})
    return out


@pytest.fixture
def v2env(var_dir, no_network, monkeypatch):
    catalog_v2._cache.update(key=None, cat=None)
    from gembti.v2 import api as v2api
    v2api._link_hits.clear()
    games = v2_games()
    path = var_dir / "v2snap.db"
    catalog_v2.write_snapshot(path, "v2-test", games, {t: P.tag_name[t] for t in P.family_of},
                              {t: str(t) for t in P.family_of}, {"note": "test"})
    monkeypatch.setattr(catalog_v2, "validate", lambda p: ([], {"coverage_gate": "test", "games": len(games)}))
    catalog_v2.promote(path)
    with TestClient(app) as c:
        yield c, games


def session(c):
    return c.post("/api/v2/sessions").json()


def walk(c, s, pick):
    ans = {}
    for _ in range(40):
        r = c.post("/api/v2/route", json={**s, "answers": ans}).json()
        if r["done"]:
            return ans, r
        q = r["next"]
        ans[q["question_id"]] = pick(q)
    raise AssertionError


def pick_fixed(q):
    if q["stage"] == "explore":
        return ["A", "C"] if q["question_id"] in ("E01", "E03", "E06", "E10") else ["B"]
    return "A"


def test_flow_submit_and_filters_do_not_change_type(v2env):
    c, _ = v2env
    s = session(c)
    ans, r = walk(c, s, pick_fixed)
    assert 20 <= len(r["path"]) <= 24
    sub = c.post("/api/v2/submit", json={**s, "answers": ans, "free_texts": {"F01": "v2 서술"}}).json()
    assert sub["recommendations"]["status"] == "ok"
    card = sub["card"]
    sid = sub["submission_id"]
    for f in ({"min_reviews": 50000}, {"min_positive": 95}, {"korean_only": True}, {"min_reviews": 100000}):
        rr = c.post(f"/api/v2/submissions/{sid}/recommendations", json={**s, "filters": f}).json()["recommendations"]
        for it in rr["items"]:
            assert it["review"]["total_reviews"] >= f.get("min_reviews", 500)
            if f.get("korean_only"):
                assert it["korean"] == "true"
        v = c.post(f"/api/v2/submissions/{sid}/view", json={**s}).json()
        assert v["card"] == card


def test_same_inputs_same_recommendations(v2env):
    c, _ = v2env
    outs = []
    for _ in range(2):
        s = session(c)
        ans, _ = walk(c, s, pick_fixed)
        sub = c.post("/api/v2/submit", json={**s, "answers": dict(reversed(list(ans.items())))}).json()
        outs.append(([i["appid"] for i in sub["recommendations"]["items"]], sub["card"]["type_id"],
                     sub["recommendations"]["repro_key"]))
    assert outs[0] == outs[1]


def test_route_requires_owner_and_drops_off_path(v2env):
    c, _ = v2env
    s = session(c)
    assert c.post("/api/v2/route", json={**s, "owner_key": "x", "answers": {}}).status_code == 404
    ans, _ = walk(c, s, pick_fixed)
    ans2 = {**ans, "E01": ["N"], "E03": ["N"], "E06": ["N"], "E10": ["N"]}
    r2 = c.post("/api/v2/route", json={**s, "answers": ans2}).json()
    assert set(r2["dropped"]) <= set(ans2) and all(d not in r2["path"] for d in r2["dropped"])
    assert c.post("/api/v2/submit", json={**s, "answers": {"E01": ["A"]}}).status_code == 422


# ---------------------------------------------------------------- 라이브러리 (가짜 Steam)

def fake_transport(owned_payload, vanity=STEAMID, status=200):
    def handler(req: httpx.Request):
        if "ResolveVanityURL" in req.url.path:
            return httpx.Response(200, json={"response": {"success": 1, "steamid": vanity}})
        if "GetOwnedGames" in req.url.path:
            if status != 200:
                return httpx.Response(status, headers={"Retry-After": "0"})
            assert req.url.params["include_appinfo"] == "1" and req.url.params["include_played_free_games"] == "1"
            return httpx.Response(200, json={"response": owned_payload})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


class FakeMeta:
    def __init__(self, games):
        self.games = {g["appid"]: g for g in games}
        self.calls = 0

    def get_items(self, batch):
        self.calls += 1
        assert len(batch) <= 3
        from gembti.collector.steam_client import FetchResult
        items = [{"appid": a, "name": self.games[a]["name"],
                  "tags": [{"tagid": t["tagid"], "weight": t["weight"]} for t in self.games[a]["tags"]]}
                 for a in batch if a in self.games]
        return FetchResult(True, 200, {"response": {"store_items": items}}, None, 1, "now")


def test_parse_identity():
    assert parse_identity(STEAMID) == ("steamid", STEAMID)
    assert parse_identity(f"https://steamcommunity.com/profiles/{STEAMID}/") == ("steamid", STEAMID)
    assert parse_identity("steamcommunity.com/id/some_name") == ("vanity", "some_name")
    for bad in ("nickname", "https://evil.com/id/x", "https://steamcommunity.com.evil.com/id/x",
                "http://steamcommunity.com:8080/id/x", "12345", "https://steamcommunity.com/groups/x", "file:///etc"):
        with pytest.raises(LibraryError):
            parse_identity(bad)


def test_adapter_errors_are_distinguished():
    with pytest.raises(LibraryError, match="no_api_key"):
        SteamWebAPI(key="").owned_games(STEAMID)
    api = SteamWebAPI(key="k", transport=fake_transport({}), sleep=lambda s: None)
    assert api.owned_games(STEAMID).status == "private_or_unavailable"
    api = SteamWebAPI(key="k", transport=fake_transport({"game_count": 0}), sleep=lambda s: None)
    assert api.owned_games(STEAMID).status == "empty"
    api = SteamWebAPI(key="k", transport=fake_transport({}, status=429), sleep=lambda s: None)
    with pytest.raises(LibraryError, match="rate_limited"):
        api.owned_games(STEAMID)
    api = SteamWebAPI(key="k", transport=fake_transport({}, status=403), sleep=lambda s: None)
    with pytest.raises(LibraryError, match="api_key_rejected"):
        api.owned_games(STEAMID)


def link_library(c, s, games, monkeypatch, minutes=None):
    owned = {"game_count": 6, "games": [
        {"appid": g["appid"], "name": g["name"], "playtime_forever": (minutes or [6000, 3000, 2400, 900, 0, 120])[k]}
        for k, g in enumerate(games[:6])]}
    meta = FakeMeta(games)
    api = SteamWebAPI(key="k", transport=fake_transport(owned), sleep=lambda s: None)
    monkeypatch.setattr(library_job, "start_import",
                        lambda key, raw, fam, ser: library_job.run_import(key, raw, fam, ser, api=api, meta_client=meta))
    r = c.post("/api/v2/library/link", json={**s, "input": f"https://steamcommunity.com/profiles/{STEAMID}"})
    assert r.status_code == 200
    return meta


def test_library_link_profile_and_recommendation_effect(v2env, monkeypatch):
    c, games = v2env
    s = session(c)
    meta = link_library(c, s, games, monkeypatch)
    st = c.post("/api/v2/library/status", json=s).json()
    assert st["status"] == "ok" and st["owned_count"] == 6 and st["profile"]["enabled"]
    assert meta.calls == 2  # 플레이 시간>0인 5개만 3개씩
    ans, _ = walk(c, s, pick_fixed)
    sub = c.post("/api/v2/submit", json={**s, "answers": ans}).json()
    recs = sub["recommendations"]
    assert recs["library"]["used"] is True
    owned_ids = {g["appid"] for g in games[:6]}
    assert not owned_ids & {i["appid"] for i in recs["items"]}  # 0분 게임 포함 보유 제외
    # 유형은 라이브러리와 무관: 연결하지 않은 세션과 같다
    s2 = session(c)
    ans2, _ = walk(c, s2, pick_fixed)
    sub2 = c.post("/api/v2/submit", json={**s2, "answers": ans2}).json()
    assert sub2["card"] == sub["card"] and sub2["recommendations"]["library"]["used"] is False
    # 추천 요청마다 외부 호출 안 함
    before = meta.calls
    c.post(f"/api/v2/submissions/{sub['submission_id']}/recommendations", json={**s, "filters": {}})
    assert meta.calls == before
    # 설정: 반영 끄기·보유 제외 끄기
    st = c.post("/api/v2/library/settings", json={**s, "use_for_reco": False, "exclude_owned": False}).json()
    r = c.post(f"/api/v2/submissions/{sub['submission_id']}/recommendations", json={**s, "filters": {}}).json()
    assert r["recommendations"]["library"]["used"] is False and r["recommendations"]["counts"]["owned_excluded"] == 0


def test_unlink_and_expiry_delete_private_data(v2env, monkeypatch):
    c, games = v2env
    s = session(c)
    link_library(c, s, games, monkeypatch)
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM v2_library_cache").fetchone()[0] == 1
    assert c.post("/api/v2/library/unlink", json=s).json()["ok"]
    for t in ("v2_library_links", "v2_library_games", "v2_library_cache"):
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    s2 = session(c)
    link_library(c, s2, games, monkeypatch)
    con.execute("UPDATE v2_library_links SET expires_at = '2000-01-01T00:00:00+09:00'")
    con.execute("UPDATE v2_library_cache SET fetched_at = '2000-01-01T00:00:00+09:00'")
    res = store.purge_v2(con)
    assert res["library_links"] == 1 and res["library_cache"] == 0  # 연결 삭제 시 캐시도 함께 제거됨
    assert con.execute("SELECT COUNT(*) FROM v2_library_games").fetchone()[0] == 0


def test_link_rate_limit_and_invalid_input(v2env, monkeypatch):
    c, _ = v2env
    s = session(c)
    assert c.post("/api/v2/library/link", json={**s, "input": "my nickname"}).status_code == 422
    monkeypatch.setattr(library_job, "start_import", lambda *a: None)
    codes = [c.post("/api/v2/library/link", json={**s, "input": STEAMID}).status_code for _ in range(6)]
    assert codes.count(429) >= 1


def test_library_failure_keeps_question_flow(v2env, monkeypatch):
    c, _ = v2env
    s = session(c)
    api = SteamWebAPI(key="", sleep=lambda s: None)  # 키 없음
    monkeypatch.setattr(library_job, "start_import",
                        lambda key, raw, fam, ser: library_job.run_import(key, raw, fam, ser, api=api))
    assert c.post("/api/v2/library/link", json={**s, "input": STEAMID}).status_code == 200
    st = c.post("/api/v2/library/status", json=s).json()
    assert st["status"] == "error" and st["error_code"] == "no_api_key"
    ans, _ = walk(c, s, pick_fixed)
    sub = c.post("/api/v2/submit", json={**s, "answers": ans}).json()
    assert sub["recommendations"]["status"] == "ok" and sub["recommendations"]["library"]["used"] is False


def test_backup_excludes_library_and_steamid(v2env, monkeypatch):
    c, games = v2env
    s = session(c)
    link_library(c, s, games, monkeypatch)
    ans, _ = walk(c, s, pick_fixed)
    c.post("/api/v2/submit", json={**s, "answers": ans, "free_texts": {"F02": "V2-CANARY-서술"}})
    path = maintenance.backup(keep=5)
    raw = path.read_bytes()
    assert STEAMID.encode() not in raw and "V2-CANARY-서술".encode() not in raw
    b = sqlite3.connect(path)
    for t in ("v2_library_links", "v2_library_games", "v2_library_cache", "v2_free_texts"):
        assert b.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    assert b.execute("SELECT COUNT(*) FROM v2_submissions").fetchone()[0] == 1
    assert b.execute("SELECT COUNT(*) FROM v2_recommendations").fetchone()[0] > 0
    b.close()
    restored = config.VAR_DIR / "restored"
    restored.mkdir()
    monkeypatch.setattr(config, "SERVICE_DB", restored / "service.db")
    maintenance.restore(path)
    r = db.connect()
    assert r.execute("SELECT COUNT(*) FROM v2_submissions").fetchone()[0] == 1
    assert r.execute("SELECT COUNT(*) FROM v2_library_links").fetchone()[0] == 0
    assert STEAMID.encode() not in (restored / "service.db").read_bytes()


def test_route_calls_do_not_exhaust_submit_limit(v2env, monkeypatch):
    c, _ = v2env
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 10)
    from gembti import app as app_mod
    app_mod._hits.clear()
    s = session(c)
    ans, _ = walk(c, s, pick_fixed)  # /route 20회 이상
    assert c.post("/api/v2/submit", json={**s, "answers": ans}).status_code == 200
    app_mod._hits.clear()
