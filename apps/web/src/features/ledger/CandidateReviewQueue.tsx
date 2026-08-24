import { useEffect, useRef, useState } from "react";

import type { PolicyReviewItem } from "../../api/generated";
import { CandidateReviewDialog, ISSUE_COPY } from "./CandidateReviewDialog";

function isTermsOnly(item: PolicyReviewItem): boolean {
  return item.issues.some((issue) => issue.code === "TERMS_ONLY_RIDER");
}

export function CandidateReviewQueue({
  items,
  onMutated,
}: {
  items: PolicyReviewItem[];
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
                <code>{item.issues[0]?.code ?? "LOW_CONFIDENCE"}</code>
                <p>{ISSUE_COPY[item.issues[0]?.code ?? "LOW_CONFIDENCE"]}</p>
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
