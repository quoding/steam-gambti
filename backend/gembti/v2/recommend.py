"""v2 추천 (recommendation v2.0). v1 계산 경로(gembti.recommend)는 바꾸지 않는다.

계산 순서
1. 제외: 콘텐츠 적격성 → 사용자 필터(한국어·OS·최소 리뷰 수·최소 긍정률) → 보유 게임(선택) → 강한 기피(avoid 태그 W≥0.5).
2. Q(g) = Σ_계열 max(P×W×IDF) / Qmax,  Qmax = Σ_positive 계열 (그 계열 최대 P × 1 × IDF 상한 2).  0~1.
   기피 패널티 A(g) = 1.25 × Σ_계열 max(A×W×IDF) / Amax,  Amax = Σ_avoid 계열 (최대 A × 2).  0~1.25. 한 번만 뺀다.
   v1에는 리뷰·인기 보너스가 점수에 없었다(동점 정렬에만 사용) → v2도 보너스 0, 정렬 보조로만 사용.
3. 라이브러리 사용 시 취향 = 0.7Q + 0.3L (L = 라이브러리 계열 벡터와 게임 계열 벡터의 cosine, 0~1). 미사용·부족·실패면 취향 = Q.
4. 최종 = 취향 − A.  '답변에 맞춘 추천' 자격은 질문 positive 서로 다른 그룹 2개 이상에서 W≥0.3 (라이브러리로 올리지 않음).
5. 정렬: 최종↓ → Steam 공식 평가 코드↓ → 긍정률↓ → 리뷰 수↓ → appid↑. 시리즈 1개, 그룹 Jaccard≥0.8 보류 후 해제.
6. 참고 추천: 자격 미달 후보. 패널티↑ → (라이브러리 사용 시 L↓) → 리뷰 수↓ → 긍정률↓ → appid↑.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from gembti.policy import Policy as V1Policy
from gembti.policy import content_status
from gembti.v2.engine import TagState
from gembti.v2.library_profile import cosine, family_weights
from gembti.v2.policy import V2Policy

MAX_SLOTS = 5
STRONG_AVOID_W = 0.5
QUALIFY_W = 0.3
AVOID_FACTOR = 1.25
IDF_MAX = 2.0
JACCARD_HOLD = 0.8
Q_WEIGHT, L_WEIGHT = 0.7, 0.3
L_NOTE_MIN = 0.3
POSITIVE_PRESETS = (75, 80, 90, 95)
REVIEW_PRESETS = (500, 1000, 5000, 10000, 50000, 100000)


@dataclass(frozen=True)
class FiltersV2:
    korean_only: bool = False
    os: str = "windows"
    min_positive: int = 75
    min_reviews: int = 500
    exclude_owned: bool = True

    def __post_init__(self):
        if self.os not in ("windows", "mac", "linux"):
            raise ValueError("os")
        if self.min_positive not in POSITIVE_PRESETS:
            raise ValueError("min_positive는 75/80/90/95")
        if self.min_reviews not in REVIEW_PRESETS:
            raise ValueError("min_reviews는 500/1000/5000/10000/50000/100000")


def weights(game: dict) -> dict[int, float]:
    ws = [t["weight"] for t in game["tags"] if t.get("weight")]
    if ws:
        m = max(ws)
        return {t["tagid"]: (t.get("weight") or 0) / m for t in game["tags"]}
    return {t["tagid"]: 1 / t["rank"] for t in game["tags"]}


def compute_idf(p: V2Policy, catalog: list[dict]) -> dict[int, float]:
    n = len(catalog)
    out = {}
    for t in p.family_of:
        df = sum(1 for g in catalog if any(x["tagid"] == t for x in g["tags"]))
        out[t] = min(IDF_MAX, max(1.0, math.log((n + 1) / (df + 1)) + 1))
    return out


def positive_ratio(g: dict) -> float | None:
    tr = g.get("total_reviews")
    if not tr:
        return None
    return g["total_positive"] / tr


def filter_reasons(v1: V1Policy, g: dict, f: FiltersV2, owned: set[int]) -> list[str]:
    r = []
    cs = content_status(v1, g)
    if cs:
        r.append(cs)
    if f.korean_only and g["korean"] != "true":
        r.append("filter_korean")
    if g[f.os] != "true":
        r.append(f"filter_os_{f.os}")
    tr = g.get("total_reviews")
    if tr is None or tr < f.min_reviews:  # 미확인 값을 0으로 바꾸지 않는다: 없으면 통과시키지 않음
        r.append("filter_min_reviews")
    ratio = positive_ratio(g)
    if ratio is None or ratio < f.min_positive / 100:  # 원래 count 비율로 판단
        r.append("filter_min_positive")
    if f.exclude_owned and g["appid"] in owned:
        r.append("owned")
    return r


def evaluate(p: V2Policy, g: dict, tags: dict[int, TagState], idf: dict[int, float], lib_vec: dict | None) -> dict:
    W = weights(g)
    pos: dict[str, float] = {}
    pos_tag: dict[str, int] = {}
    qual: dict[str, int] = {}
    neg: dict[str, float] = {}
    strong = []
    for tid, w in W.items():
        ts = tags.get(tid)
        if ts is None or w <= 0 or ts.normalized is None:
            continue
        fam = ts.family_id
        if ts.state == "positive":
            c = ts.normalized * w * idf[tid]
            if c > pos.get(fam, -1):
                pos[fam], pos_tag[fam] = c, tid
            if w >= QUALIFY_W and (fam not in qual or w > W[qual[fam]]):
                qual[fam] = tid
        elif ts.state == "avoid":
            if w >= STRONG_AVOID_W:
                strong.append({"tagid": tid, "W": w})
            c = abs(ts.normalized) * w * idf[tid]
            neg[fam] = max(neg.get(fam, 0.0), c)
    pos_f: dict[str, float] = {}
    for ts in tags.values():
        if ts.state == "positive" and ts.normalized is not None:
            pos_f[ts.family_id] = max(pos_f.get(ts.family_id, 0.0), ts.normalized)
    avd_f: dict[str, float] = {}
    for ts in tags.values():
        if ts.state == "avoid" and ts.normalized is not None:
            avd_f[ts.family_id] = max(avd_f.get(ts.family_id, 0.0), abs(ts.normalized))
    qmax = sum(v * IDF_MAX for v in pos_f.values())
    amax = sum(v * IDF_MAX for v in avd_f.values())
    Q = min(1.0, sum(pos.values()) / qmax) if qmax else 0.0
    A = AVOID_FACTOR * min(1.0, sum(neg.values()) / amax) if amax else 0.0
    gvec = family_weights(g["tags"], p.family_of)
    L = cosine(lib_vec, gvec) if lib_vec else None
    taste = Q_WEIGHT * Q + L_WEIGHT * L if L is not None else Q
    return {"appid": g["appid"], "Q": Q, "L": L, "penalty": A, "taste": taste, "final": taste - A,
            "matched": {f: pos_tag[f] for f in pos}, "qualified": qual, "strong_avoid": strong,
            "groups": set(gvec)}


def _jacc(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def recommend_v2(p: V2Policy, v1: V1Policy, tags: dict[int, TagState], catalog: list[dict], f: FiltersV2,
                 owned: set[int], lib_vec: dict | None, idf: dict[int, float] | None = None) -> dict:
    idf = idf or compute_idf(p, catalog)
    content_ok = [g for g in catalog if not content_status(v1, g)]
    pool, excluded = [], []
    owned_excluded = 0
    for g in catalog:
        rs = filter_reasons(v1, g, f, owned)
        if rs:
            if rs == ["owned"]:
                owned_excluded += 1
            excluded.append({"appid": g["appid"], "reasons": rs})
            continue
        ev = evaluate(p, g, tags, idf, lib_vec)
        if ev["strong_avoid"]:
            excluded.append({"appid": g["appid"], "reasons": ["strong_avoid"], "strong_avoid": ev["strong_avoid"]})
            continue
        pool.append((g, ev))
    passing_filters = len(pool) + owned_excluded + sum(1 for e in excluded if e["reasons"] == ["strong_avoid"])

    def pkey(x):
        g, ev = x
        return (-ev["final"], -(g.get("review_score") or 0), -(positive_ratio(g) or 0), -(g.get("total_reviews") or 0),
                g["appid"])

    eligible = sorted([x for x in pool if len(x[1]["qualified"]) >= 2], key=pkey)
    picked: list = []
    series: set = set()
    held = []
    for g, ev in eligible:
        if len(picked) == MAX_SLOTS:
            break
        if g["series_id"] in series:
            continue
        if any(_jacc(ev["groups"], e["groups"]) >= JACCARD_HOLD for _, e in picked):
            held.append((g, ev))
            continue
        picked.append((g, ev))
        series.add(g["series_id"])
    for g, ev in held:
        if len(picked) == MAX_SLOTS:
            break
        if g["series_id"] not in series:
            picked.append((g, ev))
            series.add(g["series_id"])
    picked.sort(key=pkey)
    ids = {g["appid"] for g, _ in picked}
    use_l = lib_vec is not None
    ref_pool = sorted([x for x in pool if x[0]["appid"] not in ids],
                      key=lambda x: (x[1]["penalty"], -(x[1]["L"] or 0) if use_l else 0,
                                     -(x[0].get("total_reviews") or 0), -(positive_ratio(x[0]) or 0), x[0]["appid"]))
    ref: list[tuple[dict, dict]] = []
    for g, ev in ref_pool:
        if len(picked) + len(ref) == MAX_SLOTS:
            break
        if g["series_id"] in series:
            continue
        ref.append((g, ev))
        series.add(g["series_id"])

    def row(kind, g, ev):
        return {"appid": g["appid"], "kind": kind, "Q": round(ev["Q"], 6),
                "L": None if ev["L"] is None else round(ev["L"], 6), "penalty": round(ev["penalty"], 6),
                "final": round(ev["final"], 6), "qualified_tags": sorted(ev["qualified"].values()),
                "matched_tags": sorted(ev["matched"].values()),
                "library_note": bool(use_l and ev["L"] is not None and ev["L"] >= L_NOTE_MIN)}

    items = [row("personalized", g, e) for g, e in picked] + [row("reference", g, e) for g, e in ref]
    for i, it in enumerate(items):
        it["rank"] = i + 1
    return {"items": items, "counts": {"catalog_total": len(catalog), "content_eligible": len(content_ok),
                                       "passing_filters": passing_filters, "owned_excluded": owned_excluded,
                                       "strong_avoid_excluded": sum(1 for e in excluded if "strong_avoid" in e["reasons"]),
                                       "candidates": len(pool), "recommended": len(items),
                                       "personalized": len(picked), "reference": len(ref)},
            "excluded": excluded, "library_used": use_l}
