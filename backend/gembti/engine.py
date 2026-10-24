"""고정 배점 엔진: 태그 점수·상태(4절), 결정적 session_id·동점(5.3), 카드(5.4·6절).

순수 함수만 둔다. 자유 서술·필터·제출 ID·시각은 입력으로 받지 않는다.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from itertools import combinations

from gembti.policy import Policy

# ---------------------------------------------------------------- 응답 정규화


def normalize_answers(policy: Policy, answers: dict[str, str | None]) -> dict[str, str]:
    """누락 객관식은 명시적 X. 허용되지 않은 코드·문항은 ValueError."""
    unknown = set(answers) - set(policy.question_ids)
    if unknown:
        raise ValueError(f"알 수 없는 문항: {sorted(unknown)}")
    out = {}
    for qid in policy.question_ids:
        code = answers.get(qid)
        code = "X" if code is None else str(code).strip().upper()
        if code not in policy.allowed_codes(qid):
            raise ValueError(f"{qid}: 허용되지 않은 선택 {code!r}")
        out[qid] = code
    return out


def session_id(policy: Policy, answers: dict[str, str]) -> str:
    """5.3 판정용 session_id: 고정 직렬화의 SHA-256 소문자 hex."""
    v = policy.versions
    lines = [
        "gembti-session-v1",
        f"questionnaire_version={v['questionnaire_version']}",
        f"scoring_version={v['scoring_version']}",
        f"type_mapping_version={v['type_mapping_version']}",
        f"tie_policy_version={v['tie_policy_version']}",
    ] + [f"{qid}={answers[qid]}" for qid in policy.question_ids]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def tag_hash(sid: str, tagid: int) -> str:
    return hashlib.sha256(f"gembti-tie-v1|{sid}|tag|{tagid}".encode()).hexdigest()


def group_hash(sid: str, group_id: str) -> str:
    return hashlib.sha256(f"gembti-tie-v1|{sid}|group|{group_id}".encode()).hexdigest()


# ---------------------------------------------------------------- 태그 점수


@dataclass
class TagResult:
    tagid: int
    group_id: str
    valid_questions: list[str]
    n: int
    positive_count: int
    negative_count: int
    direct_answers: dict[str, str]
    raw_sum: int
    max_sum: int
    normalized: Fraction | None
    state: str  # unknown / conditional / positive / avoid / neutral_or_weak
    direct_strength: Fraction | None
    not_selected: list[str] = field(default_factory=list)  # 전용 선택지에서 이 태그를 고르지 않아 0점

    def as_dict(self) -> dict:
        d = asdict(self)
        d["normalized"] = None if self.normalized is None else float(self.normalized)
        d["direct_strength"] = None if self.direct_strength is None else float(self.direct_strength)
        return d


def score_tags(policy: Policy, answers: dict[str, str]) -> dict[int, TagResult]:
    out: dict[int, TagResult] = {}
    for tag in policy.tags:
        valid: list[str] = []
        pts: dict[str, int] = {}
        not_selected: list[str] = []
        for qid in tag.questions:
            code = answers[qid]
            if code == "X":
                continue
            valid.append(qid)
            p = policy.scoring[qid][code].get(tag.tagid, 0)
            pts[qid] = p
            if p == 0 and policy.questions[int(qid[1:]) - 1]["kind"] == "special":
                not_selected.append(qid)
        n = len(valid)
        pos = sum(1 for v in pts.values() if v > 0)
        neg = sum(1 for v in pts.values() if v < 0)
        raw = sum(pts.values())
        max_sum = sum(policy.max_points(q, tag.tagid) for q in tag.questions)
        norm = Fraction(raw, max_sum) if n == 2 else None

        d: Fraction | None = None
        if all(q in pts for q in tag.direct):
            d = sum((Fraction(pts[q], policy.max_points(q, tag.tagid)) for q in tag.direct), Fraction(0))
            d = d / len(tag.direct)
        direct_pos = all(pts.get(q, 0) > 0 for q in tag.direct)
        direct_neg = all(pts.get(q, 0) < 0 for q in tag.direct)

        if n < 2 or norm is None:
            state = "unknown"
        elif pos >= 1 and neg >= 1:
            state = "conditional"
        elif pos == 2 and norm >= Fraction(1, 2) and direct_pos:
            state = "positive"
        elif neg == 2 and norm <= Fraction(-1, 2) and direct_neg:
            state = "avoid"
        else:
            state = "neutral_or_weak"
        out[tag.tagid] = TagResult(tag.tagid, tag.group_id, valid, n, pos, neg,
                                   {q: answers[q] for q in tag.direct}, raw, max_sum, norm, state, d,
                                   not_selected)
    return out


# ---------------------------------------------------------------- 동점 정렬과 기록


@dataclass
class Candidate:
    cid: str  # tagid 문자열 또는 Gxx
    score: Fraction
    d: Fraction
    hkey: str


def _req(v: Fraction | None) -> Fraction:
    """positive 태그는 n=2·직접 응답이 있으므로 점수와 D가 항상 있다."""
    if v is None:
        raise AssertionError("positive 태그에 점수/D 없음")
    return v


def _sort_key(c: Candidate):
    return (-c.score, -c.d, c.hkey, c.cid.encode("utf-8"))


def rank_with_log(stage: str, cands: list[Candidate], sid: str, log: list[dict]) -> list[Candidate]:
    """점수→D→해시→(충돌 시) ID 사전순. 같은 점수 후보 쌍을 한 번씩 기록."""
    ordered = sorted(cands, key=_sort_key)
    for a, b in combinations(ordered, 2):
        if a.score != b.score:
            continue
        if a.d != b.d:
            decided = "direct_strength"
        elif a.hkey != b.hkey:
            decided = "hash_order"
        else:
            decided = "hash_collision_fallback"
        log.append({
            "stage": stage, "session_id": sid,
            "candidates": [{"id": c.cid, "score": float(c.score), "d": float(c.d), "hash_key": c.hkey}
                           for c in (a, b)],
            "winner": a.cid, "decided_by": decided,
        })
    return ordered


@dataclass
class CardResult:
    type_id: str
    type_name: str
    tagline: str
    is_discovering: bool
    positive_tagids: list[int]
    independent_evidence: int
    independent_evidence_unmerged: int
    group_order: list[str]  # positive 그룹 순위
    group_representatives: dict[str, int]
    main_groups: list[str]  # 주요 근거(최대 3, 사회적 그룹 최대 1)
    main_tagids: list[int]
    extra_social_tagids: list[int]  # 건너뛴 사회적 선호(추가 정보)
    tie_log: list[dict]

    def as_dict(self) -> dict:
        return asdict(self)


def build_card(policy: Policy, tags: dict[int, TagResult], sid: str) -> CardResult:
    log: list[dict] = []
    positive = [t for t in tags.values() if t.state == "positive"]
    by_group: dict[str, list[TagResult]] = {}
    for t in positive:
        by_group.setdefault(t.group_id, []).append(t)

    reps: dict[str, TagResult] = {}
    for gid in sorted(by_group):
        members = by_group[gid]
        cands = [Candidate(str(t.tagid), _req(t.normalized), _req(t.direct_strength), tag_hash(sid, t.tagid))
                 for t in members]
        best = rank_with_log("tag_representative", cands, sid, log)[0] if len(cands) > 1 else cands[0]
        reps[gid] = next(t for t in members if str(t.tagid) == best.cid)

    gcands = [Candidate(gid, _req(r.normalized), _req(r.direct_strength), group_hash(sid, gid))
              for gid, r in reps.items()]
    group_order = [c.cid for c in rank_with_log("group_rank", gcands, sid, log)]

    social = {g.group_id for g in policy.groups if g.social}
    non_social_pos = [g for g in reps if g not in social]
    indep = len(non_social_pos) + (1 if any(g in social for g in reps) else 0)
    indep_unmerged = len(reps)

    main: list[str] = []
    skipped_social: list[str] = []
    for gid in group_order:
        if gid in social and any(g in social for g in main):
            skipped_social.append(gid)  # 사회적 그룹은 주요 근거 최대 1칸
        elif len(main) < 3:
            main.append(gid)

    disc = policy.types["discovering"]
    discovering = len(positive) < policy.types["min_positive_tags"] or indep < policy.types["min_independent_evidence"]
    if discovering:
        type_id, name, tagline = disc["type_id"], disc["name"], disc["tagline"]
    else:
        pair = set(main[:2])
        chosen = next((t for t in policy.types["types"] if "combo_groups" in t and set(t["combo_groups"]) == pair), None)
        if chosen is None:
            chosen = next(t for t in policy.types["types"] if t.get("top_group") == main[0])
        type_id, name, tagline = chosen["type_id"], chosen["name"], chosen["tagline"]

    return CardResult(
        type_id=type_id, type_name=name, tagline=tagline, is_discovering=discovering,
        positive_tagids=sorted(t.tagid for t in positive),
        independent_evidence=indep, independent_evidence_unmerged=indep_unmerged,
        group_order=group_order,
        group_representatives={g: r.tagid for g, r in reps.items()},
        main_groups=main, main_tagids=[reps[g].tagid for g in main],
        extra_social_tagids=[reps[g].tagid for g in skipped_social],
        tie_log=log,
    )


@dataclass
class Judgement:
    session_id: str
    answers: dict[str, str]
    tags: dict[int, TagResult]
    card: CardResult
    versions: dict


def judge(policy: Policy, raw_answers: dict[str, str | None]) -> Judgement:
    answers = normalize_answers(policy, raw_answers)
    sid = session_id(policy, answers)
    tags = score_tags(policy, answers)
    card = build_card(policy, tags, sid)
    return Judgement(sid, answers, tags, card, dict(policy.versions))


def tie_stats(card: CardResult) -> dict:
    """5.3 동점 해소 집계(제출 1건). 비교 수 0이면 비율은 None(계산 불가)."""
    out: dict[str, object] = {}
    for stage in ("tag_representative", "group_rank"):
        rows = [r for r in card.tie_log if r["stage"] == stage]
        n = len(rows)
        ds = sum(1 for r in rows if r["decided_by"] == "direct_strength")
        hs = sum(1 for r in rows if r["decided_by"] in ("hash_order", "hash_collision_fallback"))
        out[stage] = {"score_tie_comparisons": n, "direct_strength": ds, "hash": hs,
                      "collision": sum(1 for r in rows if r["decided_by"] == "hash_collision_fallback"),
                      "direct_strength_rate": ds / n if n else None, "hash_rate": hs / n if n else None}
    out["used_hash"] = any(r["decided_by"] != "direct_strength" for r in card.tie_log)
    return out
