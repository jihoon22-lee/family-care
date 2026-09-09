import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { EvidenceDetailResponse } from "../../api/generated";
import { authStore } from "../identity/authStore";
import {
  useEvidenceDisclosure,
  type EvidenceSource,
} from "./useEvidenceDisclosure";

function item(number: number): EvidenceDetailResponse {
  return {
    evidence_id: `synthetic-evidence-${number}`,
    document_version_id: `synthetic-document-${number}`,
    document_label: `Sample Document ${number}`,
    physical_page: number + 1,
    bbox: null,
    bounded_excerpt: `Synthetic excerpt ${number}`,
    review_state: "USER_CONFIRMED",
  };
}
function Harness({
  sources,
  next = sources,
}: {
  sources: EvidenceSource[];
  next?: EvidenceSource[];
}) {
  const disclosure = useEvidenceDisclosure("synthetic-run");
  return (
    <>
      <button onClick={() => disclosure.show(sources)}>open</button>
      <button onClick={() => disclosure.show(next)}>next</button>
      <button onClick={disclosure.close}>close</button>
      <button onClick={disclosure.showMore}>more</button>
      <button onClick={disclosure.retry}>retry</button>
      {disclosure.open ? (
        <section>
          <span>
            {disclosure.items.map((value) => value.document_label).join(",")}
          </span>
          <span>{`total:${disclosure.total} failed:${disclosure.failed} remaining:${disclosure.remaining}`}</span>
        </section>
      ) : null}
    </>
  );
}

describe("evidence disclosure request scope", () => {
  it("keeps successful entries, loads 16 at a time, and retries only failed entries", async () => {
    const loaders = Array.from({ length: 18 }, (_, index) =>
      vi.fn().mockResolvedValue(item(index)),
    );
    loaders[2].mockRejectedValueOnce(new Error("synthetic unavailable"));
    render(
      <Harness
        sources={loaders.map((load, index) => ({ key: String(index), load }))}
      />,
    );
    fireEvent.click(screen.getByText("open"));
    await waitFor(() =>
      expect(screen.getByText("total:18 failed:1 remaining:2")).toBeVisible(),
    );
    expect(screen.getByText(/Sample Document 0/)).toBeVisible();
    expect(loaders[16]).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("more"));
    await waitFor(() =>
      expect(screen.getByText("total:18 failed:1 remaining:0")).toBeVisible(),
    );
    fireEvent.click(screen.getByText("retry"));
    await waitFor(() =>
      expect(screen.getByText("total:18 failed:0 remaining:0")).toBeVisible(),
    );
    expect(loaders[2]).toHaveBeenCalledTimes(2);
    expect(loaders[0]).toHaveBeenCalledTimes(1);
  });

  it("aborts the previous candidate and ignores a late result", async () => {
    let resolve!: (value: EvidenceDetailResponse) => void;
    const first = vi.fn((signal: AbortSignal) => {
      void signal;
      return new Promise<EvidenceDetailResponse>((done) => {
        resolve = done;
      });
    });
    render(
      <Harness
        sources={[{ key: "a", load: first }]}
        next={[{ key: "b", load: async () => item(2) }]}
      />,
    );
    fireEvent.click(screen.getByText("open"));
    fireEvent.click(screen.getByText("next"));
    await screen.findByText("Sample Document 2");
    await act(async () => resolve(item(1)));
    expect(first.mock.calls[0][0].aborted).toBe(true);
    expect(screen.queryByText("Sample Document 1")).toBeNull();
  });

  it("does not reopen a closed drawer when the request resolves", async () => {
    let resolve!: (value: EvidenceDetailResponse) => void;
    render(
      <Harness
        sources={[
          {
            key: "a",
            load: () =>
              new Promise((done) => {
                resolve = done;
              }),
          },
        ]}
      />,
    );
    fireEvent.click(screen.getByText("open"));
    fireEvent.click(screen.getByText("close"));
    await act(async () => resolve(item(1)));
    expect(screen.queryByText("Sample Document 1")).toBeNull();
  });

  it("clears retained excerpts and aborts pending requests on logout", async () => {
    let resolve!: (value: EvidenceDetailResponse) => void;
    const pending = vi.fn((signal: AbortSignal) => {
      void signal;
      return new Promise<EvidenceDetailResponse>((done) => {
        resolve = done;
      });
    });
    render(
      <Harness
        sources={[
          { key: "a", load: async () => item(1) },
          { key: "b", load: pending },
        ]}
      />,
    );
    fireEvent.click(screen.getByText("open"));
    await screen.findByText("Sample Document 1");
    act(() => authStore.clear());
    await act(async () => resolve(item(2)));
    expect(pending.mock.calls[0][0].aborted).toBe(true);
    expect(screen.queryByText(/Sample Document/)).toBeNull();
  });
});
