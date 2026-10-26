"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import FiltersV2Fields from "@/components/FiltersV2Fields";
import LibraryPanel from "@/components/LibraryPanel";
import TopBar from "@/components/TopBar";
import TypeHero from "@/components/TypeHero";
import { api, errorMessage } from "@/lib/api";
import { load, remove, save } from "@/lib/storage";
import type { Card } from "@/lib/types";
import { DEFAULT_FILTERS, V2KEYS, type V2Card, type V2Filters, type V2Game, type V2Recs, type V2Session } from "@/lib/v2";

interface Result { submission_id: string; card: V2Card; recommendations: V2Recs }
const KO = { true: "한국어 지원", false: "한국어 미지원", unknown: "한국어 지원 확인 안 됨" } as const;

function GameV2({ g }: { g: V2Game }) {
  const [ok, setOk] = useState(Boolean(g.image));
  const ref = g.kind === "reference";
  return (
    <article className={`card game ${ref ? "ref" : ""}`}>
      <div className="game-img">
        {ok && g.image ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={g.image} alt="" loading="lazy" onError={() => setOk(false)} />
        ) : <div className="ph" aria-hidden>{g.name}</div>}
      </div>
      <div className="game-body">
        <div className="game-title">{g.name}</div>
        <div className="small" style={{ fontWeight: 700 }}>
          {g.review.label ? `Steam 평가 ${g.review.label}` : "Steam 공식 평가 확인 안 됨"}
          {g.review.positive_pct !== null && ` · 긍정률 ${g.review.positive_pct}%`}
          {` · 리뷰 ${g.review.total_reviews.toLocaleString()}개`}
        </div>
        <div className="tiny">리뷰 집계: Steam 구매 기준·전체 언어 · 최근 평가는 확인할 수 있는 자료가 없어 표시하지 않아요
          {g.review.free_game_note && " · 리뷰 집계 범위가 달라 Steam 상점에 표시된 리뷰 수와 차이가 날 수 있어요."}</div>
        <div className="chips">{g.tags.slice(0, 5).map((t) => <span key={t} className="chip soft" style={{ fontSize: 13, padding: "4px 10px" }}>{t}</span>)}</div>
        {g.short_description ? (
          <p className="game-desc" style={{ margin: 0 }}>{g.short_description}{!g.description_is_korean && <span className="tiny"> (상점 소개 원문)</span>}</p>
        ) : <p className="game-desc tiny" style={{ margin: 0 }}>상점 소개가 아직 없어요.</p>}
        <div className="reason">{g.reason}{g.library_note && " 플레이 기록도 참고했어요."}</div>
        <div className="game-foot">
          <span className={`lang ${g.korean}`}>{KO[g.korean]}</span>
          <a className="btn btn-ghost" style={{ minHeight: 40, padding: "0 16px", fontSize: 14 }} href={g.store_url} target="_blank" rel="noopener noreferrer">Steam 상점에서 보기 ↗</a>
        </div>
      </div>
    </article>
  );
}

export default function V2Result() {
  const [res, setRes] = useState<Result | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "none" | "error">("loading");
  const [err, setErr] = useState<string | null>(null);
  const [filters, setFilters] = useState<V2Filters>(DEFAULT_FILTERS);
  const [busy, setBusy] = useState(false);
  const session = typeof window === "undefined" ? null : load<V2Session>("local", V2KEYS.session);

  const applyResult = (r: Result) => {
    setRes(r);
    if (r.recommendations.filters) setFilters(r.recommendations.filters);
    save("session", V2KEYS.result, r);
    setState("ready");
  };

  const restore = useCallback(async () => {
    const cached = load<Result>("session", V2KEYS.result);
    if (cached) { applyResult(cached); return; }
    const last = load<string>("local", "gembti:v2:last");
    const s = load<V2Session>("local", V2KEYS.session);
    if (!last || !s) { setState("none"); return; }
    try { applyResult(await api<Result>(`/v2/submissions/${last}/view`, { ...s })); }
    catch (e) { setErr(errorMessage(e)); setState("error"); }
  }, []);
  useEffect(() => { restore(); }, [restore]);

  const rerun = useCallback(async (f?: V2Filters) => {
    if (!res || !session) return;
    setBusy(true); setErr(null);
    try {
      const r = await api<{ recommendations: V2Recs }>(`/v2/submissions/${res.submission_id}/recommendations`,
        { ...session, filters: f ?? filters });
      applyResult({ ...res, recommendations: r.recommendations });
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  }, [res, session, filters]);

  if (state === "loading") return <main className="shell"><TopBar /><div className="skeleton" style={{ height: 240, borderRadius: 24 }} /></main>;
  if (state === "none") {
    return <main className="shell"><TopBar /><div className="card stack"><h2>아직 v2 결과가 없어요</h2>
      <Link className="btn btn-primary" href="/v2/">v2 검사 시작하기</Link></div></main>;
  }
  if (state === "error" || !res) {
    return <main className="shell"><TopBar /><div className="card stack"><div className="notice err">{err}</div>
      <button className="btn btn-primary" onClick={restore}>다시 시도</button></div></main>;
  }
  const card = res.card;
  const recs = res.recommendations;
  const items = recs.items ?? [];
  const pers = items.filter((i) => i.kind === "personalized");
  const refs = items.filter((i) => i.kind === "reference");
  const c = recs.counts;
  const lib = recs.library;

  return (
    <main className="shell wide">
      <TopBar right={<Link className="btn-text small" href="/v2/" onClick={() => { remove("session", V2KEYS.result); }}>다시 검사</Link>} />
      <div style={{ maxWidth: 720 }}>
        <TypeHero card={card as unknown as Card} />
        <p className="tiny" style={{ margin: "8px 4px 0" }}>질문 {card.question_count}개의 답변으로만 정한 결과예요. 라이브러리와 필터는 유형을 바꾸지 않아요.</p>
        {card.checked_families && (
          <div className="card" style={{ marginTop: 16 }}>
            <p className="small" style={{ margin: 0 }}><b>이번에 확인한 영역</b> · {card.checked_families.join(", ")}</p>
            {card.unchecked_families && card.unchecked_families.length > 0 &&
              <p className="tiny" style={{ margin: "6px 0 0" }}>아직 확인하지 않은 영역 · {card.unchecked_families.join(", ")}</p>}
          </div>
        )}
        {card.evidence.length > 0 && (
          <div className="card" style={{ marginTop: 16 }}>
            <h3 style={{ marginBottom: 6 }}>이 결과의 근거</h3>
            {card.evidence.map((e) => (
              <details key={e.tagid} className="acc">
                <summary>{e.name} <span className="tiny">· {e.family}</span></summary>
                <ul className="small muted" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                  {e.answers.map((a) => <li key={a.question_id}><b>{a.text}</b> — “{a.answer}”</li>)}
                </ul>
              </details>
            ))}
            {card.extra_tags.length > 0 && <p className="small muted" style={{ margin: "12px 0 0" }}>추가로 확인된 취향: {card.extra_tags.map((t) => t.name).join(", ")}</p>}
          </div>
        )}
        <div className="card stack" style={{ marginTop: 16 }}>
          <div className="row"><h3>Steam 라이브러리</h3><span className="spacer" />
            <span className={`badge ${lib?.used ? "pers" : "ref"}`}>{lib?.linked ? (lib.used ? "추천에 반영됨" : "연결됨 · 반영 안 함") : "연결 안 함"}</span></div>
          {lib?.linked && !lib.used && lib.reason && <p className="tiny" style={{ margin: 0 }}>반영하지 않은 이유: {lib.reason}</p>}
          {session && <details className="acc"><summary>{lib?.linked ? "라이브러리 설정" : "라이브러리 연결하기 (선택)"}</summary>
            <div style={{ marginTop: 10 }}><LibraryPanel session={session} onChange={() => rerun()} /></div></details>}
        </div>
      </div>

      <details className="card" style={{ marginTop: 16, maxWidth: 720 }}>
        <summary style={{ cursor: "pointer", fontWeight: 700 }}>
          추천 조건 · 긍정률 {filters.min_positive}% 이상 · 리뷰 {filters.min_reviews.toLocaleString()}개 이상{filters.korean_only ? " · 한국어만" : ""}
        </summary>
        <div className="stack" style={{ marginTop: 12 }}>
          <FiltersV2Fields value={filters} onChange={setFilters} />
          <button className="btn btn-primary" onClick={() => rerun()} disabled={busy}>{busy ? "다시 고르는 중…" : "이 조건으로 다시 추천"}</button>
          <p className="tiny" style={{ margin: 0 }}>추천 목록에는 처음부터 리뷰 500개 이상·긍정률 75% 이상인 게임만 들어 있어요. 조건을 낮춰도 그보다 낮은 게임은 나오지 않아요.</p>
        </div>
      </details>
      {err && <div className="notice err" style={{ marginTop: 12 }}>{err}</div>}

      {recs.status === "catalog_unavailable" ? (
        <div className="notice warn" style={{ marginTop: 24 }}>지금은 v2 게임 목록을 준비 중이라 추천을 보여드리지 못해요. 유형 결과는 저장됐어요.</div>
      ) : (
        <>
          {c && (
            <p className="small muted" style={{ marginTop: 20 }}>
              전체 후보 {c.catalog_total.toLocaleString()}개 · 현재 조건 통과 {c.passing_filters.toLocaleString()}개
              {c.owned_excluded > 0 && ` · 가진 게임 제외 ${c.owned_excluded}개`}
              {c.strong_avoid_excluded > 0 && ` · 피하고 싶다고 답한 태그로 제외 ${c.strong_avoid_excluded}개`}
              {` · 추천 ${c.recommended}개`}
            </p>
          )}
          {pers.length > 0 && <><div className="section-head"><h2>답변에 맞춘 추천</h2><span className="badge pers">확인된 취향 태그 2개 이상</span></div>
            <div className="games">{pers.map((g) => <GameV2 key={g.appid} g={g} />)}</div></>}
          {refs.length > 0 && <><div className="section-head"><h2>참고 추천</h2><span className="badge ref">판정 근거가 적어 관심 확인용으로 보여드려요</span></div>
            <div className="games">{refs.map((g) => <GameV2 key={g.appid} g={g} />)}</div></>}
          {items.length < 5 && (
            <div className="notice warn" style={{ marginTop: 16 }}>
              {items.length === 0 ? "지금 조건에 맞는 게임이 없어요." : `조건에 맞는 게임이 ${items.length}개뿐이에요.`}
              {" "}최소 리뷰 수·최소 긍정률을 낮추거나 한국어·운영체제 조건, ‘가진 게임 빼기’를 바꾸면 더 볼 수 있어요. 조건은 자동으로 풀지 않아요.
            </div>
          )}
        </>
      )}
      <p className="footer">게임 정보·평가 출처: Steam · v2 미리보기 · 스냅샷 {recs.snapshot_id ?? "-"}</p>
    </main>
  );
}
