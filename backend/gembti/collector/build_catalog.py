"""수집 원본 → 후보 검수 → 300개 선정(8.2·8.3) → manifest → staging 스냅샷.

선정은 슬롯별 탐욕이 아니라 전체 제약(슬롯 정원·한국어 최소·협동/PvP 40·시리즈 2·개발사 6)을
동시에 만족시키는 정수계획으로 푼다. 목적은 슬롯 안 순위(긍정률↓→리뷰 수↓→appid↑)의 합 최소.
충족 불가하면 부족 슬롯을 보고하고 manifest를 'incomplete'로 남기며 스냅샷을 만들지 않는다.

사용: uv run python -m gembti.collector.build_catalog --run-id 2026-10-24a
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict

from gembti.collector.normalize import assign_bucket, exclusion_reasons, norm_key, normalize_item, review_size
from gembti.config import CATALOG_DIR, POLICY_DIR, RAW_DIR, STAGING_DIR
from gembti.policy import load_policy

CATALOG_POLICY = json.loads((POLICY_DIR / "catalog.json").read_text(encoding="utf-8"))
QUOTAS = {g: (q["small"], q["mid"], q["large"], q["korean_min"]) for g, q in CATALOG_POLICY["quotas"].items()}
SIZES = ("small", "mid", "large")
MIN_COOP = CATALOG_POLICY["min_coop"]
MIN_PVP = CATALOG_POLICY["min_pvp"]
MAX_SERIES = CATALOG_POLICY["max_series"]
MAX_DEVELOPER = CATALOG_POLICY["max_developer"]
COOP, PVP = 1685, 1775
MANIFEST_DIR = CATALOG_DIR / "manifest"
OVERRIDES = CATALOG_DIR / "series_overrides.json"

_EDITION = re.compile(
    r"\b(remastered|remaster|definitive|enhanced|complete|goty|game of the year|anniversary|director'?s cut|"
    r"deluxe|ultimate|hd|edition|redux|reloaded|classic|legacy|royal|gold)\b")
_TRAIL_NUM = re.compile(r"(\s+(\d+|[ivx]+))+$")


def base_title(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).casefold()
    s = re.sub(r"[™®©]", "", s)
    s = re.split(r"\s*[:\-–—]\s+|\s*:\s*", s)[0]
    s = _EDITION.sub(" ", s)
    s = re.sub(r"[^0-9a-z가-힣぀-ヿ一-鿿 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _TRAIL_NUM.sub("", s).strip()
    return s


def assign_series(games: list[dict]) -> None:
    """프랜차이즈 키·정규화한 기본 제목·수동 override를 union-find로 묶어 series_id를 만든다."""
    overrides = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    parent = {g["appid"]: g["appid"] for g in games}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    keys: dict[str, list[int]] = defaultdict(list)
    for g in games:
        if g["franchise"]:
            keys["f:" + norm_key(g["franchise"])].append(g["appid"])
        bt = base_title(g["name"] or "")
        if len(bt) >= 3:
            keys["t:" + bt].append(g["appid"])
        ov = overrides.get("series", {}).get(str(g["appid"]))
        if ov:
            keys["o:" + ov].append(g["appid"])
    for members in keys.values():
        for m in members[1:]:
            union(members[0], m)
    for sep in overrides.get("separate", []):  # 잘못 묶인 경우 수동 분리
        for a in sep:
            if a in parent:
                parent[a] = a
    labels: dict[int, list[dict]] = {}
    for g in games:
        root = find(g["appid"])
        labels.setdefault(root, [])
        labels[root].append(g)
    for root, group in labels.items():
        sid = f"series:{root}"
        for g in group:
            g["series_id"] = sid
            g["series_members"] = len(group)


def load_run(run_id: str):
    run_dir = RAW_DIR / run_id
    tagl = {}
    for lang in ("koreana", "english"):
        rec = json.loads((run_dir / f"taglist_{lang}.json").read_text(encoding="utf-8"))
        if not rec["ok"]:
            raise SystemExit(f"GetTagList {lang} 실패: {rec['error']}")
        tagl[lang] = rec
    items, failures, requested = {}, [], set()
    for line in (run_dir / "items.jsonl").open(encoding="utf-8"):
        rec = json.loads(line)
        requested.update(rec["requested"])
        if not rec["ok"]:
            failures.append(rec)
            continue
        got = {}
        for it in rec["data"].get("response", {}).get("store_items", []):
            got[it.get("appid") or it.get("id")] = it
        for a in rec["requested"]:
            if a in got:
                items[a] = (got[a], rec["fetched_at"])
    # 나중의 성공 기록이 실패를 대체
    failed_ids = {a for r in failures for a in r["requested"]} - set(items)
    return tagl, items, failed_ids, requested


def select(eligible: list[dict], log) -> tuple[list[dict] | None, dict]:
    import importlib
    from typing import Any

    pulp: Any = importlib.import_module("pulp")  # 번들 타입 정보가 실제 API와 달라 Any로 다룬다
    slots = defaultdict(list)
    for g in eligible:
        slots[(g["bucket"], g["size"])].append(g)
    rank: dict[int, int] = {}
    for gs in slots.values():
        gs.sort(key=lambda g: (-g["percent_positive"], -g["review_count"], g["appid"]))
        for i, g in enumerate(gs):
            rank[g["appid"]] = i
    avail = {}
    for b, (s, m, l_, kmin) in QUOTAS.items():
        for size, q in zip(SIZES, (s, m, l_)):
            gs = slots.get((b, size), [])
            avail[f"{b}/{size}"] = {"quota": q, "available": len(gs),
                                    "korean_available": sum(1 for g in gs if g["korean"] == "true")}
    prob = pulp.LpProblem("gembti_catalog", pulp.LpMinimize)
    x = {g["appid"]: pulp.LpVariable(f"x_{g['appid']}", cat="Binary") for g in eligible}
    prob += pulp.lpSum(rank[a] * v for a, v in x.items())
    for b, (s, m, l_, kmin) in QUOTAS.items():
        for size, q in zip(SIZES, (s, m, l_)):
            prob += pulp.lpSum(x[g["appid"]] for g in slots.get((b, size), [])) == q, f"slot_{b}_{size}"
        prob += pulp.lpSum(x[g["appid"]] for size in SIZES for g in slots.get((b, size), [])
                           if g["korean"] == "true") >= kmin, f"ko_{b}"
    prob += pulp.lpSum(x[g["appid"]] for g in eligible if g["has_coop"]) >= MIN_COOP, "coop"
    prob += pulp.lpSum(x[g["appid"]] for g in eligible if g["has_pvp"]) >= MIN_PVP, "pvp"
    by_series, by_dev = defaultdict(list), defaultdict(list)
    for g in eligible:
        by_series[g["series_id"]].append(g["appid"])
        by_dev[g["developer_key"]].append(g["appid"])
    for i, (k, v) in enumerate(by_series.items()):
        if len(v) > MAX_SERIES:
            prob += pulp.lpSum(x[a] for a in v) <= MAX_SERIES, f"series_{i}"
    for i, (k, v) in enumerate(by_dev.items()):
        if len(v) > MAX_DEVELOPER:
            prob += pulp.lpSum(x[a] for a in v) <= MAX_DEVELOPER, f"dev_{i}"
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=300))
    status = pulp.LpStatus[prob.status]
    log(f"ILP status: {status}")
    diag = {"solver_status": status, "slots": avail}
    if status != "Optimal":
        return None, diag
    chosen = [g for g in eligible if x[g["appid"]].value() > 0.5]
    for g in chosen:
        g["slot_rank"] = rank[g["appid"]]
    return chosen, diag


def verify(chosen: list[dict]) -> dict:
    """선정 결과의 제약을 선정기와 독립적으로 다시 센다."""
    checks = {}
    c = Counter((g["bucket"], g["size"]) for g in chosen)
    for b, (s, m, l_, kmin) in QUOTAS.items():
        for size, q in zip(SIZES, (s, m, l_)):
            checks[f"slot {b}/{size} == {q}"] = c[(b, size)] == q
        ko = sum(1 for g in chosen if g["bucket"] == b and g["korean"] == "true")
        checks[f"korean {b} >= {kmin} (got {ko})"] = ko >= kmin
    coop = sum(1 for g in chosen if g["has_coop"])
    pvp = sum(1 for g in chosen if g["has_pvp"])
    checks[f"coop >= {MIN_COOP} (got {coop})"] = coop >= MIN_COOP
    checks[f"pvp >= {MIN_PVP} (got {pvp})"] = pvp >= MIN_PVP
    ms = max(Counter(g["series_id"] for g in chosen).values())
    md = max(Counter(g["developer_key"] for g in chosen).values())
    checks[f"series <= {MAX_SERIES} (max {ms})"] = ms <= MAX_SERIES
    checks[f"developer <= {MAX_DEVELOPER} (max {md})"] = md <= MAX_DEVELOPER
    checks[f"total == {CATALOG_POLICY['total']}"] = len(chosen) == CATALOG_POLICY["total"]
    checks["unique appids"] = len({g["appid"] for g in chosen}) == len(chosen)
    checks["no exclusion reasons"] = all(not g["exclusion_reasons"] for g in chosen)
    checks["korean status known"] = all(g["korean"] in ("true", "false") for g in chosen)
    return checks


def verify_snapshot(path) -> dict:
    """승격 전 스냅샷 DB를 현재 카탈로그 정책으로 다시 센다(선정기와 독립)."""
    import sqlite3
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    games = []
    for r in con.execute("SELECT * FROM games"):
        tags = {t[0] for t in con.execute("SELECT tagid FROM game_tags WHERE appid = ?", (r["appid"],))}
        games.append({"appid": r["appid"], "bucket": r["manifest_bucket"], "size": r["review_size"],
                      "korean": r["korean"], "has_coop": COOP in tags, "has_pvp": PVP in tags,
                      "series_id": r["series_id"], "developer_key": r["developer_key"], "exclusion_reasons": [],
                      "review_count": r["review_count"], "percent_positive": r["percent_positive"]})
    con.close()
    checks = verify(games)
    lo = {s: CATALOG_POLICY["review_size_bounds"][s] for s in SIZES}
    checks["review size matches count"] = all(
        g["review_count"] >= lo[g["size"]][0] and (lo[g["size"]][1] is None or g["review_count"] <= lo[g["size"]][1])
        for g in games)
    checks["reviews >= 500 and positive >= 75"] = all(g["review_count"] >= 500 and g["percent_positive"] >= 75
                                                     for g in games)
    return checks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    lines = []

    def log(s):
        print(s)
        lines.append(s)

    policy = load_policy()
    target = {t.tagid for t in policy.tags}
    group_of = {t.tagid: t.group_id for t in policy.tags}
    social = {g.group_id for g in policy.groups if g.social}

    tagl, items, failed_ids, requested = load_run(args.run_id)
    ko = {t["tagid"]: t["name"] for t in tagl["koreana"]["data"]["response"]["tags"]}
    en = {t["tagid"]: t["name"] for t in tagl["english"]["data"]["response"]["tags"]}
    missing = [t for t in target if t not in ko or t not in en]
    if missing:
        raise SystemExit(f"판정 태그 누락으로 중지: {missing}")
    for t in policy.tags:
        if ko[t.tagid] != t.name_ko or en[t.tagid] != t.name_en:
            log(f"[주의] 태그 이름 변경: {t.tagid} policy={t.name_ko}/{t.name_en} api={ko[t.tagid]}/{en[t.tagid]}")
    log(f"GetTagList: ko {len(ko)} / en {len(en)} tags; 20 target tags present")

    with open(CATALOG_DIR / "seeds" / "candidates.csv") as f:
        seeds = {int(r["appid"]): r for r in csv.DictReader(f)}
    now_ts = int(time.time())
    games = []
    for appid in sorted(requested):
        if appid not in items:
            continue
        g = normalize_item(*items[appid])
        g["appid"] = appid
        g["exclusion_reasons"] = exclusion_reasons(g, target, now_ts)
        g["bucket"] = assign_bucket(g, group_of, social)
        if g["bucket"] is None and "no_target_tag" not in g["exclusion_reasons"]:
            g["exclusion_reasons"].append("only_social_target_tags")
        g["size"] = review_size(g["review_count"] or 0)
        g["has_coop"] = any(t["tagid"] == COOP for t in g["tags"])
        g["has_pvp"] = any(t["tagid"] == PVP for t in g["tags"])
        g["target_tags"] = [t["tagid"] for t in g["tags"] if t["tagid"] in target]
        g["seed_source"] = seeds.get(appid, {}).get("source")
        games.append(g)
    eligible = [g for g in games if not g["exclusion_reasons"]]
    assign_series(eligible)
    reasons = Counter(r for g in games for r in g["exclusion_reasons"])
    log(f"seeds {len(seeds)}, requested {len(requested)}, fetched {len(items)}, failed {len(failed_ids)}, "
        f"eligible {len(eligible)}")
    log("exclusion reasons: " + json.dumps(dict(reasons.most_common()), ensure_ascii=False))

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    with (MANIFEST_DIR / "candidates_review.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["appid", "name", "eligible", "exclusion_reasons", "bucket", "size", "review_count",
                    "percent_positive", "korean", "english", "windows", "has_coop", "has_pvp", "series_id",
                    "developer_key", "fetched_at", "seed_source"])
        for g in games:
            w.writerow([g["appid"], g["name"], not g["exclusion_reasons"], ";".join(g["exclusion_reasons"]),
                        g["bucket"], g["size"], g["review_count"], g["percent_positive"], g["korean"],
                        g["english"], g["windows"], g["has_coop"], g["has_pvp"], g.get("series_id"),
                        g["developer_key"], g["fetched_at"], g["seed_source"]])
        for a in sorted(failed_ids):
            w.writerow([a, "", False, "fetch_failed", "", "", "", "", "", "", "", "", "", "", "", "", ""])

    chosen, diag = select(eligible, log)
    manifest = {
        "catalog_policy_version": policy.versions["catalog_policy_version"],
        "raw_run_id": args.run_id,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "criteria": {"min_reviews": 500, "min_percent_positive": 75, "review_summary": "summary_filtered",
                     "store_context": "GetItems country_code=KR language=koreana",
                     "adult_only_rule": "content_descriptorids에 3 또는 4가 있으면 제외",
                     "base_game_rule": "GetItems type == 0 (게임)",
                     "korean_rule": "supported_languages에 elanguage 4 supported=true",
                     "quotas": QUOTAS, "min_coop": MIN_COOP, "min_pvp": MIN_PVP,
                     "max_series": MAX_SERIES, "max_developer": MAX_DEVELOPER},
        "counts": {"seeds": len(seeds), "fetched": len(items), "failed": len(failed_ids),
                   "eligible": len(eligible), "exclusion_reasons": dict(reasons)},
        "selection": diag,
    }
    if chosen is None:
        manifest["status"] = "incomplete"
        log("카탈로그 선정 미완료: 제약을 동시에 만족하는 300개 없음. 부족 슬롯:")
        for k, v in diag["slots"].items():
            if v["available"] < v["quota"]:
                log(f"  {k}: available {v['available']} < quota {v['quota']}")
        (MANIFEST_DIR / "catalog_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        (MANIFEST_DIR / "build_report.txt").write_text("\n".join(lines) + "\n")
        raise SystemExit(1)

    checks = verify(chosen)
    for k, v in checks.items():
        log(f"  [{'OK' if v else 'FAIL'}] {k}")
    manifest["checks"] = checks
    manifest["status"] = "frozen" if all(checks.values()) else "incomplete"
    checked_at = time.strftime("%Y-%m-%d")
    manifest["games"] = [{
        "appid": g["appid"], "name": g["name"], "bucket": g["bucket"], "review_size": g["size"],
        "review_count": g["review_count"], "percent_positive": g["percent_positive"],
        "korean": g["korean"], "english": g["english"], "has_coop": g["has_coop"], "has_pvp": g["has_pvp"],
        "target_tags": g["target_tags"], "series_id": g["series_id"], "franchise": g["franchise"],
        "developer_key": g["developer_key"], "developers": g["developers"], "slot_rank": g["slot_rank"],
        "seed_source": g["seed_source"], "fetched_at": g["fetched_at"],
        "review": {"result": "pass", "checked_at": checked_at,
                   "source": f"GetItems(KR/koreana) raw run {args.run_id}",
                   "checked_fields": ["type", "visible", "purchasable_kr", "windows", "vr_only", "early_access",
                                      "release", "content_descriptorids", "reviews", "languages", "tags"]},
    } for g in sorted(chosen, key=lambda g: (g["bucket"], SIZES.index(g["size"]), g["slot_rank"]))]
    (MANIFEST_DIR / "catalog_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (MANIFEST_DIR / "build_report.txt").write_text("\n".join(lines) + "\n")
    if manifest["status"] != "frozen":
        raise SystemExit("검증 실패: 스냅샷을 만들지 않음")

    from gembti.catalog import write_snapshot_db
    snapshot_id = f"cat-{args.run_id}-{time.strftime('%Y%m%d%H%M%S')}"
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    path = STAGING_DIR / f"{snapshot_id}.db"
    write_snapshot_db(path, snapshot_id, chosen, ko, en, manifest_meta={
        "raw_run_id": args.run_id, "catalog_policy_version": manifest["catalog_policy_version"]})
    log(f"staging snapshot: {path}")
    (MANIFEST_DIR / "build_report.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
