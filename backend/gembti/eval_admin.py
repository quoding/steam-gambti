"""평가 운영 CLI (오프라인·수동). 실제 참가 데이터만 집계하며 인원·결과를 만들어내지 않는다.

uv run python -m gembti.eval_admin create-codes --phase pilot --n 5
uv run python -m gembti.eval_admin report [--out ../var/reports/eval.md]
uv run python -m gembti.eval_admin tech-check
"""
from __future__ import annotations

import argparse
import json
import secrets
import statistics
from collections import Counter
from datetime import datetime, timedelta

from gembti import db
from gembti.catalog import CatalogUnavailable, load_active_catalog
from gembti.engine import judge
from gembti.policy import get_policy
from gembti.service import KST, now_iso

TARGET_N = 20
WINDOW_DAYS = 28
ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def pct(a, b):
    return "계산 불가" if not b else f"{a}/{b} ({a / b:.0%})"


def create_codes(phase: str, n: int, note: str | None) -> list[str]:
    con = db.connect()
    db.migrate(con)
    p = get_policy()
    codes = []
    for _ in range(n):
        code = "GB" + "".join(secrets.choice(ALPH) for _ in range(6))
        con.execute("INSERT INTO eval_participants VALUES (?,?,?,?,?)",
                    (code, phase, p.versions["experiment_protocol_version"], now_iso(), note))
        codes.append(code)
    con.close()
    return codes


def tech_check(con) -> dict:
    """기술 조건: 중복 추천·필터 위반 0, 저장 객관식 재판정 = 저장 결과(서술 무관)."""
    p = get_policy()
    dup = con.execute("SELECT COUNT(*) FROM (SELECT submission_id, request_no, kind, appid, COUNT(*) c "
                      "FROM recommendations WHERE kind != 'popular_baseline' "
                      "GROUP BY submission_id, request_no, appid HAVING c > 1)").fetchone()[0]
    violations, checked = 0, 0
    try:
        cat = load_active_catalog()
    except CatalogUnavailable:
        cat = None
    reqs = con.execute("SELECT * FROM rec_requests").fetchall()
    for r in reqs:
        if cat is None or r["snapshot_id"] != cat.snapshot_id:
            continue
        f = json.loads(r["filters_json"])
        played = set(json.loads(r["played_json"]))
        for row in con.execute("SELECT appid FROM recommendations WHERE submission_id = ? AND request_no = ?",
                               (r["submission_id"], r["request_no"])):
            g = cat.by_appid.get(row["appid"])
            checked += 1
            if g is None or row["appid"] in played or g[f["os"]] != "true" or (f["korean_only"] and g["korean"] != "true"):
                violations += 1
    mismatch = 0
    subs = con.execute("SELECT s.submission_id, r.type_id, r.session_id FROM submissions s "
                       "JOIN results r USING (submission_id)").fetchall()
    for s in subs:
        ans = {a["question_id"]: a["code"] for a in con.execute(
            "SELECT question_id, code FROM answers WHERE submission_id = ?", (s["submission_id"],))}
        j = judge(p, ans)
        if j.card.type_id != s["type_id"] or j.session_id != s["session_id"]:
            mismatch += 1
    return {"duplicate_recommendations": dup, "filter_violations": violations, "checked_items": checked,
            "rejudge_mismatch": mismatch, "submissions": len(subs),
            "pass": dup == 0 and violations == 0 and mismatch == 0}


def _results(con, where: str, args=()):
    return con.execute(
        "SELECT e.*, r.card_json, r.tie_stats_json, r.type_id, rq.summary_json FROM eval_sessions e "
        "JOIN eval_participants p USING (code) JOIN results r USING (submission_id) "
        "LEFT JOIN rec_requests rq ON rq.submission_id = e.submission_id AND rq.purpose = 'eval' "
        f"WHERE {where} ORDER BY e.started_at", args).fetchall()


def pilot_section(con) -> list[str]:
    p = get_policy()
    rows = _results(con, "p.phase = 'pilot' AND e.completed_at IS NOT NULL")
    n = len(rows)
    out = ["## 개발 파일럿", f"- 완료 인원: {n} (목표 5, 본평가 제외)"]
    types = Counter(r["type_id"] for r in rows)
    out.append("- 유형 분포: " + ", ".join(
        f"{t['type_id']} {pct(types[t['type_id']], n)}" for t in p.types["types"]) +
        f", 아직 발견 중 {pct(types['DISCOVERING'], n)}")
    tops: Counter[str] = Counter()
    social_flip = 0
    tie: dict[str, Counter[str]] = {"tag_representative": Counter(), "group_rank": Counter()}
    used_hash = 0
    strong = weak = final = 0
    q56 = 0
    for r in rows:
        card = json.loads(r["card_json"])
        if card["group_order"]:
            tops[card["group_order"][0]] += 1
        regular_sep = len(card["positive_tagids"]) >= 3 and card["independent_evidence_unmerged"] >= 3
        if regular_sep != (not card["is_discovering"]):
            social_flip += 1
        ts = json.loads(r["tie_stats_json"])
        for st, counter in tie.items():
            for k in ("score_tie_comparisons", "direct_strength", "hash", "collision"):
                counter[k] += ts[st][k]
        used_hash += 1 if ts["used_hash"] else 0
        if r["summary_json"]:
            sm = json.loads(r["summary_json"])
            strong += sm["strong_avoid_excluded"]
            weak += sm["weak_penalty_only"]
            final += sm["candidate_count"]
        a = {x["question_id"]: x["code"] for x in con.execute(
            "SELECT question_id, code FROM answers WHERE submission_id = ?", (r["submission_id"],))}
        pos = lambda c: c in ("A", "B")
        if a.get("Q05") not in (None, "X") and a.get("Q06") not in (None, "X") and pos(a["Q05"]) != pos(a["Q06"]):
            q56 += 1
    out.append("- 1위 그룹 분포: " + ", ".join(f"{g.group_id} {pct(tops[g.group_id], n)}" for g in p.groups))
    out.append(f"- 사회적 근거 중복 영향(G09/G10 각각 셀 때와 정규 카드 자격이 달라진 수): {social_flip}")
    for st, c in tie.items():
        m = c["score_tie_comparisons"]
        out.append(f"- 동점({st}): 점수 동점 비교 {m}, 직접 강도 해소 {pct(c['direct_strength'], m)}, "
                   f"해시 순서 {pct(c['hash'], m)}, 해시 충돌 예외 {c['collision']}")
    out.append(f"- 카드 생성 중 해시를 한 번 이상 쓴 제출: {pct(used_hash, n)}")
    out.append(f"- 추천 후보 감소(합계): 강한 기피 제외 {strong}, 약한 기피 패널티만 {weak}, 최종 후보 {final}")
    out.append(f"- Q05/Q06 한쪽만 긍정한 응답 수: {q56}/{n} (관측값. 이해도 판단은 면담 기록으로 따로 남김)")
    out += awkward_lines(rows)
    return out


def awkward_lines(rows) -> list[str]:
    """발견 중 비율, 고정 목록의 개인화·참고 비율, 어색한 추천 사유."""
    n = len(rows)
    disc = sum(1 for r in rows if not r["regular_card"])
    pers = refs = 0
    for r in rows:
        if r["summary_json"]:
            sm = json.loads(r["summary_json"])
            pers += sm["personalized"]
            refs += sm["reference"]
    out = [f"- ‘아직 발견 중’ 비율: {pct(disc, n)}",
           f"- 고정 목록 추천 구성: 개인화 {pct(pers, pers + refs)}, 참고 {pct(refs, pers + refs)}"]
    try:
        cat = load_active_catalog()
    except CatalogUnavailable:
        cat = None
    items = []
    for r in rows:
        aw = json.loads(r["awkward_json"] or "{}")
        fixed = set(json.loads(r["list_a_json"] if r["fixed_is_a"] else r["list_b_json"]))
        for appid, reason in aw.items():
            name = cat.by_appid[int(appid)]["name"] if cat and int(appid) in cat.by_appid else appid
            items.append(f"  - {r['code']}: {name} ({'고정' if int(appid) in fixed else '인기 기준선'}) — {reason or '사유 없음'}")
    out.append(f"- 어색한 추천 표시: {len(items)}건")
    return out + items


def main_section(con) -> list[str]:
    rows = _results(con, "p.phase = 'main'")
    out = ["## 본평가 (고정 배점 vs 순수 인기 기준선)"]
    if not rows:
        return out + ["- 본평가 참가 기록 없음. 모집 시작 전."]
    start = datetime.fromisoformat(rows[0]["started_at"])
    deadline = start + timedelta(days=WINDOW_DAYS)
    now = datetime.now(KST)
    started = len(rows)
    completed = [r for r in rows if r["completed_at"]]
    valid_all = [r for r in completed if not r["excluded_reason"] and r["regular_card"]
                 and r["fixed_all_personalized"] and r["both_full"]
                 and datetime.fromisoformat(r["started_at"]) <= deadline]
    valid = valid_all[:TARGET_N]
    n = len(valid)
    excl: Counter[str] = Counter()
    for r in completed:
        if r["excluded_reason"]:
            excl[r["excluded_reason"]] += 1
        elif not r["regular_card"]:
            excl["discovering_card"] += 1
        elif not r["fixed_all_personalized"]:
            excl["fixed_has_reference"] += 1
        elif not r["both_full"]:
            excl["list_lt_5"] += 1
    if n >= TARGET_N:
        status = "본평가 마감 (모집 순서상 첫 20명)"
    elif now < deadline:
        status = f"모집 중 ({n}/{TARGET_N}) · 관측 결과만 보고"
    else:
        status = "표본 부족 · 판정 보류"
    out += [f"- 상태: **{status}**", f"- 모집 시작 {start:%Y-%m-%d}, 마감 {deadline:%Y-%m-%d}",
            f"- 모집(시작) {started}, 완료 {len(completed)}, 주분석 자격 {len(valid_all)}, 사용 {n}",
            "- 제외 사유: " + (json.dumps(dict(excl), ensure_ascii=False) if excl else "없음")]

    def side(r, fixed: bool):
        lists = [json.loads(r["list_a_json"]), json.loads(r["list_b_json"])]
        a_is_fixed = bool(r["fixed_is_a"])
        return lists[0] if a_is_fixed == fixed else lists[1]

    if n == 0:
        out.append("- 선호율·관심 평균: 계산 불가 (유효 0명)")
    else:
        wins = ties = 0
        fixed_int, base_int, overlap = [], [], []
        for r in valid:
            pref = r["preference"]
            fixed_label = "A" if r["fixed_is_a"] else "B"
            wins += pref == fixed_label
            ties += pref == "tie"
            rt = json.loads(r["ratings_json"])
            fixed_int.append(sum(rt[str(a)] == "interested" for a in side(r, True)))
            base_int.append(sum(rt[str(a)] == "interested" for a in side(r, False)))
            overlap.append(r["overlap_count"] / 5)
        rate = (wins + 0.5 * ties) / n
        out += [f"- 관측 블라인드 선호율 (고정 승 + 0.5×동률)/n = {rate:.2f} (n={n})",
                f"- ‘관심 있음’ 평균(5개 중): 고정 {statistics.mean(fixed_int):.2f}, 인기 기준선 {statistics.mean(base_int):.2f}",
                f"- 목록 겹침률 평균: {statistics.mean(overlap):.0%}"]
        if n >= TARGET_N:
            ok = rate >= 0.60 and statistics.mean(fixed_int) >= 3 and statistics.mean(fixed_int) >= statistics.mean(base_int)
            eligible_ratio = len(valid_all) / len(completed)
            out.append(f"- 사전 기준(선호 ≥0.60, 관심 ≥3/5·기준선 이상, 자격 비율 ≥70%={eligible_ratio:.0%}): "
                       f"{'충족' if ok and eligible_ratio >= 0.7 else '미충족'} — 통계적 우월성 입증이 아님")
        else:
            out.append("- n<20: 추천 품질 통과·우월성·본평가 완료를 선언하지 않음")
    comp_rate = len(completed) / started
    secs = [r["objective_seconds"] for r in completed if r["objective_seconds"]]
    out.append(f"- 완료율 {comp_rate:.0%}, 객관식 완료 시간 중앙값 "
               f"{statistics.median(secs) / 60:.1f}분" if secs else f"- 완료율 {comp_rate:.0%}, 객관식 시간 기록 없음")
    hits = [r for r in completed if r["hidden_in_catalog"]]
    out.append(f"- 숨긴 좋아하는 게임 Hit@10 {pct(sum(1 for r in hits if r['hit_rank'] and r['hit_rank'] <= 10), len(hits))}, "
               f"Hit@20 {pct(sum(1 for r in hits if r['hit_rank'] and r['hit_rank'] <= 20), len(hits))}, "
               f"카탈로그 밖 제외 {len(completed) - len(hits)} (참고값·게이트 미사용)")
    refc = sum(1 for r in completed if not r["fixed_all_personalized"])
    out.append(f"- 탐색 집계: 참고 추천 포함 제출 {pct(refc, len(completed))}")
    out += awkward_lines(completed)
    return out


def report() -> str:
    con = db.connect()
    db.migrate(con)
    tc = tech_check(con)
    lines = [f"# 겜BTI 평가 보고서 ({now_iso()})", "",
             f"- 기술 검증: {'통과' if tc['pass'] else '실패'} " + json.dumps(tc, ensure_ascii=False),
             "- 기술 검증은 사용자 추천 품질 통과와 별개다.", ""]
    lines += pilot_section(con) + [""] + main_section(con)
    con.close()
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-codes")
    c.add_argument("--phase", choices=["pilot", "main"], required=True)
    c.add_argument("--n", type=int, default=5)
    c.add_argument("--note")
    r = sub.add_parser("report")
    r.add_argument("--out")
    sub.add_parser("tech-check")
    args = ap.parse_args()
    if args.cmd == "create-codes":
        print("\n".join(create_codes(args.phase, args.n, args.note)))
    elif args.cmd == "report":
        text = report()
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
        print(text)
    else:
        con = db.connect()
        db.migrate(con)
        print(json.dumps(tech_check(con), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
