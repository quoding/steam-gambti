"""질문지 v2.3(GEMBTI_QUESTION_BANK_v2.3.md) — 문구만 짧게 바꾸고 연결·배점·분기는 v2.2 그대로인지 검사(문서 §9 완료 기준)."""
from pathlib import Path

import pytest

from gembti.v2 import import_bank
from gembti.v2.api import question_payload
from gembti.v2.engine import route, score_tags
from gembti.v2.policy import active_version, get_v2
from tests import test_v2_bank22 as t22
from tests.test_v2_api import session, v2env, walk  # noqa: F401  (픽스처 재사용)

P = get_v2("v2.3")
P22 = get_v2("v2.2")
DOC = Path(__file__).resolve().parents[2] / "GEMBTI_QUESTION_BANK_v2.3.md"


@pytest.mark.skipif(not DOC.exists(), reason="비공개 문서가 없는 환경")
def test_policy_matches_document_verbatim():
    explore, confirm = import_bank.parse_v23(DOC.read_text(encoding="utf-8"))
    for k in ("questions", "intro", "intro_title", "hint"):
        assert explore[k] == P.explore[k]
    for k in ("questions", "scale", "prompt", "guide"):
        assert confirm[k] == P.confirm[k]


def test_versions_and_active():
    v = P.versions
    assert (v["questionnaire_version"], v["bank_version"], v["layout_version"]) == ("v2.3", "2026-10-26-r2", "v2.3-l1")
    assert {k: x for k, x in v.items() if k not in ("questionnaire_version", "bank_version", "layout_version")} == \
        {k: x for k, x in P22.versions.items() if k not in ("questionnaire_version", "bank_version", "layout_version")}
    assert P.routing == {**P22.routing, "source": P.routing["source"]}
    assert active_version() == "v2.3"


def test_links_ids_and_points_unchanged_from_v22():
    def links(p):
        return [(q["question_id"], o["code"], o["family_id"], o["tag_hint"]) for q in p.explore["questions"]
                for o in q["options"]]
    assert links(P) == links(P22)
    keep = ("question_id", "tagid", "family_id", "role")
    assert [[q[k] for k in keep] for q in P.confirm["questions"]] == [[q[k] for k in keep] for q in P22.confirm["questions"]]
    assert P.points == P22.points and [s["code"] for s in P.confirm["scale"]] == list("ABCDEX")


def test_counts_and_length_limits():
    acts = [o["label"] for q in P.explore["questions"] for o in q["options"] if o["code"] in "ABCD"]
    assert len(P.explore_ids) == 12 and len(acts) == 48 and len(P.confirm_by_id) == 75 and len(P.tag_name) == 25
    assert max(map(len, acts)) <= 18
    assert max(len(q["text"]) for q in P.confirm["questions"]) <= 30
    assert max(len(s["label"]) for s in P.confirm["scale"]) <= 12
    assert {q["text"] for q in P.explore["questions"]} == {"어떤 플레이가 끌려요?"}


def test_payload_has_no_internal_metadata():
    for qid in [*P.explore_ids, *P.confirm_by_id]:
        d = question_payload(P, qid)
        assert all(set(o) == {"code", "label"} for o in d["options"])  # 계열·태그·배점 미노출
        assert not {"tagid", "family_id", "intent", "role"} & set(d)
        if qid.startswith("C"):
            assert d["prompt"] == "이런 플레이는 어때요?" and d["text"] == P.confirm_by_id[qid]["text"]
            assert d["context"] is None  # 긴 공통 설명을 매 문항 반복하지 않음
        else:
            assert d["hint"] == "끌리는 것, 최대 2개"


@pytest.mark.parametrize("name", ["test_paths_end_between_20_and_24", "test_initial_order_and_social_limit",
                                  "test_all_cards_reachable_and_unknown_unexposed",
                                  "test_explore_multi_select_order_invariant", "test_tag_pick_uses_selection_ratio",
                                  "test_reserve_does_not_override_negative",
                                  "test_back_navigation_drops_out_of_path_answers",
                                  "test_regular_card_stops_after_one_reserve_without_new_family",
                                  "test_regular_card_unknown_top_tag_keeps_unknown",
                                  "test_card_unmet_opens_new_family_with_budget"])
def test_v22_routing_regressions_hold_on_v23(name, monkeypatch):
    monkeypatch.setattr(t22, "P", P)
    f = getattr(t22, name)
    if name == "test_paths_end_between_20_and_24":
        for ex, cf in [(lambda q: ["X"], lambda q, i: "X"), (lambda q: ["N"], lambda q, i: "X"),
                       (lambda q: ["A"], lambda q, i: "A"), (lambda q: ["B", "C"], lambda q, i: "E"),
                       (lambda q: ["A", "D"], lambda q, i: "ABCDEX"[i % 6])]:
            f(ex, cf)
    else:
        f()


def test_same_semantic_answers_same_scores_as_v22():
    """같은 경로·같은 의미의 답이면 태그 점수·상태는 v2.2와 같다. 동점 해시에 버전이 들어가 경로 자체는 다를 수 있다(문서 §7.4)."""
    a = {q: ["A", "C"] for q in P.explore_ids}
    path = route(P22, a).path
    for i, q in enumerate(path[12:]):
        a[q] = "ABCDEX"[i % 6]
    path = route(P22, a).path

    def key(p):
        return {t: (s.state, s.raw, s.n, s.normalized) for t, s in score_tags(p, a, path).items()}
    assert key(P) == key(P22)


def test_new_attempt_uses_v23_and_old_attempt_keeps_v22(v2env):  # noqa: F811
    from gembti import db
    c, _ = v2env
    s = session(c)
    con = db.connect()
    con.execute("UPDATE v2_sessions SET attempt_version='v2.2' WHERE session_key=?", (s["session_key"],))
    old = c.post("/api/v2/route", json={**s, "answers": {"E01": ["A"]}}).json()
    assert old["questionnaire_version"] == "v2.2" and old["next"]["text"] == P22.explore_by_id["E02"]["text"]
    ans, r = walk(c, s, lambda q: ["A"] if q["stage"] == "explore" else "A")
    assert r["questionnaire_version"] == "v2.3"
    sub = c.post("/api/v2/submit", json={**s, "answers": ans}).json()
    assert sub["versions"]["questionnaire_version"] == "v2.3" if "versions" in sub else True
    v = c.post(f"/api/v2/submissions/{sub['submission_id']}/view", json={**s}).json()
    assert v["card"] == sub["card"]
    q = c.get("/api/v2/questionnaire").json()
    assert q["intro_title"] == "어떤 게임이 끌리는지 알아볼까요?"
    con.close()
