import type { TermsEditionResponse } from "../../api/clauses";

export interface ClauseSearchFilterValues {
  effectiveOn: string;
  insurerKey: string;
  productKey: string;
  termsEditionId: string;
}

export function ClauseSearchFilters({
  editions,
  values,
  onChange,
  onReset,
}: {
  editions: TermsEditionResponse[];
  values: ClauseSearchFilterValues;
  onChange: (values: ClauseSearchFilterValues) => void;
  onReset: () => void;
}) {
  return (
    <fieldset className="clause-filters">
      <legend>검색 범위</legend>
      <div className="clause-filter-grid">
        <label>
          <span>약관 판본</span>
          <select
            aria-label="약관 판본 Terms edition"
            value={values.termsEditionId}
            onChange={(event) =>
              onChange({ ...values, termsEditionId: event.target.value })
            }
          >
            <option value="">모든 판본</option>
            {editions.map((edition) => (
              <option key={edition.id} value={edition.id}>
                {edition.insurer_display} · {edition.product_display}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>적용 기준일</span>
          <input
            aria-label="적용 기준일 Effective date"
            type="date"
            value={values.effectiveOn}
            onChange={(event) =>
              onChange({ ...values, effectiveOn: event.target.value })
            }
          />
        </label>
        <label>
          <span>보험사 키 (선택)</span>
          <input
            aria-label="보험사 키 Insurer filter"
            type="text"
            value={values.insurerKey}
            onChange={(event) =>
              onChange({ ...values, insurerKey: event.target.value })
            }
          />
        </label>
        <label>
          <span>상품 키 (선택)</span>
          <input
            aria-label="상품 키 Product filter"
            type="text"
            value={values.productKey}
            onChange={(event) =>
              onChange({ ...values, productKey: event.target.value })
            }
          />
        </label>
      </div>
      <button type="button" className="quiet-button" onClick={onReset}>
        필터 초기화 Reset filters
      </button>
    </fieldset>
  );
}
