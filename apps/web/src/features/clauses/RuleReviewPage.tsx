import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiError } from "../../api/errors";
import type { PolicyReviewItem } from "../../api/generated";
import { useQueryCache, useResource } from "../../api/query-cache";
import {
  listRuleReviewItems,
  type RuleReviewDomain,
  type RuleReviewStatus,
} from "../../api/rules";
import { CoverageRuleReviewDialog } from "./CoverageRuleReviewDialog";
import { RiderClauseReviewDialog } from "./RiderClauseReviewDialog";

const ISSUE_COPY = {
  COMMON_SPECIAL_TERMS_CONFLICT:
    "공통 약관과 특별 약관의 적용 범위를 다시 확인해야 합니다.",
  CONFLICTING_EVIDENCE: "서로 다른 근거가 있어 하나를 선택할 수 없습니다.",
  INVALID_DATE: "날짜의 앞뒤 관계를 다시 확인해야 합니다.",
  INVALID_UNIT: "금액 또는 단위를 다시 확인해야 합니다.",
  LOW_CONFIDENCE: "후보 값과 근거의 일치 여부를 확인해 주세요.",
  MISSING_EVIDENCE: "판단을 뒷받침할 증권 또는 약관 근거가 필요합니다.",
  STALE_EVIDENCE: "문서 판본이 바뀌어 최신 근거를 다시 연결해야 합니다.",
  TERMS_ONLY_RIDER: "약관에만 있는 담보는 실제 가입 담보로 보지 않습니다.",
  UNSUPPORTED_DSL:
    "현재 지원하지 않는 규칙 구조라 자동 판정에 사용하지 않습니다.",
  UNSUPPORTED_STRUCTURE: "자동으로 구조화하기 어려운 항목입니다.",
  WRONG_EDITION: "계약일에 적용되는 약관 판본인지 다시 확인해야 합니다.",
} as const;

function errorCopy(error: ApiError | undefined): string | undefined {
  if (!error) return undefined;
  return error.code === "AUTHENTICATION_REQUIRED"
    ? "로그인이 필요합니다. 인증을 확인한 뒤 다시 열어 주세요."
    : "검토 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
}

function evidenceComplete(item: PolicyReviewItem): boolean {
  const ids = new Set(item.evidence.map((evidence) => evidence.evidence_id));
  return (
    ids.size > 0 &&
    item.fields.every(
      (field) =>
        field.evidence_ids.length > 0 &&
        field.evidence_ids.every((id) => ids.has(id)),
    )
  );
}

function ReviewColumn({
  domain,
  title,
  eyebrow,
  onOpen,
}: {
  domain: RuleReviewDomain;
  title: string;
  eyebrow: string;
  onOpen: (item: PolicyReviewItem, opener: HTMLButtonElement) => void;
}) {
  const resource = useResource(`rule-review:${domain}`, async (signal) => {
    const statuses: RuleReviewStatus[] = [
      "NEEDS_REVIEW",
      "AI_VERIFIED",
      "USER_CONFIRMED",
    ];
    const responses = await Promise.all(
      statuses.map((status) => listRuleReviewItems(domain, status, signal)),
    );
    return [
      ...new Map(
        responses.flat().map((item) => [item.review_item_id, item]),
      ).values(),
    ];
  });
  const items = resource.data ?? [];
  return (
    <section
      className={`rule-review-column rule-review-${domain}`}
      aria-labelledby={`${domain}-title`}
    >
      <header>
        <div>
          <span>{eyebrow}</span>
          <h2 id={`${domain}-title`}>{title}</h2>
        </div>
        <strong aria-label={`${title} 검토 필요 ${items.length}건`}>
          {items.length}
        </strong>
      </header>
      {resource.loading && !resource.data ? (
        <p role="status">검토 항목을 불러오는 중입니다.</p>
      ) : null}
      {resource.error ? <p role="alert">{errorCopy(resource.error)}</p> : null}
      {!resource.loading && !resource.error && items.length === 0 ? (
        <p className="rule-review-empty">지금 추가로 확인할 항목이 없습니다.</p>
      ) : null}
      <ol className="rule-review-list">
        {items.map((item) => {
          const issue = item.issues[0];
          const complete = evidenceComplete(item);
          return (
            <li key={item.review_item_id}>
              <div className="rule-review-card-head">
                <code>{issue?.code ?? "LOW_CONFIDENCE"}</code>
                <span
                  className={complete ? "evidence-ready" : "evidence-missing"}
                >
                  {complete ? "근거 연결됨" : "근거 필요"}
                </span>
              </div>
              <small>{item.status}</small>
              <p>{ISSUE_COPY[issue?.code ?? "LOW_CONFIDENCE"]}</p>
              <button
                type="button"
                className="review-button"
                onClick={(event) => onOpen(item, event.currentTarget)}
              >
                {domain === "rider_clause" ? "연결 검토" : "규칙 검토"}
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function RuleReviewPage() {
  const [openItem, setOpenItem] = useState<PolicyReviewItem>();
  const openerRef = useRef<HTMLButtonElement | undefined>(undefined);
  const cache = useQueryCache();

  useEffect(() => {
    if (!openItem) openerRef.current?.focus();
  }, [openItem]);

  const open = useCallback(
    (item: PolicyReviewItem, opener: HTMLButtonElement) => {
      openerRef.current = opener;
      setOpenItem(item);
    },
    [],
  );

  const mutated = useCallback(() => {
    if (openItem) cache.invalidate(`rule-review:${openItem.candidate_kind}`);
    setOpenItem(undefined);
  }, [cache, openItem]);

  return (
    <main id="main-content" className="rule-review-page" tabIndex={-1}>
      <section
        className="rule-review-intro"
        aria-labelledby="rule-review-title"
      >
        <div>
          <p className="eyebrow">가입 담보에서 판정 규칙까지 이어지는 근거선</p>
          <h1 id="rule-review-title">보장 규칙 검토</h1>
          <p>
            추가 확인이 필요하거나 게시 준비가 끝난 연결을 모아 보여줍니다.
            왼쪽에서 실제 가입 담보와 약관 조항을 연결하고, 오른쪽에서 구조화된
            규칙을 확인합니다.
          </p>
        </div>
        <aside className="rule-review-boundary" aria-label="자동 판정 경계">
          <span>AI_VERIFIED</span>
          <strong>검증된 저장 버전만 다음 단계로 게시할 수 있습니다.</strong>
          <small>
            지원하지 않는 문장과 근거가 부족한 항목은 판정에 사용하지 않습니다.
          </small>
        </aside>
      </section>
      <div className="rule-review-flow" aria-label="연결 및 규칙 검토 대기열">
        <ReviewColumn
          domain="rider_clause"
          eyebrow="01 · Policy ↔ Terms"
          title="담보와 약관 연결"
          onOpen={open}
        />
        <div className="rule-review-bridge" aria-hidden="true">
          <span>Evidence</span>
        </div>
        <ReviewColumn
          domain="coverage_rule"
          eyebrow="02 · Terms → Rule"
          title="보장 규칙"
          onOpen={open}
        />
      </div>
      {openItem?.candidate_kind === "rider_clause" ? (
        <RiderClauseReviewDialog
          item={openItem}
          onClose={() => setOpenItem(undefined)}
          onMutated={mutated}
        />
      ) : null}
      {openItem?.candidate_kind === "coverage_rule" ? (
        <CoverageRuleReviewDialog
          item={openItem}
          onClose={() => setOpenItem(undefined)}
          onMutated={mutated}
        />
      ) : null}
    </main>
  );
}

export { ISSUE_COPY };
