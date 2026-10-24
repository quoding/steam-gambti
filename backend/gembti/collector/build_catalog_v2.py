"""v2 확장 카탈로그 선정 (catalog_policy v2.0). v1 300개는 그대로 보존한다.

- 개별 적격: 정식 출시·KR 접근·Windows·VR 전용 아님·성인 전용 아님·한국어 상태 확인·영어/한국어 지원·25태그 중 1개 이상,
  콘텐츠 정책(제외·보류·미검토 제외), 리뷰는 GetAppReviews(전체 언어) 총 500개 이상·긍정 비율 0.75 이상(카운트로 판단).
- 집합 제약: 최대 1,000개(상한 목표), 시리즈≤2, 개발사≤6, 한국어 지원≥50%, 협동·PvP 각 40 이상,
  계열마다 W≥0.3 태그를 가진 게임 10개 이상(정식 전환 게이트). 버킷 정원은 v1 전용이라 쓰지 않는다.
- 기준을 낮춰 숫자를 맞추지 않는다. 충족 불가하면 보고하고 스냅샷을 만들지 않는다.

사용: uv run python -m gembti.collector.build_catalog_v2 --v1-run 2026-10-24a --v2-run v2-2026-10-24
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import time
from collections import Counter
from typing import Any

from gembti.collector.build_catalog import assign_series
from gembti.collector.normalize import exclusion_reasons, normalize_item
from gembti.config import CATALOG_DIR, RAW_DIR, STAGING_DIR
from gembti.policy import content_status, get_policy
from gembti.v2 import catalog_v2
from gembti.v2.library_profile import family_weights
from gembti.v2.policy import get_v2

MAX_GAMES = 1000
REVIEW_REASONS = {"reviews_lt_500", "positive_lt_75"}
OUT_DIR = CATALOG_DIR / "v2"


def load_items(runs: list[str]) -> dict[int, tuple[dict, str]]:
    items: dict[int, tuple[dict, str]] = {}
    for run in runs:
        for line in (RAW_DIR / run / "items.jsonl").open(encoding="utf-8"):
            rec = json.loads(line)
            if not rec["ok"]:
                continue
            for it in rec["data"].get("response", {}).get("store_items", []):
                items[it.get("appid") or it.get("id")] = (it, rec["fetched_at"])
    return items


def load_reviews(run: str, purchase_type: int) -> dict[int, dict]:
    """rp2 요청 정책의 수집분만 읽는다(rp1 문자열 enum 수집분은 섞지 않음). 같은 파라미터 해시만 인정."""
    from gembti.collector.fetch_reviews import REQUEST_POLICY, params_hash
    path = RAW_DIR / run / f"reviews_{REQUEST_POLICY}_pt{purchase_type}.jsonl"
    out: dict[int, dict] = {}
    if not path.exists():
        return out
    ph = params_hash(purchase_type)
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        if r["ok"] and r["scope"].get("params_hash") == ph:
            out[r["appid"]] = {**r["summary"], "fetched_at": r["fetched_at"], "scope": r["scope"],
                               "source": "IUserReviewsService/GetAppReviews/v1"}
    return out


def passes(rv: dict | None) -> bool:
    if not rv or not rv["total_reviews"]:
        return False
    return rv["total_reviews"] >= catalog_v2.MIN_REVIEWS and \
        rv["total_positive"] / rv["total_reviews"] >= catalog_v2.MIN_POSITIVE


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-run", required=True)
    ap.add_argument("--v2-run", required=True)
    args = ap.parse_args()
    log: list[str] = []

    def say(s: str) -> None:
        print(s)
        log.append(s)

    p, v1 = get_v2(), get_policy()
    targets = set(p.family_of)
    items = load_items([args.v1_run, args.v2_run])
    reviews = load_reviews(args.v2_run, 0)  # 적격 판정 기준: Steam 구매(purchase_type=0)
    reviews_all = load_reviews(args.v2_run, 1)  # 비교·감사용(전체 구매). 적격 판정·표시에 쓰지 않는다
    tl = {lang: json.loads((RAW_DIR / args.v2_run / f"taglist_{lang}.json").read_text())["data"]["response"]["tags"]
          for lang in ("koreana", "english")}
    ko = {t["tagid"]: t["name"] for t in tl["koreana"]}
    en = {t["tagid"]: t["name"] for t in tl["english"]}
    missing = sorted(targets - set(en))
    if missing:
        raise SystemExit(f"v2 판정 태그가 사전에 없음: {missing}")
    now = int(time.time())
    games: list[dict] = []
    reasons: Counter[str] = Counter()
    for appid, (it, fetched) in sorted(items.items()):
        g = normalize_item(it, fetched)
        g["appid"] = appid
        rs = [r for r in exclusion_reasons(g, targets, now) if r not in REVIEW_REASONS]
        cs = content_status(v1, g)
        if cs:
            rs.append(cs)
        rv = reviews.get(appid)
        if rv is None:
            rs.append("review_summary_unavailable")
        elif not passes(rv):
            alt = reviews_all.get(appid)
            if alt is not None and passes(alt):
                # 전체 구매 기준은 충족하지만 현재 선정 집계 범위(Steam 구매)에서는 미충족.
                # 전체 구매 값은 비교·감사용일 뿐 구제·대체에 쓰지 않는다(제외 유지).
                rs.append("v2_excluded_purchase_scope")
            elif alt is None and (g["review_count"] or 0) >= 500 and (g["percent_positive"] or 0) >= 75:
                rs.append("v2_excluded_purchase_scope_unchecked")
            elif rv["total_reviews"] < catalog_v2.MIN_REVIEWS:
                rs.append("v2_reviews_lt_500")
            else:
                rs.append("v2_positive_lt_75")
        g["review"] = rv
        g["review_all_purchase"] = reviews_all.get(appid)
        g["exclusion_reasons"] = rs
        g["families_w03"] = sorted(f for f, w in family_weights(g["tags"], p.family_of).items() if w >= 0.3)
        g["has_coop"] = any(t["tagid"] == 1685 for t in g["tags"])
        g["has_pvp"] = any(t["tagid"] == 1775 for t in g["tags"])
        reasons.update(rs)
        games.append(g)
    eligible = [g for g in games if not g["exclusion_reasons"]]
    assign_series(eligible)
    # GetItems summary_filtered 기준으로는 통과했지만 v2 출처로 탈락한 경계 게임
    boundary = [g["appid"] for g in games if {"v2_reviews_lt_500", "v2_positive_lt_75"} & set(g["exclusion_reasons"])
                and (g["review_count"] or 0) >= 500 and (g["percent_positive"] or 0) >= 75]
    holds = [g["appid"] for g in games if any(r.startswith("v2_excluded_purchase_scope") for r in g["exclusion_reasons"])]
    say(f"후보 {len(items)}, 리뷰 요약 rp2 Steam 구매 {len(reviews)} · 전체 구매 {len(reviews_all)}, 적격 {len(eligible)}")
    say("제외 사유: " + json.dumps(dict(reasons.most_common()), ensure_ascii=False))
    say(f"GetItems로는 통과·Steam 구매 기준 하한 미달: {len(boundary)}개 / 그중 전체 구매 기준은 충족(구매 범위 제외): {len(holds)}개")

    pulp: Any = importlib.import_module("pulp")
    prob = pulp.LpProblem("gembti_v2", pulp.LpMaximize)
    rank = {g["appid"]: i for i, g in enumerate(sorted(
        eligible, key=lambda g: (-g["review"]["total_positive"] / g["review"]["total_reviews"],
                                 -g["review"]["total_reviews"], g["appid"])))}
    n = len(eligible)
    x = {g["appid"]: pulp.LpVariable(f"x_{g['appid']}", cat="Binary") for g in eligible}
    prob += pulp.lpSum(x[a] * (n + 1 - rank[a]) / (n + 1) + x[a] * 1.0 for a in x)  # 개수 우선, 그다음 품질
    prob += pulp.lpSum(x.values()) <= MAX_GAMES, "max_games"
    total = pulp.lpSum(x.values())
    prob += pulp.lpSum(x[g["appid"]] for g in eligible if g["korean"] == "true") >= 0.5 * total, "korean_half"
    prob += pulp.lpSum(x[g["appid"]] for g in eligible if g["has_coop"]) >= 40, "coop"
    prob += pulp.lpSum(x[g["appid"]] for g in eligible if g["has_pvp"]) >= 40, "pvp"
    for i, (k, grp) in enumerate(Counter(g["series_id"] for g in eligible).items()):
        if grp > 2:
            prob += pulp.lpSum(x[g["appid"]] for g in eligible if g["series_id"] == k) <= 2, f"series_{i}"
    for i, (k, grp) in enumerate(Counter(g["developer_key"] for g in eligible).items()):
        if grp > 6:
            prob += pulp.lpSum(x[g["appid"]] for g in eligible if g["developer_key"] == k) <= 6, f"dev_{i}"
    avail = {f: sum(1 for g in eligible if f in g["families_w03"]) for f in p.family_by_id}
    for f in p.family_by_id:
        need = min(catalog_v2.FAMILY_GATE, avail[f])  # 불가능한 계열은 가능한 만큼만, 아래에서 게이트 실패로 보고
        prob += pulp.lpSum(x[g["appid"]] for g in eligible if f in g["families_w03"]) >= need, f"cover_{f}"
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=600))
    status = pulp.LpStatus[prob.status]
    say(f"ILP status: {status}")
    say("계열별 적격 게임(W≥0.3): " + json.dumps(avail, ensure_ascii=False))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "candidates_review_v2.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["appid", "name", "eligible", "exclusion_reasons", "v2_steam_total_reviews", "v2_steam_total_positive",
                    "v2_all_total_reviews", "v2_all_total_positive", "review_score", "getitems_review_count",
                    "getitems_percent_positive", "korean", "is_free", "families_w03"])
        for g in games:
            rv = g["review"] or {}
            ra = g["review_all_purchase"] or {}
            w.writerow([g["appid"], g["name"], not g["exclusion_reasons"], ";".join(g["exclusion_reasons"]),
                        rv.get("total_reviews"), rv.get("total_positive"), ra.get("total_reviews"),
                        ra.get("total_positive"), rv.get("review_score"), g["review_count"], g["percent_positive"],
                        g["korean"], g["is_free"], "|".join(g["families_w03"])])
    if status != "Optimal":
        (OUT_DIR / "build_report_v2.txt").write_text("\n".join(log) + "\n")
        raise SystemExit("v2 카탈로그 선정 실패: 집합 제약을 만족하는 해 없음")
    chosen = [g for g in eligible if x[g["appid"]].value() > 0.5]
    sid = f"cat-v2-{time.strftime('%Y%m%d%H%M%S')}"
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / f"{sid}.db"
    catalog_v2.write_snapshot(path, sid, chosen, ko, en, {"catalog_policy_version": p.versions["catalog_policy_version"],
                                                         "review_policy_version": p.versions["review_policy_version"],
                                                         "runs": f"{args.v1_run},{args.v2_run}"})
    errs, report = catalog_v2.validate(path)
    say(f"선정 {len(chosen)}개 (상한 {MAX_GAMES}) · 검증 오류 {errs or '없음'}")
    say("검증 보고: " + json.dumps(report, ensure_ascii=False))
    say(f"staging snapshot: var/staging/{path.name}")
    manifest = {"catalog_policy_version": p.versions["catalog_policy_version"], "snapshot_id": sid,
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": [args.v1_run, args.v2_run],
                "counts": {"candidates": len(items), "review_summaries": len(reviews), "eligible": len(eligible),
                           "selected": len(chosen), "boundary_dropped_by_v2_source": len(boundary),
                           "purchase_scope_excluded": len(holds)},
                "exclusion_reasons": dict(reasons), "report": report, "errors": errs,
                "games": [{"appid": g["appid"], "name": g["name"], "korean": g["korean"],
                           "total_reviews": g["review"]["total_reviews"], "total_positive": g["review"]["total_positive"],
                           "review_score": g["review"].get("review_score"), "families_w03": g["families_w03"],
                           "series_id": g["series_id"], "developer_key": g["developer_key"]} for g in chosen]}
    (OUT_DIR / "manifest_v2.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (OUT_DIR / "build_report_v2.txt").write_text("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
