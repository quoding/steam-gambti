"""서비스 SQLite. submission_id는 고유 관계키, session_id는 비고유 판정 seed 인덱스.

JEV 실험 결과 테이블은 여기에 두지 않는다(별도 실험 DB).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from gembti import config

MIGRATIONS: list[str] = [
    # 1: 기본 구조
    """
    CREATE TABLE submissions (
      submission_id TEXT PRIMARY KEY,
      session_id TEXT NOT NULL,
      owner_key_hash TEXT NOT NULL,
      mode TEXT NOT NULL CHECK (mode IN ('service','eval')),
      participant_code TEXT,
      questionnaire_version TEXT NOT NULL, scoring_version TEXT NOT NULL,
      type_mapping_version TEXT NOT NULL, tie_policy_version TEXT NOT NULL,
      algorithm_version TEXT NOT NULL,
      experiment_consent INTEGER NOT NULL DEFAULT 0,
      unanswered_to_x TEXT NOT NULL DEFAULT '[]',
      created_at TEXT NOT NULL
    );
    CREATE INDEX idx_submissions_session ON submissions(session_id);
    CREATE TABLE answers (
      submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
      question_id TEXT NOT NULL, code TEXT NOT NULL,
      PRIMARY KEY (submission_id, question_id)
    );
    CREATE TABLE free_texts (
      submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
      free_text_id TEXT NOT NULL, text TEXT NOT NULL, expires_at TEXT NOT NULL,
      PRIMARY KEY (submission_id, free_text_id)
    );
    CREATE TABLE results (
      submission_id TEXT PRIMARY KEY REFERENCES submissions(submission_id) ON DELETE CASCADE,
      session_id TEXT NOT NULL, type_id TEXT NOT NULL,
      card_json TEXT NOT NULL, tags_json TEXT NOT NULL, tie_log_json TEXT NOT NULL, tie_stats_json TEXT NOT NULL,
      versions_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE rec_requests (
      submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
      request_no INTEGER NOT NULL, purpose TEXT NOT NULL,
      filters_json TEXT NOT NULL, played_json TEXT NOT NULL, snapshot_id TEXT NOT NULL,
      algorithm_version TEXT NOT NULL, summary_json TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY (submission_id, request_no)
    );
    CREATE TABLE recommendations (
      submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
      request_no INTEGER NOT NULL, rank INTEGER NOT NULL, appid INTEGER NOT NULL,
      kind TEXT NOT NULL CHECK (kind IN ('personalized','reference','popular_baseline')),
      matched_tags_json TEXT NOT NULL, detail_json TEXT NOT NULL,
      PRIMARY KEY (submission_id, request_no, kind, rank)
    );
    CREATE TABLE feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      submission_id TEXT NOT NULL REFERENCES submissions(submission_id) ON DELETE CASCADE,
      rating INTEGER CHECK (rating BETWEEN 1 AND 5), comment TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE eval_participants (
      code TEXT PRIMARY KEY, phase TEXT NOT NULL CHECK (phase IN ('pilot','main')),
      study_version TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT
    );
    CREATE TABLE eval_sessions (
      code TEXT NOT NULL REFERENCES eval_participants(code),
      submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(submission_id) ON DELETE CASCADE,
      started_at TEXT NOT NULL, objective_seconds REAL,
      favorites_json TEXT NOT NULL, hidden_appid INTEGER, hidden_in_catalog INTEGER,
      hit_rank INTEGER, ab_seed TEXT NOT NULL, fixed_is_a INTEGER NOT NULL,
      list_a_json TEXT NOT NULL, list_b_json TEXT NOT NULL, list_hash TEXT NOT NULL,
      overlap_count INTEGER NOT NULL, fixed_all_personalized INTEGER NOT NULL, regular_card INTEGER NOT NULL,
      both_full INTEGER NOT NULL,
      ratings_json TEXT, preference TEXT CHECK (preference IN ('A','B','tie')), familiar_json TEXT,
      completed_at TEXT, excluded_reason TEXT,
      PRIMARY KEY (code, submission_id)
    );
    """,
    # 2: 평가에서 게임별 '어색한 추천' 사유
    """
    ALTER TABLE eval_sessions ADD COLUMN awkward_json TEXT
    """,
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.SERVICE_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def migrate(con: sqlite3.Connection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = con.execute("SELECT version FROM schema_version").fetchone()
    current = row[0] if row else 0
    for i, sql in enumerate(MIGRATIONS[current:], start=current + 1):
        con.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split(sql):
                con.execute(stmt)
            con.execute("DELETE FROM schema_version")
            con.execute("INSERT INTO schema_version VALUES (?)", (i,))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def _split(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


@contextmanager
def tx(con: sqlite3.Connection):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
