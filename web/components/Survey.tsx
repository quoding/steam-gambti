"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { load, remove, save } from "@/lib/storage";
import type { Code, Filters, Questionnaire } from "@/lib/types";
import FilterFields from "./FilterFields";
import GamePicker from "./GamePicker";

export interface SurveyData {
  answers: Record<string, Code>;
  free_texts: Record<string, string>;
  filters: Filters;
  played: { appid: number; name: string }[];
  favorites: { appid: number; name: string }[];
  experiment_consent: boolean;
  started_at: number;
  objective_done_at?: number;
}

const empty = (): SurveyData => ({
  answers: {}, free_texts: {}, filters: { korean_only: false, os: "windows" }, played: [], favorites: [],
  experiment_consent: false, started_at: Date.now(),
});

type Step = { kind: "q"; i: number } | { kind: "f"; i: number } | { kind: "filters" } | { kind: "confirm" };

function parseHash(h: string, nq: number, nf: number): Step | null {
  const m = /^#(q|f)(\d+)$/.exec(h);
  if (m) {
    const i = Number(m[2]) - 1;
    if (m[1] === "q" && i >= 0 && i < nq) return { kind: "q", i };
    if (m[1] === "f" && i >= 0 && i < nf) return { kind: "f", i };
  }
  if (h === "#filters") return { kind: "filters" };
  if (h === "#confirm") return { kind: "confirm" };
  return null;
}
const toHash = (s: Step) => (s.kind === "q" ? `#q${s.i + 1}` : s.kind === "f" ? `#f${s.i + 1}` : `#${s.kind}`);

export default function Survey({
  storageKey, withFavorites = false, onSubmit, submitLabel = "결과 보기",
}: {
  storageKey: string;
  withFavorites?: boolean;
  onSubmit: (d: SurveyData) => Promise<void>;
  submitLabel?: string;
}) {
  const [qn, setQn] = useState<Questionnaire | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [data, setData] = useState<SurveyData>(empty);
  const [step, setStep] = useState<Step>({ kind: "q", i: 0 });
  const [showGrid, setShowGrid] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const advanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const headingRef = useRef<HTMLDivElement>(null);

  const fetchQ = useCallback(async () => {
    setLoadErr(null);
    try { setQn(await api<Questionnaire>("/questionnaire")); } catch (e) { setLoadErr(errorMessage(e)); }
  }, []);

  useEffect(() => {
    fetchQ();
    const saved = load<SurveyData>("session", storageKey);
    if (saved) setData({ ...empty(), ...saved });
  }, [fetchQ, storageKey]);

  useEffect(() => { save("session", storageKey, data); }, [data, storageKey]);

  // URL 해시 = 현재 단계. 브라우저 뒤로 가기로 이전 문항에 돌아간다.
  useEffect(() => {
    if (!qn) return;
    const nq = qn.questions.length, nf = qn.free_texts.length;
    const sync = () => {
      const s = parseHash(window.location.hash, nq, nf);
      if (s) setStep(s);
      else window.history.replaceState(null, "", toHash({ kind: "q", i: 0 }));
    };
    sync();
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, [qn]);

  useEffect(() => { headingRef.current?.focus({ preventScroll: true }); window.scrollTo({ top: 0 }); }, [step]);

  const go = (s: Step) => {
    if (advanceTimer.current) clearTimeout(advanceTimer.current);
    window.history.pushState(null, "", toHash(s));
    setStep(s);
  };

  if (loadErr) {
    return (
      <div className="card stack">
        <div className="notice err">{loadErr}</div>
        <button className="btn btn-primary" onClick={fetchQ}>다시 불러오기</button>
      </div>
    );
  }
  if (!qn) return <div className="card"><div className="skeleton" style={{ height: 320 }} /></div>;

  const nq = qn.questions.length;
  const nf = qn.free_texts.length;
  const answered = qn.questions.filter((q) => data.answers[q.question_id]).length;
  const unanswered = qn.questions.filter((q) => !data.answers[q.question_id]);
  const totalSteps = nq + nf + 2;
  const pos = step.kind === "q" ? step.i : step.kind === "f" ? nq + step.i : step.kind === "filters" ? nq + nf : nq + nf + 1;

  const next = (): Step => {
    if (step.kind === "q") return step.i + 1 < nq ? { kind: "q", i: step.i + 1 } : { kind: "f", i: 0 };
    if (step.kind === "f") return step.i + 1 < nf ? { kind: "f", i: step.i + 1 } : { kind: "filters" };
    return { kind: "confirm" };
  };
  const prev = (): Step | null => {
    if (step.kind === "q") return step.i > 0 ? { kind: "q", i: step.i - 1 } : null;
    if (step.kind === "f") return step.i > 0 ? { kind: "f", i: step.i - 1 } : { kind: "q", i: nq - 1 };
    if (step.kind === "filters") return { kind: "f", i: nf - 1 };
    return { kind: "filters" };
  };

  const choose = (qid: string, code: Code) => {
    setData((d) => {
      const answers = { ...d.answers, [qid]: code };
      const doneAll = qn.questions.every((q) => answers[q.question_id]);
      return { ...d, answers, objective_done_at: doneAll && !d.objective_done_at ? Date.now() : d.objective_done_at };
    });
    if (advanceTimer.current) clearTimeout(advanceTimer.current);
    advanceTimer.current = setTimeout(() => go(next()), 280);
  };

  const doSubmit = async () => {
    setSubmitting(true);
    setSubmitErr(null);
    try {
      await onSubmit(data);
      remove("session", storageKey);
    } catch (e) {
      setSubmitErr(e instanceof Error && e.message.startsWith("ui:") ? e.message.slice(3) : errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  const p = prev();
  const progress = (
    <div className="progress">
      <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={totalSteps} aria-valuenow={pos + 1}>
        <div className="progress-fill" style={{ width: `${((pos + 1) / totalSteps) * 100}%` }} />
      </div>
      <span className="progress-label">{step.kind === "q" ? `${step.i + 1} / ${nq}` : step.kind === "f" ? "선택 서술" : "마무리"}</span>
    </div>
  );

  return (
    <div>
      {progress}
      {step.kind === "q" && (() => {
        const q = qn.questions[step.i];
        const sel = data.answers[q.question_id];
        return (
          <div className="card" key={q.question_id}>
            <div className="row">
              <span className="q-num">질문 {step.i + 1}</span>
              <span className="spacer" />
              <button className="btn-text small" onClick={() => setShowGrid((v) => !v)} aria-expanded={showGrid}>
                답변 현황 {answered}/{nq}
              </button>
            </div>
            {showGrid && (
              <div className="qgrid" style={{ margin: "12px 0" }}>
                {qn.questions.map((qq, i) => {
                  const a = data.answers[qq.question_id];
                  return (
                    <button key={qq.question_id} className={`qcell ${a ? (a === "X" ? "x" : "done") : ""} ${i === step.i ? "cur" : ""}`}
                      aria-label={`질문 ${i + 1}${a ? " 답변함" : " 미답변"}`} onClick={() => { setShowGrid(false); go({ kind: "q", i }); }}>
                      {i + 1}
                    </button>
                  );
                })}
              </div>
            )}
            {step.i === 0 && <p className="notice small" style={{ marginTop: 12 }}>{qn.intro}</p>}
            <div className="q-text" tabIndex={-1} ref={headingRef} id={`qt-${q.question_id}`}>{q.text}</div>
            <div className="options" role="radiogroup" aria-labelledby={`qt-${q.question_id}`}>
              {q.options.map((o) => (
                <button key={o.code} role="radio" aria-checked={sel === o.code}
                  className={`opt ${o.code === "X" ? "unknown" : ""}`} onClick={() => choose(q.question_id, o.code)}>
                  <span className="dot" aria-hidden />
                  <span>{o.label}</span>
                </button>
              ))}
            </div>
            <div className="navrow">
              <button className="btn btn-ghost" disabled={!p} onClick={() => p && go(p)}>이전</button>
              <span className="spacer" />
              <button className={sel ? "btn btn-primary" : "btn btn-text"} onClick={() => go(next())}>
                {sel ? "다음" : "나중에 답할게요"}
              </button>
            </div>
          </div>
        );
      })()}

      {step.kind === "f" && (() => {
        const f = qn.free_texts[step.i];
        const v = data.free_texts[f.free_text_id] ?? "";
        return (
          <div className="card stack" key={f.free_text_id}>
            <div className="row">
              <span className="q-num">선택 서술 {step.i + 1} / {nf}</span>
              <span className="spacer" />
              <button className="btn-text small" onClick={() => go({ kind: "filters" })}>서술 모두 건너뛰기</button>
            </div>
            <div className="q-text" tabIndex={-1} ref={headingRef}>{f.text}</div>
            <textarea value={v} maxLength={f.max_chars} placeholder="쓰고 싶은 만큼만 적어주세요. 비워둬도 괜찮아요."
              onChange={(e) => setData((d) => ({ ...d, free_texts: { ...d.free_texts, [f.free_text_id]: e.target.value } }))} />
            <div className="row tiny"><span>유형·추천 계산에는 쓰이지 않아요. 30일 뒤 지워지고 백업에는 넣지 않아요.</span><span className="spacer" /><span>{v.length} / {f.max_chars}</span></div>
            <div className="navrow">
              <button className="btn btn-ghost" onClick={() => p && go(p)}>이전</button>
              <span className="spacer" />
              <button className={v.trim() ? "btn btn-primary" : "btn btn-ghost"} onClick={() => go(next())}>{v.trim() ? "다음" : "건너뛰기"}</button>
            </div>
          </div>
        );
      })()}

      {step.kind === "filters" && (
        <div className="card stack">
          <div tabIndex={-1} ref={headingRef}><h2>추천 조건 확인</h2></div>
          <p className="muted small" style={{ margin: 0 }}>결과 화면에서도 언제든 바꿀 수 있어요. 바꿔도 유형 카드는 그대로예요.</p>
          <FilterFields value={data.filters} onChange={(filters) => setData((d) => ({ ...d, filters }))} />
          <GamePicker label="이미 해본 게임 (선택 · 추천에서 빼요)" max={20} value={data.played}
            onChange={(played) => setData((d) => ({ ...d, played }))} />
          {withFavorites && (
            <GamePicker label="좋아하는 게임 3개 (평가용 · 추천 계산에는 쓰지 않고 목록에서만 빼요)" max={3} value={data.favorites}
              onChange={(favorites) => setData((d) => ({ ...d, favorites }))} />
          )}
          <div className="toggle" style={{ borderBottom: "none" }}>
            <div>
              <div style={{ fontWeight: 700 }}>연구용 오프라인 분석 동의 (선택)</div>
              <div className="tiny">
                동의하면 저장된 답변과 서술을 나중에 운영자가 별도 분석 도구로 다시 판정해볼 수 있어요. 서술은 30일 뒤 지워지고,
                백업에는 들어가지 않아 복구 대상에서도 빠져요. 동의하지 않아도 결과는 똑같아요.
              </div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={data.experiment_consent} aria-label="연구용 오프라인 분석 동의"
                onChange={(e) => setData((d) => ({ ...d, experiment_consent: e.target.checked }))} />
              <span />
            </label>
          </div>
          <div className="navrow">
            <button className="btn btn-ghost" onClick={() => p && go(p)}>이전</button>
            <span className="spacer" />
            <button className="btn btn-primary" onClick={() => go({ kind: "confirm" })}
              disabled={withFavorites && data.favorites.length !== 3}>다음</button>
          </div>
        </div>
      )}

      {step.kind === "confirm" && (
        <div className="card stack">
          <div tabIndex={-1} ref={headingRef}><h2>제출 전 확인</h2></div>
          {unanswered.length > 0 ? (
            <div className="notice warn">
              <b>{unanswered.length}개 문항</b>에 아직 답하지 않았어요. 이대로 제출하면 이 문항들은
              <b> ‘잘 모르겠어요’</b>로 기록되고, 관련 태그는 판단하지 않은 채로 남아요.
              <div className="chips" style={{ marginTop: 10 }}>
                {unanswered.map((q) => {
                  const i = qn.questions.indexOf(q);
                  return <button key={q.question_id} className="chip soft" style={{ border: "none", cursor: "pointer" }}
                    onClick={() => go({ kind: "q", i })}>질문 {i + 1} 답하기</button>;
                })}
              </div>
            </div>
          ) : (
            <div className="notice">객관식 20개에 모두 답했어요.</div>
          )}
          <div className="small muted">
            서술 {Object.values(data.free_texts).filter((v) => v.trim()).length}개 작성 ·
            {data.filters.korean_only ? " 한국어 지원만" : " 한국어 필터 꺼짐"} ·
            {" "}{{ windows: "Windows", mac: "macOS", linux: "Linux" }[data.filters.os]} ·
            {" "}이미 해본 게임 {data.played.length}개
          </div>
          {submitErr && <div className="notice err">{submitErr}</div>}
          <div className="navrow">
            <button className="btn btn-ghost" onClick={() => p && go(p)}>이전</button>
            <span className="spacer" />
            <button className="btn btn-primary" onClick={doSubmit} disabled={submitting}>
              {submitting ? "계산 중…" : unanswered.length ? `모르겠어요로 기록하고 ${submitLabel}` : submitLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
