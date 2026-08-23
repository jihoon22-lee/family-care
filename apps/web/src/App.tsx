import "./styles.css";

const decisionStates = [
  { label: "MATCH", description: "확인된 조건이 일치함" },
  { label: "UNKNOWN", description: "추가 근거가 필요함" },
  { label: "NO_MATCH", description: "확인된 조건이 불일치함" },
] as const;

export function App() {
  return (
    <div className="app-shell">
      <header className="masthead">
        <a className="wordmark" href="#main-content" aria-label="FamilyCare 홈">
          <span className="wordmark-mark" aria-hidden="true">
            FC
          </span>
          <span>FamilyCare</span>
        </a>
        <span className="phase-badge">Foundation · 기반 구축 중</span>
      </header>

      <main id="main-content" className="foundation-layout">
        <aside className="evidence-rail" aria-label="판정 상태 원칙">
          <div className="compass" aria-hidden="true">
            <span className="compass-ring" />
            <span className="compass-needle" />
            <span className="compass-center" />
          </div>

          <ol className="decision-states">
            {decisionStates.map((state) => (
              <li
                key={state.label}
                className={`decision-state state-${state.label.toLowerCase()}`}
              >
                <span className="state-node" aria-hidden="true" />
                <span className="state-copy">
                  <strong>{state.label}</strong>
                  <small>{state.description}</small>
                </span>
              </li>
            ))}
          </ol>
        </aside>

        <section className="hero" aria-labelledby="product-title">
          <p className="eyebrow">우리 가족의 보험을 근거로 확인하는 방법</p>
          <h1 id="product-title">FamilyCare</h1>
          <p className="hero-lead">
            가입한 담보를 먼저 확인하고, 약관의 정확한 페이지와 미확인 조건을
            함께 보여주는 가족용 보험 안내 도구입니다.
          </p>

          <div
            className="boundary-note"
            role="note"
            aria-label="서비스 이용 원칙"
          >
            <span className="boundary-index">원칙 01</span>
            <div>
              <h2>근거가 없으면 확정하지 않습니다.</h2>
              <p>
                FamilyCare는 보험금 지급을 보장하지 않습니다. 실제 계약 상태와
                보험사의 심사를 다시 확인할 수 있도록 증권·약관 근거와 부족한
                정보를 나란히 제시합니다.
              </p>
            </div>
          </div>

          <dl className="foundation-facts">
            <div>
              <dt>현재 단계</dt>
              <dd>안전한 개발 기반과 검증 자동화</dd>
            </div>
            <div>
              <dt>공개 저장소</dt>
              <dd>합성 데이터만 허용</dd>
            </div>
            <div>
              <dt>외부 연결</dt>
              <dd>Drive·AI·운영 배포 제외</dd>
            </div>
          </dl>
        </section>
      </main>

      <footer className="page-footer">
        <span>FamilyCare / 2026</span>
        <span>Evidence before conclusions.</span>
      </footer>
    </div>
  );
}
