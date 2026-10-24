"""seed 후보의 GetItems 일괄 수집 (오프라인). 3개씩, 2초 간격.

원본 응답은 var/raw/<run_id>/items.jsonl에 요청 단위로 보존한다(가격 필드 포함 원본 그대로,
서비스 정규화 대상에서는 제외). 같은 run_id로 다시 실행하면 이미 성공한 AppID는 건너뛴다.

사용: uv run python -m gembti.collector.fetch_items --run-id 2026-10-24a [--tags]
"""
from __future__ import annotations

import argparse
import csv
import json
import time

from gembti.collector.steam_client import SteamAuthStop, SteamClient
from gembti.config import CATALOG_DIR, RAW_DIR

BATCH = 3


def fetch_tags(client: SteamClient, run_dir) -> None:
    for lang in ("koreana", "english"):
        res = client.get_tag_list(lang)
        rec = {"language": lang, "ok": res.ok, "status": res.status, "error": res.error,
               "fetched_at": res.fetched_at, "data": res.data}
        (run_dir / f"taglist_{lang}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        print(f"GetTagList {lang}: ok={res.ok} status={res.status}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--seeds", default=str(CATALOG_DIR / "seeds" / "candidates.csv"))
    ap.add_argument("--tags", action="store_true", help="GetTagList(한·영)도 수집")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    run_dir = RAW_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "items.jsonl"
    done: set[int] = set()
    if out.exists():
        for line in out.open(encoding="utf-8"):
            rec = json.loads(line)
            if rec["ok"]:
                done.update(rec["requested"])
    with open(args.seeds) as f:
        appids = [int(r["appid"]) for r in csv.DictReader(f)]
    todo = [a for a in appids if a not in done]
    if args.limit:
        todo = todo[: args.limit]
    client = SteamClient()
    if args.tags or not (run_dir / "taglist_koreana.json").exists():
        fetch_tags(client, run_dir)
    print(f"{len(appids)} seeds, {len(done)} done, {len(todo)} to fetch")
    started = time.time()
    with out.open("a", encoding="utf-8") as f:
        for i in range(0, len(todo), BATCH):
            batch = todo[i: i + BATCH]
            try:
                res = client.get_items(batch)
            except SteamAuthStop as e:
                f.write(json.dumps({"requested": batch, "ok": False, "status": "auth_stop", "error": str(e),
                                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
                print(f"STOP: {e}")
                raise SystemExit(2)
            rec = {"requested": batch, "ok": res.ok, "status": res.status, "error": res.error,
                   "attempts": res.attempts, "fetched_at": res.fetched_at,
                   "data": res.data if res.ok else None}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            if (i // BATCH) % 25 == 0:
                print(f"{i + len(batch)}/{len(todo)} ({time.time() - started:.0f}s) last ok={res.ok}", flush=True)
    print("done")


if __name__ == "__main__":
    main()
