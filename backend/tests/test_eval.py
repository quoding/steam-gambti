"""블라인드 평가 흐름과 집계. 참가자는 테스트 안에서만 만든 가상 기록이다."""

import pytest
from fastapi.testclient import TestClient

from gembti import eval_admin
from gembti.app import app
from gembti.evaluation import ab_layout, hidden_favorite

REF = {**{f"Q{i:02d}": "B" for i in range(1, 21)}, "Q03": "C", "Q17": "C", "Q18": "C"}


@pytest.fixture
def client(snapshot, no_network):
    with TestClient(app) as c:
        yield c


def run_one(c, code, games, pref="A"):
    favs = [games[0]["appid"], games[1]["appid"], 123]
    r = c.post("/api/eval/submit", json={"code": code, "answers": REF, "favorites": favs, "objective_seconds": 200})
    assert r.status_code == 200, r.text
    body = r.json()
    shown = {g["appid"] for g in body["list_a"] + body["list_b"]}
    assert not shown & set(favs)  # 좋아하는 게임은 목록에서 제외
    assert all("reason" not in g and "kind" not in g for g in body["list_a"] + body["list_b"])  # 블라인드
    rate = c.post("/api/eval/rate", json={"code": code, "submission_id": body["submission_id"],
                                          "ratings": {str(a): "interested" for a in shown}, "preference": pref})
    assert rate.status_code == 200, rate.text
    return body, rate.json()


def test_eval_flow_and_reveal(client, snapshot):
    code = eval_admin.create_codes("main", 1, None)[0]
    assert client.post("/api/eval/start", json={"code": code}).json()["phase"] == "main"
    assert client.post("/api/eval/start", json={"code": "NOPE1234"}).status_code == 404
    body, rate = run_one(client, code, snapshot)
    assert rate["reveal"]["fixed_list"] in ("A", "B")
    assert rate["reveal"]["card"]["type_id"] == "T14"
    again = client.post("/api/eval/rate", json={"code": code, "submission_id": body["submission_id"],
                                                "ratings": {}, "preference": "A"})
    assert again.status_code == 409


def test_awkward_reasons_recorded(client, snapshot):
    code = eval_admin.create_codes("pilot", 1, None)[0]
    favs = [snapshot[0]["appid"], snapshot[1]["appid"], 7]
    body = client.post("/api/eval/submit", json={"code": code, "answers": REF, "favorites": favs}).json()
    shown = [g["appid"] for g in body["list_a"] + body["list_b"]]
    r = client.post("/api/eval/rate", json={"code": code, "submission_id": body["submission_id"],
                                            "ratings": {str(a): "neutral" for a in shown}, "preference": "tie",
                                            "awkward": {str(shown[0]): "슈팅 게임인데 전략으로 나옴"}})
    assert r.status_code == 200
    bad = client.post("/api/eval/rate", json={"code": code, "submission_id": "x", "ratings": {},
                                              "preference": "tie", "awkward": {"1": "a" * 201}})
    assert bad.status_code in (404, 422)
    text = eval_admin.report()
    assert "어색한 추천 표시: 1건" in text and "슈팅 게임인데 전략으로 나옴" in text
    assert "‘아직 발견 중’ 비율" in text and "고정 목록 추천 구성" in text


def test_must_rate_every_game(client, snapshot):
    code = eval_admin.create_codes("main", 1, None)[0]
    favs = [snapshot[0]["appid"], snapshot[1]["appid"], 5]
    body = client.post("/api/eval/submit", json={"code": code, "answers": REF, "favorites": favs}).json()
    r = client.post("/api/eval/rate", json={"code": code, "submission_id": body["submission_id"],
                                            "ratings": {}, "preference": "tie"})
    assert r.status_code == 422


def test_hidden_and_ab_are_reproducible():
    assert hidden_favorite("GBABCDEF", [1, 2, 3]) == hidden_favorite("GBABCDEF", [1, 2, 3])
    assert ab_layout("GBX", "s1", [1, 2, 3], [4, 5, 6]) == ab_layout("GBX", "s1", [1, 2, 3], [4, 5, 6])


def test_report_never_declares_pass_below_20(client, snapshot):
    codes = eval_admin.create_codes("main", 3, None)
    for c in codes:
        run_one(client, c, snapshot)
    text = eval_admin.report()
    assert "모집 중 (" in text or "표본 부족 · 판정 보류" in text
    assert "n<20: 추천 품질 통과·우월성·본평가 완료를 선언하지 않음" in text


def test_report_empty_is_not_computed(var_dir):
    text = eval_admin.report()
    assert "본평가 참가 기록 없음" in text
    assert "완료 인원: 0" in text and "계산 불가" in text


def test_tech_check(client, snapshot):
    client.post("/api/submissions", json={"answers": REF, "filters": {"korean_only": True}})
    from gembti import db
    tc = eval_admin.tech_check(db.connect())
    assert tc["pass"] and tc["duplicate_recommendations"] == 0 and tc["filter_violations"] == 0
    assert tc["checked_items"] > 0
