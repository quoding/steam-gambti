"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Hit { appid: number; name: string }

/** 카탈로그 이름 검색 + AppID 직접 입력. 선택한 게임은 제외 조건에만 쓰인다. */
export default function GamePicker({
  value, onChange, max, label, allowAppId = true,
}: { value: Hit[]; onChange: (v: Hit[]) => void; max: number; label: string; allowAppId?: boolean }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "error" | "unavailable">("idle");

  useEffect(() => {
    const term = q.trim();
    if (!term || /^\d+$/.test(term)) { setHits([]); return; }
    setState("loading");
    const t = setTimeout(async () => {
      try {
        const r = await api<{ status: string; items: Hit[] }>(`/catalog/search?q=${encodeURIComponent(term)}`);
        setHits(r.items);
        setState(r.status === "ok" ? "idle" : "unavailable");
      } catch { setState("error"); }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  const add = (h: Hit) => {
    if (value.length >= max || value.some((v) => v.appid === h.appid)) return;
    onChange([...value, h]);
    setQ("");
  };
  const appid = /^\d{1,8}$/.test(q.trim()) ? Number(q.trim()) : null;

  return (
    <div>
      <label className="small" style={{ fontWeight: 700 }}>{label}</label>
      <input type="search" placeholder={allowAppId ? "게임 이름 또는 Steam AppID" : "게임 이름"} value={q}
        onChange={(e) => setQ(e.target.value)} disabled={value.length >= max} style={{ marginTop: 6 }} />
      {state === "error" && <p className="tiny">검색에 실패했어요. 잠시 뒤 다시 입력해주세요.</p>}
      {state === "unavailable" && <p className="tiny">게임 목록을 준비 중이라 검색할 수 없어요.</p>}
      <div className="picker-list">
        {allowAppId && appid !== null && (
          <div className="picker-item">
            <span className="small">AppID {appid} 직접 추가</span>
            <button className="btn btn-ghost" style={{ minHeight: 36 }} onClick={() => add({ appid, name: `AppID ${appid}` })}>추가</button>
          </div>
        )}
        {hits.map((h) => (
          <div className="picker-item" key={h.appid}>
            <span className="small">{h.name}</span>
            <button className="btn btn-ghost" style={{ minHeight: 36 }} onClick={() => add(h)}
              disabled={value.some((v) => v.appid === h.appid)}>추가</button>
          </div>
        ))}
        {q.trim() && !appid && state === "idle" && hits.length === 0 && (
          <p className="tiny">추천 목록(300개) 안에서 찾지 못했어요.{allowAppId ? " Steam AppID 숫자로도 추가할 수 있어요." : ""}</p>
        )}
      </div>
      {value.length > 0 && (
        <div className="chips" style={{ marginTop: 10 }}>
          {value.map((v) => (
            <span className="chip soft" key={v.appid}>
              {v.name}
              <button aria-label={`${v.name} 빼기`} onClick={() => onChange(value.filter((x) => x.appid !== v.appid))}
                style={{ border: "none", background: "none", cursor: "pointer", fontWeight: 800, color: "inherit" }}>×</button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
