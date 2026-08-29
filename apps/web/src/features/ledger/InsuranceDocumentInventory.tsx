import { type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  InventoryComponentResponse,
  InventorySetItemResponse,
  MemberInsuranceDocumentInventoryResponse,
  RegisteredPolicyInventoryResponse,
  RoleDocumentSummaryResponse,
  UnregisteredDocumentSetResponse,
} from "../../api/generated";
import {
  attachInsuranceDocumentSetItem,
  createInsuranceDocumentComponent,
  createInsuranceDocumentSet,
  detachInsuranceDocumentSetItem,
} from "../../api/insurance-document-inventory";
import { useInsuranceDocumentInventory } from "./useInsuranceDocumentInventory";

const ROLE_LABELS: Record<InventoryComponentResponse["role"], string> = {
  application: "청약서",
  policy: "증권",
  product_explanation: "상품설명서",
  supporting: "보조자료",
  terms: "약관",
};

const PROCESSING_LABELS: Record<
  InventoryComponentResponse["processing_state"],
  string
> = {
  FAILED: "판독 실패",
  OCR_REQUIRED: "OCR 필요",
  PASSWORD_REQUIRED: "암호 해제 필요",
  PENDING: "처리 중",
  READY: "처리 완료",
};

const REVIEW_LABELS: Record<
  InventoryComponentResponse["review_state"],
  string
> = {
  CONFLICT: "상충",
  REJECTED: "제외",
  SUGGESTED: "연결 제안",
  USER_CONFIRMED: "사용자 확인",
};

const MATCH_LABELS: Record<InventorySetItemResponse["match_state"], string> = {
  CONFLICT: "상충",
  REJECTED: "제외",
  SUGGESTED: "연결 제안",
  USER_CONFIRMED: "사용자 확인",
};

const DUPLICATE_LABELS: Record<
  InventoryComponentResponse["duplicate_state"],
  string
> = {
  CROSS_MEMBER_COPY_POSSIBLE: "가족 간 공유 사본 가능성",
  SAME_MEMBER_DUPLICATE: "같은 구성원 중복",
  UNIQUE: "고유 문서",
};

const CLASSIFICATION_LABELS: Record<
  UnregisteredDocumentSetResponse["primary_classification"],
  string
> = {
  APPLICATION_ONLY: "청약서만 있는 자료",
  POLICY_UNREVIEWED: "증권 검토 대기 자료",
  PRODUCT_EXPLANATION_ONLY: "상품설명서만 있는 자료",
  SUPPORTING_ONLY: "보조자료만 있는 자료",
  TERMS_ONLY: "약관만 있는 자료",
};

const STATUS_LABELS: Record<string, string> = {
  active: "현재 유효",
  cancelled: "해지·취소",
  expired: "기간 만료",
  inactive: "현재 비활성",
  unknown: "현재 상태 확인 필요",
};

interface InventorySetTarget {
  displayLabel: string;
  documentSetId?: string;
  key: string;
  label: string;
  policyId?: string;
  version?: number;
}

type DetachHandler = (item: InventorySetItemResponse) => void;

interface ComponentReviewInput {
  pageEnd: number;
  pageStart: number;
  role: InventoryComponentResponse["role"];
}

type ComponentReviewHandler = (
  component: InventoryComponentResponse,
  input: ComponentReviewInput,
) => void;

function setTargets(
  data: MemberInsuranceDocumentInventoryResponse,
): InventorySetTarget[] {
  const registered = data.registered_policies.flatMap((policy) => {
    return [
      {
        displayLabel: policy.product_display,
        documentSetId: policy.document_set_id ?? undefined,
        key: policy.document_set_id ?? `policy:${policy.policy_id}`,
        label: `등록된 보험 · ${policy.insurer_display} · ${policy.product_display}`,
        policyId: policy.policy_id,
        version: policy.document_set_version ?? undefined,
      },
    ];
  });
  const unregistered = data.unregistered_document_sets.map((documentSet) => ({
    displayLabel: documentSet.display_label,
    documentSetId: documentSet.id,
    key: documentSet.id,
    label: `가입 확인 안 됨 · ${documentSet.display_label}`,
    version: documentSet.version,
  }));
  return [...registered, ...unregistered];
}

function mutationErrorMessage(): string {
  return "문서 검수·연결 상태를 변경하지 못했습니다. 현황을 다시 확인한 뒤 시도해 주세요.";
}

function pageLabel(component: InventoryComponentResponse): string {
  return component.page_start === component.page_end
    ? `${component.page_start}쪽`
    : `${component.page_start}–${component.page_end}쪽`;
}

function sourceComponentLabel(
  sourceCount: number,
  componentCount: number,
): string {
  return `원본 ${sourceCount}개 · 구간 ${componentCount}개`;
}

function ComponentMeta({
  component,
  matchState,
}: {
  component: InventoryComponentResponse;
  matchState?: InventorySetItemResponse["match_state"];
}) {
  return (
    <span className="insurance-inventory-component-meta">
      <span>{pageLabel(component)}</span>
      <span>{PROCESSING_LABELS[component.processing_state]}</span>
      <span>
        {matchState
          ? MATCH_LABELS[matchState]
          : REVIEW_LABELS[component.review_state]}
      </span>
      {component.duplicate_state !== "UNIQUE" ? (
        <span>{DUPLICATE_LABELS[component.duplicate_state]}</span>
      ) : null}
    </span>
  );
}

function DetachButton({
  item,
  onDetach,
  pending,
}: {
  item: InventorySetItemResponse;
  onDetach?: DetachHandler;
  pending: boolean;
}) {
  if (!onDetach || !item.id) return null;
  return (
    <button
      aria-label={`${ROLE_LABELS[item.component.role]} ${pageLabel(item.component)} 연결 해제`}
      className="insurance-inventory-item-action"
      disabled={pending}
      onClick={() => onDetach(item)}
      type="button"
    >
      {pending ? "해제 중…" : "연결 해제"}
    </button>
  );
}

function RoleDocument({
  document,
  onDetach,
  pendingItemId,
}: {
  document: RoleDocumentSummaryResponse;
  onDetach?: DetachHandler;
  pendingItemId?: string;
}) {
  return (
    <li className="insurance-inventory-role">
      <div className="insurance-inventory-role-heading">
        <strong>{ROLE_LABELS[document.role]}</strong>
        <span>
          {sourceComponentLabel(
            document.source_count,
            document.component_count,
          )}
        </span>
      </div>
      {document.bundled_source ? (
        <div className="insurance-inventory-role-flags">
          <span>묶음 문서</span>
        </div>
      ) : null}
      {document.items.length > 0 ? (
        <ul className="insurance-inventory-component-list">
          {document.items.map((item, index) => (
            <li key={item.id ?? `${document.role}-${index}`}>
              <div className="insurance-inventory-component-row">
                <ComponentMeta
                  component={item.component}
                  matchState={item.match_state}
                />
                <DetachButton
                  item={item}
                  onDetach={onDetach}
                  pending={item.id === pendingItemId}
                />
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function SummaryCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="insurance-inventory-summary-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>건</small>
    </div>
  );
}

function PolicyInventoryCard({
  onDetach,
  pendingItemId,
  policy,
}: {
  onDetach?: DetachHandler;
  pendingItemId?: string;
  policy: RegisteredPolicyInventoryResponse;
}) {
  const hasTerms = policy.completeness === "CERTIFICATE_AND_TERMS";
  return (
    <article className="insurance-inventory-policy-card">
      <header className="insurance-inventory-card-heading">
        <div>
          <p>{policy.insurer_display}</p>
          <h3>{policy.product_display}</h3>
        </div>
        <span className="insurance-inventory-status">
          {STATUS_LABELS[policy.status] ?? "현재 상태 확인 필요"}
        </span>
      </header>
      <div className="insurance-inventory-policy-facts">
        <span>
          <strong>{policy.rider_count}</strong>개<small>가입 담보</small>
        </span>
        <span>
          <strong>{hasTerms ? "증권+약관" : "증권만"}</strong>
          <small>문서 완전성</small>
        </span>
      </div>
      <ul className="insurance-inventory-role-list">
        {policy.documents.map((document, index) => (
          <RoleDocument
            document={document}
            key={`${document.role}-${index}`}
            onDetach={onDetach}
            pendingItemId={pendingItemId}
          />
        ))}
      </ul>
      <div className="insurance-inventory-policy-notes">
        {policy.missing_document_roles.includes("terms") ? (
          <span className="inventory-note inventory-note-caution">
            약관 보완 필요
          </span>
        ) : null}
        <span className="inventory-note">
          상품설명서 {policy.has_product_explanation ? "있음" : "없음"}
        </span>
        <span className="inventory-note">
          청약서 {policy.has_application ? "있음" : "없음"}
        </span>
      </div>
    </article>
  );
}

function UnregisteredSetCard({
  documentSet,
  onDetach,
  pendingItemId,
}: {
  documentSet: UnregisteredDocumentSetResponse;
  onDetach?: DetachHandler;
  pendingItemId?: string;
}) {
  return (
    <article className="insurance-inventory-unregistered-card">
      <header className="insurance-inventory-card-heading">
        <div>
          <p>{documentSet.insurer_display ?? "보험사 확인 전"}</p>
          <h3>{documentSet.product_display ?? documentSet.display_label}</h3>
          {documentSet.product_display ? (
            <small>{documentSet.display_label}</small>
          ) : null}
        </div>
        <span className="insurance-inventory-unconfirmed">가입 확인 안 됨</span>
      </header>
      <div className="insurance-inventory-unregistered-meta">
        <span>{CLASSIFICATION_LABELS[documentSet.primary_classification]}</span>
        <span>
          {sourceComponentLabel(
            documentSet.source_count,
            documentSet.component_count,
          )}
        </span>
      </div>
      <ul className="insurance-inventory-role-list">
        {documentSet.items.map((item, index) => (
          <li
            className="insurance-inventory-role"
            key={item.id ?? `set-item-${index}`}
          >
            <div className="insurance-inventory-role-heading">
              <strong>{ROLE_LABELS[item.component.role]}</strong>
              <span>{MATCH_LABELS[item.match_state]}</span>
            </div>
            <div className="insurance-inventory-component-row">
              <ComponentMeta
                component={item.component}
                matchState={item.match_state}
              />
              <DetachButton
                item={item}
                onDetach={onDetach}
                pending={item.id === pendingItemId}
              />
            </div>
          </li>
        ))}
      </ul>
      <div className="insurance-inventory-policy-notes">
        <span className="inventory-note">
          상품설명서 {documentSet.has_product_explanation ? "있음" : "없음"}
        </span>
        <span className="inventory-note">
          청약서 {documentSet.has_application ? "있음" : "없음"}
        </span>
      </div>
    </article>
  );
}

function ComponentReviewControls({
  component,
  onConfirm,
  pending,
}: {
  component: InventoryComponentResponse;
  onConfirm: ComponentReviewHandler;
  pending: boolean;
}) {
  const [role, setRole] = useState<InventoryComponentResponse["role"] | "">("");
  const [pageStart, setPageStart] = useState(String(component.page_start));
  const [pageEnd, setPageEnd] = useState(String(component.page_end));
  const parsedPageStart = Number(pageStart);
  const parsedPageEnd = Number(pageEnd);
  const validPageRange =
    /^\d+$/.test(pageStart) &&
    /^\d+$/.test(pageEnd) &&
    Number.isInteger(parsedPageStart) &&
    Number.isInteger(parsedPageEnd) &&
    parsedPageStart >= component.page_start &&
    parsedPageEnd >= parsedPageStart &&
    parsedPageEnd <= component.page_end;

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!role || !validPageRange) return;
    onConfirm(component, {
      pageEnd: parsedPageEnd,
      pageStart: parsedPageStart,
      role,
    });
  }

  return (
    <form className="insurance-inventory-attach-controls" onSubmit={submit}>
      <div>
        <p className="insurance-inventory-unpaired-note">
          가져오기 분류 제안: {ROLE_LABELS[component.role]}
        </p>
        <p className="insurance-inventory-unpaired-note">
          실제 PDF를 확인해 역할과 1-based 페이지 범위를 직접 지정해 주세요.
        </p>
        <label>
          <span>문서 역할</span>
          <select
            aria-label="검수할 문서 역할"
            disabled={pending}
            onChange={(event) =>
              setRole(
                event.target.value as InventoryComponentResponse["role"] | "",
              )
            }
            required
            value={role}
          >
            <option value="">역할 선택</option>
            {Object.entries(ROLE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>시작 페이지</span>
          <input
            aria-label="시작 페이지"
            disabled={pending}
            inputMode="numeric"
            max={component.page_end}
            min={component.page_start}
            onChange={(event) => setPageStart(event.target.value)}
            required
            type="number"
            value={pageStart}
          />
        </label>
        <label>
          <span>마지막 페이지</span>
          <input
            aria-label="마지막 페이지"
            disabled={pending}
            inputMode="numeric"
            max={component.page_end}
            min={component.page_start}
            onChange={(event) => setPageEnd(event.target.value)}
            required
            type="number"
            value={pageEnd}
          />
        </label>
      </div>
      <button
        aria-label="검수 내용 확정"
        className="insurance-inventory-item-action insurance-inventory-attach-action"
        disabled={pending || !role || !validPageRange}
        type="submit"
      >
        {pending ? "저장 중…" : "검수 내용 확정"}
      </button>
    </form>
  );
}

function UnpairedComponent({
  component,
  onAttach,
  onConfirmReview,
  onSelectTarget,
  pending,
  selectedTargetId,
  targets,
}: {
  component: InventoryComponentResponse;
  onAttach?: (component: InventoryComponentResponse) => void;
  onConfirmReview?: ComponentReviewHandler;
  onSelectTarget?: (componentId: string, setId: string) => void;
  pending: boolean;
  selectedTargetId?: string;
  targets?: InventorySetTarget[];
}) {
  const componentId = component.id;
  const availableTargets = targets ?? [];
  const attachable = component.review_state === "USER_CONFIRMED";
  return (
    <li className="insurance-inventory-unpaired-item">
      <div className="insurance-inventory-role-heading">
        <strong>{ROLE_LABELS[component.role]}</strong>
        <span>가입 확인 안 됨</span>
      </div>
      <ComponentMeta component={component} />
      {componentId &&
      attachable &&
      onAttach &&
      onSelectTarget &&
      availableTargets.length > 0 ? (
        <div className="insurance-inventory-attach-controls">
          <label>
            <span>연결 대상</span>
            <select
              aria-label={`${ROLE_LABELS[component.role]} ${pageLabel(component)} 문서를 연결할 보험`}
              disabled={pending}
              onChange={(event) =>
                onSelectTarget(componentId, event.target.value)
              }
              value={selectedTargetId ?? availableTargets[0]?.key}
            >
              {availableTargets.map((target) => (
                <option key={target.key} value={target.key}>
                  {target.label}
                </option>
              ))}
            </select>
          </label>
          <button
            aria-label={`${ROLE_LABELS[component.role]} ${pageLabel(component)} 문서 연결`}
            className="insurance-inventory-item-action insurance-inventory-attach-action"
            disabled={pending}
            onClick={() => onAttach(component)}
            type="button"
          >
            {pending ? "연결 중…" : "이 문서 연결"}
          </button>
        </div>
      ) : componentId && !attachable ? (
        <p className="insurance-inventory-unpaired-note">검수 후 연결 가능</p>
      ) : componentId ? (
        <p className="insurance-inventory-unpaired-note">
          연결할 문서 묶음이 없습니다. 먼저 문서를 가져와 주세요.
        </p>
      ) : component.document_batch_item_id &&
        component.processing_state === "READY" &&
        component.review_state === "SUGGESTED" &&
        onConfirmReview ? (
        <ComponentReviewControls
          component={component}
          onConfirm={onConfirmReview}
          pending={pending}
        />
      ) : (
        <p className="insurance-inventory-unpaired-note">
          처리 이력을 확인할 수 없어 검수할 수 없습니다.
        </p>
      )}
    </li>
  );
}

function UnreadableSources({
  sources,
}: {
  sources: MemberInsuranceDocumentInventoryResponse["unreadable_sources"];
}) {
  if (sources.length === 0) return null;
  return (
    <div className="insurance-inventory-unreadable">
      <div className="insurance-inventory-section-heading compact">
        <div>
          <span>Unreadable sources</span>
          <h3>판독 필요 자료</h3>
        </div>
        <strong>{sources.length}건</strong>
      </div>
      <ul className="insurance-inventory-unreadable-list">
        {sources.map((source) => (
          <li key={source.document_batch_item_id}>
            <div className="insurance-inventory-role-heading">
              <strong>{source.display_label}</strong>
              <span>{ROLE_LABELS[source.source_kind]}</span>
            </div>
            <span className="insurance-inventory-component-meta">
              <span>{PROCESSING_LABELS[source.processing_state]}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function InventoryContent({
  data,
  memberId,
  onAttach,
  onConfirmReview,
  onDetach,
  onSelectTarget,
  pendingAction,
  selectedTargets,
  targets,
}: {
  data: MemberInsuranceDocumentInventoryResponse;
  memberId: string;
  onAttach: (component: InventoryComponentResponse) => void;
  onConfirmReview: ComponentReviewHandler;
  onDetach: DetachHandler;
  onSelectTarget: (componentId: string, setId: string) => void;
  pendingAction?: string;
  selectedTargets: Readonly<Record<string, string>>;
  targets: InventorySetTarget[];
}) {
  return (
    <>
      <div
        className="insurance-inventory-summary"
        role="group"
        aria-label="보험 문서 요약"
      >
        <SummaryCard
          label="증권 근거 보험"
          value={data.summary.certificate_backed_policies}
        />
        <SummaryCard
          label="증권+약관"
          value={data.summary.certificate_and_terms}
        />
        <SummaryCard label="증권만" value={data.summary.certificate_only} />
        <SummaryCard
          label="미연결 약관"
          value={data.summary.terms_only_documents}
        />
        <SummaryCard
          label="상품설명서"
          value={data.summary.product_explanation_documents}
        />
        <SummaryCard
          label="판독 필요"
          value={data.summary.unreadable_documents}
        />
      </div>

      <div className="insurance-inventory-actions">
        <p>
          증권·약관을 확인한 뒤 부족한 문서를 같은 가족 구성원에게 추가할 수
          있습니다.
        </p>
        <a
          className="event-start-link"
          href={`/app/documents/import?member=${encodeURIComponent(memberId)}`}
        >
          누락 문서 추가
        </a>
      </div>

      <section
        className="insurance-inventory-registered"
        aria-labelledby="insurance-inventory-registered-title"
      >
        <div className="insurance-inventory-section-heading">
          <div>
            <span>Certificate-backed</span>
            <h2 id="insurance-inventory-registered-title">등록된 보험</h2>
          </div>
          <strong>{data.registered_policies.length}건</strong>
        </div>
        {data.registered_policies.length === 0 ? (
          <p className="insurance-inventory-empty">
            증권 근거가 확인된 보험이 없습니다.
          </p>
        ) : (
          <div className="insurance-inventory-policy-list">
            {data.registered_policies.map((policy) => (
              <PolicyInventoryCard
                key={policy.policy_id}
                onDetach={onDetach}
                pendingItemId={
                  pendingAction?.startsWith("detach:")
                    ? pendingAction.slice("detach:".length)
                    : undefined
                }
                policy={policy}
              />
            ))}
          </div>
        )}
      </section>

      <section
        className="insurance-inventory-unregistered"
        aria-labelledby="insurance-inventory-unregistered-title"
      >
        <div className="insurance-inventory-section-heading">
          <div>
            <span>Needs pairing</span>
            <h2 id="insurance-inventory-unregistered-title">
              가입 확인 안 된 문서
            </h2>
          </div>
          <strong>{data.unregistered_document_sets.length}묶음</strong>
        </div>
        {data.unregistered_document_sets.length === 0 ? (
          <p className="insurance-inventory-empty">
            미연결 문서 묶음이 없습니다.
          </p>
        ) : (
          <div className="insurance-inventory-unregistered-list">
            {data.unregistered_document_sets.map((documentSet) => (
              <UnregisteredSetCard
                documentSet={documentSet}
                key={documentSet.id}
                onDetach={onDetach}
                pendingItemId={
                  pendingAction?.startsWith("detach:")
                    ? pendingAction.slice("detach:".length)
                    : undefined
                }
              />
            ))}
          </div>
        )}
        {data.unpaired_components.length > 0 ? (
          <div className="insurance-inventory-unpaired">
            <div className="insurance-inventory-section-heading compact">
              <div>
                <span>Unpaired components</span>
                <h3>아직 묶이지 않은 문서</h3>
              </div>
              <strong>{data.unpaired_components.length}건</strong>
            </div>
            <ul className="insurance-inventory-unpaired-list">
              {data.unpaired_components.map((component, index) => (
                <UnpairedComponent
                  component={component}
                  key={component.id ?? `${component.role}-${index}`}
                  onAttach={onAttach}
                  onConfirmReview={onConfirmReview}
                  onSelectTarget={onSelectTarget}
                  pending={
                    component.id
                      ? pendingAction === `attach:${component.id}`
                      : pendingAction ===
                        `create:${component.document_batch_item_id}`
                  }
                  selectedTargetId={
                    component.id ? selectedTargets[component.id] : undefined
                  }
                  targets={targets}
                />
              ))}
            </ul>
          </div>
        ) : null}
        <UnreadableSources sources={data.unreadable_sources} />
      </section>
    </>
  );
}

export function InsuranceDocumentInventory({
  memberId,
}: {
  memberId: string | undefined;
}) {
  const { data, error, loading, reload } =
    useInsuranceDocumentInventory(memberId);
  const [selectedTargets, setSelectedTargets] = useState<
    Readonly<Record<string, string>>
  >({});
  const [pendingAction, setPendingAction] = useState<string>();
  const [mutationError, setMutationError] = useState<string>();
  const targets = useMemo(() => (data ? setTargets(data) : []), [data]);
  const unpairedComponentIds = useMemo(
    () =>
      data?.unpaired_components.flatMap((component) =>
        component.id ? [component.id] : [],
      ) ?? [],
    [data],
  );

  useEffect(() => {
    if (targets.length === 0 || unpairedComponentIds.length === 0) return;
    setSelectedTargets((current) => {
      const next: Record<string, string> = {};
      let changed = false;
      for (const componentId of unpairedComponentIds) {
        const currentTarget = current[componentId];
        const nextTarget =
          currentTarget &&
          targets.some((target) => target.key === currentTarget)
            ? currentTarget
            : targets[0]?.key;
        if (nextTarget) next[componentId] = nextTarget;
        if (next[componentId] !== currentTarget) changed = true;
      }
      if (Object.keys(current).length !== Object.keys(next).length)
        changed = true;
      return changed ? next : current;
    });
  }, [targets, unpairedComponentIds]);

  async function attach(component: InventoryComponentResponse): Promise<void> {
    if (!data || !component.id || component.review_state !== "USER_CONFIRMED")
      return;
    const targetKey = selectedTargets[component.id] ?? targets[0]?.key;
    const target = targets.find((candidate) => candidate.key === targetKey);
    if (!target) return;
    setMutationError(undefined);
    setPendingAction(`attach:${component.id}`);
    let createdSet = false;
    try {
      let documentSetId = target.documentSetId;
      let documentSetVersion = target.version;
      if (!documentSetId || documentSetVersion === undefined) {
        if (!target.policyId) return;
        const created = await createInsuranceDocumentSet(data.member_id, {
          display_label: target.displayLabel,
          policy_contract_id: target.policyId,
        });
        documentSetId = created.id;
        documentSetVersion = created.version;
        createdSet = true;
      }
      await attachInsuranceDocumentSetItem(documentSetId, {
        expected_set_version: documentSetVersion,
        insurance_document_component_id: component.id,
        match_state: "USER_CONFIRMED",
      });
      reload();
    } catch {
      if (createdSet) reload();
      setMutationError(mutationErrorMessage());
    } finally {
      setPendingAction(undefined);
    }
  }

  async function confirmReview(
    component: InventoryComponentResponse,
    input: ComponentReviewInput,
  ): Promise<void> {
    if (
      !data ||
      component.id ||
      !component.document_batch_item_id ||
      component.processing_state !== "READY"
    )
      return;
    setMutationError(undefined);
    setPendingAction(`create:${component.document_batch_item_id}`);
    try {
      await createInsuranceDocumentComponent(data.member_id, {
        document_batch_item_id: component.document_batch_item_id,
        page_end: input.pageEnd,
        page_start: input.pageStart,
        review_state: "USER_CONFIRMED",
        role: input.role,
      });
      reload();
    } catch {
      setMutationError(mutationErrorMessage());
    } finally {
      setPendingAction(undefined);
    }
  }

  async function detach(item: InventorySetItemResponse): Promise<void> {
    if (!item.id) return;
    setMutationError(undefined);
    setPendingAction(`detach:${item.id}`);
    try {
      await detachInsuranceDocumentSetItem(item.id, {
        expected_version: item.version,
      });
      reload();
    } catch {
      setMutationError(mutationErrorMessage());
    } finally {
      setPendingAction(undefined);
    }
  }

  function selectTarget(componentId: string, setId: string): void {
    setSelectedTargets((current) => ({ ...current, [componentId]: setId }));
  }

  return (
    <section
      className="insurance-inventory"
      aria-labelledby="insurance-inventory-title"
    >
      <div className="insurance-inventory-heading">
        <div>
          <span>Document completeness</span>
          <h2 id="insurance-inventory-title">보험·문서 현황</h2>
          <p>
            증권으로 가입이 확인된 보험과, 아직 계약에 연결되지 않은 자료를
            분리해 표시합니다.
          </p>
        </div>
      </div>
      {loading && !data ? (
        <p
          className="insurance-inventory-loading"
          role="status"
          aria-live="polite"
        >
          보험·문서 현황을 불러오는 중입니다.
        </p>
      ) : null}
      {error ? (
        <p
          className="insurance-inventory-error"
          role="alert"
          aria-label="문서 현황 오류"
        >
          보험·문서 현황을 불러오지 못했습니다. 원장 내용은 계속 표시됩니다.
          <button
            className="import-quiet-button"
            onClick={reload}
            type="button"
          >
            문서 현황 다시 불러오기
          </button>
        </p>
      ) : null}
      {mutationError ? (
        <p
          className="insurance-inventory-error"
          role="alert"
          aria-label="문서 현황 변경 오류"
        >
          {mutationError}
        </p>
      ) : null}
      {data ? (
        <InventoryContent
          data={data}
          memberId={memberId ?? data.member_id}
          onAttach={attach}
          onConfirmReview={confirmReview}
          onDetach={detach}
          onSelectTarget={selectTarget}
          pendingAction={pendingAction}
          selectedTargets={selectedTargets}
          targets={targets}
        />
      ) : null}
    </section>
  );
}
