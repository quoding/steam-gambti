"""GetAppReviews 집계 파라미터 대조 실험 (오프라인 점검 도구, 리뷰 본문 저장 없음).

각 조합마다 첫 페이지를 독립 요청한다(cursor 재사용 없음). 직렬화된 실제 input_json을 기록한다(이 API는 키 없음).
요약 필드가 없으면 '누락'으로 기록하고 0으로 바꾸지 않는다.

사용: uv run python -m gembti.collector.review_param_check --out ../var/raw/review_param_check.json
"""
from __future__ import annotations

import argparse
import json
import time

import httpx

from gembti.collector.fetch_reviews import ENDPOINT

GAMES = {730: "Counter-Strike 2(무료)", 440: "Team Fortress 2(무료)", 570: "Dota 2(무료)",
         1284410: "GWENT(무료)", 3651950: "Evil Egg", 413150: "Stardew Valley(유료)",
         646570: "Slay the Spire(유료)", 4053520: "Holo vs Robo(유료·경계)"}
VARIANTS: list[tuple[str, dict[str, object]]] = [
    ("문자열 purchase_type=steam·review_type=all (기존 수집 요청)", {"review_type": "all", "purchase_type": "steam"}),
    ("enum purchase_type=0(Steam)·review_type=0", {"review_type": 0, "purchase_type": 0}),
    ("enum purchase_type=1(All)·review_type=0", {"review_type": 0, "purchase_type": 1}),
    ("enum purchase_type=2(NonSteamPurchase)·review_type=0", {"review_type": 0, "purchase_type": 2}),
]
MISSING = "누락"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    c = httpx.Client(timeout=20)
    rows = []
    for appid, label in GAMES.items():
        for name, extra in VARIANTS:
            body = {"appid": appid, "filter": "summary", "languages": ["all"], "num_per_page": 1,
                    "filter_offtopic_activity": True, **extra}
            req = c.build_request("GET", ENDPOINT, params={"input_json": json.dumps(body, separators=(",", ":"))})
            r = c.send(req)
            d = (r.json() if r.status_code == 200 else {}).get("response", {})
            q = d.get("query_summary")
            rows.append({"appid": appid, "label": label, "variant": name,
                         "sent_input_json": req.url.params["input_json"], "http": r.status_code,
                         "total_reviews": q.get("total_reviews", MISSING) if q else MISSING,
                         "total_positive": q.get("total_positive", MISSING) if q else MISSING,
                         "total_negative": q.get("total_negative", MISSING) if q else MISSING,
                         "review_score_desc": q.get("review_score_desc", MISSING) if q else MISSING,
                         "total_matching": d.get("total_matching", MISSING),
                         "page_reviews": len(d.get("reviews", [])),
                         "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            time.sleep(2)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("직렬화 예:", rows[1]["sent_input_json"])
    for x in rows:
        print(f"{x['label']:<20} {x['variant']:<52} total={x['total_reviews']!s:>9} +{x['total_positive']!s:>9} "
              f"-{x['total_negative']!s:>8} matching={x['total_matching']!s:>9} page={x['page_reviews']} "
              f"{x['review_score_desc']}")


if __name__ == "__main__":
    main()
