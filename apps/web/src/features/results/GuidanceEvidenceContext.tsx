import { createContext, useContext } from "react";
import type {
  GuidanceEvidence,
  GuidanceSemanticEvidence,
} from "../../api/generated";

export type GuidanceEvidenceReferences = (
  GuidanceEvidence | GuidanceSemanticEvidence
)[];
export const GuidanceEvidenceContext = createContext<
  ((evidence: GuidanceEvidenceReferences) => void) | undefined
>(undefined);
export function useGuidanceEvidence() {
  return useContext(GuidanceEvidenceContext);
}
