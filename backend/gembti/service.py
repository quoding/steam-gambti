"""검사 제출·결과·추천 저장. 외부 API·JEV를 호출하지 않는다."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from gembti import config
from gembti.catalog import Catalog, CatalogUnavailable, load_active_catalog
from gembti.engine import Judgement, judge, tie_stats
from gembti.policy import Policy
from gembti.recommend import Filters, recommend

KST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def hash_key(key: str) -> str:
    return hashlib.sha256(("gembti-owner-v1|" + key).encode()).hexdigest()


def check_owner(con: sqlite3.Connection, submission_id: str, owner_key: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)).fetchone()
    if row is None or not hmac.compare_digest(row["owner_key_hash"], hash_key(owner_key or "")):
        raise PermissionError("not found")
    return row


def purge_expired_free_texts(con: sqlite3.Connection) -> int:
    return con.execute("DELETE FROM free_texts WHERE expires_at < ?", (now_iso(),)).rowcount


def validate_free_texts(policy: Policy, free_texts: dict[str, str] | None) -> dict[str, str]:
    allowed = {f["free_text_id"] for f in policy.free_texts}
    out = {}
    for k, v in (free_texts or {}).items():
        if k not in allowed:
            raise ValueError(f"알 수 없는 서술 문항 {k}")
        v = (v or "").strip()
        if len(v) > config.FREE_TEXT_MAX_CHARS:
            raise ValueError(f"{k}: {config.FREE_TEXT_MAX_CHARS}자 초과")
        if v:
            out[k] = v
    return out


def validate_played(played: list[int] | None) -> list[int]:
    played = sorted({int(a) for a in (played or [])})
    if len(played) > config.MAX_PLAYED_APPIDS:
        raise ValueError("이미 해본 게임은 최대 50개")
    return played


def create_submission(con: sqlite3.Connection, policy: Policy, answers: dict, free_texts: dict | None,
                      consent: bool, mode: str = "service", participant_code: str | None = None
                      ) -> tuple[str, str, Judgement]:
    j = judge(policy, answers)
    fts = validate_free_texts(policy, free_texts)
    unanswered = sorted(q for q in policy.question_ids if answers.get(q) in (None, ""))
    sub_id = secrets.token_hex(16)
    owner_key = secrets.token_urlsafe(24)
    created = now_iso()
    expires = (datetime.now(KST) + timedelta(days=config.FREE_TEXT_RETENTION_DAYS)).isoformat(timespec="seconds")
    v = policy.versions
    con.execute("BEGIN IMMEDIATE")
    try:
        purge_expired_free_texts(con)
        con.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            sub_id, j.session_id, hash_key(owner_key), mode, participant_code, v["questionnaire_version"],
            v["scoring_version"], v["type_mapping_version"], v["tie_policy_version"], v["algorithm_version"],
            1 if consent else 0, json.dumps(unanswered), created))
        con.executemany("INSERT INTO answers VALUES (?,?,?)", [(sub_id, q, c) for q, c in j.answers.items()])
        con.executemany("INSERT INTO free_texts VALUES (?,?,?,?)",
                        [(sub_id, k, t, expires) for k, t in fts.items()])
        con.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?)", (
            sub_id, j.session_id, j.card.type_id,
            json.dumps({k: v for k, v in j.card.as_dict().items() if k != "tie_log"}),
            json.dumps({str(t): r.as_dict() for t, r in j.tags.items()}),
            json.dumps(j.card.tie_log), json.dumps(tie_stats(j.card)), json.dumps(j.versions), created))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return sub_id, owner_key, j


def judgement_for(con: sqlite3.Connection, policy: Policy, submission_id: str) -> Judgement:
    """저장된 객관식으로 재판정(결정적). 저장 결과와 같아야 한다."""
    rows = con.execute("SELECT question_id, code FROM answers WHERE submission_id = ?", (submission_id,)).fetchall()
    return judge(policy, {r["question_id"]: r["code"] for r in rows})


def store_recommendations(con: sqlite3.Connection, submission_id: str, purpose: str, filters: Filters,
                          played: list[int], cat: Catalog, rec: dict, baseline: list[int] | None = None) -> int:
    con.execute("BEGIN IMMEDIATE")
    try:
        n = con.execute("SELECT COALESCE(MAX(request_no), 0) + 1 FROM rec_requests WHERE submission_id = ?",
                        (submission_id,)).fetchone()[0]
        summary = {"personalized": rec["personalized_count"], "reference": rec["reference_count"],
                   "shortfall": rec["shortfall"], "candidate_count": rec["candidate_count"],
                   "strong_avoid_excluded": sum(1 for e in rec["excluded"] if "strong_avoid" in e["reasons"]),
                   "weak_penalty_only": len(rec["weak_penalty_only"]),
                   "excluded": rec["excluded"]}
        con.execute("INSERT INTO rec_requests VALUES (?,?,?,?,?,?,?,?,?)", (
            submission_id, n, purpose, json.dumps({"korean_only": filters.korean_only, "os": filters.os}),
            json.dumps(played), cat.snapshot_id, rec["algorithm_version"], json.dumps(summary), now_iso()))
        con.executemany("INSERT INTO recommendations VALUES (?,?,?,?,?,?,?)", [
            (submission_id, n, r["rank"], r["appid"], r["kind"], json.dumps(r["matched_tags"]), json.dumps(r))
            for r in rec["items"]])
        if baseline is not None:
            con.executemany("INSERT INTO recommendations VALUES (?,?,?,?,?,?,?)", [
                (submission_id, n, i + 1, a, "popular_baseline", "[]", "{}") for i, a in enumerate(baseline)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return n


# ---------------------------------------------------------------- 사용자 화면용 payload


def _tag_name(policy: Policy, cat: Catalog | None, tagid: int) -> str:
    if tagid in policy.tag_by_id:
        return policy.tag_by_id[tagid].name_ko
    if cat and tagid in cat.tag_names:
        ko, en = cat.tag_names[tagid]
        return ko or en or str(tagid)
    return str(tagid)


def _answer_label(policy: Policy, qid: str, code: str) -> str:
    q = policy.questions[int(qid[1:]) - 1]
    return next(o["label"] for o in q["options"] if o["code"] == code)


def card_payload(policy: Policy, j: Judgement) -> dict:
    c = j.card
    tag = lambda t: {"tagid": t, "name": policy.tag_by_id[t].name_ko}
    evidence = []
    for t in c.main_tagids:
        tr = j.tags[t]
        evidence.append({**tag(t), "answers": [
            {"question_id": q, "axis": policy.questions[int(q[1:]) - 1]["axis"],
             "answer": _answer_label(policy, q, j.answers[q])} for q in tr.valid_questions]})
    return {
        "type_id": c.type_id, "name": c.type_name, "tagline": c.tagline, "is_discovering": c.is_discovering,
        "main_tags": [tag(t) for t in c.main_tagids],
        "extra_social_tags": [tag(t) for t in c.extra_social_tagids],
        "positive_tags": [tag(t) for t in c.positive_tagids],
        "evidence": evidence,
        "empty_text": policy.types["discovering"]["empty_text"] if not c.positive_tagids else None,
        "answers": [{"question_id": q, "code": code, "axis": policy.questions[int(q[1:]) - 1]["axis"],
                     "answer": _answer_label(policy, q, code)} for q, code in j.answers.items()],
    }


def game_payload(policy: Policy, cat: Catalog, appid: int) -> dict:
    g = cat.by_appid[appid]
    shown = [t for t in g["tags"]][:6]
    return {
        "appid": appid, "name": g["name"], "image": g["header_image"], "store_url": g["store_url"],
        "short_description": g["short_description"] or None,
        "description_is_korean": g["short_description_lang"] == "ko",
        "korean": g["korean"], "tags": [_tag_name(policy, cat, t["tagid"]) for t in shown],
    }


def recs_payload(policy: Policy, cat: Catalog, rec: dict) -> dict:
    items = []
    for r in rec["items"]:
        p = game_payload(policy, cat, r["appid"])
        p["kind"] = r["kind"]
        matched = [_tag_name(policy, cat, t) for t in r["matched_tags"]]
        p["matched_tags"] = matched
        if r["kind"] == "personalized":
            p["reason"] = f"답변에서 끌린다고 고른 {'·'.join(matched)} 태그가 이 게임에 실제로 붙어 있어요."
        elif matched:
            p["reason"] = f"{'·'.join(matched)} 태그가 겹쳐요. 판정 근거가 적어 관심 확인용으로 보여드려요."
        else:
            p["reason"] = "판정 근거가 적어 많은 사람이 평가한 게임을 관심 확인용으로 보여드려요."
        items.append(p)
    return {"items": items, "personalized_count": rec["personalized_count"],
            "reference_count": rec["reference_count"], "shortfall": rec["shortfall"],
            "snapshot_id": cat.snapshot_id}


def run_recommendation(con, policy, submission_id, j, filters: Filters, played: list[int], purpose="service"):
    try:
        cat = load_active_catalog()
    except CatalogUnavailable as e:
        return {"status": "catalog_unavailable", "message": str(e)}
    rec = recommend(policy, j.tags, cat.games, filters, set(played), cat.idf)
    store_recommendations(con, submission_id, purpose, filters, played, cat, rec)
    return {"status": "ok", **recs_payload(policy, cat, rec)}


def delete_submission(con: sqlite3.Connection, submission_id: str) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("DELETE FROM eval_sessions WHERE submission_id = ?", (submission_id,))
        con.execute("DELETE FROM submissions WHERE submission_id = ?", (submission_id,))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
