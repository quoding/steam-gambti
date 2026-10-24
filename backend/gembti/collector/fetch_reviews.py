"""리뷰 요약 수집 (오프라인, v2). IUserReviewsService/GetAppReviews/v1.

요청 정책 rp2 (2026-10-24 실측 반영):
- input_json 안의 enum은 숫자로 보낸다: review_type=0(All), purchase_type=0(Steam)/1(All)/2(NonSteamPurchase).
  문자열("steam"/"all")은 무시되고 기본값 0(Steam 구매)으로 처리된다(rp1 수집분의 결함).
- 언어는 `languages:["all"]`(복수). `language` 단수는 무시되어 영어만 집계된다.
- `num_per_page=0`이어도 본문이 오므로 1로 요청하고, 리뷰 본문·작성자는 저장 전에 버린다. cursor는 쓰지 않는다(첫 페이지).
- 이어받기·중복 판정 키는 (appid, 요청 파라미터 해시). 파라미터가 다른 기록은 섞지 않는다.
- query_summary나 total_* 필드가 없으면 '요약 누락'으로 기록하고 0으로 바꾸지 않는다.

사용: uv run python -m gembti.collector.fetch_reviews --run-id <RUN> --appids-file <csv> [--purchase-type 0]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time

from gembti.collector.steam_client import API, SteamAuthStop, SteamClient
from gembti.config import RAW_DIR

ENDPOINT = f"{API}/IUserReviewsService/GetAppReviews/v1/"
REQUEST_POLICY = "rp2"
PURCHASE_TYPES = {0: "Steam", 1: "All", 2: "NonSteamPurchase"}


def request_body(appid: int, purchase_type: int = 0) -> dict:
    if purchase_type not in PURCHASE_TYPES:
        raise ValueError("purchase_type은 0/1/2")
    return {"appid": appid, "filter": "summary", "languages": ["all"], "review_type": 0,
            "purchase_type": purchase_type, "filter_offtopic_activity": True, "num_per_page": 1}


def serialize(body: dict) -> str:
    return json.dumps(body, separators=(",", ":"), sort_keys=True)


def params_hash(purchase_type: int) -> str:
    """appid를 뺀 집계 파라미터 전체의 해시(캐시·이어받기 키)."""
    body = request_body(0, purchase_type)
    del body["appid"]
    return hashlib.sha256(f"{REQUEST_POLICY}|{serialize(body)}".encode()).hexdigest()[:16]


def scope(purchase_type: int) -> dict:
    return {"endpoint": "IUserReviewsService/GetAppReviews/v1", "request_policy": REQUEST_POLICY,
            "params_hash": params_hash(purchase_type), "languages": "all", "period": "all",
            "review_type": 0, "purchase_type": purchase_type, "purchase_type_name": PURCHASE_TYPES[purchase_type],
            "filter_offtopic_activity": True}


def summarize(resp: dict) -> dict | None:
    """요약만 남긴다(리뷰 본문·작성자 ID 폐기). 필드가 하나라도 없으면 None(누락) — 0으로 바꾸지 않는다."""
    r = resp.get("response") or {}
    q = r.get("query_summary")
    keys = ("total_positive", "total_negative", "total_reviews")
    if not q or any(q.get(k) is None for k in keys):
        return None
    out = {k: q.get(k) for k in ("review_score", "review_score_desc", *keys)}
    out["total_matching"] = r.get("total_matching")
    return out


def fetch_one(client: SteamClient, appid: int, purchase_type: int = 0):
    return client.get_json(ENDPOINT, {"input_json": serialize(request_body(appid, purchase_type))})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--appids-file", required=True, help="appid 열이 있는 CSV")
    ap.add_argument("--purchase-type", type=int, default=0, choices=sorted(PURCHASE_TYPES))
    args = ap.parse_args()
    run_dir = RAW_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / f"reviews_{REQUEST_POLICY}_pt{args.purchase_type}.jsonl"
    ph = params_hash(args.purchase_type)
    done = set()
    if out.exists():
        for line in out.open(encoding="utf-8"):
            r = json.loads(line)
            if r["ok"] and r["scope"].get("params_hash") == ph:
                done.add(r["appid"])
    with open(args.appids_file) as f:
        appids = [int(r["appid"]) for r in csv.DictReader(f)]
    todo = [a for a in appids if a not in done]
    print(f"{len(appids)} appids, {len(done)} done(같은 파라미터), {len(todo)} to fetch · 요청 예: "
          f"{serialize(request_body(appids[0] if appids else 0, args.purchase_type))}", flush=True)
    client = SteamClient()
    t0 = time.time()
    with out.open("a", encoding="utf-8") as fh:
        for i, a in enumerate(todo):
            try:
                res = fetch_one(client, a, args.purchase_type)
            except SteamAuthStop as e:
                print(f"STOP {e}")
                raise SystemExit(2)
            summ = summarize(res.data) if res.ok and res.data else None
            status = "ok" if summ is not None else ("summary_missing" if res.ok else "request_failed")
            fh.write(json.dumps({"appid": a, "ok": summ is not None, "status": res.status, "result": status,
                                 "error": res.error, "fetched_at": res.fetched_at,
                                 "sent_input_json": serialize(request_body(a, args.purchase_type)),
                                 "scope": scope(args.purchase_type), "summary": summ},
                                ensure_ascii=False) + "\n")
            fh.flush()
            if i % 100 == 0:
                print(f"{i}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    print("done")


if __name__ == "__main__":
    main()
