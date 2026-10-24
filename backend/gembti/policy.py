"""정책 데이터(policy/*.json) 로드와 무결성 검사."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from gembti.config import POLICY_DIR

CODES_GENERAL = ("A", "B", "C", "D", "E", "X")
CODES_SPECIAL = ("A", "B", "C", "D", "X")


@dataclass(frozen=True)
class Tag:
    tagid: int
    name_ko: str
    name_en: str
    group_id: str
    questions: tuple[str, ...]  # 이 태그에 배점하는 문항 2개
    direct: tuple[str, ...]  # 직접 근거 문항
    primary_q: str | None


@dataclass(frozen=True)
class Group:
    group_id: str
    name: str
    tagids: tuple[int, ...]
    social: bool


@dataclass
class Policy:
    versions: dict
    tags: list[Tag]
    groups: list[Group]
    questions: list[dict]
    free_texts: list[dict]
    intro: str
    scoring: dict[str, dict[str, dict[int, int]]]  # qid -> code -> tagid -> points
    types: dict
    aliases: dict
    content: dict = field(default_factory=dict)  # policy/content_review.json
    tag_by_id: dict[int, Tag] = field(default_factory=dict)
    group_by_id: dict[str, Group] = field(default_factory=dict)

    @property
    def question_ids(self) -> list[str]:
        return [q["question_id"] for q in self.questions]

    def allowed_codes(self, qid: str) -> tuple[str, ...]:
        q = next(q for q in self.questions if q["question_id"] == qid)
        return tuple(o["code"] for o in q["options"])

    def max_points(self, qid: str, tagid: int) -> int:
        return max(self.scoring[qid][c].get(tagid, 0) for c in self.scoring[qid])


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_policy(policy_dir: Path = POLICY_DIR) -> Policy:
    versions = _read(policy_dir / "versions.json")
    tj = _read(policy_dir / "tags.json")
    gj = _read(policy_dir / "groups.json")
    qj = _read(policy_dir / "questions.json")
    sj = _read(policy_dir / "scoring.json")
    ty = _read(policy_dir / "types.json")
    al = _read(policy_dir / "aliases.json")
    ct = _read(policy_dir / "content_review.json")

    tags = []
    for t in tj["tags"]:
        qs = tuple(t["joint"]) if "joint" in t else (t["primary_q"], t["support_q"])
        tags.append(Tag(t["tagid"], t["name_ko"], t["name_en"], t["group_id"], qs,
                        tuple(t["direct"]), t.get("primary_q")))
    groups = [Group(g["group_id"], g["name"], tuple(g["tagids"]), g["social"]) for g in gj["groups"]]
    scoring = {
        qid: {code: {int(k): v for k, v in pts.items()} for code, pts in spec["points"].items()}
        for qid, spec in sj["questions"].items()
    }
    p = Policy(versions, tags, groups, qj["questions"], qj["free_texts"], qj["intro"], scoring, ty, al, ct)
    p.tag_by_id = {t.tagid: t for t in tags}
    p.group_by_id = {g.group_id: g for g in groups}
    validate(p)
    return p


def validate(p: Policy) -> None:
    """정책 무결성: 20태그·20문항·각 태그 2문항·그룹 배정·버전 일치."""
    errs = []
    if len(p.tags) != 20 or len({t.tagid for t in p.tags}) != 20:
        errs.append("판정 태그는 서로 다른 20개여야 함")
    qids = p.question_ids
    if qids != [f"Q{i:02d}" for i in range(1, 21)]:
        errs.append("객관식은 Q01~Q20 순서여야 함")
    if [f["free_text_id"] for f in p.free_texts] != ["F01", "F02", "F03"]:
        errs.append("자유 서술은 F01~F03")
    for t in p.tags:
        if len(t.questions) != 2 or len(set(t.questions)) != 2:
            errs.append(f"{t.tagid}: 문항 2개 필요")
        if t.group_id not in p.group_by_id or t.tagid not in p.group_by_id[t.group_id].tagids:
            errs.append(f"{t.tagid}: 그룹 배정 불일치")
        for q in t.questions:
            if all(t.tagid not in p.scoring[q][c] for c in p.scoring[q]):
                errs.append(f"{t.tagid}: {q} 배점 없음")
    for qid in qids:
        allowed = set(p.allowed_codes(qid)) - {"X"}
        if set(p.scoring[qid]) != allowed:
            errs.append(f"{qid}: 배점 코드 {sorted(p.scoring[qid])} != 선택지 {sorted(allowed)}")
        if "X" in p.scoring[qid]:
            errs.append(f"{qid}: X는 배점하면 안 됨")
        for code, pts in p.scoring[qid].items():
            for tid in pts:
                if tid not in p.tag_by_id or qid not in p.tag_by_id[tid].questions:
                    errs.append(f"{qid}/{code}: 매핑 밖 태그 {tid}에 배점")
    grouped = sorted(t for g in p.groups for t in g.tagids)
    if grouped != sorted(p.tag_by_id):
        errs.append("모든 태그가 정확히 한 그룹에 속해야 함")
    if p.types.get("type_mapping_version") != p.versions["type_mapping_version"]:
        errs.append("type_mapping_version 불일치")
    if p.content.get("catalog_policy_version") != p.versions["catalog_policy_version"]:
        errs.append("content_review catalog_policy_version 불일치")
    if any(r["decision"] not in ("keep", "exclude", "hold") for r in p.content.get("reviews", [])):
        errs.append("content_review decision은 keep/exclude/hold")
    if errs:
        raise ValueError("정책 무결성 오류: " + "; ".join(errs))


def content_status(p: Policy, game: dict) -> str | None:
    """성적 콘텐츠 적격성. None=적격. 검토 대상 태그가 있는데 검토 기록이 없으면 보류."""
    decision = {r["appid"]: r["decision"] for r in p.content.get("reviews", [])}.get(game["appid"])
    if decision == "exclude":
        return "content_excluded"
    if decision == "hold":
        return "content_hold"
    if decision is None and any(t["tagid"] in p.content.get("review_trigger_tagids", []) for t in game["tags"]):
        return "content_review_pending"
    return None


@lru_cache(maxsize=1)
def get_policy() -> Policy:
    return load_policy()
