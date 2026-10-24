"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { LIB_ERRORS, type V2Library, type V2Session } from "@/lib/v2";

const STATUS_TEXT: Record<string, string> = {
  empty: "공개된 라이브러리에 게임이 없어요.",
  private_or_unavailable: "게임 목록이나 플레이 시간을 볼 수 없어요. 프로필의 게임 정보가 비공개이거나 Steam에서 정보를 주지 않았을 수 있어요.",
};

function hours(m: number | null) {
  if (m === null) return "시간 정보 없음";
  if (m === 0) return "플레이 기록 없음";
  return m < 60 ? `${m}분` : `${Math.round(m / 6) / 10}시간`;
}

export default function LibraryPanel({ session, onChange, compact = false }:
  { session: V2Session; onChange?: () => void; compact?: boolean }) {
  const [lib, setLib] = useState<V2Library | null>(null);
  const [input, setInput] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const changed = useRef(onChange);
  changed.current = onChange;
  const wasLoading = useRef(false);

  // 상태 조회. 부모에는 '불러오기 완료'처럼 실제 변화가 있을 때만 알린다(조회만으로 재추천하지 않음)
  const refresh = useCallback(async () => {
    try {
      const l = await api<V2Library>("/v2/library/status", session);
      setLib(l);
      if (l.status === "loading") {
        wasLoading.current = true;
        timer.current = setTimeout(refresh, 1500);
      } else if (wasLoading.current) {
        wasLoading.current = false;
        changed.current?.();
      }
    } catch (e) { setErr(errorMessage(e)); }
  }, [session]);

  useEffect(() => { refresh(); return () => { if (timer.current) clearTimeout(timer.current); }; }, [refresh]);

  const connect = async () => {
    setErr(null); setBusy(true);
    try {
      await api("/v2/library/link", { ...session, input });
      setLib({ status: "loading" });
      wasLoading.current = true;
      timer.current = setTimeout(refresh, 1000);
    } catch (e) {
      const code = (e as { code?: string }).code ?? "";
      setErr(LIB_ERRORS[code] ?? errorMessage(e));
    } finally { setBusy(false); }
  };

  const settings = async (patch: Record<string, unknown>) => {
    try { setLib(await api<V2Library>("/v2/library/settings", { ...session, ...patch })); changed.current?.(); }
    catch (e) { setErr(errorMessage(e)); }
  };

  const unlink = async () => {
    if (!window.confirm("연결을 해제하면 불러온 게임 목록과 플레이 시간, 취향 계산 결과를 바로 지워요.")) return;
    try { await api("/v2/library/unlink", session); setLib({ status: "none" }); changed.current?.(); }
    catch (e) { setErr(errorMessage(e)); }
  };

  if (!lib) return <div className="skeleton" style={{ height: 80 }} />;

  if (lib.status === "none" || lib.status === "error") {
    return (
      <div className="stack">
        {lib.status === "error" && <div className="notice warn">{LIB_ERRORS[lib.error_code ?? ""] ?? "라이브러리를 불러오지 못했어요."}</div>}
        <label className="small" style={{ fontWeight: 700 }}>Steam 프로필 주소 또는 SteamID64</label>
        <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
          placeholder="https://steamcommunity.com/id/내주소  또는  7656119…" />
        <p className="tiny" style={{ margin: 0 }}>
          공개된 게임 목록과 플레이 시간만 조회해요. 비밀번호·로그인은 받지 않으며, 계정 소유를 확인하는 기능은 아니에요.
          불러온 목록은 마지막 연결 후 7일 뒤 자동 삭제되고, 연결 해제 시 바로 지워져요. 서버 백업에는 넣지 않아요.
          유형 결과는 질문으로만 정하고, 라이브러리는 추천 순서에만 참고해요.
        </p>
        {err && <div className="notice err">{err}</div>}
        <button className="btn btn-ghost" onClick={connect} disabled={busy || input.trim().length < 3}>
          {busy ? "요청 중…" : "라이브러리 불러오기"}
        </button>
      </div>
    );
  }
  if (lib.status === "loading") {
    return <div className="notice">Steam에서 라이브러리를 불러오는 중이에요. 그동안 질문을 계속 진행해도 돼요.</div>;
  }
  if (lib.status !== "ok") {
    return (
      <div className="stack">
        <div className="notice warn">{STATUS_TEXT[lib.status] ?? "라이브러리를 사용할 수 없어요."}</div>
        <button className="btn-text small" onClick={unlink}>연결 기록 지우기</button>
      </div>
    );
  }
  const p = lib.profile;
  return (
    <div className="stack">
      <div className="notice">
        게임 {lib.owned_count}개를 불러왔어요.{" "}
        {p?.enabled
          ? `플레이 시간 상위 ${p.analyzed_count}개 중 ${p.tagged_count}개의 태그로 추천 순서를 보조해요.`
          : `추천 반영 안 함: ${p?.reason ?? "준비 중"}`}
        {lib.meta_status === "partial" && " 일부 게임의 태그를 받지 못했어요."}
      </div>
      <div className="toggle">
        <div><div style={{ fontWeight: 700 }}>라이브러리를 추천에 반영</div><div className="tiny">유형 결과는 바뀌지 않아요.</div></div>
        <label className="switch"><input type="checkbox" aria-label="라이브러리를 추천에 반영" checked={!!lib.use_for_reco}
          onChange={(e) => settings({ use_for_reco: e.target.checked })} /><span /></label>
      </div>
      <div className="toggle" style={{ borderBottom: compact ? "none" : undefined }}>
        <div><div style={{ fontWeight: 700 }}>이미 가진 게임은 추천에서 빼기</div><div className="tiny">플레이하지 않은 게임도 빠져요.</div></div>
        <label className="switch"><input type="checkbox" aria-label="보유 게임 제외" checked={!!lib.exclude_owned}
          onChange={(e) => settings({ exclude_owned: e.target.checked })} /><span /></label>
      </div>
      {!compact && lib.games && (
        <details className="acc">
          <summary>플레이 시간순 게임 목록 · 분석에서 뺄 게임 고르기</summary>
          <div className="picker-list" style={{ maxHeight: 320 }}>
            {lib.games.map((g) => (
              <label key={g.appid} className="picker-item">
                <span className="small">{g.name ?? `AppID ${g.appid}`} <span className="tiny">· {hours(g.minutes)}{g.analyzed ? (g.tagged ? " · 분석됨" : " · 태그 없음") : ""}</span></span>
                <input type="checkbox" aria-label={`${g.name} 분석 제외`} checked={g.analysis_excluded}
                  onChange={(e) => {
                    const cur = (lib.games ?? []).filter((x) => x.analysis_excluded).map((x) => x.appid);
                    settings({ analysis_excluded: e.target.checked ? [...cur, g.appid] : cur.filter((a) => a !== g.appid) });
                  }} />
              </label>
            ))}
          </div>
          <p className="tiny">체크한 게임은 취향 계산에서만 빠지고, ‘가진 게임’으로는 계속 남아요.</p>
        </details>
      )}
      {err && <div className="notice err">{err}</div>}
      <button className="btn-text small" onClick={unlink} style={{ alignSelf: "flex-start" }}>연결 해제하고 기록 지우기</button>
    </div>
  );
}
