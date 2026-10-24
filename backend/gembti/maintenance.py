"""운영 유지 작업: 서비스 DB 백업(SQLite backup API)·만료 서술 삭제·복구.

uv run python -m gembti.maintenance backup [--keep 14]
uv run python -m gembti.maintenance purge
uv run python -m gembti.maintenance restore <backup.db>   # API 중지 후 실행
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

from gembti import config, db
from gembti.service import purge_expired_free_texts


def backup(keep: int) -> Path:
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.BACKUP_DIR / f"service-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = db.connect()
    out = sqlite3.connect(dest)
    dest.chmod(0o600)  # 개인 응답 포함: 소유자만 읽기
    with out:
        src.backup(out)
    out.close()
    src.close()
    chk = sqlite3.connect(dest).execute("PRAGMA integrity_check").fetchone()[0]
    if chk != "ok":
        dest.unlink()
        raise SystemExit(f"백업 무결성 실패: {chk}")
    olds = sorted(config.BACKUP_DIR.glob("service-*.db"))
    for f in olds[:-keep]:
        f.unlink()
    return dest


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
    chk = sqlite3.connect(f"file:{path}?mode=ro", uri=True).execute("PRAGMA integrity_check").fetchone()[0]
    if chk != "ok":
        raise SystemExit(f"복구 대상 손상: {chk}")
    if config.SERVICE_DB.exists():
        keep = config.SERVICE_DB.with_name(f"service.before-restore-{time.strftime('%Y%m%d-%H%M%S')}.db")
        shutil.copy2(config.SERVICE_DB, keep)
        print(f"현재 DB 보존: {keep}")
    for suffix in ("-wal", "-shm"):
        Path(str(config.SERVICE_DB) + suffix).unlink(missing_ok=True)
    shutil.copy2(path, config.SERVICE_DB)
    print(f"복구 완료: {path} -> {config.SERVICE_DB}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--keep", type=int, default=14)
    sub.add_parser("purge")
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
    else:
        restore(Path(args.path))


if __name__ == "__main__":
    main()
