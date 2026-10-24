"""필터 조합별 통과 후보 수와 자동 완화 없음 검증 (활성 v2 카탈로그, 읽기 전용).

사용: GEMBTI_VAR_DIR=../var uv run python -m gembti.v2.filter_matrix
"""
from __future__ import annotations

import random

from gembti.policy import get_policy
from gembti.v2.catalog_v2 import load_active
from gembti.v2.engine import judge_v2, route
from gembti.v2.library_profile import family_weights
from gembti.v2.policy import get_v2
from gembti.v2.recommend import FiltersV2, filter_reasons, positive_ratio, recommend_v2

REVIEWS = (500, 5000, 50000, 100000)
POSITIVES = (75, 90, 95)


def main() -> None:
    p, v1, cat = get_v2(), get_policy(), load_active()
    print(f"스냅샷 {cat.snapshot_id} · 전체 {len(cat.games)}개")
    print("| 최소 리뷰 \\ 최소 긍정률 | " + " | ".join(f"{x}%" for x in POSITIVES) + " |")
    print("|---|" + "---:|" * len(POSITIVES))
    passing = {}
    for rv in REVIEWS:
        row = []
        for pos in POSITIVES:
            f = FiltersV2(min_reviews=rv, min_positive=pos)
            n = sum(1 for g in cat.games if not filter_reasons(v1, g, f, set()))
            passing[(rv, pos)] = n
            row.append(f"{n:,}")
        print(f"| {rv:,}개 이상 | " + " | ".join(row) + " |")
    print("\n계열별 W≥0.3 후보 수 (리뷰 5만·10만, 긍정률 75%)")
    for rv in (50000, 100000):
        f = FiltersV2(min_reviews=rv)
        cov = {fid: 0 for fid in p.family_by_id}
        for g in cat.games:
            if filter_reasons(v1, g, f, set()):
                continue
            for fid, w in family_weights(g["tags"], p.family_of).items():
                if w >= 0.3:
                    cov[fid] += 1
        print(f"  {rv:,}+: " + ", ".join(f"{k} {v}" for k, v in cov.items()))
    # 자동 완화 없음: 엄격한 조합에서 추천이 모두 조건을 지키고, 부족하면 실제 개수만 나온다
    rng = random.Random(7)
    viol = short = runs = 0
    for _ in range(200):
        a: dict[str, str | list[str]] = {q: sorted(rng.sample("ABCD", 2)) for q in p.explore_ids}
        while True:
            r = route(p, a)
            if r.done or r.next_question is None:
                break
            a[r.next_question] = rng.choice(["A", "B", "C"])
        j = judge_v2(p, a)
        for rv in REVIEWS:
            for pos in POSITIVES:
                f = FiltersV2(min_reviews=rv, min_positive=pos, korean_only=rng.random() < 0.3)
                res = recommend_v2(p, v1, j.tags, cat.games, f, set(), None, cat.idf)
                runs += 1
                for it in res["items"]:
                    g = cat.by_appid[it["appid"]]
                    ratio = positive_ratio(g) or 0.0
                    if g["total_reviews"] < rv or ratio < pos / 100 or (f.korean_only and g["korean"] != "true"):
                        viol += 1
                if len(res["items"]) < 5:
                    short += 1
                    assert len(res["items"]) == min(5, res["counts"]["candidates"]) or res["counts"]["candidates"] >= 5
    print(f"\n자동 완화 검사: 추천 실행 {runs}회, 조건 위반 0이어야 함 → {viol}, 5개 미만 실행 {short}회(실제 개수만 표시)")


if __name__ == "__main__":
    main()
