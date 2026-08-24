import type { ClauseHierarchyNode } from "../../api/clauses";

function hierarchyRoots(nodes: ClauseHierarchyNode[]): ClauseHierarchyNode[] {
  if (nodes.some((node) => node.children?.length)) return nodes;
  const byId = new Map<string, ClauseHierarchyNode>();
  for (const node of nodes) byId.set(node.clause_id, { ...node, children: [] });
  const roots: ClauseHierarchyNode[] = [];
  for (const node of byId.values()) {
    const parent = node.parent_clause_id
      ? byId.get(node.parent_clause_id)
      : undefined;
    if (parent) {
      parent.children?.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

function ClauseTree({
  nodes,
  level,
}: {
  nodes: ClauseHierarchyNode[];
  level: number;
}) {
  return (
    <ul className="clause-tree" role={level === 1 ? "tree" : undefined}>
      {nodes.map((node) => (
        <li key={node.clause_id} role="treeitem" aria-level={level}>
          <div className="clause-tree-row">
            <span>{node.clause_type}</span>
            <strong>{node.label}</strong>
            <small>
              물리 페이지 {node.physical_page_start}–{node.physical_page_end}
            </small>
          </div>
          {node.children?.length ? (
            <ClauseTree nodes={node.children} level={level + 1} />
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function ClauseHierarchy({
  editionLabel,
  error,
  loading,
  nodes,
  onClose,
}: {
  editionLabel?: string;
  error?: boolean;
  loading: boolean;
  nodes: ClauseHierarchyNode[];
  onClose: () => void;
}) {
  const roots = hierarchyRoots(nodes);
  return (
    <section
      className="clause-hierarchy"
      aria-labelledby="clause-hierarchy-title"
    >
      <header className="section-heading">
        <div>
          <span>Hierarchy</span>
          <h2 id="clause-hierarchy-title">조항 계층</h2>
        </div>
        <button type="button" className="quiet-button" onClick={onClose}>
          닫기
        </button>
      </header>
      {editionLabel ? (
        <p className="clause-hierarchy-edition">{editionLabel}</p>
      ) : null}
      {loading ? (
        <p className="loading-state" role="status" aria-live="polite">
          조항 계층을 불러오는 중입니다.
        </p>
      ) : error ? (
        <p role="alert">
          조항 계층을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.
        </p>
      ) : roots.length === 0 ? (
        <p className="empty-state compact">표시할 계층 정보가 없습니다.</p>
      ) : (
        <ClauseTree nodes={roots} level={1} />
      )}
    </section>
  );
}
