"""1차 사용자 평가: 고정 배점 vs 순수 인기 기준선 블라인드 비교(12절).

참가자 코드는 운영자가 CLI(gembti.eval_admin)로 발급한다. 인원·동의·삭제는 submission_id 기준이며
session_id로 참가자를 합치지 않는다. 알고리즘명·추천 이유·유형 카드는 평가 뒤에만 공개한다.
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gembti import config, db, service
from gembti.catalog import CatalogUnavailable, load_active_catalog
from gembti.policy import get_policy
from gembti.recommend import Filters, full_personalized_ranking, popularity_baseline, recommend

router = APIRouter(prefix="/api/eval")
policy = get_policy()
STUDY_VERSION = policy.versions["experiment_protocol_version"]


class CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class FilterIn(BaseModel):
    korean_only: bool = False
    os: Literal["windows", "mac", "linux"] = "windows"


class EvalSubmitIn(CodeIn):
    answers: dict[str, Literal["A", "B", "C", "D", "E", "X"] | None]
    free_texts: dict[str, str] = Field(default_factory=dict)
    experiment_consent: bool = False
    filters: FilterIn = Field(default_factory=FilterIn)
    played: list[int] = Field(default_factory=list, max_length=config.MAX_PLAYED_APPIDS)
    favorites: list[int] = Field(min_length=3, max_length=3)
    objective_seconds: float | None = Field(default=None, ge=0, le=86400)


class RateIn(CodeIn):
    submission_id: str = Field(max_length=64)
    ratings: dict[str, Literal["interested", "neutral", "not_interested"]]
    preference: Literal["A", "B", "tie"]
    familiar: list[int] = Field(default_factory=list, max_length=10)
    played_late: list[int] = Field(default_factory=list, max_length=10)
    awkward: dict[str, str] = Field(default_factory=dict, max_length=10)  # appid -> 어색한 이유(선택)


def _participant(con, code: str):
    row = con.execute("SELECT * FROM eval_participants WHERE code = ?", (code.strip().upper(),)).fetchone()
    if row is None:
        raise HTTPException(404, "unknown_code")
    return row


def hidden_favorite(code: str, favorites: list[int]) -> int:
    """12.3: 익명 참가자 코드로 재현 가능하게 1개를 숨긴다."""
    h = int(hashlib.sha256(f"gembti-hide-v1|{code}".encode()).hexdigest(), 16)
    return favorites[h % 3]


def ab_layout(code: str, submission_id: str, fixed: list[int], baseline: list[int]):
    seed = hashlib.sha256(f"gembti-ab-v1|{code}|{submission_id}".encode()).hexdigest()
    rng = random.Random(int(seed, 16))  # 기록된 seed로 재현 가능한 섞기
    fixed_is_a = rng.random() < 0.5
    f, b = fixed[:], baseline[:]
    rng.shuffle(f)
    rng.shuffle(b)
    a_list, b_list = (f, b) if fixed_is_a else (b, f)
    return seed, fixed_is_a, a_list, b_list


@router.post("/start")
def start(body: CodeIn):
    con = db.connect()
    try:
        p = _participant(con, body.code)
        done = con.execute("SELECT COUNT(*) FROM eval_sessions WHERE code = ? AND completed_at IS NOT NULL",
                           (p["code"],)).fetchone()[0]
    finally:
        con.close()
    return {"ok": True, "phase": p["phase"], "completed_before": done}


@router.post("/submit")
def submit(body: EvalSubmitIn):
    con = db.connect()
    try:
        p = _participant(con, body.code)
        code = p["code"]
        try:
            cat = load_active_catalog()
        except CatalogUnavailable:
            raise HTTPException(503, "catalog_unavailable")
        played = service.validate_played(body.played)
        favorites = [int(a) for a in body.favorites]
        if len(set(favorites)) != 3:
            raise HTTPException(422, "favorites_must_be_distinct")
        sub_id, owner_key, j = service.create_submission(con, policy, body.answers, body.free_texts,
                                                         body.experiment_consent, "eval", code)
        filters = Filters(**body.filters.model_dump())
        exclude = set(played) | set(favorites)  # 좋아하는 게임은 제외에만 사용
        rec = recommend(policy, j.tags, cat.games, filters, exclude, cat.idf)
        baseline = popularity_baseline(cat.games, filters, exclude, policy)
        service.store_recommendations(con, sub_id, "eval", filters, sorted(exclude), cat, rec, baseline)
        fixed = [r["appid"] for r in rec["items"]]
        hidden = hidden_favorite(code, favorites)
        in_cat = hidden in cat.by_appid
        hit_rank = None
        if in_cat:
            ranking = full_personalized_ranking(policy, j.tags, cat.games, filters, exclude - {hidden}, cat.idf)
            hit_rank = ranking.index(hidden) + 1 if hidden in ranking else None
        seed, fixed_is_a, a_list, b_list = ab_layout(code, sub_id, fixed, baseline)
        list_hash = hashlib.sha256(json.dumps([a_list, b_list]).encode()).hexdigest()
        con.execute("INSERT INTO eval_sessions (code, submission_id, started_at, objective_seconds, favorites_json, "
                    "hidden_appid, hidden_in_catalog, hit_rank, ab_seed, fixed_is_a, list_a_json, list_b_json, "
                    "list_hash, overlap_count, fixed_all_personalized, regular_card, both_full) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        code, sub_id, service.now_iso(), body.objective_seconds, json.dumps(favorites), hidden,
                        int(in_cat), hit_rank, seed, int(fixed_is_a), json.dumps(a_list), json.dumps(b_list),
                        list_hash, len(set(fixed) & set(baseline)), int(rec["personalized_count"] == 5),
                        int(not j.card.is_discovering), int(len(fixed) == 5 and len(baseline) == 5)))
    finally:
        con.close()
    # 같은 게임은 양쪽에서 같은 제목·이미지·소개로 표시. 이유·종류는 숨김.
    show = lambda ids: [{k: v for k, v in service.game_payload(policy, cat, a).items()} for a in ids]
    return {"submission_id": sub_id, "owner_key": owner_key, "list_a": show(a_list), "list_b": show(b_list)}


@router.post("/rate")
def rate(body: RateIn):
    con = db.connect()
    try:
        p = _participant(con, body.code)
        s = con.execute("SELECT * FROM eval_sessions WHERE code = ? AND submission_id = ?",
                        (p["code"], body.submission_id)).fetchone()
        if s is None:
            raise HTTPException(404, "not_found")
        if s["completed_at"]:
            raise HTTPException(409, "already_rated")
        shown = set(json.loads(s["list_a_json"])) | set(json.loads(s["list_b_json"]))
        if {int(k) for k in body.ratings} != shown:
            raise HTTPException(422, "rate_every_game")
        if not {int(k) for k in body.awkward} <= shown or any(len(v) > 200 for v in body.awkward.values()):
            raise HTTPException(422, "invalid_awkward")
        excluded = "played_late" if set(body.played_late) & shown else None
        con.execute("UPDATE eval_sessions SET ratings_json = ?, preference = ?, familiar_json = ?, completed_at = ?, "
                    "excluded_reason = ?, awkward_json = ? WHERE submission_id = ?",
                    (json.dumps(body.ratings), body.preference,
                     json.dumps({"familiar": body.familiar, "played_late": body.played_late}),
                     service.now_iso(), excluded,
                     json.dumps({k: v.strip() for k, v in body.awkward.items()}, ensure_ascii=False),
                     body.submission_id))
        j = service.judgement_for(con, policy, body.submission_id)
        recs = con.execute("SELECT appid, detail_json FROM recommendations WHERE submission_id = ? AND kind != "
                           "'popular_baseline' ORDER BY rank", (body.submission_id,)).fetchall()
    finally:
        con.close()
    cat = load_active_catalog()
    reveal = {"fixed_list": "A" if s["fixed_is_a"] else "B",
              "card": service.card_payload(policy, j),
              "reasons": {r["appid"]: json.loads(r["detail_json"])["kind"] for r in recs},
              "fixed_items": service.recs_payload(policy, cat, {
                  "items": [json.loads(r["detail_json"]) for r in recs],
                  "personalized_count": 0, "reference_count": 0, "shortfall": 0})["items"]}
    return {"ok": True, "excluded_reason": excluded, "reveal": reveal}
