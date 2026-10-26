"""v2 분기·배점·유형 판정 (순수 함수).

경로는 항상 응답에서 다시 계산한다: 탐색 12 → 확인 8(4계열×2) → 필요 시 추가 최대 2세트(각 ≤2문항).
경로 밖 응답은 판정·canonical에서 제외한다. 라이브러리·필터·서술·시각은 입력이 아니다.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

from gembti.v2.policy import EXPLORE_CODES, V2Policy

Answer = str | list[str]


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 응답 검증


def norm_explore(v: Answer | None) -> tuple[str, ...] | None:
    if v is None:
        return None
    codes = [v] if isinstance(v, str) else list(v)
    codes = sorted({str(c).strip().upper() for c in codes})
    if codes in (["N"], ["X"]):
        return tuple(codes)
    if 1 <= len(codes) <= 2 and all(c in EXPLORE_CODES for c in codes):
        return tuple(codes)
    raise ValueError(f"탐색 응답은 A~D 중 1~2개, 또는 N 또는 X: {v!r}")


def norm_confirm(p: V2Policy, v: Answer | None) -> str | None:
    if v is None:
        return None
    c = str(v).strip().upper()
    if c not in p.points:
        raise ValueError(f"확인 응답은 A~E 또는 X: {v!r}")
    return c


# ---------------------------------------------------------------- 분기


def explore_canonical(p: V2Policy, answers: dict) -> str:
    return "\n".join(f"{q}={'+'.join(norm_explore(answers.get(q)) or ())}" for q in p.explore_ids)


def interest(p: V2Policy, answers: dict) -> tuple[dict[str, int], dict[int, int]]:
    """계열 흥미 점수(문항당 계열 최대 1회)와 태그 힌트 수. 미선택에 음수 없음."""
    fam: dict[str, int] = {}
    tag: dict[int, int] = {}
    for qid in p.explore_ids:
        codes = norm_explore(answers.get(qid)) or ()
        q = p.explore_by_id[qid]
        seen = set()
        for o in q["options"]:
            if o["code"] in codes and o["family_id"]:
                if o["family_id"] not in seen:
                    fam[o["family_id"]] = fam.get(o["family_id"], 0) + 1
                    seen.add(o["family_id"])
                tag[o["tag_hint"]] = tag.get(o["tag_hint"], 0) + 1
    return fam, tag


@dataclass
class Route:
    path: list[str]
    next_question: str | None
    initial_families: list[str]
    interest_families: list[str]
    explore_slot_family: str | None
    chosen_tags: dict[str, int]
    extra_sets: list[list[str]] = field(default_factory=list)
    stage: str = "explore"
    done: bool = False


def _family_order(p: V2Policy, rhash: str, fam_int: dict[str, int]) -> list[str]:
    return sorted(p.family_by_id, key=lambda f: (-fam_int.get(f, 0), _h(f"{rhash}|family|{f}")))


def _pick_tag(p: V2Policy, rhash: str, fid: str, tag_int: dict[int, int]) -> int:
    tags = [t["tagid"] for t in p.family_by_id[fid]["tags"]]
    return min(tags, key=lambda t: (-tag_int.get(t, 0), _h(f"{rhash}|tag|{t}")))


def route(p: V2Policy, answers: dict) -> Route:
    if p.routing.get("algorithm") == "v2.2":
        return route_v22(p, answers)
    r = p.routing
    path = list(p.explore_ids)
    for qid in p.explore_ids:
        if norm_explore(answers.get(qid)) is None:
            return Route(path, qid, [], [], None, {}, stage="explore")
    rhash = _h(f"gembti-route-v2|{p.versions['routing_version']}|{explore_canonical(p, answers)}")
    fam_int, tag_int = interest(p, answers)
    order = _family_order(p, rhash, fam_int)
    social = p.social
    chosen: list[str] = []
    interest_fams: list[str] = []
    for f in order:  # 흥미 있는 계열 상위 3 (사회 계열 최대 1)
        if len(chosen) == r["interest_slots"] or fam_int.get(f, 0) == 0:
            break
        if f in social and any(c in social for c in chosen):
            continue
        chosen.append(f)
        interest_fams.append(f)
    # 남은 슬롯(탐색 슬롯 1 + 흥미 부족분)은 아직 고르지 않은 계열에서 해시 순서로
    slot_order = sorted((f for f in p.family_by_id if f not in chosen), key=lambda f: _h(f"{rhash}|slot|{f}"))
    explore_slot = None
    for f in slot_order:
        if len(chosen) == r["confirm_families"]:
            break
        if f in social and any(c in social for c in chosen):
            continue
        chosen.append(f)
        explore_slot = f
    tags = {f: _pick_tag(p, rhash, f, tag_int) for f in chosen}
    for f in chosen:
        path += p.confirm_by_tag[tags[f]][:2]
    base = Route(path, None, chosen, interest_fams, explore_slot, dict(tags), stage="confirm")
    for qid in path:
        if qid.startswith("C") and norm_confirm(p, answers.get(qid)) is None:
            base.next_question = qid
            return base
    # 추가 세트: 경계마다 다시 평가
    asked_fams = list(chosen)
    for s in range(r["extra_sets_max"]):
        st = score_tags(p, answers, base.path)
        card = build_card(p, st, session_id_v2(p, answers, base.path))
        if not card.is_discovering:
            break
        extra = _next_extra_set(p, rhash, st, base.path, asked_fams, order, tags, tag_int)
        if not extra:
            break
        base.extra_sets.append(extra)
        base.path += extra
        base.stage = "extra"
        for qid in extra:
            if norm_confirm(p, answers.get(qid)) is None:
                base.next_question = qid
                return base
    base.done = True
    base.stage = "done"
    return base


def _next_extra_set(p, rhash, st, path, asked_fams, order, tags, tag_int) -> list[str]:
    size = p.routing["extra_set_size"]
    singles_n1, singles_cond = [], []
    for t, res in st.items():
        unused = [q for q in p.confirm_by_tag[t] if q not in path]
        if not unused:
            continue
        if res.n == 1 and res.positive_count == 1:
            singles_n1.append((t, unused[0]))
        elif res.state == "conditional":
            singles_cond.append((t, unused[0]))
    singles = [q for _, q in sorted(singles_n1)] + [q for _, q in sorted(singles_cond)]
    out = singles[:size]
    if size - len(out) >= 2:
        for f in order:  # 미조사 계열(흥미순→해시)의 대표 태그 2문항
            if f in asked_fams:
                continue
            tag = _pick_tag(p, rhash, f, tag_int)
            asked_fams.append(f)
            tags[f] = tag
            out += p.confirm_by_tag[tag][:2]
            break
    return out[:size]


# ---------------------------------------------------------------- 분기 v2.2 (질문지 문서 §8)


def _exposure(p: V2Policy) -> dict[int, int]:
    exp: dict[int, int] = {}
    for q in p.explore["questions"]:
        for o in q["options"]:
            if o["tag_hint"] is not None:
                exp[o["tag_hint"]] = exp.get(o["tag_hint"], 0) + 1
    return exp


def _pick_tag_v22(p: V2Policy, rhash: str, fid: str, sel: dict[int, int], exp: dict[int, int]) -> int:
    """선택 횟수/노출 횟수 → 선택 횟수 → 해시. 노출이 많은 태그가 자동으로 유리하지 않게 한다."""
    tags = [t["tagid"] for t in p.family_by_id[fid]["tags"]]
    return min(tags, key=lambda t: (-(sel.get(t, 0) / exp[t]), -sel.get(t, 0), _h(f"{rhash}|tag|{t}")))


def route_v22(p: V2Policy, answers: dict) -> Route:
    r, v = p.routing, p.versions
    path = list(p.explore_ids)
    for qid in p.explore_ids:
        if norm_explore(answers.get(qid)) is None:
            return Route(path, qid, [], [], None, {}, stage="explore")
    rhash = _h(f"gembti-route-v2|{v['routing_version']}|{v['bank_version']}|{v['layout_version']}|"
               f"{explore_canonical(p, answers)}")
    fam_int, sel = interest(p, answers)
    exp = _exposure(p)
    order = _family_order(p, rhash, fam_int)
    social = p.social
    top: list[str] = []
    for f in order:  # 상위3: 관심 0 계열도 해시 순서로 들어온다(모두 N/X여도 4계열 확인)
        if len(top) == r["interest_slots"]:
            break
        if f in social and any(c in social for c in top):
            continue
        top.append(f)
    slot = next(f for f in sorted((f for f in p.family_by_id if f not in top), key=lambda f: _h(f"{rhash}|slot|{f}"))
                if not (f in social and any(c in social for c in top)))
    chosen = top + [slot]
    tags = {f: _pick_tag_v22(p, rhash, f, sel, exp) for f in chosen}
    path += [f"C{tags[f]}_1" for f in chosen] + [f"C{tags[f]}_2" for f in chosen]
    base = Route(path, None, chosen, [f for f in top if fam_int.get(f, 0) > 0], slot, dict(tags), stage="confirm")
    for qid in path[len(p.explore_ids):]:
        if norm_confirm(p, answers.get(qid)) is None:
            base.next_question = qid
            return base
    while len(base.path) < r["max_total"]:
        st = score_tags(p, answers, base.path)
        card = build_card(p, st, session_id_v2(p, answers, base.path))
        top_open = [tags[f] for f in top if st[tags[f]].n < 2 or st[tags[f]].state == "conditional"]
        if not card.is_discovering and not top_open:
            break
        extra = _extra_v22(p, rhash, st, base.path, order, tags, sel, exp, card.is_discovering,
                           [tags[f] for f in top])
        if not extra:
            break
        base.extra_sets.append(extra)
        base.path += extra
        base.stage = "extra"
        for qid in extra:
            if norm_confirm(p, answers.get(qid)) is None:
                base.next_question = qid
                return base
    base.done = True
    base.stage = "done"
    return base


def _extra_v22(p, rhash, st, path, order, tags, sel, exp, discovering, top_tags) -> list[str]:
    budget = p.routing["max_total"] - len(path)
    rank = {f: i for i, f in enumerate(order)}
    # 정규 카드를 이미 충족했으면 상위3계열 태그의 보완만(추가 사유가 그것뿐이므로)
    probed = sorted((t for t in st if discovering or t in top_tags), key=lambda t: rank[p.family_of[t]])
    for want in ("n_lt_2", "conditional"):  # _3은 태그별 1회, positive/avoid 태그는 다시 묻지 않음
        for t in probed:
            q3 = f"C{t}_3"
            if q3 in path or st[t].state in ("positive", "avoid"):
                continue
            if (want == "n_lt_2" and st[t].n < 2) or (want == "conditional" and st[t].state == "conditional"):
                return [q3]
    if discovering and budget >= 2:  # 새 계열은 기본 2문항을 끝낼 예산이 있을 때만
        for f in order:
            if f in tags:
                continue
            t = _pick_tag_v22(p, rhash, f, sel, exp)
            tags[f] = t
            return [f"C{t}_1", f"C{t}_2"]
    return []


# ---------------------------------------------------------------- 배점·상태


@dataclass
class TagState:
    tagid: int
    family_id: str
    questions: list[str]
    n: int
    positive_count: int
    negative_count: int
    raw: int
    normalized: float | None
    state: str
    direct_strength: float | None


def score_tags(p: V2Policy, answers: dict, path: list[str]) -> dict[int, TagState]:
    """경로 안의 확인 문항만. 같은 문항은 한 태그에만 배점."""
    by_tag: dict[int, list[tuple[str, int]]] = {}
    asked: dict[int, list[str]] = {}
    for qid in path:
        if not qid.startswith("C"):
            continue
        q = p.confirm_by_id[qid]
        asked.setdefault(q["tagid"], []).append(qid)
        code = norm_confirm(p, answers.get(qid))
        if code is None or code == "X":
            continue
        pts = p.points[code]
        assert pts is not None  # X는 위에서 걸렀다
        by_tag.setdefault(q["tagid"], []).append((qid, pts))
    out = {}
    for t, qids in asked.items():
        vals = [v for _, v in by_tag.get(t, [])]
        n = len(vals)
        pos = sum(1 for v in vals if v > 0)
        neg = sum(1 for v in vals if v < 0)
        raw = sum(vals)
        norm = raw / (2 * n) if n else None
        if n < 2:
            state = "unknown"
        elif pos and neg:
            state = "conditional"
        elif pos >= 2 and neg == 0 and norm is not None and norm >= 0.5:
            state = "positive"
        elif neg >= 2 and pos == 0 and norm is not None and norm <= -0.5:
            state = "avoid"
        else:
            state = "neutral_or_weak"
        d = (sum(1 for v in vals if v == 2) / n) if n else None
        out[t] = TagState(t, p.family_of[t], qids, n, pos, neg, raw, norm, state, d)
    return out


# ---------------------------------------------------------------- 판정 seed·카드


def session_id_v2(p: V2Policy, answers: dict, path: list[str]) -> str:
    v = p.versions
    lines = ["gembti-session-v2"] + [f"{k}={v[k]}" for k in
                                     ("questionnaire_version", "scoring_version", "type_mapping_version",
                                      "routing_version", "tie_policy_version")]
    lines += [f"{k}={v[k]}" for k in ("bank_version", "layout_version") if k in v]  # v2.2부터(v2.0 seed 불변)
    for qid in sorted(path):
        if qid.startswith("E"):
            code = "+".join(norm_explore(answers.get(qid)) or ())
        else:
            code = norm_confirm(p, answers.get(qid)) or ""
        if code:
            lines.append(f"{qid}={code}")
    return _h("\n".join(lines))


@dataclass
class CardV2:
    type_id: str
    name: str
    tagline: str
    is_discovering: bool
    positive_tagids: list[int]
    independent_families: int
    family_order: list[str]
    main_tagids: list[int]
    extra_tagids: list[int]

    def as_dict(self) -> dict:
        return asdict(self)


def build_card(p: V2Policy, st: dict[int, TagState], sid: str) -> CardV2:
    pos = [s for s in st.values() if s.state == "positive"]
    best: dict[str, TagState] = {}
    for s in pos:  # 계열 점수 = 그 계열 positive 태그 최댓값 1회
        cur = best.get(s.family_id)
        key = (s.normalized, s.direct_strength, _h(f"{sid}|tag|{s.tagid}"))
        if cur is None or key[:2] > (cur.normalized, cur.direct_strength) or (
                key[:2] == (cur.normalized, cur.direct_strength) and key[2] < _h(f"{sid}|tag|{cur.tagid}")):
            best[s.family_id] = s
    fam_order = sorted(best, key=lambda f: (-(best[f].normalized or 0), -(best[f].direct_strength or 0),
                                           _h(f"{sid}|family|{f}")))
    social = p.social
    indep = len([f for f in best if f not in social]) + (1 if any(f in social for f in best) else 0)
    main: list[int] = []
    took_social = False
    for f in fam_order:
        if len(main) == 3:
            break
        if f in social:
            if took_social:
                continue
            took_social = True
        main.append(best[f].tagid)
    extra = sorted(s.tagid for s in pos if s.tagid not in main)
    disc = p.cards["discovering"]
    regular = len(pos) >= p.cards["min_positive_tags"] and indep >= p.cards["min_independent_families"]
    if not regular:
        return CardV2(disc["type_id"], disc["name"], disc["tagline"], True, sorted(s.tagid for s in pos), indep,
                      fam_order, main, extra)
    c = next(c for c in p.cards["cards"] if c["family_id"] == fam_order[0])
    return CardV2(c["type_id"], c["name"], c["tagline"], False, sorted(s.tagid for s in pos), indep, fam_order,
                  main, extra)


@dataclass
class JudgementV2:
    session_id: str
    route: Route
    tags: dict[int, TagState]
    card: CardV2
    answers: dict  # 경로 안의 최종 응답만
    dropped: list[str]  # 경로 밖이라 제외된 응답 문항


def judge_v2(p: V2Policy, answers: dict, require_done: bool = True) -> JudgementV2:
    unknown = [k for k in answers if k not in p.explore_by_id and k not in p.confirm_by_id]
    if unknown:
        raise ValueError(f"알 수 없는 문항 {sorted(unknown)}")
    for k, v in answers.items():  # 형식 검증
        norm_explore(v) if k.startswith("E") else norm_confirm(p, v)
    r = route(p, answers)
    if require_done and not r.done:
        raise ValueError(f"아직 답하지 않은 경로 문항: {r.next_question}")
    final = {q: answers[q] for q in r.path if answers.get(q) is not None}
    dropped = sorted(k for k in answers if k not in r.path)
    st = score_tags(p, answers, r.path)
    sid = session_id_v2(p, answers, r.path)
    return JudgementV2(sid, r, st, build_card(p, st, sid), final, dropped)
