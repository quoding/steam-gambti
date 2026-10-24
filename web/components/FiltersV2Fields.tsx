"use client";
import type { V2Filters } from "@/lib/v2";

export default function FiltersV2Fields({ value, onChange }: { value: V2Filters; onChange: (f: V2Filters) => void }) {
  return (
    <div className="stack">
      <div className="toggle">
        <div><div style={{ fontWeight: 700 }}>한국어 지원 게임만</div><div className="tiny">조건을 몰래 풀지 않아요.</div></div>
        <label className="switch"><input type="checkbox" aria-label="한국어 지원 게임만" checked={value.korean_only}
          onChange={(e) => onChange({ ...value, korean_only: e.target.checked })} /><span /></label>
      </div>
      <div>
        <div style={{ fontWeight: 700 }}>운영체제</div>
        <div className="seg" role="group" aria-label="운영체제" style={{ marginTop: 6 }}>
          {(["windows", "mac", "linux"] as const).map((o) => (
            <button key={o} aria-pressed={value.os === o} onClick={() => onChange({ ...value, os: o })}>
              {{ windows: "Windows", mac: "macOS", linux: "Linux" }[o]}</button>
          ))}
        </div>
      </div>
      <div>
        <div style={{ fontWeight: 700 }}>최소 긍정률</div>
        <div className="seg" role="group" aria-label="최소 긍정률" style={{ marginTop: 6, flexWrap: "wrap" }}>
          {[75, 80, 90, 95].map((p) => (
            <button key={p} aria-pressed={value.min_positive === p} onClick={() => onChange({ ...value, min_positive: p })}>{p}% 이상</button>
          ))}
        </div>
        <p className="tiny" style={{ margin: "6px 0 0" }}>Steam의 ‘압도적으로 긍정적’ 같은 평가 문구는 리뷰 수까지 반영한 Steam 자체 기준이라 95%와 같지 않아요.</p>
      </div>
      <div>
        <div style={{ fontWeight: 700 }}>최소 리뷰 수</div>
        <div className="seg" role="group" aria-label="최소 리뷰 수" style={{ marginTop: 6, flexWrap: "wrap" }}>
          {[500, 1000, 5000, 10000, 50000, 100000].map((n) => (
            <button key={n} aria-pressed={value.min_reviews === n} onClick={() => onChange({ ...value, min_reviews: n })}>
              {n >= 10000 ? `${n / 10000}만` : n.toLocaleString()}개 이상</button>
          ))}
        </div>
        <p className="tiny" style={{ margin: "6px 0 0" }}>리뷰 수는 Steam에서 구매한 사용자의 전체 언어 리뷰 기준이에요. 판매량이나 동시 접속자를 뜻하지 않아요. 기준을 올려도 인기순으로 바뀌지 않고, 그 안에서 취향 순서를 써요.</p>
      </div>
    </div>
  );
}
