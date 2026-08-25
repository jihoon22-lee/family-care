/// <reference types="node" />

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const viteConfig = readFileSync(
  resolve(process.cwd(), "vite.config.ts"),
  "utf8",
);

describe("FamilyCare service-worker cache policy", () => {
  it("keeps the precache limited to hashed app-shell assets", () => {
    expect(viteConfig).not.toContain("runtimeCaching");
    expect(viteConfig).not.toMatch(
      /NetworkFirst|CacheFirst|StaleWhileRevalidate/,
    );
    expect(viteConfig).toContain('globPatterns: ["**/*.{css,html,js,svg}"]');
  });

  it("denies navigation fallback for private data paths", () => {
    expect(viteConfig).toContain("navigateFallbackDenylist");
    for (const path of [
      "/api/",
      "/documents/",
      "/evidence/",
      "/medical-events/",
      "/results/",
      "/claims/",
    ]) {
      expect(viteConfig).toContain(`^${path.replaceAll("/", "\\/")}`);
    }
  });
});
