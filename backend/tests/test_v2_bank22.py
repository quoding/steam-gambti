"""질문지 v2.2 은행(GEMBTI_QUESTION_BANK_v2.2.md) 적용 검사 — 문서 §11.1 완료 검사 목록."""
import random
from pathlib import Path

import pytest

from gembti.v2 import import_bank
from gembti.v2.engine import judge_v2, route, score_tags
from gembti.v2.policy import BANKS, active_version, get_v2, load_v2
from tests.test_v2_api import session, v2env, walk  # noqa: F401  (픽스처 재사용)

P = get_v2("v2.2")
DOC = Path(__file__).resolve().parents[2] / "docs" / "source" / "GEMBTI_QUESTION_BANK_v2.2.md"


def run(p, ex, cf):
    a: dict = {}
    for i in range(40):
        r = route(p, a)
        if r.done:
            return judge_v2(p, a), a
        q = r.next_question
        a[q] = ex(q) if q.startswith("E") else cf(q, i)
    raise AssertionError("종료 안 됨")


@pytest.mark.skipif(not DOC.exists(), reason="비공개 문서가 없는 환경")
def test_policy_matches_document_verbatim():
    explore, confirm = import_bank.parse(DOC.read_text(encoding="utf-8"))
    assert explore["questions"] == P.explore["questions"]
    assert confirm["questions"] == P.confirm["questions"]
    assert confirm["scale"] == P.confirm["scale"] and confirm["context"] == P.confirm["context"]
    assert explore["intro"] == P.explore["intro"]


def test_counts_and_structure():
    acts = [o for q in P.explore["questions"] for o in q["options"] if o["code"] in "ABCD"]
    assert len(P.explore_ids) == 12 and len(acts) == 48 and len(P.confirm_by_id) == 75
    assert len({o["tag_hint"] for o in acts}) == 25 and len(P.family_by_id) == 16
    assert [s["code"] for s in P.confirm["scale"]] == list("ABCDEX")
    for q in P.explore["questions"]:  # 탐색은 A~D + N/X만, 확인 척도와 섞이지 않음
        assert [o["code"] for o in q["options"]] == ["A", "B", "C", "D", "N", "X"]
    assert get_v2("v2.2").versions["questionnaire_version"] == "v2.2"
    assert get_v2("v2.0").versions["questionnaire_version"] == "v2.0" and load_v2().routing["routing_version"] == "v2.0"


@pytest.mark.parametrize("ex,cf", [
    (lambda q: ["X"], lambda q, i: "X"), (lambda q: ["N"], lambda q, i: "X"),
    (lambda q: ["A"], lambda q, i: "A"), (lambda q: ["B", "C"], lambda q, i: "E"),
    (lambda q: ["A", "D"], lambda q, i: "ABCDEX"[i % 6]),
])
def test_paths_end_between_20_and_24(ex, cf):
    j, _ = run(P, ex, cf)
    assert 20 <= len(j.route.path) <= 24
    assert len(j.route.chosen_tags) >= 4


def test_initial_order_and_social_limit():
    j, _ = run(P, lambda q: ["C", "D"], lambda q, i: "B")
    conf = j.route.path[12:20]
    tags = [j.route.chosen_tags[f] for f in j.route.initial_families]
    assert conf == [f"C{t}_1" for t in tags] + [f"C{t}_2" for t in tags]
    assert sum(f in P.social for f in j.route.initial_families) <= 1


def test_all_cards_reachable_and_unknown_unexposed():
    rng = random.Random(5)
    cards = set()
    for _ in range(1500):
        pick = {q: sorted(rng.sample("ABCD", rng.choice([1, 2]))) for q in P.explore_ids}
        j, _ = run(P, pick.__getitem__, lambda q, i: rng.choice("AABBCDX"))
        cards.add(j.card.type_id)
        asked = {P.confirm_by_id[q]["tagid"] for q in j.route.path if q.startswith("C")}
        assert set(j.tags) == asked  # 미노출 태그는 상태를 만들지 않음(unknown)
    assert cards >= {f"V{i:02d}" for i in range(1, 17)} | {"DISCOVERING"}


def test_explore_multi_select_order_invariant():
    a = {q: ["A", "C"] for q in P.explore_ids}
    b = {q: ["C", "A"] for q in P.explore_ids}
    assert route(P, a).path == route(P, b).path


def test_tag_pick_uses_selection_ratio():
    # G01: 1695는 탐색 노출 1회(E01-A), 3834는 2회(E05-B, E09-C).
    a = {q: ["N"] for q in P.explore_ids}
    a.update({"E01": ["A"], "E05": ["B"]})  # 1695 1/1 > 3834 1/2 → 선택 횟수가 같아도 비율로 1695
    assert route(P, a).chosen_tags.get("G01") == 1695
    a.update({"E01": ["N"], "E09": ["C"]})  # 3834 2/2 > 1695 0/1
    assert route(P, a).chosen_tags.get("G01") == 3834


def test_reserve_does_not_override_negative():
    a = {q: ["A"] for q in P.explore_ids}
    r = route(P, a)
    t = r.chosen_tags[r.initial_families[0]]
    for q in r.path[12:]:
        a[q] = "A"
    a[f"C{t}_1"], a[f"C{t}_2"] = "A", "D"  # 상반 → conditional → _3 보완
    r = route(P, a)
    assert f"C{t}_3" in r.path
    a[f"C{t}_3"] = "A"
    j = judge_v2(P, {k: v for k, v in a.items() if k in route(P, a).path or k.startswith("E")}, require_done=False)
    assert j.tags[t].state == "conditional"


def test_back_navigation_drops_out_of_path_answers():
    a = {q: ["A"] for q in P.explore_ids}
    r = route(P, a)
    for q in r.path[12:]:
        a[q] = "A"
    b = dict(a)
    b.update({q: ["B"] for q in P.explore_ids})  # 탐색을 바꾸면 경로 재계산
    nb = route(P, b)
    dropped = [q for q in a if q not in nb.path]
    assert dropped
    st = score_tags(P, b, nb.path)
    scored = {q for s in st.values() for q in s.questions}
    assert not scored & set(dropped)


def test_attempt_version_pinning(v2env):  # noqa: F811
    from gembti import db
    c, _ = v2env
    s = session(c)
    con = db.connect()  # 마이그레이션 4 이전처럼 진행 중인 v2.0 응시를 흉내
    con.execute("UPDATE v2_sessions SET attempt_version='v2.0' WHERE session_key=?", (s["session_key"],))
    old = c.post("/api/v2/route", json={**s, "answers": {"E01": ["A"]}}).json()
    assert old["questionnaire_version"] == "v2.0"
    assert old["next"]["text"] == get_v2("v2.0").explore_by_id["E02"]["text"]
    ans, r = walk(c, s, lambda q: ["A"] if q["stage"] == "explore" else "A")  # 새 응시(빈 답) → 현재 활성 은행
    assert r["questionnaire_version"] == active_version()
    sub = c.post("/api/v2/submit", json={**s, "answers": ans}).json()
    con.execute("UPDATE v2_sessions SET attempt_version='v2.0' WHERE session_key=?", (s["session_key"],))
    v = c.post(f"/api/v2/submissions/{sub['submission_id']}/view", json={**s}).json()
    assert v["card"] == sub["card"]  # 과거 제출은 자기 은행으로 조회
    assert {"v2.0", "v2.2"} <= set(BANKS)
    con.close()


def _base(p, conf):
    a = {q: ["A"] for q in p.explore_ids}
    r = route(p, a)
    for q in r.path[12:]:
        a[q] = conf
    return a, r


def test_regular_card_stops_after_one_reserve_without_new_family():
    """정규 카드 충족 + 상위 계열 태그 conditional → _3 1회 뒤 종료, 새 계열 없음, 상태 유지."""
    a, r = _base(P, "A")
    assert route(P, a).done and len(route(P, a).path) == 20  # 모두 긍정이면 20에서 종료
    t = r.chosen_tags[r.initial_families[0]]
    a[f"C{t}_2"] = "D"
    r2 = route(P, a)
    assert r2.extra_sets == [[f"C{t}_3"]]
    a[f"C{t}_3"] = "A"
    r3 = route(P, a)
    assert r3.done and len(r3.path) == 21 and set(r3.chosen_tags) == set(r.chosen_tags)
    assert judge_v2(P, a).tags[t].state == "conditional"


def test_regular_card_unknown_top_tag_keeps_unknown():
    a, r = _base(P, "A")
    t = r.chosen_tags[r.initial_families[0]]
    a[f"C{t}_2"] = "X"
    a[f"C{t}_3"] = "X"
    r3 = route(P, a)
    assert r3.done and len(r3.path) == 21 and judge_v2(P, a).tags[t].state == "unknown"


def test_card_unmet_opens_new_family_with_budget():
    a, r = _base(P, "C")  # 모두 중립 → 카드 미달
    r2 = route(P, a)
    new = [f for f in r2.chosen_tags if f not in r.chosen_tags]
    assert new and r2.extra_sets[0] == [f"C{r2.chosen_tags[new[0]]}_1", f"C{r2.chosen_tags[new[0]]}_2"]
