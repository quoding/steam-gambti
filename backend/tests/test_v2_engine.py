"""v2 분기·배점·유형: 8.2 기술 게이트 중 질문 관련 항목."""
import random

import pytest

from gembti.v2.engine import judge_v2, route, score_tags, session_id_v2
from gembti.v2.policy import get_v2

P = get_v2()


def run(explore: dict, confirm_fn, rng=None) -> dict:
    """탐색 응답을 넣고, 경로가 묻는 확인 문항에 confirm_fn(qid)로 답하며 끝까지 진행."""
    ans = dict(explore)
    for _ in range(40):
        r = route(P, ans)
        if r.done:
            return ans
        ans[r.next_question] = confirm_fn(r.next_question) if r.next_question.startswith("C") else \
            explore.get(r.next_question, ["X"])
    raise AssertionError("경로가 끝나지 않음")


def rand_explore(rng):
    out = {}
    for qid in P.explore_ids:
        k = rng.random()
        out[qid] = ["N"] if k < 0.1 else ["X"] if k < 0.15 else sorted(rng.sample("ABCD", rng.choice([1, 2])))
    return out


def test_random_paths_are_20_to_24_without_duplicates():
    rng = random.Random(1)
    lengths = {}
    for _ in range(3000):
        ex = rand_explore(rng)
        ans = run(ex, lambda q: rng.choice("ABCDEX"))
        r = route(P, ans)
        conf = [q for q in r.path if q.startswith("C")]
        total = 12 + len(conf)
        assert 20 <= total <= 24, total
        assert len(r.path) == len(set(r.path))
        assert all(q in P.confirm_by_id or q in P.explore_by_id for q in r.path)
        social = [f for f in r.initial_families if f in P.social]
        assert len(social) <= 1 and len(r.initial_families) == 4
        lengths[total] = lengths.get(total, 0) + 1
    assert set(lengths) <= {20, 21, 22, 23, 24} and 20 in lengths and 24 in lengths  # 세트당 최대 2문항


def test_explore_answers_never_set_tag_states():
    ex = {q: ["A", "B"] for q in P.explore_ids}
    r = route(P, ex)
    st = score_tags(P, ex, r.path)
    assert all(s.state == "unknown" and s.n == 0 for s in st.values())


def test_not_selected_options_get_no_negative():
    from gembti.v2.engine import interest
    fam, _ = interest(P, {q: ["N"] for q in P.explore_ids})
    assert fam == {}
    fam, _ = interest(P, {"E01": ["A"], **{q: ["X"] for q in P.explore_ids[1:]}})
    assert fam == {"G01": 1}


def test_same_question_multi_select_counts_family_once():
    from gembti.v2.engine import interest
    # E01 A·B는 서로 다른 계열(G01, G05) → 각 1
    fam, _ = interest(P, {"E01": ["A", "B"]})
    assert fam == {"G01": 1, "G05": 1}


def test_top_interest_families_are_confirmed_and_slot_is_other():
    # G12(슈팅)를 세 문항 모두에서 선택
    ex = {q: ["X"] for q in P.explore_ids}
    for q in P.explore["questions"]:
        for o in q["options"]:
            if o["family_id"] in ("G12", "G06"):
                ex[q["question_id"]] = sorted(set(ex[q["question_id"]]) - {"X"} | {o["code"]})
    r = route(P, ex)
    assert set(r.interest_families) == {"G12", "G06"}  # 흥미 있는 계열만 흥미 슬롯
    assert r.explore_slot_family not in {"G12", "G06"}


def test_all_unknown_explore_has_no_fixed_id_priority():
    firsts = set()
    for k in range(40):
        ex = {q: ["X"] for q in P.explore_ids}
        ex["E12"] = ["N"] if k % 2 else ["X"]
        ex["E11"] = [["N"], ["X"]][k % 2]
        ex["E10"] = ["A"] if k >= 20 else ["X"]
        firsts.add(tuple(route(P, ex).initial_families))
    assert len(firsts) > 1  # 응답이 다르면 순서도 해시로 달라지며 G01 고정이 아니다


def test_back_edit_recomputes_path_and_drops_off_path():
    rng = random.Random(5)
    ex = rand_explore(rng)
    ans = run(ex, lambda q: "A")
    j1 = judge_v2(P, ans)
    ex2 = dict(ex)
    ex2["E01"] = ["N"] if ex["E01"] != ["N"] else ["A"]
    ex2["E05"] = ["X"] if ex["E05"] != ["X"] else ["B"]
    changed = {**ans, **ex2}
    r2 = route(P, changed)
    if not r2.done:
        changed = run(changed, lambda q: changed.get(q) or "A")
    j2 = judge_v2(P, changed)
    assert set(j2.answers) == set(route(P, changed).path) & set(changed)
    assert all(d not in j2.route.path for d in j2.dropped)
    assert j1.session_id != j2.session_id


def test_determinism_and_input_order():
    rng = random.Random(9)
    ex = rand_explore(rng)
    ans = run(ex, lambda q: rng.choice("ABCDEX"))
    rev = dict(reversed(list(ans.items())))
    a, b = judge_v2(P, ans), judge_v2(P, rev)
    assert a.session_id == b.session_id and a.card == b.card
    lower = {k: ([c.lower() for c in v] if isinstance(v, list) else v.lower()) for k, v in ans.items()}
    assert judge_v2(P, lower).session_id == a.session_id


def test_x_and_unexposed_are_distinct_in_canonical():
    ex = {q: ["X"] for q in P.explore_ids}
    r = route(P, ex)
    a = {**ex, **{q: "X" for q in r.path if q.startswith("C")}}
    sid_x = session_id_v2(P, a, r.path)
    b = dict(a)
    del b[r.path[-1]]
    assert sid_x != session_id_v2(P, b, r.path)
    st = score_tags(P, a, r.path)
    assert all(s.state == "unknown" and s.normalized is None for s in st.values())  # X를 0으로 바꾸지 않음


def test_state_rules():
    ex = {q: ["X"] for q in P.explore_ids}
    r = route(P, ex)
    t = r.chosen_tags[r.initial_families[0]]
    q1, q2 = P.confirm_by_tag[t][:2]
    def st(c1, c2):
        return score_tags(P, {**ex, q1: c1, q2: c2}, r.path)[t]
    assert st("A", "B").state == "positive"  # (2+1)/4 = 0.75
    assert st("B", "B").state == "positive"  # 0.5
    assert st("B", "C").state == "neutral_or_weak"  # 양수 1개
    assert st("A", "D").state == "conditional"
    assert st("E", "D").state == "avoid"
    assert st("D", "D").state == "avoid"  # -0.5
    assert st("A", "X").state == "unknown" and st("A", "X").n == 1


def test_judge_requires_complete_path():
    with pytest.raises(ValueError):
        judge_v2(P, {"E01": ["A"]})
    with pytest.raises(ValueError):
        judge_v2(P, {"E01": ["A", "B", "C"]})
    with pytest.raises(ValueError):
        judge_v2(P, {"Q01": "A"})


def _answer_for_family(target: str) -> dict:
    """target 계열을 최상위 positive로 만드는 응답을 탐색으로 찾는다."""
    rng = random.Random(hash(target) % 1000)
    for _ in range(4000):
        ex = {}
        for q in P.explore["questions"]:
            opts = [o["code"] for o in q["options"] if o["family_id"] == target]
            others = [o["code"] for o in q["options"] if o["code"] in "ABCD" and o["family_id"] != target]
            ex[q["question_id"]] = sorted(set(opts + [rng.choice(others)])) if opts else [rng.choice(others)]
        r = route(P, ex)
        if target not in r.initial_families:
            continue
        tgt = r.chosen_tags[target]
        ans = run(ex, lambda q, tgt=tgt: "A" if P.confirm_by_id[q]["tagid"] == tgt else "B")
        j = judge_v2(P, ans)
        if not j.card.is_discovering and j.card.family_order[0] == target:
            return ans
    raise AssertionError(f"{target} 카드에 도달하는 경로를 찾지 못함")


@pytest.mark.parametrize("fam", [f"G{i:02d}" for i in range(1, 17)])
def test_every_card_is_reachable(fam):
    j = judge_v2(P, _answer_for_family(fam))
    card = next(c for c in P.cards["cards"] if c["family_id"] == fam)
    assert j.card.type_id == card["type_id"]
    assert len(j.card.main_tagids) <= 3
    assert sum(1 for t in j.card.main_tagids if P.family_of[t] in P.social) <= 1


def test_discovering_when_evidence_short():
    ex = {q: ["X"] for q in P.explore_ids}
    ans = run(ex, lambda q: "C")
    j = judge_v2(P, ans)
    assert j.card.is_discovering and j.card.positive_tagids == []
    conf = [q for q in j.route.path if q.startswith("C")]
    assert 8 <= len(conf) <= 12
