"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import LibraryPanel from "@/components/LibraryPanel";
import TopBar from "@/components/TopBar";
import { errorMessage } from "@/lib/api";
import { ensureSession, type V2Session } from "@/lib/v2";

export default function V2Home() {
  const [session, setSession] = useState<V2Session | null>(null);
  const [showLib, setShowLib] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { ensureSession().then(setSession).catch((e) => setErr(errorMessage(e))); }, []);

  return (
    <main className="shell">
      <TopBar right={<span className="chip soft">v2 미리보기</span>} />
      <section className="hero-intro">
        <span className="chip warm">질문 20~24개 · 약 6분</span>
        <h1 style={{ marginTop: 14 }}>끌리는 장면부터 고르면,<br />질문이 나에게 맞춰져요</h1>
        <p>처음 12문항으로 관심 있는 쪽을 찾고, 그중 몇 가지를 서로 다른 상황으로 다시 확인해요. 근거가 확인된 취향만 결과에 써요.</p>
      </section>
      {err && <div className="notice err">{err}</div>}
      <div className="card stack" style={{ marginTop: 20 }}>
        <Link className="btn btn-primary btn-block" href="/v2/test/">질문만으로 시작하기</Link>
        <button className="btn btn-ghost btn-block" onClick={() => setShowLib((v) => !v)} aria-expanded={showLib}>
          {showLib ? "라이브러리 연결 닫기" : "Steam 라이브러리도 연결하기 (선택)"}
        </button>
        {showLib && session && <LibraryPanel session={session} compact />}
        <p className="tiny" style={{ margin: 0 }}>
          라이브러리는 추천 순서와 ‘이미 가진 게임 빼기’에만 쓰여요. 연결하지 않아도 모든 기능을 쓸 수 있어요.
        </p>
      </div>
      <div className="card stack" style={{ marginTop: 16 }}>
        <h3>v1과 달라진 점</h3>
        <ul className="muted small" style={{ margin: 0, paddingLeft: 18 }}>
          <li>슈팅·공포·리듬·레이싱·플랫폼까지 16개 영역, 25개 Steam 태그를 다뤄요.</li>
          <li>추천 게임마다 Steam 평가(Steam 구매 기준·전체 언어의 긍정률·리뷰 수)를 보여주고, 최소 긍정률과 리뷰 수로 거를 수 있어요.</li>
          <li>모든 태그를 다 묻지는 않아요. 주요 취향 몇 가지를 근거와 함께 찾는 것이 목표예요.</li>
        </ul>
        <p className="tiny" style={{ margin: 0 }}>기존 검사는 <Link href="/">처음 화면</Link>에서 그대로 할 수 있어요.</p>
      </div>
    </main>
  );
}
