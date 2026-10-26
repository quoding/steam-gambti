"""Steam 키 등록 후 실제 계정 점검 (운영자 수동 실행). 운영 API를 실제 화면과 같은 순서로 호출한다.

연결 → 보유 목록·플레이 시간 → 분석 제외 → 프로필 재계산 → 질문 제출·보유 제외 확인 → 보유 제외 끄기 → 연결 해제·삭제 확인.
SteamID/프로필 주소는 숨김 입력으로 받는다(셸 기록·로그에 남지 않음). 출력은 개수·상태만.
점검이 만든 v2 세션·제출은 마지막에 그 키로만 정확히 지운다(다른 사용자 데이터는 건드리지 않음).

사용(backend/): GEMBTI_VAR_DIR=../var uv run python -m gembti.v2.library_check [--api http://127.0.0.1:8790]
"""
from __future__ import annotations

import argparse
import getpass
import time

import httpx

from gembti import db


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8790")
    args = ap.parse_args()
    ident = getpass.getpass("SteamID64 또는 steamcommunity.com 프로필 주소(입력은 화면에 보이지 않음): ").strip()
    c = httpx.Client(base_url=f"{args.api}/api/v2", timeout=60)
    s = c.post("/sessions", json={}).json()
    s = {"session_key": s["session_key"], "owner_key": s["owner_key"]}
    ok = []

    def step(name: str, cond: bool, detail: str = "") -> None:
        ok.append(cond)
        print(f"[{'OK' if cond else '확인 필요'}] {name} {detail}")

    def answers() -> dict:
        ans: dict = {}
        for _ in range(40):
            rt = c.post("/route", json={**s, "answers": ans}).json()
            if rt["done"]:
                break
            q = rt["next"]
            ans[q["question_id"]] = ["A"] if q["stage"] == "explore" else "B"
        return ans

    ans = answers()
    sub0 = c.post("/submit", json={**s, "answers": ans}).json()  # 연결 전 기준 유형
    card_key = lambda cd: (cd["type_id"], [t["tagid"] for t in cd["main_tags"]], [t["tagid"] for t in cd["positive_tags"]])
    r = c.post("/library/link", json={**s, "input": ident})
    step("연결 요청", r.status_code == 200, f"HTTP {r.status_code}")
    st: dict = {}
    for _ in range(90):
        st = c.post("/library/status", json=s).json()
        if st.get("status") != "loading":
            break
        time.sleep(2)
    step("불러오기", st.get("status") == "ok", f"상태 {st.get('status')} {st.get('error_code') or ''}")
    if st.get("status") == "ok":
        games = st.get("games", [])
        played = [g for g in games if (g.get("minutes") or 0) > 0]
        unknown = [g for g in games if g.get("minutes") is None]
        prof = st.get("profile") or {}
        hours = sum(g.get("minutes") or 0 for g in games) / 60
        print(f"  누적 플레이 {hours:,.0f}시간(상위 표시 목록 합)")
        print(f"  보유 {st.get('owned_count')}개 · 플레이 기록 있음 {len(played)} · 시간 정보 없음 {len(unknown)} · "
              f"메타 {st.get('meta_status')}")
        print(f"  프로필: 반영 {prof.get('enabled')} · 분석 {prof.get('analyzed_count')} · 태그 확보 {prof.get('tagged_count')}"
              f" · 태그 가중치 비율 {prof.get('tagged_weight_share')} · 사유 {prof.get('reason')}")
        step("플레이 시간순 정렬", [g.get("minutes") or -1 for g in games] ==
             sorted([g.get("minutes") or -1 for g in games], reverse=True))
        if played:
            top = played[0]["appid"]
            st2 = c.post("/library/settings", json={**s, "analysis_excluded": [top]}).json()
            ex = [g for g in st2.get("games", []) if g["appid"] == top]
            step("분석 제외 반영(보유 사실은 유지)", bool(ex) and ex[0]["analysis_excluded"] and not ex[0]["analyzed"])
            c.post("/library/settings", json={**s, "analysis_excluded": []})
    sub = c.post("/submit", json={**s, "answers": ans}).json()
    recs = sub["recommendations"]
    if recs.get("status") == "ok":
        owned = {g["appid"] for g in st.get("games", [])} if st.get("status") == "ok" else set()
        rec_ids = {i["appid"] for i in recs["items"]}
        lib = recs.get("library", {})
        print(f"  추천 {len(rec_ids)}개 · 라이브러리 반영 {lib.get('used')} ({lib.get('reason')}) · "
              f"보유 제외 {recs['counts']['owned_excluded']}개")
        step("보유 게임이 추천에 없음(전체 보유 목록 기준)", not (owned & rec_ids) or not owned)
        c.post("/library/settings", json={**s, "exclude_owned": False})
        r2 = c.post(f"/submissions/{sub['submission_id']}/recommendations", json={**s, "filters": {}}).json()
        step("보유 제외 끄기 반영", r2["recommendations"]["counts"]["owned_excluded"] == 0)
        step("연결 전후 유형·근거 태그 동일", card_key(sub0["card"]) == card_key(sub["card"]),
             f"{sub0['card']['type_id']} → {sub['card']['type_id']}")
        step("플레이 기록이 추천 점수에 반영", bool(lib.get("used")) == bool((st.get("profile") or {}).get("enabled")),
             f"반영 {lib.get('used')}")
        if lib.get("used"):
            base = [i["appid"] for i in sub0["recommendations"]["items"]]
            print(f"  연결 전 추천과 겹침 {len(set(base) & rec_ids)}/{len(rec_ids)}")
        c.post("/library/settings", json={**s, "use_for_reco": False})
        r3 = c.post(f"/submissions/{sub['submission_id']}/recommendations", json={**s, "filters": {}}).json()
        step("추천 반영 끄기", not r3["recommendations"].get("library", {}).get("used"))
    else:
        print(f"  추천 상태: {recs.get('status')}")
    c.post("/library/unlink", json=s)
    con = db.connect()
    left = sum(con.execute(f"SELECT COUNT(*) FROM {t} WHERE session_key = ?", (s["session_key"],)).fetchone()[0]
               for t in ("v2_library_links", "v2_library_games"))
    step("연결 해제 후 연결·목록 삭제", left == 0)
    # 이 점검이 만든 세션·제출만 정확히 삭제
    subs = [r[0] for r in con.execute("SELECT submission_id FROM v2_submissions WHERE session_key = ?", (s["session_key"],))]
    for t in ("v2_recommendations", "v2_rec_requests", "v2_free_texts"):
        con.executemany(f"DELETE FROM {t} WHERE submission_id = ?", [(x,) for x in subs])
    con.execute("DELETE FROM v2_submissions WHERE session_key = ?", (s["session_key"],))
    con.execute("DELETE FROM v2_sessions WHERE session_key = ?", (s["session_key"],))
    con.close()
    print(f"\n결과: {sum(ok)}/{len(ok)} 통과 (점검용 세션·제출 삭제 완료)")


if __name__ == "__main__":
    main()
