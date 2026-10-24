"""JEV 오프라인 도구 경계: 동의만 내보내기, 서비스 DB 불변, 키 없으면 실행 거부."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

from gembti import config, db, service
from gembti.engine import normalize_answers, score_tags
from gembti.policy import get_policy

REF = {**{f"Q{i:02d}": "B" for i in range(1, 21)}, "Q03": "C", "Q17": "C", "Q18": "C"}
TOOL = Path(__file__).resolve().parents[2] / "experiments" / "jev" / "jev_tool.py"


@pytest.fixture
def jev(var_dir, monkeypatch):
    spec = importlib.util.spec_from_file_location("jev_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "EXP_DIR", var_dir / "experiments")
    monkeypatch.setattr(mod, "EXP_DB", var_dir / "experiments" / "jev.db")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return mod


def test_export_consent_only_and_service_db_untouched(jev, no_network):
    p = get_policy()
    con = db.connect()
    db.migrate(con)
    service.create_submission(con, p, REF, {"F01": "협동이 좋아요"}, True)
    service.create_submission(con, p, REF, {"F01": "비동의"}, False)
    con.close()
    before = hashlib.sha256(config.SERVICE_DB.read_bytes()).hexdigest()
    eid = jev.export()
    jev.plan()
    jev.run("J-QF", execute=False, export_id=eid, repeat=0)
    with pytest.raises(SystemExit):
        jev.run("J-Q", execute=True, export_id=eid, repeat=0)  # 키 없음
    jev.diff()
    assert hashlib.sha256(config.SERVICE_DB.read_bytes()).hexdigest() == before
    e = jev.exp_con()
    assert e.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] == 1
    assert "비동의" not in e.execute("SELECT free_texts_json FROM inputs").fetchone()[0]
    assert e.execute("SELECT COUNT(*) FROM calls WHERE status != 'dry_run'").fetchone()[0] == 0
    assert e.execute("SELECT COUNT(*) FROM runs WHERE executed = 1").fetchone()[0] == 0


def test_jev_postprocess_masks():
    import importlib.util as u
    spec = u.spec_from_file_location("jev_tool2", TOOL)
    mod = u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = get_policy()
    fixed = score_tags(p, normalize_answers(p, {"Q01": "A", "Q02": "A"}))
    out = {"score_1695": {"score": 4.0}, "evidence_1695": {"probabilities": {"0": 0, "1": 0, "2": 1}},
           "score_3834": {"score": 4.0}, "evidence_3834": {"probabilities": {"0": 0.9, "1": 0, "2": 0.1}},
           "score_9": {"score": 4.0}, "evidence_9": {"probabilities": {"2": 1}}}
    jt = mod.jev_tags(fixed, out)
    assert jt[1695].state == "positive"
    assert jt[3834].state == "unknown"  # 근거 상태 insufficient 마스킹
    assert jt[9].state == "unknown"  # 객관식 n<2 구조 마스크 유지


def test_experiment_copies_follow_service_deletion_and_expiry(jev, no_network):
    from gembti import maintenance
    p = get_policy()
    con = db.connect()
    db.migrate(con)
    a, _, _ = service.create_submission(con, p, REF, {"F01": "지울 서술"}, True)
    b, _, _ = service.create_submission(con, p, REF, {"F01": "만료될 서술"}, True)
    jev.export()
    jev.run("J-QF", execute=False, export_id=None, repeat=0)
    service.delete_submission(con, a)
    con.execute("UPDATE free_texts SET expires_at = '2000-01-01T00:00:00+09:00' WHERE submission_id = ?", (b,))
    service.purge_expired_free_texts(con)
    res = maintenance.purge_experiment_copies(con)
    assert res == {"removed_inputs": 1, "expired_text_copies": 1}
    e = jev.exp_con()
    assert [r[0] for r in e.execute("SELECT submission_id FROM inputs")] == [b]
    assert "만료될 서술" not in e.execute("SELECT free_texts_json FROM inputs").fetchone()[0]
    assert e.execute("SELECT COUNT(*) FROM calls WHERE condition = 'J-QF'").fetchone()[0] == 0
