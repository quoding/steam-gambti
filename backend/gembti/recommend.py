"""고정 배점 추천(10절)·서비스 참고 추천·순수 인기 기준선(10.3).

카탈로그는 메모리의 게임 dict 목록이다: appid, name, series_id, korean, windows/mac/linux,
review_count, percent_positive, tags=[{tagid, weight, rank}].
유형 이름은 입력으로 받지 않는다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from gembti.engine import TagResult
from gembti.policy import Policy, content_status

MAX_SLOTS = 5
STRONG_AVOID_W = 0.5
AVOID_FACTOR = 1.25
JACCARD_HOLD = 0.8
QUALIFY_W = 0.3  # algorithm v1.3: 개인화 자격은 그룹별 일치 태그 W≥0.3
OS_KEYS = {"windows": "windows", "mac": "mac", "linux": "linux"}


@dataclass(frozen=True)
class Filters:
    korean_only: bool = False
    os: str = "windows"

    def __post_init__(self):
        if self.os not in OS_KEYS:
            raise ValueError("os는 windows/mac/linux")


def weights(game: dict) -> tuple[dict[int, float], bool]:
    """W(g,t). (W, fallback 사용 여부)."""
    ws = [t["weight"] for t in game["tags"] if t.get("weight")]
    if ws:
        m = max(ws)
        return {t["tagid"]: (t.get("weight") or 0) / m for t in game["tags"]}, False
    return {t["tagid"]: 1 / t["rank"] for t in game["tags"]}, True


def compute_idf(policy: Policy, catalog: list[dict]) -> dict[int, float]:
    out = {}
    for tag in policy.tags:
        df = sum(1 for g in catalog if any(t["tagid"] == tag.tagid for t in g["tags"]))
        out[tag.tagid] = min(2.0, max(1.0, math.log(301 / (df + 1)) + 1))
    return out


def passes_filters(game: dict, f: Filters, policy: Policy | None = None) -> list[str]:
    reasons = []
    if policy is not None:
        cs = content_status(policy, game)
        if cs:
            reasons.append(cs)
    if f.korean_only and game["korean"] != "true":
        reasons.append("filter_korean")
    if game[OS_KEYS[f.os]] != "true":
        reasons.append(f"filter_os_{f.os}")
    return reasons


def _game_groups(policy: Policy, game: dict) -> set[str]:
    return {policy.tag_by_id[t["tagid"]].group_id for t in game["tags"] if t["tagid"] in policy.tag_by_id}


def evaluate_game(policy: Policy, game: dict, user: dict[int, TagResult], idf: dict[int, float]) -> dict:
    W, fallback = weights(game)
    pos: dict[str, dict] = {}
    qualified: dict[str, int] = {}  # 그룹 -> W≥0.3인 일치 태그(그룹당 1회)
    neg: dict[str, dict] = {}
    strong = []
    weak = []
    for tid, w in W.items():
        tr = user.get(tid)
        if tr is None or w <= 0:
            continue
        gid = tr.group_id
        if tr.state == "positive" and tr.normalized is not None:
            c = float(tr.normalized) * w * idf[tid]
            if gid not in pos or c > pos[gid]["contribution"]:
                pos[gid] = {"tagid": tid, "W": w, "contribution": c}
            if w >= QUALIFY_W and (gid not in qualified or w > W[qualified[gid]]):
                qualified[gid] = tid
        elif tr.state == "avoid" and tr.normalized is not None:
            # 강한 기피 판정은 태그별 W만으로 (IDF·사용자 강도 미적용)
            if w >= STRONG_AVOID_W:
                strong.append({"tagid": tid, "W": w})
            else:
                weak.append({"tagid": tid, "W": w})
            c = abs(float(tr.normalized)) * w * idf[tid]
            if gid not in neg or c > neg[gid]["contribution"]:
                neg[gid] = {"tagid": tid, "W": w, "contribution": c}
    pos_sum = sum(v["contribution"] for v in pos.values())
    neg_sum = sum(v["contribution"] for v in neg.values())
    return {
        "appid": game["appid"],
        "matched_positive": pos, "qualified": qualified, "avoid_contrib": neg,
        "strong_avoid": strong, "weak_avoid": weak,
        "score": pos_sum - AVOID_FACTOR * neg_sum,
        "penalty": neg_sum,
        "w_fallback": fallback,
        "groups": _game_groups(policy, game),
    }


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def recommend(policy: Policy, user: dict[int, TagResult], catalog: list[dict], filters: Filters,
              played: set[int], idf: dict[int, float] | None = None) -> dict:
    idf = idf or compute_idf(policy, catalog)
    excluded = []
    pool = []
    for g in catalog:
        if g["appid"] in played:
            excluded.append({"appid": g["appid"], "reasons": ["played"]})
            continue
        fr = passes_filters(g, filters, policy)
        if fr:
            excluded.append({"appid": g["appid"], "reasons": fr})
            continue
        ev = evaluate_game(policy, g, user, idf)
        if ev["strong_avoid"]:
            excluded.append({"appid": g["appid"], "reasons": ["strong_avoid"], "strong_avoid": ev["strong_avoid"]})
            continue
        pool.append((g, ev))

    def pers_key(item):
        g, ev = item
        return (-ev["score"], -(g["percent_positive"] or 0), -(g["review_count"] or 0), g["appid"])

    eligible = sorted([x for x in pool if len(x[1]["qualified"]) >= 2], key=pers_key)

    picked: list[tuple[dict, dict]] = []
    series_used: set[str] = set()
    held = []
    for g, ev in eligible:
        if len(picked) == MAX_SLOTS:
            break
        if g["series_id"] in series_used:
            continue
        if any(_jaccard(ev["groups"], pev["groups"]) >= JACCARD_HOLD for _, pev in picked):
            held.append((g, ev))
            continue
        picked.append((g, ev))
        series_used.add(g["series_id"])
    for g, ev in held:  # Jaccard 보류만 풀어 재검토 (시리즈·필터·강한 기피는 유지)
        if len(picked) == MAX_SLOTS:
            break
        if g["series_id"] in series_used:
            continue
        picked.append((g, ev))
        series_used.add(g["series_id"])
    picked.sort(key=pers_key)

    picked_ids = {g["appid"] for g, _ in picked}
    ref_pool = sorted([x for x in pool if x[0]["appid"] not in picked_ids],
                      key=lambda x: (x[1]["penalty"], -(x[0]["review_count"] or 0),
                                     -(x[0]["percent_positive"] or 0), x[0]["appid"]))
    reference: list[tuple[dict, dict]] = []
    for g, ev in ref_pool:
        if len(picked) + len(reference) == MAX_SLOTS:
            break
        if g["series_id"] in series_used:
            continue
        reference.append((g, ev))
        series_used.add(g["series_id"])

    def row(kind, rank, g, ev):
        return {"rank": rank, "kind": kind, "appid": g["appid"],
                "matched_tags": sorted(v["tagid"] for v in ev["matched_positive"].values()),
                "qualified_tags": sorted(ev["qualified"].values()),
                "matched_groups": sorted(ev["matched_positive"]),
                "contributions": {k: round(v["contribution"], 6) for k, v in ev["matched_positive"].items()},
                "weak_avoid": ev["weak_avoid"], "penalty": round(ev["penalty"], 6),
                "score": round(ev["score"], 6), "w_fallback": ev["w_fallback"]}

    items = [row("personalized", i + 1, g, ev) for i, (g, ev) in enumerate(picked)]
    items += [row("reference", len(items) + i + 1, g, ev) for i, (g, ev) in enumerate(reference)]
    return {
        "items": items,
        "personalized_count": len(picked),
        "reference_count": len(reference),
        "shortfall": MAX_SLOTS - len(items),
        "excluded": excluded,
        "weak_penalty_only": sorted(ev["appid"] for _, ev in pool if ev["weak_avoid"]),
        "candidate_count": len(pool),
        "algorithm_version": policy.versions["algorithm_version"],
    }


def popularity_baseline(catalog: list[dict], filters: Filters, played: set[int],
                        policy: Policy | None = None) -> list[int]:
    """순수 인기 기준선: 응답·추론 태그를 읽지 않는다. 기피 제외·패널티 없음.
    콘텐츠 적격성(성적 콘텐츠 제외)은 양쪽 목록에 같게 적용한다."""
    pool = [g for g in catalog if g["appid"] not in played and not passes_filters(g, filters, policy)]
    pool.sort(key=lambda g: (-(g["review_count"] or 0), -(g["percent_positive"] or 0), g["appid"]))
    out: list[int] = []
    series: set[str] = set()
    for g in pool:
        if len(out) == MAX_SLOTS:
            break
        if g["series_id"] in series:
            continue
        out.append(g["appid"])
        series.add(g["series_id"])
    return out


def full_personalized_ranking(policy: Policy, user: dict[int, TagResult], catalog: list[dict],
                              filters: Filters, played: set[int], idf: dict[int, float] | None = None) -> list[int]:
    """평가용(12.3 Hit@K): 다양성 처리 전 개인화 자격 게임 전체 순위."""
    idf = idf or compute_idf(policy, catalog)
    rows = []
    for g in catalog:
        if g["appid"] in played or passes_filters(g, filters, policy):
            continue
        ev = evaluate_game(policy, g, user, idf)
        if ev["strong_avoid"] or len(ev["qualified"]) < 2:
            continue
        rows.append((-ev["score"], -(g["percent_positive"] or 0), -(g["review_count"] or 0), g["appid"]))
    return [r[3] for r in sorted(rows)]
