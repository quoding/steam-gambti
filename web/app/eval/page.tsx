"use client";
import { useState } from "react";
import GameCard from "@/components/GameCard";
import Survey, { type SurveyData } from "@/components/Survey";
import TopBar from "@/components/TopBar";
import TypeHero from "@/components/TypeHero";
import { api, errorMessage } from "@/lib/api";
import { KEYS, remove } from "@/lib/storage";
import type { Card, Game } from "@/lib/types";

type Rating = "interested" | "neutral" | "not_interested";
interface Lists { submission_id: string; list_a: Game[]; list_b: Game[] }
interface Reveal { fixed_list: "A" | "B"; card: Card; fixed_items: Game[] }

const R: { k: Rating; label: string }[] = [
  { k: "interested", label: "관심 있음" }, { k: "neutral", label: "보통" }, { k: "not_interested", label: "관심 없음" },
];

export default function EvalPage() {
  const [code, setCode] = useState("");
  const [phase, setPhase] = useState<"code" | "survey" | "rate" | "done">("code");
  const [err, setErr] = useState<string | null>(null);
  const [lists, setLists] = useState<Lists | null>(null);
  const [ratings, setRatings] = useState<Record<number, Rating>>({});
  const [playedLate, setPlayedLate] = useState<number[]>([]);
  const [awkward, setAwkward] = useState<Record<number, string>>({});
  const [pref, setPref] = useState<"A" | "B" | "tie" | null>(null);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [excluded, setExcluded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setErr(null);
    try {
      await api("/eval/start", { code: code.trim().toUpperCase() });
      setPhase("survey");
    } catch (e) { setErr(e instanceof Error && "status" in e && (e as { status: number }).status === 404 ? "참가 코드를 확인해주세요." : errorMessage(e)); }
  };

  const submit = async (d: SurveyData) => {
    if (d.favorites.length !== 3) throw new Error("ui:좋아하는 게임 3개를 골라주세요.");
    const objective = d.objective_done_at ? (d.objective_done_at - d.started_at) / 1000 : null;
    const r = await api<Lists>("/eval/submit", {
      code: code.trim().toUpperCase(), answers: d.answers,
      free_texts: Object.fromEntries(Object.entries(d.free_texts).filter(([, v]) => v.trim())),
      experiment_consent: d.experiment_consent, filters: d.filters, played: d.played.map((p) => p.appid),
      favorites: d.favorites.map((f) => f.appid), objective_seconds: objective,
    });
    remove("session", KEYS.eval);
    setLists(r);
    setPhase("rate");
    window.scrollTo({ top: 0 });
  };

  const allGames = lists ? Array.from(new Map([...lists.list_a, ...lists.list_b].map((g) => [g.appid, g])).values()) : [];
  const complete = lists && allGames.every((g) => ratings[g.appid]) && pref;

  const sendRatings = async () => {
    if (!lists || !pref) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ excluded_reason: string | null; reveal: Reveal }>("/eval/rate", {
        code: code.trim().toUpperCase(), submission_id: lists.submission_id,
        ratings: Object.fromEntries(Object.entries(ratings)), preference: pref, played_late: playedLate,
        awkward: Object.fromEntries(Object.entries(awkward)),
      });
      setReveal(r.reveal);
      setExcluded(r.excluded_reason);
      setPhase("done");
      window.scrollTo({ top: 0 });
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  const listBlock = (label: "A" | "B", games: Game[]) => (
    <section style={{ marginTop: 24 }}>
      <div className="section-head"><h2>목록 {label}</h2><span className="tiny">{games.length}개</span></div>
      {games.length < 5 && <p className="notice small">이 목록은 조건에 맞는 게임이 {games.length}개뿐이에요.</p>}
      <div className="games">
        {games.map((g) => (
          <div key={`${label}-${g.appid}`} className="stack">
            <GameCard g={g} showReason={false} />
            <div className="rate3" role="group" aria-label={`${g.name} 관심도`}>
              {R.map((r) => (
                <button key={r.k} aria-pressed={ratings[g.appid] === r.k}
                  onClick={() => setRatings((x) => ({ ...x, [g.appid]: r.k }))}>{r.label}</button>
              ))}
            </div>
            <label className="tiny row" style={{ gap: 6 }}>
              <input type="checkbox" checked={playedLate.includes(g.appid)}
                onChange={(e) => setPlayedLate((p) => e.target.checked ? [...p, g.appid] : p.filter((a) => a !== g.appid))} />
              이미 해본 게임이에요
            </label>
            <label className="tiny row" style={{ gap: 6 }}>
              <input type="checkbox" checked={g.appid in awkward}
                onChange={(e) => setAwkward((x) => {
                  const n = { ...x };
                  if (e.target.checked) n[g.appid] = ""; else delete n[g.appid];
                  return n;
                })} />
              이 추천이 어색해요
            </label>
            {g.appid in awkward && (
              <input type="text" maxLength={200} placeholder="어떤 점이 어색한가요? (선택)" value={awkward[g.appid]}
                onChange={(e) => setAwkward((x) => ({ ...x, [g.appid]: e.target.value }))} />
            )}
          </div>
        ))}
      </div>
    </section>
  );

  return (
    <main className={`shell ${phase === "rate" || phase === "done" ? "wide" : ""}`}>
      <TopBar right={<span className="chip soft">평가 참여</span>} />
      {phase === "code" && (
        <div className="card stack">
          <h2>추천 비교 평가에 참여해요</h2>
          <p className="muted" style={{ margin: 0 }}>
            검사를 마치면 서로 다른 방식으로 고른 게임 목록 두 개를 보여드려요. 어떤 방식인지 모르는 상태에서 게임마다 관심도를 고르고,
            더 마음에 드는 목록을 골라주세요. 방식과 유형 결과는 평가 뒤에 공개돼요.
          </p>
          <input type="text" placeholder="참가 코드" value={code} onChange={(e) => setCode(e.target.value)} autoCapitalize="characters" />
          {err && <div className="notice err">{err}</div>}
          <button className="btn btn-primary" onClick={start} disabled={code.trim().length < 4}>시작하기</button>
        </div>
      )}
      {phase === "survey" && <Survey storageKey={KEYS.eval} withFavorites onSubmit={submit} submitLabel="목록 보기" />}
      {phase === "rate" && lists && (
        <>
          <div className="card" style={{ maxWidth: 720 }}>
            <h2>두 목록을 평가해주세요</h2>
            <p className="muted small" style={{ margin: "8px 0 0" }}>
              두 목록에 같은 게임이 있을 수 있어요. 같은 게임은 같은 관심도로 한 번만 고르면 돼요. 이미 해본 게임이 있다면 표시해주세요.
            </p>
          </div>
          {listBlock("A", lists.list_a)}
          {listBlock("B", lists.list_b)}
          <div className="card stack" style={{ marginTop: 24, maxWidth: 720 }}>
            <h3>전체적으로 어느 목록이 더 마음에 드나요?</h3>
            <div className="rate3">
              {(["A", "B", "tie"] as const).map((p) => (
                <button key={p} aria-pressed={pref === p} onClick={() => setPref(p)}>{p === "tie" ? "비슷해요" : `목록 ${p}`}</button>
              ))}
            </div>
            {err && <div className="notice err">{err}</div>}
            <button className="btn btn-primary" disabled={!complete || busy} onClick={sendRatings}>
              {complete ? "평가 제출" : "모든 게임과 목록 선호를 골라주세요"}
            </button>
          </div>
        </>
      )}
      {phase === "done" && reveal && (
        <div className="stack" style={{ maxWidth: 720 }}>
          <div className="notice">
            참여해주셔서 고마워요! 목록 <b>{reveal.fixed_list}</b>가 답변 기반 추천, 다른 목록은 리뷰가 많은 인기작 순서였어요.
            {excluded === "played_late" && " 이미 해본 게임이 있어 이번 비교는 집계에서 빠져요."}
          </div>
          <TypeHero card={reveal.card} />
          <div className="section-head"><h2>답변 기반 추천의 이유</h2></div>
          <div className="games">{reveal.fixed_items.map((g) => <GameCard key={g.appid} g={g} />)}</div>
        </div>
      )}
    </main>
  );
}
