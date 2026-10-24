"""라이브러리 '추정 취향' 프로필 (library_policy v2.0, 순수 함수).

질문 positive/avoid·카드 근거에는 쓰지 않는다. 저시간·0시간 게임을 싫어한다고 추정하지 않는다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

TOP_N = 20
HOURS_CAP = 300.0
GAME_SHARE_CAP = 0.4
SERIES_SHARE_CAP = 0.4
MIN_TAGGED_GAMES = 3
MIN_TAGGED_WEIGHT_SHARE = 0.6


@dataclass
class LibraryProfile:
    enabled: bool
    reason: str | None
    analyzed: list[dict] = field(default_factory=list)  # appid, hours, raw, share, tagged
    family_vector: dict[str, float] = field(default_factory=dict)
    tagged_count: int = 0
    analyzed_count: int = 0
    tagged_weight_share: float = 0.0


def family_weights(tags: list[dict], family_of: dict[int, str]) -> dict[str, float]:
    """게임의 계열별 W(태그 weight/게임 최대 weight)의 계열 내 최댓값 1회."""
    ws = [t.get("weight") or 0 for t in tags]
    m = max(ws) if ws else 0
    out: dict[str, float] = {}
    for t in tags:
        f = family_of.get(t["tagid"])
        if f is None:
            continue
        w = (t.get("weight") or 0) / m if m else 1 / t.get("rank", 1)
        out[f] = max(out.get(f, 0.0), w)
    return out


def _cap(shares: dict[int, float], groups: dict[int, str | None], cap: float, keyed: bool) -> dict[int, float] | None:
    """상한을 넘는 몫을 잘라 상한 아래 항목에 비례 재분배(water-filling). 불가능하면 None."""
    s = dict(shares)
    for _ in range(100):
        if keyed:
            totals: dict[str, float] = {}
            for a, v in s.items():
                g = groups.get(a)
                if g:
                    totals[g] = totals.get(g, 0.0) + v
            over = {g: t for g, t in totals.items() if t > cap + 1e-12}
            if not over:
                return s
            excess = 0.0
            fixed = set()
            for g, t in over.items():
                members = [a for a in s if groups.get(a) == g]
                for a in members:
                    nv = s[a] * cap / t
                    excess += s[a] - nv
                    s[a] = nv
                    fixed.add(a)
            capped_groups = set(over)
            free = [a for a in s if groups.get(a) not in capped_groups and a not in fixed]
        else:
            over_items = [a for a, v in s.items() if v > cap + 1e-12]
            if not over_items:
                return s
            excess = sum(s[a] - cap for a in over_items)
            for a in over_items:
                s[a] = cap
            free = [a for a, v in s.items() if v < cap - 1e-12]
        base = sum(s[a] for a in free)
        if not free or base <= 0:
            return None
        for a in free:
            s[a] += excess * s[a] / base
    return None


def build_profile(owned: list[dict], meta: dict[int, list[dict]], family_of: dict[int, str],
                  series_of: dict[int, str], analysis_excluded: set[int]) -> LibraryProfile:
    """owned: [{appid, minutes(None 가능)}], meta: appid -> tags([{tagid, weight, rank}]) (태그 확보분만)."""
    played = [g for g in owned if g["appid"] not in analysis_excluded and (g.get("minutes") or 0) > 0]
    played.sort(key=lambda g: (-g["minutes"], g["appid"]))
    top = played[:TOP_N]
    if not top:
        return LibraryProfile(False, "플레이 시간이 있는 게임이 없음")
    rows = []
    for g in top:
        hours = g["minutes"] / 60
        raw = math.log1p(min(hours, HOURS_CAP))
        has_tags = bool(meta.get(g["appid"])) and bool(family_weights(meta[g["appid"]], family_of))
        rows.append({"appid": g["appid"], "hours": round(hours, 2), "raw": raw, "tagged": has_tags})
    total_raw = sum(r["raw"] for r in rows)
    tagged = [r for r in rows if r["tagged"]]
    share = sum(r["raw"] for r in tagged) / total_raw if total_raw else 0.0
    prof = LibraryProfile(False, None, rows, {}, len(tagged), len(rows), round(share, 4))
    if len(tagged) < MIN_TAGGED_GAMES:
        prof.reason = f"태그를 확보한 플레이 게임이 {len(tagged)}개(최소 {MIN_TAGGED_GAMES})"
        return prof
    if share < MIN_TAGGED_WEIGHT_SHARE:
        prof.reason = f"태그 확보 게임의 가중치 비율 {share:.0%} < {MIN_TAGGED_WEIGHT_SHARE:.0%}"
        return prof
    tr = sum(r["raw"] for r in tagged)
    capped_games = _cap({r["appid"]: r["raw"] / tr for r in tagged}, {}, GAME_SHARE_CAP, keyed=False)
    if capped_games is None:
        prof.reason = "게임별 기여 상한(40%)을 만족할 수 없음"
        return prof
    series = {a: series_of.get(a) for a in capped_games}  # 시리즈 매핑 미확인 게임은 합치지 않음(None)
    capped = _cap(capped_games, series, SERIES_SHARE_CAP, keyed=True)
    if capped is None or any(v > GAME_SHARE_CAP + 1e-9 for v in capped.values()):
        prof.reason = "시리즈·게임 기여 상한(40%)을 동시에 만족할 수 없음"
        return prof
    vec: dict[str, float] = {}
    for a, w in capped.items():
        for f, fw in family_weights(meta[a], family_of).items():
            vec[f] = vec.get(f, 0.0) + w * fw
    for r in rows:
        r["share"] = capped.get(r["appid"], 0.0)
    prof.enabled = True
    prof.family_vector = vec
    return prof


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))
