"""v2 추천·필터·라이브러리 프로필. 게임은 테스트 전용 가상 데이터."""
import math

import pytest

from gembti.policy import get_policy
from gembti.v2.engine import TagState
from gembti.v2.library_profile import build_profile, cosine
from gembti.v2.policy import get_v2
from gembti.v2.recommend import FiltersV2, recommend_v2

P = get_v2()
V1 = get_policy()


def ts(tid, state, norm):
    return TagState(tid, P.family_of[tid], [], 2, 2 if norm > 0 else 0, 2 if norm < 0 else 0, 0, norm, state, 1.0)


# 오픈월드(G01)·퍼즐(G06)·슈팅(G12) positive, 전략(G05) avoid
USER = {1695: ts(1695, "positive", 1.0), 1664: ts(1664, "positive", 0.75), 1774: ts(1774, "positive", 0.5),
        9: ts(9, "avoid", -1.0)}


def game(appid, tags, reviews=10000, pos=None, korean="true", series=None, score=8):
    pos = reviews * 9 // 10 if pos is None else pos
    return {"appid": appid, "name": f"t{appid}", "series_id": series or f"s{appid}", "korean": korean,
            "windows": "true", "mac": "false", "linux": "false", "total_reviews": reviews, "total_positive": pos,
            "review_score": score, "tags": [{"tagid": t, "weight": w, "rank": i + 1} for i, (t, w) in enumerate(tags)]}


DEFAULT = FiltersV2()


def rec(cat, f=DEFAULT, owned=frozenset(), lib=None):
    return recommend_v2(P, V1, USER, cat, f, set(owned), lib)


def kinds(r, k=None):
    return [i["appid"] for i in r["items"] if k is None or i["kind"] == k]


def test_qualify_0299_0300():
    r = rec([game(1, [(1695, 1000), (1664, 299)]), game(2, [(1695, 1000), (1664, 300)])])
    assert kinds(r, "personalized") == [2] and kinds(r, "reference") == [1]


def test_avoid_0499_0500():
    r = rec([game(1, [(1695, 1000), (1664, 900), (9, 499)]), game(2, [(1695, 1000), (1664, 900), (9, 500)])])
    assert kinds(r) == [1]
    assert r["items"][0]["penalty"] > 0
    assert r["counts"]["strong_avoid_excluded"] == 1


@pytest.mark.parametrize("n,thr,ok", [(499, 500, False), (500, 500, True), (999, 1000, False), (1000, 1000, True),
                                      (99999, 100000, False), (100000, 100000, True)])
def test_review_count_boundaries(n, thr, ok):
    r = rec([game(1, [(1695, 1000), (1664, 900)], reviews=n)], FiltersV2(min_reviews=thr))
    assert (kinds(r) == [1]) is ok


@pytest.mark.parametrize("pct", [75, 80, 90, 95])
def test_positive_ratio_boundaries_use_counts(pct):
    exact = game(1, [(1695, 1000), (1664, 900)], reviews=1000, pos=pct * 10)
    below = game(2, [(1695, 1000), (1664, 900)], reviews=1000, pos=pct * 10 - 1)
    r = rec([exact, below], FiltersV2(min_positive=pct))
    assert kinds(r) == [1]


def test_unknown_reviews_not_treated_as_zero_or_pass():
    g = game(1, [(1695, 1000), (1664, 900)])
    g["total_reviews"] = None
    g["total_positive"] = None
    r = rec([g])
    assert kinds(r) == [] and r["excluded"][0]["reasons"] == ["filter_min_reviews", "filter_min_positive"]


def test_invalid_presets_rejected():
    with pytest.raises(ValueError):
        FiltersV2(min_reviews=499)
    with pytest.raises(ValueError):
        FiltersV2(min_positive=70)


def test_owned_excluded_even_zero_minutes_and_toggle():
    cat = [game(1, [(1695, 1000), (1664, 900)]), game(2, [(1695, 1000), (1664, 800)])]
    r = rec(cat, owned={1})
    assert kinds(r) == [2] and r["counts"]["owned_excluded"] == 1
    assert set(kinds(rec(cat, FiltersV2(exclude_owned=False), owned={1}))) == {1, 2}


def test_library_reorders_but_cannot_grant_personalized():
    a = game(1, [(1695, 1000), (1664, 400)], reviews=5000)  # 개인화 자격 O
    b = game(2, [(1695, 1000), (1664, 400), (1752, 900)], reviews=5000)  # 리듬 강함
    c = game(3, [(1752, 1000), (699, 900)], reviews=900000)  # 질문 근거 없음, 라이브러리만 일치
    lib = {"G14": 1.0, "G15": 0.8}
    q_only = rec([a, b, c])
    with_lib = rec([a, b, c], lib=lib)
    assert kinds(with_lib, "personalized") == kinds(q_only, "personalized") or \
        set(kinds(with_lib, "personalized")) == {1, 2}
    assert 3 in kinds(with_lib, "reference") and 3 not in kinds(with_lib, "personalized")
    ref3 = next(i for i in with_lib["items"] if i["appid"] == 3)
    assert ref3["library_note"] is True
    item = next(i for i in with_lib["items"] if i["appid"] == 2)
    assert abs(item["final"] - (0.7 * item["Q"] + 0.3 * item["L"] - item["penalty"])) < 1e-5


def test_strong_avoid_excluded_even_with_high_library_match():
    g = game(1, [(9, 1000), (1695, 900), (1664, 800)])
    r = rec([g], lib={"G05": 1.0})
    assert kinds(r) == []


def test_no_duplicates_and_max_five():
    cat = [game(i, [(1695, 1000), (1664, 900), (1774, 500)], reviews=1000 + i) for i in range(1, 30)]
    ids = kinds(rec(cat))
    assert len(ids) == 5 and len(set(ids)) == 5


def test_counts_are_distinct():
    cat = [game(1, [(1695, 1000), (1664, 900)]), game(2, [(1695, 1000), (1664, 900)], korean="false"),
           game(3, [(1695, 1000), (1664, 900)]), game(4, [(9, 1000)])]
    r = rec(cat, FiltersV2(korean_only=True), owned={3})
    c = r["counts"]
    assert c["catalog_total"] == 4 and c["passing_filters"] == 3 and c["owned_excluded"] == 1
    assert c["strong_avoid_excluded"] == 1 and c["recommended"] == 1


# ---------------------------------------------------------------- 라이브러리 프로필

FAM = P.family_of


def meta(*tags):
    return [{"tagid": t, "weight": w, "rank": i + 1} for i, (t, w) in enumerate(tags)]


def test_profile_top20_log_weight_and_caps():
    owned = [{"appid": i, "minutes": (i + 1) * 600} for i in range(30)] + [{"appid": 999, "minutes": 0}]
    m = {i: meta((1695, 1000), (1774, 500)) for i in range(30)}
    prof = build_profile(owned, m, FAM, {}, set())
    assert prof.enabled and prof.analyzed_count == 20
    assert [r["appid"] for r in prof.analyzed][:2] == [29, 28]
    assert all(r["share"] <= 0.4 + 1e-9 for r in prof.analyzed)
    assert abs(sum(r["share"] for r in prof.analyzed) - 1) < 1e-9
    top = prof.analyzed[0]
    assert abs(top["raw"] - math.log1p(min(30 * 600 / 60, 300))) < 1e-9


def test_profile_game_cap_redistributes():
    owned = [{"appid": 1, "minutes": 300 * 60}, {"appid": 2, "minutes": 60}, {"appid": 3, "minutes": 60}]
    m = {a: meta((1695, 1000)) for a in (1, 2, 3)}
    prof = build_profile(owned, m, FAM, {}, set())
    shares = {r["appid"]: r["share"] for r in prof.analyzed}
    assert abs(shares[1] - 0.4) < 1e-9 and abs(shares[2] - 0.3) < 1e-9


def test_profile_gates():
    owned = [{"appid": a, "minutes": 600} for a in (1, 2, 3)]
    assert not build_profile(owned, {1: meta((1695, 1)), 2: meta((1695, 1))}, FAM, {}, set()).enabled
    owned4 = owned + [{"appid": 4, "minutes": 600 * 50}]
    p2 = build_profile(owned4, {a: meta((1695, 1)) for a in (1, 2, 3)}, FAM, {}, set())
    assert not p2.enabled and "가중치 비율" in p2.reason
    p3 = build_profile(owned, {a: meta((1695, 1)) for a in (1, 2, 3)}, FAM, {}, {1})
    assert not p3.enabled  # 분석 제외 후 태그 확보 2개


def test_profile_series_cap_infeasible_disables():
    # 4개 중 3개가 같은 시리즈 → 시리즈 40%로 줄이면 남은 1개가 60%가 되어 게임 상한과 동시 충족 불가
    owned = [{"appid": a, "minutes": 6000} for a in (1, 2, 3, 4)]
    m = {a: meta((1695, 1000)) for a in (1, 2, 3, 4)}
    prof = build_profile(owned, m, FAM, {1: "S", 2: "S", 3: "S"}, set())
    assert not prof.enabled and "상한" in prof.reason and prof.family_vector == {}


def test_profile_series_cap_feasible_and_zero_playtime_not_dislike():
    owned = [{"appid": a, "minutes": 6000} for a in range(1, 8)] + [{"appid": 99, "minutes": 0}]
    m = {a: meta((1695, 1000)) for a in range(1, 8)}
    m[99] = meta((1774, 1000))
    prof = build_profile(owned, m, FAM, {1: "S", 2: "S", 3: "S", 4: "S"}, set())
    shares = {r["appid"]: r["share"] for r in prof.analyzed}
    assert prof.enabled and abs(sum(shares[a] for a in (1, 2, 3, 4)) - 0.4) < 1e-9
    assert all(v <= 0.4 + 1e-9 for v in shares.values()) and abs(sum(shares.values()) - 1) < 1e-9
    assert "G12" not in prof.family_vector  # 0분 게임은 분석에서 빠질 뿐 음수로 쓰지 않음
    assert all(v >= 0 for v in prof.family_vector.values())


def test_cosine_range():
    assert cosine({"G01": 1}, {"G01": 0.5}) == 1.0 and cosine({"G01": 1}, {"G02": 1}) == 0.0
    assert cosine({}, {"G01": 1}) == 0.0
