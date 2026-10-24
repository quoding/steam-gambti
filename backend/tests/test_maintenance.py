"""백업에 자유 서술 원문이 남지 않고, 복원 시 만료 서술이 되살아나지 않는다."""
import hashlib
import shutil
import sqlite3

from gembti import config, db, maintenance, service
from gembti.policy import get_policy

REF = {**{f"Q{i:02d}": "B" for i in range(1, 21)}, "Q03": "C", "Q17": "C", "Q18": "C"}
SECRET = "아주특별한서술원문-백업에남으면안됨-XYZ123"


def seed(con):
    p = get_policy()
    a, _, _ = service.create_submission(con, p, REF, {"F01": SECRET, "F02": "두번째 서술 SECRET2"}, True)
    b, _, _ = service.create_submission(con, p, REF, {}, False)
    return a, b


def test_backup_has_no_free_text_and_service_db_untouched(var_dir):
    con = db.connect()
    db.migrate(con)
    seed(con)
    con.execute("PRAGMA wal_checkpoint(FULL)")
    before = hashlib.sha256(config.SERVICE_DB.read_bytes()).hexdigest()
    path = maintenance.backup(keep=14)
    assert hashlib.sha256(config.SERVICE_DB.read_bytes()).hexdigest() == before
    raw = path.read_bytes()
    assert SECRET.encode() not in raw and b"SECRET2" not in raw
    c = sqlite3.connect(path)
    assert c.execute("SELECT COUNT(*) FROM free_texts").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 2  # 다른 데이터는 그대로
    assert c.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 40
    assert maintenance.is_safe_backup(path)
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert not list(config.BACKUP_DIR.glob(".tmp-*"))  # 임시 사본 제거
    assert con.execute("SELECT COUNT(*) FROM free_texts").fetchone()[0] == 2  # 원본 서술 유지


def test_convert_existing_raw_backup(var_dir):
    con = db.connect()
    db.migrate(con)
    seed(con)
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    raw_backup = config.BACKUP_DIR / "service-20000101-000000.db"
    out = sqlite3.connect(raw_backup)
    con.backup(out)  # 예전 방식(원문 포함)
    out.close()
    assert SECRET.encode() in raw_backup.read_bytes()
    done = maintenance.convert_existing()
    assert len(done) == 1
    assert SECRET.encode() not in raw_backup.read_bytes() and maintenance.is_safe_backup(raw_backup)
    assert maintenance.convert_existing() == []


def test_restore_purges_expired_and_drops_meta(var_dir):
    con = db.connect()
    db.migrate(con)
    seed(con)
    # 서술이 남아 있는 옛 백업을 흉내 내고, 그중 하나는 만료 상태로 만든다
    old = var_dir / "old.db"
    o = sqlite3.connect(old)
    con.backup(o)
    o.execute("UPDATE free_texts SET expires_at = '2000-01-01T00:00:00+09:00' WHERE free_text_id = 'F01'")
    o.commit()
    o.close()
    con.close()
    maintenance.restore(old)
    c = db.connect()
    texts = [r[0] for r in c.execute("SELECT text FROM free_texts")]
    assert SECRET not in texts and "두번째 서술 SECRET2" in texts
    assert c.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 2
    safe = maintenance.backup(keep=14)
    c.close()
    maintenance.restore(safe)
    c = db.connect()
    assert c.execute("SELECT COUNT(*) FROM free_texts").fetchone()[0] == 0  # 안전 백업 복구 → 서술 없음
    assert c.execute("SELECT name FROM sqlite_master WHERE name = 'gembti_backup_meta'").fetchone() is None
    shutil.rmtree(config.BACKUP_DIR, ignore_errors=True)
