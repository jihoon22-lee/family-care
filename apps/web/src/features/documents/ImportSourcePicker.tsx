import type {
  BatchSourceRequest,
  ImportSourceResponse,
} from "../../api/generated";

const DOCUMENT_KIND_OPTIONS: Array<{
  label: string;
  value: BatchSourceRequest["document_kind"];
}> = [
  { label: "증권", value: "policy" },
  { label: "약관", value: "terms" },
  { label: "상품설명서", value: "product_explanation" },
  { label: "청약서", value: "application" },
  { label: "보조자료", value: "supporting" },
];

function sizeLabel(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024)
    return `${Math.max(1, Math.ceil(sizeBytes / 1024))} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ImportSourcePicker({
  disabled = false,
  onKindChange,
  onChange,
  selectedIds,
  selectedKinds,
  sources,
}: {
  disabled?: boolean;
  onChange: (sourceId: string, selected: boolean) => void;
  onKindChange: (
    sourceId: string,
    documentKind: BatchSourceRequest["document_kind"],
  ) => void;
  selectedIds: ReadonlySet<string>;
  selectedKinds: ReadonlyMap<string, BatchSourceRequest["document_kind"]>;
  sources: ImportSourceResponse[];
}) {
  return (
    <fieldset className="import-source-picker" disabled={disabled}>
      <legend>가져올 PDF</legend>
      {sources.length === 0 ? (
        <p className="import-muted">가져오기 폴더에 준비된 PDF가 없습니다.</p>
      ) : (
        <ul className="import-source-list">
          {sources.map((source) => (
            <li key={source.source_id}>
              <label>
                <input
                  checked={selectedIds.has(source.source_id)}
                  onChange={(event) =>
                    onChange(source.source_id, event.target.checked)
                  }
                  type="checkbox"
                />
                <span>
                  <strong>{source.display_label}</strong>
                  <small>
                    {sizeLabel(source.size_bytes)}
                    {source.encrypted ? " · 암호 확인 가능성 있음" : ""}
                  </small>
                </span>
                <select
                  aria-label={`${source.display_label} 문서 종류`}
                  disabled={!selectedIds.has(source.source_id)}
                  onChange={(event) =>
                    onKindChange(
                      source.source_id,
                      event.target.value as BatchSourceRequest["document_kind"],
                    )
                  }
                  value={selectedKinds.get(source.source_id) ?? "supporting"}
                >
                  {DOCUMENT_KIND_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}
