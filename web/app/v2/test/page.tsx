"use client";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import FiltersV2Fields from "@/components/FiltersV2Fields";
import TopBar from "@/components/TopBar";
import { api, errorMessage } from "@/lib/api";
import { load, remove, save } from "@/lib/storage";
import { DEFAULT_FILTERS, ensureSession, V2KEYS, type V2Answers, type V2Filters, type V2Question,
  type V2Route, type V2Session } from "@/lib/v2";

type Step = { kind: "q"; q: V2Question } | { kind: "free" } | { kind: "filters" };
interface Extra { free: Record<string, string>; filters: V2Filters; consent: boolean }
const FREE = [
  { id: "F01", text: "게임이나 비슷한 놀이에서 특히 재미있었던 순간을 적어주세요. 무엇을 하고 있어서 재미있었나요?" },
  { id: "F02", text: "게임을 하다가 그만두고 싶어지는 상황은 무엇인가요? 어떤 점이 부담스러운지도 적어주세요." },
  { id: "F03", text: "같은 활동도 어떤 조건에서는 좋고 어떤 조건에서는 싫은가요? 앞선 선택지로 설명하기 어려웠던 점을 적어주세요." },
];

export default function V2Test() {
  const router = useRouter();
  const [session, setSession] = useState<V2Session | null>(null);
  const [answers, setAnswers] = useState<V2Answers>({});
  const [history, setHistory] = useState<string[]>([]);
  const [qcache, setQcache] = useState<Record<string, V2Question>>({});
  const [step, setStep] = useState<Step | null>(null);
  const [rt, setRt] = useState<V2Route | null>(null);
  const [pick, setPick] = useState<string[]>([]);
  const [extra, setExtra] = useState<Extra>({ free: {}, filters: DEFAULT_FILTERS, consent: false });
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const headRef = useRef<HTMLDivElement>(null);

  const ask = useCallback(async (s: V2Session, a: V2Answers, prevPath?: string[]) => {
    setBusy(true); setErr(null);
    try {
      const r = await api<V2Route>("/v2/route", { ...s, answers: a });
      setRt(r);
      if (prevPath) {
        const changed = prevPath.filter((q) => !r.path.includes(q)).length;
        setNotice(changed ? `앞 답변이 바뀌어 이후 질문 ${changed}개가 다른 질문으로 바뀌었어요.` : null);
      }
      if (r.next) {
        setQcache((c) => { const n = { ...c, [r.next!.question_id]: r.next! }; save("session", "gembti:v2:qcache", n); return n; });
        setHistory((h) => {
          const kept = h.filter((q) => r.path.includes(q) && q !== r.next!.question_id);
          const n = [...kept, r.next!.question_id];
          save("session", V2KEYS.history, n);
          return n;
        });
        const cur = a[r.next.question_id];
        setPick(Array.isArray(cur) ? cur : cur ? [cur] : []);
        setStep({ kind: "q", q: r.next });
        window.history.pushState(null, "", `#${r.next.question_id}`);
      } else {
        setStep({ kind: "free" });
        window.history.pushState(null, "", "#free");
      }
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const s = await ensureSession();
        setSession(s);
        const a = load<V2Answers>("session", V2KEYS.answers) ?? {};
        setAnswers(a);
        setHistory(load<string[]>("session", V2KEYS.history) ?? []);
        setQcache(load<Record<string, V2Question>>("session", "gembti:v2:qcache") ?? {});
        const ex = load<Extra>("session", V2KEYS.extra);
        if (ex) setExtra(ex);
        await ask(s, a);
      } catch (e) { setErr(errorMessage(e)); }
    })();
  }, [ask]);

  useEffect(() => { save("session", V2KEYS.extra, extra); }, [extra]);
  useEffect(() => { headRef.current?.focus({ preventScroll: true }); window.scrollTo({ top: 0 }); }, [step]);

  // 브라우저 뒤로 가기: 이전 문항을 다시 보여주고 답을 고칠 수 있게 한다
  useEffect(() => {
    const onPop = () => {
      const h = window.location.hash.slice(1);
      if (h === "free") { setStep({ kind: "free" }); return; }
      if (h === "filters") { setStep({ kind: "filters" }); return; }
      const q = qcache[h];
      if (q) {
        const cur = answers[h];
        setPick(Array.isArray(cur) ? cur : cur ? [cur] : []);
        setStep({ kind: "q", q });
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [qcache, answers]);

  const commit = (qid: string, value: string | string[]) => {
    if (!session) return;
    const a = { ...answers, [qid]: value };
    setAnswers(a);
    save("session", V2KEYS.answers, a);
    ask(session, a, rt?.path);
  };

  const goBack = () => {
    if (!step) return;
    if (step.kind === "filters") { setStep({ kind: "free" }); return; }
    const curId = step.kind === "q" ? step.q.question_id : null;
    const idx = curId ? history.indexOf(curId) : history.length;
    const prev = history[idx - 1];
    if (prev && qcache[prev]) {
      const cur = answers[prev];
      setPick(Array.isArray(cur) ? cur : cur ? [cur] : []);
      setStep({ kind: "q", q: qcache[prev] });
    }
  };

  const submit = async () => {
    if (!session) return;
    setBusy(true); setErr(null);
    try {
      const r = await api<{ submission_id: string }>("/v2/submit", {
        ...session, answers, experiment_consent: extra.consent, filters: extra.filters,
        free_texts: Object.fromEntries(Object.entries(extra.free).filter(([, v]) => v.trim())),
      });
      save("session", V2KEYS.result, r);
      save("local", "gembti:v2:last", r.submission_id);
      remove("session", V2KEYS.answers); remove("session", V2KEYS.history); remove("session", V2KEYS.extra);
      router.push("/v2/result/");
    } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  };

  if (!step) {
    return <main className="shell"><TopBar />{err ? <div className="notice err">{err}</div> : <div className="skeleton" style={{ height: 320 }} />}</main>;
  }
  const answered = rt?.answered ?? 0;
  const total = Math.max(rt?.path_length ?? 20, 20);
  const pct = step.kind === "q" ? Math.min(100, (answered / total) * 100) : 100;

  return (
    <main className="shell">
      <TopBar right={<span className="chip soft">v2</span>} />
      <div className="progress">
        <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={answered}>
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="progress-label">{step.kind === "q" ? `${answered + 1} / ${total}` : "마무리"}</span>
      </div>
      {notice && <div className="notice small" style={{ marginBottom: 12 }}>{notice}</div>}
      {err && <div className="notice err" style={{ marginBottom: 12 }}>{err}</div>}

      {step.kind === "q" && (() => {
        const q = step.q;
        const explore = q.stage === "explore";
        const excl = q.exclusive_codes ?? [];
        const toggle = (code: string) => {
          if (!explore) { setPick([code]); commit(q.question_id, code); return; }
          setPick((p) => {
            if (excl.includes(code)) return p.includes(code) ? [] : [code];
            const base = p.filter((c) => !excl.includes(c));
            if (base.includes(code)) return base.filter((c) => c !== code);
            return base.length >= (q.max_select ?? 2) ? base : [...base, code];
          });
        };
        return (
          <div className="card" key={q.question_id}>
            <div className="row">
              <span className="q-num">{explore ? "끌리는 쪽 찾기" : "취향 확인"}</span>
              <span className="spacer" />
              {explore && <span className="tiny">최대 {q.max_select ?? 2}개</span>}
            </div>
            <div className="q-text" tabIndex={-1} ref={headRef} id={`qt-${q.question_id}`}>{q.text}</div>
            <div className="options" role={explore ? "group" : "radiogroup"} aria-labelledby={`qt-${q.question_id}`}>
              {q.options.map((o) => (
                <button key={o.code} role={explore ? "checkbox" : "radio"} aria-checked={pick.includes(o.code)}
                  className={`opt ${["X", "N"].includes(o.code) ? "unknown" : ""}`} onClick={() => toggle(o.code)} disabled={busy}>
                  <span className="dot" aria-hidden style={explore ? { borderRadius: 6 } : undefined} />
                  <span>{o.label}</span>
                </button>
              ))}
            </div>
            <div className="navrow">
              <button className="btn btn-ghost" onClick={goBack} disabled={history.indexOf(q.question_id) <= 0 || busy}>이전</button>
              <span className="spacer" />
              {explore && (
                <button className="btn btn-primary" disabled={!pick.length || busy} onClick={() => commit(q.question_id, pick)}>다음</button>
              )}
            </div>
          </div>
        );
      })()}

      {step.kind === "free" && (
        <div className="card stack">
          <div tabIndex={-1} ref={headRef}><h2>더 하고 싶은 말이 있나요? (선택)</h2></div>
          <p className="tiny" style={{ margin: 0 }}>객관식 개수에 들어가지 않고 결과 계산에도 쓰지 않아요. 30일 뒤 지워지며 백업에도 넣지 않아요.</p>
          {FREE.map((f) => (
            <div key={f.id}>
              <label className="small" style={{ fontWeight: 700 }}>{f.text}</label>
              <textarea maxLength={300} value={extra.free[f.id] ?? ""} style={{ minHeight: 80, marginTop: 6 }}
                onChange={(e) => setExtra((x) => ({ ...x, free: { ...x.free, [f.id]: e.target.value } }))} />
            </div>
          ))}
          <div className="navrow">
            <button className="btn btn-ghost" onClick={goBack}>이전</button>
            <span className="spacer" />
            <button className="btn btn-primary" onClick={() => { setStep({ kind: "filters" }); window.history.pushState(null, "", "#filters"); }}>
              {Object.values(extra.free).some((v) => v.trim()) ? "다음" : "건너뛰기"}
            </button>
          </div>
        </div>
      )}

      {step.kind === "filters" && (
        <div className="card stack">
          <div tabIndex={-1} ref={headRef}><h2>추천 조건</h2></div>
          <p className="muted small" style={{ margin: 0 }}>결과 화면에서도 바꿀 수 있어요. 바꿔도 유형은 그대로예요.</p>
          <FiltersV2Fields value={extra.filters} onChange={(filters) => setExtra((x) => ({ ...x, filters }))} />
          <div className="toggle" style={{ borderBottom: "none" }}>
            <div>
              <div style={{ fontWeight: 700 }}>연구용 오프라인 분석 동의 (선택)</div>
              <div className="tiny">답변과 서술을 운영자가 별도 도구로 다시 분석할 수 있어요. 라이브러리 정보는 포함하지 않아요.</div>
            </div>
            <label className="switch"><input type="checkbox" aria-label="연구용 분석 동의" checked={extra.consent}
              onChange={(e) => setExtra((x) => ({ ...x, consent: e.target.checked }))} /><span /></label>
          </div>
          <div className="navrow">
            <button className="btn btn-ghost" onClick={goBack}>이전</button>
            <span className="spacer" />
            <button className="btn btn-primary" onClick={submit} disabled={busy}>{busy ? "계산 중…" : "결과 보기"}</button>
          </div>
        </div>
      )}
    </main>
  );
}
