"""검사 API·저장·재현성·스냅샷 원칙 통합 테스트."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from gembti import catalog as catalog_mod
from gembti import config, db
from gembti.app import app
from tests.conftest import fixture_games, make_snapshot

REF = {**{f"Q{i:02d}": "B" for i in range(1, 21)}, "Q03": "C", "Q17": "C", "Q18": "C"}
REF_SID = "aebe9d6167d4940468c88395079033c757188a9c98e2831234a4b4dc1836c846"


@pytest.fixture
def client(snapshot, no_network):
    with TestClient(app) as c:
        yield c


def submit(c, answers=REF, **kw):
    r = c.post("/api/submissions", json={"answers": answers, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def rows(sql, *args):
    con = sqlite3.connect(config.SERVICE_DB)
    con.row_factory = sqlite3.Row
    out = con.execute(sql, args).fetchall()
    con.close()
    return out


def test_questionnaire_has_no_scores(client):
    q = client.get("/api/questionnaire").json()
    assert len(q["questions"]) == 20 and len(q["free_texts"]) == 3
    assert "points" not in json.dumps(q) and "meaning" not in json.dumps(q)


def test_same_answers_same_session_and_card_distinct_submissions(client):
    a = submit(client)
    b = submit(client, answers=dict(reversed(list(REF.items()))))
    assert a["submission_id"] != b["submission_id"]
    assert a["card"] == b["card"] and a["card"]["type_id"] == "T14"
    subs = rows("SELECT submission_id, session_id FROM submissions")
    assert len(subs) == 2 and {s["session_id"] for s in subs} == {REF_SID}
    assert a["recommendations"]["items"] == b["recommendations"]["items"]


def test_free_text_does_not_change_results(client):
    a = submit(client)
    b = submit(client, free_texts={"F01": "협동해서 보스를 잡을 때", "F02": "x" * 300, "F03": "턴제가 좋아요"},
               experiment_consent=True)
    assert a["card"] == b["card"] and a["recommendations"]["items"] == b["recommendations"]["items"]
    r = client.post("/api/submissions", json={"answers": REF, "free_texts": {"F01": "x" * 301}})
    assert r.status_code == 422
    assert client.post("/api/submissions", json={"answers": REF, "free_texts": {"F09": "a"}}).status_code == 422


def test_invalid_codes_rejected(client):
    assert client.post("/api/submissions", json={"answers": {"Q03": "E"}}).status_code == 422
    assert client.post("/api/submissions", json={"answers": {"Q01": "Z"}}).status_code == 422


def test_missing_answers_recorded_as_x(client):
    r = submit(client, answers={"Q01": "A"})
    sub = rows("SELECT unanswered_to_x FROM submissions WHERE submission_id = ?", r["submission_id"])[0]
    assert len(json.loads(sub["unanswered_to_x"])) == 19
    codes = rows("SELECT code FROM answers WHERE submission_id = ?", r["submission_id"])
    assert sum(1 for c in codes if c["code"] == "X") == 19
    assert r["card"]["is_discovering"] and r["card"]["empty_text"]


def test_view_after_restart_is_identical(client):
    a = submit(client)
    catalog_mod._cache.update(key=None, catalog=None)  # 프로세스 재시작 흉내: 메모리 캐시 제거
    v = client.post(f"/api/submissions/{a['submission_id']}/view", json={"owner_key": a["owner_key"]}).json()
    assert v["card"] == a["card"]
    assert v["recommendations"]["items"] == a["recommendations"]["items"]
    bad = client.post(f"/api/submissions/{a['submission_id']}/view", json={"owner_key": "wrong"})
    assert bad.status_code == 404


def test_filters_and_played(client, snapshot):
    a = submit(client, filters={"korean_only": True})
    by = {g["appid"]: g for g in snapshot}
    assert all(by[i["appid"]]["korean"] == "true" for i in a["recommendations"]["items"])
    first = a["recommendations"]["items"][0]["appid"]
    r = client.post(f"/api/submissions/{a['submission_id']}/recommendations",
                    json={"owner_key": a["owner_key"], "filters": {"korean_only": True}, "played": [first]}).json()
    got = [i["appid"] for i in r["recommendations"]["items"]]
    assert first not in got and len(got) == len(set(got))
    m = submit(client, filters={"os": "mac"})
    assert all(by[i["appid"]]["mac"] == "true" for i in m["recommendations"]["items"])
    # 필터 변경은 카드를 바꾸지 않는다
    assert m["card"] == a["card"]


def test_kinds_and_labels(client):
    a = submit(client)
    items = a["recommendations"]["items"]
    assert 0 < len(items) <= 5
    kinds = [i["kind"] for i in items]
    assert kinds == sorted(kinds, key=lambda k: k != "personalized")  # 개인화 먼저
    for i in items:
        if i["kind"] == "personalized":
            assert len(i["matched_tags"]) >= 2


def test_catalog_unavailable_still_returns_card(var_dir, no_network):
    with TestClient(app) as c:
        r = c.post("/api/submissions", json={"answers": REF})
        assert r.status_code == 200
        body = r.json()
        assert body["card"]["type_id"] == "T14"
        assert body["recommendations"]["status"] == "catalog_unavailable"
        assert c.get("/api/health").json()["catalog"] is None


def test_failed_snapshot_does_not_replace_good_one(client, var_dir):
    before = json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())
    broken = make_snapshot(var_dir, fixture_games(10), sid="broken")
    with pytest.raises(ValueError):
        catalog_mod.promote(broken, expected=40)  # 게임 수 미달
    (var_dir / "garbage.db").write_bytes(b"not a database")
    with pytest.raises(ValueError):
        catalog_mod.promote(var_dir / "garbage.db", expected=40)
    assert json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text()) == before
    assert client.post("/api/submissions", json={"answers": REF}).json()["recommendations"]["status"] == "ok"


def test_promote_backup_and_rollback(client, var_dir):
    first = json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())["snapshot_id"]
    newer = make_snapshot(var_dir, fixture_games(), sid="test-snap-2")
    catalog_mod.promote(newer, expected=40)
    assert json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())["previous"] == first
    assert list((var_dir / "backups").glob("active_snapshot.*.json"))
    assert json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())["snapshot_id"] == "test-snap-2"
    catalog_mod.rollback(first, expected=40)
    assert json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())["snapshot_id"] == first
    with pytest.raises(ValueError):
        catalog_mod.rollback("does-not-exist", expected=40)


def test_feedback_and_delete(client):
    a = submit(client, free_texts={"F01": "메모"}, experiment_consent=True)
    sid = a["submission_id"]
    assert client.post(f"/api/submissions/{sid}/feedback",
                       json={"owner_key": a["owner_key"], "rating": 4, "comment": "좋아요"}).status_code == 200
    assert client.post(f"/api/submissions/{sid}/delete", json={"owner_key": a["owner_key"]}).status_code == 200
    for t in ("submissions", "answers", "free_texts", "results", "recommendations", "feedback"):
        assert rows(f"SELECT COUNT(*) c FROM {t} WHERE submission_id = ?", sid)[0]["c"] == 0


def test_body_limit_and_rate_limit(client, monkeypatch):
    big = {"answers": REF, "free_texts": {"F01": "가" * 20000}}
    assert client.post("/api/submissions", json=big).status_code == 413
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MINUTE", 2)
    from gembti import app as app_mod
    app_mod._hits.clear()
    codes = [client.post("/api/submissions", json={"answers": REF}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    app_mod._hits.clear()


def test_free_text_expiry_purged(client):
    a = submit(client, free_texts={"F01": "old"})
    con = db.connect()
    con.execute("UPDATE free_texts SET expires_at = '2000-01-01T00:00:00+09:00'")
    con.close()
    submit(client)
    assert rows("SELECT COUNT(*) c FROM free_texts WHERE submission_id = ?", a["submission_id"])[0]["c"] == 0


def test_service_code_has_no_external_calls():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "gembti"
    for f in ("app.py", "service.py", "engine.py", "recommend.py", "catalog.py", "evaluation.py", "db.py"):
        imports = [ln for ln in (root / f).read_text().splitlines() if ln.lstrip().startswith(("import ", "from "))]
        bad = [ln for ln in imports if any(m in ln for m in ("httpx", "requests", "urllib", "collector", "jev"))]
        assert not bad, (f, bad)


def test_snapshot_with_excluded_content_is_rejected(client, var_dir):
    before = json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())
    games = fixture_games()
    games[0] = {**games[0], "appid": 2495450}  # content_review: exclude
    bad = make_snapshot(var_dir, games, sid="has-excluded")
    with pytest.raises(ValueError, match="콘텐츠 정책 위반"):
        catalog_mod.promote(bad, expected=40)
    pend = fixture_games()
    pend[0] = {**pend[0], "tags": pend[0]["tags"] + [{"tagid": 9130, "weight": 10, "rank": 9}]}  # 미검토 + 트리거
    with pytest.raises(ValueError, match="콘텐츠 정책 위반"):
        catalog_mod.promote(make_snapshot(var_dir, pend, sid="pending"), expected=40)
    # 롤백도 같은 검증: 이미 snapshots/에 있는 위반 스냅샷으로는 돌아갈 수 없다
    import shutil
    shutil.copy2(bad, config.SNAPSHOT_DIR / "has-excluded.db")
    with pytest.raises(ValueError, match="콘텐츠 정책 위반"):
        catalog_mod.rollback("has-excluded", expected=40)
    held = fixture_games()
    held[0] = {**held[0], "appid": 3464370}  # hold는 허용(추천 단계에서만 빠짐)
    catalog_mod.promote(make_snapshot(var_dir, held, sid="has-hold"), expected=40)
    r = client.post("/api/submissions", json={"answers": REF}).json()
    assert 3464370 not in [i["appid"] for i in r["recommendations"]["items"]]
    catalog_mod.rollback(before["snapshot_id"], expected=40)
    assert json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())["snapshot_id"] == before["snapshot_id"]
