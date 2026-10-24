"use client";
import type { Filters } from "@/lib/types";

const OS: { key: Filters["os"]; label: string }[] = [
  { key: "windows", label: "Windows" }, { key: "mac", label: "macOS" }, { key: "linux", label: "Linux" },
];

export default function FilterFields({ value, onChange }: { value: Filters; onChange: (f: Filters) => void }) {
  return (
    <div>
      <div className="toggle">
        <div>
          <div style={{ fontWeight: 700 }}>한국어 지원 게임만 보기</div>
          <div className="tiny">켜면 한국어 지원이 확인된 게임만 추천해요. 후보가 줄어도 조건을 몰래 풀지 않아요.</div>
        </div>
        <label className="switch">
          <input type="checkbox" checked={value.korean_only} aria-label="한국어 지원 게임만 보기"
            onChange={(e) => onChange({ ...value, korean_only: e.target.checked })} />
          <span />
        </label>
      </div>
      <div className="toggle" style={{ borderBottom: "none" }}>
        <div>
          <div style={{ fontWeight: 700 }}>사용하는 컴퓨터</div>
          <div className="tiny">지원이 확인된 게임만 보여드려요.</div>
        </div>
        <div className="seg" role="group" aria-label="운영체제">
          {OS.map((o) => (
            <button key={o.key} aria-pressed={value.os === o.key} onClick={() => onChange({ ...value, os: o.key })}>{o.label}</button>
          ))}
        </div>
      </div>
    </div>
  );
}
