"use client";
import { useState } from "react";
import type { Game } from "@/lib/types";

const LANG = { true: "한국어 지원", false: "한국어 미지원", unknown: "한국어 지원 확인 안 됨" } as const;

export default function GameCard({ g, showReason = true }: { g: Game; showReason?: boolean }) {
  const [imgOk, setImgOk] = useState(Boolean(g.image));
  const ref = g.kind === "reference";
  return (
    <article className={`card game ${ref && showReason ? "ref" : ""}`}>
      <div className="game-img">
        {imgOk && g.image ? (
          // 외부 Steam CDN 이미지. 실패하면 자리표시자로 바꾼다.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={g.image} alt="" loading="lazy" onError={() => setImgOk(false)} />
        ) : (
          <div className="ph" aria-hidden>{g.name}</div>
        )}
      </div>
      <div className="game-body">
        <div className="game-title">{g.name}</div>
        <div className="chips">
          {g.tags.slice(0, 5).map((t) => (
            <span key={t} className={`chip ${g.matched_tags?.includes(t) && showReason ? "" : "soft"}`} style={{ fontSize: 13, padding: "4px 10px" }}>{t}</span>
          ))}
        </div>
        {g.short_description ? (
          <p className="game-desc" style={{ margin: 0 }}>
            {g.short_description}
            {!g.description_is_korean && <span className="tiny"> (상점 소개 원문)</span>}
          </p>
        ) : (
          <p className="game-desc tiny" style={{ margin: 0 }}>상점 소개가 아직 없어요.</p>
        )}
        {showReason && g.reason && <div className="reason">{g.reason}</div>}
        <div className="game-foot">
          <span className={`lang ${g.korean}`}>{LANG[g.korean]}</span>
          <a className="btn btn-ghost" style={{ minHeight: 40, padding: "0 16px", fontSize: 14 }} href={g.store_url} target="_blank" rel="noopener noreferrer">
            Steam 상점에서 보기 ↗
          </a>
        </div>
      </div>
    </article>
  );
}
