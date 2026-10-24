"""v2 카탈로그 스냅샷 (catalog_policy v2.0). v1 스냅샷·포인터와 분리.

리뷰 수·긍정률·공식 평가 문구는 같은 출처(GetAppReviews, 전체 언어)의 한 세트만 저장·표시한다.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from gembti import config

POINTER = "active_snapshot_v2.json"
SNAP_SUBDIR = "snapshots_v2"
MIN_REVIEWS = 500
MIN_POSITIVE = 0.75
FAMILY_GATE = 10  # 계열마다 W≥0.3 태그를 가진 적격 게임 수

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tags (tagid INTEGER PRIMARY KEY, name_ko TEXT, name_en TEXT);
CREATE TABLE games (
  appid INTEGER PRIMARY KEY, name TEXT NOT NULL, short_description TEXT, short_description_lang TEXT,
  header_image TEXT, store_url TEXT NOT NULL,
  windows TEXT NOT NULL, mac TEXT NOT NULL, linux TEXT NOT NULL, korean TEXT NOT NULL, english TEXT NOT NULL,
  series_id TEXT NOT NULL, developer_key TEXT NOT NULL,
  review_score INTEGER, review_score_desc TEXT,
  total_positive INTEGER NOT NULL, total_negative INTEGER NOT NULL, total_reviews INTEGER NOT NULL,
  review_source TEXT NOT NULL, review_scope_json TEXT NOT NULL, review_fetched_at TEXT NOT NULL,
  recent_status TEXT NOT NULL, getitems_fetched_at TEXT NOT NULL, is_free INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE game_tags (appid INTEGER NOT NULL, tagid INTEGER NOT NULL, weight INTEGER, rank INTEGER NOT NULL,
  PRIMARY KEY (appid, tagid));
"""


def snap_dir() -> Path:
    return config.VAR_DIR / SNAP_SUBDIR


def pointer_path() -> Path:
    return config.VAR_DIR / POINTER


def write_snapshot(path: Path, snapshot_id: str, games: list[dict], ko: dict, en: dict, meta: dict) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    with con:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO meta VALUES (?,?)",
                        {"snapshot_id": snapshot_id, "game_count": str(len(games)),
                         "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                         **{k: str(v) for k, v in meta.items()}}.items())
        con.executemany("INSERT INTO tags VALUES (?,?,?)", [(t, ko.get(t), en.get(t)) for t in sorted(set(ko) | set(en))])
        for g in games:
            r = g["review"]
            con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                g["appid"], g["name"], g["short_description"], g["short_description_lang"], g["header_image"],
                g["store_url"], g["windows"], g["mac"], g["linux"], g["korean"], g["english"], g["series_id"],
                g["developer_key"], r.get("review_score"), r.get("review_score_desc"), r["total_positive"],
                r["total_negative"], r["total_reviews"], r["source"], json.dumps(r["scope"]), r["fetched_at"],
                "unavailable", g["fetched_at"], int(bool(g.get("is_free")))))
            con.executemany("INSERT INTO game_tags VALUES (?,?,?,?)",
                            [(g["appid"], t["tagid"], t["weight"], t["rank"]) for t in g["tags"]])
    con.close()


def load_games(path: Path) -> list[dict]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    games = [dict(r) for r in con.execute("SELECT * FROM games ORDER BY appid")]
    tags: dict[int, list] = {}
    for r in con.execute("SELECT appid, tagid, weight, rank FROM game_tags ORDER BY appid, rank"):
        tags.setdefault(r[0], []).append({"tagid": r[1], "weight": r[2], "rank": r[3]})
    con.close()
    for g in games:
        g["tags"] = tags.get(g["appid"], [])
    return games


def validate(path: Path) -> tuple[list[str], dict]:
    """승격 게이트: 무결성·리뷰 하한·콘텐츠·한국어 50%·협동/PvP 40·시리즈 2·개발사 6·계열 커버리지."""
    from gembti.policy import content_status, get_policy
    from gembti.v2.library_profile import family_weights
    from gembti.v2.policy import get_v2
    errs: list[str] = []
    report: dict = {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            errs.append("integrity_check 실패")
        con.close()
        games = load_games(path)
    except sqlite3.Error as e:
        return [f"sqlite 오류: {e}"], report
    n = len(games)
    if n == 0:
        return ["게임 0개"], report
    v1, p = get_policy(), get_v2()
    bad_reviews = [g["appid"] for g in games
                   if g["total_reviews"] < MIN_REVIEWS or g["total_positive"] / g["total_reviews"] < MIN_POSITIVE]
    if bad_reviews:
        errs.append(f"리뷰 하한 위반 {bad_reviews[:10]}")
    bad_content = [g["appid"] for g in games if content_status(v1, g) in ("content_excluded", "content_review_pending")]
    if bad_content:
        errs.append(f"콘텐츠 정책 위반 {bad_content[:10]}")
    if any(g["windows"] != "true" or g["korean"] not in ("true", "false") for g in games):
        errs.append("필수 필드 손상")
    ko = sum(1 for g in games if g["korean"] == "true")
    coop = sum(1 for g in games if any(t["tagid"] == 1685 for t in g["tags"]))
    pvp = sum(1 for g in games if any(t["tagid"] == 1775 for t in g["tags"]))
    from collections import Counter
    ms = max(Counter(g["series_id"] for g in games).values())
    md = max(Counter(g["developer_key"] for g in games).values())
    cover = {f: 0 for f in p.family_by_id}
    for g in games:
        if content_status(v1, g):
            continue
        for f, w in family_weights(g["tags"], p.family_of).items():
            if w >= 0.3:
                cover[f] += 1
    report = {"games": n, "korean_ratio": round(ko / n, 4), "coop": coop, "pvp": pvp, "max_series": ms,
              "max_developer": md, "family_coverage": cover}
    if ko / n < 0.5:
        errs.append(f"한국어 지원 비율 {ko / n:.1%} < 50%")
    if coop < 40 or pvp < 40:
        errs.append(f"협동 {coop}·PvP {pvp} (각 40 이상)")
    if ms > 2 or md > 6:
        errs.append(f"시리즈 최대 {ms}(≤2)·개발사 최대 {md}(≤6)")
    short = {f: c for f, c in cover.items() if c < FAMILY_GATE}
    report["coverage_gate"] = "pass" if not short else f"fail {short}"
    return errs, report


def promote(staging: Path) -> dict:
    errs, report = validate(staging)
    if errs:
        raise ValueError("v2 승격 거부(기존 v2 스냅샷 유지): " + "; ".join(errs))
    snap_dir().mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{staging}?mode=ro", uri=True)
    sid = con.execute("SELECT value FROM meta WHERE key='snapshot_id'").fetchone()[0]
    con.close()
    dest = snap_dir() / f"{sid}.db"
    shutil.copy2(staging, dest)
    prev = read_pointer()
    ptr = {"snapshot_id": sid, "path": str(dest), "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "previous": prev.get("snapshot_id") if prev else None, "coverage_gate": report["coverage_gate"]}
    tmp = pointer_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(ptr))
    os.replace(tmp, pointer_path())
    return {**ptr, "report": report}


def read_pointer() -> dict | None:
    try:
        return json.loads(pointer_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@dataclass
class CatalogV2:
    snapshot_id: str
    games: list[dict]
    by_appid: dict[int, dict]
    idf: dict[int, float]
    coverage_gate: str


_cache: dict = {"key": None, "cat": None}


class CatalogV2Unavailable(RuntimeError):
    pass


def load_active() -> CatalogV2:
    ptr = read_pointer()
    if not ptr or not Path(ptr["path"]).exists():
        raise CatalogV2Unavailable("v2 카탈로그 없음")
    key = (ptr["snapshot_id"], ptr["path"])
    if _cache["key"] == key:
        return _cache["cat"]
    from gembti.v2.policy import get_v2
    from gembti.v2.recommend import compute_idf
    games = load_games(Path(ptr["path"]))
    cat = CatalogV2(ptr["snapshot_id"], games, {g["appid"]: g for g in games}, compute_idf(get_v2(), games),
                    ptr.get("coverage_gate", "unknown"))
    _cache.update(key=key, cat=cat)
    return cat
