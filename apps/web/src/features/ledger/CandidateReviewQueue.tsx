import { useEffect, useRef, useState } from "react";

import type { PolicyReviewItem } from "../../api/generated";
import { CandidateReviewDialog, ISSUE_COPY } from "./CandidateReviewDialog";

function isTermsOnly(item: PolicyReviewItem): boolean {
  return item.issues.some((issue) => issue.code === "TERMS_ONLY_RIDER");
}

function stringField(
  item: PolicyReviewItem,
  fieldId: string,
): string | undefined {
  const value = item.fields.find((field) => field.field_id === fieldId)?.value;
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function insuranceLabel(
  item: PolicyReviewItem,
  items: PolicyReviewItem[],
): string {
  const policyCandidate =
    item.candidate_kind === "policy_contract"
      ? item
      : items.find(
          (candidate) =>
            candidate.aggregate_id === item.aggregate_id &&
            candidate.candidate_kind === "policy_contract",
        );
  const insurer = policyCandidate
    ? stringField(policyCandidate, "insurer")
    : undefined;
  const product = policyCandidate
    ? stringField(policyCandidate, "product_name")
    : undefined;
  if (insurer && product) return `${insurer} · ${product}`;
  if (product) return product;
  if (insurer) return insurer;
  return stringField(item, "rider_name") ?? "식별 정보 확인 필요";
}

export function CandidateReviewQueue({
  items,
  memberDisplayName,
  onMutated,
}: {
  items: PolicyReviewItem[];
  memberDisplayName: string;
  onMutated: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [openItem, setOpenItem] = useState<PolicyReviewItem>();
  const openerRef = useRef<HTMLButtonElement | undefined>(undefined);

  useEffect(() => {
    if (!openItem) openerRef.current?.focus();
  }, [openItem]);

  function open(item: PolicyReviewItem, opener: HTMLButtonElement) {
    openerRef.current = opener;
    setOpenItem(item);
  }

  const visible = expanded ? items : items.filter((item) => !isTermsOnly(item));
  return (
    <section className="review-queue" aria-labelledby="review-queue-title">
      <header>
        <div>
          <span>Exception queue</span>
          <h2 id="review-queue-title">추가 확인 필요</h2>
        </div>
        <button
          type="button"
          className="secondary-button"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "검토 목록 접기" : "검토 필요 항목 보기"}
        </button>
      </header>
      {visible.length === 0 ? (
        <p className="empty-inline">현재 바로 검토할 후보가 없습니다.</p>
      ) : (
        <ul className="review-list">
          {visible.map((item) => (
            <li key={item.review_item_id}>
              <div>
                <p className="review-member">대상: {memberDisplayName}</p>
                <p className="review-policy">
                  보험: {insuranceLabel(item, items)}
                </p>
                {(item.issues.length > 0
                  ? item.issues
                  : [{ code: "LOW_CONFIDENCE" as const, field_id: null }]
                ).map((issue) => (
                  <div
                    key={`${item.review_item_id}:${issue.code}:${issue.field_id ?? "all"}`}
                  >
                    <code>{issue.code}</code>
                    <p>{ISSUE_COPY[issue.code]}</p>
                  </div>
                ))}
              </div>
              <button
                type="button"
                className="review-button"
                onClick={(event) => open(item, event.currentTarget)}
              >
                후보 검토
              </button>
            </li>
          ))}
        </ul>
      )}
      {openItem ? (
        <CandidateReviewDialog
          item={openItem}
          onClose={() => setOpenItem(undefined)}
          onConfirmed={onMutated}
        />
      ) : null}
    </section>
  );
}
