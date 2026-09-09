import { describe, expect, it } from "vitest";
import { ApiError } from "./errors";
import { QueryCache } from "./query-cache";

describe("GET recovery", () => {
  it("keeps the last successful response during an unsuccessful refresh", async () => {
    const cache = new QueryCache();
    const saved = { amount: "300", event_version: 1 };
    await cache.load("synthetic-result", async () => saved);
    const error = new ApiError("NETWORK_ERROR", 0);
    await cache.load(
      "synthetic-result",
      async () => {
        throw error;
      },
      true,
    );
    expect(cache.snapshot("synthetic-result")).toEqual({
      data: saved,
      error,
      loading: false,
    });
    await cache.load(
      "synthetic-result",
      async () => ({ amount: "400", event_version: 2 }),
      true,
    );
    expect(cache.snapshot("synthetic-result")).toEqual({
      data: { amount: "400", event_version: 2 },
      error: undefined,
      loading: false,
    });
  });

  it("cannot restore a successful response after the session cache is cleared", async () => {
    const cache = new QueryCache();
    await cache.load("synthetic-result", async () => "original");
    let resolve!: (value: string) => void;
    const pending = cache.load(
      "synthetic-result",
      () =>
        new Promise<string>((done) => {
          resolve = done;
        }),
      true,
    );
    cache.clear();
    resolve("late private response");
    await pending;
    expect(cache.snapshot("synthetic-result").data).toBeUndefined();
  });
});
