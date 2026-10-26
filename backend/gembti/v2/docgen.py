"""policy/v2/*.json → docs/v2/QUESTIONNAIRE.md (문서와 기계 설정을 같은 원본에서 생성).

사용: uv run python -m gembti.v2.docgen
"""
from __future__ import annotations

from gembti.config import REPO_ROOT
from gembti.v2.policy import get_v2

OUT = REPO_ROOT / "docs" / "v2" / "QUESTIONNAIRE.md"

ROLE_KO = {"direct": "직접", "supplement": "보완", "primary": "기본 직접", "reserve": "예비 직접"}


def render() -> str:
    p = get_v2()
    v = p.versions
    L = ["# 겜BTI v2 문항 은행·배점·분기·카드", "",
         "이 문서는 `policy/v2/*.json`에서 자동 생성된다. 직접 고치지 말고 JSON을 고친 뒤 `python -m gembti.v2.docgen`을 실행한다.", "",
         "버전: " + " · ".join(f"{k} {val}" for k, val in v.items()), "",
         "## 1. 태그 registry (25개, 16계열)", "",
         "| 계열 | 이름 | 사회 | tagid | 한국어 | 영어 |", "|---|---|---|---:|---|---|"]
    for f in p.families:
        for t in f["tags"]:
            L.append(f"| {f['family_id']} | {f['name']} | {'예' if f['social'] else ''} | {t['tagid']} | {t['name_ko']} | {t['name_en']} |")
    L += ["", "tagid·이름은 2026-10-24 GetTagList(ko/en) 원본과 대조(정적 검증 `validate_v2`).", "",
          "## 2. 탐색 12문항 (분기 전용)", "",
          p.explore["note"], "", f"안내: {p.explore['intro']}", "",
          "| 문항 | 질문 | 코드 | 선택지 | 계열 | 태그 힌트 |", "|---|---|---|---|---|---:|"]
    for q in p.explore["questions"]:
        for o in q["options"]:
            L.append(f"| {q['question_id']} | {q['text'] if o['code'] == 'A' else ''} | {o['code']} | {o['label']} | "
                     f"{o['family_id'] or '—'} | {o['tag_hint'] or '—'} |")
    L += ["", "선택 규칙: A~D 중 최대 2개, 또는 N(어느 것도 끌리지 않음)·X(모르겠음) 단독. 계열별 등장 횟수는 각 3회(48칸).", "",
          "## 3. 확인 척도와 상태 규칙", "",
          "| 코드 | 선택지 | 배점 |", "|---|---|---:|"]
    for s in p.confirm["scale"]:
        L.append(f"| {s['code']} | {s['label']} | {'결측' if s['points'] is None else s['points']} |")
    L += ["", "- 정규화 = 합계 / (2 × 유효 개수 n). X는 분모에서 제외. 서로 다른 문항만 근거로 센다(문항당 대상 태그 1개).",
          "- unknown: n<2 → conditional: 양수·음수 모두 → positive: 양수≥2·음수 0·정규화≥0.5 → avoid: 음수≥2·양수 0·정규화≤−0.5 → 그 외 neutral_or_weak.",
          f"- 직접 응답 강도 D = ‘{p.confirm['scale'][0]['label']}’ 수 / n (동점 보조).", "",
          "## 4. 확인 문항 은행 (75개 = 25태그 × 3상황)", "",
          "1·2번은 기본 확인(직접), 3번은 추가 단계용 다른 상황(보완). 각 문항은 대상 태그 하나에만 배점한다.", "",
          "| 문항 | 계열 | 태그 | 역할 | 상황·측정 의도 | 질문 | 근접 태그 구별 |", "|---|---|---|---|---|---|---|"]
    for q in p.confirm["questions"]:
        L.append(f"| {q['question_id']} | {q['family_id']} | {p.tag_name[q['tagid']]}({q['tagid']}) | "
                 f"{ROLE_KO.get(q['role'], q['role'])} | {q.get('situation') or q.get('intent', '')} | {q['text']} | "
                 f"{q['distinguish']} |")
    L += ["", "## 5. 커버리지", "", "| 태그 | 계열 | 기본 2문항 | 보완 | 런타임 근거 수 | 상태 규칙 |", "|---|---|---|---|---|---|"]
    for f in p.families:
        for t in f["tags"]:
            qs = p.confirm_by_tag[t["tagid"]]
            L.append(f"| {t['name_ko']}({t['tagid']}) | {f['family_id']} | {qs[0]}, {qs[1]} | {qs[2]} | "
                     "경로에 들어온 문항의 유효 응답 n(0~3) | 3절 공통 |")
    r = p.routing
    if r.get("algorithm") == "v2.2":
        route_lines = [(f"- 탐색 {r['explore_count']} → 확인 {r['confirm_families']}태그 × 2문항 → 추가 최대 "
                        f"{r['max_total'] - r['base_total']}문항. 노출 {r['base_total']}~{r['max_total']}개."),
                       f"- 상위 계열: {r['family_tie']}", f"- 탐색 슬롯: {r['slot']}", f"- 계열 안의 태그: {r['tag_pick']}",
                       f"- 최초 순서: {r['initial_order']}", f"- 추가 조건: {r['extra_trigger']}",
                       "- 추가 우선순위: " + " / ".join(r["extra_priority"]), f"- 해석: {r['interpretation']}",
                       f"- 종료: {r['stop_rule']}"]
    else:
        route_lines = [
            (f"- 탐색 {r['explore_count']} → 확인 {r['confirm_families']}계열 × {r['questions_per_family']}문항 → 추가 최대 "
             f"{r['extra_sets_max']}세트(세트당 최대 {r['extra_set_size']}문항). 노출 {r['base_total']}~{r['max_total']}개."),
            (f"- 확인 계열: 흥미 점수 상위 {r['interest_slots']}개(흥미 0은 제외) + 아직 고르지 않은 계열에서 결정적 탐색 슬롯 "
             f"{r['explore_slot']}개(흥미 부족분도 같은 방식으로 채움). 최초 4계열에 사회 계열(G09·G10) 최대 "
             f"{r['max_social_in_initial']}개."),
            f"- 동점: {r['tie']}", "- 계열 안의 태그: 탐색 태그 힌트 수 → 해시.",
            "- 추가 우선순위: " + " / ".join(r["extra_priority"]), f"- 종료: {r['stop_rule']}"]
    L += ["", "## 6. 분기", "", *route_lines,
          "- 흥미 점수: 탐색 문항마다 선택한 계열 +1(문항당 계열 최대 1회). 선택하지 않은 계열에 음수 없음.",
          "- 뒤로 가서 탐색 답을 바꾸면 경로를 다시 계산하고, 경로에서 빠진 응답은 판정·canonical에서 제외한다.",
          "- 라이브러리·필터·서술은 분기 입력이 아니다.", "",
          "## 7. 카드 16개", "", "| 계열 | 유형 ID | 이름 | 한 줄 설명 |", "|---|---|---|---|"]
    for c in p.cards["cards"]:
        L.append(f"| {c['family_id']} | {c['type_id']} | {c['name']} | {c['tagline']} |")
    d = p.cards["discovering"]
    L += [f"| — | {d['type_id']} | {d['name']} | {d['tagline']} |", "", p.cards["rule"],
          (f"정규 카드: positive 태그 ≥ {p.cards['min_positive_tags']} 그리고 독립 계열 ≥ {p.cards['min_independent_families']}"
           "(G09·G10은 합쳐 1). 계열 점수 = 그 계열 positive 태그 정규화 최댓값 1회 → D → 해시."), "",
          "## 8. canonical·재현성", "",
          ("- session_id_v2 = SHA-256(`gembti-session-v2` + 판정 버전(v2.2부터 bank·layout 버전 포함) + 경로 안 노출 문항을 ID 순으로 `ID=코드`). "
           "탐색 복수 선택은 코드 정렬 후 `+`로 연결. 미노출 문항은 줄이 없고 X는 `=X`로 구분."),
          "- 자유 서술·라이브러리·필터·시각·제출 ID는 넣지 않는다. 추천 재현 키는 별도(추천 버전·스냅샷·필터·라이브러리 프로필 해시·보유 목록 해시).", ""]
    return "\n".join(L)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
