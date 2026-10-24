"""JEV 오프라인 재판정 도구 (기준 문서 13절). 서비스·배포 프로세스와 연결하지 않는 수동 스크립트.

- 서비스 DB는 읽기 전용(mode=ro)으로만 연다. 결과는 var/experiments/jev.db 에만 쓴다.
- 실험 동의(experiment_consent=1) 제출만 내보낸다.
- 조건: D(고정 배점), J-Q(객관식 원문만), J-QF(객관식 + 서술 F01~F03).
- 모델 jev-1.13.0 고정, 20태그 한 요청(문맥 초과 시 tagid 오름차순 10개씩 분할), 누적 예상 $2에서 다음 요청 전 중지.
- 기본은 dry-run(호출 없음). 실제 호출은 --execute 와 TYPESAFE_API_KEY가 모두 있어야 한다.

실행 위치: backend/ 에서
  uv run python ../experiments/jev/jev_tool.py export
  uv run python ../experiments/jev/jev_tool.py plan
  uv run python ../experiments/jev/jev_tool.py run --condition J-Q [--execute]
  uv run python ../experiments/jev/jev_tool.py diff
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from gembti import config  # noqa: E402
from gembti.engine import TagResult, build_card, normalize_answers, score_tags, session_id  # noqa: E402
from gembti.policy import get_policy  # noqa: E402

MODEL = "jev-1.13.0"
PROMPT_VERSION = "jev-rubric-v1.0"
ENDPOINT = os.environ.get("JEV_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
PRICE_PER_MTOK_INPUT = float(os.environ.get("JEV_PRICE_PER_MTOK", "0.042"))  # 문서 작성 시점 단가. 실제 청구는 콘솔 확인
BUDGET_USD = 2.0
EXP_DIR = config.VAR_DIR / "experiments"
EXP_DB = EXP_DIR / "jev.db"
LEVELS = ["강한 기피: 이 태그의 특징을 분명히 피하고 싶어한다",
          "약한 기피: 이 태그의 특징에 다소 부정적이다",
          "중립: 끌림도 거부감도 뚜렷하지 않다",
          "약한 선호: 이 태그의 특징에 다소 끌린다",
          "강한 선호: 이 태그의 특징에 분명히 끌린다"]
EVIDENCE = ["insufficient: 응답에 이 태그를 판단할 근거가 부족하다",
            "conditional: 근거가 상반되거나 조건에 따라 달라진다",
            "sufficient: 응답에 일관된 근거가 있다"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS exports (export_id TEXT PRIMARY KEY, created_at TEXT, policy_versions TEXT, n INTEGER);
CREATE TABLE IF NOT EXISTS inputs (export_id TEXT, submission_id TEXT, session_id TEXT, answers_json TEXT,
  questions_json TEXT, free_texts_json TEXT, PRIMARY KEY (export_id, submission_id));
CREATE TABLE IF NOT EXISTS runs (experiment_run_id TEXT PRIMARY KEY, export_id TEXT, condition TEXT, model_id TEXT,
  prompt_version TEXT, created_at TEXT, executed INTEGER, repeat_index INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS calls (experiment_run_id TEXT, submission_id TEXT, session_id TEXT, condition TEXT,
  chunk INTEGER, input_hash TEXT, request_json TEXT, raw_output TEXT, status TEXT, input_tokens INTEGER,
  output_tokens INTEGER, cost_usd REAL, seconds REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS suggestions (experiment_run_id TEXT, submission_id TEXT, tagid INTEGER, note TEXT);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def exp_con() -> sqlite3.Connection:
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(EXP_DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def service_ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{config.SERVICE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- 내보내기

def export() -> str:
    p = get_policy()
    src = service_ro()
    subs = src.execute("SELECT submission_id, session_id FROM submissions WHERE experiment_consent = 1 "
                       "AND questionnaire_version = ? ORDER BY created_at",
                       (p.versions["questionnaire_version"],)).fetchall()
    eid = "exp-" + time.strftime("%Y%m%d-%H%M%S")
    con = exp_con()
    with con:
        con.execute("INSERT INTO exports VALUES (?,?,?,?)", (eid, now(), json.dumps(p.versions), len(subs)))
        for s in subs:
            ans = {r["question_id"]: r["code"] for r in src.execute(
                "SELECT question_id, code FROM answers WHERE submission_id = ?", (s["submission_id"],))}
            fts = {r["free_text_id"]: r["text"] for r in src.execute(
                "SELECT free_text_id, text FROM free_texts WHERE submission_id = ?", (s["submission_id"],))}
            qtext = {q["question_id"]: {"text": q["text"], "answer": next(o["label"] for o in q["options"]
                                                                          if o["code"] == ans[q["question_id"]])}
                     for q in p.questions}
            con.execute("INSERT INTO inputs VALUES (?,?,?,?,?,?)", (eid, s["submission_id"], s["session_id"],
                        json.dumps(ans), json.dumps(qtext, ensure_ascii=False), json.dumps(fts, ensure_ascii=False)))
    src.close()
    print(f"{eid}: 동의 제출 {len(subs)}건 내보냄 (동의 없는 제출 0건)")
    return eid


# ---------------------------------------------------------------- 요청 구성

def build_state(qtext: dict, fts: dict, condition: str) -> str:
    lines = ["[객관식 질문과 응답]"]
    for qid in sorted(qtext):
        lines.append(f"{qid}. {qtext[qid]['text']}\n응답: {qtext[qid]['answer']}")
    if condition == "J-QF":
        lines.append("[자유 서술]")
        for fid in ("F01", "F02", "F03"):
            lines.append(f"{fid}: {fts.get(fid, '(응답 없음)')}")
    return "\n".join(lines)


def build_questions(tagids: list[int]) -> dict:
    p = get_policy()
    qs = {}
    for t in tagids:
        tag = p.tag_by_id[t]
        name = f"{tag.name_ko}({tag.name_en}, Steam tagid {t})"
        qs[f"score_{t}"] = {"type": "score", "criteria": LEVELS,
                            "instructions": f"이 사람은 Steam 태그 '{name}'의 게임 특징을 얼마나 선호하는가?"}
        qs[f"evidence_{t}"] = {"type": "score", "criteria": EVIDENCE,
                               "instructions": f"응답에 '{name}' 선호를 판단할 근거가 어느 정도 있는가?"}
    return qs


def chunks() -> list[list[int]]:
    tags = sorted(t.tagid for t in get_policy().tags)
    if os.environ.get("JEV_SPLIT") == "10":
        return [tags[:10], tags[10:]]
    return [tags]


def est_tokens(text: str) -> int:
    return int(len(text) * 1.2) + 50  # 보수적 추정(한국어 문자당 ~1토큰 이상)


# ---------------------------------------------------------------- 실행

def call(payload: dict, key: str) -> tuple[dict | None, str, float]:
    import httpx
    header = os.environ.get("JEV_AUTH_HEADER", "Authorization")
    prefix = os.environ.get("JEV_AUTH_PREFIX", "Bearer ")
    t0 = time.time()
    for attempt in range(3):
        try:
            r = httpx.post(ENDPOINT, json=payload, headers={header: prefix + key}, timeout=60)
        except httpx.HTTPError as e:
            err = f"network: {e}"
        else:
            if r.status_code == 200:
                return r.json(), "ok", time.time() - t0
            if r.status_code in (401, 403):
                return None, f"auth_{r.status_code}", time.time() - t0
            err = f"http_{r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                return None, err, time.time() - t0
        time.sleep((5, 15, 45)[attempt])
    return None, err, time.time() - t0


def run(condition: str, execute: bool, export_id: str | None, repeat: int) -> None:
    if condition not in ("J-Q", "J-QF"):
        raise SystemExit("condition은 J-Q 또는 J-QF (D는 diff에서 고정 배점으로 계산)")
    key = os.environ.get("TYPESAFE_API_KEY")
    if execute and not key:
        raise SystemExit("TYPESAFE_API_KEY 없음: 실제 실행하지 않음(준비 상태). --execute 없이 dry-run 가능")
    con = exp_con()
    export_id = export_id or con.execute("SELECT export_id FROM exports ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    inputs = con.execute("SELECT * FROM inputs WHERE export_id = ?", (export_id,)).fetchall()
    spent = con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls WHERE status = 'ok'").fetchone()[0]
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", (run_id, export_id, condition, MODEL, PROMPT_VERSION,
                now(), int(execute), repeat, None))
    skipped_empty = 0
    for inp in inputs:
        fts = json.loads(inp["free_texts_json"])
        if condition == "J-QF" and not any(v.strip() for v in fts.values()):
            skipped_empty += 1
            continue
        state = build_state(json.loads(inp["questions_json"]), fts, condition)
        for ci, tagids in enumerate(chunks()):
            payload = {"state": state, "model": MODEL, "questions": build_questions(tagids)}
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            ihash = hashlib.sha256(body.encode()).hexdigest()
            est = est_tokens(body) * PRICE_PER_MTOK_INPUT / 1e6
            if spent + est > BUDGET_USD:
                print(f"예산 한도 ${BUDGET_USD}: 누적 ${spent:.4f} + 예상 ${est:.4f} → 중지")
                con.commit()
                return
            if not execute:
                con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    run_id, inp["submission_id"], inp["session_id"], condition, ci, ihash, body, None, "dry_run",
                    est_tokens(body), 0, est, 0, now()))
                continue
            out, status, secs = call(payload, key or "")
            usage = (out or {}).get("usage", {})
            cost = usage.get("input_tokens", 0) * PRICE_PER_MTOK_INPUT / 1e6
            spent += cost
            con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                run_id, inp["submission_id"], inp["session_id"], condition, ci, ihash, body,
                json.dumps(out, ensure_ascii=False) if out else None, status, usage.get("input_tokens"),
                usage.get("output_tokens"), cost, secs, now()))
            con.commit()
            if status.startswith("auth_"):
                raise SystemExit(f"{status}: 중지")
    con.commit()
    mode = "실행" if execute else "dry-run(호출 없음)"
    print(f"{run_id} {condition} {mode}: 입력 {len(inputs)}건, 서술 없음 제외 {skipped_empty}건, 누적 비용 ${spent:.4f}")


def plan() -> None:
    con = exp_con()
    row = con.execute("SELECT export_id, n FROM exports ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit("export 먼저 실행")
    inputs = con.execute("SELECT * FROM inputs WHERE export_id = ?", (row["export_id"],)).fetchall()
    tot = 0
    for cond in ("J-Q", "J-QF"):
        toks = sum(est_tokens(json.dumps({"state": build_state(json.loads(i["questions_json"]),
                                                               json.loads(i["free_texts_json"]), cond),
                                          "questions": build_questions(t)}, ensure_ascii=False))
                   for i in inputs for t in chunks())
        tot += toks
        print(f"{cond}: 요청 {len(inputs) * len(chunks())}회, 추정 입력 {toks:,} 토큰, ${toks * PRICE_PER_MTOK_INPUT / 1e6:.4f}")
    print(f"합계(반복 제외) ${tot * PRICE_PER_MTOK_INPUT / 1e6:.4f}; 10세션×5회 반복 검증은 별도 추가; 한도 ${BUDGET_USD}")


# ---------------------------------------------------------------- 후처리·비교

def jev_tags(fixed: dict[int, TagResult], answers_out: dict) -> dict[int, TagResult]:
    """JEV 점수를 같은 상태 규칙으로 변환. 객관식 n≥2 구조 마스크 유지, 근거 상태 마스킹."""
    out = {}
    for t, base in fixed.items():
        s = answers_out.get(f"score_{t}")
        ev = answers_out.get(f"evidence_{t}")
        tr = TagResult(**{**base.__dict__})
        if s is None or base.n < 2:
            tr.state, tr.normalized = "unknown", None
        else:
            norm = Fraction(s["score"]).limit_denominator(10000) - 2
            norm = norm / 2
            probs = (ev or {}).get("probabilities", {})
            evstate = max(probs, key=lambda k: probs[k]) if probs else "2"
            tr.normalized = norm
            if evstate == "0":
                tr.state = "unknown"
            elif evstate == "1":
                tr.state = "conditional"
            elif norm >= Fraction(1, 2):
                tr.state = "positive"
            elif norm <= Fraction(-1, 2):
                tr.state = "avoid"
            else:
                tr.state = "neutral_or_weak"
        out[t] = tr
    return out


def diff() -> None:
    p = get_policy()
    con = exp_con()
    runs = con.execute("SELECT * FROM runs WHERE executed = 1").fetchall()
    if not runs:
        print("실제 실행된 JEV run 없음 → 비교 불가(준비 상태). dry-run 기록은 비교에 쓰지 않음.")
        return
    for run_ in runs:
        rows = con.execute("SELECT submission_id, session_id, raw_output FROM calls WHERE experiment_run_id = ? "
                           "AND status = 'ok'", (run_["experiment_run_id"],)).fetchall()
        by_sub: dict[str, dict] = {}
        for r in rows:
            by_sub.setdefault(r["submission_id"], {"sid": r["session_id"], "answers": {}})
            by_sub[r["submission_id"]]["answers"].update(json.loads(r["raw_output"])["answers"])
        changed = 0
        trans: dict[str, int] = {}
        for sub, d in by_sub.items():
            inp = con.execute("SELECT answers_json FROM inputs WHERE submission_id = ? AND export_id = ?",
                              (sub, run_["export_id"])).fetchone()
            ans = normalize_answers(p, json.loads(inp["answers_json"]))
            sid = session_id(p, ans)  # 조건 간 공유 seed: 객관식에서만 계산
            fixed = score_tags(p, ans)
            jt = jev_tags(fixed, d["answers"])
            c_d, c_j = build_card(p, fixed, sid), build_card(p, jt, sid)
            changed += c_d.type_id != c_j.type_id
            for t in fixed:
                k = f"{fixed[t].state}->{jt[t].state}"
                trans[k] = trans.get(k, 0) + 1
        n = len(by_sub)
        print(f"{run_['experiment_run_id']} {run_['condition']}: n={n}, 유형 변경 {changed}/{n}; 상태 전이 "
              + json.dumps(dict(sorted(trans.items())), ensure_ascii=False))
    print("차이는 개선의 근거가 아니다. D→J-Q(판정 방식)와 J-Q→J-QF(서술 문맥)를 분리해 해석한다.")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export")
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--condition", required=True)
    r.add_argument("--execute", action="store_true")
    r.add_argument("--export-id")
    r.add_argument("--repeat", type=int, default=0)
    sub.add_parser("diff")
    a = ap.parse_args()
    if a.cmd == "export":
        export()
    elif a.cmd == "plan":
        plan()
    elif a.cmd == "run":
        run(a.condition, a.execute, a.export_id, a.repeat)
    else:
        diff()


if __name__ == "__main__":
    main()
