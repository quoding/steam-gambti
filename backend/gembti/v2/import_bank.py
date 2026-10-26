"""질문지 문서(GEMBTI_QUESTION_BANK_v2.2.md)의 표를 그대로 기계 설정으로 옮긴다 (문구 요약·수정 없음).

사용(backend/): uv run python -m gembti.v2.import_bank ../GEMBTI_QUESTION_BANK_v2.2.md ../policy/v2/banks/v2.2
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
    intro = [_quoted(x) for x in intro_block.splitlines() if x.startswith("“")]
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
        opts = [{"code": c, "label": lab, "family_id": f, "tag_hint": int(t)} for c, lab, f, t in _rows(body)]
        opts += [{"code": "N", "label": n_label, "family_id": None, "tag_hint": None},
                 {"code": "X", "label": x_label, "family_id": None, "tag_hint": None}]
        explore_qs.append({"question_id": qid, "text": text, "max_select": 2, "exclusive_codes": ["N", "X"],
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
                               "text": text, "intent": intent, "distinguish": dist})
    explore = {"questionnaire_version": QUESTIONNAIRE_VERSION, "bank_version": BANK_VERSION, "stage": "explore",
               "source": "GEMBTI_QUESTION_BANK_v2.2.md §3.1·3.2·5", "intro": "\n\n".join(intro),
               "note": "분기 선택 전용. 선택 하나는 연결 계열 탐색 +1, 연결 tagid 선택 +1. N/X·비선택은 점수 없음.",
               "questions": explore_qs}
    confirm = {"questionnaire_version": QUESTIONNAIRE_VERSION, "bank_version": BANK_VERSION, "stage": "confirm",
               "source": "GEMBTI_QUESTION_BANK_v2.2.md §3.3·6", "context": context, "scale": scale,
               "note": "각 응답은 대상 tagid 하나에만 기여. _1·_2 기본 직접 근거, _3 예비 직접 근거.",
               "questions": confirm_qs}
    return explore, confirm


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    explore, confirm = parse(src.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("explore.json", explore), ("confirm.json", confirm)):
        (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"탐색 {len(explore['questions'])} · 확인 {len(confirm['questions'])} → {out}")


if __name__ == "__main__":
    main()
