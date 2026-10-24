"""겜BTI API. 저장된 DB만 사용하며 Steam·JEV를 호출하지 않는다.

실행: uv run uvicorn gembti.app:app --host 127.0.0.1 --port 8790
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gembti import config, db, evaluation, service
from gembti.catalog import CatalogUnavailable, load_active_catalog
from gembti.policy import content_status, get_policy
from gembti.recommend import Filters

policy = get_policy()


@asynccontextmanager
async def lifespan(app: FastAPI):
    con = db.connect()
    db.migrate(con)
    con.close()
    yield


app = FastAPI(title="gembti", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# ---------------------------------------------------------------- 요청 크기·빈도 제한

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "?"
    if peer in ("127.0.0.1", "::1"):  # nginx/cloudflared 뒤
        return (request.headers.get("cf-connecting-ip") or request.headers.get("x-real-ip") or peer).strip()
    return peer


@app.middleware("http")
async def limits(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE"):
        cl = request.headers.get("content-length")
        if cl is None or not cl.isdigit() or int(cl) > config.MAX_BODY_BYTES:
            return JSONResponse({"error": "request_too_large"}, status_code=413)
        key = client_ip(request)
        now = time.monotonic()
        with _lock:
            q = _hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= config.RATE_LIMIT_PER_MINUTE:
                return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": "30"})
            q.append(now)
    return await call_next(request)


def con_dep():
    return db.connect()


# ---------------------------------------------------------------- 모델

Code = Literal["A", "B", "C", "D", "E", "X"]


class FilterIn(BaseModel):
    korean_only: bool = False
    os: Literal["windows", "mac", "linux"] = "windows"


class SubmitIn(BaseModel):
    answers: dict[str, Code | None]
    free_texts: dict[str, str] = Field(default_factory=dict)
    experiment_consent: bool = False
    filters: FilterIn = Field(default_factory=FilterIn)
    played: list[int] = Field(default_factory=list, max_length=config.MAX_PLAYED_APPIDS)


class OwnerIn(BaseModel):
    owner_key: str = Field(max_length=64)


class RecIn(OwnerIn):
    filters: FilterIn = Field(default_factory=FilterIn)
    played: list[int] = Field(default_factory=list, max_length=config.MAX_PLAYED_APPIDS)


class FeedbackIn(OwnerIn):
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=config.FEEDBACK_MAX_CHARS)


# ---------------------------------------------------------------- 엔드포인트


@app.get("/api/health")
def health():
    try:
        cat = load_active_catalog()
        catalog = {"snapshot_id": cat.snapshot_id, "games": len(cat.games)}
    except CatalogUnavailable:
        catalog = None
    return {"ok": True, "catalog": catalog, "versions": policy.versions}


@app.get("/api/questionnaire")
def questionnaire():
    return {
        "intro": policy.intro, "versions": policy.versions,
        "questions": [{"question_id": q["question_id"], "text": q["text"], "kind": q["kind"],
                       "options": [{"code": o["code"], "label": o["label"]} for o in q["options"]]}
                      for q in policy.questions],
        "free_texts": [{"free_text_id": f["free_text_id"], "text": f["text"], "max_chars": f["max_chars"]}
                       for f in policy.free_texts],
    }


@app.get("/api/catalog/search")
def catalog_search(q: str = "", limit: int = 20):
    q = q.strip().casefold()[:60]
    try:
        cat = load_active_catalog()
    except CatalogUnavailable:
        return {"status": "catalog_unavailable", "items": []}
    if len(q) < 1:
        return {"status": "ok", "items": []}
    hits = [g for g in cat.games if q in (g["name"] or "").casefold() and content_status(policy, g) is None]
    hits.sort(key=lambda g: (not g["name"].casefold().startswith(q), -g["review_count"]))
    return {"status": "ok", "items": [{"appid": g["appid"], "name": g["name"], "image": g["header_image"]}
                                      for g in hits[: max(1, min(limit, 20))]]}


@app.post("/api/submissions")
def submit(body: SubmitIn):
    con = con_dep()
    try:
        played = service.validate_played(body.played)
        sub_id, owner_key, j = service.create_submission(con, policy, body.answers, body.free_texts,
                                                         body.experiment_consent)
        recs = service.run_recommendation(con, policy, sub_id, j, Filters(**body.filters.model_dump()), played)
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        con.close()
    return {"submission_id": sub_id, "owner_key": owner_key, "card": service.card_payload(policy, j),
            "recommendations": recs, "filters": body.filters.model_dump(), "played": played}


@app.post("/api/submissions/{submission_id}/view")
def view(submission_id: str, body: RecIn):
    """새로고침 복구: 저장 답변으로 결정적 재판정 + 마지막 추천 요청 조건으로 다시 계산."""
    con = con_dep()
    try:
        service.check_owner(con, submission_id, body.owner_key)
        j = service.judgement_for(con, policy, submission_id)
        last = con.execute("SELECT filters_json, played_json FROM rec_requests WHERE submission_id = ? "
                           "ORDER BY request_no DESC LIMIT 1", (submission_id,)).fetchone()
        filters = json.loads(last["filters_json"]) if last else body.filters.model_dump()
        played = json.loads(last["played_json"]) if last else []
        recs = service.run_recommendation(con, policy, submission_id, j, Filters(**filters), played, "view")
    except PermissionError:
        raise HTTPException(404, "not_found")
    finally:
        con.close()
    return {"submission_id": submission_id, "card": service.card_payload(policy, j), "recommendations": recs,
            "filters": filters, "played": played}


@app.post("/api/submissions/{submission_id}/recommendations")
def rerecommend(submission_id: str, body: RecIn):
    con = con_dep()
    try:
        service.check_owner(con, submission_id, body.owner_key)
        played = service.validate_played(body.played)
        j = service.judgement_for(con, policy, submission_id)
        recs = service.run_recommendation(con, policy, submission_id, j,
                                          Filters(**body.filters.model_dump()), played, "filter_change")
    except PermissionError:
        raise HTTPException(404, "not_found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        con.close()
    return {"recommendations": recs, "filters": body.filters.model_dump(), "played": played}


@app.post("/api/submissions/{submission_id}/feedback")
def feedback(submission_id: str, body: FeedbackIn):
    con = con_dep()
    try:
        service.check_owner(con, submission_id, body.owner_key)
        con.execute("INSERT INTO feedback (submission_id, rating, comment, created_at) VALUES (?,?,?,?)",
                    (submission_id, body.rating, body.comment.strip(), service.now_iso()))
    except PermissionError:
        raise HTTPException(404, "not_found")
    finally:
        con.close()
    return {"ok": True}


@app.post("/api/submissions/{submission_id}/delete")
def delete(submission_id: str, body: OwnerIn):
    con = con_dep()
    try:
        service.check_owner(con, submission_id, body.owner_key)
        service.delete_submission(con, submission_id)
    except PermissionError:
        raise HTTPException(404, "not_found")
    finally:
        con.close()
    return {"ok": True}


app.include_router(evaluation.router)
