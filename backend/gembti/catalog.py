"""카탈로그 스냅샷 DB: 작성·검증·승격(9.2)과 서비스용 읽기.

서비스는 active_snapshot.json이 가리키는 마지막 정상 스냅샷만 읽는다. 외부 API를 호출하지 않는다.
승격은 검증 통과 후 포인터 파일을 tmp→rename으로 원자 교체하고 직전 포인터·DB를 백업한다.
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

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tags (tagid INTEGER PRIMARY KEY, name_ko TEXT, name_en TEXT);
CREATE TABLE games (
  appid INTEGER PRIMARY KEY, name TEXT NOT NULL, short_description TEXT, short_description_lang TEXT,
  header_image TEXT, capsule_image TEXT, store_url TEXT NOT NULL,
  windows TEXT NOT NULL CHECK (windows IN ('true','false','unknown')),
  mac TEXT NOT NULL CHECK (mac IN ('true','false','unknown')),
  linux TEXT NOT NULL CHECK (linux IN ('true','false','unknown')),
  korean TEXT NOT NULL CHECK (korean IN ('true','false','unknown')),
  english TEXT NOT NULL,
  review_count INTEGER NOT NULL, percent_positive INTEGER NOT NULL,
  series_id TEXT NOT NULL, developer_key TEXT NOT NULL,
  manifest_bucket TEXT NOT NULL, review_size TEXT NOT NULL, fetched_at TEXT NOT NULL
);
CREATE TABLE game_tags (appid INTEGER NOT NULL, tagid INTEGER NOT NULL, weight INTEGER, rank INTEGER NOT NULL,
  PRIMARY KEY (appid, tagid));
"""
EXPECTED_GAMES = 300


class CatalogUnavailable(RuntimeError):
    pass


def write_snapshot_db(path: Path, snapshot_id: str, games: list[dict], ko: dict, en: dict,
                      manifest_meta: dict) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    with con:
        con.executescript(SCHEMA)
        meta = {"snapshot_id": snapshot_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "game_count": str(len(games)), **{k: str(v) for k, v in manifest_meta.items()}}
        con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
        con.executemany("INSERT INTO tags VALUES (?,?,?)",
                        [(t, ko.get(t), en.get(t)) for t in sorted(set(ko) | set(en))])
        for g in games:
            con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                g["appid"], g["name"], g["short_description"], g["short_description_lang"], g["header_image"],
                g["capsule_image"], g["store_url"], g["windows"], g["mac"], g["linux"], g["korean"],
                g["english"], g["review_count"], g["percent_positive"], g["series_id"], g["developer_key"],
                g["bucket"], g["size"], g["fetched_at"]))
            con.executemany("INSERT INTO game_tags VALUES (?,?,?,?)",
                            [(g["appid"], t["tagid"], t["weight"], t["rank"]) for t in g["tags"]])
    con.close()


def validate_snapshot(path: Path, expected: int = EXPECTED_GAMES) -> list[str]:
    """승격 차단 조건: 손상·게임 수 불일치·태그 누락·필터 필수 필드 손상."""
    errs = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            errs.append("integrity_check 실패")
        n = con.execute("SELECT COUNT(*) FROM games").fetchone()[0]
        if n != expected:
            errs.append(f"게임 수 {n} != {expected}")
        no_tags = con.execute("SELECT COUNT(*) FROM games WHERE appid NOT IN (SELECT appid FROM game_tags)").fetchone()[0]
        if no_tags:
            errs.append(f"태그 없는 게임 {no_tags}개")
        bad = con.execute("SELECT COUNT(*) FROM games WHERE korean NOT IN ('true','false') OR windows != 'true' "
                          "OR name IS NULL OR name = '' OR store_url IS NULL").fetchone()[0]
        if bad:
            errs.append(f"필터 필수 필드 손상 {bad}개")
        from gembti.policy import get_policy
        targets = {t.tagid for t in get_policy().tags}
        have = {r[0] for r in con.execute("SELECT tagid FROM tags WHERE name_ko IS NOT NULL AND name_en IS NOT NULL")}
        if targets - have:
            errs.append(f"판정 태그 사전 누락 {sorted(targets - have)}")
        if not con.execute("SELECT value FROM meta WHERE key='snapshot_id'").fetchone():
            errs.append("snapshot_id 없음")
        con.close()
    except sqlite3.Error as e:
        errs.append(f"sqlite 오류: {e}")
    return errs


def read_active_pointer() -> dict | None:
    try:
        return json.loads(config.ACTIVE_SNAPSHOT_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def promote(staging_path: Path, expected: int = EXPECTED_GAMES) -> dict:
    errs = validate_snapshot(staging_path, expected)
    if errs:
        raise ValueError("승격 거부(기존 정상 스냅샷 유지): " + "; ".join(errs))
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f"file:{staging_path}?mode=ro", uri=True)
    sid = con.execute("SELECT value FROM meta WHERE key='snapshot_id'").fetchone()[0]
    con.close()
    dest = config.SNAPSHOT_DIR / f"{sid}.db"
    shutil.copy2(staging_path, dest)
    if validate_snapshot(dest, expected):
        dest.unlink()
        raise ValueError("복사본 검증 실패")
    prev = read_active_pointer()
    stamp = time.strftime("%Y%m%d%H%M%S")
    if prev:
        (config.BACKUP_DIR / f"active_snapshot.{stamp}.json").write_text(json.dumps(prev))
    pointer = {"snapshot_id": sid, "path": str(dest), "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "previous": prev.get("snapshot_id") if prev else None}
    tmp = config.ACTIVE_SNAPSHOT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(pointer))
    os.replace(tmp, config.ACTIVE_SNAPSHOT_FILE)
    return pointer


def rollback(snapshot_id: str, expected: int = EXPECTED_GAMES) -> dict:
    path = config.SNAPSHOT_DIR / f"{snapshot_id}.db"
    errs = validate_snapshot(path, expected)
    if errs:
        raise ValueError("; ".join(errs))
    prev = read_active_pointer()
    pointer = {"snapshot_id": snapshot_id, "path": str(path), "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "previous": prev.get("snapshot_id") if prev else None, "rollback": True}
    tmp = config.ACTIVE_SNAPSHOT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(pointer))
    os.replace(tmp, config.ACTIVE_SNAPSHOT_FILE)
    return pointer


@dataclass
class Catalog:
    snapshot_id: str
    games: list[dict]
    by_appid: dict[int, dict]
    tag_names: dict[int, tuple[str | None, str | None]]
    idf: dict[int, float]


_cache: dict = {"key": None, "catalog": None}


def load_active_catalog() -> Catalog:
    """마지막 정상 스냅샷을 읽어 메모리에 둔다. 포인터가 바뀌면 다시 읽는다."""
    ptr = read_active_pointer()
    if not ptr:
        raise CatalogUnavailable("활성 카탈로그 스냅샷이 없습니다")
    key = (ptr["snapshot_id"], ptr["path"])
    if _cache["key"] == key:
        return _cache["catalog"]
    path = Path(ptr["path"])
    if not path.exists():
        raise CatalogUnavailable("활성 스냅샷 파일이 없습니다")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        games = [dict(r) for r in con.execute("SELECT * FROM games ORDER BY appid")]
        tags: dict[int, list] = {}
        for r in con.execute("SELECT appid, tagid, weight, rank FROM game_tags ORDER BY appid, rank"):
            tags.setdefault(r["appid"], []).append({"tagid": r["tagid"], "weight": r["weight"], "rank": r["rank"]})
        names = {r["tagid"]: (r["name_ko"], r["name_en"]) for r in con.execute("SELECT * FROM tags")}
        con.close()
    except sqlite3.Error as e:
        raise CatalogUnavailable(f"스냅샷 손상: {e}") from e
    for g in games:
        g["tags"] = tags.get(g["appid"], [])
    from gembti.policy import get_policy
    from gembti.recommend import compute_idf
    cat = Catalog(ptr["snapshot_id"], games, {g["appid"]: g for g in games}, names,
                  compute_idf(get_policy(), games))
    _cache.update(key=key, catalog=cat)
    return cat
