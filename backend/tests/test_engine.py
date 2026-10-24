"""4.3 계산 예시, 5.3 기준 벡터, 5.4·6절 카드 분기."""
import json
import subprocess
import sys
from fractions import Fraction

import pytest

from gembti.engine import judge, normalize_answers, score_tags, session_id
from gembti.policy import load_policy

P = load_policy()
REF_SID = "aebe9d6167d4940468c88395079033c757188a9c98e2831234a4b4dc1836c846"


def all_x(**kw):
    a = {q: "X" for q in P.question_ids}
    a.update(kw)
    return a


def ref_answers():
    a = {q: "B" for q in P.question_ids}
    a.update(Q03="C", Q17="C", Q18="C")
    return a


# ---------------------------------------------------------------- 3절 배점표 (문서 값 직접 기재)

GENERAL = {"Q01": (1695, 3834), "Q02": (3834, 1695), "Q05": (599, 12472), "Q06": (12472, 599),
           "Q07": (1643, 7332), "Q08": (7332, 1643), "Q09": (9, 1741), "Q10": (1741, 9),
           "Q11": (1664, 6129), "Q12": (6129, 1664), "Q13": (1742, 6426), "Q14": (6426, 1742),
           "Q15": (1662, 1702), "Q16": (1702, 1662), "Q19": (32322, 1666), "Q20": (1666, 32322)}


def test_scoring_table_matches_document():
    prim = {"A": 4, "B": 2, "C": 0, "D": -2, "E": -4}
    sup = {"A": 2, "B": 1, "C": 0, "D": -1, "E": -2}
    for q, (p, s) in GENERAL.items():
        for c in "ABCDE":
            assert P.scoring[q][c] == {p: prim[c], s: sup[c]}, (q, c)
        assert "X" not in P.scoring[q]
    for c in "ABCDE":
        assert P.scoring["Q04"][c] == {1716: sup[c], 3959: sup[c]}
    assert P.scoring["Q03"] == {"A": {1716: 4, 3959: 0}, "B": {1716: 0, 3959: 4},
                                "C": {1716: 2, 3959: 2}, "D": {1716: -4, 3959: -4}}
    for q in ("Q17", "Q18"):
        assert P.scoring[q] == {"A": {1685: 2, 1775: 0}, "B": {1685: 0, 1775: 2},
                                "C": {1685: 1, 1775: 1}, "D": {1685: -2, 1775: -2}}


def test_tag_ids_and_coverage():
    assert [t.tagid for t in P.tags] == [1695, 3834, 1716, 3959, 599, 12472, 1643, 7332, 9, 1741,
                                         1664, 6129, 1742, 6426, 1662, 1702, 1685, 1775, 32322, 1666]
    for t in P.tags:
        assert len(t.questions) == 2


# ---------------------------------------------------------------- 4.3 계산 예시

def test_example_q01a_q02b():
    t = score_tags(P, normalize_answers(P, all_x(Q01="A", Q02="B")))
    assert t[1695].normalized == Fraction(5, 6) and t[1695].state == "positive"
    assert t[3834].normalized == Fraction(4, 6) and t[3834].state == "positive"


def test_example_q01a_q02x_unknown():
    t = score_tags(P, normalize_answers(P, all_x(Q01="A")))
    assert t[1695].n == 1 and t[1695].state == "unknown" and t[1695].normalized is None
    assert t[3834].state == "unknown"


def test_example_conditional():
    t = score_tags(P, normalize_answers(P, all_x(Q01="A", Q02="E")))
    assert t[1695].state == "conditional" and t[3834].state == "conditional"


def test_example_neutral_is_not_unknown():
    t = score_tags(P, normalize_answers(P, all_x(Q01="C", Q02="C")))
    assert t[1695].n == 2 and t[1695].normalized == 0 and t[1695].state == "neutral_or_weak"


def test_example_roguelite():
    t = score_tags(P, normalize_answers(P, all_x(Q03="B", Q04="A")))
    assert t[3959].normalized == 1 and t[3959].state == "positive"
    assert t[1716].normalized == Fraction(2, 6) and t[1716].state == "neutral_or_weak"
    assert t[1716].not_selected == ["Q03"]


def test_example_roguelike():
    t = score_tags(P, normalize_answers(P, all_x(Q03="A", Q04="A")))
    assert t[1716].normalized == 1 and t[1716].state == "positive"
    assert t[3959].normalized == Fraction(2, 6) and t[3959].state == "neutral_or_weak"


def test_example_social_cc():
    t = score_tags(P, normalize_answers(P, all_x(Q17="C", Q18="C")))
    assert t[1685].normalized == Fraction(1, 2) and t[1685].state == "positive"
    assert t[1775].normalized == Fraction(1, 2) and t[1775].state == "positive"


def test_avoid_requires_direct_negative_and_two_negatives():
    t = score_tags(P, normalize_answers(P, all_x(Q01="E", Q02="D")))
    assert t[1695].state == "avoid"  # (-4-1)/6
    assert t[3834].state == "avoid"  # (-2-2)/6 = -0.667
    t = score_tags(P, normalize_answers(P, all_x(Q01="C", Q02="E")))
    assert t[1695].state == "neutral_or_weak"  # 보조 음수만으로 기피 확정 금지


def test_x_excluded_from_denominator():
    t = score_tags(P, normalize_answers(P, all_x(Q09="A")))
    assert t[9].normalized is None and t[9].raw_sum == 4  # 분모를 줄여 고득점화하지 않음


def test_missing_answers_become_x_and_invalid_rejected():
    assert normalize_answers(P, {"Q01": "a"})["Q20"] == "X"
    with pytest.raises(ValueError):
        normalize_answers(P, {"Q03": "E"})  # 전용 선택지에 E 없음
    with pytest.raises(ValueError):
        normalize_answers(P, {"Q99": "A"})


# ---------------------------------------------------------------- 5.3 기준 벡터

def test_reference_vector():
    j = judge(P, ref_answers())
    assert j.session_id == REF_SID
    assert all(t.state == "positive" and t.normalized == Fraction(1, 2) and t.direct_strength == Fraction(1, 2)
               for t in j.tags.values())
    assert j.card.group_order == ["G10", "G11", "G08", "G07", "G04", "G01", "G06", "G09", "G03", "G02", "G05"]
    assert j.card.main_groups == ["G10", "G11", "G08"]
    assert j.card.main_tagids == [1775, 1666, 1662]
    assert j.card.type_id == "T14"
    assert j.card.extra_social_tagids == [1685]


def test_session_id_independent_of_input_order_and_case():
    a = ref_answers()
    rev = dict(reversed(list(a.items())))
    assert session_id(P, normalize_answers(P, rev)) == REF_SID
    assert session_id(P, normalize_answers(P, {k: v.lower() for k, v in a.items()})) == REF_SID


def test_session_id_stable_across_processes():
    code = ("import json,sys;from gembti.engine import judge;from gembti.policy import load_policy;"
            "j=judge(load_policy(),json.loads(sys.argv[1]));print(j.session_id, j.card.type_id)")
    out = subprocess.run([sys.executable, "-c", code, json.dumps(ref_answers())],
                         capture_output=True, text=True, check=True, env={"PYTHONHASHSEED": "123"})
    assert out.stdout.split() == [REF_SID, "T14"]


def test_tie_log_stages():
    j = judge(P, ref_answers())
    stages = {r["stage"] for r in j.card.tie_log}
    assert stages == {"tag_representative", "group_rank"}
    assert all(r["decided_by"] == "hash_order" for r in j.card.tie_log)
    # 11개 그룹 사이 같은 점수 쌍 = C(11,2)
    assert sum(1 for r in j.card.tie_log if r["stage"] == "group_rank") == 55


def test_rank_order_score_then_d_then_hash_then_id():
    from gembti.engine import Candidate, rank_with_log
    F = Fraction
    log = []
    c = [Candidate("A", F(1, 2), F(1, 2), "bb"), Candidate("B", F(1, 2), F(1), "ff"),
         Candidate("C", F(5, 6), F(0), "00"), Candidate("D", F(1, 2), F(1, 2), "aa"),
         Candidate("E", F(1, 2), F(1, 2), "aa")]
    order = [x.cid for x in rank_with_log("group_rank", c, "sid", log)]
    assert order == ["C", "B", "D", "E", "A"]
    decided = {tuple(sorted(x["id"] for x in r["candidates"])): r["decided_by"] for r in log}
    assert decided[("A", "B")] == "direct_strength"
    assert decided[("A", "D")] == "hash_order"
    assert decided[("D", "E")] == "hash_collision_fallback"
    assert not any("C" in k for k in decided)  # 점수가 다른 후보는 동점 비교에 넣지 않음


# ---------------------------------------------------------------- 5.4 / 6절 카드

def make(groups_pos: list[str]):
    """지정한 그룹들의 직접 태그를 강하게 positive로 만드는 응답."""
    a = all_x()
    prim = {"G01": ("Q01", "Q02"), "G03": ("Q05", "Q06"), "G04": ("Q07", "Q08"), "G05": ("Q09", "Q10"),
            "G06": ("Q11", "Q12"), "G07": ("Q13", "Q14"), "G08": ("Q15", "Q16"), "G11": ("Q19", "Q20")}
    for g in groups_pos:
        if g == "G02":
            a.update(Q03="A", Q04="A")
        elif g in ("G09", "G10"):
            a["Q17"] = "C" if {"G09", "G10"} <= set(groups_pos) else ("A" if g == "G09" else "B")
            a["Q18"] = a["Q17"]
        else:
            q1, q2 = prim[g]
            a.update({q1: "A", q2: "A"})
    return a


def test_g01_g09_g10_is_discovering():
    j = judge(P, make(["G01", "G09", "G10"]))
    assert len(j.card.positive_tagids) == 4  # 오픈월드·탐험·협동·PvP
    assert j.card.independent_evidence == 2 and j.card.independent_evidence_unmerged == 3
    assert j.card.is_discovering and j.card.type_id == "DISCOVERING"


def test_g01_g02_g09_g10_is_regular():
    j = judge(P, make(["G01", "G02", "G09", "G10"]))
    assert j.card.independent_evidence == 3 and not j.card.is_discovering
    social = [g for g in j.card.main_groups if g in ("G09", "G10")]
    assert len(social) <= 1 and len(j.card.main_groups) == 3


def test_combo_types():
    for groups, tid in ((["G03", "G04", "G05"], None), (["G01", "G08", "G06"], None)):
        j = judge(P, make(groups))
        assert not j.card.is_discovering
        pair = set(j.card.main_groups[:2])
        combos = {frozenset(["G03", "G04"]): "T01", frozenset(["G01", "G08"]): "T02",
                  frozenset(["G02", "G11"]): "T03", frozenset(["G06", "G07"]): "T04"}
        expected = combos.get(frozenset(pair))
        tops = {"G01": "T05", "G02": "T06", "G03": "T07", "G04": "T08", "G05": "T09", "G06": "T10",
                "G07": "T11", "G08": "T12", "G09": "T13", "G10": "T14", "G11": "T15"}
        assert j.card.type_id == (expected or tops[j.card.main_groups[0]])


def test_score_beats_hash():
    # G05 강함(A,A: 전략 (4+2)/6=1), G01 약함(B,B: 0.5) -> 점수 높은 G05가 해시와 무관하게 1위
    a = all_x(Q09="A", Q10="A", Q01="B", Q02="B", Q11="A", Q12="A")
    a["Q11"], a["Q12"] = "B", "A"
    j = judge(P, a)
    assert j.card.group_order[0] == "G05"


def test_all_x_is_discovering_with_no_tags():
    j = judge(P, {})
    assert j.card.is_discovering and j.card.positive_tagids == []
    assert all(t.state == "unknown" for t in j.tags.values())


def test_free_text_not_an_input():
    import inspect

    from gembti import engine
    assert "free" not in inspect.signature(engine.judge).parameters
