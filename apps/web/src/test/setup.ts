import "@testing-library/jest-dom/vitest";

beforeEach(() => {
  // Identity and business state are intentionally memory-only. Start every
  // browser test with empty Web Storage so an accidental persistence write is
  // observable in the focused assertions.
  window.localStorage.clear();
  window.sessionStorage.clear();
});
