"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import FilterFields from "@/components/FilterFields";
import GameCard from "@/components/GameCard";
import GamePicker from "@/components/GamePicker";
import TopBar from "@/components/TopBar";
import TypeHero from "@/components/TypeHero";
import { api, errorMessage } from "@/lib/api";
import { KEYS, load, remove, save } from "@/lib/storage";
import type { Filters, Recs, ResultPayload } from "@/lib/types";

type Last = { submission_id: string; owner_key: string };

export default function ResultPage() {
  const router = useRouter();
  const [res, setRes] = useState<ResultPayload | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "none" | "error">("loading");
  const [err, setErr] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({ korean_only: false, os: "windows" });
  const [played, setPlayed] = useState<{ appid: number; name: string }[]>([]);
  const [recBusy, setRecBusy] = useState(false);
  const [recErr, setRecErr] = useState<string | null>(null);
  const [rating, setRating] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [fbState, setFbState] = useState<"idle" | "sent" | "error">("idle");
  const last = typeof window === "undefined" ? null : load<Last>("local", KEYS.last);

  const restore = useCallback(async () => {
    setState("loading");
    const cached = load<ResultPayload>("session", KEYS.result);
    if (cached) { apply(cached); return; }
    const l = load<Last>("local", KEYS.last);
    if (!l) { setState("none"); return; }
    try {
      const r = await api<ResultPayload>(`/submissions/${l.submission_id}/view`, { owner_key: l.owner_key });
      save("session", KEYS.result, r);
      apply(r);
    } catch (e) {
      setErr(errorMessage(e));
      setState("error");
    }
  }, []);

  function apply(r: ResultPayload) {
    setRes(r);
    setFilters(r.filters);
    setPlayed((r.played ?? []).map((a) => ({ appid: a, name: `AppID ${a}` })));
    setState("ready");
  }

  useEffect(() => { restore(); }, [restore]);

  const rerun = async () => {
    if (!res || !last) return;
    setRecBusy(true);
    setRecErr(null);
    try {
      const r = await api<{ recommendations: Recs; filters: Filters; played: number[] }>(
        `/submissions/${res.submission_id}/recommendations`,
        { owner_key: last.owner_key, filters, played: played.map((p) => p.appid) });
      const next = { ...res, recommendations: r.recommendations, filters: r.filters, played: r.played };
      setRes(next);
      save("session", KEYS.result, next);
    } catch (e) { setRecErr(errorMessage(e)); } finally { setRecBusy(false); }
  };

  const sendFeedback = async () => {
    if (!res || !last) return;
    try {
      await api(`/submissions/${res.submission_id}/feedback`, { owner_key: last.owner_key, rating, comment });
      setFbState("sent");
    } catch { setFbState("error"); }
  };

  const deleteMine = async () => {
    if (!res || !last || !window.confirm("내 응답과 결과를 서버에서 지울까요? 되돌릴 수 없어요.")) return;
    try {
      await api(`/submissions/${res.submission_id}/delete`, { owner_key: last.owner_key });
      remove("session", KEYS.result);
      remove("local", KEYS.last);
      router.push("/");
    } catch (e) { setRecErr(errorMessage(e)); }
  };

  const retakeWithAnswers = () => {
    if (!res) return;
    const answers = Object.fromEntries(res.card.answers.filter((a) => a.code !== "X").map((a) => [a.question_id, a.code]));
    save("session", KEYS.progress, { answers, free_texts: {}, filters, played, favorites: [], experiment_consent: false, started_at: Date.now() });
    remove("session", KEYS.result);
    router.push("/test/#q1");
  };
  const retakeFresh = () => {
    remove("session", KEYS.progress);
    remove("session", KEYS.result);
    router.push("/test/#q1");
  };

  if (state === "loading") {
    return <main className="shell"><TopBar /><div className="skeleton" style={{ height: 240, borderRadius: 24 }} /></main>;
  }
  if (state === "none") {
    return (
      <main className="shell"><TopBar />
        <div className="card stack">
          <h2>아직 결과가 없어요</h2>
          <p className="muted">이 브라우저에서 완료한 검사가 없어요. 5분이면 끝나요.</p>
          <Link className="btn btn-primary" href="/test/">검사 시작하기</Link>
        </div>
      </main>
    );
  }
  if (state === "error" || !res) {
    return (
      <main className="shell"><TopBar />
        <div className="card stack">
          <div className="notice err">{err}</div>
          <div className="row">
            <button className="btn btn-primary" onClick={restore}>다시 시도</button>
            <button className="btn btn-ghost" onClick={retakeFresh}>새로 검사하기</button>
          </div>
        </div>
      </main>
    );
  }

  const recs = res.recommendations;
  const items = recs.items ?? [];
  const pers = items.filter((i) => i.kind === "personalized");
  const refs = items.filter((i) => i.kind === "reference");
  const card = res.card;

  return (
    <main className="shell wide">
      <TopBar right={<button className="btn-text small" onClick={retakeFresh}>다시 검사</button>} />
      <div style={{ maxWidth: 720 }}>
        <TypeHero card={card} />
        {card.evidence.length > 0 && (
          <div className="card" style={{ marginTop: 16 }}>
            <h3 style={{ marginBottom: 6 }}>이 결과의 근거</h3>
            {card.evidence.map((e) => (
              <details key={e.tagid} className="acc">
                <summary>{e.name}</summary>
                <ul className="small muted" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                  {e.answers.map((a) => <li key={a.question_id}><b>{a.axis}</b> — “{a.answer}”</li>)}
                </ul>
              </details>
            ))}
            {card.extra_social_tags.length > 0 && (
              <p className="small muted" style={{ margin: "12px 0 0" }}>
                추가로 확인된 선호: {card.extra_social_tags.map((t) => t.name).join(", ")}
                <span className="tiny"> (같은 질문에서 나온 선호라 대표 근거에는 하나만 넣었어요)</span>
              </p>
            )}
          </div>
        )}
      </div>

      <details className="card" style={{ marginTop: 16, maxWidth: 720 }}>
        <summary style={{ cursor: "pointer", fontWeight: 700 }}>
          추천 조건 바꾸기 · {filters.korean_only ? "한국어 지원만" : "한국어 필터 꺼짐"} · {{ windows: "Windows", mac: "macOS", linux: "Linux" }[filters.os]} · 제외 {played.length}개
        </summary>
        <div className="stack" style={{ marginTop: 12 }}>
          <FilterFields value={filters} onChange={setFilters} />
          <GamePicker label="이미 해본 게임 빼기" max={20} value={played} onChange={setPlayed} />
          <button className="btn btn-primary" onClick={rerun} disabled={recBusy}>{recBusy ? "다시 고르는 중…" : "이 조건으로 다시 추천"}</button>
          <p className="tiny" style={{ margin: 0 }}>조건을 바꿔도 유형 카드는 바뀌지 않아요.</p>
        </div>
      </details>
      {recErr && <div className="notice err" style={{ marginTop: 12 }}>{recErr}</div>}

      {recs.status === "catalog_unavailable" ? (
        <div className="notice warn" style={{ marginTop: 24 }}>
          지금은 게임 목록을 불러올 수 없어 추천을 보여드리지 못해요. 유형 결과는 정상적으로 저장됐어요. 잠시 뒤 이 페이지를 새로고침해주세요.
        </div>
      ) : (
        <>
          {pers.length > 0 && (
            <>
              <div className="section-head">
                <h2>답변에 맞춘 추천</h2>
                <span className="badge pers">일치 태그 2개 이상</span>
              </div>
              <div className="games">{pers.map((g) => <GameCard key={g.appid} g={g} />)}</div>
            </>
          )}
          {refs.length > 0 && (
            <>
              <div className="section-head">
                <h2>참고 추천</h2>
                <span className="badge ref">판정 근거가 적어 관심 확인용으로 보여드려요</span>
              </div>
              <div className="games">{refs.map((g) => <GameCard key={g.appid} g={g} />)}</div>
            </>
          )}
          {items.length === 0 && (
            <div className="notice warn" style={{ marginTop: 24 }}>
              지금 조건에 맞는 게임이 없어요. 한국어·운영체제 조건과 이미 해본 게임 제외는 그대로 지켰어요. 조건을 바꿔 다시 추천받을 수 있어요.
            </div>
          )}
          {items.length > 0 && (recs.shortfall ?? 0) > 0 && (
            <p className="notice" style={{ marginTop: 16 }}>
              조건에 맞는 게임이 {items.length}개뿐이에요. 한국어·운영체제 조건과 강하게 피하고 싶다고 답한 태그 제외는 풀지 않았어요.
            </p>
          )}
        </>
      )}

      <div style={{ maxWidth: 720 }}>
        <details className="card" style={{ marginTop: 32 }}>
          <summary style={{ cursor: "pointer", fontWeight: 700 }}>내 답변 보기</summary>
          <ol className="small" style={{ margin: "12px 0 0", paddingLeft: 22 }}>
            {card.answers.map((a) => (
              <li key={a.question_id} style={{ marginBottom: 4 }}>
                <span className="muted">{a.axis}</span> — {a.code === "X" ? <i className="muted">{a.answer}</i> : a.answer}
              </li>
            ))}
          </ol>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn btn-ghost" onClick={retakeWithAnswers}>이 답변을 고쳐서 다시 하기</button>
          </div>
        </details>

        <div className="card stack" style={{ marginTop: 16 }}>
          <h3>결과가 어땠나요?</h3>
          {fbState === "sent" ? <p className="muted" style={{ margin: 0 }}>의견 고마워요!</p> : (
            <>
              <div className="stars" role="group" aria-label="만족도">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button key={n} aria-pressed={rating === n} aria-label={`${n}점`} onClick={() => setRating(n)}>{n}</button>
                ))}
              </div>
              <textarea maxLength={500} placeholder="잘 맞았던 점, 어색했던 점을 자유롭게 (선택)" value={comment}
                onChange={(e) => setComment(e.target.value)} style={{ minHeight: 90 }} />
              {fbState === "error" && <div className="notice err">보내지 못했어요. 다시 시도해주세요.</div>}
              <button className="btn btn-primary" onClick={sendFeedback} disabled={!rating && !comment.trim()}>의견 보내기</button>
            </>
          )}
        </div>

        <div className="row" style={{ marginTop: 20, justifyContent: "center" }}>
          <button className="btn btn-ghost" onClick={retakeFresh}>처음부터 다시 답하기</button>
          <button className="btn-text tiny" onClick={deleteMine}>내 응답 삭제</button>
        </div>
        <p className="footer">결과 화면을 캡처해서 공유할 수 있어요. 게임 정보 출처: Steam 상점</p>
      </div>
    </main>
  );
}
