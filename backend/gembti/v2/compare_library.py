"""질문 단독 vs 질문+라이브러리 비교 (8.3). 같은 v2 응답·같은 후보·같은 필터만 비교한다.

실제 사용자 라이브러리가 없을 때는 `--synthetic`으로 카탈로그 게임을 무작위로 골라 만든 합성 라이브러리를 쓴다.
합성 결과는 경로·변화량 진단일 뿐 사용자 만족도나 효과 근거가 아니다.

사용: uv run python -m gembti.v2.compare_library --synthetic --n 300
"""
from __future__ import annotations

import argparse
import random
import statistics

from gembti.policy import get_policy
from gembti.v2.catalog_v2 import load_active
from gembti.v2.engine import judge_v2, route
from gembti.v2.library_profile import build_profile
from gembti.v2.policy import get_v2
from gembti.v2.recommend import FiltersV2, recommend_v2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261024)
    args = ap.parse_args()
    p, v1, cat = get_v2(), get_policy(), load_active()
    rng = random.Random(args.seed)
    series = {g["appid"]: g["series_id"] for g in cat.games}
    overlaps, changed, pers_q, pers_ql, disabled = [], 0, [], [], 0
    for _ in range(args.n):
        a: dict[str, str | list[str]] = {q: sorted(rng.sample("ABCD", rng.choice([1, 2]))) for q in p.explore_ids}
        while True:
            r = route(p, a)
            if r.done or r.next_question is None:
                break
            a[r.next_question] = rng.choice(["A", "A", "B", "B", "C"])
        j = judge_v2(p, a)
        lib_games = rng.sample(cat.games, 15)
        owned = [{"appid": g["appid"], "minutes": rng.randint(30, 20000)} for g in lib_games]
        prof = build_profile(owned, {g["appid"]: g["tags"] for g in lib_games}, p.family_of, series, set())
        if not prof.enabled:
            disabled += 1
            continue
        f = FiltersV2(exclude_owned=False)  # 같은 후보 풀 비교를 위해 보유 제외는 양쪽 모두 끔
        q = recommend_v2(p, v1, j.tags, cat.games, f, set(), None, cat.idf)
        ql = recommend_v2(p, v1, j.tags, cat.games, f, set(), prof.family_vector, cat.idf)
        a_ids = [i["appid"] for i in q["items"]]
        b_ids = [i["appid"] for i in ql["items"]]
        overlaps.append(len(set(a_ids) & set(b_ids)) / max(1, len(a_ids)))
        changed += a_ids != b_ids
        pers_q.append(q["counts"]["personalized"])
        pers_ql.append(ql["counts"]["personalized"])
    n = len(overlaps)
    print(f"[합성 라이브러리 진단 · 실제 사용자 아님] 스냅샷 {cat.snapshot_id}, 응답 {args.n}, 프로필 비활성 {disabled}, 비교 {n}")
    if n:
        print(f"Top5 겹침 평균 {statistics.mean(overlaps):.2f}, 목록이 달라진 비율 {changed / n:.0%}, "
              f"개인화 수 평균 질문만 {statistics.mean(pers_q):.2f} / 질문+라이브러리 {statistics.mean(pers_ql):.2f}"
              " (라이브러리는 개인화 자격을 바꾸지 않으므로 같아야 함)")


if __name__ == "__main__":
    main()
