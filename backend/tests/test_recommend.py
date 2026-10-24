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


# ---------------------------------------------------------------- algorithm v1.3: 개인화 자격 W≥0.3

def test_qualify_boundary_0299_vs_0300():
    # 오픈월드 W=1.0(G01) + 퍼즐 W=0.299 → 자격 그룹 1개 → 참고
    a = game(1, [(1695, 1000), (1664, 299)])
    b = game(2, [(1695, 1000), (1664, 300)])  # 퍼즐 W=0.300 → 자격 그룹 2개 → 개인화
    res = recommend(P, USER, [a, b], Filters(), set())
    assert ids(res, "personalized") == [2] and ids(res, "reference") == [1]
    item = next(r for r in res["items"] if r["appid"] == 2)
    assert item["qualified_tags"] == [1664, 1695]


def test_same_group_counts_once():
    # 오픈월드·탐험(같은 G01) 둘 다 W≥0.3이어도 그룹 1개 → 개인화 아님
    g = game(1, [(1695, 1000), (3834, 900)])
    res = recommend(P, USER, [g], Filters(), set())
    assert ids(res, "personalized") == [] and ids(res, "reference") == [1]


def test_score_formula_unchanged_by_qualification():
    # 약한 일치(W<0.3)도 점수 기여는 그대로 (점수식 유지)
    g = game(1, [(1695, 1000), (1664, 500), (1742, 100)])
    res = recommend(P, USER, [g], Filters(), set())
    from gembti.recommend import compute_idf
    idf = compute_idf(P, [g])
    exp = 1.0 * idf[1695] * 1 + 1.0 * idf[1664] * 0.5 + 1.0 * idf[1742] * 0.1
    assert abs(res["items"][0]["score"] - round(exp, 6)) < 1e-5
    assert res["items"][0]["qualified_tags"] == [1664, 1695]


def test_social_groups_still_separate():
    u = judge(P, {"Q17": "C", "Q18": "C"}).tags  # 협동·PvP positive
    g = game(1, [(1685, 1000), (1775, 400)])
    assert ids(recommend(P, u, [g], Filters(), set()), "personalized") == [1]


# ---------------------------------------------------------------- 콘텐츠 적격성

def test_content_exclusion_applies_to_all_lists():
    excluded = game(2495450, [(1695, 1000), (1664, 900)], reviews=10 ** 6)  # 검토 결과 exclude
    held = game(3464370, [(1695, 1000), (1664, 900)], reviews=10 ** 6)  # hold
    pending = game(777, [(1695, 1000), (1664, 900), (12095, 300)], reviews=10 ** 6)  # 검토 기록 없음 + 트리거 태그
    kept = game(1920960, [(1695, 1000), (1664, 900), (9130, 200)], reviews=10)  # keep: 태그만으로 제외 안 함
    ok = game(5, [(1695, 1000), (1664, 900)])
    cat = [excluded, held, pending, kept, ok]
    res = recommend(P, USER, cat, Filters(), set())
    assert set(ids(res)) == {1920960, 5}
    reasons = {e["appid"]: e["reasons"] for e in res["excluded"]}
    assert reasons[2495450] == ["content_excluded"] and reasons[3464370] == ["content_hold"]
    assert reasons[777] == ["content_review_pending"]
    assert set(popularity_baseline(cat, Filters(), set(), P)) == {1920960, 5}
    from gembti.recommend import full_personalized_ranking
    assert set(full_personalized_ranking(P, USER, cat, Filters(), set())) == {1920960, 5}
