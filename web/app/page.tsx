import Link from "next/link";
import TopBar from "@/components/TopBar";

export default function Home() {
  return (
    <main className="shell">
      <TopBar />
      <section className="hero-intro">
        <span className="chip warm">약 5분 · 객관식 20개</span>
        <h1 style={{ marginTop: 14 }}>내가 끌리는 게임,<br />상황 질문으로 찾아봐요</h1>
        <p>게임 용어를 몰라도 괜찮아요. 짧은 상황을 읽고 끌리는 정도만 고르면, 답변과 실제로 맞는 Steam 태그를 근거로 취향 유형과 게임을 보여드려요.</p>
      </section>

      <div className="card stack" style={{ marginTop: 24 }}>
        <h3>이렇게 진행돼요</h3>
        <ol className="steps">
          <li><span><b>상황 질문 20개</b> — 한 화면에 하나씩. 떠올리기 어려우면 ‘잘 모르겠어요’를 골라도 돼요.</span></li>
          <li><span><b>자유 서술 3개 (선택)</b> — 쓰고 싶을 때만. 결과 계산에는 쓰이지 않아요.</span></li>
          <li><span><b>추천 조건 확인</b> — 한국어 지원 게임만 볼지, 이미 해본 게임을 뺄지 정해요.</span></li>
          <li><span><b>유형 카드와 게임 추천</b> — 근거가 된 태그와 답변, 추천 이유를 함께 보여드려요.</span></li>
        </ol>
        <Link href="/test/" className="btn btn-primary btn-block" style={{ marginTop: 20 }}>검사 시작하기</Link>
        <p className="tiny" style={{ textAlign: "center", margin: "8px 0 0" }}>
          중간에 나가도 이 브라우저에서 이어서 할 수 있어요. 로그인은 필요 없어요.
        </p>
      </div>

      <div className="card stack" style={{ marginTop: 16 }}>
        <h3>알아두면 좋아요</h3>
        <ul className="muted small" style={{ margin: 0, paddingLeft: 18 }}>
          <li>오픈 월드·로그라이크·전략·퍼즐·협동 같은 20개 Steam 태그만 판정해요. 모든 장르를 다루지는 않아요.</li>
          <li>근거가 부족하면 억지로 유형을 붙이지 않고 ‘아직 발견 중’으로 솔직하게 알려드려요.</li>
          <li>추천은 미리 검토한 Steam 게임 300개 안에서 골라요. 가격 정보는 다루지 않아요.</li>
        </ul>
      </div>
      <p className="footer">게임 정보 출처: Steam 상점 · 겜BTI는 Valve와 관련이 없어요.</p>
    </main>
  );
}
