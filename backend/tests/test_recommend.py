"""10절 추천 규칙. 여기 게임은 테스트 전용 가상 데이터이며 서비스 카탈로그가 아니다."""
from gembti.engine import judge
from gembti.policy import load_policy
from gembti.recommend import Filters, compute_idf, popularity_baseline, recommend

P = load_policy()

# 사용자: 오픈월드/탐험(G01)·퍼즐/논리(G06)·스토리(G07) positive, 전략(G05)·시뮬(G03) avoid
USER = judge(P, {"Q01": "A", "Q02": "A", "Q11": "A", "Q12": "A", "Q13": "A", "Q14": "A",
                 "Q09": "E", "Q10": "E", "Q05": "E", "Q06": "E"}).tags


def game(appid, tags, korean="true", reviews=1000, pos=90, series=None, mac="false"):
    return {"appid": appid, "name": f"test-{appid}", "series_id": series or f"app:{appid}", "korean": korean,
            "windows": "true", "mac": mac, "linux": "false", "review_count": reviews, "percent_positive": pos,
            "tags": [{"tagid": t, "weight": w, "rank": i + 1} for i, (t, w) in enumerate(tags)]}


def ids(res, kind=None):
    return [r["appid"] for r in res["items"] if kind is None or r["kind"] == kind]


def test_user_states():
    assert USER[9].state == "avoid" and USER[599].state == "avoid" and USER[1695].state == "positive"


def test_w_049_penalty_w_050_excluded():
    cat = [game(1, [(1695, 100), (1664, 90), (9, 49)]), game(2, [(1695, 100), (1664, 90), (9, 50)])]
    res = recommend(P, USER, cat, Filters(), set())
    assert ids(res) == [1]
    ex = {e["appid"]: e for e in res["excluded"]}
    assert ex[2]["reasons"] == ["strong_avoid"] and ex[2]["strong_avoid"][0]["W"] == 0.5
    assert res["items"][0]["penalty"] > 0 and res["weak_penalty_only"] == [1]
    assert res["items"][0]["kind"] == "personalized"


def test_each_avoid_tag_checked_separately():
    # 전략 0.20 + 시뮬레이션 0.60 → 시뮬레이션 강한 기피로 제외
    cat = [game(1, [(1695, 100), (1664, 90), (9, 20), (599, 60)])]
    res = recommend(P, USER, cat, Filters(), set())
    assert ids(res) == []
    assert res["excluded"][0]["strong_avoid"] == [{"tagid": 599, "W": 0.6}]


def test_threshold_not_scaled_by_idf_or_strength():
    # W=0.49는 IDF(최대 2)와 강도 1을 곱하면 0.98이지만 제외하지 않는다
    cat = [game(1, [(1695, 100), (1664, 90), (9, 49)])]
    assert ids(recommend(P, USER, cat, Filters(), set())) == [1]


def test_weak_group_max_penalty_only():
    # 같은 그룹 약한 기피 두 개(전략·턴제 전략) → 그룹 최대 하나만 반영
    g = game(1, [(1695, 100), (1664, 90), (9, 40), (1741, 30)])
    res = recommend(P, USER, [g], Filters(), set())
    idf = compute_idf(P, [g])
    assert abs(res["items"][0]["penalty"] - 1.0 * 0.4 * idf[9]) < 1e-6


def test_personalized_needs_two_groups_else_reference():
    cat = [game(1, [(1695, 100), (3834, 90)]),  # G01만 → 참고
           game(2, [(1695, 100), (1664, 80)])]  # G01+G06 → 개인화
    res = recommend(P, USER, cat, Filters(), set())
    assert ids(res, "personalized") == [2] and ids(res, "reference") == [1]
    assert res["shortfall"] == 3


def test_filters_and_played_never_relaxed():
    cat = [game(1, [(1695, 100), (1664, 80)], korean="false"),
           game(2, [(1695, 100), (1664, 80)], korean="unknown"),
           game(3, [(1695, 100), (1664, 80)]),
           game(4, [(1695, 100), (1664, 80)], mac="true")]
    res = recommend(P, USER, cat, Filters(korean_only=True), {3})
    assert ids(res) == [4]
    assert ids(recommend(P, USER, cat, Filters(os="mac"), set())) == [4]
    assert ids(recommend(P, USER, cat, Filters(), set())) == [1, 2, 3, 4]  # 기본 필터 꺼짐


def test_no_duplicates_series_limit_and_max_five():
    cat = [game(i, [(1695, 100), (1664, 80), (1742, 70)], series="s1" if i < 4 else None, reviews=9000 - i)
           for i in range(1, 12)]
    res = recommend(P, USER, cat, Filters(), set())
    got = ids(res)
    assert len(got) == 5 and len(set(got)) == 5
    assert sum(1 for a in got if a < 4) == 1


def test_jaccard_hold_then_release():
    a = game(1, [(1695, 100), (1664, 90)], pos=99)
    b = game(2, [(1695, 100), (1664, 90)], pos=98)   # a와 그룹 집합 동일 → 보류
    c = game(3, [(1695, 100), (1742, 50)], pos=50)   # 점수 낮지만 다른 조합
    res = recommend(P, USER, [a, b, c], Filters(), set())
    assert set(ids(res, "personalized")) == {1, 2, 3}  # 5개 미만이라 보류 해제


def test_reference_order_penalty_then_reviews():
    cat = [game(1, [(3834, 100), (9, 40)], reviews=90000),
           game(2, [(3834, 100)], reviews=1000),
           game(3, [(3834, 100)], reviews=5000)]
    res = recommend(P, USER, cat, Filters(), set())
    assert ids(res, "reference") == [3, 2, 1]


def test_popularity_baseline_ignores_answers():
    cat = [game(1, [(9, 100)], reviews=900), game(2, [(1695, 100)], reviews=500)]
    assert popularity_baseline(cat, Filters(), set()) == [1, 2]  # 기피(전략)여도 제외하지 않음


def test_idf_bounds():
    cat = [game(i, [(1695, 100)]) for i in range(300)]
    idf = compute_idf(P, cat)
    assert idf[1695] == 1.0 and idf[1666] == 2.0
