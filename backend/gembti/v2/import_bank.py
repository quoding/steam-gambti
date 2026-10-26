"""질문지 문서(GEMBTI_QUESTION_BANK_v2.2.md)의 표를 그대로 기계 설정으로 옮긴다 (문구 요약·수정 없음).

사용(backend/): uv run python -m gembti.v2.import_bank ../GEMBTI_QUESTION_BANK_v2.2.md ../policy/v2/banks/v2.2
      uv run python -m gembti.v2.import_bank ../GEMBTI_QUESTION_BANK_v2.3.md ../policy/v2/banks/v2.3  (형식이 달라 parse_v23)
출력: explore.json, confirm.json. 버전·분기 설정(versions.json, routing.json)은 손으로 관리한다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BANK_VERSION = "2026-10-26-r1"
QUESTIONNAIRE_VERSION = "v2.2"
ROLE = {"기본 직접 근거": "primary", "예비 직접 근거": "reserve"}
ROLE_V23 = {"기본 확인": "primary", "예비 확인": "reserve"}
# 의미가 바뀌지 않는 명백한 띄어쓰기·오탈자 정정(2026-10-26 사용자 승인). 원문 → 정정. 문서 원본은 그대로 둔다.
ERRATA = {"질문은20~24개이며": "질문은 20~24개이며"}


def _fix(text: str) -> str:
    for a, b in ERRATA.items():
        text = text.replace(a, b)
    return text


def _quoted(line: str) -> str:
    m = re.search(r"“(.+)”", line)
    if not m:
        raise ValueError(f"따옴표 문장 없음: {line}")
    return m.group(1)


def _section(md: str, start: str, end: str) -> str:
    a = md.index(start)
    return md[a:md.index(end, a)]


def _rows(block: str) -> list[list[str]]:
    out = []
    for line in block.splitlines():
        if line.startswith("|") and not re.match(r"^\|[-|: ]+\|$", line):
            out.append([c.strip() for c in line.strip().strip("|").split("|")])
    return out[1:] if out else out  # 머리글 제외


def parse(md: str) -> tuple[dict, dict]:
    intro_block = _section(md, "### 3.1 시작 안내", "### 3.2")
    intro = [_fix(_quoted(x)) for x in intro_block.splitlines() if x.startswith("“")]
    nx_block = _section(md, "### 3.2 탐색 공통 선택 방법", "### 3.3")
    n_label = re.search(r"공통 N: “(.+?)”", nx_block).group(1)  # type: ignore[union-attr]
    x_label = re.search(r"공통 X: “(.+?)”", nx_block).group(1)  # type: ignore[union-attr]
    scale_block = _section(md, "### 3.3 확인 공통 선택지", "## 4.")
    scale = []
    for code, label, pts in _rows(scale_block.split("매 질문에")[0]):
        scale.append({"code": code, "label": label,
                      "points": None if pts == "미관측" else int(pts.replace("+", "").replace("−", "-"))})
    context = re.search(r"공통 중립 맥락: “(.+?)”", scale_block).group(1)  # type: ignore[union-attr]

    explore_qs = []
    ex_block = _section(md, "## 5. 탐색12문항", "### 5.13")
    for m in re.finditer(r"^### (E\d\d)\. (.+)$", ex_block, re.MULTILINE):
        qid, text = m.group(1), m.group(2).strip()
        body = ex_block[m.end():].split("\n### ")[0]
        opts = [{"code": c, "label": _fix(lab), "family_id": f, "tag_hint": int(t)} for c, lab, f, t in _rows(body)]
        opts += [{"code": "N", "label": n_label, "family_id": None, "tag_hint": None},
                 {"code": "X", "label": x_label, "family_id": None, "tag_hint": None}]
        explore_qs.append({"question_id": qid, "text": _fix(text), "max_select": 2, "exclusive_codes": ["N", "X"],
                           "options": opts})

    confirm_qs = []
    c_block = _section(md, "## 6. 확인75문항", "## 7.")
    for m in re.finditer(r"^### (\d+) · (.+?) \((.+?)\) · (G\d\d)$", c_block, re.MULTILINE):
        tagid, fid = int(m.group(1)), m.group(4)
        body = c_block[m.end():].split("\n### ")[0]
        intent = re.search(r"\*\*측정 의도:\*\* (.+)", body).group(1).strip()  # type: ignore[union-attr]
        dist = re.search(r"\*\*가까운 취향과 구별:\*\* (.+)", body).group(1).strip()  # type: ignore[union-attr]
        for qid, text, role in _rows(body):
            confirm_qs.append({"question_id": qid, "tagid": tagid, "family_id": fid, "role": ROLE[role],
                               "text": _fix(text), "intent": intent, "distinguish": dist})
    explore = {"questionnaire_version": QUESTIONNAIRE_VERSION, "bank_version": BANK_VERSION, "stage": "explore",
               "source": "GEMBTI_QUESTION_BANK_v2.2.md §3.1·3.2·5", "errata": ERRATA, "intro": "\n\n".join(intro),
               "note": "분기 선택 전용. 선택 하나는 연결 계열 탐색 +1, 연결 tagid 선택 +1. N/X·비선택은 점수 없음.",
               "questions": explore_qs}
    confirm = {"questionnaire_version": QUESTIONNAIRE_VERSION, "bank_version": BANK_VERSION, "stage": "confirm",
               "source": "GEMBTI_QUESTION_BANK_v2.2.md §3.3·6", "context": context, "scale": scale,
               "note": "각 응답은 대상 tagid 하나에만 기여. _1·_2 기본 직접 근거, _3 예비 직접 근거.",
               "questions": confirm_qs}
    return explore, confirm


def _bold(block: str, label: str) -> str:
    return re.search(rf"{label}: \*\*(.+?)\*\*", block).group(1)  # type: ignore[union-attr]


def parse_v23(md: str) -> tuple[dict, dict]:
    """v2.3: 탐색 질문은 공통 문장 하나, 확인은 공통 질문 + 짧은 행동 문구. 연결·ID는 v2.2와 같다."""
    intro_block = _section(md, "### 2.1 시작 화면", "### 2.2")
    title = re.search(r"^\*\*(.+)\*\*$", intro_block, re.MULTILINE).group(1)  # type: ignore[union-attr]
    intro = [_quoted(x) for x in intro_block.splitlines() if x.startswith("“")]
    ex_head = _section(md, "### 2.2 탐색 화면", "### 2.3")
    prompt, hint = _bold(ex_head, "질문"), _bold(ex_head, "짧은 안내")
    n_label, x_label = _bold(ex_head, "공통 N"), _bold(ex_head, "공통 X")
    c_head = _section(md, "### 2.3 확인 화면", "## 3.")
    c_prompt = _bold(c_head, "공통 질문")
    guide = [re.search(r"첫 확인 화면에서만 안내: \*\*(.+?)\*\*", c_head).group(1),  # type: ignore[union-attr]
             re.search(r"한 번만 표시: \*\*(.+?)\*\*", c_head).group(1)]  # type: ignore[union-attr]
    scale = [{"code": code, "label": label, "points": None if pts == "미관측" else int(pts.replace("+", "").replace("−", "-"))}
             for code, label, _meaning, pts in _rows(c_head.split("| 코드")[1].split("\n\n")[0].join(["| 코드", ""]))]
    explore_qs = []
    ex_block = _section(md, "## 3. 탐색 12문항 전체", "## 4.")
    for m in re.finditer(r"^### (E\d\d)$", ex_block, re.MULTILINE):
        qid = m.group(1)
        body = ex_block[m.end():].split("\n### ")[0]
        opts = [{"code": c, "label": lab, "family_id": f, "tag_hint": int(t)} for c, lab, f, t in _rows(body)]
        opts += [{"code": "N", "label": n_label, "family_id": None, "tag_hint": None},
                 {"code": "X", "label": x_label, "family_id": None, "tag_hint": None}]
        explore_qs.append({"question_id": qid, "text": prompt, "max_select": 2, "exclusive_codes": ["N", "X"],
                           "options": opts})
    measure = {int(r[0]): r[5] for r in _rows(_section(md, "## 5. 태그 연결", "## 6."))}
    confirm_qs = []
    c_block = _section(md, "## 4. 확인 75문항 전체", "## 5.")
    for m in re.finditer(r"^### (\d+) · (.+?) · (G\d\d)$", c_block, re.MULTILINE):
        tagid, fid = int(m.group(1)), m.group(3)
        body = c_block[m.end():].split("\n### ")[0]
        for qid, text, role in _rows(body):
            confirm_qs.append({"question_id": qid, "tagid": tagid, "family_id": fid, "role": ROLE_V23[role],
                               "text": text, "intent": measure[tagid]})
    src = "GEMBTI_QUESTION_BANK_v2.3.md"
    explore = {"questionnaire_version": "v2.3", "bank_version": "2026-10-26-r2", "stage": "explore",
               "source": f"{src} §2.1·2.2·3", "intro_title": title, "intro": "\n\n".join(intro), "hint": hint,
               "note": "분기 선택 전용. 선택 하나는 연결 계열 탐색 +1, 연결 tagid 선택 +1. N/X·비선택은 점수 없음.",
               "questions": explore_qs}
    confirm = {"questionnaire_version": "v2.3", "bank_version": "2026-10-26-r2", "stage": "confirm",
               "source": f"{src} §2.3·4", "prompt": c_prompt, "guide": guide, "scale": scale,
               "note": "각 응답은 대상 tagid 하나에만 기여. _1·_2 기본 확인, _3 예비 확인.",
               "questions": confirm_qs}
    return explore, confirm


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    md = src.read_text(encoding="utf-8")
    explore, confirm = (parse_v23 if "v2.3" in src.name else parse)(md)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("explore.json", explore), ("confirm.json", confirm)):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"탐색 {len(explore['questions'])} · 확인 {len(confirm['questions'])} → {out}")


if __name__ == "__main__":
    main()
