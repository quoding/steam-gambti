"""v2 정책 데이터(policy/v2/*.json) 로드와 정적 검증."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from gembti.config import POLICY_DIR

V2_DIR = POLICY_DIR / "v2"
EXPLORE_CODES = ("A", "B", "C", "D")
# 질문지 은행: 버전마다 문항·분기 설정을 분리한다. registry·cards는 공용(policy/v2/).
# 과거 응시·제출은 자기 버전의 은행으로만 다시 판정한다(새 은행으로 묵시 재판정 금지).
BANKS = {"v2.0": V2_DIR, "v2.2": V2_DIR / "banks" / "v2.2",
         "v2.3": V2_DIR / "banks" / "v2.3"}


@dataclass
class V2Policy:
    versions: dict
    families: list[dict]
    explore: dict
    confirm: dict
    cards: dict
    routing: dict
    family_of: dict[int, str] = field(default_factory=dict)
    tag_name: dict[int, str] = field(default_factory=dict)
    family_by_id: dict[str, dict] = field(default_factory=dict)
    confirm_by_id: dict[str, dict] = field(default_factory=dict)
    confirm_by_tag: dict[int, list[str]] = field(default_factory=dict)
    explore_by_id: dict[str, dict] = field(default_factory=dict)
    points: dict[str, int | None] = field(default_factory=dict)

    @property
    def explore_ids(self) -> list[str]:
        return [q["question_id"] for q in self.explore["questions"]]

    @property
    def social(self) -> set[str]:
        return {f["family_id"] for f in self.families if f["social"]}


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def active_version() -> str:
    return _read(V2_DIR / "active.json")["questionnaire_version"]


def load_v2(d: Path = V2_DIR, shared: Path | None = None) -> V2Policy:
    shared = shared or (d if (d / "registry.json").exists() else V2_DIR)
    reg = _read(shared / "registry.json")
    p = V2Policy(_read(d / "versions.json"), reg["families"], _read(d / "explore.json"), _read(d / "confirm.json"),
                 _read(shared / "cards.json"), _read(d / "routing.json"))
    for f in p.families:
        p.family_by_id[f["family_id"]] = f
        for t in f["tags"]:
            p.family_of[t["tagid"]] = f["family_id"]
            p.tag_name[t["tagid"]] = t["name_ko"]
    for q in p.confirm["questions"]:
        p.confirm_by_id[q["question_id"]] = q
        p.confirm_by_tag.setdefault(q["tagid"], []).append(q["question_id"])
    for q in p.explore["questions"]:
        p.explore_by_id[q["question_id"]] = q
    p.points = {s["code"]: s["points"] for s in p.confirm["scale"]}
    errs = validate_v2(p)
    if errs:
        raise ValueError("v2 정책 정적 검증 실패: " + "; ".join(errs))
    return p


def validate_v2(p: V2Policy, known_tagids: set[int] | None = None) -> list[str]:
    """불명 ID·누락 배점·중복 근거·갈 곳 없는 분기를 0으로 만든다."""
    errs = []
    tags = [t["tagid"] for f in p.families for t in f["tags"]]
    if len(tags) != 25 or len(set(tags)) != 25:
        errs.append(f"태그는 서로 다른 25개여야 함 (현재 {len(set(tags))})")
    if len(p.families) != 16 or len({f["family_id"] for f in p.families}) != 16:
        errs.append("계열은 16개")
    if known_tagids is not None:
        unknown = sorted(set(tags) - known_tagids)
        if unknown:
            errs.append(f"Steam 사전에 없는 tagid {unknown}")
    if len(p.explore["questions"]) != p.routing["explore_count"]:
        errs.append("탐색 문항 수 불일치")
    appear: dict[str, int] = {}
    for q in p.explore["questions"]:
        fams = []
        for o in q["options"]:
            if o["code"] in EXPLORE_CODES:
                if o["family_id"] not in p.family_by_id:
                    errs.append(f"{q['question_id']}/{o['code']}: 불명 계열")
                elif p.family_of.get(o["tag_hint"]) != o["family_id"]:
                    errs.append(f"{q['question_id']}/{o['code']}: tag_hint가 계열 밖")
                fams.append(o["family_id"])
        if len(fams) != len(set(fams)):
            errs.append(f"{q['question_id']}: 한 문항에 같은 계열 선택지 중복")
        for f in set(fams):
            appear[f] = appear.get(f, 0) + 1
        codes = {o["code"] for o in q["options"]}
        if not {"N", "X"} <= codes:
            errs.append(f"{q['question_id']}: '끌리지 않음'·'모르겠음' 선택지 필요")
    missing = [f for f in p.family_by_id if appear.get(f, 0) == 0]
    if missing:
        errs.append(f"탐색 경로 없는 계열 {missing}")
    if set(p.points) != {"A", "B", "C", "D", "E", "X"} or p.points["X"] is not None:
        errs.append("확인 척도는 A~E·X, X는 결측")
    for t in tags:
        qs = p.confirm_by_tag.get(t, [])
        if len(qs) < 3:
            errs.append(f"{t}: 확인 문항 3개 필요(기본 2 + 추가 1), 현재 {len(qs)}")
        sits = [p.confirm_by_id[q].get("situation") or p.confirm_by_id[q]["text"] for q in qs]
        if len(set(sits)) != len(sits):
            errs.append(f"{t}: 같은 상황 반복")
    if p.routing.get("algorithm") == "v2.2":
        errs += _validate_v22(p, tags)
    texts = [q["text"] for q in p.confirm["questions"]]
    if len(texts) != len(set(texts)):
        errs.append("같은 문구의 확인 문항 중복")
    for q in p.confirm["questions"]:
        if q["tagid"] not in p.family_of or p.family_of[q["tagid"]] != q["family_id"]:
            errs.append(f"{q['question_id']}: 대상 태그·계열 불일치")
    card_fams = {c["family_id"] for c in p.cards["cards"]}
    if card_fams != set(p.family_by_id):
        errs.append("16계열 모두 카드 필요")
    for k in ("questionnaire_version", "scoring_version", "type_mapping_version", "routing_version"):
        if not p.versions.get(k, "").startswith("v2"):
            errs.append(f"{k}는 v2 계열 버전이어야 함")
    return errs


def _validate_v22(p: V2Policy, tags: list[int]) -> list[str]:
    """질문지 v2.2 명세 §11.1: 계열별 탐색 3회·서로 다른 위치, 모든 태그 분기 보기, 태그별 _1·_2 기본 + _3 예비."""
    errs = []
    pos: dict[str, list[str]] = {}
    hinted = set()
    for q in p.explore["questions"]:
        acts = [o for o in q["options"] if o["code"] in EXPLORE_CODES]
        if len(acts) != 4:
            errs.append(f"{q['question_id']}: 활동 보기는 4개")
        for o in acts:
            pos.setdefault(o["family_id"], []).append(o["code"])
            hinted.add(o["tag_hint"])
    for f in p.family_by_id:
        if len(pos.get(f, [])) != 3 or len(set(pos.get(f, []))) != 3:
            errs.append(f"{f}: 탐색 3회·서로 다른 위치 필요 ({pos.get(f)})")
    if set(tags) - hinted:
        errs.append(f"탐색 보기가 없는 태그 {sorted(set(tags) - hinted)}")
    for t in tags:
        want = {f"C{t}_1": "primary", f"C{t}_2": "primary", f"C{t}_3": "reserve"}
        got = {q: p.confirm_by_id[q].get("role") for q in p.confirm_by_tag.get(t, [])}
        if got != want:
            errs.append(f"{t}: 확인 문항은 _1·_2 기본, _3 예비 ({got})")
    if len(p.confirm["questions"]) != 75:
        errs.append("확인 문항은 75개")
    return errs


@cache
def get_v2(version: str | None = None) -> V2Policy:
    """version 없으면 현재 새 응시에 쓰는 은행(policy/v2/active.json)."""
    v = version or active_version()
    if v not in BANKS:
        raise KeyError(f"알 수 없는 질문지 버전 {v}")
    p = load_v2(BANKS[v], V2_DIR)
    if p.versions["questionnaire_version"] != v:
        raise ValueError(f"은행 {v}의 versions.json 불일치")
    return p
