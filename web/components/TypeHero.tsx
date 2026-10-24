import type { Card } from "@/lib/types";

export default function TypeHero({ card }: { card: Card }) {
  if (card.is_discovering) {
    return (
      <section className="type-hero discover">
        <span className="hero-glyph" aria-hidden>🧭</span>
        <div className="eyebrow" style={{ color: "var(--accent)" }}>나의 겜BTI</div>
        <h1>{card.name}</h1>
        <div className="tagline">{card.tagline}</div>
        <div style={{ marginTop: 18 }}>
          <div className="small" style={{ fontWeight: 700, marginBottom: 8 }}>지금까지 확인된 취향</div>
          {card.positive_tags.length ? (
            <div className="chips">{card.positive_tags.map((t) => <span key={t.tagid} className="chip">{t.name}</span>)}</div>
          ) : (
            <p className="muted" style={{ margin: 0 }}>{card.empty_text}</p>
          )}
        </div>
        <p className="small muted" style={{ margin: "14px 0 0" }}>
          서로 다른 취향 근거가 3개 이상 모이면 유형이 정해져요. 모르는 항목은 억지로 채우지 않았어요.
        </p>
      </section>
    );
  }
  const [, second, third] = card.main_tags;
  return (
    <section className="type-hero">
      <span className="hero-glyph" aria-hidden>✦</span>
      <div className="eyebrow">나의 겜BTI</div>
      <h1>{card.name}</h1>
      <div className="tagline">{card.tagline}</div>
      {second && (
        <div className="also">추가로 {second.name}{third ? `, ${third.name}` : ""}에도 끌리는 답변을 골랐어요</div>
      )}
      <div className="chips">{card.main_tags.map((t) => <span key={t.tagid} className="chip">{t.name}</span>)}</div>
    </section>
  );
}
