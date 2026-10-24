"""seed 후보 AppID 명단 작성 (오프라인).

공개 Steam 상점 태그 검색 페이지(store.steampowered.com/search)에서 판정 태그별로
'게임' 카테고리 결과의 AppID를 모은다. GetAppList는 사용하지 않는다(기준 문서 8.3).
각 AppID에 처음 발견된 검색 URL과 수집 시각을 출처로 남긴다.

사용: uv run python -m gembti.collector.harvest_seeds --per-tag 300
"""
from __future__ import annotations

import argparse
import csv
import re
import time
from urllib.parse import urlencode

from gembti.collector.steam_client import SteamClient
from gembti.config import CATALOG_DIR
from gembti.policy import load_policy

SEARCH = "https://store.steampowered.com/search/results/"
APPID_RE = re.compile(r'data-ds-appid="(\d+)"')
OUT = CATALOG_DIR / "seeds" / "candidates.csv"


def search_url(tagid: int, sort: str, start: int, count: int) -> tuple[str, dict]:
    params = {"query": "", "start": start, "count": count, "tags": tagid, "category1": 998,
              "infinite": 1, "cc": "KR", "l": "english", "os": "win"}
    if sort:
        params["sort_by"] = sort
    return SEARCH, params


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-tag", type=int, default=300, help="정렬 방식마다 태그당 가져올 결과 수")
    ap.add_argument("--sorts", default="Reviews_DESC,", help="쉼표 구분; 빈 값은 상점 기본(관련도) 정렬")
    args = ap.parse_args()

    policy = load_policy()
    existing: dict[int, dict] = {}
    if OUT.exists():
        with OUT.open() as f:
            for row in csv.DictReader(f):
                existing[int(row["appid"])] = row
    client = SteamClient()
    found = dict(existing)
    for tag in policy.tags:
        for sort in args.sorts.split(","):
            for start in range(0, args.per_tag, 100):
                url, params = search_url(tag.tagid, sort, start, 100)
                res = client.get_json(url, params)
                if not res.ok or res.data is None:
                    print(f"[warn] tag {tag.tagid} sort={sort!r} start={start}: {res.error}")
                    break
                ids = APPID_RE.findall(res.data.get("results_html", ""))
                for a in ids:
                    a = int(a)
                    if a not in found:
                        found[a] = {"appid": a, "source": f"{url}?{urlencode(params)}",
                                    "source_tagid": tag.tagid, "found_at": res.fetched_at}
                print(f"tag {tag.tagid} sort={sort or 'relevance'} start={start}: {len(ids)} ids, total {len(found)}")
                if len(ids) < 100:
                    break
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["appid", "source", "source_tagid", "found_at"])
        w.writeheader()
        for a in sorted(found):
            w.writerow(found[a])
    print(f"wrote {len(found)} candidates -> {OUT} at {time.strftime('%F %T')}")


if __name__ == "__main__":
    main()
