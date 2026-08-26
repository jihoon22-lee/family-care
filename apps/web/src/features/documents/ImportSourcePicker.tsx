import type { ImportSourceResponse } from "../../api/generated";

function sizeLabel(sizeBytes: number): string {
  if (sizeBytes < 1024 * 1024)
    return `${Math.max(1, Math.ceil(sizeBytes / 1024))} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ImportSourcePicker({
  disabled = false,
  onChange,
  selectedIds,
  sources,
}: {
  disabled?: boolean;
  onChange: (sourceId: string, selected: boolean) => void;
  selectedIds: ReadonlySet<string>;
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
              </label>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}
