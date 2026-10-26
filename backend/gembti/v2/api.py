"""v2 API (/api/v2). v1 경로·테이블과 분리. 기능 플래그 GEMBTI_V2_ENABLED=0이면 등록하지 않는다."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from gembti import config, db
from gembti.policy import get_policy
from gembti.service import KST, now_iso
from gembti.v2 import library_job, store
from gembti.v2.catalog_v2 import CatalogV2Unavailable, load_active
from gembti.v2.engine import judge_v2, route
from gembti.v2.policy import BANKS, get_v2
from gembti.v2.recommend import POSITIVE_PRESETS, REVIEW_PRESETS, FiltersV2, positive_ratio, recommend_v2

router = APIRouter(prefix="/api/v2")
P = get_v2()
V1 = get_policy()
REVIEW_KO = {9: "압도적으로 긍정적", 8: "매우 긍정적", 7: "긍정적", 6: "대체로 긍정적", 5: "복합적",
             4: "대체로 부정적", 3: "부정적", 2: "매우 부정적", 1: "압도적으로 부정적"}
LINK_PER_MINUTE = 5
_link_hits: dict[str, deque] = defaultdict(deque)


class Owner(BaseModel):
    session_key: str = Field(max_length=64)
    owner_key: str = Field(max_length=64)


class RouteIn(Owner):
    answers: dict[str, str | list[str]] = Field(default_factory=dict)
    questionnaire_version: str | None = Field(default=None, max_length=16)  # 응시 시작 때 받은 값을 그대로


class FilterIn(BaseModel):
    korean_only: bool = False
    os: Literal["windows", "mac", "linux"] = "windows"
    min_positive: int = 75
    min_reviews: int = 500


class SubmitIn(RouteIn):
    free_texts: dict[str, str] = Field(default_factory=dict)
    experiment_consent: bool = False
    filters: FilterIn = Field(default_factory=FilterIn)


class RecIn(Owner):
    filters: FilterIn = Field(default_factory=FilterIn)


class LinkIn(Owner):
    input: str = Field(max_length=200)


class LibSettingsIn(Owner):
    use_for_reco: bool | None = None
    exclude_owned: bool | None = None
    analysis_excluded: list[int] | None = Field(default=None, max_length=200)


def _con_owner(o: Owner):
    con = db.connect()
    try:
        store.check_session(con, o.session_key, o.owner_key)
    except PermissionError:
        con.close()
        raise HTTPException(404, "not_found")
    return con


def _series_of() -> dict[int, str]:
    out: dict[int, str] = {}
    from gembti.catalog import CatalogUnavailable, load_active_catalog
    try:
        out.update({g["appid"]: g["series_id"] for g in load_active_catalog().games})
    except CatalogUnavailable:
        pass  # v1 카탈로그가 없으면 v2 매핑만 사용
    try:
        out.update({g["appid"]: g["series_id"] for g in load_active().games})
    except CatalogV2Unavailable:
        pass
    return out


# ---------------------------------------------------------------- 질문


def _policy_for(con, body: RouteIn, fresh: bool):
    if body.questionnaire_version and body.questionnaire_version not in BANKS:
        raise HTTPException(422, "unknown_questionnaire_version")
    return get_v2(store.attempt_version(con, body.session_key, body.questionnaire_version, fresh,
                                        P.versions["questionnaire_version"]))


def question_payload(p, qid: str) -> dict:
    if qid.startswith("E"):
        q = p.explore_by_id[qid]
        return {"question_id": qid, "stage": "explore", "text": q["text"], "max_select": q["max_select"],
                "exclusive_codes": q["exclusive_codes"],
                "options": [{"code": o["code"], "label": o["label"]} for o in q["options"]]}
    q = p.confirm_by_id[qid]
    return {"question_id": qid, "stage": "confirm", "text": q["text"], "context": p.confirm.get("context"),
            "options": [{"code": s["code"], "label": s["label"]} for s in p.confirm["scale"]]}


@router.post("/sessions")
def new_session():
    con = db.connect()
    try:
        key, owner = store.new_session(con, P.versions["questionnaire_version"])
    finally:
        con.close()
    return {"session_key": key, "owner_key": owner, "versions": P.versions}


@router.get("/questionnaire")
def questionnaire(version: str | None = None):
    if version is not None and version not in BANKS:
        raise HTTPException(404, "not_found")
    p = get_v2(version)
    return {"versions": p.versions, "intro": p.explore["intro"], "base_total": p.routing["base_total"],
            "max_total": p.routing["max_total"],
            "free_texts": [{"free_text_id": f["free_text_id"], "text": f["text"], "max_chars": f["max_chars"]}
                           for f in V1.free_texts],
            "filters": {"min_positive": list(POSITIVE_PRESETS), "min_reviews": list(REVIEW_PRESETS)}}


@router.post("/route")
def next_question(body: RouteIn):
    con = _con_owner(body)
    try:
        p = _policy_for(con, body, fresh=not body.answers)
    finally:
        con.close()
    try:
        r = route(p, body.answers)
    except ValueError as e:
        raise HTTPException(422, str(e))
    kept = [q for q in r.path if q in body.answers]
    return {"questionnaire_version": p.versions["questionnaire_version"],
            "done": r.done, "next": question_payload(p, r.next_question) if r.next_question else None,
            "answered": len(kept), "path_length": len(r.path), "max_total": p.routing["max_total"],
            "path": r.path, "dropped": sorted(k for k in body.answers if k not in r.path), "stage": r.stage}


# ---------------------------------------------------------------- 결과·추천


def _lib_state(con, key: str) -> dict:
    lv = store.library_view(con, key)
    if lv is None:
        return {"linked": False, "used": False, "reason": "연결하지 않음", "owned": set(), "vec": None,
                "profile_hash": None, "exclude_owned": False}
    prof = lv["profile"] or {}
    used = lv["status"] == "ok" and lv["use_for_reco"] and prof.get("enabled", False)
    reason = None if used else (
        "라이브러리 연결 실패" if lv["status"] == "error" else
        "추천 반영 꺼짐" if not lv["use_for_reco"] else prof.get("reason") or "프로필 준비 중")
    owned = {g["appid"] for g in lv["games"]} if lv["status"] == "ok" else set()
    return {"linked": lv["status"] == "ok", "used": bool(used), "reason": reason, "owned": owned,
            "vec": prof.get("family_vector") if used else None, "profile_hash": prof.get("profile_hash") or lv["profile_hash"],
            "exclude_owned": bool(lv["exclude_owned"]) and lv["status"] == "ok",
            "analysis_excluded": sorted(g["appid"] for g in lv["games"] if g["analysis_excluded"])}


def card_payload(p, j) -> dict:
    c = j.card
    def tag(t):
        return {"tagid": t, "name": p.tag_name[t], "family": p.family_by_id[p.family_of[t]]["name"]}
    evidence = []
    for t in c.main_tagids:
        st = j.tags[t]
        evidence.append({**tag(t), "answers": [
            {"question_id": q, "text": p.confirm_by_id[q].get("situation") or p.confirm_by_id[q]["text"],
             "answer": next(s["label"] for s in p.confirm["scale"] if s["code"] == str(j.answers.get(q)).upper())}
            for q in st.questions if q in j.answers]})
    return {"type_id": c.type_id, "name": c.name, "tagline": c.tagline, "is_discovering": c.is_discovering,
            "main_tags": [tag(t) for t in c.main_tagids], "extra_tags": [tag(t) for t in c.extra_tagids],
            "positive_tags": [tag(t) for t in c.positive_tagids], "evidence": evidence,
            "empty_text": p.cards["discovering"]["empty_text"] if not c.positive_tagids else None,
            "question_count": len(j.route.path),
            # 결과에서 '이번에 확인한 영역'과 '아직 확인하지 않은 영역'을 구별해 보여준다(관심 높음으로 단정하지 않음)
            "checked_families": [p.family_by_id[f]["name"] for f in j.route.chosen_tags],
            "unchecked_families": [f["name"] for f in p.families if f["family_id"] not in j.route.chosen_tags]}


def _game(cat, appid: int, it: dict) -> dict:
    g = cat.by_appid[appid]
    ratio = positive_ratio(g)
    names = [P.tag_name.get(t["tagid"]) or str(t["tagid"]) for t in g["tags"][:6]]
    q = [P.tag_name[t] for t in it["qualified_tags"]]
    if it["kind"] == "personalized":
        reason = f"답변에서 끌린다고 확인한 {'·'.join(q)} 태그가 이 게임의 주요 태그예요."
    else:
        m = [P.tag_name[t] for t in it["matched_tags"]]
        reason = (f"{'·'.join(m)} 태그가 겹쳐요. " if m else "") + "판정 근거가 적어 관심 확인용으로 보여드려요."
    return {"appid": appid, "name": g["name"], "image": g["header_image"], "store_url": g["store_url"],
            "short_description": g["short_description"] or None,
            "description_is_korean": g["short_description_lang"] == "ko", "korean": g["korean"],
            "tags": names, "kind": it["kind"], "reason": reason, "library_note": it["library_note"],
            "review": {"label": REVIEW_KO.get(g["review_score"]) if g["review_score"] else None,
                       "positive_pct": round(ratio * 100) if ratio is not None else None,
                       "total_reviews": g["total_reviews"], "source": "Steam 구매 기준·전체 언어",
                       "free_game_note": bool(g.get("is_free"))}}


def _recommend(con, sub_id: str, key: str, j, f: FilterIn) -> dict:
    try:
        cat = load_active()
    except CatalogV2Unavailable:
        return {"status": "catalog_unavailable"}
    lib = _lib_state(con, key)
    filters = FiltersV2(f.korean_only, f.os, f.min_positive, f.min_reviews, lib["exclude_owned"])
    r = recommend_v2(P, V1, j.tags, cat.games, filters, lib["owned"], lib["vec"], cat.idf)
    fj = json.dumps(f.model_dump() | {"exclude_owned": lib["exclude_owned"]}, sort_keys=True)
    owned_h = hashlib.sha256(json.dumps(sorted(lib["owned"])).encode()).hexdigest()[:16] if lib["owned"] else None
    lib_rec = {"linked": lib["linked"], "used": lib["used"], "reason": lib["reason"],
               "profile_hash": lib["profile_hash"], "owned_hash": owned_h,
               "analysis_excluded": lib.get("analysis_excluded", [])}
    repro = hashlib.sha256("|".join([j.session_id, P.versions["recommendation_version"], cat.snapshot_id, fj,
                                     json.dumps(lib_rec, sort_keys=True)]).encode()).hexdigest()
    n = con.execute("SELECT COALESCE(MAX(request_no),0)+1 FROM v2_rec_requests WHERE submission_id = ?",
                    (sub_id,)).fetchone()[0]
    with db.tx(con):
        con.execute("INSERT INTO v2_rec_requests VALUES (?,?,?,?,?,?,?,?,?)",
                    (sub_id, n, fj, json.dumps(lib_rec), cat.snapshot_id, P.versions["recommendation_version"], repro,
                     json.dumps(r["counts"]), now_iso()))
        con.executemany("INSERT INTO v2_recommendations VALUES (?,?,?,?,?,?)",
                        [(sub_id, n, it["rank"], it["appid"], it["kind"], json.dumps(it)) for it in r["items"]])
    return {"status": "ok", "snapshot_id": cat.snapshot_id, "coverage_gate": cat.coverage_gate,
            "items": [_game(cat, it["appid"], it) for it in r["items"]], "counts": r["counts"],
            "library": {k: v for k, v in lib_rec.items() if k in ("linked", "used", "reason")},
            "filters": f.model_dump(), "repro_key": repro[:16]}


@router.post("/submit")
def submit(body: SubmitIn):
    con = _con_owner(body)
    try:
        p = _policy_for(con, body, fresh=False)
        try:
            j = judge_v2(p, body.answers, require_done=True)
        except ValueError as e:
            raise HTTPException(422, str(e))
        fts = {}
        for k, v in body.free_texts.items():
            if k not in {f["free_text_id"] for f in V1.free_texts} or len(v) > config.FREE_TEXT_MAX_CHARS:
                raise HTTPException(422, "invalid_free_text")
            if v.strip():
                fts[k] = v.strip()
        sub_id, owner = secrets.token_hex(16), body.owner_key
        exp = (datetime.now(KST) + timedelta(days=config.FREE_TEXT_RETENTION_DAYS)).isoformat(timespec="seconds")
        with db.tx(con):
            store.purge_v2(con)
            route_log = {k: getattr(j.route, k) for k in ("initial_families", "interest_families",
                                                          "explore_slot_family", "chosen_tags", "extra_sets")}
            route_log["dropped"] = j.dropped  # 경로 변경으로 빠진 문항 ID만(응답 원문은 저장하지 않음)
            con.execute("INSERT INTO v2_submissions (submission_id, session_key, session_id_v2, owner_key_hash, "
                        "versions_json, answers_json, path_json, dropped_json, card_json, tags_json, "
                        "experiment_consent, created_at, route_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                sub_id, body.session_key, j.session_id,
                hashlib.sha256(("gembti-v2-owner|" + owner).encode()).hexdigest(), json.dumps(p.versions),
                json.dumps(j.answers, sort_keys=True), json.dumps(j.route.path), json.dumps(j.dropped),
                json.dumps(j.card.as_dict()), json.dumps({str(t): s.__dict__ for t, s in j.tags.items()}),
                int(body.experiment_consent), now_iso(), json.dumps(route_log, sort_keys=True)))
            con.executemany("INSERT INTO v2_free_texts VALUES (?,?,?,?)", [(sub_id, k, v, exp) for k, v in fts.items()])
        recs = _recommend(con, sub_id, body.session_key, j, body.filters)
    finally:
        con.close()
    return {"submission_id": sub_id, "card": card_payload(p, j), "recommendations": recs}


def _load_sub(con, sub_id: str, o: Owner):
    row = con.execute("SELECT * FROM v2_submissions WHERE submission_id = ? AND session_key = ?",
                      (sub_id, o.session_key)).fetchone()
    if row is None:
        raise HTTPException(404, "not_found")
    versions = json.loads(row["versions_json"])
    qv = versions.get("questionnaire_version")
    if qv not in BANKS or get_v2(qv).versions != versions:
        raise HTTPException(409, "submission_version_mismatch")  # 과거 제출을 새 정책으로 묵시 재판정하지 않음
    p = get_v2(qv)  # 제출이 쓴 은행으로만 다시 판정
    return row, p, judge_v2(p, json.loads(row["answers_json"]))


@router.post("/submissions/{sub_id}/recommendations")
def rerecommend(sub_id: str, body: RecIn):
    con = _con_owner(body)
    try:
        _, _, j = _load_sub(con, sub_id, body)
        try:
            recs = _recommend(con, sub_id, body.session_key, j, body.filters)
        except ValueError as e:
            raise HTTPException(422, str(e))
    finally:
        con.close()
    return {"recommendations": recs}


@router.post("/submissions/{sub_id}/view")
def view(sub_id: str, body: RecIn):
    con = _con_owner(body)
    try:
        _, p, j = _load_sub(con, sub_id, body)
        last = con.execute("SELECT filters_json FROM v2_rec_requests WHERE submission_id = ? ORDER BY request_no DESC "
                           "LIMIT 1", (sub_id,)).fetchone()
        f = FilterIn(**{k: v for k, v in json.loads(last[0]).items() if k != "exclude_owned"}) if last else body.filters
        recs = _recommend(con, sub_id, body.session_key, j, f)
    finally:
        con.close()
    return {"submission_id": sub_id, "card": card_payload(p, j), "recommendations": recs}


# ---------------------------------------------------------------- 라이브러리


def _limit(key: str) -> None:
    now = time.monotonic()
    q = _link_hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= LINK_PER_MINUTE:
        raise HTTPException(429, "rate_limited")
    q.append(now)


@router.post("/library/link")
def link(body: LinkIn, request: Request):
    from gembti.app import client_ip
    _limit("s:" + body.session_key)
    _limit("ip:" + client_ip(request))
    from gembti.v2.steam_library import LibraryError, parse_identity
    try:
        parse_identity(body.input)
    except LibraryError as e:
        raise HTTPException(422, e.code)
    con = _con_owner(body)
    con.close()
    library_job.start_import(body.session_key, body.input, P.family_of, _series_of())
    return {"status": "started"}


def _lib_payload(con, key: str) -> dict:
    lv = store.library_view(con, key)
    running = library_job.is_running(key)
    if lv is None:
        return {"status": "loading" if running else "none"}
    out = {k: lv[k] for k in ("status", "error_code", "fetched_at", "expires_at", "use_for_reco", "exclude_owned",
                              "meta_status")}
    out["status"] = "loading" if running else lv["status"]
    prof = lv["profile"] or {}
    tagged = {r["appid"]: r for r in prof.get("analyzed", [])}
    out["profile"] = {k: prof.get(k) for k in ("enabled", "reason", "analyzed_count", "tagged_count",
                                               "tagged_weight_share")}
    out["owned_count"] = len(lv["games"])
    out["games"] = [{"appid": g["appid"], "name": g["name"], "minutes": g["minutes"],
                     "analysis_excluded": bool(g["analysis_excluded"]),
                     "analyzed": g["appid"] in tagged, "tagged": tagged.get(g["appid"], {}).get("tagged")}
                    for g in lv["games"][:60]]
    return out


@router.post("/library/status")
def lib_status(body: Owner):
    con = _con_owner(body)
    try:
        return _lib_payload(con, body.session_key)
    finally:
        con.close()


@router.post("/library/settings")
def lib_settings(body: LibSettingsIn):
    con = _con_owner(body)
    try:
        if store.library_view(con, body.session_key) is None:
            raise HTTPException(404, "not_linked")
        if body.use_for_reco is not None:
            con.execute("UPDATE v2_library_links SET use_for_reco = ? WHERE session_key = ?",
                        (int(body.use_for_reco), body.session_key))
        if body.exclude_owned is not None:
            con.execute("UPDATE v2_library_links SET exclude_owned = ? WHERE session_key = ?",
                        (int(body.exclude_owned), body.session_key))
        if body.analysis_excluded is not None:
            con.execute("UPDATE v2_library_games SET analysis_excluded = 0 WHERE session_key = ?", (body.session_key,))
            con.executemany("UPDATE v2_library_games SET analysis_excluded = 1 WHERE session_key = ? AND appid = ?",
                            [(body.session_key, a) for a in body.analysis_excluded])
            # 새로 분석 대상에 든 게임의 태그가 캐시에 없으면 '다시 불러오기'에서만 받는다(설정 변경 시 외부 호출 없음)
            library_job.refresh_profile(con, body.session_key, P.family_of, _series_of())
        return _lib_payload(con, body.session_key)
    finally:
        con.close()


@router.post("/library/unlink")
def lib_unlink(body: Owner):
    con = _con_owner(body)
    try:
        store.unlink(con, body.session_key)
    finally:
        con.close()
    return {"ok": True}
