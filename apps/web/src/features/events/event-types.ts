import type {
  MedicalEventResponse,
  OptionalQuestionResponse,
  StructuredFactResponse,
} from "../../api/generated";
import type { EventResult } from "../../api/results";

export type EventMode = MedicalEventResponse["mode"];
export type FactState = StructuredFactResponse["state"];
export type MedicalEventFact = StructuredFactResponse;
export type OptionalQuestion = OptionalQuestionResponse;
export type MedicalEvent = MedicalEventResponse;
export type { EventResult };

export type ResultGroup = "claim_review" | "needs_information" | "mismatch";

/** Map the generated technical result to the safe action-first UI group. */
export function resultGroup(
  result: "MATCH" | "NO_MATCH" | "UNKNOWN",
): ResultGroup {
  if (result === "MATCH") return "claim_review";
  if (result === "NO_MATCH") return "mismatch";
  return "needs_information";
}

export function userFacingResultGroup(group: ResultGroup): string {
  if (group === "claim_review") return "청구 검토";
  if (group === "mismatch") return "조건 불일치";
  return "추가 확인 필요";
}
