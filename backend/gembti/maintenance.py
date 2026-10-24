"""운영 유지 작업: 서비스 DB 백업(SQLite backup API)·만료 서술 삭제·복구.

uv run python -m gembti.maintenance backup [--keep 14]
uv run python -m gembti.maintenance purge
uv run python -m gembti.maintenance convert-backups   # 원문 포함 기존 백업을 안전 사본으로 교체
uv run python -m gembti.maintenance restore <backup.db>   # API 중지 후 실행
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from gembti import config, db
from gembti.service import purge_expired_free_texts

BACKUP_META = "gembti_backup_meta"


def _shred(path: Path) -> None:
    """임시 사본을 0으로 덮어쓴 뒤 삭제."""
    for f in (path, Path(str(path) + "-journal"), Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if f.exists():
            size = f.stat().st_size
            with open(f, "r+b") as fh:
                fh.write(b"\0" * size)
                fh.flush()
                os.fsync(fh.fileno())
            f.unlink()


def _texts(con: sqlite3.Connection) -> list[str]:
    try:
        return [r[0] for r in con.execute("SELECT text FROM free_texts")]
    except sqlite3.OperationalError:
        return []


def make_safe_copy(src: sqlite3.Connection, dest: Path) -> dict:
    """서술 원문을 뺀 백업: backup API 사본 → 서술 삭제 → VACUUM INTO 재작성 → 원문 바이트 부재 검증.
    src(서비스 DB 또는 기존 백업)는 읽기만 한다."""
    originals = _texts(src)
    tmp = dest.with_name(f".tmp-{dest.name}")
    t = sqlite3.connect(tmp)
    tmp.chmod(0o600)
    with t:
        src.backup(t)
    t.execute("PRAGMA secure_delete = ON")
    t.execute("DELETE FROM free_texts")
    t.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP_META} (key TEXT PRIMARY KEY, value TEXT)")
    t.execute(f"INSERT OR REPLACE INTO {BACKUP_META} VALUES ('free_texts', 'removed'), ('created_at', ?)",
              (time.strftime("%Y-%m-%dT%H:%M:%S%z"),))
    t.commit()
    if dest.exists():
        dest.unlink()
    t.execute("VACUUM INTO ?", (str(dest),))
    t.close()
    _shred(tmp)
    dest.chmod(0o600)
    return verify_safe_copy(dest, originals)


def verify_safe_copy(path: Path, originals: list[str]) -> dict:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    chk = c.execute("PRAGMA integrity_check").fetchone()[0]
    n = c.execute("SELECT COUNT(*) FROM free_texts").fetchone()[0]
    meta = dict(c.execute(f"SELECT key, value FROM {BACKUP_META}").fetchall())
    c.close()
    raw = path.read_bytes()
    leaked = sum(1 for t in originals if len(t.encode()) >= 2 and t.encode() in raw)
    ok = chk == "ok" and n == 0 and meta.get("free_texts") == "removed" and leaked == 0
    if not ok:
        path.unlink(missing_ok=True)
        raise SystemExit(f"안전 백업 검증 실패: integrity={chk} free_texts={n} leaked={leaked}")
    return {"integrity": chk, "free_texts": n, "checked_texts": len(originals), "leaked": leaked}


def is_safe_backup(path: Path) -> bool:
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        v = c.execute(f"SELECT value FROM {BACKUP_META} WHERE key = 'free_texts'").fetchone()
        c.close()
        return bool(v and v[0] == "removed")
    except sqlite3.Error:
        return False


def backup(keep: int) -> Path:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.BACKUP_DIR / f"service-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = db.connect()
    res = make_safe_copy(src, dest)
    src.close()
    print(f"안전 백업(서술 제외) 검증: {res}")
    olds = sorted(config.BACKUP_DIR.glob("service-*.db"))
    for f in olds[:-keep]:
        _shred(f)
    return dest


def convert_existing() -> list[str]:
    """원문 포함 기존 백업을 안전 사본으로 교체(검증 후 원본 파쇄)."""
    done = []
    for f in sorted(config.BACKUP_DIR.glob("service-*.db")):
        if is_safe_backup(f):
            continue
        new = f.with_name(f.stem + ".safe.db")
        src = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        res = make_safe_copy(src, new)
        src.close()
        _shred(f)
        new.rename(f)
        done.append(f"{f.name}: {res}")
    return done


def purge_experiment_copies(con: sqlite3.Connection) -> dict:
    """11.2: 서비스에서 삭제된 제출의 실험 입력·원시 출력 제거, 만료된 서술 사본 비우기."""
    exp = config.VAR_DIR / "experiments" / "jev.db"
    if not exp.exists():
        return {"experiment_db": "없음"}
    alive = {r[0] for r in con.execute("SELECT submission_id FROM submissions")}
    texts = {(r[0], r[1]) for r in con.execute("SELECT submission_id, free_text_id FROM free_texts")}
    e = sqlite3.connect(exp)
    removed = blanked = 0
    with e:
        for (sub,) in e.execute("SELECT DISTINCT submission_id FROM inputs").fetchall():
            if sub not in alive:
                removed += e.execute("DELETE FROM inputs WHERE submission_id = ?", (sub,)).rowcount
                e.execute("DELETE FROM calls WHERE submission_id = ?", (sub,))
                e.execute("DELETE FROM suggestions WHERE submission_id = ?", (sub,))
        for sub, fts in e.execute("SELECT submission_id, free_texts_json FROM inputs").fetchall():

            kept = {k: v for k, v in json.loads(fts).items() if (sub, k) in texts}
            if kept != json.loads(fts):
                e.execute("UPDATE inputs SET free_texts_json = ? WHERE submission_id = ?",
                          (json.dumps(kept, ensure_ascii=False), sub))
                # 서술이 들어간 J-QF 요청 원문·출력도 제거
                e.execute("DELETE FROM calls WHERE submission_id = ? AND condition = 'J-QF'", (sub,))
                blanked += 1
    e.close()
    return {"removed_inputs": removed, "expired_text_copies": blanked}


def restore(path: Path) -> None:
    """임시 사본에서 만료 서술을 먼저 지운 뒤 설치. 설치 후 실험 사본도 정리. API는 미리 중지할 것."""
    tmp = config.SERVICE_DB.with_name(f".restore-{time.strftime('%Y%m%d-%H%M%S')}.db")
    shutil.copy2(path, tmp)
    c = sqlite3.connect(tmp)
    if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        c.close()
        _shred(tmp)
        raise SystemExit("복구 대상 손상")
    c.execute("PRAGMA secure_delete = ON")
    expired = purge_expired_free_texts(c)
    c.execute(f"DROP TABLE IF EXISTS {BACKUP_META}")
    c.commit()
    c.close()
    if config.SERVICE_DB.exists():
        keep = config.SERVICE_DB.with_name(f"service.before-restore-{time.strftime('%Y%m%d-%H%M%S')}.db")
        shutil.copy2(config.SERVICE_DB, keep)
        keep.chmod(0o600)
        print(f"현재 DB 보존: {keep}")
    for suffix in ("-wal", "-shm"):
        Path(str(config.SERVICE_DB) + suffix).unlink(missing_ok=True)
    os.replace(tmp, config.SERVICE_DB)
    config.SERVICE_DB.chmod(0o600)
    con = db.connect()
    print(f"복구 완료: {path} (만료 서술 {expired}건 제거, 실험 사본 정리 {purge_experiment_copies(con)})")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--keep", type=int, default=14)
    sub.add_parser("purge")
    sub.add_parser("convert-backups")
    r = sub.add_parser("restore")
    r.add_argument("path")
    args = ap.parse_args()
    if args.cmd == "backup":
        print(backup(args.keep))
    elif args.cmd == "purge":
        con = db.connect()
        db.migrate(con)
        print(f"만료 서술 삭제: {purge_expired_free_texts(con)}건")
        print(f"실험 사본 정리: {purge_experiment_copies(con)}")
        con.close()
    elif args.cmd == "convert-backups":
        print("\n".join(convert_existing()) or "변환할 원문 포함 백업 없음")
    else:
        restore(Path(args.path))


if __name__ == "__main__":
    main()
